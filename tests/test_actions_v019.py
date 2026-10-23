"""Action harness contracts, recovery and setup boundaries without services."""

import json
import time
import unittest

from context_relay import (ContentPolicy, MemoryPolicy, OutputPolicy, VerificationPolicy, connect)
from context_relay.actions import ActionHarness, ActionProposalError, ActionResult
from context_relay.native import NativeConnection


HISTORY = [{"role": "user", "content": "Export as JSON."}]


class ActionHarnessTests(unittest.TestCase):
    def agent(self, provider, **options):
        agent = connect(provider, **options)
        self.addCleanup(agent.close)
        return agent

    def test_convenience_uses_original_provider_options_response_and_prepared_turn(self):
        calls, writes = [], []
        response = {"tool": "save", "arguments": {"format": "json"}}

        def provider(**options):
            calls.append(options)
            return response

        agent = self.agent(provider, memory=MemoryPolicy())
        harness = agent.protect_tools({"save": lambda format: writes.append(format) or {"ok": True}})
        result = harness.invoke(messages=HISTORY, model="local-fixture", temperature=0)
        self.assertIsInstance(result, ActionResult)
        self.assertIs(result.agent_result.response, response)
        self.assertTrue(result.executed)
        self.assertEqual(writes, ["JSON"])
        self.assertEqual(calls[0]["model"], "local-fixture")
        self.assertEqual(calls[0]["temperature"], 0)
        self.assertEqual(set(calls[0]), {"messages", "model", "temperature"})
        self.assertEqual(result.execution["initial_check"]["revision"], result.agent_result.prepared.revision)
        self.assertEqual(response, {"tool": "save", "arguments": {"format": "json"}})
        report = result.execution
        report["executed"] = False
        self.assertTrue(result.executed)

    def test_known_repair_restores_binding_without_extra_model_call(self):
        requests, charged = [], []
        def provider(**options):
            requests.append(options)
            return {"tool": "charge", "arguments": {"amount": 999, "note": "Keep this note"}}
        agent = self.agent(provider)
        agent.remember("amount", 10)
        harness = ActionHarness(agent, {"charge": lambda amount, note: charged.append((amount, note))},
            contracts={"charge": {"bindings": {"amount": "amount"}, "unchecked": ("note",)}}, repair="known")
        result = harness.invoke(messages=HISTORY)
        self.assertTrue(result.executed)
        self.assertEqual(result.execution["status"], "repaired")
        self.assertEqual(result.execution["repair_attempts"], 1)
        self.assertEqual(result.execution["repair_callback_calls"], 0)
        self.assertEqual(charged, [(10, "Keep this note")])
        self.assertEqual(len(requests), 1)

    def test_content_repair_cannot_change_an_unchecked_note(self):
        writes = []
        agent = self.agent(lambda **kw: {"tool": "save", "arguments": {"name": "Translated", "note": "Keep"}})
        harness = ActionHarness(agent, {"save": lambda name, note: writes.append((name, note))},
            contracts={"save": {"content": ContentPolicy({"name": "Original"}, exact=("/name",)),
                                 "unchecked": ("note",)}},
            repair=lambda request: {"name": "Original", "note": "Changed"})
        result = harness.invoke(messages=HISTORY)
        self.assertFalse(result.executed)
        self.assertEqual(result.execution["status"], "blocked")
        self.assertIn("repair_scope_violation", {row["code"] for row in result.execution["final_check"]["issues"]})
        self.assertEqual(writes, [])

    def test_verification_contract_controls_the_action(self):
        writes = []
        policy = VerificationPolicy(sources={"source": {
            "data": {"amount": 10}, "trusted": True, "independence_key": "ledger",
            "observed_at": time.time(), "max_age_seconds": 3600,
        }}, claims={"amount": {"source": "source", "path": "/amount"}})
        for amount, executed in ((999, False), (10, True)):
            with self.subTest(amount=amount):
                agent = self.agent(lambda **kw: {"tool": "charge", "arguments": {"amount": amount}})
                harness = ActionHarness(agent, {"charge": lambda amount: writes.append(amount)},
                                        contracts={"charge": {"verification": policy}})
                result = harness.invoke(messages=HISTORY)
                self.assertEqual(result.executed, executed)
                self.assertEqual(result.execution["final_check"]["verification_check"]["valid"], executed)
        self.assertEqual(writes, [10])

    def test_output_policy_conflict_fails_before_registration_or_provider_call(self):
        calls = []
        agent = self.agent(lambda **kw: calls.append(kw), output=OutputPolicy(format="json"))
        before = agent.guard.turn().revision
        with self.assertRaisesRegex(ValueError, "OutputPolicy"):
            ActionHarness(agent, {"noop": lambda: None})
        self.assertEqual(agent.guard.turn().revision, before)
        self.assertEqual(agent.guard.store.policies(agent.session_id), [])
        self.assertEqual(calls, [])

    def test_native_connection_is_explicitly_refused(self):
        native = NativeConnection.__new__(NativeConnection)
        with self.assertRaisesRegex(TypeError, "native"):
            ActionHarness(native, {"noop": lambda: None})

    def test_inventory_contract_and_repair_flags_fail_without_partial_setup(self):
        calls = []
        agent = self.agent(lambda **kw: calls.append(kw))
        tool = lambda value: value
        invalid = [
            ({}, {}), ({"noop": lambda: None} | {f"x{i}": lambda: None for i in range(128)}, {}),
            ({"work": tool}, {"contracts": {"other": {"unchecked": ("value",)}}}),
            ({"work": tool}, {"contracts": {"work": {"replace": True, "unchecked": ("value",)}}}),
            ({"work": tool}, {"contracts": {"work": {"memory": "yes", "unchecked": ("value",)}}}),
            ({"work": tool}, {"contracts": {"work": {"name": "other", "unchecked": ("value",)}}}),
            ({"work": tool}, {"contracts": {"work": []}}),
            ({"work": tool}, {"repair": "guess"}), ({"work": tool}, {"repair": False}),
            ({"work": tool}, {"decoder": False}),
        ]
        before = agent.guard.turn().revision
        for inventory, options in invalid:
            with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                ActionHarness(agent, inventory, **options)
            self.assertEqual(agent.guard.turn().revision, before)
            self.assertEqual(agent.guard.store.policies(agent.session_id), [])
        self.assertEqual(calls, [])

    def test_duplicate_existing_policy_does_not_silently_replace_it(self):
        agent = self.agent(lambda **kw: None)
        agent.allow_tool("noop")
        before = agent.guard.store.policies(agent.session_id)
        revision = agent.guard.turn().revision
        with self.assertRaises(ValueError):
            ActionHarness(agent, {"first": lambda: None, "noop": lambda: None})
        self.assertEqual(agent.guard.store.policies(agent.session_id), before)
        self.assertEqual(agent.guard.turn().revision, revision)

    def test_async_configuration_callbacks_are_rejected_before_registration(self):
        async def callback(*args, **kwargs):
            return {}
        agent = self.agent(lambda **kw: None)
        for options in ({"decoder": callback}, {"repair": callback}):
            with self.subTest(options=options), self.assertRaises(TypeError):
                ActionHarness(agent, {"noop": lambda: None}, **options)
        with self.assertRaises(TypeError):
            ActionHarness(agent, {"noop": callback})
        self.assertEqual(agent.guard.store.policies(agent.session_id), [])

    def test_stream_option_fails_before_model_and_tool(self):
        calls = []
        agent = self.agent(lambda **kw: calls.append(kw))
        harness = ActionHarness(agent, {"noop": lambda: self.fail("must not run")})
        for option in (True, None, 1, "false"):
            with self.subTest(option=option), self.assertRaises(ValueError):
                harness.invoke(messages=HISTORY, stream=option)
        self.assertEqual(calls, [])

    def test_decoder_result_is_copied_and_decoder_failure_never_dispatches(self):
        response = {"native": {"tool": "append", "arguments": {"rows": ["original"]}}}
        agent = self.agent(lambda **kw: response)
        harness = ActionHarness(agent, {"append": lambda rows: rows.append("executor")},
                                contracts={"append": {"unchecked": ("rows",)}},
                                decoder=lambda value: value["native"])
        result = harness.invoke(messages=HISTORY)
        self.assertTrue(result.executed)
        self.assertEqual(response["native"]["arguments"]["rows"], ["original"])
        self.assertIs(result.agent_result.response, response)
        failing = self.agent(lambda **kw: response)
        def decoder(value):
            raise RuntimeError("bad envelope")
        harness = ActionHarness(failing, {"noop": lambda: self.fail("must not run")}, decoder=decoder)
        with self.assertRaises(ActionProposalError) as caught:
            harness.invoke(messages=HISTORY)
        self.assertIs(caught.exception.response, response)

    def test_coverage_names_claims_and_direct_call_boundary(self):
        agent = self.agent(lambda **kw: None, memory=MemoryPolicy())
        harness = ActionHarness(agent, {"save": lambda format, note: None},
                                contracts={"save": {"unchecked": ("note",)}})
        report = harness.coverage()
        self.assertTrue(report["inventory_fully_registered"])
        self.assertFalse(report["direct_calls_protected"])
        self.assertEqual(report["tools"]["save"]["checked"], {"format": {"memory": "export_format"}})
        self.assertEqual(report["tools"]["save"]["unchecked"], ["note"])
        self.assertEqual(harness.coverage(["save", "other"])["unregistered_tools"], ["other"])

    def test_closed_connection_cannot_start_harness_or_invoke_existing_one(self):
        agent = self.agent(lambda **kw: {"tool": "noop", "arguments": {}})
        harness = ActionHarness(agent, {"noop": lambda: self.fail("must not run")})
        agent.close()
        with self.assertRaises(RuntimeError):
            harness.invoke(messages=HISTORY)
        with self.assertRaises(RuntimeError):
            ActionHarness(agent, {"other": lambda: None})

    def test_undeclared_or_missing_unchecked_arguments_have_uniform_proposal_errors(self):
        for arguments in ({"value": 1, "unknown": 2}, {}):
            with self.subTest(arguments=arguments):
                agent = self.agent(lambda **kw: {"tool": "work", "arguments": arguments})
                harness = ActionHarness(agent, {"work": lambda value: self.fail("must not run")},
                                        contracts={"work": {"unchecked": ("value",)}})
                with self.assertRaises(ActionProposalError):
                    harness.invoke(messages=HISTORY)


class AsyncActionHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_provider_options_and_known_repair_execute_once(self):
        calls, writes = [], []
        async def provider(**options):
            calls.append(options)
            return json.dumps({"tool": "save", "arguments": {"format": "CSV"}})
        async with connect(provider, memory=MemoryPolicy()) as agent:
            harness = agent.protect_tools({"save": lambda format: writes.append(format)}, repair="known")
            result = await harness.invoke(messages=HISTORY, model="offline")
        self.assertTrue(result.executed)
        self.assertEqual(result.execution["status"], "repaired")
        self.assertEqual(writes, ["JSON"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "offline")


if __name__ == "__main__":
    unittest.main()
