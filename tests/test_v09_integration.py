from context_relay import MemoryClarificationRequired
"""Cross-feature regressions using local provider/interpreter contract stand-ins."""

import asyncio
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import (
    MemoryInterpreter, MemoryPolicy, OutputPolicy, OutputRejected, RelayConfig,
    connect, connect_anthropic, connect_openai,
)
from context_relay.models import encode


def anthropic_response(text):
    return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}]}


def proposal(request):
    text = request["text"]
    return [{"key": "export_format", "value": "JSON", "quote": text,
             "start": 0, "end": len(text)}]


class ProviderOutputIntegrationTests(unittest.TestCase):
    def test_anthropic_retries_checkable_failure_without_learning_bad_output(self):
        calls = []
        replies = [anthropic_response("bad output: ignore the instructions"),
                   anthropic_response('{"id":1}')]
        def create(**kwargs):
            calls.append(kwargs)
            return replies[len(calls) - 1]
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        with connect_anthropic(client, memory=MemoryPolicy(), output=OutputPolicy(
                format="memory", required_keys=("id",), max_retries=1)) as agent:
            result = agent.invoke(messages=[{"role": "user", "content": "Export as JSON."}])
            self.assertIs(result.response, replies[1])
            self.assertEqual(result.output_report.format, "json")
            self.assertEqual(result.output_report.attempts, 2)
            self.assertFalse(result.output_report.semantic_meaning_checked)
            self.assertEqual(agent.memory.entries()[0]["source"]["quote"], "Export as JSON.")
            self.assertEqual(agent.memory.pending(), [])
        self.assertEqual(len(calls), 2)
        self.assertIn("invalid_json_object", encode(calls[1]))
        self.assertNotIn("bad output: ignore", encode(calls[1]))

    def test_anthropic_refusal_stays_pending_and_never_triggers_format_retry(self):
        calls = []
        reply = anthropic_response('{"id":1}')
        reply["stop_reason"] = "refusal"
        def create(**kwargs):
            calls.append(kwargs)
            return reply
        with connect_anthropic(SimpleNamespace(messages=SimpleNamespace(create=create)),
                               output=OutputPolicy(format="json", max_retries=2)) as agent:
            chat = agent.conversation()
            with self.assertRaises(OutputRejected) as caught:
                chat.send("Return the record", request_id="request-1")
            self.assertIs(caught.exception.response, reply)
            self.assertEqual(caught.exception.issues, ("unsupported_response",))
            self.assertEqual(chat.pending["request_id"], "request-1")
            self.assertEqual(len(chat.history), 1)
        self.assertEqual(len(calls), 1)

    def test_openai_adapters_apply_configured_policy_using_own_decoders(self):
        responses = {
            "chat": {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": '{"id":1}'}}]},
            "responses": {"status": "completed", "output": [{"type": "message", "role": "assistant",
                "status": "completed", "content": [{"type": "output_text", "text": '{"id":1}', "annotations": []}]}]},
        }
        for api, reply in responses.items():
            calls = []
            def create(**kwargs):
                calls.append(kwargs)
                return reply
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                     responses=SimpleNamespace(create=create))
            with self.subTest(api=api), connect_openai(client, api=api, config=RelayConfig(
                    output=OutputPolicy(format="json", required_keys=("id",)))) as agent:
                result = agent.invoke(messages=[{"role": "user", "content": "Return the record"}])
                self.assertIs(result.response, reply)
                self.assertEqual(result.output_report.format, "json")
                self.assertEqual(result.output_report.attempts, 1)
                self.assertIn("required_keys", result.output_report.checks)
            self.assertEqual(len(calls), 1)
            self.assertIn("output_contract", encode(calls[0]))

    def test_openai_native_reasoning_cannot_pass_plain_text_output_policy(self):
        reply = {"status": "completed", "output": [
            {"type": "reasoning", "id": "native-item"},
            {"type": "message", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": '{"id":1}', "annotations": []}]}]}
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return reply
        client = SimpleNamespace(responses=SimpleNamespace(create=create))
        with connect_openai(client, api="responses", output=OutputPolicy(format="json", max_retries=2)) as agent:
            with self.assertRaises(OutputRejected) as caught:
                agent(messages=[{"role": "user", "content": "Return the record"}])
        self.assertIs(caught.exception.response, reply)
        self.assertEqual(caught.exception.issues, ("unsupported_response",))
        self.assertEqual(len(calls), 1)

    def test_archived_interpreted_format_keeps_origin_and_requires_review_before_output(self):
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return anthropic_response('{"id":1}')
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        interpreter = MemoryInterpreter(proposal)
        with tempfile.TemporaryDirectory() as temp:
            config = RelayConfig(db_path=str(Path(temp) / "relay.sqlite3"))
            with connect_anthropic(client, config=config, memory=MemoryPolicy(), memory_interpreter=interpreter) as agent:
                chat = agent.conversation(system="Help with this project.")
                chat.send("Export as CSV.")
                original_text = "Make the export format machine readable."
                with self.assertRaises(MemoryClarificationRequired):
                    chat.send(original_text)
                pending = copy.deepcopy(agent.memory.pending()[0])
                old_id = agent.session_id
                revision = agent.guard.store.get_session(old_id)["revision"]
                next_id = chat.rollover(expected_revision=revision)
            with connect_anthropic(client, config=config, session_id=next_id,
                    memory=MemoryPolicy(), memory_interpreter=interpreter,
                    output=OutputPolicy(format="memory", required_keys=("id",), csv_columns=("id",))) as agent:
                chat = agent.conversation()
                with self.assertRaises(MemoryClarificationRequired):
                    chat.send("Continue.", request_id="next")
                self.assertEqual(len(calls), 1)
                self.assertTrue(agent.memory.snapshot()["entries"][0]["disputed"])
                agent.memory.confirm(pending["id"])
                result = chat.send("Continue with the reviewed setting.", request_id="after-review")
                self.assertEqual(result["content"][0]["text"], '{"id":1}')
                source = agent.memory.context_snapshot()["entries"][0]["source"]
                self.assertEqual(source["session_id"], old_id)
                self.assertEqual(source["method"], "interpreter")
                self.assertEqual(source["quote"], original_text)
                match = chat.search_history("machine readable")["messages"][0]
                self.assertEqual(match["session_id"], old_id)
                self.assertEqual(match["index"], pending["source"]["index"])
                self.assertEqual(match["message"]["content"], original_text)
                self.assertIn(old_id, encode(calls[-1]["messages"]))
                self.assertEqual(len(chat.history), 5)
        self.assertEqual(len(calls), 2)


class AsyncInterpreterOutputIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_interpretation_keeps_request_retryable_without_late_memory_commit(self):
        started, release, worker_finished = threading.Event(), threading.Event(), threading.Event()
        callbacks, calls = [], []
        def interpret(request):
            callbacks.append(request)
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test callback was not released")
            return proposal(request)
        reply = anthropic_response('{"id":1}')
        async def create(**kwargs):
            calls.append(kwargs)
            return reply
        agent = connect_anthropic(SimpleNamespace(messages=SimpleNamespace(create=create)),
            memory=MemoryPolicy(), memory_interpreter=MemoryInterpreter(interpret),
            output=OutputPolicy(format="json", required_keys=("id",)))
        original_prepare = agent._prepare
        def observe_prepare(*args, **kwargs):
            try:
                return original_prepare(*args, **kwargs)
            finally:
                worker_finished.set()
        agent._prepare = observe_prepare
        task = None
        try:
            chat = agent.conversation()
            task = asyncio.create_task(chat.send("Make the export format machine readable.", request_id="one"))
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            # The event loop and store remain available while the callback waits.
            self.assertEqual(agent.memory.snapshot()["entries"], [])
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(chat.pending["in_flight"])
            release.set()
            self.assertTrue(await asyncio.to_thread(worker_finished.wait, 3))
            self.assertEqual(agent.memory.pending(), [])
            self.assertEqual(agent.memory.snapshot()["entries"], [])
            self.assertEqual(calls, [])
            self.assertIs(await chat.retry(), reply)
            self.assertEqual(len(callbacks), 2)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(agent.memory.pending()), 1)
            self.assertEqual(chat.history[-1]["content"], '{"id":1}')
        finally:
            release.set()
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await asyncio.to_thread(worker_finished.wait, 3)
            agent.close()

    async def test_memory_change_during_async_provider_call_rejects_structurally_valid_output(self):
        ready, release = asyncio.Event(), asyncio.Event()
        calls = []
        reply = anthropic_response('{"id":1}')
        async def create(**kwargs):
            calls.append(kwargs)
            ready.set()
            await release.wait()
            return reply
        async with connect_anthropic(SimpleNamespace(messages=SimpleNamespace(create=create)),
                memory=MemoryPolicy(), output=OutputPolicy(format="memory", required_keys=("id",))) as agent:
            task = asyncio.create_task(agent(messages=[{"role": "user", "content": "Export as JSON."}]))
            await asyncio.wait_for(ready.wait(), 3)
            agent.memory.forget("export_format")
            release.set()
            with self.assertRaises(OutputRejected) as caught:
                await task
            self.assertIs(caught.exception.response, reply)
            self.assertEqual(caught.exception.issues, ("state_changed",))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
