"""Memory constraints must stop side effects, not merely inspect a verdict."""
import tempfile
import unittest
from pathlib import Path

from context_relay import (ActionRejected, AutomaticMemory, ContextGuard,
                           MemoryBudgetExceeded, MemoryPolicy, PolicyError, connect)


class MemoryActionTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        self.history = [{"role": "user", "content": "Export as CSV. Respond in English."}]
        self.memory.process(self.history)
        self.guard.remember("approved", True)
        self.guard.allow_tool("export", when={"approved": True},
                              memory_bindings={"format": "export_format", "language": "response_language"},
                              optional_args=("rows",))
        self.calls = []

    def execute(self, tool, arguments):
        self.calls.append((tool, arguments))
        return {"written": True}

    def action(self, **changes):
        arguments = {"format": "CSV", "language": "English", "rows": []}
        arguments.update(changes)
        return arguments

    def change(self, text):
        self.history += [{"role": "assistant", "content": "Acknowledged."},
                         {"role": "user", "content": text}]
        self.memory.process(self.history)

    def reject(self, turn, arguments, code):
        with self.assertRaises(ActionRejected) as caught:
            turn.wrap_dispatcher(self.execute)("export", arguments)
        self.assertIn(code, {row["code"] for row in caught.exception.verdict["issues"]})
        self.assertEqual(self.calls, [])
        return caught.exception.verdict

    def test_wrong_preference_blocks_actual_tool_with_source(self):
        verdict = self.reject(self.guard.turn(), self.action(format="JSON"), "memory_binding_mismatch")
        problem = next(row for row in verdict["issues"] if row["code"] == "memory_binding_mismatch")
        self.assertEqual(problem["memory_key"], "export_format")
        self.assertEqual(problem["expected"], "CSV")
        self.assertIn("Export as CSV", problem["source"]["quote"])
        self.assertEqual(self.guard.store.facts(self.guard.session_id).keys(), {"approved"})

    def test_matching_action_runs_once_and_reusing_turn_requires_refresh(self):
        turn = self.guard.turn()
        wrapped = turn.wrap_dispatcher(self.execute)
        self.assertEqual(wrapped("export", self.action()), {"written": True})
        with self.assertRaises(ActionRejected) as caught:
            wrapped("export", self.action())
        self.assertEqual(caught.exception.decision, "refresh")
        self.assertEqual(len(self.calls), 1)

    def test_missing_memory_cannot_fall_back_to_model_argument(self):
        self.memory.forget("export_format")
        self.reject(self.guard.turn(), self.action(), "memory_missing")

    def test_pending_correction_blocks_even_the_old_value(self):
        self.change("Use CSV for the log format.")
        self.change("Actually, use JSON instead of CSV.")
        self.assertTrue(self.memory.pending())
        self.reject(self.guard.turn(), self.action(), "memory_disputed")

    def test_update_after_generation_does_not_refresh_old_proposal(self):
        turn = self.guard.turn()
        self.change("Change the export format from CSV to JSON.")
        verdict = self.reject(turn, self.action(), "memory_binding_mismatch")
        self.assertIn("stale_revision", {row["code"] for row in verdict["issues"]})
        self.assertEqual(self.guard.turn().wrap_dispatcher(self.execute)(
            "export", self.action(format="JSON")), {"written": True})

    def test_matching_memory_does_not_override_explicit_permission(self):
        self.guard.remember("approved", False)
        self.reject(self.guard.turn(), self.action(), "requirement_failed")

    def test_unregistered_tool_is_not_authorized_by_memory(self):
        with self.assertRaises(ActionRejected) as caught:
            self.guard.turn().wrap_dispatcher(self.execute)("another_tool", self.action())
        self.assertIn("unknown_tool", {row["code"] for row in caught.exception.verdict["issues"]})
        self.assertEqual(self.calls, [])

    def test_missing_and_extra_arguments_are_rejected(self):
        self.reject(self.guard.turn(), {"language": "English"}, "missing_argument")
        self.reject(self.guard.turn(), self.action(force=True), "unexpected_argument")

    def test_memory_comparison_remains_type_sensitive(self):
        for value in (True, 1, None, ["CSV"]):
            with self.subTest(value=value):
                self.reject(self.guard.turn(), self.action(format=value), "memory_binding_mismatch")

    def test_no_memory_from_another_conversation(self):
        with ContextGuard() as other:
            other.allow_tool("export", memory_bindings={"format": "export_format"})
            with self.assertRaises(ActionRejected) as caught:
                other.turn().wrap_dispatcher(self.execute)("export", {"format": "CSV"})
            self.assertEqual(caught.exception.verdict["issues"][0]["code"], "memory_missing")
        self.assertEqual(self.calls, [])

    def test_policy_bindings_cannot_be_ambiguous(self):
        for options in (
            {"bindings": {"format": "trusted_format"}},
            {"optional_args": ["format"]},
        ):
            with self.subTest(options=options), self.assertRaises(PolicyError):
                self.guard.allow_tool("bad", memory_bindings={"format": "export_format"}, **options)
        for invalid in ([], {"format": ""}, {"": "export_format"}, {"format": 1}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.guard.allow_tool("bad", memory_bindings=invalid)

    def test_prepared_connection_enforces_before_writing_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.txt"

            def write_export(format, language, rows):
                path.write_text(format, encoding="utf-8")
                return str(path)

            def model_call(messages):
                return self.action(format="JSON")

            with connect(model_call, guard=self.guard, memory=MemoryPolicy()) as agent:
                result = agent.invoke(messages=self.history)
                with self.assertRaises(ActionRejected):
                    result.prepared.wrap(write_export, tool="export")(**result.response)
            self.assertFalse(path.exists())

    def test_persisted_policy_and_memory_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "relay.sqlite"
            with ContextGuard(db_path) as guard:
                session_id = guard.session_id
                AutomaticMemory(guard.store, session_id).process([
                    {"role": "user", "content": "Export as JSON."}])
                guard.allow_tool("export", memory_bindings={"format": "export_format"})
            with ContextGuard(db_path, session_id=session_id) as guard:
                self.assertEqual(guard.turn().wrap_dispatcher(self.execute)(
                    "export", {"format": "JSON"}), {"written": True})

    def test_inspection_respects_sessions_with_larger_memory_policies(self):
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id,
                                     policy=MemoryPolicy(max_entries=100, max_chars=80000))
            names = ["setting" + chr(97 + n // 26) + chr(97 + n % 26) for n in range(70)]
            memory.process([{"role": "user", "content": f"Set {name} to small."} for name in names])
            self.assertEqual(len(memory.entries()), 70)
            with self.assertRaises(MemoryBudgetExceeded):
                AutomaticMemory(guard.store, guard.session_id).context_snapshot()
            guard.allow_tool("select", memory_bindings={"value": "settingaa"})
            self.assertEqual(guard.turn().wrap_dispatcher(self.execute)(
                "select", {"value": "small"}), {"written": True})


if __name__ == "__main__":
    unittest.main()
