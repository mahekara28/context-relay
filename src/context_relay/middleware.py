"""Install Relay around existing Python tools without a server or framework.

Capture a turn before requesting a model proposal. Wrap tools against that turn;
never silently refresh an old proposal's revision at execution time.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Iterable
from functools import wraps
from pathlib import Path

from .adapters import guarded_tool_call
from .budget import TokenBudget
from .context import ContextPlan, compile_context
from .engine import Relay
from .evidence import EvidenceArchive, RecoveredEvidence
from .models import ConflictError, clone, issue, nonempty_string, timestamp, validate_policy, validate_revision
from .store import RelayStore
from .content import ContentPolicy
from .verification import VerificationPolicy
from .outcomes import OutcomePolicy
from .action_repair import RepairScope, RepairUnavailable


class ActionRejected(RuntimeError):
    """An action was blocked or needs refreshed context; its tool did not run."""

    def __init__(self, verdict: dict):
        self.verdict = clone(verdict)
        self.decision = self.verdict["decision"]
        self.recovery_context = clone(self.verdict["recovery_context"])
        reasons = "; ".join(item["message"] for item in self.verdict["issues"])
        super().__init__(f"Tool {self.verdict['tool']!r}: {self.decision}. {reasons}")


def _sync_callable(function: Callable) -> None:
    if not callable(function):
        raise TypeError("Expected a callable Python tool or dispatcher.")
    candidates = (function, getattr(function, "__call__", None))
    if any(inspect.iscoroutinefunction(item) or inspect.isasyncgenfunction(item)
           or inspect.isgeneratorfunction(item) for item in candidates):
        raise TypeError("Relay wrappers require synchronous, non-generator tools. Native async tools are not supported.")


def _sync_result(value):
    # A sync decorator can conceal an async/generator implementation. Reject it
    # without awaiting or iterating it, and close fresh coroutine/generator values.
    if inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
        if inspect.iscoroutine(value) or inspect.isgenerator(value):
            value.close()
        raise TypeError("A wrapped tool must return a JSON-compatible result, not an awaitable or generator.")
    return value


_REPAIRABLE_ARGUMENT_ISSUES = frozenset({"binding_mismatch", "memory_binding_mismatch", "missing_argument",
                                       "content_mismatch", "verification_failed"})
_CLARIFICATION_ISSUES = frozenset({"memory_missing", "memory_disputed"})


def _rejection_status(check):
    codes = {row["code"] for row in check["issues"]}
    if (codes & _CLARIFICATION_ISSUES and codes <= _REPAIRABLE_ARGUMENT_ISSUES | _CLARIFICATION_ISSUES
            and ("verification_failed" not in codes or check.get("verification_check", {}).get("ready") is True)):
        return "clarification_required"
    return "blocked"


def _can_repair(check):
    codes = {row["code"] for row in check["issues"]}
    return (bool(codes) and codes <= _REPAIRABLE_ARGUMENT_ISSUES
            and ("verification_failed" not in codes or check.get("verification_check", {}).get("ready") is True))


class GuardedTurn:
    """One harness-owned snapshot for one model decision.

    Successful execution advances the stored revision. Capture a new turn and
    re-plan before another action. Results are JSON-compatible values; they are
    copied by the core. Function return object identity is not preserved.
    """

    def __init__(self, relay: Relay, session_id: str, context: dict):
        if type(context) is not dict or context.get("session_id") != session_id:
            raise ValueError("Context must belong to the turn's session.")
        self._relay = relay
        self._session_id = session_id
        self._context = clone(context)

    @property
    def context(self) -> dict:
        """A copy suitable for the model request; mutation cannot change the guard."""
        return clone(self._context)

    @property
    def context_text(self) -> str:
        return self._context["text"]

    @property
    def revision(self) -> int:
        return self._context["revision"]

    def _call(self, tool: str, arguments: dict, executor: Callable):
        result = guarded_tool_call(self._relay, self._session_id, self._context,
                                   {"tool": tool, "arguments": arguments}, executor)
        if not result["executed"]:
            raise ActionRejected(result["check"])
        return result["result"]

    def execute_with_recovery(self, tool: str, arguments: dict, executor: Callable,
                              *, repair: Callable | str | None = None) -> dict:
        """Execute once, with an optional bounded pre-execution repair.

        A synchronous ``repair(request)`` callback may change only failed bound
        arguments, failed verified claims and declared content scopes. Other data stays identical.
        Row rules permit selected field edits at existing indices only. Row
        counts, supplied identity keys and unprotected fields stay fixed there;
        keyless rows have no inferred identity. Every revised proposal is checked
        normally before dispatch. Repair remains disabled by default.

        ``repair="known"`` copies trusted binding/current undisputed memory
        values, failed computed claims and exact source subtrees without a model
        call. Identity-keyed row repairs update selected fields while preserving
        original identities, row order and unselected data. Missing/extra identities
        and prohibited row reordering cannot be reconstructed. It refuses conflicting sources and numbers
        that cannot survive the Python JSON boundary without precision loss.

        Missing/disputed memory requires clarification. Permissions and stale
        revisions never become repairable. The capture transaction ends before
        callbacks run, and final execution checks the ORIGINAL revision.
        Reports distinguish deterministic attempts from callback invocations.
        Callback errors return ``repair_failed``. Unsupported deterministic
        repairs return ``repair_unavailable``. Tool errors propagate and NEVER
        trigger another repair after execution has started. This is a data
        boundary check, not a guarantee of arbitrary semantic correctness.
        """
        nonempty_string(tool, "tool name")
        if type(arguments) is not dict:
            raise TypeError("arguments must be a JSON argument dictionary.")
        arguments = clone(arguments)
        _sync_callable(executor)
        if type(repair) is str:
            if repair != "known":
                raise ValueError("repair must be 'known', a synchronous callback, or None.")
        elif repair is not None:
            _sync_callable(repair)
        repair_mode = "known" if type(repair) is str else "callback" if repair is not None else None

        def dispatch(name, checked_arguments):
            return _sync_result(executor(name, checked_arguments))

        proposal = {"tool": tool, "arguments": arguments}
        initial = guarded_tool_call(self._relay, self._session_id, self._context, proposal, dispatch)
        report = {"status": "executed" if initial["executed"] else _rejection_status(initial["check"]),
                  "executed": initial["executed"], "completed": False, "business_status": "not_started", "repair_attempts": 0,
                  "repair_mode": repair_mode, "repair_callback_calls": 0,
                  "initial_check": clone(initial["check"]), "final_check": clone(initial["check"])}
        if initial["executed"]:
            report.update(result=initial["result"], receipt=initial["receipt"],
                          business_status=initial["business_status"], completed=initial["completed"])
            return report
        if repair is None or not _can_repair(initial["check"]):
            return report

        # Recapture both projections and the rejection under one transaction.
        # Keep the ORIGINAL revision for this diagnostic: a concurrent change
        # must not quietly renew the old proposal's permission to request repair.
        from .memory import AutomaticMemory
        with self._relay.store._transaction(write=True):
            current = self._relay.check(self._session_id, {
                **proposal, "context_revision": self.revision,
            })
            context = current["recovery_context"]
            memory = (AutomaticMemory(self._relay.store, self._session_id).effective_snapshot()
                      if _can_repair(current) else None)
            policy = (next(row for row in self._relay.store.policies(self._session_id)
                           if row["tool"] == tool) if _can_repair(current) else None)
        report["final_check"] = clone(current)
        if not _can_repair(current):
            report["status"] = _rejection_status(current)
            return report

        report.update(repair_attempts=1, repair_context_revision=context["revision"])
        scope = RepairScope(current, policy)
        try:
            if repair_mode == "known":
                revised = scope.known(arguments, policy, context, memory)
            else:
                report["repair_callback_calls"] = 1
                revised = _sync_result(repair(clone({
                    "tool": tool, "arguments": arguments, "rejection": current,
                    "context": context, "user_memory": memory,
                })))
            if type(revised) is not dict:
                raise TypeError("repair must return a JSON argument dictionary for the same tool.")
            revised = clone(revised)
        except RepairUnavailable as exc:
            if repair_mode == "known":
                report.update(status="repair_unavailable", repair_unavailable_reason=exc.reason)
            else:
                report.update(status="repair_failed", error_type=type(exc).__name__)
            return report
        except Exception as exc:
            report.update(status="repair_failed", error_type=type(exc).__name__)
            return report

        report["repair_scope"] = scope.check(arguments, revised)
        if not report["repair_scope"]["valid"]:
            # Persist a separate explicit rejection. The normal check alone may
            # allow optional data; it must not become an authorization to edit it.
            with self._relay.store._transaction(write=True):
                rejected = self._relay.check(self._session_id, {
                    "tool": tool, "arguments": revised, "context_revision": self.revision,
                })
                rejected.update(id=str(uuid.uuid4()), decision="block", created_at=timestamp(),
                                repair_scope=clone(report["repair_scope"]))
                rejected["issues"].append(issue(
                    "repair_scope_violation", "arguments",
                    "Repair changed data outside the failed supported scopes; see repair_scope."))
                self._relay.store._save_check(self._session_id, rejected)
            report.update(status="blocked", final_check=clone(rejected))
            return report

        # Never catch execution exceptions here. The original callback may have
        # already performed an external effect before reporting a failure.
        final = guarded_tool_call(self._relay, self._session_id, context,
                                  {"tool": tool, "arguments": revised}, dispatch)
        report.update(status="repaired" if final["executed"] else _rejection_status(final["check"]),
                      executed=final["executed"], final_check=clone(final["check"]))
        if final["executed"]:
            report.update(result=final["result"], receipt=final["receipt"],
                          business_status=final["business_status"], completed=final["completed"])
        return report

    def wrap(self, function: Callable, *, tool: str | None = None) -> Callable:
        """Wrap an existing sync function and keep its normal calling convention.

        Positional-only and keyword-only parameters are supported. Defaults are
        included in policy checking. Variadic signatures must use an explicit
        adapter or ``wrap_dispatcher`` so their argument mapping is unambiguous.
        Keep the returned function private to the harness, outside model control.
        """
        _sync_callable(function)
        name = tool if tool is not None else getattr(function, "__name__", None)
        nonempty_string(name, "tool name")
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError) as exc:
            raise TypeError("Tool signature is unavailable; use wrap_dispatcher with an explicit argument mapping.") from exc
        if any(parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
               for parameter in signature.parameters.values()):
            raise TypeError("Variadic tools require an explicit adapter or wrap_dispatcher.")

        def execute(_name, checked_arguments):
            bound = inspect.BoundArguments(signature, checked_arguments)
            return _sync_result(function(*bound.args, **bound.kwargs))

        @wraps(function)
        def protected(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs)
            arguments.apply_defaults()
            return self._call(name, dict(arguments.arguments), execute)

        return protected

    def wrap_dispatcher(self, executor: Callable) -> Callable:
        """Wrap an existing ``executor(tool_name, arguments)`` dispatch function.

        The dispatcher must allowlist its tools. Only checked, copied arguments
        reach it. It must return JSON data and must not update this Relay store.
        """
        _sync_callable(executor)

        def execute(tool, checked_arguments):
            return _sync_result(executor(tool, checked_arguments))

        @wraps(executor)
        def protected(tool: str, arguments: dict):
            return self._call(tool, arguments, execute)

        return protected


class PreparedTurn(GuardedTurn):
    """Selected model input and tool checks bound to one captured state revision."""

    def __init__(self, relay: Relay, session_id: str, context: dict, plan: ContextPlan,
                 *, archive: EvidenceArchive | None = None, options: dict | None = None,
                 memory_snapshot: dict | None = None):
        super().__init__(relay, session_id, context)
        self._plan = plan
        self._archive = archive
        self._options = dict(options or {})
        self._recovery = None
        self._memory_report = None
        self._memory_snapshot = clone(memory_snapshot)

    @property
    def memory_snapshot(self) -> dict | None:
        """Recall captured at this exact revision; never a fresh store read."""
        return clone(self._memory_snapshot)

    @property
    def history_id(self) -> str:
        """Identity of the captured original history, separate from state revision."""
        if self._archive is None:
            raise ValueError("This turn has no evidence archive; create it with ContextGuard.prepare().")
        return self._archive.history_id

    def recover_evidence(self, indices: tuple[int, ...] | list[int], *, history_id: str,
                         max_chars: int = 8000) -> RecoveredEvidence:
        """Read original evidence without changing state, permissions, or the plan.

        To use it in a new bounded model request, use guard.recover_context().
        Appending a recovered packet manually invalidates the old plan's budget.
        """
        if self._archive is None:
            raise ValueError("This turn has no evidence archive; create it with ContextGuard.prepare().")
        return self._archive.recover(indices, history_id=history_id, max_chars=max_chars)

    @property
    def messages(self) -> list[dict]:
        """A defensive copy of the selected plain-text conversation."""
        return self._plan.messages

    @property
    def report(self) -> dict:
        """Coverage, provenance, and budget accounting for the prepared request."""
        report = self._plan.report
        report["session_id"] = self._session_id
        report["state_revision"] = self.revision
        if self._archive is not None:
            report["history_id"] = self.history_id
        if self._recovery is not None:
            report["recovery"] = clone(self._recovery)
        if self._memory_report is not None:
            report["memory_processing"] = clone(self._memory_report)
        return report


def _tool_policy(tool, *, when=None, bindings=None, memory_bindings=None,
                 content=None, verification=None, outcome=None, optional_args=(), title=None):
    """Build one canonical data-only contract for registration or resumption."""
    nonempty_string(tool, "tool")
    if when is not None and type(when) is not dict:
        raise ValueError("when must be a dictionary of fact names and required values.")
    if bindings is not None and type(bindings) is not dict:
        raise ValueError("bindings must be a dictionary of argument names and fact names.")
    if memory_bindings is not None and type(memory_bindings) is not dict:
        raise ValueError("memory_bindings must be a dictionary of argument names and memory keys.")
    if content is not None and not isinstance(content, ContentPolicy):
        raise TypeError("content must be a ContentPolicy or None.")
    if verification is not None and not isinstance(verification, VerificationPolicy):
        raise TypeError("verification must be a VerificationPolicy or None.")
    if outcome is not None and not isinstance(outcome, OutcomePolicy):
        raise TypeError("outcome must be an OutcomePolicy or None.")
    if isinstance(optional_args, (str, bytes)):
        raise ValueError("optional_args must be an iterable of argument names, not a string.")
    return validate_policy({
        "id": tool, "title": title if title is not None else f"Policy for {tool}", "tool": tool,
        "requirements": [{"key": key, "op": "eq", "value": value} for key, value in (when or {}).items()],
        "bindings": bindings if bindings is not None else {}, "optional_args": list(optional_args),
        **({"memory_bindings": memory_bindings} if memory_bindings is not None else {}),
        **({"content": content.to_dict()} if content is not None else {}),
        **({"verification": verification.to_dict()} if verification is not None else {}),
        **({"outcome": outcome.to_dict()} if outcome is not None else {}),
    })


class ContextGuard:
    """Small entry point for integrating Relay into an existing Python project.

    Owns its SQLite store; close it or use a context manager. Omit session_id to
    create a new workflow. Retain session_id in your application's conversation
    record and supply it with the same database to resume that workflow later.
    No web server, model client, telemetry, or network connection is created.
    """

    def __init__(self, db_path: str | Path = ":memory:", *, name: str = "Agent workflow",
                 session_id: str | None = None, busy_timeout_ms: int = 10000):
        self.store = RelayStore(db_path, busy_timeout_ms=busy_timeout_ms)
        try:
            session = (self.store.create_session(name) if session_id is None
                       else self.store.get_session(session_id))
        except BaseException:
            self.store.close()
            raise
        self.session_id = session["id"]
        self.relay = Relay(self.store)

    def remember(self, key: str, value, *, source: str = "application", text: str = "",
                 expected_revision: int | None = None) -> dict:
        """Record an explicit trusted update; previous values remain in history."""
        return self.store.record_fact(self.session_id, key, value, source=source,
                                      text=text, expected_revision=expected_revision)

    def allow_tool(self, tool: str, *, when: dict | None = None, bindings: dict | None = None,
                   memory_bindings: dict | None = None, content: ContentPolicy | None = None,
                   verification: VerificationPolicy | None = None, outcome: OutcomePolicy | None = None,
                   optional_args: Iterable[str] = (), title: str | None = None) -> None:
        """Register equality prerequisites and argument-to-fact bindings for a tool.

        ``when={"approved": True}`` requires that current fact. Every function
        parameter (including defaults) must be bound or explicitly optional.
        Optional arguments have no value constraints here. For ne/in operators,
        use ``guard.store.add_policy(guard.session_id, full_policy)``.
        ``memory_bindings`` restricts arguments to current undisputed automatic
        user memory. Known standard preference spellings are canonicalized;
        other values use exact JSON equality. It never grants permissions or
        copies user statements into trusted facts. Missing/disputed values block.
        ``content`` snapshots application-owned reference data and checks declared
        fields/rows before dispatch, including repaired proposals. Protected top-level
        arguments are required; do not also list them in ``optional_args``.
        ``verification`` checks declared claims against developer-selected sources
        and deterministic rules immediately before execution, including freshness.
        It does not verify explanations or the underlying source's truth.
        """
        self.store.add_policy(self.session_id, _tool_policy(
            tool, when=when, bindings=bindings, memory_bindings=memory_bindings,
            content=content, verification=verification, outcome=outcome, optional_args=optional_args, title=title))


    def turn(self, *, max_chars: int = 12000) -> GuardedTurn:
        """Capture current state BEFORE the model call, then wrap its tools.

        The middleware enforces the core's 12,000-character execution ceiling.
        A smaller capture budget may be requested, but not a larger one.
        """
        if type(max_chars) is not int or not 0 <= max_chars <= 12000:
            raise ValueError("max_chars must be an integer from 0 to the 12000-character execution ceiling.")
        return GuardedTurn(self.relay, self.session_id,
                           self.relay.context(self.session_id, max_chars=max_chars))

    def prepare(self, messages: list[dict], *, max_chars: int = 16000, recent_turns: int = 2,
                pinned: tuple[int, ...] | list[int] = (), retrieve_turns: int = 0,
                query: str | None = None, token_budget: TokenBudget | None = None,
                protocol: str = "plain", source_labels: bool = False,
                request_data: dict | None = None, memory=None,
                expected_revision: int | None = None) -> PreparedTurn:
        """Prepare bounded context BEFORE the model call and retain its revision.

        Defaults to plain-text role/content messages. protocol='ollama' accepts
        validated native text/tool histories and preserves whole tool exchanges.
        Required instructions,
        current application-recorded state, pinned turns, and the configured
        recent turns cannot be silently dropped. The caller retains the full
        original transcript; this turn also captures an in-memory evidence copy.
        Inspect ``report`` for all omissions. Character counts are not provider
        token limits; supply ``token_budget`` with your model's counter to enforce
        both caps. Other native/multimodal message protocols require adapters.
        source_labels adds original message IDs to plain model inputs only;
        request_data supplies host-owned JSON metadata counted in the same cap.

        memory optionally reads an AutomaticMemory snapshot from this exact
        guard/session into a separate state.user_memory field. It never extracts
        or writes memory here. State and recall are captured in one transaction;
        expected_revision can reject a concurrent change before compilation.

        Record authoritative corrections with remember() before this call.
        This method selects context; it does not extract or reconcile facts.
        """
        if type(pinned) not in (tuple, list):
            raise ValueError("pinned must be a tuple or list of original message indices.")
        # A host counter may run arbitrary code. Capture pins before invoking it
        # so this plan and any later recovery preserve the same mandatory turns.
        captured_pins = tuple(pinned)
        if type(source_labels) is not bool:
            raise ValueError("source_labels must be a boolean.")
        if type(protocol) is not str or protocol not in {"plain", "ollama", "openai_chat"}:
            raise ValueError("protocol must be 'plain', 'ollama' or 'openai_chat'.")
        if protocol != "plain" and source_labels:
            raise ValueError("Source-labelled answer requests currently require the plain protocol.")
        if request_data is not None and type(request_data) is not dict:
            raise ValueError("request_data must be a JSON dictionary or None.")
        captured_request_data = clone(request_data)
        if expected_revision is not None:
            validate_revision(expected_revision)
        archive = EvidenceArchive(messages, scope_id=self.session_id, protocol=protocol)
        if memory is not None:
            from .memory import AutomaticMemory
            if (not isinstance(memory, AutomaticMemory) or memory.store is not self.store
                    or memory.session_id != self.session_id):
                raise ValueError("memory must belong to this guard's store and conversation.")
        # State and recall share a revision. Release the read transaction before
        # calling an application token counter or compiling the provider input.
        with self.store._transaction():
            captured = self.turn().context
            if expected_revision is not None and captured["revision"] != expected_revision:
                raise ConflictError(expected_revision, captured["revision"])
            captured_memory = memory.context_snapshot() if memory is not None else None
        state = {key: captured[key] for key in ("session_id", "revision", "facts", "sources")}
        if captured_memory is not None:
            state["user_memory"] = captured_memory
        if source_labels:
            state["evidence_history_id"] = archive.history_id
        if captured_request_data is not None:
            state["request_data"] = clone(captured_request_data)
        compiler_options = dict(state=state, max_chars=max_chars, recent_turns=recent_turns,
                                pinned=captured_pins, retrieve_turns=retrieve_turns, query=query,
                                token_budget=token_budget)
        if protocol == "ollama":
            from .protocols import compile_ollama_context
            plan = compile_ollama_context(archive.messages, **compiler_options)
        elif protocol == "openai_chat":
            from .openai import compile_openai_chat_context
            plan = compile_openai_chat_context(archive.messages, **compiler_options)
        else:
            plan = compile_context(archive.messages, source_labels=source_labels, **compiler_options)
        options = {"max_chars": max_chars, "recent_turns": recent_turns, "pinned": captured_pins,
                   "retrieve_turns": retrieve_turns, "query": query, "token_budget": token_budget,
                   "protocol": protocol, "source_labels": source_labels,
                   "request_data": captured_request_data, "memory": memory}
        return PreparedTurn(self.relay, self.session_id, captured, plan, archive=archive, options=options,
                            memory_snapshot=captured_memory)

    def recover_context(self, prepared: PreparedTurn, indices: tuple[int, ...] | list[int], *,
                        history_id: str, max_chars: int | None = None,
                        token_budget: TokenBudget | None = None,
                        request_data: dict | None = None) -> PreparedTurn:
        """Prepare again with selected original evidence required and latest state.

        Use the returned messages for a NEW model decision, then wrap its tools
        with that same returned turn. This never refreshes an old tool proposal
        or changes facts. Original roles and chronological placement are kept.
        Existing preparation options are retained unless explicitly overridden.
        Oversized required evidence raises; it is never silently shortened.
        """
        if (not isinstance(prepared, PreparedTurn) or prepared._relay is not self.relay
                or prepared._session_id != self.session_id or prepared._archive is None):
            raise ValueError("Prepared turn must originate from this ContextGuard and session.")
        # Snapshot supported inputs before validation and before another model
        # counter can mutate them. Let the archive reject unsupported types.
        requested = tuple(indices) if type(indices) in (tuple, list) else indices
        restored = prepared._archive.select_indices(requested, history_id=history_id)
        options = dict(prepared._options)
        options["pinned"] = tuple(sorted(set(options.get("pinned", ())) | set(restored)))
        if max_chars is not None:
            options["max_chars"] = max_chars
        if token_budget is not None:
            options["token_budget"] = token_budget
        if request_data is not None:
            options["request_data"] = clone(request_data)
        result = self.prepare(prepared._archive.messages, **options)
        result._recovery = {"from_history_id": history_id, "requested_indices": list(requested),
                            "restored_indices": restored}
        return result

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> ContextGuard:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
