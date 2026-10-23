"""Opt-in trusted projection and broader native formats, without provider calls."""

import asyncio
import copy
import unittest
from types import SimpleNamespace

from context_relay import RelayConfig, TokenBudget, auto_connect, connect_anthropic, connect_openai
from context_relay.models import BudgetExceeded, encode
from context_relay.budget import TokenBudgetExceeded


class Client:
    def __init__(self, call=None):
        self.calls = []
        self.response = object()
        self.responses = SimpleNamespace(create=call or self.create)
        self.messages = SimpleNamespace(create=call or self.create)

    def create(self, **request):
        self.calls.append(request)
        return self.response


def user(text="Continue"):
    return {"role": "user", "content": text}


def thinking():
    return {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "", "signature": "opaque-prefix=="},
        {"type": "text", "text": "Done"}]}


class NativeMemoryTests(unittest.TestCase):
    def connect(self, provider, **options):
        client = Client()
        relay = auto_connect(client, provider=provider, native=True, native_memory=True,
                             **({"api": "responses"} if provider == "openai" else {}), **options)
        self.addCleanup(relay.close)
        return relay, client

    def test_openai_projection_preserves_opaque_items_and_original_instructions(self):
        relay, client = self.connect("openai")
        relay.remember("export_format", "JSON")
        history = [{"type": "compaction", "encrypted_content": "opaque+/=="}, user()]
        original = copy.deepcopy(history)
        first = relay.invoke_result(input=history, instructions="Application policy")
        self.assertEqual(client.calls[0]["input"], original)
        self.assertEqual(history, original)
        self.assertTrue(first.prepared.request["instructions"].startswith("Application policy\n\n"))
        self.assertIn('"export_format":"JSON"', first.prepared.request["instructions"])
        self.assertTrue(first.prepared.report["memory_injected"])
        self.assertFalse(first.prepared.report["automatic_memory"])
        self.assertIs(first.response, client.response)
        relay.remember("export_format", "CSV")
        second = relay.prepare(input=history, instructions="Application policy")
        self.assertIn('"export_format":"CSV"', second.request["instructions"])
        self.assertNotIn('"export_format":"JSON"', second.request["instructions"])
        self.assertEqual(second.request["input"], original)

    def test_openai_prepared_instructions_idempotent_or_explicitly_stale(self):
        relay, client = self.connect("openai")
        relay.remember("format", "JSON")
        prepared = relay.prepare(input=[user()])
        self.assertEqual(relay.prepare(**prepared.request).request, prepared.request)
        relay.remember("format", "CSV")
        with self.assertRaisesRegex(ValueError, "original application instructions"):
            relay(**prepared.request)
        self.assertEqual(client.calls, [])

    def test_claude_projection_and_updates_append_without_changing_signed_prefix(self):
        relay, client = self.connect("anthropic")
        relay.remember("format", "JSON")
        initial = [user("Earlier"), thinking(), user()]
        original = copy.deepcopy(initial)
        system = [{"type": "text", "text": "Stable", "cache_control": {"type": "ephemeral"}}]
        tools = [{"name": "read", "input_schema": {"type": "object"}}]
        first = relay.invoke_result(messages=initial, system=system, tools=tools)
        self.assertEqual(initial, original)
        sent = first.prepared.request
        self.assertEqual(sent["messages"][:-1], original)
        self.assertEqual(sent["system"], system)
        self.assertEqual(sent["tools"], tools)
        self.assertEqual(sent["messages"][-1]["role"], "system")
        self.assertIn('"format":"JSON"', sent["messages"][-1]["content"])
        history = sent["messages"] + [thinking(), user("Now CSV")]
        relay.remember("format", "CSV")
        second = relay.invoke_result(messages=history, system=system, tools=tools)
        self.assertEqual(second.prepared.request["messages"][:-1], history)
        self.assertIn('"format":"CSV"', second.prepared.request["messages"][-1]["content"])
        self.assertTrue(second.prepared.report["prefix_replay_checked"])
        # The same revision remains present without adding a message every call.
        third = relay.prepare(messages=second.prepared.request["messages"] + [thinking(), user()],
                              system=system, tools=tools)
        self.assertFalse(third.report["memory_injected"])
        self.assertTrue(third.report["memory_present"])
        self.assertEqual(len(client.calls), 2)

    def test_claude_omitted_memory_changed_prefix_system_and_tools_stop_before_dispatch(self):
        relay, client = self.connect("anthropic")
        relay.remember("format", "JSON")
        first = relay.invoke_result(messages=[user()], system="Stable", tools=[])
        history = first.prepared.request["messages"] + [thinking(), user("Next")]
        cases = [dict(messages=[user(), thinking(), user("Next")], system="Stable", tools=[]),
                 dict(messages=history, system="Changed", tools=[]),
                 dict(messages=history, system="Stable", tools=[{"name": "new"}])]
        changed = copy.deepcopy(history)
        changed[0]["content"] = "Rewritten"
        cases.append(dict(messages=changed, system="Stable", tools=[]))
        for request in cases:
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, "previously sent prefix"):
                relay(**request)
        self.assertEqual(len(client.calls), 1)

    def test_claude_prepare_is_read_only_and_same_input_retry_is_exact(self):
        relay, client = self.connect("anthropic")
        relay.remember("format", "JSON")
        relay.prepare(messages=[user("Never sent")])
        relay(messages=[user("Actually sent")])
        relay(messages=[user("Actually sent")])
        self.assertEqual(client.calls[0], client.calls[1])

    def test_failed_dispatch_keeps_checkpoint_and_retries_do_not_mutate_source(self):
        calls = []
        def fail(**request):
            calls.append(copy.deepcopy(request))
            request["messages"].clear()
            raise RuntimeError("network result unknown")
        with connect_anthropic(Client(fail), native=True, native_memory=True) as relay:
            relay.remember("format", "JSON")
            history = [user()]
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    relay(messages=history)
            self.assertEqual(history, [user()])
            self.assertEqual(calls[0], calls[1])

    def test_claude_memory_only_at_complete_user_or_tool_boundary(self):
        relay, client = self.connect("anthropic")
        relay.remember("format", "JSON")
        call = {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "unchanged"},
            {"type": "tool_use", "id": "t", "name": "read", "input": {}}]}
        with self.assertRaises(ValueError):
            relay(messages=[user(), call])
        with self.assertRaisesRegex(ValueError, "completed user/tool-result"):
            relay(messages=[user(), thinking()])
        result = relay.invoke_result(messages=[user(), call, {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "data"}]}])
        self.assertEqual(result.prepared.request["messages"][-2]["content"][0]["type"], "tool_result")
        self.assertEqual(result.prepared.request["messages"][-1]["role"], "system")
        self.assertEqual(len(client.calls), 1)

    def test_fresh_connection_can_adopt_provider_compacted_history(self):
        relay, client = self.connect("anthropic")
        relay.remember("format", "JSON")
        relay(messages=[user()])
        compacted = [{"role": "assistant", "content": [
            {"type": "compaction", "content": "provider summary", "signature": "opaque"}]}, user()]
        with self.assertRaisesRegex(ValueError, "fresh native connection"):
            relay(messages=compacted)
        with connect_anthropic(client, native=True, native_memory=True, guard=relay.guard) as resumed:
            result = resumed.invoke_result(messages=compacted)
            self.assertEqual(result.prepared.request["messages"][:-1], compacted)

    def test_projection_counts_toward_both_budgets_and_failure_does_not_reserve_history(self):
        counted = []
        def counter(requests):
            counted.append(copy.deepcopy(requests))
            return len(encode(requests[0]))
        for budget in ("chars", "tokens"):
            with self.subTest(budget=budget):
                options = ({"config": RelayConfig(max_chars=200)} if budget == "chars"
                           else {"token_budget": TokenBudget(200, counter)})
                relay, client = self.connect("anthropic", **options)
                relay.remember("format", "JSON")
                with self.assertRaises(BudgetExceeded if budget == "chars" else TokenBudgetExceeded):
                    relay(messages=[user("Unsent")])
                self.assertEqual(client.calls, [])
                self.assertIsNone(relay._prefix_checkpoint)
        self.assertIn('"format":"JSON"', counted[0][0]["messages"][-1]["content"])

    def test_no_facts_adds_no_empty_prompt_and_defaults_remain_unchanged(self):
        for provider, input_name in (("openai", "input"), ("anthropic", "messages")):
            with self.subTest(provider=provider):
                relay, _ = self.connect(provider)
                self.assertEqual(relay.prepare(**{input_name: [user()]}).request, {input_name: [user()]})
        with connect_anthropic(Client(), native=True) as relay:
            relay.remember("format", "JSON")
            self.assertEqual(relay.prepare(messages=[user()]).request, {"messages": [user()]})

    def test_native_memory_is_an_explicit_boolean_and_separate_from_automatic_memory(self):
        for connector, options in ((connect_openai, {"api": "responses"}), (connect_anthropic, {})):
            with self.subTest(connector=connector):
                with self.assertRaises(ValueError):
                    connector(Client(), native_memory=True, **options)
                with self.assertRaises(TypeError):
                    connector(Client(), native=True, native_memory="yes", **options)
                with self.assertRaises(ValueError):
                    auto_connect(Client(), provider="anthropic", native=True, native_memory=True, memory=True)


class NativeFormatTests(unittest.TestCase):
    def test_custom_tool_roundtrip_preserves_raw_input_and_checks_result_family(self):
        client = Client()
        call = {"type": "custom_tool_call", "name": "grammar", "call_id": "c", "input": "raw\ninput",
                "caller": {"type": "direct"}, "extension": None}
        output = {"type": "custom_tool_call_output", "call_id": "c", "output": [
            {"type": "input_text", "text": "Done"}]}
        with connect_openai(client, api="responses", native=True) as relay:
            history = [user(), call, output]
            result = relay.invoke_result(input=history, tools=[{"type": "custom", "name": "grammar"}])
            self.assertEqual(result.prepared.request["input"], history)
            self.assertEqual(result.prepared.report["custom_tool_calls"], 1)
            for bad in ({**output, "type": "function_call_output"},
                        {**output, "caller": {"type": "program", "caller_id": "hidden"}}):
                with self.assertRaises(ValueError):
                    relay(input=[user(), call, bad])
            with self.assertRaises(ValueError):
                relay(input=[user(), call])
        self.assertEqual(len(client.calls), 1)

    def test_claude_media_and_search_results_survive_in_user_and_tool_content(self):
        client = Client()
        blocks = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="},
             "cache_control": {"type": "ephemeral"}},
            {"type": "image", "source": {"type": "file", "file_id": "file-image"}},
            {"type": "document", "source": {"type": "url", "url": "https://example.invalid/report.pdf"},
             "title": "Report", "citations": {"enabled": True}},
            {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "Fact"}},
            {"type": "document", "source": {"type": "content", "content": [
                {"type": "text", "text": "Evidence"},
                {"type": "image", "source": {"type": "url", "url": "https://example.invalid/photo.png"}}]}},
            {"type": "search_result", "source": "https://example.invalid", "title": "Source",
             "content": [{"type": "text", "text": "Result"}], "citations": {"enabled": True}},
        ]
        history = [{"role": "user", "content": blocks},
                   {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "read", "input": {}}]},
                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": blocks}]}]
        original = copy.deepcopy(history)
        with connect_anthropic(client, native=True) as relay:
            result = relay.invoke_result(messages=history)
        self.assertEqual(result.prepared.request["messages"], original)
        self.assertEqual(history, original)
        self.assertFalse(result.prepared.report["opaque_context_tokens_measured"])

    def test_claude_unknown_sources_nested_native_blocks_and_wrong_roles_fail_closed(self):
        client = Client()
        cases = [{"type": "image", "source": {"type": "unknown", "data": "x"}},
                 {"type": "image", "source": {"type": ["url"], "url": "link"}},
                 {"type": "document", "source": {"type": "content", "content": [{"type": "tool_use"}]}},
                 {"type": "image", "source": {"type": "base64", "media_type": "text/plain", "data": "x"}},
                 {"type": "search_result", "source": "link", "title": "Title", "content": [{"type": "thinking"}]}]
        cases.append({"type": "search_result", "source": "link", "title": "Title", "content": "not-blocks"})
        with connect_anthropic(client, native=True) as relay:
            for block in cases:
                with self.subTest(block=block), self.assertRaises(ValueError):
                    relay(messages=[{"role": "user", "content": [block]}])
            with self.assertRaises(ValueError):
                relay(messages=[user(), {"role": "assistant", "content": [
                    {"type": "image", "source": {"type": "file", "file_id": "file"}}]}])
        self.assertEqual(client.calls, [])


class AsyncNativeMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_projection_reserves_before_await_and_keeps_stream_unconsumed(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Stream:
            def __aiter__(self):
                raise AssertionError("Do not consume")
        stream = Stream()
        async def create(**request):
            calls.append(request)
            entered.set()
            await release.wait()
            return stream
        async with connect_anthropic(Client(create), native=True, native_memory=True) as relay:
            relay.remember("format", "JSON")
            pending = asyncio.create_task(relay.invoke_result(messages=[user("First")], stream=True))
            await entered.wait()
            with self.assertRaisesRegex(ValueError, "previously sent prefix"):
                await relay(messages=[user("Changed")])
            release.set()
            result = await pending
            self.assertIs(result.response, stream)
            self.assertEqual(calls[0], result.prepared.request)


if __name__ == "__main__":
    unittest.main()
