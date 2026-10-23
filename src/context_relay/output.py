"""Opt-in deterministic output contracts; no judge model or automatic tool action."""

from __future__ import annotations

import csv
import inspect
import io
import json
import math
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, fields
from decimal import Decimal, DecimalException

from .models import clone
from .content import ContentCheck, ContentPolicy
from .verification import VerificationCheck, VerificationPolicy


@dataclass(frozen=True, slots=True)
class OutputPolicy:
    """Check complete text, JSON objects or CSV with an explicit header.

    ``format='memory'`` follows one unambiguous remembered JSON/CSV preference.
    Retries are disabled by default; enabling them may make extra provider calls.
    Optional ``content`` compares declared JSON fields with an application-owned
    source snapshot. Checks do not establish truth or arbitrary semantic meaning.
    """

    format: str = "text"
    required_keys: tuple[str, ...] = ()
    csv_columns: tuple[str, ...] = ()
    max_chars: int = 64000
    max_words: int | None = None
    max_retries: int = 0
    memory_key: str = "export_format"
    decoder: object = None
    content: ContentPolicy | None = None
    verification: VerificationPolicy | None = None

    def __post_init__(self):
        if type(self.format) is not str or self.format not in {"text", "json", "csv", "memory"}:
            raise ValueError("Output format must be text, json, csv or memory.")
        for name in ("required_keys", "csv_columns"):
            values = getattr(self, name)
            if (type(values) not in (tuple, list) or len(values) > 128
                    or any(type(value) is not str or not value.strip() or len(value) > 200 for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError(f"{name} must contain at most 128 unique nonempty names of at most 200 characters.")
            object.__setattr__(self, name, tuple(values))
        if self.format == "csv" and not self.csv_columns:
            raise ValueError("CSV checking requires csv_columns; arbitrary prose is otherwise valid one-column CSV.")
        if self.required_keys and self.format not in {"json", "memory"}:
            raise ValueError("required_keys applies only to json or memory format.")
        if self.csv_columns and self.format not in {"csv", "memory"}:
            raise ValueError("csv_columns applies only to csv or memory format.")
        for name, low, high in (("max_chars", 1, 2000000), ("max_retries", 0, 2)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer from {low} to {high}.")
        if self.max_words is not None and (type(self.max_words) is not int or not 1 <= self.max_words <= 1000000):
            raise ValueError("max_words must be a positive integer at most 1000000, or None.")
        if type(self.memory_key) is not str or not self.memory_key.strip() or len(self.memory_key) > 80:
            raise ValueError("memory_key must be a nonempty string of at most 80 characters.")
        if type(self.content) is dict:
            object.__setattr__(self, "content", ContentPolicy.from_dict(self.content))
        if self.content is not None:
            if not isinstance(self.content, ContentPolicy):
                raise TypeError("content must be a ContentPolicy, serialized content policy, or None.")
            if self.format not in {"json", "memory"}:
                raise ValueError("Content output checking requires JSON; check structured tool arguments before CSV/text rendering.")
            self.content.argument_names  # JSON output contracts require an object reference.
        if type(self.verification) is dict:
            object.__setattr__(self, "verification", VerificationPolicy.from_dict(self.verification))
        if self.verification is not None:
            if not isinstance(self.verification, VerificationPolicy):
                raise TypeError("verification must be a VerificationPolicy, serialized policy, or None.")
            if self.format not in {"json", "memory"}:
                raise ValueError("Claim verification requires JSON output.")
        if self.decoder is not None and (not callable(self.decoder) or any(
            inspect.iscoroutinefunction(fn) or inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn)
            for fn in (self.decoder, getattr(self.decoder, "__call__", None))
        )):
            raise TypeError("Output decoder must synchronously return complete plain text.")

    def to_dict(self) -> dict:
        """Export data-only settings, including a lossless content snapshot."""
        if self.decoder is not None:
            raise ValueError("Callable decoders cannot be serialized as configuration.")
        data = {field.name: getattr(self, field.name) for field in fields(self) if field.name not in {"content", "verification"}}
        data["required_keys"], data["csv_columns"] = list(self.required_keys), list(self.csv_columns)
        data["content"] = self.content.to_dict() if self.content is not None else None
        data["verification"] = self.verification.to_dict() if self.verification is not None else None
        return clone(data)

    def validate_options(self, options: dict):
        if "stream" in options and options["stream"] is not False:
            raise ValueError("Output checking requires stream=False.")
        for key in ("tools", "tool_choice", "functions", "function_call"):
            if key in options and options[key] not in (None, [], "none", False):
                raise ValueError("Output checking accepts final text responses, not tool-enabled requests.")

    def _data(self, issues: tuple[str, ...]) -> dict:
        return {"output_contract": {
            "format": self.format, "memory_key": self.memory_key if self.format == "memory" else None,
            "required_keys": list(self.required_keys), "csv_columns": list(self.csv_columns),
            **({"protected_content": self.content.describe()} if self.content is not None else {}),
            **({"verified_claims": self.verification.describe()} if self.verification is not None else {}),
            "max_chars": self.max_chars, "max_words": self.max_words, "previous_failed_checks": list(issues),
            "instruction": "Return only the requested complete output. JSON must be an object with unique keys; CSV must match the given header and row widths. No code fences. For memory format, use the current undisputed memory value. Correct any previous failed checks.",
        }}


@dataclass(frozen=True, slots=True)
class OutputReport:
    format: str
    attempts: int
    checks: tuple[str, ...]
    revision: int
    semantic_meaning_checked: bool = False
    content: ContentCheck | None = None
    verification: VerificationCheck | None = None


class OutputRejected(ValueError):
    """A response failed a check; original response remains available to its owner."""

    def __init__(self, response, *, issues: tuple[str, ...], attempts: int, prepared,
                 content: ContentCheck | None = None, verification: VerificationCheck | None = None):
        self.response, self.issues = response, issues
        self.attempts, self.prepared, self.content = attempts, prepared, content
        self.verification = verification
        super().__init__("Output rejected: " + ", ".join(issues) + ".")


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key.")
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError("Non-finite JSON value.")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON number exceeds finite floating-point range.")
    return number


def _plain_text(response):
    if type(response) is str:
        return response
    if type(response) is dict:
        if response.get("done", True) is not True or response.get("done_reason") not in (None, "stop"):
            raise ValueError("Incomplete response.")
        message = response.get("message")
        if type(message) is dict and set(message) == {"role", "content"} and message["role"] == "assistant":
            return message["content"]
    raise TypeError("Supply an output decoder for this complete provider response.")


def _check_text(text: str, policy: OutputPolicy, format: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    checks = ["nonempty_text", "character_limit"]
    if not text.strip():
        return ("empty_output",), tuple(checks)
    if len(text) > policy.max_chars:
        return ("character_limit",), tuple(checks)
    issues = []
    if policy.max_words is not None:
        checks.append("whitespace_word_limit")
        if len(text.split()) > policy.max_words:
            issues.append("word_limit")
    if format == "json":
        checks.extend(("strict_json_object", "required_keys"))
        try:
            value = json.loads(text, object_pairs_hook=_json_object, parse_constant=_invalid_constant,
                               parse_float=Decimal if policy.content is not None or policy.verification is not None else _finite_float)
            if type(value) is not dict:
                raise ValueError("Expected a JSON object.")
        except (ValueError, RecursionError, DecimalException):
            issues.append("invalid_json_object")
        else:
            if not set(policy.required_keys) <= value.keys():
                issues.append("missing_required_keys")
    elif format == "csv":
        checks.extend(("csv_header", "csv_row_widths"))
        try:
            rows = csv.reader(io.StringIO(text, newline=""), strict=True)
            if next(rows, None) != list(policy.csv_columns):
                issues.append("csv_header")
            # Iterate bounded text; don't allocate a second full table.
            if any(len(row) != len(policy.csv_columns) for row in rows):
                issues.append("csv_row_widths")
        except csv.Error:
            issues.append("invalid_csv")
    return tuple(issues), tuple(checks)


class _OutputRun:
    """Shared sync/async state machine; retry feedback contains fixed codes only."""

    def __init__(self, agent, messages, options):
        self.agent, self.policy = agent, agent.output
        self.policy.validate_options(options)
        self.messages, self.options = clone(messages), options
        self.prepared = None
        self.revision = None
        self.format = self.policy.format
        self.issues = ()
        self.response, self.attempts = None, 0
        self.decoder = self.policy.decoder or getattr(agent, "_response_decoder", _plain_text)

    def _accept_prepared(self, prepared):
        self.prepared = prepared
        with self.agent.guard.store._transaction():
            current = self.agent.guard.store.get_session(self.agent.session_id)["revision"]
            if current != prepared.revision or self.revision is not None and current != self.revision:
                raise OutputRejected(self.response, issues=("state_changed",), attempts=self.attempts, prepared=prepared)
            if self.format == "memory":
                if self.agent.memory is None:
                    raise ValueError("Memory output format requires enabled automatic memory.")
                snapshot = self.agent.memory.context_snapshot()
                entry = next((item for item in snapshot["entries"] if item["key"] == self.policy.memory_key), None)
                if entry is None or entry.get("disputed"):
                    raise ValueError("The output format memory is missing or disputed; resolve it before generation.")
                self.format = entry["value"].casefold()
                if self.format not in {"json", "csv"}:
                    raise ValueError("Memory output checking supports JSON and CSV only.")
                if self.format == "csv" and not self.policy.csv_columns:
                    raise ValueError("Memory selected CSV; configure csv_columns for meaningful checking.")
            if self.policy.content is not None and self.format != "json":
                raise ValueError("Content output checking requires the resolved memory format to be JSON.")
            if self.policy.verification is not None:
                if self.format != "json":
                    raise ValueError("Claim verification requires the resolved format to be JSON.")
                readiness = self.policy.verification.readiness()
                if not readiness.valid:
                    raise OutputRejected(self.response, issues=("verification_unavailable",),
                                         attempts=self.attempts, prepared=prepared, verification=readiness)
            self.revision = current
        return prepared

    def prepare(self, attempt):
        return self._accept_prepared(self.agent._prepare(
            self.messages, request_data=self.policy._data(self.issues), update_memory=attempt == 0))

    async def prepare_async(self, attempt):
        return self._accept_prepared(await self.agent._prepare_async(
            self.messages, request_data=self.policy._data(self.issues), update_memory=attempt == 0))

    def check(self, response, attempt):
        self.response, self.attempts = response, attempt + 1
        if isinstance(response, (Iterator, AsyncIterator)) or inspect.isawaitable(response):
            if inspect.iscoroutine(response):
                response.close()
            raise OutputRejected(response, issues=("unsupported_response",), attempts=attempt + 1, prepared=self.prepared)
        try:
            text = self.decoder(response)
            if inspect.iscoroutine(text):
                text.close()
            if type(text) is not str:
                raise TypeError("Decoder must return text.")
        except Exception as exc:
            raise OutputRejected(response, issues=("unsupported_response",), attempts=attempt + 1, prepared=self.prepared) from exc
        self.issues, checks = _check_text(text, self.policy, self.format)
        content_check = verification_check = None
        if self.policy.content is not None and not self.issues:
            content_check = self.policy.content.check_json(text)
            checks += ("protected_content",)
            if not content_check.valid:
                self.issues = tuple(dict.fromkeys(item["code"] for item in content_check.report["issues"])) or ("content_mismatch",)
        if self.policy.verification is not None:
            # Source expiry during inference must stop retries too. A new model
            # response cannot repair unavailable application-owned evidence.
            verification_check = (self.policy.verification.check_json(text) if not self.issues
                                  else self.policy.verification.readiness())
            checks += ("verified_claims" if not self.issues else "verification_sources_ready",)
            if not verification_check.report["ready"]:
                raise OutputRejected(response, issues=("verification_unavailable",), attempts=attempt + 1,
                                     prepared=self.prepared, content=content_check, verification=verification_check)
            if not verification_check.valid:
                self.issues += ("verification_failed",)
        if self.agent.guard.store.get_session(self.agent.session_id)["revision"] != self.revision:
            raise OutputRejected(response, issues=("state_changed",), attempts=attempt + 1, prepared=self.prepared)
        if not self.issues:
            from .connection import AgentResult
            return AgentResult(response, self.prepared, OutputReport(self.format, attempt + 1, checks, self.revision, content=content_check, verification=verification_check))
        if attempt == self.policy.max_retries:
            raise OutputRejected(response, issues=self.issues, attempts=attempt + 1, prepared=self.prepared, content=content_check, verification=verification_check)
        return None


def checked_call(agent, messages, options):
    run = _OutputRun(agent, messages, options)
    for attempt in range(run.policy.max_retries + 1):
        prepared = run.prepare(attempt)
        result = run.check(agent._model_call(messages=prepared.messages, **options), attempt)
        if result is not None:
            return result


async def checked_call_async(agent, messages, options):
    run = _OutputRun(agent, messages, options)
    for attempt in range(run.policy.max_retries + 1):
        prepared = await run.prepare_async(attempt)
        pending = agent._model_call(messages=prepared.messages, **options)
        if not inspect.isawaitable(pending):
            raise TypeError("An async model callable must return an awaitable provider response.")
        result = run.check(await pending, attempt)
        if result is not None:
            return result
