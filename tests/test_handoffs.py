import tempfile
import unittest
from pathlib import Path

from context_relay import ConflictError, RelayStore
from context_relay.handoffs import HandoffService
from context_relay.organization import AccessDenied, Principal
from context_relay.task_service import TaskService


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = RelayStore(Path(self.temporary.name) / "relay.db")
        self.tasks = TaskService(self.store, authorize=lambda principal, action: True)
        self.handoffs = HandoffService(self.tasks)
        self.sender = Principal("team", "sender")
        self.recipient = Principal("team", "recipient")
        self.task = self.tasks.create(self.sender, goal="Build JSON report",
                                      completion_criteria=["Oracle passes"],
                                      workspace={"repository": "example", "root": self.temporary.name})

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_offer_freezes_revision_and_packet(self):
        offer = self.handoffs.offer(self.sender, self.task["id"], recipient="recipient")
        self.assertEqual(offer["revision"], 0)
        self.tasks.update(self.sender, self.task["id"], expected_revision=0,
                          request_id="change", changes={"open_work": ["New item"]})
        self.assertEqual(self.handoffs.inspect(self.recipient, offer["id"])["packet"], offer["packet"])

    def test_accept_requires_named_recipient_and_fresh_revision(self):
        offer = self.handoffs.offer(self.sender, self.task["id"], recipient="recipient")
        with self.assertRaises(AccessDenied):
            self.handoffs.accept(self.sender, offer["id"])
        accepted = self.handoffs.accept(self.recipient, offer["id"])
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(self.handoffs.accept(self.recipient, offer["id"]), accepted)
        stale = self.handoffs.offer(self.sender, self.task["id"], recipient="recipient")
        self.tasks.update(self.sender, self.task["id"], expected_revision=0,
                          request_id="change", changes={"open_work": ["New item"]})
        with self.assertRaises(ConflictError):
            self.handoffs.accept(self.recipient, stale["id"])


if __name__ == "__main__":
    unittest.main()
