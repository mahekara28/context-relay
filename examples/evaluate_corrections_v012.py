"""Evaluate a declared correction corpus; no network, LLM or expected-value injection.

The default is the frozen implementer-authored development corpus. A separately
authored corpus may be supplied; keep its origin and prior exposure explicit.
Reports are exclusive-create JSON and include inputs, expectations and failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from evaluate_corrections import ROOT, __version__, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "evaluations/corrections-v0.12-development-corpus.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", default="implementer-authored development corpus")
    args = parser.parse_args()
    raw = args.corpus.read_bytes()
    cases = json.loads(raw)["cases"]
    results = [evaluate(case) for case in cases]
    categories = {}
    for result in results:
        result["passed"] = result["passed"] and result["authority_unchanged"]
        counts = categories.setdefault(result["category"], {"passed": 0, "total": 0})
        counts["passed"] += int(result["passed"])
        counts["total"] += 1
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "version": __version__,
              "kind": "deterministic extraction; no LLM or network", "corpus_origin": args.origin,
              "corpus_sha256": hashlib.sha256(raw).hexdigest(),
              "memory_source_sha256": hashlib.sha256((ROOT / "src/context_relay/memory.py").read_bytes()).hexdigest(),
              "summary": {"passed": sum(row["passed"] for row in results), "total": len(results), "categories": categories},
              "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    main()
