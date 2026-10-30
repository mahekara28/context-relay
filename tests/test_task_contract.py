import json
import unittest
from pathlib import Path

from context_relay.task_contract import TaskContext, validate_task_snapshot


EXAMPLE = Path(__file__).resolve().parents[1] / "schemas/examples/task-context-v1alpha1.json"


class TaskContractTests(unittest.TestCase):
    def setUp(self):
        self.example = json.loads(EXAMPLE.read_text())

    def test_public_example_round_trips_without_aliasing(self):
        record = TaskContext.from_dict(self.example)
        self.example["instructions"][0]["text"] = "changed"
        self.assertEqual(record.to_dict()["instructions"][0]["text"], "Use JSON output")

    def test_rejects_unknown_fields_and_unsupported_version(self):
        self.example["surprise"] = True
        with self.assertRaises(ValueError):
            validate_task_snapshot(self.example)
        del self.example["surprise"]
        self.example["schema_version"] = "future"
        with self.assertRaises(ValueError):
            validate_task_snapshot(self.example)

    def test_rejects_duplicate_ids_and_bad_digests(self):
        self.example["instructions"].append(self.example["instructions"][0])
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_task_snapshot(self.example)
        self.example["instructions"].pop()
        self.example["artifacts"].append({"path": "file.py", "digest": "sha256:wrong", "size": 1})
        with self.assertRaisesRegex(ValueError, "digest"):
            validate_task_snapshot(self.example)


if __name__ == "__main__":
    unittest.main()
