"""Independent offline fixtures for metering and conservative improvement decisions."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from context_relay.harness_eval import (HarnessBudget, HarnessBudgetExceeded, HarnessCase,
    assess_improvement, evaluate_harness, improve_harness)


def cases():
    return [HarnessCase("development", {"number": 2}, {"answer": 4}),
            HarnessCase("holdout", {"number": 3}, {"answer": 6}, split="holdout")]


def run(value, config, context):
    return context.model_call(lambda: {"answer": value["number"] * config["multiplier"]})


def judge(expected, result, trace):
    return {"completed": result == expected, "wrong_action": False, "clarification": False}


def seal_report(report):
    report.pop("report_sha256")
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class HarnessEvaluationTests(unittest.TestCase):
    def evaluate(self, **options):
        settings = dict(cases=cases(), baseline={"multiplier": 1}, candidate={"multiplier": 2},
                        run=run, judge=judge)
        settings.update(options)
        return evaluate_harness(**settings)

    def improve(self, **options):
        settings = dict(cases=cases(), baseline={"multiplier": 1},
                        propose=lambda failures, context: {"multiplier": 2}, run=run, judge=judge)
        settings.update(options)
        return improve_harness(**settings)

    def test_paired_improvement_and_alternating_order(self):
        report = self.evaluate()
        self.assertEqual(assess_improvement(report), {"accepted": True, "reasons": [], "completion_gain": 2,
            "by_split": {"development": {"baseline_completed": 0, "candidate_completed": 1, "completion_gain": 1},
                         "holdout": {"baseline_completed": 0, "candidate_completed": 1, "completion_gain": 1}}})
        self.assertEqual([(row["case_id"], row["variant"]) for row in report["results"]],
                         [("development", "baseline"), ("development", "candidate"),
                          ("holdout", "candidate"), ("holdout", "baseline")])
        self.assertEqual(report["usage"], {"runs": 4, "model_calls": 4, "tool_calls": 0,
                                          "budget_exhausted": False})

    def test_case_and_configuration_are_defensive(self):
        original_input, expected = {"number": 2}, {"answer": 4}
        case = HarnessCase("one", original_input, expected, split="holdout")
        original_input["number"] = expected["answer"] = -1
        returned_input = case.input
        returned_input["number"] = 999
        baseline, candidate, seen = {"multiplier": 1}, {"multiplier": 2}, []
        def mutating_run(value, config, context):
            answer = value["number"] * config["multiplier"]
            value["number"] = config["multiplier"] = 1000
            seen.append((value, config))
            return {"answer": answer}
        def mutating_judge(label, result, trace):
            completed = label == result
            label["answer"] = result["answer"] = 999
            trace["events"].append({"secret": "judge mutation"})
            return {"completed": completed, "wrong_action": False, "clarification": False}
        report = self.evaluate(cases=[case], baseline=baseline, candidate=candidate,
                               run=mutating_run, judge=mutating_judge)
        self.assertTrue(assess_improvement(report)["accepted"])
        self.assertEqual(case.input, {"number": 2})
        self.assertEqual(case.expected, {"answer": 4})
        self.assertEqual((baseline, candidate), ({"multiplier": 1}, {"multiplier": 2}))
        self.assertNotIn("judge mutation", json.dumps(report))

    def test_runner_never_receives_expected_and_report_omits_content(self):
        fixture = [HarnessCase("one", {"prompt": "PRIVATE_INPUT"}, "HIDDEN_LABEL", split="holdout")]
        observed = []
        def workflow(value, config, context):
            observed.append((value, config, context.trace))
            return "PRIVATE_RESULT"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            report = self.evaluate(cases=fixture, run=workflow, report_path=path)
            text = path.read_text(encoding="utf-8") + json.dumps(report)
        self.assertNotIn("HIDDEN_LABEL", json.dumps(observed))
        for secret in ("PRIVATE_INPUT", "PRIVATE_RESULT", "HIDDEN_LABEL"):
            self.assertNotIn(secret, text)

    def test_fresh_working_directory_and_context_cannot_escape_run(self):
        directories, saved = [], []
        def workflow(value, config, context):
            self.assertEqual(list(context.workdir.iterdir()), [])
            (context.workdir / "local-state").write_text("fixture", encoding="utf-8")
            directories.append(context.workdir)
            saved.append(context)
            return run(value, config, context)
        report = self.evaluate(run=workflow)
        self.assertTrue(assess_improvement(report)["accepted"])
        self.assertEqual(len(set(directories)), 4)
        self.assertTrue(all(not path.exists() for path in directories))
        with self.assertRaises(RuntimeError):
            saved[0].tool_call(lambda: self.fail("Closed run executed a tool"))

    def test_model_budget_consumed_before_callback_and_cannot_be_swallowed(self):
        dispatched = []
        def workflow(value, config, context):
            try:
                context.model_call(lambda: dispatched.append(value["number"]))
            except HarnessBudgetExceeded:
                pass
            return {"answer": value["number"] * config["multiplier"]}
        report = self.evaluate(run=workflow, budget=HarnessBudget(max_model_calls=2))
        self.assertEqual(len(dispatched), 2)
        self.assertEqual(report["usage"]["model_calls"], 2)
        self.assertEqual([row["status"] for row in report["results"]],
                         ["scored", "scored", "budget_exhausted", "not_run"])
        self.assertIn("budget_exhausted", assess_improvement(report)["reasons"])

    def test_tool_exception_is_counted_once_and_never_retried(self):
        effects = []
        def effect():
            effects.append("effect happened before error")
            raise RuntimeError("PRIVATE_FAILURE_DETAIL")
        def workflow(value, config, context):
            return context.tool_call(effect)
        report = self.evaluate(run=workflow, budget=HarnessBudget(max_tool_calls=1))
        self.assertEqual(len(effects), 1)
        self.assertEqual(report["usage"]["tool_calls"], 1)
        self.assertEqual(report["results"][0]["status"], "error")
        self.assertEqual(report["results"][0]["trace"]["events"],
                         [{"kind": "tool_call", "status": "error", "error_type": "RuntimeError"}])
        self.assertNotIn("PRIVATE_FAILURE_DETAIL", json.dumps(report))
        self.assertFalse(assess_improvement(report)["accepted"])

    def test_caught_callback_error_still_disqualifies_run(self):
        def fail():
            raise ValueError("error")
        def workflow(value, config, context):
            try:
                context.model_call(fail)
            except ValueError:
                pass
            return {"answer": value["number"] * config["multiplier"]}
        report = self.evaluate(run=workflow)
        self.assertTrue(all(row["status"] == "error" for row in report["results"]))
        self.assertEqual(report["usage"]["model_calls"], 4)
        self.assertFalse(assess_improvement(report)["accepted"])

    def test_caught_write_then_error_cannot_dispatch_another_effect(self):
        effects = []
        def effect():
            effects.append("written")
            raise ValueError("Failure after write")
        def workflow(value, config, context):
            try:
                context.tool_call(effect)
            except ValueError:
                pass
            with self.assertRaises(RuntimeError):
                context.tool_call(lambda: effects.append("unsafe second write"))
            with self.assertRaises(RuntimeError):
                context.model_call(lambda: self.fail("No subsequent inference is allowed"))
            return {"answer": value["number"] * config["multiplier"]}
        report = self.evaluate(run=workflow)
        self.assertEqual(effects, ["written"] * 4)
        self.assertEqual(report["usage"]["tool_calls"], 4)
        self.assertEqual(report["usage"]["model_calls"], 0)
        self.assertFalse(assess_improvement(report)["accepted"])

    def test_max_runs_rejects_before_any_work(self):
        with self.assertRaises(ValueError):
            self.evaluate(budget=HarnessBudget(max_runs=3),
                          run=lambda *_: self.fail("Preflight dispatched a workflow"))

    def test_per_case_regression_cannot_hide_behind_aggregate_gain(self):
        fixture = [HarnessCase(name, {"name": name}, True, split="holdout") for name in ("a", "b", "c")]
        def workflow(value, config, context):
            return value["name"] in config["passes"]
        report = self.evaluate(cases=fixture, baseline={"passes": ["a"]}, candidate={"passes": ["b", "c"]}, run=workflow)
        decision = assess_improvement(report)
        self.assertEqual(decision["completion_gain"], 1)
        self.assertIn("completion_regression", decision["reasons"])
        self.assertFalse(decision["accepted"])

    def test_wrong_candidate_action_blocks_improvement(self):
        def score(expected, result, trace):
            wrong = result["answer"] == 6
            return {"completed": result == expected and not wrong, "wrong_action": wrong, "clarification": False}
        decision = assess_improvement(self.evaluate(judge=score))
        self.assertIn("candidate_wrong_action", decision["reasons"])
        self.assertFalse(decision["accepted"])

    def test_invalid_scorer_fails_closed(self):
        invalid = [True, {}, {"completed": 1, "wrong_action": False, "clarification": False},
                   {"completed": True, "wrong_action": True, "clarification": False},
                   {"completed": True, "wrong_action": False, "clarification": True},
                   {"completed": True, "wrong_action": False, "clarification": False, "extra": True}]
        for score in invalid:
            with self.subTest(score=score):
                report = self.evaluate(judge=lambda *_: score)
                self.assertFalse(assess_improvement(report)["accepted"])
                self.assertTrue(all(row["status"] == "error" for row in report["results"]))

    def test_missing_holdout_no_gain_and_unchanged_config_are_rejected(self):
        report = self.evaluate(cases=[cases()[0]])
        self.assertIn("missing_holdout", assess_improvement(report)["reasons"])
        report = self.evaluate(candidate={"multiplier": 1})
        self.assertIn("no_completion_gain", assess_improvement(report)["reasons"])
        self.assertIn("unchanged_configuration", assess_improvement(report)["reasons"])

    def test_modified_and_partial_reports_never_approve(self):
        report = self.evaluate()
        report["results"][0]["config_sha256"] = "changed"
        self.assertIn("invalid_report", assess_improvement(report)["reasons"])
        for partial in ({}, None, [], {"schema_version": 1}):
            self.assertFalse(assess_improvement(partial)["accepted"])

    def test_inconsistent_fingerprints_and_accounting_fail_even_with_checksum(self):
        report = self.evaluate()
        report["results"][0]["config_sha256"] = report["candidate_sha256"]
        seal_report(report)
        self.assertIn("configuration_mismatch", assess_improvement(report)["reasons"])
        report = self.evaluate()
        report["usage"]["model_calls"] = 0
        seal_report(report)
        self.assertIn("invalid_accounting", assess_improvement(report)["reasons"])
        report = self.evaluate()
        report["results"].pop()
        seal_report(report)
        self.assertIn("incomplete_or_failed_run", assess_improvement(report)["reasons"])

    def test_cross_split_copied_answer_is_rejected_before_any_callback(self):
        for heldout_label in ("COPIED_ANSWER", "DIFFERENT_LABEL"):
            with self.subTest(expected=heldout_label), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "trace.jsonl"
                fixture = [HarnessCase("dev", {"task": "same task", "version": 1}, "COPIED_ANSWER"),
                           HarnessCase("fake-holdout", {"version": 1, "task": "same task"}, heldout_label,
                                       split="holdout")]
                def workflow(*_):
                    self.fail("Overlapping inputs must be rejected before workflow execution")
                def copied_answer(failures, context):
                    self.fail("Overlapping inputs must be rejected before proposing a copied answer")
                with self.assertRaisesRegex(ValueError, "must not overlap"):
                    self.improve(cases=fixture, run=workflow, propose=copied_answer, report_path=path)
                self.assertFalse(path.exists())
                with self.assertRaisesRegex(ValueError, "must not overlap"):
                    self.evaluate(cases=fixture, run=workflow)

    def test_recomputed_report_checksum_does_not_hide_cross_split_overlap(self):
        report = self.evaluate()
        report["cases"][1]["input_sha256"] = report["cases"][0]["input_sha256"]
        seal_report(report)
        decision = assess_improvement(report)
        self.assertIn("overlapping_split_inputs", decision["reasons"])
        self.assertFalse(decision["accepted"])

    def test_development_only_success_with_zero_holdout_completion_is_rejected(self):
        def workflow(value, config, context):
            return run(value, config, context) if value["number"] == 2 else {"answer": 0}
        decision = assess_improvement(self.evaluate(run=workflow))
        self.assertFalse(decision["accepted"])
        self.assertIn("no_holdout_completion", decision["reasons"])
        self.assertEqual(decision["completion_gain"], 1)
        self.assertEqual(decision["by_split"], {
            "development": {"baseline_completed": 0, "candidate_completed": 1, "completion_gain": 1},
            "holdout": {"baseline_completed": 0, "candidate_completed": 0, "completion_gain": 0}})

    def test_known_failure_repair_can_keep_already_successful_holdout(self):
        def workflow(value, config, context):
            return run(value, config, context) if value["number"] == 2 else {"answer": 6}
        decision = assess_improvement(self.evaluate(run=workflow))
        self.assertTrue(decision["accepted"])
        self.assertEqual(decision["completion_gain"], 1)
        self.assertEqual(decision["by_split"]["holdout"],
                         {"baseline_completed": 1, "candidate_completed": 1, "completion_gain": 0})

    def test_invalid_surrogate_case_id_fails_during_construction_before_effects(self):
        effects = []
        with self.assertRaises(UnicodeEncodeError):
            case = HarnessCase("invalid-\ud800", {"number": 2}, {"answer": 4}, split="holdout")
            self.evaluate(cases=[case], run=lambda *_: effects.append("must never happen"))
        self.assertEqual(effects, [])

    def test_synchronous_callbacks_must_not_return_deferred_results(self):
        async def deferred():
            return {"answer": 4}
        report = self.evaluate(run=lambda *_: deferred())
        self.assertTrue(all(row["status"] == "error" for row in report["results"]))
        self.assertFalse(assess_improvement(report)["accepted"])
        with self.assertRaises(TypeError):
            self.evaluate(run=deferred)

    def test_invalid_config_cases_and_output_are_rejected(self):
        for config in (None, [], {"x": float("inf")}, {"x": object()}, {"x": "a" * 64000}):
            with self.subTest(config=type(config).__name__):
                with self.assertRaises((TypeError, ValueError)):
                    self.evaluate(candidate=config)
        with self.assertRaises(ValueError):
            self.evaluate(cases=[cases()[0], cases()[0]])
        with self.assertRaises(ValueError):
            HarnessCase("a", {}, "label", split="train")
        report = self.evaluate(run=lambda *_: float("nan"))
        self.assertFalse(assess_improvement(report)["accepted"])

    def test_budget_requires_bounded_real_integers_and_positive_run_count(self):
        for value in (True, -1, 1.5, 10001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HarnessBudget(max_tool_calls=value)
        with self.assertRaises(ValueError):
            HarnessBudget(max_runs=0)

    def test_zero_call_budgets_block_before_any_callback_and_keep_baseline(self):
        for kind in ("model_call", "tool_call"):
            with self.subTest(kind=kind):
                dispatched = []
                def workflow(value, config, context):
                    return getattr(context, kind)(lambda: dispatched.append("unwanted call"))
                selection = self.improve(run=workflow,
                    budget=HarnessBudget(max_model_calls=0, max_tool_calls=0))
                self.assertEqual(dispatched, [])
                self.assertEqual(selection["selected"], "baseline")
                self.assertFalse(selection["decision"]["accepted"])
                self.assertIn("budget_exhausted", selection["decision"]["reasons"])
                self.assertEqual(selection["report"]["usage"],
                    {"runs": 1, "model_calls": 0, "tool_calls": 0, "budget_exhausted": True})

    def test_pure_local_improvement_can_run_with_zero_model_and_tool_budget(self):
        def local_workflow(value, config, context):
            return {"answer": value["number"] * config["multiplier"]}
        selection = self.improve(run=local_workflow,
            budget=HarnessBudget(max_model_calls=0, max_tool_calls=0))
        self.assertEqual(selection["selected"], "candidate")
        self.assertTrue(selection["decision"]["accepted"])
        self.assertEqual(selection["report"]["usage"],
            {"runs": 4, "model_calls": 0, "tool_calls": 0, "budget_exhausted": False})

    def test_interruption_journal_does_not_contain_final_report(self):
        def interrupt(*_):
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            with self.assertRaises(KeyboardInterrupt):
                self.evaluate(run=interrupt, report_path=path)
            events = [json.loads(line)["event"] for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events, ["manifest", "started"])
            with self.assertRaises(FileExistsError):
                self.evaluate(report_path=path)

    def test_improvement_proposal_gets_only_failed_development_data(self):
        fixture = [HarnessCase("dev-failed", {"number": 2}, {"answer": 4}),
                   HarnessCase("dev-good", {"number": 0}, {"answer": 0}),
                   HarnessCase("SECRET_HOLDOUT_ID", {"number": 3, "private": "HOLDOUT_INPUT"},
                               {"answer": 6}, split="holdout")]
        proposals, runs = [], []
        def propose(failures, context):
            proposals.append(failures)
            return {"multiplier": 2}
        def workflow(value, config, context):
            runs.append((value["number"], config["multiplier"]))
            return run(value, config, context)
        selection = self.improve(cases=fixture, propose=propose, run=workflow)
        self.assertEqual(selection["selected"], "candidate")
        self.assertEqual(len(proposals), 1)
        self.assertEqual([case["id"] for case in proposals[0]], ["dev-failed"])
        self.assertNotIn("HOLDOUT", json.dumps(proposals))
        self.assertEqual(runs, [(2, 1), (0, 1), (3, 1), (2, 2), (0, 2), (3, 2)])
        self.assertEqual(selection["report"]["usage"]["model_calls"], 6)

    def test_failed_or_unhelpful_proposal_keeps_baseline(self):
        def failed(_, context):
            raise RuntimeError("PRIVATE_PROPOSAL_ERROR")
        for proposer in (failed, lambda *_: {"multiplier": 0}, lambda *_: "not a config"):
            with self.subTest(proposer=proposer):
                selection = self.improve(propose=proposer)
                self.assertEqual(selection["selected"], "baseline")
                self.assertEqual(selection["config"], {"multiplier": 1})
                self.assertFalse(selection["decision"]["accepted"])
                self.assertNotIn("PRIVATE_PROPOSAL_ERROR", json.dumps(selection))

    def test_no_development_failure_does_not_invoke_proposer(self):
        selection = self.improve(baseline={"multiplier": 2},
                                 propose=lambda *_: self.fail("No failures need a proposal"))
        self.assertEqual(selection["selected"], "baseline")
        self.assertEqual(selection["proposal_status"], "no_development_failures")
        self.assertEqual(selection["report"]["usage"]["runs"], 2)

    def test_proposer_mutation_does_not_change_frozen_cases(self):
        fixture = cases()
        candidate = {"multiplier": 2}
        def propose(failures, context):
            failures[0]["input"]["number"] = 1000
            failures[0]["expected"]["answer"] = -1
            return candidate
        selection = self.improve(cases=fixture, propose=propose)
        candidate["multiplier"] = 999
        self.assertEqual(selection["selected"], "candidate")
        self.assertEqual(selection["config"], {"multiplier": 2})
        self.assertEqual(fixture[0].input, {"number": 2})
        self.assertEqual(fixture[0].expected, {"answer": 4})

    def test_proposal_model_call_uses_shared_budget_and_trace(self):
        invoked = []
        def propose(failures, context):
            self.assertEqual(list(context.workdir.iterdir()), [])
            return context.model_call(lambda: invoked.append("proposed") or {"multiplier": 2})
        selection = self.improve(propose=propose, budget=HarnessBudget(max_model_calls=5))
        self.assertEqual(selection["selected"], "candidate")
        self.assertEqual(invoked, ["proposed"])
        self.assertEqual(selection["report"]["usage"]["model_calls"], 5)
        self.assertEqual(selection["report"]["proposal_trace"], {"model_calls": 1, "tool_calls": 0,
            "events": [{"kind": "model_call", "status": "returned"}]})

    def test_proposal_budget_exhaustion_falls_back_without_another_model_call(self):
        def propose(failures, context):
            try:
                context.model_call(lambda: self.fail("Proposal must not exceed budget"))
            except HarnessBudgetExceeded:
                pass
            return {"multiplier": 2}
        selection = self.improve(propose=propose, budget=HarnessBudget(max_model_calls=2))
        self.assertEqual(selection["selected"], "baseline")
        self.assertEqual(selection["proposal_status"], "budget_exhausted")
        self.assertEqual(selection["report"]["usage"]["model_calls"], 2)
        self.assertEqual(selection["report"]["usage"]["runs"], 2)
        self.assertIn("budget_exhausted", selection["decision"]["reasons"])

    def test_proposal_tool_is_forbidden_even_if_error_is_caught(self):
        def propose(failures, context):
            with self.assertRaises(RuntimeError):
                context.tool_call(lambda: self.fail("Proposer cannot execute tools"))
            return {"multiplier": 2}
        selection = self.improve(propose=propose)
        self.assertEqual(selection["selected"], "baseline")
        self.assertEqual(selection["proposal_status"], "failed")
        self.assertEqual(selection["report"]["usage"]["tool_calls"], 0)

    def test_caught_proposer_model_failure_cannot_retry_or_approve(self):
        attempts = []
        def fail():
            attempts.append("attempt")
            raise ValueError("Provider failed")
        def propose(failures, context):
            try:
                context.model_call(fail)
            except ValueError:
                pass
            with self.assertRaises(RuntimeError):
                context.model_call(lambda: self.fail("Proposer must not retry"))
            return {"multiplier": 2}
        selection = self.improve(propose=propose)
        self.assertEqual(selection["selected"], "baseline")
        self.assertEqual(attempts, ["attempt"])
        self.assertEqual(selection["report"]["usage"]["model_calls"], 3)
        self.assertIn("proposal_failed", selection["decision"]["reasons"])


if __name__ == "__main__":
    unittest.main()
