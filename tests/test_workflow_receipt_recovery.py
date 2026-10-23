"""Durable checked arguments across interrupted workflow effects and readback."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import OutcomePolicy, OutcomeVerifier, RelayConfig, auto_connect
from context_relay.models import ToolExecutionError
from context_relay.responses_workflow import WorkflowLimitExceeded, WorkflowPending


class WorkflowReceiptRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / "workflow.sqlite3")
        self.calls, self.effects, self.readbacks = [], [], []
        self.fail_effect = True
        self.readback_mode = "available"
        self.proposed = 999
        self.large = False
        self.agents = []
        self.session_id = None

    def provider(self, **request):
        self.calls.append(copy.deepcopy(request))
        return {"status": "completed", "output": [{
            "type": "function_call", "call_id": "call-" + str(len(self.calls)),
            "name": "write", "arguments": json.dumps({"value": self.proposed}),
            "status": "completed"}]}

    def write(self, value):
        self.effects.append(copy.deepcopy(value))
        if self.fail_effect:
            raise RuntimeError("destination committed, response delivery failed")
        return {"ok": True}

    def verify(self, request):
        self.readbacks.append(copy.deepcopy(request))
        if self.readback_mode == "interrupted":
            raise KeyboardInterrupt("readback interrupted")
        if self.readback_mode == "unavailable" or request["arguments"] is None:
            raise OSError("checked destination readback unavailable")
        return request["arguments"] == {"value": self.effects[-1]}

    def workflow(self):
        client = SimpleNamespace(responses=SimpleNamespace(create=self.provider))
        agent = auto_connect(client, provider="openai", api="responses", native=True,
            config=RelayConfig(db_path=self.path, max_chars=200000), session_id=self.session_id)
        self.agents.append(agent)
        self.addCleanup(agent.close)
        if self.session_id is None:
            self.session_id = agent.session_id
            if not self.large:
                agent.remember("approved", 7)
        contract = {"unchecked": ["value"]} if self.large else {"bindings": {"value": "approved"}}
        contract["outcome"] = OutcomePolicy(required={"/ok": True})
        return agent.responses_workflow({"write": self.write}, contracts={"write": contract},
            schemas={"write": {"type": "object", "properties": {
                "value": {"type": "string" if self.large else "integer"}},
                "required": ["value"], "additionalProperties": False}},
            memory=False, outcome_verifier=OutcomeVerifier(self.verify, verifier_id="destination-v1"))

    def restart(self):
        for agent in self.agents:
            agent.close()
        return self.workflow()

    @staticmethod
    def send(workflow, request_id="one"):
        return workflow.send("Write the approved value.", request_id=request_id, model="recorded")

    @staticmethod
    def output(workflow):
        return json.loads(workflow.history[-1]["output"])

    def test_repaired_exception_keeps_receipt_across_restart_and_reconciliation(self):
        workflow = self.workflow()
        with self.assertRaises(ToolExecutionError) as caught:
            self.send(workflow)
        receipt = caught.exception.receipt
        self.assertEqual(receipt["arguments"], {"value": 7})
        self.assertEqual(self.effects, [7])
        self.assertEqual(workflow.pending["phase"], "action_pending")

        reopened = self.restart()
        with self.assertRaises(WorkflowPending):
            self.send(reopened)
        result = reopened.reconcile("one", result={"ok": True}, expected_phase="action_pending")
        self.assertTrue(result.completed)
        self.assertFalse(result.executed)
        self.assertFalse(result.execution["callback_reexecuted"])
        self.assertEqual(result.execution["receipt"]["id"], receipt["id"])
        self.assertEqual(self.readbacks[-1]["arguments"], {"value": 7})
        self.assertEqual(self.output(reopened)["checked_arguments"], {"value": 7})
        self.assertNotIn("repair_attempts", self.output(reopened))
        self.assertEqual(json.loads(result.response["output"][0]["arguments"]), {"value": 999})
        self.assertTrue(self.send(reopened).replayed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.effects, [7])
        self.assertEqual(len(self.readbacks), 1)

    def test_other_handle_can_reconcile_before_exception_reaches_workflow(self):
        first, second = self.workflow(), self.workflow()
        dispatch = first._harness._finish
        reconciled = []
        def reconcile_after_engine_commit(*args):
            try:
                return dispatch(*args)
            except ToolExecutionError:
                reconciled.append(second.reconcile("one", result={"ok": True}, expected_phase="action_pending"))
                raise
        first._harness._finish = reconcile_after_engine_commit
        with self.assertRaises(ToolExecutionError):
            self.send(first)
        self.assertTrue(reconciled[0].completed)
        self.assertEqual(second.request("one")["result"]["status"], "reconciled")
        self.assertIsNone(second.pending)
        self.assertEqual(self.output(second)["checked_arguments"], {"value": 7})
        self.assertEqual(self.effects, [7])
        self.assertEqual(len(self.calls), 1)

    def check_readback_recovery(self, mode):
        self.fail_effect = False
        self.readback_mode = mode
        workflow = self.workflow()
        if mode == "interrupted":
            with self.assertRaises(KeyboardInterrupt):
                self.send(workflow)
        else:
            self.assertEqual(self.send(workflow).status, "verification_pending")
        reopened = self.restart()
        self.readback_mode = "available"
        result = reopened.reconcile("one", result={"ok": True}, expected_phase="action_pending")
        self.assertTrue(result.completed)
        self.assertEqual(self.readbacks[-1]["arguments"], {"value": 7})
        self.assertEqual(self.output(reopened)["checked_arguments"], {"value": 7})
        self.assertEqual(result.execution["receipt"]["status"], "succeeded")
        if mode == "unavailable":
            self.assertEqual(self.output(reopened)["repair_attempts"], 1)
        else:
            self.assertNotIn("repair_attempts", self.output(reopened))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.effects, [7])

    def test_interrupted_readback_keeps_original_receipt(self):
        self.check_readback_recovery("interrupted")

    def test_unavailable_readback_keeps_original_receipt(self):
        self.check_readback_recovery("unavailable")

    def test_older_verification_pending_record_still_supplies_checked_arguments(self):
        self.fail_effect = False
        self.readback_mode = "unavailable"
        workflow = self.workflow()
        self.assertEqual(self.send(workflow).status, "verification_pending")
        with workflow._store._transaction(write=True):
            record = workflow._record("one")
            record.pop("action_receipt_sequence", None)
            workflow._save_record(record)
        reopened = self.restart()
        self.readback_mode = "available"
        result = reopened.reconcile("one", result={"ok": True}, expected_phase="action_pending")
        self.assertTrue(result.completed)
        self.assertEqual(self.output(reopened)["checked_arguments"], {"value": 7})
        self.assertEqual(self.effects, [7])

    def test_successive_requests_recover_only_their_own_tool_receipts(self):
        workflow = self.workflow()
        receipts = []
        for request_id, approved in (("first", 7), ("second", 8)):
            if approved != 7:
                self.agents[-1].remember("approved", approved)
            with self.assertRaises(ToolExecutionError) as caught:
                self.send(workflow, request_id)
            receipts.append(caught.exception.receipt["id"])
            result = workflow.reconcile(request_id, result={"ok": True}, expected_phase="action_pending")
            self.assertTrue(result.completed)
            self.assertEqual(result.execution["receipt"]["id"], receipts[-1])
            self.assertEqual(self.readbacks[-1]["arguments"], {"value": approved})
            self.assertEqual(self.output(workflow)["checked_arguments"], {"value": approved})
        self.assertNotEqual(*receipts)
        self.assertEqual(workflow.request("first")["result"]["execution"]["receipt"]["arguments"], {"value": 7})
        self.assertEqual(self.effects, [7, 8])

    def test_reconciliation_uses_executed_arguments_after_current_facts_change(self):
        workflow = self.workflow()
        with self.assertRaises(ToolExecutionError):
            self.send(workflow)
        self.agents[-1].remember("approved", 9)
        reopened = self.restart()
        result = reopened.reconcile("one", result={"ok": True}, expected_phase="action_pending")
        self.assertTrue(result.completed)
        self.assertEqual(self.readbacks[-1]["arguments"], {"value": 7})
        self.assertEqual(self.output(reopened)["checked_arguments"], {"value": 7})
        with self.assertRaises(ToolExecutionError):
            self.send(reopened, "two")
        result = reopened.reconcile("two", result={"ok": True}, expected_phase="action_pending")
        self.assertTrue(result.completed)
        self.assertEqual(self.output(reopened)["checked_arguments"], {"value": 9})
        self.assertEqual(self.effects, [7, 9])

    def test_unstarted_interruption_does_not_borrow_an_earlier_receipt(self):
        self.fail_effect = False
        workflow = self.workflow()
        self.assertTrue(self.send(workflow, "earlier").completed)
        def interrupted(*args):
            raise KeyboardInterrupt("stopped before dispatch")
        workflow._harness._finish = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.send(workflow)
        reopened = self.restart()
        result = reopened.reconcile("one", result={"ok": True}, expected_phase="action_pending")
        self.assertEqual(result.status, "verification_pending")
        self.assertFalse(result.completed)
        self.assertIsNone(self.readbacks[-1]["arguments"])
        self.assertNotIn("receipt", result.execution)
        self.assertEqual(reopened.pending["phase"], "action_pending")
        self.assertEqual(self.effects, [7])

    def test_interrupted_receipt_transaction_rolls_back_linkage_and_stays_unknown(self):
        workflow = self.workflow()
        append = workflow._store._append_event
        def interrupt_receipt(session_id, kind, payload):
            if kind == "tool_result":
                raise KeyboardInterrupt("interrupted before receipt transaction commit")
            return append(session_id, kind, payload)
        workflow._store._append_event = interrupt_receipt
        with self.assertRaises(KeyboardInterrupt):
            self.send(workflow)
        self.assertEqual(self.effects, [7])
        self.assertNotIn("action_receipt_sequence", workflow.pending)
        reopened = self.restart()
        result = reopened.reconcile("one", result={"ok": True}, expected_phase="action_pending")
        self.assertFalse(result.completed)
        self.assertEqual(result.status, "verification_pending")
        self.assertIsNone(self.readbacks[-1]["arguments"])
        self.assertNotIn("receipt", result.execution)
        self.assertEqual(reopened.pending["phase"], "action_pending")
        self.assertEqual(self.effects, [7])

    def test_reconciliation_bounds_include_retained_checked_arguments(self):
        self.large = True
        self.proposed = "x" * 62000
        workflow = self.workflow()
        with self.assertRaises(ToolExecutionError):
            self.send(workflow)
        reopened = self.restart()
        with self.assertRaises(WorkflowLimitExceeded):
            reopened.reconcile("one", result={"ok": True, "detail": "y" * 3000}, expected_phase="action_pending")
        self.assertEqual(reopened.pending["phase"], "action_pending")
        self.assertFalse(any(item.get("type") == "function_call_output" for item in reopened.history))
        result = reopened.reconcile("one", result={"ok": True}, expected_phase="action_pending")
        self.assertTrue(result.completed)
        self.assertEqual(self.output(reopened)["checked_arguments"], {"value": self.proposed})
        self.assertEqual(self.effects, [self.proposed])
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
