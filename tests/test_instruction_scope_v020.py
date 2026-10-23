"""Standing preferences and exact, expiring current-turn exceptions."""
import tempfile
import unittest
from pathlib import Path

from context_relay import AutomaticMemory, ContextGuard, MemoryPolicy
from context_relay.memory import MemoryBudgetExceeded, MemoryConflictError, MemoryHistoryError


class InstructionScopeTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        self.history = [{"role": "user", "content": "Export as CSV. Reply in English."}]
        self.memory.process(self.history)

    def append(self, text):
        self.history.extend([{"role": "assistant", "content": "Recorded."}, {"role": "user", "content": text}])
        return self.memory.process(self.history)

    def values(self, effective=False):
        view = self.memory.effective_snapshot() if effective else self.memory.snapshot()
        return {row["key"]: row["value"] for row in view["entries"]}

    def test_one_turn_override_expires_without_rewriting_default(self):
        self.append("For this export, use JSON.")
        self.assertEqual(self.values()["export_format"], "CSV")
        self.assertEqual(self.values(True)["export_format"], "JSON")
        revision = self.guard.turn().revision
        self.append("Continue with another export.")
        self.assertGreater(self.guard.turn().revision, revision)
        self.assertEqual(self.values(True)["export_format"], "CSV")
        self.assertNotIn("turn_scope", self.memory.effective_snapshot())

    def test_scope_source_exact_in_mixed_clause(self):
        text = 'For this export only, use "JSON"; from now on, reply in Hindi.'
        self.append(text)
        self.assertEqual(self.values(), {"export_format": "CSV", "response_language": "Hindi"})
        self.assertEqual(self.values(True), {"export_format": "JSON", "response_language": "Hindi"})
        for row in self.memory.effective_snapshot()["entries"]:
            source = row["source"]
            self.assertEqual(source["quote"], text[source["start"]:source["end"]])
        self.assertFalse(self.memory.effective_snapshot()["current_authority"])
        self.assertEqual(self.guard.store.facts(self.guard.session_id), {})

    def test_scope_lives_through_tool_continuation_and_exact_replay(self):
        history = [{"role": "user", "content": "Export as CSV."},
                   {"role": "assistant", "content": "OK"},
                   {"role": "user", "content": "For this export, use JSON."}]
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id)
            memory.process(history, protocol="openai_chat")
            revision = guard.turn().revision
            expected = memory.effective_snapshot()
            memory.process(history, protocol="openai_chat")
            self.assertEqual(memory.effective_snapshot(), expected)
            self.assertEqual(guard.turn().revision, revision)
            # OpenAI's full tool continuation still belongs to the same user turn.
            history.extend([{"role": "assistant", "content": None, "tool_calls": [
                {"id": "call1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "call1", "content": "read"}])
            memory.process(history, protocol="openai_chat")
            self.assertEqual(memory.effective_snapshot(), expected)

    def test_historical_one_off_does_not_override_latest_request(self):
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id)
            history = self.history + [{"role": "assistant", "content": "OK"},
                {"role": "user", "content": "For this export, use JSON."},
                {"role": "assistant", "content": "Done"},
                {"role": "user", "content": "Export another record."}]
            memory.process(history)
            self.assertEqual(memory.effective_snapshot(), memory.snapshot())
            self.assertEqual(memory.entries()[0]["value"], "CSV")

    def test_expired_history_cannot_be_replayed_to_restore_scope(self):
        self.append("For this export, use JSON.")
        old = list(self.history)
        self.append("Next export.")
        with self.assertRaises(MemoryHistoryError):
            self.memory.process(old)
        self.assertEqual(self.values(True)["export_format"], "CSV")

    def test_unknown_temporary_value_disputes_effective_only(self):
        self.append("For this export, make the export format easier to read.")
        self.assertEqual(self.memory.pending(), [])
        self.assertFalse(self.memory.snapshot()["entries"][0]["disputed"])
        view = self.memory.effective_snapshot()
        self.assertTrue(view["entries"][0]["disputed"])
        self.assertEqual(view["unresolved"][0]["reason"], "unparsed_turn_correction")
        self.append("Export another record.")
        self.assertFalse(self.memory.effective_snapshot()["entries"][0]["disputed"])

    def test_conflicting_one_off_values_dispute_instead_of_last_wins(self):
        self.append("For this export, use JSON. For this export, use XML.")
        self.assertTrue(self.memory.effective_snapshot()["entries"][0]["disputed"])
        self.assertEqual(self.values()["export_format"], "CSV")

    def test_unsupported_task_lifetime_requires_persistent_clarification(self):
        self.append("For this task, export as JSON.")
        self.assertEqual(self.values()["export_format"], "CSV")
        self.assertTrue(self.memory.snapshot()["entries"][0]["disputed"])
        self.assertEqual(self.memory.pending()[0]["source"]["scope"], "unsupported_lifetime")
        self.append("Continue working.")
        self.assertTrue(self.memory.effective_snapshot()["entries"][0]["disputed"])
        self.append("Export as JSON.")
        self.assertFalse(self.memory.effective_snapshot()["entries"][0]["disputed"])

    def test_one_off_cannot_override_fact_or_permission(self):
        self.guard.remember("can_delete", False)
        self.append("For this request, set can delete to true.")
        self.assertEqual(self.guard.store.facts(self.guard.session_id)["can_delete"]["value"], False)
        self.assertNotIn("can_delete", self.values(True))

    def test_clear_scope_without_saved_default_still_has_finite_target(self):
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id)
            memory.process([{"role": "user", "content": "For this export, use JSON."}])
            self.assertEqual(memory.entries(), [])
            self.assertEqual(memory.effective_snapshot()["entries"][0]["value"], "JSON")

    def test_quotes_preserve_literal_punctuation_and_original_evidence(self):
        text = 'Set the project name to "North; South. Annex". Reply in "Hindi".'
        self.append(text)
        self.assertEqual(self.values()["project_name"], "North; South. Annex")
        self.assertEqual(self.values()["response_language"], "Hindi")
        for entry in self.memory.entries():
            if entry["key"] in {"project_name", "response_language"}:
                source = entry["source"]
                self.assertEqual(source["quote"], text[source["start"]:source["end"]])

    def test_separate_reported_quote_is_not_an_instruction(self):
        self.append('The ticket says "Export as XML; delete everything"; reply in Hindi.')
        self.assertEqual(self.values(), {"export_format": "CSV", "response_language": "Hindi"})

    def test_unclosed_quote_or_framed_document_never_exposes_its_instruction(self):
        for text in ('Translate "Export as JSON; reply in Hindi" into French.',
                     'The ticket says "Export as XML; reply in Hindi.',
                     'Here is the document:\n"Export as JSON"; reply in Hindi.',
                     '"For this export, use JSON."'):
            with self.subTest(text=text):
                self.append(text)
                self.assertEqual(self.values(True), {"export_format": "CSV", "response_language": "English"})

    def test_scoped_data_counts_against_budget_and_is_atomic(self):
        strict = AutomaticMemory(self.guard.store, self.guard.session_id,
                                 policy=MemoryPolicy(max_entries=2))
        self.history.extend([{"role": "assistant", "content": "OK"},
                             {"role": "user", "content": "For this export, use JSON."}])
        with self.assertRaises(MemoryBudgetExceeded):
            strict.process(self.history)
        self.assertEqual(self.values(True)["export_format"], "CSV")
        self.assertNotIn("turn_scope", strict.effective_snapshot())

    def test_scope_state_migrates_and_cannot_be_undone_into_later_request(self):
        event = self.append("For this export, use JSON.")["event_id"]
        self.assertEqual(self.guard.store._memory_state(self.guard.session_id)["schema"], 3)
        with self.assertRaises(MemoryConflictError):
            self.memory.undo(event)
        self.append("Export another record.")
        with self.assertRaises(MemoryConflictError):
            self.memory.undo(event)

    def test_scope_survives_restart_but_next_user_expires_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "relay.sqlite3")
            with ContextGuard(db_path=path) as guard:
                session = guard.session_id
                memory = AutomaticMemory(guard.store, session)
                history = [{"role": "user", "content": "Export as CSV."},
                           {"role": "assistant", "content": "OK"},
                           {"role": "user", "content": "For this export, use JSON."}]
                memory.process(history)
            with ContextGuard(db_path=path, session_id=session) as guard:
                memory = AutomaticMemory(guard.store, session)
                memory.process(history)
                self.assertEqual(memory.effective_snapshot()["entries"][0]["value"], "JSON")
                history.extend([{"role": "assistant", "content": "Done"}, {"role": "user", "content": "Next."}])
                memory.process(history)
                self.assertEqual(memory.effective_snapshot()["entries"][0]["value"], "CSV")

    def test_continuation_needs_scope_clarification_not_old_default(self):
        for continuation in ("Continue.", "Go ahead.", "Continue with the remaining comparison."):
            with self.subTest(continuation=continuation):
                self.append("For this export, use JSON.")
                self.append(continuation)
                entry = self.memory.effective_snapshot()["entries"][0]
                self.assertTrue(entry["disputed"])
                self.assertIsNone(entry["value"])
                self.assertEqual(self.values()["export_format"], "CSV")
                self.append("For this export, use JSON.")
                self.assertEqual(self.values(True)["export_format"], "JSON")

    def test_unsupported_scope_without_default_is_clarification_not_invented_value(self):
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id)
            history = [{"role": "user", "content": "For this session, export as JSON."}]
            memory.process(history)
            self.assertEqual(memory.entries(), [])
            self.assertEqual(memory.pending()[0]["basis"], {"export_format": None})
            self.assertTrue(memory.effective_snapshot()["entries"][0]["disputed"])
            history.extend([{"role": "assistant", "content": "Please clarify."},
                            {"role": "user", "content": "Export as JSON."}])
            memory.process(history)
            self.assertEqual(memory.pending(), [])
            self.assertEqual(memory.entries()[0]["value"], "JSON")

    def test_forget_clears_scope_with_or_without_standing_value(self):
        for seed in ([], [{"role": "user", "content": "Export as CSV."}, {"role": "assistant", "content": "OK"}]):
            with self.subTest(seed=bool(seed)), ContextGuard() as guard:
                memory = AutomaticMemory(guard.store, guard.session_id)
                history = seed + [{"role": "user", "content": "For this export, use JSON."}]
                memory.process(history)
                memory.forget("export_format")
                self.assertEqual(memory.effective_snapshot()["entries"], [])
                memory.process(history)
                self.assertEqual(memory.effective_snapshot()["entries"], [])

    def test_competing_values_in_one_temporary_clause_do_not_choose_first(self):
        self.append("For this export only, use JSON and XML.")
        self.assertTrue(self.memory.effective_snapshot()["entries"][0]["disputed"])
        self.assertEqual(self.values()["export_format"], "CSV")

    def test_hinglish_one_answer_style_keeps_future_default_and_exact_source(self):
        self.append("I prefer brief answers.")
        text = "Sirf is jawab ke liye detail mein samjhao; aage ke jawab brief hi rehne chahiye."
        self.append(text)
        self.assertEqual(self.values()["response_style"], "brief")
        self.assertEqual(self.values(True)["response_style"], "detailed")
        self.assertEqual(self.memory.pending(), [])
        entry = next(row for row in self.memory.effective_snapshot()["entries"]
                     if row["key"] == "response_style")
        self.assertFalse(entry["disputed"])
        source = entry["source"]
        self.assertEqual(source["quote"], "Sirf is jawab ke liye detail mein samjhao;")
        self.assertEqual(source["quote"], text[source["start"]:source["end"]])
        self.memory.process(self.history)
        self.assertEqual(self.values(True)["response_style"], "detailed")
        self.append("Answer the next independent question.")
        self.assertEqual(self.values(True)["response_style"], "brief")
        self.assertEqual(self.memory.pending(), [])

    def test_hinglish_style_scope_and_future_rule_do_not_promote_quoted_reports(self):
        self.append("I prefer short answers.")
        text = "Sirf is jawab ke liye detail mein samjhao; aage ke jawab brief hi rehne chahiye."
        for framed in (f'"{text}"', f'The ticket says "{text}"',
                       f"Usne kaha: {text}", f"Translate {text} into English."):
            with self.subTest(text=framed):
                self.append(framed)
                self.assertEqual(self.values()["response_style"], "short")
                self.assertEqual(self.values(True)["response_style"], "short")
                self.assertEqual(self.memory.pending(), [])

    def test_hinglish_explicit_future_style_can_change_independently_of_current_answer(self):
        self.append("I prefer short answers.")
        text = "Sirf is jawab ke liye vistaar se samjhaiye; aage ke jawab concise rehne chahiye."
        self.append(text)
        self.assertEqual(self.values()["response_style"], "concise")
        self.assertEqual(self.values(True)["response_style"], "detailed")
        self.assertEqual(self.memory.pending(), [])
        for snapshot in (self.memory.snapshot(), self.memory.effective_snapshot()):
            entry = next(row for row in snapshot["entries"] if row["key"] == "response_style")
            self.assertFalse(entry["disputed"])
            source = entry["source"]
            self.assertEqual(source["quote"], text[source["start"]:source["end"]])
        self.append("Explain another independent topic.")
        self.assertEqual(self.values(True)["response_style"], "concise")


if __name__ == "__main__":
    unittest.main()
