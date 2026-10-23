"""Business-success and fresh-source boundaries with real tool effects counted."""
import time
import unittest

from context_relay import (ContextGuard, EvidenceChanged, EvidenceSource, OutcomePolicy,
                           ToolExecutionError, auto_connect)


class FreshOutcomeTests(unittest.TestCase):
    def test_declined_receipt_is_executed_but_never_business_completed_or_retried(self):
        effects = []
        with auto_connect(lambda **kw: {"tool": "save", "arguments": {}}) as agent:
            harness = agent.protect_tools({"save": lambda: effects.append(1) or {"ok": False}},
                contracts={"save": {"outcome": OutcomePolicy(required={"/ok": True})}}, repair="known")
            result = harness.invoke(messages=[{"role": "user", "content": "Save the record."}])
            self.assertTrue(result.executed)
            self.assertFalse(result.completed)
            self.assertEqual(result.execution["business_status"], "not_satisfied")
            self.assertEqual(result.execution["receipt"]["outcome"]["business_status"], "not_satisfied")
            self.assertEqual(result.execution["repair_attempts"], 0)
            self.assertEqual(effects, [1])

    def test_missing_result_fields_are_unknown_and_truthy_numbers_do_not_mean_true(self):
        policy = OutcomePolicy(required={"/ok": True, "/receipt/id": "A17"})
        for value, expected in (({"ok": True}, "unknown"),
                                ({"ok": 1, "receipt": {"id": "A17"}}, "not_satisfied"),
                                ({"ok": True, "receipt": {"id": "A17"}}, "succeeded")):
            with self.subTest(value=value):
                with ContextGuard() as guard:
                    guard.allow_tool("save", outcome=policy)
                    result = guard.turn().execute_with_recovery("save", {}, lambda name, args: value)
                    self.assertEqual(result["business_status"], expected)
                    self.assertEqual(result["completed"], expected == "succeeded")

    def test_absent_business_contract_is_unchecked_not_complete(self):
        with ContextGuard() as guard:
            guard.allow_tool("save")
            result = guard.turn().execute_with_recovery("save", {}, lambda name, args: {"ok": True})
            self.assertEqual(result["business_status"], "unchecked")
            self.assertFalse(result["completed"])

    def test_effect_then_exception_has_unknown_business_outcome(self):
        with ContextGuard() as guard:
            guard.allow_tool("save", outcome=OutcomePolicy(required={"/ok": True}))
            def effect(name, args):
                raise RuntimeError("Effect may have happened before this failure")
            with self.assertRaises(ToolExecutionError) as raised:
                guard.turn().execute_with_recovery("save", {}, effect, repair="known")
            self.assertEqual(raised.exception.receipt["outcome"]["business_status"], "unknown")
            self.assertFalse(raised.exception.receipt["outcome"]["completed"])

    def test_source_changed_within_ttl_is_not_accepted(self):
        data = {"version": "v1", "facts": {"balance": 100}, "observed_at": time.time()}
        source = EvidenceSource(lambda: data, max_age_seconds=300)
        original = source.read()
        data["facts"]["balance"] = 20
        with self.assertRaises(EvidenceChanged) as raised:
            source.assert_current(original)
        self.assertEqual(raised.exception.reason, "source_changed")
        self.assertEqual(original.facts, {"balance": 100})

    def test_expired_or_future_sources_stop_before_apply(self):
        for offset in (-31, 6):
            with self.subTest(offset=offset):
                source = EvidenceSource(lambda: {"version": "v1", "facts": {"allowed": True},
                                                  "observed_at": time.time() + offset})
                with self.assertRaises(EvidenceChanged):
                    source.read()

    def test_changed_source_refresh_invalidates_old_proposal(self):
        data = {"version": "v1", "facts": {"allowed": True, "amount": 100}, "observed_at": time.time()}
        source = EvidenceSource(lambda: data)
        with ContextGuard() as guard:
            guard.allow_tool("save", when={"allowed": True}, bindings={"amount": "amount"})
            source.read().apply(guard)
            original = guard.turn()
            data.update(version="v2", facts={"allowed": False, "amount": 20})
            source.read().apply(guard)
            effects = []
            rejected = original.execute_with_recovery("save", {"amount": 100},
                lambda name, args: effects.append(args) or {"ok": True}, repair="known")
            self.assertFalse(rejected["executed"])
            self.assertEqual(effects, [])

    def test_missing_permission_field_cannot_leave_old_permission_active(self):
        data = {"version": "v1", "facts": {"allowed": True, "amount": 100}, "observed_at": time.time()}
        source = EvidenceSource(lambda: data)
        with ContextGuard() as guard:
            source.read().apply(guard)
            before = guard.turn().revision
            data.update(version="v2", facts={"amount": 20})
            with self.assertRaises(EvidenceChanged):
                source.read().apply(guard)
            self.assertEqual(guard.turn().revision, before)
            self.assertEqual(guard.store.facts(guard.session_id)["amount"]["value"], 100)

    def test_source_reordering_and_observation_refresh_are_not_data_changes(self):
        data = {"version": "v1", "facts": {"b": 2, "a": {"y": 3, "x": 4}}, "observed_at": time.time()}
        source = EvidenceSource(lambda: data)
        first = source.read()
        data.update(facts={"a": {"x": 4, "y": 3}, "b": 2}, observed_at=time.time())
        source.assert_current(first)

    def test_evidence_cannot_replace_workflow_identity_metadata(self):
        for key in ("workflow_request_id", "workflow_source_version", "workflow_evidence_keys"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    EvidenceSource(lambda: {"version": "v1", "facts": {key: "forged"},
                                             "observed_at": time.time()}).read()


if __name__ == "__main__":
    unittest.main()
