"""Known-case regression and varied setting/intent boundaries; no model calls."""

import json
from pathlib import Path
import unittest

from context_relay import AutomaticMemory, MemoryConflictError, MemoryInterpreter, MemoryPolicy, RelayStore


def user(text):
    return {"role": "user", "content": text}


class CorrectionIntentTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.addCleanup(self.store.close)

    def memory(self, seed=("Export as CSV.", "Reply in English.", "Keep responses detailed."), **options):
        memory = AutomaticMemory(self.store, self.store.create_session("Intent regression")["id"], **options)
        history = [user(text) for text in seed]
        memory.process(history)
        return memory, history

    def values(self, memory):
        return {entry["key"]: entry["value"] for entry in memory.entries()}

    def check_sources_and_replay(self, memory, history):
        for record in memory.entries() + memory.pending():
            source = record["source"]
            self.assertEqual(source["quote"], history[source["index"]]["content"][source["start"]:source["end"]])
        snapshot = memory.snapshot()
        self.assertFalse(snapshot["current_authority"])
        self.assertFalse(snapshot["trusted_instruction"])
        self.assertEqual(self.store.facts(memory.session_id), {})
        self.assertEqual(memory.process(history)["status"], "unchanged")
        self.assertEqual(memory.snapshot(), snapshot)

    def test_previous_v014_corpus_is_now_development_regression(self):
        self.check_corpus("corrections-v0.14-holdout.json")

    def test_previously_unseen_v015_corpus_is_now_development_regression(self):
        self.check_corpus("corrections-v0.15-fresh.json")

    def check_corpus(self, filename):
        path = Path(__file__).resolve().parents[1] / "evaluations" / filename
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            with self.subTest(case=case["id"], text=case["text"]):
                memory, history = self.memory(case["before"])
                before = self.values(memory)
                history.append(user(case["text"]))
                memory.process(history)
                if case["kind"] == "clear":
                    self.assertEqual(self.values(memory), before | case["expected"])
                    self.assertEqual(memory.pending(), [])
                else:
                    self.assertEqual(self.values(memory), before)
                    if case["kind"] == "uncertain":
                        self.assertEqual({p["key"] for p in memory.pending()}, set(case["targets"]))
                        self.assertTrue(all(p["value"] is None for p in memory.pending()))
                        self.assertEqual({e["key"] for e in memory.snapshot()["entries"] if e["disputed"]}, set(case["targets"]))
                    elif case["id"] == "v015-abstain-04":
                        # Keep the historical corpus unchanged. v0.23 recognizes
                        # report scope but cannot infer its end, so the default
                        # stays CSV and its use pauses for scope clarification.
                        self.assertEqual(self.values(memory)["export_format"], "CSV")
                        self.assertEqual(len(memory.pending()), 1)
                        pending = memory.pending()[0]
                        self.assertEqual(pending["targets"], ["export_format"])
                        self.assertEqual(pending["reason"], "unparsed_correction")
                        self.assertIsNone(pending["value"])
                        self.assertEqual(pending["source"]["scope"], "unsupported_lifetime")
                        self.assertEqual(pending["source"]["quote"], "For this report only, export as JSON;")
                        self.assertEqual({e["key"] for e in memory.snapshot()["entries"] if e["disputed"]}, {"export_format"})
                        self.assertNotIn("turn_scope", memory.effective_snapshot())
                        with self.assertRaises(MemoryConflictError):
                            memory.confirm(pending["id"])
                    else:
                        self.assertEqual(memory.pending(), [])
                self.check_sources_and_replay(memory, history)

    def test_custom_values_do_not_inherit_response_suffix_normalization(self):
        memory, history = self.memory(("Set the delivery window to morning.",))
        history.append(user("Change my delivery window from morning to evening responses as the standing preference."))
        memory.process(history)
        self.assertEqual(self.values(memory), {"delivery_window": "morning"})
        self.assertFalse(any(p["value"] == "evening" for p in memory.pending()))
        self.check_sources_and_replay(memory, history)

    def test_historical_roles_never_promote_in_any_assignment_orientation(self):
        for qualifier in ("old", "earlier", "previous"):
            for text in (f"JSON is my {qualifier} export format.",
                         f"My {qualifier} export format is JSON.",
                         f"Save JSON as my {qualifier} export format.",
                         f"Make JSON my {qualifier} export format."):
                with self.subTest(text=text):
                    memory, history = self.memory(("Export as CSV.",))
                    before = memory.snapshot()
                    history.append(user(text))
                    memory.process(history)
                    self.assertEqual(memory.snapshot(), before)
                    self.check_sources_and_replay(memory, history)

    def test_custom_literal_suffix_words_are_never_silently_removed(self):
        for value in ("Born Again", "Selected By Default"):
            with self.subTest(value=value):
                memory, history = self.memory(("Set the project name to Sunrise.",))
                history.append(user(f"Set the project name to {value}. Please remember that."))
                memory.process(history)
                self.assertEqual(self.values(memory), {"project_name": "Sunrise"})
                self.assertEqual({p["key"] for p in memory.pending()}, {"project_name"})
                self.assertTrue(all(p["value"] is None for p in memory.pending()))
                self.check_sources_and_replay(memory, history)

    def test_exact_custom_subject_beats_qualifier_stripping(self):
        for base, qualified, old, new in (
            ("delivery window", "saved delivery window", "afternoon", "evening"),
            ("region", "default region", "west", "east"),
            ("model", "current model", "small", "large"),
        ):
            for text in (f"Set the {qualified} to {new}.",
                         f"Please change my {qualified} from {old} to {new} as the standing preference."):
                with self.subTest(text=text):
                    memory, history = self.memory((f"Set the {base} to original.", f"Set the {qualified} to {old}."))
                    history.append(user(text))
                    memory.process(history)
                    self.assertEqual(self.values(memory), {base.replace(" ", "_"): "original", qualified.replace(" ", "_"): new})
                    self.assertEqual(memory.pending(), [])
                    self.check_sources_and_replay(memory, history)

    def test_saved_finite_alias_does_not_create_an_unrelated_generic_field(self):
        memory, history = self.memory()
        history.append(user("Set my saved response language to Hindi."))
        memory.process(history)
        self.assertEqual(self.values(memory), {"export_format": "CSV", "response_language": "Hindi", "response_style": "detailed"})
        self.assertEqual(memory.pending(), [])
        self.check_sources_and_replay(memory, history)

    def test_composed_sources_include_dependencies_but_not_unrelated_siblings(self):
        memory, history = self.memory()
        text = "You have CSV recorded as my export format; please correct that to XML. My standing response language is now Tamil."
        history.append(user(text))
        memory.process(history)
        entries = {e["key"]: e for e in memory.entries()}
        self.assertEqual(entries["export_format"]["value"], "XML")
        self.assertEqual(entries["response_language"]["value"], "Tamil")
        self.assertIn("CSV recorded", entries["export_format"]["source"]["quote"])
        self.assertNotIn("response language", entries["export_format"]["source"]["quote"])
        self.assertNotIn("CSV", entries["response_language"]["source"]["quote"])
        self.check_sources_and_replay(memory, history)

    def test_composed_setting_paraphrases_generalize_across_values(self):
        cases = (
            ("For every export in the future, XML is what I prefer.", "export_format", "XML"),
            ("For every output format going forward, YAML is what I need.", "output_format", "YAML"),
            ("Please update our reply language: Tamil.", "response_language", "Tamil"),
            ("Update the default response style: concise.", "response_style", "concise"),
            ("The default reply language needs to become Marathi.", "response_language", "Marathi"),
            ("Our export preference must become TOML.", "export_format", "TOML"),
            ("From this point onwards, respond to me in French.", "response_language", "French"),
            ("As of now, answer me in Bengali.", "response_language", "Bengali"),
            ("Our preference about exports is YAML.", "export_format", "YAML"),
            ("My preference for later answers is brief.", "response_style", "brief"),
            ("My preference for future replies is Urdu.", "response_language", "Urdu"),
            ("Going forward, concise is the reply style I need.", "response_style", "concise"),
            ("JSONL is the export format I prefer.", "export_format", "JSONL"),
            ("For later replies choose Japanese.", "response_language", "Japanese"),
            ("For future responses choose short.", "response_style", "short"),
            ("Set exports to JSON.", "export_format", "JSON"),
            ("Set reply language to Hindi.", "response_language", "Hindi"),
            ("Set our reply style to brief.", "response_style", "brief"),
        )
        for text, key, value in cases:
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before | {key: value})
                self.assertEqual(memory.pending(), [])
                self.check_sources_and_replay(memory, history)

    def test_reconsideration_pair_grounds_only_its_named_setting_and_source(self):
        for text, key, value in (
            ("I have rethought the response language; please choose Spanish permanently.", "response_language", "Spanish"),
            ("I've reconsidered our export preference. Go with TSV from now on.", "export_format", "TSV"),
            ("I reconsidered the response style; use concise.", "response_style", "concise"),
        ):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before | {key: value})
                self.assertEqual(memory.pending(), [])
                self.assertEqual(next(e for e in memory.entries() if e["key"] == key)["source"]["quote"], text)
                self.check_sources_and_replay(memory, history)

    def test_old_value_checks_stay_bound_to_the_correct_setting(self):
        for text, key, value, mismatch in (
            ("Instead of CSV for exports, choose YAML.", "export_format", "YAML", False),
            ("Rather than French for reply language, please use Tamil.", "response_language", "Tamil", True),
            ("Switch all future responses from English to Hindi.", "response_language", "Hindi", False),
            ("Change my later replies from brief to short.", "response_style", "short", True),
        ):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before if mismatch else before | {key: value})
                if mismatch:
                    self.assertEqual(memory.pending()[0]["reason"], "old_value_mismatch")
                    self.assertEqual(memory.pending()[0]["targets"], [key])
                else:
                    self.assertEqual(memory.pending(), [])
                self.check_sources_and_replay(memory, history)

    def test_changed_preferences_and_scoped_old_value_rejection_need_clarification(self):
        for text, key in (
            ("Our reply language preference has changed; I'll specify the new value later.", "response_language"),
            ("My response style has changed; I will clarify the replacement soon.", "response_style"),
            ("Change the export format; the previous setting is no longer suitable for me.", "export_format"),
            ("Switch our response style; the current choice no longer suits me.", "response_style"),
            ("My export preference no longer works for me.", "export_format"),
            ("I have reconsidered our response language.", "response_language"),
        ):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assertEqual([p["key"] for p in memory.pending()], [key])
                pending = memory.pending()[0]
                self.assertIsNone(pending["value"])
                self.assertEqual(pending["reason"], "unparsed_correction")
                self.assertEqual(pending["source"]["quote"], text)
                with self.assertRaises(MemoryConflictError):
                    memory.confirm(pending["id"])
                self.check_sources_and_replay(memory, history)

    def test_subject_targeting_excludes_setting_names_inside_custom_subjects_or_values(self):
        cases = (
            ("Please adjust the timeout for exports by 5 seconds.", {"timeout_for_exports"}),
            ("Please make the timeout for exports easier to configure.", {"timeout_for_exports"}),
            ("Change response language to the one used for exports.", {"response_language"}),
            ("Use whatever suits the output for the response language.", {"response_language"}),
            ("Please adjust the timeout for imports by 5 seconds.", set()),
            ("Please make export format documentation easier to read.", set()),
            ("Please make the response style guide easier to follow.", set()),
            ("Change response language to the one used for exports and output format.", {"response_language"}),
        )
        for text, targets in cases:
            with self.subTest(text=text):
                memory, history = self.memory(("Export as CSV.", "Reply in English.", "Set the timeout for exports to 30 seconds."))
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assertEqual({p["key"] for p in memory.pending()}, targets)
                self.check_sources_and_replay(memory, history)

    def test_value_first_choices_and_update_requests_dispute_each_affected_setting(self):
        for verb in ("choose", "keep", "go with", "use"):
            with self.subTest(verb=verb):
                memory, history = self.memory()
                before = self.values(memory)
                history.append(user(f"Export as JSON and {verb} either Hindi or Tamil for the response language."))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assertEqual({p["key"] for p in memory.pending()}, {"export_format", "response_language"})
                self.assertTrue(all(p["value"] is None for p in memory.pending()))
                self.check_sources_and_replay(memory, history)
        memory, history = self.memory()
        history.append(user("Update reply language to something appropriate."))
        memory.process(history)
        self.assertEqual([p["key"] for p in memory.pending()], ["response_language"])
        self.assertIsNone(memory.pending()[0]["value"])

    def test_known_longer_subject_wins_even_when_it_contains_a_reserved_setting_name(self):
        memory, history = self.memory(("Keep responses detailed.", "Set the response style guide to the original edition."))
        history.append(user("Please make the response style guide easier to follow."))
        memory.process(history)
        self.assertEqual([p["key"] for p in memory.pending()], ["response_style_guide"])
        self.assertFalse(next(e for e in memory.snapshot()["entries"] if e["key"] == "response_style")["disputed"])

    def test_interpreter_same_value_wrong_key_or_abstention_cannot_hide_uncertainty(self):
        for proposals in ([], [("export_format", " csv ")], [("unrelated", "something")]):
            with self.subTest(proposals=proposals):
                memory, history = self.memory()
                def interpret(request):
                    return [{"key": key, "value": value, "quote": request["text"], "start": 0, "end": len(request["text"])} for key, value in proposals]
                memory.interpreter = MemoryInterpreter(interpret)
                history.append(user("Please make exports easier to read."))
                memory.process(history)
                pending = next(p for p in memory.pending() if p["key"] == "export_format")
                self.assertIsNone(pending["value"])
                self.assertEqual(pending["reason"], "unparsed_correction")
                self.assertTrue(next(e for e in memory.snapshot()["entries"] if e["key"] == "export_format")["disputed"])
                self.check_sources_and_replay(memory, history)

    def test_repeated_current_value_interpretation_cannot_hide_later_uncertainty(self):
        memory, history = self.memory()
        memory.interpreter = MemoryInterpreter(lambda request: [
            {"key": "export_format", "value": "csv", "quote": request["text"], "start": 0, "end": len(request["text"])}])
        for text in ("Please make exports easier to read.", "Please make exports more useful."):
            history.append(user(text))
            memory.process(history)
        earlier_proposal = next(p for p in memory.pending() if p["value"] == "CSV")
        captured = set(earlier_proposal["clarifies"])
        history.append(user("Please make exports easier to share."))
        memory.process(history)
        later_flags = {p["id"] for p in memory.pending() if p["value"] is None} - captured
        self.assertEqual(len(later_flags), 2)
        memory.confirm(earlier_proposal["id"])
        self.assertEqual({p["id"] for p in memory.pending() if p["value"] is None}, later_flags)
        self.assertTrue(next(e for e in memory.snapshot()["entries"] if e["key"] == "export_format")["disputed"])
        self.check_sources_and_replay(memory, history)

    def test_deduplicated_replacement_interpretation_retains_the_later_unclear_source(self):
        memory, history = self.memory()
        memory.interpreter = MemoryInterpreter(lambda request: [
            {"key": "export_format", "value": "JSON", "quote": request["text"], "start": 0, "end": len(request["text"])}])
        history.append(user("Please make exports easier to read."))
        memory.process(history)
        first = memory.pending()[0]
        self.assertEqual(first["value"], "JSON")
        self.assertEqual(len(memory.pending()), 1)
        second_text = "Please make exports easier to share."
        history.append(user(second_text))
        memory.process(history)
        later = next(p for p in memory.pending() if p["value"] is None)
        self.assertEqual(later["source"]["quote"], second_text)
        memory.confirm(first["id"])
        self.assertEqual(self.values(memory)["export_format"], "JSON")
        self.assertEqual([p["id"] for p in memory.pending()], [later["id"]])
        self.assertTrue(next(e for e in memory.snapshot()["entries"] if e["key"] == "export_format")["disputed"])
        self.check_sources_and_replay(memory, history)

    def test_unfamiliar_literal_aliases_keep_review_and_ambiguity_boundaries(self):
        for text, target in (("Set exports to Parquet.", "export_format"),
                             ("Set reply language to Esperanto.", "response_language"),
                             ("Set the output to Parquet.", "export_format")):
            with self.subTest(text=text):
                memory, history = self.memory()
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assertEqual(memory.pending()[0]["targets"], [target])
                self.assertEqual(memory.pending()[0]["reason"], "unrecognized_value_needs_review")
        memory, history = self.memory(("Export as CSV.", "Set the output format to XML."))
        history.append(user("Set the output to Parquet."))
        memory.process(history)
        self.assertEqual(memory.pending()[0]["reason"], "ambiguous_target")
        self.assertEqual(set(memory.pending()[0]["targets"]), {"export_format", "output_format"})

    def test_misleading_near_matches_never_promote_or_dispute(self):
        controls = (
            "My preference for future replies is not Hindi.",
            "Our preference about the example exports is YAML.",
            "The instruction regarding future replies is Hindi.",
            "The following is a sample: my preference for replies is Hindi.",
            '"My preference for future replies is Hindi."',
            "My coworker has reconsidered the export format; please use JSON permanently.",
            "If I reconsidered the export format, please use JSON.",
            "I might reconsider the export format; please choose JSON.",
            "I have reconsidered nothing; please use JSON.",
            "I have reconsidered the export format; please explain JSON.",
            "My export format preference has not changed; I will explain it later.",
            "Switch the export format; do not change it yet.",
            "Do not switch the response language; the old one still works for me.",
            "My export format has changed for this response.",
            "Tomorrow, my export format preference may change.",
            "Is it true that my export format preference has changed?",
            "The document says my response style has changed.",
            "For every export, compare JSON and CSV.",
            "Instead of CSV for exports, could JSON be useful?",
        )
        for text in controls:
            with self.subTest(text=text):
                memory, history = self.memory()
                before = memory.snapshot()
                history.append(user(text))
                memory.process(history)
                self.assertEqual(memory.snapshot(), before)
                self.check_sources_and_replay(memory, history)

    def test_unknown_temporary_lifetime_requires_clarification(self):
        # v0.20 no longer silently ignores an understood setting with no known end.
        memory, history = self.memory()
        history.append(user("Our preference about exports is YAML for now."))
        memory.process(history)
        self.assertEqual(self.values(memory)["export_format"], "CSV")
        self.assertEqual(memory.pending()[0]["source"]["scope"], "unsupported_lifetime")
        self.assertTrue(next(row for row in memory.effective_snapshot()["entries"]
                             if row["key"] == "export_format")["disputed"])

    def test_compound_parse_atomicity_aliases_and_review_policy(self):
        memory, history = self.memory()
        history.append(user("Actually, exports: JSON; replies: Hindi."))
        memory.process(history)
        self.assertEqual(self.values(memory), {"export_format": "JSON", "response_language": "Hindi", "response_style": "detailed"})
        before = self.values(memory)
        history.append(user("Set exports to YAML and change the response language to the one we discussed."))
        memory.process(history)
        self.assertEqual(self.values(memory), before)
        self.assertEqual({p["key"] for p in memory.pending()}, {"export_format", "response_language"})
        self.assertTrue(all(p["value"] is None for p in memory.pending()))
        self.check_sources_and_replay(memory, history)

        memory, history = self.memory()
        memory.policy = MemoryPolicy(mode="review")
        history.append(user("My preference for future replies is Hindi."))
        memory.process(history)
        self.assertEqual(self.values(memory)["response_language"], "English")
        self.assertEqual(memory.pending()[0]["value"], "Hindi")
        memory.confirm(memory.pending()[0]["id"])
        self.assertEqual(self.values(memory)["response_language"], "Hindi")


if __name__ == "__main__":
    unittest.main()
