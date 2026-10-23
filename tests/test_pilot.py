"""Pilot accounting and isolation contracts; all callbacks are offline stand-ins.

These tests measure the runner, not model intelligence or a real customer pilot.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from context_relay import RelayConfig
from context_relay.models import encode
from context_relay.pilot import run_pilot


def case(name="one", text="Help with this request.", expected="ok"):
    return {"id": name, "messages": [{"role": "user", "content": text}], "expected": expected}


def baseline(messages, *, max_chars):
    return messages


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "pilot.jsonl"

    def options(self, **overrides):
        return {"model_call": lambda **kwargs: "ok", "baseline_prepare": baseline,
                "baseline_description": "Offline identity preparation for runner tests",
                "model_description": "Offline complete-text callback; no model inference",
                "dataset_kind": "synthetic", "report_path": self.path, **overrides}

    def records(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def results(self):
        return [row for row in self.records() if row["event"] == "result"]

    def test_labels_are_hidden_and_both_modes_share_defensive_options_and_cap(self):
        inputs = [case(expected="HELDOUT-DO-NOT-SEND")]
        original = copy.deepcopy(inputs)
        model_options = {"model": "test-model", "options": {"temperature": 0}}
        requests, prepared, scored = [], [], []
        def actual_baseline(messages, *, max_chars):
            prepared.append((copy.deepcopy(messages), max_chars))
            messages[-1]["content"] = "Baseline's actual transformed request."
            return messages
        def model(**kwargs):
            requests.append(copy.deepcopy(kwargs))
            kwargs["messages"][-1]["content"] = "provider mutation"
            kwargs["options"]["temperature"] = 99
            return "some answer"
        def score(text, expected):
            scored.append((text, expected))
            return {"runner_test_criterion": True}
        summary = run_pilot(inputs, **self.options(model_call=model, baseline_prepare=actual_baseline,
            model_options=model_options, config=RelayConfig(max_chars=5000), score=score))
        self.assertEqual(prepared, [(original[0]["messages"], 5000)])
        self.assertEqual(inputs, original)
        self.assertEqual(model_options["options"]["temperature"], 0)
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request["options"] == {"temperature": 0} for request in requests))
        self.assertTrue(all(request["model"] == "test-model" for request in requests))
        self.assertNotIn("HELDOUT-DO-NOT-SEND", encode(requests))
        self.assertNotIn("HELDOUT-DO-NOT-SEND", encode(prepared[0][0]))
        self.assertEqual(scored, [("some answer", "HELDOUT-DO-NOT-SEND")] * 2)
        self.assertEqual(requests[0]["messages"][-1]["content"], "Baseline's actual transformed request.")
        self.assertEqual(summary["comparison_budget_chars"], 5000)

    def test_alternating_order_and_each_case_repeat_has_fresh_memory(self):
        requests = []
        def model(**kwargs):
            requests.append(copy.deepcopy(kwargs["messages"]))
            return "ok"
        summary = run_pilot([case("first", "Export as CSV."), case("second", "Continue.")],
                            **self.options(model_call=model, repeats=2, max_calls=8, include_content=True))
        rows = self.results()
        self.assertEqual([row["mode"] for row in rows],
                         ["baseline", "relay", "relay", "baseline", "relay", "baseline", "baseline", "relay"])
        for row in rows:
            if row["mode"] == "relay":
                block = next(message["content"] for message in row["messages"]
                             if message["content"].startswith("APPLICATION-RECORDED TASK DATA\n"))
                entries = json.loads(block[block.index("{"):])["state"]["user_memory"]["entries"]
                if row["case_id"] == "first":
                    self.assertEqual([(entry["key"], entry["value"]) for entry in entries], [("export_format", "CSV")])
                else:
                    self.assertEqual(entries, [])
        self.assertEqual(len(requests), 8)
        self.assertEqual(summary["totals"]["baseline"]["attempts"], 4)
        self.assertEqual(summary["totals"]["relay"]["provider_calls"], 4)

    def test_default_report_omits_raw_inputs_answers_labels_options_and_error_messages(self):
        def failed(**kwargs):
            raise RuntimeError("PRIVATE-PROVIDER-ERROR")
        run_pilot([case(text="PRIVATE-PROMPT", expected="PRIVATE-LABEL")], **self.options(
            model_call=failed, model_options={"api_key": "PRIVATE-OPTION"}))
        raw = self.path.read_text(encoding="utf-8")
        for secret in ("PRIVATE-PROMPT", "PRIVATE-LABEL", "PRIVATE-OPTION", "PRIVATE-PROVIDER-ERROR"):
            self.assertNotIn(secret, raw)
        self.assertTrue(all(row["error_type"] == "RuntimeError" for row in self.results()))
        self.assertTrue(all(row["provider_calls"] == 1 for row in self.results()))
        self.assertTrue(all("messages" not in row and "expected" not in row and "error" not in row
                            for row in self.results()))

    def test_content_opt_in_records_answer_label_request_and_capped_oversized_answer(self):
        scorer_calls = []
        summary = run_pilot([case(text="Input text", expected="expected answer")], **self.options(
            model_call=lambda **kwargs: "very-long-answer", include_content=True, max_response_chars=4,
            score=lambda *args: scorer_calls.append(args) or {"would_pass": True}))
        for row in self.results():
            self.assertEqual(row["response"], "very")
            self.assertTrue(row["response_truncated"])
            self.assertEqual(row["response_chars"], len("very-long-answer"))
            self.assertEqual(row["expected"], "expected answer")
            self.assertIn("messages", row)
            self.assertFalse(row["completed"])
        self.assertEqual(scorer_calls, [])
        self.assertEqual(summary["totals"]["baseline"]["completed"], 0)
        self.assertEqual(summary["totals"]["relay"]["failed"], 1)

    def test_report_is_exclusive_and_existing_evidence_is_never_overwritten(self):
        self.path.write_text("prior evidence\n", encoding="utf-8")
        calls = []
        with self.assertRaises(FileExistsError):
            run_pilot([case()], **self.options(model_call=lambda **kwargs: calls.append("model"),
                baseline_prepare=lambda *args, **kwargs: calls.append("baseline")))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "prior evidence\n")
        self.assertEqual(calls, [])

    def test_planned_call_cap_is_checked_before_report_or_any_callback(self):
        calls = []
        with self.assertRaisesRegex(ValueError, "max_calls"):
            run_pilot([case()], **self.options(max_calls=1,
                model_call=lambda **kwargs: calls.append("model"),
                baseline_prepare=lambda *args, **kwargs: calls.append("baseline")))
        self.assertEqual(calls, [])
        self.assertFalse(self.path.exists())

    def test_invalid_later_case_is_rejected_before_any_partial_run(self):
        calls = []
        malformed = case("two")
        malformed["messages"] = [{"role": "assistant", "content": "No initial user."}]
        with self.assertRaises(ValueError):
            run_pilot([case(), malformed], **self.options(model_call=lambda **kwargs: calls.append(kwargs)))
        self.assertEqual(calls, [])
        self.assertFalse(self.path.exists())

    def test_budget_block_is_accounted_without_provider_call_or_success(self):
        calls = []
        def oversized(messages, *, max_chars):
            return [{"role": "user", "content": "x" * (max_chars + 1)}]
        summary = run_pilot([case()], **self.options(config=RelayConfig(max_chars=4000),
            model_call=lambda **kwargs: calls.append(kwargs) or "ok", baseline_prepare=oversized))
        blocked, accepted = self.results()
        self.assertEqual(blocked["error_type"], "BudgetExceeded")
        self.assertEqual(blocked["provider_calls"], 0)
        self.assertFalse(blocked["completed"])
        self.assertTrue(accepted["completed"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(summary["totals"]["baseline"]["failed"], 1)

    def test_preprocessing_error_is_not_replaced_with_a_fabricated_baseline(self):
        calls = []
        def broken_baseline(messages, *, max_chars):
            raise LookupError("actual baseline failed")
        summary = run_pilot([case()], **self.options(baseline_prepare=broken_baseline,
            model_call=lambda **kwargs: calls.append(kwargs) or "ok"))
        first = self.results()[0]
        self.assertEqual(first["mode"], "baseline")
        self.assertEqual(first["error_type"], "LookupError")
        self.assertEqual(first["provider_calls"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(summary["totals"]["baseline"]["completed"], 0)

    def test_provider_failures_and_wrong_answers_never_retry_or_count_as_success(self):
        calls = []
        def model(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise TimeoutError("host timeout")
            return "wrong"
        summary = run_pilot([case()], **self.options(model_call=model, max_calls=2))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(not row["completed"] for row in self.results()))
        self.assertTrue(all(total["completed"] == 0 and total["failed"] == 1
                            for total in summary["totals"].values()))

    def test_pending_review_count_survives_provider_failure_after_memory_preparation(self):
        sample = case()
        sample["messages"] = [{"role": "user", "content": "Keep responses detailed."},
                              {"role": "assistant", "content": "Acknowledged."},
                              {"role": "user", "content": "Make it brief."}]
        def failed(**kwargs):
            raise TimeoutError("host timeout after preparation")
        summary = run_pilot([sample], **self.options(model_call=failed))
        rows = {row["mode"]: row for row in self.results()}
        self.assertEqual(rows["baseline"]["pending_reviews"], 0)
        self.assertEqual(rows["relay"]["pending_reviews"], 1)
        self.assertEqual(summary["totals"]["relay"]["pending_reviews"], 1)
        self.assertFalse(rows["relay"]["completed"])

    def test_empty_response_cannot_be_marked_complete_by_a_permissive_scorer(self):
        scored = []
        summary = run_pilot([case()], **self.options(model_call=lambda **kwargs: " \n\t",
            score=lambda *args: scored.append(args) or {"unchecked": True}))
        self.assertEqual(scored, [])
        self.assertTrue(all(total["completed"] == 0 for total in summary["totals"].values()))
        self.assertTrue(all(row.get("error_type") for row in self.results()))

    def test_scorer_failures_and_non_boolean_or_empty_criteria_stay_failed(self):
        invalid = [{}, {"match": 1}, {"match": "true"}, {"": True}, [], {"match": False}]
        for index, criteria in enumerate(invalid):
            with self.subTest(criteria=criteria):
                self.path = Path(self.directory.name) / f"score-{index}.jsonl"
                summary = run_pilot([case()], **self.options(score=lambda *args: criteria))
                self.assertTrue(all(total["failed"] == 1 and total["completed"] == 0
                                    for total in summary["totals"].values()))
        self.path = Path(self.directory.name) / "score-exception.jsonl"
        def unscorable(*args):
            raise ValueError("cannot grade")
        run_pilot([case()], **self.options(score=unscorable))
        self.assertTrue(all(row["error_type"] == "ValueError" and not row["completed"] for row in self.results()))

    def test_non_text_and_coroutine_responses_are_not_scored_or_leaked(self):
        async def asynchronous():
            return "ok"
        coroutines, scored = [], []
        def wrong_transport(**kwargs):
            coroutine = asynchronous()
            coroutines.append(coroutine)
            return coroutine
        run_pilot([case()], **self.options(model_call=wrong_transport,
            score=lambda *args: scored.append(args) or {"match": True}))
        self.assertEqual(scored, [])
        self.assertTrue(all(coro.cr_frame is None for coro in coroutines))
        self.assertTrue(all(row["error_type"] == "TypeError" and not row["completed"] for row in self.results()))
        self.path = Path(self.directory.name) / "non-text.jsonl"
        run_pilot([case()], **self.options(model_call=lambda **kwargs: {"content": "ok"}))
        self.assertTrue(all(row["error_type"] == "TypeError" for row in self.results()))

    def test_interrupt_retains_flushed_prior_results_and_started_attempt_without_fake_summary(self):
        calls = []
        def interrupted(**kwargs):
            calls.append(kwargs)
            if len(calls) == 3:
                raise KeyboardInterrupt("stop the pilot")
            return "ok"
        with self.assertRaises(KeyboardInterrupt):
            run_pilot([case("first"), case("second")], **self.options(model_call=interrupted))
        rows = self.records()
        self.assertEqual([row["event"] for row in rows],
                         ["manifest", "started", "result", "started", "result", "started"])
        self.assertEqual(rows[-1]["case_id"], "second")
        self.assertEqual(len(calls), 3)

    def test_manual_effort_remains_unknown_even_when_both_modes_succeed(self):
        summary = run_pilot([case()], **self.options())
        self.assertFalse(summary["manual_effort_measured"])
        for row in self.results():
            self.assertTrue(row["completed"])
            self.assertIsNone(row["manual_interventions"])
            self.assertIsNone(row["maintenance_seconds"])


if __name__ == "__main__":
    unittest.main()
