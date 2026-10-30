import tempfile
import unittest
from pathlib import Path

from context_relay import ConflictError, RelayStore
from context_relay.artifact_resolution import resolve_artifacts
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

    def test_receiver_verifies_local_artifact_bytes(self):
        path = Path(self.temporary.name) / "result.json"
        path.write_text('{"ok":true}')
        captured = self.tasks.capture_artifact(self.sender, self.task["id"], expected_revision=0,
                                               request_id="capture", path="result.json")
        offer = self.handoffs.offer(self.sender, captured["id"], recipient="recipient")
        self.assertEqual(resolve_artifacts(offer["packet"], root=self.temporary.name),
                         [{"path": "result.json", "status": "ok"}])
        path.write_text('{"ok":false}')
        self.assertEqual(resolve_artifacts(offer["packet"], root=self.temporary.name)[0]["status"], "changed")
        path.unlink()
        self.assertEqual(resolve_artifacts(offer["packet"], root=self.temporary.name)[0]["status"], "missing")

    def test_new_client_receives_current_instruction_with_source(self):
        old = self.tasks.add_instruction(self.sender, self.task["id"], expected_revision=0,
                                         request_id="old", text="Produce CSV",
                                         source={"kind": "user", "ref": "message-1"})
        old_id = old["instructions"][0]["id"]
        current = self.tasks.add_instruction(self.sender, self.task["id"], expected_revision=1,
                                             request_id="new", text="Produce JSON",
                                             source={"kind": "user", "ref": "message-2"},
                                             supersedes=[old_id])
        offered = self.handoffs.offer(self.sender, self.task["id"], recipient="recipient")
        with RelayStore(self.store.path) as receiver_store:
            receiver_tasks = TaskService(receiver_store, authorize=lambda principal, action: True)
            received = HandoffService(receiver_tasks).accept(self.recipient, offered["id"])
        self.assertEqual(received["packet"]["revision"], current["revision"])
        self.assertEqual([item["text"] for item in received["packet"]["instructions"]], ["Produce JSON"])
        self.assertEqual(received["packet"]["instructions"][0]["source"]["ref"], "message-2")

    def test_refresh_creates_new_packet_and_cancel_blocks_old_offer(self):
        first = self.handoffs.offer(self.sender, self.task["id"], recipient="recipient")
        self.tasks.update(self.sender, self.task["id"], expected_revision=0,
                          request_id="changed", changes={"open_work": ["Review"]})
        second = self.handoffs.refresh(self.sender, first["id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["packet"]["revision"], 0)
        self.assertEqual(second["packet"]["revision"], 1)
        self.handoffs.cancel(self.sender, first["id"])
        with self.assertRaises(ValueError):
            self.handoffs.accept(self.recipient, first["id"])
        self.assertEqual(self.handoffs.accept(self.recipient, second["id"])["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
