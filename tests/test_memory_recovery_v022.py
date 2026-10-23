"""Mixed-message corrections and chronological scope consumption at dispatch."""
import itertools
import unittest

from context_relay import AutomaticMemory, ContextGuard, MemoryPolicy


def user(text):
    return {"role": "user", "content": text}


def semantic_view(memory):
    """Compare behavior and exact provenance, excluding batch revision/hashes."""
    view = memory.effective_snapshot()
    def source(record):
        return {key: record["source"].get(key) for key in
                ("index", "quote", "start", "end", "scope", "method")}
    return {
        "standing": [(row["key"], row["value"], source(row)) for row in memory.entries()],
        "effective": [(row["key"], row["value"], row["disputed"], row.get("scope"), source(row))
                      for row in view["entries"]],
        "unresolved": [(row["key"], row["value"], row["reason"], source(row))
                       for row in view["unresolved"]],
        "turn": view.get("turn_scope", {}).get("index"),
    }


class MixedCorrectionRecoveryTests(unittest.TestCase):
    def memory(self):
        guard = ContextGuard()
        self.addCleanup(guard.close)
        memory = AutomaticMemory(guard.store, guard.session_id)
        history = [user("Export as CSV. Reply in English.")]
        memory.process(history)
        guard.allow_tool("export", memory_bindings={"format": "export_format"})
        return guard, memory, history

    def test_clear_correction_with_bounded_acknowledgment_reaches_actual_callback(self):
        for text, repair in itertools.product((
                "Export as JSON. Thanks, this will help.",
                "Thank you, that is helpful. Export as JSON.",
                "Export as JSON. Thanks for checking.",
                "Thanks for helping. Export as JSON.",
                "Export as JSON and reply in Hindi. Thanks, that helps!"), (None, "known")):
            with self.subTest(text=text, repair=repair):
                guard, memory, history = self.memory()
                history.append(user(text))
                memory.process(history)
                effects = []
                result = guard.turn().execute_with_recovery("export", {"format": "JSON"},
                    lambda tool, arguments: effects.append(arguments), repair=repair)
                self.assertTrue(result["executed"])
                self.assertEqual(effects, [{"format": "JSON"}])
                self.assertEqual(memory.pending(), [])
                entry = next(row for row in memory.entries() if row["key"] == "export_format")
                self.assertEqual(entry["source"]["quote"], text[entry["source"]["start"]:entry["source"]["end"]])

    def test_unknown_qualifier_disputes_old_value_and_never_repairs_to_it(self):
        for text, repair in itertools.product((
                "Export as JSON. Make the export format easier to read.",
                "Export as JSON. Make the export format easier to read. Thanks, this will help.",
                "Export as JSON. The client needs it.",
                "The client needs it. Export as JSON.",
                "Export as JSON. Does that look correct?",
                "Export as JSON. Do not change anything yet.",
                "Export as JSON. Just kidding; keep CSV.",
                "Export as JSON. Thanks, this will help if approved."), (None, "known")):
            with self.subTest(text=text, repair=repair):
                guard, memory, history = self.memory()
                history.append(user(text))
                memory.process(history)
                entry = next(row for row in memory.effective_snapshot()["entries"] if row["key"] == "export_format")
                self.assertEqual(entry["value"], "CSV")
                self.assertTrue(entry["disputed"])
                effects = []
                result = guard.turn().execute_with_recovery("export", {"format": "JSON"},
                    lambda tool, arguments: effects.append(arguments), repair=repair)
                self.assertFalse(result["executed"])
                self.assertEqual(effects, [])
                history.append(user("Export as JSON."))
                memory.process(history)
                self.assertEqual(memory.pending(), [])

    def test_quotes_conditions_and_document_frames_do_not_create_instructions(self):
        for text in (
                '"Export as JSON. Thanks, this will help."',
                '`Export as JSON.` Thanks, this will help.',
                'The ticket says "Export as JSON."',
                "Here is the document:\nExport as JSON. Thanks, this will help.",
                "For the demo:\nExport as JSON. Thanks, this will help.",
                "The documentation lists examples such as response_language=Hindi and response_style=short.",
                "Export as JSON. These are examples.",
                "If approved, export as JSON. Thanks, this will help.",
                "Do not export as JSON. Thanks, this will help.",
                "Export as JSON only if approved. Thanks, this will help."):
            with self.subTest(text=text):
                _, memory, history = self.memory()
                history.append(user(text))
                memory.process(history)
                self.assertEqual({row["key"]: row["value"] for row in memory.entries()},
                                 {"export_format": "CSV", "response_language": "English"})
                self.assertEqual(memory.pending(), [])

    def test_quoted_literal_never_becomes_a_nested_instruction(self):
        _, memory, history = self.memory()
        text = 'Set the project name to "Export as JSON". Thanks, this will help.'
        history.append(user(text))
        memory.process(history)
        self.assertEqual({row["key"]: row["value"] for row in memory.entries()},
            {"export_format": "CSV", "response_language": "English", "project_name": "Export as JSON"})

    def test_review_mode_and_replay_keep_manual_decisions(self):
        _, memory, history = self.memory()
        memory.policy = MemoryPolicy(mode="review")
        history.append(user("Export as JSON. Thanks, this will help."))
        memory.process(history)
        proposal = memory.pending()[0]
        self.assertEqual(proposal["value"], "JSON")
        memory.dismiss(proposal["id"])
        memory.process(history)
        self.assertEqual(memory.pending(), [])
        memory.policy = MemoryPolicy()
        history.append(user("Export as JSON. Thanks, that helps."))
        memory.process(history)
        memory.forget("export_format")
        memory.process(history)
        self.assertNotIn("export_format", {row["key"] for row in memory.entries()})


class ScopeBatchRecoveryTests(unittest.TestCase):
    def view_for_batches(self, history, boundaries):
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id)
            for boundary in boundaries:
                memory.process(history[:boundary], protocol="openai_chat")
            result = semantic_view(memory)
            before = memory.effective_snapshot()
            memory.process(history, protocol="openai_chat")
            self.assertEqual(memory.effective_snapshot(), before)
            for record in memory.entries() + memory.pending():
                source = record["source"]
                self.assertEqual(source["quote"], history[source["index"]]["content"][source["start"]:source["end"]])
            return result

    def test_every_batch_partition_matches_chronological_consumption(self):
        cases = (
            ("Export as CSV.", "For this task, export as JSON.", "Continue working."),
            ("For this session, export as JSON.", "Continue working."),
            ("Export as CSV.", "For this export, use JSON.", "Continue."),
            ("Export as CSV.", "For this export, use JSON.", "Continue.", "Next export."),
            ("Export as CSV.", "For this task, export as JSON.", "Export as XML.", "Continue."),
            ("Export as CSV.", "For this export, use JSON.", "Next export.", "Continue."),
            ("Export as CSV.", "For this export, use JSON.", "For this export, use XML."),
            ("Export as CSV.", "Export as JSON. For this export only, use CSV. Thanks, this will help."),
        )
        for texts in cases:
            history = []
            for text in texts:
                history.extend([user(text), {"role": "assistant", "content": "Acknowledged."}])
            history.pop()  # A plain-text batch ends at its pending user request.
            expected = self.view_for_batches(history, range(1, len(history) + 1, 2))
            for cuts in itertools.product((False, True), repeat=len(texts) - 1):
                boundaries = [2 * index + 1 for index, cut in enumerate(cuts) if cut] + [len(history)]
                with self.subTest(texts=texts, boundaries=boundaries):
                    self.assertEqual(self.view_for_batches(history, boundaries), expected)

    def test_imported_scope_dispute_blocks_actual_dispatch(self):
        for scope, repair in itertools.product(("For this task, export as JSON.",
                                                "For this export, use JSON."), (None, "known")):
            with self.subTest(scope=scope, repair=repair), ContextGuard() as guard:
                memory = AutomaticMemory(guard.store, guard.session_id)
                memory.process([user("Export as CSV."), user(scope), user("Continue.")])
                guard.allow_tool("export", memory_bindings={"format": "export_format"})
                effects = []
                result = guard.turn().execute_with_recovery("export", {"format": "CSV"},
                    lambda tool, arguments: effects.append(arguments), repair=repair)
                self.assertFalse(result["executed"])
                self.assertEqual(effects, [])


if __name__ == "__main__":
    unittest.main()
