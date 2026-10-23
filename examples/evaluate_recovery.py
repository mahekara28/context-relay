"""Evaluate bounded evidence recovery using an already installed local model.

Two fixed, synthetic logistics tasks are compared in three conditions. This is
a reproducible smoke evaluation, not a production benchmark. No downloads,
hosted model calls, tool execution, or expected-answer injection are performed.
Use --dry-run to inspect first requests without contacting Ollama.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import RelayConfig, __version__, connect
from context_relay.models import clone, encode
from ollama_agent import LocalOllama


BUDGET = 3000
MODES = (
    {"id": "full_history", "max_chars": 100000, "max_rounds": 0},
    {"id": "bounded_no_recovery", "max_chars": BUDGET, "max_rounds": 0},
    {"id": "bounded_recovery", "max_chars": BUDGET, "max_rounds": 2},
)
SYSTEM = (
    "Answer the final logistics question from the available original records. "
    "Follow the JSON evidence response format in the application task data. "
    "Return only the requested value, with no explanation in answer. If needed "
    "records are omitted, request evidence using the shipment or parcel ID and "
    "relevant record words. If the requested fact is not established, answer "
    "unknown. An estimated date is not a confirmed actual arrival date."
)
METRICS = (
    "prompt_eval_count", "prompt_eval_cached_count", "eval_count", "total_duration",
    "load_duration", "prompt_eval_duration", "eval_duration", "done_reason",
)


def message(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def distractors() -> list[dict]:
    history = []
    for index in range(6):
        history.extend([
            message("user", (
                f"Facilities note {index}: the stationery cupboard contains recycled paper, "
                "blue folders, spare pens, staplers, and adhesive labels. The meeting room "
                "inventory lists six chairs, a whiteboard, a projector remote, two extension "
                "cables, and spare adapters. The lunch survey asks whether employees prefer "
                "soup, sandwiches, or fruit. A separate cleaning rota covers kitchen surfaces "
                "and meeting room floors. Please acknowledge this facilities inventory. "
                "These office notes contain no shipment assignment, parcel arrival scan, "
                "or change to a logistics record."
            )),
            message("assistant", (
                f"Facilities note {index} acknowledged. The stationery inventory, meeting "
                "room equipment, food survey, and cleaning rota remain administrative "
                "records. I will not treat this office inventory as a shipment manifest "
                "or a parcel delivery confirmation."
            )),
        ])
    return history


def fixtures() -> list[dict]:
    handling_note = (
        " Handling notes: crates are sealed and their labels must remain visible. "
        "The carrier should check the seal inventory before unloading. Pallets "
        "containing fragile items remain upright. Warehouse staff keep the signed "
        "paperwork with the receiving log and record any damaged wrapping separately. "
        "The safety briefing covers marked walkways, high-visibility clothing, "
        "forklift separation, and emergency access routes."
    )
    acknowledgement = (
        "The logistics record is noted with its exact scope. Handling instructions "
        "remain attached to that record. The inventory and safety notes do not "
        "change the stated shipment information or establish another shipment's "
        "status. Keep the original record available for later questions."
    )
    return [
        {
            "id": "buried_loading_dock",
            "description": "An early manifest assigns a dock; irrelevant later exchanges compete for context.",
            "messages": [message("system", SYSTEM),
                         message("user", "Manifest record: Cobalt shipment CB-17 is assigned loading dock Dock-7." + handling_note),
                         message("assistant", acknowledgement)]
                        + distractors()
                        + [message("user", "Which loading dock does the manifest assign to Cobalt shipment CB-17? Return the exact dock value.")],
            "expected_answer": "Dock-7",
            "expected_evidence_index": 1,
            "host_state": {"workflow": "logistics"},
        },
        {
            "id": "unconfirmed_arrival",
            "description": "An estimated arrival exists, but no confirmed actual arrival exists anywhere in the history.",
            "messages": [message("system", SYSTEM),
                         message("user", (
                             "Parcel record: Cedar parcel CD-28 has estimated arrival 2026-09-18. "
                             "This is an estimate only. No delivered scan or confirmed actual arrival date is available."
                         ) + handling_note), message("assistant", acknowledgement)]
                        + distractors()
                        + [message("user", "What is the confirmed actual arrival date of Cedar parcel CD-28? Return YYYY-MM-DD only if confirmed; otherwise unknown.")],
            "expected_answer": "unknown",
            "expected_evidence_index": None,
            "host_state": {"workflow": "logistics"},
        },
    ]


def evidence_schema(history_id: str) -> dict:
    """Constrain only structure and the known snapshot ID, never the expected answer."""
    identity = {"type": "string", "enum": [history_id]}
    answer = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "history_id": identity,
            "citations": {"type": "array", "items": {
                "type": "object", "properties": {
                    "source_id": {"type": "string", "pattern": "^m[0-9]+$"},
                    "quote": {"type": "string"},
                }, "required": ["source_id", "quote"], "additionalProperties": False,
            }},
        },
        "required": ["answer", "history_id", "citations"], "additionalProperties": False,
    }
    lookup = {
        "type": "object",
        "properties": {"history_id": identity,
                       "need_evidence": {"type": "string", "minLength": 1, "maxLength": 512}},
        "required": ["history_id", "need_evidence"], "additionalProperties": False,
    }
    return {"oneOf": [answer, lookup]}


def task_data(messages: list[dict]) -> dict:
    candidates = [item for item in messages if item["role"] == "user"
                  and item["content"].startswith("APPLICATION-RECORDED TASK DATA\n")]
    if len(candidates) != 1:
        raise ValueError("Expected exactly one generated Relay task-data message.")
    content = candidates[0]["content"]
    parsed = json.loads(content[content.index("{"):])
    history_id = parsed["state"]["evidence_history_id"]
    if type(history_id) is not str or len(history_id) != 64:
        raise ValueError("Prepared task data did not contain an evidence history ID.")
    return parsed


def decode_ollama(response: dict) -> dict:
    message_value = response.get("message")
    if type(message_value) is not dict or type(message_value.get("content")) is not str:
        raise ValueError("Ollama returned no complete text response.")
    result = json.loads(message_value["content"])
    if type(result) is not dict:
        raise ValueError("Expected a JSON answer or evidence-request object.")
    return result


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def error_details(exc: Exception) -> dict:
    return {"type": type(exc).__name__, "message": str(exc)}


class _DryRunOnly(Exception):
    """Stop after preparation; do not invent a provider response."""


def summarize(records: list[dict], *, dry_run: bool) -> dict:
    summary = {}
    for mode in MODES:
        rows = [row for row in records if row["mode"] == mode["id"]]
        actual_calls = [call for row in rows for call in row["calls"] if call["status"] != "dry_run"]
        summary[mode["id"]] = {
            "cases": len(rows),
            "completed_tasks": None if dry_run else sum(row.get("task_completed") is True for row in rows),
            "cited_answers": None if dry_run else sum(row.get("grounded_status") == "cited" for row in rows),
            "abstentions": None if dry_run else sum(row.get("grounded_status") == "abstained" for row in rows),
            "unresolved_or_budget_exceeded": None if dry_run else sum(
                row.get("grounded_status") in {"unresolved", "budget_exceeded"} for row in rows),
            "errors": sum(row["status"] == "error" for row in rows),
            "actual_model_calls": len(actual_calls),
            "prompt_eval_count": sum(call.get("metrics", {}).get("prompt_eval_count") or 0 for call in actual_calls),
            "eval_count": sum(call.get("metrics", {}).get("eval_count") or 0 for call in actual_calls),
            "wall_seconds": round(sum(row.get("wall_seconds", 0) for row in rows), 6),
        }
    return summary


def run_case(client: LocalOllama | None, model: str, fixture: dict, mode: dict,
             report: dict, output: Path, *, dry_run: bool) -> None:
    row = {
        "fixture_id": fixture["id"], "mode": mode["id"], "status": "running",
        "max_chars": mode["max_chars"], "max_rounds": mode["max_rounds"],
        "expected_answer": fixture["expected_answer"], "calls": [],
    }
    report["records"].append(row)
    save_report(output, report)
    started = time.perf_counter()

    def provider(*, messages):
        data = task_data(messages)
        request = {
            "model": model, "messages": clone(messages),
            "format": evidence_schema(data["state"]["evidence_history_id"]),
            "stream": False,
            "options": {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 256},
            "keep_alive": "2m",
        }
        call = {
            "call": len(row["calls"]) + 1,
            "status": "dry_run" if dry_run else "running",
            "request": request,
            "serialized_message_chars": len(encode(messages)),
            "coverage": data["coverage"],
        }
        row["calls"].append(call)
        save_report(output, report)
        if dry_run:
            raise _DryRunOnly()
        call_started = time.perf_counter()
        try:
            response = client._request("/api/chat", request)
            call["response"] = response
            call["metrics"] = {key: response.get(key) for key in METRICS}
            if response.get("done") is not True:
                raise ValueError("Ollama did not report a completed response; raw response retained.")
            call["status"] = "responded"
            return response
        except Exception as exc:
            call["status"] = "error"
            call["error"] = error_details(exc)
            raise
        finally:
            call["wall_seconds"] = round(time.perf_counter() - call_started, 6)
            save_report(output, report)

    try:
        settings = RelayConfig(max_chars=mode["max_chars"], recent_turns=1, retrieve_turns=0)
        with connect(provider, config=settings) as agent:
            for key, value in fixture["host_state"].items():
                agent.remember(key, value, source="shared-fixture-state")
            result = agent.run_grounded(messages=fixture["messages"], decoder=decode_ollama,
                                        max_rounds=mode["max_rounds"], search_limit=2)
        row["status"] = "evaluated"
        row["grounded_status"] = result.status
        row["grounded_answer"] = result.answer
        row["grounded_report"] = result.report
        row["prepared_report"] = result.prepared.report
        row["citation_check"] = result.check.report if result.check is not None else None
        returned = result.answer.get("answer") if result.answer is not None else None
        row["exact_answer_match"] = returned == fixture["expected_answer"]
        expected_status = "abstained" if fixture["expected_answer"] == "unknown" else "cited"
        row["task_completed"] = row["exact_answer_match"] and result.status == expected_status
    except _DryRunOnly:
        row["status"] = "dry_run"
        row["task_completed"] = None
        row["exact_answer_match"] = None
    except Exception as exc:
        row["status"] = "error"
        row["task_completed"] = False
        row["error"] = error_details(exc)
    finally:
        row["wall_seconds"] = round(time.perf_counter() - started, 6)
        report["summary"] = summarize(report["records"], dry_run=dry_run)
        save_report(output, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Already installed local GGUF Ollama model.")
    parser.add_argument("--output", required=True, type=Path, help="JSON report, checkpointed before/after calls.")
    parser.add_argument("--timeout", type=float, default=60, help="HTTP timeout in seconds (default 60, maximum 600).")
    parser.add_argument("--port", type=int, default=11434, help="Loopback Ollama port.")
    parser.add_argument("--dry-run", action="store_true", help="Prepare first requests; no Ollama access, model outputs, or scores.")
    args = parser.parse_args(argv)
    selected = fixtures()
    report = {
        "schema_version": 1, "evaluation": "synthetic_local_evidence_recovery_smoke_test",
        "library_sha256": hashlib.sha256(b"".join(
            path.name.encode("utf-8") + path.read_bytes()
            for path in sorted((Path(__file__).resolve().parents[1] / "src" / "context_relay").glob("*.py"))
        )).hexdigest(),
        "started_at": datetime.now(timezone.utc).isoformat(), "status": "preparing",
        "requested_model": args.model, "library_version": __version__,
        "python": platform.python_version(), "platform": platform.platform(),
        "fixtures": selected, "fixture_sha256": hashlib.sha256(encode(selected).encode("utf-8")).hexdigest(),
        "modes": clone(list(MODES)), "budget_unit": "serialized message characters, not model tokens",
        "planned_cases": 6, "maximum_planned_model_calls": 10,
        "order": "mode order rotates by fixture index; no warmup and no repeated samples",
        "schema_assistance": "history_id is constrained to the actual prepared snapshot ID in every condition; expected answers and source IDs are not supplied by the schema",
        "completion_rule": "exact human-authored expected value plus status cited; expected unknown requires status abstained",
        "limitations": [
            "Only two hand-authored synthetic fixtures; no representative workload or statistical significance.",
            "All modes use the same grounding contract, source labels, original history, and answer-free host facts.",
            "The full-history reference has a larger character budget, so it is not a matched-cost control.",
            "The two bounded modes use identical limits and selectors; only the allowed recovery rounds differ.",
            "Bounded recovery may make three model calls where a no-recovery case makes one; compare total tokens and time.",
            "A literal citation check cannot establish that the cited quote logically supports the answer; exact expected-value checks are separate fixture scoring.",
            "The schema supplies the correct snapshot ID to avoid measuring 64-character ID copying; identity errors remain checked by the library.",
            "Character limits do not guarantee a token fit. Ollama prompt counts are observed after calls, not estimated beforehand.",
            "Model loading, prompt caching, hardware, and case order affect timings; one run cannot establish latency improvements.",
            "The fixtures test record retrieval and abstention, not real transactions, automatic correction extraction, or universal context-rot prevention.",
        ],
        "records": [], "summary": {},
    }
    try:
        client = LocalOllama(port=args.port, timeout=args.timeout)
        model = args.model
        if not args.dry_run:
            report["status"] = "validating_local_model"
            save_report(args.output, report)
            model = client.require_local_model(args.model)
            report["model"] = model
            report["ollama_version"] = client._request("/api/version")
            report["model_details"] = client._request("/api/show", {"model": model})
            report["model_tags"] = [entry for entry in client.installed_models() if entry.get("name") == model]
        report["status"] = "dry_run" if args.dry_run else "running"
        for fixture_number, fixture in enumerate(selected):
            modes = list(MODES[fixture_number:]) + list(MODES[:fixture_number])
            for mode in modes:
                print(f"{fixture['id']} / {mode['id']} ...", flush=True)
                run_case(None if args.dry_run else client, model, fixture, mode,
                         report, args.output, dry_run=args.dry_run)
        errors = any(row["status"] == "error" for row in report["records"])
        report["status"] = "dry_run" if args.dry_run else ("complete_with_errors" if errors else "complete")
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_report(args.output, report)
        print(json.dumps(report["summary"], indent=2))
        print(f"Full inputs, raw outputs, checks, and measurements: {args.output}")
        return 1 if errors else 0
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        save_report(args.output, report)
        print(f"Interrupted; completed responses and the pending request remain in {args.output}.", file=sys.stderr)
        return 130
    except Exception as exc:
        report["status"] = "setup_error"
        report["error"] = error_details(exc)
        save_report(args.output, report)
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
