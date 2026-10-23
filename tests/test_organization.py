"""Scoped application boundary tests; all model functions are offline stand-ins."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

from context_relay import RelayConfig
from context_relay.organization import AccessDenied, CapacityExceeded, Principal, SessionManager


ALICE = Principal("team-a", "alice")
BOB = Principal("team-a", "bob")
OTHER = Principal("team-b", "alice")


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []

    def model(self, *, messages):
        self.calls.append(messages)
        return "Offline acknowledgement."

    def manager(self, **options):
        manager = SessionManager(Path(self.directory.name) / "scoped", **options)
        self.addCleanup(manager.close)
        return manager

    def test_denied_setup_does_not_create_storage_or_call_provider(self):
        manager = self.manager(authorize=lambda principal, action: False)
        with self.assertRaises(AccessDenied):
            manager.connect(self.model, principal=ALICE, provider="plain")
        self.assertFalse(manager.directory.exists())
        self.assertEqual(self.calls, [])
        self.assertEqual(manager.metrics()["denied"], 1)

    def test_truthy_nonboolean_permission_is_denied(self):
        manager = self.manager(authorize=lambda principal, action: "yes")
        with self.assertRaises(AccessDenied):
            manager.connect(self.model, principal=ALICE, provider="plain")

    def test_denied_config_path_is_not_read(self):
        manager = self.manager(authorize=lambda principal, action: False)
        with self.assertRaises(AccessDenied):
            manager.connect(self.model, principal=ALICE, provider="plain", config="nonexistent-private-config.json")
        self.assertFalse(manager.directory.exists())

    def test_same_user_in_other_tenant_and_other_user_cannot_resume(self):
        manager = self.manager(authorize=lambda principal, action: True)
        with manager.connect(self.model, principal=ALICE, provider="plain") as first:
            session_id = first.session_id
            first.conversation().send("Export as CSV.")
        for principal in (BOB, OTHER):
            with self.assertRaises(AccessDenied):
                manager.connect(self.model, principal=principal, provider="plain", session_id=session_id)
        self.assertEqual(len(list(manager.directory.glob("*.sqlite3"))), 1)
        self.assertEqual(len(self.calls), 1)

    def test_owner_resumes_memory_and_original_history(self):
        manager = self.manager(authorize=lambda principal, action: True)
        with manager.connect(self.model, principal=ALICE, provider="plain") as first:
            chat = first.conversation()
            chat.send("Export as CSV.")
            session_id = chat.session_id
        with manager.connect(self.model, principal=ALICE, provider="plain", session_id=session_id) as second:
            chat = second.conversation()
            self.assertEqual(len(chat.history), 2)
            chat.send("Export as JSON.")
            self.assertEqual(chat.memory_snapshot()["entries"][0]["value"], "JSON")

    def test_existing_connection_rechecks_revocation_for_calls_and_reads(self):
        permitted = True
        manager = self.manager(authorize=lambda principal, action: permitted)
        connection = manager.connect(self.model, principal=ALICE, provider="plain")
        chat = connection.conversation()
        permitted = False
        for operation in (lambda: chat.send("Hello"), lambda: chat.history, lambda: connection.session_id):
            with self.assertRaises(AccessDenied):
                operation()
        connection.close()  # Revocation must not prevent resource cleanup.
        self.assertEqual(self.calls, [])

    def test_permission_revoked_during_provider_call_withholds_result(self):
        permitted = True
        def model(*, messages):
            nonlocal permitted
            permitted = False
            return "Do not return after revocation."
        manager = self.manager(authorize=lambda principal, action: permitted)
        with manager.connect(model, principal=ALICE) as connection:
            with self.assertRaises(AccessDenied):
                connection(messages=[{"role": "user", "content": "Hello"}])
        self.assertEqual(manager.metrics()["active"], 0)
        self.assertEqual(manager.metrics()["failed"], 1)

    def test_scoped_database_and_guard_cannot_be_overridden(self):
        manager = self.manager(authorize=lambda principal, action: True)
        for options in ({"guard": None}, {"config": RelayConfig(db_path="elsewhere.sqlite3")}):
            with self.assertRaises(ValueError):
                manager.connect(self.model, principal=ALICE, provider="plain", **options)
        self.assertFalse(manager.directory.exists())

    def test_metrics_contain_counts_not_messages_or_identities(self):
        manager = self.manager(authorize=lambda principal, action: True)
        with manager.connect(self.model, principal=ALICE, provider="plain") as connection:
            connection.conversation().send("The project owner is Mira.")
        metrics = manager.metrics()
        self.assertEqual(metrics["active"], 0)
        self.assertGreaterEqual(metrics["completed"], 3)
        self.assertTrue(all(type(value) in (int, float) for value in metrics.values()))
        self.assertNotIn("Mira", json.dumps(metrics))
        self.assertNotIn("alice", json.dumps(metrics))

    def test_capacity_rejects_without_extra_provider_call_and_cleanup_waits(self):
        entered, release = threading.Event(), threading.Event()
        errors = []
        def model(*, messages):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test release")
            return "done"
        manager = self.manager(authorize=lambda principal, action: True, max_concurrent=1)
        first = manager.connect(model, principal=ALICE)
        second = manager.connect(self.model, principal=BOB, provider="plain")
        def worker():
            try:
                first(messages=[{"role": "user", "content": "Hello"}])
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(CapacityExceeded):
                second(messages=[{"role": "user", "content": "Hello"}])
            with self.assertRaises(CapacityExceeded):
                first.close()
            with self.assertRaises(CapacityExceeded):
                manager.close()
        finally:
            release.set()
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.calls, [])
        self.assertEqual(manager.metrics()["active"], 0)

    def test_manager_close_closes_connections_without_closing_provider(self):
        manager = self.manager(authorize=lambda principal, action: True)
        connection = manager.connect(self.model, principal=ALICE, provider="plain")
        manager.close()
        with self.assertRaises(RuntimeError):
            connection(messages=[{"role": "user", "content": "Hello"}])

    def test_new_session_invalid_provider_does_not_create_database(self):
        manager = self.manager(authorize=lambda principal, action: True)
        with self.assertRaises(TypeError):
            manager.connect(object(), principal=ALICE)
        self.assertFalse(manager.directory.exists())

    def test_principal_is_not_accepted_from_a_dictionary(self):
        manager = self.manager(authorize=lambda principal, action: True)
        with self.assertRaises(TypeError):
            manager.connect(self.model, principal={"tenant": "team-a", "subject": "alice"})
        self.assertFalse(manager.directory.exists())


class OrganizationAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_releases_capacity_and_retains_pending_request(self):
        entered = asyncio.Event()
        async def model(*, messages):
            entered.set()
            await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as directory:
            with SessionManager(directory, authorize=lambda principal, action: True, max_concurrent=1) as manager:
                async with manager.connect(model, principal=ALICE) as connection:
                    chat = connection.conversation()
                    task = asyncio.create_task(chat.send("Export as CSV."))
                    await asyncio.wait_for(entered.wait(), 3)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertIsNotNone(chat.pending)
                    self.assertEqual(manager.metrics()["active"], 0)
                    self.assertEqual(manager.metrics()["failed"], 1)


if __name__ == "__main__":
    unittest.main()
