import itertools
import unittest
from pathlib import Path

from context_relay import RelayStore
from context_relay.handoffs import HandoffService
from context_relay.organization import Principal
from context_relay.task_service import TaskService


class TaskLifecycleStateMachineTests(unittest.TestCase):
    def test_short_operation_sequences_preserve_revisions_and_offer_bytes(self):
        operations = ("update", "offer", "accept", "cancel")
        for sequence in itertools.product(operations, repeat=3):
            with self.subTest(sequence=sequence):
                store = RelayStore()
                try:
                    tasks = TaskService(store, authorize=lambda principal, action: True)
                    handoffs = HandoffService(tasks)
                    sender = Principal("team", "sender")
                    recipient = Principal("team", "recipient")
                    task = tasks.create(sender, goal="Finish", completion_criteria=["Done"],
                                        workspace={"repository": "example", "root": str(Path.cwd().resolve())})
                    offers = []
                    for index, operation in enumerate(sequence):
                        current = tasks.show(sender, task["id"])
                        try:
                            if operation == "update":
                                tasks.update(sender, task["id"], expected_revision=current["revision"],
                                             request_id=f"u-{index}", changes={"open_work": [str(index)]})
                            elif operation == "offer":
                                offers.append(handoffs.offer(sender, task["id"], recipient="recipient"))
                            elif operation == "accept" and offers:
                                handoffs.accept(recipient, offers[-1]["id"])
                            elif operation == "cancel" and offers:
                                handoffs.cancel(sender, offers[-1]["id"])
                        except ValueError:
                            pass  # A stale or already finalized transition is a valid rejection.
                        after = tasks.show(sender, task["id"])
                        self.assertGreaterEqual(after["revision"], current["revision"])
                        events = tasks.list_events(sender, task["id"])
                        self.assertEqual([event["revision"] for event in events],
                                         list(range(after["revision"] + 1)))
                        for offered in offers:
                            stored = handoffs.inspect(sender, offered["id"])
                            self.assertEqual(stored["packet"], offered["packet"])
                            self.assertEqual(stored["packet_digest"], offered["packet_digest"])
                finally:
                    store.close()


if __name__ == "__main__":
    unittest.main()
