"""Named-owner storage and exact-contract tool resumption with local SQLite."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import ActionRejected, ContextGuard, RelayConfig, auto_connect
from context_relay.actions import ActionHarness
from context_relay.models import encode
from context_relay.organization import AccessDenied, Principal, SessionManager
from context_relay.persistence import named_storage_path
from context_relay.tools import ToolRegistry


ALICE = Principal("team", "alice")
BOB = Principal("team", "bob")
OTHER_TEAM = Principal("other-team", "alice")
HISTORY = [{"role": "user", "content": "Export as JSON."}]


def owner_path(directory, principal):
    digest = hashlib.sha256(encode([principal.tenant, principal.subject]).encode()).hexdigest()
    return Path(directory) / digest


class ResumeToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.calls = []
        self.guard.remember("amount", 10)

    def test_identical_resume_attaches_new_function_without_policy_or_revision_write(self):
        original = ToolRegistry(self.guard)
        original.register(lambda amount: None, name="charge", bindings={"amount": "amount"})
        before = self.guard.store.events(self.guard.session_id)
        revision = self.guard.turn().revision
        resumed = ToolRegistry(self.guard)
        resumed.register(lambda amount: self.calls.append(amount), name="charge",
                         bindings={"amount": "amount"}, resume=True)
        self.assertEqual(self.guard.turn().revision, revision)
        self.assertEqual(self.guard.store.events(self.guard.session_id), before)
        self.assertTrue(resumed.coverage()["tools"]["charge"]["resumed"])
        resumed.bind(self.guard.turn()).dispatch("charge", {"amount": 10})
        self.assertEqual(self.calls, [10])

    def test_resume_new_contract_registers_normally(self):
        registry = ToolRegistry(self.guard)
        before = self.guard.turn().revision
        registry.register(lambda amount: self.calls.append(amount), name="charge",
                          bindings={"amount": "amount"}, resume=True)
        self.assertEqual(self.guard.turn().revision, before + 1)
        self.assertFalse(registry.coverage()["tools"]["charge"]["resumed"])

    def test_changed_contract_and_invalid_resume_options_cannot_write(self):
        ToolRegistry(self.guard).register(lambda amount: None, name="charge", bindings={"amount": "amount"})
        before = self.guard.store.events(self.guard.session_id)
        for options in ({"unchecked": ("amount",), "resume": True},
                        {"bindings": {"amount": "other"}, "resume": True},
                        {"bindings": {"amount": "amount"}, "title": "Changed", "resume": True},
                        {"bindings": {"amount": "amount"}, "resume": True, "replace": True},
                        {"bindings": {"amount": "amount"}, "resume": 1}):
            with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                ToolRegistry(self.guard).register(lambda amount: None, name="charge", **options)
        self.assertEqual(self.guard.store.events(self.guard.session_id), before)

    def test_new_default_is_checked_against_identical_contract(self):
        ToolRegistry(self.guard).register(lambda amount=10, note="old": None, name="charge",
                                         bindings={"amount": "amount"}, unchecked=("note",))
        resumed = ToolRegistry(self.guard)
        resumed.register(lambda amount=999, note="new": self.calls.append((amount, note)), name="charge",
                         bindings={"amount": "amount"}, unchecked=("note",), resume=True)
        with self.assertRaises(ActionRejected):
            resumed.bind(self.guard.turn()).dispatch("charge", {})
        self.assertEqual(self.calls, [])
        resumed.bind(self.guard.turn()).dispatch("charge", {"amount": 10})
        self.assertEqual(self.calls, [(10, "new")])
        self.assertEqual(resumed.coverage()["tools"]["charge"]["unchecked"], ["note"])

    def test_resume_does_not_refresh_an_executed_turn_or_changed_permission(self):
        self.guard.remember("approved", True)
        policy = {"bindings": {"amount": "amount"}, "when": {"approved": True}}
        first = ToolRegistry(self.guard)
        first.register(lambda amount: self.calls.append(amount), name="charge", **policy)
        old_turn = self.guard.turn()
        first.bind(old_turn).dispatch("charge", {"amount": 10})
        resumed = ToolRegistry(self.guard)
        resumed.register(lambda amount: self.calls.append(amount), name="charge", resume=True, **policy)
        with self.assertRaises(ActionRejected):
            resumed.bind(old_turn).dispatch("charge", {"amount": 10})
        self.guard.remember("approved", False)
        with self.assertRaises(ActionRejected):
            resumed.bind(self.guard.turn()).dispatch("charge", {"amount": 10})
        self.assertEqual(self.calls, [10])

    def test_resumed_guard_does_not_accept_pre_restart_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.sqlite3"
            with ContextGuard(path) as first:
                session_id = first.session_id
                registry = ToolRegistry(first)
                registry.register(lambda: None, name="run")
                turn = first.turn()
            with ContextGuard(path, session_id=session_id) as second:
                registry = ToolRegistry(second)
                registry.register(lambda: self.calls.append(True), name="run", resume=True)
                with self.assertRaises(ValueError):
                    registry.bind(turn)
        self.assertEqual(self.calls, [])


class ResumeActionHarnessTests(unittest.TestCase):
    def test_named_harness_reopens_without_session_id_and_invokes_only_new_function(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            provider = lambda **kwargs: {"tool": "save", "arguments": {"format": "json"}}
            with auto_connect(provider, conversation="export", storage=directory) as first:
                harness = ActionHarness(first, {"save": lambda format: calls.append(("old", format))}, resume=True)
                self.assertTrue(harness.invoke(messages=HISTORY).executed)
                session_id = first.session_id
            with auto_connect(provider, conversation="export", storage=directory) as second:
                self.assertEqual(second.session_id, session_id)
                revision = second.guard.turn().revision
                events = second.guard.store.events(session_id)
                harness = second.protect_tools({"save": lambda format: calls.append(("new", format))}, resume=True)
                self.assertEqual(second.guard.turn().revision, revision)
                self.assertEqual(second.guard.store.events(session_id), events)
                self.assertTrue(harness.coverage()["tools"]["save"]["resumed"])
                self.assertTrue(harness.invoke(messages=HISTORY).executed)
        self.assertEqual(calls, [("old", "JSON"), ("new", "JSON")])

    def test_entire_inventory_rolls_back_if_later_resume_contract_changed(self):
        with auto_connect(lambda **kwargs: None) as agent:
            agent.remember("amount", 10)
            ActionHarness(agent, {"charge": lambda amount: None},
                          contracts={"charge": {"bindings": {"amount": "amount"}}})
            before = agent.guard.store.events(agent.session_id)
            policies = agent.guard.store.policies(agent.session_id)
            revision = agent.guard.turn().revision
            with self.assertRaises(ValueError):
                ActionHarness(agent, {"new_tool": lambda: None, "charge": lambda amount: None},
                              contracts={"charge": {"unchecked": ("amount",)}}, resume=True)
            self.assertEqual(agent.guard.turn().revision, revision)
            self.assertEqual(agent.guard.store.policies(agent.session_id), policies)
            self.assertEqual(agent.guard.store.events(agent.session_id), before)

    def test_resuming_mixed_existing_and_new_inventory_only_writes_new_policy(self):
        with auto_connect(lambda **kwargs: None) as agent:
            ActionHarness(agent, {"existing": lambda: None})
            revision = agent.guard.turn().revision
            harness = ActionHarness(agent, {"existing": lambda: None, "new": lambda: None}, resume=True)
            self.assertEqual(agent.guard.turn().revision, revision + 1)
            report = harness.coverage()["tools"]
            self.assertTrue(report["existing"]["resumed"])
            self.assertFalse(report["new"]["resumed"])


class NamedOrganizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "sessions"
        self.calls = []

    def model(self, **kwargs):
        self.calls.append(kwargs)
        return "Recorded locally."

    def manager(self, authorize=lambda principal, action: True):
        manager = SessionManager(self.directory, authorize=authorize)
        self.addCleanup(manager.close)
        return manager

    def test_same_named_owner_resumes_original_history_and_memory_after_manager_restart(self):
        manager = self.manager()
        with manager.connect(self.model, principal=ALICE, conversation="work", provider="plain") as first:
            chat = first.conversation()
            chat.send("Export as CSV.")
            session_id = chat.session_id
        manager.close()
        reopened = self.manager()
        with reopened.connect(self.model, principal=ALICE, conversation="work", provider="plain") as second:
            chat = second.conversation()
            self.assertEqual(chat.session_id, session_id)
            self.assertEqual(len(chat.history), 2)
            self.assertEqual(chat.memory_snapshot()["entries"][0]["value"], "CSV")
            chat.send("Export as JSON.")
            self.assertEqual(chat.memory_snapshot()["entries"][0]["value"], "JSON")
        self.assertEqual(len(self.calls), 2)

    def test_names_users_and_tenants_have_separate_memory_and_history(self):
        manager = self.manager()
        ids = []
        for principal, name in ((ALICE, "work"), (ALICE, "personal"), (BOB, "work"), (OTHER_TEAM, "work")):
            with manager.connect(self.model, principal=principal, conversation=name, provider="plain") as connection:
                chat = connection.conversation()
                ids.append(chat.session_id)
                self.assertEqual(chat.history, [])
                self.assertEqual(chat.memory_snapshot()["entries"], [])
                chat.send("Export as JSON.")
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(len(list(self.directory.glob("*/*.sqlite3"))), 4)
        self.assertEqual(len([path for path in self.directory.iterdir() if path.is_dir()]), 3)

    def test_denied_named_connect_creates_no_storage_and_existing_reads_recheck_permission(self):
        permitted = False
        manager = self.manager(lambda principal, action: permitted)
        with self.assertRaises(AccessDenied):
            manager.connect(self.model, principal=ALICE, conversation="work", provider="plain")
        self.assertFalse(self.directory.exists())
        permitted = True
        connection = manager.connect(self.model, principal=ALICE, conversation="work", provider="plain")
        chat = connection.conversation()
        permitted = False
        for operation in (lambda: connection.session_id, lambda: chat.history,
                          lambda: chat.memory_snapshot(), lambda: chat.send("Hello")):
            with self.subTest(operation=operation), self.assertRaises(AccessDenied):
                operation()
        connection.close()
        self.assertEqual(self.calls, [])

    def test_named_scoped_overrides_and_ambiguous_session_selection_fail_before_storage(self):
        manager = self.manager()
        options = ({"storage": self.temp.name}, {"storage": None}, {"namespace": "other"},
                   {"namespace": None}, {"guard": None}, {"session_id": "another-id"},
                   {"config": RelayConfig(db_path=str(Path(self.temp.name) / "outside.sqlite3"))})
        for option in options:
            with self.subTest(option=option), self.assertRaises(ValueError):
                manager.connect(self.model, principal=ALICE, conversation="work", provider="plain", **option)
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.calls, [])

    def test_owner_and_conversation_labels_are_hashed_within_scoped_directory(self):
        principal = Principal("../../tenant", "../owner\\name")
        conversation = "../../outside/नाम"
        manager = self.manager()
        with manager.connect(self.model, principal=principal, conversation=conversation, provider="plain"):
            pass
        owner = owner_path(manager.directory, principal)
        expected = named_storage_path(conversation, storage=owner, namespace="owner")
        databases = list(manager.directory.rglob("*.sqlite3"))
        self.assertEqual(databases, [expected])
        self.assertEqual(expected.parent, owner)
        self.assertEqual(owner.parent, manager.directory)
        self.assertEqual(len(owner.name), 64)
        self.assertEqual(len(expected.stem), 64)
        self.assertFalse((Path(self.temp.name) / "outside").exists())

    def test_existing_owner_link_is_refused_before_named_helper_or_provider(self):
        manager = self.manager()
        owner = owner_path(manager.directory, ALICE)
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == owner or original(path)):
            with self.assertRaises(AccessDenied):
                manager.connect(self.model, principal=ALICE, conversation="work", provider="plain")
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.calls, [])

    def test_owner_resolution_alias_to_sibling_owner_is_refused(self):
        manager = self.manager()
        with manager.connect(self.model, principal=BOB, conversation="work", provider="plain") as bob:
            bob.conversation().send("Export as CSV.")
        alice_path, bob_path = owner_path(manager.directory, ALICE), owner_path(manager.directory, BOB)
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            # Model a Windows directory junction: resolution changes owner,
            # while is_symlink remains false and the resolved parent is local.
            return bob_path if path == alice_path else original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve):
            with self.assertRaises(AccessDenied):
                manager.connect(self.model, principal=ALICE, conversation="work", provider="plain")
        self.assertEqual(len(self.calls), 1)

    def test_existing_file_cannot_be_used_as_owner_directory(self):
        manager = self.manager()
        self.directory.mkdir()
        owner = owner_path(manager.directory, ALICE)
        owner.write_text("untouched", encoding="utf-8")
        with self.assertRaises((OSError, ValueError)):
            manager.connect(self.model, principal=ALICE, conversation="work", provider="plain")
        self.assertEqual(owner.read_text(encoding="utf-8"), "untouched")
        self.assertEqual(self.calls, [])

    def test_unnamed_sessions_still_create_distinct_workflows(self):
        manager = self.manager()
        with manager.connect(self.model, principal=ALICE, provider="plain") as first:
            session_id = first.session_id
            first.conversation().send("Export as CSV.")
        with manager.connect(self.model, principal=ALICE, provider="plain") as second:
            self.assertNotEqual(second.session_id, session_id)
            self.assertEqual(second.conversation().memory_snapshot()["entries"], [])
        self.assertEqual(len(list(self.directory.glob("*.sqlite3"))), 1)


class AsyncNamedOrganizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_named_resume_restores_memory_and_rechecks_authorization(self):
        calls = []
        permitted = True
        async def provider(**kwargs):
            calls.append(kwargs)
            return "Recorded locally."
        with tempfile.TemporaryDirectory() as directory:
            with SessionManager(directory, authorize=lambda principal, action: permitted) as manager:
                async with manager.connect(provider, principal=ALICE, conversation="work") as first:
                    chat = first.conversation()
                    await chat.send("Export as JSON.")
                    session_id = chat.session_id
                async with manager.connect(provider, principal=ALICE, conversation="work") as second:
                    chat = second.conversation()
                    self.assertEqual(chat.session_id, session_id)
                    self.assertEqual(chat.memory_snapshot()["entries"][0]["value"], "JSON")
                    permitted = False
                    with self.assertRaises(AccessDenied):
                        await chat.send("Continue")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
