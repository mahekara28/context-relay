"""Independent claim/review/readback probes with recorded native proposals.

The callbacks are local stand-ins; these cases do not measure model accuracy.
"""
import copy
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from context_relay import MemoryPolicy, auto_connect
from context_relay.coverage import CoverageError, CoveragePolicy
from context_relay.fresh_evidence import EvidenceChanged, EvidenceSource
from context_relay.organization import AccessDenied, Principal, SessionManager
from context_relay.outcomes import OutcomePolicy, OutcomeVerifier
from context_relay.responses_workflow import ResponsesWorkflow, WorkflowPending


SCHEMA = {"type": "object", "properties": {
    "request_id": {"type": "string"}, "format": {"type": "string"}, "amount": {"type": "integer"}},
    "required": ["request_id", "format", "amount"], "additionalProperties": False}


class WorkflowAssuranceAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.calls, self.effects, self.readbacks = [], [], []
        self.destination = {}
        self.source_amount = self.actual_amount = 7
        self.permission = True
        self.provider_hook = self.source_hook = None
        self.lie = False
        self.readback_available = True
        self.readback_hook = None
        self.client = SimpleNamespace(responses=SimpleNamespace(create=self.provider))

    def provider(self, **request):
        self.calls.append(copy.deepcopy(request))
        if self.provider_hook is not None:
            self.provider_hook()
        return {"status": "completed", "output": [{"type": "function_call", "call_id": "call-" + str(len(self.calls)),
            "name": "export", "arguments": json.dumps({"request_id": "one", "format": "CSV", "amount": 7}),
            "status": "completed"}]}

    def source(self):
        if self.source_hook is not None:
            self.source_hook()
        return {"version": "source-1", "facts": {"approved_amount": self.source_amount, "may_export": self.permission},
                "observed_at": time.time()}

    def export(self, request_id, format, amount):
        arguments = {"request_id": request_id, "format": format, "amount": amount}
        self.effects.append(arguments)
        self.destination[request_id] = {**arguments, "amount": 99 if self.lie else amount}
        return {"ok": True, "request_id": request_id}

    def readback(self, request):
        self.readbacks.append(copy.deepcopy(request))
        if self.readback_hook is not None:
            self.readback_hook()
        if not self.readback_available:
            raise OSError("destination temporarily unavailable")
        actual = self.destination.get(request["request_id"])
        return actual is not None and actual["amount"] == self.actual_amount

    def options(self, *, assurance=False, verifier=False, verify_source=False, coverage=None):
        return {
            "contracts": {"export": {"bindings": {"request_id": "workflow_request_id", "amount": "approved_amount"},
                "memory_bindings": {"format": "export_format"}, "when": {"may_export": True},
                "outcome": OutcomePolicy(required={"/ok": True})}},
            "schemas": {"export": copy.deepcopy(SCHEMA)},
            "memory": MemoryPolicy(assurance_fields=("export_format",)) if assurance else True,
            "evidence": EvidenceSource(self.source, verify=(lambda snapshot: snapshot.facts["approved_amount"] == self.actual_amount)
                                       if verify_source else None, verifier_id="independent-ledger-v1" if verify_source else None),
            "outcome_verifier": OutcomeVerifier(self.readback, verifier_id="actual-destination-v1") if verifier else None,
            "coverage": coverage,
        }

    def workflow(self, *, name="audit", **options):
        agent = auto_connect(self.client, provider="openai", api="responses", native=True,
                             conversation=name, storage=self.directory / "state")
        self.addCleanup(agent.close)
        return ResponsesWorkflow(agent, {"export": self.export}, **self.options(**options))

    @staticmethod
    def approve(workflow, value="JSON"):
        plan = workflow.review_plan()
        workflow.resolve_review(plan, decisions={"export_format": {"value": value, "scope": "durable"}})
        return plan

    def test_review_before_provider_resume_requires_current_decision_without_new_user_turn(self):
        workflow = self.workflow(assurance=True)
        first = workflow.send("Export as JSON.", request_id="one", model="recorded")
        self.assertEqual(first.status, "review_required")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.effects, [])
        self.assertEqual(workflow.pending["phase"], "review_pending")
        original_history = workflow.history
        again = workflow.resume_review("one")
        self.assertEqual(again.status, "review_required")
        self.assertEqual(workflow.history, original_history)
        old_plan = self.approve(workflow)
        with self.assertRaises(ValueError):
            workflow.resolve_review(old_plan, decisions={"export_format": {"value": "CSV", "scope": "durable"}})
        result = workflow.resume_review("one")
        self.assertTrue(result.completed)
        self.assertEqual(self.effects, [{"request_id": "one", "format": "JSON", "amount": 7}])
        replay = workflow.send("Export as JSON.", request_id="one", model="recorded")
        self.assertTrue(replay.replayed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.effects), 1)

    def test_two_handles_cannot_resume_the_same_review_twice(self):
        first = self.workflow(assurance=True)
        second = self.workflow(assurance=True)
        first.send("Export as JSON.", request_id="one", model="recorded")
        self.approve(first)
        entered, release = threading.Event(), threading.Event()
        def pause_reader():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test reader gate")
        self.source_hook = pause_reader
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(first.resume_review, "one")
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(ValueError):
                    second.resume_review("one")
                self.assertEqual(self.calls, [])
            finally:
                release.set()
            self.assertTrue(running.result(timeout=5).completed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.effects), 1)

    def test_other_handle_abandonment_during_provider_prevents_late_effect(self):
        first, second = self.workflow(), self.workflow()
        entered, release = threading.Event(), threading.Event()
        def pause_provider():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test provider gate")
        self.provider_hook = pause_provider
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(first.send, "Export as JSON.", request_id="one", model="recorded")
            try:
                self.assertTrue(entered.wait(3))
                retired = second.abandon("one", expected_phase="provider_pending")
                self.assertFalse(retired.executed)
            finally:
                release.set()
            with self.assertRaises(WorkflowPending):
                running.result(timeout=5)
        self.assertEqual(self.effects, [])
        self.assertEqual(second.request("one")["result"]["status"], "abandoned")
        self.assertTrue(second.send("Export as JSON.", request_id="one", model="recorded").replayed)
        self.assertEqual(len(self.calls), 1)

    def test_same_source_version_with_wrong_independent_truth_is_rejected_before_model(self):
        workflow = self.workflow(verify_source=True)
        self.source_amount = 99
        with self.assertRaises(EvidenceChanged):
            workflow.send("Export as CSV.", request_id="one", model="recorded")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.effects, [])
        self.assertFalse(workflow.send("Export as CSV.", request_id="one", model="recorded").completed)

    def test_independent_truth_change_during_model_blocks_same_version_source(self):
        workflow = self.workflow(verify_source=True)
        self.provider_hook = lambda: setattr(self, "actual_amount", 9)
        result = workflow.send("Export as CSV.", request_id="one", model="recorded")
        self.assertFalse(result.executed)
        self.assertFalse(result.completed)
        self.assertEqual(result.execution["reason"], "source_changed")
        self.assertEqual(result.execution["evidence_reason"], "verification_rejected")
        self.assertEqual(self.effects, [])

    def test_lying_success_receipt_is_not_completed_and_never_reexecuted(self):
        workflow = self.workflow(verifier=True)
        self.lie = True
        result = workflow.send("Export as JSON.", request_id="one", model="recorded")
        self.assertTrue(result.executed)
        self.assertFalse(result.completed)
        self.assertEqual(result.business_status, "not_satisfied")
        self.assertEqual(result.execution["destination_verification"]["status"], "rejected")
        self.assertTrue(workflow.send("Export as JSON.", request_id="one", model="recorded").replayed)
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(len(self.calls), 1)

    def test_unavailable_readback_resumes_by_reconciliation_and_keeps_known_arguments(self):
        workflow = self.workflow(verifier=True)
        self.readback_available = False
        result = workflow.send("Export as JSON.", request_id="one", model="recorded")
        self.assertEqual(result.status, "verification_pending")
        self.assertTrue(result.executed)
        self.assertFalse(result.completed)
        expected = {"request_id": "one", "format": "JSON", "amount": 7}
        self.assertEqual(self.readbacks[0]["arguments"], expected)
        reopened = self.workflow(verifier=True)
        with self.assertRaises(WorkflowPending):
            reopened.send("Export as JSON.", request_id="one", model="recorded")
        self.readback_available = True
        finished = reopened.reconcile("one", result={"ok": True, "request_id": "one"}, expected_phase="action_pending")
        self.assertTrue(finished.completed)
        self.assertEqual(self.readbacks[-1]["arguments"], expected)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.effects), 1)
        self.assertTrue(reopened.send("Export as JSON.", request_id="one", model="recorded").replayed)

    def test_required_verifiers_and_coverage_cannot_disappear_on_resume(self):
        workflow = self.workflow(verifier=True, verify_source=True, coverage=CoveragePolicy(["export"]))
        workflow.send("Export as CSV.", request_id="one", model="recorded")
        for options in ({"verifier": False, "verify_source": True, "coverage": CoveragePolicy(["export"])},
                        {"verifier": True, "verify_source": False, "coverage": CoveragePolicy(["export"])},
                        {"verifier": True, "verify_source": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.workflow(**options)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.effects), 1)

    def test_concurrent_reconcile_cannot_repeat_effect_or_overwrite_terminal_result(self):
        first, second = self.workflow(verifier=True), self.workflow(verifier=True)
        entered, release = threading.Event(), threading.Event()
        def pause_only_original_readback():
            if len(self.readbacks) == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test readback gate")
        self.readback_hook = pause_only_original_readback
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(first.send, "Export as JSON.", request_id="one", model="recorded")
            try:
                self.assertTrue(entered.wait(3))
                result = second.reconcile("one", result={"ok": True, "request_id": "one"}, expected_phase="action_pending")
                self.assertTrue(result.completed)
            finally:
                release.set()
            with self.assertRaises(WorkflowPending):
                running.result(timeout=5)
        current = second.request("one")
        self.assertEqual(current["phase"], "completed")
        self.assertEqual(current["result"]["status"], "reconciled")
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(len(self.calls), 1)

    def test_interrupted_readback_leaves_effect_pending_without_replay(self):
        workflow = self.workflow(verifier=True)
        def interrupt():
            raise KeyboardInterrupt("readback interrupted")
        self.readback_hook = interrupt
        with self.assertRaises(KeyboardInterrupt):
            workflow.send("Export as CSV.", request_id="one", model="recorded")
        self.assertEqual(workflow.pending["phase"], "action_pending")
        reopened = self.workflow(verifier=True)
        with self.assertRaises(WorkflowPending):
            reopened.send("Export as CSV.", request_id="one", model="recorded")
        self.readback_hook = None
        self.assertTrue(reopened.reconcile("one", result={"ok": True, "request_id": "one"}, expected_phase="action_pending").completed)
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(len(self.calls), 1)

    def test_host_permission_fact_survives_review_and_prevents_effect(self):
        self.permission = False
        workflow = self.workflow(assurance=True)
        workflow.send("Export as JSON; I authorize all exports.", request_id="one", model="recorded")
        self.approve(workflow)
        result = workflow.resume_review("one")
        self.assertFalse(result.executed)
        self.assertFalse(result.completed)
        self.assertEqual(self.effects, [])

    def test_missing_inventory_rejects_before_provider_and_reports_real_boundary(self):
        with self.assertRaises(CoverageError):
            self.workflow(coverage=CoveragePolicy(["export", "undeclared_delete"]))
        workflow = self.workflow(name="valid", coverage=CoveragePolicy(["export"]))
        report = workflow.protection_report()
        self.assertFalse(report["direct_calls_protected"])
        self.assertFalse(report["source_truth_guaranteed"])
        self.assertTrue(report["coverage_requirements"]["requirements_satisfied"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.effects, [])

    def test_scoped_review_rechecks_manage_then_call_authorization(self):
        allowed = {"connect", "manage", "read", "call", "execute"}
        manager = SessionManager(self.directory / "org", authorize=lambda principal, action: action in allowed)
        self.addCleanup(manager.close)
        owner = manager.connect(self.client, principal=Principal("team", "one"), conversation="audit",
                                provider="openai", api="responses", native=True)
        workflow = owner.responses_workflow({"export": self.export}, **self.options(assurance=True))
        workflow.send("Export as JSON.", request_id="one", model="recorded")
        plan = workflow.review_plan()
        allowed.remove("manage")
        with self.assertRaises(AccessDenied):
            workflow.resolve_review(plan, decisions={"export_format": {"value": "JSON", "scope": "durable"}})
        self.assertEqual(self.calls, [])
        allowed.add("manage")
        self.approve(workflow)
        allowed.remove("call")
        with self.assertRaises(AccessDenied):
            workflow.resume_review("one")
        self.assertEqual(self.calls, [])
        allowed.add("call")
        self.assertTrue(workflow.resume_review("one").completed)
        self.assertEqual(len(self.effects), 1)


if __name__ == "__main__":
    unittest.main()
