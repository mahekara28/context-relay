"""Score frozen cases; keep clear-update recall separate from safe abstention."""
import argparse
import hashlib
import json
from pathlib import Path

from context_relay import AutomaticMemory, RelayStore, __version__


def evaluate(corpus_path):
    raw = Path(corpus_path).read_bytes()
    corpus = json.loads(raw)
    rows = []
    for case in corpus["cases"]:
        history = []
        with RelayStore() as store:
            memory = AutomaticMemory(store, store.create_session(case["id"])["id"])
            for text in case["before"]:
                history.append({"role": "user", "content": text})
                memory.process(history)
                history.append({"role": "assistant", "content": "Acknowledged."})
            before = {e["key"]: e["value"] for e in memory.entries()}
            history.append({"role": "user", "content": case["text"]})
            memory.process(history)
            after = {e["key"]: e["value"] for e in memory.entries()}
            pending = memory.pending()
            if case["kind"] == "clear":
                passed = after == case["expected"] and not pending
                wrong_update = after != before and after != case["expected"]
            elif case["kind"] == "review":
                passed = after == before and any(p["value"] == case["candidate"] for p in pending)
                wrong_update = after != before
            else:
                passed = after == before and not pending
                wrong_update = after != before
            provenance_ok = all(
                record["source"]["quote"] == history[record["source"]["index"]]["content"][
                    record["source"]["start"]:record["source"]["end"]]
                for record in memory.entries() + pending)
            rows.append(dict(id=case["id"], kind=case["kind"], passed=passed and provenance_ok,
                             wrong_update=wrong_update, provenance_ok=provenance_ok,
                             before=before, after=after,
                             pending=[{k: p[k] for k in ("key", "value", "reason")} for p in pending]))
    return {"version": __version__, "dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "description": corpus["description"], "cases": rows,
            "by_kind": {kind: {"passed": sum(r["passed"] for r in rows if r["kind"] == kind),
                               "total": sum(r["kind"] == kind for r in rows)}
                        for kind in ("clear", "review", "abstain")},
            "wrong_updates": sum(r["wrong_update"] for r in rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.corpus)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, ensure_ascii=False)
    print(json.dumps({k: result[k] for k in ("version", "dataset_sha256", "by_kind", "wrong_updates")}, indent=2))
