"""Automatic rollover preserves ownership and exact stored evidence."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from context_relay import MemoryPolicy, RelayConfig, connect
from context_relay.conversation import (
    ConversationAlreadyCompleted, ConversationBusy, ConversationLimitExceeded,
    ConversationPending, HistoryPolicy,
)


class HistoryPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = RelayConfig(db_path=str(Path(temporary.name) / "relay.sqlite3"))
        self.calls = []

    def agent(self, provider=None, **options):
        def answer(*, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return "Acknowledged."
        agent = connect(provider or answer, config=self.config, **options)
        self.addCleanup(agent.close)
        return agent

    def chat(self, agent=None, **options):
        return (agent or self.agent()).conversation(history_policy=HistoryPolicy(50), **options)

    def test_rolls_before_next_turn_and_keeps_original_guard_bound(self):
        agent = self.agent(memory=MemoryPolicy())
        chat = self.chat(agent, system="Be brief.")
        original = chat.session_id
        chat.send("Use JSON for export format.", request_id="one")
        self.assertEqual(chat.session_id, original)
        chat.send("Continue.", request_id="two", temperature=0)
        self.assertNotEqual(chat.session_id, original)
        self.assertEqual(agent.session_id, original)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(chat.history[0], {"role": "system", "content": "Be brief."})
        self.assertEqual(len(chat.history), 3)
        self.assertEqual(chat.agent.memory.entries()[0]["value"], "JSON")
        self.assertEqual(chat.agent.memory.entries()[0]["source"]["session_id"], original)
        self.assertIn("Use JSON for export format.", [item["message"]["content"] for item in chat.history_page()["messages"]])
        self.assertEqual(self.calls[-1][1], {"temperature": 0})

    def test_owner_closes_successor_and_only_keeps_current_child(self):
        agent = self.agent()
        chat = self.chat(agent)
        chat.send("First")
        chat.send("Second")
        previous = chat.agent
        chat.send("Third")
        self.assertTrue(previous._closed)
        self.assertEqual(len(agent._conversation_children), 1)
        active = chat.agent
        agent.close()
        self.assertTrue(active._closed)
        with self.assertRaises(RuntimeError):
            chat.send("Closed")

    def test_conversation_context_closes_child_not_owner(self):
        agent = self.agent()
        with self.chat(agent) as chat:
            chat.send("First")
            chat.send("Second")
            child = chat.agent
        self.assertTrue(child._closed)
        self.assertFalse(agent._closed)

    def test_duplicate_does_not_roll_or_call_provider(self):
        chat = self.chat()
        chat.send("First", request_id="one")
        original = chat.session_id
        with self.assertRaises(ConversationAlreadyCompleted):
            chat.send("First", request_id="one")
        self.assertEqual(chat.session_id, original)
        chat.send("Second", request_id="two")
        successor = chat.session_id
        with self.assertRaises(ConversationAlreadyCompleted):
            chat.send("First", request_id="one")
        self.assertEqual(chat.session_id, successor)
        self.assertEqual(len(self.calls), 2)

    def test_failure_stays_pending_without_implicit_retry_or_rollover(self):
        calls = []
        def fail(*, messages):
            calls.append(messages)
            raise ConnectionError("offline")
        chat = self.chat(self.agent(fail))
        original = chat.session_id
        with self.assertRaises(ConnectionError):
            chat.send("First")
        with self.assertRaises(ConversationPending):
            chat.send("Next")
        self.assertEqual(chat.session_id, original)
        self.assertEqual(len(calls), 1)

    def test_tool_permissions_stop_auto_rollover_without_copying(self):
        agent = self.agent()
        chat = self.chat(agent)
        agent.allow_tool("publish")
        chat.send("First")
        with self.assertRaisesRegex(ValueError, "tool permissions"):
            chat.send("Second")
        self.assertIsNone(chat.next_session_id)
        self.assertEqual(len(self.calls), 1)

    def test_oversized_new_message_creates_no_empty_archive(self):
        chat = self.chat(max_history_chars=200)
        chat.send("First")
        with self.assertRaises(ConversationLimitExceeded):
            chat.send("x" * 300)
        self.assertIsNone(chat.next_session_id)
        self.assertEqual(len(self.calls), 1)

    def test_user_append_overflow_can_roll_before_threshold(self):
        chat = self.agent().conversation(max_history_chars=180, history_policy=HistoryPolicy(180))
        chat.send("First")
        original = chat.session_id
        chat.send("x" * 90)
        self.assertNotEqual(chat.session_id, original)
        self.assertEqual(len(self.calls), 2)

    def test_does_not_roll_to_fit_an_unbounded_provider_response(self):
        chat = self.chat(self.agent(lambda **kwargs: "x" * 300), max_history_chars=200)
        with self.assertRaises(ConversationLimitExceeded):
            chat.send("First")
        self.assertIsNone(chat.next_session_id)
        self.assertIsNotNone(chat.pending)

    def test_invalid_policy_and_in_memory_rejected(self):
        for value in (True, 0, 20000001, "20"):
            with self.assertRaises(ValueError):
                HistoryPolicy(value)
        with connect(lambda **kwargs: "OK") as agent:
            with self.assertRaisesRegex(ValueError, "persistent"):
                agent.conversation(history_policy=HistoryPolicy())
        with self.assertRaises(TypeError):
            self.agent().conversation(history_policy={})
        with self.assertRaises(ValueError):
            self.agent().conversation(max_history_chars=100, history_policy=HistoryPolicy(101))

    def test_same_object_reentrant_send_is_busy(self):
        nested = []
        def provider(**kwargs):
            with self.assertRaises(ConversationBusy):
                chat.send("nested")
            nested.append(True)
            return "OK"
        chat = self.chat(self.agent(provider))
        chat.send("First")
        self.assertEqual(nested, [True])

    def test_async_rollover_and_cancellation_keep_pending_successor(self):
        async def scenario():
            started, wait = asyncio.Event(), asyncio.Event()
            count = 0
            async def provider(**kwargs):
                nonlocal count
                count += 1
                if count == 2:
                    started.set()
                    await wait.wait()
                return "Acknowledged."
            agent = self.agent(provider)
            chat = self.chat(agent)
            await chat.send("First")
            original = chat.session_id
            task = asyncio.create_task(chat.send("Second"))
            await started.wait()
            self.assertNotEqual(chat.session_id, original)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(chat.pending["in_flight"])
            self.assertEqual(chat.pending["message"]["content"], "Second")
            with self.assertRaises(ConversationPending):
                await chat.send("Third")
            self.assertEqual(count, 2)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
