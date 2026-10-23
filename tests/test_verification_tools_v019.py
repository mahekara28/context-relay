"""Declared claim verification at the tool boundary; all execution is local."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import (ActionRejected, AutomaticMemory, ContentPolicy,
                           ContextGuard, PolicyError)
from context_relay.models import validate_policy
from context_relay.tools import ToolRegistry
from context_relay.verification import VerificationPolicy


def contract(**source_options):
    source = {"data": {"units": 2, "price": 10, "limit": 15, "private": "private-ledger-data"},
              "trusted": True, "independence_key": "billing-ledger", "observed_at": time.time(),
              "max_age_seconds": 3600, **source_options}
    total = {"op": "multiply", "args": [{"source": "ledger", "path": "/units"},
                                           {"source": "ledger", "path": "/price"}]}
    return VerificationPolicy(sources={"ledger": source}, claims={
        "total": total, "approved": {"op": "lte", "args": [total, {"source": "ledger", "path": "/limit"}]}})


class VerificationToolTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.calls = []

    def register(self, policy=None, **options):
        self.guard.allow_tool("invoice", verification=contract() if policy is None else policy, **options)

    def action(self, arguments, *, revision=None):
        return {"tool": "invoice", "arguments": arguments,
                "context_revision": self.guard.turn().revision if revision is None else revision}

    def executor(self, tool, arguments):
        self.calls.append((tool, arguments))
        return {"saved": True}

    def execute(self, arguments, *, revision=None):
        return self.guard.relay.execute(self.guard.session_id,
                                        self.action(arguments, revision=revision), self.executor)

    def test_correct_derived_claims_execute_once_with_verification_report(self):
        self.register()
        result = self.execute({"total": 20, "approved": False})
        self.assertTrue(result["executed"])
        self.assertEqual(self.calls, [("invoice", {"total": 20, "approved": False})])
        report = result["check"]["verification_check"]
        self.assertTrue(report["valid"])
        self.assertFalse(report["semantic_meaning_checked"])
        self.assertEqual(report["tool_argument_scope"], {
            "declared_claim_arguments": ["approved", "total"], "other_supplied_arguments": []})

    def test_wrong_missing_and_boolean_numeric_claims_never_execute(self):
        self.register()
        for arguments in ({"total": 2000, "approved": False}, {"total": 20, "approved": True},
                          {"total": 20}, {"approved": False}, {"total": True, "approved": False}, {}):
            with self.subTest(arguments=arguments):
                result = self.execute(arguments)
                self.assertFalse(result["executed"])
                self.assertEqual(result["check"]["decision"], "block")
                self.assertIn("verification_failed", {row["code"] for row in result["check"]["issues"]})
                self.assertFalse(result["check"]["verification_check"]["valid"])
        self.assertEqual(self.calls, [])

    def test_untrusted_expired_and_future_sources_block_before_tool(self):
        for options in ({"trusted": False}, {"observed_at": time.time() - 7200},
                        {"observed_at": time.time() + 3600}):
            with self.subTest(options=options):
                self.register(contract(**options))
                result = self.execute({"total": 20, "approved": False})
                self.assertFalse(result["executed"])
                self.assertFalse(result["check"]["verification_check"]["ready"])
        self.assertEqual(self.calls, [])

    def test_source_expiry_is_rechecked_at_execution_even_without_revision_change(self):
        now = time.time()
        self.register(contract(observed_at=now, max_age_seconds=1))
        proposal = self.action({"total": 20, "approved": False})
        with patch("context_relay.verification.time.time", return_value=now):
            check = self.guard.relay.check(self.guard.session_id, proposal)
        self.assertEqual(check["decision"], "allow")
        with patch("context_relay.verification.time.time", return_value=now + 2):
            result = self.guard.relay.execute(self.guard.session_id, proposal, self.executor)
        self.assertFalse(result["executed"])
        self.assertEqual(result["check"]["revision"], proposal["context_revision"])
        self.assertFalse(result["check"]["verification_check"]["ready"])
        self.assertEqual(self.calls, [])

    def test_claim_projection_preserves_independent_bindings_content_and_unchecked_scope(self):
        self.guard.remember("destination", "invoice.json")
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Export as JSON."}])
        self.register(bindings={"destination": "destination"}, memory_bindings={"format": "export_format"},
                      content=ContentPolicy({"rows": [{"name": "Original name"}]}, exact=("/rows",)),
                      optional_args=("explanation",))
        arguments = {"total": 20, "approved": False, "destination": "invoice.json", "format": "json",
                     "rows": [{"name": "Original name"}], "explanation": "Unverified business advice"}
        result = self.execute(arguments)
        self.assertTrue(result["executed"])
        self.assertEqual(self.calls[0][1]["format"], "JSON")
        self.assertTrue(result["check"]["content_check"]["valid"])
        report = result["check"]["verification_check"]
        self.assertTrue(report["valid"])
        self.assertEqual(report["tool_argument_scope"]["other_supplied_arguments"],
                         ["destination", "explanation", "format", "rows"])
        self.assertFalse(report["semantic_meaning_checked"])
        for name, value, expected in (("destination", "other.json", "binding_mismatch"),
                                      ("rows", [{"name": "Translated name"}], "content_mismatch")):
            with self.subTest(argument=name):
                failed = self.execute({**arguments, name: value})
                self.assertFalse(failed["executed"])
                self.assertTrue(failed["check"]["verification_check"]["valid"])
                self.assertIn(expected, {row["code"] for row in failed["check"]["issues"]})
        self.assertEqual(len(self.calls), 1)

    def test_overlapping_memory_claim_checks_the_normalized_dispatched_value(self):
        AutomaticMemory(self.guard.store, self.guard.session_id).process([
            {"role": "user", "content": "Export as JSON."}])
        for expected, allowed in (("json", False), ("JSON", True)):
            with self.subTest(expected=expected):
                policy = VerificationPolicy(sources={"ledger": {
                    "data": {"format": expected}, "trusted": True, "independence_key": "ledger",
                    "observed_at": time.time(), "max_age_seconds": 3600,
                }}, claims={"format": {"source": "ledger", "path": "/format"}})
                self.register(policy, memory_bindings={"format": "export_format"})
                result = self.execute({"format": "json"})
                self.assertEqual(result["executed"], allowed)
        self.assertEqual(self.calls, [("invoice", {"format": "JSON"})])

    def test_extra_argument_is_not_silently_approved_by_claim_projection(self):
        self.register()
        result = self.execute({"total": 20, "approved": False, "explanation": "Buy now"})
        self.assertFalse(result["executed"])
        self.assertTrue(result["check"]["verification_check"]["valid"])
        self.assertIn("unexpected_argument", {row["code"] for row in result["check"]["issues"]})
        self.assertEqual(self.calls, [])

    def test_verification_never_overrides_permissions_or_stale_context(self):
        self.guard.remember("permitted", False)
        self.register(when={"permitted": True})
        blocked = self.execute({"total": 20, "approved": False})
        self.assertFalse(blocked["executed"])
        self.assertTrue(blocked["check"]["verification_check"]["valid"])
        revision = self.guard.turn().revision
        self.guard.remember("permitted", True)
        stale = self.execute({"total": 20, "approved": False}, revision=revision)
        self.assertFalse(stale["executed"])
        self.assertEqual(stale["check"]["decision"], "refresh")
        self.assertEqual(self.calls, [])

    def test_verification_failure_remains_blocked_without_explicit_repair(self):
        self.register()
        result = self.guard.turn().execute_with_recovery("invoice", {"total": 2000, "approved": True},
                                                        self.executor)
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(self.calls, [])

    def test_policy_is_canonical_and_optional_overlap_or_malformed_contract_fails(self):
        self.register()
        stored = self.guard.store.policies(self.guard.session_id)[0]
        self.assertEqual(validate_policy(stored), stored)
        restored = VerificationPolicy.from_dict(stored["verification"])
        self.assertTrue(restored.check({"total": 20, "approved": False}).valid)
        for options in ({"verification": None}, {"optional_args": ["total"]}):
            with self.subTest(options=options), self.assertRaises(PolicyError):
                validate_policy({**stored, **options})
        with self.assertRaises(TypeError):
            self.guard.allow_tool("other", verification=stored["verification"])
        with self.assertRaises(PolicyError):
            self.register(optional_args=("approved",))

    def test_verification_policy_survives_restart_and_original_mapping_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.sqlite3"
            with ContextGuard(path) as first:
                first.allow_tool("invoice", verification=contract())
                session_id = first.session_id
                snapshot = first.store.policies(session_id)[0]
                snapshot["verification"]["sources"]["ledger"]["data"]["units"] = 200
            with ContextGuard(path, session_id=session_id) as reopened:
                proposal = {"tool": "invoice", "arguments": {"total": 20, "approved": False},
                            "context_revision": reopened.turn().revision}
                result = reopened.relay.execute(session_id, proposal, self.executor)
                self.assertTrue(result["executed"])
                self.assertTrue(result["check"]["verification_check"]["valid"])
        self.assertEqual(len(self.calls), 1)

    def test_old_policies_keep_existing_behavior_and_report_shape(self):
        self.guard.allow_tool("invoice", optional_args=("total",))
        result = self.execute({"total": 2000})
        self.assertTrue(result["executed"])
        self.assertNotIn("verification_check", result["check"])
        self.assertNotIn("verification", self.guard.store.policies(self.guard.session_id)[0])


class VerificationRegistryTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.registry = ToolRegistry(self.guard)
        self.calls = []

    def invoice(self, total, approved, note=""):
        self.calls.append((total, approved, note))
        return {"saved": True}

    def test_registered_claims_count_as_checks_and_coverage_is_redacted(self):
        self.registry.register(self.invoice, verification=contract(), unchecked=("note",))
        coverage = self.registry.coverage()["tools"]["invoice"]
        self.assertEqual(coverage["checked"], {"total": {"verification": True}, "approved": {"verification": True}})
        self.assertEqual(coverage["unchecked"], ["note"])
        self.assertEqual(coverage["verification"]["claim_arguments"], ["approved", "total"])
        self.assertFalse(coverage["verification"]["semantic_meaning_checked"])
        self.assertNotIn("private-ledger-data", json.dumps(coverage))
        self.assertEqual(self.registry.bind(self.guard.turn()).dispatch("invoice", {"total": 20, "approved": False}),
                         {"saved": True})
        self.assertEqual(self.calls, [(20, False, "")])

    def test_missing_or_wrong_registered_claim_never_calls_tool(self):
        self.registry.register(self.invoice, verification=contract(), unchecked=("note",))
        bound = self.registry.bind(self.guard.turn())
        for arguments in ({"total": 2000, "approved": False}, {"total": 20}, {}):
            with self.subTest(arguments=arguments), self.assertRaises(ActionRejected):
                bound.dispatch("invoice", arguments)
        self.assertEqual(self.calls, [])

    def test_unchecked_overlap_or_claim_for_unknown_parameter_fails_without_registration(self):
        for function, options in (
            (self.invoice, {"verification": contract(), "unchecked": ("total", "note")}),
            (lambda amount: None, {"verification": contract(), "unchecked": ("amount",)}),
            (self.invoice, {"verification": contract().to_dict(), "unchecked": ("note",)}),
        ):
            with self.subTest(function=function), self.assertRaises((ValueError, TypeError)):
                self.registry.register(function, **options)
        self.assertEqual(self.registry.coverage()["registered_tools"], [])
        self.assertEqual(self.guard.store.policies(self.guard.session_id), [])

    def test_replacing_verification_makes_an_old_bound_turn_stale(self):
        self.registry.register(self.invoice, verification=contract(), unchecked=("note",))
        old = self.registry.bind(self.guard.turn())
        self.registry.register(self.invoice, verification=contract(), unchecked=("note",), replace=True)
        with self.assertRaises(ActionRejected):
            old.dispatch("invoice", {"total": 20, "approved": False})
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
