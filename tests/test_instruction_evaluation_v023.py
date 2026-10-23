"""Scoring, accounting and interruption evidence without making model calls."""

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import context_relay as api


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("instruction_evaluation_v023", ROOT / "examples/evaluate_instructions_v023.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def corpus():
    return {"seed": ["Export as CSV."], "standing": {"export_format": "CSV"}}


def case(**changes):
    return {"id": "review-probe", "split": "development", "language": "English", "kind": "change",
            "text": "Give every export a machine-readable shape.", "expected": {"export_format": "JSON"}, **changes}


def interpreter(values):
    return api.MemoryInterpreter(lambda request: [{"key": key, "value": value, "quote": request["text"],
        "start": 0, "end": len(request["text"])} for key, value in values], review_unmatched=True)


def state_api(*, standing=None, snapshot=None, pending=None, fail=False, unreadable=False):
    """Inject corrupted post-process state to test the grader, not the parser."""
    class Memory:
        def __init__(self, *args, **kwargs):
            self.count = 0

        def process(self, history):
            self.count += 1
            if fail and self.count > 1:
                try:
                    raise ValueError("underlying failure")
                except ValueError as error:
                    raise RuntimeError("processing failed") from error

        def entries(self):
            if self.count == 1:
                return [{"key": "export_format", "value": "CSV"}]
            if unreadable:
                raise RuntimeError("storage unavailable")
            return [{"key": key, "value": value} for key, value in (standing or {}).items()]

        def effective_snapshot(self):
            if unreadable:
                raise RuntimeError("snapshot unavailable")
            return snapshot if snapshot is not None else {"entries": []}

        def pending(self):
            if unreadable:
                raise RuntimeError("pending unavailable")
            return pending or []

    return SimpleNamespace(RelayStore=api.RelayStore, MemoryPolicy=api.MemoryPolicy, AutomaticMemory=Memory)


class InstructionScoringTests(unittest.TestCase):
    def test_conflicting_proposals_are_not_collapsed_into_a_correct_suggestion(self):
        row = evaluation.evaluate_case(case(), corpus(), api,
            interpreter([("export_format", "XML"), ("export_format", "JSON")]))
        self.assertFalse(row["suggestion_correct"])
        self.assertEqual([p["value"] for p in row["suggestions"]], ["XML", "JSON"])
        self.assertEqual(len(row["pending"]), 2)
        self.assertFalse(row["decision_correct"])
        self.assertEqual(row["standing"], corpus()["standing"])

    def test_single_correct_proposal_is_counted_without_autoapproval(self):
        row = evaluation.evaluate_case(case(), corpus(), api, interpreter([("export_format", "JSON")]))
        self.assertTrue(row["suggestion_correct"])
        self.assertFalse(row["decision_correct"])
        self.assertEqual(row["interpreter_calls"], 1)
        self.assertEqual(row["standing"], {"export_format": "CSV"})
        self.assertLessEqual(row["interpreter_seconds"], row["seconds"])
        self.assertEqual(row["timing_scope"], "case_wall_time_including_setup_seed_and_checks")

    def test_extra_and_incorrect_proposals_are_not_counted_as_correct(self):
        for values in ([('export_format', 'JSON'), ('delivery_window', 'morning')], [('export_format', 'XML')]):
            with self.subTest(values=values):
                row = evaluation.evaluate_case(case(), corpus(), api, interpreter(values))
                self.assertFalse(row["suggestion_correct"])

    def test_non_interpreted_extra_dispute_invalidates_suggestion_success(self):
        pending = [{"key": "export_format", "value": "JSON", "targets": ["export_format"],
                    "reason": "interpreted_needs_review"},
                   {"key": "another", "value": None, "targets": ["another"], "reason": "unparsed_correction"}]
        fake = state_api(standing=corpus()["standing"], pending=pending,
                         snapshot={"entries": [{"key": "export_format", "value": "CSV", "disputed": True}]})
        self.assertFalse(evaluation.evaluate_case(case(), corpus(), fake)["suggestion_correct"])

    def test_clarification_does_not_hide_unrelated_effective_values(self):
        unclear = case(kind="uncertain", targets=["export_format"], expected={})
        snapshot = {"entries": [{"key": "export_format", "value": "CSV", "disputed": True},
                                {"key": "surprise", "value": "added", "disputed": False}]}
        fake = state_api(standing=corpus()["standing"], snapshot=snapshot)
        row = evaluation.evaluate_case(unclear, corpus(), fake)
        self.assertFalse(row["decision_correct"])
        self.assertEqual(row["outcome"], "missed_clarification")

    def test_clarification_does_not_hide_unrelated_pending_targets(self):
        unclear = case(kind="uncertain", targets=["export_format"], expected={})
        fake = state_api(standing=corpus()["standing"],
            snapshot={"entries": [{"key": "export_format", "value": "CSV", "disputed": True}]},
            pending=[{"key": "surprise", "value": "new", "targets": ["surprise"], "reason": "interpreted_needs_review"}])
        self.assertFalse(evaluation.evaluate_case(unclear, corpus(), fake)["decision_correct"])

    def test_deleted_standing_keys_are_incorrect_mutations(self):
        row = evaluation.evaluate_case(case(), corpus(), state_api(standing={}))
        self.assertTrue(row["incorrect_mutation"])
        self.assertFalse(row["decision_correct"])

    def test_processing_error_still_reports_a_readable_mutation(self):
        row = evaluation.evaluate_case(case(), corpus(), state_api(standing={}, fail=True))
        self.assertEqual(row["outcome"], "error")
        self.assertTrue(row["incorrect_mutation"])
        self.assertEqual(row["standing"], {})
        self.assertEqual([error["type"] for error in row["cause_chain"]], ["RuntimeError", "ValueError"])

    def test_unreadable_error_state_is_unknown_instead_of_zero(self):
        row = evaluation.evaluate_case(case(), corpus(), state_api(fail=True, unreadable=True))
        self.assertIsNone(row["incorrect_mutation"])
        self.assertIsNone(row["unnecessary_review"])
        self.assertEqual(set(row["state_errors"]), {"standing", "snapshot", "pending"})
        summary = evaluation.summarize([row])["all"]
        self.assertEqual(summary["unknown_mutation_states"], 1)
        self.assertEqual(summary["unknown_review_states"], 1)
        self.assertEqual(summary["outcomes"], {"error": 1})

    def test_failed_interpreter_attempt_is_counted_without_seed_calls(self):
        def fail(request):
            raise RuntimeError("decoder unavailable")
        row = evaluation.evaluate_case(case(), corpus(), api, api.MemoryInterpreter(fail, review_unmatched=True))
        self.assertEqual(row["interpreter_calls"], 1)
        self.assertEqual(row["outcome"], "error")
        self.assertFalse(row["incorrect_mutation"])
        self.assertEqual(row["standing"], corpus()["standing"])


class InstructionJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.output = root / "result.json"
        self.journal = root / "result.json.jsonl"
        self.corpus = root / "corpus.json"
        self.corpus.write_text(json.dumps({**corpus(), "cases": [case(id="one"), case(id="two")]}), encoding="utf-8")
        self.arguments = ["--corpus", str(self.corpus), "--output", str(self.output), "--source", str(ROOT / "src")]

    def row(self, identifier="one", **changes):
        return {"id": identifier, "split": "development", "decision_correct": False,
                "suggestion_correct": False, "incorrect_mutation": False, "unnecessary_review": False,
                "interpreter_calls": 0, "outcome": "missed_change", **changes}

    def events(self):
        return [json.loads(line) for line in self.journal.read_text(encoding="utf-8").splitlines()]

    def test_interrupt_retains_completed_case_without_a_canonical_report(self):
        with patch.object(evaluation, "evaluate_case", side_effect=[self.row(), KeyboardInterrupt]), redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                evaluation.main(self.arguments)
        self.assertFalse(self.output.exists())
        events = self.events()
        self.assertEqual([event["event"] for event in events],
                         ["started", "case_started", "case_completed", "case_started", "interrupted"])
        self.assertEqual(events[2]["data"], self.row())
        before = self.journal.read_bytes()
        with self.assertRaises(SystemExit) as error:
            evaluation.main(self.arguments)
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.journal.read_bytes(), before)

    def test_completed_report_has_exclusive_journal_and_matching_hash(self):
        with patch.object(evaluation, "evaluate_case", side_effect=[self.row(), self.row("two")]), redirect_stdout(io.StringIO()):
            self.assertEqual(evaluation.main(self.arguments), 0)
        report = json.loads(self.output.read_bytes())
        self.assertTrue(report["complete"])
        self.assertEqual(report["schema"], 2)
        self.assertEqual(report["selected_case_ids"], ["one", "two"])
        self.assertEqual(self.events()[-1], {"event": "completed", "data": {
            "completed_cases": 2, "report_sha256": hashlib.sha256(self.output.read_bytes()).hexdigest()}})

    def test_error_rows_are_preserved_and_make_the_command_unsuccessful(self):
        rows = [self.row(outcome="error", error="example error"), self.row("two")]
        with patch.object(evaluation, "evaluate_case", side_effect=rows), redirect_stdout(io.StringIO()):
            self.assertEqual(evaluation.main(self.arguments), 1)
        self.assertEqual(json.loads(self.output.read_bytes())["cases"], rows)
        self.assertEqual(self.events()[2]["data"]["error"], "example error")

    def test_source_change_invalidates_run_without_discarding_evidence(self):
        with patch.object(evaluation, "evaluate_case", side_effect=[self.row(), self.row("two")]), \
                patch.object(evaluation, "_source_hashes", side_effect=[{"file.py": "old"}, {"file.py": "new"}]), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(evaluation.main(self.arguments), 1)
        self.assertFalse(json.loads(self.output.read_bytes())["source_unchanged"])
        self.assertEqual(self.events()[-1]["event"], "completed")


if __name__ == "__main__":
    unittest.main()
