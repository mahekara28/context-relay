"""Named local persistence: independent restart, isolation and crash probes."""

import json
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from context_relay import ContextGuard, RelayConfig, auto_connect
from context_relay.conversation import (ConversationAlreadyCompleted,
                                        ConversationBusy, ConversationPending)
from context_relay.persistence import named_storage_path


WORKER = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from context_relay import auto_connect

mode, storage, name = sys.argv[2:5]
def provider(*, messages):
    if mode == "crash":
        os._exit(23)
    return "Saved locally."

if mode == "race":
    gate, ready = map(Path, sys.argv[5:7])
    ready.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 15
    while not gate.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("start gate")
        time.sleep(0.01)

with auto_connect(provider, conversation=name, storage=storage) as agent:
    if mode == "rollover":
        chat = agent.conversation(system="Keep item names unchanged.")
        chat.send("Export as JSON.", request_id="seed")
        root = agent.session_id
        successor = chat.rollover(expected_revision=agent.guard.turn().revision)
        print(json.dumps({"root": root, "successor": successor}))
    elif mode == "crash":
        agent.conversation().send("Export as JSON.", request_id="crashed")
        raise AssertionError("crash provider unexpectedly returned")
    else:
        print(json.dumps({"session": agent.session_id,
                          "sessions": len(agent.guard.store.list_sessions()),
                          "database": agent.guard.store.path}))
'''


class NamedPersistenceAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.storage = self.directory / "named"
        self.calls = []
        self.worker = self.directory / "worker.py"
        self.worker.write_text(textwrap.dedent(WORKER), encoding="utf-8")
        self.source = str(Path(__file__).resolve().parents[1] / "src")

    def provider(self, *, messages):
        self.calls.append(messages)
        return "Saved locally."

    def connect(self, name="refund-desk", *, namespace="default", provider=None, **options):
        agent = auto_connect(self.provider if provider is None else provider, provider="plain",
                             conversation=name, storage=self.storage, namespace=namespace, **options)
        self.addCleanup(agent.close)
        return agent

    def memory(self, agent):
        return {entry["key"]: entry["value"] for entry in agent.memory.entries()}

    def worker_command(self, mode, name="refund-desk", *extra):
        return [sys.executable, str(self.worker), self.source, mode,
                str(self.storage), name, *(str(item) for item in extra)]

    def test_close_reopen_name_recovers_history_and_memory_without_saved_uuid(self):
        first = self.connect()
        chat = first.conversation(system="Keep item names unchanged.")
        chat.send("Export as JSON. Reply in Hindi.", request_id="one")
        previous_id, history = first.session_id, chat.history
        first.close()
        second = self.connect()
        self.assertEqual(second.session_id, previous_id)
        self.assertEqual(second.conversation().history, history)
        self.assertEqual(self.memory(second), {"export_format": "JSON", "response_language": "Hindi"})
        self.assertEqual(len(self.calls), 1)

    def test_different_names_and_namespaces_have_independent_databases(self):
        first = self.connect("orders", namespace="team-a")
        first.conversation().send("Export as JSON.")
        second = self.connect("orders", namespace="team-b")
        third = self.connect("invoices", namespace="team-a")
        self.assertEqual(second.memory.entries(), [])
        self.assertEqual(third.memory.entries(), [])
        self.assertEqual(second.conversation().history, [])
        self.assertEqual(third.conversation().history, [])
        self.assertEqual(len({first.session_id, second.session_id, third.session_id}), 3)
        self.assertEqual(len({first.guard.store.path, second.guard.store.path, third.guard.store.path}), 3)

    def test_unnamed_connection_retains_explicit_in_memory_default(self):
        with auto_connect(lambda **kwargs: "Saved locally.") as first:
            first.conversation().send("Export as JSON.")
            self.assertEqual(first.guard.store.path, ":memory:")
        with auto_connect(lambda **kwargs: "Saved locally.") as second:
            self.assertEqual(second.guard.store.path, ":memory:")
            self.assertEqual(second.memory.entries(), [])

    def test_two_simultaneous_processes_create_one_canonical_root(self):
        gate = self.directory / "start"
        ready = [self.directory / f"ready-{index}" for index in range(2)]
        processes = [subprocess.Popen(self.worker_command("race", "shared", gate, item),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for item in ready]
        try:
            deadline = time.monotonic() + 15
            while not all(item.exists() for item in ready):
                if any(process.poll() is not None for process in processes):
                    self.fail("A worker exited before the shared start gate.")
                if time.monotonic() > deadline:
                    self.fail("Workers did not reach the shared start gate.")
                time.sleep(0.01)
            gate.write_text("go", encoding="utf-8")
            outcomes = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, stderr)
                outcomes.append(json.loads(stdout))
            self.assertEqual(outcomes[0]["session"], outcomes[1]["session"])
            self.assertEqual(outcomes[0]["database"], outcomes[1]["database"])
            self.assertEqual([outcome["sessions"] for outcome in outcomes], [1, 1])
            resumed = self.connect("shared")
            self.assertEqual(resumed.session_id, outcomes[0]["session"])
            self.assertEqual(len(resumed.guard.store.list_sessions()), 1)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)

    def test_process_restart_follows_rollover_and_preserves_request_deduplication(self):
        completed = subprocess.run(self.worker_command("rollover"), capture_output=True,
                                   text=True, timeout=25, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        ids = json.loads(completed.stdout)
        agent = self.connect()
        self.assertEqual(agent.session_id, ids["successor"])
        self.assertNotEqual(agent.session_id, ids["root"])
        chat = agent.conversation()
        self.assertEqual(chat.history, [{"role": "system", "content": "Keep item names unchanged."}])
        self.assertEqual(self.memory(agent)["export_format"], "JSON")
        with self.assertRaises(ConversationAlreadyCompleted):
            chat.send("Export as JSON.", request_id="seed")
        self.assertEqual(self.calls, [])
        chat.send("Continue with the current settings.", request_id="next")
        self.assertEqual(len(self.calls), 1)

    def test_completed_request_id_after_restart_does_not_call_provider_twice(self):
        first = self.connect()
        first.conversation().send("Export as JSON.", request_id="stable-id")
        first.close()
        second = self.connect()
        with self.assertRaises(ConversationAlreadyCompleted) as completed:
            second.conversation().send("Export as JSON.", request_id="stable-id")
        self.assertEqual(completed.exception.assistant_message["content"], "Saved locally.")
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            second.conversation().send("Export as CSV.", request_id="stable-id")
        self.assertEqual(len(self.calls), 1)

    def test_provider_failure_is_pending_after_reopen_without_implicit_replay(self):
        failed_calls = []
        def failure(*, messages):
            failed_calls.append(messages)
            raise ConnectionError("local simulated provider outage")
        first = self.connect(provider=failure)
        with self.assertRaises(ConnectionError):
            first.conversation().send("Export as JSON.", request_id="pending")
        first.close()
        second = self.connect()
        chat = second.conversation()
        self.assertEqual(chat.pending["request_id"], "pending")
        self.assertFalse(chat.pending["in_flight"])
        self.assertEqual(self.calls, [])
        with self.assertRaises(ConversationPending):
            chat.send("Another request.")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(failed_calls), 1)
        self.assertEqual(chat.retry(), "Saved locally.")
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(chat.pending)

    def test_actual_worker_crash_is_not_silently_replayed(self):
        crashed = subprocess.run(self.worker_command("crash"), capture_output=True,
                                 text=True, timeout=25, check=False)
        self.assertEqual(crashed.returncode, 23, crashed.stderr)
        agent = self.connect()
        chat = agent.conversation()
        self.assertEqual(chat.pending["request_id"], "crashed")
        self.assertTrue(chat.pending["in_flight"])
        self.assertEqual(self.calls, [])
        with self.assertRaises(ConversationBusy):
            chat.retry()
        self.assertEqual(self.calls, [])
        self.assertEqual(chat.retry(recover_inflight=True), "Saved locally.")
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(chat.pending)

    def test_deleted_history_reopens_empty_without_resurrecting_alias_or_memory(self):
        first = self.connect()
        chat = first.conversation()
        chat.send("Export as JSON.", request_id="deleted-request")
        old_id = first.session_id
        result = chat.delete_history(expected=chat.deletion_plan())
        self.assertEqual(result["deleted"]["sessions"], 1)
        first.close()
        second = self.connect()
        self.assertNotEqual(second.session_id, old_id)
        self.assertEqual(second.memory.entries(), [])
        self.assertEqual(second.conversation().history, [])
        self.assertEqual(len(second.guard.store.list_sessions()), 1)
        second.conversation().send("Export as CSV.", request_id="deleted-request")
        self.assertEqual(self.memory(second)["export_format"], "CSV")

    def test_stale_deletion_plan_cannot_remove_newer_named_history(self):
        first = self.connect()
        chat = first.conversation()
        chat.send("Export as JSON.")
        old_plan = chat.deletion_plan()
        chat.send("Keep the order names unchanged.")
        with self.assertRaises(ValueError):
            chat.delete_history(expected=old_plan)
        expected = chat.history
        first.close()
        second = self.connect()
        self.assertEqual(second.conversation().history, expected)
        self.assertEqual(self.memory(second)["export_format"], "JSON")

    def test_deleting_rolled_over_lineage_removes_named_root_alias(self):
        first = self.connect()
        chat = first.conversation()
        chat.send("Export as JSON.", request_id="before-rollover")
        root_id = first.session_id
        next_id = chat.rollover(expected_revision=first.guard.turn().revision)
        first.close()
        successor = self.connect()
        self.assertEqual(successor.session_id, next_id)
        current = successor.conversation()
        current.send("Continue.", request_id="after-rollover")
        deleted = current.delete_history(expected=current.deletion_plan())
        self.assertEqual(deleted["deleted"]["sessions"], 2)
        successor.close()
        reopened = self.connect()
        self.assertNotIn(reopened.session_id, {root_id, next_id})
        self.assertEqual(reopened.memory.entries(), [])
        self.assertEqual(reopened.conversation().history, [])
        self.assertEqual(len(reopened.guard.store.list_sessions()), 1)

    def test_names_are_labels_and_cannot_escape_storage_directory(self):
        for name, namespace in (("../../outside.sqlite3", "default"),
                                ("..\\outside.sqlite3", "..\\tenant"),
                                ("C:/outside/absolute.sqlite3", "team/../../other"),
                                ("हिंदी नाम", "देवनागरी")):
            with self.subTest(name=name):
                path = named_storage_path(name, storage=self.storage, namespace=namespace)
                self.assertEqual(path.parent, self.storage.resolve())
                agent = self.connect(name, namespace=namespace)
                self.assertEqual(Path(agent.guard.store.path).resolve().parent, self.storage.resolve())
        self.assertFalse((self.directory / "outside.sqlite3").exists())

    def test_database_and_sqlite_sidecar_symlinks_are_refused_before_connection(self):
        database = named_storage_path("refund-desk", storage=self.storage)
        original_lstat = Path.lstat
        for blocked in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
            with self.subTest(path=blocked.name):
                # Emulate the filesystem answer without requiring Windows symlink privilege.
                def observed_stat(path):
                    return (SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
                            if path == blocked else original_lstat(path))
                with patch("context_relay.persistence.Path.lstat",
                           autospec=True, side_effect=observed_stat):
                    with self.assertRaises((ValueError, OSError)):
                        self.connect()
        self.assertEqual(self.calls, [])

    def test_conflicting_identity_or_database_options_reject_before_connection(self):
        guard = ContextGuard()
        self.addCleanup(guard.close)
        for options in ({"guard": guard}, {"session_id": guard.session_id},
                        {"config": RelayConfig(db_path=str(self.directory / "other.sqlite3"))}):
            with self.subTest(options=tuple(options)), self.assertRaises(ValueError):
                self.connect(**options)
        self.assertFalse(self.storage.exists())
        self.assertEqual(self.calls, [])

    def test_storage_and_nondefault_namespace_require_a_name(self):
        for options in ({"storage": self.storage}, {"namespace": "team-a"}):
            with self.subTest(options=tuple(options)), self.assertRaises(ValueError):
                auto_connect(lambda **kwargs: "Saved locally.", **options)
        self.assertFalse(self.storage.exists())


if __name__ == "__main__":
    unittest.main()
