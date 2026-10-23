"""Real lock contention, transaction outcomes and configured wait propagation."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from context_relay import ContextGuard, RelayConfig, ToolRegistry, auto_connect, connect
from context_relay.native import connect_native
from context_relay.persistence import named_connection_config
from context_relay.store import RelayStore, StoreBusy


def answer(*, messages):
    raise AssertionError("Storage tests must not invoke a model provider.")


@contextmanager
def held_lock(store):
    acquired, release = threading.Event(), threading.Event()
    def holder():
        with store._locked():
            acquired.set()
            release.wait(5)
    worker = threading.Thread(target=holder)
    worker.start()
    if not acquired.wait(3):
        release.set()
        worker.join(3)
        raise AssertionError("Holder failed to acquire lock.")
    try:
        yield
    finally:
        release.set()
        worker.join(3)
        if worker.is_alive():
            raise AssertionError("Lock holder did not stop.")


class StoreBusyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "relay.sqlite3"

    def store(self, **kwargs):
        store = RelayStore(self.path, **kwargs)
        self.addCleanup(store.close)
        return store

    def test_strict_timeout_configuration_and_roundtrip(self):
        for value in (True, -1, 60001, 0.1, "20", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    RelayConfig(busy_timeout_ms=value)
                with self.assertRaises(ValueError):
                    RelayStore(busy_timeout_ms=value)
        for value in (0, 35, 60000):
            config = RelayConfig(busy_timeout_ms=value)
            self.assertEqual(config.to_dict()["busy_timeout_ms"], value)
            path = self.directory / "config.json"
            path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
            self.assertEqual(RelayConfig.from_file(path).busy_timeout_ms, value)
        self.assertEqual(RelayConfig().busy_timeout_ms, 10000)

    def test_shared_python_lock_has_bounded_wait(self):
        store = self.store(busy_timeout_ms=30)
        with held_lock(store):
            started = time.monotonic()
            with self.assertRaises(StoreBusy) as caught:
                store.list_sessions()
            elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.phase, "thread_lock")
        self.assertTrue(caught.exception.before_transaction)
        self.assertEqual(caught.exception.timeout_ms, 30)
        self.assertGreaterEqual(elapsed, 0.015)
        self.assertLess(elapsed, 1)
        self.assertEqual(store._depth, 0)
        self.assertEqual(store.list_sessions(), [])

    def test_zero_timeout_does_not_wait_or_start_transaction(self):
        store = self.store(busy_timeout_ms=0)
        with held_lock(store):
            started = time.monotonic()
            with self.assertRaises(StoreBusy):
                store.create_session("blocked")
            self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(store.list_sessions(), [])

    def test_other_sqlite_writer_fails_at_begin_and_store_recovers(self):
        holder = self.store(busy_timeout_ms=30)
        contender = self.store(busy_timeout_ms=30)
        session = holder.create_session("existing")["id"]
        with holder._transaction(write=True):
            started = time.monotonic()
            with self.assertRaises(StoreBusy) as caught:
                contender.record_fact(session, "key", "value")
            elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.phase, "begin")
        self.assertTrue(caught.exception.before_transaction)
        self.assertLess(elapsed, 1)
        self.assertEqual(contender._depth, 0)
        self.assertFalse(contender._connection.in_transaction)
        self.assertEqual(contender.facts(session), {})
        contender.record_fact(session, "key", "later")
        self.assertEqual(contender.facts(session)["key"]["value"], "later")

    def test_busy_wal_snapshot_inside_transaction_is_not_preexecution(self):
        first = self.store(busy_timeout_ms=30)
        second = self.store(busy_timeout_ms=30)
        first.create_session("before")
        effects = []
        with self.assertRaises(StoreBusy) as caught:
            with first._transaction():
                first.list_sessions()  # Establish an older WAL read snapshot.
                second.create_session("other-writer")
                effects.append("already happened")
                first.create_session("cannot-upgrade-snapshot")
        self.assertEqual(caught.exception.phase, "statement")
        self.assertFalse(caught.exception.before_transaction)
        self.assertEqual(effects, ["already happened"])
        self.assertFalse(first._connection.in_transaction)
        self.assertEqual(first._depth, 0)
        self.assertEqual(len(first.list_sessions()), 2)
        first.create_session("usable after rollback")

    def test_commit_busy_after_effect_rolls_back_database_not_external_effect(self):
        store = self.store(busy_timeout_ms=30)
        store.create_session("before")
        store._connection.execute("PRAGMA journal_mode = DELETE")
        reader = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM sessions").fetchall()
        effects = []
        try:
            with self.assertRaises(StoreBusy) as caught:
                with store._transaction(write=True):
                    store.create_session("will roll back")
                    effects.append("external effect")
        finally:
            reader.execute("ROLLBACK")
        self.assertEqual(caught.exception.phase, "commit")
        self.assertFalse(caught.exception.before_transaction)
        self.assertEqual(effects, ["external effect"])
        self.assertEqual(len(store.list_sessions()), 1)
        self.assertFalse(store._connection.in_transaction)
        self.assertEqual(store._depth, 0)
        store.create_session("after reader released")

    def test_initialization_contention_is_typed_and_closes_failed_handle(self):
        holder = self.store(busy_timeout_ms=30)
        captured = []
        original = sqlite3.connect
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            captured.append(result)
            return result
        with holder._transaction(write=True), patch("context_relay.store.sqlite3.connect", capture):
            with self.assertRaises(StoreBusy) as caught:
                RelayStore(self.path, busy_timeout_ms=30)
        self.assertEqual(caught.exception.phase, "initialize")
        self.assertTrue(caught.exception.before_transaction)
        self.assertEqual(caught.exception.timeout_ms, 30)
        self.assertEqual(len(captured), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            captured[0].execute("SELECT 1")

    def test_non_lock_operational_error_is_preserved_and_rolled_back(self):
        store = self.store(busy_timeout_ms=30)
        with self.assertRaises(sqlite3.OperationalError) as caught:
            with store._transaction(write=True):
                store.create_session("rollback")
                store._connection.execute("SELECT * FROM absent_table")
        self.assertNotIsInstance(caught.exception, StoreBusy)
        self.assertEqual(store.list_sessions(), [])
        self.assertFalse(store._connection.in_transaction)

    def test_rollback_failure_closes_handle_instead_of_reusing_unknown_transaction(self):
        store = self.store()
        connection = store._connection
        class FailedRollback:
            def execute(self, statement, *args):
                if statement == "ROLLBACK":
                    raise sqlite3.OperationalError("injected rollback failure")
                return connection.execute(statement, *args)
            def __getattr__(self, name):
                return getattr(connection, name)
        store._connection = FailedRollback()
        original = ValueError("original operation failed")
        with self.assertRaises(ValueError) as caught:
            with store._transaction(write=True):
                store.create_session("not committed")
                raise original
        self.assertIs(caught.exception, original)
        self.assertTrue(store._closed)
        self.assertEqual(store._depth, 0)
        with RelayStore(self.path) as reopened:
            self.assertEqual(reopened.list_sessions(), [])

    def test_error_codes_not_message_substrings_determine_busy(self):
        store = self.store()
        error = sqlite3.OperationalError("database is locked")
        # Python 3.10 does not expose SQLite's named result-code constants.
        error.sqlite_errorcode = getattr(sqlite3, "SQLITE_ERROR", 1)
        with self.assertRaises(sqlite3.OperationalError) as caught:
            with store._transaction():
                raise error
        self.assertIs(caught.exception, error)
        locked = sqlite3.OperationalError("fixed test detail")
        locked.sqlite_errorcode = getattr(sqlite3, "SQLITE_LOCKED", 6)
        with self.assertRaises(StoreBusy) as caught:
            with store._transaction():
                raise locked
        self.assertEqual(caught.exception.phase, "statement")
        self.assertFalse(caught.exception.before_transaction)

    def test_nested_operations_preserve_depth_and_outer_outcome(self):
        store = self.store()
        with store._transaction(write=True):
            first = store.create_session("first")
            with store._transaction(write=True):
                store.record_fact(first["id"], "key", "value")
            self.assertEqual(store._depth, 1)
        self.assertEqual(store._depth, 0)
        self.assertEqual(store.facts(first["id"])["key"]["value"], "value")
        with self.assertRaises(StoreBusy) as caught:
            with store._transaction(write=True):
                store.create_session("rollback")
                raise StoreBusy(40, "begin", before_transaction=True)
        self.assertFalse(caught.exception.before_transaction)
        self.assertEqual(caught.exception.phase, "statement")
        self.assertEqual(len(store.list_sessions()), 1)

    def test_close_timeout_can_be_retried_on_connection(self):
        agent = connect(answer, config=RelayConfig(busy_timeout_ms=30))
        self.addCleanup(agent.close)
        with held_lock(agent.guard.store):
            with self.assertRaises(StoreBusy):
                agent.close()
            self.assertFalse(agent._closed)
            self.assertFalse(agent.guard.store._closed)
        agent.close()
        self.assertTrue(agent._closed)
        self.assertTrue(agent.guard.store._closed)

    def test_native_close_timeout_can_be_retried(self):
        agent = connect_native(answer, prepare_request=lambda request: request,
                               protocol="openai_responses_native", config=RelayConfig(busy_timeout_ms=30))
        self.addCleanup(agent.close)
        with held_lock(agent.guard.store):
            with self.assertRaises(StoreBusy):
                agent.close()
            self.assertFalse(agent._closed)
        agent.close()
        self.assertTrue(agent.guard.store._closed)

    def test_propagates_to_new_resumed_named_and_rollover_guards(self):
        config = RelayConfig(db_path=str(self.path), busy_timeout_ms=27)
        with connect(answer, config=config) as agent:
            identity = agent.session_id
            self.assertEqual(agent.guard.store.busy_timeout_ms, 27)
            next_id = agent.guard.store.create_session("successor")["id"]
            child = agent._conversation_successor(agent, next_id)
            self.assertEqual(child.guard.store.busy_timeout_ms, 27)
        with connect(answer, config=config, session_id=identity) as resumed:
            self.assertEqual(resumed.guard.store.busy_timeout_ms, 27)
        with auto_connect(answer, conversation="named", storage=self.directory,
                          config=RelayConfig(busy_timeout_ms=31)) as named:
            self.assertEqual(named.guard.store.busy_timeout_ms, 31)
            with named.guard.store._transaction(write=True):
                with self.assertRaises(StoreBusy) as caught:
                    named_connection_config(RelayConfig(busy_timeout_ms=32), "named", storage=self.directory)
            self.assertEqual(caught.exception.timeout_ms, 32)

    def test_external_guard_rollover_uses_actual_store_timeout(self):
        with ContextGuard(self.path, busy_timeout_ms=29) as guard:
            with connect(answer, guard=guard) as agent:
                next_id = guard.store.create_session("child")["id"]
                child = agent._conversation_successor(agent, next_id)
                self.assertEqual(child.guard.store.busy_timeout_ms, 29)

    def test_registry_setup_and_bind_cannot_wait_on_unbounded_store_lock(self):
        with ContextGuard(busy_timeout_ms=30) as guard:
            registry = ToolRegistry(guard)
            def tool(value):
                return value
            registry.register(tool, unchecked=("value",))
            turn = guard.turn()
            with held_lock(guard.store):
                with self.assertRaises(StoreBusy):
                    registry.bind(turn)
                with self.assertRaises(StoreBusy):
                    registry.register(lambda second: second, name="second", unchecked=("second",))
            self.assertEqual(registry.coverage()["registered_tools"], ["tool"])

    def test_callback_duration_is_not_misrepresented_as_lock_deadline(self):
        store = self.store(busy_timeout_ms=1)
        with store._transaction(write=True):
            # Own reentrant operations remain valid after the wait duration.
            time.sleep(0.01)
            store.create_session("same holder")
        self.assertEqual(len(store.list_sessions()), 1)


if __name__ == "__main__":
    unittest.main()
