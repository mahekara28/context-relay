"""Evaluate one configuration change against a protected export workflow.

Run from this checkout: python examples/harness_improvement.py
Optional journal:      python examples/harness_improvement.py --report trial.jsonl

The model responses are deliberate fixtures. Files are written to fresh temporary
directories and read back; no API, model, or service is required. These results
illustrate the evaluation loop, not evidence of improvement on a real model.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import (ContentPolicy, HarnessBudget, HarnessCase,
                           MemoryClarificationRequired, RowRule, auto_connect,
                           improve_harness)
from context_relay.models import strict_equal


def export_cases():
    """Development failure plus separate checks the proposer does not receive."""
    rows = [{"id": "A-17", "name": "Steel bottle", "units": 3},
            {"id": "B-29", "name": "Notebook", "units": 12}]
    changed_rows = [{"id": "A-17", "name": "Translated bottle", "units": 999}, rows[1]]
    cases = []
    for identifier, split, messages, proposed_format, proposed_rows, expected_format in (
        ("changed-format", "development", ["Export as CSV.", "Actually, export as JSON."], "CSV", rows, "JSON"),
        ("ordinary-csv", "holdout", ["Export as CSV."], "CSV", rows, "CSV"),
        ("equivalent-json", "holdout", ["Export as JSON."], "json", rows, "JSON"),
        ("preserve-content", "holdout", ["Export as JSON."], "JSON", changed_rows, "JSON"),
    ):
        cases.append(HarnessCase(identifier, {
            "messages": [{"role": "user", "content": text} for text in messages],
            "source_rows": rows,
            "proposal": {"tool": "save_export", "arguments": {"format": proposed_format, "rows": proposed_rows}},
        }, {"format": expected_format, "rows": [
            {"id": "A-17", "name": "Steel bottle", "units": 3},
            {"id": "B-29", "name": "Notebook", "units": 12},
        ]}, split=split))
    return cases


def run_export(input, config, context):
    """The only tunable setting enables known-value repair; checks stay fixed."""
    if (type(config) is not dict or set(config) != {"repair"}
            or config["repair"] is not None and config["repair"] != "known"):
        raise ValueError("Only repair=None or repair='known' is supported.")

    def provider(*, messages):
        return context.model_call(lambda: input["proposal"])

    def write_export(format, rows):
        path = context.workdir / ("export." + format.lower())
        if format == "JSON":
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        elif format == "CSV":
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("id", "name", "units"))
                writer.writeheader()
                writer.writerows(rows)
        else:
            raise ValueError("The example supports only CSV and JSON.")
        # An injected failure exercises the boundary after an effect has happened.
        if input.get("fail_after_write", False):
            raise OSError("The export was written, but its acknowledgement failed.")
        return {"file": path.name}

    def save_export(format, rows):
        return context.tool_call(write_export, format, rows)

    content = ContentPolicy({"rows": input["source_rows"]}, rows=(
        RowRule("/rows", ("name", "units"), key="id", allow_extra_fields=False),
    ))
    with auto_connect(provider) as agent:
        actions = agent.protect_tools({"save_export": save_export},
            contracts={"save_export": {"content": content}}, repair=config["repair"])
        try:
            action = actions.invoke(messages=input["messages"])
        except MemoryClarificationRequired as error:
            return {"artifact": None, "status": "clarification", "question": error.prompt,
                    "repair_attempts": 0}
        execution = action.execution

    # Check the actual artifact, independently of the tool's success receipt.
    files = list(context.workdir.glob("export.*"))
    artifact = None
    if len(files) > 1:
        raise RuntimeError("A run produced more than one export.")
    if files:
        path = files[0]
        if path.suffix == ".json":
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            with path.open(encoding="utf-8", newline="") as stream:
                rows = [{**row, "units": int(row["units"])} for row in csv.DictReader(stream)]
        artifact = {"format": path.suffix[1:].upper(), "rows": rows}
    question = ("Please confirm the original row identities before exporting."
                if execution["status"] == "repair_unavailable" else None)
    return {"artifact": artifact, "status": execution["status"], "question": question,
            "repair_attempts": execution["repair_attempts"]}


def judge_export(expected, result, trace):
    """A success receipt alone never counts as a completed export."""
    artifact = result["artifact"]
    exact = artifact is not None and strict_equal(artifact, expected)
    return {"completed": exact and trace["tool_calls"] == 1 and result["question"] is None,
            "wrong_action": artifact is not None and not exact or trace["tool_calls"] > 1,
            "clarification": result["question"] is not None}


def propose_repair(failures, context):
    """An application-owned proposal; it cannot alter contracts or execute code."""
    if any(row["result"] and row["result"]["status"] == "blocked" for row in failures):
        return {"repair": "known"}
    return {"repair": None}


def run_demo(*, report_path=None):
    return improve_harness(export_cases(), {"repair": None}, propose_repair, run_export,
        judge_export, budget=HarnessBudget(max_runs=8, max_model_calls=8, max_tool_calls=8),
        report_path=report_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, help="Create a new JSONL evaluation journal (no raw case content).")
    args = parser.parse_args(argv)
    result = run_demo(report_path=args.report)
    print("Offline export evaluation | deterministic responses, real temporary files")
    for variant in ("baseline", "candidate"):
        rows = [row for row in result["report"]["results"] if row["variant"] == variant]
        completed = sum(bool(row["score"] and row["score"]["completed"]) for row in rows)
        wrong = sum(bool(row["score"] and row["score"]["wrong_action"]) for row in rows)
        clarifications = sum(bool(row["score"] and row["score"]["clarification"]) for row in rows)
        calls = sum(row["trace"]["model_calls"] for row in rows)
        writes = sum(row["trace"]["tool_calls"] for row in rows)
        print(f"{variant}: {completed}/{len(rows)} completed, {wrong} wrong actions, "
              f"{clarifications} clarifications, {calls} model calls, {writes} writes")
    print(f"Selected: {result['selected']} {json.dumps(result['config'])}")
    print("Selection is returned for review; no live configuration was changed.")
    return 0 if result["decision"]["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
