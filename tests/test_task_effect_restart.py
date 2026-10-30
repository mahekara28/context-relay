import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import RelayStore
from context_relay.organization import Principal
from context_relay.task_effects import TaskEffectService
from context_relay.task_service import TaskService


def _crash_after_file_write(database, effect_id):
    from context_relay.task_effects import _apply_file_effect

    store = RelayStore(database)
    effects = TaskEffectService(TaskService(store, authorize=lambda principal, action: True))

    def write_then_die(root, effect, content):
        _apply_file_effect(root, effect, content)
        os._exit(9)

    with patch("context_relay.task_effects._apply_file_effect", side_effect=write_then_die):
        effects.execute(Principal("team", "host"), effect_id, content=b"verified bytes")


class TaskEffectRestartTests(unittest.TestCase):
    def test_restart_after_real_file_effect_reconciles_without_second_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = str(Path(temporary) / "tasks.db")
            store = RelayStore(database)
            try:
                principal = Principal("team", "host")
                tasks = TaskService(store, authorize=lambda identity, action: True)
                task = tasks.create(principal, goal="Write artifact", completion_criteria=["Bytes present"],
                                    workspace={"repository": "example", "root": temporary})
                effect = TaskEffectService(tasks).prepare(
                    principal, task["id"], expected_revision=0,
                    destination_key="write-once", operation="write_file", path="result.txt",
                    content=b"verified bytes", allowed_operations={"write_file"})
            finally:
                store.close()
            child = multiprocessing.get_context("spawn").Process(
                target=_crash_after_file_write, args=(database, effect["id"]))
            child.start()
            child.join(timeout=15)
            self.assertEqual(child.exitcode, 9)
            artifact = Path(temporary) / "result.txt"
            self.assertEqual(artifact.read_bytes(), b"verified bytes")
            restarted = RelayStore(database)
            try:
                effects = TaskEffectService(TaskService(restarted, authorize=lambda identity, action: True))
                self.assertEqual(effects.inspect(principal, effect["id"])["status"], "running")
                self.assertEqual(effects.reconcile(principal, effect["id"])["status"], "applied")
                self.assertEqual(artifact.read_bytes(), b"verified bytes")
            finally:
                restarted.close()


if __name__ == "__main__":
    unittest.main()
