"""Context snapshots, structural audits, and checks at the tool execution boundary."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable

from .models import BudgetExceeded, ToolExecutionError, clone, encode, issue, nonempty_string, strict_equal, timestamp
from .store import RelayStore
from .memory_values import normalize_memory_value
from .content import ContentPolicy
from .verification import VerificationPolicy


def _failure_message(error):
    """Diagnostic formatting must never erase a receipt after an effect."""
    try:
        # __str__ may return a str subclass with hostile slicing/encoding hooks.
        # The unbound builtin makes an exact base string without invoking them.
        message = str.__str__(str(error))
        return message[:2000].encode("utf-8", errors="replace").decode("utf-8")
    except BaseException:
        return "Exception message unavailable."


def _failure_type(error):
    try:
        name = str.__str__(type.__getattribute__(type(error), "__name__"))
        return name[:200].encode("utf-8", errors="replace").decode("utf-8")
    except BaseException:
        return "Exception"


class Relay:
    def __init__(self, store: RelayStore):
        self.store = store

    def context(self, session_id: str, *, max_chars: int = 12000, keys: Iterable[str] | None = None) -> dict:
        """Return compact JSON with source references; budgets count Unicode characters.

        Selection is explicit. Unknown selected keys raise KeyError; nothing is
        silently dropped to fit. This is state transport, not semantic summarization.
        """
        if type(max_chars) is not int or max_chars < 0:
            raise ValueError("max_chars must be a nonnegative integer.")
        if isinstance(keys, (str, bytes)):
            raise ValueError("keys must be an iterable of fact keys, not a string.")
        with self.store._transaction():
            session = self.store._session(session_id)
            facts = self.store.facts(session_id)
            selected = sorted(facts) if keys is None else list(dict.fromkeys(keys))
            for key in selected:
                nonempty_string(key, "Selected fact key")
                if key not in facts:
                    raise KeyError(f"Unknown fact key: {key}")
            bundle = {"session_id": session_id, "revision": session["revision"],
                      "facts": {key: facts[key]["value"] for key in selected},
                      "sources": {key: facts[key]["event_id"] for key in selected}}
            text = encode(bundle)
            if len(text) > max_chars:
                raise BudgetExceeded(len(text), max_chars)
            return {**bundle, "text": text, "size_chars": len(text), "size_bytes": len(text.encode("utf-8"))}

    def audit_context(self, session_id: str, candidate: dict) -> dict:
        """Check a full structured snapshot against current state and provenance.

        All session facts are required by this audit. Free-text `text` is not
        interpreted. Use the returned restored_context as the corrected bundle.
        """
        if type(candidate) is not dict:
            raise ValueError("candidate must be a JSON object.")
        candidate = clone(candidate)
        with self.store._transaction():
            restored = self.context(session_id)
            facts = self.store.facts(session_id)
            problems = []
            if "session_id" in candidate and candidate["session_id"] != session_id:
                problems.append(issue("session_mismatch", "session_id", "Context belongs to another session.",
                                      expected=session_id, actual=candidate["session_id"]))
            if type(candidate.get("revision")) is not int or candidate["revision"] != restored["revision"]:
                problems.append(issue("stale_revision", "revision", "Context revision is not current.",
                                      expected=restored["revision"], actual=candidate.get("revision")))
            supplied = candidate.get("facts", {})
            sources = candidate.get("sources", {})
            if type(supplied) is not dict:
                problems.append(issue("invalid_facts", "facts", "Context facts must be an object.", actual=supplied))
                supplied = {}
            if type(sources) is not dict:
                problems.append(issue("invalid_sources", "sources", "Context sources must be an object.", actual=sources))
                sources = {}
            for key, fact in facts.items():
                if key not in supplied:
                    problems.append(issue("missing_fact", key, "Current fact is missing from context.",
                                          expected=fact["value"], event_id=fact["event_id"]))
                elif not strict_equal(fact["value"], supplied[key]):
                    problems.append(issue("fact_mismatch", key, "Context value differs from the latest recorded fact.",
                                          expected=fact["value"], actual=supplied[key], event_id=fact["event_id"]))
                if key not in sources or sources[key] != fact["event_id"]:
                    problems.append(issue("source_mismatch", key, "Source must reference the latest fact event.",
                                          expected=fact["event_id"], actual=sources.get(key), event_id=fact["event_id"]))
            for key in supplied.keys() - facts.keys():
                problems.append(issue("unknown_fact", key, "Context contains an unrecorded fact.", actual=supplied[key]))
            for key in sources.keys() - facts.keys():
                problems.append(issue("unknown_source", key, "Source references an unrecorded fact key.", actual=sources[key]))
            return {"ok": not problems, "revision": restored["revision"], "issues": problems,
                    "restored_context": restored}

    def check(self, session_id: str, action: dict) -> dict:
        """Fail closed for unsupported actions and record a deterministic verdict."""
        started = time.perf_counter()
        if type(action) is not dict:
            raise ValueError("action must be a JSON object.")
        action = clone(action)
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            session = self.store._session(session_id)
            facts = self.store.facts(session_id)
            policies = self.store.policies(session_id)
            problems = []
            normalizations = []
            content_check = None
            content = None
            verification_check = None
            verification = None
            tool = action.get("tool")
            if type(tool) is not str or not tool.strip():
                problems.append(issue("invalid_tool", "tool", "A nonempty tool name is required.", actual=tool))
            unknown_fields = set(action) - {"tool", "arguments", "context_revision"}
            if unknown_fields:
                problems.append(issue("unknown_action_fields", "action", "Action contains unsupported fields.", actual=sorted(unknown_fields)))
            arguments = action.get("arguments")
            if type(arguments) is not dict:
                problems.append(issue("invalid_arguments", "arguments", "Tool arguments must be an object.", actual=arguments))
                arguments = {}
            revision = action.get("context_revision")
            valid_revision = type(revision) is int and revision >= 0
            if not valid_revision:
                problems.append(issue("invalid_revision", "context_revision", "A nonnegative integer context_revision is required.",
                                      expected=session["revision"], actual=revision))
            policy = next((policy for policy in policies if policy["tool"] == tool), None)
            if policy is None:
                problems.append(issue("unknown_tool", "tool", "No developer-controlled policy permits this tool.", actual=tool))
            else:
                for requirement in policy["requirements"]:
                    key, op, expected = requirement["key"], requirement["op"], requirement["value"]
                    fact = facts.get(key)
                    if fact is None:
                        problems.append(issue("missing_fact", key, "A policy requires a fact that has not been recorded.", expected=expected))
                        continue
                    actual = fact["value"]
                    matched = strict_equal(actual, expected) if op == "eq" else (
                        not strict_equal(actual, expected) if op == "ne" else any(strict_equal(actual, item) for item in expected))
                    if not matched:
                        problems.append(issue("requirement_failed", key, f"Policy requirement '{op}' is not satisfied.",
                                              expected=expected, actual=actual, event_id=fact["event_id"]))
                memory_bindings = policy.get("memory_bindings", {})
                content = ContentPolicy.from_dict(policy["content"]) if "content" in policy else None
                verification = VerificationPolicy.from_dict(policy["verification"]) if "verification" in policy else None
                permitted = set(policy["bindings"]) | set(memory_bindings) | set(policy["optional_args"])
                if content is not None:
                    permitted.update(content.argument_names)
                if verification is not None:
                    permitted.update(verification.argument_names)
                for argument in sorted(set(arguments) - permitted):
                    problems.append(issue("unexpected_argument", argument, "This argument is not permitted by the policy.", actual=arguments[argument]))
                for argument, key in policy["bindings"].items():
                    fact = facts.get(key)
                    if fact is None:
                        problems.append(issue("missing_fact", key, f"Argument '{argument}' needs a recorded fact."))
                    if argument not in arguments:
                        problems.append(issue("missing_argument", argument, "A bound tool argument is required.",
                                              expected=fact["value"] if fact else None, event_id=fact["event_id"] if fact else None))
                    elif fact and not strict_equal(arguments[argument], fact["value"]):
                        problems.append(issue("binding_mismatch", argument, f"Argument must match current fact '{key}'.",
                                              expected=fact["value"], actual=arguments[argument], event_id=fact["event_id"]))
                if memory_bindings:
                    # Read the memory owner's public projection under this same
                    # transaction; never promote inferred values into facts.
                    from .memory import AutomaticMemory
                    snapshot = AutomaticMemory(self.store, session_id).effective_snapshot()
                    entries = {entry["key"]: entry for entry in snapshot["entries"]}
                    for argument, key in memory_bindings.items():
                        entry = entries.get(key)
                        expected = normalize_memory_value(key, entry["value"]) if entry else None
                        supplied = normalize_memory_value(key, arguments.get(argument))
                        if entry is None:
                            code, message = "memory_missing", f"Argument '{argument}' requires recorded user memory '{key}'."
                        elif entry["disputed"]:
                            code, message = "memory_disputed", f"Resolve pending changes to memory '{key}' before acting."
                        elif argument not in arguments:
                            code, message = "missing_argument", "A memory-bound tool argument is required."
                        elif not strict_equal(supplied, expected):
                            code, message = "memory_binding_mismatch", f"Argument must match current user memory '{key}'."
                        else:
                            if not strict_equal(arguments[argument], expected):
                                normalizations.append({"argument": argument, "memory_key": key,
                                                       "from": arguments[argument], "to": expected})
                            continue
                        problem = issue(code, argument, message,
                                        expected=expected,
                                        actual=arguments.get(argument))
                        problem.update(memory_key=key, source=entry["source"] if entry else None)
                        problems.append(problem)
            if content is not None or verification is not None:
                # Check the exact values dispatch will receive, after any already
                # permitted finite memory spelling normalization.
                checked_arguments = clone(arguments)
                for change in normalizations:
                    checked_arguments[change["argument"]] = change["to"]
            if content is not None:
                content_check = content.check(checked_arguments).report
                if not content_check["valid"]:
                    problems.append(issue("content_mismatch", "content",
                                          "Protected content differs from the application snapshot; see content_check."))
            if verification is not None:
                # Only declared claim arguments belong to this contract. Other
                # arguments retain their independent fact/memory/content checks.
                claim_names = set(verification.argument_names)
                claims = {key: checked_arguments[key] for key in claim_names if key in checked_arguments}
                verification_check = verification.check(claims).report
                verification_check["tool_argument_scope"] = {
                    "declared_claim_arguments": sorted(claim_names),
                    "other_supplied_arguments": sorted(set(arguments) - claim_names),
                }
                if not verification_check["valid"]:
                    problems.append(issue("verification_failed", "verification",
                                          "Declared claims failed verification; see verification_check."))
            blocked = bool(problems)
            stale = valid_revision and revision != session["revision"]
            if stale:
                problems.append(issue("stale_revision", "context_revision", "Refresh context before executing this action.",
                                      expected=session["revision"], actual=revision))
            recovery = self.context(session_id)
            result = {"id": str(uuid.uuid4()), "decision": "block" if blocked else "refresh" if stale else "allow",
                      "tool": tool, "revision": session["revision"], "issues": problems, "recovery_context": recovery,
                      "duration_ms": round((time.perf_counter() - started) * 1000, 3), "created_at": timestamp()}
            if content_check is not None:
                result["content_check"] = content_check
            if verification_check is not None:
                result["verification_check"] = verification_check
            if result["decision"] == "allow" and normalizations:
                result["argument_normalizations"] = normalizations
            self.store._save_check(session_id, result)
            return result

    def execute(self, session_id: str, action: dict, executor: Callable) -> dict:
        """Validate and call `executor(tool, arguments)` under a local write lock.

        Return {check, executed, result?, receipt?}. Callback exceptions commit a
        failed receipt, then raise ToolExecutionError chained from the exception.
        Callbacks must return JSON data and must not write to this store. This
        transaction is NOT an atomic transaction with an external system: a
        process crash after an external effect can still leave no receipt. Use
        the external service's idempotency/transaction controls for such effects.
        """
        if not callable(executor):
            raise ValueError("executor must be callable.")
        # SQLite text must be encodable before a callback can start. In
        # particular, JSON's escaped lone surrogates are not valid UTF-8 data.
        encode(action).encode("utf-8")
        action = clone(action)
        failure = None
        failure_message = None
        failure_type = None
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            check = self.check(session_id, action)
            if check["decision"] != "allow":
                return {"check": check, "executed": False}
            # Apply only the finite spelling changes accepted by this check,
            # under the same lock. Receipt arguments match what the tool sees.
            for change in check.get("argument_normalizations", []):
                action["arguments"][change["argument"]] = change["to"]
            policy = next(row for row in self.store.policies(session_id) if row["tool"] == action["tool"])
            outcome = None
            if "outcome" in policy:
                from .outcomes import OutcomePolicy
                outcome = OutcomePolicy.from_dict(policy["outcome"])
            business = {"business_status": "unchecked", "completed": False}
            phase = "callback"
            self.store._callback_active = True
            try:
                result = executor(action["tool"], clone(action["arguments"]))
                phase = "result_encoding"
                result = clone(result)
                encode(result).encode("utf-8")
                if outcome is not None:
                    phase = "outcome_check"
                    business = outcome.check(result)
            except BaseException as exc:
                failure = exc
                # Keep callback mutation protection active even while invoking
                # an application's potentially broken exception formatter.
                failure_message = _failure_message(exc)
                failure_type = _failure_type(exc)
            finally:
                self.store._callback_active = False
            payload = {"tool": action["tool"], "arguments": action["arguments"], "check_id": check["id"],
                       "status": "failed" if failure is not None else "succeeded",
                       "outcome": ({"business_status": "unknown", "completed": False}
                                   if failure is not None else business)}
            if failure is not None:
                payload["error"] = {"type": failure_type,
                                    "message": failure_message, "phase": phase,
                                    "executor_completed": phase in {"result_encoding", "outcome_check"}}
            else:
                payload["result"] = result
            receipt = self.store._append_event(session_id, "tool_result", payload)
        if failure is not None:
            if not isinstance(failure, Exception):
                # Preserve cancellation/interrupt semantics, but only after the
                # failed receipt commits and invalidates the captured turn.
                raise failure
            raise ToolExecutionError(f"Tool {action['tool']!r} failed during {phase}: {failure_message}",
                                     check=check, receipt=receipt) from failure
        return {"check": check, "executed": True, "result": result, "receipt": receipt,
                "business_status": business["business_status"], "completed": business["completed"]}
