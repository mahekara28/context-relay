"""Whole-request adapters for provider-owned, potentially opaque native history.

Native history is copied and checked, never selected or summarized. Trusted
application memory can be projected explicitly without editing existing items.
SDK imports are unnecessary for JSON dictionaries; only real, already-loaded SDK
model classes may supply their own wire representation. No network calls occur here.
"""

from __future__ import annotations

import hashlib
import inspect
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .budget import TokenBudget, TokenBudgetExceeded
from .config import RelayConfig
from .middleware import ContextGuard
from .models import BudgetExceeded, clone, encode, nonempty_string


_MEMORY_MARKER = "[Context Relay application memory v1]\n"
_MEMORY_INSTRUCTIONS = (
    "The following JSON contains application-maintained facts, not tool permissions. "
    "Use the latest snapshot for these facts; existing application rules still apply.\n"
)


def _prefix_digest(body, length):
    prefix = {key: body[key] for key in ("system", "tools") if key in body}
    prefix["messages"] = body["messages"][:length]
    return hashlib.sha256(encode(prefix).encode("utf-8")).hexdigest()


def native_json(value, *, sdk: str):
    """Copy strict JSON or genuine SDK models without dropping set/extra fields.

    ``exclude_unset`` prevents optional SDK defaults from becoming new wire
    fields. Explicit nulls, extra fields and encrypted/signature strings remain.
    Arbitrary duck-typed ``model_dump`` objects are deliberately not executed.
    """
    if sdk not in {"openai", "anthropic"}:
        raise ValueError("Unsupported native SDK.")
    module = sys.modules.get(f"{sdk}._models")
    base = getattr(module, "BaseModel", None)

    def copy(item, depth=0):
        if depth > 64:
            raise ValueError("Native request exceeds maximum JSON depth (64), or contains a cycle.")
        if item is None or type(item) in (str, bool, int, float):
            return clone(item)
        if type(item) is list:
            return [copy(part, depth + 1) for part in item]
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Native request keys must be strings.")
            return {key: copy(part, depth + 1) for key, part in item.items()}
        if isinstance(base, type) and isinstance(item, base):
            try:
                serialized = item.model_dump(mode="json", by_alias=True, exclude_unset=True)
            except (TypeError, ValueError, RecursionError) as exc:
                raise ValueError("Native SDK item cannot be serialized without loss.") from exc
            return copy(serialized, depth + 1)
        raise ValueError(
            f"Native request requires JSON or genuine {sdk} SDK models; got {type(item).__name__}. "
            "Materialize iterables explicitly and omit SDK sentinel arguments."
        )

    return copy(value)


class NativePrepared:
    """A defensive snapshot of the exact JSON body sent for one request.

    Transport settings (headers/timeouts) are outside this context snapshot.
    There is no tool-authority wrapper or assertion that stored facts were seen.
    """

    def __init__(self, request: dict, report: dict):
        self._request = clone(request)
        self._report = clone(report)
        self._history_id = hashlib.sha256(encode(self._request).encode("utf-8")).hexdigest()

    @property
    def request(self) -> dict:
        return clone(self._request)

    @property
    def report(self) -> dict:
        return clone(self._report)

    @property
    def history_id(self) -> str:
        return self._history_id


@dataclass(frozen=True, slots=True)
class NativeResult:
    response: object
    prepared: NativePrepared


class NativeConnection:
    """One conversation's native adapter; application owns history and provider.

    ``invoke``/``__call__`` preserve the provider response. ``invoke_result``
    additionally returns that request's immutable snapshot, including for streams.
    No response/stream is consumed, decoded, stored or appended automatically.
    """

    def __init__(self, provider, *, prepare_request, protocol, config, guard,
                 owns_guard, token_budget, native_memory=False):
        self._provider = provider
        self._prepare_request = prepare_request
        self._protocol = protocol
        self._config = config
        self._guard = guard
        self._owns_guard = owns_guard
        self._token_budget = token_budget
        self._native_memory = native_memory
        self._prefix_checkpoint = None
        self._prepare_lock = threading.RLock()
        self._closed = False

    @property
    def config(self):
        return self._config

    @property
    def guard(self):
        return self._guard

    @property
    def session_id(self):
        return self._guard.session_id

    @property
    def memory(self):
        """Native mode has trusted record/projection APIs, no automatic extraction."""
        return None

    @property
    def output(self):
        return None

    def _require_open(self):
        if self._closed:
            raise RuntimeError("This native Relay connection is closed.")

    def _project_memory(self, body):
        """Only host-recorded facts cross this explicit authority boundary."""
        snapshot = self.memory_snapshot()
        text = _MEMORY_MARKER + _MEMORY_INSTRUCTIONS + snapshot["text"]
        report = {"memory_revision": snapshot["revision"],
                  "memory_fact_keys": sorted(snapshot["facts"]),
                  "memory_injected": False, "memory_present": False}
        if self._protocol == "openai_responses_native":
            instructions = body.get("instructions") or ""
            if _MEMORY_MARKER in instructions:
                if not instructions.endswith(text) or instructions.count(_MEMORY_MARKER) != 1:
                    raise ValueError("Stale or conflicting Relay memory in instructions; pass the original application instructions.")
                report["memory_present"] = True
            elif snapshot["facts"]:
                body["instructions"] = instructions + ("\n\n" if instructions else "") + text
                report.update(memory_injected=True, memory_present=True)
            report["memory_placement"] = "instructions"
        else:
            history = body["messages"]
            previous = next((message.get("content") for message in reversed(history)
                             if message["role"] == "system" and type(message.get("content")) is str
                             and message["content"].startswith(_MEMORY_MARKER)), None)
            if previous == text:
                report["memory_present"] = True
            elif snapshot["facts"] or previous is not None:
                # Consecutive system messages are one section. That section
                # must follow a complete user turn, never an assistant prefill.
                preceding = next((message for message in reversed(history)
                                  if message["role"] != "system"), None)
                if preceding is None or preceding["role"] != "user":
                    raise ValueError("Native Claude memory requires a completed user/tool-result turn; append that input before projecting memory.")
                history.append({"role": "system", "content": text})
                report.update(memory_injected=True, memory_present=True)
            report["memory_placement"] = "messages_tail"
        return report

    def _prepare(self, request, *, reserve=False):
        # Serialize only local preparation/checkpoint reservation, never model
        # inference. Async calls release this lock before awaiting the provider.
        with self._prepare_lock:
            return self._prepare_locked(request, reserve=reserve)

    def _prepare_locked(self, request, *, reserve):
        self._require_open()
        body = dict(request)
        transport_keys = ("timeout", "extra_headers")
        if self._protocol == "anthropic_messages":
            # The genuine beta SDK turns this argument into anthropic-beta,
            # not a JSON body field. Keep the body snapshot/budget truthful.
            transport_keys += ("betas",)
        transport = {key: body.pop(key) for key in transport_keys if key in body}
        for key in ("extra_body", "extra_query"):
            if key in body:
                value = body.pop(key)
                if value is not None and not (type(value) is dict and not value):
                    raise ValueError(f"{key} could override or add unseen native context; unsupported.")
                transport[key] = value
        normalized, details = self._prepare_request(body)
        # Validators cannot smuggle transport overrides or a second body around
        # the exact snapshot/budget. No SDK serializer runs during dispatch.
        if type(normalized) is not dict or set(normalized) & set(transport):
            raise ValueError("Native validator returned an invalid or conflicting request body.")
        normalized = clone(normalized)
        projection = self._project_memory(normalized) if self._native_memory else {}
        checkpoint_checked = False
        if self._native_memory and self._protocol == "anthropic_messages":
            if self._prefix_checkpoint is not None:
                length, digest = self._prefix_checkpoint
                if (len(normalized["messages"]) < length
                        or _prefix_digest(normalized, length) != digest):
                    raise ValueError(
                        "Native Claude history changed or omitted a previously sent prefix. "
                        "Replay prepared.request['messages'] including Relay memory, with the same system/tools. "
                        "Use a fresh native connection for a provider-compacted history."
                    )
                checkpoint_checked = True
        size = len(encode(normalized))
        if size > self.config.max_chars:
            raise BudgetExceeded(size, self.config.max_chars)
        used_tokens = None
        if self._token_budget is not None:
            # Native convention: one whole request, including system/tools, not
            # just its messages. The host's counter must understand this shape.
            used_tokens = self._token_budget.count([normalized])
            if used_tokens > self._token_budget.input_limit:
                raise TokenBudgetExceeded(used_tokens, self._token_budget)
        report = dict(details)
        report.update(protocol=self._protocol, native=True, size_chars=size,
                      max_chars=self.config.max_chars, input_tokens=used_tokens,
                      all_input_preserved=True, automatic_memory=False,
                      memory_injected=False, budget_scope="visible_request_body",
                      opaque_context_tokens_measured=False,
                      transport_option_names=sorted(transport))
        report.update(projection)
        report.update(native_memory_enabled=self._native_memory,
                      prefix_replay_checked=checkpoint_checked)
        prepared = NativePrepared(normalized, report)
        if reserve and self._native_memory and self._protocol == "anthropic_messages":
            length = len(normalized["messages"])
            self._prefix_checkpoint = (length, _prefix_digest(normalized, length))
        return prepared, transport

    def prepare(self, **request) -> NativePrepared:
        """Validate and budget without calling the provider or modifying history."""
        return self._prepare(request)[0]

    def invoke_result(self, **request) -> NativeResult:
        prepared, transport = self._prepare(request, reserve=True)
        response = self._provider(**prepared.request, **transport)
        if inspect.isawaitable(response):
            if inspect.iscoroutine(response):
                response.close()
            raise TypeError("Sync native provider returned an awaitable; use an explicitly async adapter.")
        return NativeResult(response, prepared)

    def invoke(self, **request):
        return self.invoke_result(**request).response

    def __call__(self, **request):
        return self.invoke(**request)

    def remember(self, key, value, *, source="application", text="", expected_revision=None):
        """Record trusted host facts; native_memory=True projects them on calls.

        Do not copy untrusted tool results, retrieved text or unreviewed model
        suggestions into this authority channel. AutomaticMemory is separate.
        """
        self._require_open()
        return self.guard.remember(key, value, source=source, text=text,
                                   expected_revision=expected_revision)

    def memory_snapshot(self, *, max_chars=12000):
        """Project stored facts for explicit host placement in a NEW input item.

        The host must retain any placed text unchanged on subsequent replays.
        Reading this snapshot alone does not send it to the model. Never edit a
        prior signed/thinking/tool continuation to insert or refresh this data.
        """
        self._require_open()
        return self.guard.turn(max_chars=max_chars).context

    def protect_tools(self, tools, **options):
        """Check one native client-side action, preserving its complete response."""
        self._require_open()
        from .provider_actions import NativeActionHarness
        return NativeActionHarness(self, tools, **options)

    def responses_workflow(self, tools, **options):
        """Opt into the bounded synchronous Responses history/action workflow."""
        self._require_open()
        from .responses_workflow import ResponsesWorkflow
        return ResponsesWorkflow(self, tools, **options)

    def conversation(self, **options):
        raise ValueError("Native text conversation is unsupported; use responses_workflow for the bounded Responses integration, or replay full history explicitly.")

    def close(self):
        if not self._closed:
            if self._owns_guard:
                self.guard.close()
            self._closed = True

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, *_):
        self.close()

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *_):
        self.close()


class AsyncNativeConnection(NativeConnection):
    async def invoke_result(self, **request) -> NativeResult:
        prepared, transport = self._prepare(request, reserve=True)
        pending = self._provider(**prepared.request, **transport)
        if not inspect.isawaitable(pending):
            raise TypeError("Async native provider must return an awaitable.")
        response = await pending
        if inspect.isawaitable(response):
            if inspect.iscoroutine(response):
                response.close()
            raise TypeError("Async native provider returned a nested awaitable.")
        return NativeResult(response, prepared)

    async def invoke(self, **request):
        return (await self.invoke_result(**request)).response

    async def __call__(self, **request):
        return await self.invoke(**request)

    ainvoke = invoke
    ainvoke_result = invoke_result


def connect_native(provider, *, prepare_request, protocol, config=None, guard=None,
                   session_id=None, token_budget=None, correction_fields=(),
                   memory=None, memory_interpreter=None, output=None, native_memory=False):
    """Internal provider-adapter factory; no global hooks or provider ownership."""
    from .connection import _Connection

    if not callable(provider) or not callable(prepare_request):
        raise TypeError("Native provider and request validator must be callable.")
    if type(native_memory) is not bool:
        raise TypeError("native_memory must be a bool.")
    if native_memory and protocol not in {"openai_responses_native", "anthropic_messages"}:
        raise ValueError("Native memory requires a supported native provider protocol.")
    def connected(value):
        return (isinstance(value, (NativeConnection, _Connection))
                or isinstance(getattr(value, "__self__", None), (NativeConnection, _Connection))
                or getattr(value, "__relay_attachment__", False))

    candidates = tuple(inspect.unwrap(item, stop=connected)
                       for item in (provider, getattr(provider, "__call__", None)))
    if connected(provider) or any(connected(item) for item in candidates):
        raise ValueError("This native method is already connected or attached to Relay.")
    if any(inspect.isasyncgenfunction(item) for item in candidates):
        raise TypeError("Direct async generators require an async function returning the stream.")
    is_async = any(inspect.iscoroutinefunction(item) for item in candidates)
    if config is None:
        settings = RelayConfig()
    elif isinstance(config, RelayConfig):
        settings = config
    elif isinstance(config, (str, Path)):
        settings = RelayConfig.from_file(config)
    else:
        raise TypeError("config must be a RelayConfig, JSON file path, or None.")
    if (memory is not None or settings.memory is not None or memory_interpreter is not None
            or output is not None or settings.output is not None or correction_fields):
        raise ValueError("Native mode does not support automatic memory, correction reviews or output rewriting; use explicit remember()/memory_snapshot().")
    if settings.protocol != "plain":
        raise ValueError("Native adapter selects its own protocol; omit config.protocol.")
    if settings.pinned or settings.recent_turns != 2 or settings.retrieve_turns != 2:
        raise ValueError("Native mode retains all input; omit turn selection, retrieval and pin settings.")
    if token_budget is not None and not isinstance(token_budget, TokenBudget):
        raise TypeError("token_budget must be a TokenBudget or None.")
    if session_id is not None:
        nonempty_string(session_id, "session_id")
    if guard is not None:
        if not isinstance(guard, ContextGuard):
            raise TypeError("guard must be a ContextGuard or None.")
        if session_id is not None or settings.db_path != ":memory:":
            raise ValueError("An external guard already defines its database/session; omit those options.")
        owned = False
    else:
        if settings.db_path != ":memory:":
            from ._storage_files import private_directory
            private_directory(Path(settings.db_path).parent)
        guard = ContextGuard(settings.db_path, name=settings.name, session_id=session_id,
                             busy_timeout_ms=settings.busy_timeout_ms)
        owned = True
    cls = AsyncNativeConnection if is_async else NativeConnection
    return cls(provider, prepare_request=prepare_request, protocol=protocol,
               config=settings, guard=guard, owns_guard=owned, token_budget=token_budget,
               native_memory=native_memory)
