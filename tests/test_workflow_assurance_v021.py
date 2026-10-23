"""One protected workflow from reviewed intent to independently checked outcome."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import (CoverageError, CoveragePolicy, EvidenceChanged, EvidenceSource, MemoryPolicy,
                           OutcomePolicy, OutcomeVerifier, RelayConfig, WorkflowHistoryPolicy,
                           WorkflowPending, auto_connect)


class WorkflowAssuranceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "private" / "state.sqlite3"
        self.calls, self.effects, self.readbacks = [], [], []
        self.verdict, self.source_valid = True, True
        self.agent = self.connect()
        self.schemas = {"save": {"type": "object", "properties": {
            "format": {"type": "string", "enum": ["CSV", "JSON"]}, "value": {"type": "integer"}},
            "required": ["format", "value"], "additionalProperties": False}}
        self.contracts = {"save": {"bindings": {"value": "approved_value"},
            "memory_bindings": {"format": "export_format"}, "outcome": OutcomePolicy(required={"/ok": True})}}

    def connect(self, session_id=None):
        client = SimpleNamespace(responses=SimpleNamespace(create=self.provider))
        agent = auto_connect(client, provider="openai", api="responses", native=True,
                             config=RelayConfig(db_path=str(self.path), max_chars=48000), session_id=session_id)
        self.addCleanup(agent.close)
        return agent

    def provider(self, **request):
        self.calls.append(request)
        return {"status": "completed", "output": [{"type": "function_call", "call_id": f"call-{len(self.calls)}",
            "name": "save", "arguments": '{"format":"CSV","value":70}', "status": "completed"}]}

    def save(self, format, value):
        self.effects.append({"format": format, "value": value})
        return {"ok": True, "value": value}

    def readback(self, request):
        self.readbacks.append(request)
        return self.verdict

    def workflow(self, agent=None, **options):
        defaults = {"memory": MemoryPolicy(assurance_fields=("export_format",)),
            "history_policy": WorkflowHistoryPolicy(rollover_after_chars=6000),
            "coverage": CoveragePolicy(["save"]),
            "outcome_verifier": OutcomeVerifier(self.readback, verifier_id="destination-v1"),
            "evidence": EvidenceSource(lambda: {"version": "v1", "facts": {"approved_value": 7}, "observed_at": time.time()},
                verify=lambda snapshot: self.source_valid, verifier_id="source-v1")}
        return (agent or self.agent).responses_workflow({"save": self.save}, contracts=self.contracts,
                                                       schemas=self.schemas, **(defaults | options))

    def review(self, workflow, value, scope="durable"):
        plan = workflow.review_plan()
        workflow.resolve_review(plan, decisions={"export_format": {"value": value, "scope": scope}})
        return workflow.resume_review(plan["request_id"])

    def test_unfamiliar_change_review_repair_and_replay_finish_once(self):
        workflow = self.workflow()
        first = workflow.send("Export as CSV.", request_id="one", model="scripted")
        self.assertEqual(first.status, "review_required")
        self.assertEqual(self.calls + self.effects, [])
        self.assertTrue(self.review(workflow, "CSV").completed)
        second = workflow.send("The receiving parser chokes on commas; give it a JSON object this time.", request_id="two", model="scripted")
        self.assertEqual(second.status, "review_required")
        self.assertEqual(len(self.calls), 1)
        result = self.review(workflow, "JSON", "turn")
        self.assertTrue(result.completed)
        self.assertEqual(self.effects, [{"format": "CSV", "value": 7}, {"format": "JSON", "value": 7}])
        self.assertEqual(self.readbacks[-1]["arguments"], {"format": "JSON", "value": 7})
        self.assertTrue(workflow.send("The receiving parser chokes on commas; give it a JSON object this time.", request_id="two", model="scripted").replayed)
        self.assertEqual(len(self.calls), 2)
        with self.assertRaises(ValueError):
            workflow.resume_review("two")

    def test_review_can_resume_after_restart_without_new_user_turn(self):
        workflow = self.workflow()
        workflow.send("Export as JSON.", request_id="one", model="scripted")
        plan, session_id = workflow.review_plan(), workflow.session_id
        self.agent.close()
        resumed = self.workflow(self.connect(session_id))
        resumed.resolve_review(plan, decisions={"export_format": {"value": "JSON", "scope": "durable"}})
        self.assertTrue(resumed.resume_review("one").completed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len([m for m in resumed.history if m.get("content") == "Export as JSON."]), 1)

    def test_no_review_no_model_even_when_resume_is_called(self):
        workflow = self.workflow()
        workflow.send("Export as JSON.", request_id="one", model="scripted")
        self.assertEqual(workflow.resume_review("one").status, "review_required")
        self.assertEqual(self.calls + self.effects, [])
        self.assertTrue(self.review(workflow, "JSON").completed)

    def test_bad_source_blocks_before_inference(self):
        self.source_valid = False
        workflow = self.workflow(memory=True)
        with self.assertRaises(EvidenceChanged):
            workflow.send("Export as JSON.", request_id="one", model="scripted")
        self.assertEqual(self.calls + self.effects, [])
        self.assertFalse(workflow.request("one")["result"]["completed"])

    def test_lying_receipt_does_not_count_as_completed(self):
        self.verdict = False
        workflow = self.workflow(memory=True)
        result = workflow.send("Export as JSON.", request_id="one", model="scripted")
        self.assertTrue(result.executed)
        self.assertFalse(result.completed)
        self.assertEqual(result.business_status, "not_satisfied")
        self.assertEqual(result.execution["destination_verification"]["status"], "rejected")
        self.assertTrue(workflow.send("Export as JSON.", request_id="one", model="scripted").replayed)
        self.assertEqual(len(self.effects), 1)

    def test_unavailable_readback_can_reconcile_without_repeating_action(self):
        self.verdict = None
        workflow = self.workflow(memory=True)
        result = workflow.send("Export as JSON.", request_id="one", model="scripted")
        self.assertEqual(result.status, "verification_pending")
        self.assertFalse(result.completed)
        self.assertEqual(workflow.pending["phase"], "action_pending")
        with self.assertRaises(WorkflowPending):
            workflow.send("Export as JSON.", request_id="one", model="scripted")
        self.verdict = True
        result = workflow.reconcile("one", result={"ok": True, "value": 7}, expected_phase="action_pending")
        self.assertTrue(result.completed)
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(workflow.pending)

    def test_resume_cannot_silently_drop_protection(self):
        self.workflow()
        for changed in ({"outcome_verifier": None}, {"coverage": None}, {"memory": True},
                        {"evidence": EvidenceSource(lambda: None)}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.workflow(self.connect(self.agent.session_id), **changed)

    def test_protection_report_is_content_free_and_explicit(self):
        workflow = self.workflow(memory=True)
        workflow.send("Export as JSON.", request_id="one", model="scripted")
        report = workflow.protection_report(["save", "bypass"])
        self.assertEqual(report["coverage"]["unregistered_tools"], ["bypass"])
        self.assertFalse(report["direct_calls_protected"])
        self.assertFalse(report["source_truth_guaranteed"])
        self.assertNotIn(str(self.path), json.dumps(report))
        self.assertFalse(report["coverage_requirements"]["requirements_satisfied"])
        self.assertEqual(report["coverage"]["tools"]["save"]["checked"]["value"], {"fact": "approved_value"})
        self.assertNotIn('"facts":', json.dumps(report))
        self.assertTrue(report["storage"]["secure_delete"])

    def test_missing_declared_tool_is_setup_failure(self):
        with self.assertRaises(CoverageError):
            self.workflow(coverage=CoveragePolicy(["save", "bypass"]))
        self.assertEqual(self.calls + self.effects, [])


if __name__ == "__main__":
    unittest.main()
