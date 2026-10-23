"""Native Claude request contracts; stand-ins make no network/model calls."""

import copy
import functools
import unittest
from types import SimpleNamespace

from context_relay import MemoryPolicy, OutputPolicy, RelayConfig, TokenBudget, connect_anthropic
from context_relay.budget import TokenBudgetExceeded
from context_relay.models import BudgetExceeded, encode


def client_with(create):
    return SimpleNamespace(messages=SimpleNamespace(create=create),
                           beta=SimpleNamespace(messages=SimpleNamespace(create=create)))


def native_history():
    return [
        {"role": "user", "content": "Read the two files."},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "opaque-signature-雪"},
            {"type": "redacted_thinking", "data": "opaque-redacted-000="},
            {"type": "tool_use", "id": "call-a", "name": "read_file",
             "input": {"path": "a.py", "option": None}},
            {"type": "tool_use", "id": "call-b", "name": "read_file", "input": {"path": "b.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-b", "content": "B\n"},
            {"type": "tool_result", "tool_use_id": "call-a", "is_error": False,
             "content": [{"type": "text", "text": "A 雪", "cache_control": {"type": "ephemeral"}}]},
        ]},
    ]


class NativeAnthropicTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.reply = [], object()
        def create(**kwargs):
            self.calls.append(kwargs)
            return self.reply
        self.client = client_with(create)

    def test_parallel_tools_thinking_signatures_and_null_input_are_exact(self):
        history = native_history()
        original = copy.deepcopy(history)
        with connect_anthropic(self.client, native=True) as agent:
            result = agent.invoke_result(messages=history, model="host-model", max_tokens=2048)
        self.assertIs(result.response, self.reply)
        self.assertEqual(self.calls[0]["messages"], original)
        self.assertEqual(result.prepared.request["messages"], original)
        self.assertEqual(history, original)
        report = result.prepared.report
        self.assertTrue(report["all_input_preserved"])
        self.assertFalse(report["memory_injected"])
        self.assertEqual(report["native_block_counts"]["tool_result"], 2)
        self.assertEqual(report["native_block_counts"]["thinking"], 1)
        self.assertEqual(report["native_block_counts"]["redacted_thinking"], 1)

    def test_replay_never_inserts_or_rebuilds_prior_prefix(self):
        history = native_history()
        system = [{"type": "text", "text": "固定 instructions", "cache_control": {"type": "ephemeral"}}]
        tools = [{"name": "read_file", "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}}}, "defer_loading": True}]
        with connect_anthropic(self.client, native=True) as agent:
            agent(messages=history[:1], system=system, tools=tools)
            agent.remember("new_setting", "must not rewrite the system prompt")
            agent(messages=history, system=system, tools=tools)
        self.assertEqual(self.calls[0]["messages"], self.calls[1]["messages"][:1])
        self.assertEqual(self.calls[0]["system"], self.calls[1]["system"])
        self.assertEqual(self.calls[0]["tools"], self.calls[1]["tools"])
        self.assertEqual(self.calls[1]["messages"], history)
        self.assertNotIn("new_setting", encode(self.calls[1]))

    def test_mid_conversation_system_and_tool_changes_remain_at_original_positions(self):
        history = native_history() + [
            {"role": "system", "clear_at": "next_user_message", "output_config": {"effort": "high"},
             "content": [{"type": "text", "text": "Turn reminder"},
                         {"type": "tool_addition", "tool": {"type": "tool_reference", "name": "read_file"}},
                         {"type": "tool_removal", "tool": {"type": "tool_reference", "name": "old_tool"}}]},
        ]
        with connect_anthropic(self.client, native=True, beta=True) as agent:
            agent(messages=history, system="Initial stable system", betas=["host-selected-beta"])
        self.assertEqual(self.calls[0]["messages"], history)
        self.assertEqual(self.calls[0]["system"], "Initial stable system")
        self.assertEqual(self.calls[0]["betas"], ["host-selected-beta"])

    def test_threshold_compaction_and_context_management_are_preserved(self):
        history = native_history() + [
            {"role": "assistant", "content": [
                {"type": "compaction", "content": "Provider summary", "encrypted_content": "opaque-blob==",
                 "cache_control": {"type": "ephemeral"}, "future_metadata": {"keep": [None, True]}},
                {"type": "text", "text": "Completed."}]},
            {"role": "user", "content": "Continue."},
        ]
        management = {"edits": [{"type": "clear_thinking_20251015", "keep": "all"},
                                {"type": "compact_20260112", "pause_after_compaction": True}]}
        thinking = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "error"}}
        with connect_anthropic(self.client, native=True, beta=True) as agent:
            result = agent.invoke_result(messages=history, context_management=management,
                                         thinking=thinking, betas=["compact-2026-01-12"])
        self.assertEqual(self.calls[0]["messages"], history)
        self.assertEqual(self.calls[0]["context_management"], management)
        self.assertEqual(self.calls[0]["thinking"], thinking)
        self.assertTrue(result.prepared.report["provider_context_management_requested"])
        self.assertFalse(result.prepared.report["provider_effective_context_known"])

    def test_signed_on_demand_compaction_carries_signature_tool_changes_and_kept_turns(self):
        block = {"type": "compaction", "content": "Signed provider summary", "signature": "signed==",
                 "encrypted_content": "encrypted==", "tool_changes": [
                     {"type": "tool_removal", "tool": {"type": "tool_reference", "name": "old"}}]}
        history = [{"role": "assistant", "content": [block]},
                   {"role": "assistant", "content": [{"type": "thinking", "thinking": "Kept", "signature": "kept=="},
                                                       {"type": "text", "text": "Prior answer"}]},
                   {"role": "user", "content": "Next"}]
        with connect_anthropic(self.client, native=True, beta=True) as agent:
            agent(messages=history, compaction={"type": "summarize"}, betas=["compact-2026-09-04"])
        self.assertEqual(self.calls[0]["messages"], history)
        self.assertEqual(self.calls[0]["compaction"], {"type": "summarize"})

    def test_failed_compaction_null_content_is_preserved_as_a_noop(self):
        history = [{"role": "assistant", "content": [{"type": "compaction", "content": None}]},
                   {"role": "user", "content": "Continue"}]
        with connect_anthropic(self.client, native=True, beta=True) as agent:
            agent(messages=history)
        self.assertIsNone(self.calls[0]["messages"][0]["content"][0]["content"])

    def test_request_budget_includes_tools_system_and_all_provider_options(self):
        request = {"messages": native_history(), "system": "s" * 50,
                   "tools": [{"name": "read_file", "input_schema": {"description": "d" * 100}}],
                   "context_management": {"edits": [{"type": "compact_20260112"}]}}
        size = len(encode(request))
        with connect_anthropic(self.client, native=True, beta=True, config=RelayConfig(max_chars=size)) as agent:
            result = agent.invoke_result(**request)
        self.assertEqual(result.prepared.report["size_chars"], size)
        self.assertEqual(result.prepared.report["budget_scope"], "visible_request_body")
        with connect_anthropic(self.client, native=True, beta=True, config=RelayConfig(max_chars=size - 1)) as agent:
            with self.assertRaises(BudgetExceeded):
                agent(**request)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(request["messages"], native_history())

    def test_budget_never_drops_old_native_turns_to_make_room(self):
        history = [{"role": "user", "content": "old" * 1000},
                   {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "old-signature"}]},
                   {"role": "user", "content": "New"}]
        with connect_anthropic(self.client, native=True, config=RelayConfig(max_chars=1000)) as agent:
            with self.assertRaises(BudgetExceeded):
                agent(messages=history)
        self.assertEqual(self.calls, [])

    def test_token_counter_receives_full_request_and_cannot_mutate_sent_state(self):
        request = {"messages": native_history(), "system": "Stable", "tools": [{"name": "read_file"}]}
        size = len(encode(request))
        measured = []
        def count(items):
            measured.append(copy.deepcopy(items))
            value = len(encode(items[0]))
            items[0]["messages"].clear()
            return value
        with connect_anthropic(self.client, native=True, token_budget=TokenBudget(size, count)) as agent:
            agent(**request)
        self.assertEqual(measured, [[request]])
        self.assertEqual(self.calls[0], request)
        with connect_anthropic(self.client, native=True, token_budget=TokenBudget(size - 1, count)) as agent:
            with self.assertRaises(TokenBudgetExceeded):
                agent(**request)
        self.assertEqual(len(self.calls), 1)

    def test_preparation_is_a_read_only_snapshot_with_no_provider_call(self):
        request = {"messages": native_history()}
        with connect_anthropic(self.client, native=True) as agent:
            prepared = agent.prepare(**request)
            exposed = prepared.request
            exposed["messages"].clear()
            request["messages"][1]["content"][0]["signature"] = "changed afterward"
            self.assertEqual(prepared.request["messages"], native_history())
            self.assertFalse(prepared.report["automatic_memory"])
        self.assertEqual(self.calls, [])

    def test_pairing_failures_are_rejected_before_provider(self):
        mutations = [lambda h: h.pop(),
                     lambda h: h[-1]["content"].pop(),
                     lambda h: h[-1]["content"][0].update(tool_use_id="unknown"),
                     lambda h: h[-1]["content"].append(copy.deepcopy(h[-1]["content"][0])),
                     lambda h: h[-1]["content"].insert(0, {"type": "text", "text": "Wrong order"}),
                     lambda h: h.insert(2, {"role": "assistant", "content": "Interrupted"}),
                     lambda h: h[1]["content"][3].update(id="call-a"),
                     lambda h: h[1]["content"].append({"type": "compaction", "content": "Mid tool round"})]
        with connect_anthropic(self.client, native=True) as agent:
            for mutate in mutations:
                history = native_history()
                mutate(history)
                with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                    agent(messages=history)
        self.assertEqual(self.calls, [])

    def test_missing_signature_and_malformed_native_blocks_fail_without_repair(self):
        histories = [
            [{"role": "user", "content": "Start"}, {"role": "assistant", "content": [{"type": "thinking", "thinking": "x"}]}],
            [{"role": "user", "content": [{"type": "thinking", "thinking": "x", "signature": "s"}]}],
            [{"role": "assistant", "content": "Unrelated prefix"}],
            [{"role": "system", "content": "Move me to top level"}, {"role": "user", "content": "Hi"}],
        ]
        with connect_anthropic(self.client, native=True) as agent:
            for history in histories:
                with self.subTest(history=history), self.assertRaises(ValueError):
                    agent(messages=history)
        self.assertEqual(self.calls, [])

    def test_unsupported_modalities_and_server_tool_blocks_are_not_flattened(self):
        # v0.13 explicitly supports image/document sources; malformed media and
        # server-tool protocols remain rejected. Preservation is tested separately.
        for block in [{"type": "image", "source": {"type": "unknown", "url": "https://example.com/image"}},
                      {"type": "document", "source": {"type": "file"}},
                      {"type": "server_tool_use", "id": "server-1", "name": "web_search", "input": {}},
                      {"type": "future_unknown", "opaque": "keep"}]:
            with self.subTest(block=block), connect_anthropic(self.client, native=True) as agent:
                with self.assertRaises(ValueError):
                    agent(messages=[{"role": "user", "content": [block]}])
        history = native_history()
        history[-1]["content"][0]["content"] = [{"type": "image", "source": {}}]
        with connect_anthropic(self.client, native=True) as agent:
            with self.assertRaises(ValueError):
                agent(messages=history)
        self.assertEqual(self.calls, [])

    def test_server_hidden_context_and_body_override_are_rejected(self):
        with connect_anthropic(self.client, native=True) as agent:
            for options in [{"container": "old-container"}, {"mcp_servers": []},
                            {"conversation": "server-conversation"}, {"extra_body": {"messages": []}},
                            {"extra_query": {"hidden": "context"}}]:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    agent(messages=[{"role": "user", "content": "Hi"}], **options)
        self.assertEqual(self.calls, [])

    def test_text_memory_output_and_selection_options_are_not_silently_ignored(self):
        for options in [{"memory": MemoryPolicy()}, {"config": RelayConfig(memory=MemoryPolicy())},
                        {"output": OutputPolicy()}, {"config": RelayConfig(output=OutputPolicy())},
                        {"config": RelayConfig(pinned=(0,))}, {"config": RelayConfig(recent_turns=1)}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                connect_anthropic(self.client, native=True, **options)
        for options in [{"native": 1}, {"native": True, "beta": "yes"}, {"beta": True}]:
            with self.subTest(options=options), self.assertRaises((TypeError, ValueError)):
                connect_anthropic(self.client, **options)
        self.assertEqual(self.calls, [])

    def test_explicit_snapshot_is_never_automatically_inserted(self):
        with connect_anthropic(self.client, native=True) as agent:
            agent.remember("format", "JSON")
            snapshot = agent.memory_snapshot()
            self.assertEqual(snapshot["facts"]["format"], "JSON")
            history = [{"role": "user", "content": snapshot["text"]}, {"role": "user", "content": "Continue"}]
            agent(messages=history)
            with self.assertRaises(ValueError):
                agent.conversation()
        self.assertEqual(self.calls[0]["messages"], history)

    def test_beta_and_stable_methods_are_selected_explicitly(self):
        called = []
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: called.append("stable")),
            beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: called.append("beta"))))
        for beta in (False, True):
            with connect_anthropic(client, native=True, beta=beta) as agent:
                agent(messages=[{"role": "user", "content": "Hi"}])
        self.assertEqual(called, ["stable", "beta"])

    def test_unknown_fields_on_supported_blocks_and_options_are_retained(self):
        history = native_history()
        history[1]["content"][0]["provider_extension"] = {"do_not_remove": "signature-metadata"}
        with connect_anthropic(self.client, native=True, beta=True) as agent:
            agent(messages=history, future_option={"preserve": [None, 1]},
                  thinking={"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}})
        self.assertEqual(self.calls[0]["messages"], history)
        self.assertEqual(self.calls[0]["future_option"], {"preserve": [None, 1]})

    def test_response_stream_metadata_and_error_ownership_are_unchanged(self):
        class Stream:
            def __iter__(self):
                raise AssertionError("Relay consumed the stream")
            def close(self):
                raise AssertionError("Relay closed the stream")
        self.reply = Stream()
        headers, timeout = {"anthropic-beta": "thinking-binding-controls-2026-08-01"}, object()
        with connect_anthropic(self.client, native=True) as agent:
            result = agent.invoke_result(messages=[{"role": "user", "content": "Hi"}],
                                         stream=True, extra_headers=headers, timeout=timeout)
        self.assertIs(result.response, self.reply)
        self.assertIs(self.calls[0]["extra_headers"], headers)
        self.assertIs(self.calls[0]["timeout"], timeout)
        self.assertNotIn("extra_headers", result.prepared.request)
        failure = RuntimeError("Provider rejected a signature")
        def fail(**kwargs):
            self.calls.append(kwargs)
            raise failure
        with connect_anthropic(client_with(fail), native=True) as agent:
            with self.assertRaises(RuntimeError) as caught:
                agent(messages=native_history())
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(self.calls), 2)


class AsyncNativeAnthropicTests(unittest.IsolatedAsyncioTestCase):
    async def test_decorated_async_beta_preserves_native_payload_and_stream(self):
        requests = []
        class Stream:
            def __aiter__(self):
                raise AssertionError("Relay consumed the stream")
            async def aclose(self):
                raise AssertionError("Relay closed the stream")
        stream = Stream()
        def decorate(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)
            return wrapper
        @decorate
        async def create(**kwargs):
            requests.append(kwargs)
            return stream
        async with connect_anthropic(client_with(create), native=True, beta=True) as agent:
            result = await agent.invoke_result(messages=native_history(), stream=True,
                                               context_management={"edits": []})
        self.assertIs(result.response, stream)
        self.assertEqual(requests[0]["messages"], native_history())
        self.assertEqual(requests[0]["context_management"], {"edits": []})


if __name__ == "__main__":
    unittest.main()
