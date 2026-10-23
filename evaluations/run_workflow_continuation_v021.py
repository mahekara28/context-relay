"""Deterministic local-file continuation comparison, with no model/network calls.

Both arms use the same guarded workflow, evidence, repair and destination checks.
The only difference is checkpoint handoff. This measures the runner's specified
workflow behavior, not LLM comprehension, model quality or organisational load.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import platform
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import context_relay
from context_relay import EvidenceSource, RelayConfig, auto_connect
from context_relay.models import encode
from context_relay.outcomes import OutcomePolicy, OutcomeVerifier
from context_relay.responses_workflow import ResponsesWorkflow
from context_relay.workflow_history import WorkflowHistoryPolicy


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def journal(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def fingerprints():
    imported = Path(context_relay.__file__).resolve().parent
    paths = list(imported.glob("*.py")) + [Path(__file__).resolve()]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def document(manifest, format):
    if format == "JSON":
        return (encode(manifest) + "\n").encode("utf-8")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["document", "name", "quantity", "amount"])
    for row in manifest["rows"]:
        writer.writerow([manifest["document"], row["name"], row["quantity"], row["amount"]])
    return stream.getvalue().encode("utf-8")


def cases(count):
    texts = ["Export as CSV.", "For this export only, use JSON.", "Export the next document.",
        "Make the export format easier to read.", "Use CSV as the export format from now on."]
    result = []
    for number in range(count):
        slot = number % 12
        case = {"id": f"export-{number:03}", "text": texts[slot] if slot < len(texts) else "Export another document.",
            "format": "JSON" if slot == 1 else "CSV", "expected_effect": slot not in (3, 6, 7),
            "scenario": {3: "clarification", 5: "bad_proposal", 6: "permission_revoked", 7: "source_changed",
                         8: "effect_interrupted", 9: "bad_proposal"}.get(slot, "ordinary")}
        case["manifest"] = {"document": f"DOC-{number:03}", "rows": [
            {"name": "Bolts", "quantity": 17 + number, "amount": 2300 + number},
            {"name": "Washers", "quantity": 40, "amount": 875}]}
        result.append(case)
    return result


def run_arm(root, plan, *, checkpoint):
    root.mkdir()
    sources, output = root / "sources", root / "documents"
    sources.mkdir()
    output.mkdir()
    for case in plan:
        save(sources / (case["id"] + ".json"), case["manifest"])
    by_id = {case["id"]: case for case in plan}
    current = {"case": None, "allowed": True}
    provider_calls, starts, operations, controls = [], [], [], []
    task_completions = 0

    def source():
        case = current["case"]
        raw = (sources / (case["id"] + ".json")).read_bytes()
        return {"version": hashlib.sha256(raw).hexdigest(), "observed_at": time.time(),
                "facts": {"document_id": case["id"], "manifest": json.loads(raw)}}

    def expected_file(case):
        return output / (case["id"] + "." + case["format"].lower())

    def correct_file(case):
        path = expected_file(case)
        return path.is_file() and path.read_bytes() == document(case["manifest"], case["format"])

    def readback(payload):
        case = by_id[payload["request_id"]]
        return case["expected_effect"] and correct_file(case)

    def export(request_id, source_version, document_id, format, manifest):
        case = by_id[request_id]
        arguments = {"request_id": request_id, "source_version": source_version, "document_id": document_id,
                     "format": format, "manifest": manifest}
        wrong = (not case["expected_effect"] or document_id != request_id or format != case["format"]
                 or manifest != case["manifest"] or source_version != source()["version"])
        starts.append({"request_id": request_id, "wrong": wrong, "arguments": copy.deepcopy(arguments)})
        journal(root / "effect-starts.jsonl", starts[-1])
        # Exclusive creation reveals repeated effects instead of overwriting
        # evidence. No destination retry or automatic idempotent hiding occurs.
        path = output / (document_id + "." + format.lower())
        raw = document(manifest, format)
        with path.open("xb") as stream:
            stream.write(raw)
        if case["scenario"] == "effect_interrupted":
            raise KeyboardInterrupt("SCRIPTED_AFTER_FILE_WRITE")
        return {"ok": True, "sha256": hashlib.sha256(raw).hexdigest()}

    def provider(**request):
        case = current["case"]
        snapshot = source()
        arguments = {"request_id": case["id"], "source_version": snapshot["version"],
                     "document_id": case["id"], "format": "JSON" if case["format"] == "CSV" else "CSV",
                     "manifest": copy.deepcopy(case["manifest"])}
        if case["scenario"] == "bad_proposal":
            arguments["manifest"]["rows"][0]["amount"] += 999
        if case["scenario"] == "permission_revoked":
            current["allowed"] = False
        if case["scenario"] == "source_changed":
            path = sources / (case["id"] + ".json")
            changed = copy.deepcopy(case["manifest"])
            changed["rows"][0]["amount"] += 1
            path.write_text(encode(changed), encoding="utf-8")
        response = {"status": "completed", "store": False, "presence_penalty": 0, "frequency_penalty": 0,
            "parallel_tool_calls": True, "output": [
                {"type": "reasoning", "id": "reasoning-" + case["id"], "summary": [], "status": "completed",
                 "encrypted_content": "SYNTHETIC-NOT-PROVIDER-CIPHERTEXT:" + "x" * 1200},
                {"type": "function_call", "id": "function-" + case["id"], "call_id": "call-" + case["id"],
                 "name": "export", "arguments": encode(arguments), "status": "completed"}]}
        entry = {"request_id": case["id"], "input_chars": len(encode(request)),
                 "request": copy.deepcopy(request), "response": copy.deepcopy(response)}
        provider_calls.append(entry)
        journal(root / "provider-calls.jsonl", entry)
        return response

    schemas = {"export": {"type": "object", "additionalProperties": False,
        "properties": {"request_id": {"type": "string"}, "source_version": {"type": "string"},
            "document_id": {"type": "string"}, "format": {"type": "string", "enum": ["CSV", "JSON"]},
            "manifest": {"type": "object", "additionalProperties": False, "properties": {
                "document": {"type": "string"}, "rows": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "properties": {
                        "name": {"type": "string"}, "quantity": {"type": "integer"}, "amount": {"type": "integer"}},
                    "required": ["name", "quantity", "amount"]}}}, "required": ["document", "rows"]}},
        "required": ["request_id", "source_version", "document_id", "format", "manifest"]}}
    contracts = {"export": {"bindings": {"request_id": "workflow_request_id", "source_version": "workflow_source_version",
        "document_id": "document_id", "manifest": "manifest"}, "memory_bindings": {"format": "export_format"},
        "outcome": OutcomePolicy(required={"/ok": True})}}
    def connect():
        agent = auto_connect(SimpleNamespace(responses=SimpleNamespace(create=provider)), provider="openai",
            api="responses", native=True, conversation="continuation-evaluation", storage=root / "storage",
            config=RelayConfig(max_chars=14000))
        workflow = ResponsesWorkflow(agent, {"export": export}, schemas=schemas, contracts=contracts,
            evidence=EvidenceSource(source), authorize_action=lambda: current["allowed"],
            outcome_verifier=OutcomeVerifier(readback, verifier_id="frozen-local-file-oracle-v1"),
            history_policy=WorkflowHistoryPolicy(rollover_after_chars=10000, max_archive_chars=4000000) if checkpoint else None)
        return agent, workflow

    agent, workflow = connect()
    begin = time.perf_counter()
    try:
        for number, case in enumerate(plan):
            current.update(case=case, allowed=True)
            started = time.perf_counter()
            counts = (len(provider_calls), len(starts))
            record = {"request_id": case["id"], "scenario": case["scenario"], "expected_effect": case["expected_effect"]}
            result, failure = None, None
            try:
                result = workflow.send(case["text"], request_id=case["id"], model="scripted-no-llm", store=False)
                record["first_attempt"] = {"result": result.to_dict()}
            except BaseException as error:
                failure = error
                record["first_attempt"] = {"error": type(error).__name__, "message": str(error)}
            record["first_attempt"].update(physical_file_correct=correct_file(case),
                provider_calls=len(provider_calls) - counts[0], callback_starts=len(starts) - counts[1])
            journal(root / "first-attempts.jsonl", record)
            if isinstance(failure, KeyboardInterrupt) and str(failure) == "SCRIPTED_AFTER_FILE_WRITE":
                # A scripted host reads and validates the real file. This is
                # explicit reconciliation, not product inference or retry.
                if correct_file(case):
                    try:
                        result = workflow.reconcile(case["id"], expected_phase="action_pending",
                            result={"ok": True, "sha256": hashlib.sha256(expected_file(case).read_bytes()).hexdigest()})
                        record["reconciliation"] = result.to_dict()
                    except BaseException as error:
                        record["reconciliation_error"] = {"error": type(error).__name__, "message": str(error)}
            elif failure is not None and not isinstance(failure, Exception):
                record["interrupted"] = True
            record["final"] = None if result is None else result.to_dict()
            record["physical_file_correct"] = correct_file(case)
            record["correct_finished"] = bool(case["expected_effect"] and result is not None and result.completed and correct_file(case))
            record["expected_behavior"] = (record["correct_finished"] if case["expected_effect"] else
                result is not None and not result.completed and result.status == ("clarification_required" if case["scenario"] == "clarification" else "blocked")
                and len(starts) == counts[1] and not expected_file(case).exists())
            record["elapsed_seconds_illustrative_only"] = time.perf_counter() - started
            operations.append(record)
            journal(root / "operations.jsonl", record)
            if record.get("interrupted") or workflow.pending is not None:
                break
            if (number + 1) % 12 == 0:
                if checkpoint and all(item["expected_behavior"] for item in operations[-12:]):
                    workflow.finish_task(expected_revision=workflow.task_snapshot()["revision"])
                    task_completions += 1
                counts = (len(provider_calls), len(starts))
                previous_id = agent.session_id
                agent.close()
                agent, workflow = connect()
                replay = workflow.send(case["text"], request_id=case["id"], model="scripted-no-llm", store=False)
                control = {"after_request": case["id"], "same_session": previous_id == agent.session_id,
                    "replayed": replay.replayed, "provider_call_delta": len(provider_calls) - counts[0],
                    "callback_start_delta": len(starts) - counts[1]}
                controls.append(control)
                journal(root / "restart-controls.jsonl", control)
        history = workflow.history_status()
        archives = {"segments": [], "native_items_included": False}
        after = -1
        while True:
            page = workflow.archive_segments(after=after, limit=100)
            archives["segments"].extend(page["segments"])
            after = page["next_after"]
            if not page["has_more"]:
                break
        archive_integrity = len(archives["segments"]) == history["archive"]["segments"]
        for entry in archives["segments"]:
            archive = workflow.read_archive(entry["segment_id"])
            archive_integrity &= hashlib.sha256(encode(archive["history"]).encode()).hexdigest() == entry["sha256"]
        save(root / "history-status.json", history)
        save(root / "archive-metadata.json", archives)
        counts = Counter(item["request_id"] for item in starts)
        expected_paths = {expected_file(case).name for case in plan if case["expected_effect"]}
        wrong_files = [path.name for path in output.iterdir() if path.name not in expected_paths
                       or not correct_file(by_id[path.stem])]
        result = {"operations_planned": len(plan), "operations_observed": len(operations),
            "tasks_requiring_file": sum(case["expected_effect"] for case in plan),
            "correct_finished": sum(item["correct_finished"] for item in operations),
            "first_attempt_correct_finished": sum(bool(item["expected_effect"] and item["first_attempt"].get("result", {}).get("completed")
                                                       and item["first_attempt"]["physical_file_correct"]) for item in operations),
            "wrong_actions": sum(item["wrong"] for item in starts),
            "repeated_action_starts": sum(max(0, count - 1) for count in counts.values()), "wrong_files": wrong_files,
            "clarifications": sum(item["first_attempt"].get("result", {}).get("status") == "clarification_required" for item in operations),
            "preparation_failures": sum(item["first_attempt"].get("error") in {"BudgetExceeded", "TokenBudgetExceeded", "WorkflowLimitExceeded"} for item in operations),
            "scripted_destination_reconciliations": sum("reconciliation" in item for item in operations),
            "explicit_host_task_completions": task_completions,
            "provider_calls": len(provider_calls), "callback_starts": len(starts), "handoffs": history["archive"]["segments"],
            "maximum_sent_input_chars": max((item["input_chars"] for item in provider_calls), default=0),
            "hard_input_chars": 14000, "token_counter_configured": False, "model_token_bound_claimed": False,
            "archive_integrity_checked": archive_integrity, "restart_controls": controls,
            "interrupted": any(item.get("interrupted", False) for item in operations),
            "all_expected_behaviors": len(operations) == len(plan) and all(item["expected_behavior"] for item in operations),
            "elapsed_seconds_single_run_not_benchmark": time.perf_counter() - begin}
        save(root / "summary.json", result)
        return result
    finally:
        agent.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--operations", type=int, default=96)
    args = parser.parse_args()
    if not 12 <= args.operations <= 120 or args.operations % 12:
        parser.error("operations must be a multiple of 12 from 12 through 120")
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    before = fingerprints()
    plan = cases(args.operations)
    save(root / "frozen-plan.json", plan)
    save(root / "source-before.json", before)
    report = {"kind": "deterministic_guarded_workflow_comparison", "version": context_relay.__version__,
        "python": platform.python_version(), "live_model_calls": 0, "network_calls": 0,
        "source": "authored synthetic documents written to actual local files",
        "baseline": "same guarded ResponsesWorkflow with checkpoint handoff disabled",
        "limits": ["No LLM reasoning quality or organisational workload measured.",
                   "Clarification and deliberate interrupted-effect cases are authored; reconciliation is explicit scripted host readback.",
                   "The host explicitly finishes each twelve-case task batch only after its expected behaviors pass; Relay does not infer task completion.",
                   "Synthetic opaque reasoning exercises preservation only, not provider cryptographic validity.",
                   "Character budget is measured; no tokenizer or real model-window guarantee is claimed.",
                   "Elapsed time is one local illustration, not a performance benchmark."]}
    report["without_handoff"] = run_arm(root / "without-handoff", plan, checkpoint=False)
    report["with_handoff"] = (None if report["without_handoff"]["interrupted"] else
                              run_arm(root / "with-handoff", plan, checkpoint=True))
    after = fingerprints()
    save(root / "source-after.json", after)
    report["source_unchanged"] = before == after
    save(root / "report.json", report)
    print(json.dumps({key: report[key] for key in ("source_unchanged", "without_handoff", "with_handoff")}, indent=2))
    return 0 if report["source_unchanged"] and report["with_handoff"] and report["with_handoff"]["all_expected_behaviors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
