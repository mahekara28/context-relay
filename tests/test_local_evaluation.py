"""The evaluation harness must report real failures, without a model or network."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES))
import evaluate_local_model as evaluation


class LocalEvaluationScoringTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"answer": "Dock-7", "evidence": ["cobalt-manifest"]}

    def test_completion_requires_both_exact_answer_and_evidence(self):
        correct = evaluation.score_output(json.dumps(self.expected), self.expected)
        self.assertTrue(correct["schema_valid"])
        self.assertTrue(correct["task_complete"])

        wrong_evidence = evaluation.score_output(
            '{"answer":"Dock-7","evidence":["another-manifest"]}', self.expected)
        self.assertTrue(wrong_evidence["schema_valid"])
        self.assertTrue(wrong_evidence["answer_correct"])
        self.assertFalse(wrong_evidence["evidence_correct"])
        self.assertFalse(wrong_evidence["task_complete"])

        wrong_answer = evaluation.score_output(
            '{"answer":"Dock-8","evidence":["cobalt-manifest"]}', self.expected)
        self.assertTrue(wrong_answer["evidence_correct"])
        self.assertFalse(wrong_answer["answer_correct"])
        self.assertFalse(wrong_answer["task_complete"])

    def test_duplicates_and_invalid_shapes_are_not_valid_model_answers(self):
        invalid = [
            '{"answer":"Dock-7","evidence":["cobalt-manifest","cobalt-manifest"]}',
            '{"answer":"Dock-7","evidence":["cobalt-manifest"],"extra":true}',
            '{"answer":"Dock-7"}',
            '{"answer":7,"evidence":["cobalt-manifest"]}',
            '{"answer":"Dock-7","evidence":"cobalt-manifest"}',
            '{"answer":"Dock-7","evidence":[{}]}',
            '[]', 'null', 'not JSON',
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                score = evaluation.score_output(raw, self.expected)
                self.assertFalse(score["schema_valid"])
                self.assertFalse(score["task_complete"])

    def test_unknown_requires_an_empty_evidence_set(self):
        expected = {"answer": "unknown", "evidence": []}
        self.assertTrue(evaluation.score_output(json.dumps(expected), expected)["task_complete"])
        score = evaluation.score_output('{"answer":"unknown","evidence":["estimate"]}', expected)
        self.assertTrue(score["answer_correct"])
        self.assertFalse(score["task_complete"])


class LocalEvaluationInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = evaluation.fixtures()
        cls.prepared = evaluation.build_requests(cls.fixtures)

    def test_modes_share_state_pins_and_original_message_provenance(self):
        for fixture in self.fixtures:
            rows = [row for row in self.prepared if row["fixture"] == fixture["id"]]
            self.assertEqual({row["mode"] for row in rows}, set(evaluation.MODES))
            state = rows[0]["application_supplied_state"]
            for row in rows:
                with self.subTest(fixture=fixture["id"], mode=row["mode"]):
                    self.assertEqual(row["application_supplied_state"], state)
                    self.assertEqual(row["application_supplied_pins"], fixture["pins"])
                    included = row["selection_report"]["included_indices"]
                    originals = row["messages"][:-2] + row["messages"][-1:]
                    self.assertEqual(originals, [fixture["messages"][index] for index in included])
                    self.assertEqual(row["messages"][-1], fixture["messages"][-1])
                    for pinned_index in fixture["pins"]:
                        self.assertIn(pinned_index, included)

    def test_budget_controls_and_full_reference_are_labeled(self):
        for row in self.prepared:
            with self.subTest(fixture=row["fixture"], mode=row["mode"]):
                size = len(evaluation.encode(row["messages"]))
                self.assertEqual(size, row["selection_report"]["size_chars"])
                if row["mode"] == "full_plus_state":
                    self.assertGreater(size, evaluation.BUDGET)
                    self.assertTrue(row["exceeds_comparison_budget"])
                    self.assertEqual(row["selection_report"]["omitted_indices"], [])
                else:
                    self.assertLessEqual(size, evaluation.BUDGET)
                    self.assertFalse(row["exceeds_comparison_budget"])

    def test_fixture_order_rotates_modes_within_repeats(self):
        order = evaluation.execution_order(self.prepared, 3)
        self.assertEqual(len(order), len(self.prepared) * 3)
        for fixture in self.fixtures:
            first_modes = []
            for repeat in range(3):
                modes = [row["mode"] for number, row in order
                         if number == repeat and row["fixture"] == fixture["id"]]
                self.assertEqual(set(modes), set(evaluation.MODES))
                first_modes.append(modes[0])
            self.assertEqual(set(first_modes), set(evaluation.MODES))


class LocalEvaluationFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "report.json"
        self.prepared = evaluation.build_requests([evaluation.fixtures()[0]])
        self.report = {"records": []}

    def test_done_false_cannot_count_as_completed_even_with_correct_json(self):
        expected = self.prepared[0]["expected"]

        class IncompleteClient:
            def _request(self, _path, _payload):
                return {"done": False, "message": {"content": json.dumps(expected)}}

        with contextlib.redirect_stdout(io.StringIO()):
            success = evaluation.run_evaluation(
                IncompleteClient(), "fake-local", self.prepared, self.report, self.output)
        self.assertFalse(success)
        for row in self.report["records"]:
            self.assertEqual(row["status"], "error")
            self.assertEqual(json.loads(row["raw_proposal"]), expected)
            self.assertNotIn("score", row)
        for summary in self.report["summary"].values():
            self.assertEqual(summary["task_complete"], 0)
            self.assertEqual(summary["completed_responses"], 0)

    def test_timeout_preserves_completed_records_and_continues(self):
        expected = self.prepared[0]["expected"]
        output = self.output
        snapshots = []

        class SometimesUnavailableClient:
            count = 0

            def _request(self, path, payload):
                self.count += 1
                snapshots.append(json.loads(output.read_text(encoding="utf-8")))
                if self.count == 2:
                    raise TimeoutError("Synthetic local timeout; no network was used.")
                return {"done": True, "message": {"content": json.dumps(expected)},
                        "prompt_eval_count": 17, "eval_count": 9}

        with contextlib.redirect_stdout(io.StringIO()):
            success = evaluation.run_evaluation(
                SometimesUnavailableClient(), "fake-local", self.prepared, self.report, self.output)
        self.assertFalse(success)
        self.assertEqual([row["status"] for row in self.report["records"]],
                         ["completed", "error", "completed"])
        self.assertEqual(snapshots[0]["records"][0]["status"], "running")
        self.assertEqual(snapshots[1]["records"][0]["status"], "completed")
        self.assertEqual(snapshots[1]["records"][1]["status"], "running")
        self.assertEqual(snapshots[2]["records"][1]["error"]["type"], "TimeoutError")
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), self.report)
        self.assertEqual(self.report["summary"]["recent_plus_state"]["task_complete"], 0)
        self.assertEqual(self.report["summary"]["relay"]["task_complete"], 1)


if __name__ == "__main__":
    unittest.main()
