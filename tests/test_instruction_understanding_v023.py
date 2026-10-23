"""Bounded language recognition, ambiguity at dispatch, and review-only fallback."""
import itertools
import unittest

from context_relay import AutomaticMemory, ContextGuard, MemoryInterpreter, MemoryPolicy


def user(text):
    return {"role": "user", "content": text}


class InstructionUnderstandingTests(unittest.TestCase):
    def memory(self, *, seed=True, interpreter=None):
        guard = ContextGuard()
        self.addCleanup(guard.close)
        memory = AutomaticMemory(guard.store, guard.session_id,
                                 policy=MemoryPolicy(max_chars=12000), interpreter=interpreter)
        history = [user("Export as CSV. Reply in English. Keep responses detailed.")] if seed else []
        if history:
            memory.process(history)
        return guard, memory, history

    def append(self, memory, history, text):
        history.append(user(text))
        memory.process(history)
        for entry in memory.effective_snapshot()["entries"] + memory.pending():
            source = entry["source"]
            original = history[source["index"]]["content"]
            self.assertEqual(source["quote"], original[source["start"]:source["end"]])
        self.assertFalse(memory.snapshot()["current_authority"])
        self.assertEqual(memory.store.facts(memory.session_id), {})

    def test_additional_complete_imperatives_preserve_original_evidence(self):
        cases = (
            ("Please stick to JSON for exports.", "export_format", "JSON"),
            ("From now on, exports should come in JSON.", "export_format", "JSON"),
            ("I'd like you to export JSON.", "export_format", "JSON"),
            ("JSON me export kar do.", "export_format", "JSON"),
            ("Export format JSON kar dena.", "export_format", "JSON"),
            ("Abse Hindi mein baat karo.", "response_language", "Hindi"),
            ("French mein boliye.", "response_language", "French"),
            ("English ki jagah Hindi mein baat karo.", "response_language", "Hindi"),
            ("Jawab chhote rakha karo.", "response_style", "short"),
            ("अब से JSON में एक्सपोर्ट करो।", "export_format", "JSON"),
            ("जवाब छोटे रखना।", "response_style", "short"),
            ('अब से "हिंदी" में बात करो।', "response_language", "Hindi"),
        )
        for text, key, value in cases:
            with self.subTest(text=text):
                _, memory, history = self.memory()
                self.append(memory, history, text)
                self.assertEqual({e["key"]: e["value"] for e in memory.entries()}[key], value)
                self.assertEqual(memory.pending(), [])
                source = next(e for e in memory.entries() if e["key"] == key)["source"]
                self.assertEqual(source["index"], 1)

    def test_conflicting_direct_assignments_never_choose_the_last_value(self):
        for text, seed in itertools.product((
                "Export as JSON. Export as XML.",
                "Use JSON for exports and use XML for exports.",
                'Reply in "Hindi". Reply in "French".'), (True, False)):
            with self.subTest(text=text, seed=seed):
                guard, memory, history = self.memory(seed=seed)
                before = memory.entries()
                self.append(memory, history, text)
                self.assertEqual(memory.entries(), before)
                self.assertTrue(memory.pending())
                key = "response_language" if text.startswith("Reply") else "export_format"
                guard.allow_tool("deliver", memory_bindings={"setting": key})
                effects = []
                result = guard.turn().execute_with_recovery("deliver", {"setting": "XML"},
                    lambda tool, arguments: effects.append(arguments), repair="known")
                self.assertFalse(result["executed"])
                self.assertEqual(effects, [])
                self.append(memory, history, "Reply in Hindi." if key == "response_language" else "Export as JSON.")
                self.assertEqual(memory.pending(), [])

    def test_equivalent_repeated_values_are_not_conflicts(self):
        _, memory, history = self.memory()
        self.append(memory, history, 'Export as "JSON". Export as json.')
        self.assertEqual(memory.pending(), [])
        self.assertEqual(memory.entries()[0]["value"], "JSON")

    def test_unknown_or_hedged_changes_block_stale_repair(self):
        for text in ("Export format kuch aur kar dena.", "Export format shayad JSON karna.",
                     "निर्यात प्रारूप कुछ और रखना।", "Please stick to something else for exports.",
                     "Exports should come in a different format."):
            with self.subTest(text=text):
                guard, memory, history = self.memory()
                self.append(memory, history, text)
                self.assertEqual(memory.entries()[0]["value"], "CSV")
                self.assertTrue(memory.effective_snapshot()["entries"][0]["disputed"])
                guard.allow_tool("export", memory_bindings={"format": "export_format"})
                effects = []
                result = guard.turn().execute_with_recovery("export", {"format": "JSON"},
                    lambda tool, arguments: effects.append(arguments), repair="known")
                self.assertFalse(result["executed"])
                self.assertEqual(effects, [])

    def test_report_and_task_lifetimes_persist_as_questions_through_followups(self):
        cases = (
            "For this report, export as JSON.",
            "Keep using CSV normally, but use JSON just for this report.",
            "Export as CSV. For this report, export as JSON.",
            "For this task, export as JSON. Export as CSV.",
            "Export as JSON, but only for this report.",
            "Is report ke liye export format JSON rakho.",
            "इस काम के लिए निर्यात प्रारूप JSON रखो।",
        )
        for text in cases:
            with self.subTest(text=text):
                _, memory, history = self.memory()
                self.append(memory, history, text)
                self.assertEqual(memory.entries()[0]["value"], "CSV")
                self.assertEqual(memory.pending()[0]["source"]["scope"], "unsupported_lifetime")
                self.append(memory, history, "Continue working.")
                self.assertTrue(memory.effective_snapshot()["entries"][0]["disputed"])
                self.append(memory, history, "Export as CSV.")
                self.assertEqual(memory.pending(), [])

    def test_hindi_turn_exception_preserves_durable_value_and_continuation_is_uncertain(self):
        _, memory, history = self.memory()
        self.append(memory, history, 'सिर्फ इस जवाब के लिए "हिंदी" में जवाब दो।')
        values = {e["key"]: e for e in memory.effective_snapshot()["entries"]}
        self.assertEqual(values["response_language"]["value"], "Hindi")
        self.assertEqual(values["response_language"]["scope"], "turn")
        self.assertEqual({e["key"]: e["value"] for e in memory.entries()}["response_language"], "English")
        self.append(memory, history, "जारी रखो।")
        self.assertTrue(next(e for e in memory.effective_snapshot()["entries"]
                             if e["key"] == "response_language")["disputed"])
        self.append(memory, history, "Start a new reply.")
        self.assertEqual({e["key"]: e["value"] for e in memory.effective_snapshot()["entries"]}["response_language"], "English")

    def test_confirming_a_default_does_not_decide_a_same_message_task_boundary(self):
        _, memory, history = self.memory()
        memory.policy = MemoryPolicy(mode="review", max_chars=12000)
        self.append(memory, history, "Export as JSON. For this report, export as XML.")
        default = next(p for p in memory.pending() if p["value"] == "JSON")
        memory.confirm(default["id"])
        self.assertEqual(memory.entries()[0]["value"], "JSON")
        self.assertEqual(memory.pending()[0]["source"]["scope"], "unsupported_lifetime")
        self.assertTrue(memory.effective_snapshot()["entries"][0]["disputed"])

    def test_new_scope_and_conflict_paths_match_incremental_and_imported_history(self):
        histories = (
            ["Export as CSV. For this report, export as JSON.", "Continue working."],
            ['सिर्फ इस जवाब के लिए "हिंदी" में जवाब दो।', "जारी रखो।"],
            ["Export as JSON. Export as XML.", "Export as JSON."],
        )
        def semantic(view):
            return ([(e["key"], e["value"], e["disputed"], e.get("scope"),
                      e["source"]["quote"], e["source"]["index"]) for e in view["entries"]],
                    [(e["key"], e["value"], e["reason"]) for e in view["unresolved"]])
        for texts in histories:
            with self.subTest(texts=texts):
                _, incremental, history = self.memory()
                for text in texts:
                    self.append(incremental, history, text)
                _, imported, _ = self.memory(seed=False)
                imported.process(history)
                self.assertEqual(semantic(imported.effective_snapshot()), semantic(incremental.effective_snapshot()))
                snapshot = imported.effective_snapshot()
                imported.process(history)
                self.assertEqual(imported.effective_snapshot(), snapshot)

    def test_reported_conditional_and_quoted_controls_do_not_update_memory(self):
        cases = (
            'The ticket says "Please stick to JSON for exports."',
            "If approved, exports should come in JSON.",
            "Agar approval mile, JSON me export kar do.",
            "उसने कहा हिंदी में बात करो।",
            '"अब से JSON में एक्सपोर्ट करो।"',
            "Do not stick to JSON for exports.",
            "Export format JSON mat karna.",
        )
        for text in cases:
            with self.subTest(text=text):
                _, memory, history = self.memory()
                before = memory.entries()
                self.append(memory, history, text)
                self.assertEqual(memory.entries(), before)
                self.assertEqual(memory.pending(), [])

    def test_contrast_cannot_separate_a_scoped_request_from_its_framing(self):
        prefixes = (
            "The manager says use CSV normally",
            "If approved, keep using CSV normally",
            "Do not use CSV normally",
            'The manager says use "CSV" normally',
            'If approved, keep using "CSV" normally',
            'The ticket says "use CSV normally"',
            "Suppose we use CSV normally",
        )
        for prefix, lifetime in itertools.product(prefixes, ("export", "report")):
            with self.subTest(prefix=prefix, lifetime=lifetime):
                _, memory, history = self.memory()
                before = memory.effective_snapshot()
                self.append(memory, history, f"{prefix}, but use JSON for this {lifetime}.")
                self.assertEqual(memory.effective_snapshot(), before)
                self.assertEqual(memory.pending(), [])

    def test_contrast_after_a_complete_default_keeps_its_explicit_exception(self):
        for prefix in ("Use CSV normally", "Keep using CSV normally", "Continue using CSV by default",
                       "Export as CSV", 'Use "CSV" normally'):
            with self.subTest(prefix=prefix):
                _, memory, history = self.memory()
                self.append(memory, history, f"{prefix}, but use JSON for this export.")
                self.assertEqual(memory.entries()[0]["value"], "CSV")
                self.assertEqual(memory.effective_snapshot()["entries"][0]["value"], "JSON")
                self.assertEqual(memory.effective_snapshot()["entries"][0]["scope"], "turn")

    def test_unmatched_interpretation_is_opt_in_and_always_pending(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                calls = []
                def interpret(request):
                    calls.append(request)
                    text = request["text"]
                    return [{"key": "response_style", "value": "concise", "quote": text,
                             "start": 0, "end": len(text)}]
                _, memory, history = self.memory(interpreter=MemoryInterpreter(interpret, review_unmatched=enabled))
                text = "Kam shabdon mein seedhi baat karni hai. Thanks for checking."
                self.append(memory, history, text)
                self.assertEqual(len(calls), int(enabled))
                if enabled:
                    self.assertEqual(calls[0]["text"], text)
                    self.assertEqual(memory.pending()[0]["reason"], "interpreted_needs_review")
                    self.assertEqual({e["key"]: e["value"] for e in memory.entries()}["response_style"], "detailed")
                    memory.confirm(memory.pending()[0]["id"])
                    self.assertEqual({e["key"]: e["value"] for e in memory.entries()}["response_style"], "concise")

    def test_unmatched_interpreter_keeps_context_guards(self):
        calls = []
        _, memory, history = self.memory(interpreter=MemoryInterpreter(lambda request: calls.append(request) or [], review_unmatched=True))
        for text in ('"Make everything shorter."', "`Make everything shorter.`", "> Make everything shorter.",
                     'The ticket says "Make everything shorter."', "If approved, make everything shorter.",
                     "उसने कहा छोटे जवाब देना।", "Agar approved, jawab chhote karna.",
                     "For this task, make everything shorter.", "Is report ke liye jawab chhote rakho.",
                     "Set my API key to abc123."):
            with self.subTest(text=text):
                self.append(memory, history, text)
                self.assertEqual(calls, [])

    def test_empty_interpreter_result_cannot_clear_detected_ambiguity(self):
        calls = []
        _, memory, history = self.memory(interpreter=MemoryInterpreter(lambda request: calls.append(request) or [], review_unmatched=True))
        self.append(memory, history, "Make the export format easier to read.")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(memory.pending()[0]["value"])
        self.assertTrue(memory.effective_snapshot()["entries"][0]["disputed"])


if __name__ == "__main__":
    unittest.main()
