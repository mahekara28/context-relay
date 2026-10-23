"""Compare context selection using a model already installed in local Ollama.

This is a small synthetic smoke evaluation, not a benchmark of general model
quality. It downloads nothing and never executes model-proposed actions.
Use --dry-run to inspect every request without contacting Ollama.
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

from context_relay import ContextGuard, __version__, compile_context
from context_relay.models import encode
from ollama_agent import LocalOllama


MODES = ("full_plus_state", "recent_plus_state", "relay")
BUDGET = 3200
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "evidence"],
    "additionalProperties": False,
}
SYSTEM = (
    "Answer the final user question from the provided records and application-recorded "
    "current facts only. Current application facts supersede older facts about the same "
    "entity. Keep different entities and environments separate. Return only a JSON "
    "object with answer (a string) and evidence (an array of the exact evidence labels "
    "supporting that answer). Copy only the requested value into answer. If the "
    "requested fact is not known from the supplied material, return answer=\"unknown\" "
    "and evidence=[]. Do not guess from a similar record or an estimate."
)


def message(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def distractors(count: int = 8) -> list[dict]:
    """Deliberately irrelevant but readable exchanges; not adversarial instructions."""
    result = []
    for number in range(count):
        result.extend([
            message("user", (
                f"[evidence:admin-{number}] Unrelated facilities update {number}: "
                "The office stationery cupboard contains blue folders, recycled paper, "
                "and spare pens. The meeting room inventory lists four chairs, a whiteboard, "
                "and two extension cables. The lunch menu survey asks employees whether "
                "they prefer soup or sandwiches. These administrative notes describe the "
                "office inventory only. Please acknowledge the facilities update."
            )),
            message("assistant", (
                f"Acknowledged administrative update {number}. The supplies and meeting "
                "room inventory have been noted. The lunch survey remains a separate "
                "administrative matter and establishes no customer or release decision."
            )),
        ])
    return result


def fixtures() -> list[dict]:
    """Fixed examples with human-authored expected values, never model-generated labels."""
    noise = distractors()
    return [
        {
            "id": "changed_resolution",
            "description": "An explicit host-recorded correction is available equally to every mode.",
            "messages": [message("system", SYSTEM),
                         message("user", "[evidence:order-original] Customer initially requested a refund for order ORD-1042."),
                         message("assistant", "The initial request was a refund.")]
                        + noise[:6]
                        + [message("user", "[evidence:resolution-change] Correction for ORD-1042: the customer now requests a replacement, not a refund."),
                           message("assistant", "The current resolution is replacement.")]
                        + noise[6:]
                        + [message("user", "What is the current resolution for order ORD-1042? Return only refund, replacement, or unknown in answer.")],
            "state": {"order_id": "ORD-1042", "resolution": "replacement", "resolution_evidence": "resolution-change"},
            "pins": [],
            "query": "ORD-1042 current resolution replacement correction",
            "expected": {"answer": "replacement", "evidence": ["resolution-change"]},
            "disclosure": "The host explicitly supplies the corrected resolution and its evidence label to ALL modes; this case does not test automatic correction extraction.",
        },
        {
            "id": "buried_record",
            "description": "A requested manifest value is early in an otherwise irrelevant history.",
            "messages": [message("system", SYSTEM),
                         message("user", "[evidence:cobalt-manifest] The Cobalt shipment CB-17 manifest assigns loading dock Dock-7. Packing crates are sealed."),
                         message("assistant", "The Cobalt manifest has been recorded.")]
                        + noise
                        + [message("user", "Which loading dock does the Cobalt shipment CB-17 manifest assign? Return the exact dock value or unknown.")],
            "state": {"workflow": "logistics", "requested_shipment": "CB-17"},
            "pins": [],
            "query": "Cobalt shipment CB-17 manifest loading dock",
            "expected": {"answer": "Dock-7", "evidence": ["cobalt-manifest"]},
            "disclosure": "The dock answer is only in the original transcript, not in host-recorded state. Relay's lexical query repeats words from the pending request.",
        },
        {
            "id": "environment_scope",
            "description": "The recent production value must not replace the older staging value.",
            "messages": [message("system", SYSTEM),
                         message("user", "[evidence:atlas-staging] Atlas staging deployment has approved region eu-west-2. This approval applies only to staging."),
                         message("assistant", "The staging approval is recorded separately.")]
                        + noise
                        + [message("user", "[evidence:atlas-production] Atlas production deployment has approved region us-east-1. This approval applies only to production."),
                           message("assistant", "The production approval is recorded separately."),
                           message("user", "Which region is approved for Atlas staging deployment? Return the exact region identifier or unknown.")],
            "state": {"service": "Atlas", "requested_environment": "staging"},
            "pins": [],
            "query": "Atlas staging deployment approved region",
            "expected": {"answer": "eu-west-2", "evidence": ["atlas-staging"]},
            "disclosure": "Both regions remain in the full history; the state identifies the target environment but contains neither answer.",
        },
        {
            "id": "pinned_constraint",
            "description": "An explicit host pin preserves an early output constraint in every mode.",
            "messages": [message("system", SYSTEM),
                         message("user", "[evidence:notice-format] Aurora release customer notices must use plain_text format. HTML is prohibited for this release."),
                         message("assistant", "The customer notice format requirement is recorded.")]
                        + noise
                        + [message("user", "Which format must the Aurora release customer notice use? Return the exact format name or unknown.")],
            "state": {"release": "Aurora"},
            "pins": [1],
            "query": "Aurora release customer notice format",
            "expected": {"answer": "plain_text", "evidence": ["notice-format"]},
            "disclosure": "The host supplies pin index 1 to BOTH budgeted selectors. This is a control for pin retention, not evidence that Relay alone identifies important instructions.",
        },
        {
            "id": "insufficient_evidence",
            "description": "An estimate and routing update do not establish a confirmed arrival date.",
            "messages": [message("system", SYSTEM),
                         message("user", "[evidence:cedar-estimate] Cedar parcel CD-28 has estimated arrival 2026-09-18. This is an estimate, not confirmed delivery."),
                         message("assistant", "Only an estimate is available for Cedar.")]
                        + noise
                        + [message("user", "[evidence:cedar-routing] Cedar parcel CD-28 has been assigned hub North-3. No delivered scan or actual arrival date is available."),
                           message("assistant", "The routing update contains no confirmed arrival."),
                           message("user", "What is the confirmed actual arrival date of Cedar parcel CD-28? Return YYYY-MM-DD only if confirmed; otherwise unknown.")],
            "state": {"parcel": "CD-28", "workflow": "delivery_tracking"},
            "pins": [],
            "query": "Cedar CD-28 confirmed actual arrival date",
            "expected": {"answer": "unknown", "evidence": []},
            "disclosure": "No mode receives a confirmed date. A correct abstention completes this task; an estimated date fails it.",
        },
    ]


def build_requests(selected: list[dict], *, budget: int = BUDGET) -> list[dict]:
    """Construct matched inputs. No model/network calls; expected answers stay out of prompts."""
    prepared = []
    for fixture in selected:
        with ContextGuard(name="Local evaluation: " + fixture["id"]) as guard:
            for key, value in fixture["state"].items():
                guard.remember(key, value, source="evaluation-host")
            relay_turn = guard.prepare(
                fixture["messages"], max_chars=budget, recent_turns=1,
                pinned=fixture["pins"], retrieve_turns=2, query=fixture["query"],
            )
            snapshot = relay_turn.context
            state = {key: snapshot[key] for key in ("session_id", "revision", "facts", "sources")}
            plans = {
                # A deliberately generous reference; unlike the other modes it
                # is not constrained to the experimental character budget.
                "full_plus_state": compile_context(fixture["messages"], state=state, max_chars=1_000_000,
                                                    recent_turns=1, pinned=fixture["pins"]),
                "recent_plus_state": compile_context(fixture["messages"], state=state, max_chars=budget,
                                                      recent_turns=1, pinned=fixture["pins"]),
                "relay": relay_turn,
            }
            for mode in MODES:
                plan = plans[mode]
                messages = plan.messages
                prepared.append({
                    "fixture": fixture["id"], "mode": mode,
                    "description": fixture["description"], "disclosure": fixture["disclosure"],
                    "expected": fixture["expected"], "messages": messages,
                    "prompt_sha256": hashlib.sha256(encode(messages).encode("utf-8")).hexdigest(),
                    "selection_report": plan.report,
                    "exceeds_comparison_budget": len(encode(messages)) > budget,
                    "application_supplied_pins": fixture["pins"],
                    "application_supplied_state": state,
                    "retrieval_query": fixture["query"] if mode == "relay" else None,
                })
    return prepared


def score_output(raw: str, expected: dict) -> dict:
    """Exact answer AND exact evidence-set checks; a blocked action is not a success."""
    try:
        proposal = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return {"schema_valid": False, "answer_correct": False, "evidence_correct": False,
                "task_complete": False, "parse_error": str(exc), "proposal": None}
    valid = (type(proposal) is dict and set(proposal) == {"answer", "evidence"}
             and type(proposal["answer"]) is str and type(proposal["evidence"]) is list
             and all(type(item) is str for item in proposal["evidence"])
             and len(set(proposal["evidence"])) == len(proposal["evidence"]))
    answer_correct = bool(valid and proposal["answer"] == expected["answer"])
    evidence_correct = bool(valid and set(proposal["evidence"]) == set(expected["evidence"]))
    return {"schema_valid": valid, "answer_correct": answer_correct,
            "evidence_correct": evidence_correct,
            "task_complete": answer_correct and evidence_correct, "proposal": proposal}


def execution_order(prepared: list[dict], repeats: int) -> list[tuple[int, dict]]:
    """Rotate mode order by fixture and repeat; retain the actual order in the report."""
    result = []
    fixture_ids = list(dict.fromkeys(item["fixture"] for item in prepared))
    by_key = {(item["fixture"], item["mode"]): item for item in prepared}
    for repeat in range(repeats):
        for number, fixture_id in enumerate(fixture_ids):
            offset = (number + repeat) % len(MODES)
            modes = MODES[offset:] + MODES[:offset]
            result.extend((repeat, by_key[(fixture_id, mode)]) for mode in modes)
    return result


def summarize(records: list[dict]) -> dict:
    summary = {}
    for mode in MODES:
        rows = [row for row in records if row["mode"] == mode]
        finished = [row for row in rows if row.get("status") == "completed"]
        counts = [row.get("metrics", {}).get("prompt_eval_count") for row in finished]
        counts = [value for value in counts if type(value) is int]
        times = [row["wall_seconds"] for row in finished]
        summary[mode] = {
            "attempted": len(rows), "completed_responses": len(finished),
            "task_complete": sum(row.get("score", {}).get("task_complete", False) for row in finished),
            "answer_correct": sum(row.get("score", {}).get("answer_correct", False) for row in finished),
            "evidence_correct": sum(row.get("score", {}).get("evidence_correct", False) for row in finished),
            "run_errors": sum(row.get("status") == "error" for row in rows),
            "mean_prompt_eval_count": sum(counts) / len(counts) if counts else None,
            "mean_wall_seconds": sum(times) / len(times) if times else None,
        }
    return summary


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_evaluation(client, model_name: str, prepared: list[dict], report: dict, output: Path,
                   *, repeats: int = 1) -> bool:
    """Accept a tiny fake client with _request() for network-free tests. Return no-run-errors."""
    for position, (repeat, item) in enumerate(execution_order(prepared, repeats)):
        request = {
            "model": model_name, "messages": item["messages"], "format": SCHEMA,
            "stream": False, "keep_alive": "2m",
            "options": {"temperature": 0, "seed": 2718 + repeat, "num_ctx": 8192, "num_predict": 256},
        }
        row = {"position": position, "repeat": repeat + 1, "fixture": item["fixture"],
               "mode": item["mode"], "prompt_sha256": item["prompt_sha256"],
               "options": request["options"], "status": "running"}
        report["records"].append(row)
        save_report(output, report)
        started = time.perf_counter()
        print(f"[{position + 1}/{len(prepared) * repeats}] {item['fixture']} / {item['mode']}", flush=True)
        try:
            response = client._request("/api/chat", request)
            row["response"] = response
            reply = response.get("message")
            if type(reply) is not dict or type(reply.get("content")) is not str:
                raise ValueError("Ollama returned no text response; raw response is retained.")
            row["raw_proposal"] = reply["content"]
            row["metrics"] = {key: response.get(key) for key in (
                "prompt_eval_count", "prompt_eval_cached_count", "eval_count", "total_duration",
                "load_duration", "prompt_eval_duration", "eval_duration", "done_reason",
            )}
            if response.get("done") is not True:
                raise ValueError("Ollama did not confirm a completed response.")
            row["score"] = score_output(reply["content"], item["expected"])
            row["status"] = "completed"
        except (ValueError, RuntimeError, OSError) as exc:
            row["status"] = "error"
            row["error"] = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            row["wall_seconds"] = round(time.perf_counter() - started, 6)
            report["summary"] = summarize(report["records"])
            save_report(output, report)
    return not any(row["status"] == "error" for row in report["records"])


def main(argv: list[str] | None = None) -> int:
    available = fixtures()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="An already installed local GGUF Ollama model; never downloaded.")
    parser.add_argument("--output", required=True, type=Path, help="JSON report path; written after each call.")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60, help="Per HTTP request seconds (default 60, maximum 600).")
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--fixtures", nargs="+", choices=["all"] + [item["id"] for item in available], default=["all"])
    parser.add_argument("--dry-run", action="store_true", help="Save exact inputs without contacting Ollama or checking its model list.")
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 100:
        parser.error("--repeats must be from 1 through 100.")
    selected = [item for item in available if "all" in args.fixtures or item["id"] in args.fixtures]
    report = {
        "schema_version": 1, "evaluation": "synthetic_local_context_selection_smoke_test",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "preparing", "requested_model": args.model,
        "library_version": __version__, "python": platform.python_version(), "platform": platform.platform(),
        "comparison_budget_chars": BUDGET, "budget_unit": "serialized JSON characters, not model tokens",
        "repeats": args.repeats, "warmup": "none; initial load and cache effects remain in timings",
        "order": "mode order rotates by fixture index and repeat; each call uses the same seed within a repeat",
        "limitations": [
            "Five hand-authored synthetic fixtures are not a representative or statistically significant benchmark.",
            "All modes receive identical current application state and explicit pins; the host supplies these.",
            "The recent control uses the public compiler with lexical retrieval disabled; this isolates selection policy, not unrelated framework performance.",
            "The full reference exceeds the comparison character budget; it is not a matched-cost control.",
            "Lexical queries repeat target words from pending requests; no automatic semantic state extraction is tested.",
            "This evaluates exact answers and evidence labels, not live actions, end-to-end real workflows, or prevention of every form of context rot.",
            "Hardware, loading, prompt caching, and installed model template affect observed latency; no speed claim follows from a single run.",
        ],
        "prepared_inputs": [], "records": [], "summary": {},
    }
    try:
        client = LocalOllama(port=args.port, timeout=args.timeout)
        prepared = build_requests(selected)
        report["prepared_inputs"] = prepared
        report["planned_call_count"] = len(prepared) * args.repeats
        if args.dry_run:
            report["status"] = "dry_run"
            save_report(args.output, report)
            print(f"Saved {len(prepared)} prepared inputs without network calls: {args.output}")
            return 0
        report["status"] = "validating_local_model"
        save_report(args.output, report)
        model_name = client.require_local_model(args.model)
        report["model"] = model_name
        report["ollama_version"] = client._request("/api/version")
        report["model_details"] = client._request("/api/show", {"model": model_name})
        report["model_tags"] = [item for item in client.installed_models() if item.get("name") == model_name]
        report["status"] = "running"
        success = run_evaluation(client, model_name, prepared, report, args.output, repeats=args.repeats)
        report["status"] = "complete" if success else "complete_with_errors"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_report(args.output, report)
        print(json.dumps(report["summary"], indent=2))
        print(f"Complete inputs, raw responses, and measurements: {args.output}")
        return 0 if success else 1
    except (ValueError, RuntimeError, OSError) as exc:
        report["status"] = "setup_error"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        save_report(args.output, report)
        print(f"Evaluation stopped: {exc}. No model downloads or external tool actions were performed.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        save_report(args.output, report)
        print(f"Interrupted; completed responses and pending record retained in {args.output}.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
