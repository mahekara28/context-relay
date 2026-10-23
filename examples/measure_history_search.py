"""Reproducible synthetic archive measurement; no provider/model is called."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

from context_relay import RelayConfig, connect


def measure(rows: int = 10000, repeats: int = 5) -> dict:
    if type(rows) is not int or not 100 <= rows <= 100000:
        raise ValueError("rows must be from 100 to 100000")
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError("repeats must be from 1 to 20")

    def no_provider(**kwargs):
        raise AssertionError("This fixture must not call a provider")

    with tempfile.TemporaryDirectory() as directory:
        with connect(no_provider, config=RelayConfig(db_path=str(Path(directory) / "search.sqlite3"))) as agent:
            chat = agent.conversation(max_history_chars=20000000)
            # Direct fixture insertion avoids pretending these generated messages
            # are real users or provider responses. Only retrieval is measured.
            with agent.guard.store._transaction(write=True):
                for index in range(rows):
                    suffix = " needle7429" if index == rows - 1 else ""
                    chat._append({"role": "user", "content": f"Synthetic invoice {index} blue report archive{suffix}"})
            started = time.perf_counter()
            indexed = agent.guard.store.enable_history_index()
            build_ms = (time.perf_counter() - started) * 1000
            report = {"kind": "synthetic_archive_search", "sqlite_version": sqlite3.sqlite_version,
                      "rows": rows, "query": "needle7429", "provider_calls": 0,
                      "index_available": indexed, "index_build_ms": round(build_ms, 3),
                      "caveat": "One rare-token query; no production load, provider, or model evaluation."}

            def search(mode):
                cursor, scanned, matches = None, 0, []
                while True:
                    page = chat.search_history("needle7429", mode=mode, after=cursor, scan_limit=1000)
                    scanned += page["scanned"]
                    matches.extend((item["session_id"], item["index"]) for item in page["messages"])
                    cursor = page["next_cursor"]
                    if cursor is None:
                        return scanned, matches

            for mode in ("literal", "terms") if indexed else ("literal",):
                search(mode)  # One identical unmeasured warm-up for each mode.
                timings = []
                for _ in range(repeats):
                    started = time.perf_counter()
                    scanned, matches = search(mode)
                    timings.append(round((time.perf_counter() - started) * 1000, 3))
                report[mode] = {"timings_ms": timings, "median_ms": statistics.median(timings),
                                "rows_decoded_by_relay": scanned, "match_indices": [index for _, index in matches]}
                assert matches == [(chat.session_id, rows - 1)]
            if indexed:
                plan = agent.guard.store._connection.execute(
                    "EXPLAIN QUERY PLAN SELECT s.ordinal, m.session_id, m.sequence, m.payload "
                    "FROM conversation_search JOIN conversation_messages m ON m.rowid = conversation_search.rowid "
                    "JOIN conversation_segments s ON s.session_id = m.session_id "
                    "WHERE conversation_search MATCH ? AND s.root_id = ? AND s.ordinal <= ? "
                    "AND (s.ordinal > ? OR (s.ordinal = ? AND m.sequence > ?)) "
                    "ORDER BY s.ordinal, m.sequence LIMIT ?", ('"needle7429"', chat.session_id, 0, -1, -1, -1, 1001))
                report["indexed_query_plan"] = [row[3] for row in plan]
                assert any("VIRTUAL TABLE INDEX" in item for item in report["indexed_query_plan"])
            return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(measure(args.rows, args.repeats), indent=2)
    if args.output is not None:
        with args.output.open("x", encoding="utf-8") as target:
            target.write(result + "\n")
    print(result)
