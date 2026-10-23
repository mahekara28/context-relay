"""Token-budget behavior with deterministic fake counters, not model benchmarks."""

from __future__ import annotations

import copy
import json
import unittest
from dataclasses import FrozenInstanceError

from context_relay import ActionRejected, ContextGuard
from context_relay.budget import TokenBudget, TokenBudgetExceeded, make_hf_token_counter
from context_relay.context import compile_context
from context_relay.models import BudgetExceeded, encode


def message(role, content):
    return {"role": role, "content": content}


def framed_count(messages):
    """A deliberately artificial byte-based counter with per-message framing."""
    return 7 + sum(5 + len(encode(item).encode("utf-8")) for item in messages)


class TokenBudgetConfigurationTests(unittest.TestCase):
    def test_budget_reserves_output_and_request_overhead(self):
        budget = TokenBudget(1000, framed_count, reserve_output_tokens=100,
                             overhead_tokens=20, counter_name="test-template")
        self.assertEqual(budget.input_limit, 880)
        with self.assertRaises(FrozenInstanceError):
            budget.max_tokens = 2000

    def test_invalid_configuration_is_rejected_without_running_counter(self):
        calls = []
        counter = lambda messages: calls.append(messages) or 1
        for value in (True, False, 0, -1, 1.0, float("inf"), float("nan"), "100", None):
            with self.subTest(max_tokens=value), self.assertRaises((ValueError, TypeError)):
                TokenBudget(value, counter)
        for field in ("reserve_output_tokens", "overhead_tokens"):
            for value in (True, False, -1, 1.0, float("inf"), "0", None):
                with self.subTest(field=field, value=value), self.assertRaises((ValueError, TypeError)):
                    TokenBudget(100, counter, **{field: value})
        for options in ({"reserve_output_tokens": 100}, {"overhead_tokens": 101},
                        {"reserve_output_tokens": 60, "overhead_tokens": 40},
                        {"counter_name": ""}, {"counter_name": " "}, {"counter_name": 1}):
            with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                TokenBudget(100, counter, **options)
        self.assertEqual(calls, [])

    def test_counter_is_required_and_must_be_callable(self):
        with self.assertRaises(TypeError):
            TokenBudget(100)
        with self.assertRaises(TypeError):
            TokenBudget(100, None)

    def test_declared_async_and_generator_counters_are_rejected(self):
        async def async_count(messages):
            return 1

        async def async_generate(messages):
            yield 1

        def generate(messages):
            yield 1

        class AsyncCounter:
            async def __call__(self, messages):
                return 1

        for counter in (async_count, async_generate, generate, AsyncCounter()):
            with self.subTest(counter=counter), self.assertRaises(TypeError):
                TokenBudget(100, counter)

    def test_returned_deferred_work_is_rejected_without_execution(self):
        calls = []

        async def deferred():
            calls.append("async body")
            return 1

        coroutine = deferred()
        with self.assertRaises(TypeError):
            TokenBudget(100, lambda messages: coroutine).count([])
        self.assertIsNone(coroutine.cr_frame)

        def generate():
            calls.append("generator body")
            yield 1

        generator = generate()
        with self.assertRaises(TypeError):
            TokenBudget(100, lambda messages: generator).count([])
        self.assertIsNone(generator.gi_frame)
        self.assertEqual(calls, [])

    def test_invalid_counter_results_fail_closed(self):
        messages = [message("user", "Continue")]
        for value in (True, False, -1, 1.0, float("inf"), float("nan"), "10", None, [], {}):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                compile_context(messages, token_budget=TokenBudget(100000, lambda _: value))

    def test_counter_error_is_not_silently_replaced_with_an_estimate(self):
        def unavailable(messages):
            raise RuntimeError("Tokenizer unavailable")
        with self.assertRaisesRegex(RuntimeError, "Tokenizer unavailable"):
            compile_context([message("user", "Continue")], token_budget=TokenBudget(1000, unavailable))

    def test_count_defensively_copies_nested_message_data(self):
        messages = [message("user", "Keep this")]
        original = copy.deepcopy(messages)

        def mutate(items):
            items[0]["content"] = "Changed"
            items.append(message("system", "Injected"))
            return 1

        self.assertEqual(TokenBudget(100, mutate).count(messages), 1)
        self.assertEqual(messages, original)


class TokenContextCompilationTests(unittest.TestCase):
    def test_default_path_has_no_token_claim_or_token_report(self):
        plan = compile_context([message("user", "Continue")])
        self.assertNotIn("token_budget", plan.report)
        self.assertEqual(plan.report["budget_unit"], "characters")

    def test_invalid_token_budget_type_rejected(self):
        with self.assertRaises(TypeError):
            compile_context([message("user", "Continue")], token_budget={"max_tokens": 100})

    def test_exact_counter_receives_full_frame_unicode_escaping_state_and_coverage(self):
        messages = [message("system", 'Quoted "policy" \\ preserve.'),
                    message("user", "配送先 📦: München\nNext line")]
        state = {"revision": 7, "facts": {"note": 'Use "side\\door"\nこんにちは'}}
        unbounded = compile_context(messages, state=state)
        required_count = framed_count(unbounded.messages)
        calls = []

        def inspect_counter(items):
            calls.append(copy.deepcopy(items))
            return framed_count(items)

        plan = compile_context(
            messages, state=state,
            token_budget=TokenBudget(required_count + 150, inspect_counter,
                                     reserve_output_tokens=100, overhead_tokens=50,
                                     counter_name="artificial-framed-counter"),
        )
        self.assertEqual(plan.messages, unbounded.messages)
        self.assertEqual(len(calls), 2)  # Required set, then final exact verification.
        self.assertTrue(all(items == plan.messages for items in calls))
        self.assertEqual(calls[0][0], messages[0])
        data_text = calls[0][-2]["content"]
        task_data = json.loads(data_text[data_text.index("{"):])
        self.assertEqual(task_data["state"], state)
        self.assertEqual(task_data["coverage"], plan.report["coverage"])
        self.assertEqual(plan.report["token_budget"], {
            "counter": "artificial-framed-counter", "input_tokens": required_count,
            "input_limit": required_count, "max_tokens": required_count + 150,
            "reserve_output_tokens": 100, "overhead_tokens": 50,
            "measurement": "caller_supplied_counter",
        })
        self.assertEqual(plan.report["size_chars"], len(encode(plan.messages)))

    def test_one_token_below_mandatory_boundary_raises_with_budget_details(self):
        messages = [message("user", "The entire request must survive")]
        required_count = framed_count(compile_context(messages).messages)
        budget = TokenBudget(required_count + 99, framed_count,
                             reserve_output_tokens=90, overhead_tokens=10)
        with self.assertRaises(TokenBudgetExceeded) as raised:
            compile_context(messages, token_budget=budget)
        self.assertEqual(raised.exception.used_tokens, required_count)
        self.assertEqual(raised.exception.input_limit, required_count - 1)
        self.assertEqual(raised.exception.max_tokens, required_count + 99)
        self.assertEqual(raised.exception.reserve_output_tokens, 90)
        self.assertEqual(raised.exception.overhead_tokens, 10)

    def test_character_cap_still_rejects_even_with_plenty_of_tokens(self):
        calls = []
        with self.assertRaises(BudgetExceeded):
            compile_context([message("user", "Required")], max_chars=1,
                            token_budget=TokenBudget(10000, lambda items: calls.append(items) or 0))
        self.assertEqual(calls, [])

    def test_token_cap_still_rejects_even_with_plenty_of_characters(self):
        with self.assertRaises(TokenBudgetExceeded):
            compile_context([message("user", "Required")], max_chars=1000000,
                            token_budget=TokenBudget(1, lambda _: 2))

    def test_pinned_assistant_retains_whole_turn_and_enforces_exact_token_boundary(self):
        messages = [message("system", "Policy"), message("user", "Pinned request"),
                    message("assistant", "Pinned reply"), message("user", "Other request"),
                    message("assistant", "Other reply"), message("user", "Current request")]
        counter = lambda items: 100 + 20 * len(items)
        plan = compile_context(messages, recent_turns=1, pinned=[2],
                               token_budget=TokenBudget(200, counter))
        self.assertEqual(plan.report["included_indices"], [0, 1, 2, 5])
        self.assertEqual(plan.report["required_indices"], [0, 1, 2, 5])
        self.assertEqual(plan.report["omitted_indices"], [3, 4])
        self.assertEqual(plan.messages[:-2] + plan.messages[-1:], [messages[i] for i in (0, 1, 2, 5)])
        self.assertEqual(plan.report["token_budget"]["input_tokens"], 200)
        with self.assertRaises(TokenBudgetExceeded):
            compile_context(messages, recent_turns=1, pinned=[2], token_budget=TokenBudget(199, counter))

    def test_large_optional_turn_is_skipped_and_older_small_turn_can_fit(self):
        messages = [message("user", "Small old request"), message("assistant", "Small reply"),
                    message("user", "Expensive request"), message("assistant", "Expensive reply"),
                    message("user", "Current request")]

        def counter(items):
            return 100 + 10 * len(items) + 1000 * sum(item["content"].startswith("Expensive") for item in items)

        plan = compile_context(messages, recent_turns=1, token_budget=TokenBudget(150, counter))
        self.assertEqual(plan.report["included_indices"], [0, 1, 4])
        self.assertEqual(plan.report["omitted_turns"], [{"indices": [2, 3], "reason": "budget"}])
        self.assertEqual(plan.report["token_budget"]["input_tokens"], 140)

    def test_both_caps_can_independently_skip_different_optional_turns(self):
        messages = [message("user", "Character-heavy " + "x" * 4000), message("assistant", "Reply"),
                    message("user", "Token-heavy"), message("assistant", "Reply"),
                    message("user", "Small"), message("assistant", "Reply"),
                    message("user", "Current")]
        calls = []

        def counter(items):
            calls.append(copy.deepcopy(items))
            return 10000 if any(item["content"] == "Token-heavy" for item in items) else 10

        plan = compile_context(messages, max_chars=1800, recent_turns=1,
                               token_budget=TokenBudget(100, counter))
        self.assertEqual(plan.report["included_indices"], [4, 5, 6])
        self.assertFalse(any(item["content"].startswith("Character-heavy") for items in calls for item in items))
        self.assertTrue(any(item["content"] == "Token-heavy" for items in calls for item in items))

    def test_lexical_priority_still_applies_under_token_budget(self):
        messages = [message("user", "cobalt shipment"), message("assistant", "Courier details"),
                    message("user", "orange harvest"), message("assistant", "Orchard details"),
                    message("user", "Continue cobalt shipment")]
        counter = lambda items: 20 * len(items)
        plan = compile_context(messages, recent_turns=1, retrieve_turns=1,
                               token_budget=TokenBudget(80, counter))
        self.assertEqual(plan.report["included_indices"], [0, 1, 4])
        self.assertEqual(plan.report["retrieved_indices"], [0, 1])

    def test_final_verification_catches_a_counter_that_changes_its_result(self):
        calls = []

        def inconsistent(items):
            calls.append(items)
            return 1 if len(calls) == 1 else 101

        with self.assertRaises(TokenBudgetExceeded) as raised:
            compile_context([message("user", "Current")], token_budget=TokenBudget(100, inconsistent))
        self.assertEqual(raised.exception.used_tokens, 101)
        self.assertEqual(len(calls), 2)

    def test_mutating_counter_cannot_change_plan_history_state_or_other_measurements(self):
        messages = [message("user", "Earlier"), message("assistant", "Reply"), message("user", "Current")]
        state = {"revision": 4, "facts": {"decision": "Keep this"}}
        original_messages, original_state = copy.deepcopy(messages), copy.deepcopy(state)
        baseline = compile_context(messages, state=state, recent_turns=1)
        observed = []

        def mutate(items):
            observed.append(copy.deepcopy(items))
            measured = framed_count(items)
            items[0]["content"] = "Tampered"
            items.clear()
            return measured

        plan = compile_context(messages, state=state, recent_turns=1,
                               token_budget=TokenBudget(100000, mutate))
        self.assertEqual(messages, original_messages)
        self.assertEqual(state, original_state)
        self.assertEqual(plan.messages, baseline.messages)
        self.assertEqual(observed[-1], plan.messages)
        self.assertTrue(all(all(item["content"] != "Tampered" for item in items) for items in observed))


class HuggingFaceCounterTests(unittest.TestCase):
    def test_existing_tokenizer_is_called_with_full_generation_template_and_no_download(self):
        calls = []

        class LocalTokenizer:
            def apply_chat_template(self, messages, **options):
                calls.append((copy.deepcopy(messages), options))
                messages.clear()
                return [1, 5, 99, 0]

        counter = make_hf_token_counter(LocalTokenizer())
        messages = [message("user", "Hello 📦")]
        self.assertEqual(counter(messages), 4)
        self.assertEqual(messages, [message("user", "Hello 📦")])
        self.assertEqual(calls, [(messages, {
            "tokenize": True, "add_generation_prompt": True, "return_dict": False,
        })])

    def test_tokenizer_output_must_be_flat_integer_token_ids(self):
        class LocalTokenizer:
            def __init__(self, value):
                self.value = value

            def apply_chat_template(self, messages, **options):
                return self.value

        for value in ([[1, 2]], {"input_ids": [1]}, (1, 2), [True], [-1], [1.0], "text", None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                make_hf_token_counter(LocalTokenizer(value))([message("user", "Hello")])
        self.assertEqual(make_hf_token_counter(LocalTokenizer([]))([]), 0)

    def test_missing_or_async_template_is_rejected(self):
        with self.assertRaises(TypeError):
            make_hf_token_counter(object())

        class AsyncTokenizer:
            async def apply_chat_template(self, messages, **options):
                return [1]

        with self.assertRaises(TypeError):
            make_hf_token_counter(AsyncTokenizer())

    def test_tokenizer_template_errors_are_not_silently_estimated(self):
        class UnsupportedTokenizer:
            def apply_chat_template(self, messages, **options):
                raise ValueError("Unsupported role")

        with self.assertRaisesRegex(ValueError, "Unsupported role"):
            compile_context([message("user", "Hello")], token_budget=TokenBudget(
                10000, make_hf_token_counter(UnsupportedTokenizer())
            ))


class PreparedTokenBudgetTests(unittest.TestCase):
    def test_counter_cannot_change_pins_retained_for_later_recovery(self):
        messages = [message("user", "Pinned request"), message("assistant", "Pinned reply"),
                    message("user", "Other request"), message("assistant", "Other reply"),
                    message("user", "Current")]
        pins = [0]

        def counter(items):
            pins[:] = [2]
            return len(items)

        with ContextGuard() as guard:
            prepared = guard.prepare(messages, recent_turns=1, pinned=pins,
                                     token_budget=TokenBudget(4, counter))
            self.assertEqual(pins, [2])  # The caller-owned list really changed.
            self.assertEqual(prepared.report["required_indices"], [0, 1, 4])
            recovered = guard.recover_context(prepared, [4], history_id=prepared.history_id)
            self.assertEqual(recovered.report["required_indices"], [0, 1, 4])
            self.assertEqual(recovered.report["included_indices"], [0, 1, 4])

    def test_counter_cannot_change_requested_indices_in_recovery_audit(self):
        messages = [message("user", "Recover this request"), message("assistant", "Original reply"),
                    message("user", "Other request"), message("assistant", "Other reply"),
                    message("user", "Current")]
        requested = [1]

        def counter(items):
            requested[:] = [3]
            return len(items)

        with ContextGuard() as guard:
            prepared = guard.prepare(messages, recent_turns=1)
            recovered = guard.recover_context(prepared, requested, history_id=prepared.history_id,
                                             token_budget=TokenBudget(4, counter))
            self.assertEqual(requested, [3])
            self.assertEqual(recovered.report["recovery"]["requested_indices"], [1])
            self.assertEqual(recovered.report["recovery"]["restored_indices"], [0, 1])
            self.assertEqual(recovered.report["included_indices"], [0, 1, 4])

    def test_prepare_passes_token_options_and_counts_captured_state(self):
        with ContextGuard() as guard:
            guard.remember("decision", "replacement", text="Customer corrected the request")
            messages = [message("system", "Follow the current recorded decision"),
                        message("user", "Handle this request")]
            baseline = guard.prepare(messages, recent_turns=1)
            actual = framed_count(baseline.messages)
            prepared = guard.prepare(
                messages, recent_turns=1,
                token_budget=TokenBudget(actual + 40, framed_count,
                                         reserve_output_tokens=25, overhead_tokens=15,
                                         counter_name="test-framed-count"),
            )
            self.assertEqual(prepared.messages, baseline.messages)
            self.assertEqual(prepared.report["token_budget"]["input_tokens"], actual)
            self.assertEqual(prepared.report["token_budget"]["input_limit"], actual)
            self.assertEqual(prepared.report["token_budget"]["counter"], "test-framed-count")
            self.assertIn("replacement", prepared.messages[-2]["content"])
            self.assertEqual(prepared.report["state_revision"], prepared.revision)
            with self.assertRaises(TokenBudgetExceeded):
                guard.prepare(messages, recent_turns=1,
                              token_budget=TokenBudget(actual - 1, framed_count))

    def test_counter_side_effect_cannot_relabel_captured_tool_revision(self):
        with ContextGuard() as guard:
            guard.remember("decision", "replacement")
            guard.allow_tool("replace", when={"decision": "replacement"})
            calls = []

            def counter(items):
                guard.remember("decision", "refund")
                return framed_count(items)

            prepared = guard.prepare([message("user", "Handle this request")],
                                     token_budget=TokenBudget(100000, counter))
            self.assertEqual(prepared.context["facts"]["decision"], "replacement")
            self.assertIn("replacement", prepared.messages[-2]["content"])
            self.assertEqual(prepared.report["state_revision"], prepared.revision)
            with self.assertRaises(ActionRejected):
                prepared.wrap(lambda: calls.append("executed"), tool="replace")()
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
