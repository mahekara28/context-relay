"""Useful English variants without treating unknown surrounding prose as facts."""

import unittest

from context_relay import AutomaticMemory, MemoryPolicy, RelayStore


def user(text):
    return {"role": "user", "content": text}


def append(history, text):
    return history + [{"role": "assistant", "content": "Acknowledged."}, user(text)]


class MemoryLanguageTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()

    def tearDown(self):
        self.store.close()

    def memory(self, policy=None):
        return AutomaticMemory(self.store, self.store.create_session("Language test")["id"], policy=policy)

    def values(self, memory):
        return {entry["key"]: entry["value"] for entry in memory.entries()}

    def test_short_preferences_use_named_keys(self):
        cases = {
            "I prefer brief answers.": {"response_style": "brief"},
            "Please keep responses short.": {"response_style": "short"},
            "I prefer detailed responses.": {"response_style": "detailed"},
            "Keep answers concise.": {"response_style": "concise"},
            "Reply in Hindi.": {"response_language": "Hindi"},
            "Respond in Portuguese.": {"response_language": "Portuguese"},
            "Export as CSV.": {"export_format": "CSV"},
            "EXPORT AS jsonl.": {"export_format": "JSONL"},  # v0.14 finite spelling normalization.
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                memory = self.memory()
                memory.process([user(text)])
                self.assertEqual(self.values(memory), expected)

    def test_courtesy_prefixes_and_standalone_fragments_preserve_source_spans(self):
        text = "  Thanks!\n Okay, please reply in Hindi.\nExport as CSV, please. Thank you. "
        memory = self.memory()
        report = memory.process([user(text)])
        self.assertEqual(self.values(memory), {"response_language": "Hindi", "export_format": "CSV"})
        self.assertEqual(report["ignored"], [])
        self.assertEqual({entry["source"]["quote"] for entry in memory.entries()},
                         {"Okay, please reply in Hindi.", "Export as CSV, please."})
        for entry in memory.entries():
            source = entry["source"]
            self.assertEqual(source["quote"], text[source["start"]:source["end"]])
            self.assertEqual(source["text"], text)

    def test_politeness_is_not_a_memory_and_question_politeness_is_not_ignored(self):
        memory = self.memory()
        memory.process([user("Okay. Thank you! Please.")])
        self.assertEqual(self.values(memory), {})
        self.assertEqual(self.store.events(memory.session_id), [])
        other = self.memory()
        other.process([user("Reply in Hindi. Okay?")])
        self.assertEqual(self.values(other), {})

    def test_multiple_clear_aliases_remain_atomic(self):
        memory = self.memory()
        memory.process([user("Thanks. Keep responses brief. Reply in Hindi. Export as CSV.")])
        self.assertEqual(self.values(memory), {
            "response_style": "brief", "response_language": "Hindi", "export_format": "CSV",
        })
        self.assertEqual(len(self.store.events(memory.session_id)), 1)

    def test_new_aliases_update_existing_named_keys(self):
        memory = self.memory()
        history = [user("The response language is English. The export format is CSV.")]
        memory.process(history)
        memory.process(append(history, "Okay, actually, reply in Hindi. Export as JSON."))
        self.assertEqual(self.values(memory), {"response_language": "Hindi", "export_format": "JSON"})
        self.assertEqual(memory.pending(), [])

    def test_named_old_value_correction_uses_target_even_when_another_value_matches(self):
        memory = self.memory()
        history = [user("Use CSV for the export format. Use CSV for the log format.")]
        memory.process(history)
        memory.process(append(history, "Export as JSON instead of CSV."))
        self.assertEqual(self.values(memory), {"export_format": "JSON", "log_format": "CSV"})
        self.assertEqual(memory.pending(), [])

    def test_named_language_and_style_old_values_are_checked(self):
        memory = self.memory()
        history = [user("Keep responses detailed. Reply in English.")]
        memory.process(history)
        memory.process(append(history, "I prefer brief answers instead of detailed responses. Reply in Hindi instead of English."))
        self.assertEqual(self.values(memory), {"response_style": "brief", "response_language": "Hindi"})
        self.assertEqual(memory.pending(), [])

    def test_wrong_old_value_requires_review_and_disputes_only_named_target(self):
        memory = self.memory()
        history = [user("Export as XML. Use CSV for the log format.")]
        memory.process(history)
        memory.process(append(history, "Export as JSON instead of CSV."))
        self.assertEqual(self.values(memory), {"export_format": "XML", "log_format": "CSV"})
        proposal = memory.pending()[0]
        self.assertEqual(proposal["reason"], "old_value_mismatch")
        self.assertEqual(proposal["targets"], ["export_format"])
        self.assertEqual({e["key"]: e["disputed"] for e in memory.snapshot()["entries"]},
                         {"export_format": True, "log_format": False})
        memory.confirm(proposal["id"])
        self.assertEqual(self.values(memory)["export_format"], "JSON")

    def test_missing_named_old_value_needs_review_even_if_other_key_matches(self):
        memory = self.memory()
        history = [user("Use CSV for the log format.")]
        memory.process(history)
        memory.process(append(history, "Export as JSON instead of CSV."))
        self.assertEqual(self.values(memory), {"log_format": "CSV"})
        self.assertEqual(memory.pending()[0]["key"], "export_format")
        self.assertEqual(memory.pending()[0]["reason"], "old_value_mismatch")

    def test_explicit_later_setter_resolves_named_old_value_dispute(self):
        memory = self.memory()
        history = [user("Export as XML.")]
        memory.process(history)
        history = append(history, "Export as JSON instead of CSV.")
        memory.process(history)
        memory.process(append(history, "Export as JSON."))
        self.assertEqual(self.values(memory), {"export_format": "JSON"})
        self.assertEqual(memory.pending(), [])

    def test_review_policy_still_requires_confirmation_for_new_aliases(self):
        memory = self.memory(MemoryPolicy(mode="review"))
        memory.process([user("Please keep responses short.")])
        self.assertEqual(self.values(memory), {})
        proposal = memory.pending()[0]
        self.assertEqual(proposal["reason"], "review_mode")
        memory.confirm(proposal["id"])
        self.assertEqual(self.values(memory), {"response_style": "short"})

    def test_shorthand_never_guesses_a_target_from_existing_memory(self):
        cases = ["Actually, CSV please.", "Actually, use Hindi.", "Make it brief.", "JSON instead."]
        for text in cases:
            with self.subTest(text=text):
                memory = self.memory()
                history = [user("Export as CSV. Reply in English. Keep responses detailed.")]
                memory.process(history)
                before = self.values(memory)
                memory.process(append(history, text))
                self.assertEqual(self.values(memory), before)
                self.assertEqual(memory.pending(), [])

    def test_unrecognized_values_do_not_turn_prose_into_preferences(self):
        # v0.12 explicitly maps "in detail" to style, never to a language.
        memory = self.memory()
        memory.process([user("Reply in detail.")])
        self.assertEqual(self.values(memory), {"response_style": "detailed"})
        cases = ["Respond in confidence.", "Export as needed.",
                 "Export as requested.", "Keep responses coming.", "I prefer honest answers.",
                 "Reply in Hindi for this sentence.", "Export as CSV for the sample."]
        for text in cases:
            with self.subTest(text=text):
                memory = self.memory()
                memory.process([user(text)])
                self.assertEqual(self.values(memory), {})
                self.assertEqual(memory.pending(), [])

    def test_explicit_fields_allow_values_outside_small_alias_vocabulary(self):
        memory = self.memory()
        memory.process([user("Set the response language to Esperanto. Set the export format to Parquet.")])
        # v0.14 requires review for unfamiliar literal values in finite keys.
        self.assertEqual(memory.entries(), [])
        for proposal in memory.pending():
            self.assertEqual(proposal["reason"], "unrecognized_value_needs_review")
            memory.confirm(proposal["id"])
        self.assertEqual(self.values(memory), {"response_language": "Esperanto", "export_format": "Parquet"})

    def test_quoted_hypothetical_negative_and_framed_aliases_remain_ineligible(self):
        cases = [
            '"Reply in Hindi."', "`Export as CSV.`", "> Keep responses brief.",
            "If possible, reply in Hindi.", "Maybe export as CSV.", "Do not export as CSV.",
            "Someone said: reply in Hindi.", "Thanks. Here is a request:\nReply in Hindi.",
            "Okay, translate this:\nReply in Hindi.", "For the demo:\nExport as CSV.",
            "Thanks. Reply in Hindi. Does that sound right?", "Reply in Hindi and maybe export as CSV.",
            "Thanks for checking. Reply in Hindi.", "Reply in Hindi. Thanks for checking.",
        ]
        for text in cases:
            with self.subTest(text=text):
                memory = self.memory()
                memory.process([user(text)])
                self.assertEqual(self.values(memory), {})
                self.assertEqual(memory.pending(), [])

    def test_previous_eight_framing_failures_stay_fixed_with_new_aliases(self):
        frames = ["Is the following correct?", "Here are the notes to review:",
                  "Can you validate this statement?", "For the demo:",
                  "The PDF contains this sentence.", "The customer claims the following.",
                  "Please check:", "The test fixture contains:"]
        for frame in frames:
            with self.subTest(frame=frame):
                memory = self.memory()
                memory.process([user("Thanks.\n" + frame + "\nExport as CSV.")])
                self.assertEqual(self.values(memory), {})
                self.assertEqual(memory.pending(), [])

    def test_unsafe_fragment_never_partially_updates_other_aliases(self):
        memory = self.memory()
        history = [user("Reply in English.")]
        memory.process(history)
        memory.process(append(history, "Reply in Hindi. Export as CSV. These are examples."))
        self.assertEqual(self.values(memory), {"response_language": "English"})
        self.assertEqual(len(self.store.events(memory.session_id)), 1)

    def test_aliases_never_extract_non_user_roles(self):
        memory = self.memory()
        memory.process([{"role": "system", "content": "Keep responses brief."},
                        user("Help me."),
                        {"role": "assistant", "content": "Reply in Hindi. Export as CSV."},
                        user("Continue.")])
        self.assertEqual(self.values(memory), {})


if __name__ == "__main__":
    unittest.main()
