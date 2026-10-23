"""Real optional SDKs/frameworks with simulated local transports, no inference.

These tests verify SDK serialization, parsing and integration. Fake encrypted
content/signatures are fixtures, not proof that a provider accepts or validates
them. Every SDK HTTP request is intercepted by MockTransport; no key is real.
"""

import asyncio
import importlib
import importlib.util
import json
import os
import unittest
from unittest.mock import patch

from context_relay import RelayConfig, auto_connect
from context_relay.anthropic import connect_anthropic
from context_relay.attachment import attach
from context_relay.langchain import connect_langchain
from context_relay.native import native_json
from context_relay.openai import connect_openai


def available(name):
    return importlib.util.find_spec(name) is not None


def sdk_http(name):
    # SDK v3 OpenAI/v2 Anthropic use httpx2; earlier SDKs use httpx.
    module = importlib.import_module(name + "._client")
    return getattr(module, "httpx2", None) or getattr(module, "httpx")


def response_body(output):
    return {"id": "resp_mock", "object": "response", "created_at": 0.0,
            "model": "host-configured-model", "status": "completed", "output": output,
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "error": None, "incomplete_details": None,
            "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13,
                      "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 1}}}


def reasoning_call():
    return [{"type": "reasoning", "id": "rs_mock", "summary": [],
             "encrypted_content": "MOCK-opaque+/==", "status": "completed"},
            {"type": "function_call", "id": "fc_mock", "call_id": "call_mock",
             "name": "lookup", "arguments": '{ "id": 7 }', "status": "completed"}]


def response_text():
    return [{"type": "message", "id": "msg_mock", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": "Done", "annotations": [], "logprobs": []}]}]


def anthropic_body(content, *, reason="end_turn"):
    return {"id": "msg_mock", "type": "message", "role": "assistant", "model": "host-model",
            "content": content, "stop_reason": reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 3}}


def anthropic_thinking_call():
    return [{"type": "thinking", "thinking": "MOCK reasoning", "signature": "MOCK-signature+/=="},
            {"type": "tool_use", "id": "tool_mock", "name": "lookup", "input": {"id": 7}}]


@unittest.skipUnless(available("openai"), "Optional openai SDK is not installed")
class RealOpenAISyncTests(unittest.TestCase):
    def make_client(self, handler):
        import openai
        http = sdk_http("openai")
        http_client = http.Client(transport=http.MockTransport(handler))
        sdk = openai.OpenAI(api_key="test-not-a-real-key", base_url="https://relay.invalid/v1",
                            http_client=http_client, max_retries=0, timeout=5, _strict_response_validation=True)
        self.addCleanup(sdk.close)
        return sdk, http

    def test_real_custom_tool_and_memory_projection_wire_body(self):
        from openai.types.responses import ResponseCustomToolCall
        http = sdk_http("openai")
        captured = []
        def handle(request):
            captured.append(json.loads(request.content))
            return http.Response(200, json=response_body(response_text()))
        sdk, _ = self.make_client(handle)
        call = ResponseCustomToolCall(type="custom_tool_call", call_id="custom_mock",
                                      name="grammar", input="raw expression", caller={"type": "direct"})
        items = [{"role": "user", "content": "Evaluate"}, call,
                 {"type": "custom_tool_call_output", "call_id": "custom_mock", "output": "ready"}]
        with auto_connect(sdk, api="responses", native=True, native_memory=True) as relay:
            relay.remember("export_format", "JSON")
            result = relay.invoke_result(model="host-model", input=items, instructions="Stable policy",
                                         tools=[{"type": "custom", "name": "grammar"}])
            self.assertEqual(captured[0], result.prepared.request)
            self.assertEqual(captured[0]["input"], native_json(items, sdk="openai"))
            self.assertIn('"export_format":"JSON"', captured[0]["instructions"])
            self.assertEqual(result.response.output_text, "Done")

    def test_actual_reasoning_function_roundtrip_and_whole_wire_body(self):
        http = sdk_http("openai")
        captured = []
        def handle(request):
            captured.append(json.loads(request.content))
            output = reasoning_call() if len(captured) == 1 else response_text()
            return http.Response(200, json=response_body(output))
        sdk, _ = self.make_client(handle)
        with auto_connect(sdk, api="responses", native=True) as relay:
            history = [{"role": "user", "content": "Lookup item seven"}]
            first = relay.invoke_result(model="host-configured-model", input=history, store=False)
            self.assertEqual(captured[0], first.prepared.request)
            self.assertEqual(type(first.response.output[0]).__module__.split(".")[0], "openai")
            history.extend(first.response.output)
            history.append({"type": "function_call_output", "call_id": "call_mock", "output": "ready"})
            second = relay.invoke_result(model="host-configured-model", input=history, store=False,
                                          context_management=[{"type": "compaction", "compact_threshold": 1000}])
            self.assertEqual(captured[1], second.prepared.request)
            self.assertEqual(captured[1]["input"], native_json(history, sdk="openai"))
            self.assertEqual(captured[1]["input"][1]["encrypted_content"], "MOCK-opaque+/==")
            self.assertEqual(captured[1]["input"][2]["arguments"], '{ "id": 7 }')
            self.assertEqual(second.response.output_text, "Done")
        self.assertFalse(sdk.is_closed())

    def test_actual_compact_output_objects_replayed_without_pruning(self):
        http = sdk_http("openai")
        captured = []
        canonical = [{"type": "compaction", "id": "cmp_mock", "encrypted_content": "MOCK-COMPACTION=="}]
        def handle(request):
            captured.append((request.url.path, json.loads(request.content)))
            if request.url.path.endswith("/compact"):
                return http.Response(200, json={"id": "compact_mock", "object": "response.compaction",
                    "created_at": 0, "output": canonical, "usage": response_body([])["usage"]})
            return http.Response(200, json=response_body(response_text()))
        sdk, _ = self.make_client(handle)
        compacted = sdk.responses.compact(model="host-configured-model", input=[{"role": "user", "content": "Earlier instruction"}])
        with connect_openai(sdk, api="responses", native=True) as relay:
            result = relay.invoke_result(model="host-configured-model",
                input=[*compacted.output, {"role": "user", "content": "Continue"}])
            self.assertEqual(captured[-1][1], result.prepared.request)
            self.assertEqual(captured[-1][1]["input"][:-1], native_json(compacted.output, sdk="openai"))
            self.assertEqual(captured[-1][1]["input"][0]["encrypted_content"], "MOCK-COMPACTION==")

    def test_retained_user_compact_window_from_actual_sdk_raw_response(self):
        # OpenAI 3.36.0's strict CompactedResponse.output schema describes only
        # assistant output messages, while the documented compact window can
        # retain user messages. The exact raw JSON SDK path preserves that
        # canonical window without coercing roles to satisfy a local SDK type.
        http = sdk_http("openai")
        captured = []
        canonical = [{"type": "message", "id": "user_before", "role": "user", "status": "completed",
                      "content": [{"type": "input_text", "text": "Earlier instruction"}]},
                     {"type": "compaction", "id": "cmp_mock", "encrypted_content": "MOCK-COMPACTION=="}]
        def handle(request):
            captured.append(json.loads(request.content))
            if request.url.path.endswith("/compact"):
                return http.Response(200, json={"id": "compact_mock", "object": "response.compaction",
                    "created_at": 0, "output": canonical, "usage": response_body([])["usage"]})
            return http.Response(200, json=response_body(response_text()))
        sdk, _ = self.make_client(handle)
        raw = sdk.responses.with_raw_response.compact(model="host-model", input=[{"role": "user", "content": "Earlier instruction"}])
        compacted = raw.http_response.json()
        with connect_openai(sdk, api="responses", native=True) as relay:
            result = relay.invoke_result(model="host-model", input=[*compacted["output"], {"role": "user", "content": "Continue"}])
            self.assertEqual(captured[-1], result.prepared.request)
            self.assertEqual(captured[-1]["input"][:-1], canonical)

    def test_actual_stream_is_unconsumed_until_application_iterates(self):
        http = sdk_http("openai")
        reads = []
        class Body(http.SyncByteStream):
            def __iter__(self):
                reads.append("read")
                event = {"type": "response.output_text.delta", "sequence_number": 1,
                         "item_id": "msg_mock", "output_index": 0, "content_index": 0, "delta": "Hi", "logprobs": []}
                yield ("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode()
        sdk, _ = self.make_client(lambda request: http.Response(200, headers={"content-type": "text/event-stream"}, stream=Body()))
        with connect_openai(sdk, api="responses", native=True) as relay:
            stream = relay(model="host-model", input=[{"role": "user", "content": "Hi"}], stream=True)
            self.assertEqual(reads, [])
            self.assertEqual(next(iter(stream)).delta, "Hi")
            self.assertEqual(reads, ["read"])
            stream.close()

    def test_actual_sdk_http_error_propagates_once(self):
        import openai
        http = sdk_http("openai")
        calls = []
        def handle(request):
            calls.append(request)
            return http.Response(400, json={"error": {"message": "fixture rejection", "type": "invalid_request_error", "code": "fixture"}})
        sdk, _ = self.make_client(handle)
        with connect_openai(sdk, api="responses", native=True) as relay:
            with self.assertRaises(openai.BadRequestError):
                relay(model="host-model", input=[{"role": "user", "content": "Hi"}])
        self.assertEqual(len(calls), 1)


@unittest.skipUnless(available("openai"), "Optional openai SDK is not installed")
class RealOpenAIAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_sdk_serialization_and_unconsumed_stream(self):
        import openai
        http = sdk_http("openai")
        calls, reads = [], []
        class Body(http.AsyncByteStream):
            async def __aiter__(self):
                reads.append("read")
                event = {"type": "response.output_text.delta", "sequence_number": 1,
                         "item_id": "msg_mock", "output_index": 0, "content_index": 0, "delta": "Hi", "logprobs": []}
                yield ("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode()
        async def handle(request):
            calls.append(json.loads(request.content))
            if calls[-1].get("stream"):
                return http.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())
            return http.Response(200, json=response_body(response_text()))
        async with openai.AsyncOpenAI(api_key="test-not-real", base_url="https://relay.invalid/v1",
             http_client=http.AsyncClient(transport=http.MockTransport(handle)), max_retries=0, timeout=5,
             _strict_response_validation=True) as sdk:
            async with connect_openai(sdk, api="responses", native=True) as relay:
                result = await relay.invoke_result(model="host-model", input=[{"role": "user", "content": "Hi"}])
                self.assertEqual(calls[0], result.prepared.request)
                stream = await relay(model="host-model", input=[{"role": "user", "content": "Hi"}], stream=True)
                self.assertEqual(reads, [])
                self.assertEqual((await anext(stream.__aiter__())).delta, "Hi")
                await stream.close()


@unittest.skipUnless(available("anthropic"), "Optional anthropic SDK is not installed")
class RealAnthropicSyncTests(unittest.TestCase):
    def make_client(self, handler):
        import anthropic
        http = sdk_http("anthropic")
        sdk = anthropic.Anthropic(api_key="test-not-real", base_url="https://relay.invalid",
            http_client=http.Client(transport=http.MockTransport(handler)), max_retries=0, timeout=5,
            _strict_response_validation=True)
        self.addCleanup(sdk.close)
        return sdk

    def test_real_media_and_append_only_memory_projection_roundtrip(self):
        http = sdk_http("anthropic")
        captured = []
        def handle(request):
            captured.append(json.loads(request.content))
            return http.Response(200, json=anthropic_body([
                {"type": "thinking", "thinking": "", "signature": "MOCK-binding"},
                {"type": "text", "text": "Done"}]))
        sdk = self.make_client(handle)
        history = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
            {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "Report"}}]}]
        with auto_connect(sdk, native=True, native_memory=True) as relay:
            relay.remember("export_format", "JSON")
            first = relay.invoke_result(model="host-model", messages=history, system="Stable policy", max_tokens=2000)
            history = first.prepared.request["messages"] + [
                {"role": "assistant", "content": first.response.content},
                {"role": "user", "content": "Continue"}]
            relay.remember("export_format", "CSV")
            second = relay.invoke_result(model="host-model", messages=history, system="Stable policy", max_tokens=2000)
            self.assertEqual(captured[-1], second.prepared.request)
            self.assertEqual(captured[-1]["messages"][:-1], native_json(history, sdk="anthropic"))
            self.assertEqual(captured[-1]["messages"][2]["content"][0]["signature"], "MOCK-binding")
            self.assertEqual(captured[-1]["system"], "Stable policy")
            self.assertIn('"export_format":"CSV"', captured[-1]["messages"][-1]["content"])

    def test_real_signed_thinking_tool_roundtrip(self):
        http = sdk_http("anthropic")
        captured = []
        def handle(request):
            captured.append(json.loads(request.content))
            first = len(captured) == 1
            return http.Response(200, json=anthropic_body(anthropic_thinking_call() if first else [{"type": "text", "text": "Done"}],
                                                         reason="tool_use" if first else "end_turn"))
        sdk = self.make_client(handle)
        with connect_anthropic(sdk, native=True) as relay:
            history = [{"role": "user", "content": "Lookup seven"}]
            first = relay.invoke_result(model="host-model", messages=history, max_tokens=2000,
                                         thinking={"type": "enabled", "budget_tokens": 1024})
            self.assertEqual(captured[0], first.prepared.request)
            history.extend([{"role": "assistant", "content": first.response.content},
                            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_mock", "content": "ready"}]}])
            second = relay.invoke_result(model="host-model", messages=history, max_tokens=2000,
                                          thinking={"type": "enabled", "budget_tokens": 1024})
            self.assertEqual(captured[1], second.prepared.request)
            self.assertEqual(captured[1]["messages"], native_json(history, sdk="anthropic"))
            self.assertEqual(captured[1]["messages"][1]["content"][0]["signature"], "MOCK-signature+/==")
            self.assertEqual(second.response.content[0].text, "Done")
        self.assertFalse(sdk.is_closed())

    def test_real_beta_compaction_blocks_and_context_management(self):
        http = sdk_http("anthropic")
        captured, headers = [], []
        def handle(request):
            captured.append(json.loads(request.content))
            headers.append(dict(request.headers))
            content = [{"type": "compaction", "content": "MOCK compacted content", "signature": "MOCK-binding"},
                       {"type": "text", "text": "Done"}]
            return http.Response(200, json=anthropic_body(content))
        sdk = self.make_client(handle)
        with auto_connect(sdk, native=True, beta=True) as relay:
            history = [{"role": "user", "content": "Summarize"}]
            options = {"model": "host-model", "max_tokens": 2000,
                       "betas": ["context-management-2025-06-27"],
                       "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]}}
            first = relay.invoke_result(messages=history, **options)
            self.assertEqual(captured[0], first.prepared.request)
            self.assertIn("context-management-2025-06-27", headers[0]["anthropic-beta"])
            history.extend([{"role": "assistant", "content": first.response.content}, {"role": "user", "content": "Continue"}])
            second = relay.invoke_result(messages=history, **options)
            self.assertEqual(captured[1], second.prepared.request)
            self.assertEqual(captured[1]["messages"][1]["content"], native_json(first.response.content, sdk="anthropic"))

    def test_real_beta_compaction_as_new_history_prefix_and_null_noop(self):
        http = sdk_http("anthropic")
        captured = []
        def handle(request):
            captured.append(json.loads(request.content))
            return http.Response(200, json=anthropic_body([{"type": "compaction", "content": None}]))
        sdk = self.make_client(handle)
        with connect_anthropic(sdk, native=True, beta=True) as relay:
            first = relay(model="host-model", messages=[{"role": "user", "content": "Compact"}], max_tokens=100)
            source = first.content[0]
            self.assertIsNone(source.content)
            history = [{"role": "assistant", "content": [source]}, {"role": "user", "content": "Continue"}]
            result = relay.invoke_result(model="host-model", messages=history, max_tokens=100)
            self.assertEqual(captured[-1], result.prepared.request)
            self.assertIsNone(captured[-1]["messages"][0]["content"][0]["content"])
            self.assertEqual(captured[-1]["messages"][0]["content"], native_json([source], sdk="anthropic"))


@unittest.skipUnless(available("anthropic"), "Optional anthropic SDK is not installed")
class RealAnthropicAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_async_sdk_preserves_body_and_stream_ownership(self):
        import anthropic
        http = sdk_http("anthropic")
        captured, reads = [], []
        class Body(http.AsyncByteStream):
            async def __aiter__(self):
                reads.append("read")
                yield b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}\n\n'
                yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        async def handle(request):
            captured.append(json.loads(request.content))
            if captured[-1].get("stream"):
                return http.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())
            return http.Response(200, json=anthropic_body([{"type": "text", "text": "Done"}]))
        async with anthropic.AsyncAnthropic(api_key="test-not-real", base_url="https://relay.invalid",
            http_client=http.AsyncClient(transport=http.MockTransport(handle)), max_retries=0, timeout=5,
            _strict_response_validation=True) as sdk:
            async with connect_anthropic(sdk, native=True) as relay:
                result = await relay.invoke_result(model="host-model", messages=[{"role": "user", "content": "Hi"}], max_tokens=100)
                self.assertEqual(captured[0], result.prepared.request)
                stream = await relay(model="host-model", messages=[{"role": "user", "content": "Hi"}], max_tokens=100, stream=True)
                self.assertEqual(reads, [])
                self.assertEqual((await anext(stream.__aiter__())).delta.text, "Hi")
                await stream.close()


@unittest.skipUnless(available("ollama"), "Optional ollama SDK is not installed")
class RealOllamaTests(unittest.TestCase):
    def test_actual_client_attachment_positional_call_and_sdk_message_replay(self):
        import httpx
        import ollama
        calls = []
        def handle(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "host-model", "created_at": "2026-10-22T00:00:00Z",
                "message": {"role": "assistant", "content": "Done"}, "done": True, "done_reason": "stop"})
        sdk = ollama.Client(host="https://relay.invalid", transport=httpx.MockTransport(handle))
        original = sdk.chat
        with attach(sdk) as relay:
            history = [{"role": "user", "content": "Hello"}]
            first = sdk.chat("host-model", history)
            self.assertIsInstance(first, ollama.ChatResponse)
            history.extend([first.message, {"role": "user", "content": "Continue"}])
            sdk.chat("host-model", history)
            self.assertEqual(calls[1]["messages"][1], {"role": "assistant", "content": "Done"})
            self.assertEqual(calls[1]["messages"][-1], {"role": "user", "content": "Continue"})
        self.assertEqual(sdk.chat, original)
        sdk._client.close()


@unittest.skipUnless(available("langchain_core") and available("langgraph"), "Optional LangChain/LangGraph are not installed")
class RealFrameworkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)

    async def test_real_runnable_sync_async_and_standardized_tool_metadata(self):
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        from langchain_core.runnables import RunnableLambda
        calls = []
        answer = AIMessage(content="Done", id="reply")
        def model(messages):
            calls.append(messages)
            return answer
        runnable = RunnableLambda(model)
        history = [HumanMessage(content="Lookup", id="human"),
                   AIMessage(content="", id="call", tool_calls=[{"type": "tool_call", "id": "call_1", "name": "lookup", "args": {"id": 7}}]),
                   ToolMessage(content="ready", tool_call_id="call_1", name="lookup", id="result", artifact={"rows": 1})]
        with connect_langchain(runnable) as relay:
            bridged = relay.as_runnable()
            self.assertIsInstance(bridged, RunnableLambda)
            self.assertIs(bridged.invoke(history), answer)
            self.assertIs(await bridged.ainvoke(history), answer)
            self.assertEqual(calls[0][-2].tool_calls, history[-2].tool_calls)
            self.assertEqual(calls[0][-1].artifact, {"rows": 1})
            self.assertEqual(calls[0][-1].id, "result")
            self.assertEqual(len(history), 3)

    async def test_real_stategraph_add_messages_preserves_original_state_and_isolates_thread(self):
        from langchain_core.messages import AIMessage, HumanMessage
        from langchain_core.runnables import RunnableLambda
        from langgraph.graph import END, START, MessagesState, StateGraph
        with connect_langchain(RunnableLambda(lambda messages: AIMessage(content="Done", id="answer")), thread_id="tenant-a") as relay:
            builder = StateGraph(MessagesState)
            builder.add_node("model", relay.node)
            builder.add_edge(START, "model")
            builder.add_edge("model", END)
            graph = builder.compile()
            source = [HumanMessage(content="Hello", id="source")]
            result = graph.invoke({"messages": source}, config={"configurable": {"thread_id": "tenant-a"}})
            self.assertEqual([message.id for message in result["messages"]], ["source", "answer"])
            self.assertEqual([message.id for message in source], ["source"])
            with self.assertRaisesRegex(ValueError, "thread_id"):
                graph.invoke({"messages": source}, config={"configurable": {"thread_id": "tenant-b"}})
            async_builder = StateGraph(MessagesState)
            async_builder.add_node("model", relay.anode)
            async_builder.add_edge(START, "model")
            async_builder.add_edge("model", END)
            result = await async_builder.compile().ainvoke({"messages": source}, config={"configurable": {"thread_id": "tenant-a"}})
            self.assertEqual([message.id for message in result["messages"]], ["source", "answer"])


if __name__ == "__main__":
    unittest.main()
