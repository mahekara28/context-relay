"""Independent action-harness attacks with literal local outcome oracles."""

import asyncio
import unittest

from context_relay import ActionRejected, ToolExecutionError, auto_connect
from context_relay.actions import ActionHarness, ActionProposalError, ActionResult


HISTORY = [{"role": "user", "content": "Export as JSON. Reply in Hindi."}]


class ActionHarnessAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.effects = []
        self.provider_calls = []

    def connect(self, response, *, during_call=None):
        def provider(*, messages, **options):
            self.provider_calls.append((messages, options))
            if during_call is not None:
                during_call()
            return response
        agent = auto_connect(provider)
        self.addCleanup(agent.close)
        return agent

    def refund(self, amount):
        self.effects.append(amount)
        return {"refunded": amount}

    def basic(self, response, **options):
        agent = self.connect(response)
        harness = ActionHarness(agent, tools={"refund": self.refund},
                                contracts={"refund": {"unchecked": ("amount",)}}, **options)
        return agent, harness

    def test_one_canonical_proposal_runs_once_and_report_is_a_copy(self):
        response = {"tool": "refund", "arguments": {"amount": 20}}
        agent, harness = self.basic(response)
        result = harness.invoke(messages=HISTORY, model="offline-fixture")
        self.assertIsInstance(result, ActionResult)
        self.assertIs(result.agent_result.response, response)
        self.assertTrue(result.executed)
        self.assertEqual(self.effects, [20])
        self.assertEqual(len(self.provider_calls), 1)
        self.assertEqual(self.provider_calls[0][1], {"model": "offline-fixture"})
        report = result.execution
        report["executed"] = False
        self.assertTrue(result.executed)
        self.assertTrue(result.execution["executed"])
        with self.assertRaises(ActionRejected):
            result.agent_result.prepared.wrap(self.refund, tool="refund")(20)
        self.assertEqual(self.effects, [20])

    def test_conventional_memory_fields_are_enrolled_without_contracts(self):
        def export(format, language):
            self.effects.append((format, language))
            return {"saved": True}
        agent = self.connect('{"tool":"export","arguments":{"format":"json","language":"hindi"}}')
        harness = ActionHarness(agent, tools={"export": export})
        result = harness.invoke(messages=HISTORY)
        self.assertTrue(result.executed)
        self.assertEqual(self.effects, [("JSON", "Hindi")])
        checked = harness.coverage()["tools"]["export"]["checked"]
        self.assertEqual(checked, {"format": {"memory": "export_format"},
                                   "language": {"memory": "response_language"}})

    def test_omitted_default_is_checked_before_tool_runs(self):
        def export(format="CSV"):
            self.effects.append(format)
            return format
        agent = self.connect({"tool": "export", "arguments": {}})
        harness = ActionHarness(agent, tools={"export": export})
        result = harness.invoke(messages=HISTORY)
        self.assertFalse(result.executed)
        self.assertEqual(self.effects, [])
        self.assertIn("memory_binding_mismatch", {
            issue["code"] for issue in result.execution["final_check"]["issues"]})

    def test_uncovered_default_parameter_rejects_whole_inventory_atomically(self):
        agent = self.connect({"tool": "first", "arguments": {}})
        before = agent.guard.store.get_session(agent.session_id)["revision"]
        before_events = agent.guard.store.events(agent.session_id)
        def first():
            self.effects.append("first")
        def second(recipient="unreviewed@example.test"):
            self.effects.append(recipient)
        with self.assertRaises((TypeError, ValueError)):
            ActionHarness(agent, tools={"first": first, "second": second})
        self.assertEqual(agent.guard.store.policies(agent.session_id), [])
        self.assertEqual(agent.guard.store.get_session(agent.session_id)["revision"], before)
        self.assertEqual(agent.guard.store.events(agent.session_id), before_events)
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.effects, [])

    def test_atomic_setup_keeps_preexisting_unrelated_policy(self):
        agent = self.connect({"tool": "first", "arguments": {}})
        agent.guard.allow_tool("existing", optional_args=("value",))
        before = agent.guard.store.policies(agent.session_id)
        revision = agent.guard.turn().revision
        with self.assertRaises((TypeError, ValueError)):
            ActionHarness(agent, tools={"first": lambda: None, "bad": lambda amount: None})
        self.assertEqual(agent.guard.store.policies(agent.session_id), before)
        self.assertEqual(agent.guard.turn().revision, revision)

    def test_fact_changed_during_provider_call_does_not_refresh_permission(self):
        agent = None
        def change_state():
            agent.guard.remember("approved_amount", 20)
        agent = self.connect({"tool": "refund", "arguments": {"amount": 20}},
                             during_call=change_state)
        agent.guard.remember("approved_amount", 10)
        harness = ActionHarness(agent, tools={"refund": self.refund},
                                contracts={"refund": {"bindings": {"amount": "approved_amount"}}})
        result = harness.invoke(messages=HISTORY)
        self.assertFalse(result.executed)
        self.assertIn("stale_revision", {
            issue["code"] for issue in result.execution["final_check"]["issues"]})
        self.assertEqual(self.effects, [])
        self.assertEqual(len(self.provider_calls), 1)

    def test_wrong_fact_proposal_remains_blocked_without_implicit_repair(self):
        agent = self.connect({"tool": "refund", "arguments": {"amount": 2000}})
        agent.guard.remember("approved_amount", 20)
        harness = ActionHarness(agent, tools={"refund": self.refund},
                                contracts={"refund": {"bindings": {"amount": "approved_amount"}}})
        result = harness.invoke(messages=HISTORY)
        self.assertFalse(result.executed)
        self.assertEqual(result.execution["repair_attempts"], 0)
        self.assertEqual(self.effects, [])

    def test_changed_policy_during_model_call_cannot_weaken_registered_contract(self):
        agent = None
        def weaken_policy():
            agent.guard.allow_tool("refund", optional_args=("amount",))
        agent = self.connect({"tool": "refund", "arguments": {"amount": 2000}},
                             during_call=weaken_policy)
        agent.guard.remember("approved_amount", 20)
        harness = ActionHarness(agent, tools={"refund": self.refund},
                                contracts={"refund": {"bindings": {"amount": "approved_amount"}}})
        with self.assertRaises(ValueError):
            harness.invoke(messages=HISTORY)
        self.assertEqual(self.effects, [])
        self.assertEqual(len(self.provider_calls), 1)

    def test_stream_request_refused_before_provider(self):
        _, harness = self.basic({"tool": "refund", "arguments": {"amount": 20}})
        with self.assertRaises(ValueError):
            harness.invoke(messages=HISTORY, stream=True)
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.effects, [])

    def test_unknown_tool_arguments_and_mixed_proposals_never_execute(self):
        responses = [
            {"tool": "delete_account", "arguments": {"amount": 20}},
            {"tool": "refund", "arguments": {"amount": 20, "recipient": "attacker"}},
            {"tool": "refund", "arguments": {"amount": 20}, "explanation": "Also approve more"},
            [{"tool": "refund", "arguments": {"amount": 20}}],
            {"tool": "refund", "arguments": [{"amount": 20}]},
            {"tool": "", "arguments": {}},
            {"tool": "refund"},
            {"arguments": {"amount": 20}},
            {"tool": "refund", "arguments": {"amount": float("nan")}},
            {"tool": "refund", "arguments": {"amount": float("inf")}},
            {"tool_calls": [{"name": "refund", "arguments": {"amount": 20}}]},
        ]
        for response in responses:
            with self.subTest(response=response):
                _, harness = self.basic(response)
                with self.assertRaises(ActionProposalError):
                    harness.invoke(messages=HISTORY)
        self.assertEqual(self.effects, [])
        self.assertEqual(len(self.provider_calls), len(responses))

    def test_duplicate_nonfinite_deep_and_lossy_raw_json_rejected(self):
        responses = [
            '{"tool":"refund","tool":"refund","arguments":{"amount":20}}',
            '{"tool":"refund","arguments":{"amount":2000,"amount":20}}',
            '{"tool":"refund","arguments":{"amount":NaN}}',
            '{"tool":"refund","arguments":{"amount":Infinity}}',
            '{"tool":"refund","arguments":{"amount":0.10000000000000001}}',
            '{"tool":"refund","arguments":{"amount":1e-10000}}',
            '{"tool":"refund","arguments":{"amount":' + '[' * 70 + '20' + ']' * 70 + '}}',
            '{"tool":"refund","arguments":{"amount":"' + 'x' * 64001 + '"}}',
            '{"tool":"refund","arguments":{"amount":20}} trailing text',
        ]
        for index, response in enumerate(responses):
            with self.subTest(index=index):
                _, harness = self.basic(response)
                with self.assertRaises(ActionProposalError):
                    harness.invoke(messages=HISTORY)
        self.assertEqual(self.effects, [])

    def test_exact_large_integer_and_representable_decimal_are_preserved(self):
        for raw, expected in (("9007199254740993", 9007199254740993), ("0.1", 0.1)):
            with self.subTest(raw=raw):
                _, harness = self.basic('{"tool":"refund","arguments":{"amount":' + raw + '}}')
                result = harness.invoke(messages=HISTORY)
                self.assertTrue(result.executed)
                self.assertEqual(self.effects[-1], expected)
                self.assertIs(type(self.effects[-1]), type(expected))

    def test_custom_decoder_cannot_bypass_strict_shape_or_number_checks(self):
        envelope = object()
        for decoded in (
            {"tool": "refund", "arguments": {"amount": float("nan")}},
            {"tool": "refund", "arguments": {"amount": 20}, "second_tool": "refund"},
            '{"tool":"refund","arguments":{"amount":2000,"amount":20}}',
        ):
            with self.subTest(decoded=decoded):
                _, harness = self.basic(envelope, decoder=lambda ignored: decoded)
                with self.assertRaises(ActionProposalError):
                    harness.invoke(messages=HISTORY)
        self.assertEqual(self.effects, [])

    def test_native_envelope_requires_explicit_decoder(self):
        envelope = {"provider": "fixture", "result": {"tool": "refund", "arguments": {"amount": 20}}}
        _, unsupported = self.basic(envelope)
        with self.assertRaises(ActionProposalError):
            unsupported.invoke(messages=HISTORY)
        _, supported = self.basic(envelope, decoder=lambda response: response["result"])
        self.assertTrue(supported.invoke(messages=HISTORY).executed)
        self.assertEqual(self.effects, [20])

    def test_async_decoder_is_rejected_before_any_model_request(self):
        async def decoder(response):
            return response
        agent = self.connect({"tool": "refund", "arguments": {"amount": 20}})
        with self.assertRaises((TypeError, ValueError)):
            ActionHarness(agent, tools={"refund": self.refund},
                          contracts={"refund": {"unchecked": ("amount",)}}, decoder=decoder)
        self.assertEqual(agent.guard.store.policies(agent.session_id), [])
        self.assertEqual(self.provider_calls, [])
        self.assertEqual(self.effects, [])

    def test_inventory_defaults_and_contracts_are_snapshotted(self):
        default = ["original"]
        def export(rows=default):
            self.effects.append(rows)
            return rows
        inventory = {"export": export}
        contract = {"export": {"unchecked": ["rows"]}}
        agent = self.connect({"tool": "export", "arguments": {}})
        harness = ActionHarness(agent, tools=inventory, contracts=contract)
        inventory["export"] = lambda rows: self.effects.append("replaced")
        default.append("late mutation")
        contract["export"]["unchecked"].clear()
        self.assertTrue(harness.invoke(messages=HISTORY).executed)
        self.assertEqual(self.effects, [["original"]])

    def test_coverage_is_explicit_about_direct_python_calls(self):
        _, harness = self.basic({"tool": "refund", "arguments": {"amount": 20}})
        coverage = harness.coverage()
        self.assertTrue(coverage["inventory_fully_registered"])
        self.assertFalse(coverage["direct_calls_protected"])
        self.assertFalse(coverage["semantic_meaning_checked"])
        self.assertEqual(coverage["tools"]["refund"]["unchecked"], ["amount"])
        coverage["tools"]["refund"]["unchecked"].append("other")
        self.assertEqual(harness.coverage()["tools"]["refund"]["unchecked"], ["amount"])
        self.refund(2000)
        self.assertEqual(self.effects, [2000])  # Direct host code is outside this boundary.

    def test_provider_failure_never_calls_tools(self):
        def fail():
            raise RuntimeError("synthetic provider failure")
        agent = self.connect({"tool": "refund", "arguments": {"amount": 20}}, during_call=fail)
        harness = ActionHarness(agent, tools={"refund": self.refund},
                                contracts={"refund": {"unchecked": ("amount",)}})
        with self.assertRaisesRegex(RuntimeError, "synthetic provider failure"):
            harness.invoke(messages=HISTORY)
        self.assertEqual(self.effects, [])
        self.assertEqual(len(self.provider_calls), 1)

    def test_failure_after_effect_never_invokes_repair(self):
        repairs = []
        def fail(amount):
            self.effects.append(amount)
            raise RuntimeError("effect already happened")
        agent = self.connect({"tool": "refund", "arguments": {"amount": 20}})
        harness = ActionHarness(agent, tools={"refund": fail},
                                contracts={"refund": {"unchecked": ("amount",)}},
                                repair=lambda request: repairs.append(request))
        with self.assertRaises(ToolExecutionError):
            harness.invoke(messages=HISTORY)
        self.assertEqual(self.effects, [20])
        self.assertEqual(repairs, [])
        self.assertEqual(len(self.provider_calls), 1)
        receipts = [event for event in agent.guard.store.events(agent.session_id)
                    if event["kind"] == "tool_result"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["status"], "failed")

    def test_async_provider_binds_its_own_captured_turn(self):
        async def scenario():
            async def provider(*, messages, **kwargs):
                self.provider_calls.append(messages)
                await asyncio.sleep(0)
                return '{"tool":"refund","arguments":{"amount":20}}'
            agent = auto_connect(provider)
            self.addCleanup(agent.close)
            harness = ActionHarness(agent, tools={"refund": self.refund},
                                    contracts={"refund": {"unchecked": ("amount",)}})
            result = await harness.invoke(messages=HISTORY)
            self.assertTrue(result.executed)
            self.assertEqual(self.effects, [20])
            self.assertEqual(len(self.provider_calls), 1)
            with self.assertRaises(ActionRejected):
                result.agent_result.prepared.wrap(self.refund, tool="refund")(20)
            self.assertEqual(self.effects, [20])
        asyncio.run(scenario())

    def test_async_provider_state_change_is_stale(self):
        async def scenario():
            agent = None
            async def provider(*, messages):
                await asyncio.sleep(0)
                agent.guard.remember("amount", 20)
                return {"tool": "refund", "arguments": {"amount": 20}}
            agent = auto_connect(provider)
            self.addCleanup(agent.close)
            agent.guard.remember("amount", 10)
            harness = ActionHarness(agent, tools={"refund": self.refund},
                                    contracts={"refund": {"bindings": {"amount": "amount"}}})
            result = await harness.invoke(messages=HISTORY)
            self.assertFalse(result.executed)
            self.assertIn("stale_revision", {
                issue["code"] for issue in result.execution["final_check"]["issues"]})
            self.assertEqual(self.effects, [])
        asyncio.run(scenario())

    def test_variadic_and_async_tools_fail_setup_without_provider_call(self):
        async def asynchronous(amount):
            self.effects.append(amount)
        for function in (lambda **kwargs: None, asynchronous):
            with self.subTest(function=function):
                agent = self.connect({"tool": "refund", "arguments": {"amount": 20}})
                revision = agent.guard.turn().revision
                with self.assertRaises((TypeError, ValueError)):
                    ActionHarness(agent, tools={"refund": function},
                                  contracts={"refund": {"unchecked": ("amount",)}})
                self.assertEqual(agent.guard.turn().revision, revision)
                self.assertEqual(agent.guard.store.policies(agent.session_id), [])
        self.assertEqual(self.effects, [])
        self.assertEqual(self.provider_calls, [])


if __name__ == "__main__":
    unittest.main()
