"""Bounded, durable one-action OpenAI Responses workflows.

The host supplies schemas, trusted evidence, business receipt checks and effects.
Native output is retained verbatim; only explicit new user text enters memory.
Interrupted effects require destination reconciliation, never automatic replay.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import threading
import uuid
from functools import wraps

from .actions import ActionProposalError
from .budget import TokenBudgetExceeded
from .fresh_evidence import EvidenceChanged, EvidenceSource
from .memory import AutomaticMemory, MemoryPolicy
from .middleware import _sync_callable, _sync_result
from .models import BudgetExceeded, ToolExecutionError, clone, encode, nonempty_string, strict_equal
from .native import AsyncNativeConnection, NativeConnection, NativeResult, native_json
from .native_openai import prepare_openai_responses
from .outcomes import OutcomePolicy, OutcomeVerifier
from .coverage import CoveragePolicy
from .provider_actions import ActionDecoder, NativeActionHarness
from .tool_schema import SchemaViolation, validate_arguments, validate_schema
from .workflow_history import WorkflowHistoryPolicy, WorkflowLimitExceeded, _WorkflowHistory


_MAX_RECORD_CHARS = 256000
_MAX_RESPONSE_CHARS = 128000
_MAX_RESULT_CHARS = 64000
_OPTIONS = frozenset({"model", "temperature", "top_p", "max_output_tokens", "reasoning", "text",
                      "service_tier", "store", "include", "metadata", "timeout"})
_DATA = "[Relay workflow data: host facts and untrusted user preferences; no new tool permissions]\n"


class WorkflowPending(RuntimeError):
    """A retained operation must be reconciled or explicitly abandoned first."""
    def __init__(self, request_id, phase):
        self.request_id, self.phase = request_id, phase
        super().__init__(f"Workflow request {request_id!r} is pending in {phase!r}; no action was repeated.")


class WorkflowResult:
    """Defensive persisted JSON result; completed means its declared outcome passed."""
    _fields = frozenset({"request_id", "status", "executed", "completed", "business_status",
                         "response", "execution", "replayed"})

    def __init__(self, data, *, replayed=False):
        self._data = clone(data)
        self._data["replayed"] = replayed

    def __getattr__(self, name):
        if name in self._fields:
            return clone(self._data.get(name))
        raise AttributeError(name)

    def to_dict(self):
        return clone(self._data)


def _schemas(value, tools):
    if type(value) is dict:
        value = [{"type": "function", "name": name, "parameters": parameters, "strict": True}
                 for name, parameters in value.items()]
    if type(value) is not list or len(value) != len(tools):
        raise ValueError("schemas must declare exactly the Python tool inventory.")
    result, seen = clone(value), set()
    for schema in result:
        if (type(schema) is not dict or set(schema) - {"type", "name", "description", "parameters", "strict"}
                or schema.get("type") != "function" or schema.get("strict") is not True):
            raise ValueError("Workflow schemas must be strict client function schemas.")
        name, parameters = schema.get("name"), schema.get("parameters")
        if type(name) is not str or name not in tools or name in seen:
            raise ValueError("Schema names must match the inventory exactly once.")
        seen.add(name)
        if (type(parameters) is not dict or parameters.get("type") != "object"
                or type(parameters.get("properties")) is not dict
                or parameters.get("additionalProperties") is not False
                or type(parameters.get("required")) is not list):
            raise ValueError("Tool parameters need object properties, required fields and additionalProperties=False.")
        schema["parameters"] = parameters = validate_schema(parameters)
        names = set(inspect.signature(tools[name]).parameters)
        required = parameters["required"]
        if (any(type(key) is not str for key in required) or len(required) != len(set(required))
                or set(parameters["properties"]) != names or set(required) != names):
            raise ValueError("Strict schema properties and required fields must match every Python parameter.")
    return result


class ResponsesWorkflow(_WorkflowHistory):
    """Persist one complete native proposal/action per explicit user request.

    Resume with the same session and identical configuration. Completed request
    IDs are replayed from storage. Unknown provider/effect interruptions are not
    retried. Python callback identity is host-owned and cannot be authenticated
    by a serialized configuration fingerprint.
    """
    def __init__(self, agent, tools, *, contracts, schemas, instructions="", evidence=None,
                 repair="known", memory=True, authorize_action=None,
                 max_history_chars=2000000, max_requests=256, history_policy=None,
                 coverage=None, outcome_verifier=None):
        if (not isinstance(agent, NativeConnection) or isinstance(agent, AsyncNativeConnection)
                or agent._protocol != "openai_responses_native"):
            raise TypeError("ResponsesWorkflow requires a synchronous native OpenAI Responses connection.")
        agent._require_open()
        if agent._native_memory:
            raise ValueError("Workflow owns fact placement; omit native_memory on its connection.")
        if type(tools) is not dict or not 1 <= len(tools) <= 128:
            raise ValueError("tools must contain 1 through 128 declared functions.")
        tools = dict(tools)
        if type(contracts) is not dict or set(contracts) != set(tools):
            raise ValueError("Every workflow tool needs an explicit contract and OutcomePolicy.")
        for contract in contracts.values():
            if type(contract) is not dict or not isinstance(contract.get("outcome"), OutcomePolicy):
                raise TypeError("Every workflow contract requires outcome=OutcomePolicy(...).")
        if type(instructions) is not str or len(instructions) > 64000:
            raise ValueError("instructions must be a string of at most 64000 characters.")
        if type(memory) is not bool and not isinstance(memory, MemoryPolicy):
            raise TypeError("memory must be a boolean or MemoryPolicy.")
        if history_policy is not None and not isinstance(history_policy, WorkflowHistoryPolicy):
            raise TypeError("history_policy must be a WorkflowHistoryPolicy or None.")
        if coverage is not None and not isinstance(coverage, CoveragePolicy):
            raise TypeError("coverage must be a CoveragePolicy or None.")
        if outcome_verifier is not None and not isinstance(outcome_verifier, OutcomeVerifier):
            raise TypeError("outcome_verifier must be an OutcomeVerifier or None.")
        if evidence is not None and not isinstance(evidence, EvidenceSource):
            raise TypeError("evidence must be an EvidenceSource or None.")
        if authorize_action is not None:
            _sync_callable(authorize_action)
        if type(max_history_chars) is not int or not 256 <= max_history_chars <= 20000000:
            raise ValueError("max_history_chars must be from 256 through 20000000.")
        if type(max_requests) is not int or not 1 <= max_requests <= 10000:
            raise ValueError("max_requests must be from 1 through 10000.")
        if repair not in (None, "known"):
            raise ValueError("Durable workflows support repair=None or repair='known'.")
        schemas = _schemas(schemas, tools)
        self._agent, self._store = agent, agent.guard.store
        self._evidence, self._authorize = evidence, authorize_action
        self._max_history, self._max_requests = max_history_chars, max_requests
        self._history_policy = history_policy
        self._coverage_policy, self._outcome_verifier = coverage, outcome_verifier
        self._schemas, self._instructions = schemas, instructions
        self._parameter_schemas = {item["name"]: item["parameters"] for item in schemas}
        self._run_lock = threading.Lock()
        self._active_snapshot = self._active_record = None
        self._original_started = False
        self._memory = (AutomaticMemory(self._store, agent.session_id,
                        policy=memory if isinstance(memory, MemoryPolicy) else None) if memory else None)
        self._outcomes = {name: contract["outcome"] for name, contract in contracts.items()}
        wrapped = {name: self._wrap(name, function) for name, function in tools.items()}
        with self._store._transaction(write=True):
            self._store._assert_mutable()
            self._store._connection.execute("""CREATE TABLE IF NOT EXISTS responses_workflows (
                session_id TEXT PRIMARY KEY REFERENCES sessions(id), payload TEXT NOT NULL)""")
            self._store._connection.execute("""CREATE TABLE IF NOT EXISTS responses_workflow_requests (
                session_id TEXT NOT NULL REFERENCES responses_workflows(session_id), request_id TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(session_id, request_id))""")
            self._store._connection.execute("""CREATE TABLE IF NOT EXISTS responses_workflow_segments (
                session_id TEXT NOT NULL REFERENCES responses_workflows(session_id), ordinal INTEGER NOT NULL,
                item_count INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(session_id, ordinal))""")
            self._harness = NativeActionHarness(agent, wrapped, contracts=contracts, repair=repair, resume=True)
            if coverage is not None:
                self._harness.require_coverage(coverage)
            profile = {"schema": 1, "schemas": schemas, "instructions": instructions,
                       "memory": memory.to_dict() if isinstance(memory, MemoryPolicy) else memory,
                       "repair": repair, "max_history_chars": max_history_chars, "max_requests": max_requests,
                       "authorize_action": authorize_action is not None,
                       "evidence": None if evidence is None else evidence.describe(),
                       "policies": {name: self._harness._registry._entries[name].policy for name in tools}}
            if history_policy is not None:
                profile["history_policy"] = history_policy.to_dict()
            if coverage is not None:
                profile["coverage"] = coverage.to_dict()
            if outcome_verifier is not None:
                profile["outcome_verifier_id"] = outcome_verifier.verifier_id
            if len(encode(profile)) > _MAX_RECORD_CHARS:
                raise WorkflowLimitExceeded("Workflow configuration exceeds its storage bound.")
            current = self._state()
            if current is None:
                old = self._store._memory_state(agent.session_id)
                if old is not None and old["processed_count"]:
                    raise ValueError("A new workflow cannot reuse unrelated automatic-memory history.")
                if self._store._connection.execute("SELECT 1 FROM conversations WHERE session_id=?", (self.session_id,)).fetchone():
                    raise ValueError("A native workflow needs its own session, separate from text conversations.")
                state = {"profile": profile, "history": [], "users": [], "pending_id": None}
                if history_policy is not None:
                    state.update(segment_id=0, task=self._empty_task())
                self._save_state(state)
            elif not strict_equal(current["profile"], profile):
                raise ValueError("Resuming a workflow requires its identical configuration and contracts.")

    @property
    def session_id(self):
        return self._agent.session_id

    def protection_report(self, inventory=None):
        """Describe actual configuration without exposing stored preference values."""
        self._agent._require_open()
        with self._store._transaction():
            self._state()
            coverage = self._harness.coverage(inventory)
            memory = None if self._memory is None else self._memory.effective_snapshot()
            return {"coverage": coverage,
                "coverage_requirements": None if self._coverage_policy is None else self._coverage_policy.check(coverage),
                "instruction_assurance": None if memory is None else memory.get("assurance", {"enabled": False}),
                "evidence": None if self._evidence is None else self._evidence.describe(),
                "destination_verifier_id": None if self._outcome_verifier is None else self._outcome_verifier.verifier_id,
                "history": self.history_status(), "storage": self._store.privacy_status(),
                "direct_calls_protected": False, "source_truth_guaranteed": False}

    def review_plan(self):
        self._agent._require_open()
        if self._memory is None:
            raise ValueError("Instruction review requires configured automatic memory.")
        with self._store._transaction():
            pending_id = self._state()["pending_id"]
            record = None if pending_id is None else self._record(pending_id)
            if record is None or record["phase"] != "review_pending":
                raise ValueError("No instruction review is pending.")
            return {"request_id": pending_id, "memory": self._memory.review_plan()}

    def resolve_review(self, plan, *, decisions):
        """Confirm the original pending request; never starts inference or a tool."""
        self._agent._require_open()
        if not self._run_lock.acquire(blocking=False):
            raise WorkflowPending(None, "in_flight")
        try:
            with self._store._transaction(write=True):
                if not strict_equal(plan, self.review_plan()):
                    raise ValueError("The pending review changed; inspect a fresh review plan.")
                return self._memory.resolve_review(plan["memory"], decisions=decisions)
        finally:
            self._run_lock.release()

    def resume_review(self, request_id):
        """Continue an explicitly reviewed request, without appending a new turn."""
        return self._run(None, request_id=request_id, provider_options=None, resume_review=True)

    def _wrap(self, name, function):
        _sync_callable(function)
        signature = inspect.signature(function)
        @wraps(function)
        def effect(*args, **kwargs):
            # The engine already holds the write transaction at this boundary.
            # A second resumed handle cannot retire this claim and then permit
            # the abandoned original to act under an otherwise unchanged policy.
            active = self._active_record
            state = self._state()
            current = None if active is None else self._record(active["request_id"])
            if (active is None or state["pending_id"] != active["request_id"] or current is None
                    or current["token"] != active["token"] or current["phase"] != "action_pending"):
                raise WorkflowPending(None if active is None else active["request_id"], "retired")
            arguments = dict(signature.bind(*args, **kwargs).arguments)
            validate_arguments(self._parameter_schemas[name], arguments)
            if self._evidence is not None:
                self._evidence.assert_current(self._active_snapshot)
            # A potentially slow evidence reader must not outlive the final
            # authorization decision unnoticed.
            if self._authorize is not None and _sync_result(self._authorize()) is not True:
                raise PermissionError("Workflow action authorization was denied.")
            # The engine appends this callback's tool_result as the next event.
            # Its write transaction and callback mutation guard keep the sequence
            # exclusive. Persist only the linkage, not a second copy of possibly
            # large arguments. A normal exception commits it with the receipt;
            # a process/transaction interruption rolls both back to unknown.
            current["action_receipt_sequence"] = self._store._session(self.session_id)["revision"] + 1
            self._save_record(current)
            active["action_receipt_sequence"] = current["action_receipt_sequence"]
            self._original_started = True
            return function(*args, **kwargs)
        return effect

    def _state(self):
        self._store._session(self.session_id)
        row = self._store._connection.execute("SELECT payload FROM responses_workflows WHERE session_id=?", (self.session_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def _save_state(self, state):
        size = len(encode({key: state[key] for key in ("history", "users")}))
        if size > self._max_history:
            raise WorkflowLimitExceeded(f"Workflow history needs {size} characters; limit is {self._max_history}. No history was trimmed.")
        self._store._connection.execute("INSERT INTO responses_workflows VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload",
                                        (self.session_id, encode(state)))

    def _record(self, request_id):
        row = self._store._connection.execute("SELECT payload FROM responses_workflow_requests WHERE session_id=? AND request_id=?",
                                             (self.session_id, request_id)).fetchone()
        return None if row is None else json.loads(row[0])

    def _save_record(self, record):
        serialized = encode(record)
        if len(serialized) > _MAX_RECORD_CHARS:
            raise WorkflowLimitExceeded("Workflow request exceeds its bounded record size; reconcile the retained pending operation.")
        self._store._connection.execute("INSERT INTO responses_workflow_requests VALUES (?,?,?) ON CONFLICT(session_id,request_id) DO UPDATE SET payload=excluded.payload",
                                        (self.session_id, record["request_id"], serialized))

    def _action_receipt(self, record):
        sequence = record.get("action_receipt_sequence")
        if sequence is None:
            # Workflows saved before receipt linkage can retain a receipt when
            # destination verification returned unavailable. Never guess from
            # the original proposal or the latest result for the same tool.
            return record.get("verification_pending", {}).get("receipt")
        row = self._store._connection.execute(
            "SELECT payload FROM events WHERE session_id=? AND sequence=? AND kind='tool_result'",
            (self.session_id, sequence)).fetchone()
        if row is None:
            return None
        receipt = json.loads(row[0])
        return receipt if receipt.get("tool") == record["tool"] else None

    @property
    def history(self):
        self._agent._require_open()
        with self._store._transaction():
            return self._state()["history"]

    @property
    def pending(self):
        self._agent._require_open()
        with self._store._transaction():
            request_id = self._state()["pending_id"]
            return None if request_id is None else self._record(request_id)

    def request(self, request_id):
        self._agent._require_open()
        nonempty_string(request_id, "request_id", limit=128)
        with self._store._transaction():
            record = self._record(request_id)
            if record is None:
                raise KeyError(request_id)
            return record

    def _deletion_plan(self):
        session = self._store._session(self.session_id)
        row = self._store._connection.execute(
            "SELECT payload FROM responses_workflows WHERE session_id=?", (self.session_id,)).fetchone()
        if row is None:
            raise KeyError(self.session_id)
        state = json.loads(row[0])
        digest = hashlib.sha256(row[0].encode("utf-8"))
        count, stored_chars = 0, len(row[0])
        for request in self._store._connection.execute(
                "SELECT payload FROM responses_workflow_requests WHERE session_id=? ORDER BY request_id",
                (self.session_id,)):
            payload = request[0]
            digest.update(str(len(payload)).encode("ascii") + b":" + payload.encode("utf-8"))
            count += 1
            stored_chars += len(payload)
        archive_count = 0
        for archive in self._store._connection.execute(
                "SELECT payload FROM responses_workflow_segments WHERE session_id=? ORDER BY ordinal", (self.session_id,)):
            payload = archive[0]
            digest.update(b"archive:" + str(len(payload)).encode("ascii") + b":" + payload.encode("utf-8"))
            stored_chars += len(payload)
            archive_count += 1
        return {"session_id": self.session_id, "revision": session["revision"],
                "request_count": count, "history_chars": len(encode(state["history"])),
                "archive_count": archive_count,
                "checks_count": self._store._connection.execute(
                    "SELECT COUNT(*) FROM checks WHERE session_id=?", (self.session_id,)).fetchone()[0],
                "stored_chars": stored_chars, "fingerprint": digest.hexdigest()}

    def deletion_plan(self):
        """Preview this workflow's stored payloads and revision for explicit deletion."""
        self._agent._require_open()
        with self._store._transaction():
            return self._deletion_plan()

    def delete_history(self, *, expected):
        """Logically delete one fully terminal workflow and its session data.

        Requires its unchanged deletion_plan(). Pending or locally in-flight
        work blocks deletion. Backups, SQLite free pages/WAL and provider copies
        are outside this operation; this is not secure filesystem erasure.
        """
        self._agent._require_open()
        if type(expected) is not dict:
            raise TypeError("expected must be the dictionary returned by deletion_plan().")
        expected = clone(expected)
        if not self._run_lock.acquire(blocking=False):
            raise WorkflowPending(None, "in_flight")
        try:
            with self._store._transaction(write=True):
                self._store._assert_mutable()
                state = self._state()
                if state["pending_id"] is not None:
                    pending = self._record(state["pending_id"])
                    raise WorkflowPending(pending["request_id"], pending["phase"])
                # Check every retained request too, so inconsistent pending
                # metadata cannot authorize deleting an unresolved effect.
                pending = self._store._connection.execute(
                    "SELECT request_id,json_extract(payload,'$.phase') AS phase "
                    "FROM responses_workflow_requests WHERE session_id=? "
                    "AND json_extract(payload,'$.phase') IS NOT 'completed' LIMIT 1", (self.session_id,)).fetchone()
                if pending is not None:
                    raise WorkflowPending(pending["request_id"], pending["phase"])
                actual = self._deletion_plan()
                if not strict_equal(actual, expected):
                    raise ValueError("Workflow changed since the deletion plan; obtain and review a fresh plan.")
                counts = {}
                for table in ("responses_workflow_segments", "responses_workflow_requests", "responses_workflows", "automatic_memory",
                              "checks", "policies", "facts", "events", "sessions"):
                    key = "id" if table == "sessions" else "session_id"
                    counts[table] = self._store._connection.execute(
                        "DELETE FROM " + table + " WHERE " + key + "=?", (self.session_id,)).rowcount
                return {"session_id": self.session_id, "deleted": counts}
        finally:
            self._run_lock.release()

    @staticmethod
    def _result(record, execution, response=None):
        return {"request_id": record["request_id"], "status": execution["status"],
                "executed": execution.get("executed", False), "completed": execution.get("completed", False),
                "business_status": execution.get("business_status", "not_started"),
                "response": response, "execution": execution}

    @staticmethod
    def _function_output(record, execution):
        output = {key: execution[key] for key in ("status", "executed", "completed", "business_status") if key in execution}
        if "repair_attempts" in execution or not execution.get("reconciled"):
            output["repair_attempts"] = execution.get("repair_attempts", 0)
        if "result" in execution:
            output["result"] = execution["result"]
        receipt = execution.get("receipt", {})
        if (execution.get("executed") or execution.get("reconciled")) and "arguments" in receipt:
            output["checked_arguments"] = receipt["arguments"]
        if execution.get("reconciled"):
            output["reconciled"] = True
        serialized = encode(output)
        if len(serialized) > _MAX_RESULT_CHARS:
            raise WorkflowLimitExceeded("Tool result exceeds the native replay bound; reconcile with a bounded destination receipt.")
        return {"type": "function_call_output", "call_id": record["call_id"], "output": serialized}

    def _complete(self, record, execution, *, append_output=False):
        with self._store._transaction(write=True):
            self._store._assert_mutable()
            state, current = self._state(), self._record(record["request_id"])
            if state["pending_id"] != record["request_id"] or current["token"] != record["token"]:
                raise WorkflowPending(record["request_id"], current["phase"])
            if append_output:
                state["history"].append(self._function_output(record, execution))
            record.update(phase="completed", result=self._result(record, execution, record.pop("response", None)))
            state["pending_id"] = None
            self._save_record(record)
            self._save_state(state)
            self._store._append_event(self.session_id, "workflow_completed", {
                "request_id": record["request_id"], "status": execution["status"],
                "completed": execution.get("completed", False), "executed": execution.get("executed", False)})
            return WorkflowResult(record["result"])

    def _begin(self, text, request_id, options):
        with self._store._transaction(write=True):
            self._store._assert_mutable()
            old, state = self._record(request_id), self._state()
            payload = {"text": text, "options": options}
            if old is not None:
                if not strict_equal(old["input"], payload):
                    raise ValueError("A request_id cannot be reused for different text or provider options.")
                if old["phase"] == "completed":
                    return WorkflowResult(old["result"], replayed=True)
                raise WorkflowPending(request_id, old["phase"])
            if state["pending_id"] is not None:
                pending = self._record(state["pending_id"])
                raise WorkflowPending(pending["request_id"], pending["phase"])
            count = self._store._connection.execute("SELECT COUNT(*) FROM responses_workflow_requests WHERE session_id=?", (self.session_id,)).fetchone()[0]
            if count >= self._max_requests:
                raise WorkflowLimitExceeded("Workflow request count reached its bound; start an explicitly separate workflow.")
            record = {"request_id": request_id, "input": payload, "phase": "preparing", "token": str(uuid.uuid4())}
            if self._history_policy is not None:
                record["segment_id"] = state["segment_id"]
                self._capture_task(state, text, request_id)
            state["pending_id"] = request_id
            state["users"].append({"role": "user", "content": text})
            state["history"].append({"role": "user", "content": text})
            if (self._history_policy is not None
                    and len(encode({key: state[key] for key in ("history", "users")})) > self._max_history):
                self._rotate_history(state, record, trailing=1)
            self._save_state(state)
            self._save_record(record)
            return record

    def _prepare_history(self, state, record, memory, options):
        """Choose a complete native segment before capturing its action turn."""
        turn = self._agent.guard.turn()
        def data_item():
            return {"role": "user", "content": _DATA + encode({
                "request_id": record["request_id"], "facts": turn.context["facts"], "memory": memory})}
        state["history"].insert(len(state["history"]) - 1, data_item())
        if state.get("checkpoint_refresh_required") and len(state["history"]) == 2:
            state["history"].insert(0, self._checkpoint(state))
            state.pop("checkpoint_refresh_required")
        def request():
            return dict(options, input=state["history"], tools=self._schemas,
                instructions=self._instructions, parallel_tool_calls=False, truncation="disabled")
        budget_error = None
        try:
            prepared = self._agent.prepare(**request())
        except (BudgetExceeded, TokenBudgetExceeded) as error:
            budget_error = error
            prepared = None
        rotate = self._history_policy is not None and (
            prepared is None or len(encode(prepared.request)) > self._history_policy.rollover_after_chars
            or state.get("checkpoint_refresh_required")
            or len(encode({key: state[key] for key in ("history", "users")})) > self._max_history)
        if rotate and self._rotate_history(state, record, trailing=2):
            # Checkpoint events advance the revision. Facts and permission checks
            # must use the turn captured after that transition, never a stale one.
            turn = self._agent.guard.turn()
            state["history"][-2] = data_item()
            prepared = self._agent.prepare(**request())
        elif budget_error is not None:
            raise budget_error
        if self._history_policy is not None:
            state["latest_input_budget"] = {key: prepared.report.get(key) for key in (
                "size_chars", "max_chars", "input_tokens", "budget_scope", "opaque_context_tokens_measured")}
            record["history_coverage"] = {"segment_id": state["segment_id"], "archive": self._archive_totals(),
                "archive_included_in_input": False, "reasoning_continuity_preserved": not bool(state["segment_id"])}
        self._save_state(state)
        return turn, request()

    def send(self, text, *, request_id, **provider_options):
        """One explicit request; never retries provider calls or external effects."""
        return self._run(text, request_id=request_id, provider_options=provider_options)

    def _run(self, text, *, request_id, provider_options, resume_review=False):
        self._agent._require_open()
        nonempty_string(request_id, "request_id", limit=128)
        if not resume_review:
            nonempty_string(text, "text", limit=64000)
        unknown = set(provider_options or {}) - _OPTIONS
        if unknown:
            raise ValueError("Workflow owns native history, tools and request control fields: " + ", ".join(sorted(unknown)))
        options = clone(provider_options or {})
        if len(encode(options)) > 16000:
            raise WorkflowLimitExceeded("Provider options exceed 16000 characters.")
        if not self._run_lock.acquire(blocking=False):
            raise WorkflowPending(request_id, "in_flight")
        try:
            if self._coverage_policy is not None:
                self._harness.require_coverage(self._coverage_policy)
            if resume_review:
                with self._store._transaction(write=True):
                    self._store._assert_mutable()
                    record = self._record(request_id)
                    if (record is None or self._state()["pending_id"] != request_id
                            or record["phase"] != "review_pending"):
                        raise ValueError("Only an unstarted review_pending request can resume.")
                    options = clone(record["input"]["options"])
                    record["phase"] = "preparing"
                    self._save_record(record)
            else:
                record = self._begin(text, request_id, options)
            if isinstance(record, WorkflowResult):
                return record
            try:
                snapshot = None if self._evidence is None else self._evidence.read()
                with self._store._transaction(write=True):
                    active = self._record(request_id)
                    if (self._state()["pending_id"] != request_id or active["token"] != record["token"]
                            or active["phase"] != "preparing"):
                        raise WorkflowPending(request_id, active["phase"])
                    if snapshot is not None:
                        snapshot.apply(self._agent.guard)
                    self._agent.guard.remember("workflow_request_id", request_id, source="workflow")
                    state = self._state()
                    if self._memory is not None:
                        self._memory.process(state["users"])
                        memory = self._memory.effective_snapshot(for_context=True)
                    else:
                        memory = None
                    if memory is not None and memory["unresolved"]:
                        if any(item.get("reason") == "confirmation_required" for item in memory["unresolved"]):
                            execution = {"status": "review_required", "executed": False, "completed": False,
                                         "business_status": "not_started", "memory": memory}
                            record["phase"] = "review_pending"
                            self._save_record(record)
                            return WorkflowResult(self._result(record, execution))
                        return self._complete(record, {"status": "clarification_required", "executed": False,
                            "completed": False, "business_status": "not_started", "memory": memory})
                    turn, native_request = self._prepare_history(state, record, memory, options)
                    record.update(phase="provider_pending", revision=turn.revision)
                    self._save_record(record)
            except Exception:
                # No provider or action began. Persist an honest terminal failure,
                # then preserve the error for the host; identical IDs never retry.
                self._complete(record, {"status": "preparation_failed", "executed": False,
                                       "completed": False, "business_status": "not_started"})
                raise
            self._active_snapshot = snapshot
            result = self._agent.invoke_result(**native_request)
            response = native_json(result.response, sdk="openai")
            if len(encode(response)) > _MAX_RESPONSE_CHARS:
                self._complete(record, {"status": "proposal_too_large", "executed": False,
                                       "completed": False, "business_status": "not_started"})
                raise WorkflowLimitExceeded("Native response exceeds 128000 characters; no action ran.")
            try:
                proposal = ActionDecoder("openai_responses")(response)
                if proposal["tool"] not in self._outcomes:
                    raise ActionProposalError(response, code="unknown_action_tool")
                call = next(item for item in response["output"] if item["type"] == "function_call")
                # Validate completed history before any client effect. Unsupported
                # opaque formats and duplicate call IDs cannot poison a later replay.
                record.update(response=response, call_id=call["call_id"], tool=proposal["tool"])
                with self._store._transaction(write=True):
                    state = self._state()
                    current = self._record(request_id)
                    if (state["pending_id"] != request_id or current["token"] != record["token"]
                            or current["phase"] != "provider_pending"):
                        raise WorkflowPending(request_id, current["phase"])
                    state["history"].extend(response["output"])
                    probe = state["history"] + [{"type": "function_call_output", "call_id": call["call_id"], "output": "{}"}]
                    prepare_openai_responses({"input": probe, "tools": self._schemas})
                    # Reserve bounded space for a minimal truthful tool result.
                    if len(encode({"history": probe, "users": state["users"]})) + 512 > self._max_history:
                        raise WorkflowLimitExceeded("Insufficient room for native tool output; no action ran.")
                    record["phase"] = "action_pending"
                    self._save_state(state)
                    self._save_record(record)
            except Exception:
                self._complete(record, {"status": "invalid_proposal", "executed": False,
                                       "completed": False, "business_status": "not_started"})
                raise
            self._active_record = record
            self._original_started = False
            try:
                execution = self._harness._finish(NativeResult(response, result.prepared), turn).execution
            except ToolExecutionError as error:
                if self._original_started:
                    raise  # Its external effect may already have happened.
                cause = error.__cause__
                code = ("source_changed" if isinstance(cause, EvidenceChanged)
                        else "authorization_denied" if isinstance(cause, PermissionError)
                        else "schema_violation" if isinstance(cause, SchemaViolation)
                        else "pre_effect_failed")
                execution = {"status": "blocked", "executed": False, "completed": False,
                    "business_status": "not_started", "reason": code, "receipt": error.receipt}
                if isinstance(cause, EvidenceChanged):
                    execution["evidence_reason"] = cause.reason
            except ActionProposalError:
                self._complete(record, {"status": "invalid_proposal", "executed": False,
                    "completed": False, "business_status": "not_started"}, append_output=True)
                raise
            return self._verify_and_complete(record, execution)
        finally:
            self._active_snapshot = self._active_record = None
            self._run_lock.release()

    def _verify_and_complete(self, record, execution):
        if self._outcome_verifier is not None and (execution.get("executed") or execution.get("reconciled")):
            verification = self._outcome_verifier.check(request_id=record["request_id"], tool=record["tool"],
                arguments=execution.get("receipt", {}).get("arguments"), result=execution.get("result"))
            execution["destination_verification"] = verification
            if verification["status"] != "accepted":
                execution.update(completed=False, business_status="unknown" if verification["status"] == "unavailable" else "not_satisfied")
            if verification["status"] == "unavailable":
                # The effect may already exist. Retain the claim for explicit
                # readback/reconciliation, never repeat the original callback.
                execution["status"] = "verification_pending"
                with self._store._transaction(write=True):
                    current = self._record(record["request_id"])
                    if (self._state()["pending_id"] != record["request_id"]
                            or current["token"] != record["token"] or current["phase"] != "action_pending"):
                        raise WorkflowPending(record["request_id"], current["phase"])
                    record["verification_pending"] = execution
                    self._save_record(record)
                return WorkflowResult(self._result(record, execution, record.get("response")))
        record.pop("verification_pending", None)
        return self._complete(record, execution, append_output=True)

    def reconcile(self, request_id, *, result, expected_phase):
        """Record a host-obtained destination receipt without invoking the effect.

        The host must establish that the receipt belongs to this request/call.
        A matching declared outcome does not authenticate an external system.
        """
        self._agent._require_open()
        if not self._run_lock.acquire(blocking=False):
            raise WorkflowPending(request_id, "in_flight")
        try:
            result = clone(result)
            if len(encode(result)) > _MAX_RESULT_CHARS - 1000:
                raise WorkflowLimitExceeded("Reconciliation receipt exceeds its bound.")
            with self._store._transaction(write=True):
                record = self._record(request_id)
                if record is None:
                    raise KeyError(request_id)
                if expected_phase != "action_pending" or record["phase"] != expected_phase:
                    raise ValueError("Reconciliation requires the matching pending action phase.")
                outcome = self._outcomes[record["tool"]].check(result)
                execution = {"status": "reconciled", "executed": False, "result": result,
                             "reconciled": True, "callback_reexecuted": False, **outcome}
                retained = self._action_receipt(record)
                if retained is not None:
                    execution["receipt"] = clone(retained)
                previous = record.get("verification_pending", {})
                if "repair_attempts" in previous:
                    execution["repair_attempts"] = previous["repair_attempts"]
                return self._verify_and_complete(record, execution)
        finally:
            self._run_lock.release()

    def abandon(self, request_id, *, expected_phase):
        """Explicitly end a pre-action interrupted request; never retries it."""
        self._agent._require_open()
        if not self._run_lock.acquire(blocking=False):
            raise WorkflowPending(request_id, "in_flight")
        try:
            with self._store._transaction(write=True):
                record = self._record(request_id)
                if record is None:
                    raise KeyError(request_id)
                if expected_phase not in {"preparing", "review_pending", "provider_pending"} or record["phase"] != expected_phase:
                    raise ValueError("Only the matching pre-action pending phase can be abandoned.")
                return self._complete(record, {"status": "abandoned", "executed": False,
                                       "completed": False, "business_status": "not_started"})
        finally:
            self._run_lock.release()
