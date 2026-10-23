"""Anthropic contract stand-ins only: no SDK, credentials, inference, or network."""

import copy
import functools
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import ContextGuard, MemoryPolicy, RelayConfig, TokenBudget
from context_relay.anthropic import _decode_anthropic_text, _request_input, connect_anthropic
from context_relay.budget import TokenBudgetExceeded
from context_relay.conversation import ConversationResponseError
from context_relay.models import BudgetExceeded, encode


def message(role, content):
    return {"role": role, "content": content}


def response(text="Completed text."):
    return {"id": "offline-message", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}


def client_with(call):
    return SimpleNamespace(messages=SimpleNamespace(create=call))


class AnthropicConnectionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.response = response()
        def create(**kwargs):
            self.calls.append(kwargs)
            return self.response
        self.client = client_with(create)

    def test_system_blocks_preserve_order_boundaries_and_original_text(self):
        history = [message("system", "First instruction.\n"), message("system", " 雪 and spaces "),
                   message("user", "Hello")]
        original = copy.deepcopy(history)
        with connect_anthropic(self.client) as agent:
            result = agent.invoke(messages=history, model="host-model", max_tokens=128)
        self.assertIs(result.response, self.response)
        self.assertEqual(self.calls[0]["system"], [
            {"type": "text", "text": "First instruction.\n"},
            {"type": "text", "text": " 雪 and spaces "}])
        self.assertEqual(self.calls[0]["messages"], result.prepared.messages[2:])
        self.assertEqual(history, original)
        self.assertEqual(self.calls[0]["model"], "host-model")
        self.assertEqual(self.calls[0]["max_tokens"], 128)

    def test_no_system_option_is_invented_and_noncontext_options_pass_through(self):
        metadata = {"user_id": "offline-user"}
        with connect_anthropic(self.client) as agent:
            result = agent(messages=[message("user", "Hi")], model="host-model", max_tokens=128,
                           metadata=metadata, timeout=5, stop_sequences=["STOP"],
                           thinking={"type": "disabled"})
        self.assertIs(result, self.response)
        self.assertNotIn("system", self.calls[0])
        self.assertIs(self.calls[0]["metadata"], metadata)
        self.assertEqual(self.calls[0]["timeout"], 5)
        self.assertEqual(self.calls[0]["stop_sequences"], ["STOP"])

    def test_top_level_system_and_unknown_context_options_never_reach_provider(self):
        with connect_anthropic(self.client) as agent:
            for options in [{"system": "Unbudgeted"}, {"system": None}, {"tools": []},
                            {"tool_choice": {"type": "auto"}}, {"container": "old-session"},
                            {"context_management": {}}, {"mcp_servers": []},
                            {"output_config": {"format": {"type": "json_schema"}}},
                            {"input": "override"}, {"extra_body": {"messages": []}},
                            {"extra_query": {"system": "override"}}, {"betas": ["unseen-context"]},
                            {"thinking": {"type": "enabled", "budget_tokens": 1000}},
                            {"thinking": {"type": "disabled", "other": "hidden"}}]:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    agent(messages=[message("user", "Hello")], **options)
        self.assertEqual(self.calls, [])

    def test_native_blocks_and_unknown_message_fields_are_not_flattened(self):
        for history in [
            [message("user", [{"type": "text", "text": "Hello"}])],
            [message("user", [{"type": "image", "source": {}}])],
            [message("user", "Start"), message("assistant", [{"type": "thinking", "thinking": "Private"}]),
             message("user", "Next")],
            [{"role": "user", "content": "Hello", "cache_control": {"type": "ephemeral"}}],
            [message("user", "Start"), message("tool", "Native result")],
            [SimpleNamespace(role="user", content="SDK object")],
        ]:
            with self.subTest(history=history), connect_anthropic(self.client) as agent:
                with self.assertRaises((TypeError, ValueError)):
                    agent(messages=history)
        self.assertEqual(self.calls, [])

    def test_developer_late_system_prefill_and_empty_system_fail_explicitly(self):
        histories = [
            [message("developer", "Different authority"), message("user", "Hi")],
            [message("user", "Hi"), message("system", "Later"), message("user", "Next")],
            [message("user", "Hi"), message("assistant", "Partial")],
            [message("system", "  "), message("user", "Hi")],
        ]
        with connect_anthropic(self.client) as agent:
            for history in histories:
                with self.subTest(history=history), self.assertRaises(ValueError):
                    agent(messages=history)
        self.assertEqual(self.calls, [])

    def test_system_text_is_mandatory_and_cannot_bypass_character_budget(self):
        with connect_anthropic(self.client, config=RelayConfig(max_chars=1000)) as agent:
            with self.assertRaises(BudgetExceeded):
                agent(messages=[message("system", "x" * 1200), message("user", "Hi")])
        self.assertEqual(self.calls, [])

    def test_token_counter_can_measure_mapped_system_and_messages_together(self):
        history = [message("system", "x" * 200), message("user", "Hi")]
        with connect_anthropic(self.client) as agent:
            prepared = agent.invoke(messages=history).prepared
        size = len(encode(_request_input(prepared.messages, {})))
        def count(messages):
            return len(encode(_request_input(messages, {})))
        self.calls.clear()
        with connect_anthropic(self.client, token_budget=TokenBudget(size, count)) as agent:
            agent(messages=history)
        self.assertEqual(len(self.calls), 1)
        with connect_anthropic(self.client, token_budget=TokenBudget(size - 1, count)) as agent:
            with self.assertRaises(TokenBudgetExceeded):
                agent(messages=history)
        self.assertEqual(len(self.calls), 1)

    def test_optional_old_turn_is_removed_without_changing_system_or_latest_text(self):
        history = [message("system", "Required instructions"), message("user", "x" * 4000),
                   message("assistant", "Old reply"), message("user", "Current question")]
        with connect_anthropic(self.client, config=RelayConfig(max_chars=1600, recent_turns=1)) as agent:
            result = agent.invoke(messages=history)
        self.assertEqual(result.prepared.report["omitted_indices"], [1, 2])
        self.assertEqual(self.calls[0]["system"], [{"type": "text", "text": "Required instructions"}])
        self.assertEqual(self.calls[0]["messages"][-1], history[-1])

    def test_sync_stream_passthrough_is_never_consumed_or_closed(self):
        class Stream:
            def __iter__(self):
                raise AssertionError("Stream was consumed")
            def close(self):
                raise AssertionError("Stream was closed")
        self.response = Stream()
        with connect_anthropic(self.client) as agent:
            result = agent(messages=[message("user", "Hi")], stream=True)
        self.assertIs(result, self.response)

    def test_provider_failure_is_original_exception_with_no_automatic_retry(self):
        failure = RuntimeError("Provider unavailable")
        def create(**kwargs):
            self.calls.append(kwargs)
            raise failure
        with connect_anthropic(client_with(create)) as agent:
            with self.assertRaises(RuntimeError) as caught:
                agent(messages=[message("user", "Hi")])
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(self.calls), 1)

    def test_config_file_and_external_guard_use_existing_connection_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "relay.json"
            path.write_text(json.dumps({"db_path": "db/relay.sqlite3", "max_chars": 5000}), encoding="utf-8")
            with connect_anthropic(self.client, config=path) as agent:
                self.assertEqual(agent.config.protocol, "plain")
                self.assertEqual(agent.config.max_chars, 5000)
            self.assertTrue((Path(temp) / "db/relay.sqlite3").exists())
        with ContextGuard() as guard:
            with connect_anthropic(self.client, guard=guard) as agent:
                self.assertIs(agent.guard, guard)
            guard.remember("still", "open")
        self.assertEqual(self.calls, [])

    def test_missing_or_already_connected_client_and_wrong_protocol_fail_at_setup(self):
        for client in [object(), client_with(None)]:
            with self.subTest(client=client), self.assertRaises(TypeError):
                connect_anthropic(client)
        for config in [RelayConfig(protocol="ollama"), RelayConfig(protocol="openai_chat"), {}]:
            with self.subTest(config=config), self.assertRaises((TypeError, ValueError)):
                connect_anthropic(self.client, config=config)
        with connect_anthropic(self.client) as agent:
            with self.assertRaises(ValueError):
                connect_anthropic(client_with(agent))
        self.assertEqual(self.calls, [])

    def test_managed_history_and_memory_resume_with_provider_decoder(self):
        with tempfile.TemporaryDirectory() as temp:
            config = RelayConfig(db_path=str(Path(temp) / "relay.sqlite3"))
            with connect_anthropic(self.client, config=config, memory=MemoryPolicy()) as agent:
                session_id = agent.session_id
                chat = agent.conversation(system="Help with the project.")
                self.assertIs(chat.send("Export as CSV.", request_id="first"), self.response)
            with connect_anthropic(self.client, config=config, session_id=session_id, memory=MemoryPolicy()) as agent:
                chat = agent.conversation()
                chat.send("Export as JSON.", request_id="second")
                self.assertEqual(len(chat.history), 5)
                self.assertEqual(chat.history[2], message("assistant", "Completed text."))
                self.assertIn('"value":"JSON"', encode(agent.memory.snapshot()))
            self.assertEqual(len(self.calls), 2)
            self.assertEqual(self.calls[-1]["system"], [{"type": "text", "text": "Help with the project."}])

    def test_managed_unsupported_reply_keeps_original_response_and_pending_request(self):
        self.response["content"].append({"type": "thinking", "thinking": "Native reasoning", "signature": "x"})
        with connect_anthropic(self.client) as agent:
            chat = agent.conversation()
            with self.assertRaises(ConversationResponseError) as caught:
                chat.send("Hello", request_id="one")
            self.assertIs(caught.exception.response, self.response)
            self.assertIsNotNone(chat.pending)
            self.assertEqual(chat.history, [message("user", "Hello")])
        self.assertEqual(len(self.calls), 1)


class AnthropicDecoderTests(unittest.TestCase):
    def test_completed_dict_and_sdk_shaped_text_preserve_characters(self):
        result = response("First ")
        result["content"].append({"type": "text", "text": "雪\n", "citations": []})
        self.assertEqual(_decode_anthropic_text(result), "First 雪\n")
        result["content"] = [SimpleNamespace(type="text", text="SDK shaped", citations=None)]
        self.assertEqual(_decode_anthropic_text(SimpleNamespace(**result)), "SDK shaped")

    def test_incomplete_refused_paused_and_tool_stop_reasons_are_rejected(self):
        for reason in [None, "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal",
                       "model_context_window_exceeded", "future_unknown"]:
            result = response()
            result["stop_reason"] = reason
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                _decode_anthropic_text(result)

    def test_native_content_citations_and_unknown_block_fields_are_rejected(self):
        blocks = [{"type": "thinking", "thinking": "Private", "signature": "x"},
                  {"type": "tool_use", "id": "tool-1", "name": "lookup", "input": {}},
                  {"type": "redacted_thinking", "data": "opaque"},
                  {"type": "text", "text": "Quoted", "citations": [{"type": "page_location"}]},
                  {"type": "text", "text": "Annotated", "future_metadata": {"important": True}},
                  {"type": "text", "text": 7}]
        for block in blocks:
            result = response()
            result["content"].append(block)
            with self.subTest(block=block), self.assertRaises(ValueError):
                _decode_anthropic_text(result)

    def test_malformed_empty_or_wrong_role_responses_are_rejected(self):
        for changes in [{"type": "event"}, {"role": "user"}, {"content": []},
                        {"content": "text"}, {"content": [{"type": "text", "text": "  "}]},
                        {"stop_sequence": "STOP"}, {"stop_details": {"type": "refusal"}}]:
            result = response()
            result.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _decode_anthropic_text(result)


class AsyncAnthropicConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_decorated_async_client_and_managed_text_keep_response_identity(self):
        calls, reply = [], response()
        def sdk_decorator(function):
            @functools.wraps(function)
            def decorated(*args, **kwargs):
                return function(*args, **kwargs)
            return decorated
        class Resource:
            @sdk_decorator
            async def create(self, **kwargs):
                calls.append(kwargs)
                return reply
        async with connect_anthropic(client_with(Resource().create)) as agent:
            chat = agent.conversation(system="Instructions")
            self.assertIs(await chat.send("Hello"), reply)
            self.assertEqual(chat.history[-1]["content"], "Completed text.")
        self.assertEqual(len(calls), 1)

    async def test_async_stream_passes_through_without_iteration_or_closure(self):
        class Stream:
            def __aiter__(self):
                raise AssertionError("Stream consumed")
            async def aclose(self):
                raise AssertionError("Stream closed")
        stream = Stream()
        async def create(**kwargs):
            return stream
        async with connect_anthropic(client_with(create)) as agent:
            self.assertIs(await agent(messages=[message("user", "Hi")], stream=True), stream)

    async def test_direct_async_generator_provider_is_rejected_before_execution(self):
        async def create(**kwargs):
            raise AssertionError("Generator started")
            yield response()
        with self.assertRaises(TypeError):
            connect_anthropic(client_with(create))


if __name__ == "__main__":
    unittest.main()
