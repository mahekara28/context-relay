"""Native-history unit tests; stand-ins do not claim live-provider validation."""

import asyncio
import functools
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay.budget import TokenBudget, TokenBudgetExceeded
from context_relay.config import RelayConfig
from context_relay.memory import MemoryPolicy
from context_relay.middleware import ContextGuard
from context_relay.models import BudgetExceeded, clone, encode
from context_relay.native import AsyncNativeConnection, NativeConnection, native_json
from context_relay.openai import connect_openai


def history():
    return [
        {"role": "user", "content": "Look up the order."},
        {"type": "reasoning", "id": "rs_1", "summary": [],
         "encrypted_content": "opaque\nA+/==", "status": "completed", "provider_extension": {"x": None}},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "lookup",
         "arguments": '{ "order": 7 }', "status": "completed"},
        {"type": "function_call_output", "call_id": "call_1", "output": '{ "status": "ready" }'},
    ]


class Provider:
    def __init__(self, response=None):
        self.response = object() if response is None else response
        self.calls = []
        self.closed = False

    def create(self, **request):
        self.calls.append(request)
        return self.response

    def close(self):
        self.closed = True


def client(provider):
    return SimpleNamespace(responses=provider)


class NativeOpenAITests(unittest.TestCase):
    def connect(self, provider=None, **options):
        provider = provider or Provider()
        relay = connect_openai(client(provider), api="responses", native=True, **options)
        self.addCleanup(relay.close)
        return relay, provider

    def test_full_native_request_preserved_without_mutating_source(self):
        relay, provider = self.connect()
        items = history()
        original = clone(items)
        management = [{"type": "compaction", "compact_threshold": 1000}]
        result = relay.invoke_result(input=items, model="configured-by-host", store=False,
                                     context_management=management,
                                     tools=[{"type": "function", "name": "lookup", "parameters": {}}])
        self.assertIs(result.response, provider.response)
        self.assertEqual(provider.calls[0], result.prepared.request)
        self.assertEqual(provider.calls[0]["input"], original)
        self.assertEqual(provider.calls[0]["context_management"], management)
        self.assertEqual(items, original)
        provider.calls[0]["input"][1]["encrypted_content"] = "changed-by-provider"
        items[1]["encrypted_content"] = "changed-by-caller"
        inspected = result.prepared.request
        inspected["input"].clear()
        self.assertEqual(result.prepared.request["input"], original)
        self.assertEqual(result.prepared.report["opaque_items"], 1)
        self.assertFalse(result.prepared.report["memory_injected"])

    def test_canonical_compaction_window_retained_including_prior_user_items(self):
        relay, provider = self.connect()
        items = [{"role": "user", "content": "First instruction"},
                 {"type": "compaction", "encrypted_content": "signed/opaque==", "id": "cmp_1",
                  "created_by": "provider", "future_field": {"keep": True}},
                 {"role": "user", "content": "Continue"}]
        self.assertIs(relay(input=items, model="host-model"), provider.response)
        self.assertEqual(provider.calls[0]["input"], items)

    def test_parallel_call_results_can_arrive_in_reverse_order(self):
        relay, provider = self.connect()
        items = history()[:3]
        items.append({"type": "function_call", "call_id": "call_2", "name": "other", "arguments": "not-valid-json"})
        items.extend([{"type": "function_call_output", "call_id": "call_2", "output": []}, history()[-1]])
        relay(input=items)
        self.assertEqual(provider.calls[0]["input"], items)

    def test_missing_duplicate_or_unmatched_function_results_fail_before_provider(self):
        relay, provider = self.connect()
        bad = [history()[:-1], history() + [history()[-1]],
               [history()[0], history()[-1]],
               history() + [history()[2], history()[-1]],
               history()[:3] + [{"role": "user", "content": "new turn"}, history()[-1]],
               history()[:3] + [{"type": "compaction", "encrypted_content": "hidden"}, history()[-1]]]
        for items in bad:
            with self.subTest(items=items), self.assertRaises(ValueError):
                relay(input=items)
        self.assertEqual(provider.calls, [])

    def test_hidden_context_and_overrides_rejected(self):
        relay, provider = self.connect()
        for options in ({"previous_response_id": "resp_hidden"}, {"conversation": "conv"},
                        {"prompt": {"id": "pmpt"}}, {"extra_body": {"input": []}},
                        {"extra_query": {"conversation": "hidden"}}, {"truncation": "auto"},
                        {"messages": []}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                relay(input=history(), **options)
        with self.assertRaisesRegex(ValueError, "item_reference"):
            relay(input=[{"type": "item_reference", "id": "hidden"}])
        self.assertEqual(provider.calls, [])

    def test_unsupported_and_partial_native_content_rejected_without_flattening(self):
        relay, provider = self.connect()
        cases = [
            [{"type": "reasoning", "id": "rs", "summary": []}],
            [{"type": "reasoning", "summary": [], "encrypted_content": "opaque", "status": "in_progress"}],
            [{"type": "compaction", "encrypted_content": None}],
            [{"type": "computer_call", "id": "unsupported"}],
            [{"role": "user", "content": [{"type": "unknown_block"}]}],
            [{"role": "tool", "content": "not-a-responses-role"}],
        ]
        for items in cases:
            with self.subTest(items=items), self.assertRaises(ValueError):
                relay(input=items)
        with self.assertRaisesRegex(ValueError, "function tools"):
            relay(input=history(), tools=[{"type": "web_search"}])
        self.assertEqual(provider.calls, [])

    def test_native_content_metadata_and_roles_remain_exact(self):
        relay, provider = self.connect()
        items = [
            {"role": "developer", "content": "Follow the application."},
            {"role": "user", "content": [{"type": "input_image", "image_url": "https://example.invalid/image.png", "detail": "low"}]},
            {"type": "message", "id": "m_1", "role": "assistant", "status": "completed", "phase": "final_answer",
             "content": [{"type": "output_text", "text": "Photo", "annotations": [{"type": "url_citation", "url": "https://example.invalid"}], "logprobs": []}]},
            {"role": "user", "content": "Continue"},
        ]
        relay(input=items)
        self.assertEqual(provider.calls[0]["input"], items)

    def test_entire_visible_body_counted_and_over_budget_never_pruned(self):
        request = {"input": history(), "instructions": "policy" * 30,
                   "tools": [{"type": "function", "name": "lookup", "description": "x" * 200}]}
        size = len(encode(request))
        relay, provider = self.connect(config=RelayConfig(max_chars=size - 1))
        with self.assertRaises(BudgetExceeded) as raised:
            relay(**request)
        self.assertEqual(raised.exception.size_chars, size)
        self.assertEqual(provider.calls, [])
        exact, _ = self.connect(config=RelayConfig(max_chars=size))
        self.assertEqual(exact.prepare(**request).request, request)

    def test_native_token_counter_receives_all_body_and_cannot_mutate_dispatch(self):
        counted = []
        def counter(requests):
            counted.append(clone(requests))
            requests[0]["input"].clear()
            return 11
        relay, provider = self.connect(token_budget=TokenBudget(10, counter))
        with self.assertRaises(TokenBudgetExceeded):
            relay(input=history(), tools=[{"type": "function", "name": "lookup"}], instructions="rules")
        self.assertIn("tools", counted[0][0])
        self.assertIn("instructions", counted[0][0])
        self.assertEqual(provider.calls, [])
        good, good_provider = self.connect(token_budget=TokenBudget(11, counter))
        good(input=history())
        self.assertEqual(good_provider.calls[0]["input"], history())

    def test_explicit_records_durable_and_never_implicitly_injected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "native.sqlite3"))
            relay, provider = self.connect(config=config)
            relay.remember("export_format", "CSV", source="user", text="Export as CSV")
            snapshot = relay.memory_snapshot()
            self.assertIn("CSV", snapshot["text"])
            relay(input=history())
            self.assertEqual(provider.calls[0]["input"], history())
            session_id = relay.session_id
            relay.close()
            resumed, _ = self.connect(config=config, session_id=session_id)
            self.assertEqual(resumed.memory_snapshot(), snapshot)
            other, _ = self.connect(config=config)
            self.assertNotIn("CSV", other.memory_snapshot()["text"])
            other.close()
            resumed.close()

    def test_stream_returned_unconsumed_and_not_closed(self):
        touched = []
        def events():
            touched.append(True)
            yield "event"
        stream = events()
        relay, provider = self.connect(Provider(stream))
        self.assertIs(relay(input=history(), stream=True), stream)
        relay.close()
        self.assertEqual(touched, [])
        self.assertFalse(provider.closed)
        self.assertEqual(next(stream), "event")
        stream.close()

    def test_provider_error_preserves_caller_history_and_retries_are_explicit(self):
        class Broken(Provider):
            def create(self, **request):
                self.calls.append(request)
                request["input"].clear()
                raise RuntimeError("provider failed")
        relay, provider = self.connect(Broken())
        items = history()
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            relay(input=items)
        self.assertEqual(items, history())
        self.assertEqual(len(provider.calls), 1)

    def test_connections_do_not_patch_sdk_or_close_external_guard(self):
        provider = Provider()
        bound = provider.create
        guard = ContextGuard()
        self.addCleanup(guard.close)
        relay, _ = self.connect(provider, guard=guard)
        self.assertEqual(provider.create, bound)
        relay.close()
        guard.remember("alive", True)
        with self.assertRaises(RuntimeError):
            relay(input=history())
        self.assertFalse(provider.closed)

    def test_unsupported_configuration_rejected_before_database_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "must-not-exist.sqlite3"
            for options in ({"memory": MemoryPolicy()}, {"memory_interpreter": object()},
                            {"output": object()}, {"correction_fields": [object()]}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    self.connect(config=RelayConfig(db_path=str(path)), **options)
            for config in (RelayConfig(db_path=str(path), pinned=(0,)),
                           RelayConfig(db_path=str(path), memory=MemoryPolicy())):
                with self.assertRaises(ValueError):
                    self.connect(config=config)
            self.assertFalse(path.exists())

    def test_native_mode_and_materialized_json_are_explicit(self):
        with self.assertRaises(ValueError):
            connect_openai(client(Provider()), native=True)
        relay, provider = self.connect()
        for items in ("Hello", (item for item in history()), [], [object()]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                relay(input=items)
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(ValueError, "cycle"):
            relay(input=cyclic)
        class PretendSDK:
            def model_dump(self, **kwargs):
                raise AssertionError("Do not execute a duck-typed serializer")
        with self.assertRaises(ValueError):
            relay(input=[PretendSDK()])
        self.assertEqual(provider.calls, [])

    def test_decorated_connections_and_attachment_markers_cannot_be_reconnected(self):
        inner, provider = self.connect()
        @functools.wraps(inner.invoke)
        def decorated(**request):
            return inner.invoke(**request)
        def attached(**request):
            return provider.create(**request)
        attached.__relay_attachment__ = True
        for method in (inner, inner.invoke, decorated, attached):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "already connected"):
                self.connect(SimpleNamespace(create=method))
        self.assertEqual(provider.calls, [])

    def test_function_output_blocks_are_bounded_without_losing_supported_metadata(self):
        relay, provider = self.connect()
        for block in ({"type": "item_reference", "id": "hidden"}, {"type": "input_text"},
                      {"type": "input_file"}, "untyped", {"type": ["input_text"]}):
            items = history()
            items[-1]["output"] = [block]
            with self.subTest(block=block), self.assertRaises(ValueError):
                relay(input=items)
        self.assertEqual(provider.calls, [])
        items = history()
        items[-1]["output"] = [
            {"type": "input_text", "text": "Complete", "prompt_cache_breakpoint": None},
            {"type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "low"},
            {"type": "input_file", "file_id": "file_1", "detail": "auto"},
        ]
        relay(input=items)
        self.assertEqual(provider.calls[0]["input"], items)

    def test_transport_settings_preserved_but_not_counted_as_model_context(self):
        relay, provider = self.connect()
        timeout = object()
        headers = {"x-request-id": "host-id"}
        prepared = relay.invoke_result(input=history(), timeout=timeout, extra_headers=headers)
        self.assertIs(provider.calls[0]["timeout"], timeout)
        self.assertIs(provider.calls[0]["extra_headers"], headers)
        self.assertEqual(prepared.prepared.request, {"input": history()})

    @unittest.skipUnless(importlib.util.find_spec("openai"), "Optional OpenAI SDK is not installed")
    def test_genuine_sdk_reasoning_and_function_objects_serialize_all_set_fields(self):
        from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem
        reason = ResponseReasoningItem(id="rs_sdk", type="reasoning", summary=[], encrypted_content="opaque+==", extension={"retain": None})
        call = ResponseFunctionToolCall(id="fc_sdk", type="function_call", call_id="call_sdk", name="lookup", arguments='{"a":1}')
        items = [history()[0], reason, call,
                 {"type": "function_call_output", "call_id": "call_sdk", "output": "ok"}]
        relay, provider = self.connect()
        relay(input=items)
        expected = [history()[0], reason.model_dump(mode="json", by_alias=True, exclude_unset=True),
                    call.model_dump(mode="json", by_alias=True, exclude_unset=True), items[-1]]
        self.assertEqual(provider.calls[0]["input"], expected)
        self.assertEqual(reason.encrypted_content, "opaque+==")
        self.assertNotIn("status", provider.calls[0]["input"][1])


class NativeOpenAIAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_identity_and_request_snapshots_per_call(self):
        class AsyncProvider(Provider):
            async def create(self, **request):
                self.calls.append(request)
                await asyncio.sleep(0)
                return self.response
        provider = AsyncProvider()
        with connect_openai(client(provider), api="responses", native=True) as relay:
            self.assertIsInstance(relay, AsyncNativeConnection)
            first, second = await asyncio.gather(relay.ainvoke_result(input=history(), model="one"),
                                                  relay.ainvoke_result(input=history(), model="two"))
            self.assertIs(first.response, provider.response)
            self.assertIs(second.response, provider.response)
            self.assertEqual(first.prepared.request["model"], "one")
            self.assertEqual(second.prepared.request["model"], "two")
            self.assertNotEqual(first.prepared.history_id, second.prepared.history_id)

    async def test_async_cancel_leaves_source_and_stored_records_intact_no_retries(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        class Waiting(Provider):
            async def create(self, **request):
                self.calls.append(request)
                request["input"].clear()
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
        provider = Waiting()
        async with connect_openai(client(provider), api="responses", native=True) as relay:
            relay.remember("format", "CSV")
            before = relay.memory_snapshot()
            items = history()
            task = asyncio.create_task(relay(input=items))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(cancelled.is_set())
            self.assertEqual(items, history())
            self.assertEqual(relay.memory_snapshot(), before)
            self.assertEqual(len(provider.calls), 1)

    async def test_async_stream_is_returned_without_iteration_or_close(self):
        touched = []
        async def events():
            touched.append(True)
            yield "event"
        stream = events()
        class AsyncProvider(Provider):
            async def create(self, **request):
                return stream
        async with connect_openai(client(AsyncProvider()), api="responses", native=True) as relay:
            self.assertIs(await relay(input=history(), stream=True), stream)
            self.assertEqual(touched, [])
        self.assertEqual(await anext(stream), "event")
        await stream.aclose()


if __name__ == "__main__":
    unittest.main()
