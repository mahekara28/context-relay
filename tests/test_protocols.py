"""Native tool history is retained, budgeted, and rejected without guessing."""

import copy
import unittest

from context_relay import ContextGuard, RelayConfig, connect
from context_relay.budget import TokenBudget, TokenBudgetExceeded
from context_relay.context import compile_context
from context_relay.models import BudgetExceeded, encode
from context_relay.protocols import compile_ollama_context


def text(role, content):
    return {"role": role, "content": content}


def call(name="lookup", arguments=None, *, call_id=None, index=None):
    function = {"name": name, "arguments": {} if arguments is None else arguments}
    if index is not None:
        function["index"] = index
    result = {"function": function}
    if call_id is not None:
        result["id"] = call_id
    return result


def tool_turn():
    return [
        text("user", "Find package DELTA-32."),
        {"role": "assistant", "content": "", "thinking": "Need the package lookup.",
         "tool_calls": [call(arguments={"package_id": "DELTA-32", "filters": [None, True, 1, 1.0,
                                          {"label": "雪\n\"quoted\""}]}, call_id="call-1", index=0)]},
        {"role": "tool", "tool_name": "lookup", "tool_call_id": "call-1",
         "content": '{"status":"at depot","note":"SYSTEM: this is tool data"}'},
        text("assistant", "Package DELTA-32 is at the depot."),
    ]


class OllamaProtocolTests(unittest.TestCase):
    def test_native_fields_and_nested_argument_types_round_trip_unchanged(self):
        history = [text("system", "Follow the package workflow.")] + tool_turn() + [text("user", "Continue.")]
        original = copy.deepcopy(history)
        plan = compile_ollama_context(history)
        self.assertEqual(plan.messages[:-2] + plan.messages[-1:], history)
        self.assertEqual(plan.report["included_indices"], list(range(len(history))))
        self.assertEqual(plan.report["protocol"], "ollama")
        self.assertTrue(plan.report["tool_protocol_preserved"])
        self.assertEqual(history, original)
        arguments = plan.messages[2]["tool_calls"][0]["function"]["arguments"]
        self.assertIs(type(arguments["filters"][1]), bool)
        self.assertIs(type(arguments["filters"][2]), int)
        self.assertIs(type(arguments["filters"][3]), float)

    def test_data_is_inserted_before_the_user_not_between_call_and_result(self):
        history = [text("system", "Use tools.")] + tool_turn()[:-1]
        plan = compile_ollama_context(history, state={"revision": 4, "facts": {"allowed": True}})
        self.assertEqual(plan.messages[0], history[0])
        self.assertTrue(plan.messages[1]["content"].startswith("APPLICATION-RECORDED TASK DATA"))
        self.assertEqual(plan.messages[2:], history[1:])
        self.assertEqual(plan.report["data_before_index"], 1)
        self.assertEqual(plan.report["state_revision"], 4)

    def test_multiple_sequential_batches_remain_one_turn(self):
        history = tool_turn()[:-1] + [
            {"role": "assistant", "tool_calls": [call("route", {"depot": "D3"})]},
            {"role": "tool", "tool_name": "route", "content": "Use train T2."},
        ]
        plan = compile_ollama_context(history)
        self.assertEqual(plan.messages[1:], history)
        self.assertEqual(plan.report["required_indices"], list(range(len(history))))

    def test_parallel_name_only_results_are_accepted_in_issued_order(self):
        history = [text("user", "Fetch both locations."),
                   {"role": "assistant", "tool_calls": [call("north"), call("south")]},
                   {"role": "tool", "tool_name": "north", "content": "N"},
                   {"role": "tool", "tool_name": "south", "content": "S"}]
        plan = compile_ollama_context(history)
        self.assertEqual(plan.messages[1:], history)

    def test_same_name_calls_remain_distinct_in_issued_order(self):
        history = [text("user", "Fetch both packages."),
                   {"role": "assistant", "tool_calls": [call(arguments={"id": 1}), call(arguments={"id": 2})]},
                   {"role": "tool", "tool_name": "lookup", "content": "Package one"},
                   {"role": "tool", "tool_name": "lookup", "content": "Package two"}]
        self.assertEqual(compile_ollama_context(history).messages[1:], history)

    def test_ids_match_parallel_results_even_when_returned_out_of_order(self):
        history = [text("user", "Fetch both packages."),
                   {"role": "assistant", "tool_calls": [call(call_id="a"), call(call_id="b")]},
                   {"role": "tool", "tool_call_id": "b", "content": "Package two"},
                   {"role": "tool", "tool_name": "lookup", "tool_call_id": "a", "content": "Package one"}]
        self.assertEqual(compile_ollama_context(history).messages[1:], history)

    def test_a_pin_on_one_tool_result_retains_its_entire_turn(self):
        history = tool_turn() + [text("user", "Unrelated large request " + "x" * 4000),
                                 text("assistant", "Large response " + "y" * 4000),
                                 text("user", "Continue package task.")]
        plan = compile_ollama_context(history, max_chars=2200, recent_turns=1, pinned=[2])
        self.assertEqual(plan.report["included_indices"], [0, 1, 2, 3, 6])
        self.assertEqual(plan.report["required_indices"], [0, 1, 2, 3, 6])
        self.assertEqual(plan.report["omitted_indices"], [4, 5])
        self.assertEqual(plan.messages[:-2], history[:4])

    def test_omitted_tool_turn_never_leaves_an_orphan_result(self):
        history = tool_turn()
        history[2]["content"] = "large tool result " + "z" * 8000
        history += [text("user", "New unrelated request.")]
        plan = compile_ollama_context(history, max_chars=1500, recent_turns=1)
        self.assertEqual(plan.report["included_indices"], [4])
        self.assertEqual(plan.report["omitted_turns"], [{"indices": [0, 1, 2, 3], "reason": "budget"}])
        self.assertFalse(any(item["role"] == "tool" or "tool_calls" in item for item in plan.messages))

    def test_large_required_tool_result_fails_instead_of_truncating(self):
        history = tool_turn()[:-1]
        history[2]["content"] = "z" * 6000
        with self.assertRaises(BudgetExceeded):
            compile_ollama_context(history, max_chars=2000, recent_turns=1)

    def test_character_boundary_measures_native_dicts_and_inserted_state(self):
        history = tool_turn()[:-1]
        full = compile_ollama_context(history, state={"quote": "📦雪\n\""})
        actual = len(encode(full.messages))
        self.assertEqual(actual, full.report["size_chars"])
        self.assertEqual(len(encode(history)), full.report["original_size_chars"])
        self.assertEqual(compile_ollama_context(history, state={"quote": "📦雪\n\""}, max_chars=actual).messages,
                         full.messages)
        with self.assertRaises(BudgetExceeded) as raised:
            compile_ollama_context(history, state={"quote": "📦雪\n\""}, max_chars=actual - 1)
        self.assertEqual(raised.exception.size_chars, actual)

    def test_token_counter_sees_actual_native_fields_and_reserves_tool_overhead(self):
        history = tool_turn()[:-1]
        expected = compile_ollama_context(history).messages
        measured = []

        def counter(messages):
            measured.append(copy.deepcopy(messages))
            return len(encode(messages))  # deterministic test tokenizer stand-in

        size = len(encode(expected))
        budget = TokenBudget(size + 300, counter, reserve_output_tokens=100, overhead_tokens=200)
        result = compile_ollama_context(history, token_budget=budget)
        self.assertEqual(measured[-1], expected)
        self.assertEqual(result.report["token_budget"]["input_tokens"], size)
        with self.assertRaises(TokenBudgetExceeded):
            compile_ollama_context(history, token_budget=TokenBudget(size + 299, counter,
                                  reserve_output_tokens=100, overhead_tokens=200))

    def test_mutating_counter_and_returned_messages_cannot_change_tool_arguments(self):
        history = tool_turn()[:-1]

        def counter(messages):
            messages[-2]["tool_calls"][0]["function"]["arguments"]["package_id"] = "FORGED"
            return 1

        plan = compile_ollama_context(history, token_budget=TokenBudget(100, counter))
        view = plan.messages
        view[-2]["tool_calls"][0]["function"]["arguments"].clear()
        history[1]["tool_calls"].clear()
        self.assertEqual(plan.messages[-2]["tool_calls"][0]["function"]["arguments"]["package_id"], "DELTA-32")

    def test_retrieval_can_find_lexical_terms_inside_native_arguments(self):
        history = tool_turn() + [text("user", "Noise " + "x" * 7000), text("assistant", "Noise"),
                                 text("user", "Find the DELTA-32 package.")]
        plan = compile_ollama_context(history, max_chars=2200, recent_turns=1, retrieve_turns=1)
        self.assertEqual(plan.report["retrieved_indices"], [0, 1, 2, 3])

    def test_missing_results_and_unfinished_assistant_requests_fail(self):
        invalid = [
            [text("user", "x"), {"role": "assistant", "tool_calls": [call()]}],
            [text("user", "x"), text("assistant", "done")],
            [text("user", "x"), {"role": "assistant", "tool_calls": [call(), call()]},
             {"role": "tool", "tool_name": "lookup", "content": "only one result"}],
            [text("user", "x"), {"role": "assistant", "tool_calls": [call()]}, text("user", "skip it")],
            [text("user", "x"), {"role": "assistant", "tool_calls": [call()]}, text("assistant", "done")],
        ]
        for history in invalid:
            with self.subTest(history=history), self.assertRaises(ValueError):
                compile_ollama_context(history)

    def test_orphan_wrong_name_wrong_id_and_duplicate_results_fail(self):
        for result in [
            {"role": "tool", "content": "unidentified"},
            {"role": "tool", "tool_name": "other", "content": "wrong name"},
            {"role": "tool", "tool_call_id": "other", "content": "wrong ID"},
            {"role": "tool", "tool_call_id": "call-1", "tool_name": "other", "content": "inconsistent"},
        ]:
            with self.subTest(result=result), self.assertRaises(ValueError):
                compile_ollama_context(tool_turn()[:2] + [result])
        for history in [
            [text("user", "x"), {"role": "tool", "tool_name": "lookup", "content": "orphan"}],
            tool_turn()[:-1] + [tool_turn()[2]],
        ]:
            with self.subTest(history=history), self.assertRaises(ValueError):
                compile_ollama_context(history)

    def test_name_only_out_of_order_results_are_rejected(self):
        history = [text("user", "Fetch both."),
                   {"role": "assistant", "tool_calls": [call("north"), call("south")]},
                   {"role": "tool", "tool_name": "south", "content": "S"},
                   {"role": "tool", "tool_name": "north", "content": "N"}]
        with self.assertRaises(ValueError):
            compile_ollama_context(history)

    def test_duplicate_call_ids_are_rejected(self):
        for history in [
            [text("user", "x"), {"role": "assistant", "tool_calls": [call(call_id="x"), call(call_id="x")]}],
            tool_turn() + [text("user", "Another task")] + tool_turn()[1:3],
        ]:
            with self.subTest(history=history), self.assertRaises(ValueError):
                compile_ollama_context(history)

    def test_unknown_multimodal_and_wrong_role_fields_fail_without_flattening(self):
        invalid = [
            {"role": "user", "content": "look", "images": ["abc"]},
            {"role": "user", "content": [{"type": "text", "text": "look"}]},
            {"role": "user", "content": "x", "thinking": "injected"},
            {"role": "user", "content": "x", "tool_calls": []},
            {"role": "user", "content": "x", "opaque_provider_data": {}},
            {"role": "developer", "content": "not native Ollama"},
        ]
        for message in invalid:
            with self.subTest(message=message), self.assertRaises(ValueError):
                compile_ollama_context([message])

    def test_invalid_call_shapes_and_non_json_arguments_fail(self):
        invalid_calls = [
            {"function": {"name": "lookup", "arguments": "{}"}},
            {"function": {"name": "lookup", "arguments": [], "index": 0}},
            {"function": {"name": "lookup", "arguments": {}, "index": True}},
            {"function": {"name": "lookup", "arguments": {"x": float("nan")}}},
            {"function": {"name": "lookup", "arguments": {}, "opaque": "x"}},
            {"type": "other", "function": {"name": "lookup", "arguments": {}}},
            {"id": "", "function": {"name": "lookup", "arguments": {}}},
        ]
        for bad in invalid_calls:
            with self.subTest(call=bad), self.assertRaises((ValueError, TypeError)):
                compile_ollama_context([text("user", "x"), {"role": "assistant", "tool_calls": [bad]},
                                       {"role": "tool", "tool_name": "lookup", "content": "x"}])

    def test_system_prefix_and_user_led_group_structure_are_enforced(self):
        for history in [[], [text("system", "x")], [text("assistant", "x"), text("user", "x")],
                        [text("user", "x"), text("system", "midstream"), text("user", "x")]]:
            with self.subTest(history=history), self.assertRaises(ValueError):
                compile_ollama_context(history)

    def test_shared_option_validation_still_applies(self):
        for options in [{"max_chars": True}, {"recent_turns": 0}, {"retrieve_turns": -1},
                        {"pinned": [True]}, {"pinned": [10]}, {"query": []}, {"state": []}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                compile_ollama_context([text("user", "x")], **options)


class PlainSourceLabelsTests(unittest.TestCase):
    def setUp(self):
        self.history = [text("system", "Leave instructions untouched."),
                        text("developer", "Leave these instructions untouched too."),
                        text("user", "Use CSV."), text("assistant", "Understood."),
                        text("user", "Continue.")]

    def test_labels_point_to_original_indices_without_rewriting_roles_or_history(self):
        original = copy.deepcopy(self.history)
        plan = compile_context(self.history, source_labels=True)
        self.assertEqual(plan.messages[:2], self.history[:2])
        self.assertEqual(plan.messages[2], text("user", "[Relay source: m2]\nUse CSV."))
        self.assertEqual(plan.messages[3], text("assistant", "[Relay source: m3]\nUnderstood."))
        self.assertEqual(plan.messages[-1], text("user", "[Relay source: m4]\nContinue."))
        self.assertEqual(self.history, original)
        self.assertTrue(plan.report["source_labels"])
        self.assertEqual(plan.report["original_size_chars"], len(encode(original)))

    def test_source_labels_are_opt_in(self):
        plan = compile_context(self.history)
        self.assertEqual(plan.messages[:-2] + plan.messages[-1:], self.history)
        self.assertNotIn("source_labels", plan.report)
        for wrong in (1, "true", None):
            with self.subTest(value=wrong), self.assertRaises(ValueError):
                compile_context(self.history, source_labels=wrong)

    def test_labels_keep_original_positions_after_older_messages_are_omitted(self):
        history = [text("user", "Huge old request " + "x" * 5000),
                   text("assistant", "Old answer"), text("user", "Current request")]
        plan = compile_context(history, max_chars=1500, recent_turns=1, source_labels=True)
        self.assertEqual(plan.report["included_indices"], [2])
        self.assertEqual(plan.messages[-1]["content"], "[Relay source: m2]\nCurrent request")

    def test_source_label_overhead_is_included_in_exact_character_budget(self):
        history = [text("system", "Instruction."), text("user", "Quote this.\n雪")]
        plan = compile_context(history, source_labels=True)
        size = len(encode(plan.messages))
        self.assertEqual(plan.report["size_chars"], size)
        self.assertEqual(compile_context(history, source_labels=True, max_chars=size).messages, plan.messages)
        with self.assertRaises(BudgetExceeded):
            compile_context(history, source_labels=True, max_chars=size - 1)
        unlabelled = compile_context(history, max_chars=size - 1)
        self.assertLess(unlabelled.report["size_chars"], size)

    def test_token_counter_sees_labels_not_just_original_text(self):
        expected = compile_context(self.history, source_labels=True).messages
        measured = []

        def count(messages):
            measured.append(messages)
            return len(encode(messages))

        size = len(encode(expected))
        plan = compile_context(self.history, source_labels=True, token_budget=TokenBudget(size, count))
        self.assertEqual(measured[-1], expected)
        self.assertEqual(plan.report["token_budget"]["input_tokens"], size)
        with self.assertRaises(TokenBudgetExceeded):
            compile_context(self.history, source_labels=True, token_budget=TokenBudget(size - 1, count))


class NativeProtocolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)

    def test_guard_prepare_preserves_native_batch_and_captured_revision(self):
        self.guard.remember("allowed_lookup", "DELTA-32")
        history = tool_turn()[:-1]
        prepared = self.guard.prepare(history, protocol="ollama")
        self.assertEqual(prepared.messages[1:], history)
        self.assertEqual(prepared.report["state_revision"], prepared.revision)
        self.assertIn("DELTA-32", prepared.messages[0]["content"])
        self.assertEqual(prepared.report["protocol"], "ollama")
        evidence = prepared.recover_evidence([2], history_id=prepared.history_id)
        self.assertEqual(evidence.indices, [0, 1, 2])
        self.assertEqual(evidence.messages, history)
        self.assertEqual(evidence.as_message()["role"], "user")
        self.assertFalse(evidence.report["current_authority"])

    def test_recovery_retains_protocol_original_snapshot_and_whole_tool_turn(self):
        history = tool_turn() + [text("user", "Continue.")]
        history[2]["content"] += "x" * 4000
        original = copy.deepcopy(history)
        prepared = self.guard.prepare(history, protocol="ollama", recent_turns=1, max_chars=2000)
        self.assertEqual(prepared.report["included_indices"], [4])
        history[1]["tool_calls"][0]["function"]["arguments"]["package_id"] = "FORGED"
        self.guard.remember("next_action", "wait")
        recovered = self.guard.recover_context(prepared, [2], history_id=prepared.history_id, max_chars=10000)
        self.assertEqual(recovered.report["protocol"], "ollama")
        self.assertEqual(recovered.report["recovery"]["restored_indices"], [0, 1, 2, 3])
        self.assertEqual(recovered.report["required_indices"], [0, 1, 2, 3, 4])
        self.assertEqual(recovered.history_id, prepared.history_id)
        self.assertEqual(recovered.messages[:-2], original[:4])
        self.assertGreater(recovered.revision, prepared.revision)
        self.assertIn('"next_action":"wait"', recovered.messages[-2]["content"])

    def test_connection_passes_native_pending_tool_results_and_other_options(self):
        requests = []
        response = object()
        tool_schemas = [{"type": "function", "function": {"name": "lookup"}}]

        def provider(*, messages, **options):
            requests.append((messages, options))
            return response

        with connect(provider, config=RelayConfig(protocol="ollama"), guard=self.guard) as agent:
            result = agent.invoke(messages=tool_turn()[:-1], model="existing-local-model",
                                  tools=tool_schemas, stream=False)
        self.assertIs(result.response, response)
        self.assertEqual(requests[0][0][1:], tool_turn()[:-1])
        self.assertIs(requests[0][1]["tools"], tool_schemas)
        self.assertEqual(requests[0][1]["model"], "existing-local-model")
        self.assertEqual(result.prepared.report["protocol"], "ollama")

    def test_invalid_native_history_never_reaches_connected_model(self):
        requests = []
        with connect(lambda *, messages: requests.append(messages),
                     config=RelayConfig(protocol="ollama"), guard=self.guard) as agent:
            with self.assertRaises(ValueError):
                agent(messages=tool_turn()[:2])
        self.assertEqual(requests, [])

    def test_connection_native_token_boundary_counts_state_calls_and_results(self):
        history = tool_turn()[:-1]
        expected = self.guard.prepare(history, protocol="ollama").messages
        size = len(encode(expected))
        requests = []
        counter_inputs = []

        def count(messages):
            counter_inputs.append(copy.deepcopy(messages))
            return len(encode(messages))

        budget = TokenBudget(size + 200, count, overhead_tokens=150, reserve_output_tokens=50)
        with connect(lambda *, messages: requests.append(messages), guard=self.guard,
                     config=RelayConfig(protocol="ollama"), token_budget=budget) as agent:
            agent(messages=history)
        self.assertEqual(requests, [expected])
        self.assertEqual(counter_inputs[-1], expected)
        with connect(lambda *, messages: requests.append(messages), guard=self.guard,
                     config=RelayConfig(protocol="ollama"),
                     token_budget=TokenBudget(size - 1, count)) as agent:
            with self.assertRaises(TokenBudgetExceeded):
                agent(messages=history)
        self.assertEqual(len(requests), 1)

    def test_native_source_labels_are_rejected(self):
        with self.assertRaises(ValueError):
            self.guard.prepare(tool_turn()[:-1], protocol="ollama", source_labels=True)

    def test_plain_and_native_archives_do_not_share_evidence_identity(self):
        history = [text("user", "This text works in both protocols.")]
        plain = self.guard.prepare(history)
        native = self.guard.prepare(history, protocol="ollama")
        self.assertNotEqual(plain.history_id, native.history_id)
        with self.assertRaises(ValueError):
            native.recover_evidence([0], history_id=plain.history_id)


class AsyncNativeProtocolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_connection_keeps_native_tool_batch(self):
        requests = []
        response = object()

        async def provider(*, messages, model):
            requests.append(messages)
            return response

        async with connect(provider, config=RelayConfig(protocol="ollama")) as agent:
            result = await agent.invoke(messages=tool_turn()[:-1], model="existing-model")
        self.assertIs(result.response, response)
        self.assertEqual(requests[0][1:], tool_turn()[:-1])


if __name__ == "__main__":
    unittest.main()
