"""A free pilot starter: reviewed application memory versus Relay, with real files.

Both variants receive the same fixed proposals and source rows. The baseline
already validates and repairs arguments; it is not an unprotected agent.
Reviewer replies are scripted examples, not measured human work or a live pilot.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import csv
import hashlib
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import (ContentPolicy, HarnessBudget, HarnessCase,
                           MemoryClarificationRequired, RowRule, auto_connect,
                           evaluate_harness)
from context_relay.models import clone, strict_equal


def pilot_cases():
    rows = [{"id": "A-17", "name": "Steel bottle", "units": 3},
            {"id": "B-29", "name": "Notebook", "units": 12}]

    def step(text, reviewed_format, proposed_format="CSV", proposed_rows=None, clarification=None):
        return {"instruction": text, "reviewed_format": reviewed_format,
                "proposal": {"tool": "save_export", "arguments": {
                    "format": proposed_format, "rows": clone(rows if proposed_rows is None else proposed_rows)}},
                "clarification_reply": clarification}

    changed = clone(rows)
    changed[0].update(name="Translated bottle", units=999)
    specifications = [
        ("changed-instruction", "development", [step("Export as JSON. Thanks, that helps.", "JSON")], ["JSON"]),
        ("hindi-correction", "development", [step("अब से निर्यात JSON में करो।", "JSON")], ["JSON"]),
        ("repair-preserves-content", "development", [step("Export as JSON.", "JSON", "CSV", changed)], ["JSON"]),
        ("temporary-then-standing", "holdout", [step("For this export only, use JSON.", "JSON"),
            step("Please produce another export.", "CSV")], ["JSON", "CSV"]),
        ("reported-instruction", "holdout", [step('The customer wrote, "Export as JSON."', "CSV", "JSON")], ["CSV"]),
        ("clarify-then-finish", "holdout", [step("Change the export format to something else.", "JSON",
            clarification="Export as JSON.")], ["JSON"]),
        ("conflict-then-finish", "holdout", [step("Export as JSON. Export as XML.", "JSON",
            clarification="Export as JSON.")], ["JSON"]),
        ("report-scope-clarification", "holdout", [step("Use JSON for this report only.", "JSON",
            clarification="Export as JSON.")], ["JSON"]),
        ("unknown-identity", "holdout", [step("Export as JSON.", "JSON", "JSON",
            [{"id": "unknown", "name": "Steel bottle", "units": 3}, rows[1]])], []),
    ]
    return [HarnessCase(identifier, {"case_id": identifier, "source_rows": rows, "steps": steps},
                        {"artifacts": [{"format": format, "rows": clone(rows)} for format in formats],
                         "should_complete": bool(formats)}, split=split)
            for identifier, split, steps, formats in specifications]


def _read_exports(directory):
    artifacts = []
    for path in sorted(directory.glob("export-*.*")):
        if path.suffix == ".json":
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            with path.open(encoding="utf-8", newline="") as stream:
                rows = [{**row, "units": int(row["units"])} for row in csv.DictReader(stream)]
        artifacts.append({"format": path.suffix[1:].upper(), "rows": rows})
    return artifacts


def _ordinary_validator(proposal, reviewed_format, source_rows):
    """An ordinary validator with the same known-value repair opportunity.

    Reviewed memory is supplied by the application; this baseline makes no claim
    to infer it from natural language. Unknown row identities require review.
    """
    if type(proposal) is not dict or set(proposal) != {"tool", "arguments"} or proposal["tool"] != "save_export":
        return None
    arguments = proposal["arguments"]
    if type(arguments) is not dict or set(arguments) != {"format", "rows"}:
        return None
    rows = arguments["rows"]
    if (reviewed_format not in ("CSV", "JSON") or type(rows) is not list
            or len(rows) != len(source_rows) or any(type(row) is not dict for row in rows)
            or [row.get("id") for row in rows] != [row["id"] for row in source_rows]):
        return None
    return {"format": reviewed_format, "rows": clone(source_rows)}


def run_workflow(input, config, context):
    if config not in ({"mode": "reviewed_baseline"}, {"mode": "relay"}):
        raise ValueError("Choose reviewed_baseline or relay.")
    baseline = config["mode"] == "reviewed_baseline"
    history = [{"role": "user", "content": "Export as CSV."}]
    step_number = 0
    proposal = None
    pauses = reviews = repairs = 0
    statuses = []
    source_rows = input["source_rows"]

    def provider(*, messages):
        # Replace this with the participant's existing model boundary. Expected
        # artifacts and reviewed baseline values are not model inputs.
        return context.model_call(lambda: clone(proposal))

    def write_export(format, rows):
        path = context.workdir / f"export-{step_number:03d}.{format.lower()}"
        if format == "JSON":
            with path.open("x", encoding="utf-8") as stream:
                json.dump(rows, stream, ensure_ascii=False)
        elif format == "CSV":
            with path.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("id", "name", "units"))
                writer.writeheader()
                writer.writerows(rows)
        else:
            raise ValueError("Only CSV/JSON exports are allowed.")
        return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def save_export(format, rows):
        return context.tool_call(write_export, format, rows)

    with ExitStack() as stack:
        if not baseline:
            content = ContentPolicy({"rows": source_rows}, rows=(
                RowRule("/rows", ("name", "units"), key="id", allow_extra_fields=False),))
            agent = stack.enter_context(auto_connect(provider))
            actions = agent.protect_tools({"save_export": save_export},
                contracts={"save_export": {"content": content}}, repair="known")
        for step_number, step in enumerate(input["steps"]):
            history.append({"role": "user", "content": step["instruction"]})
            proposal = step["proposal"]
            if baseline:
                reviews += 1  # A scripted reviewer decision, not measured human time.
                proposed = provider(messages=clone(history))
                checked = _ordinary_validator(proposed, step["reviewed_format"], source_rows)
                if checked is None:
                    pauses += 1
                    statuses.append("clarification")
                    break
                repairs += not strict_equal(checked, proposed["arguments"])
                save_export(**checked)
                statuses.append("executed")
            else:
                try:
                    result = actions.invoke(messages=history)
                except MemoryClarificationRequired as error:
                    pauses += 1
                    reply = step.get("clarification_reply")
                    if reply is None:
                        statuses.append("clarification")
                        break
                    # A separately supplied user response, never derived from
                    # the expected result or an automatic approval of a model.
                    reviews += 1
                    history.extend([{"role": "assistant", "content": error.prompt},
                                    {"role": "user", "content": reply}])
                    try:
                        result = actions.invoke(messages=history)
                    except MemoryClarificationRequired:
                        pauses += 1
                        statuses.append("clarification")
                        break
                execution = result.execution
                statuses.append(execution["status"])
                repairs += execution["repair_attempts"]
                if not execution["executed"]:
                    pauses += 1
                    break
            history.append({"role": "assistant", "content": "Export saved."})
    return {"case_id": input["case_id"], "mode": config["mode"],
            "artifacts": _read_exports(context.workdir), "statuses": statuses,
            "clarification_requests": pauses, "scripted_review_decisions": reviews,
            "repair_attempts": repairs, "unfinished": len(statuses) != len(input["steps"])
                or not statuses or statuses[-1] not in ("executed", "repaired")}


def judge_workflow(expected, result, trace):
    actual = result["artifacts"]
    wrong = any(i >= len(expected["artifacts"]) or not strict_equal(item, expected["artifacts"][i])
                for i, item in enumerate(actual))
    complete = bool(expected["should_complete"] and not result["unfinished"] and not wrong
                    and strict_equal(actual, expected["artifacts"])
                    and trace["tool_calls"] == len(expected["artifacts"]))
    return {"completed": complete, "wrong_action": wrong,
            "clarification": not complete and bool(result["clarification_requests"])}


def run_pilot_kit(*, report_path=None):
    observations = []
    counters = ("clarification_requests", "scripted_review_decisions", "repair_attempts")
    with ExitStack() as stack:
        stream = None
        if report_path is not None:
            report_path = Path(report_path)
            if report_path.exists():
                raise FileExistsError(report_path)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            stream = stack.enter_context(report_path.with_suffix(".observations.jsonl").open(
                "x", encoding="utf-8", newline="\n"))

        def record(row):
            if stream is not None:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()

        def run(input, config, context):
            row = {"case_id": input["case_id"], "mode": config["mode"]}
            record({"event": "started", **row})
            try:
                result = run_workflow(input, config, context)
            except BaseException:
                # The harness retains metered calls on errors. Review/repair
                # counts are unknown if the workflow did not return them.
                row.update(status="error", **dict.fromkeys(counters))
                observations.append(row)
                record({"event": "observation", **row})
                raise
            row.update(status="returned", **{key: result[key] for key in counters})
            observations.append(row)
            record({"event": "observation", **row})
            return result

        record({"event": "pilot_started", "synthetic": True})
        report = evaluate_harness(pilot_cases(), {"mode": "reviewed_baseline"}, {"mode": "relay"},
            run, judge_workflow, budget=HarnessBudget(max_runs=18, max_model_calls=24, max_tool_calls=24),
            report_path=report_path)
        record({"event": "pilot_finished", "observations": len(observations)})
    return {"report": report, "observations": observations}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="New JSONL journal; never overwrite earlier runs.")
    args = parser.parse_args(argv)
    result = run_pilot_kit(report_path=args.report)
    report = result["report"]
    print("Synthetic pilot starter: fixed proposals, scripted reviews, real temporary files.")
    for variant in ("baseline", "candidate"):
        rows = [row for row in report["results"] if row["variant"] == variant]
        observations = [row for row in result["observations"]
                        if row["mode"] == ("reviewed_baseline" if variant == "baseline" else "relay")]
        counts_known = len(observations) == len(rows) and all(row["status"] == "returned" for row in observations)
        print(json.dumps({"variant": variant, "runs": len(rows),
            "completed": sum(bool(row["score"] and row["score"]["completed"]) for row in rows),
            "wrong_actions": sum(bool(row["score"] and row["score"]["wrong_action"]) for row in rows),
            "unresolved_clarifications": sum(bool(row["score"] and row["score"]["clarification"]) for row in rows),
            "errors": sum(row["status"] != "scored" for row in rows),
            "model_calls": sum(row["trace"]["model_calls"] for row in rows),
            "tool_calls": sum(row["trace"]["tool_calls"] for row in rows),
            "review_counts_complete": counts_known,
            **{key: sum(row[key] for row in observations) if counts_known else None
               for key in ("clarification_requests", "scripted_review_decisions", "repair_attempts")}}))
    print("This is not an outside pilot or evidence that Relay beats an existing framework.")
    return 1 if any(row["status"] != "scored" or row["score"]["wrong_action"] for row in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
