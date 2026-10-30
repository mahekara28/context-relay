import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import ConflictError, RelayStore
from context_relay.organization import Principal
from context_relay.task_actions import check_file_action
from context_relay.task_effects import TaskEffectService, _apply_file_effect
from context_relay.task_service import TaskService


class TaskActionTests(unittest.TestCase):
    def test_action_check_is_revisioned_and_workspace_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "report.json").write_text("{}")
            store = RelayStore()
            try:
                service = TaskService(store, authorize=lambda principal, action: True)
                principal = Principal("team", "host")
                task = service.create(principal, goal="Finish", completion_criteria=["Done"],
                                      workspace={"repository": "example", "root": temporary})
                checked = check_file_action(service, principal, task["id"], expected_revision=0,
                                            operation="write_file", path="report.json",
                                            allowed_operations={"write_file"})
                self.assertTrue(checked["precondition_digest"].startswith("sha256:"))
                with self.assertRaises(ValueError):
                    check_file_action(service, principal, task["id"], expected_revision=0,
                                      operation="write_file", path="../outside",
                                      allowed_operations={"write_file"})
                service.update(principal, task["id"], expected_revision=0,
                               request_id="change", changes={"open_work": ["Review"]})
                with self.assertRaises(ConflictError):
                    check_file_action(service, principal, task["id"], expected_revision=0,
                                      operation="write_file", path="report.json",
                                      allowed_operations={"write_file"})
            finally:
                store.close()

    def test_intent_is_durable_and_destination_key_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = RelayStore(Path(temporary) / "tasks.db")
            try:
                principal = Principal("team", "host")
                tasks = TaskService(store, authorize=lambda identity, action: True)
                task = tasks.create(principal, goal="Finish", completion_criteria=["Done"],
                                    workspace={"repository": "example", "root": temporary})
                effects = TaskEffectService(tasks)
                prepared = effects.prepare(principal, task["id"], expected_revision=0,
                                           destination_key="write-1", operation="write_file",
                                           path="result.json", content=b"{}",
                                           allowed_operations={"write_file"})
                self.assertEqual(prepared["status"], "prepared")
                self.assertEqual(effects.prepare(principal, task["id"], expected_revision=0,
                                                 destination_key="write-1", operation="write_file",
                                                 path="result.json", content=b"{}",
                                                 allowed_operations={"write_file"}), prepared)
                with self.assertRaises(ValueError):
                    effects.prepare(principal, task["id"], expected_revision=0,
                                    destination_key="write-1", operation="write_file",
                                    path="result.json", content=b"different",
                                    allowed_operations={"write_file"})
            finally:
                store.close()

    def test_file_effect_executes_outside_database_write_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = RelayStore(Path(temporary) / "tasks.db")
            try:
                principal = Principal("team", "host")
                tasks = TaskService(store, authorize=lambda identity, action: True)
                task = tasks.create(principal, goal="Finish", completion_criteria=["Done"],
                                    workspace={"repository": "example", "root": temporary})
                effects = TaskEffectService(tasks)
                prepared = effects.prepare(principal, task["id"], expected_revision=0,
                                           destination_key="write", operation="write_file",
                                           path="result.json", content=b"{}",
                                           allowed_operations={"write_file"})

                def callback(root, effect, content):
                    self.assertFalse(store._connection.in_transaction)
                    _apply_file_effect(root, effect, content)

                with patch("context_relay.task_effects._apply_file_effect", side_effect=callback):
                    result = effects.execute(principal, prepared["id"], content=b"{}")
                self.assertEqual(result["status"], "applied")
                self.assertEqual((Path(temporary) / "result.json").read_bytes(), b"{}")
                self.assertEqual(effects.prepare(principal, task["id"], expected_revision=0,
                                                 destination_key="write", operation="write_file",
                                                 path="result.json", content=b"{}",
                                                 allowed_operations={"write_file"})["status"], "applied")
            finally:
                store.close()

    def test_reconcile_uses_destination_bytes_without_replaying(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = RelayStore(Path(temporary) / "tasks.db")
            try:
                principal = Principal("team", "host")
                tasks = TaskService(store, authorize=lambda identity, action: True)
                task = tasks.create(principal, goal="Finish", completion_criteria=["Done"],
                                    workspace={"repository": "example", "root": temporary})
                effects = TaskEffectService(tasks)
                prepared = effects.prepare(principal, task["id"], expected_revision=0,
                                           destination_key="write", operation="write_file",
                                           path="result.json", content=b"{}",
                                           allowed_operations={"write_file"})
                with store._transaction(write=True):
                    store._connection.execute("UPDATE task_effects SET status='running' WHERE id=?",
                                              (prepared["id"],))
                (Path(temporary) / "result.json").write_bytes(b"{}")
                result = effects.reconcile(principal, prepared["id"])
                self.assertEqual(result["status"], "applied")
                with store._transaction():
                    observation = store._connection.execute(
                        "SELECT conclusion, observed_digest FROM task_effect_observations WHERE effect_id=?",
                        (prepared["id"],),
                    ).fetchone()
                self.assertEqual(observation["conclusion"], "applied")
                self.assertEqual(observation["observed_digest"], prepared["desired_digest"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
