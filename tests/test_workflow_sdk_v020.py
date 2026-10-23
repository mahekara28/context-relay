"""Actual OpenAI SDK serialization with intercepted HTTP; no hosted inference.

Opaque reasoning data is a fixture, not an authenticated provider signature.
The protocol replay follows https://developers.openai.com/api/docs/guides/function-calling.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from context_relay import auto_connect
from test_real_sdks import available, response_body, sdk_http


SCHEMAS = [{"type": "function", "name": "lookup", "description": "Read one permitted record.",
            "strict": True, "parameters": {"type": "object", "properties": {"id": {"type": "integer"}},
                                           "required": ["id"], "additionalProperties": False}}]


def provider_response(number, arguments='{ "id": 7 }'):
    output = [{"type": "reasoning", "id": f"rs_{number}", "summary": [],
               "encrypted_content": f"MOCK-opaque-{number}+/==", "status": "completed"},
              {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}",
               "name": "lookup", "arguments": arguments, "status": "completed"}]
    return response_body(output) | {"id": f"resp_{number}", "parallel_tool_calls": False}


@unittest.skipUnless(available("openai"), "Optional openai SDK is not installed")
class ResponsesWorkflowSDKTests(unittest.TestCase):
    def setUp(self):
        self.requests, self.effects = [], []

    def client(self, responder):
        import openai
        http = sdk_http("openai")
        def handler(request):
            self.assertEqual(request.url.path, "/v1/responses")
            self.requests.append(json.loads(request.content))
            return http.Response(200, json=responder(len(self.requests)))
        client = openai.OpenAI(api_key="offline-sdk-fixture", base_url="https://relay.invalid/v1",
            http_client=http.Client(transport=http.MockTransport(handler)), max_retries=0,
            _strict_response_validation=True)
        self.addCleanup(client.close)
        return client

    def lookup(self, id):
        self.effects.append(id)
        return {"ok": True, "id": id}

    def workflow(self, agent, callback=None, **options):
        from context_relay import OutcomePolicy
        from context_relay.responses_workflow import ResponsesWorkflow
        return ResponsesWorkflow(agent, {"lookup": callback or self.lookup},
            contracts={"lookup": {"bindings": {"id": "selected_id"},
                                   "outcome": OutcomePolicy(required={"/ok": True})}},
            schemas=copy.deepcopy(SCHEMAS), instructions="Use the registered lookup tool.", **options)

    def test_complete_sdk_roundtrip_replays_opaque_items_and_matching_result(self):
        bodies = [provider_response(1), provider_response(2)]
        original = copy.deepcopy(bodies)
        with auto_connect(self.client(lambda n: bodies[n - 1]), api="responses", native=True) as agent:
            agent.remember("selected_id", 7)
            workflow = self.workflow(agent)
            first = workflow.send("Look up the approved record.", request_id="one", model="fixture-model", store=False)
            second = workflow.send("Read it again.", request_id="two", model="fixture-model", store=False)
            self.assertTrue(first.executed)
            self.assertTrue(first.completed)
            self.assertTrue(second.completed)
            self.assertEqual(workflow.request("one")["phase"], "completed")
            history = workflow.history
            history.clear()
            self.assertTrue(workflow.history)
        self.assertEqual(self.effects, [7, 7])
        self.assertEqual(len(self.requests), 2)
        replay = self.requests[1]["input"]
        index = next(i for i, item in enumerate(replay) if item.get("id") == "rs_1")
        self.assertEqual(replay[index:index + 2], original[0]["output"])
        result = replay[index + 2]
        self.assertEqual(result["type"], "function_call_output")
        self.assertEqual(result["call_id"], "call_1")
        envelope = json.loads(result["output"])
        self.assertTrue(envelope["completed"])
        self.assertEqual(envelope["result"], {"ok": True, "id": 7})
        self.assertEqual(envelope["checked_arguments"], {"id": 7})
        self.assertEqual(bodies, original)
        self.assertEqual(self.requests[0]["tools"], SCHEMAS)
        self.assertFalse(self.requests[0]["store"])

    def test_known_repair_keeps_original_sdk_call_and_runs_correct_argument_once(self):
        original = provider_response(1, '{ "id": 999 }')
        with auto_connect(self.client(lambda n: original), api="responses", native=True) as agent:
            agent.remember("selected_id", 7)
            workflow = self.workflow(agent, repair="known")
            result = workflow.send("Read the permitted record.", request_id="repair", model="fixture-model", store=False)
            self.assertTrue(result.completed)
            self.assertEqual(result.execution["repair_attempts"], 1)
            native_call = next(item for item in workflow.history if item.get("type") == "function_call")
            self.assertEqual(native_call["arguments"], '{ "id": 999 }')
            self.assertEqual(native_call["call_id"], "call_1")
        self.assertEqual(self.effects, [7])
        self.assertEqual(len(self.requests), 1)

    def test_domain_failure_is_executed_but_not_successful_work(self):
        def lookup(id):
            self.effects.append(id)
            return {"ok": False, "reason": "Record unavailable"}
        with auto_connect(self.client(provider_response), api="responses", native=True) as agent:
            agent.remember("selected_id", 7)
            workflow = self.workflow(agent, lookup)
            result = workflow.send("Read the approved record.", request_id="domain-failure", model="fixture-model")
            self.assertTrue(result.executed)
            self.assertFalse(result.completed)
            self.assertEqual(result.business_status, "not_satisfied")
            self.assertEqual(workflow.request("domain-failure")["phase"], "completed")
            replay = workflow.send("Read the approved record.", request_id="domain-failure", model="fixture-model")
            self.assertTrue(replay.replayed)
            self.assertFalse(replay.completed)
        self.assertEqual(self.effects, [7])
        self.assertEqual(len(self.requests), 1)

    def test_named_restart_resumes_history_and_completed_id_without_reexecution(self):
        client = self.client(provider_response)
        with tempfile.TemporaryDirectory() as directory:
            options = {"api": "responses", "native": True, "conversation": "sdk-workflow",
                       "storage": Path(directory)}
            with auto_connect(client, **options) as agent:
                agent.remember("selected_id", 7)
                workflow = self.workflow(agent)
                first = workflow.send("Read the approved record.", request_id="one", model="fixture-model", store=False)
                session_id, history = agent.session_id, workflow.history
                self.assertTrue(first.completed)
            with auto_connect(client, **options) as agent:
                self.assertEqual(agent.session_id, session_id)
                workflow = self.workflow(agent)
                replay = workflow.send("Read the approved record.", request_id="one", model="fixture-model", store=False)
                self.assertTrue(replay.replayed)
                self.assertEqual(workflow.history, history)
                self.assertEqual(len(self.requests), 1)
                second = workflow.send("Read another approved record.", request_id="two", model="fixture-model", store=False)
                self.assertTrue(second.completed)
        self.assertEqual(self.effects, [7, 7])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[1]["input"][:len(history)], history)

    def test_permission_revision_changed_during_sdk_dispatch_blocks_action(self):
        def responder(number):
            agent.remember("selected_id", 8)
            return provider_response(number)
        with auto_connect(self.client(responder), api="responses", native=True) as agent:
            agent.remember("selected_id", 7)
            workflow = self.workflow(agent)
            result = workflow.send("Read the approved record.", request_id="stale", model="fixture-model")
            self.assertFalse(result.executed)
            self.assertFalse(result.completed)
            self.assertEqual(workflow.request("stale")["phase"], "completed")
        self.assertEqual(self.effects, [])
        self.assertEqual(len(self.requests), 1)

    def test_sdk_timeout_is_pending_and_restart_never_reissues_provider_request(self):
        import openai
        http = sdk_http("openai")
        def timeout(number):
            raise http.ReadTimeout("Simulated intercepted transport timeout")
        client = self.client(timeout)
        with tempfile.TemporaryDirectory() as directory:
            options = {"api": "responses", "native": True, "conversation": "pending-sdk",
                       "storage": Path(directory)}
            with auto_connect(client, **options) as agent:
                agent.remember("selected_id", 7)
                workflow = self.workflow(agent)
                with self.assertRaises(openai.APITimeoutError):
                    workflow.send("Read the approved record.", request_id="timeout", model="fixture-model")
                self.assertEqual(workflow.pending["phase"], "provider_pending")
            with auto_connect(client, **options) as agent:
                workflow = self.workflow(agent)
                self.assertEqual(workflow.pending["phase"], "provider_pending")
                self.assertEqual(workflow.request("timeout")["phase"], "provider_pending")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.effects, [])

    def test_sdk_parses_compaction_but_unsupported_mixed_action_fails_closed(self):
        from context_relay import ActionProposalError
        body = provider_response(1)
        body["output"].insert(1, {"type": "compaction", "id": "cmp_1", "encrypted_content": "MOCK-compaction=="})
        with auto_connect(self.client(lambda n: body), api="responses", native=True) as agent:
            agent.remember("selected_id", 7)
            workflow = self.workflow(agent)
            with self.assertRaises(ActionProposalError):
                workflow.send("Read the approved record.", request_id="compaction", model="fixture-model")
            self.assertIsNone(workflow.pending)
            record = workflow.request("compaction")
            self.assertEqual(record["phase"], "completed")
            self.assertEqual(record["result"]["status"], "invalid_proposal")
            self.assertFalse(record["result"]["completed"])
            self.assertFalse(record["result"]["executed"])
            self.assertFalse(any(item.get("type") in {"compaction", "function_call"}
                                 for item in workflow.history))
        self.assertEqual(self.effects, [])
        self.assertEqual(len(self.requests), 1)

    def test_effect_interrupt_restart_reconciles_without_replaying_callback(self):
        from context_relay.responses_workflow import WorkflowPending
        def interrupted_lookup(id):
            self.effects.append(id)
            if len(self.effects) == 1:
                raise KeyboardInterrupt("Simulated interruption after the effect")
            return {"ok": True, "id": id}
        client = self.client(provider_response)
        with tempfile.TemporaryDirectory() as directory:
            options = {"api": "responses", "native": True, "conversation": "interrupted-sdk",
                       "storage": Path(directory)}
            with auto_connect(client, **options) as agent:
                agent.remember("selected_id", 7)
                workflow = self.workflow(agent, interrupted_lookup)
                with self.assertRaises(KeyboardInterrupt):
                    workflow.send("Read the approved record.", request_id="interrupted", model="fixture-model")
                self.assertEqual(workflow.pending["phase"], "action_pending")
            with auto_connect(client, **options) as agent:
                workflow = self.workflow(agent, interrupted_lookup)
                with self.assertRaises(WorkflowPending):
                    workflow.send("Read the approved record.", request_id="interrupted", model="fixture-model")
                self.assertEqual(self.effects, [7])
                self.assertEqual(len(self.requests), 1)
                # This fixture stands for the host's destination lookup, not
                # an authenticated receipt or an inferred success by Relay.
                result = workflow.reconcile("interrupted", result={"ok": True, "id": 7},
                                            expected_phase="action_pending")
                self.assertTrue(result.completed)
                self.assertFalse(result.executed)
                self.assertFalse(result.execution["callback_reexecuted"])
                self.assertIsNone(workflow.pending)
                replay = workflow.send("Read the approved record.", request_id="interrupted", model="fixture-model")
                self.assertTrue(replay.replayed)
                self.assertEqual(self.effects, [7])
                self.assertEqual(len(self.requests), 1)
                followup = workflow.send("Read it once more.", request_id="next", model="fixture-model")
                self.assertTrue(followup.completed)
        self.assertEqual(self.effects, [7, 7])
        self.assertEqual(len(self.requests), 2)
        outputs = [item for item in self.requests[1]["input"]
                   if item.get("type") == "function_call_output"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["call_id"], "call_1")
        self.assertTrue(json.loads(outputs[0]["output"])["reconciled"])
        self.assertTrue(json.loads(outputs[0]["output"])["completed"])


if __name__ == "__main__":
    unittest.main()
