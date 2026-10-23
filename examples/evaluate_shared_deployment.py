"""Bounded spawned-process SQLite exercise, with no network or model calls."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import platform
import queue
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_relay import ContentPolicy, ContextGuard, ToolExecutionError, __version__
from context_relay.organization import AccessDenied, Principal, SessionManager


def source_hashes():
    source = Path(__file__).resolve().parents[1] / "src/context_relay"
    return {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(source.glob("*.py"))}


def latency_summary(values):
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50_seconds": None, "p95_seconds": None, "p99_seconds": None}
    return {"samples": len(ordered), **{
        f"p{percent}_seconds": ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]
        for percent in (50, 95, 99)}}


def summarize_workers(rows, *, expected_workers):
    """Missing workers and unexpected errors are failures, never zero-error passes."""
    return {"passed": len(rows) == expected_workers and all(row.get("passed") is True for row in rows),
            "workers_reported": len(rows), "workers_expected": expected_workers,
            "unexpected_error_types": dict(Counter(row["error_type"] for row in rows if "error_type" in row)),
            "invariants_failed": sum(sum(value is not True for value in row.get("invariants", {}).values())
                                     for row in rows),
            "actions": sum(row.get("actions", 0) for row in rows),
            "latency": latency_summary([value for row in rows for value in row.get("durations", [])])}


def _offline_model(*, messages):
    raise AssertionError("This exercise must never call a model.")


def _worker(task, ready, release, results):
    row = {"worker": task["worker"], "passed": False, "invariants": {}, "durations": [], "actions": 0}
    try:
        # Every child independently opens SQLite. The coordinator captures the
        # contested revision before any child starts and releases all together.
        with ContextGuard(task["database"], session_id=task["contested_session"]) as contested:
            ready.put(task["worker"])
            if not release.wait(task["deadline"]):
                raise TimeoutError("Start gate deadline")
            effects = Path(task["effects"])
            callback_calls = 0

            def side_effect(_tool, _arguments):
                nonlocal callback_calls
                callback_calls += 1
                with (effects / f"contested-{task['worker']}.txt").open("x") as destination:
                    destination.write("synthetic effect")
                return {"written": True}

            started = time.perf_counter()
            outcome = contested.relay.execute(contested.session_id, {
                "tool": "write", "arguments": {"token": "shared"},
                "context_revision": task["contested_revision"]}, side_effect)
            row["durations"].append(time.perf_counter() - started)
            row["actions"] += 1
            row["contention_executed"] = outcome["executed"]
            row["contention_callback_calls"] = callback_calls
            codes = {issue["code"] for issue in outcome["check"]["issues"]}
            row["invariants"]["contender_is_one_execution_or_stale"] = (
                callback_calls == int(outcome["executed"]) and
                (outcome["executed"] or "stale_revision" in codes))

        blocked = executed = stale = 0
        for index, session_id in task["sessions"]:
            started = time.perf_counter()
            with ContextGuard(task["database"], session_id=session_id) as guard:
                turn = guard.turn()
                calls = []

                def save(_tool, arguments):
                    calls.append(arguments)
                    with (effects / f"isolated-{index}.json").open("x", encoding="utf-8") as destination:
                        json.dump(arguments, destination)
                    return {"written": True}

                wrong = turn.execute_with_recovery("write", {"token": f"session-{index + 1}"}, save)
                blocked += int(not wrong["executed"] and not calls and any(
                    issue["code"] == "content_mismatch" for issue in wrong["final_check"]["issues"]))
                good = turn.execute_with_recovery("write", {"token": f"session-{index}"}, save)
                executed += int(good["executed"] and len(calls) == 1)
                repeated = turn.execute_with_recovery("write", {"token": f"session-{index}"}, save)
                stale += int(not repeated["executed"] and len(calls) == 1 and any(
                    issue["code"] == "stale_revision" for issue in repeated["final_check"]["issues"]))
                receipts = [event for event in guard.store.events(session_id) if event["kind"] == "tool_result"]
                row["invariants"][f"isolated_receipt_{index}"] = len(receipts) == 1 and receipts[0]["status"] == "succeeded"
            row["durations"].append(time.perf_counter() - started)
            row["actions"] += 3
        row["invariants"].update(content_mismatches_blocked=blocked == len(task["sessions"]),
                                  isolated_successes=executed == len(task["sessions"]),
                                  old_turns_blocked=stale == len(task["sessions"]))
        row["isolated_sessions"] = len(task["sessions"])

        started = time.perf_counter()
        with ContextGuard(task["database"], session_id=task["failure_session"]) as guard:
            turn, failed_calls, repairs = guard.turn(), [], []

            def fail_after_effect(_tool, arguments):
                failed_calls.append(arguments)
                (effects / f"failed-{task['worker']}.txt").write_text("effect before failure")
                raise RuntimeError("synthetic callback failure")

            def repair(request):
                repairs.append(request)
                return {"token": "failure"}

            raised = False
            try:
                turn.execute_with_recovery("write", {"token": "failure"}, fail_after_effect, repair=repair)
            except ToolExecutionError:
                raised = True
            repeated = turn.execute_with_recovery("write", {"token": "failure"}, fail_after_effect, repair=repair)
            receipts = [event for event in guard.store.events(guard.session_id) if event["kind"] == "tool_result"]
            row["invariants"]["failed_callback_not_retried"] = (
                raised and len(failed_calls) == 1 and not repairs and not repeated["executed"] and
                len(receipts) == 1 and receipts[0]["status"] == "failed")
        row["durations"].append(time.perf_counter() - started)
        row["actions"] += 2

        started = time.perf_counter()
        denials = 0
        for permitted, principal in ((True, Principal("synthetic", "other")),
                                     (False, Principal("synthetic", "owner"))):
            with SessionManager(task["owner_directory"], authorize=lambda principal, action: permitted) as manager:
                try:
                    with manager.connect(_offline_model, provider="plain", principal=principal,
                                         session_id=task["owner_session"]):
                        pass
                except AccessDenied:
                    denials += 1
        row["invariants"]["cross_owner_and_host_denials"] = denials == 2
        row["durations"].append(time.perf_counter() - started)
        row["actions"] += 2
        row["passed"] = all(row["invariants"].values())
    except BaseException as exc:
        row["error_type"] = type(exc).__name__
    results.put(row)


def _new_session(database, token):
    with ContextGuard(database) as guard:
        guard.allow_tool("write", content=ContentPolicy({"token": token}, exact=("/token",)))
        return guard.session_id, guard.turn().revision


def run_exercise(directory, *, workers=4, sessions_per_worker=25, deadline=60):
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("workers must be from 1 to 16")
    if (type(sessions_per_worker) is not int or not 1 <= sessions_per_worker <= 250
            or workers * sessions_per_worker > 2000):
        raise ValueError("Use 1-250 sessions per worker and no more than 2000 total")
    if type(deadline) not in (int, float) or not 1 <= deadline <= 300:
        raise ValueError("deadline must be from 1 to 300 seconds")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    effects = directory / "effects"
    effects.mkdir()  # Existing artifacts cannot silently be reused.
    database, owners = directory / "shared.sqlite3", directory / "owners"
    setup_start = time.perf_counter()
    contested, revision = _new_session(database, "shared")
    sessions = [_new_session(database, f"session-{index}")[0] for index in range(workers * sessions_per_worker)]
    failures = [_new_session(database, "failure")[0] for _ in range(workers)]
    with SessionManager(owners, authorize=lambda principal, action: True) as manager:
        with manager.connect(_offline_model, provider="plain", principal=Principal("synthetic", "owner")) as owner:
            owner_session = owner.session_id
    setup_seconds = time.perf_counter() - setup_start
    context = mp.get_context("spawn")
    ready, results, release = context.Queue(), context.Queue(), context.Event()
    processes, rows = [], []
    started = time.perf_counter()
    limit = started + deadline
    gate_released, timed_out = None, False
    try:
        for worker in range(workers):
            task = {"worker": worker, "database": str(database), "effects": str(effects),
                    "contested_session": contested, "contested_revision": revision,
                    "sessions": [(index, sessions[index]) for index in range(worker, len(sessions), workers)],
                    "failure_session": failures[worker], "owner_directory": str(owners),
                    "owner_session": owner_session, "deadline": deadline}
            process = context.Process(target=_worker, args=(task, ready, release, results))
            process.start()
            processes.append(process)
        # Keep failure bounded even if a child dies before reaching the gate.
        for _ in processes:
            ready.get(timeout=max(0.01, limit - time.perf_counter()))
        gate_released = time.perf_counter()
        release.set()
        for _ in processes:
            rows.append(results.get(timeout=max(0.01, limit - time.perf_counter())))
    except queue.Empty:
        timed_out = True
    finally:
        release.set()
        for process in processes:
            process.join(timeout=max(0, min(1, limit - time.perf_counter())))
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        for channel in (ready, results):
            channel.close()
            channel.join_thread()
    finished = time.perf_counter()
    elapsed = finished - started
    summary = summarize_workers(rows, expected_workers=workers)
    with ContextGuard(database, session_id=contested) as guard:
        receipts = [event for event in guard.store.events(contested) if event["kind"] == "tool_result"]
    proof = {"one_contended_callback": sum(row.get("contention_callback_calls", 0) for row in rows) == 1,
             "one_contended_success": sum(row.get("contention_executed", False) for row in rows) == 1,
             "one_contended_effect_file": len(list(effects.glob("contested-*.txt"))) == 1,
             "one_contended_success_receipt": len(receipts) == 1 and receipts[0]["status"] == "succeeded",
             "all_isolated_artifacts_exact": all(
                 (effects / f"isolated-{index}.json").is_file() and
                 json.loads((effects / f"isolated-{index}.json").read_text()) == {"token": f"session-{index}"}
                 for index in range(len(sessions))),
             "all_failed_callbacks_recorded_effect": len(list(effects.glob("failed-*.txt"))) == workers,
             "all_workers_exit_cleanly": all(process.exitcode == 0 for process in processes),
             "within_deadline": not timed_out}
    active_seconds = finished - gate_released if gate_released else None
    return {"passed": summary["passed"] and all(proof.values()), "workers": workers,
            "isolated_sessions": len(sessions), "database_files_for_action_contention": 1,
            "setup_seconds": setup_seconds, "elapsed_seconds_including_spawn": elapsed,
            "active_seconds": active_seconds,
            "actions_per_second_including_spawn": summary["actions"] / elapsed,
            "actions_per_second_after_gate": summary["actions"] / active_seconds if active_seconds else None,
            "coordinator_invariants": proof, **{key: value for key, value in summary.items() if key != "passed"},
            "worker_results": sorted(rows, key=lambda row: row["worker"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--sessions-per-worker", type=int, default=25)
    parser.add_argument("--deadline", type=float, default=60)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16 or not 1 <= args.sessions_per_worker <= 250 or args.workers * args.sessions_per_worker > 2000:
        parser.error("Use 1-16 workers, 1-250 sessions each and no more than 2000 total sessions.")
    if not 1 <= args.deadline <= 300:
        parser.error("Use a 1-300 second child-process deadline.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    report = {"schema": 1, "status": "RUNNING", "relay_version": __version__,
              "python": platform.python_version(), "platform": platform.platform(),
              "sqlite": sqlite3.sqlite_version, "logical_cpu_count": os.cpu_count(),
              "process_start_method": "spawn", "source_sha256": before,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "model_calls": 0, "network_calls": 0,
              "latency_unit": "One operation group: contention, isolated open/block/write/replay, failure/replay, or owner denials.",
              "limitations": ["Synthetic single-machine filesystem exercise; not an organisational pilot or production capacity SLA.",
                              "One SQLite file serializes writes; callbacks hold its transaction and must be short.",
                              "Owner checks use SessionManager's separate owner files; raw ContextGuard is a trusted host API, not authentication.",
                              "No process crash during an external effect, distributed storage, hosted models or real users are tested.",
                              "Completed callback failures are receipted; a process crash after an external effect still needs host idempotency.",
                              "Percentiles are nearest-rank over mixed operation groups, not provider latency or individual SQL queries."]}
    with args.output.open("x", encoding="utf-8") as destination:
        def save():
            destination.seek(0)
            json.dump(report, destination, indent=2)
            destination.truncate()
            destination.flush()
        save()
        try:
            with tempfile.TemporaryDirectory(prefix="relay-shared-") as directory:
                report["result"] = run_exercise(directory, workers=args.workers,
                                               sessions_per_worker=args.sessions_per_worker, deadline=args.deadline)
            report["source_unchanged"] = before == source_hashes()
            report["status"] = "PASSED" if report["result"]["passed"] and report["source_unchanged"] else "FAILED"
        except BaseException as exc:
            report["status"], report["error_type"] = "ERROR", type(exc).__name__
        save()
    print(json.dumps({"report": str(args.output), "status": report["status"],
                      "actions": report.get("result", {}).get("actions", 0)}))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
