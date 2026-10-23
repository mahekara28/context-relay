"""Exercise completed artifacts and fair comparison in the free pilot starter."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load_example(name):
    path = Path(__file__).resolve().parents[1] / "examples" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot = load_example("instruction_pilot")


class InstructionPilotTests(unittest.TestCase):
    def test_complete_export_workflows_and_rejected_unknown_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.jsonl"
            result = pilot.run_pilot_kit(report_path=path)
            report = result["report"]
            self.assertEqual(len(report["results"]), 18)
            for variant in ("baseline", "candidate"):
                rows = [row for row in report["results"] if row["variant"] == variant]
                self.assertTrue(all(row["status"] == "scored" for row in rows), rows)
                self.assertEqual(sum(row["score"]["completed"] for row in rows), 8)
                self.assertFalse(any(row["score"]["wrong_action"] for row in rows))
                unknown = next(row for row in rows if row["case_id"] == "unknown-identity")
                self.assertFalse(unknown["score"]["completed"])
                self.assertTrue(unknown["score"]["clarification"])
                self.assertEqual(unknown["trace"]["tool_calls"], 0)
            observations = result["observations"]
            resolved = next(row for row in observations
                            if row["mode"] == "relay" and row["case_id"] == "conflict-then-finish")
            self.assertEqual(resolved["clarification_requests"], 1)
            self.assertEqual(resolved["scripted_review_decisions"], 1)
            saved = [json.loads(line) for line in path.with_suffix(".observations.jsonl").read_text().splitlines()]
            self.assertEqual([row for row in saved if row["event"] == "observation"],
                             [{"event": "observation", **row} for row in observations])
            self.assertEqual(saved[-1]["event"], "pilot_finished")
            with self.assertRaises(FileExistsError):
                pilot.run_pilot_kit(report_path=path)

    def test_baseline_does_not_construct_a_relay_connection(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                pilot, "auto_connect", side_effect=AssertionError("Baseline used Relay")):
            context = SimpleNamespace(workdir=Path(directory),
                model_call=lambda fn: fn(), tool_call=lambda fn, *args: fn(*args))
            result = pilot.run_workflow(pilot.pilot_cases()[0].input, {"mode": "reviewed_baseline"}, context)
            self.assertFalse(result["unfinished"])
            self.assertEqual(result["artifacts"][0]["format"], "JSON")

    def test_error_counters_are_retained_as_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                pilot, "run_workflow", side_effect=RuntimeError("Unavailable destination")):
            path = Path(directory) / "pilot.jsonl"
            result = pilot.run_pilot_kit(report_path=path)
            self.assertEqual(len(result["observations"]), 18)
            self.assertTrue(all(row["clarification_requests"] is None for row in result["observations"]))
            saved = [json.loads(line) for line in path.with_suffix(".observations.jsonl").read_text().splitlines()]
            failed = [row for row in saved if row["event"] == "observation"]
            self.assertEqual(len(failed), 18)
            self.assertTrue(all(row["status"] == "error" and row["repair_attempts"] is None for row in failed))

    def test_validator_preserves_values_but_does_not_guess_row_identity(self):
        source = [{"id": "A", "name": "Original name", "units": 2}]
        proposal = {"tool": "save_export", "arguments": {
            "format": "csv", "rows": [{"id": "A", "name": "Wrong name", "units": 999}]}}
        checked = pilot._ordinary_validator(proposal, "JSON", source)
        self.assertEqual(checked, {"format": "JSON", "rows": source})
        self.assertEqual(proposal["arguments"]["rows"][0]["units"], 999)
        proposal["arguments"]["rows"][0]["id"] = "unknown"
        self.assertIsNone(pilot._ordinary_validator(proposal, "JSON", source))

    def test_saved_wrong_content_is_not_hidden_by_success_receipt_or_later_success(self):
        expected = {"artifacts": [{"format": "JSON", "rows": [{"id": "A", "units": 2}]}],
                    "should_complete": True}
        result = {"artifacts": [{"format": "JSON", "rows": [{"id": "A", "units": 3}]}],
                  "unfinished": False, "clarification_requests": 0}
        score = pilot.judge_workflow(expected, result, {"tool_calls": 1})
        self.assertTrue(score["wrong_action"])
        self.assertFalse(score["completed"])
        result["artifacts"].append(expected["artifacts"][0])
        self.assertTrue(pilot.judge_workflow(expected, result, {"tool_calls": 2})["wrong_action"])

    def test_blocking_an_expected_task_is_not_counted_as_completion(self):
        score = pilot.judge_workflow({"artifacts": [], "should_complete": False},
            {"artifacts": [], "unfinished": True, "clarification_requests": 1}, {"tool_calls": 0})
        self.assertEqual(score, {"completed": False, "wrong_action": False, "clarification": True})


if __name__ == "__main__":
    unittest.main()
