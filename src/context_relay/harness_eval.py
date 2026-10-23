"""Offline workflow comparisons and one proposed, evaluated configuration change.

Callbacks are trusted Python, not sandboxed code. Use simulated tools or an
isolated test destination: this runner never makes live effects safe to replay.
Only calls made through the context are metered; SDK retries, callback timeouts
and external state remain the host's responsibility. Nothing changes live policy.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .middleware import _sync_callable, _sync_result
from .models import clone, encode, nonempty_string


_VALUE_LIMIT = 64000
_REPORT_LIMIT = 8000000
_SCORE_KEYS = {"completed", "wrong_action", "clarification"}


def _bounded(value, *, object_only=False, limit=_VALUE_LIMIT):
    if object_only and type(value) is not dict:
        raise TypeError("Expected a JSON object.")
    text = encode(value)
    if len(text) > limit:
        raise ValueError("Evaluation data exceeds its character limit.")
    text.encode("utf-8")  # Reject strings that cannot be written to the journal.
    return json.loads(text)


def _digest(value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class HarnessCase:
    """A bounded case whose expected value is withheld from the workflow."""

    id: str
    split: str
    _input_json: str
    _expected_json: str

    def __init__(self, id, input, expected, *, split="development"):
        nonempty_string(id, "Case id", limit=128)
        id.encode("utf-8")
        if split not in ("development", "holdout"):
            raise ValueError("split must be development or holdout.")
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "split", split)
        object.__setattr__(self, "_input_json", encode(_bounded(input, object_only=True)))
        object.__setattr__(self, "_expected_json", encode(_bounded(expected)))

    @property
    def input(self):
        return json.loads(self._input_json)

    @property
    def expected(self):
        return json.loads(self._expected_json)


@dataclass(frozen=True, slots=True)
class HarnessBudget:
    """Shared ceilings for both variants, counting failed calls before dispatch.

    A zero model/tool ceiling explicitly forbids that kind of metered call.
    """

    max_runs: int = 200
    max_model_calls: int = 400
    max_tool_calls: int = 400

    def __post_init__(self):
        for name in ("max_runs", "max_model_calls", "max_tool_calls"):
            value = getattr(self, name)
            minimum = 1 if name == "max_runs" else 0
            if type(value) is not int or not minimum <= value <= 10000:
                raise ValueError(f"{name} must be an integer from {minimum} through 10000.")

    def to_dict(self):
        return {name: getattr(self, name) for name in ("max_runs", "max_model_calls", "max_tool_calls")}


class HarnessBudgetExceeded(RuntimeError):
    """No callback was dispatched for the rejected attempt."""


class _Meter:
    def __init__(self, budget):
        self.budget = budget
        self.counts = {"runs": 0, "model_calls": 0, "tool_calls": 0}
        self.exhausted = False

    def consume(self, key):
        if self.exhausted or self.counts[key] >= getattr(self.budget, "max_" + key):
            self.exhausted = True
            raise HarnessBudgetExceeded("Evaluation call budget exhausted; nothing was retried.")
        self.counts[key] += 1


class _Context:
    def __init__(self, meter, workdir, *, allow_tools=True):
        self._meter, self.workdir = meter, Path(workdir)
        self._allow_tools = allow_tools
        self._thread = threading.get_ident()
        self._open, self._failed = True, False
        self._events = []

    @property
    def trace(self):
        return {"model_calls": sum(item["kind"] == "model_call" for item in self._events),
                "tool_calls": sum(item["kind"] == "tool_call" for item in self._events),
                "events": clone(self._events)}

    def _call(self, kind, function, args, kwargs):
        if not self._open or threading.get_ident() != self._thread:
            self._failed = True
            raise RuntimeError("Evaluation calls must run synchronously within their owning run.")
        if self._failed:
            raise RuntimeError("A prior metered call failed; this context cannot dispatch another call.")
        if kind == "tool_call" and not self._allow_tools:
            self._failed = True
            raise RuntimeError("Improvement proposals cannot dispatch tools.")
        try:
            _sync_callable(function)
            self._meter.consume(kind + "s")
        except Exception:
            self._failed = True
            raise
        event = {"kind": kind, "status": "started"}
        self._events.append(event)
        try:
            value = _sync_result(function(*args, **kwargs))
        except BaseException as error:
            event.update(status="error", error_type=type(error).__name__[:128])
            self._failed = True
            raise
        event["status"] = "returned"
        return value

    def model_call(self, function, *args, **kwargs):
        """Call once; this does not disable retries configured inside an SDK."""
        return self._call("model_call", function, args, kwargs)

    def tool_call(self, function, *args, **kwargs):
        """Call a test tool once; failures may follow an effect and are not retried."""
        return self._call("tool_call", function, args, kwargs)


def _score(value):
    if (type(value) is not dict or set(value) != _SCORE_KEYS
            or any(type(item) is not bool for item in value.values())
            or value["completed"] and (value["wrong_action"] or value["clarification"])):
        raise ValueError("judge must return consistent completed, wrong_action and clarification booleans.")
    return clone(value)


def _trace_counts(trace):
    if (type(trace) is not dict or set(trace) != {"model_calls", "tool_calls", "events"}
            or type(trace["events"]) is not list):
        raise ValueError("Invalid trace.")
    counts = {}
    failed = False
    for name in ("model_calls", "tool_calls"):
        count = trace[name]
        if type(count) is not int or count < 0 or count != sum(
                event["kind"] == name[:-1] for event in trace["events"]):
            raise ValueError("Trace count mismatch.")
        counts[name] = count
    for event in trace["events"]:
        if event["kind"] not in ("model_call", "tool_call") or event["status"] not in ("returned", "error"):
            raise ValueError("Invalid or unfinished trace event.")
        failed |= event["status"] == "error"
    return counts, failed


def _overlapping_inputs(cases):
    splits = {}
    for case in cases:
        if splits.setdefault(case["input_sha256"], case["split"]) != case["split"]:
            return True
    return False


class _Experiment:
    def __init__(self, cases, baseline, candidate, run, judge, budget, report_path, design):
        if type(cases) not in (list, tuple) or not 1 <= len(cases) <= 100:
            raise ValueError("Supply 1 through 100 HarnessCase values.")
        if any(not isinstance(case, HarnessCase) for case in cases):
            raise TypeError("Every case must be a HarnessCase.")
        if len({case.id for case in cases}) != len(cases):
            raise ValueError("Case ids must be unique.")
        if sum(len(case._input_json) + len(case._expected_json) for case in cases) > 2000000:
            raise ValueError("The case set exceeds 2000000 characters.")
        manifest = [{"id": case.id, "split": case.split, "input_sha256": _digest(case.input),
                     "expected_sha256": _digest(case.expected)} for case in cases]
        if _overlapping_inputs(manifest):
            raise ValueError("Development and holdout inputs must not overlap, even with different expected values.")
        _sync_callable(run)
        _sync_callable(judge)
        budget = HarnessBudget() if budget is None else budget
        if not isinstance(budget, HarnessBudget):
            raise TypeError("budget must be a HarnessBudget.")
        if len(cases) * 2 > budget.max_runs:
            raise ValueError("Both variants must fit max_runs before any callback starts.")
        self.cases, self.run, self.judge = tuple(cases), run, judge
        self.configs = {"baseline": _bounded(baseline, object_only=True)}
        if candidate is not None:
            self.configs["candidate"] = _bounded(candidate, object_only=True)
        self.meter, self.stream = _Meter(budget), None
        self.report = {"schema_version": 1, "design": design,
            "baseline_sha256": _digest(self.configs["baseline"]), "candidate_sha256": None,
            "cases": manifest,
            "budget": budget.to_dict(), "results": [], "usage": {}, "proposal_trace": None,
            "limitations": ["Trusted callbacks; temporary directories are not a security sandbox.",
                "Only context calls are metered; SDK retries and callback deadlines are host-owned.",
                "Scores and configuration identities are host declarations, not proof of future reliability.",
                "Hashes omit raw content and do not provide transcript replay or authenticated approval."]}
        if candidate is not None:
            self.report["candidate_sha256"] = _digest(self.configs["candidate"])
        if report_path is not None:
            destination = Path(report_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.stream = destination.open("x", encoding="utf-8")

    def __enter__(self):
        try:
            self.write("manifest", self.report)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        if self.stream is not None:
            self.stream.close()

    def write(self, event, data):
        if self.stream is not None:
            self.stream.write(encode({"event": event, "data": data}) + "\n")
            self.stream.flush()

    def execute(self, case, variant):
        config = self.configs[variant]
        row = {"case_id": case.id, "split": case.split, "variant": variant,
               "config_sha256": _digest(config), "status": "not_run", "score": None,
               "trace": {"model_calls": 0, "tool_calls": 0, "events": []}}
        result = None
        self.write("started", {"case_id": case.id, "variant": variant})
        if not self.meter.exhausted:
            self.meter.consume("runs")
            with tempfile.TemporaryDirectory(prefix="relay-eval-") as directory:
                context = _Context(self.meter, directory)
                try:
                    result = _bounded(_sync_result(self.run(case.input, clone(config), context)))
                    if self.meter.exhausted:
                        raise HarnessBudgetExceeded()
                    if context._failed:
                        raise RuntimeError("A metered callback failed.")
                    row["result_sha256"] = _digest(result)
                    row["score"] = _score(_sync_result(self.judge(case.expected, clone(result), context.trace)))
                    row["status"] = "scored"
                except Exception as error:
                    row.update(status="budget_exhausted" if self.meter.exhausted else "error",
                               error_type=type(error).__name__[:128])
                finally:
                    context._open = False
                    row["trace"] = context.trace
        self.report["results"].append(row)
        self.write("result", row)
        return clone(row), clone(result)

    def finish(self):
        self.report["usage"] = {**self.meter.counts, "budget_exhausted": self.meter.exhausted}
        report = _bounded(self.report, limit=_REPORT_LIMIT)
        report["report_sha256"] = _digest(report)
        self.write("report", report)
        return clone(report)


def evaluate_harness(cases, baseline, candidate, run, judge, *, budget=None, report_path=None):
    """Compare configurations on the same frozen cases; expected labels stay hidden.

    ``run(input, config, context)`` returns bounded JSON. ``judge(expected,
    result, trace)`` returns exactly three booleans: completed, wrong_action,
    clarification. Variant order alternates per case. Each run receives a new
    temporary working directory and copied JSON; external state is host-owned.
    Development and holdout inputs must be distinct; duplicate input hashes
    across splits are rejected before any callback, regardless of their labels.
    Reports contain hashes and outcomes, never raw inputs, labels or results.
    A journal is created exclusively and flushed at each attempt; interruption
    leaves an incomplete journal, never an approved result.
    """
    if candidate is None:
        raise TypeError("candidate must be a JSON configuration object.")
    with _Experiment(cases, baseline, candidate, run, judge, budget, report_path, "alternating_pairs") as trial:
        for index, case in enumerate(trial.cases):
            for variant in (("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")):
                trial.execute(case, variant)
        return trial.finish()


def assess_improvement(report):
    """Conservative local decision, not an authorization token or statistical proof.

    Require complete, error-free paired scores, distinct development/holdout
    inputs, a completed candidate holdout, no wrong candidate action or per-case
    regression, and an overall completion gain. Split totals distinguish known
    failure repair from holdout improvement. The report checksum detects
    accidental edits; a caller able to rewrite it can also recompute it.
    """
    reasons, gain = [], 0
    by_split = {split: {"baseline_completed": 0, "candidate_completed": 0, "completion_gain": 0}
                for split in ("development", "holdout")}
    try:
        data = _bounded(report, object_only=True, limit=_REPORT_LIMIT)
        checksum = data.pop("report_sha256")
        if checksum != _digest(data):
            raise ValueError("Report checksum mismatch.")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("Unsupported report schema.")
        HarnessBudget(**data["budget"])
        cases, results = data["cases"], data["results"]
        if type(cases) is not list or not cases or type(results) is not list:
            raise ValueError("Missing expected cases or results.")
        ids = {case["id"] for case in cases}
        if len(ids) != len(cases) or any(case["split"] not in ("development", "holdout") for case in cases):
            raise ValueError("Invalid expected cases.")
        if _overlapping_inputs(cases):
            reasons.append("overlapping_split_inputs")
        if not any(case["split"] == "holdout" for case in cases):
            reasons.append("missing_holdout")
        if data["candidate_sha256"] is None:
            reasons.append("no_candidate")
        elif data["baseline_sha256"] == data["candidate_sha256"]:
            reasons.append("unchanged_configuration")
        rows, measured = {}, {"runs": 0, "model_calls": 0, "tool_calls": 0}
        for row in results:
            key = row["case_id"], row["variant"]
            if key in rows or key[0] not in ids or key[1] not in ("baseline", "candidate"):
                raise ValueError("Unexpected or duplicate result.")
            if row["config_sha256"] != data[key[1] + "_sha256"]:
                reasons.append("configuration_mismatch")
            if row["status"] not in ("scored", "error", "budget_exhausted", "not_run"):
                raise ValueError("Unsupported result status.")
            measured["runs"] += row["status"] != "not_run"
            counts, failed = _trace_counts(row["trace"])
            for name, count in counts.items():
                measured[name] += count
            if failed and row["status"] == "scored":
                reasons.append("incomplete_or_failed_run")
            rows[key] = row
        if data["proposal_trace"] is not None:
            counts, failed = _trace_counts(data["proposal_trace"])
            if failed or counts["tool_calls"]:
                reasons.append("proposal_failed")
            for name, count in counts.items():
                measured[name] += count
        for case in cases:
            scores = {}
            for mode in ("baseline", "candidate"):
                row = rows.get((case["id"], mode))
                if row is None or row["status"] != "scored":
                    reasons.append("incomplete_or_failed_run")
                    continue
                if row["split"] != case["split"]:
                    raise ValueError("Case split mismatch.")
                scores[mode] = _score(row["score"])
                by_split[case["split"]][mode + "_completed"] += int(scores[mode]["completed"])
                if mode == "candidate" and scores[mode]["wrong_action"]:
                    reasons.append("candidate_wrong_action")
            if len(scores) != 2:
                continue
            before, after = scores["baseline"], scores["candidate"]
            if before["completed"] and not after["completed"]:
                reasons.append("completion_regression")
        if not by_split["holdout"]["candidate_completed"]:
            reasons.append("no_holdout_completion")
        if data["usage"]["budget_exhausted"] is not False:
            reasons.append("budget_exhausted")
        for name in ("runs", "model_calls", "tool_calls"):
            count = data["usage"][name]
            if (type(count) is not int or count != measured[name]
                    or count > data["budget"]["max_" + name]):
                reasons.append("invalid_accounting")
    except (KeyError, TypeError, ValueError, RecursionError):
        reasons.append("invalid_report")
    for counts in by_split.values():
        counts["completion_gain"] = counts["candidate_completed"] - counts["baseline_completed"]
        gain += counts["completion_gain"]
    if gain <= 0:
        reasons.append("no_completion_gain")
    return {"accepted": not reasons, "reasons": sorted(set(reasons)),
            "completion_gain": gain, "by_split": by_split}


def improve_harness(cases, baseline, propose, run, judge, *, budget=None, report_path=None):
    """Try at most one JSON configuration proposal, keeping baseline on failure.

    ``propose(development_failures, context)`` sees only failed development inputs,
    labels, results and traces. It never receives holdout data or live state.
    It is invoked at most once with a fresh working directory. Its model_call
    uses the shared budget; tool_call is forbidden. A failed metered call stops
    further calls even if its exception is caught. Unwrapped I/O remains host-owned.
    The baseline is evaluated once, not repeated after the proposal.
    The selected config is returned as data; no code or policy is installed.
    """
    _sync_callable(propose)
    with _Experiment(cases, baseline, None, run, judge, budget, report_path, "baseline_then_candidate") as trial:
        failures = []
        for case in trial.cases:
            row, result = trial.execute(case, "baseline")
            if case.split == "development" and (row["status"] != "scored" or not row["score"]["completed"]):
                failures.append({"id": case.id, "input": case.input, "expected": case.expected,
                                 "result": result, "score": row["score"], "status": row["status"], "trace": row["trace"]})
        proposal_status = "no_development_failures"
        if failures and not trial.meter.exhausted:
            trial.write("proposal_started", {})
            with tempfile.TemporaryDirectory(prefix="relay-proposal-") as directory:
                context = _Context(trial.meter, directory, allow_tools=False)
                try:
                    candidate = _bounded(_sync_result(propose(clone(failures), context)), object_only=True)
                    if trial.meter.exhausted:
                        raise HarnessBudgetExceeded()
                    if context._failed:
                        raise RuntimeError("The proposal's metered callback failed.")
                    trial.configs["candidate"] = candidate
                    trial.report["candidate_sha256"] = _digest(candidate)
                    proposal_status = "evaluated"
                except Exception as error:
                    proposal_status = "budget_exhausted" if trial.meter.exhausted else "failed"
                    trial.report["proposal_error_type"] = type(error).__name__[:128]
                finally:
                    context._open = False
                    trial.report["proposal_trace"] = context.trace
            trial.write("proposal_finished", {"status": proposal_status, "trace": trial.report["proposal_trace"]})
            if "candidate" in trial.configs:
                for case in trial.cases:
                    trial.execute(case, "candidate")
        elif trial.meter.exhausted:
            proposal_status = "budget_exhausted"
        trial.report["proposal_status"] = proposal_status
        report = trial.finish()
        decision = assess_improvement(report)
        selected = "candidate" if decision["accepted"] else "baseline"
        return {"selected": selected, "config": clone(trial.configs[selected]),
                "decision": decision, "report": report, "proposal_status": proposal_status}
