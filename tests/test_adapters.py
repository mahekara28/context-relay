import unittest

from context_relay import Relay, RelayStore, guarded_tool_call


class HarnessAdapterTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.addCleanup(self.store.close)
        self.relay = Relay(self.store)
        self.session_id = self.store.create_session("Harness integration")["id"]
        self.store.record_fact(self.session_id, "intent", "refund")
        self.store.record_fact(self.session_id, "order", "ORDER-42")
        self.store.add_policy(self.session_id, {
            "id": "refund", "title": "Refund only when requested", "tool": "refund",
            "requirements": [{"key": "intent", "op": "eq", "value": "refund"}],
            "bindings": {"order": "order"},
        })
        self.context = self.relay.context(self.session_id)
        self.tool_call = {"tool": "refund", "arguments": {"order": "ORDER-42"}}
        self.calls = []

    def executor(self, tool, arguments):
        self.calls.append((tool, arguments))
        return {"ok": True}

    def test_current_harness_snapshot_allows_valid_callback(self):
        result = guarded_tool_call(self.relay, self.session_id, self.context, self.tool_call, self.executor)
        self.assertTrue(result["executed"])
        self.assertEqual(result["check"]["revision"], self.context["revision"])
        self.assertEqual(self.calls, [("refund", {"order": "ORDER-42"})])

    def test_cancelled_decision_between_snapshot_and_execution_blocks(self):
        self.store.record_fact(self.session_id, "intent", "replacement")
        result = guarded_tool_call(self.relay, self.session_id, self.context, self.tool_call, self.executor)
        self.assertFalse(result["executed"])
        self.assertEqual(result["check"]["decision"], "block")
        self.assertEqual(self.calls, [])

    def test_cross_session_context_is_rejected_before_execution(self):
        other_id = self.store.create_session("Another customer")["id"]
        with self.assertRaisesRegex(ValueError, "session_id"):
            guarded_tool_call(self.relay, other_id, self.context, self.tool_call, self.executor)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.checks(other_id), [])

    def test_model_cannot_supply_revision_or_other_envelope_fields(self):
        for field, value in [("context_revision", self.context["revision"]),
                             ("session_id", self.session_id), ("force", True)]:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "exactly tool and arguments"):
                guarded_tool_call(self.relay, self.session_id, self.context,
                                  {**self.tool_call, field: value}, self.executor)
        self.assertEqual(self.calls, [])

    def test_invalid_snapshot_revision_is_rejected(self):
        for revision in (True, None, -1, "3", 3.0):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, "revision"):
                guarded_tool_call(self.relay, self.session_id, {**self.context, "revision": revision},
                                  self.tool_call, self.executor)
        self.assertEqual(self.calls, [])

    def test_future_revision_never_allows_execution(self):
        result = guarded_tool_call(self.relay, self.session_id,
                                   {**self.context, "revision": self.context["revision"] + 100},
                                   self.tool_call, self.executor)
        self.assertFalse(result["executed"])
        self.assertEqual(result["check"]["decision"], "refresh")
        self.assertEqual(self.calls, [])

    def test_unrelated_update_keeps_trusted_old_revision_and_requests_refresh(self):
        self.store.record_fact(self.session_id, "delivery_note", "Call before arrival")
        result = guarded_tool_call(self.relay, self.session_id, self.context, self.tool_call, self.executor)
        self.assertFalse(result["executed"])
        self.assertEqual(result["check"]["decision"], "refresh")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
