import tempfile
import unittest
from pathlib import Path

from context_relay import RelayStore
from context_relay.handoffs import HandoffService
from context_relay.organization import Principal
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


if __name__ == "__main__":
    unittest.main()
