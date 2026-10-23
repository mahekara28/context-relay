"""Coverage gates and independent readback fail closed without model calls."""
import time
import unittest
from unittest.mock import patch

from context_relay import ContextGuard, EvidenceChanged, EvidenceSource, OutcomePolicy, ToolRegistry
from context_relay.coverage import CoverageError, CoveragePolicy
from context_relay.outcomes import OutcomeVerifier


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.guard.remember("approved", "Alice")
        self.registry = ToolRegistry(self.guard)
        self.effects = []

    def register(self, **options):
        def send(name):
            self.effects.append(name)
            return {"ok": True}
        self.registry.register(send, bindings={"name": "approved"}, **options)

    def test_missing_inventory_fails_before_dispatch(self):
        self.register(outcome=OutcomePolicy(required={"/ok": True}))
        with self.assertRaises(CoverageError) as caught:
            self.registry.require_coverage(CoveragePolicy(["send", "erase"]))
        self.assertEqual(caught.exception.report["issues"][0], {"code": "missing_tools", "tools": ["erase"]})
        self.assertEqual(self.effects, [])

    def test_outcome_is_required_unless_explicitly_disabled(self):
        self.register()
        with self.assertRaises(CoverageError):
            self.registry.require_coverage(CoveragePolicy(["send"]))
        report = self.registry.require_coverage(CoveragePolicy(["send"], require_outcomes=False))
        self.assertTrue(report["requirements_satisfied"])
        self.assertFalse(report["direct_calls_protected"])
        self.registry.bind(self.guard.turn()).dispatch("send", {"name": "Alice"})
        self.assertEqual(self.effects, ["Alice"])

    def test_unchecked_exception_is_exact_and_visible(self):
        self.registry.register(lambda note: {"ok": True}, name="save", unchecked=("note",),
                               outcome=OutcomePolicy(required={"/ok": True}))
        with self.assertRaises(CoverageError):
            self.registry.require_coverage(CoveragePolicy(["save"]))
        report = self.registry.require_coverage(CoveragePolicy(["save"], allow_unchecked={"save": ["note"]}))
        self.assertEqual(report["coverage"]["tools"]["save"]["unchecked"], ["note"])
        with self.assertRaises(CoverageError):
            self.registry.require_coverage(CoveragePolicy(["save"], allow_unchecked={"save": ["other"]}))

    def test_inventory_growth_is_rechecked_on_bind(self):
        self.register(outcome=OutcomePolicy(required={"/ok": True}))
        self.registry.require_coverage(CoveragePolicy(["send"]))
        self.registry.register(lambda: {"ok": True}, name="extra", outcome=OutcomePolicy(required={"/ok": True}))
        with self.assertRaises(CoverageError):
            self.registry.bind(self.guard.turn())
        self.assertEqual(self.effects, [])

    def test_stale_policy_cannot_look_covered(self):
        self.register(outcome=OutcomePolicy(required={"/ok": True}))
        self.guard.allow_tool("send", optional_args=("name",))
        report = self.registry.coverage(["send"])
        self.assertEqual(report["stale_contracts"], ["send"])
        with self.assertRaises(CoverageError):
            self.registry.require_coverage(CoveragePolicy(["send"]))

    def test_policy_is_defensive_and_validates_exceptions(self):
        names, exceptions = ["send"], {"send": ["name"]}
        policy = CoveragePolicy(names, allow_unchecked=exceptions)
        names.append("erase")
        exceptions["send"].append("other")
        self.assertEqual(policy.inventory, ("send",))
        self.assertEqual(policy.to_dict()["allow_unchecked"], {"send": ["name"]})
        for inventory, options in (("send", {}), ([], {}), (["send", "send"], {}),
                                   (["send"], {"allow_unchecked": {"erase": []}}),
                                   (["send"], {"require_outcomes": 1})):
            with self.subTest(inventory=inventory, options=options), self.assertRaises((TypeError, ValueError)):
                CoveragePolicy(inventory, **options)


class IndependentEvidenceTests(unittest.TestCase):
    def source(self, verify, **options):
        return EvidenceSource(lambda: {"version": "1", "facts": {"count": 4}, "observed_at": time.time()},
                              verify=verify, verifier_id="actual-file-count-v1", **options)

    def test_false_truthy_unknown_and_failure_never_accept(self):
        for value in (False, 1, "yes", None, {"ok": True}):
            with self.subTest(value=value), self.assertRaises(EvidenceChanged):
                self.source(lambda snapshot: value).read()
        def broken(snapshot):
            raise OSError("source unavailable")
        with self.assertRaises(EvidenceChanged) as caught:
            self.source(broken).read()
        self.assertEqual(caught.exception.reason, "verification_unavailable")

    def test_verifier_runs_at_both_reads_with_defensive_values(self):
        calls = []
        def verify(snapshot):
            calls.append(snapshot.facts)
            snapshot.facts["count"] = 900
            return snapshot.facts == {"count": 4}
        source = self.source(verify)
        snapshot = source.read()
        source.assert_current(snapshot)
        self.assertEqual(calls, [{"count": 4}, {"count": 4}])
        self.assertEqual(source.describe(), {"max_age_seconds": 30, "verifier_id": "actual-file-count-v1"})

    def test_matching_source_still_rechecked_against_independent_data(self):
        actual = {"count": 4}
        source = self.source(lambda snapshot: snapshot.facts["count"] == actual["count"])
        snapshot = source.read()
        actual["count"] = 5
        with self.assertRaises(EvidenceChanged) as caught:
            source.assert_current(snapshot)
        self.assertEqual(caught.exception.reason, "verification_rejected")

    def test_verification_cannot_refresh_an_expired_snapshot(self):
        with patch("context_relay.fresh_evidence.time.time", side_effect=[10, 10, 40]):
            with self.assertRaises(EvidenceChanged) as caught:
                self.source(lambda _: True, max_age_seconds=20).read()
        self.assertEqual(caught.exception.reason, "expired")

    def test_optional_mode_preserves_existing_profile(self):
        source = EvidenceSource(lambda: {"version": "1", "facts": {"count": 4}, "observed_at": time.time()})
        self.assertEqual(source.describe(), {"max_age_seconds": 30})
        for options in ({"verify": lambda _: True}, {"verifier_id": "unused"},
                        {"verify": lambda _: True, "verifier_id": "x" * 129}):
            with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                EvidenceSource(lambda: None, **options)


class OutcomeVerifierTests(unittest.TestCase):
    def test_only_true_readback_accepts_and_input_is_defensive(self):
        result = {"ok": True}
        def readback(request):
            self.assertEqual(request["request_id"], "export-1")
            request["result"]["ok"] = False
            return True
        verifier = OutcomeVerifier(readback, verifier_id="file-hash-v1")
        report = verifier.check(request_id="export-1", tool="export", arguments={}, result=result)
        self.assertEqual(report["status"], "accepted")
        self.assertFalse(report["external_truth_verified"])
        self.assertEqual(result, {"ok": True})

    def test_wrong_missing_and_throwing_readback_are_not_success(self):
        for value, status in ((False, "rejected"), (1, "unavailable"), (None, "unavailable")):
            verifier = OutcomeVerifier(lambda request: value, verifier_id="disk-v1")
            self.assertEqual(verifier.check(request_id="x", tool="send", arguments=None, result={})["status"], status)
        def broken(request):
            raise RuntimeError("private error")
        report = OutcomeVerifier(broken, verifier_id="disk-v1").check(request_id="x", tool="send", arguments=None, result={})
        self.assertEqual(report["reason"], "readback_failed")
        self.assertNotIn("private", str(report))


if __name__ == "__main__":
    unittest.main()
