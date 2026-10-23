"""Bounded indirect corrections, source direction and misleading controls."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path
import unittest

from context_relay import AutomaticMemory, MemoryConflictError, MemoryPolicy, RelayStore
from context_relay.memory import (_FINITE_SETTINGS, _SETTING_SUBJECTS, _extract,
                                  _polite_question, _request_body)
from context_relay.memory_plan import plan_corrections


def user(text):
    return {"role": "user", "content": text}


class IndirectMemoryTests(unittest.TestCase):
    def memory(self, extra=(), *, review=False):
        store = RelayStore()
        self.addCleanup(store.close)
        memory = AutomaticMemory(store, store.create_session("Indirect correction")["id"],
                                 policy=MemoryPolicy(max_chars=12000))
        history = [user(t) for t in ("Export as CSV.", "Reply in English.",
                                     "Keep responses detailed.", *extra)]
        memory.process(history)
        if review:
            memory.policy = MemoryPolicy(mode="review", max_chars=12000)
        return memory, history

    def append(self, memory, history, text):
        history.append(user(text))
        memory.process(history)
        for record in memory.entries() + memory.pending():
            source = record["source"]
            original = history[source["index"]]["content"]
            self.assertEqual(source["quote"], original[source["start"]:source["end"]])
        self.assertFalse(memory.snapshot()["current_authority"])
        self.assertFalse(memory.snapshot()["trusted_instruction"])
        self.assertEqual(memory.store.facts(memory.session_id), {})
        snapshot = memory.snapshot()
        self.assertEqual(memory.process(history)["status"], "unchanged")
        self.assertEqual(memory.snapshot(), snapshot)

    def values(self, memory):
        return {e["key"]: e["value"] for e in memory.entries()}

    def test_named_dissatisfaction_can_link_an_explicit_choice_in_same_message(self):
        cases = (
            ("My export format no longer works for me; I'd rather have JSON.", "export_format", "JSON"),
            ("My export format no longer works for me, I'd rather have JSON.", "export_format", "JSON"),
            ("CSV is too awkward for exports, use XML instead.", "export_format", "XML"),
            ("CSV is too awkward for exports; use YAML instead.", "export_format", "YAML"),
            ("CSV no longer works for exports. I prefer XML instead.", "export_format", "XML"),
            ("CSV exports are too cumbersome; I would rather use TSV.", "export_format", "TSV"),
            ("The response language no longer suits me; I prefer Hindi.", "response_language", "Hindi"),
            ("The response style is too verbose; I'd rather have concise responses instead.", "response_style", "concise"),
            ("Your replies are overly wordy; I'd prefer short answers.", "response_style", "short"),
            ("The response style is too detailed; I would rather have brief replies.", "response_style", "brief"),
            ("My export format is no longer suitable for me. Choose TOML instead.", "export_format", "TOML"),
            ("My reply language is unsuitable; choose Spanish.", "response_language", "Spanish"),
            ("My exports no longer fit my needs; I prefer JSONL.", "export_format", "JSONL"),
        )
        for text, key, expected in cases:
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                self.append(memory, history, text)
                self.assertEqual(self.values(memory), before | {key: expected})
                self.assertEqual(memory.pending(), [])
                record = next(e for e in memory.entries() if e["key"] == key)
                self.assertEqual(record["source"]["quote"], text)

    def test_rather_have_selector_is_bounded_to_a_named_setting(self):
        for phrase in ("I'd rather have", "I'd rather use", "I would rather have", "I would rather use"):
            with self.subTest(phrase=phrase):
                memory, history = self.memory()
                self.append(memory, history, f"{phrase} JSON for my exports instead.")
                self.assertEqual(self.values(memory)["export_format"], "JSON")
                self.assertEqual(memory.pending(), [])

    def test_negative_old_values_stay_uncertain_without_guessing_a_replacement(self):
        cases = (
            ("I don't want CSV for exports anymore.", "export_format"),
            ("I no longer want CSV as my export format.", "export_format"),
            ("Do not use CSV for exports.", "export_format"),
            ("Don't use CSV for exports any longer.", "export_format"),
            ("Stop using English in replies.", "response_language"),
            ("Could you stop using English in replies?", "response_language"),
            ("My response language isn't English anymore.", "response_language"),
            ("My export format is not CSV anymore.", "export_format"),
            ("My export format should not be CSV.", "export_format"),
            ("I don't prefer detailed responses as my default anymore.", "response_style"),
            ("The response style is overly lengthy.", "response_style"),
            ("My export format is too hard to read.", "export_format"),
            ("My response language no longer suits me.", "response_language"),
        )
        for text, key in cases:
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                self.append(memory, history, text)
                self.assertEqual(self.values(memory), before)
                self.assertEqual([p["key"] for p in memory.pending()], [key])
                pending = memory.pending()[0]
                self.assertIsNone(pending["value"])
                self.assertEqual(pending["reason"], "unparsed_correction")
                with self.assertRaises(MemoryConflictError):
                    memory.confirm(pending["id"])
                self.assertEqual({e["key"] for e in memory.snapshot()["entries"] if e["disputed"]}, {key})

    def test_rejecting_a_different_value_does_not_suspend_a_valid_current_choice(self):
        for text in ("My export format is not JSON anymore.",
                     "I don't want YAML for exports.",
                     "Stop using German in replies."):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = memory.snapshot()
                self.append(memory, history, text)
                self.assertEqual(memory.snapshot(), before)

    def test_explicit_replacement_clears_its_same_message_rejection(self):
        for text in ("Stop using CSV for exports; I prefer JSON.",
                     "My export format isn't CSV anymore; use YAML instead.",
                     "I no longer want CSV for exports; I'd rather have TOML."):
            with self.subTest(text=text):
                memory, history = self.memory()
                self.append(memory, history, text)
                self.assertEqual(memory.pending(), [])
                self.assertNotEqual(self.values(memory)["export_format"], "CSV")

    def test_old_value_mismatch_does_not_overwrite_the_current_preference(self):
        for text in ("JSON is too cumbersome for exports; choose XML.",
                     "Stop using YAML for exports; I'd rather have TOML."):
            with self.subTest(text=text):
                memory, history = self.memory()
                self.append(memory, history, text)
                self.assertEqual(self.values(memory)["export_format"], "CSV")
                self.assertEqual(memory.pending()[0]["reason"], "old_value_mismatch")
                self.assertEqual(memory.pending()[0]["targets"], ["export_format"])

    def test_ambiguous_singular_reference_suspends_all_same_message_candidates(self):
        for referent in ("that one", "this one", "one of them", "one of those"):
            with self.subTest(referent=referent):
                memory, history = self.memory()
                text = ("You have CSV recorded as my export format and English as my response language; "
                        f"{referent} is wrong.")
                self.append(memory, history, text)
                self.assertEqual({p["key"] for p in memory.pending()}, {"export_format", "response_language"})
                self.assertTrue(all(p["value"] is None for p in memory.pending()))

    def test_ambiguous_output_rejection_never_selects_one_format_by_recency(self):
        for next_clause in ("", " I'd rather have JSON."):
            with self.subTest(next_clause=next_clause):
                memory, history = self.memory(("Set output format to CSV.",))
                self.append(memory, history, "Stop using CSV for output." + next_clause)
                self.assertEqual(self.values(memory)["export_format"], "CSV")
                self.assertEqual(self.values(memory)["output_format"], "CSV")
                self.assertEqual({p["key"] for p in memory.pending()}, {"export_format", "output_format"})

    def test_only_compatible_rejected_alias_is_disputed_without_a_new_assignment(self):
        memory, history = self.memory(("Set output format to YAML.",))
        self.append(memory, history, "Stop using CSV for output.")
        self.assertEqual([p["key"] for p in memory.pending()], ["export_format"])

    def test_style_description_is_bounded_and_does_not_dispute_language(self):
        memory, history = self.memory()
        self.append(memory, history, "Your replies are too verbose.")
        self.assertEqual([p["key"] for p in memory.pending()], ["response_style"])

    def test_ambiguous_description_does_not_invent_a_domain_or_a_value(self):
        memory, history = self.memory()
        self.append(memory, history, "Your replies are too awkward.")
        self.assertEqual({p["key"] for p in memory.pending()}, {"response_style", "response_language"})
        self.assertTrue(all(p["value"] is None for p in memory.pending()))

    def test_custom_subject_names_are_exact_and_never_grant_tool_authority(self):
        memory, history = self.memory(("Set the delivery window to morning.",
                                       "Set the saved delivery window to afternoon."))
        self.append(memory, history, "My saved delivery window no longer suits me; I'd rather have evening.")
        self.assertEqual(self.values(memory)["delivery_window"], "morning")
        self.assertEqual(self.values(memory)["saved_delivery_window"], "evening")
        self.assertEqual(memory.pending(), [])

    def test_recognized_indirect_updates_still_require_review_in_review_mode(self):
        memory, history = self.memory(review=True)
        self.append(memory, history, "CSV is too awkward for exports; I'd rather have JSON.")
        self.assertEqual(self.values(memory)["export_format"], "CSV")
        proposal = memory.pending()[0]
        self.assertEqual(proposal["reason"], "review_mode")
        self.assertEqual(proposal["value"], "JSON")
        memory.confirm(proposal["id"])
        self.assertEqual(self.values(memory)["export_format"], "JSON")

    def test_later_named_clarification_resolves_only_the_named_uncertainty(self):
        memory, history = self.memory()
        self.append(memory, history, "Your replies are too awkward.")
        self.append(memory, history, "Reply in Hindi.")
        self.assertEqual(self.values(memory)["response_language"], "Hindi")
        self.assertEqual([p["key"] for p in memory.pending()], ["response_style"])

    def test_unrelated_named_sibling_keeps_an_independent_source_span(self):
        memory, history = self.memory()
        self.append(memory, history, "CSV is too awkward for exports; use JSON instead. Reply in Hindi.")
        entries = {e["key"]: e for e in memory.entries()}
        self.assertNotIn("Reply", entries["export_format"]["source"]["quote"])
        self.assertNotIn("CSV", entries["response_language"]["source"]["quote"])
        self.assertEqual(memory.pending(), [])

    def test_unrelated_document_and_guide_subjects_do_not_dispute_preferences(self):
        for text in ("The export format documentation is too awkward.",
                     "The response style guide is too long.",
                     "My CSV parser is too slow.",
                     "JSON is too awkward for the response language."):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                self.append(memory, history, text)
                self.assertEqual({k: v for k, v in self.values(memory).items() if k in before}, before)
                self.assertEqual(memory.pending(), [])
                self.assertFalse(any(e["disputed"] for e in memory.snapshot()["entries"]))

    def test_quoted_reported_hypothetical_temporary_and_negative_controls(self):
        controls = (
            '\"CSV is too awkward for exports; use JSON instead.\"',
            "`My export format is not CSV anymore.`",
            "> My response language no longer suits me.",
            "My teammate said CSV is too awkward for exports; use JSON instead.",
            "The document says CSV is too awkward for exports; use JSON instead.",
            "If CSV is too awkward for exports, use JSON instead.",
            "Maybe CSV is too awkward for exports; use JSON instead.",
            "The export format is too cumbersome if needed.",
            "For this response, the export format is too cumbersome; use JSON instead.",
            "Stop using CSV for exports for this single download only.",
            "Do not change my export format; CSV is still correct.",
            "Do not stop using CSV for exports.",
            "I don't want you to stop using CSV for exports.",
            "Translate this: my export format is not CSV anymore.",
            "Would CSV be too awkward for exports?",
            "CSV is too awkward for exports. Is JSON better?",
        )
        for text in controls:
            with self.subTest(text=text):
                memory, history = self.memory()
                before = memory.snapshot()
                self.append(memory, history, text)
                self.assertEqual(memory.snapshot(), before)

    def test_unbounded_temporary_rejection_requires_clarification(self):
        # A day/session/"for now" lifetime cannot be guessed from a completed turn.
        memory, history = self.memory()
        self.append(memory, history, "My export format no longer works for me for now; choose JSON.")
        self.assertEqual(self.values(memory)["export_format"], "CSV")
        self.assertEqual(memory.pending()[0]["source"]["scope"], "unsupported_lifetime")

    def test_exposed_independent_english_cases_are_now_development_regression(self):
        path = Path(__file__).resolve().parents[1] / "evaluations" / "instructions-v0.18-independent.json"
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            if case["language"] != "en":
                continue
            with self.subTest(case=case["id"]):
                store = RelayStore()
                self.addCleanup(store.close)
                memory = AutomaticMemory(store, store.create_session("Exposed regression")["id"])
                history = [user(text) for text in case["before"]]
                memory.process(history)
                before = self.values(memory)
                self.append(memory, history, case["text"])
                if case["kind"] == "clear":
                    self.assertEqual(self.values(memory), before | case["expected"])
                    self.assertEqual(memory.pending(), [])
                else:
                    self.assertEqual(self.values(memory), before)
                    targets = set(case.get("targets", []))
                    self.assertEqual({p["key"] for p in memory.pending()}, targets)
                    self.assertTrue(all(p["value"] is None for p in memory.pending()))

    def test_personal_comparatives_generalize_with_old_value_checks(self):
        for text, key, expected, mismatch in (
            ("YAML would suit my exports better than CSV.", "export_format", "YAML", False),
            ("Spanish would suit our reply language better than English.", "response_language", "Spanish", False),
            ("Concise suits my response style better than detailed.", "response_style", "concise", False),
            ("XML would suit my exports better than JSON.", "export_format", "XML", True),
        ):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                self.append(memory, history, text)
                self.assertEqual(self.values(memory), before if mismatch else before | {key: expected})
                if mismatch:
                    self.assertEqual(memory.pending()[0]["reason"], "old_value_mismatch")
                else:
                    self.assertEqual(memory.pending(), [])

    def test_subject_first_desires_keep_temporal_subject_wrappers(self):
        for prefix in ("I'd rather have", "I would rather have", "I want", "I prefer"):
            for subject in ("all future replies", "my later answers", "our responses"):
                with self.subTest(prefix=prefix, subject=subject):
                    memory, history = self.memory()
                    self.append(memory, history, f"{prefix} {subject} in Marathi.")
                    self.assertEqual(self.values(memory)["response_language"], "Marathi")
                    self.assertEqual(memory.pending(), [])

    def test_functional_subjects_resolve_only_the_named_preference(self):
        for text, key, value in (
            ("The language you usually respond in is no longer what I prefer.", "response_language", None),
            ("I need to change the amount of detail you include; I haven't chosen the replacement.", "response_style", None),
            ("Tamil is the language I would like you to use in later answers.", "response_language", "Tamil"),
            ("YAML is the export format I'd like you to use.", "export_format", "YAML"),
        ):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                self.append(memory, history, text)
                self.assertEqual(self.values(memory), before if value is None else before | {key: value})
                if value is None:
                    self.assertEqual([p["key"] for p in memory.pending()], [key])
                    self.assertIsNone(memory.pending()[0]["value"])
                else:
                    self.assertEqual(memory.pending(), [])

    def test_historical_subject_first_assertion_grounds_a_pronoun_replacement(self):
        for old, mismatch in (("CSV", False), ("JSON", True)):
            with self.subTest(old=old):
                memory, history = self.memory()
                self.append(memory, history, f"For exports, the previous choice was {old}; make it TOML permanently.")
                self.assertEqual(self.values(memory)["export_format"], "CSV" if mismatch else "TOML")
                if mismatch:
                    self.assertEqual(memory.pending()[0]["reason"], "old_value_mismatch")
                else:
                    self.assertEqual(memory.pending(), [])

    def test_response_bundles_update_only_style_and_language_with_exact_sources(self):
        for text in ("For later sessions, concise responses in Tamil are what I prefer.",
                     "For all future conversations, brief answers in German are what I want.",
                     "I want brief replies in Marathi."):
            with self.subTest(text=text):
                memory, history = self.memory()
                self.append(memory, history, text)
                self.assertEqual(memory.pending(), [])
                self.assertEqual(self.values(memory)["export_format"], "CSV")
                for key in ("response_style", "response_language"):
                    self.assertEqual(next(e for e in memory.entries() if e["key"] == key)["source"]["quote"], text)

    def test_qualitative_style_request_needs_a_value_or_stays_uncertain(self):
        for text in ("My usual answers could use more detail.",
                     "My future replies could use less detail; I'll clarify the replacement soon."):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                self.append(memory, history, text)
                self.assertEqual(self.values(memory), before)
                self.assertEqual([p["key"] for p in memory.pending()], ["response_style"])
                self.assertIsNone(memory.pending()[0]["value"])

    def test_rejected_literal_targets_matching_finite_settings_not_custom_data(self):
        memory, history = self.memory(("Set output format to CSV.", "Set project code to CSV."))
        self.append(memory, history, "CSV no longer works for me; I haven't settled on replacements.")
        self.assertEqual({p["key"] for p in memory.pending()}, {"export_format", "output_format"})
        self.assertFalse(next(e for e in memory.snapshot()["entries"] if e["key"] == "project_code")["disputed"])

    def test_elliptical_rejection_requires_an_explicit_prior_rejection(self):
        memory, history = self.memory()
        before = memory.snapshot()
        self.append(memory, history, "Detailed replies don't either.")
        self.assertEqual(memory.snapshot(), before)
        self.append(memory, history, "CSV no longer works for me, and detailed replies don't either. I'll specify replacements later.")
        self.assertEqual({p["key"] for p in memory.pending()}, {"export_format", "response_style"})

    def test_new_desire_forms_keep_reported_hypothetical_temporary_and_negation_boundaries(self):
        controls = (
            "JSON would suit exports better than CSV.",
            "If needed, JSON would suit my exports better than CSV.",
            "Maybe I'd rather have all future replies in Hindi.",
            "My teammate says JSON would suit my exports better than CSV.",
            "The report says detailed replies in Hindi are what I want.",
            "For this conversation only, I'd rather have replies in Hindi.",
            "I don't want detailed replies in Hindi.",
            "The old choice was CSV; do not make it JSON.",
            "If my usual answers could use more detail, what would happen?",
            "Translate this: French is the language I'd like you to use in future replies.",
            "For future conversations, detailed answers in Spanish are what my colleague wants.",
        )
        for text in controls:
            with self.subTest(text=text):
                memory, history = self.memory()
                before = memory.snapshot()
                self.append(memory, history, text)
                self.assertEqual(memory.snapshot(), before)

    def test_long_whitespace_keeps_matching_bounded_and_original_evidence_exact(self):
        script = textwrap.dedent(r"""
            import sys
            sys.path.insert(0, sys.argv[1])
            from context_relay import AutomaticMemory, MemoryPolicy, RelayStore
            cases = [
                ("use " + " " * 15995 + "X", {"export_format": "CSV", "response_language": "English"}),
                ("use " + "\t" * 15995 + "X", {"export_format": "CSV", "response_language": "English"}),
                ("Export " + " " * 15975 + "as JSON.", {"export_format": "JSON", "response_language": "English"}),
                ("Reply " + "\t" * 15975 + "in Hindi.", {"export_format": "CSV", "response_language": "Hindi"}),
                ("Export as JSON." + "\n" * 15955 + "Reply in Hindi.", {"export_format": "JSON", "response_language": "Hindi"}),
                ("\n" * 15900 + "\u2003Translate: Reply in Hindi.", {"export_format": "CSV", "response_language": "English"}),
                ("\r" * 15900 + "\u2003> Reply in Hindi.", {"export_format": "CSV", "response_language": "English"}),
            ]
            for text, expected in cases:
                with RelayStore() as store:
                    memory = AutomaticMemory(store, store.create_session("Whitespace regression")["id"],
                                             policy=MemoryPolicy(max_chars=40000))
                    history = [{"role": "user", "content": t} for t in ("Export as CSV.", "Reply in English.", text)]
                    memory.process(history)
                    assert {e["key"]: e["value"] for e in memory.entries()} == expected
                    assert memory.pending() == []
                    for entry in memory.entries():
                        source = entry["source"]
                        assert source["quote"] == history[source["index"]]["content"][source["start"]:source["end"]]
            print("passed seven max-length whitespace cases")
        """)
        result = subprocess.run([sys.executable, "-c", script, str(Path(__file__).resolve().parents[1] / "src")],
                                capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("passed seven", result.stdout)

    def test_separator_guards_preserve_commas_custom_spacing_and_provenance(self):
        for separator in (" ", ", ", " , ", "\t,\t"):
            with self.subTest(separator=separator):
                memory, history = self.memory()
                text = "Export as JSON" + separator + "going forward."
                self.append(memory, history, text)
                self.assertEqual(self.values(memory)["export_format"], "JSON")
                self.assertEqual(memory.pending(), [])
        memory, history = self.memory()
        self.append(memory, history, "Export as JSON  ,  and reply in Hindi.")
        self.assertEqual(self.values(memory)["export_format"], "JSON")
        self.assertEqual(self.values(memory)["response_language"], "Hindi")
        self.append(memory, history, "Set project name to Sunrise  Industries.")
        self.assertEqual(self.values(memory)["project_name"], "Sunrise  Industries")

    def test_planner_never_assigns_an_unbound_pronoun_from_an_earlier_turn(self):
        memory, _ = self.memory()
        entries = {entry["key"]: entry for entry in memory.entries()}
        for text in ("That one is wrong; use JSON instead.", "CSV is too awkward; use JSON instead."):
            with self.subTest(text=text):
                plan = plan_corrections(text, entries, subjects=_SETTING_SUBJECTS,
                                        values=_FINITE_SETTINGS, extract=_extract,
                                        request_body=_request_body, polite_question=_polite_question)
                self.assertIsNone(plan)


if __name__ == "__main__":
    unittest.main()
