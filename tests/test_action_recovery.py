"""One new argument proposal may precede an action; a started tool never retries."""

import inspect
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import (ActionRejected, AutomaticMemory, ContextGuard,
                           MemoryPolicy, ToolExecutionError)


class ActionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "export.txt"
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.memory = AutomaticMemory(self.guard.store, self.guard.session_id)
        self.history = [{"role": "user", "content": "Export as CSV. Respond in English."}]
        self.memory.process(self.history)
        self.guard.remember("approved", True)
        self.guard.allow_tool("export", when={"approved": True},
                              memory_bindings={"format": "export_format"},
                              optional_args=("rows",))
        self.calls = []
        self.repairs = []

    def execute(self, tool, arguments):
        self.calls.append((tool, arguments))
        self.output.write_text(arguments["format"], encoding="utf-8")
        return {"path": str(self.output)}

    def repair(self, request):
        self.repairs.append(request)
        entry = next(row for row in request["user_memory"]["entries"]
                     if row["key"] == "export_format")
        return {**request["arguments"], "format": entry["value"]}

    def recover(self, arguments=None, *, repair=True, turn=None):
        return (turn or self.guard.turn()).execute_with_recovery(
            "export", {"format": "JSON"} if arguments is None else arguments,
            self.execute, repair=self.repair if repair else None)

    def assert_not_executed(self, result, status, attempts):
        self.assertEqual(result["status"], status)
        self.assertFalse(result["executed"])
        self.assertEqual(result["repair_attempts"], attempts)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())
        self.assertNotIn("receipt", result)
        self.assertNotIn("result", result)

    def change(self, text):
        self.history += [{"role": "assistant", "content": "Acknowledged."},
                         {"role": "user", "content": text}]
        self.memory.process(self.history)

    def test_correct_proposal_executes_once_without_repair_and_cannot_reuse_turn(self):
        turn = self.guard.turn()
        result = self.recover({"format": "CSV"}, turn=turn)
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["repair_attempts"], 0)
        self.assertEqual(result["initial_check"], result["final_check"])
        self.assertEqual(self.output.read_text(encoding="utf-8"), "CSV")
        second = self.recover({"format": "CSV"}, turn=turn)
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(second["repair_attempts"], 0)
        self.assertFalse(second["executed"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.repairs, [])

    def test_default_is_check_only_and_does_not_invent_a_repair(self):
        result = self.guard.turn().execute_with_recovery("export", {"format": "JSON"}, self.execute)
        self.assert_not_executed(result, "blocked", 0)
        self.assertEqual(self.repairs, [])

    def test_one_repair_uses_observed_memory_and_then_writes_correct_output(self):
        result = self.recover()
        self.assertEqual(result["status"], "repaired")
        self.assertTrue(result["executed"])
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(len(self.repairs), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "CSV")
        request = self.repairs[0]
        self.assertEqual(set(request), {"tool", "arguments", "rejection", "context", "user_memory"})
        self.assertEqual(request["tool"], "export")
        self.assertEqual(request["arguments"], {"format": "JSON"})
        self.assertEqual(request["context"]["facts"], {"approved": True})
        self.assertEqual(request["context"]["revision"], result["repair_context_revision"])
        self.assertEqual(result["initial_check"]["decision"], "block")
        self.assertEqual(result["final_check"]["decision"], "allow")
        self.assertEqual(result["receipt"]["arguments"], {"format": "CSV"})
        self.assertEqual(result["result"], {"path": str(self.output)})

    def test_missing_argument_with_known_value_can_be_repaired(self):
        result = self.recover({"rows": []})
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(self.calls, [("export", {"format": "CSV", "rows": []})])
        self.assertEqual(result["initial_check"]["issues"][0]["code"], "missing_argument")

    def test_trusted_binding_can_be_repaired_without_using_inferred_memory(self):
        self.guard.remember("format", "CSV")
        self.guard.allow_tool("trusted_export", bindings={"format": "format"})
        result = self.guard.turn().execute_with_recovery(
            "trusted_export", {"format": "csv"}, self.execute,
            repair=lambda request: {"format": request["context"]["facts"]["format"]})
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(result["initial_check"]["issues"][0]["code"], "binding_mismatch")
        self.assertEqual(self.calls, [("trusted_export", {"format": "CSV"})])

    def test_second_bad_proposal_stops_after_one_callback(self):
        def still_wrong(request):
            self.repairs.append(request)
            return {"format": "YAML"}

        result = self.guard.turn().execute_with_recovery(
            "export", {"format": "JSON"}, self.execute, repair=still_wrong)
        self.assert_not_executed(result, "blocked", 1)
        self.assertEqual(len(self.repairs), 1)
        self.assertEqual(result["final_check"]["issues"][0]["actual"], "YAML")

    def test_missing_memory_requires_clarification_without_repair(self):
        self.memory.forget("export_format")
        result = self.recover()
        self.assert_not_executed(result, "clarification_required", 0)
        self.assertEqual(self.repairs, [])

    def test_disputed_memory_requires_clarification_even_if_old_value_matches(self):
        self.change("Use CSV for the log format.")
        self.change("Actually, use JSON instead of CSV.")
        self.assertTrue(self.memory.pending())
        result = self.recover({"format": "CSV"})
        self.assert_not_executed(result, "clarification_required", 0)
        self.assertEqual(self.repairs, [])

    def test_permission_failure_does_not_offer_repair_or_clarification(self):
        self.guard.remember("approved", False)
        self.memory.forget("export_format")
        result = self.recover()
        self.assert_not_executed(result, "blocked", 0)
        self.assertEqual(self.repairs, [])

    def test_stale_proposal_is_not_refreshed_for_repair(self):
        turn = self.guard.turn()
        self.guard.remember("delivery_note", "Use the side entrance")
        result = self.recover(turn=turn)
        self.assert_not_executed(result, "blocked", 0)
        self.assertIn("stale_revision", {row["code"] for row in result["initial_check"]["issues"]})
        self.assertEqual(self.repairs, [])

    def test_unknown_tool_and_unpermitted_fields_are_not_repairable(self):
        for tool, arguments in (("delete", {}), ("export", {"format": "JSON", "force": True})):
            with self.subTest(tool=tool):
                result = self.guard.turn().execute_with_recovery(
                    tool, arguments, self.execute, repair=self.repair)
                self.assert_not_executed(result, "blocked", 0)
        self.assertEqual(self.repairs, [])

    def test_repair_cannot_replace_tool_or_revision(self):
        for extra in ({"tool": "another_tool"}, {"context_revision": 9999}):
            with self.subTest(extra=extra):
                result = self.guard.turn().execute_with_recovery(
                    "export", {"format": "JSON"}, self.execute,
                    repair=lambda request: {"format": "CSV", **extra})
                self.assert_not_executed(result, "blocked", 1)
                self.assertEqual(result["final_check"]["issues"][0]["code"], "unexpected_argument")

    def test_callback_mutations_do_not_change_revision_or_stored_requirements(self):
        arguments = {"format": "JSON", "rows": [{"id": 1}]}
        turn = self.guard.turn()

        def tamper(request):
            request["context"]["revision"] = 9999
            request["context"]["facts"]["approved"] = False
            request["user_memory"]["entries"][0]["value"] = "YAML"
            request["arguments"]["rows"][0]["id"] = 2
            return {"format": "CSV", "rows": request["arguments"]["rows"]}

        result = turn.execute_with_recovery("export", arguments, self.execute, repair=tamper)
        self.assert_not_executed(result, "blocked", 1)
        self.assertEqual(arguments, {"format": "JSON", "rows": [{"id": 1}]})
        self.assertEqual(self.guard.store.facts(self.guard.session_id)["approved"]["value"], True)
        self.assertEqual(next(row for row in self.memory.snapshot()["entries"]
                              if row["key"] == "export_format")["value"], "CSV")
        self.assertEqual(result["repair_context_revision"], turn.revision)
        self.assertFalse(result["repair_scope"]["valid"])
        self.assertEqual(result["repair_scope"]["allowed_paths"], ["/format"])
        self.assertIn("/rows", result["repair_scope"]["changed_paths"])
        self.assertIn("repair_scope_violation", {
            row["code"] for row in result["final_check"]["issues"]})

    def test_callback_errors_and_non_json_arguments_fail_without_execution(self):
        def broken(request):
            raise RuntimeError("Do not copy potentially private callback messages into the report")

        for repair, error in ((broken, "RuntimeError"), (lambda request: [], "TypeError"),
                              (lambda request: {"format": object()}, "ValueError")):
            with self.subTest(error=error):
                result = self.guard.turn().execute_with_recovery(
                    "export", {"format": "JSON"}, self.execute, repair=repair)
                self.assert_not_executed(result, "repair_failed", 1)
                self.assertEqual(result["error_type"], error)
                self.assertNotIn("error", result)

    def test_concealed_async_or_generator_repair_is_not_run(self):
        ran = []

        async def asynchronous():
            ran.append(True)
            return {"format": "CSV"}

        def generator():
            ran.append(True)
            yield {"format": "CSV"}

        for factory, getstate, closed in ((asynchronous, inspect.getcoroutinestate, inspect.CORO_CLOSED),
                                           (generator, inspect.getgeneratorstate, inspect.GEN_CLOSED)):
            value = factory()
            self.addCleanup(value.close)
            result = self.guard.turn().execute_with_recovery(
                "export", {"format": "JSON"}, self.execute, repair=lambda request: value)
            self.assert_not_executed(result, "repair_failed", 1)
            self.assertEqual(getstate(value), closed)
        self.assertEqual(ran, [])

    def test_tool_raising_action_rejected_after_writing_is_never_retried(self):
        verdict = self.guard.relay.check(self.guard.session_id, {
            "tool": "export", "arguments": {"format": "JSON"}, "context_revision": self.guard.turn().revision,
        })

        def failed_after_write(tool, arguments):
            self.execute(tool, arguments)
            raise ActionRejected(verdict)

        with self.assertRaises(ToolExecutionError) as caught:
            self.guard.turn().execute_with_recovery(
                "export", {"format": "CSV"}, failed_after_write, repair=self.repair)
        self.assertIsInstance(caught.exception.__cause__, ActionRejected)
        self.assertEqual(caught.exception.receipt["status"], "failed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.repairs, [])
        self.assertTrue(self.output.exists())

    def test_repaired_tool_failure_is_not_another_repair_opportunity(self):
        def failed_after_write(tool, arguments):
            self.execute(tool, arguments)
            raise RuntimeError("External operation failed after a partial effect")

        with self.assertRaises(ToolExecutionError):
            self.guard.turn().execute_with_recovery(
                "export", {"format": "JSON"}, failed_after_write, repair=self.repair)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.repairs), 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "CSV")

    def test_change_between_first_rejection_and_capture_prevents_repair(self):
        from context_relay.adapters import guarded_tool_call

        def concurrent_change(*args):
            result = guarded_tool_call(*args)
            self.guard.remember("approved", False)
            return result

        with patch("context_relay.middleware.guarded_tool_call", side_effect=concurrent_change):
            result = self.recover()
        self.assert_not_executed(result, "blocked", 0)
        self.assertEqual(self.repairs, [])
        self.assertIn("stale_revision", {row["code"] for row in result["final_check"]["issues"]})

    def test_repair_snapshot_does_not_reimpose_default_memory_limit(self):
        with ContextGuard() as guard:
            memory = AutomaticMemory(guard.store, guard.session_id,
                                     policy=MemoryPolicy(max_entries=100, max_chars=80000))
            names = ["setting" + chr(97 + n // 26) + chr(97 + n % 26) for n in range(70)]
            memory.process([{"role": "user", "content": f"Set {name} to small."} for name in names])
            guard.allow_tool("select", memory_bindings={"value": "settingaa"})

            def repair(request):
                self.assertEqual(len(request["user_memory"]["entries"]), 70)
                return {"value": "small"}

            result = guard.turn().execute_with_recovery(
                "select", {"value": "large"}, lambda tool, arguments: arguments, repair=repair)
            self.assertEqual(result["status"], "repaired")

    def test_repair_receipt_and_tool_receive_normalized_memory_value(self):
        result = self.guard.turn().execute_with_recovery(
            "export", {"format": "JSON"}, self.execute, repair=lambda request: {"format": "csv"})
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(self.output.read_text(encoding="utf-8"), "CSV")
        self.assertEqual(result["receipt"]["arguments"], {"format": "CSV"})
        self.assertEqual(result["final_check"]["argument_normalizations"], [
            {"argument": "format", "memory_key": "export_format", "from": "csv", "to": "CSV"}])


class RecoveryConcurrencyTests(unittest.TestCase):
    def test_repair_releases_database_lock_and_concurrent_change_blocks_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "relay.sqlite"
            output = Path(directory) / "export.txt"
            with ContextGuard(db_path) as guard:
                guard.remember("format", "CSV")
                guard.allow_tool("export", bindings={"format": "format"})
                finished = threading.Event()
                errors = []

                def concurrent_update():
                    try:
                        with ContextGuard(db_path, session_id=guard.session_id) as other:
                            other.remember("format", "JSON")
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        finished.set()

                def repair(request):
                    worker = threading.Thread(target=concurrent_update, daemon=True)
                    worker.start()
                    self.addCleanup(worker.join, 10)
                    self.assertTrue(finished.wait(3), "Repair callback must not hold Relay's SQLite lock")
                    self.assertEqual(errors, [])
                    return {"format": request["context"]["facts"]["format"]}

                def execute(tool, arguments):
                    output.write_text(arguments["format"], encoding="utf-8")
                    return True

                result = guard.turn().execute_with_recovery(
                    "export", {"format": "YAML"}, execute, repair=repair)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["repair_attempts"], 1)
                self.assertFalse(output.exists())
                self.assertIn("stale_revision", {row["code"] for row in result["final_check"]["issues"]})
                self.assertEqual(guard.store.facts(guard.session_id)["format"]["value"], "JSON")


if __name__ == "__main__":
    unittest.main()
