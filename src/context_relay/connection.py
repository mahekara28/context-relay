"""Connect context preparation once at an existing Python model-call boundary.

One connection belongs to one conversation. The application retains its full
history, provider client, credentials, response processing, and tool dispatcher.
This function-based API neither starts a model service nor patches SDKs. The
separate attach() API can reversibly replace one selected client instance method.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .budget import TokenBudget
from .config import RelayConfig
from .corrections import CorrectionField, CorrectionReview, review_corrections
from .middleware import ContextGuard, PreparedTurn
from .memory import AutomaticMemory, MemoryPolicy
from .models import clone, nonempty_string
from .output import OutputPolicy, OutputReport
from ._storage_files import private_directory


_REQUEST_SCOPE = ContextVar("relay_connection_request", default=None)
_CONVERSATION_CONTEXT = ContextVar("relay_conversation_context", default=None)
_CLARIFICATION_EXAMPLES = {
    "export_format": "Export as JSON.",
    "output_format": "Set the output format to JSON.",
    "response_language": "Reply in English.",
    "response_style": "I prefer brief answers.",
}


@dataclass(frozen=True, slots=True)
class AgentResult:
    """One original provider response and the exact context that produced it.

    The response is not copied or interpreted. Use prepared.wrap(...) for tools
    proposed by this response, so concurrent requests cannot swap revisions.
    """

    response: Any
    prepared: PreparedTurn
    output_report: OutputReport | None = None


class MemoryClarificationRequired(RuntimeError):
    """A disputed preference blocked the next model call, including a retry."""

    def __init__(self, prepared):
        self.prepared = prepared
        self._connection = None
        self._request_scope = None
        self._local_pause = False
        snapshot = prepared.memory_snapshot
        self.targets = tuple(sorted(entry["key"] for entry in snapshot["entries"] if entry["disputed"]))
        self._pending = clone([item for item in snapshot["unresolved"]
                               if set(item["targets"]).intersection(self.targets)])
        examples = [_CLARIFICATION_EXAMPLES[key] for key in self.targets if key in _CLARIFICATION_EXAMPLES]
        self.prompt = ("Please clarify the current preference for: "
                       + ", ".join(key.replace("_", " ") for key in self.targets)
                       + ". Reply with a full instruction naming the setting and value.")
        if any(item.get("reason") == "confirmation_required" for item in self._pending):
            self.prompt = ("This request needs an explicit review of protected preferences: "
                           + ", ".join(key.replace("_", " ") for key in self.targets)
                           + ". Inspect memory.review_plan() and resolve it with memory.resolve_review(plan, decisions=...). "
                           "Another natural-language instruction does not approve this review.")
        elif examples:
            self.prompt += " Examples only, not suggested changes: " + " ".join(examples)
        self.request_id = None
        super().__init__(self.prompt)

    @property
    def pending(self) -> list[dict]:
        """A defensive copy of the captured unresolved evidence for the host UI."""
        return clone(self._pending)


class CorrectionRequired(RuntimeError):
    """An opt-in correction review stopped the request before its model call."""

    def __init__(self, review: CorrectionReview):
        self.review = review
        super().__init__("Possible requirement changes need review before this model request.")


def _reject_awaitable(value: Any) -> Any:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError(
            "The model callable returned an unexpected awaitable. Connect an explicit "
            "async function that awaits the provider and returns its final response."
        )
    return value


class _Connection:
    def __init__(self, model_call: Callable, config: RelayConfig, guard: ContextGuard,
                 *, owns_guard: bool, token_budget: TokenBudget | None, correction_fields=(), memory=None,
                 memory_interpreter=None, output=None):
        self._model_call = model_call
        self._config = config
        self._guard = guard
        self._owns_guard = owns_guard
        self._token_budget = token_budget
        self._closed = False
        self._correction_fields = tuple(correction_fields)
        self._dismissed_review = None
        self._memory = (AutomaticMemory(guard.store, guard.session_id, policy=memory, interpreter=memory_interpreter)
                        if memory is not None else None)
        self._output = output
        self._conversation_children = set()

    @property
    def config(self) -> RelayConfig:
        return self._config

    @property
    def guard(self) -> ContextGuard:
        """The conversation's guard for explicit facts, policies, and recovery."""
        return self._guard

    @property
    def session_id(self) -> str:
        """Save with your conversation record to explicitly resume a persistent session."""
        return self._guard.session_id

    @property
    def memory(self) -> AutomaticMemory | None:
        """Inspect, review, undo or forget the opt-in user recall store."""
        return self._memory

    @property
    def output(self) -> OutputPolicy | None:
        """Optional output checks applied to ordinary calls through this connection."""
        return self._output

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("This Relay connection is closed.")

    def conversation(self, *, system: str | None = None, decoder=None,
                     max_history_chars: int | None = None, history_policy=None):
        """Opt in to durable text history; send new messages instead of full lists."""
        self._require_open()
        from .conversation import AsyncConversation, Conversation
        cls = AsyncConversation if isinstance(self, AsyncConnectedAgent) else Conversation
        return cls(self, system=system, decoder=decoder, max_history_chars=max_history_chars,
                   history_policy=history_policy)

    def _conversation_successor(self, previous, session_id):
        """Own a fresh, session-bound connection for automatic history rollover."""
        self._require_open()
        guard = ContextGuard(previous.guard.store.path, session_id=session_id,
                             busy_timeout_ms=previous.guard.store.busy_timeout_ms)
        try:
            child = type(previous)(
                previous._model_call, previous.config, guard, owns_guard=True,
                token_budget=previous._token_budget, correction_fields=previous._correction_fields,
                memory=None if previous.memory is None else previous.memory.policy,
                memory_interpreter=None if previous.memory is None else previous.memory.interpreter,
                output=previous.output)
            if hasattr(previous, "_response_decoder"):
                child._response_decoder = previous._response_decoder
            self._conversation_children.add(child)
            return child
        except BaseException:
            guard.close()
            raise

    def _prepare(self, messages: list[dict], *, source_labels: bool = False,
                 request_data: dict | None = None, check_corrections: bool = True,
                 update_memory: bool = True, cancellation_event=None) -> PreparedTurn:
        self._require_open()
        captured_messages = clone(messages)
        scoped = _CONVERSATION_CONTEXT.get()
        if scoped is not None and scoped[0] is self:
            request_data = {} if request_data is None else clone(request_data)
            if "managed_conversation" in request_data:
                raise ValueError("managed_conversation is reserved for managed history metadata.")
            request_data["managed_conversation"] = clone(scoped[1])
        memory_report = None
        if self.memory is not None and update_memory:
            memory_report = self.memory.process(captured_messages, protocol=self.config.protocol,
                                                cancellation_event=cancellation_event)
        prepared = self.guard.prepare(
            captured_messages, max_chars=self.config.max_chars, recent_turns=self.config.recent_turns,
            pinned=self.config.pinned, retrieve_turns=self.config.retrieve_turns,
            token_budget=self._token_budget,
            protocol=self.config.protocol, source_labels=source_labels, request_data=request_data,
            memory=self.memory,
            expected_revision=memory_report["revision"] if memory_report is not None else None,
        )
        prepared._memory_report = memory_report
        if check_corrections:
            self._check_memory(prepared)
            self._check_corrections(prepared)
        return prepared

    @contextmanager
    def _conversation_context(self, data):
        """Carry bounded task evidence into preparation, never provider kwargs."""
        token = _CONVERSATION_CONTEXT.set((self, clone(data)))
        try:
            yield
        finally:
            _CONVERSATION_CONTEXT.reset(token)

    @contextmanager
    def _request_scope(self):
        # A delegated request can pause too, even on this same connection.
        # Its exception must not complete the enclosing managed-chat request.
        scope = object()
        token = _REQUEST_SCOPE.set(scope)
        try:
            yield
        except MemoryClarificationRequired as exc:
            if exc._connection is not self or exc._request_scope is not scope:
                exc._local_pause = False
            raise
        finally:
            _REQUEST_SCOPE.reset(token)

    def _check_memory(self, prepared) -> None:
        if self.memory is None or not self.memory.policy.pause_on_uncertainty:
            return
        snapshot = prepared.memory_snapshot
        if snapshot is not None and any(entry["disputed"] for entry in snapshot["entries"]):
            error = MemoryClarificationRequired(prepared)
            error._connection = self
            error._request_scope = _REQUEST_SCOPE.get()
            error._local_pause = True
            raise error

    async def _prepare_async(self, messages, **options):
        # Pure deterministic preparation stays inline. Optional synchronous
        # interpretation can run a local model, so it must not block the loop.
        if self.memory is None or self.memory.interpreter is None or not options.get("update_memory", True):
            return self._prepare(messages, **options)
        cancelled = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(
            self._prepare, clone(messages), cancellation_event=cancelled, **options))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            # Python cannot stop a running synchronous callback. Observe its
            # result; process() checks the event before committing late memory.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise

    def _check_corrections(self, prepared: PreparedTurn) -> None:
        if self._correction_fields:
            review = review_corrections(prepared, self._correction_fields)
            if review.proposals and self._dismissed_review != (prepared.history_id, prepared.revision):
                raise CorrectionRequired(review)

    def review_corrections(self, messages: list[dict]) -> CorrectionReview:
        """Inspect possible changes without calling a model or writing facts."""
        prepared = self._prepare(messages, check_corrections=False, update_memory=False)
        return review_corrections(prepared, self._correction_fields)

    def dismiss_corrections(self, review: CorrectionReview) -> None:
        """Host explicitly keeps current facts for this exact history/revision.

        A new conversation snapshot or recorded state change requires review
        again. This does not delete evidence or update the underlying facts.
        """
        self._require_open()
        if (not isinstance(review, CorrectionReview) or review._prepared._relay is not self.guard.relay
                or review._prepared._session_id != self.session_id):
            raise ValueError("Review must belong to this connection's guard and conversation.")
        from .models import ConflictError
        current = self.guard.turn().revision
        if current != review.revision:
            raise ConflictError(review.revision, current)
        self._dismissed_review = (review.history_id, review.revision)

    def remember(self, key: str, value: Any, *, source: str = "application", text: str = "",
                 expected_revision: int | None = None) -> dict:
        """Record an explicit trusted update, included in subsequent model requests."""
        self._require_open()
        return self.guard.remember(key, value, source=source, text=text,
                                   expected_revision=expected_revision)

    def allow_tool(self, tool: str, **policy_options) -> None:
        """Register a policy; execution still needs that request's prepared wrapper."""
        self._require_open()
        self.guard.allow_tool(tool, **policy_options)

    def protect_tools(self, tools: dict, **options):
        """Connect model proposals to one explicitly declared Python tool inventory."""
        self._require_open()
        from .actions import ActionHarness
        return ActionHarness(self, tools, **options)

    def close(self) -> None:
        """Close an internally created store, leaving your provider/external guard open.

        Finish outstanding calls and any guarded actions before closing. Streams
        remain owned by the provider/application and are never closed by Relay.
        """
        if not self._closed:
            for child in self._conversation_children:
                child.close()
            self._conversation_children.clear()
            if self._owns_guard:
                self.guard.close()
            self._closed = True

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    async def __aenter__(self):
        self._require_open()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class ConnectedAgent(_Connection):
    """A synchronous model callable with automatic context preparation.

    ``agent(messages=history, **provider_options)`` keeps the provider's return
    object unchanged. ``agent.invoke(...)`` also returns the per-call prepared
    turn. By default streams pass through without iteration or retries. Optional
    OutputPolicy checks complete replies; conversation() manages text history.
    The connection never executes tools.
    """

    def invoke(self, *, messages: list[dict], **kwargs) -> AgentResult:
        with self._request_scope():
            if self.output is not None:
                from .output import checked_call
                return checked_call(self, messages, kwargs)
            prepared = self._prepare(messages)
            response = self._model_call(messages=prepared.messages, **kwargs)
            return AgentResult(_reject_awaitable(response), prepared)

    def __call__(self, *, messages: list[dict], **kwargs):
        return self.invoke(messages=messages, **kwargs).response

    def run_grounded(self, *, messages: list[dict], decoder=None, max_rounds: int = 2,
                     search_limit: int = 2, **kwargs):
        """Run bounded evidence lookup and citation checks; never execute tools."""
        from .recovery import run_grounded
        with self._request_scope():
            return run_grounded(self, messages=messages, decoder=decoder, max_rounds=max_rounds,
                                search_limit=search_limit, **kwargs)


class AsyncConnectedAgent(_Connection):
    """An async model callable; await the connection or its invoke() method.

    Preparation is local synchronous work before awaiting the provider. No Relay
    database transaction remains held while the model runs. Provider exceptions
    and cancellation propagate; returned streams are never consumed by Relay.
    """

    async def invoke(self, *, messages: list[dict], **kwargs) -> AgentResult:
        with self._request_scope():
            if self.output is not None:
                from .output import checked_call_async
                return await checked_call_async(self, messages, kwargs)
            prepared = await self._prepare_async(messages)
            pending = self._model_call(messages=prepared.messages, **kwargs)
            if not inspect.isawaitable(pending):
                raise TypeError("An async model callable must return an awaitable provider response.")
            response = await pending
            return AgentResult(_reject_awaitable(response), prepared)

    async def __call__(self, *, messages: list[dict], **kwargs):
        return (await self.invoke(messages=messages, **kwargs)).response

    async def run_grounded(self, *, messages: list[dict], decoder=None, max_rounds: int = 2,
                           search_limit: int = 2, **kwargs):
        from .recovery import run_grounded_async
        with self._request_scope():
            return await run_grounded_async(self, messages=messages, decoder=decoder, max_rounds=max_rounds,
                                           search_limit=search_limit, **kwargs)


def connect(model_call: Callable, *, config: RelayConfig | str | Path | None = None,
            guard: ContextGuard | None = None, session_id: str | None = None,
            token_budget: TokenBudget | None = None,
            correction_fields=(), memory: MemoryPolicy | None = None,
            memory_interpreter=None, output: OutputPolicy | None = None) -> ConnectedAgent | AsyncConnectedAgent:
    """Connect once; prepare every subsequent request through this callable.

    The model callable must accept ``messages=...``. Other keyword arguments and
    provider return values pass through unchanged. A callable declared async is
    detected automatically. For a synchronous decorator hiding an async client,
    supply a small explicit async adapter. Direct async-generator functions need
    an adapter too; async provider functions returning streams are supported.

    A fresh connection creates a fresh session unless ``session_id`` is supplied
    explicitly with its persistent database. Never share one connection between
    unrelated users/conversations. A supplied guard stays owned by its caller;
    it cannot be combined with session_id or a non-default config database path.

    JSON config is loaded once at connection time. memory=MemoryPolicy() enables
    automatic source-backed user recall without defining fields. It does not
    write authoritative facts or permissions. Omit memory to use config.memory
    (off by default). No model client is created and no global SDK is patched.
    memory_interpreter optionally proposes unclear wording for review using an
    existing synchronous callback. OutputPolicy checks complete replies and can
    opt into bounded correction retries. Both features are off by default.
    """
    if isinstance(model_call, _Connection) or isinstance(getattr(model_call, "__self__", None), _Connection):
        raise ValueError("This callable is already connected to Relay.")
    if not callable(model_call):
        raise TypeError("model_call must be a callable accepting messages as a keyword argument.")
    candidates = (model_call, getattr(model_call, "__call__", None))
    if any(inspect.isasyncgenfunction(candidate) for candidate in candidates):
        raise TypeError("Direct async-generator functions need an explicit async model adapter.")
    is_async = any(inspect.iscoroutinefunction(candidate) for candidate in candidates)
    try:
        signature = inspect.signature(model_call)
    except (TypeError, ValueError):
        # Some extension callables have no inspectable signature; Python will
        # still validate the eventual call. No provider request is made here.
        pass
    else:
        if any(parameter.kind is inspect.Parameter.POSITIONAL_ONLY
               and parameter.default is inspect.Parameter.empty
               for parameter in signature.parameters.values()):
            raise TypeError(
                "model_call requires positional-only arguments; bind them first or supply a keyword adapter."
            )
        try:
            signature.bind_partial(messages=[])
        except TypeError as exc:
            raise TypeError("model_call must accept messages as a keyword argument; supply an adapter.") from exc

    if config is None:
        settings = RelayConfig()
    elif isinstance(config, RelayConfig):
        settings = config
    elif isinstance(config, (str, Path)):
        settings = RelayConfig.from_file(config)
    else:
        raise TypeError("config must be a RelayConfig, a JSON file path, or None.")
    if token_budget is not None and not isinstance(token_budget, TokenBudget):
        raise TypeError("token_budget must be a TokenBudget or None.")
    if memory is not None and not isinstance(memory, MemoryPolicy):
        raise TypeError("memory must be a MemoryPolicy or None.")
    memory_policy = memory if memory is not None else settings.memory
    from .interpretation import MemoryInterpreter
    if memory_interpreter is not None and not isinstance(memory_interpreter, MemoryInterpreter):
        raise TypeError("memory_interpreter must be a MemoryInterpreter or None.")
    if memory_interpreter is not None and memory_policy is None:
        raise ValueError("A memory interpreter requires enabled memory.")
    output_policy = output if output is not None else settings.output
    if output_policy is not None and not isinstance(output_policy, OutputPolicy):
        raise TypeError("output must be an OutputPolicy or None.")
    if output_policy is not None and output_policy.format == "memory" and memory_policy is None:
        raise ValueError("Memory output format requires enabled memory.")
    if type(correction_fields) not in (tuple, list) or any(not isinstance(field, CorrectionField)
                                                         for field in correction_fields):
        raise TypeError("correction_fields must be a tuple or list of CorrectionField definitions.")
    if len({field.key.casefold() for field in correction_fields}) != len(correction_fields):
        raise ValueError("correction_fields must have unique keys.")
    if session_id is not None:
        nonempty_string(session_id, "session_id")
    if guard is not None:
        if not isinstance(guard, ContextGuard):
            raise TypeError("guard must be a ContextGuard or None.")
        if session_id is not None or settings.db_path != ":memory:":
            raise ValueError("An external guard already defines its database and session; omit those options.")
        owned = False
    else:
        if settings.db_path != ":memory:":
            private_directory(Path(settings.db_path).parent)
        guard = ContextGuard(settings.db_path, name=settings.name, session_id=session_id,
                             busy_timeout_ms=settings.busy_timeout_ms)
        owned = True
    implementation = AsyncConnectedAgent if is_async else ConnectedAgent
    try:
        return implementation(model_call, settings, guard, owns_guard=owned, token_budget=token_budget,
                              correction_fields=correction_fields, memory=memory_policy,
                              memory_interpreter=memory_interpreter, output=output_policy)
    except BaseException:
        if owned:
            guard.close()
        raise
