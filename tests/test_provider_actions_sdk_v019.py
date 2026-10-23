"""Actual optional SDKs, intercepted HTTP, local tool effects; no hosted inference."""
import asyncio
import json
import unittest

from context_relay import auto_connect
from test_real_sdks import available, sdk_http, response_body, reasoning_call, anthropic_body, anthropic_thinking_call


@unittest.skipUnless(available("openai"), "Optional openai SDK is not installed")
class OpenAIActionSDKTests(unittest.TestCase):
    def client(self, handler):
        import openai
        http = sdk_http("openai")
        sdk = openai.OpenAI(api_key="offline-fixture", base_url="https://relay.invalid/v1",
            http_client=http.Client(transport=http.MockTransport(handler)), max_retries=0,
            _strict_response_validation=True)
        self.addCleanup(sdk.close)
        return sdk

    def test_native_reasoning_repair_executes_one_local_tool_and_keeps_original(self):
        from context_relay import NativeActionHarness
        http, requests, effects = sdk_http("openai"), [], []
        def handler(request):
            requests.append(json.loads(request.content))
            output = reasoning_call()
            output[1]["arguments"] = '{"id":999}'
            return http.Response(200, json=response_body(output))
        with auto_connect(self.client(handler), api="responses", native=True) as agent:
            agent.remember("selected_id", 7)
            harness = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id) or {"id": id}},
                contracts={"lookup": {"bindings": {"id": "selected_id"}}}, repair="known")
            result = harness.invoke(model="fixture-model", input=[{"role": "user", "content": "Look up seven"}],
                tools=[{"type": "function", "name": "lookup", "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}])
        self.assertEqual(effects, [7])
        self.assertEqual(len(requests), 1)
        self.assertEqual(result.execution["status"], "repaired")
        self.assertEqual(requests[0], result.agent_result.prepared.request)
        self.assertEqual(result.agent_result.response.output[0].encrypted_content, "MOCK-opaque+/==")
        self.assertEqual(result.agent_result.response.output[1].arguments, '{"id":999}')

    def test_native_state_change_during_sdk_request_never_refreshes_permission(self):
        from context_relay import NativeActionHarness
        http, effects = sdk_http("openai"), []
        def handler(request):
            agent.remember("selected_id", 8)
            return http.Response(200, json=response_body(reasoning_call()))
        with auto_connect(self.client(handler), api="responses", native=True) as agent:
            agent.remember("selected_id", 7)
            actions = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id)},
                contracts={"lookup": {"bindings": {"id": "selected_id"}}}, repair="known")
            result = actions.invoke(model="fixture", input=[{"role": "user", "content": "Lookup"}])
        self.assertFalse(result.executed)
        self.assertEqual(effects, [])

    def test_incomplete_sdk_tool_call_cannot_execute(self):
        from context_relay import NativeActionHarness, ActionProposalError
        http, effects = sdk_http("openai"), []
        body = response_body(reasoning_call())
        body.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        with auto_connect(self.client(lambda request: http.Response(200, json=body)), api="responses", native=True) as agent:
            actions = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id)}, contracts={"lookup": {"unchecked": ["id"]}})
            with self.assertRaises(ActionProposalError):
                actions.invoke(model="fixture", input=[{"role": "user", "content": "Lookup"}])
        self.assertEqual(effects, [])

    def test_chat_sdk_decodes_single_function_call_through_text_connection(self):
        from context_relay import ActionDecoder
        http, effects = sdk_http("openai"), []
        body = {"id": "chat_mock", "object": "chat.completion", "created": 0, "model": "fixture",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "logprobs": None,
                    "message": {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_mock", "type": "function", "function": {"name": "lookup", "arguments": '{"id":7}'}}]}}]}
        with auto_connect(self.client(lambda request: http.Response(200, json=body))) as agent:
            agent.remember("selected_id", 7)
            actions = agent.protect_tools({"lookup": lambda id: effects.append(id) or id},
                contracts={"lookup": {"bindings": {"id": "selected_id"}}}, decoder=ActionDecoder("openai_chat"))
            result = actions.invoke(messages=[{"role": "user", "content": "Lookup"}], model="fixture")
        self.assertTrue(result.executed)
        self.assertEqual(effects, [7])

    def test_async_native_sdk_single_dispatch(self):
        from context_relay import NativeActionHarness
        import openai
        http, effects = sdk_http("openai"), []
        async def run():
            async with openai.AsyncOpenAI(api_key="offline-fixture", base_url="https://relay.invalid/v1",
                http_client=http.AsyncClient(transport=http.MockTransport(lambda request: http.Response(200, json=response_body(reasoning_call())))),
                max_retries=0, _strict_response_validation=True) as sdk:
                async with auto_connect(sdk, api="responses", native=True) as agent:
                    agent.remember("selected_id", 7)
                    harness = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id) or id},
                        contracts={"lookup": {"bindings": {"id": "selected_id"}}})
                    result = await harness.invoke(model="fixture", input=[{"role": "user", "content": "Lookup"}])
                    self.assertTrue(result.executed)
        asyncio.run(run())
        self.assertEqual(effects, [7])


@unittest.skipUnless(available("anthropic"), "Optional anthropic SDK is not installed")
class AnthropicActionSDKTests(unittest.TestCase):
    def test_thinking_tool_history_replays_without_rewriting_signature(self):
        from context_relay import NativeActionHarness
        import anthropic
        http, requests, effects = sdk_http("anthropic"), [], []
        def handler(request):
            requests.append(json.loads(request.content))
            return http.Response(200, json=anthropic_body(anthropic_thinking_call(), reason="tool_use"))
        with anthropic.Anthropic(api_key="offline-fixture", base_url="https://relay.invalid",
            http_client=http.Client(transport=http.MockTransport(handler)), max_retries=0,
            _strict_response_validation=True) as sdk:
            with auto_connect(sdk, native=True) as agent:
                agent.remember("selected_id", 7)
                harness = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id) or {"id": id}},
                    contracts={"lookup": {"bindings": {"id": "selected_id"}}})
                first = harness.invoke(model="fixture", messages=[{"role": "user", "content": "Lookup"}], max_tokens=2000)
                history = first.agent_result.prepared.request["messages"] + [
                    {"role": "assistant", "content": first.agent_result.response.content},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_mock", "content": "done"}]}]
                second = harness.invoke(model="fixture", messages=history, max_tokens=2000)
        self.assertEqual(effects, [7, 7])
        self.assertTrue(second.executed)
        self.assertEqual(requests[1]["messages"][1]["content"][0]["signature"], "MOCK-signature+/==")
        self.assertEqual(requests[1], second.agent_result.prepared.request)


@unittest.skipUnless(available("ollama"), "Optional ollama SDK is not installed")
class OllamaActionSDKTests(unittest.TestCase):
    def test_actual_sdk_tool_object_uses_guarded_dispatch(self):
        from context_relay import ActionDecoder
        import httpx, ollama
        effects = []
        def handler(request):
            return httpx.Response(200, json={"model": "fixture", "done": True, "done_reason": "stop", "message": {
                "role": "assistant", "content": "", "tool_calls": [{"function": {"name": "lookup", "arguments": {"id": 7}}}]}})
        sdk = ollama.Client(host="https://relay.invalid", transport=httpx.MockTransport(handler))
        self.addCleanup(sdk._client.close)
        with auto_connect(sdk) as agent:
            agent.remember("selected_id", 7)
            harness = agent.protect_tools({"lookup": lambda id: effects.append(id) or id},
                contracts={"lookup": {"bindings": {"id": "selected_id"}}}, decoder=ActionDecoder("ollama"))
            result = harness.invoke(messages=[{"role": "user", "content": "Lookup"}], model="fixture")
        self.assertTrue(result.executed)
        self.assertEqual(effects, [7])
        self.assertIsInstance(result.agent_result.response, ollama.ChatResponse)
