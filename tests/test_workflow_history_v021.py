"""Local exact native handoff, durable identities and bounded failure cases."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from context_relay import RelayConfig, TokenBudget, auto_connect
from context_relay.budget import TokenBudgetExceeded
from context_relay.models import BudgetExceeded, ConflictError, encode
from context_relay.outcomes import OutcomePolicy
from context_relay.responses_workflow import ResponsesWorkflow, WorkflowPending, WorkflowLimitExceeded
from context_relay.workflow_history import WorkflowHistoryPolicy
from test_real_sdks import available, response_body, sdk_http


class WorkflowHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / "history.sqlite3")
        self.calls, self.effects, self.responses = [], [], []
        self.hook = self.effect_hook = None
        self.proposed = 7
        self.schemas = {"write": {"type": "object", "properties": {"value": {"type": "integer"}},
            "required": ["value"], "additionalProperties": False}}
        self.contracts = {"write": {"bindings": {"value": "approved_value"},
            "outcome": OutcomePolicy(required={"/ok": True})}}
        self.policy = WorkflowHistoryPolicy(rollover_after_chars=2200)
        self.agent = self.connect()
        self.agent.remember("approved_value", 7)

    def connect(self, session_id=None, *, token_budget=None, max_chars=12000):
        agent = auto_connect(SimpleNamespace(responses=SimpleNamespace(create=self.provider)), provider="openai",
            api="responses", native=True, config=RelayConfig(db_path=self.path, max_chars=max_chars),
            session_id=session_id, token_budget=token_budget)
        self.addCleanup(agent.close)
        return agent

    def provider(self, **request):
        self.calls.append(copy.deepcopy(request))
        if self.hook:
            self.hook()
        index = len(self.calls)
        response = {"status": "completed", "store": False, "presence_penalty": 0, "frequency_penalty": 0,
            "parallel_tool_calls": True, "output": [
                {"type": "reasoning", "id": f"rs_{index}", "summary": [], "status": "completed",
                 "encrypted_content": f"OPAQUE-{index}:" + "x" * 900},
                {"type": "function_call", "id": f"fc_{index}", "call_id": f"call_{index}",
                 "name": "write", "arguments": json.dumps({"value": self.proposed}), "status": "completed"}]}
        self.responses.append(copy.deepcopy(response))
        return response

    def write(self, value):
        self.effects.append(value)
        if self.effect_hook:
            self.effect_hook()
        return {"ok": True, "value": value}

    def workflow(self, agent=None, **options):
        return ResponsesWorkflow(agent or self.agent, {"write": self.write}, contracts=self.contracts,
            schemas=self.schemas, history_policy=options.pop("history_policy", self.policy), **options)

    def send(self, workflow, number, text="Continue."):
        return workflow.send(text, request_id=f"r{number}", model="local-fixture", store=False)

    def test_long_run_restart_exact_archive_metadata_repair_and_no_duplicate_effect(self):
        self.proposed = 99
        workflow = self.workflow()
        original_task = "Compare records INV-100 and INV-209; preserve their amounts and explain remaining differences."
        self.send(workflow, 0, original_task)
        first_history = workflow.history
        for number in range(1, 20):
            result = self.send(workflow, number)
            self.assertTrue(result.completed)
            self.assertEqual(result.execution["repair_attempts"], 1)
            self.assertEqual(result.response, self.responses[-1])
        status = workflow.history_status()
        self.assertGreater(status["archive"]["segments"], 10)
        self.assertFalse(status["archive_included_in_input"])
        self.assertFalse(status["reasoning_continuity_preserved"])
        first_archive = workflow.read_archive(0)
        self.assertEqual(first_archive["history"], first_history)
        self.assertEqual(first_archive["sha256"], hashlib.sha256(encode(first_history).encode()).hexdigest())
        self.assertEqual(first_archive["history"][-3:-1], self.responses[0]["output"])
        output = json.loads(first_archive["history"][-1]["output"])
        self.assertEqual(output["checked_arguments"], {"value": 7})
        self.assertEqual(output["repair_attempts"], 1)
        checkpoint = json.loads(workflow.history[0]["content"].split("\n", 1)[1])
        self.assertEqual(checkpoint["task"]["requests"][0]["text"], original_task)
        self.assertFalse(checkpoint["task"]["completion_inferred"])
        self.assertFalse(checkpoint["model_summary_used"])
        self.assertEqual(checkpoint["task"]["requests"][0]["action"]["business_completed"], True)
        original_history = workflow.history
        self.agent.close()
        resumed = self.workflow(self.connect(workflow.session_id))
        self.assertEqual(resumed.history, original_history)
        replay = self.send(resumed, 0, original_task)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.response, self.responses[0])
        self.assertEqual(len(self.effects), 20)
        for number in range(20, 30):
            self.assertTrue(self.send(resumed, number).completed)
        self.assertEqual(len(self.calls), 30)
        self.assertEqual(self.effects, [7] * 30)
        self.assertTrue(all(len(encode(call)) <= 12000 for call in self.calls))
        # Each fresh request omits opaque earlier blocks, while the archive has
        # their exact content. Native continuity is explicitly not claimed.
        self.assertFalse(any(item.get("id") == "rs_1" for item in self.calls[-1]["input"]))

    def test_token_budget_triggers_handoff_and_validates_the_whole_fresh_request(self):
        counter = lambda messages: len(encode(messages[0]))
        budget = TokenBudget(4500, counter, counter_name="serialized-characters-test-counter")
        agent = self.connect(token_budget=budget)
        agent.remember("approved_value", 7)
        workflow = self.workflow(agent, history_policy=WorkflowHistoryPolicy(rollover_after_chars=20000))
        for number in range(7):
            self.assertTrue(self.send(workflow, number).completed)
        self.assertGreater(workflow.history_status()["archive"]["segments"], 0)
        self.assertTrue(all(counter([call]) <= budget.input_limit for call in self.calls))
        self.assertEqual(workflow.history_status()["latest_input_budget"]["input_tokens"], counter([self.calls[-1]]))
        self.assertEqual(workflow.history_status()["token_counter"], budget.counter_name)

    def test_fresh_request_that_cannot_fit_does_not_commit_an_archive_or_call_provider(self):
        workflow = self.workflow()
        self.send(workflow, 0, "Keep task ABC-123 open.")
        original = workflow.history
        with self.assertRaises(BudgetExceeded):
            self.send(workflow, 1, "Details " + "z" * 11900)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(workflow.history_status()["archive"]["segments"], 0)
        self.assertEqual(workflow.history[:len(original)], original)
        self.assertEqual(workflow.request("r1")["result"]["status"], "preparation_failed")
        self.assertIsNone(workflow.pending)

    def test_archive_capacity_stops_without_eviction_or_a_new_effect(self):
        workflow = self.workflow(history_policy=WorkflowHistoryPolicy(rollover_after_chars=2200, max_segments=1))
        self.send(workflow, 0)
        self.send(workflow, 1)
        archive = workflow.read_archive(0)
        with self.assertRaises(WorkflowLimitExceeded):
            self.send(workflow, 2)
        self.assertEqual(workflow.read_archive(0), archive)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.effects), 2)
        self.assertIsNone(workflow.pending)

    def test_archive_character_bound_and_complete_read_refuse_truncation(self):
        tiny = self.workflow(history_policy=WorkflowHistoryPolicy(rollover_after_chars=2200, max_archive_chars=256))
        self.send(tiny, 0)
        with self.assertRaises(WorkflowLimitExceeded):
            self.send(tiny, 1)
        self.assertEqual(tiny.archive_segments()["segments"], [])
        self.assertEqual(len(self.effects), 1)

    def test_pending_provider_restart_cannot_rotate_retry_or_edit_the_task(self):
        workflow = self.workflow()
        self.send(workflow, 0, "Compare ID-A with ID-B.")
        def interrupted():
            raise TimeoutError("unknown provider result")
        self.hook = interrupted
        with self.assertRaises(TimeoutError):
            self.send(workflow, 1)
        before = workflow.history_status()
        restarted = self.workflow(self.connect(self.agent.session_id))
        for number in (1, 2):
            with self.assertRaises(WorkflowPending):
                self.send(restarted, number)
        with self.assertRaises(WorkflowPending):
            restarted.finish_task(expected_revision=restarted.task_snapshot()["revision"])
        self.assertEqual(restarted.history_status(), before)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.effects), 1)

    def test_interrupted_action_reconciles_once_before_any_further_handoff(self):
        workflow = self.workflow()
        self.send(workflow, 0)
        def interrupted():
            raise KeyboardInterrupt("after destination effect")
        self.effect_hook = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.send(workflow, 1)
        history = workflow.history
        with self.assertRaises(WorkflowPending):
            self.send(workflow, 2)
        self.assertEqual(workflow.history, history)
        restarted = self.workflow(self.connect(self.agent.session_id))
        result = restarted.reconcile("r1", expected_phase="action_pending", result={"ok": True, "value": 7})
        self.assertTrue(result.completed)
        self.effect_hook = None
        self.assertTrue(self.send(restarted, 1).replayed)
        self.assertTrue(self.send(restarted, 2).completed)
        self.assertEqual(self.effects, [7, 7, 7])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(restarted.read_archive(1)["history"][-1]["call_id"], "call_2")

    def test_second_handle_cannot_send_or_finish_during_checkpointed_provider_call(self):
        workflow = self.workflow()
        second = self.workflow(self.connect(self.agent.session_id))
        self.send(workflow, 0)
        def during_provider():
            with self.assertRaises(WorkflowPending):
                self.send(second, 99)
            with self.assertRaises(WorkflowPending):
                second.finish_task(expected_revision=second.task_snapshot()["revision"])
        self.hook = during_provider
        self.assertTrue(self.send(workflow, 1).completed)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.effects, [7, 7])

    def test_permission_revision_changed_after_checkpoint_still_blocks_effect(self):
        workflow = self.workflow()
        self.send(workflow, 0)
        self.hook = lambda: self.agent.remember("approved_value", 8)
        result = self.send(workflow, 1)
        self.assertFalse(result.completed)
        self.assertFalse(result.executed)
        self.assertEqual(self.effects, [7])
        self.assertEqual(workflow.history_status()["archive"]["segments"], 1)

    def test_task_overflow_and_stale_edit_are_atomic_and_completion_is_host_owned(self):
        workflow = self.workflow(history_policy=WorkflowHistoryPolicy(max_task_updates=1))
        self.send(workflow, 0, "Compare IDs 25 and 28.")
        before = workflow.task_snapshot()
        with self.assertRaises(WorkflowLimitExceeded):
            self.send(workflow, 1, "Also preserve the source dates.")
        self.assertEqual(workflow.task_snapshot(), before)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ConflictError):
            workflow.update_task("Compare 25 and 28; preserve dates.", expected_revision=before["revision"] - 1)
        updated = workflow.update_task("Compare 25 and 28; preserve dates.", expected_revision=before["revision"])
        self.assertEqual(updated["task"]["requests"][0]["source"]["kind"], "application")
        finished = workflow.finish_task(expected_revision=updated["revision"])
        self.assertEqual(finished["task"]["status"], "finished")
        self.send(workflow, 1, "New task for ID 77.")
        self.assertNotEqual(workflow.task_snapshot()["task"]["task_id"], finished["task"]["task_id"])

    def test_scoped_preferences_and_disputes_survive_without_reprocessing_checkpoints(self):
        workflow = self.workflow()
        self.send(workflow, 0, "Export as CSV.")
        self.send(workflow, 1, "For this export only, use JSON.")
        result = self.send(workflow, 2, "Continue.")
        self.assertEqual(result.status, "clarification_required")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(workflow._memory.snapshot()["entries"][0]["value"], "CSV")
        resumed = self.workflow(self.connect(self.agent.session_id))
        result = self.send(resumed, 3, "New export: use CSV.")
        self.assertTrue(result.completed)
        self.assertEqual(resumed._memory.effective_snapshot()["entries"][0]["value"], "CSV")

    def test_task_consolidation_forces_new_checkpoint_without_rewriting_sent_history(self):
        workflow = self.workflow(history_policy=WorkflowHistoryPolicy(rollover_after_chars=20000))
        workflow.update_task("Keep IDs 25 and 28 exact.", expected_revision=workflow.task_snapshot()["revision"])
        self.send(workflow, 0)
        old_history = workflow.history
        self.assertIn("Keep IDs 25 and 28 exact.", old_history[0]["content"])
        workflow.update_task("The remaining task is ID 28 only.", expected_revision=workflow.task_snapshot()["revision"])
        self.send(workflow, 1)
        self.assertEqual(workflow.read_archive(0)["history"], old_history)
        self.assertIn("The remaining task is ID 28 only.", workflow.history[0]["content"])
        self.assertNotIn("Keep IDs 25 and 28 exact.", workflow.history[0]["content"])

    def test_archive_pages_and_complete_reads_are_defensive(self):
        workflow = self.workflow()
        for number in range(4):
            self.send(workflow, number)
        page = workflow.archive_segments(limit=1)
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_after"], 0)
        self.assertNotIn("history", page["segments"][0])
        self.assertEqual(workflow.archive_segments(after=page["next_after"], limit=1)["segments"][0]["segment_id"], 1)
        with self.assertRaises(WorkflowLimitExceeded):
            workflow.read_archive(0, max_chars=256)
        archive = workflow.read_archive(0)
        archive["history"].clear()
        self.assertTrue(workflow.read_archive(0)["history"])

    def test_memory_policy_configuration_is_preserved_on_resume(self):
        from context_relay.memory import MemoryPolicy
        policy = MemoryPolicy(max_new_messages=2)
        workflow = self.workflow(memory=policy)
        self.send(workflow, 0, "Export as CSV.")
        resumed = self.workflow(self.connect(self.agent.session_id), memory=policy)
        self.assertTrue(self.send(resumed, 1).completed)
        self.assertEqual(resumed._memory.policy, policy)
        with self.assertRaisesRegex(ValueError, "identical configuration"):
            self.workflow(memory=True)

    def test_explicit_review_resume_keeps_one_original_user_across_handoff_and_restart(self):
        from context_relay.memory import MemoryPolicy
        policy = MemoryPolicy(assurance_fields=("export_format",))
        workflow = self.workflow(memory=policy)
        first = self.send(workflow, 0, "Export as CSV.")
        self.assertEqual(first.status, "review_required")
        self.assertEqual(self.calls, [])
        workflow.resolve_review(workflow.review_plan(), decisions={"export_format": {"value": "CSV", "scope": "durable"}})
        self.assertTrue(workflow.resume_review("r0").completed)
        second = self.send(workflow, 1, "Keep the existing format.")
        self.assertEqual(second.status, "review_required")
        self.assertEqual(len(self.calls), 1)
        resumed = self.workflow(self.connect(self.agent.session_id), memory=policy)
        resumed.resolve_review(resumed.review_plan(), decisions={"export_format": {"value": "CSV", "scope": "durable"}})
        self.assertTrue(resumed.resume_review("r1").completed)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.effects, [7, 7])
        self.assertEqual(resumed._memory.review_plan()["processed_count"], 2)
        self.assertGreater(resumed.history_status()["archive"]["segments"], 0)
        self.assertTrue(self.send(resumed, 1, "Keep the existing format.").replayed)
        with self.assertRaises(ValueError):
            resumed.resume_review("r1")

    def test_deletion_includes_archives_stale_plan_and_sibling_isolation(self):
        workflow = self.workflow()
        sibling_agent = self.connect()
        sibling_agent.remember("approved_value", 7)
        sibling = self.workflow(sibling_agent)
        self.send(sibling, 0)
        sibling_history = sibling.history
        self.send(workflow, 1)
        stale = workflow.deletion_plan()
        self.send(workflow, 2)
        self.assertGreater(workflow.deletion_plan()["archive_count"], 0)
        with self.assertRaises(ValueError):
            workflow.delete_history(expected=stale)
        counts = workflow.delete_history(expected=workflow.deletion_plan())["deleted"]
        self.assertEqual(counts["responses_workflow_segments"], 1)
        with self.assertRaises((KeyError, ValueError)):
            workflow.archive_segments()
        self.assertEqual(sibling.history, sibling_history)

    def test_opt_in_profile_cannot_silently_change_or_resume_as_legacy(self):
        workflow = self.workflow()
        self.send(workflow, 0)
        with self.assertRaisesRegex(ValueError, "identical configuration"):
            self.workflow(history_policy=None)
        with self.assertRaisesRegex(ValueError, "identical configuration"):
            self.workflow(history_policy=WorkflowHistoryPolicy(max_segments=20))


@unittest.skipUnless(available("openai"), "Optional openai SDK is not installed")
class WorkflowHistorySDKTests(unittest.TestCase):
    def test_genuine_sdk_sends_fresh_segment_and_named_restart_replays_without_effect(self):
        import openai
        http, requests, responses, effects = sdk_http("openai"), [], [], []
        def handler(request):
            self.assertEqual(request.url.path, "/v1/responses")
            requests.append(json.loads(request.content))
            number = len(requests)
            body = response_body([
                {"type": "reasoning", "id": f"rs_{number}", "summary": [], "status": "completed",
                 "encrypted_content": "FIXTURE-only-" + "x" * 1800},
                {"type": "function_call", "name": "write", "arguments": '{"value":7}',
                 "call_id": f"call_{number}", "id": f"fc_{number}", "status": "completed"}])
            body.update(store=False, presence_penalty=0, frequency_penalty=0, parallel_tool_calls=True)
            responses.append(copy.deepcopy(body))
            return http.Response(200, json=body)
        def write(value):
            effects.append(value)
            return {"ok": True}
        policy = WorkflowHistoryPolicy(rollover_after_chars=2200)
        def workflow(agent):
            return ResponsesWorkflow(agent, {"write": write}, memory=False, history_policy=policy,
                contracts={"write": {"unchecked": ["value"], "outcome": OutcomePolicy(required={"/ok": True})}},
                schemas={"write": {"type": "object", "properties": {"value": {"type": "integer"}},
                    "required": ["value"], "additionalProperties": False}})
        with tempfile.TemporaryDirectory() as directory:
            with openai.OpenAI(api_key="offline-wire-fixture", base_url="https://relay.invalid/v1",
                    http_client=http.Client(transport=http.MockTransport(handler)), max_retries=0,
                    _strict_response_validation=True) as client:
                options = {"api": "responses", "native": True, "storage": Path(directory),
                           "conversation": "continued-sdk", "config": RelayConfig(max_chars=12000)}
                with auto_connect(client, **options) as agent:
                    flow = workflow(agent)
                    first = flow.send("Continue tracking reference REF-24.", request_id="one", model="fixture")
                    exact = flow.history
                    second = flow.send("Continue.", request_id="two", model="fixture")
                    self.assertTrue(first.completed and second.completed)
                    self.assertEqual(flow.read_archive(0)["history"], exact)
                    self.assertEqual(first.response, responses[0])
                    self.assertFalse(any(item.get("type") == "reasoning" for item in requests[1]["input"]))
                    checkpoint = json.loads(requests[1]["input"][0]["content"].split("\n", 1)[1])
                    self.assertFalse(checkpoint["reasoning_continuity_preserved"])
                    self.assertIn("REF-24", checkpoint["task"]["requests"][0]["text"])
                    identity, history = agent.session_id, flow.history
                with auto_connect(client, **options) as agent:
                    self.assertEqual(agent.session_id, identity)
                    resumed = workflow(agent)
                    self.assertEqual(resumed.history, history)
                    replay = resumed.send("Continue tracking reference REF-24.", request_id="one", model="fixture")
                    self.assertTrue(replay.replayed)
                    self.assertEqual(replay.response, responses[0])
        self.assertEqual(effects, [7, 7])
        self.assertEqual(len(requests), 2)


if __name__ == "__main__":
    unittest.main()
