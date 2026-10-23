"""Offline checks for the local workflow evaluator's selector and file grader."""

import json
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
import evaluate_agent_workflow as workflow


class WorkflowEvaluationTests(unittest.TestCase):
    def test_selector_preserves_whole_contiguous_turns_and_latest_request(self):
        history = [workflow.message("system", "S"), workflow.message("user", "old"),
                   workflow.message("assistant", "old reply"),
                   workflow.message("user", "x" * 1000),
                   workflow.message("assistant", "large turn"),
                   workflow.message("user", "latest")]
        self.assertEqual(workflow.recent_messages(history, 200), [history[0], history[-1]])

    def test_selector_fails_if_mandatory_input_cannot_fit(self):
        with self.assertRaises(ValueError):
            workflow.recent_messages([workflow.message("user", "large")], 1)

    def test_six_fixed_cases_keep_grader_labels_outside_conversations(self):
        cases = workflow.cases()
        self.assertEqual(len(cases), 6)
        self.assertEqual(len({case["id"] for case in cases}), 6)
        for case in cases:
            self.assertNotIn('"expected"', json.dumps(case["history"]))
            self.assertNotIn(case["description"], json.dumps(case["history"]))
            selected = workflow.recent_messages(case["history"] + [workflow.task_message(workflow.CSV_TEXT)])
            self.assertLessEqual(len(workflow.encode(selected)), workflow.BUDGET)

    def test_grader_reads_actual_file_and_detects_bad_row_format_and_language(self):
        proposal = {"format": "JSON", "language": "English", "summary": "Three items are included.",
                    "rows": [{"item": "Pencils", "units": 4, "unit_price": 2.5, "total": 10},
                             {"item": "Notebooks", "units": 3, "unit_price": 6, "total": 18},
                             {"item": "Markers", "units": 2, "unit_price": 4, "total": 8}]}
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source = directory / "sales.csv"
            source.write_text(workflow.CSV_TEXT, encoding="utf-8")
            result = workflow.write_export(json.dumps(proposal), directory)
            score, artifact = workflow.grade_files(result, source, {"format": "JSON", "language": "English"})
            self.assertTrue(score["task_complete"])
            self.assertEqual(json.loads(artifact["report_text"]), proposal["rows"])
            # The grader rereads the disk, rather than trusting the proposal.
            Path(result["export_path"]).write_text("[]", encoding="utf-8")
            score, _ = workflow.grade_files(result, source, {"format": "CSV", "language": "Hindi"})
            self.assertFalse(score["rows_correct"])
            self.assertFalse(score["format_correct"])
            self.assertFalse(score["language_label_and_script_correct"])

    def test_model_cannot_choose_arbitrary_file_paths_or_execute_code(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                workflow.write_export(json.dumps({"format": "../outside", "language": "English",
                                                 "summary": "x", "rows": []}), Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_nonfinite_row_values_are_rejected_before_any_file_write(self):
        with tempfile.TemporaryDirectory() as folder:
            proposal = {"format": "CSV", "language": "English", "summary": "Summary.",
                        "rows": [{"item": "Item", "units": 1, "unit_price": 1, "total": float("nan")}]}
            with self.assertRaises(ValueError):
                workflow.write_export(json.dumps(proposal), Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    @unittest.skipUnless(importlib.util.find_spec("langgraph") and importlib.util.find_spec("langchain_core"),
                         "Optional real frameworks are not installed")
    def test_local_example_disables_host_tracing_during_real_framework_execution(self):
        from langsmith import utils

        def chat(**request):
            self.assertFalse(utils.tracing_is_enabled())
            return SimpleNamespace(model_dump=lambda **kwargs: {
                "done": True, "done_reason": "stop", "message": {"content": json.dumps({
                    "format": "JSON", "language": "English", "summary": "Offline test response.",
                    "rows": []})}})

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                "LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true"}):
            directory = Path(folder)
            with (directory / "report.jsonl").open("w", encoding="utf-8") as stream:
                result = workflow.run_case(workflow.cases()[0], "recent_whole_turns",
                                           SimpleNamespace(chat=chat), "offline-test",
                                           directory / "case", stream, 1, timeout=1)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["inference_attempts"], 1)


@unittest.skipUnless(importlib.util.find_spec("langgraph") and importlib.util.find_spec("langchain_core"),
                     "Optional real frameworks are not installed")
class ThreeArmEvaluationTests(unittest.TestCase):
    def proposal(self, format="JSON", language="English", *, bad_rows=False):
        return {"format": format, "language": language,
                "summary": "Three items were exported." if language == "English" else "तीन वस्तुएँ शामिल हैं।",
                "rows": [] if bad_rows else [
                    {"item": "Pencils", "units": 4, "unit_price": 2.5, "total": 10},
                    {"item": "Notebooks", "units": 3, "unit_price": 6, "total": 18},
                    {"item": "Markers", "units": 2, "unit_price": 4, "total": 8}]}

    def client(self, proposal, captured):
        def chat(**request):
            captured.append(request)
            return SimpleNamespace(model_dump=lambda **kwargs: {
                "done": True, "done_reason": "stop", "message": {"content": json.dumps(proposal)}})
        return SimpleNamespace(chat=chat)

    def attempt(self, directory, case, arm, proposal, relay_case, captured):
        with (directory / f"{arm}.jsonl").open("x", encoding="utf-8") as log:
            runtime = {"id": case["id"], "history": case["history"]}
            return workflow.run_case(runtime, arm, self.client(proposal, captured), "offline-fixture",
                                     directory / arm, log, 1, timeout=1, relay_case=relay_case)

    def test_gate_uses_observed_memory_and_writes_no_files_before_rejection(self):
        case = workflow.cases()[0]
        relay_case = workflow.RelayCase(case["history"])
        try:
            with tempfile.TemporaryDirectory() as folder:
                record = self.attempt(Path(folder), case, "relay_action_gate", self.proposal("CSV"), relay_case, [])
                self.assertEqual(record["status"], "blocked")
                self.assertEqual(record["action_executions"], 0)
                self.assertEqual(record["written_output_files"], [])
                self.assertNotIn("expected", record)
                self.assertIn("memory_binding_mismatch", [issue["code"] for issue in record["gate_verdict"]["issues"]])
                actual_grade = workflow.assess_record(record, {"format": "JSON", "language": "English"})
                changed_grade = workflow.assess_record(record, {"format": "CSV", "language": "English"})
                self.assertTrue(actual_grade["prevention"]["prevented_invalid_action"])
                self.assertTrue(changed_grade["prevention"]["withheld_correct_output"])
                self.assertEqual(actual_grade["gate_verdict"], changed_grade["gate_verdict"])
                self.assertFalse(actual_grade["score"]["task_complete"])
                self.assertFalse(changed_grade["score"]["task_complete"])
        finally:
            relay_case.close()

    def test_memory_and_gate_prompts_match_even_when_successful_gate_runs_first(self):
        case, captured = workflow.cases()[0], []
        relay_case = workflow.RelayCase(case["history"])
        try:
            with tempfile.TemporaryDirectory() as folder:
                first = self.attempt(Path(folder), case, "relay_action_gate", self.proposal(), relay_case, captured)
                second = self.attempt(Path(folder), case, "relay_memory", self.proposal(), relay_case, captured)
                self.assertEqual(first["status"], "completed")
                self.assertEqual(second["status"], "completed")
                self.assertEqual(captured[0], captured[1])
                self.assertEqual(first["selection_report"]["state_revision"], second["selection_report"]["state_revision"])
                self.assertEqual(first["extra_model_calls"], 0)
                self.assertEqual(second["extra_model_calls"], 0)
        finally:
            relay_case.close()

    def test_gate_does_not_claim_to_check_row_math_or_complete_a_blocked_task(self):
        case = workflow.cases()[0]
        relay_case = workflow.RelayCase(case["history"])
        try:
            with tempfile.TemporaryDirectory() as folder:
                record = self.attempt(Path(folder), case, "relay_action_gate", self.proposal(bad_rows=True), relay_case, [])
                record = workflow.assess_record(record, case["expected"])
                self.assertEqual(record["status"], "completed")
                self.assertFalse(record["score"]["task_complete"])
                self.assertTrue(record["prevention"]["executed_invalid_action"])
                summary = workflow.summarize([record])["relay_action_gate"]
                self.assertEqual(summary["task_complete"], 0)
                self.assertEqual(summary["completed_workflows"], 1)
                self.assertEqual(summary["incomplete_tasks"], 1)
        finally:
            relay_case.close()

    def test_missing_memory_can_withhold_a_correct_default_proposal(self):
        case = {"id": "missing", "history": [workflow.message("system", workflow.SYSTEM)]}
        relay_case = workflow.RelayCase(case["history"])
        try:
            with tempfile.TemporaryDirectory() as folder:
                record = self.attempt(Path(folder), case, "relay_action_gate", self.proposal("CSV"), relay_case, [])
                record = workflow.assess_record(record, {"format": "CSV", "language": "English"})
                self.assertEqual(record["status"], "blocked")
                self.assertTrue(record["prevention"]["withheld_correct_output"])
                self.assertFalse(record["score"]["task_complete"])
        finally:
            relay_case.close()

    @unittest.skipUnless(importlib.util.find_spec("ollama"), "Optional Ollama SDK is not installed")
    def test_dry_run_freezes_source_and_eighteen_inputs_without_model_or_metadata_calls(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(workflow, "LocalOllama") as local, \
                patch("ollama.Client") as client, patch("builtins.print"):
            path = Path(folder) / "dry.jsonl"
            self.assertEqual(workflow.main(["--dry-run", "--model", "already-installed", "--output", str(path)]), 0)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len([row for row in rows if row["event"] == "prepared"]), 18)
            self.assertEqual(path.with_suffix(".source.py.txt").read_bytes(), Path(workflow.__file__).read_bytes())
            for case in workflow.cases():
                pair = [row for row in rows if row.get("case") == case["id"] and row.get("arm") in workflow.ARMS[1:]]
                self.assertEqual(pair[0]["messages"], pair[1]["messages"])
                self.assertEqual(pair[0]["prompt_sha256"], pair[1]["prompt_sha256"])
            local.assert_not_called()
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
