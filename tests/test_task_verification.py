import sys
import tempfile
import unittest
from pathlib import Path

from context_relay import RelayStore
from context_relay.command_receipt import run_command
from context_relay.organization import Principal
from context_relay.task_service import TaskService
from context_relay.task_verification import bind_command_receipt, verify_task_criteria


class TaskVerificationTests(unittest.TestCase):
    def test_receipt_is_bound_to_exact_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            path.write_text('{"ok":true}')
            store = RelayStore()
            try:
                service = TaskService(store, authorize=lambda principal, action: True)
                principal = Principal("team", "host")
                task = service.create(principal, goal="Finish", completion_criteria=["Oracle passes"],
                                      workspace={"repository": "example", "root": temporary})
                captured = service.capture_artifact(principal, task["id"], expected_revision=0,
                                                    request_id="artifact", path="result.json")
                receipt = run_command([sys.executable, "-c", "print('ok')"], cwd=temporary)
                binding = bind_command_receipt(captured, receipt, root=temporary)
                self.assertEqual(binding["outcome"], "verified")
                self.assertEqual(binding["task_revision"], 1)
                path.write_text('{"ok":false}')
                self.assertEqual(bind_command_receipt(captured, receipt, root=temporary)["outcome"], "failed")
            finally:
                store.close()

    def test_every_completion_criterion_needs_a_real_command_outcome(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "result.json").write_text("{}")
            store = RelayStore()
            try:
                service = TaskService(store, authorize=lambda principal, action: True)
                principal = Principal("team", "host")
                task = service.create(principal, goal="Finish", completion_criteria=["Syntax", "Oracle"],
                                      workspace={"repository": "example", "root": temporary})
                captured = service.capture_artifact(principal, task["id"], expected_revision=0,
                                                    request_id="artifact", path="result.json")
                commands = {"Syntax": [sys.executable, "-c", "print('ok')"],
                            "Oracle": [sys.executable, "-c", "raise SystemExit(1)"]}
                result = verify_task_criteria(captured, root=temporary, commands=commands)
                self.assertFalse(result["passed"])
                self.assertEqual([item["binding"]["outcome"] for item in result["checks"]],
                                 ["verified", "failed"])
                with self.assertRaises(ValueError):
                    verify_task_criteria(captured, root=temporary, commands={"Syntax": commands["Syntax"]})
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
