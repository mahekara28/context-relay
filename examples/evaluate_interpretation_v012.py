"""Six local extraction calls plus four excluded controls; no answer calls/retries.

Uses preinstalled Ollama GGUF weights, a generic JSON schema, and the unmodified
MemoryInterpreter prompt. Expected outcomes are only supplied to scoring. Raw
synthetic requests/responses and failures are retained in a new JSONL journal.
"""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from context_relay import AutomaticMemory, MemoryInterpreter, RelayStore, __version__
from ollama_agent import LocalOllama


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {"type": "array", "maxItems": 4, "items": {
    "type": "object", "properties": {key: {"type": "string"} for key in ("key", "value", "quote")},
    "required": ["key", "value", "quote"], "additionalProperties": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5:3b")
    args = parser.parse_args()
    corpus_bytes = (ROOT / "evaluations/interpretation-v0.12-corpus.json").read_bytes()
    corpus = json.loads(corpus_bytes)
    options = {"stream": False, "format": SCHEMA,
               "options": {"temperature": 0, "seed": 17, "num_predict": 512, "num_ctx": 4096},
               "keep_alive": "2m"}
    source = ROOT / "src/context_relay"
    client = LocalOllama(timeout=60)
    calls, rows = [], []
    with args.output.open("x", encoding="utf-8") as journal:
        def record(kind, **values):
            journal.write(json.dumps({"record": kind, **values}, ensure_ascii=False) + "\n")
            journal.flush()

        record("manifest", started_at=datetime.now(timezone.utc).isoformat(), version=__version__,
               corpus_sha256=hashlib.sha256(corpus_bytes).hexdigest(), description=corpus["description"],
               source_sha256={name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                              for name in ("memory.py", "interpretation.py")},
               model=args.model, options=options, max_calls=6,
               limitations=["Ten synthetic development cases, six inference calls, one model and seed.",
                            "Four exclusions make zero model calls; their passes are filter checks.",
                            "Exact candidate scoring is not general semantic accuracy.",
                            "No proposal is confirmed; manual review is still required."])
        try:
            model = client.require_local_model(args.model)
            record("environment", model=model, ollama_version=client._request("/api/version"))
            for case in corpus["cases"]:
                first_call = len(calls)

                def model_call(*, messages, **model_options):
                    if len(calls) >= 6:
                        raise RuntimeError("The six-call budget is exhausted; no inference sent.")
                    call_id = len(calls) + 1
                    calls.append(call_id)
                    payload = {"model": model, "messages": messages, **model_options}
                    record("call_started", case_id=case["id"], call_id=call_id, request=payload)
                    started = time.perf_counter()
                    try:
                        response = client._request("/api/chat", payload)
                        record("call_response", case_id=case["id"], call_id=call_id, response=response,
                               wall_seconds=round(time.perf_counter() - started, 6))
                        if response.get("done") is not True or response.get("done_reason") != "stop":
                            raise ValueError("Local model did not return complete nontruncated text.")
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
                    before = memory.entries()
                    memory.interpreter = MemoryInterpreter.from_model(model_call, model_options=options)
                    history.append({"role": "user", "content": case["text"]})
                    row = {"id": case["id"], "case": case}
                    try:
                        row["process"] = memory.process(history)
                        pending = memory.pending()
                        replay_calls = len(calls)
                        memory.process(history)
                        row.update(pending=pending, active_memory=memory.snapshot(), checks={
                            "expected_candidates": {p["key"]: p["value"] for p in pending} == case["expected"],
                            "expected_calls": len(calls) - first_call == case["expected_calls"],
                            "active_values_unchanged": memory.entries() == before,
                            "requires_review": all(p["reason"] == "interpreted_needs_review" for p in pending),
                            "exact_provenance": all(history[p["source"]["index"]]["content"][
                                p["source"]["start"]:p["source"]["end"]] == p["source"]["quote"] for p in pending),
                            "no_authority": store.facts(memory.session_id) == {},
                            "no_replay_call": len(calls) == replay_calls})
                        row["passed"] = all(row["checks"].values())
                    except Exception as error:
                        row.update(passed=False, error_type=type(error).__name__, error=str(error))
                    rows.append(row)
                    record("case_result", **row)
            summary = {"status": "complete", "model_calls": len(calls),
                       "clear_candidates": {"passed": sum(r["passed"] for r in rows if r["case"]["expected"]), "total": 4},
                       "ambiguous_abstentions": {"passed": sum(r["passed"] for r in rows if r["case"]["expected_calls"] and not r["case"]["expected"]), "total": 2},
                       "excluded_without_inference": {"passed": sum(r["passed"] for r in rows if not r["case"]["expected_calls"]), "total": 4}}
            record("summary", **summary)
            print(json.dumps(summary, indent=2))
        except Exception as error:
            record("fatal_error", error_type=type(error).__name__, error=str(error), model_calls=len(calls))
            raise


if __name__ == "__main__":
    main()
