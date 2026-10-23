"""Offline framework-shaped stand-ins; not proof against an installed SDK."""

import asyncio
import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import ContextGuard, MemoryPolicy, OutputPolicy, RelayConfig, TokenBudget
from context_relay.langchain import LangChainResponseError, connect_langchain


class Message:
    kind = "base"

    def __init__(self, content, **kwargs):
        self.content = content
        self.type = self.kind
        self.id = None
        self.name = None
        self.additional_kwargs = {}
        self.response_metadata = {}
        self.__dict__.update(kwargs)

    def model_dump(self):
        return copy.deepcopy(vars(self))


class HumanMessage(Message):
    kind = "human"


class AIMessage(Message):
    kind = "ai"

    def __init__(self, content, **kwargs):
        super().__init__(content, tool_calls=[], invalid_tool_calls=[], usage_metadata=None)
        self.__dict__.update(kwargs)


class SystemMessage(Message):
    kind = "system"


class ChatMessage(Message):
    kind = "chat"


class ToolMessage(Message):
    kind = "tool"

    def __init__(self, content, **kwargs):
        super().__init__(content, artifact=None, status="success")
        self.__dict__.update(kwargs)


class AIMessageChunk(AIMessage):
    kind = "AIMessageChunk"


TYPES = types.SimpleNamespace(HumanMessage=HumanMessage, AIMessage=AIMessage,
                              SystemMessage=SystemMessage, ToolMessage=ToolMessage, ChatMessage=ChatMessage)


class Model:
    def __init__(self):
        self.calls = []
        self.response = AIMessage("[STAND-IN; NO INFERENCE] Done", id="reply-id")
        self.closed = False

    def invoke(self, input, config=None, **kwargs):
        self.calls.append((input, config, kwargs))
        return self.response

    async def ainvoke(self, input, config=None, **kwargs):
        await asyncio.sleep(0)
        return self.invoke(input, config=config, **kwargs)

    def close(self):
        self.closed = True


def history(text="Set export format to JSON."):
    return [SystemMessage("Help the user."), HumanMessage(text)]


def task_data(messages):
    for message in messages:
        if message.content.startswith("APPLICATION-RECORDED TASK DATA\n"):
            return json.loads(message.content[message.content.index("{"):])["state"]
    raise AssertionError("Missing prepared state")


def tool_history():
    return [SystemMessage("Use tools."), HumanMessage("Look up both records.", id="user-id"),
            AIMessage("", id="assistant-id", tool_calls=[
                {"name": "lookup", "args": {"number": 1}, "id": "call-1", "type": "tool_call"},
                {"name": "lookup", "args": {"number": 2}, "id": "call-2", "type": "tool_call"},
            ], usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}),
            ToolMessage("Second failed", tool_call_id="call-2", name="lookup", status="error",
                        artifact={"record": 2}, id="tool-second"),
            ToolMessage("First found", tool_call_id="call-1", name="lookup", id="tool-first")]


class LangChainIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.types_patch = patch("context_relay.langchain._load_messages", return_value=TYPES)
        self.types_patch.start()
        self.addCleanup(self.types_patch.stop)

    def bridge(self, model=None, **kwargs):
        bridge = connect_langchain(model or Model(), **kwargs)
        self.addCleanup(bridge.close)
        return bridge

    def test_prepare_preserves_roles_names_ids_and_host_history(self):
        messages = [SystemMessage("System", id="s"), ChatMessage("Developer", role="developer"),
                    HumanMessage("Hello", id="h", name="Alice")]
        expected = [m.model_dump() for m in messages]
        prepared = self.bridge().prepare(messages)
        self.assertEqual([type(m) for m in prepared.messages],
                         [SystemMessage, ChatMessage, HumanMessage, HumanMessage])
        self.assertEqual(prepared.messages[1].role, "developer")
        self.assertEqual(prepared.messages[-1].name, "Alice")
        self.assertEqual(prepared.messages[-1].id, "h")
        self.assertEqual([m.model_dump() for m in messages], expected)
        altered = prepared.messages
        altered[-1].content = "mutated"
        self.assertEqual(prepared.messages[-1].content, "Hello")

    def test_original_response_config_callbacks_and_options_keep_identity(self):
        model = Model()
        provider_method = model.invoke.__func__
        bridge = self.bridge(model)
        config = {"tags": ["test"], "callbacks": object()}
        stop = ["stop"]
        result = bridge.invoke_result(history(), config=config, stop=stop)
        self.assertIs(result.response, model.response)
        self.assertIs(model.calls[0][1], config)
        self.assertIs(model.calls[0][2]["stop"], stop)
        self.assertIs(model.invoke.__func__, provider_method)
        self.assertEqual(result.prepared.history_id, result.prepared.report["history_id"])

    def test_tool_calls_results_error_status_artifact_and_usage_are_preserved(self):
        model = Model()
        bridge = self.bridge(model)
        messages = tool_history()
        bridge.invoke(messages)
        sent = model.calls[0][0]
        self.assertEqual([type(m) for m in sent], [SystemMessage, HumanMessage, HumanMessage,
                                                 AIMessage, ToolMessage, ToolMessage])
        self.assertEqual(sent[3].tool_calls, messages[2].tool_calls)
        self.assertEqual(sent[3].usage_metadata, messages[2].usage_metadata)
        self.assertEqual(sent[4].tool_call_id, "call-2")
        self.assertEqual(sent[4].status, "error")
        self.assertEqual(sent[4].artifact, {"record": 2})
        self.assertEqual(sent[5].tool_call_id, "call-1")

    def test_matching_raw_duplicate_function_calls_are_retained(self):
        messages = tool_history()
        raw = [{"id": call["id"], "type": "function", "function": {
            "name": call["name"], "arguments": json.dumps(call["args"])}} for call in messages[2].tool_calls]
        messages[2].additional_kwargs = {"tool_calls": raw, "refusal": None}
        prepared = self.bridge().prepare(messages)
        self.assertEqual(prepared.messages[3].additional_kwargs, messages[2].additional_kwargs)

    def test_disagreeing_raw_calls_are_rejected_before_memory_and_provider(self):
        model = Model()
        bridge = self.bridge(model, memory=MemoryPolicy())
        messages = tool_history()
        messages[2].additional_kwargs = {"tool_calls": []}
        with self.assertRaisesRegex(ValueError, "agree"):
            bridge.invoke(messages)
        self.assertEqual(model.calls, [])
        self.assertEqual(bridge.guard.store.events(bridge.session_id), [])

    def test_raw_argument_types_and_duplicate_keys_cannot_hide_disagreement(self):
        for value in ('{"number": true}', '{"number": 9, "number": 1}', '{"number": 1.0}'):
            with self.subTest(value=value):
                messages = tool_history()
                raw = [{"id": call["id"], "type": "function", "function": {
                    "name": call["name"], "arguments": json.dumps(call["args"])}}
                    for call in messages[2].tool_calls]
                raw[0]["function"]["arguments"] = value
                messages[2].additional_kwargs = {"tool_calls": raw}
                with self.assertRaisesRegex(ValueError, "disagree"):
                    self.bridge().prepare(messages)

    def test_multimodal_reasoning_extra_fields_and_partial_calls_are_rejected(self):
        bridge = self.bridge()
        variants = [HumanMessage([{"type": "image", "url": "somewhere"}]),
                    AIMessage([{"type": "reasoning", "reasoning": "private"}]),
                    HumanMessage("Text", extra_payload="unseen"),
                    AIMessage("Text", additional_kwargs={"reasoning_content": "private"}),
                    AIMessage("", invalid_tool_calls=[{"name": "bad"}]),
                    AIMessageChunk("partial"),
                    ChatMessage("Injected", role="function")]
        for message in variants:
            with self.subTest(message=message.model_dump()), self.assertRaises((TypeError, ValueError)):
                bridge.prepare([HumanMessage("Start"), message, HumanMessage("Continue")])

    def test_incomplete_or_mismatched_tool_batch_rejected(self):
        bridge = self.bridge()
        messages = tool_history()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            bridge.prepare(messages[:-1])
        messages[-1].tool_call_id = "unknown"
        with self.assertRaisesRegex(ValueError, "match"):
            bridge.prepare(messages)

    def test_tool_name_cannot_misrepresent_associated_call(self):
        messages = tool_history()
        messages[-1].name = "delete"
        with self.assertRaisesRegex(ValueError, "name must match"):
            self.bridge().prepare(messages)

    def test_old_framework_message_metadata_uses_selected_original_indices(self):
        messages = [SystemMessage("System"), HumanMessage("Same", id="old-user"),
                    AIMessage("Large old response " * 100, id="old-ai"),
                    HumanMessage("Same", id="current-user")]
        prepared = self.bridge(config=RelayConfig(max_chars=1500, recent_turns=1, retrieve_turns=0)).prepare(messages)
        self.assertNotIn(1, prepared.report["included_indices"])
        self.assertEqual(prepared.messages[-1].id, "current-user")
        self.assertEqual(prepared.report["omitted_indices"], [1, 2])

    def test_memory_only_reads_original_user_roles_and_stays_out_of_graph_history(self):
        bridge = self.bridge(memory=MemoryPolicy())
        messages = [HumanMessage("Set export format to JSON."),
                    AIMessage("Set export format to XML."), HumanMessage("Continue.")]
        prepared = bridge.prepare(messages)
        memory = task_data(prepared.messages)["user_memory"]
        self.assertEqual(memory["entries"][0]["value"], "JSON")
        self.assertEqual(memory["entries"][0]["source"]["role"], "user")
        self.assertEqual(len(messages), 3)
        self.assertFalse(any(m.content.startswith("APPLICATION-") for m in messages))

    def test_separate_bridges_do_not_share_sessions_or_memory(self):
        first, second = self.bridge(memory=MemoryPolicy()), self.bridge(memory=MemoryPolicy())
        first.invoke(history())
        prepared = second.prepare(history("Continue."))
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(task_data(prepared.messages)["user_memory"]["entries"], [])

    def test_graph_node_requires_explicit_thread_binding_and_returns_delta_only(self):
        model = Model()
        bridge = self.bridge(model, thread_id="customer-1", memory=MemoryPolicy())
        config = {"configurable": {"thread_id": "customer-1"}}
        original = {"messages": history(), "business_state": {"value": 3}}
        result = bridge.node(original, config=config)
        self.assertEqual(set(result), {"messages"})
        self.assertIs(result["messages"][0], model.response)
        self.assertEqual(len(original["messages"]), 2)
        with self.assertRaisesRegex(ValueError, "conversation"):
            bridge.node(original, config={"configurable": {"thread_id": "customer-2"}})
        self.assertEqual(len(model.calls), 1)
        with self.assertRaisesRegex(ValueError, "thread_id"):
            self.bridge().node(original)

    def test_graph_rejects_chunk_response_without_consuming_or_retrying(self):
        model = Model()
        model.response = AIMessageChunk("partial")
        bridge = self.bridge(model, thread_id="one")
        with self.assertRaises(LangChainResponseError) as caught:
            bridge.node({"messages": history()}, {"configurable": {"thread_id": "one"}})
        self.assertIs(caught.exception.response, model.response)
        self.assertEqual(len(model.calls), 1)

    def test_ordinary_invoke_does_not_consume_response_iterator(self):
        class Stream:
            def __iter__(self):
                raise AssertionError("Stream consumed")

        model = Model()
        model.response = Stream()
        bridge = self.bridge(model)
        self.assertIs(bridge.invoke(history()), model.response)

    def test_external_guard_and_model_remain_open(self):
        model = Model()
        with ContextGuard() as guard:
            with connect_langchain(model, guard=guard) as bridge:
                bridge.invoke(history())
            guard.remember("still_open", True)
        self.assertFalse(model.closed)

    def test_framework_missing_and_unsupported_output_reject_before_database_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "new" / "relay.db"
            with patch("context_relay.langchain._load_messages", side_effect=ImportError("SDK missing")):
                with self.assertRaises(ImportError):
                    connect_langchain(Model(), config=RelayConfig(db_path=str(database)))
            self.assertFalse(database.parent.exists())
            with self.assertRaisesRegex(ValueError, "OutputPolicy"):
                connect_langchain(Model(), config=RelayConfig(db_path=str(database), output=OutputPolicy()))
            self.assertFalse(database.parent.exists())

    def test_token_counter_receives_canonical_tools_and_inserted_state(self):
        seen = []

        def count(messages):
            seen.append(messages)
            return len(json.dumps(messages))

        bridge = self.bridge(token_budget=TokenBudget(15000, count))
        prepared = bridge.prepare(tool_history())
        self.assertTrue(any(any(m.get("tool_calls") for m in call) for call in seen))
        self.assertTrue(any(any(m.get("content", "").startswith("APPLICATION-") for m in call) for call in seen))
        self.assertEqual(prepared.report["budget_representation"], "canonical_text_and_function_messages")

    def test_as_runnable_uses_native_factory_without_patching_model(self):
        calls = []

        def factory(func, afunc=None, name=None):
            calls.append((func, afunc, name))
            return object()

        bridge = self.bridge()
        with patch("context_relay.langchain._load_runnable", return_value=factory):
            result = bridge.as_runnable()
        self.assertIsNotNone(result)
        self.assertEqual(calls[0][2], "ContextRelay")
        self.assertIs(calls[0][0].__self__, bridge)


class AsyncLangChainIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.types_patch = patch("context_relay.langchain._load_messages", return_value=TYPES)
        self.types_patch.start()
        self.addCleanup(self.types_patch.stop)

    async def test_async_response_identity_metadata_and_prepared_turn(self):
        model = Model()
        async with connect_langchain(model, memory=MemoryPolicy()) as bridge:
            result = await bridge.ainvoke_result(history())
            self.assertIs(result.response, model.response)
            self.assertEqual(result.prepared.revision, bridge.guard.turn().revision)
            self.assertEqual(task_data(model.calls[0][0])["user_memory"]["entries"][0]["value"], "JSON")

    async def test_async_graph_node_preserves_delta_and_thread_isolation(self):
        model = Model()
        async with connect_langchain(model, thread_id="one") as bridge:
            response = await bridge.anode({"messages": tool_history()}, {"configurable": {"thread_id": "one"}})
            self.assertIs(response["messages"][0], model.response)
            with self.assertRaises(ValueError):
                await bridge.anode({"messages": history()}, {"configurable": {"thread_id": "other"}})
            self.assertEqual(len(model.calls), 1)

    async def test_async_cancellation_propagates_without_provider_retry(self):
        class CancelledModel(Model):
            async def ainvoke(self, input, config=None, **kwargs):
                self.calls.append(input)
                raise asyncio.CancelledError()

        model = CancelledModel()
        async with connect_langchain(model) as bridge:
            with self.assertRaises(asyncio.CancelledError):
                await bridge.ainvoke(history())
            self.assertEqual(len(model.calls), 1)

    async def test_overlapping_requests_keep_each_prepared_turn(self):
        class InterleavedModel(Model):
            async def ainvoke(self, input, config=None, **kwargs):
                await asyncio.sleep(0)
                return AIMessage(input[-1].content)

        async with connect_langchain(InterleavedModel()) as bridge:
            first, second = await asyncio.gather(
                bridge.ainvoke_result(history("First question")),
                bridge.ainvoke_result(history("Second question")),
            )
            self.assertEqual(first.response.content, "First question")
            self.assertEqual(first.prepared.messages[-1]["content"], "First question")
            self.assertEqual(second.response.content, "Second question")
            self.assertEqual(second.prepared.messages[-1]["content"], "Second question")
            self.assertNotEqual(first.prepared.history_id, second.prepared.history_id)


if __name__ == "__main__":
    unittest.main()
