"""Complete owner/native/evidence/tool/file workflow; model responses are scripted."""
import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from context_relay import (AccessDenied, EvidenceSource, Principal, RelayConfig, SessionManager,
                           ToolExecutionError, WorkflowPending)

_spec = importlib.util.spec_from_file_location("release_desk_example", Path(__file__).resolve().parents[1] / "examples" / "release_desk.py")
example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(example)
ROWS = [{"filename": "ACME-17.whl", "bytes": 20, "sha256": "a" * 64}]


class ReleaseDeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.desk = example.ReleaseDesk(self.root / "destination")
        self.version = self.desk.set_source("v0.20-alpha", ROWS)
        self.allowed, self.count = True, 0
        self.formats = iter(["CSV", "JSON", "CSV"])
        self.manager = SessionManager(self.root / "sessions", authorize=lambda principal, action: self.allowed)
        self.addCleanup(self.manager.close)
        self.client = SimpleNamespace(responses=SimpleNamespace(create=self.provider))
        self.identity = Principal("local", "maintainer")

    def provider(self, **request):
        self.count += 1
        arguments = {"request_id": "request-" + str(self.count), "source_version": self.version,
                     "format": next(self.formats), "release": "v0.20-alpha", "artifacts": ROWS}
        return {"status": "completed", "output": [{"type": "function_call", "name": "export_manifest",
            "arguments": json.dumps(arguments), "call_id": "call-" + str(self.count), "status": "completed"}]}

    def session(self):
        return self.manager.connect(self.client, principal=self.identity, provider="openai", api="responses", native=True,
                                    conversation="release", config=RelayConfig(max_chars=64000))

    def workflow(self, session):
        return session.responses_workflow({"export_manifest": self.desk.export_manifest},
            contracts=example.contracts(), schemas={"export_manifest": example.SCHEMA},
            instructions=example.INSTRUCTIONS, evidence=EvidenceSource(self.desk.read_evidence))

    def test_complete_owner_workflow_writes_correct_files_with_temporary_override_and_resume(self):
        with self.session() as session:
            workflow = self.workflow(session)
            messages = ["Export as CSV.", "For this export only, use JSON.", "Export the next release manifest."]
            for index, message in enumerate(messages, 1):
                result = workflow.send(message, request_id="request-" + str(index), model="scripted")
                self.assertTrue(result.completed)
            self.assertEqual([row["receipt"]["format"] for row in self.desk.documents()], ["CSV", "JSON", "CSV"])
            documents = self.desk.documents()
            self.assertEqual(json.loads(documents[1]["document"]), {"release": "v0.20-alpha", "artifacts": ROWS})
            self.assertEqual(list(csv.reader(io.StringIO(documents[0]["document"])))[1],
                             ["v0.20-alpha", "ACME-17.whl", "20", "a" * 64])
            paths = list((self.root / "destination").glob("*.json")) + list((self.root / "destination").glob("*.csv"))
            self.assertEqual(len(paths), 3)
            self.assertTrue(all(path.read_bytes() for path in paths))
        with self.session() as session:
            replay = self.workflow(session).send("Export as CSV.", request_id="request-1", model="scripted")
            self.assertTrue(replay.replayed)
            self.assertTrue(replay.completed)
        self.assertEqual(self.count, 3)
        self.assertEqual(len(self.desk.documents()), 3)

    def test_after_commit_file_failure_reconciles_without_repeating_model_or_effect(self):
        with self.session() as session:
            workflow = self.workflow(session)
            with patch.object(self.desk, "_materialize", side_effect=OSError("disk unavailable")):
                with self.assertRaises(ToolExecutionError):
                    workflow.send("Export as CSV.", request_id="request-1", model="scripted")
            self.assertEqual(workflow.pending["phase"], "action_pending")
            self.assertEqual(len(self.desk.documents()), 1)
            with self.assertRaises(WorkflowPending):
                workflow.send("Export as CSV.", request_id="request-1", model="scripted")
            receipt = self.desk.reconcile("request-1")
            result = workflow.reconcile("request-1", result=receipt, expected_phase="action_pending")
            self.assertTrue(result.completed)
            self.assertFalse(result.execution["callback_reexecuted"])
            self.assertEqual(self.count, 1)
            self.assertEqual(len(self.desk.documents()), 1)
            self.assertEqual(len(list((self.root / "destination").glob("*.csv"))), 1)

    def test_destination_version_check_closes_gap_after_relay_freshness_check(self):
        self.desk.set_source("v0.20-alpha", ROWS, may_export=False)
        result = self.desk.export_manifest("request-1", self.version, "CSV", "v0.20-alpha", ROWS)
        self.assertIs(result["ok"], False)
        self.assertEqual(self.desk.documents(), [])

    def test_owner_revocation_blocks_reads_and_reconciliation(self):
        with self.session() as session:
            workflow = self.workflow(session)
            self.allowed = False
            for operation in (lambda: workflow.history, lambda: workflow.pending,
                              lambda: workflow.reconcile("request-1", result={"ok": True}, expected_phase="action_pending"),
                              lambda: workflow.deletion_plan(), lambda: workflow.delete_history(expected={})):
                with self.assertRaises(AccessDenied):
                    operation()
        self.assertEqual(self.desk.documents(), [])
        self.assertEqual(self.count, 0)


if __name__ == "__main__":
    unittest.main()
