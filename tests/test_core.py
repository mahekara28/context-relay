from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from context_relay import BudgetExceeded, ConflictError, PolicyError, Relay, RelayStore, ToolExecutionError


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.addCleanup(self.store.close)
        self.relay = Relay(self.store)
        self.session_id = self.store.create_session("Support")['id']
        self.store.record_fact(self.session_id, "intent", "refund", text="Please refund it.")
        self.store.record_fact(self.session_id, "order_id", "ORDER-42", source="order_service")
        self.policy = {
            "id": "refund-policy", "title": "Only refund a current refund request", "tool": "refund",
            "requirements": [{"key": "intent", "op": "eq", "value": "refund"}],
            "bindings": {"order_id": "order_id"}, "optional_args": ["note"],
        }
        self.store.add_policy(self.session_id, self.policy)

    def action(self, **changes):
        action = {"tool": "refund", "arguments": {"order_id": "ORDER-42"},
                  "context_revision": self.store.get_session(self.session_id)["revision"]}
        action.update(changes)
        return action

    def codes(self, check):
        return {issue["code"] for issue in check["issues"]}

    def test_supersession_preserves_history_and_uses_latest_evidence(self):
        initial = self.relay.context(self.session_id)
        latest = self.store.record_fact(self.session_id, "intent", "replacement", text="Actually, replace it. Do not refund.")
        self.assertEqual(self.store.facts(self.session_id)["intent"]["value"], "replacement")
        self.assertEqual(len([event for event in self.store.events(self.session_id) if event.get("key") == "intent"]), 2)
        verdict = self.relay.check(self.session_id, self.action(context_revision=initial["revision"]))
        self.assertEqual(verdict["decision"], "block")
        failure = next(issue for issue in verdict["issues"] if issue["code"] == "requirement_failed")
        self.assertEqual(failure["event_id"], latest["id"])
        self.assertEqual(failure["actual"], "replacement")
        self.assertIn("stale_revision", self.codes(verdict))

    def test_stale_but_otherwise_valid_action_requires_refresh(self):
        stale = self.action()
        self.store.record_fact(self.session_id, "unrelated", "updated")
        verdict = self.relay.check(self.session_id, stale)
        self.assertEqual(verdict["decision"], "refresh")
        self.assertEqual(verdict["recovery_context"]["revision"], stale["context_revision"] + 1)

    def test_unknown_tool_missing_arguments_extra_arguments_block(self):
        for change, code in [({"tool": "delete_everything"}, "unknown_tool"),
                             ({"arguments": {}}, "missing_argument"),
                             ({"arguments": {"order_id": "ORDER-42", "force": True}}, "unexpected_argument"),
                             ({"arguments": {"order_id": "OTHER"}}, "binding_mismatch"),
                             ({"arguments": []}, "invalid_arguments"),
                             ({"context_revision": True}, "invalid_revision"),
                             ({"tool": ""}, "invalid_tool"),
                             ({"surprise": 1}, "unknown_action_fields")]:
            with self.subTest(change=change):
                verdict = self.relay.check(self.session_id, self.action(**change))
                self.assertEqual(verdict["decision"], "block")
                self.assertIn(code, self.codes(verdict))

    def test_missing_requirement_and_binding_facts_fail_closed(self):
        self.policy["requirements"] = [{"key": "not_recorded", "op": "ne", "value": False}]
        self.policy["bindings"] = {"order_id": "also_missing"}
        self.store.add_policy(self.session_id, self.policy)
        verdict = self.relay.check(self.session_id, self.action())
        self.assertEqual(verdict["decision"], "block")
        self.assertEqual(len([issue for issue in verdict["issues"] if issue["code"] == "missing_fact"]), 2)

    def test_strict_json_equality_for_requirements_and_bindings(self):
        for stored, proposed in [(True, 1), (1, 1.0), ({"enabled": True}, {"enabled": 1}), ([False], [0])]:
            with self.subTest(stored=stored, proposed=proposed):
                self.store.record_fact(self.session_id, "order_id", stored)
                verdict = self.relay.check(self.session_id, self.action(arguments={"order_id": proposed}))
                self.assertEqual(verdict["decision"], "block")
        self.store.record_fact(self.session_id, "intent", True)
        self.policy["requirements"] = [{"key": "intent", "op": "in", "value": [1, "refund"]}]
        self.store.add_policy(self.session_id, self.policy)
        verdict = self.relay.check(self.session_id, self.action(arguments={"order_id": [False]}))
        self.assertEqual(verdict["decision"], "block")
        self.assertIn("requirement_failed", self.codes(verdict))
        self.policy["requirements"] = [{"key": "intent", "op": "ne", "value": 1}]
        self.store.add_policy(self.session_id, self.policy)
        self.assertEqual(self.relay.check(self.session_id, self.action(arguments={"order_id": [False]}))["decision"], "allow")

    def test_optional_argument_is_accepted(self):
        action = self.action(arguments={"order_id": "ORDER-42", "note": "requested"})
        self.assertEqual(self.relay.check(self.session_id, action)["decision"], "allow")

    def test_policy_edits_advance_revision_and_are_auditable(self):
        stale = self.action()
        self.policy["title"] = "Updated developer policy"
        self.store.add_policy(self.session_id, self.policy)
        self.assertEqual(self.relay.check(self.session_id, stale)["decision"], "refresh")
        event = self.store.events(self.session_id)[-1]
        self.assertEqual(event["kind"], "policy")
        self.assertEqual(event["sequence"], self.store.get_session(self.session_id)["revision"])
        self.assertEqual(event["policy"]["title"], self.policy["title"])

    def test_invalid_policies_are_rejected_without_revision_change(self):
        revision = self.store.get_session(self.session_id)["revision"]
        patches = [{"requirements": [{"key": "intent", "op": "maybe", "value": "refund"}]},
                   {"requirements": [{"key": "intent", "op": "in", "value": "refund"}]},
                   {"bindings": {"order_id": 1}}, {"optional_args": ["order_id"]},
                   {"optional_args": ["note", "note"]}, {"binding": {}}, {"id": ""}]
        for patch in patches:
            with self.subTest(patch=patch), self.assertRaises(PolicyError):
                self.store.add_policy(self.session_id, {**self.policy, **patch})
        with self.assertRaises(PolicyError):
            self.store.add_policy(self.session_id, {**self.policy, "id": "ambiguous-second-policy"})
        self.assertEqual(self.store.get_session(self.session_id)["revision"], revision)

    def test_policy_tool_change_does_not_leave_old_tool_enabled(self):
        self.policy["tool"] = "replacement"
        self.store.add_policy(self.session_id, self.policy)
        self.assertEqual(self.relay.check(self.session_id, self.action())["decision"], "block")
        self.assertEqual(self.relay.check(self.session_id, self.action(tool="replacement"))["decision"], "allow")

    def test_context_has_exact_budget_provenance_and_selected_keys(self):
        self.store.record_fact(self.session_id, "language", "हिन्दी")
        bundle = self.relay.context(self.session_id)
        self.assertEqual(bundle["size_chars"], len(bundle["text"]))
        self.assertEqual(bundle["size_bytes"], len(bundle["text"].encode("utf-8")))
        self.assertGreater(bundle["size_bytes"], bundle["size_chars"])
        self.assertEqual(json.loads(bundle["text"])["sources"], bundle["sources"])
        self.assertEqual(self.relay.context(self.session_id, max_chars=bundle["size_chars"]), bundle)
        with self.assertRaises(BudgetExceeded) as error:
            self.relay.context(self.session_id, max_chars=bundle["size_chars"] - 1)
        self.assertEqual(error.exception.size_chars, bundle["size_chars"])
        selected = self.relay.context(self.session_id, keys=["intent"])
        self.assertEqual(selected["facts"], {"intent": "refund"})
        self.assertEqual(self.relay.context(self.session_id, keys=[])["facts"], {})
        with self.assertRaises(KeyError):
            self.relay.context(self.session_id, keys=["missing"])
        with self.assertRaises(ValueError):
            self.relay.context(self.session_id, keys="intent")

    def test_context_audit_detects_dropped_changed_and_fabricated_state(self):
        candidate = self.relay.context(self.session_id)
        candidate["facts"].pop("order_id")
        candidate["facts"]["unrecorded"] = "invented"
        event = self.store.record_fact(self.session_id, "intent", "replacement")
        audit = self.relay.audit_context(self.session_id, candidate)
        self.assertFalse(audit["ok"])
        self.assertTrue({"stale_revision", "missing_fact", "fact_mismatch", "source_mismatch", "unknown_fact"} <= self.codes(audit))
        self.assertEqual(audit["restored_context"]["sources"]["intent"], event["id"])
        self.assertTrue(self.relay.audit_context(self.session_id, audit["restored_context"])["ok"])

    def test_audit_does_not_claim_free_text_semantic_validation(self):
        candidate = self.relay.context(self.session_id)
        candidate["text"] = "An arbitrary summary is not validated by this structured audit."
        self.assertTrue(self.relay.audit_context(self.session_id, candidate)["ok"])
        candidate["session_id"] = "other-session"
        candidate["revision"] = True
        self.assertTrue({"session_mismatch", "stale_revision"} <= self.codes(self.relay.audit_context(self.session_id, candidate)))

    def test_invalid_source_reference_is_detected_even_when_value_matches(self):
        candidate = self.relay.context(self.session_id)
        candidate["sources"]["intent"] = candidate["sources"]["order_id"]
        audit = self.relay.audit_context(self.session_id, candidate)
        self.assertFalse(audit["ok"])
        self.assertEqual(self.codes(audit), {"source_mismatch"})

    def test_execute_rechecks_and_never_calls_callback_on_block_or_refresh(self):
        calls = []
        callback = lambda *args: calls.append(args)
        action = self.action()
        self.assertEqual(self.relay.check(self.session_id, action)["decision"], "allow")
        self.store.record_fact(self.session_id, "unrelated", "change")
        refresh = self.relay.execute(self.session_id, action, callback)
        self.assertFalse(refresh["executed"])
        self.assertEqual(refresh["check"]["decision"], "refresh")
        self.store.record_fact(self.session_id, "intent", "replacement")
        block = self.relay.execute(self.session_id, self.action(), callback)
        self.assertFalse(block["executed"])
        self.assertEqual(block["check"]["decision"], "block")
        self.assertEqual(calls, [])

    def test_successful_execute_receipt_and_replay_requires_new_context(self):
        action = self.action()
        calls = []

        def callback(tool, arguments):
            calls.append((tool, arguments.copy()))
            arguments["order_id"] = "mutated-by-executor"
            return {"accepted": True}

        execution = self.relay.execute(self.session_id, action, callback)
        self.assertTrue(execution["executed"])
        self.assertEqual(execution["result"], {"accepted": True})
        self.assertEqual(execution["receipt"]["status"], "succeeded")
        self.assertEqual(execution["receipt"]["arguments"], {"order_id": "ORDER-42"})
        self.assertEqual(self.store.get_session(self.session_id)["revision"], action["context_revision"] + 1)
        self.assertFalse(self.relay.execute(self.session_id, action, callback)["executed"])
        self.assertEqual(len(calls), 1)

    def test_failed_callback_commits_receipt_and_propagates_original_error(self):
        def callback(tool, arguments):
            raise RuntimeError("Tool unavailable")

        with self.assertRaises(ToolExecutionError) as error:
            self.relay.execute(self.session_id, self.action(), callback)
        self.assertIsInstance(error.exception.__cause__, RuntimeError)
        receipt = error.exception.receipt
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["error"]["phase"], "callback")
        self.assertFalse(receipt["error"]["executor_completed"])
        self.assertEqual(self.store.events(self.session_id)[-1], receipt)
        self.assertEqual(self.store.checks(self.session_id)[0], error.exception.check)

    def test_unserializable_result_exposes_completed_executor(self):
        with self.assertRaises(ToolExecutionError) as error:
            self.relay.execute(self.session_id, self.action(), lambda *_: object())
        self.assertEqual(error.exception.receipt["error"]["phase"], "result_encoding")
        self.assertTrue(error.exception.receipt["error"]["executor_completed"])

    def test_reentrant_callback_mutation_is_rejected_with_receipt(self):
        def callback(tool, arguments):
            self.store.record_fact(self.session_id, "intent", "replacement")

        with self.assertRaises(ToolExecutionError):
            self.relay.execute(self.session_id, self.action(), callback)
        self.assertEqual(self.store.facts(self.session_id)["intent"]["value"], "refund")
        self.assertEqual(self.store.events(self.session_id)[-1]["status"], "failed")

    def test_budget_overflow_never_executes(self):
        self.store.record_fact(self.session_id, "oversized", "x" * 15000)
        calls = []
        with self.assertRaises(BudgetExceeded):
            self.relay.execute(self.session_id, self.action(), lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_optimistic_fact_update_and_invalid_json_fail_without_mutation(self):
        revision = self.store.get_session(self.session_id)["revision"]
        with self.assertRaises(ConflictError) as error:
            self.store.record_fact(self.session_id, "intent", "bad", expected_revision=revision - 1)
        self.assertEqual(error.exception.actual, revision)
        invalid = [float("nan"), float("inf"), {1: "bad"}, ("tuple",), {"nested": object()}]
        for value in invalid:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                self.store.record_fact(self.session_id, "bad", value)
        with self.assertRaises(ValueError):
            self.store.record_fact(self.session_id, "intent", "bad", expected_revision=True)
        self.assertEqual(self.store.get_session(self.session_id)["revision"], revision)

    def test_snapshots_cannot_mutate_stored_values(self):
        event = self.store.record_fact(self.session_id, "nested", {"items": [1]})
        event["value"]["items"].append(2)
        returned = self.store.facts(self.session_id)
        returned["nested"]["value"]["items"].append(3)
        self.assertEqual(self.store.facts(self.session_id)["nested"]["value"], {"items": [1]})

    def test_check_history_is_latest_first_and_does_not_change_revision(self):
        revision = self.store.get_session(self.session_id)["revision"]
        first = self.relay.check(self.session_id, self.action())
        second = self.relay.check(self.session_id, self.action(tool="unknown"))
        self.assertEqual([check["id"] for check in self.store.checks(self.session_id)], [second["id"], first["id"]])
        self.assertEqual(self.store.get_session(self.session_id)["revision"], revision)
        self.assertGreaterEqual(first["duration_ms"], 0)

    def test_unknown_session_and_closed_store(self):
        with self.assertRaises(KeyError):
            self.store.record_fact("unknown", "key", "value")
        with self.assertRaises(ValueError):
            self.store.create_session(" ")
        extra = RelayStore()
        extra.close()
        extra.close()
        with self.assertRaises(ValueError):
            extra.list_sessions()


class PersistenceConcurrencyTests(unittest.TestCase):
    def test_persistence_across_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.sqlite3"
            with RelayStore(path) as store:
                session_id = store.create_session("Persistent")["id"]
                event = store.record_fact(session_id, "ready", True)
                store.add_policy(session_id, {"id": "go", "title": "Go", "tool": "go",
                                               "requirements": [{"key": "ready", "op": "eq", "value": True}]})
                action = {"tool": "go", "arguments": {}, "context_revision": store.get_session(session_id)["revision"]}
                Relay(store).execute(session_id, action, lambda *_: {"ok": True})
            with RelayStore(path) as store:
                self.assertEqual(store.facts(session_id)["ready"]["event_id"], event["id"])
                self.assertEqual(len(store.policies(session_id)), 1)
                self.assertEqual(len(store.events(session_id)), 3)
                self.assertEqual(len(store.checks(session_id)), 1)

    def test_concurrent_stale_fact_writes_only_one_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.sqlite3"
            stores = [RelayStore(path), RelayStore(path)]
            try:
                session_id = stores[0].create_session("Concurrent")["id"]
                barrier = threading.Barrier(2)
                results = []

                def update(index):
                    barrier.wait(timeout=3)
                    try:
                        stores[index].record_fact(session_id, "owner", index, expected_revision=0)
                        results.append("committed")
                    except ConflictError:
                        results.append("conflict")
                    except Exception as exc:
                        results.append(exc)

                threads = [threading.Thread(target=update, args=(i,)) for i in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                self.assertCountEqual(results, ["committed", "conflict"])
                self.assertEqual(stores[0].get_session(session_id)["revision"], 1)
            finally:
                for store in stores:
                    store.close()

    def test_concurrent_execute_serializes_check_callback_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.sqlite3"
            stores = [RelayStore(path), RelayStore(path)]
            try:
                session_id = stores[0].create_session("Concurrent execution")["id"]
                stores[0].add_policy(session_id, {"id": "go", "title": "Go", "tool": "go"})
                action = {"tool": "go", "arguments": {}, "context_revision": 1}
                barrier = threading.Barrier(2)
                callbacks = []
                results = []

                def execute(index):
                    def callback(tool, arguments):
                        callbacks.append(index)
                        time.sleep(0.03)
                        return {"done": True}

                    barrier.wait(timeout=3)
                    try:
                        results.append(Relay(stores[index]).execute(session_id, action, callback))
                    except Exception as exc:
                        results.append(exc)

                threads = [threading.Thread(target=execute, args=(i,)) for i in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(len(callbacks), 1)
                self.assertTrue(all(isinstance(result, dict) for result in results), results)
                self.assertCountEqual([result["check"]["decision"] for result in results], ["allow", "refresh"])
                self.assertEqual(stores[0].get_session(session_id)["revision"], 2)
            finally:
                for store in stores:
                    store.close()


if __name__ == "__main__":
    unittest.main()
