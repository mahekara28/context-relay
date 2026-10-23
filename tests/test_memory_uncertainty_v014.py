"""Unparsed direct corrections stop stale recall without inventing a value."""

import tempfile
import unittest
from pathlib import Path

from context_relay import AutomaticMemory, MemoryInterpreter, MemoryPolicy, RelayStore
from context_relay.memory import MemoryBudgetExceeded, MemoryConflictError


def user(text):
    return {"role": "user", "content": text}


class UncertainCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.addCleanup(self.store.close)
        self.memory = AutomaticMemory(self.store, self.store.create_session("Uncertainty")["id"])
        self.history = [user("Export as CSV.")]
        self.memory.process(self.history)

    def append(self, text):
        self.history.append(user(text))
        return self.memory.process(self.history)

    def test_unknown_named_request_marks_current_value_disputed_without_a_replacement(self):
        text = "Please make future exports much easier to read."
        report = self.append(text)
        pending = self.memory.pending()[0]
        self.assertEqual(pending["reason"], "unparsed_correction")
        self.assertIsNone(pending["value"])
        self.assertEqual(pending["old"], "CSV")
        self.assertEqual(pending["targets"], ["export_format"])
        self.assertEqual(pending["source"]["method"], "uncertain")
        self.assertEqual(pending["source"]["quote"], text)
        self.assertEqual(report["pending_ids"], [pending["id"]])
        self.assertEqual(self.memory.entries()[0]["value"], "CSV")
        self.assertTrue(self.memory.snapshot()["entries"][0]["disputed"])
        self.assertEqual(self.memory.context_snapshot()["unresolved"][0]["source"]["quote"], text)
        self.assertEqual(self.store.facts(self.memory.session_id), {})
        with self.assertRaisesRegex(MemoryConflictError, "clarification"):
            self.memory.confirm(pending["id"])
        with self.assertRaisesRegex(MemoryConflictError, "clarification"):
            self.memory.confirm(pending["id"], key="export_format")

    def test_forgotten_or_never_recorded_settings_cannot_be_invented(self):
        self.memory.forget("export_format")
        self.append("Please make future exports much easier to read.")
        self.assertEqual(self.memory.entries(), [])
        self.assertEqual(self.memory.pending(), [])

    def test_later_explicit_clarification_or_reaffirmation_resolves_uncertainty(self):
        for value in ("JSON", "CSV"):
            with self.subTest(value=value):
                store = RelayStore()
                self.addCleanup(store.close)
                memory = AutomaticMemory(store, store.create_session("Clarification")["id"])
                history = [user("Export as CSV."), user("Please make exports easier to read.")]
                memory.process(history)
                self.assertTrue(memory.pending())
                memory.process(history + [user(f"Export as {value}.")])
                self.assertEqual(memory.pending(), [])
                self.assertEqual(memory.entries()[0]["value"], value)
                self.assertFalse(memory.snapshot()["entries"][0]["disputed"])

    def test_dismissal_undo_restart_and_replay_preserve_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "memory.sqlite3"
            history = [user("Export as CSV."), user("Please make exports easier to read.")]
            with RelayStore(path) as store:
                session = store.create_session("Durable")["id"]
                memory = AutomaticMemory(store, session)
                memory.process(history)
                pending = memory.pending()[0]
                event = memory.dismiss(pending["id"])
                memory.undo(event["id"])
            with RelayStore(path) as store:
                memory = AutomaticMemory(store, session)
                self.assertEqual(memory.pending()[0]["value"], None)
                self.assertTrue(memory.snapshot()["entries"][0]["disputed"])
                self.assertEqual(memory.process(history)["status"], "unchanged")
                self.assertEqual(len(memory.pending()), 1)
                memory.dismiss(memory.pending()[0]["id"])
                memory.process(history)
                self.assertEqual(memory.pending(), [])

    def test_uncertainty_obeys_budgets_and_batch_rollback(self):
        memory = AutomaticMemory(self.store, self.memory.session_id, policy=MemoryPolicy(max_entries=1))
        events = self.store.events(memory.session_id)
        history = self.history + [user("Please make exports easier to read.")]
        with self.assertRaises(MemoryBudgetExceeded):
            memory.process(history)
        self.assertEqual(memory.pending(), [])
        self.assertEqual(self.store.events(memory.session_id), events)
        self.memory.process(history)
        self.assertEqual(len(self.memory.pending()), 1)

    def test_unclear_compound_request_changes_no_values_and_disputes_named_targets(self):
        self.append("Reply in English.")
        self.append("Export as JSON and make the response language more suitable for the audience.")
        self.assertEqual({e["key"]: e["value"] for e in self.memory.entries()},
                         {"export_format": "CSV", "response_language": "English"})
        self.assertEqual({p["key"] for p in self.memory.pending()}, {"export_format", "response_language"})
        self.assertTrue(all(p["value"] is None for p in self.memory.pending()))

    def test_interpreter_abstention_or_wrong_key_cannot_hide_named_uncertainty(self):
        for proposals in ([], [{"key": "unrelated", "value": "something"}]):
            with self.subTest(proposals=proposals):
                memory = AutomaticMemory(self.store, self.store.create_session("Fallback")["id"])
                history = [user("Export as CSV.")]
                memory.process(history)
                def interpret(request):
                    return [p | {"quote": request["text"], "start": 0, "end": len(request["text"])} for p in proposals]
                memory.interpreter = MemoryInterpreter(interpret)
                history.append(user("Please make exports easier to read."))
                memory.process(history)
                self.assertTrue(memory.snapshot()["entries"][0]["disputed"])
                self.assertEqual(next(p for p in memory.pending() if p["key"] == "export_format")["value"], None)

    def test_explicit_unknown_literal_stays_literal_but_placeholder_needs_clarification(self):
        self.append("Set the export format to Parquet.")
        self.assertEqual(self.memory.entries()[0]["value"], "CSV")
        literal = self.memory.pending()[0]
        self.assertEqual(literal["reason"], "unrecognized_value_needs_review")
        self.assertEqual(literal["value"], "Parquet")
        self.memory.confirm(literal["id"])
        self.assertEqual(self.memory.entries()[0]["value"], "Parquet")
        self.append("Set the export format to whatever works.")
        self.assertEqual(self.memory.entries()[0]["value"], "Parquet")
        self.assertEqual(self.memory.pending()[0]["reason"], "unparsed_correction")

    def test_known_custom_setting_matches_its_name_not_an_unrelated_value(self):
        self.append("Set the delivery window to morning.")
        self.append("Please shift the delivery window a little later.")
        self.assertEqual(self.memory.pending()[0]["targets"], ["delivery_window"])
        self.assertFalse(next(e for e in self.memory.snapshot()["entries"] if e["key"] == "export_format")["disputed"])

    def test_quoted_reported_conditional_temporary_and_question_controls_do_not_dispute(self):
        controls = [
            '"Please make exports easier to read."', "`Please make exports easier to read.`",
            "> Please make exports easier to read.", "Someone said: please make exports easier to read.",
            "If convenient, please make exports easier to read.", "Maybe make exports easier to read.",
            "Do not make exports easier to read.", "Could you explain why exports are difficult to read?",
            "For this response, make exports easier to read.",
            "The document contains this request. Please make exports easier to read.",
            "Please make exports easier to read. Is that possible?",
            "Please make exports easier to read for this single download only.",
        ]
        for text in controls:
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Control")["id"])
                memory.process([user("Export as CSV."), user(text)])
                self.assertEqual(memory.pending(), [])
                self.assertFalse(memory.snapshot()["entries"][0]["disputed"])

    def test_unclear_unbounded_temporary_request_is_retained_for_clarification(self):
        # v0.20 preserves this uncertainty until an explicit scope/value follows.
        self.append("Please make exports easier to read for now.")
        self.assertEqual(self.memory.pending()[0]["targets"], ["export_format"])
        self.assertEqual(self.memory.pending()[0]["source"]["scope"], "unsupported_lifetime")

    def test_finite_values_normalize_but_original_quotes_and_custom_values_remain_exact(self):
        self.append("Export as json.")
        self.assertEqual(self.memory.entries()[0]["value"], "JSON")
        self.assertEqual(self.memory.entries()[0]["source"]["quote"], "Export as json.")
        self.append("Set the project code to json.")
        self.assertEqual(next(e for e in self.memory.entries() if e["key"] == "project_code")["value"], "json")
        self.memory.interpreter = MemoryInterpreter(lambda request: [
            {"key": "export_format", "value": " yaml ", "quote": request["text"], "start": 0, "end": len(request["text"])}])
        self.append("Please make exports easier to read.")
        proposal = self.memory.pending()[0]
        self.assertEqual(proposal["value"], "YAML")
        self.assertEqual(proposal["reason"], "interpreted_needs_review")

    def test_unresolved_generic_values_do_not_bypass_named_finite_vocabulary(self):
        for text in ("Set the export format to the same as last time.",
                     "Use the other one for the export format.",
                     "The export format is whichever works.",
                     "My export format is whichever works.",
                     "Set our export format to the same as before.",
                     "Change the export format to a better format."):
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Unknown value")["id"])
                memory.process([user("Export as CSV."), user(text)])
                self.assertEqual(memory.entries()[0]["value"], "CSV")
                self.assertTrue(memory.pending())
                self.assertTrue(all(p["value"] is None for p in memory.pending()))

    def test_schema_migrates_once_and_blocks_the_previous_version_guard(self):
        self.assertEqual(self.store._memory_state(self.memory.session_id)["schema"], 1)
        self.append("Please make exports easier to read.")
        state = self.store._memory_state(self.memory.session_id)
        self.assertEqual(state["schema"], 2)
        # v0.13's exact schema guard rejects this state before confirm can see
        # its new null-valued record; no old package import is needed.
        def previous_version_load(value):
            if value.get("schema") != 1:
                raise ValueError("Unsupported automatic memory schema.")
        with self.assertRaises(ValueError):
            previous_version_load(state)
        self.memory.dismiss(self.memory.pending()[0]["id"])
        self.assertEqual(self.store._memory_state(self.memory.session_id)["schema"], 2)

    def test_malformed_null_pending_state_is_rejected(self):
        self.append("Please make exports easier to read.")
        with self.store._transaction(write=True):
            state = self.store._memory_state(self.memory.session_id)
            next(iter(state["pending"].values()))["reason"] = "review_mode"
            self.store._save_memory_state(self.memory.session_id, state)
        with self.assertRaisesRegex(ValueError, "clarification-only"):
            self.memory.snapshot()

    def test_review_confirmation_clears_captured_earlier_flags_but_never_newer_flags(self):
        self.append("Please make exports easier to read.")
        earlier = self.memory.pending()[0]["id"]
        self.memory.policy = MemoryPolicy(mode="review")
        self.append("Export as JSON.")
        concrete = next(p for p in self.memory.pending() if p["value"] == "JSON")
        self.assertEqual(concrete["clarifies"], [earlier])
        self.append("Please make exports easier to read.")
        newer = [p["id"] for p in self.memory.pending() if p["value"] is None and p["id"] != earlier]
        self.assertEqual(len(newer), 1)
        self.memory.confirm(concrete["id"])
        self.assertEqual(self.memory.entries()[0]["value"], "JSON")
        self.assertEqual([p["id"] for p in self.memory.pending()], newer)
        self.assertTrue(self.memory.snapshot()["entries"][0]["disputed"])

    def test_review_reaffirmation_can_resolve_an_earlier_unknown_without_changing_value(self):
        self.append("Please make exports easier to read.")
        self.memory.policy = MemoryPolicy(mode="review")
        self.append("Export as CSV.")
        concrete = next(p for p in self.memory.pending() if p["value"] == "CSV")
        self.memory.confirm(concrete["id"])
        self.assertEqual(self.memory.pending(), [])
        self.assertEqual(self.memory.entries()[0]["value"], "CSV")

    def test_post_holdout_safety_fix_rejects_metatext_and_relational_declarations(self):
        # Added only after the frozen v0.14 evaluation exposed false promotion.
        # No original corpus/report is rewritten; this is a safety regression.
        for text in (
            "My preference for the sample replies is Hindi.",
            "Our preference about the example exports is YAML.",
            "The instruction regarding exports is JSON.",
            "The following is a sample: set export format to JSON.",
            "The sentence is export as JSON.",
            "The snippet is set the export format to YAML.",
            "Thanks. The paragraph is export as JSON.",
            "This sample contains a request. Export as JSON.",
        ):
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Metatext")["id"])
                history = [user("Export as CSV. Reply in English.")]
                memory.process(history)
                before = memory.entries()
                memory.process(history + [user(text)])
                self.assertEqual(memory.entries(), before)
                self.assertEqual(memory.pending(), [])

    def test_simple_custom_fields_and_explicit_relational_custom_setters_remain_available(self):
        self.append("The sample size is 20.")
        self.append("Set the timeout for exports to 30 seconds.")
        self.assertEqual({e["key"]: e["value"] for e in self.memory.entries()},
                         {"export_format": "CSV", "sample_size": "20", "timeout_for_exports": "30 seconds"})


if __name__ == "__main__":
    unittest.main()
