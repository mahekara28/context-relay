"""Cross-feature history/ownership tests using offline response functions."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import HistoryPolicy, MemoryPolicy, RelayConfig, connect
from context_relay.conversation import ConversationAlreadyCompleted, ConversationArchived, ConversationPending
from context_relay.organization import AccessDenied, Principal, SessionManager


ALICE = Principal("workspace", "alice")
BOB = Principal("workspace", "bob")
OTHER_TENANT = Principal("other-workspace", "alice")


class V011HistoryOrganizationIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.calls = []

    def model(self, *, messages):
        self.calls.append(messages)
        return "Acknowledged."

    def manager(self):
        manager = SessionManager(self.folder / "org", authorize=lambda principal, action: True)
        self.addCleanup(manager.close)
        return manager

    def agent(self, **options):
        agent = connect(self.model, config=RelayConfig(db_path=str(self.folder / "relay.db")),
                        memory=MemoryPolicy(), **options)
        self.addCleanup(agent.close)
        return agent

    def test_scoped_rollover_keeps_exact_original_provenance_and_owner_resume(self):
        manager = self.manager()
        scoped = manager.connect(self.model, principal=ALICE, provider="plain")
        chat = scoped.conversation(history_policy=HistoryPolicy(50))
        original = chat.session_id
        text = "Use JSON for export format."
        chat.send(text, request_id="original")
        chat.send("Continue.", request_id="followup")
        current = chat.session_id
        self.assertNotEqual(current, original)
        source = chat.memory_snapshot()["entries"][0]["source"]
        self.assertEqual(source["session_id"], original)
        record = next(row for row in chat.history_page()["messages"]
                      if row["session_id"] == source["session_id"] and row["index"] == source["index"])
        self.assertEqual(record["message"]["content"][source["start"]:source["end"]], source["quote"])
        self.assertEqual(record["message"]["content"], text)
        for wrong_owner in (BOB, OTHER_TENANT):
            with self.assertRaises(AccessDenied):
                manager.connect(self.model, principal=wrong_owner, provider="plain", session_id=current)
        scoped.close()
        with manager.connect(self.model, principal=ALICE, provider="plain", session_id=current) as resumed:
            recovered = resumed.conversation()
            self.assertEqual(recovered.session_id, current)
            self.assertEqual(len(recovered.search_history("export format")["messages"]), 1)
            self.assertEqual(recovered.memory_snapshot()["entries"][0]["source"]["session_id"], original)

    def test_scoped_completed_request_id_remains_deduplicated_across_rollover(self):
        manager = self.manager()
        scoped = manager.connect(self.model, principal=ALICE, provider="plain")
        chat = scoped.conversation(history_policy=HistoryPolicy(50))
        chat.send("First request", request_id="first")
        chat.send("Second request", request_id="second")
        current = chat.session_id
        with self.assertRaises(ConversationAlreadyCompleted) as caught:
            chat.send("First request", request_id="first")
        self.assertEqual(caught.exception.assistant_message["content"], "Acknowledged.")
        with self.assertRaisesRegex(ValueError, "different text"):
            chat.send("Changed text", request_id="first")
        self.assertEqual(chat.session_id, current)
        self.assertEqual(len(self.calls), 2)

    def test_scoped_index_search_and_deletion_keep_foreign_lineages_and_owners(self):
        manager = self.manager()
        first = manager.connect(self.model, principal=ALICE, provider="plain")
        target = first.conversation(history_policy=HistoryPolicy(50))
        if not target.enable_history_index():
            self.skipTest("FTS5 unavailable")
        target.send("Invoice target zebra", request_id="a")
        target.send("Invoice target otter", request_id="b")
        sibling = manager.connect(self.model, principal=ALICE, provider="plain")
        foreign = manager.connect(self.model, principal=BOB, provider="plain")
        sibling_chat, foreign_chat = sibling.conversation(), foreign.conversation()
        foreign_chat.enable_history_index()
        sibling_chat.send("Invoice sibling secret")
        foreign_chat.send("Invoice different owner secret")
        self.assertEqual([row["message"]["content"] for row in target.search_history("invoice", mode="terms")["messages"]],
                         ["Invoice target zebra", "Invoice target otter"])
        with self.assertRaisesRegex(ValueError, "changed"):
            target.delete_history(sibling_chat.deletion_plan())
        target.delete_history(target.deletion_plan())
        self.assertEqual([row["message"]["content"] for row in sibling_chat.search_history("invoice", mode="terms")["messages"]],
                         ["Invoice sibling secret"])
        self.assertEqual([row["message"]["content"] for row in foreign_chat.search_history("invoice", mode="terms")["messages"]],
                         ["Invoice different owner secret"])
        self.assertEqual(sibling_chat.search_history("zebra", mode="terms")["messages"], [])

    def test_old_segment_deletion_plan_detects_check_added_in_successor(self):
        manager = self.manager()
        scoped = manager.connect(self.model, principal=ALICE, provider="plain")
        old = scoped.conversation()
        current = scoped.conversation(history_policy=HistoryPolicy(50))
        current.send("First")
        current.send("Second")
        plan = old.deletion_plan()
        active = current._chat.agent
        active.guard.relay.check(active.session_id, {
            "tool": "unregistered", "arguments": {}, "context_revision": active.guard.turn().revision,
        })
        with self.assertRaisesRegex(ValueError, "changed"):
            old.delete_history(plan)
        self.assertEqual(len(current.history_page()["messages"]), 4)
        old.delete_history(old.deletion_plan())
        with self.assertRaises(KeyError):
            _ = current.history

    def test_failed_successor_reconnect_retains_recoverable_link_without_new_user(self):
        agent = self.agent()
        chat = agent.conversation(history_policy=HistoryPolicy(50))
        chat.send("Use JSON for export format.", request_id="first")
        original = chat.session_id
        with patch.object(agent, "_conversation_successor", side_effect=ConnectionError("reconnect failed")):
            with self.assertRaises(ConnectionError):
                chat.send("Continue", request_id="next")
        successor = chat.next_session_id
        self.assertIsNotNone(successor)
        self.assertEqual(chat.session_id, original)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(chat.pending)
        with self.assertRaises(ConversationArchived):
            chat.send("Continue", request_id="next")
        with connect(self.model, config=agent.config, memory=MemoryPolicy(), session_id=successor) as resumed:
            recovered = resumed.conversation(history_policy=HistoryPolicy(50))
            recovered.send("Continue", request_id="next")
            self.assertEqual(len(self.calls), 2)
            self.assertEqual(recovered.agent.memory.entries()[0]["source"]["session_id"], original)

    def test_multiple_automatic_segments_keep_first_memory_source_and_close_intermediate_guards(self):
        agent = self.agent()
        chat = agent.conversation(history_policy=HistoryPolicy(50))
        chat.send("Use JSON for export format.")
        original = chat.session_id
        chat.send("Continue one.")
        intermediate = chat.agent
        source = chat.agent.memory.entries()[0]["source"]
        chat.send("Continue two.")
        self.assertTrue(intermediate._closed)
        self.assertEqual(chat.agent.memory.entries()[0]["source"], source)
        self.assertEqual(source["session_id"], original)
        self.assertEqual(len(chat.deletion_plan()["sessions"]), 3)
        self.assertEqual(len(agent._conversation_children), 1)

    def test_rollover_memory_budget_failure_is_atomic(self):
        agent = self.agent()
        chat = agent.conversation(history_policy=HistoryPolicy(50))
        chat.send("Use JSON for export format.")
        original, revision = chat.session_id, agent.guard.turn().revision
        failure = ValueError("memory budget failure after provenance expansion")
        with patch.object(agent.memory, "_check_budget", side_effect=failure):
            with self.assertRaisesRegex(ValueError, "budget failure"):
                chat.send("Next")
        self.assertEqual(chat.session_id, original)
        self.assertIsNone(chat.next_session_id)
        self.assertEqual(agent.guard.turn().revision, revision)
        self.assertEqual(len(chat.deletion_plan()["sessions"]), 1)
        self.assertEqual(len(self.calls), 1)


class AsyncV011OrganizationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_cancellation_after_rollover_keeps_pending_and_rechecks_revocation(self):
        with tempfile.TemporaryDirectory() as folder:
            permitted = True
            started = asyncio.Event()
            waiting = asyncio.Event()
            calls = 0

            async def model(*, messages):
                nonlocal calls
                calls += 1
                if calls == 2:
                    started.set()
                    await waiting.wait()
                return "Acknowledged."

            with SessionManager(Path(folder), authorize=lambda principal, action: permitted) as manager:
                connection = manager.connect(model, principal=ALICE, provider="plain")
                chat = connection.conversation(history_policy=HistoryPolicy(50))
                await chat.send("First", request_id="first")
                original = chat.session_id
                task = asyncio.create_task(chat.send("Second", request_id="second"))
                await asyncio.wait_for(started.wait(), timeout=5)
                self.assertNotEqual(chat.session_id, original)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(chat.pending["in_flight"])
                self.assertEqual(chat.pending["request_id"], "second")
                self.assertEqual(manager.metrics()["active"], 0)
                with self.assertRaises(ConversationPending):
                    await chat.send("Third")
                permitted = False
                with self.assertRaises(AccessDenied):
                    await chat.retry()
                self.assertEqual(calls, 2)
                permitted = True
                await chat.retry()
                self.assertEqual(calls, 3)
                self.assertIsNone(chat.pending)
                active = chat._chat.agent
                connection.close()
                self.assertTrue(active._closed)


if __name__ == "__main__":
    unittest.main()
