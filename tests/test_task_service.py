import tempfile
import unittest
from pathlib import Path

from context_relay import ConflictError, RelayStore
from context_relay.organization import AccessDenied, Principal
from context_relay.task_service import TaskNotFound, TaskRequestConflict, TaskService, inspect_git_workspace


class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = RelayStore(Path(self.temporary.name) / "relay.db")
        self.service = TaskService(self.store, authorize=lambda principal, action: action != "denied")
        self.alice = Principal("team-a", "alice")
        self.bob = Principal("team-b", "bob")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def create(self):
        return self.service.create(self.alice, goal="Finish report", completion_criteria=["Oracle passes"],
                                   workspace={"repository": "context-relay", "root": "/work"})

    def test_create_and_read_persist_across_connections(self):
        task = self.create()
        self.assertEqual(task["revision"], 0)
        with RelayStore(self.store.path) as other:
            second = TaskService(other, authorize=lambda principal, action: True)
            self.assertEqual(second.show(self.alice, task["id"]), task)

    def test_tenant_scope_does_not_reveal_task(self):
        task = self.create()
        with self.assertRaises(TaskNotFound):
            self.service.show(self.bob, task["id"])

    def test_permission_denial(self):
        denied = TaskService(self.store, authorize=lambda principal, action: False)
        with self.assertRaises(AccessDenied):
            denied.create(self.alice, goal="X", completion_criteria=["Y"],
                          workspace={"repository": "repo", "root": "/work"})

    def test_update_is_cas_and_idempotent(self):
        task = self.create()
        updated = self.service.update(self.alice, task["id"], expected_revision=0,
                                      request_id="update-1", changes={"open_work": ["Run oracle"]})
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(self.service.update(self.alice, task["id"], expected_revision=0,
                                             request_id="update-1", changes={"open_work": ["Run oracle"]}), updated)
        with self.assertRaises(ConflictError):
            self.service.update(self.alice, task["id"], expected_revision=0,
                                request_id="update-2", changes={"goal": "Stale"})
        with self.assertRaises(TaskRequestConflict):
            self.service.update(self.alice, task["id"], expected_revision=0,
                                request_id="update-1", changes={"goal": "Different"})
        self.assertEqual(self.service.show(self.alice, task["id"]), updated)

    def test_agent_report_cannot_claim_host_authority(self):
        task = self.create()
        reporting = TaskService(self.store, authorize=lambda principal, action: True,
                                source_class="agent_report")
        with self.assertRaises(AccessDenied):
            reporting.create(self.alice, goal="Fake", completion_criteria=["Anything"],
                             workspace={"repository": "repo", "root": "/work"})
        with self.assertRaises(AccessDenied):
            reporting.update(self.alice, task["id"], expected_revision=0,
                             request_id="claim", changes={"goal": "Done"})
        report = reporting.update(self.alice, task["id"], expected_revision=0,
                                  request_id="report", changes={"open_work": ["I think it works"]})
        self.assertEqual(report["revision"], 1)
        with self.store._transaction():
            event = self.store._connection.execute(
                "SELECT source_class FROM task_events WHERE task_id=? AND revision=1", (task["id"],)
            ).fetchone()
        self.assertEqual(event["source_class"], "agent_report")

    def test_workspace_root_must_be_explicit_absolute_path(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.service.create(self.alice, goal="X", completion_criteria=["Y"],
                                workspace={"repository": "repo", "root": "relative"})

    def test_inspects_real_git_worktree_identity(self):
        repo = Path(__file__).resolve().parents[1]
        identity = inspect_git_workspace(repo)
        self.assertEqual(identity["root"], str(repo))
        self.assertTrue(identity["head"])
        self.assertTrue(identity["repository"])

    def test_instruction_supersession_is_explicit_and_scoped(self):
        task = self.create()
        old = self.service.add_instruction(self.alice, task["id"], expected_revision=0,
                                           request_id="old", text="Use CSV",
                                           source={"kind": "user", "ref": "message-1"})
        old_id = old["instructions"][0]["id"]
        new = self.service.add_instruction(self.alice, task["id"], expected_revision=1,
                                           request_id="new", text="Use JSON",
                                           source={"kind": "user", "ref": "message-2"},
                                           supersedes=[old_id])
        self.assertEqual([item["status"] for item in new["instructions"]], ["superseded", "active"])
        self.assertEqual(self.service.add_instruction(self.alice, task["id"], expected_revision=1,
                                                      request_id="new", text="Use JSON",
                                                      source={"kind": "user", "ref": "message-2"},
                                                      supersedes=[old_id]), new)
        with self.assertRaises(ValueError):
            self.service.add_instruction(self.alice, task["id"], expected_revision=2,
                                         request_id="bad", text="Use XML",
                                         source={"kind": "user", "ref": "message-3"},
                                         supersedes=[old_id])


if __name__ == "__main__":
    unittest.main()
