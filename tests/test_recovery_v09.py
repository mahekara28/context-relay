"""Grounded recovery must respect optional output and interpreter boundaries."""

import asyncio
import json
import threading
import unittest

from context_relay import MemoryInterpreter, MemoryPolicy, OutputPolicy, connect


HISTORY = [{"role": "user", "content": "Make it brief."}]


def suggestion(request):
    text = request["text"]
    return [{"key": "response_style", "value": "brief", "quote": text,
             "start": 0, "end": len(text)}]


def abstain(messages):
    packet = next(message["content"] for message in messages
                  if message["content"].startswith("APPLICATION-RECORDED TASK DATA"))
    state = json.loads(packet[packet.index("{"):])["state"]
    return {"answer": "unknown", "history_id": state["evidence_history_id"], "citations": []}


class RecoveryOptionalFeatureTests(unittest.TestCase):
    def test_output_policy_rejected_before_memory_or_provider_work(self):
        calls, interpretations = [], []
        interpreter = MemoryInterpreter(lambda request: interpretations.append(request) or suggestion(request))
        with connect(lambda **kw: calls.append(kw), memory=MemoryPolicy(),
                     memory_interpreter=interpreter, output=OutputPolicy(format="json")) as agent:
            with self.assertRaisesRegex(ValueError, "cannot bypass an enabled OutputPolicy"):
                agent.run_grounded(messages=HISTORY)
            self.assertEqual(calls, [])
            self.assertEqual(interpretations, [])
            self.assertIsNone(agent.guard.store._memory_state(agent.session_id))

    def test_synchronous_grounding_still_creates_review_only_memory(self):
        interpretations = []
        with connect(lambda **kw: abstain(kw["messages"]), memory=MemoryPolicy(),
                     memory_interpreter=MemoryInterpreter(
                         lambda request: interpretations.append(request) or suggestion(request))) as agent:
            result = agent.run_grounded(messages=HISTORY)
            self.assertEqual(result.status, "abstained")
            self.assertEqual(len(interpretations), 1)
            self.assertEqual(agent.memory.entries(), [])
            self.assertEqual(agent.memory.pending()[0]["reason"], "interpreted_needs_review")
            self.assertEqual(agent.guard.store.facts(agent.session_id), {})


class AsyncRecoveryOptionalFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_output_rejection_precedes_preparation_and_provider(self):
        calls, interpretations = [], []

        async def provider(*, messages):
            calls.append(messages)
            return abstain(messages)

        with connect(provider, memory=MemoryPolicy(), output=OutputPolicy(format="json"),
                     memory_interpreter=MemoryInterpreter(
                         lambda request: interpretations.append(request) or suggestion(request))) as agent:
            with self.assertRaisesRegex(ValueError, "OutputPolicy"):
                await agent.run_grounded(messages=HISTORY)
            self.assertEqual((calls, interpretations), ([], []))
            self.assertIsNone(agent.guard.store._memory_state(agent.session_id))

    async def test_async_grounding_interprets_off_loop_and_does_not_repeat_on_retry(self):
        started, release = threading.Event(), threading.Event()
        interpretation_threads, calls = [], []
        loop_thread = threading.get_ident()

        def interpret(request):
            interpretation_threads.append(threading.get_ident())
            started.set()
            if not release.wait(3):
                raise TimeoutError("Interpreter blocked the event loop")
            return suggestion(request)

        async def provider(*, messages):
            calls.append(messages)
            return "invalid JSON" if len(calls) == 1 else abstain(messages)

        with connect(provider, memory=MemoryPolicy(), memory_interpreter=MemoryInterpreter(interpret)) as agent:
            task = asyncio.create_task(agent.run_grounded(messages=HISTORY, max_rounds=1))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 2.5))
                self.assertFalse(task.done())
            finally:
                release.set()
            result = await asyncio.wait_for(task, 3)
            self.assertEqual(result.status, "abstained")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(interpretation_threads), 1)
            self.assertNotEqual(interpretation_threads[0], loop_thread)
            self.assertEqual(len(agent.memory.pending()), 1)

    async def test_cancel_during_interpretation_never_commits_late_memory_or_calls_provider(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []

        def interpret(request):
            started.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release interpreter")
            return suggestion(request)

        async def provider(*, messages):
            calls.append(messages)
            return abstain(messages)

        with connect(provider, memory=MemoryPolicy(), memory_interpreter=MemoryInterpreter(interpret)) as agent:
            original_prepare = agent._prepare

            def prepare(*args, **kwargs):
                try:
                    return original_prepare(*args, **kwargs)
                finally:
                    finished.set()

            agent._prepare = prepare
            task = asyncio.create_task(agent.run_grounded(messages=HISTORY))
            try:
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 2.5))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(calls, [])
                self.assertIsNone(agent.guard.store._memory_state(agent.session_id))
            finally:
                release.set()
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(finished.wait, 2), 2.5))
            self.assertEqual(calls, [])
            self.assertIsNone(agent.guard.store._memory_state(agent.session_id))

    async def test_invalid_grounding_options_do_not_run_interpreter(self):
        interpretations = []

        async def provider(*, messages):
            self.fail("Provider should not run")

        with connect(provider, memory=MemoryPolicy(), memory_interpreter=MemoryInterpreter(
                lambda request: interpretations.append(request) or suggestion(request))) as agent:
            for options in ({"max_rounds": 5}, {"search_limit": 0}, {"decoder": False}):
                with self.subTest(options=options), self.assertRaises((TypeError, ValueError)):
                    await agent.run_grounded(messages=HISTORY, **options)
            self.assertEqual(interpretations, [])


if __name__ == "__main__":
    unittest.main()
