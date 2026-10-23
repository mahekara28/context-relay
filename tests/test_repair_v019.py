"""Known derived-claim and identity-preserving row repairs; no model calls."""

import copy
import time
import unittest
from unittest.mock import patch

from context_relay import ContentPolicy, ContextGuard, RowRule, VerificationPolicy


ROWS = [{"id": "A", "name": "Original A", "units": 2},
        {"id": "B", "name": "Original B", "units": 4}]


def verified(**options):
    source = {"data": {"units": 2, "price": 10, "limit": 15}, "trusted": True,
              "independence_key": "ledger", "observed_at": time.time(),
              "max_age_seconds": 3600, **options}
    total = {"op": "multiply", "args": [{"source": "ledger", "path": "/units"},
                                           {"source": "ledger", "path": "/price"}]}
    return VerificationPolicy(sources={"ledger": source}, claims={"total": total,
        "approved": {"op": "lte", "args": [total, {"source": "ledger", "path": "/limit"}]}})


class RepairClaimTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.calls = []

    def executor(self, tool, arguments):
        self.calls.append(copy.deepcopy(arguments))
        return {"saved": True}

    def recover(self, arguments, repair="known", turn=None):
        return (turn or self.guard.turn()).execute_with_recovery("write", arguments, self.executor, repair=repair)

    def register(self, policy=None, **options):
        self.guard.allow_tool("write", verification=verified() if policy is None else policy, **options)

    def test_wrong_and_missing_claims_repair_to_computed_values_once(self):
        self.register()
        for arguments in ({"total": 2000, "approved": True}, {"approved": False}, {}):
            with self.subTest(arguments=arguments):
                original = copy.deepcopy(arguments)
                result = self.recover(arguments)
                self.assertTrue(result["executed"])
                self.assertEqual(result["status"], "repaired")
                self.assertEqual(result["repair_attempts"], 1)
                self.assertEqual(result["repair_callback_calls"], 0)
                self.assertEqual(self.calls[-1], {"total": 20, "approved": False})
                self.assertEqual(arguments, original)
        self.assertEqual(len(self.calls), 3)

    def test_repair_scope_contains_only_failed_claims(self):
        self.register()
        result = self.recover({"total": 2000, "approved": False})
        self.assertEqual(result["repair_scope"]["allowed_paths"], ["/total"])
        self.assertEqual(result["repair_scope"]["changed_paths"], ["/total"])
        self.assertTrue(result["executed"])

    def test_callback_cannot_change_a_good_claim_even_to_a_valid_numeric_equivalent(self):
        self.register()
        result = self.recover({"total": 20, "approved": True},
                              repair=lambda request: {"total": 20.0, "approved": False})
        self.assertFalse(result["executed"])
        self.assertTrue(result["final_check"]["verification_check"]["valid"])
        self.assertIn("repair_scope_violation", {row["code"] for row in result["final_check"]["issues"]})
        self.assertEqual(self.calls, [])

    def test_unavailable_source_never_attempts_known_or_callback_repair(self):
        for options in ({"trusted": False}, {"observed_at": time.time() - 7200},
                        {"observed_at": time.time() + 3600}):
            self.register(verified(**options))
            for repair in ("known", lambda request: self.fail("Source availability cannot be guessed.")):
                with self.subTest(options=options, repair=repair):
                    result = self.recover({"total": 2000, "approved": True}, repair)
                    self.assertFalse(result["executed"])
                    self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(self.calls, [])

    def test_unavailable_evidence_is_not_misreported_as_only_memory_clarification(self):
        self.register(verified(trusted=False), memory_bindings={"format": "export_format"})
        result = self.recover({"total": 2000, "approved": True, "format": "JSON"})
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(self.calls, [])

    def test_known_claim_refuses_precision_loss(self):
        policy = VerificationPolicy(sources={"unused": {"data": {}, "trusted": True,
            "independence_key": "ledger", "observed_at": time.time(), "max_age_seconds": 3600}},
            claims={"total": {"op": "divide", "args": [{"literal": 123456789012345678901}, {"literal": 10000000}]}})
        self.register(policy)
        result = self.recover({"total": 0})
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "repair_unavailable")
        self.assertEqual(result["repair_unavailable_reason"], "source_number_not_representable")
        self.assertEqual(self.calls, [])

    def test_expiry_during_callback_blocks_repaired_action(self):
        now = time.time()
        self.register(verified(observed_at=now, max_age_seconds=1))
        def repair(request):
            clock.return_value = now + 2
            return {"total": 20, "approved": False}
        with patch("context_relay.verification.time.time", return_value=now) as clock:
            result = self.recover({"total": 2000, "approved": True}, repair)
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_attempts"], 1)
        self.assertFalse(result["final_check"]["verification_check"]["ready"])
        self.assertEqual(self.calls, [])

    def test_state_or_permission_changes_during_callback_never_refresh_the_turn(self):
        self.guard.remember("permission", True)
        self.register(when={"permission": True})
        turn = self.guard.turn()
        def repair(request):
            self.guard.remember("permission", False)
            return {"total": 20, "approved": False}
        result = self.recover({"total": 2000, "approved": True}, repair, turn)
        self.assertFalse(result["executed"])
        self.assertIn("stale_revision", {row["code"] for row in result["final_check"]["issues"]})
        self.assertIn("requirement_failed", {row["code"] for row in result["final_check"]["issues"]})
        self.assertEqual(self.calls, [])

    def test_conflicting_verified_content_and_fact_sources_do_not_choose_one(self):
        self.guard.remember("total", 99)
        for options in ({"bindings": {"total": "total"}},
                        {"content": ContentPolicy({"total": 99}, exact=("/total",))}):
            self.register(**options)
            result = self.recover({"total": 2000, "approved": False})
            self.assertFalse(result["executed"])
            self.assertEqual(result["status"], "repair_unavailable")
            self.assertEqual(result["repair_unavailable_reason"], "conflicting_sources")
        self.assertEqual(self.calls, [])


class RepairKeyedRowsTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.calls = []

    def register(self, *, ordered=False, allow_extra=True, reference=None, key="id", **options):
        content = ContentPolicy({"rows": copy.deepcopy(ROWS if reference is None else reference)},
                                rows=(RowRule("/rows", ("name", "units"), key=key,
                                              ordered=ordered, allow_extra_fields=allow_extra),))
        self.guard.allow_tool("save", content=content, **options)

    def recover(self, rows):
        return self.guard.turn().execute_with_recovery("save", {"rows": rows},
            lambda tool, arguments: self.calls.append(copy.deepcopy(arguments)) or {"saved": True}, repair="known")

    def test_repair_matches_keys_and_preserves_order_unchecked_fields_and_original(self):
        self.register()
        proposed = [{"id": "B", "name": "Wrong B", "units": 999, "note": {"keep": "B note"}},
                    {"id": "A", "name": "Wrong A", "units": 888, "note": {"keep": "A note"}}]
        original = copy.deepcopy(proposed)
        result = self.recover(proposed)
        self.assertTrue(result["executed"])
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(self.calls, [{"rows": [
            {**ROWS[1], "note": {"keep": "B note"}}, {**ROWS[0], "note": {"keep": "A note"}}]}])
        self.assertEqual(proposed, original)
        self.assertTrue(result["repair_scope"]["valid"])
        self.assertEqual(result["repair_callback_calls"], 0)

    def test_missing_selected_fields_can_be_restored_when_keys_are_complete(self):
        self.register()
        result = self.recover([{"id": "A"}, {"id": "B"}])
        self.assertTrue(result["executed"])
        self.assertEqual(self.calls, [{"rows": ROWS}])

    def test_missing_extra_duplicate_changed_and_mistyped_identities_are_not_reconstructed(self):
        self.register()
        variants = [[], ROWS[:1], ROWS + [{"id": "C", "name": "C", "units": 1}],
                    [ROWS[0], ROWS[0]], [ROWS[0], {**ROWS[1], "id": "C"}],
                    [ROWS[0], {"name": "B", "units": 4}],
                    [ROWS[0], {**ROWS[1], "id": True}], [ROWS[0], {**ROWS[1], "id": 2}]]
        for rows in variants:
            with self.subTest(rows=rows):
                result = self.recover(copy.deepcopy(rows))
                self.assertFalse(result["executed"])
                self.assertEqual(result["status"], "repair_unavailable")
                self.assertEqual(result["repair_unavailable_reason"], "row_reconstruction_unsupported")
        self.assertEqual(self.calls, [])

    def test_identity_integer_and_same_string_remain_distinct(self):
        reference = [{"id": 1, "name": "Integer", "units": 2},
                     {"id": "1", "name": "String", "units": 4}]
        self.register(reference=reference)
        result = self.recover([{"id": "1", "name": "Wrong", "units": 0},
                               {"id": 1, "name": "Wrong", "units": 0}])
        self.assertTrue(result["executed"])
        self.assertEqual(self.calls, [{"rows": [reference[1], reference[0]]}])

    def test_ordered_rule_does_not_reorder_candidate_identities(self):
        self.register(ordered=True)
        result = self.recover(list(reversed(copy.deepcopy(ROWS))))
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_unavailable_reason"], "row_reconstruction_unsupported")
        self.assertEqual(self.calls, [])

    def test_forbidden_unknown_fields_are_not_silently_removed(self):
        self.register(allow_extra=False)
        candidate = [{**ROWS[0], "name": "Wrong", "hidden": "Keep"}, ROWS[1]]
        saved = copy.deepcopy(candidate)
        result = self.recover(candidate)
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_unavailable_reason"], "row_fields_repair_unsupported")
        self.assertEqual(candidate, saved)
        self.assertEqual(self.calls, [])

    def test_keyless_rows_remain_unavailable_for_known_repair(self):
        self.register(key=None)
        result = self.recover([{**ROWS[0], "name": "Wrong"}, ROWS[1]])
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_unavailable_reason"], "row_reconstruction_unsupported")
        self.assertEqual(self.calls, [])

    def test_row_source_precision_is_checked_before_dispatch(self):
        content = ContentPolicy.from_json('{"rows":[{"id":"A","name":"A","units":0.10000000000000001}]}',
            rows=(RowRule("/rows", ("name", "units"), key="id"),))
        self.guard.allow_tool("save", content=content)
        result = self.recover([{"id": "A", "name": "Wrong", "units": 0}])
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_unavailable_reason"], "source_number_not_representable")
        self.assertEqual(self.calls, [])

    def test_whole_argument_claim_cannot_hide_row_identity_reconstruction(self):
        policy = VerificationPolicy(sources={"ledger": {"data": {"rows": ROWS}, "trusted": True,
            "independence_key": "ledger", "observed_at": time.time(), "max_age_seconds": 3600}},
            claims={"rows": {"source": "ledger", "path": "/rows"}})
        self.register(verification=policy)
        result = self.recover([])
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_unavailable_reason"], "row_reconstruction_unsupported")
        self.assertEqual(self.calls, [])

    def test_whole_argument_binding_cannot_replace_unchecked_row_fields(self):
        self.guard.remember("rows", [{**ROWS[0], "note": "Source note"}, ROWS[1]])
        self.register(bindings={"rows": "rows"})
        result = self.recover([{**ROWS[0], "name": "Wrong", "note": "Keep my note"}, ROWS[1]])
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_unavailable_reason"], "conflicting_sources")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
