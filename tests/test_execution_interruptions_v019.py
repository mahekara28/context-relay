"""Started local effects consume their turn even when failure reporting is hostile."""

import asyncio
import unittest

from context_relay import ContextGuard, ToolExecutionError, auto_connect
from context_relay.actions import ActionHarness


class BrokenTextError(Exception):
    def __str__(self):
        raise ValueError("broken exception formatter")


class InterruptedTextError(Exception):
    def __str__(self):
        raise KeyboardInterrupt("exception formatter interrupted")


class SlicingString(str):
    def __getitem__(self, key):
        raise RuntimeError("diagnostic slicing must use the base string")


class EncodingString(str):
    def __getitem__(self, key):
        return self

    def encode(self, *args, **kwargs):
        raise RuntimeError("diagnostic encoding must use the base string")


class ReformattingString(str):
    def __str__(self):
        raise KeyboardInterrupt("diagnostic coercion must use the unbound builtin")


class SubclassTextError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class BrokenNameType(type):
    @property
    def __name__(cls):
        raise KeyboardInterrupt("type-name lookup interrupted")


class BrokenNameError(Exception, metaclass=BrokenNameType):
    pass


class FalseyError(Exception):
    def __bool__(self):
        return False


class BrokenTruthError(Exception):
    def __bool__(self):
        raise ValueError("exceptions are not booleans")


class ExecutionInterruptionTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.guard.allow_tool("refund", optional_args=("amount",))
        self.effects = []

    def assert_consumed_once(self, turn, executor, *, phase="callback"):
        receipts = [event for event in self.guard.store.events(self.guard.session_id)
                    if event["kind"] == "tool_result"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["status"], "failed")
        self.assertEqual(receipts[0]["error"]["phase"], phase)
        self.assertEqual(receipts[0]["error"]["executor_completed"], phase == "result_encoding")
        self.assertGreater(self.guard.turn().revision, turn.revision)
        second = turn.execute_with_recovery("refund", {"amount": 20}, executor, repair="known")
        self.assertFalse(second["executed"])
        self.assertEqual(second["repair_attempts"], 0)
        self.assertIn("stale_revision", {
            issue["code"] for issue in second["final_check"]["issues"]})
        self.assertEqual(self.effects, [20])
        after = [event for event in self.guard.store.events(self.guard.session_id)
                 if event["kind"] == "tool_result"]
        self.assertEqual(after, receipts)
        return receipts[0]

    def test_keyboard_interrupt_commits_receipt_then_propagates_original(self):
        self.check_interruption(KeyboardInterrupt("local simulated interruption"))

    def test_system_exit_commits_receipt_then_propagates_original(self):
        self.check_interruption(SystemExit(23))

    def test_cancelled_error_commits_receipt_then_propagates_original(self):
        self.check_interruption(asyncio.CancelledError("local simulated cancellation"))

    def check_interruption(self, interruption):
        turn = self.guard.turn()
        repairs = []
        def executor(name, arguments):
            self.effects.append(arguments["amount"])
            raise interruption
        with self.assertRaises(type(interruption)) as caught:
            turn.execute_with_recovery("refund", {"amount": 20}, executor,
                                       repair=lambda request: repairs.append(request))
        self.assertIs(caught.exception, interruption)
        self.assertEqual(repairs, [])
        receipt = self.assert_consumed_once(turn, executor)
        self.assertEqual(receipt["error"]["type"], type(interruption).__name__)

    def test_broken_exception_text_still_records_failure_and_blocks_replay(self):
        self.check_exception(BrokenTextError())

    def test_exception_text_cannot_interrupt_receipt_creation(self):
        self.check_exception(InterruptedTextError())

    def test_exception_string_slicing_hook_cannot_erase_receipt(self):
        receipt = self.check_exception(SubclassTextError(SlicingString("safe diagnostic")))
        self.assertEqual(receipt["error"]["message"], "safe diagnostic")

    def test_exception_string_encoding_hook_cannot_erase_receipt(self):
        receipt = self.check_exception(SubclassTextError(EncodingString("safe diagnostic")))
        self.assertEqual(receipt["error"]["message"], "safe diagnostic")

    def test_exception_string_coercion_uses_builtin_without_subclass_str(self):
        receipt = self.check_exception(SubclassTextError(ReformattingString("safe diagnostic")))
        self.assertEqual(receipt["error"]["message"], "safe diagnostic")

    def test_exception_type_name_subclass_is_safe_json_and_blocks_replay(self):
        class CustomNameError(Exception):
            pass
        CustomNameError.__name__ = ReformattingString("CustomNameError")
        receipt = self.check_exception(CustomNameError("effect happened"), expected_type="CustomNameError")
        self.assertIs(type(receipt["error"]["type"]), str)

    def test_exception_type_name_lookup_failure_has_safe_fallback(self):
        self.check_exception(BrokenNameError("effect happened"), expected_type="Exception")

    def test_exception_type_name_cannot_mutate_store_while_formatting(self):
        guard = self.guard
        class WritingNameType(type):
            @property
            def __name__(cls):
                guard.remember("escaped_from_formatter", True)
                return "UnexpectedName"
        class WritingNameError(Exception, metaclass=WritingNameType):
            pass
        self.check_exception(WritingNameError("effect happened"), expected_type="Exception")
        self.assertNotIn("escaped_from_formatter", guard.store.facts(guard.session_id))

    def test_falsey_exception_is_a_failure_and_blocks_replay(self):
        self.check_exception(FalseyError("effect happened"))

    def test_exception_truthiness_is_never_evaluated(self):
        self.check_exception(BrokenTruthError("effect happened"))

    def test_surrogate_exception_text_is_safe_to_persist(self):
        self.check_exception(RuntimeError("broken message \ud800"))

    def test_oversized_exception_text_is_bounded(self):
        receipt = self.check_exception(RuntimeError("x" * 100000))
        self.assertLessEqual(len(receipt["error"]["message"]), 4096)

    def check_exception(self, original, *, expected_type=None):
        turn = self.guard.turn()
        repairs = []
        def executor(name, arguments):
            self.effects.append(arguments["amount"])
            raise original
        with self.assertRaises(ToolExecutionError) as caught:
            turn.execute_with_recovery("refund", {"amount": 20}, executor,
                                       repair=lambda request: repairs.append(request))
        self.assertIs(caught.exception.__cause__, original)
        self.assertEqual(repairs, [])
        receipt = self.assert_consumed_once(turn, executor)
        self.assertEqual(receipt["error"]["type"], type(original).__name__ if expected_type is None else expected_type)
        self.assertEqual(caught.exception.receipt, receipt)
        self.assertIsInstance(receipt["error"]["message"], str)
        receipt["error"]["message"].encode("utf-8")
        return receipt

    def test_unsupported_result_consumes_turn_after_one_effect(self):
        self.check_invalid_result(object())

    def test_unpaired_surrogate_result_consumes_turn_after_one_effect(self):
        self.check_invalid_result({"result": "\udfff"})

    def test_surrogate_result_key_consumes_turn_after_one_effect(self):
        self.check_invalid_result({"\ud800": "invalid object key"})

    def check_invalid_result(self, result):
        turn = self.guard.turn()
        def executor(name, arguments):
            self.effects.append(arguments["amount"])
            return result
        with self.assertRaises(ToolExecutionError):
            turn.execute_with_recovery("refund", {"amount": 20}, executor, repair="known")
        self.assert_consumed_once(turn, executor, phase="result_encoding")

    def test_invalid_unicode_arguments_are_rejected_before_any_effect(self):
        turn = self.guard.turn()
        def executor(name, arguments):
            self.effects.append(arguments)
            return {"done": True}
        for arguments in ({"amount": "\ud800"}, {"amount": ["\udfff"]},
                          {"amount": {"\ud800": 20}}):
            with self.subTest(arguments=ascii(arguments)), self.assertRaises(ValueError):
                turn.execute_with_recovery("refund", arguments, executor)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.guard.turn().revision, turn.revision)
        receipts = [event for event in self.guard.store.events(self.guard.session_id)
                    if event["kind"] == "tool_result"]
        self.assertEqual(receipts, [])

    def test_action_harness_never_repairs_interrupted_started_action(self):
        effects, repairs, prepared_turns, model_calls = [], [], [], []
        interruption = KeyboardInterrupt("local harness interruption")
        agent = None
        def provider(*, messages):
            model_calls.append(messages)
            prepared_turns.append(agent.guard.turn())
            return {"tool": "refund", "arguments": {"amount": 20}}
        def refund(amount):
            effects.append(amount)
            raise interruption
        def dispatch(name, arguments):
            return refund(**arguments)
        agent = auto_connect(provider)
        self.addCleanup(agent.close)
        harness = ActionHarness(agent, tools={"refund": refund},
                                contracts={"refund": {"unchecked": ("amount",)}},
                                repair=lambda request: repairs.append(request))
        with self.assertRaises(KeyboardInterrupt) as caught:
            harness.invoke(messages=[{"role": "user", "content": "Process the approved refund."}])
        self.assertIs(caught.exception, interruption)
        self.assertEqual(effects, [20])
        self.assertEqual(repairs, [])
        self.assertEqual(len(model_calls), 1)
        repeated = prepared_turns[0].execute_with_recovery("refund", {"amount": 20}, dispatch,
                                                          repair=lambda request: repairs.append(request))
        self.assertFalse(repeated["executed"])
        self.assertEqual(effects, [20])
        self.assertEqual(repairs, [])
        receipts = [event for event in agent.guard.store.events(agent.session_id)
                    if event["kind"] == "tool_result"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
