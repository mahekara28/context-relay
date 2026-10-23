"""Captured Ollama v0.40.1 Responses metadata, genuine SDK, no inference/network.

Fixture copied exactly from the first finished model call in
`evaluations/responses-pilot-v020-first/model-calls.jsonl`; original failures stay
unchanged. Field types and parallel_tool_calls default are declared in
https://github.com/ollama/ollama/blob/v0.40.1/openai/responses.go.
"""
import copy
import json
import unittest

from context_relay import ActionDecoder, ActionProposalError, OutcomePolicy, auto_connect
from test_real_sdks import available, sdk_http


CAPTURED = {'id': 'resp_243646',
 'created_at': 1792751549.0,
 'error': None,
 'incomplete_details': None,
 'instructions': 'Export the approved release manifest by calling export_manifest exactly once. Copy '
                 'the current request ID, source version, release and artifacts from Relay workflow '
                 'facts. Use the current effective export_format preference (including a one-time '
                 'override). Preserve filenames, byte counts and SHA256 values exactly. Do not invent '
                 'or summarize artifact data.',
 'metadata': {},
 'model': 'qwen2.5:3b',
 'object': 'response',
 'output': [{'arguments': '{"release":"Relay-0.19.0-archive","artifacts":[{"bytes":222163,"filename":"context_relay_local-0.19.0-py3-none-any.whl","sha256":"f775d60994ac7420f58bdb30b71388825451acac140279382ff087a6cea51527"},{"bytes":2079149,"filename":"context_relay_local-0.19.0.tar.gz","sha256":"4ec08052e9f9f22deafbfbca3a94c7ac6b056fdfc0af1aec82040967d4d8f5c6"}],"format":"CSV","request_id":"standing-csv","source_version":"20f69cd4e678e0c83e9f0a39eceebf299c159c9c7b88e3ae4a6150cc82356a1c"}',
             'call_id': 'call_uazlzam7',
             'name': 'export_manifest',
             'type': 'function_call',
             'id': 'fc_resp_243646_0',
             'status': 'completed'}],
 'parallel_tool_calls': True,
 'temperature': 0.0,
 'tool_choice': 'auto',
 'tools': [{'name': 'export_manifest',
            'parameters': {'additionalProperties': False,
                           'properties': {'artifacts': {'items': {'additionalProperties': False,
                                                                  'properties': {'bytes': {'type': 'integer'},
                                                                                 'filename': {'type': 'string'},
                                                                                 'sha256': {'type': 'string'}},
                                                                  'required': ['filename',
                                                                               'bytes',
                                                                               'sha256'],
                                                                  'type': 'object'},
                                                        'type': 'array'},
                                          'format': {'enum': ['JSON', 'CSV'], 'type': 'string'},
                                          'release': {'type': 'string'},
                                          'request_id': {'type': 'string'},
                                          'source_version': {'type': 'string'}},
                           'required': ['request_id',
                                        'source_version',
                                        'format',
                                        'release',
                                        'artifacts'],
                           'type': 'object'},
            'strict': True,
            'type': 'function'}],
 'top_p': 1.0,
 'background': False,
 'completed_at': 1792751549.0,
 'max_output_tokens': 2048,
 'max_tool_calls': None,
 'previous_response_id': None,
 'prompt_cache_key': None,
 'reasoning': None,
 'safety_identifier': None,
 'service_tier': 'default',
 'status': 'completed',
 'text': {'format': {'type': 'text'}},
 'top_logprobs': 0,
 'truncation': 'disabled',
 'usage': {'input_tokens': 788,
           'input_tokens_details': {'cached_tokens': 0},
           'output_tokens': 301,
           'output_tokens_details': {'reasoning_tokens': 0},
           'total_tokens': 1089},
 'presence_penalty': 0,
 'frequency_penalty': 0,
 'store': False}


class ResponsesMetadataTests(unittest.TestCase):
    def test_exact_captured_response_decodes_without_mutation(self):
        original = copy.deepcopy(CAPTURED)
        proposal = ActionDecoder("openai_responses")(CAPTURED)
        self.assertEqual(proposal, {"tool": "export_manifest",
                                   "arguments": json.loads(CAPTURED["output"][0]["arguments"])})
        self.assertEqual(CAPTURED, original)
        # This reported default does not mean there were parallel calls.
        self.assertIs(CAPTURED["parallel_tool_calls"], True)

    def test_metadata_accepts_only_explicit_boolean_and_numeric_types(self):
        decoder = ActionDecoder("openai_responses")
        body = copy.deepcopy(CAPTURED)
        body.update(store=True, presence_penalty=-0.5, frequency_penalty=1.5)
        self.assertEqual(decoder(body)["tool"], "export_manifest")
        invalid = {"store": [None, 0, "false", {}, []],
                   "parallel_tool_calls": [None, 1, "true"],
                   "presence_penalty": [None, True, "0", {}, float("nan"), float("inf")],
                   "frequency_penalty": [None, False, "0", [], float("-inf")]}
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    body = copy.deepcopy(CAPTURED)
                    body[field] = value
                    with self.assertRaises(ActionProposalError):
                        decoder(body)

    def test_metadata_does_not_widen_action_or_envelope_grammar(self):
        variants = []
        unknown_envelope = copy.deepcopy(CAPTURED)
        unknown_envelope["provider_extension"] = {"execute": True}
        variants.append(unknown_envelope)
        unknown_item = copy.deepcopy(CAPTURED)
        unknown_item["output"][0]["provider_extension"] = {"execute": True}
        variants.append(unknown_item)
        multiple = copy.deepcopy(CAPTURED)
        multiple["output"].append(copy.deepcopy(multiple["output"][0]))
        variants.append(multiple)
        duplicate_arguments = copy.deepcopy(CAPTURED)
        duplicate_arguments["output"][0]["arguments"] = '{"format":"CSV","format":"JSON"}'
        variants.append(duplicate_arguments)
        for index, body in enumerate(variants):
            with self.subTest(attack=index), self.assertRaises(ActionProposalError):
                ActionDecoder("openai_responses")(body)


@unittest.skipUnless(available("openai"), "Optional openai SDK is not installed")
class CapturedMetadataSDKTests(unittest.TestCase):
    def test_captured_response_executes_once_and_retains_full_response_and_history(self):
        import openai
        from context_relay import ResponsesWorkflow
        http, requests, effects = sdk_http("openai"), [], []
        expected = json.loads(CAPTURED["output"][0]["arguments"])
        no_call = copy.deepcopy(CAPTURED)
        no_call.update(id="resp_metadata_followup", output=[])

        def handler(request):
            self.assertEqual(request.url.path, "/v1/responses")
            requests.append(json.loads(request.content))
            return http.Response(200, json=CAPTURED if len(requests) == 1 else no_call)

        def export_manifest(request_id, source_version, format, release, artifacts):
            effects.append({"request_id": request_id, "source_version": source_version,
                            "format": format, "release": release, "artifacts": artifacts})
            return {"ok": True}

        # Match the actual pilot's normal SDK parsing. Ollama omits the current
        # OpenAI-only cache_write_tokens usage field; do not invent it here.
        with openai.OpenAI(api_key="offline-captured-response", base_url="https://relay.invalid/v1",
                http_client=http.Client(transport=http.MockTransport(handler)), max_retries=0) as client:
            with auto_connect(client, api="responses", native=True) as agent:
                for key, value in expected.items():
                    agent.remember("expected_" + key, value)
                workflow = ResponsesWorkflow(agent, {"export_manifest": export_manifest},
                    contracts={"export_manifest": {
                        "bindings": {key: "expected_" + key for key in expected},
                        "outcome": OutcomePolicy(required={"/ok": True})}},
                    schemas=copy.deepcopy(CAPTURED["tools"]), memory=False)
                first = workflow.send("Perform the approved export.", request_id="standing-csv",
                                      model=CAPTURED["model"], store=False)
                self.assertTrue(first.executed)
                self.assertTrue(first.completed)
                self.assertEqual(first.response, CAPTURED)
                self.assertEqual(effects, [expected])
                history = workflow.history
                call_index = next(i for i, item in enumerate(history)
                                  if item.get("type") == "function_call")
                self.assertEqual(history[call_index:call_index + 1], CAPTURED["output"])
                self.assertEqual(history[call_index + 1]["call_id"], CAPTURED["output"][0]["call_id"])
                replay = workflow.send("Perform the approved export.", request_id="standing-csv",
                                       model=CAPTURED["model"], store=False)
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.response, CAPTURED)
                self.assertEqual(len(requests), 1)
                # A second mocked response contains no call, so it must not
                # execute. Its request demonstrates exact prior native replay.
                with self.assertRaises(ActionProposalError):
                    workflow.send("Continue the review.", request_id="followup",
                                  model=CAPTURED["model"], store=False)
                self.assertEqual(requests[1]["input"][:len(history)], history)
                self.assertEqual(effects, [expected])
                self.assertFalse(requests[0]["parallel_tool_calls"])
                self.assertTrue(first.response["parallel_tool_calls"])
                self.assertEqual(first.response["presence_penalty"], 0)
                self.assertEqual(first.response["frequency_penalty"], 0)
                self.assertIs(first.response["store"], False)


if __name__ == "__main__":
    unittest.main()
