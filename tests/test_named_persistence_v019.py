"""Named storage validation, ordinary connections and lifetime boundaries."""
import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import ContextGuard, RelayConfig, auto_connect
from context_relay.models import encode
from context_relay.persistence import named_connection_config, named_storage_path
from context_relay.store import RelayStore


def answer(*, messages, **kwargs):
    return "Acknowledged."


class NamedPersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_path_is_data_only_hash_of_namespace_and_exact_name(self):
        directory = self.directory / "not-created"
        path = named_storage_path("../private/name", storage=directory, namespace="project")
        digest = hashlib.sha256(encode(["project", "../private/name"]).encode("utf-8")).hexdigest()
        self.assertEqual(path, directory.resolve() / (digest + ".sqlite3"))
        self.assertFalse(directory.exists())
        self.assertNotIn("private", path.name)
        self.assertNotEqual(path, named_storage_path("../private/name", storage=directory, namespace="other"))
        self.assertNotEqual(named_storage_path("é", storage=directory), named_storage_path("e\u0301", storage=directory))

    @unittest.skipUnless(os.name == "nt", "Windows canonical-path creation race")
    def test_windows_concurrent_parent_creation_does_not_reject_named_path(self):
        import ntpath

        directory = self.directory / "created-by-peer"
        resolve = ntpath._getfinalpathname

        def create_parent_after_missing_leaf(path):
            try:
                return resolve(path)
            except OSError as error:
                # On Python 3.10 the change from missing parent (3) to missing
                # file (2) leaves a \\?\ prefix on the resolved database path.
                if (str(path).endswith(".sqlite3") and error.winerror == 3
                        and not directory.exists()):
                    directory.mkdir()
                raise

        with patch("ntpath._getfinalpathname", create_parent_after_missing_leaf):
            path = named_storage_path("shared", storage=directory)
        self.assertEqual(path.parent, directory.resolve())

    def test_invalid_labels_and_storage_do_not_create_files(self):
        for name in ("", " ", "x" * 201, "\ud800", "nul\x00name", True, 1):
            with self.subTest(name=repr(name)):
                with self.assertRaises((ValueError, TypeError)):
                    named_storage_path(name, storage=self.directory / "unused")
                with self.assertRaises((ValueError, TypeError)):
                    named_storage_path("valid", storage=self.directory / "unused", namespace=name)
        for storage in ("", " ", "has\x00nul", 1, True, {}):
            with self.assertRaises((ValueError, TypeError)):
                named_storage_path("valid", storage=storage)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_default_stays_ephemeral_and_named_default_directory_is_explicit(self):
        previous = Path.cwd()
        try:
            os.chdir(self.directory)
            with auto_connect(answer) as first:
                first.remember("key", "value")
                first_id = first.session_id
                self.assertEqual(first.config.db_path, ":memory:")
            with auto_connect(answer) as second:
                self.assertNotEqual(second.session_id, first_id)
                self.assertEqual(second.guard.store.facts(second.session_id), {})
            self.assertFalse((self.directory / ".relay").exists())
            with auto_connect(answer, conversation="chosen") as persistent:
                # Windows temp roots can use an 8.3 alias; storage returns a resolved path.
                expected = (self.directory / ".relay" / "sessions").resolve()
                self.assertEqual(Path(persistent.config.db_path).parent, expected)
        finally:
            os.chdir(previous)

    def test_persisted_memory_facts_and_id_reopen_without_model_call(self):
        calls = []
        def provider(*, messages):
            calls.append(messages)
            return "Saved."
        with auto_connect(provider, conversation="order", storage=self.directory) as first:
            first_id = first.session_id
            first.remember("order_id", "A-12")
            first(messages=[{"role": "user", "content": "Export as JSON."}])
            self.assertEqual(first.memory.entries()[0]["value"], "JSON")
        with auto_connect(provider, conversation="order", storage=self.directory) as second:
            self.assertEqual(second.session_id, first_id)
            self.assertEqual(second.memory.entries()[0]["value"], "JSON")
            self.assertEqual(second.guard.store.facts(second.session_id)["order_id"]["value"], "A-12")
        self.assertEqual(len(calls), 1)

    def test_helper_returns_new_config_and_one_root(self):
        original = RelayConfig(name="Host workflow")
        settings, first_id = named_connection_config(original, "name", storage=self.directory)
        again, second_id = named_connection_config(original, "name", storage=self.directory)
        self.assertEqual(first_id, second_id)
        self.assertEqual(settings, again)
        self.assertEqual(original.db_path, ":memory:")
        with RelayStore(settings.db_path) as store:
            self.assertEqual(len(store.list_sessions()), 1)
            self.assertEqual(store.get_session(first_id)["name"], "Host workflow")

    def test_invalid_named_option_combinations_do_not_open_storage(self):
        with ContextGuard() as guard:
            for options in ({"guard": guard}, {"session_id": "explicit"},
                            {"config": RelayConfig(db_path=str(self.directory / "other.sqlite3"))}):
                with self.assertRaises(ValueError):
                    auto_connect(answer, conversation="name", storage=self.directory, **options)
        for options in ({"storage": self.directory}, {"namespace": "tenant"}, {"namespace": None}):
            with self.assertRaises(ValueError):
                auto_connect(answer, **options)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_config_file_can_configure_nonstorage_settings(self):
        config = self.directory / "config.json"
        config.write_text(json.dumps({"name": "Configured", "max_chars": 22000}), encoding="utf-8")
        with auto_connect(answer, conversation="name", storage=self.directory / "sessions", config=config) as agent:
            self.assertEqual(agent.config.max_chars, 22000)
            self.assertEqual(agent.guard.store.get_session(agent.session_id)["name"], "Configured")
        config.write_text(json.dumps({"db_path": "different.sqlite3"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            auto_connect(answer, conversation="name", storage=self.directory / "sessions", config=config)

    def test_temporary_store_closes_on_success_and_failure(self):
        opened = []
        class TrackingStore(RelayStore):
            def __init__(self, path, **options):
                super().__init__(path, **options)
                opened.append(self)
        with patch("context_relay.persistence.RelayStore", TrackingStore):
            named_connection_config(RelayConfig(), "ok", storage=self.directory)
        self.assertTrue(opened[0]._closed)
        class FailingStore(TrackingStore):
            def create_session(self, *args, **kwargs):
                raise RuntimeError("setup failure")
        with patch("context_relay.persistence.RelayStore", FailingStore):
            with self.assertRaisesRegex(RuntimeError, "setup failure"):
                named_connection_config(RelayConfig(), "failed", storage=self.directory)
        self.assertTrue(opened[-1]._closed)
        settings, _ = named_connection_config(RelayConfig(), "failed", storage=self.directory)
        with RelayStore(settings.db_path) as store:
            self.assertEqual(len(store.list_sessions()), 1)

    def test_closing_connection_closes_owned_guard(self):
        agent = auto_connect(answer, conversation="close", storage=self.directory)
        store = agent.guard.store
        agent.close()
        self.assertTrue(store._closed)
        with self.assertRaises(RuntimeError):
            agent(messages=[{"role": "user", "content": "Continue."}])

    def test_plain_async_connection_reopens_history(self):
        async def provider(*, messages):
            return "Async answer."
        async def run():
            with auto_connect(provider, conversation="async", storage=self.directory) as agent:
                await agent.conversation().send("First", request_id="one")
                identity = agent.session_id
            with auto_connect(provider, conversation="async", storage=self.directory) as agent:
                self.assertEqual(agent.session_id, identity)
                chat = agent.conversation()
                self.assertEqual(chat.history[0]["content"], "First")
                await chat.send("Second", request_id="two")
                self.assertEqual(len(chat.history), 4)
        asyncio.run(run())

    def test_regular_sidecar_disappearance_is_allowed(self):
        database = named_storage_path("race", storage=self.directory)
        original = Path.lstat
        def transient(path, *args, **kwargs):
            if str(path).endswith(("-wal", "-shm")):
                raise FileNotFoundError("Another SQLite connection removed its sidecar")
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", transient):
            self.assertEqual(named_storage_path("race", storage=self.directory), database)

    def test_generated_directory_is_not_used_as_database_or_sidecar(self):
        for suffix in ("", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                label = "directory-" + suffix
                path = Path(str(named_storage_path(label, storage=self.directory)) + suffix)
                path.mkdir()
                with self.assertRaises(ValueError):
                    named_storage_path(label, storage=self.directory)


if __name__ == "__main__":
    unittest.main()
