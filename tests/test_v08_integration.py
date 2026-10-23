"""Cross-feature contracts; local provider doubles, never live API requests."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import MemoryPolicy, RelayConfig
from context_relay.conversation import ConversationResponseError
from context_relay.openai import connect_openai


def client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                           responses=SimpleNamespace(create=create))


def chat_response(text="Acknowledged."):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


def responses_response(text="Acknowledged."):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}]}


def values(agent):
    return {entry["key"]: entry["value"] for entry in agent.memory.entries()}


class ProviderMemoryConversationTests(unittest.TestCase):
    def test_openai_failure_restarts_with_originals_and_no_second_memory_write(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise OSError("No provider response")
            return chat_response()

        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "relay.sqlite3"))
            with connect_openai(client(create), config=config, memory=MemoryPolicy()) as agent:
                session_id = agent.session_id
                conversation = agent.conversation(system="Be concise.")
                with self.assertRaises(OSError):
                    conversation.send("Thanks! Export as CSV.", request_id="message-1")
                original = conversation.history
                remembered = agent.memory.entries()
                self.assertEqual(len(agent.guard.store.events(session_id)), 1)
            with connect_openai(client(create), config=config, memory=MemoryPolicy(), session_id=session_id) as agent:
                conversation = agent.conversation()
                self.assertEqual(conversation.history, original)
                self.assertEqual(len(calls), 1)
                self.assertFalse(conversation.pending["in_flight"])
                conversation.retry()
                self.assertEqual(agent.memory.entries(), remembered)
                self.assertEqual(len(agent.guard.store.events(session_id)), 1)
                conversation.send("Actually, export as JSON.")
                self.assertEqual(values(agent), {"export_format": "JSON"})
                self.assertEqual(len(conversation.history), 5)
                self.assertEqual(len(calls), 3)
                self.assertIsNone(conversation.pending)

    def test_responses_refusal_remains_exposed_without_polluting_memory_or_originals(self):
        refusal = {"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed",
                   "content": [{"type": "refusal", "refusal": "Cannot complete that."}]}]}
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return refusal if len(calls) == 1 else responses_response("Export as JSON.")

        with connect_openai(client(create), api="responses", memory=MemoryPolicy()) as agent:
            conversation = agent.conversation()
            with self.assertRaises(ConversationResponseError) as caught:
                conversation.send("Export as CSV.", request_id="message-1")
            self.assertIs(caught.exception.response, refusal)
            self.assertEqual(conversation.history, [{"role": "user", "content": "Export as CSV."}])
            self.assertEqual(values(agent), {"export_format": "CSV"})
            conversation.retry()
            conversation.send("Continue.")
            self.assertEqual(values(agent), {"export_format": "CSV"})
            self.assertEqual(len(calls), 3)
            self.assertTrue(all("input" in call and "messages" not in call for call in calls))

    def test_unexpected_openai_stream_stays_host_owned_and_user_is_pending(self):
        class Stream:
            def __iter__(self):
                raise AssertionError("Unexpected stream was consumed")

            def close(self):
                raise AssertionError("Unexpected stream was closed")

        stream, calls = Stream(), []

        def create(**kwargs):
            calls.append(kwargs)
            return stream

        with connect_openai(client(create), memory=MemoryPolicy()) as agent:
            conversation = agent.conversation()
            with self.assertRaises(ValueError):
                conversation.send("Reply in Hindi.", stream=True)
            self.assertEqual(values(agent), {})
            self.assertEqual(conversation.history, [])
            with self.assertRaises(ConversationResponseError) as caught:
                conversation.send("Reply in Hindi.")
            self.assertIs(caught.exception.response, stream)
            self.assertEqual(len(calls), 1)
            self.assertEqual(values(agent), {"response_language": "Hindi"})
            self.assertFalse(conversation.pending["in_flight"])

    def test_managed_history_cannot_replace_already_consumed_native_tool_history(self):
        history = [{"role": "user", "content": "Export as CSV."},
                   {"role": "assistant", "content": None, "tool_calls": [
                       {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]},
                   {"role": "tool", "tool_call_id": "call-1", "content": "Export as JSON."}]
        with connect_openai(client(lambda **kwargs: chat_response()), memory=MemoryPolicy()) as agent:
            agent(messages=history)
            with self.assertRaises(ValueError):
                agent.conversation()
            self.assertEqual(values(agent), {"export_format": "CSV"})

    def test_supported_politeness_does_not_hide_unknown_trailing_frame_in_managed_history(self):
        with connect_openai(client(lambda **kwargs: chat_response()), memory=MemoryPolicy()) as agent:
            conversation = agent.conversation()
            conversation.send("Thanks. Reply in Hindi. Check the assertion above.")
            self.assertEqual(values(agent), {})
            conversation.send("Thank you, please reply in Hindi.")
            self.assertEqual(values(agent), {"response_language": "Hindi"})
            source = agent.memory.entries()[0]["source"]
            self.assertEqual(source["index"], 2)
            self.assertEqual(source["quote"], conversation.history[2]["content"])


class AsyncProviderMemoryConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_inference_allows_other_session_writes_and_cancellation_keeps_one_original(self):
        entered, wait_forever = asyncio.Event(), asyncio.Event()
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                entered.set()
                await wait_forever.wait()
            return responses_response()

        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "relay.sqlite3"))
            async with connect_openai(client(create), api="responses", config=config, memory=MemoryPolicy()) as agent:
                conversation = agent.conversation()
                pending = asyncio.create_task(conversation.send("I prefer brief answers."))
                await asyncio.wait_for(entered.wait(), timeout=2)

                def write_other_session():
                    with connect_openai(client(lambda **kwargs: chat_response()), config=config, memory=MemoryPolicy()) as other:
                        other.conversation().send("Export as CSV.")
                        return values(other)

                try:
                    self.assertEqual(await asyncio.wait_for(asyncio.to_thread(write_other_session), timeout=2),
                                     {"export_format": "CSV"})
                finally:
                    pending.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await pending
                self.assertEqual(len(conversation.history), 1)
                self.assertEqual(values(agent), {"response_style": "brief"})
                self.assertFalse(conversation.pending["in_flight"])
                await conversation.retry()
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(conversation.history), 2)
                self.assertEqual(len(agent.guard.store.events(agent.session_id)), 1)


if __name__ == "__main__":
    unittest.main()
