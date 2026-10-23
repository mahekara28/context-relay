"""Local storage permissions and maintenance; no models or external services."""
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from context_relay import auto_connect
from context_relay._storage_files import private_directory
from context_relay.conversation import ConversationPending
from context_relay.organization import AccessDenied, Principal, SessionManager
from context_relay.outcomes import OutcomePolicy
from context_relay.responses_workflow import ResponsesWorkflow, WorkflowPending
from context_relay.storage_privacy import HistoryRetention
from context_relay.store import RelayStore


class _StorageCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def store(self, path=None, **kwargs):
        store = RelayStore(self.directory / "store.sqlite3" if path is None else path, **kwargs)
        self.addCleanup(store.close)
        return store

    def agent(self, provider=None, *, name="retention"):
        agent = auto_connect(provider or (lambda *, messages: "Acknowledged."),
                             conversation=name, storage=self.directory / "named")
        self.addCleanup(agent.close)
        return agent


class StoragePrivacyTests(_StorageCase):
    def test_status_discloses_plaintext_without_leaking_content_or_paths(self):
        store = self.store()
        session = store.create_session("secret-customer-name")
        store.record_fact(session["id"], "private-field", "private-value")
        status = store.privacy_status()
        self.assertTrue(status["persistent_plaintext"])
        self.assertFalse(status["encrypted_by_relay"])
        self.assertFalse(status["physical_erasure_guaranteed"])
        self.assertFalse(status["permissions"]["current_access_verified"])
        self.assertTrue(status["secure_delete"])
        self.assertEqual(status["temporary_storage"], "memory")
        serialized = json.dumps(status)
        for excluded in ("secret-customer-name", "private-field", "private-value", str(self.directory), session["id"]):
            self.assertNotIn(excluded, serialized)
        store.checkpoint(mode="truncate")
        self.assertIn(b"private-value", Path(store.path).read_bytes())  # It is actually plaintext.

    def test_memory_status_and_checkpoint_do_not_claim_disk_encryption(self):
        store = self.store(":memory:")
        status = store.privacy_status()
        self.assertEqual(status["storage"], "memory")
        self.assertFalse(status["persistent_plaintext"])
        self.assertFalse(status["encrypted_by_relay"])
        self.assertTrue(status["process_memory_plaintext"])
        self.assertEqual(status["creation_protection"], "memory")
        self.assertFalse(store.checkpoint(mode="truncate")["wal_truncated"])
        self.assertFalse(store.checkpoint()["wal_available"])

    @unittest.skipUnless(os.name == "nt", "Windows DACL integration")
    def test_windows_private_directory_database_and_sidecars_have_protected_owner_system_access(self):
        private = self.directory / "private" / "sessions"
        private_directory(private)
        store = self.store(private / "store.sqlite3")
        store.create_session("ensure WAL exists")
        # Query OS ACLs rather than treating Windows chmod as an access check.
        command = r'''
$ErrorActionPreference = 'Stop'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$paths = @($env:RELAY_TEST_PRIVATE, ($env:RELAY_TEST_PRIVATE + '\store.sqlite3'),
    ($env:RELAY_TEST_PRIVATE + '\store.sqlite3-wal'), ($env:RELAY_TEST_PRIVATE + '\store.sqlite3-shm'))
$rows = foreach ($path in $paths) {
    $acl = if ([System.IO.Directory]::Exists($path)) {
        [System.IO.Directory]::GetAccessControl($path)
    } else { [System.IO.File]::GetAccessControl($path) }
    $rules = $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
    $allowed = @($rules | Where-Object { $_.AccessControlType -eq 'Allow' })
    $other = @($allowed | Where-Object {
        $sid = $_.IdentityReference.Value
        $sid -ne $identity -and $sid -ne 'S-1-5-18'
    })
    [pscustomobject]@{ private = ($other.Count -eq 0); allows = $allowed.Count; protected = $acl.AreAccessRulesProtected }
}
ConvertTo-Json -InputObject @($rows) -Compress
'''
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            env={**os.environ, "RELAY_TEST_PRIVATE": str(private)}, text=True,
            capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(len(observed), 4)
        self.assertTrue(all(row["private"] and row["allows"] == 2 for row in observed), observed)
        self.assertTrue(observed[0]["protected"])
        self.assertTrue(observed[1]["protected"])
        self.assertEqual(store.privacy_status()["creation_protection"], "windows_owner_system_dacl")

    @unittest.skipUnless(os.name == "posix", "POSIX permission integration")
    def test_posix_creation_has_owner_only_modes_without_changing_existing_directory(self):
        previous = stat.S_IMODE(self.directory.stat().st_mode)
        private = self.directory / "private" / "sessions"
        private_directory(private)
        store = self.store(private / "store.sqlite3")
        store.create_session("write")
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
        for path in (Path(store.path), Path(store.path + "-wal"), Path(store.path + "-shm")):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), previous)
        self.assertEqual(store.privacy_status()["creation_protection"], "posix_owner_only")

    def test_existing_database_permissions_are_not_silently_changed(self):
        store = self.store()
        identity = store.create_session("existing")["id"]
        store.close()
        before = Path(store.path).stat().st_mode
        reopened = self.store()
        self.assertEqual(reopened.get_session(identity)["id"], identity)
        self.assertEqual(Path(store.path).stat().st_mode, before)
        self.assertEqual(reopened.privacy_status()["creation_protection"], "existing_permissions_unchanged")

    def test_existing_symlink_or_device_path_rejected_before_sqlite_open(self):
        path = self.directory / "store.sqlite3"
        real_lstat = Path.lstat
        for suffix in ("", "-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                def inspect_path(selected, *args, **kwargs):
                    if selected == Path(str(path) + suffix):
                        return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
                    return real_lstat(selected, *args, **kwargs)
                with patch.object(Path, "lstat", inspect_path), patch("context_relay.store.sqlite3.connect") as connect:
                    with self.assertRaises(ValueError):
                        RelayStore(path)
                    connect.assert_not_called()

    def test_failed_permission_creation_does_not_fall_back_to_insecure_sqlite_creation(self):
        with patch("context_relay.store.private_database", side_effect=PermissionError("creation denied")), \
                patch("context_relay.store.sqlite3.connect") as connect:
            with self.assertRaises(PermissionError):
                RelayStore(self.directory / "denied.sqlite3")
            connect.assert_not_called()
        self.assertFalse((self.directory / "denied.sqlite3").exists())

    def test_checkpoint_preserves_pending_and_completed_request_records(self):
        def provider(*, messages):
            if messages[-1]["content"] == "second request":
                raise RuntimeError("provider stopped")
            return "Done."
        agent = self.agent(provider)
        chat = agent.conversation()
        chat.send("first request", request_id="completed")
        with self.assertRaises(RuntimeError):
            chat.send("second request", request_id="pending")
        before, pending = chat.deletion_plan(), chat.pending
        result = agent.guard.store.checkpoint(mode="truncate")
        self.assertEqual(result["logical_records_deleted"], 0)
        self.assertEqual(chat.deletion_plan(), before)
        self.assertEqual(chat.pending, pending)
        self.assertFalse(result["physical_erasure_guaranteed"])
        self.assertEqual(agent.guard.store._connection.execute("SELECT COUNT(*) FROM conversation_requests").fetchone()[0], 2)

    def test_checkpoint_busy_reader_is_reported_without_claiming_truncation(self):
        store = self.store(busy_timeout_ms=20)
        store.create_session("first")
        reader = sqlite3.connect(store.path, isolation_level=None)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM sessions").fetchall()
        store.create_session("second")
        result = store.checkpoint(mode="truncate")
        self.assertTrue(result["busy"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["wal_truncated"])
        self.assertEqual(len(store.list_sessions()), 2)
        reader.execute("ROLLBACK")
        self.assertTrue(store.checkpoint(mode="truncate")["wal_truncated"])

    def test_checkpoint_cannot_run_in_a_transaction_or_callback(self):
        store = self.store()
        with store._transaction(write=True):
            with self.assertRaises(ValueError):
                store.checkpoint()
        store._callback_active = True
        try:
            with self.assertRaises(ValueError):
                store.checkpoint()
        finally:
            store._callback_active = False
        for invalid in ("PASSIVE", "truncate);DELETE FROM sessions;", None, 1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                store.checkpoint(mode=invalid)


class HistoryRetentionTests(_StorageCase):
    def test_retention_deletes_only_explicit_unchanged_lineage(self):
        first, other = self.agent(), self.agent(name="keep")
        chat = first.conversation()
        chat.send("Export as CSV.", request_id="one")
        chat.rollover(expected_revision=first.guard.turn().revision)
        second = other.conversation()
        second.send("Keep this history.", request_id="keep")
        retention = HistoryRetention(chat)
        plan = retention.plan()
        self.assertEqual(len(plan["deletion"]["sessions"]), 2)
        self.assertTrue(plan["effects"]["replay_records_removed"])
        deleted = retention.delete(expected=plan)
        self.assertTrue(deleted["logical_deletion_only"])
        self.assertFalse(deleted["external_copies_erased"])
        self.assertEqual(first.guard.store.list_sessions(), [])
        self.assertEqual(len(second.history), 2)

    def test_changed_history_invalidates_retention_plan(self):
        agent = self.agent()
        chat = agent.conversation()
        chat.send("First request.", request_id="first")
        retention = HistoryRetention(chat)
        plan = retention.plan()
        chat.send("Another request.", request_id="second")
        with self.assertRaisesRegex(ValueError, "changed"):
            retention.delete(expected=plan)
        self.assertEqual(len(chat.history), 4)

    def test_pending_provider_request_cannot_be_deleted_even_with_fresh_plan(self):
        def fail(*, messages):
            raise RuntimeError("offline provider failure")
        agent = self.agent(fail)
        chat = agent.conversation()
        with self.assertRaises(RuntimeError):
            chat.send("Export as CSV.", request_id="pending")
        retention = HistoryRetention(chat)
        with self.assertRaises(ConversationPending):
            retention.delete(expected=retention.plan())
        self.assertEqual(chat.pending["request_id"], "pending")

    def test_optional_grace_is_enforced_and_changed_policy_refuses_old_plan(self):
        chat = self.agent().conversation()
        retention = HistoryRetention(chat, grace_seconds=60)
        plan = retention.plan()
        with self.assertRaisesRegex(ValueError, "not elapsed"):
            retention.delete(expected=plan)
        with self.assertRaisesRegex(ValueError, "changed grace"):
            HistoryRetention(chat, grace_seconds=0).delete(expected=plan)
        # The host owns and retains a plan. Freeze the clock instead of sleeping.
        with patch("context_relay.storage_privacy.datetime") as clock:
            clock.fromisoformat.side_effect = datetime.fromisoformat
            clock.now.return_value = datetime.now(timezone.utc) + timedelta(seconds=61)
            self.assertTrue(retention.delete(expected=plan)["logical_deletion_only"])

    def test_scoped_conversation_does_not_unwrap_or_skip_manage_authorization(self):
        allowed = True
        manager = SessionManager(self.directory / "org", authorize=lambda principal, action: allowed or action == "read")
        self.addCleanup(manager.close)
        owner = manager.connect(lambda *, messages: "Done.", principal=Principal("team", "one"), conversation="private")
        chat = owner.conversation()
        chat.send("First request.", request_id="first")
        retention = HistoryRetention(chat)
        plan = retention.plan()
        allowed = False
        with self.assertRaises(AccessDenied):
            retention.delete(expected=plan)
        self.assertEqual(len(chat.history), 2)
        allowed = True
        self.assertTrue(retention.delete(expected=plan)["logical_deletion_only"])

    def test_inconsistent_workflow_pending_metadata_still_preserves_action_receipt(self):
        calls = []
        def tool(value):
            calls.append(value)
            raise KeyboardInterrupt("effect started")
        def provider(**kwargs):
            return {"status": "completed", "output": [{"type": "function_call", "call_id": "call-one",
                "name": "save", "arguments": '{"value":1}', "status": "completed"}]}
        agent = auto_connect(SimpleNamespace(responses=SimpleNamespace(create=provider)),
                             provider="openai", api="responses", native=True)
        self.addCleanup(agent.close)
        workflow = ResponsesWorkflow(agent, {"save": tool}, memory=False,
            contracts={"save": {"unchecked": ("value",), "outcome": OutcomePolicy(required={"/ok": True})}},
            schemas={"save": {"type": "object", "properties": {"value": {"type": "integer"}},
                              "required": ["value"], "additionalProperties": False}})
        with self.assertRaises(KeyboardInterrupt):
            workflow.send("Save one.", request_id="one", model="offline")
        # Even inconsistent state metadata must not erase the action_pending row.
        with agent.guard.store._transaction(write=True):
            state = workflow._state()
            state["pending_id"] = None
            workflow._save_state(state)
        retention = HistoryRetention(workflow)
        with self.assertRaises(WorkflowPending):
            retention.delete(expected=retention.plan())
        self.assertEqual(workflow.request("one")["phase"], "action_pending")
        self.assertEqual(calls, [1])

    def test_scoped_native_read_permission_does_not_authorize_retention_delete(self):
        manage = True
        manager = SessionManager(self.directory / "native-org",
                                 authorize=lambda principal, action: action != "manage" or manage)
        self.addCleanup(manager.close)
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: None))
        owner = manager.connect(client, principal=Principal("team", "one"), conversation="private",
                                provider="openai", api="responses", native=True)
        workflow = owner.responses_workflow({"save": lambda value: {"ok": True}}, memory=False,
            contracts={"save": {"unchecked": ("value",), "outcome": OutcomePolicy(required={"/ok": True})}},
            schemas={"save": {"type": "object", "properties": {"value": {"type": "integer"}},
                              "required": ["value"], "additionalProperties": False}})
        retention = HistoryRetention(workflow)
        plan = retention.plan()
        manage = False
        self.assertEqual(retention.plan()["deletion"], plan["deletion"])  # read stays authorized
        with self.assertRaises(AccessDenied):
            retention.delete(expected=plan)
        manage = True
        self.assertTrue(retention.delete(expected=plan)["logical_deletion_only"])

    def test_no_retry_when_public_delete_raises_typeerror_after_start(self):
        calls = []
        class Facade:
            def deletion_plan(self):
                return {"revision": 1}
            def delete_history(self, *, expected):
                calls.append(expected)
                raise TypeError("host failure after start")
        retention = HistoryRetention(Facade())
        with self.assertRaises(TypeError):
            retention.delete(expected=retention.plan())
        self.assertEqual(len(calls), 1)

    def test_malformed_or_changed_disclosure_is_not_accepted_as_plan(self):
        chat = self.agent().conversation()
        retention = HistoryRetention(chat)
        plan = retention.plan()
        plan["effects"]["external_copies_erased"] = True
        with self.assertRaises(ValueError):
            retention.delete(expected=plan)
        self.assertTrue(chat.deletion_plan()["sessions"])


if __name__ == "__main__":
    unittest.main()
