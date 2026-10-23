"""Literal end-to-end oracles and fail-closed evaluation accounting."""
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "refund_desk.py"
spec = importlib.util.spec_from_file_location("refund_desk_example_v019", EXAMPLE)
refund_desk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refund_desk)


class RefundDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = refund_desk.run_suite()

    def test_complete_offline_suite_and_actual_receipt_oracle(self):
        report = self.report
        self.assertTrue(report["validation_passed"])
        self.assertTrue(refund_desk.validate_report(report))
        summary = report["summary"]
        self.assertEqual(summary["required_cases"], 14)
        self.assertEqual(summary["boundary_cases_passed"], 14)
        self.assertEqual(summary["completed_tasks"], 5)
        self.assertEqual(summary["blocked_tasks"], 7)
        self.assertEqual(summary["clarification_requests"], 1)
        self.assertEqual(summary["interrupted_tasks"], 1)
        self.assertEqual(summary["unfinished_tasks"], 9)
        self.assertEqual(summary["correct_effects_supported"], 6)
        self.assertEqual(summary["incorrect_effects_supported"], 0)
        self.assertEqual(summary["scripted_proposal_calls"], 14)
        self.assertEqual(summary["real_model_calls"], 0)
        self.assertEqual(summary["network_calls"], 0)
        self.assertFalse(report["model_quality_evidence"])
        self.assertFalse(report["customer_pilot"])
        self.assertEqual(report["source_before"], report["source_after"])
        self.assertEqual(report["oracle"], {"order_id": "O-41", "amount": 20, "approved": True,
                                         "format": "JSON", "note": "Customer requested refund."})

    def test_known_limitation_is_wrong_effect_not_success(self):
        control = self.report["known_limitation"]
        self.assertFalse(control["counted_as_supported_success"])
        self.assertEqual(control["status"], "executed")
        self.assertEqual(control["receipts"][0]["data"]["amount"], 2000)
        self.assertEqual(self.report["summary"]["incorrect_effects_known_limitation"], 1)
        self.assertEqual(self.report["summary"]["incorrect_effects_total"], 1)
        self.assertNotIn(control["name"], self.report["required_scenarios"])

    def test_repair_completes_once_without_additional_provider_calls(self):
        rows = {row["name"]: row for row in self.report["cases"]}
        for name in ("wrong_amount_repaired", "missing_amount_repaired", "stale_format_repaired"):
            row = rows[name]
            self.assertEqual(row["status"], "repaired")
            self.assertEqual(row["repair_attempts"], 1)
            self.assertEqual(row["scripted_proposal_calls"], 1)
            self.assertEqual(row["repair_callback_calls"], 0)
            self.assertEqual(len(row["receipts"]), 1)
            self.assertEqual(row["receipts"][0]["data"]["amount"], 20)
            self.assertEqual(row["receipts"][0]["data"]["format"], "JSON")

    def test_missing_duplicate_or_failed_cases_cannot_be_hidden(self):
        for mutate in (lambda report: report["cases"].pop(),
                       lambda report: report["cases"].append(copy.deepcopy(report["cases"][0])),
                       lambda report: report["cases"][0].update(status="blocked", boundary_passed=True),
                       lambda report: report["cases"][0].update(name="unknown_case", boundary_passed=True),
                       lambda report: report.update(required_scenarios=[])):
            report = copy.deepcopy(self.report)
            mutate(report)
            self.assertFalse(refund_desk.validate_report(report))

    def test_false_summary_and_positive_flags_do_not_override_raw_outcomes(self):
        for field in ("completed_tasks", "boundary_cases_passed", "incorrect_effects_total", "scripted_proposal_calls"):
            report = copy.deepcopy(self.report)
            report["summary"][field] = 999
            self.assertFalse(refund_desk.validate_report(report))
        report = copy.deepcopy(self.report)
        report["cases"][0].update(receipts=[], effects_started=0, boundary_passed=True)
        self.assertFalse(refund_desk.validate_report(report))

    def test_wrong_added_or_mistyped_receipts_fail_literal_oracle(self):
        for value in (2000, True, 20.0, "20"):
            report = copy.deepcopy(self.report)
            report["cases"][0]["receipts"][0]["data"]["amount"] = value
            self.assertFalse(refund_desk.validate_report(report))
        report = copy.deepcopy(self.report)
        report["cases"][4]["receipts"].append(copy.deepcopy(report["cases"][0]["receipts"][0]))
        self.assertFalse(refund_desk.validate_report(report))

    def test_receipt_hashes_metadata_and_source_hashes_must_remain_consistent(self):
        for mutate in (lambda report: report["cases"][0]["receipts"][0].update(sha256="0" * 64),
                       lambda report: report.update(source_after={}),
                       lambda report: report.update(source_before={}, source_after={}),
                       lambda report: report["oracle"].update(amount=2000),
                       lambda report: report.update(customer_pilot=True),
                       lambda report: report.update(model_quality_evidence=True)):
            report = copy.deepcopy(self.report)
            mutate(report)
            self.assertFalse(refund_desk.validate_report(report))

    def test_limiter_control_cannot_be_removed_relabelled_or_marked_as_success(self):
        for mutate in (lambda report: report.pop("known_limitation"),
                       lambda report: report["known_limitation"].update(counted_as_supported_success=True),
                       lambda report: report["known_limitation"].update(status="blocked"),
                       lambda report: report["known_limitation"].update(receipts=[]),
                       lambda report: report["known_limitation"].update(effects_started=True),
                       lambda report: report["known_limitation"].update(clarification_requests=1),
                       lambda report: report["known_limitation"].update(replay_attempts=1),
                       lambda report: report["known_limitation"]["receipts"][0]["data"].update(approved=1)):
            report = copy.deepcopy(self.report)
            mutate(report)
            self.assertFalse(refund_desk.validate_report(report))

    def test_interrupted_effect_does_not_count_as_completed_or_repeat(self):
        row = next(row for row in self.report["cases"] if row["name"] == "effect_then_interrupt")
        self.assertEqual(row["status"], "interrupted")
        self.assertEqual(row["receipt_statuses"], ["failed"])
        self.assertEqual(row["effects_started"], 1)
        self.assertEqual(row["replay_attempts"], 1)
        self.assertTrue(row["replay_blocked"])
        report = copy.deepcopy(self.report)
        next(row for row in report["cases"] if row["name"] == "effect_then_interrupt")["replay_blocked"] = False
        self.assertFalse(refund_desk.validate_report(report))

    def test_named_restart_checks_same_session_and_identical_policy(self):
        row = next(row for row in self.report["cases"] if row["name"] == "named_restart")
        self.assertTrue(row["resumed_session"])
        self.assertTrue(row["resume_policy_revision_unchanged"])
        self.assertEqual(row["receipts"][0]["data"], self.report["oracle"])

    def test_cli_creates_report_exclusively_and_returns_failure_for_failed_suite(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory) / "report.json"
            with patch.object(refund_desk, "run_suite", return_value=self.report):
                self.assertEqual(refund_desk.main(["--output", str(path)]), 0)
                original = path.read_bytes()
                with self.assertRaises(FileExistsError):
                    refund_desk.main(["--output", str(path)])
                self.assertEqual(path.read_bytes(), original)
            failed = copy.deepcopy(self.report)
            failed["validation_passed"] = False
            with patch.object(refund_desk, "run_suite", return_value=failed):
                self.assertEqual(refund_desk.main([]), 1)


if __name__ == "__main__":
    unittest.main()
