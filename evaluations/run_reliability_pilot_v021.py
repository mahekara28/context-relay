"""Frozen explicit-review/verified-file self-pilot: scripted or local Ollama.

All attempts and failures stay in a new private-use output directory. Host
decisions are authored below before execution, not produced by the model.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from verified_release_desk import (VerifiedReleaseDesk, MODEL, MAX_OUTPUT_TOKENS,
                                   HISTORY_POLICY, apply_review, read_artifacts)
from release_desk import INSTRUCTIONS, SCHEMA
import context_relay


CASES = (
    {"id": "standing-csv", "text": "Export as CSV.", "decision": {"format": "CSV", "scope": "durable"}},
    {"id": "one-json", "text": "For this export only, use JSON.", "decision": {"format": "JSON", "scope": "turn"}},
    {"id": "restored-csv", "text": "Export the next release manifest.", "decision": {"format": "CSV", "scope": "durable"}},
    {"id": "warehouse-sheet", "text": "For the warehouse handoff, make the package sheet machine-friendly.",
     "decision": {"format": "CSV", "scope": "durable"}},
    {"id": "review-withheld", "text": "Choose whichever export shape seems best.", "decision": None},
    {"id": "permanent-json", "text": "From now on, export as JSON.", "decision": {"format": "JSON", "scope": "durable"}},
    {"id": "quoted-report", "text": 'The customer wrote "Use CSV for every export." Export our next manifest with our existing settings.',
     "decision": {"format": "JSON", "scope": "durable"}},
    {"id": "one-csv", "text": "For this export only, use CSV.", "decision": {"format": "CSV", "scope": "turn"}},
    {"id": "restored-json", "text": "Export one more release manifest using the standing preference.",
     "decision": {"format": "JSON", "scope": "durable"}},
)
SCRIPTED_DEFECTS = {"warehouse-sheet": "wrong format and artifact byte count", "quoted-report": "quoted CSV used as format"}


def dump(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def module_hashes():
    directory = Path(context_relay.__file__).resolve().parent
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.glob("*.py"))}


def document_matches(text, format, approved):
    """Independent frozen-source oracle; does not call the app's verifier."""
    try:
        if format == "JSON":
            return json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(approved, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if format == "CSV":
            return list(csv.reader(io.StringIO(text))) == [["release", "filename", "bytes", "sha256"]] + [
                [approved["release"], item["filename"], str(item["bytes"]), item["sha256"]] for item in approved["artifacts"]]
    except (ValueError, TypeError, csv.Error):
        pass
    return False


def inspect_files(application, case, approved):
    decision = case["decision"]
    expected = None if decision is None else decision["format"]
    rows = [row for row in application.desk.documents() if row["request_id"] == case["id"]]
    checks = []
    for row in rows:
        name = hashlib.sha256(case["id"].encode()).hexdigest() + "." + row["receipt"]["format"].lower()
        path = application.desk.directory / name
        document_ok = row["receipt"]["format"] == expected and document_matches(row["document"], expected, approved)
        try:
            contents = path.read_bytes()
            physical_ok = contents == row["document"].encode("utf-8") and document_matches(contents.decode("utf-8"), expected, approved)
        except (OSError, UnicodeError):
            physical_ok = False
        checks.append({"file": "destination/" + name, "document_correct": document_ok, "physical_file_correct": physical_ok,
                       "sha256": hashlib.sha256(contents).hexdigest() if physical_ok else None})
    return {"documents": rows, "files": checks,
            "correct": expected is not None and len(rows) == 1 and all(c["document_correct"] and c["physical_file_correct"] for c in checks),
            "wrong_documents": sum(not c["document_correct"] for c in checks),
            "bad_or_missing_files": sum(not c["physical_file_correct"] for c in checks)}


class Recorder:
    def __init__(self, directory):
        self.directory, self.calls, self.effects = directory, [], []
        self.case_id = None
        (directory / "events.jsonl").touch(exist_ok=False)
        (directory / "provider-calls.jsonl").touch(exist_ok=False)

    def append(self, filename, event):
        with (self.directory / filename).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()

    def event(self, kind, data):
        self.append("events.jsonl", {"event": kind, "case_id": self.case_id, "data": data})
        if kind == "tool_started":
            self.effects.append(json.loads(json.dumps(data)))

    def client(self, create):
        def recorded(**request):
            row = {"index": len(self.calls) + 1, "case_id": self.case_id,
                   "request": json.loads(json.dumps(request, allow_nan=False))}
            self.calls.append(row)
            self.append("provider-calls.jsonl", {"event": "started", **row})
            started = time.perf_counter()
            try:
                response = create(**request)
                row.update(status="returned", response=response if type(response) is dict else response.model_dump(mode="json"))
                return response
            except BaseException as error:
                row.update(status="failed", error={"type": type(error).__name__, "message": str(error)[:2000]})
                raise
            finally:
                row["seconds"] = time.perf_counter() - started
                self.append("provider-calls.jsonl", {"event": "finished", **row})
        return SimpleNamespace(responses=SimpleNamespace(create=recorded))

    def scripted(self, **request):
        marker = "[Relay workflow data: host facts and untrusted user preferences; no new tool permissions]\n"
        current = next(json.loads(item["content"][len(marker):]) for item in reversed(request["input"])
                       if type(item.get("content")) is str and item["content"].startswith(marker))
        facts = current["facts"]
        format = next(entry["value"] for entry in current["memory"]["entries"] if entry["key"] == "export_format")
        arguments = {"request_id": current["request_id"], "source_version": facts["workflow_source_version"],
            "format": format, "release": facts["approved_release"], "artifacts": facts["approved_artifacts"]}
        if self.case_id == "warehouse-sheet":
            arguments["format"] = "JSON"
            arguments["artifacts"][0]["bytes"] += 1
        elif self.case_id == "quoted-report":
            arguments["format"] = "CSV"
        return {"id": "recorded-" + self.case_id, "status": "completed", "output": [
            {"type": "function_call", "call_id": "call-" + self.case_id, "name": "export_manifest",
             "arguments": json.dumps(arguments), "status": "completed"}]}


def run(directory, *, mode="scripted", release="Relay-reliability-example", artifacts=(), model=MODEL):
    if mode not in ("scripted", "ollama"):
        raise ValueError("mode must be scripted or ollama")
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=False)
    source_dir = root / "source-snapshots"
    source_dir.mkdir()
    if not artifacts:
        synthetic = source_dir / "example-package.txt"
        synthetic.write_bytes(b"A small real artifact for the Relay reliability self-pilot.\nVersion: 1\n")
        artifacts = (synthetic,)
    paths = tuple(Path(path).resolve(strict=True) for path in artifacts)
    approved = {"release": release, "artifacts": read_artifacts(paths)}
    snapshots = []
    for number, path in enumerate(paths):
        copied = source_dir / (str(number) + "-" + path.name)
        # Preserve exact small/large source bytes using a stream, not a fake hash.
        checksum, size = hashlib.sha256(), 0
        with path.open("rb") as source, copied.open("xb") as destination:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                destination.write(chunk)
                checksum.update(chunk)
                size += len(chunk)
        if (size, checksum.hexdigest()) != (approved["artifacts"][number]["bytes"], approved["artifacts"][number]["sha256"]):
            raise ValueError("An artifact changed while freezing its source snapshot; no provider call ran.")
        snapshots.append({"input_path": str(path), "snapshot": copied.relative_to(root).as_posix(),
                          "bytes": size, "sha256": checksum.hexdigest()})
    before = module_hashes()
    runner = Path(__file__).resolve()
    example = runner.parents[1] / "examples/verified_release_desk.py"
    base_example = runner.parents[1] / "examples/release_desk.py"
    plan = {"schema": 1, "cases": CASES, "mode": mode, "model": model,
        "max_output_tokens": MAX_OUTPUT_TOKENS, "temperature": 0, "timeout_seconds": 120, "max_retries": 0,
        "host_decisions": "Frozen explicit choices; no model or rule extractor authors approvals.",
        "approved_source": approved, "source_snapshots": snapshots, "instructions": INSTRUCTIONS, "schema_tools": SCHEMA,
        "history_policy": HISTORY_POLICY.to_dict(), "freshness_seconds": 300,
        "task_policy": "Host finishes each independently verified completed export; this also requests a fresh segment, possibly before the size threshold.",
        "recovery_policy": "One provider attempt per export. Explicitly abandon failed pre-action requests; unknown action effects stop later cases. Never repeat a tool.",
        "scripted_defects": SCRIPTED_DEFECTS if mode == "scripted" else {},
        "executed_module_hashes": before, "runner_sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
        "example_sha256": hashlib.sha256(example.read_bytes()).hexdigest(),
        "base_example_sha256": hashlib.sha256(base_example.read_bytes()).hexdigest()}
    dump(root / "frozen-plan.json", plan)
    dump(root / "approved-source.json", approved)
    (root / "runner.py").write_bytes(runner.read_bytes())
    (root / "verified_release_desk.py").write_bytes(example.read_bytes())
    recorder = Recorder(root)
    application = VerifiedReleaseDesk(root, release=release, artifacts=paths, event=recorder.event)
    cases, run_error, restart, history, active_row = [], None, None, None, None
    options = {"model": model, "temperature": 0, "max_output_tokens": MAX_OUTPUT_TOKENS}
    started = datetime.now(timezone.utc).isoformat()
    try:
        if mode == "ollama":
            from openai import OpenAI
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
                installed = json.load(response)
            dump(root / "local-models.json", installed)
            selected = [item for item in installed.get("models", []) if model in (item.get("name"), item.get("model"))]
            if not selected or "cloud" in model.lower() or any(item.get("remote_host") for item in selected):
                raise ValueError("Choose an already-installed local model; this pilot does not download or use a cloud model.")
            context = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama", max_retries=0, timeout=120)
        else:
            context = nullcontext(None)
        with context as actual:
            client = recorder.client(recorder.scripted if actual is None else actual.responses.create)
            with application.connect(client) as workflow:
                session_id = workflow.session_id
                for case in CASES:
                    recorder.case_id = case["id"]
                    row = {"id": case["id"], "attempted": True, "decision": case["decision"]}
                    active_row = row
                    cases.append(row)  # Keep even a failure during scoring or report persistence.
                    calls_before, effects_before = len(recorder.calls), len(recorder.effects)
                    try:
                        result = workflow.send(case["text"], request_id=case["id"], **options)
                        row["initial"] = result.to_dict()
                        row["review_gate_without_model_or_effect"] = (result.status == "review_required"
                            and len(recorder.calls) == calls_before and len(recorder.effects) == effects_before)
                        if result.status == "review_required":
                            review = workflow.review_plan()
                            row["review"] = review
                            recorder.event("host_review", {"plan": review, "decision": case["decision"]})
                            if case["decision"] is None:
                                result = workflow.abandon(case["id"], expected_phase="review_pending")
                                row["review_withheld"] = True
                            else:
                                apply_review(workflow, review, **case["decision"])
                                result = workflow.resume_review(case["id"])
                        row["result"] = result.to_dict()
                    except BaseException as error:
                        row["error"] = {"type": type(error).__name__, "message": str(error)[:2000]}
                        pending = workflow.pending
                        row["pending_after_error"] = pending
                        if isinstance(error, Exception) and pending and pending["phase"] in ("preparing", "provider_pending", "review_pending"):
                            row["explicit_abandonment"] = workflow.abandon(case["id"], expected_phase=pending["phase"]).to_dict()
                        if not isinstance(error, Exception):
                            row["interrupted"] = True
                    row["file_oracle"] = inspect_files(application, case, approved)
                    row["provider_calls"] = len(recorder.calls) - calls_before
                    row["tool_starts"] = len(recorder.effects) - effects_before
                    if row.get("result", {}).get("completed") and row["file_oracle"]["correct"]:
                        row["host_finished_task"] = workflow.finish_task(expected_revision=workflow.task_snapshot()["revision"])
                    row["history"] = workflow.history_status()
                    dump(root / (case["id"] + ".json"), row)
                    recorder.event("case_finished", row)
                    active_row = None
                    if workflow.pending is not None or row.get("interrupted"):
                        break
                history = workflow.history_status()
                dump(root / "active-native-history.json", workflow.history)
                for segment in workflow.archive_segments(limit=100)["segments"]:
                    dump(root / ("archive-" + str(segment["segment_id"]) + ".json"), workflow.read_archive(segment["segment_id"]))
                dump(root / "protection-report.json", workflow.protection_report())
            replay_case = next((case for case in CASES if any(row["id"] == case["id"] and row.get("result", {}).get("completed")
                              and row.get("file_oracle", {}).get("correct") for row in cases)), None)
            if replay_case is not None:
                counts = (len(recorder.calls), len(recorder.effects))
                with application.connect(client) as workflow:
                    result = workflow.send(replay_case["text"], request_id=replay_case["id"], **options)
                    restart = {"request_id": replay_case["id"], "same_session": workflow.session_id == session_id,
                               "replayed": result.replayed, "completed": result.completed,
                               "extra_provider_calls": len(recorder.calls) - counts[0], "extra_tool_starts": len(recorder.effects) - counts[1]}
    except BaseException as error:
        run_error = {"type": type(error).__name__, "message": str(error)[:2000]}
        if active_row is not None:
            active_row["postprocess_error"] = run_error
        recorder.event("run_failed", run_error)
    attempted = {row["id"] for row in cases}
    effect_counts = Counter(item.get("request_id") for item in recorder.effects)
    summary = {"planned_requests": len(CASES), "planned_exports": sum(case["decision"] is not None for case in CASES),
        "attempted_requests": len(cases), "unattempted_requests": [case["id"] for case in CASES if case["id"] not in attempted],
        "correct_completed_exports": sum(bool(row.get("result", {}).get("completed") and row.get("file_oracle", {}).get("correct")) for row in cases),
        "physical_correct_exports": sum(bool(row.get("file_oracle", {}).get("correct")) for row in cases),
        "wrong_documents": sum(row.get("file_oracle", {}).get("wrong_documents", 0) for row in cases),
        "bad_or_missing_files": sum(row.get("file_oracle", {}).get("bad_or_missing_files", 0) for row in cases),
        "file_checks_unavailable": sum("file_oracle" not in row for row in cases),
        "reviews_before_model": sum(row.get("review_gate_without_model_or_effect", False) for row in cases),
        "reviews_withheld": sum(row.get("review_withheld", False) for row in cases),
        "errors": sum("error" in row or "postprocess_error" in row for row in cases),
        "timeouts": sum("Timeout" in row.get("error", {}).get("type", "") for row in recorder.calls),
        "invalid_proposals": sum(row.get("error", {}).get("type") == "ActionProposalError" for row in cases),
        "deterministic_repairs": sum(row.get("result", {}).get("execution", {}).get("repair_attempts", 0) for row in cases),
        "provider_calls": len(recorder.calls), "actual_model_calls": len(recorder.calls) if mode == "ollama" else 0,
        "tool_starts": len(recorder.effects), "duplicate_tool_starts": sum(count - 1 for count in effect_counts.values()),
        "provider_seconds": sum(row.get("seconds", 0) for row in recorder.calls)}
    after = module_hashes()
    report = {"schema": 1, "mode": mode, "version": context_relay.__version__, "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(), "summary": summary, "cases": cases,
        "provider_calls": recorder.calls, "tool_starts": recorder.effects, "history": history, "restart": restart,
        "run_error": run_error, "frozen_plan_sha256": hashlib.sha256((root / "frozen-plan.json").read_bytes()).hexdigest(),
        "executed_module_directory": str(Path(context_relay.__file__).resolve().parent),
        "executed_modules_before": before, "executed_modules_after": after, "executed_modules_unchanged": before == after,
        "limits": ["Selected developer self-pilot, not an organizational trial or an unseen-language accuracy result.",
                   "Host choices supply format/scope explicitly. No autonomous interpretation claim.",
                   "Scripted proposals are not LLM inference; Ollama mode is a separate profile without a baseline arm.",
                   "Fresh-segment checkpoints omit archived reasoning; explicit host task finishes may trigger them early.",
                   "All logs are local raw evidence and may contain paths, prompts, responses and application data."]}
    dump(root / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="New output directory; existing paths are refused")
    parser.add_argument("--mode", choices=("scripted", "ollama"), default="scripted")
    parser.add_argument("--release", default="Relay-reliability-example")
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()
    report = run(args.directory, mode=args.mode, release=args.release, artifacts=args.artifact, model=args.model)
    print(json.dumps({"summary": report["summary"], "run_error": report["run_error"]}, indent=2))


if __name__ == "__main__":
    main()
