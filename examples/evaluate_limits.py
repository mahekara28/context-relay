"""Optional real local v0.9 smoke: interpretation, output checks and archival.

Uses installed Ollama weights only. Makes at most six model calls; never pulls
models, uses a hosted API or replaces inference with a stand-in. No performance
or production accuracy benchmark is claimed. All failed observations are saved.
"""

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_relay import (MemoryInterpreter, MemoryPolicy, OutputPolicy, RelayConfig,
                           __version__, connect)
from context_relay.models import encode
from ollama_agent import LocalOllama


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5:1.5b")
    parser.add_argument("--native-json", action="store_true", help="Also constrain answer generation with Ollama's JSON schema option.")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report path; earlier observations are preserved.")
    source = Path(__file__).resolve().parents[1] / "src" / "context_relay"
    report = {"library_version": __version__, "status": "running",
              "library_sha256": hashlib.sha256(b"".join(p.name.encode() + p.read_bytes() for p in sorted(source.glob("*.py")))).hexdigest(),
              "started_at": datetime.now(timezone.utc).isoformat(), "native_json": args.native_json, "calls": [], "cases": [],
              "limits": ["Three synthetic cases, one local model, one sample; not general accuracy or speed evidence.",
                         "Output checks validate structure, not whether the owner value is correct.",
                         "Interpreted memory remains pending; this script does not pretend a human approved it.",
                         "Archival is explicit and preserves originals; total disk use can grow and active-memory caps still apply."]}

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    save()
    try:
        client = LocalOllama(timeout=60)
        report.update(model=client.require_local_model(args.model), ollama_version=client._request("/api/version"))

        def provider(label):
            def call(*, messages, **options):
                payload = {"model": report["model"], "messages": messages, "stream": False,
                           "options": {"temperature": 0, "seed": 17, "num_predict": 384}, "keep_alive": "2m", **options}
                if args.native_json and label in {"original_reply", "archived_followup"}:
                    payload["format"] = {"type": "object", "properties": {"owner": {"type": "string"}},
                                         "required": ["owner"], "additionalProperties": False}
                record = {"phase": label, "request": payload}
                report["calls"].append(record)
                save()
                start = time.perf_counter()
                try:
                    record["response"] = response = client._request("/api/chat", payload)
                    if response.get("done") is not True or response.get("done_reason") != "stop":
                        raise ValueError("Expected complete nontruncated text output.")
                    return response
                except Exception as error:
                    record["error"] = {"type": type(error).__name__, "message": str(error)}
                    raise
                finally:
                    record["wall_seconds"] = round(time.perf_counter() - start, 6)
                    save()
            return call

        fixtures = (
            ("unclear_wording_review", "Keep responses detailed.", "Make it brief.", "detailed", {"response_style": "brief"}),
            # Additional wording chosen before running the revised helper prompt.
            ("new_export_wording", "Export as CSV.", "I want the output in JSON.", "CSV", {"export_format": "JSON"}),
        )
        for name, initial, request, old_value, expected in fixtures:
            case = {"name": name, "expected_suggestion": expected}
            report["cases"].append(case)
            try:
                schema = {"type": "array", "maxItems": 4, "items": {"type": "object", "properties": {
                    key: {"type": "string"} for key in ("key", "value", "quote")},
                    "required": ["key", "value", "quote"], "additionalProperties": False}}
                interpreter = MemoryInterpreter.from_model(provider(name),
                    decoder=lambda response: response["message"]["content"], model_options={"format": schema})
                # Only AutomaticMemory.process runs here. No answer-provider call
                # is requested or simulated; inference is inside the interpreter.
                with connect(provider("unused_answer_provider"), memory=MemoryPolicy(), memory_interpreter=interpreter) as agent:
                    history = [{"role": "user", "content": initial}]
                    agent.memory.process(history)
                    history += [{"role": "assistant", "content": "Acknowledged."}, {"role": "user", "content": request}]
                    case["original_history"] = history
                    agent.memory.process(history)
                    case["pending"] = pending = agent.memory.pending()
                    case["active_memory"] = agent.memory.snapshot()
                    calls_before_replay = len(report["calls"])
                    agent.memory.process(history)
                    case["checks"] = {
                        "expected_suggestion": {p["key"]: p["value"] for p in pending} == expected,
                        "requires_review": bool(pending) and all(p["reason"] == "interpreted_needs_review" for p in pending),
                        "active_value_unchanged": agent.memory.entries()[0]["value"] == old_value,
                        "exact_quote": bool(pending) and all(history[p["source"]["index"]]["content"][p["source"]["start"]:p["source"]["end"]] == p["source"]["quote"] for p in pending),
                        "replay_makes_no_extra_interpretation_call": len(report["calls"]) == calls_before_replay,
                    }
            except Exception as error:
                case["error"] = {"type": type(error).__name__, "message": str(error)}
            save()

        case = {"name": "checked_reply_after_archival", "expected_owner": "Mira"}
        report["cases"].append(case)
        try:
            with tempfile.TemporaryDirectory() as directory:
                config = RelayConfig(db_path=str(Path(directory) / "chat.db"),
                                     output=OutputPolicy(format="json", required_keys=("owner",), max_retries=1))
                with connect(provider("original_reply"), config=config, memory=MemoryPolicy()) as agent:
                    chat = agent.conversation(system="Return a JSON object with exactly one string field owner containing the current project owner's name, or unknown if unavailable.")
                    first = chat.send("The project owner is Mira.", request_id="first")
                    old_id, old_history = agent.session_id, chat.history
                    case["old_history"] = old_history
                    successor = chat.rollover(expected_revision=agent.guard.store.get_session(old_id)["revision"])
                with connect(provider("archived_followup"), config=config, memory=MemoryPolicy(), session_id=successor) as agent:
                    chat = agent.conversation()
                    case["active_history_before_followup"] = chat.history
                    case["carried_memory"] = agent.memory.context_snapshot()
                    response = chat.send("Who is the current project owner?", request_id="followup")
                    matches = chat.search_history("The project owner is Mira.")["messages"]
                    case["new_history"] = chat.history
                    case["answer"] = json.loads(response["message"]["content"])
                    case["checks"] = {
                        "active_prefix_only_after_rollover": len(case["active_history_before_followup"]) == 1,
                        "archived_original_is_retrievable": any(m["session_id"] == old_id and m["message"] == old_history[1] for m in matches),
                        "carried_source_identifies_old_session": case["carried_memory"]["entries"][0]["source"]["session_id"] == old_id,
                        "exact_owner_answer": case["answer"] == {"owner": case["expected_owner"]},
                        "no_rejected_reply_saved": len(chat.history) == 3,
                    }
                    case["active_chars_before_followup"] = len(encode(case["active_history_before_followup"]))
                    case["archived_segment_chars"] = len(encode(old_history))
        except Exception as error:
            case["error"] = {"type": type(error).__name__, "message": str(error)}
        report["status"] = "complete" if all("error" not in case and all(case["checks"].values()) for case in report["cases"]) else "complete_with_failures"
    except Exception as error:
        report.update(status="setup_error", error={"type": type(error).__name__, "message": str(error)})
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({"status": report["status"], "model_calls": len(report["calls"]),
                      "cases": [{k: v for k, v in case.items() if k in ("name", "checks", "error")} for case in report["cases"]]}, indent=2))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
