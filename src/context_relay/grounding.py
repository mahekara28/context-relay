"""Check source identities and literal quotes against a captured conversation.

These checks establish which source text was available to a prepared request.
They do not establish that a source is true, current, or supports the answer.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .models import clone

if TYPE_CHECKING:
    from .middleware import PreparedTurn


def _terms(value: str) -> set[str]:
    """Unicode-aware, case-insensitive lexical terms; no semantic interpretation."""
    return set(re.findall(r"[^\W_]+", value.casefold()))


class EvidenceCheck:
    """An immutable-by-copy source-check report, not a factuality verdict."""

    def __init__(self, report: dict):
        self._report = clone(report)

    @property
    def valid(self) -> bool:
        return self._report["valid"]

    @property
    def report(self) -> dict:
        return clone(self._report)


class EvidenceCatalog:
    """Read-only original sources and checks for one prepared request.

    IDs such as ``m3`` are original message indices, scoped by ``history_id``.
    User, assistant, and plain tool messages are evidence with their original
    role preserved. System/developer instructions are never citeable sources.
    An assistant statement remains an assistant statement, not a verified fact.
    """

    def __init__(self, prepared: PreparedTurn):
        # Lazy import keeps the catalog usable by higher-level integrations
        # without making middleware and grounding import each other.
        from .middleware import PreparedTurn

        if not isinstance(prepared, PreparedTurn) or prepared._archive is None:
            raise ValueError("EvidenceCatalog requires a turn from ContextGuard.prepare().")
        self._history_id = prepared.history_id
        included = set(prepared.report["included_indices"])
        self._sources = [
            {"source_id": f"m{index}", "index": index, "role": message["role"],
             "content": message.get("content") or "", "included": index in included}
            for index, message in enumerate(prepared._archive.messages)
            if message["role"] in ("user", "assistant", "tool")
        ]
        self._by_id = {source["source_id"]: source for source in self._sources}
        self._terms_by_id = {source["source_id"]: _terms(source["content"])
                             for source in self._sources}

    @property
    def history_id(self) -> str:
        return self._history_id

    @property
    def sources(self) -> list[dict]:
        """Copies of original sources, including whether the model received them."""
        return clone(self._sources)

    def search(self, query: str, *, max_results: int = 3,
               omitted_only: bool = False) -> list[dict]:
        """Find positive lexical overlap, preferring newer messages on ties.

        Search includes omitted history and never records a fact or restores
        context itself. A match is a candidate for review/recovery, not proof
        of relevance. Whole-turn and budget rules still apply during recovery.
        """
        if type(query) is not str or not query.strip():
            raise ValueError("query must be a nonempty string.")
        if type(max_results) is not int or not 1 <= max_results <= 20:
            raise ValueError("max_results must be an integer from 1 to 20.")
        if type(omitted_only) is not bool:
            raise ValueError("omitted_only must be a boolean.")
        terms = _terms(query)
        ranked = []
        for source in self._sources:
            if omitted_only and source["included"]:
                continue
            score = len(terms & self._terms_by_id[source["source_id"]])
            if score:
                ranked.append((score, source["index"], source))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return clone([item[2] for item in ranked[:max_results]])

    def check(self, answer) -> EvidenceCheck:
        """Check the exact ``answer/history_id/citations`` response schema.

        Malformed model output returns a failed report. It never raises a
        validation error or includes captured source content in error details.
        The exact answer ``unknown`` with no citations is a valid abstention.
        Every other answer needs at least one distinct, available source with
        a nonblank exact quote. This does not check whether quotes support it.
        """
        issues: list[dict] = []
        recovery: set[int] = set()

        def result(status: str = "rejected") -> EvidenceCheck:
            return EvidenceCheck({
                "valid": not issues,
                "answer_status": status,
                "issues": issues,
                "recovery_indices": sorted(recovery),
                "checks": ["source_identity", "availability", "exact_quote"],
                "semantic_entailment_checked": False,
            })

        if type(answer) is not dict or set(answer) != {"answer", "history_id", "citations"}:
            issues.append({"code": "invalid_schema",
                           "message": "Response must contain exactly answer, history_id, and citations."})
            return result()
        if type(answer["history_id"]) is not str:
            issues.append({"code": "invalid_schema", "field": "history_id",
                           "message": "history_id must be a string."})
            return result()
        # Reject another snapshot before inspecting any claimed source/quote.
        # A cross-session response must not be usable to probe this catalog.
        if answer["history_id"] != self.history_id:
            issues.append({"code": "history_mismatch",
                           "message": "Response refers to a different captured conversation."})
            return result()
        if type(answer["answer"]) is not str or type(answer["citations"]) is not list:
            issues.append({"code": "invalid_schema",
                           "message": "answer must be a string and citations must be a list."})
            return result()
        text = answer["answer"]
        citations = answer["citations"]
        if not text.strip():
            issues.append({"code": "empty_answer", "message": "answer must not be empty."})
            return result()
        if text == "unknown":
            if citations:
                issues.append({"code": "invalid_abstention",
                               "message": "The unknown abstention must have no citations."})
                return result()
            return result("abstained")
        if not citations:
            issues.append({"code": "missing_citations",
                           "message": "An answer requires at least one source citation."})
            return result()

        seen: set[str] = set()
        for position, citation in enumerate(citations):
            if (type(citation) is not dict or set(citation) != {"source_id", "quote"}
                    or type(citation.get("source_id")) is not str
                    or type(citation.get("quote")) is not str):
                issues.append({"code": "invalid_schema", "citation_index": position,
                               "message": "Each citation must contain only string source_id and quote."})
                continue
            source_id, quote = citation["source_id"], citation["quote"]
            detail = {"citation_index": position, "source_id": source_id}
            if source_id in seen:
                issues.append({"code": "duplicate_citation", **detail,
                               "message": "Each source may be cited only once."})
                continue
            seen.add(source_id)
            source = self._by_id.get(source_id)
            if source is None:
                issues.append({"code": "unknown_source", **detail,
                               "message": "Source is not a citeable message in this captured conversation."})
                continue
            if not quote.strip() or quote not in source["content"]:
                issues.append({"code": "invalid_quote", **detail,
                               "message": "Quote must be a nonblank exact substring of the original source."})
                continue
            if not source["included"]:
                recovery.add(source["index"])
                issues.append({"code": "source_omitted", **detail,
                               "message": "Source was omitted from this prepared model request."})
        return result("sourced" if not issues else "rejected")
