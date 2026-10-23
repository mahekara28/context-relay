"""Frozen v0.11 wording corpus; deterministic extraction only, no model calls.

The corpus and expected outcomes were saved before changing memory.py. This is
an implementation-development regression corpus, not a held-out language study.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from context_relay import AutomaticMemory, MemoryPolicy, RelayStore, __version__


def evaluate(case: dict) -> dict:
    messages = []
    for text in case.get("before", []):
        messages.extend([{"role": "user", "content": text},
                         {"role": "assistant", "content": "Acknowledged."}])
    messages.append({"role": case.get("role", "user"), "content": case["text"]})
    if messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": "Continue."})
    with RelayStore() as store:
        memory = AutomaticMemory(store, store.create_session(case["id"])["id"],
                                 policy=MemoryPolicy(mode=case.get("mode", "auto")))
        report = memory.process(messages)
        entries, pending = memory.entries(), memory.pending()
        values = {entry["key"]: entry["value"] for entry in entries}
        proposals = [{k: p[k] for k in ("key", "value", "reason", "targets")} for p in pending]
        exact_sources = all(record["source"]["quote"] == messages[record["source"]["index"]]["content"][
            record["source"]["start"]:record["source"]["end"]] for record in entries + pending)
        expected = values == case["expected_entries"] and proposals == case.get("expected_pending", [])
        return {"id": case["id"], "category": case["category"], "passed": expected and exact_sources,
                "input": messages, "expected_entries": case["expected_entries"],
                "expected_pending": case.get("expected_pending", []), "entries": values,
                "pending": proposals, "exact_source_spans": exact_sources,
                "authority_unchanged": store.facts(memory.session_id) == {}, "process_report": report}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus_path = ROOT / "evaluations" / "corrections-v0.11-corpus.json"
    raw = corpus_path.read_bytes()
    corpus = json.loads(raw)
    results = [evaluate(case) for case in corpus["cases"]]
    categories = {}
    for result in results:
        score = categories.setdefault(result["category"], {"passed": 0, "total": 0})
        score["passed"] += int(result["passed"])
        score["total"] += 1
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "version": __version__,
              "kind": "deterministic development corpus; no LLM or network",
              "corpus_sha256": hashlib.sha256(raw).hexdigest(),
              "memory_source_sha256": hashlib.sha256((ROOT / "src/context_relay/memory.py").read_bytes()).hexdigest(),
              "summary": {"passed": sum(r["passed"] for r in results), "total": len(results), "categories": categories},
              "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
