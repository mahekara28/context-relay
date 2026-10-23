"""Managed transcript lifecycle, persistence and failure contracts; no inference."""

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from context_relay import MemoryPolicy, RelayConfig, connect
from context_relay.conversation import (
    AsyncConversation, Conversation, ConversationAlreadyCompleted, ConversationBusy,
    ConversationLimitExceeded, ConversationPending, ConversationResponseError,
)
from context_relay.models import encode


def answer(*, messages, **kwargs):
    return {"message": {"role": "assistant", "content": "Done."}}


class ConversationTests(unittest.TestCase):
    def agent(self, provider=answer, **options):
        result = connect(provider, **options)
        self.addCleanup(result.close)
        return result

    def test_accumulates_originals_and_returns_original_response(self):
        calls, response = [], {"message": {"role": "assistant", "content": "Done."}, "usage": 4}

        def provider(*, messages, **kwargs):
            calls.append((messages, kwargs))
            return response

        chat = Conversation(self.agent(provider), system="Be concise.")
        self.assertIs(chat.send("First", model="existing"), response)
        self.assertIs(chat.send("Second"), response)
        self.assertEqual(chat.history, [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "First"}, {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "Second"}, {"role": "assistant", "content": "Done."},
        ])
        self.assertEqual(calls[0][1], {"model": "existing"})
        self.assertIn({"role": "user", "content": "First"}, calls[1][0])
        self.assertIsNone(chat.pending)

    def test_snapshots_and_provider_mutation_do_not_change_originals(self):
        def provider(*, messages):
            messages[-1]["content"] = "Rewritten"
            return "Original assistant"

        chat = Conversation(self.agent(provider))
        chat.send("Original user")
        history = chat.history
        history[0]["content"] = "Changed"
        history.clear()
        self.assertEqual(chat.history[0]["content"], "Original user")

    def test_completed_request_identity_prevents_second_model_call(self):
        calls = []

        def provider(*, messages):
            calls.append(messages)
            return "First answer"

        chat = Conversation(self.agent(provider))
        chat.send("Hello", request_id="ui-event-1")
        with self.assertRaises(ConversationAlreadyCompleted) as caught:
            chat.send("Hello", request_id="ui-event-1")
        caught.exception.assistant_message["content"] = "Changed"
        self.assertEqual(caught.exception.assistant_message["content"], "First answer")
        self.assertEqual(len(calls), 1)
        with self.assertRaises(ValueError):
            chat.send("Different", request_id="ui-event-1")

    def test_failure_preserves_one_pending_user_and_explicit_retry(self):
        calls, failure = [], OSError("temporary provider error")

        def provider(*, messages, **kwargs):
            calls.append((messages, kwargs))
            if len(calls) == 1:
                raise failure
            return "Recovered"

        chat = Conversation(self.agent(provider))
        with self.assertRaises(OSError) as caught:
            chat.send("Hello", request_id="request-1", model="small")
        self.assertIs(caught.exception, failure)
        pending = chat.pending
        self.assertEqual(pending, {"request_id": "request-1", "in_flight": False,
                                   "message": {"role": "user", "content": "Hello"}})
        pending["message"]["content"] = "Changed"
        for text, identifier in (("Hello", "request-1"), ("Next", "request-2")):
            with self.assertRaises(ConversationPending):
                chat.send(text, request_id=identifier)
        self.assertEqual(len(calls), 1)
        self.assertEqual(chat.retry(model="small"), "Recovered")
        self.assertEqual([item["role"] for item in chat.history], ["user", "assistant"])
        self.assertEqual(calls[1][1], {"model": "small"})

    def test_generated_request_id_is_visible_after_failure(self):
        def provider(*, messages):
            raise RuntimeError("failure")

        chat = Conversation(self.agent(provider))
        with self.assertRaises(RuntimeError):
            chat.send("Retain me")
        self.assertIsInstance(chat.pending["request_id"], str)
        self.assertGreater(len(chat.pending["request_id"]), 10)

    def test_reentrant_send_and_forced_retry_remain_busy(self):
        def provider(*, messages):
            for operation in (lambda: chat.send("Another"), lambda: chat.retry(recover_inflight=True)):
                with self.assertRaises(ConversationBusy):
                    operation()
            return "Finished"

        chat = Conversation(self.agent(provider))
        self.assertEqual(chat.send("Original"), "Finished")
        self.assertEqual(len(chat.history), 2)

    def test_second_store_cannot_call_same_conversation_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "relay.sqlite3"))
            entered, release = threading.Event(), threading.Event()

            def provider(*, messages):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test worker release")
                return "Finished"

            first_agent = connect(provider, config=config)
            second_agent = connect(answer, config=config, session_id=first_agent.session_id)
            try:
                first, second = Conversation(first_agent), Conversation(second_agent)
                errors = []

                def run():
                    try:
                        first.send("Original")
                    except BaseException as exc:
                        errors.append(exc)

                worker = threading.Thread(target=run)
                worker.start()
                self.assertTrue(entered.wait(5))
                try:
                    # This write succeeds while the model is running: no DB
                    # transaction is retained across provider inference.
                    second_agent.remember("mode", "local")
                    with self.assertRaises(ConversationBusy):
                        second.send("Concurrent")
                    with self.assertRaises(ConversationBusy):
                        second.retry(recover_inflight=True)
                finally:
                    release.set()
                    worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(second.history, first.history)
            finally:
                release.set()
                second_agent.close()
                first_agent.close()

    def test_resume_keeps_system_history_request_identity_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "relay.sqlite3"))
            with connect(answer, config=config, memory=MemoryPolicy()) as agent:
                chat = Conversation(agent, system="Be brief.", max_history_chars=10000)
                chat.send("Use CSV for export format.", request_id="first")
                session, expected = agent.session_id, chat.history
            with connect(answer, config=config, session_id=session, memory=MemoryPolicy()) as agent:
                chat = Conversation(agent)
                self.assertEqual(chat.max_history_chars, 10000)
                self.assertEqual(chat.history, expected)
                with self.assertRaises(ConversationAlreadyCompleted):
                    chat.send("Use CSV for export format.", request_id="first")
                chat.send("Actually, use JSON instead of CSV.")
                self.assertEqual(agent.memory.entries()[0]["value"], "JSON")
                self.assertEqual(len(chat.history), 5)

    def test_failed_request_resumes_without_replaying_until_explicit_retry(self):
        def failure(*, messages):
            raise ConnectionError("disconnected")

        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "relay.sqlite3"))
            with connect(failure, config=config) as agent:
                chat = Conversation(agent)
                with self.assertRaises(ConnectionError):
                    chat.send("Original", request_id="one")
                session = agent.session_id
            with connect(answer, config=config, session_id=session) as agent:
                chat = Conversation(agent)
                self.assertEqual(chat.pending["request_id"], "one")
                self.assertEqual(len(chat.history), 1)
                chat.retry()
                self.assertEqual(len(chat.history), 2)

    def test_persisted_crash_claim_needs_explicit_recovery_override(self):
        def failure(*, messages):
            raise RuntimeError("failed")

        agent = self.agent(failure)
        chat = Conversation(agent)
        with self.assertRaises(RuntimeError):
            chat.send("Original")
        # Simulate a process dying after its durable claim was committed.
        with agent.guard.store._transaction(write=True):
            agent.guard.store._connection.execute(
                "UPDATE conversations SET claim_token = 'dead-worker' WHERE session_id = ?", (agent.session_id,))
        with self.assertRaises(ConversationBusy):
            chat.retry()
        agent._model_call = answer
        chat.retry(recover_inflight=True)
        self.assertEqual(len(chat.history), 2)

    def test_system_limit_and_protocol_cannot_change_during_resume(self):
        agent = self.agent()
        Conversation(agent, system="Original", max_history_chars=1000)
        with self.assertRaises(ValueError):
            Conversation(agent, system="Replacement")
        with self.assertRaises(ValueError):
            Conversation(agent, max_history_chars=2000)
        agent._config = RelayConfig(protocol="ollama")
        with self.assertRaises(ValueError):
            Conversation(agent)

    def test_memory_input_cap_checked_before_adding_user(self):
        agent = self.agent(memory=MemoryPolicy(max_message_chars=5))
        chat = Conversation(agent)
        with self.assertRaises(ValueError):
            chat.send("too long")
        self.assertEqual(chat.history, [])
        self.assertIsNone(chat.pending)

    def test_managed_transcript_cannot_replace_consumed_external_history(self):
        agent = self.agent(memory=MemoryPolicy())
        agent(messages=[{"role": "user", "content": "Use CSV for export format."}])
        with self.assertRaises(ValueError):
            Conversation(agent)

    def test_history_cap_exactly_matches_json_and_never_rewrites_old_rows(self):
        expected = [{"role": "user", "content": "héllo"}, {"role": "assistant", "content": "OK"}]
        cap = len(encode(expected))
        agent = self.agent(lambda **kwargs: "OK")
        chat = Conversation(agent, max_history_chars=cap)
        statements = []
        agent.guard.store._connection.set_trace_callback(statements.append)
        chat.send("héllo")
        self.assertEqual(chat.history, expected)
        with self.assertRaises(ConversationLimitExceeded):
            chat.send("Next")
        self.assertEqual(chat.history, expected)
        self.assertIsNone(chat.pending)
        self.assertFalse(any("UPDATE conversation_messages" in statement or "DELETE FROM conversation_messages" in statement
                             for statement in statements))

    def test_overlarge_assistant_exposes_response_and_retains_pending_user(self):
        response = "Very long " * 30
        chat = Conversation(self.agent(lambda **kwargs: response), max_history_chars=100)
        with self.assertRaises(ConversationLimitExceeded) as caught:
            chat.send("Hello")
        self.assertIs(caught.exception.response, response)
        self.assertFalse(chat.pending["in_flight"])
        self.assertEqual(chat.history, [{"role": "user", "content": "Hello"}])

    def test_context_preparation_failure_releases_claim_without_calling_model(self):
        calls = []
        chat = Conversation(self.agent(lambda **kwargs: calls.append(kwargs), config=RelayConfig(max_chars=0)))
        with self.assertRaises(ValueError):
            chat.send("Retain me")
        self.assertEqual(calls, [])
        self.assertFalse(chat.pending["in_flight"])

    def test_explicit_and_provider_decoders_keep_response_identity(self):
        response = object()
        agent = self.agent(lambda **kwargs: response)
        agent._response_decoder = lambda value: "Provider text"
        chat = Conversation(agent)
        self.assertIs(chat.send("First"), response)
        explicit = Conversation(agent, decoder=lambda value: "Custom text")
        self.assertIs(explicit.send("Second"), response)
        self.assertEqual([item["content"] for item in chat.history if item["role"] == "assistant"],
                         ["Provider text", "Custom text"])

    def test_unsupported_response_is_exposed_without_consuming_or_retrying(self):
        calls, visited = [], []

        def stream():
            visited.append(True)
            yield "chunk"

        response = stream()
        self.addCleanup(response.close)

        def provider(*, messages):
            calls.append(messages)
            return response

        chat = Conversation(self.agent(provider))
        with self.assertRaises(ConversationResponseError) as caught:
            chat.send("Hello")
        self.assertIs(caught.exception.response, response)
        self.assertEqual(len(calls), 1)
        self.assertEqual(visited, [])
        self.assertFalse(chat.pending["in_flight"])

    def test_default_decoder_rejects_nontext_or_native_tool_payloads(self):
        for response in (None, "", " ", {"message": {"role": "user", "content": "Wrong role"}},
                         {"message": {"role": "assistant", "content": []}},
                         {"message": {"role": "assistant", "content": "Hi", "tool_calls": []}}):
            with self.subTest(response=response):
                chat = Conversation(self.agent(lambda **kwargs: response))
                with self.assertRaises(ConversationResponseError):
                    chat.send("Hello")

    def test_invalid_inputs_streams_and_tools_fail_before_storage(self):
        chat = Conversation(self.agent())
        for kwargs in ({"stream": True}, {"stream": 1}, {"stream": None}, {"tools": []},
                       {"tool_choice": "none"}, {"functions": []}, {"messages": []}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                chat.send("Hello", **kwargs)
        for text in (None, "", " ", 1, [], {}):
            with self.subTest(text=text), self.assertRaises(ValueError):
                chat.send(text)
        for request_id in ("", 1, "x" * 129):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                chat.send("Hello", request_id=request_id)
        self.assertEqual(chat.history, [])

    def test_invalid_constructor_and_retry_options(self):
        agent = self.agent(memory=MemoryPolicy(max_history_chars=10000))
        for limit in (True, 1, 10001, 10.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                Conversation(agent, max_history_chars=limit)

        async def decoder(value):
            return "text"

        for invalid in (False, decoder):
            with self.assertRaises(TypeError):
                Conversation(agent, decoder=invalid)
        chat = Conversation(agent)
        with self.assertRaises(ValueError):
            chat.retry()
        with self.assertRaises(ValueError):
            chat.retry(recover_inflight=1)
        with self.assertRaises(ValueError):
            Conversation(self.agent(memory=MemoryPolicy(max_history_chars=1)))

    def test_plain_ollama_envelope_with_text_history(self):
        chat = Conversation(self.agent(config=RelayConfig(protocol="ollama")))
        chat.send("Hello", stream=False)
        self.assertEqual(chat.history[-1], {"role": "assistant", "content": "Done."})


class AsyncConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_identity_history_and_duplicate_request(self):
        response = {"message": {"role": "assistant", "content": "OK"}}

        async def provider(*, messages, **kwargs):
            return response

        with connect(provider) as agent:
            chat = AsyncConversation(agent)
            self.assertIs(await chat.send("Hello", request_id="one"), response)
            with self.assertRaises(ConversationAlreadyCompleted):
                await chat.send("Hello", request_id="one")
            self.assertEqual(len(chat.history), 2)

    async def test_cancellation_retains_user_releases_claim_then_retry_completes(self):
        entered, unblock = asyncio.Event(), asyncio.Event()

        async def provider(*, messages):
            entered.set()
            await unblock.wait()
            return "Completed"

        with connect(provider) as agent:
            chat = AsyncConversation(agent)
            task = asyncio.create_task(chat.send("Keep original", request_id="one"))
            await asyncio.wait_for(entered.wait(), 3)
            with self.assertRaises(ConversationBusy):
                await chat.send("Parallel")
            with self.assertRaises(ConversationBusy):
                await chat.retry(recover_inflight=True)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(chat.history, [{"role": "user", "content": "Keep original"}])
            self.assertFalse(chat.pending["in_flight"])
            unblock.set()
            self.assertEqual(await chat.retry(), "Completed")
            self.assertEqual(len(chat.history), 2)

    async def test_async_failure_preserves_exception_and_pending_request(self):
        failure = OSError("offline")

        async def provider(*, messages):
            raise failure

        with connect(provider) as agent:
            chat = AsyncConversation(agent)
            with self.assertRaises(OSError) as caught:
                await chat.send("Original")
            self.assertIs(caught.exception, failure)
            self.assertFalse(chat.pending["in_flight"])
            with self.assertRaises(ValueError):
                await chat.send("Next", stream=True)

    async def test_matching_connection_type_required(self):
        async def provider(*, messages):
            return "OK"

        with connect(provider) as async_agent, connect(answer) as sync_agent:
            with self.assertRaises(TypeError):
                Conversation(async_agent)
            with self.assertRaises(TypeError):
                AsyncConversation(sync_agent)


if __name__ == "__main__":
    unittest.main()
