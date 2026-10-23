"""Real isolated file effects through the public action and evaluation harnesses."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay.harness_eval import HarnessCase, assess_improvement, evaluate_harness, improve_harness


_example = Path(__file__).resolve().parents[1] / "examples" / "harness_improvement.py"
_spec = importlib.util.spec_from_file_location("relay_export_example", _example)
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


class HarnessImprovementIntegrationTests(unittest.TestCase):
    def test_known_repair_completes_actual_exports_without_another_model_call(self):
        observed = []

        def run(input, config, context):
            result = demo.run_export(input, config, context)
            files = list(context.workdir.glob("export.*"))
            observed.append({"config": config, "result": result, "trace": context.trace,
                             "directory": context.workdir, "file_count": len(files)})
            return result

        selected = improve_harness(demo.export_cases(), {"repair": None}, demo.propose_repair,
                                   run, demo.judge_export)
        self.assertEqual(selected["selected"], "candidate")
        self.assertEqual(selected["config"], {"repair": "known"})
        self.assertEqual(selected["decision"]["completion_gain"], 2)
        self.assertEqual(len({row["directory"] for row in observed}), 8)
        self.assertTrue(all(not row["directory"].exists() for row in observed))
        for index in (4, 7):  # Changed format and damaged names/numbers.
            row = observed[index]
            self.assertEqual(row["result"]["artifact"], {
                "format": "JSON", "rows": [
                    {"id": "A-17", "name": "Steel bottle", "units": 3},
                    {"id": "B-29", "name": "Notebook", "units": 12}]})
            self.assertEqual(row["result"]["status"], "repaired")
            self.assertEqual(row["result"]["repair_attempts"], 1)
            self.assertEqual(row["trace"]["model_calls"], 1)
            self.assertEqual(row["trace"]["tool_calls"], 1)
            self.assertEqual(row["file_count"], 1)
        self.assertEqual(observed[0]["file_count"], 0)
        self.assertEqual(observed[3]["file_count"], 0)
        for row in observed:
            self.assertEqual(row["trace"]["model_calls"], 1)

    def test_proposer_receives_only_the_development_failure(self):
        seen = []

        def propose(failures, context):
            seen.extend(failures)
            return demo.propose_repair(failures, context)

        result = improve_harness(demo.export_cases(), {"repair": None}, propose,
                                  demo.run_export, demo.judge_export)
        self.assertTrue(result["decision"]["accepted"])
        self.assertEqual([row["id"] for row in seen], ["changed-format"])
        self.assertFalse(seen[0]["score"]["completed"])
        self.assertEqual(seen[0]["trace"]["tool_calls"], 0)

    def test_unsafe_configuration_is_rejected_before_any_candidate_call(self):
        for config in ({"repair": "known", "disable_checks": True},
                       {"repair": "guess"}, {"repair": False}, {}):
            with self.subTest(config=config):
                result = improve_harness(demo.export_cases(), {"repair": None}, lambda _, context: config,
                                          demo.run_export, demo.judge_export)
                self.assertEqual(result["selected"], "baseline")
                self.assertEqual(result["config"], {"repair": None})
                self.assertFalse(result["decision"]["accepted"])
                for row in result["report"]["results"]:
                    if row["variant"] == "candidate":
                        self.assertEqual(row["status"], "error")
                        self.assertEqual(row["trace"]["model_calls"], 0)
                        self.assertEqual(row["trace"]["tool_calls"], 0)

    def test_known_repair_does_not_invent_a_different_row_identity(self):
        original = demo.export_cases()[2]
        input = original.input
        input["proposal"]["arguments"]["rows"][0]["id"] = "UNRELATED-ACCOUNT"
        cases = [HarnessCase("unknown-identity", input, original.expected), demo.export_cases()[1]]
        results = []

        def run(input, config, context):
            result = demo.run_export(input, config, context)
            results.append(result)
            return result

        selected = improve_harness(cases, {"repair": None}, demo.propose_repair, run, demo.judge_export)
        self.assertEqual(selected["selected"], "baseline")
        self.assertIn("no_completion_gain", selected["decision"]["reasons"])
        self.assertEqual(results[2]["status"], "repair_unavailable")
        self.assertIsNone(results[2]["artifact"])
        self.assertIn("confirm", results[2]["question"])
        for row in selected["report"]["results"]:
            if row["case_id"] == "unknown-identity":
                self.assertEqual(row["trace"]["model_calls"], 1)
                self.assertEqual(row["trace"]["tool_calls"], 0)
                self.assertFalse(row["score"]["completed"])
                self.assertFalse(row["score"]["wrong_action"])

    def test_a_write_followed_by_an_error_is_never_repeated(self):
        original = demo.export_cases()[2]
        cases = [HarnessCase("lost-acknowledgement", {**original.input, "fail_after_write": True},
                             original.expected), demo.export_cases()[1]]
        written_before_error = []

        def run(input, config, context):
            try:
                return demo.run_export(input, config, context)
            finally:
                if input.get("fail_after_write"):
                    written_before_error.append(json.loads((context.workdir / "export.json").read_text()))

        report = evaluate_harness(cases, {"repair": None}, {"repair": "known"}, run, demo.judge_export)
        self.assertEqual(written_before_error, [original.expected["rows"], original.expected["rows"]])
        self.assertFalse(assess_improvement(report)["accepted"])
        for row in report["results"]:
            if row["case_id"] == "lost-acknowledgement":
                self.assertEqual(row["status"], "error")
                self.assertIsNone(row["score"])
                self.assertEqual(row["trace"]["model_calls"], 1)
                self.assertEqual(row["trace"]["tool_calls"], 1)
                self.assertEqual(row["trace"]["events"][-1]["status"], "error")

    def test_unclear_instruction_is_counted_as_clarification_without_calling_model(self):
        original = demo.export_cases()[0]
        input = original.input
        input["messages"][-1]["content"] = "Please make exports easier to read."
        cases = [HarnessCase("unclear-format", input, original.expected), demo.export_cases()[1]]
        report = evaluate_harness(cases, {"repair": None}, {"repair": "known"},
                                  demo.run_export, demo.judge_export)
        for row in report["results"]:
            if row["case_id"] == "unclear-format":
                self.assertEqual(row["status"], "scored")
                self.assertEqual(row["score"], {"completed": False, "wrong_action": False, "clarification": True})
                self.assertEqual(row["trace"]["model_calls"], 0)
                self.assertEqual(row["trace"]["tool_calls"], 0)

    def test_success_receipt_does_not_hide_corrupted_file_contents(self):
        write_text = Path.write_text

        def corrupt(path, data, *args, **kwargs):
            if path.name == "export.json":
                rows = json.loads(data)
                rows[0]["units"] = 999
                data = json.dumps(rows)
            return write_text(path, data, *args, **kwargs)

        with patch.object(Path, "write_text", corrupt):
            result = demo.run_demo()
        self.assertEqual(result["selected"], "baseline")
        self.assertIn("candidate_wrong_action", result["decision"]["reasons"])
        for row in result["report"]["results"]:
            if row["variant"] == "candidate" and row["case_id"] != "ordinary-csv":
                self.assertTrue(row["score"]["wrong_action"])
                self.assertFalse(row["score"]["completed"])

    def test_optional_journal_contains_results_without_raw_case_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trial.jsonl"
            result = demo.run_demo(report_path=path)
            text = path.read_text(encoding="utf-8")
            events = [json.loads(line) for line in text.splitlines()]
            self.assertEqual(events[-1]["event"], "report")
            self.assertEqual(events[-1]["data"], result["report"])
            self.assertNotIn("Steel bottle", text)
            self.assertNotIn("Actually, export", text)
            with self.assertRaises(FileExistsError):
                demo.run_demo(report_path=path)

    def test_boolean_in_written_json_is_not_accepted_as_numeric_one(self):
        original = demo.export_cases()[2]
        input, expected = original.input, original.expected
        input["source_rows"][0]["units"] = 1
        input["proposal"]["arguments"]["rows"][0]["units"] = 1
        expected["rows"][0]["units"] = 1
        cases = [HarnessCase("one-unit", input, expected), demo.export_cases()[1]]
        write_text = Path.write_text

        def corrupt(path, data, *args, **kwargs):
            if path.name == "export.json":
                rows = json.loads(data)
                rows[0]["units"] = True
                data = json.dumps(rows)
            return write_text(path, data, *args, **kwargs)

        with patch.object(Path, "write_text", corrupt):
            report = evaluate_harness(cases, {"repair": None}, {"repair": "known"},
                                      demo.run_export, demo.judge_export)
        for row in report["results"]:
            if row["case_id"] == "one-unit":
                self.assertEqual(row["status"], "scored")
                self.assertTrue(row["score"]["wrong_action"])
                self.assertFalse(row["score"]["completed"])


if __name__ == "__main__":
    unittest.main()
