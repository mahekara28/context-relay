"""Review explicit user corrections without turning conversation text into facts.

This is a small, schema-guided pattern recogniser, not a general language
understander. It has no model client and never writes until the host calls
``CorrectionReview.confirm``. Even a ``suggested`` result requires that call.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .middleware import ContextGuard, PreparedTurn
from .models import clone, encode, nonempty_string, strict_equal


@dataclass(frozen=True, slots=True, init=False)
class CorrectionField:
    """One application-approved string-enum fact which can be reviewed.

    ``key`` names the recorded fact. ``choices`` contains canonical values;
    matching is case-insensitive, but confirmation stores their original case.
    The key, a version with underscores replaced by spaces, and optional
    ``aliases`` are recognised as field names. No arbitrary keys are inferred.
    """

    key: str
    choices: tuple[str, ...]
    aliases: tuple[str, ...]

    def __init__(self, key: str, *, choices: tuple[str, ...] | list[str],
                 aliases: tuple[str, ...] | list[str] = ()):
        nonempty_string(key, "Correction field key", limit=128)
        if key != key.strip() or any(ord(char) < 32 for char in key):
            raise ValueError("Correction field keys must not have surrounding whitespace or control characters.")
        if type(choices) not in (tuple, list) or not 2 <= len(choices) <= 128:
            raise ValueError("choices must contain from 2 to 128 string values.")
        captured_choices = tuple(choices)
        for value in captured_choices:
            nonempty_string(value, "Correction choice", limit=64)
            if value != value.strip() or any(ord(char) < 32 for char in value):
                raise ValueError("Correction choices must not have surrounding whitespace or control characters.")
        if len({value.casefold() for value in captured_choices}) != len(captured_choices):
            raise ValueError("Correction choices must be unique ignoring case.")
        if type(aliases) not in (tuple, list) or len(aliases) > 32:
            raise ValueError("aliases must be a tuple or list of at most 32 field names.")
        captured_aliases = tuple(aliases)
        for alias in captured_aliases:
            nonempty_string(alias, "Correction field alias", limit=128)
            if alias != alias.strip() or any(ord(char) < 32 for char in alias):
                raise ValueError("Correction aliases must not have surrounding whitespace or control characters.")
        if len({alias.casefold() for alias in captured_aliases}) != len(captured_aliases):
            raise ValueError("Correction aliases must be unique ignoring case.")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "choices", captured_choices)
        object.__setattr__(self, "aliases", captured_aliases)


def _names(field: CorrectionField) -> tuple[str, ...]:
    return tuple(dict.fromkeys((field.key, field.key.replace("_", " "), *field.aliases)))


def _pattern(values: tuple[str, ...]) -> str:
    return "(?:" + "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True)) + ")"


def _contains(text: str, value: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text, flags=re.IGNORECASE) is not None


def _canonical(field: CorrectionField, value: str) -> str | None:
    # Python's IGNORECASE also matches a few Unicode code points whose casefold
    # is different (for example ASCII I and dotted İ). Do not invent a mapping.
    return next((choice for choice in field.choices if choice.casefold() == value.casefold()), None)


def _direct(text: str, field: CorrectionField) -> tuple[str, str | None] | None:
    """Recognise a complete, single-clause imperative, never an embedded quote."""
    choices, names = _pattern(field.choices), _pattern(_names(field))
    prefix = r"(?:actually[, ]+)?(?:please\s+)?"
    suffix = r"\s*[.!]?"
    expressions = (
        (rf"use\s+(?P<new>{choices})\s+instead\s+of\s+(?P<old>{choices})", True),
        (rf"switch\s+(?:from\s+)?(?P<old>{choices})\s+to\s+(?P<new>{choices})", True),
        (rf"(?:change|set)\s+(?:the\s+)?{names}\s+to\s+(?P<new>{choices})", False),
        (rf"use\s+(?P<new>{choices})\s+for\s+(?:the\s+)?{names}", False),
    )
    for expression, has_old in expressions:
        match = re.fullmatch(prefix + expression + suffix, text.strip(), flags=re.IGNORECASE)
        if match:
            new = _canonical(field, match.group("new"))
            old = _canonical(field, match.group("old")) if has_old else None
            if new is not None and (not has_old or old is not None):
                return new, old
    return None


def _cautions(text: str) -> list[str]:
    reasons = []
    if (any(char in text for char in ('"', "'", "`", "“", "”", "‘", "’", "{", "}", "[", "]"))
            or re.search(r"(?m)^\s*>|\b(?:assistant|system|developer|tool|retrieved|document|quote)\s*:", text, re.I)):
        reasons.append("quoted_or_structured_text")
    if re.search(r"\b(?:if|suppose|assuming|hypothetical|perhaps|maybe|might|could|would|should)\b|\?", text, re.I):
        reasons.append("hypothetical_or_question")
    if re.search(r"\b(?:not|never|avoid|without|no)\b|\bdon[’']?t\b|\bdo\s+not\b", text, re.I):
        reasons.append("negation_requires_review")
    return reasons


_UNSET = object()


class CorrectionReview:
    """Read-only proposals anchored to one original request and state revision.

    A proposal ID is an identity, not an authentication token. The application
    must keep review objects and confirmation behind its own trusted interface.
    One confirmation advances the revision, so other proposals in this review
    become stale: prepare and review again before confirming another change.
    """

    def __init__(self, prepared: PreparedTurn, proposals: list[dict], report: dict):
        self._prepared = prepared
        self._relay = prepared._relay
        self._session_id = prepared._session_id
        self._history_id = prepared.history_id
        self._revision = prepared.revision
        self._proposals = clone(proposals)
        self._report = clone(report)

    @property
    def history_id(self) -> str:
        return self._history_id

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def proposals(self) -> list[dict]:
        return clone(self._proposals)

    @property
    def report(self) -> dict:
        return clone(self._report)

    def confirm(self, guard: ContextGuard, proposal_id: str, *, value=_UNSET) -> dict:
        """Explicitly accept one reviewed value using an atomic revision check.

        A ``needs_review`` proposal requires ``value``. Values must exactly
        equal a canonical candidate actually mentioned in the source request.
        Unknown IDs, different guards, or stale state cannot write a fact.
        """
        if (not isinstance(guard, ContextGuard) or guard.relay is not self._relay
                or guard.session_id != self._session_id):
            raise ValueError("Correction review must be confirmed using its originating ContextGuard and session.")
        if type(proposal_id) is not str:
            raise ValueError("proposal_id must identify a proposal in this review.")
        proposal = next((item for item in self._proposals if item["id"] == proposal_id), None)
        if proposal is None:
            raise ValueError("Unknown correction proposal_id.")
        if value is _UNSET:
            if proposal["status"] != "suggested":
                raise ValueError("This correction needs review; supply an explicitly selected candidate value.")
            selected = proposal["value"]
        else:
            selected = value
        if type(selected) is not str or selected not in proposal["candidates"]:
            raise ValueError("Confirmed value must be a canonical candidate from this proposal.")
        provenance = {
            "kind": "confirmed_correction", "proposal_id": proposal["id"],
            "history_id": self.history_id, "reviewed_revision": self.revision,
            "status_at_review": proposal["status"], "evidence": proposal["evidence"],
            "selected_value": selected,
        }
        return guard.remember(proposal["key"], selected, source="application-confirmed-correction",
                              text=encode(provenance), expected_revision=self.revision)


def review_corrections(prepared: PreparedTurn,
                       fields: tuple[CorrectionField, ...] | list[CorrectionField]) -> CorrectionReview:
    """Look only at the latest original user message, without changing state.

    Narrow complete imperatives can produce ``suggested`` proposals. Other
    correction-like mentions are ``needs_review``. A schema is mandatory; the
    recogniser does not invent keys or values and does not inspect assistant
    suggestions, synthetic context packets, or earlier user turns as changes.
    """
    if not isinstance(prepared, PreparedTurn) or prepared._archive is None:
        raise ValueError("prepared must come from ContextGuard.prepare() with an original evidence archive.")
    if type(fields) not in (tuple, list) or any(not isinstance(item, CorrectionField) for item in fields):
        raise ValueError("fields must be a tuple or list of CorrectionField objects.")
    captured_fields = tuple(fields)
    if len({field.key.casefold() for field in captured_fields}) != len(captured_fields):
        raise ValueError("Correction field keys must be unique ignoring case.")
    history = prepared._archive.messages
    index = len(history) - 1
    pending_user = history[index]["role"] == "user"
    # Native tool protocols can finish on a tool result. That result is not a
    # user correction, and neither is the earlier request already in progress.
    text = history[index]["content"] if pending_user else ""
    facts = prepared.context["facts"]
    # Only the immutable original pending USER request is examined. Generated
    # context metadata, tool results, and assistant content never enter detection.
    mentions = {field.key: [choice for choice in field.choices if _contains(text, choice)]
                for field in captured_fields}
    named = {field.key for field in captured_fields if any(_contains(text, name) for name in _names(field))}
    correction_like = re.search(r"\b(?:use|change|set|switch|replace|instead|correction)\b", text, re.I) is not None
    candidates = [field for field in captured_fields
                  if correction_like and mentions[field.key] and (not named or field.key in named)]
    proposals = []
    skipped = []
    cautions = _cautions(text)
    for field in candidates:
        direct = _direct(text, field)
        reasons = list(cautions)
        if len(candidates) > 1:
            reasons.append("multiple_candidate_fields")
        # Once a direct correction is accepted, retrying that same request must
        # not require confirmation again just because its old value is now stale.
        # A mention of only the already-recorded candidate cannot change state.
        if field.key in facts and (
            (not reasons and direct is not None and strict_equal(facts[field.key], direct[0]))
            or all(strict_equal(facts[field.key], choice) for choice in mentions[field.key])
        ):
            skipped.append({"key": field.key, "reason": "already_current"})
            continue
        if direct is None:
            reasons.append("unsupported_or_ambiguous_expression")
        elif direct[1] is not None and field.key in facts and not strict_equal(facts[field.key], direct[1]):
            reasons.append("previous_value_differs_from_recorded_state")
        if direct is not None and direct[0] == direct[1]:
            reasons.append("replacement_values_are_identical")
        status = "needs_review" if reasons else "suggested"
        proposal = {
            "key": field.key, "status": status,
            "value": direct[0] if status == "suggested" else None,
            "candidates": mentions[field.key], "current_present": field.key in facts,
            "current_value": facts.get(field.key),
            "reasons": reasons or ["recognised_explicit_imperative"],
            "evidence": {"history_id": prepared.history_id, "index": index,
                         "role": "user", "text": text, "quote": text, "start": 0, "end": len(text)},
        }
        identity = {"session_id": prepared._session_id, "state_revision": prepared.revision,
                    "proposal": proposal}
        proposal["id"] = hashlib.sha256(encode(identity).encode("utf-8")).hexdigest()
        proposals.append(proposal)
    report = {
        "history_id": prepared.history_id, "state_revision": prepared.revision,
        "message_index": index, "method": "schema_guided_patterns", "current_authority": False,
        "inspected_user_message": pending_user,
        "proposal_count": len(proposals),
        "suggested_count": sum(item["status"] == "suggested" for item in proposals),
        "needs_review_count": sum(item["status"] == "needs_review" for item in proposals),
        "writes_performed": 0, "skipped": skipped,
    }
    return CorrectionReview(prepared, proposals, report)
