"""Offline checks of evaluation mechanics; fake model responses prove no model quality."""

import importlib.util
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
import evaluate_completed_work as workflow


def proposal(format="JSON", language="English", *, bad_rows=False):
    return {"format": format, "language": language,
            "summary": "Three items were exported." if language.strip().lower() == "english" else "तीन वस्तुएँ शामिल हैं।",
            "rows": [] if bad_rows else [
                {"item": "Pencils", "units": 4, "unit_price": 2.5, "total": 10},
                {"item": "Notebooks", "units": 3, "unit_price": 6, "total": 18},
                {"item": "Markers", "units": 2, "unit_price": 4, "total": 8}]}


def initial(arguments):
    return {"call_id": "case:relay_initial", "inference_attempts": 1, "status": "proposed", "proposal": arguments}


class FakeClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def chat(self, **request):
        self.requests.append(request)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        content = json.dumps(value) if isinstance(value, dict) else value
        return SimpleNamespace(model_dump=lambda **kwargs: {
            "done": True, "done_reason": "stop", "message": {"content": content}})


class CompletedWorkDataTests(unittest.TestCase):
    def test_development_cases_retained_and_runtime_excludes_all_labels(self):
        data = workflow.load_dataset()
        self.assertEqual(data["dataset"], "development")
        self.assertEqual(len(data["cases"]), 6)
        self.assertEqual({case["id"] for case in data["cases"]},
                         {case["id"] for case in workflow.previous.cases()})
        for case in data["cases"]:
            runtime = workflow.runtime_case(case)
            self.assertEqual(set(runtime), {"id", "history", "csv"})
            self.assertNotIn(case["description"], json.dumps(runtime))

    def test_parser_keeps_known_case_equivalence_raw_and_rejects_unknown_or_duplicate(self):
        value = proposal(" json ", " english ")
        self.assertEqual(workflow.parse_proposal(json.dumps(value)), value)
        self.assertEqual(workflow.canonical_arguments(value)["format"], "JSON")
        self.assertEqual(value["format"], " json ")
        for raw in [json.dumps(proposal("JSON-ish")), '{"format":"JSON","format":"CSV"}',
                    json.dumps({**value, "rows": [{"item": "x", "units": 1, "unit_price": 1, "total": float("nan")} ]})]:
            with self.assertRaises(ValueError):
                workflow.parse_proposal(raw)

    def test_external_fixture_validated_without_changing_expected_or_input(self):
        data = {"dataset": "fresh_holdout", "cases": [{"id": "safe-01", "history": [],
            "csv": "item,units,unit_price\nNewItem,2,3\n", "expected": {"format": "CSV", "language": "English"}}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cases.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(workflow.load_dataset(path), data)
            data["cases"][0]["expected"] = {"clarification_required": True}
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(workflow.load_dataset(path), data)
            data["cases"][0]["id"] = "../escape"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                workflow.load_dataset(path)

    def test_budget_and_existing_output_fail_before_any_local_client(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(workflow, "LocalOllama") as client:
            path = Path(folder) / "report.jsonl"
            with self.assertRaises(SystemExit):
                workflow.main(["--model", "unused", "--output", str(path), "--run-local", "--max-calls", "17"])
            path.write_text("existing", encoding="utf-8")
            with self.assertRaises(SystemExit):
                workflow.main(["--model", "unused", "--output", str(path), "--run-local"])
            client.assert_not_called()
            self.assertEqual(path.read_text(), "existing")


@unittest.skipUnless(importlib.util.find_spec("langchain_core"), "Optional LangChain SDK not installed")
class CompletedWorkExecutionTests(unittest.TestCase):
    def setUp(self):
        self.case = workflow.load_dataset()["cases"][0]
        self.runtime = workflow.runtime_case(self.case)
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.stream = (self.directory / "journal.jsonl").open("x", encoding="utf-8")
        self.branches = workflow.RelayBranches(self.runtime)

    def tearDown(self):
        self.branches.close()
        self.stream.close()
        self.folder.cleanup()

    def execute(self, arm, value, *, client=None):
        calls = workflow.ModelCalls(client or FakeClient(), "offline-test", self.stream, timeout=1)
        record = workflow.execute_arm(self.runtime, arm, initial(value), self.directory / arm,
                                      self.stream, branches=self.branches, calls=calls)
        return workflow.assess_record(record, self.case["expected"]), calls

    def test_branches_share_exact_prompt_but_success_does_not_advance_other_store(self):
        first, second = workflow.ARMS[1:]
        self.assertEqual(workflow.previous.canonical(self.branches.prepared[first].messages),
                         workflow.previous.canonical(self.branches.prepared[second].messages))
        start = self.branches.revision
        record, _ = self.execute(first, proposal())
        self.assertEqual(record["status"], "executed")
        other = self.branches.bridges[second]
        self.assertEqual(other.guard.store.get_session(other.session_id)["revision"], start)
        record, _ = self.execute(second, proposal())
        self.assertEqual(record["status"], "executed")
        self.assertEqual(record["extra_model_calls"], 0)

    def test_one_repair_completes_shared_wrong_initial_proposal_without_label_leak(self):
        wrong = proposal("CSV")
        checked, _ = self.execute(workflow.ARMS[1], wrong)
        client = FakeClient(proposal())
        repaired, calls = self.execute(workflow.ARMS[2], wrong, client=client)
        self.assertEqual(checked["status"], "blocked")
        self.assertEqual(checked["written_output_files"], [])
        self.assertFalse(checked["score"]["task_complete"])
        self.assertEqual(repaired["status"], "repaired")
        self.assertTrue(repaired["score"]["task_complete"])
        self.assertEqual(repaired["extra_model_calls"], 1)
        self.assertEqual(repaired["action_executions"], 1)
        self.assertEqual(repaired["proposal"], checked["proposal"])
        payload = json.loads(client.requests[0]["messages"][1]["content"])
        self.assertEqual(set(payload), {"tool", "previous_arguments", "rejection_issues", "user_memory"})
        self.assertNotIn("expected", payload)
        self.assertEqual(calls.records[0]["character_cap"], workflow.REPAIR_BUDGET)
        self.assertEqual(repaired["outcomes"]["blocked_wrong_proposals"], 1)
        changed = workflow.assess_record(checked, {"format": "CSV", "language": "English"})
        self.assertEqual(changed["outcomes"]["false_blocks"], 1)
        self.assertEqual(changed["recovery"], checked["recovery"])

    def test_wrong_repair_stays_incomplete_without_second_call_or_write(self):
        client = FakeClient(proposal("CSV"), proposal())
        record, _ = self.execute(workflow.ARMS[2], proposal("CSV"), client=client)
        self.assertEqual(record["status"], "blocked")
        self.assertEqual(record["extra_model_calls"], 1)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(record["written_output_files"], [])
        self.assertEqual(record["outcomes"]["blocked_wrong_proposals"], 2)

    def test_known_case_equivalence_reaches_executor_canonical_without_repair(self):
        record, _ = self.execute(workflow.ARMS[2], proposal(" json ", " english "))
        self.assertEqual(record["status"], "executed")
        self.assertEqual(record["proposal"]["format"], " json ")
        self.assertEqual(record["executed_arguments"]["format"], "JSON")
        self.assertEqual(record["executed_arguments"]["language"], "English")
        self.assertTrue(record["score"]["task_complete"])
        self.assertEqual(record["extra_model_calls"], 0)

    def test_row_math_is_graded_after_execution_not_repaired_by_preference_gate(self):
        record, _ = self.execute(workflow.ARMS[2], proposal(bad_rows=True))
        self.assertEqual(record["status"], "executed")
        self.assertEqual(record["extra_model_calls"], 0)
        self.assertFalse(record["score"]["task_complete"])
        self.assertEqual(record["outcomes"]["wrong_writes"], 1)

    def test_missing_memory_requires_clarification_and_never_calls_repair(self):
        runtime = {"id": "missing", "history": [workflow.previous.message("system", workflow.previous.SYSTEM)],
                   "csv": workflow.previous.CSV_TEXT}
        branches = workflow.RelayBranches(runtime)
        try:
            calls = workflow.ModelCalls(FakeClient(), "offline-test", self.stream, timeout=1)
            record = workflow.execute_arm(runtime, workflow.ARMS[2], initial(proposal("CSV")),
                self.directory / "missing", self.stream, branches=branches, calls=calls)
            graded = workflow.assess_record(record, {"format": "CSV", "language": "English"})
            self.assertEqual(record["status"], "clarification_required")
            self.assertEqual(record["clarification_requests"], 1)
            self.assertEqual(record["extra_model_calls"], 0)
            self.assertEqual(calls.records, [])
            self.assertEqual(record["written_output_files"], [])
            self.assertEqual(graded["outcomes"]["false_blocks"], 1)
            self.assertFalse(graded["score"]["task_complete"])
            clarification_grade = workflow.assess_record(record, {"clarification_required": True})
            self.assertEqual(clarification_grade["outcomes"]["correct_clarification"], 1)
            self.assertEqual(clarification_grade["outcomes"]["false_blocks"], 0)
            self.assertFalse(clarification_grade["score"]["task_complete"])
            self.assertEqual(clarification_grade["recovery"], graded["recovery"])
        finally:
            branches.close()

    def test_repair_error_is_preserved_no_retry_no_write(self):
        record, calls = self.execute(workflow.ARMS[2], proposal("CSV"), client=FakeClient(TimeoutError("offline")))
        self.assertEqual(record["status"], "repair_failed")
        self.assertEqual(record["extra_model_calls"], 1)
        self.assertEqual(len(calls.records), 1)
        self.assertEqual(calls.records[0]["error"]["type"], "TimeoutError")
        self.assertEqual(record["written_output_files"], [])

    def test_unclear_current_preference_requests_clarification_not_model_repair(self):
        runtime = {"id": "uncertain-development", "csv": workflow.previous.CSV_TEXT,
            "history": [workflow.previous.message("system", workflow.previous.SYSTEM)]
                + workflow.previous.exchange("Export as CSV.")
                + workflow.previous.exchange("Reply in English.")
                + workflow.previous.exchange("Please make future exports much easier to read.")}
        branches = workflow.RelayBranches(runtime)
        try:
            disputed = [row for row in branches.memory["entries"] if row["key"] == "export_format"][0]
            self.assertTrue(disputed["disputed"])
            calls = workflow.ModelCalls(FakeClient(), "offline-test", self.stream, timeout=1)
            record = workflow.execute_arm(runtime, workflow.ARMS[2], initial(proposal("CSV")),
                self.directory / "uncertain", self.stream, branches=branches, calls=calls)
            self.assertEqual(record["status"], "clarification_required")
            self.assertEqual(record["repair_attempts"], 0)
            self.assertEqual(record["extra_model_calls"], 0)
            self.assertEqual(calls.records, [])
            self.assertEqual(record["written_output_files"], [])
            codes = {item["code"] for item in record["recovery"]["initial_check"]["issues"]}
            self.assertIn("memory_disputed", codes)
        finally:
            branches.close()

    def test_repair_preflight_budget_failure_counts_callback_not_model_inference(self):
        with patch.object(workflow, "REPAIR_BUDGET", 1):
            record, calls = self.execute(workflow.ARMS[2], proposal("CSV"))
        self.assertEqual(record["status"], "repair_failed")
        self.assertEqual(record["repair_attempts"], 1)
        self.assertEqual(record["extra_model_calls"], 0)
        self.assertEqual(calls.records[0]["inference_attempts"], 0)

    def test_schema_failure_in_initial_response_has_no_tool_or_repair(self):
        calls = workflow.ModelCalls(FakeClient("not-json"), "offline", self.stream, timeout=1)
        model = calls.call(self.runtime["id"], "relay_initial", self.branches.messages)
        record = workflow.execute_arm(self.runtime, workflow.ARMS[2], model, self.directory / "invalid",
                                      self.stream, branches=self.branches, calls=calls)
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["action_executions"], 0)
        self.assertEqual(record["extra_model_calls"], 0)
        self.assertEqual(len(calls.records), 1)

    def test_partial_writer_failure_preserves_initial_execution_and_effect(self):
        def partial(raw, directory):
            (directory / "report.json").write_text("incomplete", encoding="utf-8")
            raise OSError("Offline disk failure")
        with patch.object(workflow.previous, "write_export", side_effect=partial):
            record, calls = self.execute(workflow.ARMS[2], proposal())
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["action_executions"], 1)
        self.assertEqual(record["action_completions"], 0)
        self.assertEqual(record["failed_executions"], 1)
        self.assertEqual(record["outcomes"]["partial_writes"], 1)
        self.assertEqual(record["outcomes"]["wrong_writes"], 1)
        self.assertEqual(record["output_artifacts"][0]["text"], "incomplete")
        self.assertEqual(record["execution_failure"]["check"]["decision"], "allow")
        self.assertEqual(record["repair_attempts"], 0)
        self.assertEqual(calls.records, [])

    def test_partial_writer_failure_after_repair_preserves_callback_and_check(self):
        def partial(raw, directory):
            (directory / "report.json").write_text("incomplete", encoding="utf-8")
            raise OSError("Offline disk failure")
        with patch.object(workflow.previous, "write_export", side_effect=partial):
            record, calls = self.execute(workflow.ARMS[2], proposal("CSV"), client=FakeClient(proposal()))
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["action_executions"], 1)
        self.assertEqual(record["action_completions"], 0)
        self.assertEqual(record["repair_attempts"], 1)
        self.assertEqual(record["extra_model_calls"], 1)
        self.assertEqual(len(calls.records), 1)
        self.assertEqual(record["outcomes"]["blocked_wrong_proposals"], 1)
        self.assertEqual(record["outcomes"]["partial_writes"], 1)
        self.assertFalse(record["score"]["task_complete"])
        self.assertEqual(record["execution_failure"]["check"]["decision"], "allow")

    def test_partial_utf8_bytes_cannot_erase_execution_failure_or_journal(self):
        fragment = b"\xe0\xa4"
        def partial(raw, directory):
            (directory / "summary.txt").write_bytes(fragment)
            raise OSError("Offline interrupted Hindi write")
        with patch.object(workflow.previous, "write_export", side_effect=partial):
            record, _ = self.execute(workflow.ARMS[2], proposal())
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error"]["type"], "ToolExecutionError")
        artifact = record["output_artifacts"][0]
        self.assertEqual(artifact["text_decode_error"], "UnicodeDecodeError")
        self.assertEqual(base64.b64decode(artifact["raw_base64"]), fragment)
        self.assertEqual(artifact["size_bytes"], 2)
        self.assertEqual(record["outcomes"]["partial_writes"], 1)
        journal = [json.loads(line) for line in (self.directory / "journal.jsonl").read_text().splitlines()]
        self.assertEqual(journal[-1]["event"], "attempt_result")
        self.assertEqual(journal[-1]["output_artifacts"], record["output_artifacts"])

    def test_baseline_serializer_accepts_same_known_enum_spelling(self):
        record, _ = self.execute(workflow.ARMS[0], proposal(" json ", " english "))
        self.assertTrue(record["score"]["task_complete"])

    def test_export_when_clarification_required_is_wrong_not_completed_work(self):
        record, _ = self.execute(workflow.ARMS[0], proposal())
        # Grade the same real files against the ambiguous-case rubric only afterward.
        graded = workflow.assess_record(record, {"clarification_required": True})
        self.assertFalse(graded["score"]["task_complete"])
        self.assertEqual(graded["outcomes"]["wrong_writes"], 1)
        self.assertEqual(graded["outcomes"]["correct_clarification"], 0)

    def test_dry_run_default_freezes_sources_and_prepares_without_local_client(self):
        with patch.object(workflow, "LocalOllama") as local, patch.object(workflow.ModelCalls, "call") as model, \
                patch("builtins.print"):
            path = self.directory / "dry.jsonl"
            self.assertEqual(workflow.main(["--model", "unused", "--output", str(path)]), 0)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            manifest = rows[0]
            self.assertEqual(manifest["planned_initial_calls"], 12)
            self.assertEqual(manifest["maximum_total_calls"], 18)
            self.assertEqual(len([row for row in rows if row["event"] == "prepared"]), 6)
            self.assertEqual(path.with_suffix(".source.py.txt").read_bytes(), Path(workflow.__file__).read_bytes())
            self.assertEqual(path.with_suffix(".evaluate_agent_workflow.py.txt").read_bytes(), Path(workflow.previous.__file__).read_bytes())
            self.assertEqual(rows[-1]["physical_model_calls"], 0)
            local.assert_not_called()
            model.assert_not_called()

    def test_setup_failure_is_recorded_and_other_cases_continue_without_inference(self):
        path = self.directory / "setup-error.jsonl"
        real = workflow.RelayBranches
        def make(case):
            if case["id"] == "buried_preference":
                raise ValueError("Offline setup fixture")
            return real(case)
        with patch.object(workflow, "RelayBranches", side_effect=make), patch("builtins.print"):
            result = workflow.main(["--model", "unused", "--output", str(path),
                "--case-id", "buried_preference", "--case-id", "correction_to_defaults"])
        self.assertEqual(result, 2)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len([row for row in rows if row["event"] == "case_setup_error"]), 1)
        self.assertEqual(len([row for row in rows if row["event"] == "prepared"]), 1)
        self.assertEqual(rows[-1]["physical_model_calls"], 0)
        self.assertEqual(rows[-1]["case_setup_errors"], 1)

    @unittest.skipUnless(importlib.util.find_spec("ollama"), "Optional Ollama SDK not installed")
    def test_full_cli_uses_two_initial_calls_and_one_repair_not_three_initial_calls(self):
        client = FakeClient(proposal("CSV"), proposal("CSV"), proposal())
        local = SimpleNamespace(base_url="http://127.0.0.1:11434", require_local_model=lambda name: name,
                                _request=lambda *args: {"offline_fixture": True})
        path = self.directory / "simulated.jsonl"
        with patch.object(workflow, "LocalOllama", return_value=local), patch("ollama.Client", return_value=client), \
                patch("builtins.print"):
            result = workflow.main(["--model", "offline-fixture", "--output", str(path), "--run-local",
                                    "--case-id", "buried_preference", "--max-calls", "3"])
        self.assertEqual(result, 0)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        graded = [row for row in rows if row["event"] == "graded_result"]
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(graded[1]["initial_call_id"], graded[2]["initial_call_id"])
        self.assertEqual(graded[1]["proposal"], graded[2]["proposal"])
        summary = rows[-1]
        self.assertEqual(summary["physical_initial_calls"], 2)
        self.assertEqual(summary["physical_extra_calls"], 1)
        self.assertEqual(summary["arms"][workflow.ARMS[2]]["complete_correct_tasks"], 1)
        self.assertEqual(summary["arms"][workflow.ARMS[1]]["blocked_wrong_proposals"], 1)


if __name__ == "__main__":
    unittest.main()
