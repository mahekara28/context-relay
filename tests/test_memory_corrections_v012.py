"""Everyday request forms and narrowly reviewed contextual corrections."""

import json
import unittest
from pathlib import Path

from context_relay import AutomaticMemory, MemoryInterpreter, MemoryPolicy, RelayStore


ROOT = Path(__file__).resolve().parents[1]


def user(text):
    return {"role": "user", "content": text}


def append(history, text):
    return history + [{"role": "assistant", "content": "Acknowledged."}, user(text)]


class EverydayCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.addCleanup(self.store.close)
        self.memory = AutomaticMemory(self.store, self.store.create_session("Everyday wording")['id'])

    def values(self, memory=None):
        return {entry["key"]: entry["value"] for entry in (memory or self.memory).entries()}

    def test_frozen_implementer_development_corpus(self):
        corpus = json.loads((ROOT / "evaluations/corrections-v0.12-development-corpus.json").read_text(encoding="utf-8"))
        for case in corpus["cases"]:
            # v0.13 intentionally adds these request forms. Preserve the frozen
            # corpus and its historical scores; state migrations only here.
            updates = {"question": {"response_style": "brief"},
                       "uncertain_polite": {"response_style": "brief"},
                       "contraction": {"response_style": "brief"},
                       "compound": {"response_style": "brief", "response_language": "Hindi"}}
            if case["id"] in updates:
                case = {**case, "expected_entries": case["expected_entries"] | updates[case["id"]]}
            # v0.14 preserves the historical corpus while applying its explicit
            # finite-value normalization and clarification-only migrations.
            if case["id"] == "style_bare":
                case = {**case, "expected_pending": [case["expected_pending"][0] | {"value": "concise"}]}
            if case["id"] in {"conflicting_options", "unknown_export"}:
                case = {**case, "expected_pending": [{"key": "export_format", "value": None,
                    "reason": "unparsed_correction", "targets": ["export_format"]}]}
            with self.subTest(case=case["id"]):
                memory = AutomaticMemory(self.store, self.store.create_session(case["id"])['id'])
                history = []
                for text in case["before"]:
                    history.extend([user(text), {"role": "assistant", "content": "Acknowledged."}])
                history.append(user(case["text"]))
                memory.process(history)
                self.assertEqual(self.values(memory), case["expected_entries"])
                self.assertEqual([{key: proposal[key] for key in ("key", "value", "reason", "targets")}
                                  for proposal in memory.pending()], case["expected_pending"])
                for entry in memory.entries() + memory.pending():
                    source = entry["source"]
                    self.assertEqual(source["quote"], history[source["index"]]["content"][source["start"]:source["end"]])
                self.assertEqual(self.store.facts(memory.session_id), {})

    def test_request_wrappers_and_style_normalization_preserve_original_source_spans(self):
        text = "  Thanks!\nGoing forward, please answer succinctly.\nI need you to reply in Hindi.  "
        self.memory.process([user(text)])
        self.assertEqual(self.values(), {"response_style": "concise", "response_language": "Hindi"})
        for entry in self.memory.entries():
            source = entry["source"]
            self.assertEqual(source["text"], text)
            self.assertEqual(source["quote"], text[source["start"]:source["end"]])
        self.assertEqual(self.memory.entries()[1]["source"]["quote"], "Going forward, please answer succinctly.")

    def test_reaffirming_an_existing_style_can_support_a_new_review_without_rewriting_its_source(self):
        history = [user("Keep responses detailed.")]
        self.memory.process(history)
        original = self.memory.entries()[0]
        history = append(history, "Answer in detail.")
        self.memory.process(history)
        self.assertEqual(self.memory.entries()[0], original)
        history = append(history, "Make it shorter.")
        self.memory.process(history)
        proposal = self.memory.pending()[0]
        self.assertEqual(proposal["reason"], "contextual_needs_review")
        self.assertEqual(proposal["value"], "shorter")
        self.assertEqual(proposal["source"]["index"], 4)
        self.assertTrue(self.memory.snapshot()["entries"][0]["disputed"])
        self.assertEqual(self.values(), {"response_style": "detailed"})

    def test_reviewed_relative_value_is_not_rewritten_to_an_unjustified_absolute_style(self):
        history = append([user("Keep responses detailed.")], "Less verbose, please.")
        self.memory.process(history)
        proposal = self.memory.pending()[0]
        self.assertEqual(proposal["value"], "Less verbose")
        self.memory.confirm(proposal["id"])
        self.assertEqual(self.values(), {"response_style": "Less verbose"})
        self.assertEqual(self.memory.context_snapshot()["entries"][0]["source"]["method"], "contextual")
        self.assertFalse(self.memory.snapshot()["current_authority"])
        self.assertEqual(self.store.facts(self.memory.session_id), {})

    def test_format_reference_uses_recent_named_field_without_overwriting_another_equal_value(self):
        history = [user("Set the log format to CSV.")]
        history = append(history, "Set the output format to CSV.")
        self.memory.process(history)
        history = append(history, "Switch it to JSON.")
        self.memory.process(history)
        proposal = self.memory.pending()[0]
        self.assertEqual(proposal["targets"], ["output_format"])
        self.assertEqual(self.values(), {"log_format": "CSV", "output_format": "CSV"})
        self.memory.confirm(proposal["id"])
        self.assertEqual(self.values(), {"log_format": "CSV", "output_format": "JSON"})
        self.assertEqual(self.memory.process(history)["status"], "unchanged")

    def test_current_disputes_and_competing_referents_do_not_get_another_guessed_proposal(self):
        for before in (
            ["Reply in English.", "The response language is French."],
            ["Reply in English. Set the documentation language to English."],
            ["Reply in English.", "Read the last message."],
        ):
            with self.subTest(before=before):
                memory = AutomaticMemory(self.store, self.store.create_session("Ambiguous")['id'])
                history = []
                for text in before:
                    history = [user(text)] if not history else append(history, text)
                memory.process(history)
                entries, pending = memory.entries(), memory.pending()
                memory.process(append(history, "Switch to Hindi."))
                self.assertEqual(memory.entries(), entries)
                self.assertEqual(memory.pending(), pending)

    def test_new_local_forms_do_not_call_optional_interpreter(self):
        calls = []
        self.memory.interpreter = MemoryInterpreter(lambda request: calls.append(request) or [])
        history = [user("From now on, please give me brief answers.")]
        self.memory.process(history)
        self.memory.process(append(history, "More detailed, please."))
        self.assertEqual(calls, [])
        self.assertEqual(self.memory.pending()[0]["source"]["method"], "contextual")

    def test_optional_interpreter_receives_original_unfamiliar_request_after_wrapper_filter(self):
        calls = []
        def interpret(request):
            calls.append(request)
            text = request["text"]
            return [{"key": "response_style", "value": "less formal", "quote": text,
                     "start": 0, "end": len(text)}]
        self.memory.interpreter = MemoryInterpreter(interpret)
        text = "Going forward, be less formal in your responses."
        self.memory.process(append([user("Keep responses detailed.")], text))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["text"], text)
        self.assertEqual(calls[0]["known_memory"], [{"key": "response_style", "value": "detailed", "disputed": False}])
        self.assertEqual(self.memory.pending()[0]["reason"], "interpreted_needs_review")
        self.assertEqual(self.values(), {"response_style": "detailed"})

    def test_wrapper_support_never_strips_away_caution_or_framing_to_reach_interpreter(self):
        calls = []
        for text in ('"Going forward, be less formal in your responses."',
                     "Going forward, if possible, be less formal in your responses.",
                     "The customer said: going forward, be less formal in your responses.",
                     "For this example, be less formal in your responses.",
                     "I need you to avoid sharing a secret password.",
                     "I want you to reply in Hindi. Is that correct?"):
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Excluded")['id'],
                    interpreter=MemoryInterpreter(lambda request: calls.append(request) or []))
                memory.process([user(text)])
                self.assertEqual(memory.entries(), [])
                self.assertEqual(memory.pending(), [])
        self.assertEqual(calls, [])

    def test_normalized_old_style_values_are_still_checked_before_replacement(self):
        history = [user("Keep responses detailed.")]
        self.memory.process(history)
        history = append(history, "Answer concisely instead of briefly.")
        self.memory.process(history)
        proposal = self.memory.pending()[0]
        self.assertEqual(proposal["old"], "brief")
        self.assertEqual(proposal["value"], "concise")
        self.assertEqual(proposal["reason"], "old_value_mismatch")
        self.assertEqual(self.values(), {"response_style": "detailed"})

    def test_review_policy_still_requires_approval_for_normalized_named_forms(self):
        memory = AutomaticMemory(self.store, self.memory.session_id, policy=MemoryPolicy(mode="review"))
        memory.process([user("Answer briefly.")])
        self.assertEqual(memory.entries(), [])
        proposal = memory.pending()[0]
        self.assertEqual(proposal["value"], "brief")
        self.assertEqual(proposal["reason"], "review_mode")

    def test_unsupported_reply_wording_does_not_invent_fields(self):
        for text in ("Use Python for your replies.", "Use whatever works for the answers.",
                     "Set your replies to whatever works."):
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Unsupported")['id'])
                memory.process([user(text)])
                self.assertEqual(memory.entries(), [])
                self.assertEqual(memory.pending(), [])

    def test_post_holdout_pronoun_targets_never_become_named_settings(self):
        # Added after the frozen holdout exposed "Use JSON for it." creating
        # it=JSON. Historical holdout results remain unchanged and published.
        for text in ("Use JSON for it.", "Use JSON for them.", "Set this to JSON.",
                     "Set that to JSON.", "Use JSON for these.", "Use JSON for those.",
                     "Set this one to JSON.", "Set those ones to JSON."):
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Pronoun")['id'])
                history = [user("Export as CSV. Set the log format to XML.")]
                memory.process(history)
                before = memory.entries()
                memory.process(append(history, text))
                self.assertEqual(memory.entries(), before)
                self.assertEqual(memory.pending(), [])


if __name__ == "__main__":
    unittest.main()
