import tempfile
import unittest
from pathlib import Path

from context_relay import RelayStore
from context_relay.organization import AccessDenied, Principal
from context_relay.task_service import TaskNotFound, TaskService


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


if __name__ == "__main__":
    unittest.main()
