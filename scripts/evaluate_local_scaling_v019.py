"""Bounded local SQLite/process experiment. No provider or model is called."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing as mp
from pathlib import Path
import platform
import queue
import sys
import time


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from context_relay import ContextGuard, RelayConfig, auto_connect, __version__
from context_relay.store import StoreBusy


def source_hashes():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((PROJECT / "src/context_relay").glob("*.py"))}


def latency(values):
    values = sorted(values)
    return {"samples": len(values), **{
        f"p{percent}_seconds": values[max(0, math.ceil(len(values) * percent / 100) - 1)] if values else None
        for percent in (50, 95, 99)}}


def summarize(rows, workers):
    identifiers = [row.get("worker") for row in rows]
    return {
        "passed": (sorted(identifiers) == list(range(workers))
                   and all(row.get("passed") is True for row in rows)),
        "workers_expected": workers, "workers_reported": len(rows),
        "missing_workers": sorted(set(range(workers)) - set(identifiers)),
        "duplicate_workers": sorted(worker for worker, count in Counter(identifiers).items() if count > 1),
        "unexpected_errors": dict(Counter(row["error_type"] for row in rows if "error_type" in row)),
        "completed_jobs": sum(row.get("completed_jobs", 0) for row in rows),
        "effects": sum(row.get("effects", 0) for row in rows),
        "blocked_wrong_actions": sum(row.get("blocked_wrong_actions", 0) for row in rows),
        "stale_replays_blocked": sum(row.get("stale_replays_blocked", 0) for row in rows),
        "busy_results": sum(row.get("busy_results", 0) for row in rows),
        "latency": latency([value for row in rows for value in row.get("durations", [])]),
    }


def _never_model(**kwargs):
    raise AssertionError("This exercise must not call a model.")


def _prepare(guard):
    guard.remember("approved_amount", 20)
    guard.allow_tool("refund", bindings={"amount": "approved_amount"})
    return {"database": guard.store.path, "session_id": guard.session_id,
            "revision": guard.turn().revision}


def _worker(task, ready, release, results):
    row = {"worker": task["worker"], "passed": False, "durations": [], "effects": 0,
           "completed_jobs": 0, "blocked_wrong_actions": 0, "stale_replays_blocked": 0,
           "busy_results": 0}
    try:
        with ContextGuard(task["database"], session_id=task["session_id"],
                          busy_timeout_ms=task["busy_timeout_ms"]) as guard:
            ready.put(task["worker"])
            if not release.wait(task["deadline"]):
                raise TimeoutError("Worker start gate expired.")
            effect_directory = Path(task["effects"])

            def execute(tool, arguments):
                # Literal independent oracle: every allowed refund is exactly 20.
                if tool != "refund" or arguments != {"amount": 20}:
                    raise AssertionError("Incorrect action reached the callback.")
                marker = effect_directory / f"worker-{task['worker']}-effect-{row['effects']}.json"
                with marker.open("x", encoding="utf-8") as stream:
                    json.dump({"tool": tool, "arguments": arguments}, stream)
                row["effects"] += 1
                return {"saved": True}

            if task["race"]:
                start = time.perf_counter()
                try:
                    result = guard.relay.execute(guard.session_id, {
                        "tool": "refund", "arguments": {"amount": 20},
                        "context_revision": task["revision"]}, execute)
                    row["passed"] = (result["executed"] and row["effects"] == 1) or (
                        not result["executed"] and row["effects"] == 0 and
                        "stale_revision" in {issue["code"] for issue in result["check"]["issues"]})
                    row["stale_replays_blocked"] = int(not result["executed"])
                except StoreBusy as exc:
                    row["busy_results"] += 1
                    row["passed"] = exc.before_transaction and row["effects"] == 0
                row["durations"].append(time.perf_counter() - start)
                return

            for _ in range(task["jobs"]):
                start = time.perf_counter()
                turn = guard.turn()
                wrong = turn.execute_with_recovery("refund", {"amount": 2000}, execute)
                right = turn.execute_with_recovery("refund", {"amount": 20}, execute)
                repeated = turn.execute_with_recovery("refund", {"amount": 20}, execute)
                wrong_blocked = not wrong["executed"] and "binding_mismatch" in {
                    issue["code"] for issue in wrong["final_check"]["issues"]}
                replay_blocked = not repeated["executed"] and "stale_revision" in {
                    issue["code"] for issue in repeated["final_check"]["issues"]}
                row["blocked_wrong_actions"] += int(wrong_blocked)
                row["stale_replays_blocked"] += int(replay_blocked)
                row["completed_jobs"] += int(wrong_blocked and right["executed"] and replay_blocked)
                row["durations"].append(time.perf_counter() - start)
            receipts = [event for event in guard.store.events(guard.session_id) if event["kind"] == "tool_result"]
            row["receipt_count"] = len(receipts)
            row["passed"] = (row["completed_jobs"] == task["jobs"] == row["effects"] == len(receipts)
                             and all(receipt["status"] == "succeeded" for receipt in receipts))
    except StoreBusy as exc:
        row["busy_results"] += 1
        row.update(error_type="StoreBusy", busy_phase=exc.phase, busy_before_transaction=exc.before_transaction)
    except BaseException as exc:
        row["error_type"] = type(exc).__name__
    finally:
        results.put(row)


def run_case(directory, *, mode, workers, jobs=8, busy_timeout_ms=2000, deadline=30, race=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    effects = directory / "effects"
    effects.mkdir()
    sessions = []
    if race:
        with ContextGuard(directory / "shared.sqlite3", busy_timeout_ms=busy_timeout_ms) as guard:
            shared = _prepare(guard)
        sessions = [shared] * workers
    else:
        for index in range(workers):
            if mode == "named":
                with auto_connect(_never_model, conversation=f"worker-{index}", storage=directory / "named",
                                  memory=False, config=RelayConfig(busy_timeout_ms=busy_timeout_ms)) as agent:
                    sessions.append(_prepare(agent.guard))
            else:
                with ContextGuard(directory / "shared.sqlite3", busy_timeout_ms=busy_timeout_ms) as guard:
                    sessions.append(_prepare(guard))
    context = mp.get_context("spawn")
    ready, results, release = context.Queue(), context.Queue(), context.Event()
    processes, rows, ready_workers = [], [], []
    started = time.perf_counter()
    expires = started + deadline
    gate_released = None
    try:
        for worker, session in enumerate(sessions):
            task = {**session, "worker": worker, "effects": str(effects), "jobs": jobs,
                    "busy_timeout_ms": busy_timeout_ms, "deadline": deadline, "race": race}
            process = context.Process(target=_worker, args=(task, ready, release, results))
            process.start()
            processes.append(process)
        for _ in processes:
            ready_workers.append(ready.get(timeout=max(0.01, expires - time.perf_counter())))
        gate_released = time.perf_counter()
        release.set()
        for _ in processes:
            rows.append(results.get(timeout=max(0.01, expires - time.perf_counter())))
    except queue.Empty:
        pass
    finally:
        release.set()
        for process in processes:
            process.join(timeout=max(0, min(2, expires - time.perf_counter())))
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        while True:
            try:
                rows.append(results.get_nowait())
            except queue.Empty:
                break
        ready.close()
        results.close()
    markers = list(effects.glob("*.json"))
    summary = summarize(rows, workers)
    summary.update(mode="shared_revision_race" if race else mode, jobs_per_worker=0 if race else jobs,
                   busy_timeout_ms=busy_timeout_ms, marker_count=len(markers),
                   worker_exitcodes=[process.exitcode for process in processes],
                   ready_workers=sorted(ready_workers),
                   startup_seconds=None if gate_released is None else gate_released - started,
                   wall_seconds=time.perf_counter() - started, workers=sorted(rows, key=lambda row: row["worker"]))
    summary["passed"] = (summary["passed"] and len(markers) == summary["effects"]
                         and all(process.exitcode == 0 for process in processes)
                         and sorted(ready_workers) == list(range(workers)))
    if race:
        summary["passed"] = summary["passed"] and len(markers) == 1
    else:
        summary["passed"] = summary["passed"] and summary["completed_jobs"] == workers * jobs
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", default="1,4,8")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--busy-timeout-ms", type=int, default=2000)
    parser.add_argument("--race-only", action="store_true")
    args = parser.parse_args()
    workers = [int(value) for value in args.workers.split(",")]
    if (not workers or len(workers) != len(set(workers)) or any(not 1 <= value <= 16 for value in workers)
            or not 1 <= args.jobs <= 100 or not 0 <= args.busy_timeout_ms <= 60000):
        parser.error("Use distinct worker counts 1..16, jobs 1..100 and timeout 0..60000ms.")
    if args.output.exists() or args.directory.exists():
        parser.error("Use fresh output and experiment paths; prior evidence is never overwritten.")
    before = source_hashes()
    cases = []
    for count in workers:
        for mode in (("race",) if args.race_only else ("shared", "named")):
            cases.append(run_case(args.directory / f"{mode}-{count}", mode=mode, workers=count,
                                  jobs=args.jobs, busy_timeout_ms=args.busy_timeout_ms, race=mode == "race"))
    after = source_hashes()
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "version": __version__,
              "python": platform.python_version(), "platform": platform.platform(),
              "model_calls": 0, "network_calls": 0, "source_unchanged": before == after,
              "source_hashes_before": before, "source_hashes_after": after, "cases": cases,
              "passed": before == after and all(case["passed"] for case in cases),
              "scope": "Bounded offline single-host experiment; not distributed or production capacity proof.",
              "latency_unit": "Completed three-action job (wrong, correct, stale replay); race cases measure one action."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"passed": report["passed"], "report": str(args.output), "cases": len(cases),
                      "completed_jobs": sum(case["completed_jobs"] for case in cases),
                      "effects": sum(case["effects"] for case in cases)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
