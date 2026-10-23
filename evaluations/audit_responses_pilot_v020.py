"""Read-only independent verification of retained local pilot evidence.

No Relay imports, provider calls, model inference, destination writes, or replay.
The audit reads actual package artifacts, installed module bytes, SQLite records,
materialized documents and journals rather than trusting the summary alone.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import zipfile


PROJECT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def journal(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sql_rows(path, statement):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute(statement).fetchall()


def correct_document(text, format, truth):
    try:
        if format == "JSON":
            return canonical(json.loads(text)) == canonical(truth)
        if format == "CSV":
            expected = [["release", "filename", "bytes", "sha256"], *[
                [truth["release"], item["filename"], str(item["bytes"]), item["sha256"]]
                for item in truth["artifacts"]]]
            return list(csv.reader(io.StringIO(text))) == expected
        return False
    except (ValueError, TypeError, csv.Error):
        return False


def audit(directory, artifact_directory, installed_wheel):
    directory = Path(directory).resolve()
    report, plan, approved = (read(directory / name) for name in
                              ("report.json", "frozen-plan.json", "approved-source.json"))
    checks, details = {}, {}
    def check(name, value):
        checks[name] = bool(value)
    check("frozen_plan_digest", sha((directory / "frozen-plan.json").read_bytes()) == report["frozen_plan_sha256"])
    check("all_planned_cases_counted", [row["id"] for row in report["cases"]] + report["unattempted_cases"]
          == [row["id"] for row in plan["cases"]])
    truth = {"release": approved["release"], "artifacts": []}
    for row in approved["artifacts"]:
        if Path(row["filename"]).name != row["filename"]:
            raise ValueError("An approved artifact filename must be a basename.")
        path = Path(artifact_directory) / row["filename"]
        data = path.read_bytes()
        actual = {"filename": path.name, "bytes": len(data), "sha256": sha(data)}
        check("actual_artifact_" + path.name, canonical(actual) == canonical(row))
        truth["artifacts"].append(actual)
    source = {"approved_release": truth["release"], "approved_artifacts": truth["artifacts"], "may_export": True}
    check("source_version_matches_actual_artifacts", sha(canonical(source).encode("utf-8")) == report["approved_source_version"])

    imported = Path(report["library_import_path"]).parent
    imported_hashes = {path.name: sha(path.read_bytes()) for path in imported.glob("*.py")}
    checkout_hashes = {path.name: sha(path.read_bytes()) for path in (PROJECT / "src/context_relay").glob("*.py")}
    with zipfile.ZipFile(installed_wheel) as wheel:
        wheel_hashes = {Path(name).name: sha(wheel.read(name)) for name in wheel.namelist()
                        if name.startswith("context_relay/") and name.endswith(".py")}
    check("installed_modules_stable_and_match_record", imported_hashes == report["executed_module_sha256_before"]
          == report["executed_module_sha256_after"] == plan["executed_module_sha256"])
    check("installed_modules_match_current_checkout", imported_hashes == checkout_hashes)
    check("installed_modules_match_actual_wheel", imported_hashes == wheel_hashes)
    check("recorded_checkout_match_claim", report["executed_modules_match_checkout"] is True
          and plan["executed_modules_match_checkout"] is True)
    runner = directory / "runner.py"
    check("runner_snapshot_retained", runner.is_file())
    if runner.is_file():
        paths = [*sorted((PROJECT / "src/context_relay").glob("*.py")),
                 PROJECT / "examples/release_desk.py", PROJECT / "evaluations/run_responses_pilot_v020.py"]
        combined = b"".join(path.relative_to(PROJECT).as_posix().encode() +
            (runner.read_bytes() if path.name == "run_responses_pilot_v020.py" else path.read_bytes())
            for path in sorted(paths))
        check("archived_runner_and_source_match_recorded_hash", sha(combined)
              == report["source_and_harness_sha256_before"] == report["source_and_harness_sha256_after"]
              == plan["source_and_harness_sha256"])
        check("runner_has_no_sdk_auto_retry", "max_retries=0" in runner.read_text(encoding="utf-8"))

    calls, events = journal(directory / "model-calls.jsonl"), journal(directory / "case-journal.jsonl")
    starts, finishes = ([row for row in calls if row["event"] == event] for event in ("started", "finished"))
    check("every_model_call_started_and_finished_once", [r["call_index"] for r in starts]
          == [r["call_index"] for r in finishes] == list(range(1, len(starts) + 1)))
    check("model_profile_matches_frozen_plan", all(
        row["request"]["model"] == plan["model"] and row["request"]["temperature"] == plan["temperature"]
        and row["request"]["max_output_tokens"] == plan["max_output_tokens"] for row in starts))
    check("model_journal_matches_reported_total", len(starts) == report["metrics"]["model_calls"]
          == sum(row["model_calls"] for row in report["cases"]))
    check("all_attempted_cases_journaled_once", [r["id"] for r in events if r["event"] == "started"]
          == [r["id"] for r in events if r["event"] == "recorded"] == [r["id"] for r in report["cases"]])
    effects_started = [r for r in events if r["event"] == "tool_started"]
    effects_finished = [r for r in events if r["event"] == "tool_finished"]
    check("tool_starts_finishes_and_totals_match", [r["request_id"] for r in effects_started]
          == [r["request_id"] for r in effects_finished] == [r["request_id"] for r in report["tool_calls"]]
          and len(effects_started) == report["metrics"]["tool_callback_starts"])

    documents = {row[0]: {"request_id": row[0], "document": row[1], "receipt": json.loads(row[2])}
                 for row in sql_rows(directory / "destination/destination.sqlite3",
                                     "SELECT request_id,document,receipt FROM exports ORDER BY request_id")}
    correct_count = wrong_documents = bad_files = clarification_count = 0
    failures, case_verification = [], []
    for row in report["cases"]:
        check("case_file_matches_report_" + row["id"], canonical(read(directory / (row["id"] + ".json"))) == canonical(row))
        declared = row.get("host_reconciliation", row.get("result", {})).get("completed", False)
        doc = documents.get(row["id"])
        physical = False
        if doc is not None:
            filename = sha(row["id"].encode()) + "." + doc["receipt"]["format"].lower()
            path = directory / "destination" / filename
            good_document = correct_document(doc["document"], row["expected_format"], truth)
            data = path.read_bytes() if path.is_file() else None
            good_file = data == doc["document"].encode("utf-8") and good_document
            check("file_receipt_hash_" + row["id"], data is not None and sha(data) == doc["receipt"]["document_sha256"])
            wrong_documents += not good_document
            bad_files += not good_file
            physical = good_document and good_file
        correct = bool(declared is True and physical)
        correct_count += correct
        clarification = (row["expected_format"] is None and row.get("result", {}).get("status") == "clarification_required"
                         and doc is None and row["model_calls"] == 0)
        clarification_count += clarification
        check("case_outcome_recomputed_" + row["id"], row["completed_correctly"] is correct
              and row["physical_work_correct"] is physical and row["expected_clarification"] is clarification)
        check("case_sqlite_matches_report_" + row["id"], canonical(row["destination_documents"])
              == canonical([] if doc is None else [doc]))
        if "error" in row:
            failures.append({"case": row["id"], "error": row["error"], "pending": row["pending_after_attempt"],
                             "explicit_abandonment": row.get("explicit_abandonment", {}).get("status")})
        case_verification.append({"id": row["id"], "completed_correctly": correct,
                                  "physical_work_correct": physical, "clarification": clarification,
                                  "repairs": row.get("result", {}).get("execution", {}).get("repair_attempts", 0)})
    check("no_unattributed_destination_effects", set(documents) <= {row["id"] for row in report["cases"]})
    expected_tasks = sum(case["expected_format"] is not None for case in plan["cases"])
    check("business_and_file_metrics_recomputed", correct_count == report["metrics"]["correct_completed_tasks"]
          and wrong_documents == report["metrics"]["wrong_committed_documents"]
          and bad_files == report["metrics"]["incorrect_or_missing_materialized_files"]
          and clarification_count == report["metrics"]["expected_clarifications"]
          and expected_tasks - correct_count == report["metrics"]["incomplete_export_tasks"]
          and len(documents) == report["metrics"]["committed_documents"])
    usage = [row.get("parsed_sdk_response", {}).get("usage") or {} for row in finishes]
    check("provider_usage_metrics_recomputed", sum(row.get("input_tokens", 0) for row in usage)
          == report["metrics"]["provider_reported_input_tokens"]
          and sum(row.get("output_tokens", 0) for row in usage) == report["metrics"]["provider_reported_output_tokens"]
          and sum(not row for row in usage) == report["metrics"]["usage_missing_calls"]
          and round(sum(row["seconds"] for row in finishes), 4) == report["metrics"]["provider_seconds"])
    check("restart_replays_without_extra_calls", report["restart"]["same_session"] is True
          and report["restart"]["replayed"] is True and report["restart"]["provider_calls"] == 0
          and report["restart"]["tool_callback_starts"] == 0)
    session_paths = list((directory / "sessions").rglob("*.sqlite3"))
    check("one_named_session_database", len(session_paths) == 1)
    if len(session_paths) == 1:
        records = {row[0]: json.loads(row[1]) for row in sql_rows(session_paths[0],
                    "SELECT request_id,payload FROM responses_workflow_requests")}
        states = [json.loads(row[0]) for row in sql_rows(session_paths[0], "SELECT payload FROM responses_workflows")]
        check("durable_requests_all_terminal_and_retained", set(records) == {row["id"] for row in report["cases"]}
              and all(row["phase"] == "completed" for row in records.values())
              and len(states) == 1 and states[0]["pending_id"] is None)
        check("native_history_matches_durable_state", canonical(states[0]["history"])
              == canonical(read(directory / "native-history.json")))
    details.update(cases=case_verification, retained_failures=failures, recomputed_correct_completed=correct_count,
                   expected_completed=expected_tasks, model_calls=len(starts), destination_effects=len(documents))
    return {"kind": "independent-read-only-pilot-audit", "valid": all(checks.values()), "checks": checks,
        "model": report["model"], "max_output_tokens": plan["max_output_tokens"], "details": details,
        "report_sha256": sha((directory / "report.json").read_bytes()),
        "model_journal_sha256": sha((directory / "model-calls.jsonl").read_bytes()),
        "case_journal_sha256": sha((directory / "case-journal.jsonl").read_bytes()),
        "audit_script_sha256": sha(Path(__file__).read_bytes()),
        "installed_wheel": str(Path(installed_wheel).resolve()), "installed_wheel_sha256": sha(Path(installed_wheel).read_bytes()),
        "limitations": ["Checks local retained evidence; not an independent hosted provider attestation.",
            "These are selected developer self-pilots, not an organisational study or context-rot benchmark.",
            "Different model/token profiles are separate experiments; successful compact results do not erase timeouts or earlier failures.",
            "Known repair uses application-supplied source values; correct results here do not prove source trust or general semantic accuracy."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--artifact-directory", type=Path, required=True)
    parser.add_argument("--installed-wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new audit path; prior evidence is retained.")
    report = audit(args.directory, args.artifact_directory, args.installed_wheel)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"valid": report["valid"], "failed_checks": [key for key, value in report["checks"].items() if not value],
                      "model": report["model"], "details": report["details"]}, indent=2))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
