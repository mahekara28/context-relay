"""Actual local locks and independent processes; no simulated SQLite results."""

import json
import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from context_relay import ContextGuard, RelayConfig, auto_connect
from context_relay.store import StoreBusy


class ScalingAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.effects = []

    def execute(self, tool, arguments):
        self.effects.append(arguments)
        return {"saved": True}

    def guard(self, path, **options):
        guard = ContextGuard(path, **options)
        self.addCleanup(guard.close)
        return guard

    def test_other_handle_times_out_before_callback_then_can_resume_explicitly(self):
        database = self.directory / "shared.sqlite3"
        first = self.guard(database)
        first.allow_tool("refund", optional_args=("amount",))
        second = self.guard(database, session_id=first.session_id, busy_timeout_ms=100)
        turn = second.turn()
        with first.store._transaction(write=True):
            started = time.perf_counter()
            with self.assertRaises(StoreBusy) as caught:
                turn.execute_with_recovery("refund", {"amount": 20}, self.execute, repair="known")
            elapsed = time.perf_counter() - started
            self.assertEqual(caught.exception.timeout_ms, 100)
            self.assertEqual(caught.exception.phase, "begin")
            self.assertTrue(caught.exception.before_transaction)
            self.assertLess(elapsed, 2.0)
            self.assertEqual(self.effects, [])
        self.assertEqual(second.turn().revision, turn.revision)
        accepted = turn.execute_with_recovery("refund", {"amount": 20}, self.execute)
        self.assertTrue(accepted["executed"])
        self.assertEqual(self.effects, [{"amount": 20}])

    def test_shared_store_thread_lock_is_bounded_without_an_implicit_retry(self):
        guard = self.guard(self.directory / "thread.sqlite3", busy_timeout_ms=100)
        guard.allow_tool("refund", optional_args=("amount",))
        turn = guard.turn()
        entered, release = threading.Event(), threading.Event()
        errors = []
        def hold_writer():
            try:
                with guard.store._transaction(write=True):
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("test release was not signalled")
            except BaseException as exc:
                errors.append(type(exc).__name__)
        worker = threading.Thread(target=hold_writer)
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            start = time.perf_counter()
            with self.assertRaises(StoreBusy) as caught:
                turn.execute_with_recovery("refund", {"amount": 20}, self.execute)
            self.assertEqual(caught.exception.phase, "thread_lock")
            self.assertTrue(caught.exception.before_transaction)
            self.assertLess(time.perf_counter() - start, 2.0)
            self.assertEqual(self.effects, [])
        finally:
            release.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(guard.turn().revision, turn.revision)
        self.assertEqual(self.effects, [])

    def test_separate_named_database_progresses_while_one_writer_is_held(self):
        def never_model(**kwargs):
            raise AssertionError("No model call is expected.")
        options = {"storage": self.directory / "named", "config": RelayConfig(busy_timeout_ms=100),
                   "memory": False}
        first = auto_connect(never_model, conversation="held", **options)
        second = auto_connect(never_model, conversation="independent", **options)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        second.guard.allow_tool("refund", optional_args=("amount",))
        turn = second.guard.turn()
        self.assertNotEqual(first.guard.store.path, second.guard.store.path)
        with first.guard.store._transaction(write=True):
            start = time.perf_counter()
            result = turn.execute_with_recovery("refund", {"amount": 20}, self.execute)
            self.assertLess(time.perf_counter() - start, 2.0)
            self.assertTrue(result["executed"])
        self.assertEqual(self.effects, [{"amount": 20}])

    def test_named_reopen_respects_configured_busy_deadline(self):
        def never_model(**kwargs):
            raise AssertionError("No model call is expected.")
        options = {"conversation": "held", "storage": self.directory / "named",
                   "config": RelayConfig(busy_timeout_ms=100), "memory": False}
        first = auto_connect(never_model, **options)
        self.addCleanup(first.close)
        with first.guard.store._transaction(write=True):
            start = time.perf_counter()
            with self.assertRaises(StoreBusy) as caught:
                auto_connect(never_model, **options)
            self.assertEqual(caught.exception.timeout_ms, 100)
            self.assertTrue(caught.exception.before_transaction)
            self.assertLess(time.perf_counter() - start, 2.0)
        with auto_connect(never_model, **options) as resumed:
            self.assertEqual(resumed.session_id, first.session_id)

    def test_actual_commit_contention_is_explicitly_not_safe_to_retry(self):
        database = self.directory / "commit.sqlite3"
        guard = self.guard(database, busy_timeout_ms=100)
        guard.allow_tool("refund", optional_args=("amount",))
        turn = guard.turn()
        # WAL normally lets readers coexist with commits. Use SQLite's supported
        # rollback journal for this specific real COMMIT-lock failure probe.
        guard.store._connection.execute("PRAGMA journal_mode = DELETE")
        reader = sqlite3.connect(database, isolation_level=None)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM sessions").fetchall()
            start = time.perf_counter()
            with self.assertRaises(StoreBusy) as caught:
                turn.execute_with_recovery("refund", {"amount": 20}, self.execute, repair="known")
            self.assertEqual(caught.exception.phase, "commit")
            self.assertFalse(caught.exception.before_transaction)
            self.assertLess(time.perf_counter() - start, 2.0)
            self.assertEqual(self.effects, [{"amount": 20}])
        finally:
            reader.close()
        self.assertFalse(guard.store._connection.in_transaction)
        self.assertEqual(guard.turn().revision, turn.revision)
        self.assertEqual([event for event in guard.store.events(guard.session_id)
                          if event["kind"] == "tool_result"], [])
        # No automatic replay happened. An external idempotency key is needed
        # before the host can safely retry after this uncertain commit outcome.
        self.assertEqual(self.effects, [{"amount": 20}])

    def test_benchmark_cannot_report_missing_or_failed_workers_as_success(self):
        script = Path(__file__).resolve().parents[1] / "scripts/evaluate_local_scaling_v019.py"
        spec = importlib.util.spec_from_file_location("relay_scaling_report_check", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        missing = module.summarize([{"worker": 0, "passed": True}], 2)
        self.assertFalse(missing["passed"])
        self.assertEqual(missing["missing_workers"], [1])
        duplicate = module.summarize([{"worker": 0, "passed": True}, {"worker": 0, "passed": True}], 2)
        self.assertFalse(duplicate["passed"])
        self.assertEqual(duplicate["duplicate_workers"], [0])
        failed = module.summarize([{"worker": 0, "passed": True},
                                   {"worker": 1, "passed": False, "error_type": "RuntimeError"}], 2)
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["unexpected_errors"], {"RuntimeError": 1})

    def test_four_actual_processes_share_one_revision_and_execute_once(self):
        script = Path(__file__).resolve().parents[1] / "scripts/evaluate_local_scaling_v019.py"
        report = self.directory / "race-report.json"
        completed = subprocess.run([
            sys.executable, str(script), "--race-only", "--workers", "4", "--busy-timeout-ms", "1000",
            "--directory", str(self.directory / "race"), "--output", str(report),
        ], capture_output=True, text=True, timeout=45, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        outcome = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(outcome["passed"])
        self.assertEqual(outcome["model_calls"], 0)
        self.assertEqual(outcome["network_calls"], 0)
        case = outcome["cases"][0]
        self.assertEqual(case["workers_expected"], 4)
        self.assertEqual(case["workers_reported"], 4)
        self.assertEqual(case["missing_workers"], [])
        self.assertEqual(case["unexpected_errors"], {})
        self.assertEqual(case["effects"], 1)
        self.assertEqual(case["marker_count"], 1)
        self.assertEqual(case["stale_replays_blocked"] + case["busy_results"], 3)
        self.assertEqual(case["latency"]["samples"], 4)


if __name__ == "__main__":
    unittest.main()
