"""Bounded, opt-in evidence lookup and literal citation validation for text agents.

This loop checks provenance, availability, and exact quotes. It does not judge
whether a quote entails an answer, execute tools, or make statements into facts.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import AsyncIterator, Iterator
from decimal import DecimalException

from .budget import TokenBudgetExceeded
from .content import _parse
from .grounding import EvidenceCatalog
from .models import BudgetExceeded, clone, encode


_MAX_RESPONSE_CHARS = 64000

_INSTRUCTIONS = (
    "Respond with ONLY JSON. Copy history_id from state.evidence_history_id. "
    "Message labels [Relay source: mN] identify original sources. For an answer, "
    "use {answer: string, history_id: string, citations: [{source_id: string, quote: string}]}. "
    "Quote exact original text; use one citation per source. If evidence is missing, "
    "request {history_id: string, need_evidence: string} with search words instead. "
    "If the requested fact is unknown, answer 'unknown' with citations []. "
    "Separate different entities, current facts, historical suggestions, and estimates."
)

_ISSUE_CODES = frozenset({"invalid_response", "invalid_schema", "history_mismatch", "empty_answer",
                         "invalid_abstention", "missing_citations", "duplicate_citation", "unknown_source",
                         "invalid_quote", "source_omitted", "invalid_query", "evidence_requested",
                         "no_matching_omitted_evidence", "state_changed"})


class UnsupportedResponseError(TypeError):
    """A stream/deferred result cannot be checked; response permits owner cleanup."""

    def __init__(self, response):
        self.response = response
        self.closed = False
        super().__init__("Evidence recovery requires complete responses; streams and deferred values need an adapter.")


class GroundedResult:
    """A stopped loop and its audit report. `cited` is not a truth guarantee."""

    def __init__(self, *, response, prepared, check, status, answer, report):
        self.response = response
        self.prepared = prepared
        self.check = check
        self.status = status
        self._answer = clone(answer)
        self._report = clone(report)

    @property
    def answer(self):
        """Validated citation-schema output, or None for an unresolved request."""
        return clone(self._answer)

    @property
    def report(self) -> dict:
        return clone(self._report)


def _plain_response(response):
    if type(response) is dict:
        return response
    if type(response) is str:
        try:
            return _parse(response, _MAX_RESPONSE_CHARS)
        except DecimalException as exc:
            raise ValueError("Invalid JSON number.") from exc
    raise TypeError("Provide a decoder for this provider response type.")


def _reject_stream(value):
    if inspect.isawaitable(value) or isinstance(value, (Iterator, AsyncIterator)):
        error = UnsupportedResponseError(value)
        close = getattr(value, "close", None)
        if callable(close) and not inspect.iscoroutinefunction(close):
            closed = close()
            if inspect.isawaitable(closed):
                if inspect.iscoroutine(closed):
                    closed.close()
            else:
                error.closed = True
        raise error


class _Loop:
    def __init__(self, connection, decoder, max_rounds, search_limit):
        connection._require_open()
        if connection.output is not None:
            raise ValueError("run_grounded uses its own citation response schema and cannot bypass an enabled OutputPolicy. Use a separate connection without output checking for grounded answers.")
        if type(max_rounds) is not int or not 0 <= max_rounds <= 4:
            raise ValueError("max_rounds must be an integer from 0 through 4.")
        if type(search_limit) is not int or not 1 <= search_limit <= 20:
            raise ValueError("search_limit must be an integer from 1 through 20.")
        if connection.config.protocol != "plain":
            raise ValueError("run_grounded currently requires the plain-text protocol.")
        self.decoder = _plain_response if decoder is None else decoder
        if not callable(self.decoder) or any(inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)
                                              or inspect.isgeneratorfunction(fn)
                                              for fn in (self.decoder, getattr(self.decoder, "__call__", None))):
            raise TypeError("decoder must synchronously return a complete JSON answer or evidence request.")
        self.connection = connection
        self.max_rounds = max_rounds
        self.search_limit = search_limit
        self.prepared = None
        self.response = None
        self.check = None
        self.answer = None
        self.attempts = []
        self.restored = set()
        self.status = "unresolved"
        self.fallback_used = False

    @staticmethod
    def _data(issues):
        feedback = []
        for item in issues:
            code = item.get("code")
            if code in _ISSUE_CODES:
                safe = {"code": code}
                if type(item.get("citation_index")) is int and item["citation_index"] >= 0:
                    safe["citation_index"] = item["citation_index"]
                feedback.append(safe)
        return {"relay_grounding": {"instructions": _INSTRUCTIONS,
                                    "previous_issues": feedback}}

    def accept(self, response, elapsed) -> bool:
        """Inspect one completed response; return True only for a budgeted retry."""
        self.response = response
        self.check = None
        _reject_stream(response)
        catalog = EvidenceCatalog(self.prepared)
        lookup = False
        lookup_origin = None
        selected = []
        issues = []
        try:
            proposal = self.decoder(response)
        except (ValueError, TypeError) as exc:
            # Do not feed arbitrary provider text/error details back as an
            # instruction. Only fixed machine-readable feedback is repeated.
            proposal = None
            issues = [{"code": "invalid_response", "message": "Return the requested complete JSON object."}]
        _reject_stream(proposal)
        if not issues:
            try:
                # Apply the same bound to direct mappings and custom decoders.
                # encode also rejects non-finite values, cycles and depth >64.
                if len(encode(proposal)) > _MAX_RESPONSE_CHARS:
                    raise ValueError("Grounded response exceeds the character limit.")
            except (ValueError, TypeError):
                proposal = None
                issues = [{"code": "invalid_response", "message": "Return the requested complete JSON object."}]
        if type(proposal) is dict and set(proposal) == {"history_id", "need_evidence"}:
            lookup = True
            if type(proposal["history_id"]) is not str or proposal["history_id"] != catalog.history_id:
                issues = [{"code": "history_mismatch"}]
            elif (type(proposal["need_evidence"]) is not str
                  or not proposal["need_evidence"].strip() or len(proposal["need_evidence"]) > 512):
                issues = [{"code": "invalid_query"}]
            else:
                selected = [row["index"] for row in catalog.search(
                    proposal["need_evidence"], max_results=self.search_limit, omitted_only=True)]
                lookup_origin = "model_query"
                issues = [{"code": "evidence_requested" if selected else "no_matching_omitted_evidence"}]
        else:
            self.check = catalog.check(proposal)
            if not issues:
                issues = self.check.report["issues"]
            if self.check.valid:
                self.answer = clone(proposal)
                self.status = "abstained" if proposal["answer"] == "unknown" else "cited"
            else:
                selected = self.check.report["recovery_indices"]
                if selected:
                    lookup_origin = "omitted_citation"
                elif not self.fallback_used and any(item["code"] in {"missing_citations", "unknown_source", "invalid_quote"}
                         for item in issues) and not any(item["code"] == "history_mismatch" for item in issues):
                    # Use only the original user question, never a model-written
                    # fabricated quote/ID, when the model fails to request help.
                    original_question = self.prepared._archive.messages[-1]["content"]
                    self.fallback_used = True
                    if original_question.strip():
                        selected = [row["index"] for row in catalog.search(
                            original_question, max_results=self.search_limit, omitted_only=True)]
                        if selected:
                            lookup_origin = "user_request_fallback"

        current_revision = self.connection.guard.store.get_session(self.connection.session_id)["revision"]
        if current_revision != self.prepared.revision:
            self.answer = None
            self.status = "unresolved"
            issues = [{"code": "state_changed"}]

        attempt = {"call": len(self.attempts) + 1, "elapsed_seconds": round(elapsed, 6),
                   "history_id": self.prepared.history_id, "state_revision": self.prepared.revision,
                   "size_chars": self.prepared.report["size_chars"],
                   "source_indices": self.prepared.report["included_indices"],
                   "issues": clone(issues), "requested_indices": list(selected),
                   "lookup_origin": lookup_origin,
                   "action": self.status if self.answer is not None else "unresolved"}
        self.attempts.append(attempt)
        if self.answer is not None or len(self.attempts) > self.max_rounds:
            return False
        # A mismatched snapshot must not trigger access to another archive. A
        # no-match lookup stops instead of repeatedly guessing new queries.
        if lookup and any(row["code"] in {"history_mismatch", "no_matching_omitted_evidence"}
                          for row in issues):
            return False

        request_data = self._data(issues)
        try:
            if selected:
                # Search results are ranked candidates, not a list of facts
                # which all must fit. Try fewer candidates when the complete
                # set overflows. Explicit cited sources remain mandatory.
                candidates = list(selected)
                while True:
                    try:
                        replacement = self.connection.guard.recover_context(
                            self.prepared, candidates, history_id=self.prepared.history_id,
                            request_data=request_data,
                        )
                        attempt["restored_candidate_indices"] = list(candidates)
                        break
                    except (BudgetExceeded, TokenBudgetExceeded):
                        if lookup_origin not in {"model_query", "user_request_fallback"} or len(candidates) <= 1:
                            raise
                        candidates.pop()
            else:
                options = dict(self.prepared._options)
                options["request_data"] = request_data
                replacement = self.connection.guard.prepare(self.prepared._archive.messages, **options)
            self.connection._require_open()
            self.connection._check_corrections(replacement)
        except (BudgetExceeded, TokenBudgetExceeded):
            self.status = "budget_exceeded"
            attempt["action"] = "budget_exceeded"
            return False
        if selected:
            self.restored.update(replacement.report["recovery"]["restored_indices"])
        self.prepared = replacement
        attempt["action"] = "recover" if selected else "retry_with_feedback"
        return True

    def finish(self):
        return GroundedResult(
            response=self.response, prepared=self.prepared, check=self.check, status=self.status,
            answer=self.answer,
            report={"status": self.status, "model_calls": len(self.attempts),
                    "recovery_rounds": max(0, len(self.attempts) - 1),
                    "recovered_indices": sorted(self.restored), "attempts": self.attempts,
                    "semantic_entailment_checked": False},
        )


def run_grounded(connection, *, messages, decoder=None, max_rounds=2, search_limit=2, **kwargs):
    loop = _Loop(connection, decoder, max_rounds, search_limit)
    loop.prepared = connection._prepare(messages, source_labels=True, request_data=loop._data([]))
    while True:
        connection._check_memory(loop.prepared)
        started = time.perf_counter()
        response = connection._model_call(messages=loop.prepared.messages, **kwargs)
        if not loop.accept(response, time.perf_counter() - started):
            return loop.finish()


async def run_grounded_async(connection, *, messages, decoder=None, max_rounds=2, search_limit=2, **kwargs):
    loop = _Loop(connection, decoder, max_rounds, search_limit)
    loop.prepared = await connection._prepare_async(messages, source_labels=True, request_data=loop._data([]))
    while True:
        connection._check_memory(loop.prepared)
        started = time.perf_counter()
        pending = connection._model_call(messages=loop.prepared.messages, **kwargs)
        if not inspect.isawaitable(pending):
            raise TypeError("Async grounded calls require an awaitable model callable.")
        response = await pending
        try:
            again = loop.accept(response, time.perf_counter() - started)
        except UnsupportedResponseError as exc:
            if not exc.closed:
                close = getattr(exc.response, "aclose", None)
                if callable(close):
                    closed = close()
                    if inspect.isawaitable(closed):
                        await closed
                    exc.closed = True
            raise
        if not again:
            return loop.finish()
