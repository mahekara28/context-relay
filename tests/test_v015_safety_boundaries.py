"""Development safety invariants for composed corrections, without model calls."""

import unittest

from context_relay import AutomaticMemory, MemoryConflictError, RelayStore


def user(text):
    return {"role": "user", "content": text}


class ComposedCorrectionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.addCleanup(self.store.close)

    def memory(self, *seed):
        memory = AutomaticMemory(self.store, self.store.create_session("Planner safety")["id"])
        history = [user(text) for text in seed]
        memory.process(history)
        return memory, history

    def values(self, memory):
        return {entry["key"]: entry["value"] for entry in memory.entries()}

    def assert_integrity(self, memory, history):
        for record in memory.entries() + memory.pending():
            source = record["source"]
            self.assertEqual(source["quote"], history[source["index"]]["content"][source["start"]:source["end"]])
        self.assertEqual(self.store.facts(memory.session_id), {})
        snapshot = memory.snapshot()
        self.assertFalse(snapshot["current_authority"])
        self.assertFalse(snapshot["trusted_instruction"])
        self.assertEqual(memory.process(history)["status"], "unchanged")
        self.assertEqual(memory.snapshot(), snapshot)

    def test_composed_replacements_check_the_asserted_old_value(self):
        # The matching arm proves that the mismatch arm is exercising supported
        # composition, rather than passing only because both requests are ignored.
        cases = (
            ("Export as {}.", "XML", "CSV", "export_format", "YAML",
             "You have XML recorded as my export format; please correct that to YAML."),
            ("Keep responses {}.", "brief", "detailed", "response_style", "concise",
             "The response-style entry should read concise, not brief."),
            ("Set the delivery window to {}.", "afternoon", "morning", "delivery_window", "evening",
             "Change my delivery window from afternoon to evening as the standing preference."),
        )
        for seed, asserted_old, actual_other, key, replacement, text in cases:
            for actual in (asserted_old, actual_other):
                with self.subTest(text=text, actual=actual):
                    memory, history = self.memory(seed.format(actual))
                    history.append(user(text))
                    memory.process(history)
                    if actual == asserted_old:
                        self.assertEqual(self.values(memory), {key: replacement})
                        self.assertEqual(memory.pending(), [])
                    else:
                        self.assertEqual(self.values(memory), {key: actual})
                        self.assertTrue(memory.pending())
                        self.assertTrue(all(p["reason"] == "old_value_mismatch" for p in memory.pending()))
                        self.assertEqual({target for p in memory.pending() for target in p["targets"]}, {key})
                    self.assert_integrity(memory, history)

    def test_saved_preference_correction_does_not_bypass_forbidden_frames(self):
        correction = "Please correct my saved response style: brief replaces detailed."
        controls = (
            f"My teammate requested this change: {correction}",
            f"If I ever changed my mind, {correction}",
            "Do not correct my saved response style: brief replaces detailed.",
            f'"{correction}"',
            f"`{correction}`",
            f"> {correction}",
            "Please correct my saved response style: brief replaces detailed for this one answer only.",
            "My saved response style has not changed. Keep detailed as my standing response-style preference.",
            "I considered changing my response style to brief, then rejected the idea. Keep detailed as my standing preference.",
        )
        for text in controls:
            with self.subTest(text=text):
                memory, history = self.memory("Keep responses detailed.")
                before = memory.snapshot()
                history.append(user(text))
                memory.process(history)
                self.assertEqual(memory.snapshot(), before)
                self.assert_integrity(memory, history)

    def test_report_scoped_saved_preference_requires_a_known_lifetime(self):
        # v0.23 recognizes this report scope instead of ignoring the request;
        # it still must not promote brief to a durable or one-turn preference.
        text = "For this report only, please correct my saved response style: brief replaces detailed."
        memory, history = self.memory("Keep responses detailed.")
        history.append(user(text))
        memory.process(history)
        self.assertEqual(self.values(memory), {"response_style": "detailed"})
        self.assertEqual(len(memory.pending()), 1)
        pending = memory.pending()[0]
        self.assertEqual(pending["targets"], ["response_style"])
        self.assertEqual(pending["reason"], "unparsed_correction")
        self.assertIsNone(pending["value"])
        self.assertEqual(pending["source"]["scope"], "unsupported_lifetime")
        self.assertEqual(pending["source"]["quote"], text)
        self.assertTrue(memory.effective_snapshot()["entries"][0]["disputed"])
        self.assertNotIn("turn_scope", memory.effective_snapshot())
        with self.assertRaises(MemoryConflictError):
            memory.confirm(pending["id"])
        self.assert_integrity(memory, history)
        history.append(user("Continue working."))
        memory.process(history)
        self.assertEqual(memory.pending()[0]["id"], pending["id"])
        self.assertTrue(memory.effective_snapshot()["entries"][0]["disputed"])
        history.append(user("Keep responses detailed."))
        memory.process(history)
        self.assertEqual(memory.pending(), [])
        self.assertFalse(memory.effective_snapshot()["entries"][0]["disputed"])
        self.assert_integrity(memory, history)

    def test_two_same_domain_targets_do_not_supply_a_unique_pronoun_target(self):
        cases = (
            "You have CSV recorded as my export format and XML as my output format; please correct that to JSON.",
            "My export format and output format both need correcting. Please change one of them to JSON.",
        )
        for text in cases:
            with self.subTest(text=text):
                memory, history = self.memory("Export as CSV.", "Set the output format to XML.")
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assertTrue(memory.pending())
                self.assertEqual({entry["key"] for entry in memory.snapshot()["entries"] if entry["disputed"]},
                                 {"export_format", "output_format"})
                for proposal in memory.pending():
                    self.assertLessEqual(set(proposal["targets"]), {"export_format", "output_format"})
                    if proposal["value"] is not None:
                        self.assertIsNone(proposal["key"])
                        self.assertEqual(set(proposal["targets"]), {"export_format", "output_format"})
                self.assert_integrity(memory, history)

    def test_composition_preserves_unmarked_declaration_review(self):
        for text in ("The deployment region is eu-west-2.",
                     "The deployment region is eu-west-2. The existing entry is correct."):
            with self.subTest(text=text):
                memory, history = self.memory("The deployment region is us-east-1.")
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), {"deployment_region": "us-east-1"})
                self.assertEqual(len(memory.pending()), 1)
                proposal = memory.pending()[0]
                self.assertEqual(proposal["reason"], "conflicting_declaration")
                self.assertEqual(proposal["targets"], ["deployment_region"])
                self.assertEqual(proposal["value"], "eu-west-2")
                self.assert_integrity(memory, history)

    def test_historical_qualifiers_are_not_current_corrections(self):
        for seed, text in (
            ("Export as CSV.", "My old export format is JSON."),
            ("Reply in English.", "My earlier response language is Hindi."),
        ):
            with self.subTest(text=text):
                memory, history = self.memory(seed)
                before = memory.snapshot()
                history.append(user(text))
                memory.process(history)
                self.assertEqual(memory.snapshot(), before)
                self.assert_integrity(memory, history)

        # The same word can be part of an explicitly named, existing custom key.
        memory, history = self.memory("Set the old deployment region to us-east-1.")
        history.append(user("Set the old deployment region to eu-west-2."))
        memory.process(history)
        self.assertEqual(self.values(memory), {"old_deployment_region": "eu-west-2"})
        self.assertEqual(memory.pending(), [])
        self.assert_integrity(memory, history)

    def test_uncertain_plan_supersedes_fully_parsed_generic_alias_capture(self):
        for text, target in (
            ("My saved export format is wrong.", "export_format"),
            ("The output-format setting is outdated.", "output_format"),
        ):
            with self.subTest(text=text):
                memory, history = self.memory("Export as CSV.", "Set the output format to XML.")
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assertEqual({entry["key"] for entry in memory.snapshot()["entries"] if entry["disputed"]}, {target})
                self.assertEqual({key for proposal in memory.pending() for key in proposal["targets"]}, {target})
                self.assertTrue(all(p["value"] is None and p["reason"] == "unparsed_correction" for p in memory.pending()))
                self.assert_integrity(memory, history)

    def test_unrelated_unsupported_sibling_prevents_partial_promotion(self):
        correction = "For future exports, make JSON the default in place of CSV."
        unrelated = "The research backlog needs triage."
        for text in (f"{correction} {unrelated}", f"{unrelated} {correction}"):
            with self.subTest(text=text):
                memory, history = self.memory("Export as CSV.")
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assert_integrity(memory, history)

    def test_composition_retains_ambiguity_from_the_existing_output_alias(self):
        for text in ("I want the output in JSON.",
                     "I want the output in JSON. Please remember that."):
            with self.subTest(text=text):
                memory, history = self.memory("Export as CSV.", "Set the output format to XML.")
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assertTrue(memory.pending())
                self.assertEqual({entry["key"] for entry in memory.snapshot()["entries"] if entry["disputed"]},
                                 {"export_format", "output_format"})
                for proposal in memory.pending():
                    if proposal["value"] is not None:
                        self.assertIsNone(proposal["key"])
                        self.assertEqual(set(proposal["targets"]), {"export_format", "output_format"})
                self.assert_integrity(memory, history)

    def test_unchanged_sibling_is_not_part_of_the_disputed_target_set(self):
        cases = (
            (("Reply in English.", "Set the output format to CSV."),
             "My response language is fine. My output format needs replacing as a standing preference, but I have not selected the new format.",
             "response_language", "output_format"),
            (("Export as CSV.", "Keep responses detailed."),
             "My export format remains correct. My response style needs to change. I haven't specified the replacement.",
             "export_format", "response_style"),
        )
        for seed, text, unchanged, changed in cases:
            with self.subTest(text=text):
                memory, history = self.memory(*seed)
                before = self.values(memory)
                history.append(user(text))
                memory.process(history)
                self.assertEqual(self.values(memory), before)
                self.assertEqual({target for p in memory.pending() for target in p["targets"]}, {changed})
                self.assertTrue(all(p["value"] is None for p in memory.pending()))
                entries = {entry["key"]: entry for entry in memory.snapshot()["entries"]}
                self.assertFalse(entries[unchanged]["disputed"])
                self.assertTrue(entries[changed]["disputed"])
                self.assert_integrity(memory, history)

    def test_reaffirmation_support_text_never_becomes_a_generic_memory_key(self):
        memory, history = self.memory("Reply in Hindi.")
        before = memory.snapshot()
        history.append(user("Please keep Hindi as my response language. The existing entry is correct."))
        memory.process(history)
        self.assertEqual(memory.snapshot(), before)
        self.assert_integrity(memory, history)

    def test_forget_and_resume_do_not_replay_a_composed_correction(self):
        memory, history = self.memory("Export as CSV.")
        history.append(user("You have CSV recorded as my export format; please correct that to YAML."))
        memory.process(history)
        self.assertEqual(self.values(memory), {"export_format": "YAML"})
        memory.forget("export_format")
        resumed = AutomaticMemory(self.store, memory.session_id)
        self.assertEqual(resumed.process(history)["status"], "unchanged")
        self.assertEqual(resumed.entries(), [])
        self.assertEqual(resumed.pending(), [])

    def test_dismiss_and_resume_do_not_replay_a_composed_invalidation(self):
        memory, history = self.memory("Set the output format to CSV.")
        history.append(user("My output format needs replacing as a standing preference, but I have not selected the new format."))
        memory.process(history)
        self.assertTrue(memory.pending())
        self.assertTrue(all(p["value"] is None for p in memory.pending()))
        for proposal in memory.pending():
            memory.dismiss(proposal["id"])
        resumed = AutomaticMemory(self.store, memory.session_id)
        self.assertEqual(resumed.process(history)["status"], "unchanged")
        self.assertEqual(resumed.pending(), [])
        self.assertFalse(resumed.snapshot()["entries"][0]["disputed"])


if __name__ == "__main__":
    unittest.main()
