"""Measure instruction decisions without treating a pause as a completed task.

Offline by default. --model opts into an already installed local Ollama model;
model suggestions are measured separately and are never approved by this runner.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def _mutation(standing, baseline, allowed):
    if standing is None:
        return None
    missing = object()
    return any(key not in allowed or standing.get(key, missing) != allowed[key]
               for key in standing.keys() | baseline.keys()
               if standing.get(key, missing) != baseline.get(key, missing))


def _state(memory):
    """Read each view independently so a broken view does not hide mutations."""
    def effective():
        snapshot = memory.effective_snapshot()
        for entry in snapshot["entries"]:
            entry["key"], entry["value"], entry["disputed"]
        return snapshot

    state, errors = {}, {}
    for name, read in (("snapshot", effective),
                       ("standing", lambda: {e["key"]: e["value"] for e in memory.entries()}),
                       ("pending", memory.pending)):
        try:
            state[name] = read()
        except Exception as error:
            state[name] = None
            errors[name] = type(error).__name__
    state["state_errors"] = errors
    return state


def _suggestions(pending):
    return None if pending is None else [p for p in pending
        if p["reason"] == "interpreted_needs_review" and p["value"] is not None]


def _correct_suggestions(proposals, expected):
    return bool(expected and proposals and len(proposals) == len(expected)
                and len({p["key"] for p in proposals}) == len(proposals)
                and {p["key"]: p["value"] for p in proposals} == expected)


def _causes(error):
    chain, seen = [], set()
    while error is not None and id(error) not in seen and len(chain) < 5:
        seen.add(id(error))
        chain.append({"type": type(error).__name__, "message": str(error)[:500]})
        error = error.__cause__ or error.__context__
    return chain


def evaluate_case(case, corpus, api, interpreter=None):
    history = [{"role": "user", "content": text} for text in corpus["seed"]]
    calls, interpreter_seconds = 0, 0.0

    def measured(request):
        nonlocal calls, interpreter_seconds
        calls += 1
        start = time.perf_counter()
        try:
            return interpreter.callback(request)
        finally:
            interpreter_seconds += time.perf_counter() - start

    started = time.perf_counter()
    row = {"id": case["id"], "split": case["split"], "language": case["language"],
           "kind": case["kind"], "decision_correct": False, "suggestion_correct": False}
    with api.RelayStore() as store:
        memory = api.AutomaticMemory(store, store.create_session("Instruction evaluation")["id"],
                                     policy=api.MemoryPolicy(max_chars=24000))
        allowed = case.get("expected", {}) if case["kind"] == "change" else {}
        try:
            memory.process(history)
            seed = {e["key"]: e["value"] for e in memory.entries()}
            if seed != corpus["standing"]:
                raise ValueError("The seed did not establish the declared baseline.")
            if interpreter is not None:
                memory.interpreter = replace(interpreter, callback=measured)
            history.append({"role": "user", "content": case["text"]})
            memory.process(history)
            snapshot = memory.effective_snapshot()
            actual = {e["key"]: e["value"] for e in snapshot["entries"] if not e["disputed"]}
            disputed = sorted(e["key"] for e in snapshot["entries"] if e["disputed"])
            standing = {e["key"]: e["value"] for e in memory.entries()}
            pending = memory.pending()
            suggestions = _suggestions(pending)
            expected = corpus["standing"] | case.get("expected", {})
            questions = case["kind"] in ("uncertain", "scope_question")
            if questions:
                targets = set(case["targets"])
                correct = (set(disputed) == targets and standing == corpus["standing"]
                           and actual == {k: v for k, v in corpus["standing"].items() if k not in targets}
                           and all(set(p["targets"]) <= targets for p in pending))
                outcome = "clarification" if correct else "missed_clarification"
            else:
                correct = not disputed and not pending and actual == expected
                if correct and case["kind"] == "turn":
                    correct = standing == corpus["standing"] and all(
                        next(e for e in snapshot["entries"] if e["key"] == key).get("scope") == "turn"
                        for key in case["expected"])
                    # A new independent request must restore the standing preference.
                    memory.interpreter = None
                    memory.process(history + [{"role": "user", "content": "Please produce another export."}])
                    resumed = memory.effective_snapshot()
                    correct &= not any(e["disputed"] for e in resumed["entries"]) and {
                        e["key"]: e["value"] for e in resumed["entries"]} == corpus["standing"]
                    row["after_independent_request"] = resumed["entries"]
                outcome = ("recognized" if case["kind"] != "unchanged" else "unchanged") if correct else (
                    "review_needed" if disputed or pending else
                    "missed_change" if actual == corpus["standing"] else "wrong_value")
            row.update(decision_correct=bool(correct), outcome=outcome, actual=actual,
                       standing=standing, disputed=disputed, pending=pending, suggestions=suggestions,
                       suggestion_correct=_correct_suggestions(suggestions, allowed) and len(pending) == len(suggestions),
                       incorrect_mutation=_mutation(standing, corpus["standing"], allowed),
                       unnecessary_review=case["kind"] == "unchanged" and bool(disputed or pending))
        except Exception as error:
            state = _state(memory)
            snapshot = state.pop("snapshot")
            disputed = None if snapshot is None else sorted(e["key"] for e in snapshot["entries"] if e["disputed"])
            actual = None if snapshot is None else {e["key"]: e["value"] for e in snapshot["entries"] if not e["disputed"]}
            pending = state["pending"]
            row.update(outcome="error", error_type=type(error).__name__,
                       error=str(error)[:500], cause_chain=_causes(error), **state,
                       actual=actual, disputed=disputed, suggestions=_suggestions(pending),
                       incorrect_mutation=_mutation(state["standing"], corpus["standing"], allowed),
                       unnecessary_review=None if disputed is None or pending is None else
                           case["kind"] == "unchanged" and bool(disputed or pending))
    row.update(interpreter_calls=calls, interpreter_seconds=round(interpreter_seconds, 4),
               seconds=round(time.perf_counter() - started, 4),
               timing_scope="case_wall_time_including_setup_seed_and_checks")
    return row


def summarize(rows):
    groups = {}
    for split in ("development", "holdout", "all"):
        selected = [r for r in rows if split == "all" or r["split"] == split]
        groups[split] = {"cases": len(selected),
                        "decisions_correct": sum(r["decision_correct"] for r in selected),
                        "correct_suggestions_requiring_review": sum(r["suggestion_correct"] for r in selected),
                        "incorrect_mutations": sum(r["incorrect_mutation"] is True for r in selected),
                        "unknown_mutation_states": sum(r["incorrect_mutation"] is None for r in selected),
                        "unnecessary_reviews": sum(r["unnecessary_review"] is True for r in selected),
                        "unknown_review_states": sum(r["unnecessary_review"] is None for r in selected),
                        "interpreter_calls": sum(r["interpreter_calls"] for r in selected),
                        "outcomes": dict(Counter(r["outcome"] for r in selected))}
    return groups


def _source_hashes(source):
    return {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((source / "context_relay").rglob("*.py"))}


def _journal(stream, event, data):
    stream.write(json.dumps({"event": event, "data": data}, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "evaluations/instructions-v023.json")
    parser.add_argument("--source", type=Path, default=ROOT / "src")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", help="Explicitly opt into this installed local Ollama model")
    parser.add_argument("--ids", nargs="+", help="Evaluate only these exact case IDs")
    args = parser.parse_args(argv)
    journal_path = args.output.with_name(args.output.name + ".jsonl")
    if args.output.exists() or journal_path.exists():
        parser.error("Output or its journal already exists; retain prior evidence and choose a new path.")
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    import context_relay as api
    if not Path(api.__file__).resolve().is_relative_to(source):
        parser.error("The requested source was not imported.")
    raw = args.corpus.read_bytes()
    corpus = json.loads(raw)
    cases = corpus["cases"]
    if args.ids:
        if set(args.ids) - {case["id"] for case in cases}:
            parser.error("Unknown case ID.")
        cases = [case for case in cases if case["id"] in args.ids]
    interpreter = api.MemoryInterpreter.from_ollama(model=args.model) if args.model else None
    rows = []
    source_before = _source_hashes(source)
    runner_before = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest = {"schema": 2, "version": api.__version__, "dataset_kind": "synthetic",
                "corpus_sha256": hashlib.sha256(raw).hexdigest(), "runner_sha256": runner_before,
                "source_sha256": source_before, "model": args.model,
                "selected_case_ids": [case["id"] for case in cases]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("x", encoding="utf-8") as journal:
        _journal(journal, "started", manifest)
        try:
            for case in cases:
                _journal(journal, "case_started", {"id": case["id"]})
                row = evaluate_case(case, corpus, api, interpreter)
                rows.append(row)
                _journal(journal, "case_completed", row)
                print(json.dumps({"id": row["id"], "outcome": row["outcome"],
                                  "interpreter_calls": row["interpreter_calls"]}), flush=True)
        except BaseException as error:
            _journal(journal, "interrupted", {"completed_cases": len(rows), "error_type": type(error).__name__})
            raise
        result = {**manifest, "complete": True, "source_unchanged": source_before == _source_hashes(source),
                  "runner_unchanged": runner_before == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "summary": summarize(rows), "cases": rows,
                  "limitations": ["Agent-authored synthetic examples, not an independent human benchmark.",
                      "Decision and suggestion accuracy do not measure completed work.",
                      "No model suggestion is approved; schema validity is not semantic correctness.",
                      "Every case starts with fresh memory; no population accuracy is claimed."]}
        encoded = (json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        with args.output.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _journal(journal, "completed", {"completed_cases": len(rows), "report_sha256": hashlib.sha256(encoded).hexdigest()})
    print(json.dumps(result["summary"]))
    return int(not result["source_unchanged"] or not result["runner_unchanged"]
               or any(row["outcome"] == "error" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
