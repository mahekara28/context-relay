"""Evaluate a frozen wording corpus against a selected local Relay version.

No inference or network. Clear capture, uncertainty and false alarms are separate.
Known casing variants are graded as equivalent; exact canonical capture is also
reported. The corpus must be fixed before implementation scoring.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DOMAINS = {"export_format": ("CSV", "JSON"), "output_format": ("CSV", "JSON"),
            "response_language": ("English", "Hindi"),
            "response_style": ("short", "brief", "concise", "detailed")}


def semantic(values):
    result = dict(values)
    for key, value in values.items():
        if type(value) is str:
            result[key] = next((word for word in _DOMAINS.get(key, ())
                                if word.casefold() == value.strip().casefold()), value)
    return result


def grade(case, before, after, pending):
    expected = {**before, **case.get("expected", {})}
    old, new, target = semantic(before), semantic(after), semantic(expected)
    changed = {key: value for key, value in new.items() if key not in old or value != old[key]}
    removed = set(old) - set(new)
    wrong = bool(removed or any(key not in target or value != target[key] for key, value in changed.items()))
    if case["kind"] != "clear":
        wrong = bool(removed or changed)
    flags = [row for row in pending if row.get("reason") == "unparsed_correction"
             and row.get("value") is None]
    flagged = set().union(*(set(row["targets"]) for row in flags)) if flags else set()
    unchanged = new == old
    return {
        "correct_automatic_update": case["kind"] == "clear" and new == target and not pending,
        "exact_canonical_update": case["kind"] == "clear" and after == expected and not pending,
        "correct_uncertainty": case["kind"] == "uncertain" and unchanged
                               and flagged == set(case["targets"]) and len(flags) == len(pending),
        "correct_abstention": case["kind"] == "abstain" and unchanged and not pending,
        "wrong_active_update": wrong,
        "clarification_flagged": bool(flags),
        "false_alarm": case["kind"] == "abstain" and bool(pending),
    }


def evaluate(case, api):
    messages=[]
    for text in case["before"]:
        messages.extend([{"role":"user","content":text},{"role":"assistant","content":"Acknowledged."}])
    with api.ContextGuard() as guard:
        memory=api.AutomaticMemory(guard.store,guard.session_id,policy=api.MemoryPolicy(max_chars=16000))
        if messages:
            memory.process(messages[:-1])
        before={row["key"]:row["value"] for row in memory.entries()}
        messages.append({"role":"user","content":case["text"]})
        report=memory.process(messages)
        after={row["key"]:row["value"] for row in memory.entries()}
        pending=memory.pending()
        records=memory.entries()+pending
        spans=all(row["source"]["quote"] == messages[row["source"]["index"]]["content"][
                  row["source"]["start"]:row["source"]["end"]] for row in records)
        snapshot=memory.snapshot()
        memory.process(messages)
        replay=memory.snapshot() == snapshot
        authority=guard.store.facts(guard.session_id) == {}
        return {"id":case["id"],"kind":case["kind"],"before":before,"after":after,
                "pending":pending,"snapshot":snapshot,"process_report":report,
                "source_spans_exact":spans,"replay_unchanged":replay,"authority_unchanged":authority,
                **grade(case,before,after,pending)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus",type=Path,required=True)
    parser.add_argument("--source",type=Path,default=ROOT/"src")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; preserve original evidence.")
    raw=args.corpus.read_bytes()
    corpus=json.loads(raw)
    source=args.source.resolve()
    sys.path.insert(0,str(source))
    import context_relay as api
    assert Path(api.__file__).resolve().is_relative_to(source)
    rows=[evaluate(case,api) for case in corpus["cases"]]
    metrics=("correct_automatic_update","exact_canonical_update","correct_uncertainty",
             "correct_abstention","wrong_active_update","clarification_flagged","false_alarm")
    summary={key:sum(row[key] for row in rows) for key in metrics}
    summary["categories"]={kind:sum(row["kind"]==kind for row in rows) for kind in ("clear","uncertain","abstain")}
    summary["provenance_replay_authority_passed"]=all(row["source_spans_exact"] and row["replay_unchanged"]
                                                      and row["authority_unchanged"] for row in rows)
    hashes={path.relative_to(source).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((source/"context_relay").rglob("*.py"))}
    result={"schema":1,"version":api.__version__,"corpus_sha256":hashlib.sha256(raw).hexdigest(),
            "source_sha256":hashes,"grading":"Finite known casing equivalence plus separate canonical score; no model calls.",
            "summary":summary,"cases":rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as stream:
        json.dump(result,stream,indent=2,ensure_ascii=True)
        stream.write("\n")
    print(json.dumps({"version":api.__version__,"summary":summary}))


if __name__ == "__main__":
    main()
