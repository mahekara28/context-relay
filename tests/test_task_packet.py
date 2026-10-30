import json
import unittest
from pathlib import Path

from context_relay import BudgetExceeded
from context_relay.models import encode
from context_relay.task_packet import compile_task_packet


EXAMPLE = Path(__file__).resolve().parents[1] / "schemas/examples/task-context-v1alpha1.json"


class TaskPacketTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads(EXAMPLE.read_text())

    def test_packet_is_deterministic_and_preserves_source(self):
        first = compile_task_packet(self.task)
        self.assertEqual(first, compile_task_packet(self.task))
        self.assertEqual(first["instructions"][0]["source"], {"kind": "user", "ref": "request-2"})
        self.assertEqual(first["revision"], self.task["revision"])

    def test_optional_evidence_is_counted_when_budget_excludes_it(self):
        self.task["evidence"] = [{"id": "e1", "claim": "x" * 2000,
                                  "source": {"kind": "file", "ref": "README.md"},
                                  "captured_at": "2026-10-30T00:00:00Z"}]
        packet = compile_task_packet(self.task, max_chars=1024)
        self.assertEqual(packet["evidence"], [])
        self.assertEqual(packet["omitted"]["evidence"], 1)
        self.assertLessEqual(len(encode(packet)), 1024)

    def test_required_content_cannot_be_silently_dropped(self):
        self.task["goal"] = "x" * 2000
        with self.assertRaises(BudgetExceeded):
            compile_task_packet(self.task, max_chars=512)


if __name__ == "__main__":
    unittest.main()
