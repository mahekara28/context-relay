"""Linked managed-history segments retain evidence without replaying providers."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from context_relay import MemoryClarificationRequired, MemoryPolicy, RelayConfig, connect
from context_relay.conversation import (
    ConversationAlreadyCompleted, ConversationArchived, ConversationBusy,
    ConversationLimitExceeded, ConversationPending,
)
from context_relay.memory import MemoryBudgetExceeded, MemoryHistoryError
from context_relay.middleware import ActionRejected
from context_relay.models import ConflictError, encode


class ConversationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = RelayConfig(db_path=str(Path(self.directory.name) / "relay.sqlite3"))
        self.calls = []

    def agent(self, *, session_id=None, memory=None, provider=None, config=None):
        def answer(*, messages):
            self.calls.append(messages)
            return "OK"

        agent = connect(provider or answer, config=config or self.config,
                        session_id=session_id, memory=memory)
        self.addCleanup(agent.close)
        return agent

    def rollover(self, chat, **options):
        revision = chat.agent.guard.store.get_session(chat.session_id)["revision"]
        return chat.rollover(expected_revision=revision, **options)

    def test_full_segment_continues_with_exact_prefix_and_no_old_transcript(self):
        agent = self.agent(memory=MemoryPolicy())
        chat = agent.conversation(system="Be concise.", max_history_chars=180)
        chat.send("Export as CSV.", request_id="first")
        originals = chat.history
        old_id = chat.session_id
        with self.assertRaises(ConversationLimitExceeded):
            chat.send("another " * 30)
        successor = self.rollover(chat)
        self.assertEqual(chat.history, originals)
        self.assertEqual(chat.next_session_id, successor)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ConversationArchived) as caught:
            chat.send("Wrong segment")
        self.assertEqual(caught.exception.session_id, successor)
        resumed = self.agent(session_id=successor, memory=MemoryPolicy()).conversation()
        self.assertEqual(resumed.history, [originals[0]])
        resumed.send("Export as JSON.", request_id="second")
        self.assertEqual(resumed.agent.memory.entries()[0]["value"], "JSON")
        self.assertEqual(chat.session_id, old_id)
        self.assertEqual(len(resumed.history), 3)

    def test_carries_pending_conflicts_and_provenance_without_new_authority(self):
        agent = self.agent(memory=MemoryPolicy(max_chars=10000))
        chat = agent.conversation()
        chat.send("My export format is CSV.")
        with self.assertRaises(MemoryClarificationRequired):
            chat.send("My export format is JSON.")
        original = agent.memory.entries()[0]
        pending = agent.memory.pending()[0]
        successor = self.rollover(chat)
        resumed = self.agent(session_id=successor, memory=MemoryPolicy(max_chars=10000))
        recalled = resumed.memory.entries()[0]
        self.assertEqual(recalled["source"]["session_id"], chat.session_id)
        for key in ("history_id", "index", "quote", "text", "message_sha256"):
            self.assertEqual(recalled["source"][key], original["source"][key])
        self.assertEqual(resumed.memory.pending()[0]["id"], pending["id"])
        self.assertFalse(resumed.memory.snapshot()["current_authority"])
        self.assertTrue(resumed.memory.snapshot()["entries"][0]["disputed"])
        resumed.memory.confirm(pending["id"])
        self.assertEqual(resumed.memory.entries()[0]["value"], "JSON")
        self.assertEqual(agent.memory.entries()[0], original)
        self.assertEqual(agent.memory.pending()[0], pending)
        self.assertEqual(resumed.guard.store.facts(successor), {})

    def test_facts_have_lineage_and_tool_policies_need_registration_again(self):
        agent = self.agent()
        chat = agent.conversation()
        original = agent.remember("approved", True, source="application", text="Explicit review")
        agent.allow_tool("publish", when={"approved": True})
        successor = self.rollover(chat)
        store = agent.guard.store
        self.assertEqual(store.facts(successor)["approved"]["value"], True)
        event = store.events(successor)[-1]
        self.assertEqual(event["fact_sources"], {"approved": original["id"]})
        self.assertEqual(event["previous_session_id"], chat.session_id)
        self.assertFalse(event["copied_tool_policies"])
        self.assertEqual(event["archived_tool_policy_count"], 1)
        self.assertEqual(store.policies(successor), [])
        self.assertEqual(len(store.policies(chat.session_id)), 1)
        new_agent = self.agent(session_id=successor)
        check = new_agent.guard.relay.check(successor, {
            "tool": "publish", "arguments": {},
            "context_revision": store.get_session(successor)["revision"],
        })
        self.assertEqual(check["decision"], "block")

    def test_duplicate_request_is_blocked_across_multiple_segments(self):
        chat = self.agent().conversation()
        chat.send("First", request_id="same-ui-event")
        middle = self.agent(session_id=self.rollover(chat)).conversation()
        middle.send("Second")
        newest = self.agent(session_id=self.rollover(middle)).conversation()
        with self.assertRaises(ConversationAlreadyCompleted) as caught:
            newest.send("First", request_id="same-ui-event")
        self.assertEqual(caught.exception.assistant_message["content"], "OK")
        with self.assertRaises(ValueError):
            newest.send("Changed", request_id="same-ui-event")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(newest.history, [])

    def test_failed_pending_and_inflight_requests_cannot_be_archived(self):
        def failure(*, messages):
            with self.assertRaises(ConversationBusy):
                self.rollover(chat)
            raise ConnectionError("offline")

        agent = self.agent(provider=failure)
        chat = agent.conversation()
        with self.assertRaises(ConnectionError):
            chat.send("Retain me", request_id="pending")
        with self.assertRaises(ConversationPending):
            self.rollover(chat)
        with agent.guard.store._transaction(write=True):
            agent.guard.store._connection.execute(
                "UPDATE conversations SET claim_token = 'dead-worker' WHERE session_id = ?", (chat.session_id,))
        with self.assertRaises(ConversationBusy):
            self.rollover(chat)
        self.assertIsNone(chat.next_session_id)
        self.assertEqual(len(agent.guard.store.list_sessions()), 1)

    def test_stale_revision_and_too_small_prefix_limit_leave_everything_unchanged(self):
        agent = self.agent()
        chat = agent.conversation(system="Required prefix")
        agent.remember("key", "value")
        before_events = agent.guard.store.events(chat.session_id)
        with self.assertRaises(ConflictError):
            chat.rollover(expected_revision=0)
        with self.assertRaises(ConversationLimitExceeded):
            self.rollover(chat, max_history_chars=2)
        self.assertIsNone(chat.next_session_id)
        self.assertEqual(agent.guard.store.events(chat.session_id), before_events)
        self.assertEqual(len(agent.guard.store.list_sessions()), 1)
        chat.send("Still active")

    def test_mid_copy_failure_rolls_back_created_session_and_link(self):
        agent = self.agent()
        chat = agent.conversation()
        agent.remember("one", 1)
        agent.remember("two", 2)
        store, original = agent.guard.store, agent.guard.store.record_fact

        def fail_second(session_id, key, value, **options):
            if key == "two":
                raise RuntimeError("storage failed")
            return original(session_id, key, value, **options)

        store.record_fact = fail_second
        try:
            with self.assertRaisesRegex(RuntimeError, "storage failed"):
                self.rollover(chat)
        finally:
            store.record_fact = original
        self.assertIsNone(chat.next_session_id)
        self.assertEqual(len(store.list_sessions()), 1)
        self.assertEqual(store._connection.execute("SELECT COUNT(*) FROM conversation_segments").fetchone()[0], 1)

    def test_memory_origin_overflow_does_not_drop_any_records(self):
        seed = self.agent(memory=MemoryPolicy(max_chars=10000))
        chat = seed.conversation()
        chat.send("Export as CSV.")
        exact_cap = len(encode(seed.memory.snapshot()))
        smaller = self.agent(session_id=chat.session_id, memory=MemoryPolicy(max_chars=exact_cap))
        same = smaller.conversation()
        with self.assertRaises(MemoryBudgetExceeded):
            self.rollover(same)
        self.assertIsNone(chat.next_session_id)
        self.assertEqual(seed.memory.entries()[0]["value"], "CSV")

    def test_missing_memory_policy_and_mismatched_cursor_reject_rollover(self):
        agent = self.agent(memory=MemoryPolicy())
        chat = agent.conversation()
        chat.send("Export as CSV.")
        without_memory = self.agent(session_id=chat.session_id).conversation()
        with self.assertRaisesRegex(ValueError, "memory enabled"):
            self.rollover(without_memory)
        # Mixing application-owned and managed histories must not become an
        # escape hatch for resetting the original-prefix validation.
        agent(messages=chat.history + [{"role": "user", "content": "Export as JSON."}])
        with self.assertRaises(MemoryHistoryError):
            self.rollover(chat)
        self.assertIsNone(chat.next_session_id)

    def test_pinned_indices_cannot_silently_point_to_different_new_messages(self):
        agent = self.agent(config=RelayConfig(db_path=self.config.db_path, pinned=(0,)))
        chat = agent.conversation()
        with self.assertRaisesRegex(ValueError, "pinned"):
            self.rollover(chat)
        self.assertIsNone(chat.next_session_id)

    def test_chain_pagination_is_exact_scoped_and_does_not_repeat_rows(self):
        first = self.agent().conversation(system="Prefix")
        first.send("Alpha")
        second = self.agent(session_id=self.rollover(first)).conversation()
        second.send("Beta")
        unrelated = self.agent().conversation()
        unrelated.send("Private unrelated")
        cursor, seen = None, []
        while True:
            page = second.history_page(after=cursor, limit=2)
            seen.extend(page["messages"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([item["message"] for item in seen], first.history + second.history)
        self.assertEqual({item["session_id"] for item in seen}, {first.session_id, second.session_id})
        self.assertEqual([item["message"] for item in first.history_page()["messages"]], first.history)
        seen[0]["message"]["content"] = "Mutation"
        self.assertEqual(first.history[0]["content"], "Prefix")

    def test_search_scan_limit_and_casefold_preserve_original_escaped_text(self):
        chat = self.agent().conversation()
        chat.send('A quote: "CAFÉ"')
        chat.send("not a match")
        child = self.agent(session_id=self.rollover(chat)).conversation()
        child.send('Another "café"')
        cursor, found, pages = None, [], []
        while True:
            page = child.search_history('"café"', after=cursor, limit=1, scan_limit=1)
            self.assertLessEqual(page["scanned"], 1)
            found.extend(page["messages"])
            pages.append(page)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([item["message"]["content"] for item in found], ['A quote: "CAFÉ"', 'Another "café"'])
        self.assertTrue(any(not page["messages"] and page["next_cursor"] for page in pages))

    def test_page_budget_never_silently_truncates_an_original(self):
        chat = self.agent().conversation()
        chat.send("Original " * 30)
        with self.assertRaises(ConversationLimitExceeded):
            chat.history_page(max_chars=50)
        first = chat.history_page(limit=1)["messages"][0]
        exact = len(encode([first]))
        page = chat.history_page(max_chars=exact)
        self.assertEqual(page["messages"], [first])
        self.assertIsNotNone(page["next_cursor"])
        rest = chat.history_page(after=page["next_cursor"])
        self.assertEqual(rest["messages"][0]["message"]["role"], "assistant")

    def test_invalid_page_and_rollover_arguments_do_not_change_history(self):
        chat = self.agent().conversation()
        for options in ({"after": [True, 0]}, {"after": [0]}, {"after": "0"},
                        {"after": [-1, 0]}, {"after": [10, 0]}, {"limit": True},
                        {"limit": 1001}, {"max_chars": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                chat.history_page(**options)
        for options in ({"query": ""}, {"query": "x", "scan_limit": False},
                        {"query": "x", "scan_limit": 10001}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                chat.search_history(**options)
        for options in ({"expected_revision": True}, {"expected_revision": -1},
                        {"expected_revision": 0, "max_history_chars": 1},
                        {"expected_revision": 0, "max_history_chars": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                chat.rollover(**options)
        self.assertEqual(chat.history, [])

    def test_repeated_rollover_is_explicit_and_origins_stay_first_session(self):
        first = self.agent(memory=MemoryPolicy()).conversation()
        first.send("Export as CSV.")
        middle_id = self.rollover(first)
        with self.assertRaises(ConversationArchived):
            self.rollover(first)
        middle = self.agent(session_id=middle_id, memory=MemoryPolicy()).conversation()
        last_id = self.rollover(middle)
        last = self.agent(session_id=last_id, memory=MemoryPolicy())
        self.assertEqual(last.memory.entries()[0]["source"]["session_id"], first.session_id)
        last.conversation().send("Continue")

    def test_legacy_conversation_gets_one_segment_without_rewriting_messages(self):
        agent = self.agent()
        chat = agent.conversation()
        chat.send("Old release")
        store = agent.guard.store
        with store._transaction(write=True):
            store._connection.execute("DELETE FROM conversation_segments WHERE session_id = ?", (chat.session_id,))
        statements = []
        store._connection.set_trace_callback(statements.append)
        resumed = agent.conversation()
        self.assertEqual(resumed.history, chat.history)
        self.rollover(resumed)
        self.assertFalse(any("UPDATE conversation_messages" in query or "DELETE FROM conversation_messages" in query
                             for query in statements))

    def test_memory_only_database_cannot_produce_an_unresumable_successor(self):
        with connect(lambda **kwargs: "OK") as agent:
            chat = agent.conversation()
            with self.assertRaisesRegex(ValueError, "persistent database"):
                chat.rollover(expected_revision=0)
            self.assertEqual(len(agent.guard.store.list_sessions()), 1)
            self.assertIsNone(chat.next_session_id)

    def test_archiving_invalidates_previously_prepared_tool_execution(self):
        agent = self.agent()
        chat = agent.conversation()
        agent.allow_tool("publish")
        effects = []
        protected = agent.guard.turn().wrap(lambda: effects.append(True), tool="publish")
        self.rollover(chat)
        with self.assertRaises(ActionRejected) as caught:
            protected()
        self.assertEqual(caught.exception.decision, "refresh")
        self.assertEqual(effects, [])


class AsyncConversationArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_inference_blocks_local_rollover_and_completed_history_can_roll(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(*, messages):
            started.set()
            await release.wait()
            return "OK"

        with tempfile.TemporaryDirectory() as directory, connect(
            provider, memory=MemoryPolicy(),
            config=RelayConfig(db_path=str(Path(directory) / "relay.sqlite3")),
        ) as agent:
            chat = agent.conversation()
            task = asyncio.create_task(chat.send("Export as CSV."))
            await asyncio.wait_for(started.wait(), 2)
            revision = agent.guard.store.get_session(chat.session_id)["revision"]
            with self.assertRaises(ConversationBusy):
                chat.rollover(expected_revision=revision)
            release.set()
            await task
            successor = chat.rollover(expected_revision=revision)
            self.assertEqual(chat.next_session_id, successor)
            self.assertEqual(len(chat.history), 2)


if __name__ == "__main__":
    unittest.main()
