"""Provider contract tests with local stand-ins; no SDK, API keys, or network."""

import copy
import functools
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import ContextGuard, MemoryPolicy, RelayConfig, TokenBudget
from context_relay.models import BudgetExceeded, encode
from context_relay.openai import (
    _decode_chat_text, _decode_responses_text, compile_openai_chat_context, connect_openai,
)


def text(role, content):
    return {"role": role, "content": content}


def function(call_id="call-a", arguments='{"id":"P-4"}'):
    return {"type": "function", "id": call_id, "function": {"name": "lookup", "arguments": arguments}}


def tool_history():
    return [text("developer", "Use the package workflow."), text("user", "Find P-4."),
            {"role": "assistant", "content": None, "tool_calls": [function()]},
            {"role": "tool", "tool_call_id": "call-a", "content": '{"status":"at depot"}'}]


def client_with(call):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=call)),
                           responses=SimpleNamespace(create=call))


def chat_response(content="Done."):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}


def responses_response(content="Done."):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": content, "annotations": []}]}]}


class OpenAIProtocolTests(unittest.TestCase):
    def test_native_function_arguments_and_prefix_are_preserved_exactly(self):
        history = tool_history()
        history[2]["tool_calls"][0]["function"]["arguments"] = ' { "name": "雪", "x": true } '
        original = copy.deepcopy(history)
        plan = compile_openai_chat_context(history)
        self.assertEqual(plan.messages[0], original[0])
        self.assertEqual(plan.messages[2:], original[1:])
        self.assertEqual(plan.report["protocol"], "openai_chat")
        self.assertTrue(plan.report["tool_protocol_preserved"])
        self.assertEqual(history, original)

    def test_parallel_calls_allow_out_of_order_results(self):
        history = tool_history()
        history[2]["tool_calls"].append(function("call-b"))
        history.insert(3, {"role": "tool", "tool_call_id": "call-b", "content": "B"})
        self.assertEqual(compile_openai_chat_context(history).messages[2:], history[1:])

    def test_metadata_precedes_entire_latest_tool_turn(self):
        history = tool_history()
        plan = compile_openai_chat_context(history, state={"facts": {"format": "csv"}})
        self.assertIn('"format":"csv"', plan.messages[1]["content"])
        self.assertEqual(plan.messages[1]["role"], "user")
        self.assertEqual(plan.messages[2:], history[1:])

    def test_multiple_sequential_tool_batches_form_one_required_turn(self):
        history = tool_history() + [{"role": "assistant", "tool_calls": [function("call-b")]},
                                  {"role": "tool", "tool_call_id": "call-b", "content": "B"}]
        plan = compile_openai_chat_context(history, recent_turns=1)
        self.assertEqual(plan.report["required_indices"], list(range(len(history))))
        self.assertEqual(plan.messages[2:], history[1:])

    def test_pin_restores_whole_native_turn(self):
        history = tool_history() + [text("assistant", "At depot"), text("user", "Noise" + "x" * 5000),
                                  text("assistant", "Noise"), text("user", "Continue.")]
        plan = compile_openai_chat_context(history, pinned=[3], max_chars=2000, recent_turns=1)
        self.assertEqual(plan.report["included_indices"], [0, 1, 2, 3, 4, 7])
        self.assertEqual(plan.report["omitted_indices"], [5, 6])

    def test_omitted_turn_has_no_orphan_tool_messages(self):
        history = tool_history()
        history[-1]["content"] += "x" * 5000
        history.append(text("user", "New task"))
        plan = compile_openai_chat_context(history, max_chars=1800, recent_turns=1)
        self.assertEqual(plan.report["included_indices"], [0, 4])
        self.assertFalse(any(message["role"] == "tool" for message in plan.messages))

    def test_required_tool_result_cannot_be_truncated(self):
        history = tool_history()
        history[-1]["content"] += "x" * 5000
        with self.assertRaises(BudgetExceeded):
            compile_openai_chat_context(history, max_chars=1800, recent_turns=1)

    def test_exact_char_and_token_budget_include_original_native_data(self):
        history = tool_history()
        full = compile_openai_chat_context(history)
        size = len(encode(full.messages))
        seen = []
        def counter(messages):
            seen.append(messages)
            return len(encode(messages))
        plan = compile_openai_chat_context(history, max_chars=size,
                token_budget=TokenBudget(size + 30, counter, reserve_output_tokens=10, overhead_tokens=20))
        self.assertEqual(seen[-1], full.messages)
        self.assertEqual(plan.report["size_chars"], size)
        with self.assertRaises(BudgetExceeded):
            compile_openai_chat_context(history, max_chars=size - 1)

    def test_refusal_and_names_are_preserved_in_older_messages(self):
        history = [text("system", "Instructions"), {"role": "user", "content": "Old", "name": "user_one"},
                   {"role": "assistant", "content": None, "refusal": "Cannot do that.", "name": "helper"},
                   text("user", "New task")]
        plan = compile_openai_chat_context(history)
        self.assertEqual(plan.messages[:-2], history[:-1])

    def test_invalid_json_inside_argument_string_is_preserved_for_host_validation(self):
        history = tool_history()
        history[2]["tool_calls"][0]["function"]["arguments"] = "{broken:"
        self.assertEqual(compile_openai_chat_context(history).messages[-2], history[2])

    def test_wrong_missing_duplicate_or_incomplete_call_associations_fail(self):
        bad = []
        for mutate in [lambda h: h[-1].update(tool_call_id="missing"),
                       lambda h: h[-1].pop("tool_call_id"),
                       lambda h: h[2]["tool_calls"].append(function()),
                       lambda h: h.pop(),
                       lambda h: h.insert(3, text("user", "interrupt")),
                       lambda h: h.append(h[-1].copy())]:
            history = tool_history()
            mutate(history)
            bad.append(history)
        for history in bad:
            with self.subTest(history=history), self.assertRaises(ValueError):
                compile_openai_chat_context(history)

    def test_unsupported_formats_fail_without_flattening(self):
        for message in [text("user", [{"type": "text", "text": "Hello"}]),
                        {"role": "user", "content": "x", "images": ["abc"]},
                        {"role": "user", "content": "x", "tool_calls": []},
                        {"role": "assistant", "content": "x", "audio": {}},
                        {"role": "function", "name": "old", "content": "x"}]:
            with self.subTest(message=message), self.assertRaises(ValueError):
                compile_openai_chat_context([text("user", "Start"), message, text("user", "Next")])

    def test_wrong_argument_types_custom_tools_and_unknown_fields_fail(self):
        for mutate in [lambda c: c["function"].update(arguments={}),
                       lambda c: c.update(type="custom"), lambda c: c.update(index=0),
                       lambda c: c["function"].update(name=""), lambda c: c.pop("id")]:
            history = tool_history()
            mutate(history[2]["tool_calls"][0])
            with self.subTest(history=history), self.assertRaises(ValueError):
                compile_openai_chat_context(history)

    def test_prefix_and_pending_request_structure_is_strict(self):
        for history in [[], [text("system", "only")], [text("assistant", "x"), text("user", "x")],
                        [text("user", "x"), text("developer", "late"), text("user", "next")],
                        [text("user", "x"), text("assistant", "already answered")]]:
            with self.subTest(history=history), self.assertRaises(ValueError):
                compile_openai_chat_context(history)


class OpenAIConnectionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.response = object()
        def create(**kwargs):
            self.calls.append(kwargs)
            return self.response
        self.client = client_with(create)

    def test_chat_connection_preserves_response_and_provider_options(self):
        tools = [{"type": "function", "function": {"name": "lookup", "parameters": {}}}]
        with connect_openai(self.client) as agent:
            result = agent.invoke(messages=tool_history(), model="chosen-by-host", tools=tools, temperature=0)
            self.assertEqual(agent.config.protocol, "openai_chat")
        self.assertIs(result.response, self.response)
        self.assertIs(self.calls[0]["tools"], tools)
        self.assertEqual(self.calls[0]["model"], "chosen-by-host")
        self.assertEqual(self.calls[0]["messages"][2:], tool_history()[1:])

    def test_responses_maps_plain_messages_to_input_and_keeps_instructions(self):
        with connect_openai(self.client, api="responses") as agent:
            result = agent(messages=[text("user", "Hi")], model="chosen", instructions="Be concise", store=False)
        self.assertIs(result, self.response)
        self.assertNotIn("messages", self.calls[0])
        self.assertEqual(self.calls[0]["input"][-1], text("user", "Hi"))
        self.assertEqual(self.calls[0]["instructions"], "Be concise")
        self.assertIs(self.calls[0]["store"], False)

    def test_native_recovery_preserves_turn_and_protocol(self):
        with connect_openai(self.client, config=RelayConfig(recent_turns=1, max_chars=1800)) as agent:
            history = tool_history()
            history[-1]["content"] += "x" * 4000
            history.append(text("user", "Next"))
            prepared = agent.invoke(messages=history).prepared
            recovered = agent.guard.recover_context(prepared, [3], history_id=prepared.history_id, max_chars=10000)
            self.assertEqual(recovered.report["protocol"], "openai_chat")
            self.assertEqual(recovered.report["recovery"]["restored_indices"], [1, 2, 3])
            self.assertEqual(recovered.messages[:4], history[:4])

    def test_memory_can_process_native_chat_without_learning_tool_text(self):
        history = tool_history()
        history[1]["content"] = "Use CSV for the export format."
        history[-1]["content"] = "Use JSON for the export format."
        with connect_openai(self.client, memory=MemoryPolicy()) as agent:
            agent(messages=history)
            snapshot = agent.memory.snapshot()
        self.assertIn("CSV", encode(snapshot).upper())
        self.assertNotIn('"value":"JSON"', encode(snapshot))

    def test_config_file_resolves_database_and_adapter_protocol(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "relay.json"
            path.write_text(json.dumps({"db_path": "db/relay.sqlite3", "max_chars": 5000}), encoding="utf-8")
            with connect_openai(self.client, config=path) as agent:
                self.assertEqual(agent.config.protocol, "openai_chat")
                self.assertEqual(agent.config.max_chars, 5000)
                self.assertTrue(Path(agent.config.db_path).is_absolute())
            self.assertTrue((Path(temp) / "db/relay.sqlite3").exists())

    def test_api_mismatch_and_missing_client_fail_before_any_request(self):
        for kwargs in [{"api": "unknown"}, {"config": RelayConfig(protocol="ollama")}, {"config": {}}]:
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                connect_openai(self.client, **kwargs)
        with self.assertRaises(TypeError):
            connect_openai(object())
        self.assertEqual(self.calls, [])

    def test_hidden_context_and_input_overrides_are_rejected(self):
        for api in ("chat", "responses"):
            with connect_openai(self.client, api=api) as agent:
                for kwargs in [{"previous_response_id": "response-old"}, {"conversation": "conversation-old"},
                               {"prompt": {"id": "p"}}, {"input": "override"},
                               {"extra_body": {"input": "override"}}, {"truncation": "auto"},
                               {"context_management": [{"type": "compaction"}]}]:
                    with self.subTest(api=api, kwargs=kwargs), self.assertRaises(ValueError):
                        agent(messages=[text("user", "Hi")], **kwargs)
        self.assertEqual(self.calls, [])

    def test_responses_refuses_tools_and_native_items(self):
        with connect_openai(self.client, api="responses") as agent:
            for kwargs in [{"tools": [{"type": "function", "name": "lookup"}]},
                           {"tool_choice": "auto"}, {"parallel_tool_calls": True}]:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    agent(messages=[text("user", "Hi")], **kwargs)
            for history in [tool_history(), [{"type": "reasoning", "id": "r"}, text("user", "Hi")]]:
                with self.subTest(history=history), self.assertRaises(ValueError):
                    agent(messages=history)
        self.assertEqual(self.calls, [])

    def test_chat_rejects_legacy_and_custom_tool_definitions(self):
        with connect_openai(self.client) as agent:
            for kwargs in [{"functions": [{"name": "x"}]}, {"function_call": "auto"},
                           {"tools": [{"type": "custom", "custom": {"name": "x"}}]}]:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    agent(messages=[text("user", "Hi")], **kwargs)
        self.assertEqual(self.calls, [])

    def test_stream_passthrough_is_never_iterated_or_closed(self):
        class Stream:
            def __iter__(self):
                raise AssertionError("Stream consumed")
            def close(self):
                raise AssertionError("Stream closed")
        self.response = Stream()
        with connect_openai(self.client) as agent:
            result = agent(messages=[text("user", "Hi")], stream=True)
        self.assertIs(result, self.response)

    def test_provider_exception_propagates_without_retry(self):
        failure = RuntimeError("Provider unavailable")
        def create(**kwargs):
            self.calls.append(kwargs)
            raise failure
        with connect_openai(client_with(create)) as agent:
            with self.assertRaises(RuntimeError) as caught:
                agent(messages=[text("user", "Hi")])
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(self.calls), 1)

    def test_external_guard_and_provider_remain_owned_by_host(self):
        guard = ContextGuard()
        self.addCleanup(guard.close)
        with connect_openai(self.client, guard=guard) as agent:
            self.assertIs(agent.guard, guard)
        guard.remember("still", "open")
        self.assertTrue(callable(self.client.chat.completions.create))


class OpenAIDecoderTests(unittest.TestCase):
    def test_plain_completed_chat_and_responses_text_are_extracted(self):
        self.assertEqual(_decode_chat_text(chat_response()), "Done.")
        self.assertEqual(_decode_responses_text(responses_response()), "Done.")
        self.assertEqual(_decode_chat_text(SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                         message=SimpleNamespace(role="assistant", content="SDK"))])), "SDK")

    def test_chat_decoder_refuses_partial_multiple_tool_refusal_and_audio_outputs(self):
        for mutate in [lambda r: r["choices"][0].update(finish_reason="length"),
                       lambda r: r["choices"].append(r["choices"][0]),
                       lambda r: r["choices"][0]["message"].update(tool_calls=[function()]),
                       lambda r: r["choices"][0]["message"].update(refusal="No"),
                       lambda r: r["choices"][0]["message"].update(audio={"id": "audio"})]:
            response = chat_response()
            mutate(response)
            with self.subTest(response=response), self.assertRaises(ValueError):
                _decode_chat_text(response)

    def test_responses_decoder_refuses_reasoning_tools_and_refusal_items(self):
        for mutate in [lambda r: r.update(status="incomplete"),
                       lambda r: r["output"].append({"type": "reasoning", "id": "reasoning"}),
                       lambda r: r["output"].append({"type": "function_call", "call_id": "a"}),
                       lambda r: r["output"][0].update(content=[{"type": "refusal", "refusal": "No"}])]:
            response = responses_response()
            mutate(response)
            with self.subTest(response=response), self.assertRaises(ValueError):
                _decode_responses_text(response)

    def test_annotation_metadata_is_not_silently_discarded_from_managed_history(self):
        chat = chat_response()
        chat["choices"][0]["message"]["annotations"] = [{"type": "url_citation", "url": "https://example.com"}]
        responses = responses_response()
        responses["output"][0]["content"][0]["annotations"] = [{"type": "url_citation", "url": "https://example.com"}]
        for decoder, response in [(_decode_chat_text, chat), (_decode_responses_text, responses)]:
            with self.subTest(decoder=decoder.__name__), self.assertRaises(ValueError):
                decoder(response)


class AsyncOpenAIConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_transparently_decorated_sdk_async_methods_are_detected(self):
        response = object()
        def sdk_decorator(function):
            @functools.wraps(function)
            def decorated(*args, **kwargs):
                return function(*args, **kwargs)
            return decorated
        class Resource:
            @sdk_decorator
            async def create(self, **kwargs):
                return response
        resource = Resource()
        for api in ("chat", "responses"):
            async with connect_openai(client_with(resource.create), api=api) as agent:
                self.assertIs(await agent(messages=[text("user", "Hi")]), response)

    async def test_async_chat_and_responses_keep_response_identity(self):
        calls = []
        response = object()
        async def create(**kwargs):
            calls.append(kwargs)
            return response
        for api in ("chat", "responses"):
            async with connect_openai(client_with(create), api=api) as agent:
                result = await agent(messages=[text("user", "Hi")], model="chosen")
            self.assertIs(result, response)
        self.assertIn("messages", calls[0])
        self.assertIn("input", calls[1])

    async def test_async_stream_is_not_consumed(self):
        class Stream:
            def __aiter__(self):
                raise AssertionError("Stream consumed")
            async def aclose(self):
                raise AssertionError("Stream closed")
        stream = Stream()
        async def create(**kwargs):
            return stream
        async with connect_openai(client_with(create)) as agent:
            self.assertIs(await agent(messages=[text("user", "Hi")], stream=True), stream)


if __name__ == "__main__":
    unittest.main()
