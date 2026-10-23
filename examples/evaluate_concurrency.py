"""Bounded SQLite/thread exercise with fixed responses, not AI or a load SLA."""

import argparse
import concurrent.futures
import hashlib
import json
import math
import platform
import statistics
import tempfile
import time
from pathlib import Path

from context_relay import __version__
from context_relay.organization import Principal, SessionManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report path; old results must remain intact.")
    source = Path(__file__).resolve().parents[1] / "src/context_relay"
    report = {"version": __version__, "python": platform.python_version(), "platform": platform.platform(),
              "source_sha256": hashlib.sha256(b"".join(p.name.encode() + p.read_bytes()
                                                       for p in sorted(source.glob("*.py")))).hexdigest(),
              "workload": "32 conversations, four owners sharing four SQLite files, two fixed-response sends each",
              "limitations": ["No AI inference, network, external users, processes, or distributed service.",
                              "End-to-end conversation timings include local setup, memory and SQLite work.",
                              "Small single-machine measurements do not establish production capacity."],
              "runs": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        def save():
            destination.seek(0)
            json.dump(report, destination, indent=2)
            destination.truncate()
            destination.flush()
        save()
        for workers in (1, 4, 8):
            with tempfile.TemporaryDirectory() as directory:
                with SessionManager(directory, authorize=lambda principal, action: True, max_concurrent=workers) as manager:
                    def model(*, messages):
                        return "Fixed offline acknowledgement."
                    def exercise(index):
                        started = time.perf_counter()
                        try:
                            with manager.connect(model, principal=Principal("synthetic", f"owner-{index % 4}")) as ai:
                                chat = ai.conversation()
                                chat.send("Export as CSV.")
                                chat.send("Export as JSON.")
                                correct = chat.memory_snapshot()["entries"][0]["value"] == "JSON" and len(chat.history) == 4
                                return {"completed": correct, "seconds": time.perf_counter() - started}
                        except Exception as exc:
                            return {"completed": False, "seconds": time.perf_counter() - started,
                                    "error_type": type(exc).__name__}
                    started = time.perf_counter()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                        outcomes = list(pool.map(exercise, range(32)))
                    elapsed = time.perf_counter() - started
                    durations = sorted(outcome["seconds"] for outcome in outcomes)
                    completed = sum(outcome["completed"] for outcome in outcomes)
                    report["runs"].append({"workers": workers, "completed": completed, "attempted": 32,
                                           "elapsed_seconds": elapsed, "completed_per_second": completed / elapsed,
                                           "p50_seconds": statistics.median(durations),
                                           "p95_nearest_rank_seconds": durations[math.ceil(0.95 * len(durations)) - 1],
                                           "metrics": manager.metrics(), "outcomes": outcomes})
                    save()
        report["status"] = "complete" if all(run["completed"] == 32 for run in report["runs"]) else "complete_with_failures"
        save()
    print(json.dumps({"report": str(args.output), "status": report["status"],
                      "completed": [run["completed"] for run in report["runs"]]}))


if __name__ == "__main__":
    main()
