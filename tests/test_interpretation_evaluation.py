"""Offline evaluation integrity: review suggestions are not applied successes."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from evaluate_interpretation_v013 import score


class InterpretationEvaluationTests(unittest.TestCase):
    def test_rule_success_and_pending_suggestion_are_counted_separately(self):
        case = {"kind": "clear", "expected": {"export_format": "JSON"}}
        before = {"export_format": "CSV"}
        direct = score(case, before, case["expected"], [])
        review = score(case, before, before, [{"key": "export_format", "value": "JSON"}])
        self.assertTrue(direct["accepted_by_rules"])
        self.assertFalse(direct["exact_pending_candidate"])
        self.assertFalse(review["accepted_by_rules"])
        self.assertTrue(review["exact_pending_candidate"])

    def test_casing_wrong_keys_and_extra_suggestions_remain_failures(self):
        case = {"kind": "clear", "expected": {"export_format": "JSON"}}
        before = {"export_format": "CSV"}
        for pending in ([{"key": "export_format", "value": "json"}],
                        [{"key": "format", "value": "JSON"}],
                        [{"key": "export_format", "value": "JSON"}, {"key": "unknown", "value": "x"}],
                        [{"key": "export_format", "value": "XML"}, {"key": "export_format", "value": "JSON"}]):
            with self.subTest(pending=pending):
                self.assertFalse(score(case, before, before, pending)["passed"])

    def test_compound_change_requires_all_requested_values(self):
        case = {"kind": "clear", "expected": {"export_format": "JSON", "response_language": "Hindi"}}
        before = {"export_format": "CSV", "response_language": "English"}
        pending = [{"key": "export_format", "value": "JSON"}]
        self.assertFalse(score(case, before, before, pending)["passed"])
        pending.append({"key": "response_language", "value": "Hindi"})
        self.assertTrue(score(case, before, before, pending)["exact_pending_candidate"])

    def test_review_requires_unchanged_active_state_and_known_key(self):
        case = {"kind": "review", "candidate": "Hindi"}
        before = {"response_language": "English"}
        self.assertFalse(score(case, before, {"response_language": "Hindi"}, [
            {"key": "response_language", "value": "Hindi"}])["passed"])
        self.assertFalse(score(case, before, before, [{"key": "language", "value": "Hindi"}])["passed"])

    def test_abstention_does_not_count_false_pending_memory_as_success(self):
        case = {"kind": "abstain"}
        before = {"export_format": "CSV"}
        self.assertTrue(score(case, before, before, [])["passed"])
        self.assertFalse(score(case, before, before, [{"key": "export_format", "value": "JSON"}])["passed"])


if __name__ == "__main__":
    unittest.main()
