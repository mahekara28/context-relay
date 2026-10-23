"""Known spelling variants must reach actual tools consistently, without widening authority."""
import tempfile
import unittest
from pathlib import Path

from context_relay import ActionRejected, AutomaticMemory, ContextGuard
from context_relay.memory_values import normalize_memory_value


class MemoryValueTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        self.history = [{"role": "user", "content": "Export as JSON. Respond in Hindi."}]
        self.memory.process(self.history)
        self.guard.allow_tool("export", memory_bindings={"format": "export_format", "language": "response_language"},
                              optional_args=("body",))

    def test_case_variants_write_canonical_format_and_keep_original_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.txt"
            def write(tool, arguments):
                self.assertEqual(tool, "export")
                self.assertEqual(arguments["language"], "Hindi")
                path.write_text("json branch" if arguments["format"] == "JSON" else "wrong branch", encoding="utf-8")
                return arguments
            original = {"format": " json ", "language": "hInDi", "body": "Do NOT change My Text"}
            context = self.guard.turn().context
            action = {"tool": "export", "arguments": original, "context_revision": context["revision"]}
            result = self.guard.relay.execute(self.guard.session_id, action, write)
            self.assertEqual(path.read_text(encoding="utf-8"), "json branch")
            self.assertEqual(original["format"], " json ")
            self.assertEqual(result["result"], {"format": "JSON", "language": "Hindi", "body": original["body"]})
            self.assertEqual(len(result["check"]["argument_normalizations"]), 2)
            self.assertEqual(result["receipt"]["arguments"], result["result"])
            self.assertEqual(self.guard.store.checks(self.guard.session_id)[0], result["check"])

    def test_different_format_and_unknown_aliases_remain_blocked(self):
        for value in ("jsonl", "javascript object notation", "JSon file", "ＪＳＯＮ", True, 1, ["JSON"]):
            with self.subTest(value=value), self.assertRaises(ActionRejected):
                self.guard.turn().wrap_dispatcher(lambda *a: self.fail("Tool must not execute"))(
                    "export", {"format": value, "language": "Hindi"})

    def test_custom_memory_and_trusted_bindings_remain_case_sensitive(self):
        self.memory.process(self.history + [{"role": "user", "content": "Set project code to AbC."}])
        self.guard.remember("account", "AbC")
        self.guard.allow_tool("select", bindings={"account": "account"}, memory_bindings={"code": "project_code"})
        for arguments in ({"account": "abc", "code": "AbC"}, {"account": "AbC", "code": "abc"}):
            with self.subTest(arguments=arguments), self.assertRaises(ActionRejected):
                self.guard.turn().wrap_dispatcher(lambda *a: self.fail("Tool must not execute"))("select", arguments)

    def test_equivalence_does_not_bypass_dispute_permission_or_revision(self):
        turn = self.guard.turn()
        self.guard.remember("approved", False)
        self.guard.allow_tool("export", when={"approved": True},
                              memory_bindings={"format": "export_format", "language": "response_language"})
        with self.assertRaises(ActionRejected) as caught:
            turn.wrap_dispatcher(lambda *a: self.fail("Tool must not execute"))(
                "export", {"format": "json", "language": "hindi"})
        self.assertIn("requirement_failed", {item["code"] for item in caught.exception.verdict["issues"]})
        self.assertNotIn("argument_normalizations", caught.exception.verdict)
        self.guard.remember("approved", True)
        self.memory.process(self.history + [{"role": "user", "content": "The export format is CSV."}])
        with self.assertRaises(ActionRejected) as caught:
            self.guard.turn().wrap_dispatcher(lambda *a: self.fail("Tool must not execute"))(
                "export", {"format": "json", "language": "hindi"})
        self.assertIn("memory_disputed", {item["code"] for item in caught.exception.verdict["issues"]})

    def test_persisted_memory_has_same_execution_behavior_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "memory.sqlite"
            with ContextGuard(db) as guard:
                session = guard.session_id
                AutomaticMemory(guard.store, session).process(self.history)
                guard.allow_tool("export", memory_bindings={"format": "export_format"})
            with ContextGuard(db, session_id=session) as guard:
                result = guard.turn().wrap_dispatcher(lambda _, args: args)("export", {"format": "json"})
                self.assertEqual(result, {"format": "JSON"})

    def test_normalizer_only_recognizes_explicit_domains(self):
        for key, value, expected in (
            ("output_format", "markdown", "Markdown"), ("response_style", "CONCISE", "concise"),
            ("response_language", "en", "en"), ("file_path", " JSON ", " JSON "),
            ("export_format", "customFormat", "customFormat"), ("export_format", False, False),
        ):
            with self.subTest(key=key, value=value):
                self.assertEqual(normalize_memory_value(key, value), expected)
                self.assertIs(type(normalize_memory_value(key, value)), type(expected))


if __name__ == "__main__":
    unittest.main()
