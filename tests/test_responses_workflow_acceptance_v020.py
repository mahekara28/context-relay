"""Independent Responses workflow outcomes using local native-wire stand-ins.

No hosted inference, SDK compatibility claim, or real destination is involved.
"""

import copy
import json
import time
import unittest
from types import SimpleNamespace

from context_relay import auto_connect
from context_relay.fresh_evidence import EvidenceSource
from context_relay.outcomes import OutcomePolicy
from context_relay.responses_workflow import ResponsesWorkflow, WorkflowPending


NOTE = "Keep this customer's original wording."
SCHEMA = {
    "type": "object",
    "properties": {
        "request_id": {"type": "string"}, "source_version": {"type": "string"},
        "customer_id": {"type": "string"}, "amount": {"type": "integer"},
        "format": {"type": "string"}, "note": {"type": "string"},
    },
    "required": ["request_id", "source_version", "customer_id", "amount", "format", "note"],
    "additionalProperties": False,
}


class ResponsesWorkflowAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.provider_calls = []
        self.tool_calls = []
        self.destination = {}
        self.evidence_version = "ledger-1"
        self.evidence_facts = {"customer_id": "C-17", "approved_amount": 20, "may_export": True}
        self.receipt = {"ok": True, "receipt_id": "destination-1"}
        self.interrupt_after_effect = False
        self.authorized = True
        self.during_provider = None
        self.response = self.response_for("request-1", "call-1")
        client = SimpleNamespace(responses=SimpleNamespace(create=self.create))
        self.agent = auto_connect(client, provider="openai", api="responses", native=True)
        self.addCleanup(self.agent.close)

    def read_evidence(self):
        return {"version": self.evidence_version, "facts": copy.deepcopy(self.evidence_facts),
                "observed_at": time.time()}

    def response_for(self, request_id, call_id):
        arguments = {"request_id": request_id, "source_version": "ledger-1", "customer_id": "C-17",
                     "amount": 20, "format": "JSON", "note": NOTE}
        return {"id": "response-" + call_id, "status": "completed", "output": [
            {"type": "reasoning", "id": "reasoning-" + call_id, "summary": [],
             "encrypted_content": "opaque\n+/==", "status": "completed"},
            {"type": "function_call", "id": "function-" + call_id,
             "call_id": call_id, "name": "save", "arguments": json.dumps(arguments, indent=1),
             "status": "completed"},
        ]}

    def create(self, **request):
        self.provider_calls.append(copy.deepcopy(request))
        if self.during_provider is not None:
            self.during_provider()
        return copy.deepcopy(self.response)

    def save(self, request_id, source_version, customer_id, amount, format, note):
        arguments = {"request_id": request_id, "source_version": source_version,
                     "customer_id": customer_id, "amount": amount, "format": format, "note": note}
        self.tool_calls.append(copy.deepcopy(arguments))
        if self.interrupt_after_effect or self.receipt.get("ok") is True:
            self.destination[request_id] = {"ok": True, "receipt_id": "destination-1"}
        if self.interrupt_after_effect:
            raise KeyboardInterrupt("interrupted after destination accepted the action")
        return copy.deepcopy(self.receipt)

    def contracts(self):
        return {"save": {
            "bindings": {"request_id": "workflow_request_id", "source_version": "workflow_source_version",
                         "customer_id": "customer_id", "amount": "approved_amount"},
            "memory_bindings": {"format": "export_format"},
            "when": {"may_export": True}, "unchecked": ("note",),
            "outcome": OutcomePolicy(required={"/ok": True}),
        }}

    def workflow(self, **options):
        return ResponsesWorkflow(self.agent, tools={"save": self.save}, contracts=self.contracts(),
            schemas={"save": copy.deepcopy(SCHEMA)},
            evidence=EvidenceSource(self.read_evidence, max_age_seconds=30),
            authorize_action=lambda: self.authorized,
            instructions="Use the current approved evidence when proposing the save action.", **options)

    def send(self, workflow, request_id="request-1"):
        return workflow.send("Export as JSON.", request_id=request_id, model="offline-wire-fixture")

    def test_declined_receipt_is_executed_but_not_business_complete(self):
        self.receipt = {"ok": False, "reason": "destination declined"}
        workflow = self.workflow()
        result = self.send(workflow)
        self.assertTrue(result.executed)
        self.assertFalse(result.completed)
        self.assertEqual(result.business_status, "not_satisfied")
        self.assertEqual(len(self.tool_calls), 1)
        self.assertEqual(self.destination, {})
        replay = self.send(workflow)
        self.assertTrue(replay.replayed)
        self.assertFalse(replay.completed)
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(len(self.tool_calls), 1)

    def test_missing_success_field_is_unknown_and_never_retried_automatically(self):
        self.receipt = {"receipt_id": "incomplete-receipt"}
        workflow = self.workflow()
        result = self.send(workflow)
        self.assertTrue(result.executed)
        self.assertFalse(result.completed)
        self.assertEqual(result.business_status, "unknown")
        self.assertIsNone(workflow.pending)  # A returned receipt differs from an interrupted call.
        replay = self.send(workflow)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.business_status, "unknown")
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(len(self.tool_calls), 1)

    def test_numeric_one_does_not_satisfy_boolean_success(self):
        self.receipt = {"ok": 1}
        result = self.send(self.workflow())
        self.assertFalse(result.completed)
        self.assertEqual(result.business_status, "not_satisfied")
        self.assertEqual(self.destination, {})

    def test_changed_source_version_during_generation_blocks_original_executor(self):
        workflow = self.workflow()
        self.during_provider = lambda: setattr(self, "evidence_version", "ledger-2")
        result = self.send(workflow)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.execution["reason"], "source_changed")
        self.assertFalse(result.executed)
        self.assertFalse(result.completed)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(self.destination, {})
        self.assertIsNone(workflow.pending)
        self.assertEqual(workflow.request("request-1")["phase"], "completed")
        self.assertTrue(self.send(workflow).replayed)
        self.assertEqual(len(self.provider_calls), 1)

    def test_changed_facts_with_reused_version_are_not_treated_as_unchanged(self):
        workflow = self.workflow()
        self.during_provider = lambda: self.evidence_facts.update(approved_amount=99)
        result = self.send(workflow)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.execution["reason"], "source_changed")
        self.assertFalse(result.executed)
        self.assertFalse(result.completed)
        self.assertIsNone(workflow.pending)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(self.destination, {})

    def test_permission_revoked_during_generation_blocks_original_executor(self):
        workflow = self.workflow()
        self.during_provider = lambda: setattr(self, "authorized", False)
        result = self.send(workflow)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.execution["reason"], "authorization_denied")
        self.assertFalse(result.executed)
        self.assertFalse(result.completed)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(self.destination, {})
        self.assertIsNone(workflow.pending)
        self.assertEqual(workflow.request("request-1")["phase"], "completed")
        self.assertTrue(self.send(workflow).replayed)
        self.assertEqual(len(self.provider_calls), 1)

    def test_full_opaque_native_output_replays_exactly_with_one_matching_tool_result(self):
        workflow = self.workflow()
        original_output = copy.deepcopy(self.response["output"])
        first = self.send(workflow)
        self.assertTrue(first.completed)
        saved_history = workflow.history
        for original in original_output:
            self.assertIn(original, saved_history)
        results = [item for item in saved_history if item.get("type") == "function_call_output"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["call_id"], "call-1")
        workflow.history.clear()  # Public history is a defensive copy.
        self.response = self.response_for("request-2", "call-2")
        second = self.send(workflow, "request-2")
        self.assertTrue(second.completed)
        sent = self.provider_calls[-1]["input"]
        self.assertEqual(sent[:len(saved_history)], saved_history)
        self.assertEqual(len(self.tool_calls), 2)
        self.assertTrue(all(call["note"] == NOTE for call in self.tool_calls))

    def test_completed_request_id_never_repeats_provider_or_effect(self):
        workflow = self.workflow()
        first = self.send(workflow)
        record = workflow.request("request-1")
        record.clear()
        self.assertTrue(workflow.request("request-1"))
        replay = self.send(workflow)
        self.assertTrue(first.completed)
        self.assertTrue(replay.completed)
        self.assertTrue(replay.replayed)
        with self.assertRaises(ValueError):
            workflow.send("A different action.", request_id="request-1", model="offline-wire-fixture")
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(len(self.tool_calls), 1)
        self.assertEqual(len(self.destination), 1)

    def test_interrupted_effect_requires_explicit_destination_reconciliation(self):
        workflow = self.workflow()
        self.interrupt_after_effect = True
        with self.assertRaises(KeyboardInterrupt):
            self.send(workflow)
        self.assertEqual(len(self.tool_calls), 1)
        self.assertEqual(len(self.destination), 1)
        self.assertEqual(workflow.pending["phase"], "action_pending")
        pending = workflow.pending
        with self.assertRaises(WorkflowPending):
            self.send(workflow)
        self.assertEqual(workflow.pending, pending)
        with self.assertRaises(ValueError):
            workflow.reconcile("request-1", result=self.destination["request-1"], expected_phase="provider_pending")
        reconciled = workflow.reconcile("request-1", result=self.destination["request-1"],
                                        expected_phase="action_pending")
        self.assertTrue(reconciled.completed)
        self.assertIsNone(workflow.pending)
        self.assertTrue(self.send(workflow).replayed)
        self.assertEqual(len(self.tool_calls), 1)
        self.assertEqual(len(self.provider_calls), 1)

    def test_unsupported_compaction_action_response_fails_before_effect(self):
        workflow = self.workflow()
        self.response["output"].insert(0, {"type": "compaction", "id": "compaction-1",
            "encrypted_content": "opaque-compaction=="})
        with self.assertRaises(ValueError):
            self.send(workflow)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(self.destination, {})

    def test_unknown_native_output_field_fails_before_effect(self):
        workflow = self.workflow()
        self.response["output"][0]["provider_extension"] = {"preserve": [None, True]}
        with self.assertRaises(ValueError):
            self.send(workflow)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(self.destination, {})

    def test_unknown_provider_fields_are_rejected_before_provider(self):
        workflow = self.workflow()
        for option in ({"unrecognized_transport_option": True},
                       {"previous_response_id": "hidden-state"},
                       {"extra_body": {"tools": []}}):
            with self.subTest(option=option), self.assertRaises((TypeError, ValueError)):
                workflow.send("Export as JSON.", request_id="request-1", **option)
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(workflow.history, [])

    def test_schema_without_the_registered_tool_signature_fails_setup(self):
        invalid = copy.deepcopy(SCHEMA)
        del invalid["properties"]["amount"]
        invalid["required"].remove("amount")
        with self.assertRaises((TypeError, ValueError)):
            ResponsesWorkflow(self.agent, tools={"save": self.save}, contracts=self.contracts(),
                schemas={"save": invalid}, evidence=EvidenceSource(self.read_evidence))
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.tool_calls, [])

    def test_unsupported_schema_type_is_rejected_before_provider_or_effect(self):
        invalid = copy.deepcopy(SCHEMA)
        invalid["properties"]["note"] = {"type": "banana"}
        with self.assertRaises(ValueError):
            ResponsesWorkflow(self.agent, tools={"save": self.save}, contracts=self.contracts(),
                schemas={"save": invalid}, evidence=EvidenceSource(self.read_evidence))
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.tool_calls, [])

    def test_unchecked_business_value_still_obeys_declared_schema_type(self):
        workflow = self.workflow()
        arguments = json.loads(self.response["output"][1]["arguments"])
        arguments["note"] = 123  # No preservation rule does not mean arbitrary schema type.
        self.response["output"][1]["arguments"] = json.dumps(arguments)
        result = self.send(workflow)
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.executed)
        self.assertFalse(result.completed)
        self.assertIsNone(workflow.pending)
        self.assertEqual(self.tool_calls, [])
        self.assertEqual(self.destination, {})
        self.assertTrue(self.send(workflow).replayed)
        self.assertEqual(len(self.provider_calls), 1)

    def test_resuming_cannot_silently_drop_required_authorization_callback(self):
        original = self.workflow()
        self.authorized = False
        with self.assertRaises(ValueError):
            ResponsesWorkflow(self.agent, tools={"save": self.save}, contracts=self.contracts(),
                schemas={"save": copy.deepcopy(SCHEMA)},
                evidence=EvidenceSource(self.read_evidence, max_age_seconds=30),
                instructions="Use the current approved evidence when proposing the save action.")
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.tool_calls, [])
        result = self.send(original)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.execution["reason"], "authorization_denied")
        self.assertEqual(self.tool_calls, [])


if __name__ == "__main__":
    unittest.main()
