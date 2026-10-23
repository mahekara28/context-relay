"""Keep success, clarification, and false alarms distinct in the release evidence."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("correction_scoring_v014", Path(__file__).resolve().parents[1]/"examples/evaluate_corrections_v014.py")
scoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoring)


class CorrectionScoringTests(unittest.TestCase):
    def test_uncertainty_is_not_an_automatic_success(self):
        case = {"kind": "clear", "expected": {"export_format": "JSON"}}
        pending = [{"reason": "unparsed_correction", "value": None, "targets": ["export_format"]}]
        result = scoring.grade(case, {"export_format": "CSV"}, {"export_format": "CSV"}, pending)
        self.assertFalse(result["correct_automatic_update"])
        self.assertTrue(result["clarification_flagged"])
        self.assertFalse(result["wrong_active_update"])

    def test_pending_wrong_target_or_invented_value_does_not_resolve_uncertainty(self):
        case = {"kind": "uncertain", "targets": ["export_format"]}
        before = {"export_format": "CSV"}
        for proposal in (
            {"reason": "unparsed_correction", "value": None, "targets": ["other"]},
            {"reason": "interpreted_needs_review", "value": "JSON", "targets": ["export_format"]},
        ):
            with self.subTest(proposal=proposal):
                self.assertFalse(scoring.grade(case, before, before, [proposal])["correct_uncertainty"])
        result = scoring.grade(case,before,{"export_format":"the other choice"},[])
        self.assertTrue(result["wrong_active_update"])

    def test_wrong_extra_value_or_partial_compound_is_not_success(self):
        case={"kind":"clear","expected":{"export_format":"JSON","response_language":"Hindi"}}
        before={"export_format":"CSV","response_language":"English"}
        partial={**before,"export_format":"JSON"}
        self.assertFalse(scoring.grade(case,before,partial,[])["correct_automatic_update"])
        extra={**case["expected"],"invented":"value"}
        self.assertTrue(scoring.grade(case,before,extra,[])["wrong_active_update"])

    def test_known_case_equivalence_is_separate_from_exact_canonical_score(self):
        case={"kind":"clear","expected":{"export_format":"JSON"}}
        result=scoring.grade(case,{"export_format":"CSV"},{"export_format":"json"},[])
        self.assertTrue(result["correct_automatic_update"])
        self.assertFalse(result["exact_canonical_update"])
        self.assertFalse(result["wrong_active_update"])
        custom={"kind":"clear","expected":{"code":"AbC"}}
        self.assertTrue(scoring.grade(custom,{"code":"old"},{"code":"abc"},[])["wrong_active_update"])

    def test_spurious_clarification_is_a_false_alarm(self):
        case={"kind":"abstain"}
        values={"export_format":"CSV"}
        result=scoring.grade(case,values,values,[{"reason":"unparsed_correction","value":None,"targets":["export_format"]}])
        self.assertFalse(result["correct_abstention"])
        self.assertTrue(result["false_alarm"])


if __name__ == "__main__":
    unittest.main()
