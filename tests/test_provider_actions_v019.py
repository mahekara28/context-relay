"""Offline provider action boundary, malformed envelope and native-turn tests."""
import asyncio
import copy
import json
from types import SimpleNamespace
import unittest

from context_relay import ToolExecutionError, connect
from context_relay.actions import ActionHarness, ActionProposalError
from context_relay.anthropic import connect_anthropic
from context_relay.openai import connect_openai
from context_relay.provider_actions import ActionDecoder, NativeActionHarness, provider_capabilities


def responses(arguments='{"id":7}'):
    return {"object": "response", "status": "completed", "output": [
        {"type": "function_call", "call_id": "call-1", "name": "lookup", "arguments": arguments, "status": "completed"}]}


def chat(arguments='{"id":7}'):
    return {"object": "chat.completion", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {
            "name": "lookup", "arguments": arguments}}]}}]}


def anthropic():
    return {"type": "message", "role": "assistant", "stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "call-1", "name": "lookup", "input": {"id": 7}}]}


def ollama():
    return {"done": True, "done_reason": "stop", "message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "lookup", "arguments": {"id": 7}}}]}}


class ActionDecoderTests(unittest.TestCase):
    def reject(self, provider, response):
        with self.assertRaises(ActionProposalError) as caught:
            ActionDecoder(provider)(response)
        self.assertIs(caught.exception.response, response)

    def test_each_provider_returns_one_defensive_canonical_proposal(self):
        for provider, factory in (("openai_chat", chat), ("openai_responses", responses), ("anthropic", anthropic), ("ollama", ollama)):
            with self.subTest(provider=provider):
                response = factory()
                before = copy.deepcopy(response)
                decoded = ActionDecoder(provider)(response)
                self.assertEqual(decoded, {"tool": "lookup", "arguments": {"id": 7}})
                decoded["arguments"]["id"] = 9
                self.assertEqual(response, before)

    def test_unknown_provider_or_untrusted_model_dump_never_executes(self):
        calls = []
        class Duck:
            def model_dump(self, **options):
                calls.append(options)
                return responses()
        for provider in (None, True, "openai", "auto", "unknown"):
            with self.assertRaises(ValueError):
                ActionDecoder(provider)
        for provider in ("openai_chat", "openai_responses", "anthropic", "ollama"):
            self.reject(provider, Duck())
            self.reject(provider, iter([responses()]))
        self.assertEqual(calls, [])

    def test_raw_json_arguments_reject_ambiguity_nonfinite_lossy_and_bounds(self):
        bad = ['{"id":1,"id":7}', '{"id":{"x":1,"x":2}}', '{"id":NaN}', '{"id":Infinity}',
               '{"id":1e400}', '{"id":9007199254740993.0}', '{"id":0.1234567890123456789}',
               '{"id":' + '[' * 66 + '1' + ']' * 66 + '}', '{"id":"' + 'x' * 64000 + '"}',
               '[7]', 'null', '{"id":7},"tool":"other"', '{"id":7} trailing']
        for provider, factory in (("openai_chat", chat), ("openai_responses", responses)):
            for arguments in bad:
                with self.subTest(provider=provider, argument=arguments[:50]):
                    self.reject(provider, factory(arguments))
            decoded = ActionDecoder(provider)(factory('{"id":9007199254740993,"price":0.125}'))
            self.assertEqual(decoded["arguments"], {"id": 9007199254740993, "price": 0.125})

    def test_dictionary_arguments_reject_non_json_nonfinite_and_nested_bounds(self):
        deep = 1
        for _ in range(67):
            deep = [deep]
        for provider, factory in (("anthropic", anthropic), ("ollama", ollama)):
            for arguments in ({"id": float("nan")}, {"id": (1, 2)}, {"id": deep}, {1: 7}, {"id": "x" * 64000}):
                response = factory()
                if provider == "anthropic":
                    response["content"][0]["input"] = arguments
                else:
                    response["message"]["tool_calls"][0]["function"]["arguments"] = arguments
                with self.subTest(provider=provider, type=type(arguments)):
                    self.reject(provider, response)

    def test_envelope_size_is_bounded_even_when_action_is_small(self):
        body = responses()
        body["metadata"] = {"note": "x" * 256000}
        self.reject("openai_responses", body)
        body = anthropic()
        body["content"].insert(0, {"type": "text", "text": "x" * 256000})
        self.reject("anthropic", body)

    def test_chat_requires_one_completed_client_function_without_refusal(self):
        variants = []
        body = chat(); body["choices"].append(copy.deepcopy(body["choices"][0])); variants.append(body)
        body = chat(); body["choices"][0]["message"]["tool_calls"] *= 2; variants.append(body)
        for reason in ("stop", "length", "content_filter", "function_call"):
            body = chat(); body["choices"][0]["finish_reason"] = reason; variants.append(body)
        for key, value in (("refusal", "no"), ("refusal", ""), ("function_call", {"name": "lookup"}), ("audio", {}), ("annotations", [{"type": "url_citation"}])):
            body = chat(); body["choices"][0]["message"][key] = value; variants.append(body)
        body = chat(); body["object"] = "chat.completion.chunk"; variants.append(body)
        body = chat(); body["choices"][0]["index"] = False; variants.append(body)
        body = chat(); body["choices"][0]["message"]["tool_calls"][0]["type"] = "custom"; variants.append(body)
        for body in variants:
            with self.subTest(body=body):
                self.reject("openai_chat", body)

    def test_responses_refuses_incomplete_mixed_parallel_and_non_direct_calls(self):
        variants = []
        for status in ("in_progress", "incomplete", "failed", "cancelled", None):
            body = responses(); body["status"] = status; variants.append(body)
        for field, value in (("error", {"code": "failure"}), ("incomplete_details", {"reason": "max_output_tokens"})):
            body = responses(); body[field] = value; variants.append(body)
        for item in ({"type": "web_search_call"}, {"type": "custom_tool_call"}, {"type": "computer_call"}, {"type": "mcp_call"}, {"type": "future_call"}):
            body = responses(); body["output"].append(item); variants.append(body)
        body = responses(); body["output"] *= 2; variants.append(body)
        for field, value in (("status", "incomplete"), ("namespace", "system"), ("async", True), ("async", 0), ("caller", {"type": "program", "caller_id": "caller-1"}), ("caller", {"type": "direct", "extra": 1}), ("extra", "ignored")):
            body = responses(); body["output"][0][field] = value; variants.append(body)
        for body in variants:
            with self.subTest(body=body):
                self.reject("openai_responses", body)
        body = responses(); body["output"][0].update(caller={"type": "direct"}, **{"async": False, "namespace": None})
        self.assertEqual(ActionDecoder("openai_responses")(body)["arguments"], {"id": 7})

    def test_responses_known_ancillary_items_are_validated_but_never_claimed_verified(self):
        body = responses()
        body["output"][:0] = [
            {"type": "reasoning", "id": "r-1", "summary": [{"type": "summary_text", "text": "opaque summary"}],
             "content": [{"type": "reasoning_text", "text": "unverified"}], "encrypted_content": "OPAQUE=="},
            {"type": "message", "id": "m-1", "role": "assistant", "status": "completed", "phase": "commentary",
             "content": [{"type": "output_text", "text": "unverified", "annotations": [], "logprobs": []}]}]
        before = copy.deepcopy(body)
        self.assertEqual(ActionDecoder("openai_responses")(body)["arguments"], {"id": 7})
        self.assertEqual(body, before)
        for mutation in (lambda b: b["output"][0].update(status="in_progress"),
                         lambda b: b["output"][0]["summary"][0].update(type="function_call"),
                         lambda b: b["output"][1]["content"][0].update(type="refusal"),
                         lambda b: b["output"][1]["content"][0].update(annotations=[{"type": "url_citation"}])):
            variant = copy.deepcopy(body); mutation(variant)
            self.reject("openai_responses", variant)

    def test_anthropic_preserves_known_thinking_and_refuses_mixed_native_actions(self):
        body = anthropic()
        body["content"][:0] = [{"type": "text", "text": "unverified", "citations": []},
            {"type": "thinking", "thinking": "private", "signature": "OPAQUE=="}, {"type": "redacted_thinking", "data": "OPAQUE=="}]
        self.assertEqual(ActionDecoder("anthropic")(body)["arguments"], {"id": 7})
        for extra in ({"type": "server_tool_use"}, {"type": "tool_result"}, {"type": "mcp_tool_use"}, {"type": "unknown"}, anthropic()["content"][0]):
            variant = copy.deepcopy(body); variant["content"].append(extra)
            self.reject("anthropic", variant)
        for reason in ("end_turn", "max_tokens", "refusal", "pause_turn", None):
            variant = copy.deepcopy(body); variant["stop_reason"] = reason
            self.reject("anthropic", variant)
        for key, value in (("caller", {"type": "code_execution_20250825", "tool_id": "server"}), ("toolset_name", "remote")):
            variant = anthropic(); variant["content"][0][key] = value
            self.reject("anthropic", variant)
        variant = copy.deepcopy(body); variant["content"][1]["signature"] = None
        self.reject("anthropic", variant)

    def test_ollama_refuses_partial_multiple_and_mixed_action_shapes(self):
        variants = []
        for key, value in (("done", False), ("done", 1), ("done_reason", "length")):
            body = ollama(); body[key] = value; variants.append(body)
        body = ollama(); body["message"]["tool_calls"] *= 2; variants.append(body)
        for key, value in (("images", ["opaque"]), ("tool_name", "server"), ("unknown", "value")):
            body = ollama(); body["message"][key] = value; variants.append(body)
        body = ollama(); body["message"]["tool_calls"][0]["type"] = "function"; variants.append(body)
        for body in variants:
            self.reject("ollama", body)

    def test_metadata_cannot_change_action_authority_and_scope_report_is_defensive(self):
        body = responses()
        body["metadata"] = {"tool": "delete", "arguments": {"id": 999}, "verified": True}
        self.assertEqual(ActionDecoder("openai_responses")(body), {"tool": "lookup", "arguments": {"id": 7}})
        report = provider_capabilities("openai_responses")
        self.assertFalse(report["ancillary_semantics_checked"])
        self.assertFalse(report["source_truth_checked"])
        self.assertFalse(report["direct_calls_protected"])
        self.assertFalse(report["automatic_tool_loop"])
        report["ancillary_types"].append("custom_tool_call")
        self.assertNotIn("custom_tool_call", provider_capabilities("openai_responses")["ancillary_types"])


class NativeActionHarnessTests(unittest.TestCase):
    def agent(self, provider, *, anthropic_mode=False):
        sdk = SimpleNamespace(**({"messages": SimpleNamespace(create=provider)} if anthropic_mode else {"responses": SimpleNamespace(create=provider)}))
        agent = connect_anthropic(sdk, native=True) if anthropic_mode else connect_openai(sdk, api="responses", native=True)
        self.addCleanup(agent.close)
        return agent

    def test_native_complete_result_and_request_stay_unchanged(self):
        calls, effects = [], []
        response = responses()
        def provider(**request):
            calls.append(request)
            return response
        agent = self.agent(provider)
        agent.remember("chosen", 7)
        harness = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id) or id}, contracts={"lookup": {"bindings": {"id": "chosen"}}})
        result = harness.invoke(model="fixture", input=[{"role": "user", "content": "Look up seven"}], instructions="Keep this policy")
        self.assertTrue(result.executed)
        self.assertEqual(effects, [7])
        self.assertIs(result.agent_result.response, response)
        self.assertEqual(result.agent_result.prepared.request, calls[0])
        self.assertEqual(len(calls), 1)
        self.assertFalse(harness.coverage()["direct_calls_protected"])

    def test_native_conventional_parameters_need_explicit_contract_and_setup_is_atomic(self):
        calls = []
        agent = self.agent(lambda **request: calls.append(request))
        revision = agent.guard.turn().revision
        with self.assertRaises(ValueError):
            NativeActionHarness(agent, {"first": lambda: None, "save": lambda format: None})
        self.assertEqual(agent.guard.store.policies(agent.session_id), [])
        self.assertEqual(agent.guard.turn().revision, revision)
        self.assertEqual(calls, [])
        NativeActionHarness(agent, {"save": lambda format: None}, contracts={"save": {"unchecked": ["format"]}})

    def test_changed_fact_during_native_provider_call_does_not_refresh_turn(self):
        effects = []
        def provider(**request):
            agent.remember("chosen", 8)
            return responses('{"id":8}')
        agent = self.agent(provider)
        agent.remember("chosen", 7)
        harness = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id)}, contracts={"lookup": {"bindings": {"id": "chosen"}}}, repair="known")
        result = harness.invoke(model="fixture", input=[{"role": "user", "content": "Lookup"}])
        self.assertFalse(result.executed)
        self.assertEqual(effects, [])
        self.assertEqual(result.execution["repair_attempts"], 0)

    def test_native_anthropic_convenience_and_custom_decoder(self):
        effects = []
        response = anthropic()
        agent = self.agent(lambda **request: response, anthropic_mode=True)
        harness = agent.protect_tools({"lookup": lambda id: effects.append(id)}, contracts={"lookup": {"unchecked": ["id"]}})
        result = harness.invoke(model="fixture", messages=[{"role": "user", "content": "Lookup"}], max_tokens=64)
        self.assertTrue(result.executed)
        self.assertEqual(effects, [7])
        custom = self.agent(lambda **request: {"custom": "envelope"})
        harness = NativeActionHarness(custom, {"noop": lambda: None}, decoder=lambda response: {"tool": "noop", "arguments": {}})
        self.assertTrue(harness.invoke(model="fixture", input=[{"role": "user", "content": "Run"}]).executed)

    def test_native_streams_rejected_before_provider_or_tool_and_wrong_connection_refused(self):
        calls = []
        agent = self.agent(lambda **request: calls.append(request))
        harness = NativeActionHarness(agent, {"noop": lambda: self.fail("must not execute")})
        for stream in (True, None, "false", 1):
            with self.assertRaises(ValueError):
                harness.invoke(model="fixture", input=[], stream=stream)
        self.assertEqual(calls, [])
        with connect(lambda **request: {}) as text_agent:
            with self.assertRaises(TypeError):
                NativeActionHarness(text_agent, {"noop": lambda: None})
        with self.assertRaises(TypeError):
            ActionHarness(agent, {"noop": lambda: None})

    def test_native_tool_failure_after_effect_is_never_repaired_or_retried(self):
        effects, callbacks, calls = [], [], []
        def provider(**request):
            calls.append(request)
            return responses()
        def tool(id):
            effects.append(id)
            raise RuntimeError("failed after write")
        agent = self.agent(provider)
        harness = NativeActionHarness(agent, {"lookup": tool}, contracts={"lookup": {"unchecked": ["id"]}}, repair=lambda request: callbacks.append(request))
        with self.assertRaises(ToolExecutionError):
            harness.invoke(model="fixture", input=[{"role": "user", "content": "Lookup"}])
        self.assertEqual(effects, [7])
        self.assertEqual(len(calls), 1)
        self.assertEqual(callbacks, [])

    def test_native_resume_reuses_contract_without_new_revision(self):
        agent = self.agent(lambda **request: responses())
        options = {"contracts": {"lookup": {"unchecked": ["id"]}}}
        NativeActionHarness(agent, {"lookup": lambda id: id}, **options)
        revision = agent.guard.turn().revision
        effects = []
        harness = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id)}, resume=True, **options)
        self.assertEqual(agent.guard.turn().revision, revision)
        self.assertTrue(harness.invoke(model="fixture", input=[{"role": "user", "content": "Lookup"}]).executed)
        self.assertEqual(effects, [7])


class AsyncNativeActionHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_native_captures_before_await_and_preserves_response(self):
        effects = []
        response = responses()
        async def provider(**request):
            await asyncio.sleep(0)
            return response
        client = SimpleNamespace(responses=SimpleNamespace(create=provider))
        async with connect_openai(client, api="responses", native=True) as agent:
            harness = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id)}, contracts={"lookup": {"unchecked": ["id"]}})
            result = await harness.invoke(model="fixture", input=[{"role": "user", "content": "Lookup"}])
            self.assertIs(result.agent_result.response, response)
            self.assertTrue(result.executed)
        self.assertEqual(effects, [7])

    async def test_async_native_changed_state_while_awaiting_blocks_effect(self):
        effects = []
        async def provider(**request):
            await asyncio.sleep(0)
            agent.remember("chosen", 8)
            return responses('{"id":8}')
        client = SimpleNamespace(responses=SimpleNamespace(create=provider))
        async with connect_openai(client, api="responses", native=True) as agent:
            agent.remember("chosen", 7)
            harness = NativeActionHarness(agent, {"lookup": lambda id: effects.append(id)}, contracts={"lookup": {"bindings": {"id": "chosen"}}}, repair="known")
            result = await harness.invoke(model="fixture", input=[{"role": "user", "content": "Lookup"}])
            self.assertFalse(result.executed)
        self.assertEqual(effects, [])


if __name__ == "__main__":
    unittest.main()
