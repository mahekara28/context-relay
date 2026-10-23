"""Real local files and scripted native calls; no SDK, network or inference."""
import copy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("reliability_pilot_under_test", ROOT / "evaluations/run_reliability_pilot_v021.py")
pilot = module_from_spec(spec)
spec.loader.exec_module(pilot)
from context_relay import EvidenceSource
from release_desk import digest
from verified_release_desk import VerifiedReleaseDesk


class VerifiedReleaseDeskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.artifact = self.directory / "actual-package.txt"
        self.artifact.write_bytes(b"Actual local package content.\n")
        self.app = VerifiedReleaseDesk(self.directory / "application", release="R-1", artifacts=[self.artifact])

    def output_request(self, format="JSON"):
        source = self.app.desk.read_evidence()
        arguments = {"request_id": "one", "source_version": source["version"], "format": format,
                     "release": "R-1", "artifacts": source["facts"]["approved_artifacts"]}
        result = self.app.desk.export_manifest(**arguments)
        return {"request_id": "one", "tool": "export_manifest", "arguments": arguments, "result": result}

    def test_source_verification_reads_actual_bytes_instead_of_cached_metadata(self):
        snapshot = EvidenceSource(self.app.desk.read_evidence).read()
        self.assertTrue(self.app.verify_source(snapshot))
        self.artifact.write_bytes(b"Changed local package content.\n")
        self.assertFalse(self.app.verify_source(snapshot))

    def test_actual_json_and_csv_files_are_checked_against_arguments(self):
        for format in ("CSV", "JSON"):
            with self.subTest(format=format):
                # Each format uses a separate destination request identity.
                source = self.app.desk.read_evidence()
                arguments = {"request_id": format, "source_version": source["version"], "format": format,
                             "release": "R-1", "artifacts": source["facts"]["approved_artifacts"]}
                result = self.app.desk.export_manifest(**arguments)
                request = {"request_id": format, "tool": "export_manifest", "arguments": arguments, "result": result}
                self.assertTrue(self.app.verify_output(request))
                bad = copy.deepcopy(request)
                bad["arguments"]["artifacts"][0]["bytes"] += 1
                self.assertFalse(self.app.verify_output(bad))

    def test_tampered_missing_file_and_unknown_intent_never_pass(self):
        request = self.output_request()
        path = self.app.desk.directory / (digest("one") + ".json")
        path.write_bytes(b"not the committed manifest")
        self.assertFalse(self.app.verify_output(request))
        path.unlink()
        self.assertIsNone(self.app.verify_output(request))
        request["arguments"] = None
        self.assertIsNone(self.app.verify_output(request))

    def test_a_lying_receipt_cannot_substitute_another_request(self):
        request = self.output_request()
        request["result"]["request_id"] = "someone-else"
        self.assertFalse(self.app.verify_output(request))

    def test_scripted_pilot_accounts_for_all_cases_repairs_handoffs_and_restart(self):
        report = pilot.run(self.directory / "pilot")
        self.assertIsNone(report["run_error"])
        summary = report["summary"]
        self.assertEqual(summary["planned_requests"], 9)
        self.assertEqual(summary["planned_exports"], 8)
        self.assertEqual(summary["attempted_requests"], 9)
        self.assertEqual(summary["unattempted_requests"], [])
        self.assertEqual(summary["correct_completed_exports"], 8)
        self.assertEqual(summary["reviews_before_model"], 9)
        self.assertEqual(summary["reviews_withheld"], 1)
        self.assertEqual(summary["provider_calls"], 8)
        self.assertEqual(summary["actual_model_calls"], 0)
        self.assertEqual(summary["tool_starts"], 8)
        self.assertEqual(summary["deterministic_repairs"], 2)
        self.assertEqual(summary["wrong_documents"], 0)
        self.assertEqual(summary["bad_or_missing_files"], 0)
        self.assertEqual(summary["duplicate_tool_starts"], 0)
        self.assertEqual(summary["errors"], 0)
        self.assertTrue(report["executed_modules_unchanged"])
        self.assertGreater(report["history"]["archive"]["segments"], 0)
        self.assertFalse(report["history"]["reasoning_continuity_preserved"])
        self.assertTrue(report["restart"]["same_session"])
        self.assertTrue(report["restart"]["replayed"])
        self.assertEqual(report["restart"]["extra_provider_calls"], 0)
        self.assertEqual(report["restart"]["extra_tool_starts"], 0)
        by_id = {row["id"]: row for row in report["cases"]}
        for identity, format in (("restored-csv", "CSV"), ("quoted-report", "JSON"), ("restored-json", "JSON")):
            self.assertEqual(by_id[identity]["review"]["memory"]["fields"][0]["value"], format)


if __name__ == "__main__":
    unittest.main()
