"""Independent oracle/integrity checks for the matched framework comparison."""
import ast
from copy import deepcopy
from importlib.util import find_spec, module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(find_spec("langgraph"), "Optional LangGraph is not installed")
class FrameworkComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = spec_from_file_location("framework_comparison_under_test", ROOT / "evaluations" / "compare_framework_v020.py")
        cls.module = module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.report = cls.module.run_comparison(Path(cls.temporary.name) / "destinations")

    def test_real_framework_both_arms_complete_the_same_frozen_boundaries(self):
        self.assertTrue(self.module.validate_report(self.report))
        self.assertEqual(self.report["source_before"], self.report["source_after"])
        expected = {"send_tasks": 13, "operations": 16, "correct_completed_tasks": 8,
            "reported_completed_tasks": 9, "wrong_effects": 1, "supported_wrong_effects": 0,
            "known_limitation_wrong_effects": 1, "false_blocks": 0, "blocked_tasks": 3,
            "clarification_requests": 1, "repair_attempts": 2, "repair_induced_wrong_effects": 0,
            "duplicate_effects": 0, "duplicate_destination_calls": 0, "proposal_calls": 12,
            "model_calls": 0, "destination_calls": 9, "unexpected_errors": 0, "boundary_mismatches": 0}
        for arm in self.module.ARMS:
            with self.subTest(arm=arm):
                summary = self.report["arms"][arm]["summary"]
                self.assertEqual({key: summary[key] for key in expected}, expected)
                self.assertTrue(all(case["checkpoint_count"] > 2 for case in self.report["arms"][arm]["cases"]))

    def test_ordinary_validator_has_no_relay_imports(self):
        source = (ROOT / "evaluations" / "framework_baseline_v020.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or "").startswith("context_relay"))
            elif isinstance(node, ast.Import):
                self.assertFalse(any(item.name.startswith("context_relay") for item in node.names))

    def test_missing_case_operation_or_request_cannot_improve_report(self):
        for target in ("case", "operation", "request"):
            report = deepcopy(self.report)
            arm = report["arms"][self.module.ARMS[0]]
            if target == "case":
                arm["cases"].pop()
            elif target == "operation":
                arm["cases"][0]["operations"].pop()
            else:
                arm["cases"][0]["requests"].pop("durable-1")
            with self.subTest(target=target):
                self.assertFalse(self.module.validate_report(report))

    def test_missing_real_effect_cannot_be_hidden_by_recomputed_counts(self):
        report = deepcopy(self.report)
        arm = report["arms"][self.module.ARMS[0]]
        arm["cases"][0]["documents"].pop()
        arm["summary"] = self.module.summarize(arm["cases"], self.module.load_fixtures())
        self.assertFalse(self.module.validate_report(report))

    def test_known_wrong_source_effect_is_not_relabelled_correct(self):
        report = deepcopy(self.report)
        arm = report["arms"][self.module.ARMS[0]]
        control = arm["cases"][-1]
        expected = self.module.load_fixtures()["cases"][-1]["expected_documents"]["wrong-source-1"]
        self.assertFalse(self.module.document_matches(control["documents"][0], expected))
        arm["summary"]["wrong_effects"] = 0
        arm["summary"]["correct_completed_tasks"] = 9
        self.assertFalse(self.module.validate_report(report))

    def test_mutating_both_arms_proposals_still_fails_frozen_manifest(self):
        report = deepcopy(self.report)
        for arm in self.module.ARMS:
            proposal = report["arms"][arm]["cases"][0]["proposal_calls"][0]
            proposal["proposal"]["arguments"]["format"] = "XML"
            proposal["proposal_sha256"] = self.module.digest(proposal["proposal"])
        self.assertFalse(self.module.validate_report(report))

    def test_interrupted_write_replay_and_reconciliation_have_one_effect(self):
        for arm in self.module.ARMS:
            case = next(case for case in self.report["arms"][arm]["cases"]
                        if case["case_id"] == "postwrite-replay-reconcile")
            self.assertEqual([operation["result"]["status"] for operation in case["operations"]],
                             ["pending", "pending", "completed", "completed"])
            self.assertEqual(len(case["destination_calls"]), 1)
            self.assertEqual(len(case["proposal_calls"]), 1)
            self.assertEqual(len(case["documents"]), 1)


if __name__ == "__main__":
    unittest.main()
