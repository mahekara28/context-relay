"""Check completed-task grading against wrong writes, refusals and real repairs."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The evaluator's local endpoint implementation is shared with the existing example.
import sys
sys.path.insert(0, str(ROOT / "examples"))
import evaluate_repair_workflow as workflow


class WorkflowEvaluationTests(unittest.TestCase):
    def test_grader_does_not_count_right_settings_with_changed_source_as_complete(self):
        case = workflow.cases()[0]
        candidate = {"format": case["format"], "language": case["language"], "rows": case["rows"]}
        self.assertTrue(workflow.grade(candidate, case))
        altered = json.loads(json.dumps(candidate))
        altered["rows"][0]["item"] = "Translated"
        self.assertFalse(workflow.grade(altered, case))
        altered["rows"] = candidate["rows"][:-1]
        self.assertFalse(workflow.grade(altered, case))

    def test_four_arms_share_one_proposal_and_do_not_hide_bad_repair(self):
        case = workflow.cases()[0]
        original = {"format": case["format"], "language": case["language"],
                    "rows": [{"id": "wrong", "item": "Changed", "units": 900}], "note": "Keep me"}
        fixed = {**original, "rows": case["rows"], "note": "Unrelated rewrite"}
        calls, events = [], []
        def model(messages, case_id, stage):
            calls.append(stage)
            return json.dumps(original if stage == "initial" else fixed)
        with tempfile.TemporaryDirectory() as directory:
            totals = workflow.run(model, [case], Path(directory), events.append)
            self.assertEqual(totals["unchecked"]["wrong_writes"], 1)
            self.assertEqual(totals["checked"]["writes"], 0)
            self.assertEqual(totals["known_repair"]["completed_declared_task"], 1)
            self.assertEqual(totals["known_repair"]["repair_calls"], 0)
            self.assertEqual(totals["model_repair"]["writes"], 0)
            self.assertEqual(totals["model_repair"]["repair_calls"], 1)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 2)
        self.assertEqual(calls, ["initial", "repair"])
        self.assertTrue(all(e["summary_meaning_checked"] is False for e in events))

    def test_invalid_generation_remains_failed_in_every_arm_without_repair(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            totals = workflow.run(lambda *a: "not JSON", workflow.cases()[:1], Path(directory), events.append)
            self.assertFalse(list(Path(directory).iterdir()))
        self.assertTrue(all(total["tasks"] == 1 and total["blocked"] == 1 and not total["writes"] for total in totals.values()))
        self.assertTrue(all(e["status"] == "invalid_initial_response" for e in events))

    def test_duplicate_keys_and_nonfinite_values_are_not_gradeable_proposals(self):
        for raw in ('{"rows":[],"rows":[]}', '{"rows":NaN}', '{"rows":1e999}', '[]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                workflow.parse(raw)


if __name__ == "__main__":
    unittest.main()
