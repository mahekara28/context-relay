"""Named wording expansion and review-only local contextual suggestions."""

import json
import tempfile
import unittest
from pathlib import Path

from context_relay import AutomaticMemory, MemoryBudgetExceeded, MemoryInterpreter, MemoryPolicy, RelayStore


ROOT = Path(__file__).resolve().parents[1]


def user(text):
    return {"role": "user", "content": text}


def append(history, text):
    return history + [{"role": "assistant", "content": "OK"}, user(text)]


class CorrectionWordingTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.memory = AutomaticMemory(self.store, self.store.create_session("Wording")['id'])

    def tearDown(self):
        self.store.close()

    def values(self, memory=None):
        return {entry["key"]: entry["value"] for entry in (memory or self.memory).entries()}

    def test_frozen_development_corpus(self):
        corpus = json.loads((ROOT / "evaluations/corrections-v0.11-corpus.json").read_text(encoding="utf-8"))
        for case in corpus["cases"]:
            # v0.12 adds this review-only recent format reference. Keep the
            # original v0.11 corpus/report files immutable as historical evidence.
            if case["id"] == "fragment":
                case = {**case, "expected_pending": [{"key": "export_format", "value": "JSON",
                    "reason": "contextual_needs_review", "targets": ["export_format"]}]}
            if case["id"] == "polite_question":
                # v0.13 accepts the explicit named request inside this question.
                case = {**case, "expected_entries": case["expected_entries"] | {"response_style": "short"}}
            # v0.14 canonicalizes finite values and exposes plausible unparsed
            # corrections as clarification-only records. Raw corpus is unchanged.
            if case["id"] == "case_preserved":
                case = {**case, "expected_entries": {"export_format": "JSON"}}
            if case["id"] in {"compound", "reason", "format_unsupported", "meaningful_but_unsupported"}:
                case = {**case, "expected_pending": [{"key": "export_format", "value": None,
                    "reason": "unparsed_correction", "targets": ["export_format"]}]}
            with self.subTest(case=case["id"]):
                memory = AutomaticMemory(self.store, self.store.create_session(case["id"])['id'],
                                         policy=MemoryPolicy(mode=case.get("mode", "auto")))
                history = []
                for text in case["before"]:
                    history.extend([user(text), {"role": "assistant", "content": "OK"}])
                history.append({"role": case.get("role", "user"), "content": case["text"]})
                if history[-1]["role"] != "user":
                    history.append(user("Continue."))
                memory.process(history)
                self.assertEqual(self.values(memory), case["expected_entries"])
                self.assertEqual([{key: p[key] for key in ("key", "value", "reason", "targets")}
                                  for p in memory.pending()], case["expected_pending"])
                for record in memory.entries() + memory.pending():
                    source = record["source"]
                    self.assertEqual(source["quote"], history[source["index"]]["content"][source["start"]:source["end"]])
                self.assertEqual(self.store.facts(memory.session_id), {})

    def test_contextual_proposal_disputes_until_explicit_confirmation_without_model(self):
        calls = []
        self.memory.interpreter = MemoryInterpreter(lambda request: calls.append(request) or [])
        history = [user("Keep responses detailed.")]
        self.memory.process(history)
        text = "  Thanks! Please make it brief.  "
        history = append(history, text)
        result = self.memory.process(history)
        proposal = self.memory.pending()[0]
        self.assertEqual(result["pending_ids"], [proposal["id"]])
        self.assertEqual(calls, [])
        self.assertEqual(self.values(), {"response_style": "detailed"})
        self.assertTrue(self.memory.snapshot()["entries"][0]["disputed"])
        self.assertEqual(proposal["source"]["method"], "contextual")
        self.assertEqual(proposal["source"]["quote"], "Please make it brief.")
        self.memory.confirm(proposal["id"])
        self.assertEqual(self.values(), {"response_style": "brief"})
        self.assertEqual(self.memory.context_snapshot()["entries"][0]["source"]["method"], "contextual")
        self.assertEqual(self.store.facts(self.memory.session_id), {})

    def test_contextual_dismiss_and_reopen_do_not_recreate_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            history = append([user("Keep responses detailed.")], "Make it brief.")
            with RelayStore(path) as store:
                session = store.create_session("Persistent")['id']
                memory = AutomaticMemory(store, session)
                memory.process(history)
                memory.dismiss(memory.pending()[0]["id"])
            with RelayStore(path) as store:
                memory = AutomaticMemory(store, session)
                self.assertEqual(memory.process(history)["status"], "unchanged")
                self.assertEqual(memory.pending(), [])
                self.assertEqual(self.values(memory), {"response_style": "detailed"})

    def test_contextual_disputed_missing_or_forgotten_antecedents_are_not_guessed(self):
        history = [user("Keep responses detailed.")]
        self.memory.process(history)
        history = append(history, "The response style is concise.")
        self.memory.process(history)
        before = self.memory.pending()
        self.memory.process(append(history, "Make it brief."))
        self.assertEqual(self.memory.pending(), before)
        self.memory.forget("response_style")
        self.memory.process(append(append(history, "Make it brief."), "Keep it short."))
        self.assertEqual(self.memory.entries(), [])
        self.assertEqual(self.memory.pending(), [])

    def test_contextual_references_cannot_cross_archived_session_provenance(self):
        # Rollover copies provenance with source.session_id. Even matching local
        # indices and text may not bind a pronoun to the previous segment.
        history = [user("Keep responses detailed.")]
        self.memory.process(history)
        with self.store._transaction(write=True):
            state = self.store._memory_state(self.memory.session_id)
            state["entries"]["response_style"]["source"]["session_id"] = "older-session"
            self.store._save_memory_state(self.memory.session_id, state)
        self.memory.process(append(history, "Make it brief."))
        self.assertEqual(self.memory.pending(), [])

    def test_named_instruction_clears_contextual_dispute(self):
        history = append([user("Keep responses detailed.")], "Make it brief.")
        self.memory.process(history)
        history = append(history, "I want concise answers.")
        self.memory.process(history)
        self.assertEqual(self.values(), {"response_style": "concise"})
        self.assertEqual(self.memory.pending(), [])

    def test_output_alias_ambiguity_requires_selecting_a_captured_key(self):
        history = [user("Export as CSV. Set the output format to XML.")]
        self.memory.process(history)
        self.memory.process(append(history, "I want the output in JSON."))
        proposal = self.memory.pending()[0]
        self.assertIsNone(proposal["key"])
        self.assertEqual({entry["key"]: entry["disputed"] for entry in self.memory.snapshot()["entries"]},
                         {"export_format": True, "output_format": True})
        self.memory.confirm(proposal["id"], key="output_format")
        self.assertEqual(self.values(), {"export_format": "CSV", "output_format": "JSON"})

    def test_explicit_from_value_uses_named_target_and_does_not_create_wrong_field(self):
        history = [user("Export as CSV. Set the log format to CSV.")]
        self.memory.process(history)
        self.memory.process(append(history, "Switch exports from CSV to JSON."))
        self.assertEqual(self.values(), {"export_format": "JSON", "log_format": "CSV"})
        self.assertEqual(self.memory.pending(), [])

    def test_unsupported_from_syntax_cannot_fall_through_as_a_new_field(self):
        for text in ("Change the export format from CSV to Parquet.",
                     "Set the response language from English to Hindi.",
                     "Change the export format from arbitrary to JSON."):
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Unsupported")['id'])
                memory.process([user(text)])
                self.assertEqual(memory.entries(), [])
                if text == "Set the response language from English to Hindi.":
                    # Supported in v0.15, but the asserted old value is absent:
                    # retain bounded review rather than creating an active key.
                    self.assertEqual(len(memory.pending()), 1)
                    self.assertEqual(memory.pending()[0]["reason"], "old_value_mismatch")
                    self.assertEqual(memory.pending()[0]["targets"], ["response_language"])
                else:
                    self.assertEqual(memory.pending(), [])

    def test_new_named_wording_does_not_strip_or_reinterpret_negation(self):
        for text in ("Not CSV; I want the output in JSON.",
                     "I never want detailed answers.",
                     "I want brief answers, but only for this message.",
                     "I want JSON for the output instead of a password."):
            with self.subTest(text=text):
                memory = AutomaticMemory(self.store, self.store.create_session("Excluded")['id'])
                memory.process([user(text)])
                self.assertEqual(memory.entries(), [])
                self.assertEqual(memory.pending(), [])

    def test_contextual_pending_still_obeys_atomic_memory_limits(self):
        memory = AutomaticMemory(self.store, self.memory.session_id, policy=MemoryPolicy(max_entries=1))
        history = append([user("Keep responses detailed.")], "Make it brief.")
        with self.assertRaises(MemoryBudgetExceeded):
            memory.process(history)
        self.assertEqual(memory.entries(), [])
        self.assertEqual(memory.pending(), [])
        self.assertEqual(self.store.events(memory.session_id), [])


if __name__ == "__main__":
    unittest.main()
