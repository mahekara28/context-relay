"""Repair edits stay scoped and known-value repair never invents new data."""

import copy
import json
import unittest
from unittest.mock import patch

from context_relay import (
    AutomaticMemory, ContentPolicy, ContextGuard, RowRule, ToolExecutionError,
)


class RepairSafetyTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.calls = []

    def execute(self, tool, arguments):
        self.calls.append(copy.deepcopy(arguments))
        return {"written": True}

    def recover(self, arguments, repair, *, turn=None):
        return (turn or self.guard.turn()).execute_with_recovery(
            "write", arguments, self.execute, repair=repair)

    def register(self, content=None, **options):
        self.guard.allow_tool("write", content=content, **options)

    def assert_blocked(self, report):
        self.assertFalse(report["executed"])
        self.assertEqual(self.calls, [])
        self.assertNotIn("receipt", report)

    def test_callback_cannot_add_remove_or_edit_optional_data(self):
        self.guard.remember("format", "JSON")
        self.register(bindings={"format": "format"}, optional_args=("payload", "note"))
        original = {"format": "CSV", "payload": {"value": 7}, "note": "private-original"}
        for changed in ({"format": "JSON", "payload": {"value": 8}, "note": "private-original"},
                        {"format": "JSON", "payload": {"value": 7}},
                        {"format": "JSON", "payload": {"value": 7}, "note": "new-private"}):
            result = self.recover(original, lambda request: changed)
            self.assert_blocked(result)
            self.assertFalse(result["repair_scope"]["valid"])
            self.assertEqual(result["repair_callback_calls"], 1)
            self.assertEqual(result["repair_mode"], "callback")
            issue = result["final_check"]["issues"][-1]
            self.assertEqual(issue["code"], "repair_scope_violation")
            self.assertIsNone(issue["expected"])
            self.assertIsNone(issue["actual"])
            self.assertNotIn("private", json.dumps(result["repair_scope"]))
            recorded = next(row for row in self.guard.store.checks(self.guard.session_id)
                            if row["id"] == result["final_check"]["id"])
            self.assertEqual(recorded, result["final_check"])
        result = self.recover({"format": "CSV"}, lambda request: {"format": "JSON", "note": "new"})
        self.assert_blocked(result)

    def test_equivalent_memory_spelling_is_not_an_unrelated_change(self):
        self.guard.remember("format", "JSON")
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Respond in Hindi."}])
        self.register(bindings={"format": "format"}, memory_bindings={"language": "response_language"})
        result = self.recover({"format": "CSV", "language": "Hindi"},
                              lambda request: {"format": "JSON", "language": "hindi"})
        self.assertTrue(result["executed"])
        self.assertEqual(self.calls, [{"format": "JSON", "language": "Hindi"}])
        self.assertEqual(result["repair_scope"]["allowed_paths"], ["/format"])
        self.assertEqual(result["repair_scope"]["changed_paths"], ["/format"])

    def test_nested_exact_scope_does_not_authorize_sibling_edits(self):
        content = ContentPolicy({"payload": {"name": "Folders"}}, exact=("/payload/name",))
        self.register(content)
        original = {"payload": {"name": "translated", "units": 4}}
        result = self.recover(original, lambda request: {"payload": {"name": "Folders", "units": 9}})
        self.assert_blocked(result)
        self.assertEqual(result["repair_scope"]["allowed_paths"], ["/payload/name"])
        good = self.recover(original, lambda request: {"payload": {"name": "Folders", "units": 4}})
        self.assertTrue(good["executed"])
        self.assertEqual(self.calls, [{"payload": {"name": "Folders", "units": 4}}])

    def test_only_failed_content_rules_are_repair_scopes(self):
        self.register(ContentPolicy({"payload": {"name": "Folders", "units": 4}},
                                    exact=("/payload/name", "/payload/units")))
        result = self.recover({"payload": {"name": "wrong", "units": 4}},
                              lambda request: {"payload": {"name": "Folders", "units": 5}})
        self.assert_blocked(result)
        self.assertEqual(result["repair_scope"]["allowed_paths"], ["/payload/name"])

    def test_missing_nested_dictionary_can_add_only_declared_path(self):
        self.register(ContentPolicy({"payload": {"name": "Folders", "secret": "source-only"}},
                                    exact=("/payload/name",)))
        result = self.recover({}, lambda request: {"payload": {"name": "Folders", "secret": "new"}})
        self.assert_blocked(result)
        good = self.recover({}, "known")
        self.assertTrue(good["executed"])
        self.assertEqual(self.calls, [{"payload": {"name": "Folders"}}])

    def test_nested_array_exact_scope_keeps_other_items_and_length(self):
        self.register(ContentPolicy({"payload": [{"name": "Folders"}, {"name": "Tape"}]},
                                    exact=("/payload/0/name",)))
        original = {"payload": [{"name": "wrong"}, {"name": "keep"}]}
        changed = {"payload": [{"name": "Folders"}, {"name": "changed"}]}
        self.assert_blocked(self.recover(original, lambda request: changed))
        self.assert_blocked(self.recover(original, lambda request: {"payload": [{"name": "Folders"}]}))
        good = self.recover(original, "known")
        self.assertTrue(good["executed"])
        self.assertEqual(self.calls[0]["payload"][1], {"name": "keep"})

    def rows_policy(self, *, keyed=True):
        return ContentPolicy({"rows": [{"id": "a", "name": "Folders", "units": 4},
                                       {"id": "b", "name": "Tape", "units": 2}]},
                             rows=(RowRule("/rows", ("name", "units"), key="id" if keyed else None,
                                           ordered=False),))

    def test_row_repair_fixes_selected_fields_without_changing_unprotected_fields(self):
        self.register(self.rows_policy())
        original = {"rows": [{"id": "a", "name": "wrong", "units": 4, "note": "retain-A"},
                             {"id": "b", "name": "Tape", "units": 2, "note": "retain-B"}]}
        corrected = copy.deepcopy(original)
        corrected["rows"][0]["name"] = "Folders"
        wrong = copy.deepcopy(corrected)
        wrong["rows"][0]["note"] = "changed"
        self.assert_blocked(self.recover(original, lambda request: wrong))
        good = self.recover(original, lambda request: corrected)
        self.assertTrue(good["executed"])
        self.assertEqual(self.calls[0], corrected)
        self.assertNotIn("/rows/*/id", good["repair_scope"]["allowed_paths"])

    def test_row_repair_cannot_delete_add_or_reorder_rows_or_change_identity(self):
        self.register(self.rows_policy())
        original = {"rows": [{"id": "a", "name": "wrong", "units": 4},
                             {"id": "b", "name": "Tape", "units": 2}]}
        corrected = copy.deepcopy(original["rows"])
        corrected[0]["name"] = "Folders"
        for rows in (corrected[:1], corrected + [corrected[0]], list(reversed(corrected))):
            result = self.recover(original, lambda request: {"rows": rows})
            self.assert_blocked(result)
            self.assertIn("row_reconstruction_unsupported", result["repair_scope"]["reasons"])
        bad_id = copy.deepcopy(original)
        bad_id["rows"][0]["id"] = "unknown"
        result = self.recover(bad_id, lambda request: {"rows": corrected})
        self.assert_blocked(result)
        self.assertIn("row_reconstruction_unsupported", result["repair_scope"]["reasons"])

    def test_keyless_row_repair_keeps_row_positions_and_extra_fields(self):
        self.register(self.rows_policy(keyed=False))
        original = {"rows": [{"name": "wrong", "units": 4, "note": "first"},
                             {"name": "Tape", "units": 2, "note": "second"}]}
        corrected = copy.deepcopy(original)
        corrected["rows"][0]["name"] = "Folders"
        shuffled = {"rows": list(reversed(corrected["rows"]))}
        self.assert_blocked(self.recover(original, lambda request: shuffled))
        self.assertTrue(self.recover(original, lambda request: corrected)["executed"])

    def test_callback_within_scope_still_must_pass_normal_checks(self):
        self.guard.remember("format", "JSON")
        self.register(bindings={"format": "format"})
        result = self.recover({"format": "CSV"}, lambda request: {"format": "YAML"})
        self.assert_blocked(result)
        self.assertTrue(result["repair_scope"]["valid"])
        self.assertEqual(result["final_check"]["issues"][0]["code"], "binding_mismatch")

    def test_known_repair_uses_trusted_and_memory_values_without_callback(self):
        self.guard.remember("destination", "exports/report.json")
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Export as JSON. Respond in Hindi."}])
        self.register(bindings={"path": "destination"},
                      memory_bindings={"format": "export_format", "language": "response_language"},
                      optional_args=("rows",))
        arguments = {"path": "wrong", "format": "CSV", "rows": [{"item": "Tape", "units": 2}]}
        saved = copy.deepcopy(arguments)
        result = self.recover(arguments, "known")
        self.assertTrue(result["executed"])
        self.assertEqual(result["repair_mode"], "known")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(result["repair_callback_calls"], 0)
        self.assertEqual(self.calls, [{"path": "exports/report.json", "format": "JSON", "language": "Hindi",
                                      "rows": [{"item": "Tape", "units": 2}]}])
        self.assertEqual(arguments, saved)

    def test_known_exact_restore_preserves_unrelated_data_and_supports_escaped_keys(self):
        self.register(ContentPolicy({"a/b": {"~name": "Folders", "hidden": 1}},
                                    exact=("/a~1b/~0name",)), optional_args=("summary",))
        result = self.recover({"a/b": {"~name": "wrong", "hidden": 7}, "summary": "unchanged"}, "known")
        self.assertTrue(result["executed"])
        self.assertEqual(self.calls, [{"a/b": {"~name": "Folders", "hidden": 7}, "summary": "unchanged"}])

    def test_known_copies_boolean_null_integer_and_exact_float_types(self):
        source = {"content": [False, None, 1, 1.0, 0.125]}
        self.register(ContentPolicy(source, exact=("/content",)))
        result = self.recover({"content": []}, "known")
        self.assertTrue(result["executed"])
        self.assertEqual(self.calls, [source])
        self.assertIs(type(self.calls[0]["content"][2]), int)
        self.assertIs(type(self.calls[0]["content"][3]), float)

    def test_known_refuses_decimal_rounding_underflow_and_overflow(self):
        for raw in ('0.10000000000000001', '1e-1000', '1e1000'):
            self.register(ContentPolicy.from_json('{"amount":' + raw + '}', exact=("/amount",)))
            result = self.recover({"amount": 0}, "known")
            self.assert_blocked(result)
            self.assertEqual(result["status"], "repair_unavailable")
            self.assertEqual(result["repair_unavailable_reason"], "source_number_not_representable")
            self.assertEqual(result["repair_callback_calls"], 0)

    def test_known_refuses_row_reconstruction_without_guessing(self):
        self.register(self.rows_policy())
        result = self.recover({"rows": []}, "known")
        self.assert_blocked(result)
        self.assertEqual(result["status"], "repair_unavailable")
        self.assertEqual(result["repair_unavailable_reason"], "row_reconstruction_unsupported")

    def test_known_refuses_reconstructing_scalar_parent_or_missing_array(self):
        for source, path, original in (({"payload": {"name": "Folders"}}, "/payload/name", {"payload": 1}),
                                        ({"rows": [{"name": "Folders"}]}, "/rows/0/name", {})):
            self.register(ContentPolicy(source, exact=(path,)))
            result = self.recover(original, "known")
            self.assert_blocked(result)
            self.assertEqual(result["repair_unavailable_reason"], "parent_reconstruction_unsupported")

    def test_known_refuses_conflicting_bound_and_content_sources(self):
        self.guard.remember("format", "JSON")
        self.register(ContentPolicy({"format": "CSV"}, exact=("/format",)), bindings={"format": "format"})
        for arguments in ({"format": "YAML"}, {"format": "JSON"}):
            result = self.recover(arguments, "known")
            self.assert_blocked(result)
            self.assertEqual(result["repair_unavailable_reason"], "conflicting_sources")

    def test_known_refuses_conflict_after_memory_normalization(self):
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Export as JSON."}])
        self.register(ContentPolicy({"format": "CSV"}, exact=("/format",)),
                      memory_bindings={"format": "export_format"})
        result = self.recover({"format": "YAML"}, "known")
        self.assert_blocked(result)
        self.assertEqual(result["repair_unavailable_reason"], "conflicting_sources")

    def test_known_requires_existing_undisputed_memory_and_permission(self):
        self.guard.remember("approved", False)
        self.register(when={"approved": True}, memory_bindings={"format": "export_format"})
        blocked = self.recover({"format": "CSV"}, "known")
        self.assert_blocked(blocked)
        self.assertEqual(blocked["repair_attempts"], 0)
        self.guard.remember("approved", True)
        missing = self.recover({"format": "CSV"}, "known")
        self.assert_blocked(missing)
        self.assertEqual(missing["status"], "clarification_required")
        self.assertEqual(missing["repair_attempts"], 0)
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        history = [{"role": "user", "content": "Export as CSV."},
                   {"role": "assistant", "content": "OK."},
                   {"role": "user", "content": "Use CSV for the log format."},
                   {"role": "assistant", "content": "OK."},
                   {"role": "user", "content": "Actually, use JSON instead of CSV."}]
        memory.process(history)
        self.assertTrue(memory.pending())
        disputed = self.recover({"format": "CSV"}, "known")
        self.assert_blocked(disputed)
        self.assertEqual(disputed["status"], "clarification_required")
        self.assertEqual(disputed["repair_attempts"], 0)

    def test_known_does_not_renew_stale_turn_or_retry_started_tool(self):
        self.guard.remember("format", "JSON")
        self.register(bindings={"format": "format"})
        turn = self.guard.turn()
        self.guard.remember("note", "new state")
        result = self.recover({"format": "CSV"}, "known", turn=turn)
        self.assert_blocked(result)
        self.assertEqual(result["repair_attempts"], 0)
        def fail(tool, arguments):
            self.execute(tool, arguments)
            raise RuntimeError("failure after a write")
        with self.assertRaises(ToolExecutionError):
            self.guard.turn().execute_with_recovery("write", {"format": "CSV"}, fail, repair="known")
        self.assertEqual(self.calls, [{"format": "JSON"}])

    def test_known_final_check_catches_concurrent_change(self):
        from context_relay.action_repair import RepairScope
        self.guard.remember("format", "JSON")
        self.register(bindings={"format": "format"})
        original = RepairScope.known
        def change(scope, *args):
            revised = original(scope, *args)
            self.guard.remember("format", "YAML")
            return revised
        with patch.object(RepairScope, "known", change):
            result = self.recover({"format": "CSV"}, "known")
        self.assert_blocked(result)
        self.assertIn("stale_revision", {row["code"] for row in result["final_check"]["issues"]})

    def test_known_success_needs_no_repair_when_arguments_already_correct(self):
        self.guard.remember("format", "JSON")
        self.register(bindings={"format": "format"})
        result = self.recover({"format": "JSON"}, "known")
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(result["repair_callback_calls"], 0)

    def test_repair_mode_typo_rejects_before_executor(self):
        self.register(optional_args=("format",))
        with self.assertRaises(ValueError):
            self.recover({"format": "JSON"}, "knwon")
        self.assertEqual(self.calls, [])

    def test_memory_alias_change_and_content_repair_use_canonical_dispatched_values(self):
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Export as JSON."}])
        source = {"format": "JSON", "rows": [{"item": "Folders", "units": 4}]}
        self.register(ContentPolicy(source, exact=("/format", "/rows")),
                      memory_bindings={"format": "export_format"})
        original = {"format": " json ", "rows": [{"item": "changed", "units": 4}]}
        result = self.recover(original, lambda request: {"format": "Json", "rows": source["rows"]})
        self.assertTrue(result["executed"])
        self.assertEqual(self.calls, [source])
        self.assertEqual(result["receipt"]["arguments"], source)
        self.assertTrue(result["final_check"]["content_check"]["valid"])
        self.assertEqual(result["repair_scope"]["changed_paths"], ["/rows"])
        self.assertEqual(original["format"], " json ")

    def test_equivalence_does_not_permit_a_different_memory_value(self):
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Export as JSON."}])
        self.register(ContentPolicy({"rows": []}, exact=("/rows",)),
                      memory_bindings={"format": "export_format"})
        result = self.recover({"format": "json", "rows": [1]},
                              lambda request: {"format": "YAML", "rows": []})
        self.assert_blocked(result)
        self.assertFalse(result["repair_scope"]["valid"])
        self.assertIn("/format", result["repair_scope"]["changed_paths"])
        self.assertIn("memory_binding_mismatch", {
            row["code"] for row in result["final_check"]["issues"]})

    def test_fact_and_custom_memory_values_remain_case_sensitive(self):
        self.guard.remember("format", "JSON")
        self.register(ContentPolicy({"rows": []}, exact=("/rows",)), bindings={"format": "format"})
        fact = self.recover({"format": "JSON", "rows": [1]},
                            lambda request: {"format": "json", "rows": []})
        self.assert_blocked(fact)
        self.assertFalse(fact["repair_scope"]["valid"])
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Set document to JSON."}])
        entry = next(row for row in memory.snapshot()["entries"] if row["key"] == "document")
        self.assertEqual(entry["value"], "JSON")
        self.register(ContentPolicy({"rows": []}, exact=("/rows",)),
                      memory_bindings={"format": "document"})
        custom = self.recover({"format": "JSON", "rows": [1]},
                              lambda request: {"format": "json", "rows": []})
        self.assert_blocked(custom)
        self.assertFalse(custom["repair_scope"]["valid"])

    def test_memory_aliases_cannot_bypass_canonical_content_contract(self):
        memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        memory.process([{"role": "user", "content": "Export as JSON."}])
        self.register(ContentPolicy({"format": "json", "rows": []}, exact=("/format", "/rows")),
                      memory_bindings={"format": "export_format"})
        result = self.recover({"format": "json", "rows": [1]},
                              lambda request: {"format": "JSON", "rows": []})
        self.assert_blocked(result)
        self.assertTrue(result["repair_scope"]["valid"])
        self.assertFalse(result["final_check"]["content_check"]["valid"])
        self.assertEqual({row["code"] for row in result["final_check"]["issues"]}, {"content_mismatch"})

    def test_callback_cannot_smuggle_private_exception_text_into_known_diagnostics(self):
        from context_relay.action_repair import RepairUnavailable
        self.guard.remember("format", "JSON")
        self.register(bindings={"format": "format"})
        def broken(request):
            raise RepairUnavailable("private callback details")
        result = self.recover({"format": "CSV"}, broken)
        self.assert_blocked(result)
        self.assertEqual(result["status"], "repair_failed")
        self.assertEqual(result["error_type"], "RepairUnavailable")
        self.assertNotIn("private", json.dumps(result))

    def test_content_limit_does_not_authorize_rewriting_unrelated_data(self):
        self.register(ContentPolicy({"payload": "Folders"}, exact=("/payload",), max_chars=64),
                      optional_args=("note",))
        result = self.recover({"payload": "wrong", "note": "x" * 100}, "known")
        self.assert_blocked(result)
        self.assertEqual(result["repair_unavailable_reason"], "content_repair_unsupported")
        callback = self.recover({"payload": "wrong", "note": "x" * 100},
                                lambda request: {"payload": "Folders"})
        self.assert_blocked(callback)
        self.assertEqual(callback["repair_scope"]["allowed_paths"], [])

    def test_scope_diagnostic_paths_are_bounded_and_value_free(self):
        self.guard.remember("format", "JSON")
        self.register(bindings={"format": "format"}, optional_args=tuple(f"data{i}" for i in range(30)))
        original = {"format": "CSV", **{f"data{i}": "private-original" for i in range(30)}}
        revised = {"format": "JSON", **{f"data{i}": "private-replacement" for i in range(30)}}
        result = self.recover(original, lambda request: revised)
        self.assert_blocked(result)
        self.assertEqual(len(result["repair_scope"]["changed_paths"]), 20)
        self.assertTrue(result["repair_scope"]["change_limit_reached"])
        self.assertNotIn("private", json.dumps(result["repair_scope"]))


if __name__ == "__main__":
    unittest.main()
