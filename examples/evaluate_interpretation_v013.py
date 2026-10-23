"""Evaluate local review suggestions on frozen correction cases, without retries.

Only an already installed local model is used. Expected labels enter scoring,
never inference. The append-only JSONL journal retains full synthetic payloads,
raw responses, filtering decisions and errors. No suggestion is confirmed.
"""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from context_relay import AutomaticMemory, MemoryInterpreter, RelayStore, __version__
from evaluate_interpretation_v012 import SCHEMA
from ollama_agent import LocalOllama


ROOT = Path(__file__).resolve().parents[1]
OPTIONS = {"stream": False, "format": SCHEMA,
           "options": {"temperature": 0, "seed": 17, "num_predict": 512, "num_ctx": 4096},
           "keep_alive": "2m"}


def score(case, before, after, pending):
    """Keep active-rule success separate from exact, still-unconfirmed candidates."""
    combined = after | {p["key"]: p["value"] for p in pending}
    if case["kind"] == "clear":
        accepted = after == case["expected"] and not pending
        proposed = (after == before and bool(pending) and combined == case["expected"]
                    and len({p["key"] for p in pending}) == len(pending)
                    and all(p["key"] in case["expected"] for p in pending))
        return {"accepted_by_rules": accepted, "exact_pending_candidate": proposed,
                "passed": accepted or proposed}
    if case["kind"] == "review":
        proposed = (after == before and len(pending) == 1
                    and pending[0]["key"] in before and pending[0]["value"] == case["candidate"])
        return {"accepted_by_rules": False, "exact_pending_candidate": proposed, "passed": proposed}
    return {"accepted_by_rules": False, "exact_pending_candidate": False,
            "passed": after == before and not pending}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--max-calls", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.max_calls <= 32:
        parser.error("--max-calls must be between 1 and 32")
    raw = args.corpus.read_bytes()
    corpus = json.loads(raw)
    client = LocalOllama(timeout=90)
    source_hashes = {name: hashlib.sha256((ROOT / "src/context_relay" / name).read_bytes()).hexdigest()
                     for name in ("memory.py", "interpretation.py")}
    rows, calls = [], []
    with args.output.open("x", encoding="utf-8") as journal:
        def record(kind, **data):
            journal.write(json.dumps({"record": kind, **data}, ensure_ascii=True) + "\n")
            journal.flush()

        record("manifest", started_at=datetime.now(timezone.utc).isoformat(), version=__version__,
               corpus_sha256=hashlib.sha256(raw).hexdigest(), source_sha256=source_hashes,
               runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               description=corpus["description"], model=args.model, options=OPTIONS,
               max_calls=args.max_calls, timeout_seconds=90,
               limitations=["Synthetic author-separated cases, one local model and seed.",
                            "Rules were frozen before scoring; corpus was inspected before this optional-model run.",
                            "Same v0.12 model prompt/schema/options; no model or prompt tuning to these cases.",
                            "Pending suggestions are not applied and require review."])
        try:
            model = client.require_local_model(args.model)
            record("environment", model=model, ollama_version=client._request("/api/version"),
                   installed_metadata=next(m for m in client.installed_models() if m["name"] == model))
            for case in corpus["cases"]:
                first_call = len(calls)

                def model_call(*, messages, **options):
                    if len(calls) >= args.max_calls:
                        raise RuntimeError("Configured call limit exhausted; no inference sent.")
                    call_id = len(calls) + 1
                    calls.append(call_id)
                    payload = {"model": model, "messages": messages, **options}
                    record("call_started", case_id=case["id"], call_id=call_id, request=payload)
                    started = time.perf_counter()
                    try:
                        response = client._request("/api/chat", payload)
                        record("call_response", case_id=case["id"], call_id=call_id,
                               wall_seconds=round(time.perf_counter() - started, 6), response=response)
                        if response.get("done") is not True or response.get("done_reason") != "stop":
                            raise ValueError("Model response is incomplete or truncated.")
                        return response["message"]["content"]
                    except Exception as error:
                        record("call_error", case_id=case["id"], call_id=call_id,
                               error_type=type(error).__name__, error=str(error))
                        raise

                with RelayStore() as store:
                    memory = AutomaticMemory(store, store.create_session(case["id"])["id"])
                    history = []
                    for initial in case["before"]:
                        history.append({"role": "user", "content": initial})
                        memory.process(history)
                        history.append({"role": "assistant", "content": "Acknowledged."})
                    before = {e["key"]: e["value"] for e in memory.entries()}
                    memory.interpreter = MemoryInterpreter.from_model(model_call, model_options=OPTIONS)
                    history.append({"role": "user", "content": case["text"]})
                    row = {"id": case["id"], "case": case, "before": before}
                    record("case_started", **row)
                    try:
                        report = memory.process(history)
                        after = {e["key"]: e["value"] for e in memory.entries()}
                        pending = memory.pending()
                        replay_calls = len(calls)
                        memory.process(history)
                        records = memory.entries() + pending
                        checks = {
                            "exact_provenance": all(history[e["source"]["index"]]["content"][
                                e["source"]["start"]:e["source"]["end"]] == e["source"]["quote"] for e in records),
                            "no_interpreted_active_values": all(e["source"].get("method") != "interpreter" for e in memory.entries()),
                            "no_authority": not store.facts(memory.session_id),
                            "no_replay_call": len(calls) == replay_calls,
                        }
                        result = score(case, before, after, pending)
                        row.update(after=after, pending=pending, process=report, checks=checks, **result)
                        row["passed"] = row["passed"] and all(checks.values())
                    except Exception as error:
                        row.update(passed=False, accepted_by_rules=False, exact_pending_candidate=False,
                                   error_type=type(error).__name__, error=str(error))
                    row["model_calls"] = len(calls) - first_call
                    rows.append(row)
                    record("case_result", **row)
            after_hashes = {name: hashlib.sha256((ROOT / "src/context_relay" / name).read_bytes()).hexdigest()
                            for name in source_hashes}
            summary = {"status": "complete", "model_calls": len(calls),
                       "source_unchanged": after_hashes == source_hashes,
                       "by_kind": {kind: {
                           "total": sum(r["case"]["kind"] == kind for r in rows),
                           "passed": sum(r["passed"] for r in rows if r["case"]["kind"] == kind),
                           "accepted_by_rules": sum(r["accepted_by_rules"] for r in rows if r["case"]["kind"] == kind),
                           "exact_pending_candidate": sum(r["exact_pending_candidate"] for r in rows if r["case"]["kind"] == kind),
                           "model_calls": sum(r["model_calls"] for r in rows if r["case"]["kind"] == kind),
                       } for kind in ("clear", "review", "abstain")},
                       "errors": sum("error_type" in r for r in rows)}
            record("summary", **summary)
            print(json.dumps(summary, indent=2))
        except Exception as error:
            record("fatal_error", error_type=type(error).__name__, error=str(error), model_calls=len(calls))
            raise


if __name__ == "__main__":
    main()
