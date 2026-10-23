"""Freeze a small real local release workflow before any inference; retain all attempts.

Run from an installed wheel or with PYTHONPATH=src. This is a developer self-pilot,
not an organisational pilot, a context-rot benchmark, or a hosted-provider test.
"""
from __future__ import annotations
import argparse
from functools import wraps
import csv
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from release_desk import ReleaseDesk, SCHEMA, INSTRUCTIONS, contracts
import context_relay
from context_relay import EvidenceSource, Principal, RelayConfig, SessionManager, __version__

CASES = [
    {"id": "standing-csv", "text": "Export as CSV.", "expected_format": "CSV"},
    {"id": "one-export-json", "text": "For this export only, use JSON.", "expected_format": "JSON"},
    {"id": "standing-restored", "text": "Export the next release manifest.", "expected_format": "CSV"},
    {"id": "unclear-format", "text": "Make the export format easier to read.", "expected_format": None},
    {"id": "clarified-json", "text": "Use JSON as the export format from now on.", "expected_format": "JSON"},
    {"id": "standing-json", "text": "Export another release manifest.", "expected_format": "JSON"},
]


def save(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def fingerprint(paths, base):
    return hashlib.sha256(b"".join(p.relative_to(base).as_posix().encode() + p.read_bytes()
                                  for p in sorted(paths))).hexdigest()


def verify_document(document, expected_format, approved):
    try:
        if expected_format == "JSON":
            return json.dumps(json.loads(document), sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(approved, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if expected_format == "CSV":
            rows = list(csv.reader(io.StringIO(document)))
            return rows == [["release", "filename", "bytes", "sha256"]] + [
                [approved["release"], row["filename"], str(row["bytes"]), row["sha256"]]
                for row in approved["artifacts"]]
        return False
    except (ValueError, TypeError, csv.Error):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="New, empty run directory")
    parser.add_argument("--release", required=True)
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    args = parser.parse_args()
    if not 256 <= args.max_output_tokens <= 4096:
        parser.error("max-output-tokens must be from 256 through 4096")
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    project = Path(__file__).resolve().parents[1]
    source_paths = list((project / "src/context_relay").glob("*.py")) + [Path(__file__).resolve(), project / "examples/release_desk.py"]
    source_hash = fingerprint(source_paths, project)
    imported_directory = Path(context_relay.__file__).resolve().parent
    def imported_hashes():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(imported_directory.glob("*.py"))}
    imported_before = imported_hashes()
    source_module_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / "src/context_relay").glob("*.py")}
    artifacts = []
    for path in args.artifact:
        data = path.read_bytes()
        artifacts.append({"filename": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    approved = {"release": args.release, "artifacts": artifacts}
    save(root / "approved-source.json", approved)
    fixture = {"cases": CASES, "model": args.model, "temperature": 0, "max_output_tokens": args.max_output_tokens,
               "instructions": INSTRUCTIONS, "schema": SCHEMA, "repair": "known", "repair_budget": 1,
               "provider_retries": 0, "freshness_seconds": 300, "max_context_characters": 64000,
               "oracle": "Parse each committed and materialized document; exact release/filenames/byte counts/hashes and frozen format.",
               "interruption_policy": "Never retry a request. Read destination to reconcile action_pending; explicitly abandon provider_pending before continuing.",
               "source_and_harness_sha256": source_hash, "executed_module_sha256": imported_before,
               "executed_modules_match_checkout": imported_before == source_module_hashes}
    save(root / "frozen-plan.json", fixture)
    with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=10) as reply:
        server = json.load(reply)
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as reply:
        models = json.load(reply)
    save(root / "local-provider.json", {"server": server, "models": models})
    desk = ReleaseDesk(root / "destination")
    source_version = desk.set_source(approved["release"], approved["artifacts"])
    principal = Principal("local-pilot", "release-maintainer")
    rows, model_calls, effect_calls = [], [], []
    stopped = False
    def journal_case(event, row):
        with (root / "case-journal.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, **row}, ensure_ascii=False) + "\n")
    @wraps(desk.export_manifest)
    def export(*args, **kwargs):
        call = {"index": len(effect_calls) + 1, "request_id": kwargs.get("request_id", args[0] if args else None)}
        effect_calls.append(call)
        journal_case("tool_started", call)
        try:
            result = desk.export_manifest(*args, **kwargs)
            call["returned"] = True
            return result
        except BaseException as error:
            call["error"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            journal_case("tool_finished", call)
    def inspect(case):
        docs = [item for item in desk.documents() if item["request_id"] == case["id"]]
        expected = case["expected_format"]
        checks = []
        for item in docs:
            filename = hashlib.sha256(item["request_id"].encode()).hexdigest() + "." + item["receipt"]["format"].lower()
            path = root / "destination" / filename
            try:
                disk = path.read_bytes()
                file_ok = disk == item["document"].encode("utf-8") and verify_document(disk.decode("utf-8"), expected, approved)
            except (OSError, UnicodeError):
                file_ok = False
            checks.append({"file": "destination/" + filename, "file_correct": file_ok,
                           "document_correct": verify_document(item["document"], expected, approved)})
        correct = bool(expected and len(docs) == 1 and all(c["file_correct"] and c["document_correct"] for c in checks))
        return {"destination_documents": docs, "file_checks": checks, "physical_work_correct": correct,
                "wrong_effects": sum(not c["document_correct"] for c in checks)}
    started_at = datetime.now(timezone.utc).isoformat()
    from openai import OpenAI
    with (root / "model-calls.jsonl").open("x", encoding="utf-8") as journal:
        with OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama", max_retries=0, timeout=180) as client:
            original = client.responses.create
            def create(**request):
                call = {"call_index": len(model_calls) + 1, "request": request}
                model_calls.append(call)
                journal.write(json.dumps({"event": "started", **call}, ensure_ascii=False) + "\n")
                journal.flush()
                start = time.perf_counter()
                try:
                    response = original(**request)
                    call["parsed_sdk_response"] = response.model_dump(mode="json", exclude_unset=True)
                    return response
                except BaseException as error:
                    call["error"] = {"type": type(error).__name__, "message": str(error)}
                    raise
                finally:
                    call["seconds"] = round(time.perf_counter() - start, 4)
                    journal.write(json.dumps({"event": "finished", **call}, ensure_ascii=False) + "\n")
                    journal.flush()
            client.responses.create = create
            with SessionManager(root / "sessions", authorize=lambda identity, action: identity == principal) as manager:
                def connect():
                    return manager.connect(client, principal=principal, conversation="real-release-manifests",
                        api="responses", native=True, config=RelayConfig(max_chars=64000))
                def workflow(session):
                    return session.responses_workflow({"export_manifest": export},
                        contracts=contracts(), schemas={"export_manifest": SCHEMA}, instructions=INSTRUCTIONS,
                        evidence=EvidenceSource(desk.read_evidence, max_age_seconds=300))
                with connect() as session:
                    initial_session = session.session_id
                    flow = workflow(session)
                    for case in CASES:
                        before, effects_before = len(model_calls), len(effect_calls)
                        row = dict(case)
                        journal_case("started", row)
                        start = time.perf_counter()
                        try:
                            row["result"] = flow.send(case["text"], request_id=case["id"], model=args.model,
                                temperature=0, max_output_tokens=args.max_output_tokens).to_dict()
                        except BaseException as error:
                            row["error"] = {"type": type(error).__name__, "message": str(error)}
                            stopped = not isinstance(error, Exception)
                        journal_case("first_attempt_finished", row)
                        try:
                            initial = inspect(case)
                            row["first_attempt_completed_correctly"] = bool(row.get("result", {}).get("completed") and initial["physical_work_correct"])
                            pending = flow.pending
                            row["pending_after_attempt"] = None if pending is None else pending["phase"]
                            if pending and not stopped:
                                phase = pending["phase"]
                                if phase == "action_pending":
                                    receipt = desk.reconcile(case["id"])
                                    row["host_reconciliation"] = flow.reconcile(case["id"], result=receipt,
                                        expected_phase=phase).to_dict()
                                else:
                                    row["explicit_abandonment"] = flow.abandon(case["id"], expected_phase=phase).to_dict()
                            row.update(inspect(case))
                        except BaseException as error:
                            row["inspection_or_recovery_error"] = {"type": type(error).__name__, "message": str(error)}
                            stopped = True
                            journal_case("recovery_failed", row)
                        row["model_calls"] = len(model_calls) - before
                        row["tool_callback_starts"] = len(effect_calls) - effects_before
                        row["seconds"] = round(time.perf_counter() - start, 4)
                        declared = row.get("host_reconciliation", row.get("result", {})).get("completed", False)
                        row["completed_correctly"] = bool(declared and row.get("physical_work_correct", False))
                        row["expected_clarification"] = case["expected_format"] is None and row.get("result", {}).get("status") == "clarification_required" and not row.get("destination_documents", []) and row["model_calls"] == 0
                        rows.append(row)
                        save(root / (case["id"] + ".json"), row)
                        journal_case("recorded", row)
                        print(json.dumps({"case": case["id"], "correct": row["completed_correctly"], "clarification": row["expected_clarification"],
                            "error": row.get("error"), "seconds": row["seconds"]}), flush=True)
                        if stopped:
                            break
                    save(root / "native-history.json", flow.history)
                restart = {"skipped": "Run interrupted; pending requests are preserved."} if stopped else {}
                if not stopped:
                    before, effects_before = len(model_calls), len(effect_calls)
                    first = CASES[0]
                    try:
                        with connect() as resumed:
                            replay = workflow(resumed).send(first["text"], request_id=first["id"], model=args.model,
                                temperature=0, max_output_tokens=args.max_output_tokens)
                            restart = {"same_session": resumed.session_id == initial_session, "provider_calls": len(model_calls) - before,
                                       "tool_callback_starts": len(effect_calls) - effects_before, "replayed": replay.replayed, "result": replay.to_dict()}
                    except BaseException as error:
                        restart = {"error": {"type": type(error).__name__, "message": str(error)},
                                   "provider_calls": len(model_calls) - before, "tool_callback_starts": len(effect_calls) - effects_before}
    docs = desk.documents()
    usage = [call.get("parsed_sdk_response", {}).get("usage") or {} for call in model_calls]
    report = {"kind": "actual-local-developer-self-pilot", "relay_version": __version__, "library_import_path": str(Path(context_relay.__file__).resolve()),
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.platform(), "openai_sdk": importlib.metadata.version("openai"),
        "provider": server, "model": args.model, "model_metadata": [m for m in models.get("models", []) if m["name"] == args.model],
        "executed_module_sha256_before": imported_before, "executed_module_sha256_after": imported_hashes(),
        "executed_modules_match_checkout": imported_before == source_module_hashes,
        "source_and_harness_sha256_before": source_hash, "source_and_harness_sha256_after": fingerprint(source_paths, project),
        "frozen_plan_sha256": hashlib.sha256((root / "frozen-plan.json").read_bytes()).hexdigest(),
        "approved_source_version": source_version, "expected_completed_tasks": 5, "cases": rows, "restart": restart, "unattempted_cases": [c["id"] for c in CASES[len(rows):]], "tool_calls": effect_calls,
        "metrics": {"correct_completed_tasks": sum(r["completed_correctly"] for r in rows),
                    "first_attempt_correct_tasks": sum(r.get("first_attempt_completed_correctly", False) for r in rows),
                    "wrong_committed_documents": sum(r.get("wrong_effects", 0) for r in rows),
                    "incorrect_or_missing_materialized_files": sum(not c["file_correct"] for r in rows for c in r.get("file_checks", [])), "expected_clarifications": sum(r["expected_clarification"] for r in rows),
                    "incomplete_export_tasks": 5 - sum(r["completed_correctly"] for r in rows),
                    "attempted_incomplete_export_tasks": sum(r["expected_format"] is not None and not r["completed_correctly"] for r in rows),
                    "unattempted_export_tasks": sum(c["expected_format"] is not None for c in CASES[len(rows):]),
                    "errors": sum("error" in r for r in rows), "model_calls": len(model_calls), "committed_documents": len(docs),
                    "repair_attempts_in_returned_results": sum(r.get("result", {}).get("execution", {}).get("repair_attempts", 0) for r in rows),
                    "duplicate_tool_callback_starts": len(effect_calls) - len({r["request_id"] for r in effect_calls}),
                    "tool_callback_starts": len(effect_calls), "committed_unique_request_ids": len({r["request_id"] for r in docs}),
                    "provider_reported_input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                    "provider_reported_output_tokens": sum(u.get("output_tokens", 0) for u in usage),
                    "usage_missing_calls": sum(not u for u in usage),
                    "provider_seconds": round(sum(c["seconds"] for c in model_calls), 4)},
        "repair_metric_complete": not any("error" in r for r in rows),
        "limitations": ["One developer, one local model and six short authored requests; no general reliability, long-context or speed claim.",
                        "Not an organisational pilot or hosted OpenAI inference; actual local Ollama Responses inference used the OpenAI SDK.",
                        "Host owns approved source, identity, authorization, tool contract and reconciliation; source trust is not independently established by Relay.",
                        "No unprotected or weak control arm here; separate matched framework comparison isolates action-boundary behavior.",
                        "Every first-attempt failure is retained; any later experiment requires a new directory.",
                        "Repair attempts are counted from returned workflow results only; exceptions may leave additional durable checks, so partial-run repair totals are incomplete."]}
    save(root / "report.json", report)
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
