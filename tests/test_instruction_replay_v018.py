"""Regression checks for replay reporting, using no parser or real provider."""
import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "evaluate_instructions_v018.py"
SPEC = importlib.util.spec_from_file_location("instruction_replay_v018", SCRIPT)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)

SEEDS = {"export_format": "CSV", "response_language": "English", "response_style": "brief"}


class Pause(Exception):
    def __init__(self, targets):
        self.targets = targets
        self.prompt = "Choose a replacement."


class Rejected(Exception):
    pass


class FakeAI:
    def __init__(self, api, reader):
        self.api, self.reader = api, reader
        self.values = dict(SEEDS)
        self.memory = SimpleNamespace(entries=lambda: [
            {"key": key, "value": value} for key, value in self.values.items()
        ])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def call_provider(self):
        packet = {"state": {"user_memory": {"entries": self.memory.entries()}}}
        return self.reader(messages=[{
            "role": "system",
            "content": "APPLICATION-RECORDED TASK DATA\n" + json.dumps(packet),
        }])

    def __call__(self, *, messages):
        for _ in range(self.api.setup_calls):
            self.call_provider()
        if self.api.setup_error:
            raise RuntimeError("setup failed")

    def invoke(self, *, messages):
        step = self.api.steps.pop(0)
        self.values.update(step.get("values", {}))
        proposal = dict(self.values)
        for _ in range(step.get("provider_calls", 1)):
            proposal = self.call_provider()
        if "pause" in step:
            raise Pause(step["pause"])
        if step.get("error"):
            raise RuntimeError("invoke failed")
        return SimpleNamespace(prepared=object(), response=proposal)


class FakeRegistry:
    def __init__(self, ai):
        self.ai = ai

    def tool(self, **kwargs):
        def decorate(function):
            self.function = function
            return function
        return decorate

    def bind(self, prepared):
        return self

    def dispatch(self, name, proposal):
        mode = self.ai.api.dispatch_mode
        if mode == "reject":
            raise Rejected("rejected before write")
        if mode == "error":
            raise RuntimeError("dispatch failed")
        result = self.function(**proposal)
        if mode == "write_then_reject":
            raise Rejected("rejected after write")
        return result


class FakeAPI:
    MemoryClarificationRequired = Pause
    ActionRejected = Rejected
    ToolRegistry = FakeRegistry

    def __init__(self, steps, *, setup_calls=1, setup_error=False, dispatch_mode="normal"):
        self.steps = list(steps)
        self.setup_calls, self.setup_error = setup_calls, setup_error
        self.dispatch_mode = dispatch_mode

    def MemoryPolicy(self, **kwargs):
        return kwargs

    def auto_connect(self, reader, *, memory):
        return FakeAI(self, reader)


def fixture(kind):
    case = {"id": "fake-" + kind, "kind": kind, "text": "Fixture instruction.",
            "before": ["Export as CSV.", "Reply in English.", "I prefer brief answers."]}
    if kind == "clear":
        case["expected"] = {"export_format": "JSON"}
    elif kind == "uncertain":
        case["targets"] = ["export_format"]
    return case


class ReplayFailureRecordsTests(unittest.TestCase):
    def test_success_counts_actual_setup_calls(self):
        row = replay.run_case(fixture("clear"), FakeAPI([
            {"values": {"export_format": "JSON"}},
        ], setup_calls=2))
        self.assertTrue(row["fixture_passed"])
        self.assertEqual((row["setup_calls"], row["task_calls"], row["provider_boundary_calls"]), (2, 1, 3))
        self.assertEqual(row["wrong_writes"], 0)

    def test_clarification_resume_succeeds(self):
        row = replay.run_case(fixture("uncertain"), FakeAPI([
            {"pause": ["export_format"], "provider_calls": 0},
            {"values": {"export_format": "JSON"}},
        ]))
        self.assertTrue(row["fixture_passed"])
        self.assertEqual(row["clarification_requests"], 1)
        self.assertEqual(row["calls_before_required_clarification"], 0)

    def test_zero_call_uncertainty_cannot_pass(self):
        row = replay.run_case(fixture("uncertain"), FakeAPI([{"provider_calls": 0}]))
        self.assertFalse(row["fixture_passed"])
        self.assertEqual(row["reason"], "missing_clarification")
        self.assertEqual(row["wrong_writes"], 1)
        self.assertEqual(row["task_calls"], 0)

    def test_premature_provider_call_is_retained(self):
        row = replay.run_case(fixture("uncertain"), FakeAPI([{"pause": ["export_format"]}]))
        self.assertFalse(row["fixture_passed"])
        self.assertEqual(row["reason"], "side_effect_before_clarification")
        self.assertEqual(row["calls_before_required_clarification"], 1)
        self.assertEqual(row["calls_before_pause"], 1)
        self.assertEqual(row["writes"], [])

    def test_unexpected_and_repeated_pauses_have_uniform_records(self):
        unexpected = replay.run_case(fixture("clear"), FakeAPI([{"pause": ["response_style"]}]))
        repeated = replay.run_case(fixture("uncertain"), FakeAPI([
            {"pause": ["export_format"], "provider_calls": 0},
            {"pause": ["export_format"], "provider_calls": 0},
        ]))
        success = replay.run_case(fixture("abstain"), FakeAPI([{}]))
        self.assertEqual(set(unexpected), set(success))
        self.assertEqual(set(repeated), set(success))
        self.assertEqual(unexpected["reason"], "unexpected_pause")
        self.assertEqual(unexpected["calls_before_pause"], 1)
        self.assertEqual(unexpected["task_calls"], 1)
        self.assertEqual(repeated["reason"], "repeated_clarification")
        self.assertEqual(repeated["clarification_requests"], 2)
        self.assertFalse(repeated["fixture_passed"])

    def test_setup_and_invoke_errors_are_rows(self):
        setup = replay.run_case(fixture("abstain"), FakeAPI([], setup_calls=2, setup_error=True))
        invoke = replay.run_case(fixture("uncertain"), FakeAPI([{"error": True}]))
        self.assertEqual(set(setup), set(invoke))
        self.assertEqual(setup["reason"], "setup_error")
        self.assertEqual((setup["setup_calls"], setup["task_calls"]), (2, 0))
        self.assertEqual(invoke["reason"], "initial_invoke_error")
        self.assertEqual(invoke["calls_before_required_clarification"], 1)
        self.assertEqual(invoke["errors"][0]["type"], "RuntimeError")

    def test_rejection_after_correct_write_cannot_pass(self):
        row = replay.run_case(fixture("abstain"), FakeAPI([{}], dispatch_mode="write_then_reject"))
        self.assertFalse(row["fixture_passed"])
        self.assertTrue(row["blocked_write"])
        self.assertEqual(row["actual"], row["expected"])
        self.assertEqual(row["wrong_writes"], 1)
        self.assertEqual(row["reason"], "action_rejected")

    def test_dispatch_error_does_not_prevent_next_fixture(self):
        rows = [replay.run_case(fixture("abstain"), api) for api in (
            FakeAPI([{}], dispatch_mode="error"), FakeAPI([{}]),
        )]
        self.assertEqual(rows[0]["reason"], "dispatch_error")
        self.assertFalse(rows[0]["fixture_passed"])
        self.assertTrue(rows[1]["fixture_passed"])


if __name__ == "__main__":
    unittest.main()
