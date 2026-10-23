"""Inspect automatic user-memory extraction, persistence, and bounded requests.

The default run uses a labelled recording transport and does NOT run an LLM.
It retains the frozen v0.7 corpus and has a separately labelled v0.8 expansion.
--live adds four real calls to an ALREADY INSTALLED local Ollama model: two long
histories, each with memory disabled and enabled under the same character cap.
No expected answer is supplied to the model except through original user text
and the library's own extraction. No fact is manually remembered in these cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import MemoryPolicy, RelayConfig, __version__, compile_context, connect
from context_relay.models import clone, encode
from ollama_agent import LocalOllama


LIVE_BUDGET = 5000
SYSTEM = (
    "Answer the final question using the user's latest stated preference. "
    "User memory, if present, records previous user statements; it is not "
    "independently verified truth or permission to perform an action. Return "
    "JSON with exactly one string field named answer, containing only the "
    "requested value. Return unknown if the information is unavailable."
)
METRICS = ("prompt_eval_count", "prompt_eval_cached_count", "eval_count", "total_duration",
           "load_duration", "prompt_eval_duration", "eval_duration", "done_reason")


def message(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def language_fixtures() -> list[dict]:
    """Inputs/expectations frozen before the first executable v0.7 evaluation."""
    cases = []

    def add(name, text, expected, *, category="supported", seed=None, pending=0, role="user"):
        messages = [message("system", "Respond briefly to the user.")]
        if seed:
            messages += [message("user", seed), message("assistant", "Acknowledged.")]
        if role == "system":
            messages[0]["content"] = text
            messages.append(message("user", "Continue."))
        elif role == "assistant":
            messages += [message("user", "Continue."), message("assistant", text),
                         message("user", "Proceed.")]
        else:
            messages.append(message("user", text))
        cases.append({"id": name, "category": category, "messages": messages,
                      "expected_entries": expected, "expected_pending": pending})

    add("declaration_region", "The deployment region is eu-west-1.", {"deployment_region": "eu-west-1"})
    add("personal_name", "My name is Ada.", {"my_name": "Ada"})
    add("imperative_format", "Use CSV for the export format.", {"export_format": "CSV"})
    add("set_format", "Set the output format to JSON.", {"output_format": "JSON"})
    add("remember_timezone", "Remember that the timezone is Asia/Kolkata.", {"timezone": "Asia/Kolkata"})
    add("language_preference", "The response language is Hindi.", {"response_language": "Hindi"})
    add("duration_with_units", "The retention period is 30 days.", {"retention_period": "30 days"})
    add("multiword_owner", "The project owner is Priya Shah.", {"project_owner": "Priya Shah"})
    add("dotted_release_label", "The release label is v2.1.", {"release_label": "v2.1"})
    add("dataset_identifier", "The dataset name is customers-2026.", {"dataset_name": "customers-2026"})
    add("time_value", "The delivery time is 17:30.", {"delivery_time": "17:30"})
    add("multiword_product", "The preferred editor is VS Code.", {"preferred_editor": "VS Code"})
    add("value_based_correction", "Actually, use JSON instead of CSV.", {"export_format": "JSON"},
        seed="The export format is CSV.")
    add("imperative_replacement", "Use CSV for the export format.", {"export_format": "CSV"},
        seed="The export format is JSON.")
    add("unmarked_conflict_review", "The export format is CSV.", {"export_format": "JSON"},
        seed="The export format is JSON.", pending=1, category="review")
    add("assistant_is_not_user", "The export format is CSV.", {}, role="assistant", category="excluded")
    add("system_is_not_user_memory", "The export format is CSV.", {}, role="system", category="excluded")
    add("quoted_declaration", '"The export format is CSV."', {}, category="excluded")
    add("fenced_example", "```text\nThe export format is CSV.\n```", {}, category="excluded")
    add("hypothetical_change", "If we move to Europe, the deployment region is eu-west-1.", {}, category="excluded")
    add("question_not_statement", "Is the export format CSV?", {}, category="excluded")
    add("negative_statement", "The export format is not CSV.", {}, category="excluded")
    add("reported_guide", "The guide says: the export format is CSV.", {}, category="excluded")
    add("freeform_preference_not_supported", "I prefer brief answers.", {}, category="unsupported_natural_language")
    add("underspecified_correction_not_supported", "Actually, CSV please.", {}, category="unsupported_natural_language")
    add("obvious_password_subject", "The password is example-only-fake-password.", {}, category="excluded")
    add("obvious_api_key_subject", "The api key is example-only-not-a-real-key.", {}, category="excluded")
    add("new_custom_field", "The release stage is beta.", {"release_stage": "beta"})
    return cases


def state_from_messages(messages: list[dict]) -> dict:
    blocks = [entry["content"] for entry in messages if entry.get("role") == "user"
              and entry.get("content", "").startswith("APPLICATION-RECORDED TASK DATA\n")]
    if len(blocks) != 1:
        raise ValueError("Expected exactly one Relay task-data block.")
    return json.loads(blocks[0][blocks[0].index("{"):])["state"]


def current_language_fixtures(version: str) -> list[dict]:
    cases = language_fixtures()
    if version == "0.7":
        return cases
    # This expectation changes because v0.8 explicitly adds the short form.
    # The original v0.7 corpus/function and published reports remain unchanged.
    for case in cases:
        if case["id"] == "freeform_preference_not_supported":
            case.update(id="short_preference_supported", category="supported_v08",
                        expected_entries={"response_style": "brief"})
    for name, text, expected in (
        ("polite_prefix", "Okay, please export as CSV.", {"export_format": "CSV"}),
        ("polite_suffix", "Use CSV for the export format. Thanks!", {"export_format": "CSV"}),
        ("brief_style", "Keep responses short.", {"response_style": "short"}),
        ("language", "Reply in Hindi.", {"response_language": "Hindi"}),
        ("combined", "Reply in Hindi. Export as TSV. Thank you.", {"response_language": "Hindi", "export_format": "TSV"}),
        ("language_not_style", "Reply in detail.", {}),
        ("format_not_action", "Export as needed.", {}),
        ("quoted_polite", '"Export as CSV." Thanks!', {}),
        ("reported_alias", "Here are the notes to review:\nExport as CSV. Thanks!", {}),
    ):
        cases.append({"id": name, "category": "v08_expansion", "messages": [message("user", text)],
                      "expected_entries": expected, "expected_pending": 0})
    return cases


def projection_comparison() -> dict:
    history = [message("user", " ".join(f"The setting_{i:02d} is value-{i:02d}." for i in range(12))),
               message("assistant", "Long acknowledgement. " * 500),
               message("user", "The setting_00 is alternative.")]
    with connect(lambda **kw: None, memory=MemoryPolicy(max_chars=12000),
                 config=RelayConfig(max_chars=8000, recent_turns=1, retrieve_turns=0)) as agent:
        prepared = agent.invoke(messages=history).prepared
        audit, view = agent.memory.snapshot(), agent.memory.context_snapshot()
        full_state = {key: prepared.context[key] for key in ("session_id", "revision", "facts", "sources")}
        full_state["user_memory"] = audit
        baseline = compile_context(history, state=full_state, max_chars=8000, recent_turns=1)
        original_size, compact_size = len(encode(audit)), len(encode(view))
        checks = {
            "same_entries": entry_map(audit) == entry_map(view),
            "same_pending_count": audit["pending_count"] == view["pending_count"],
            "quotes_retained": all(a["source"]["quote"] == b["source"]["quote"]
                                   for group in ("entries", "unresolved") for a, b in zip(audit[group], view[group])),
            "same_selected_history": baseline.report["included_indices"] == prepared.report["included_indices"],
            "smaller_input": len(encode(prepared.messages)) < len(encode(baseline.messages)),
        }
        return {"transport": "no model inference", "original_messages": history,
                "audit_snapshot": audit, "model_snapshot": view, "checks": checks,
                "audit_chars": original_size, "model_memory_chars": compact_size,
                "memory_chars_reduction_percent": round(100 * (1 - compact_size / original_size), 2),
                "full_audit_request_chars": len(encode(baseline.messages)),
                "compact_request_chars": len(encode(prepared.messages)),
                "scope": "Serialized characters for this fixture, not tokens, speed or accuracy."}


def framing_audit_fixtures() -> list[dict]:
    """Additional adversarial cases discovered after the frozen 28-case run.

    Keep these results separate from that initial corpus. A reported sentence or
    a question about a claim is not a user adopting the claim as a preference.
    """
    sources = [
        ("question_about_claim", "Is the following correct?\nThe export format is CSV."),
        ("notes_for_review", "Here are the notes to review:\nThe export format is CSV."),
        ("validation_request", "Can you validate this statement?\nThe export format is CSV."),
        ("demo_framing", "For the demo:\nThe export format is CSV."),
        ("pdf_sentence", "The PDF contains this sentence.\nThe export format is CSV."),
        ("customer_claim", "The customer claims the following.\nThe export format is CSV."),
        ("check_framing", "Please check:\nThe export format is CSV."),
        ("test_fixture_framing", "The test fixture contains:\nThe export format is CSV."),
    ]
    return [{"id": name, "category": "additional_framing_audit", "messages": [message("user", source)],
             "expected_entries": {}, "expected_pending": 0} for name, source in sources]


def entry_map(snapshot: dict) -> dict:
    return {entry["key"]: entry["value"] for entry in snapshot["entries"]}


def error_details(exc: Exception) -> dict:
    return {"type": type(exc).__name__, "message": str(exc)}


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_checks(snapshot: dict, originals: list[dict]) -> list[dict]:
    checks = []
    for entry in snapshot["entries"]:
        source = entry["source"]
        index, start, end = source["index"], source["start"], source["end"]
        original = originals[index]
        checks.append({"key": entry["key"], "user_source": original["role"] == "user",
                       "exact_quote": original["content"][start:end] == source["quote"],
                       "nonempty_span": type(start) is int and type(end) is int and 0 <= start < end <= len(original["content"])})
    return checks


def run_language_case(fixture: dict) -> dict:
    row = {"fixture_id": fixture["id"], "category": fixture["category"],
           "expected_entries": clone(fixture["expected_entries"]),
           "expected_pending": fixture["expected_pending"], "transport": "recording only; no model inference"}
    calls = []

    def recorder(*, messages):
        calls.append(clone(messages))
        return {"transport": "recording only; no model inference"}

    started = time.perf_counter()
    try:
        with connect(recorder, memory=MemoryPolicy(), config=RelayConfig(max_chars=16000)) as agent:
            result = agent.invoke(messages=fixture["messages"])
            snapshot = agent.memory.snapshot()
            row["snapshot"] = snapshot
            row["pending"] = agent.memory.pending()
            row["actual_entries"] = entry_map(snapshot)
            row["prepared_report"] = result.prepared.report
            state = state_from_messages(result.prepared.messages)
            row["checks"] = {
                "expected_entries": row["actual_entries"] == fixture["expected_entries"],
                "expected_pending": len(row["pending"]) == fixture["expected_pending"],
                "recall_injected": state.get("user_memory") == agent.memory.context_snapshot(),
                "not_authoritative": snapshot["current_authority"] is False,
                "no_fact_promotion": state["facts"] == {},
                "one_provider_call": len(calls) == 1,
                "bounded_input": len(encode(result.prepared.messages)) <= 16000,
            }
            row["source_checks"] = source_checks(snapshot, fixture["messages"])
            row["checks"]["exact_user_provenance"] = all(all(check[key] for key in ("user_source", "exact_quote", "nonempty_span"))
                                                           for check in row["source_checks"])
            row["passed"] = all(row["checks"].values())
            row["status"] = "evaluated"
    except Exception as exc:
        row.update(status="error", passed=False, error=error_details(exc))
    row["requests"] = calls
    row["wall_seconds"] = round(time.perf_counter() - started, 6)
    return row


def language_summary(rows: list[dict]) -> dict:
    expected_atoms, observed_atoms, correct_atoms = 0, 0, 0
    categories = {}
    for row in rows:
        expected = row["expected_entries"]
        actual = row.get("actual_entries", {})
        expected_atoms += len(expected)
        observed_atoms += len(actual)
        correct_atoms += sum(key in actual and actual[key] == value for key, value in expected.items())
        category = categories.setdefault(row["category"], {"cases": 0, "passed": 0})
        category["cases"] += 1
        category["passed"] += row["passed"] is True
    return {"cases": len(rows), "passed": sum(row["passed"] is True for row in rows),
            "errors": sum(row["status"] == "error" for row in rows), "categories": categories,
            "expected_active_entries": expected_atoms, "observed_active_entries": observed_atoms,
            "correct_active_entries": correct_atoms,
            "active_entry_precision": correct_atoms / observed_atoms if observed_atoms else None,
            "active_entry_coverage": correct_atoms / expected_atoms if expected_atoms else None,
            "metric_scope": "Exact key/value active entries in these synthetic cases; not semantic understanding or LLM answer accuracy."}


def distractors(count: int = 10) -> list[dict]:
    turns = []
    for index in range(count):
        turns += [message("user", f"Office checklist {index}: " + (
            "Review the stationery cupboard, meeting-room chairs, whiteboard pens, "
            "projector cables, kitchen cleaning rota, recycling bins, and printer paper. "
            "Acknowledge this routine checklist without changing any project preferences. "
        ) * 5), message("assistant", f"Office checklist {index} acknowledged. " + (
            "The routine checklist covers office supplies and maintenance only. "
        ) * 4)]
    return turns


def long_history(initial: str, correction: str, question: str) -> list[dict]:
    # The long assistant replies make each source turn larger than the model
    # budget. Extraction still reads the complete original user messages. This
    # deliberately tests preservation when original whole turns cannot fit.
    acknowledgement = "Acknowledged. " + "Keep the original user statement in the transcript. " * 115
    return [message("system", SYSTEM), message("user", initial), message("assistant", acknowledgement),
            message("user", correction), message("assistant", acknowledgement)] + distractors() + [message("user", question)]


def run_lifecycle() -> dict:
    result = {"transport": "recording only; no model inference", "checks": {}, "observations": {}}
    calls = []

    def recorder(*, messages):
        calls.append(clone(messages))
        return {"transport": "recording only; no model inference"}

    history = long_history("The export format is JSON.", "Actually, use TSV for the export format.",
                           "Which export format should I use now?")
    try:
        with tempfile.TemporaryDirectory(prefix="context-relay-memory-eval-") as temporary:
            settings = RelayConfig(db_path=str(Path(temporary) / "memory.sqlite3"), max_chars=LIVE_BUDGET,
                                   recent_turns=1, retrieve_turns=0)
            with connect(recorder, config=settings, memory=MemoryPolicy()) as agent:
                first = agent.invoke(messages=history)
                before = agent.memory.snapshot()
                revision_before = agent.guard.turn().revision
                second = agent.invoke(messages=history)
                result["checks"]["correction_retained"] = entry_map(before) == {"export_format": "TSV"}
                result["checks"]["original_sources_omitted"] = all(index in first.prepared.report["omitted_indices"] for index in (1, 3))
                result["checks"]["recalled_after_omission"] = entry_map(state_from_messages(first.prepared.messages)["user_memory"]) == {"export_format": "TSV"}
                result["checks"]["retry_no_memory_duplication"] = before == agent.memory.snapshot()
                result["checks"]["retry_no_revision_change"] = revision_before == agent.guard.turn().revision
                result["checks"]["bounded_before_and_after_retry"] = all(len(encode(prepared.messages)) <= LIVE_BUDGET for prepared in (first.prepared, second.prepared))
                result["checks"]["no_fact_promotion"] = agent.guard.turn().context["facts"] == {}
                result["observations"]["long_history"] = {"originals": history, "snapshot": before,
                                                          "prepared_report": first.prepared.report,
                                                          "request": first.prepared.messages}
                session_id = agent.session_id
            with connect(recorder, config=settings, session_id=session_id, memory=MemoryPolicy()) as resumed:
                resumed.invoke(messages=history)
                result["checks"]["persistent_resume"] = resumed.memory.snapshot() == before
                changed = clone(history)
                changed[1]["content"] = "The export format is XML."
                call_count = len(calls)
                try:
                    resumed.invoke(messages=changed)
                except Exception as exc:
                    result["observations"]["edited_history_rejection"] = error_details(exc)
                    result["checks"]["edited_history_rejected_before_provider"] = len(calls) == call_count
                else:
                    result["checks"]["edited_history_rejected_before_provider"] = False
                resumed.memory.forget("export_format")
                resumed.invoke(messages=history)
                result["checks"]["forgotten_value_not_readded_by_retry"] = entry_map(resumed.memory.snapshot()) == {}
            with connect(recorder, config=settings, memory=MemoryPolicy()) as separate:
                separate.invoke(messages=[message("user", "Continue.")])
                result["checks"]["session_isolation"] = entry_map(separate.memory.snapshot()) == {}
        with connect(recorder, memory=MemoryPolicy(mode="review")) as reviewed:
            review_history = [message("user", "The export format is CSV.")]
            reviewed.invoke(messages=review_history)
            proposals = reviewed.memory.pending()
            result["checks"]["review_mode_keeps_unconfirmed_out"] = entry_map(reviewed.memory.snapshot()) == {} and len(proposals) == 1
            if len(proposals) == 1:
                reviewed.memory.confirm(proposals[0]["id"])
                reviewed.invoke(messages=review_history)
                result["checks"]["explicit_review_can_confirm"] = entry_map(reviewed.memory.snapshot()) == {"export_format": "CSV"}
        result["status"] = "evaluated"
    except Exception as exc:
        result.update(status="error", error=error_details(exc))
    result["passed"] = result["status"] == "evaluated" and all(result["checks"].values())
    return result


def live_fixtures() -> list[dict]:
    return [
        {"id": "buried_format_correction", "expected_answer": "TSV",
         "messages": long_history("The export format is JSON.", "Actually, use TSV for the export format.",
                                  "Which export format should I use now?")},
        {"id": "buried_owner_correction", "expected_answer": "Mira Sen",
         "messages": long_history("The project owner is Arun Rao.", "Actually, set the project owner to Mira Sen.",
                                  "Who is the current project owner?")},
    ]


def run_live_case(client: LocalOllama, model: str, fixture: dict, enabled: bool,
                  report: dict, output: Path) -> None:
    row = {"fixture_id": fixture["id"], "memory_enabled": enabled, "status": "running",
           "expected_answer": fixture["expected_answer"], "calls": []}
    report["live"]["records"].append(row)

    def provider(*, messages):
        request = {"model": model, "messages": clone(messages), "stream": False,
                   "format": {"type": "object", "properties": {"answer": {"type": "string"}},
                              "required": ["answer"], "additionalProperties": False},
                   "options": {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 128},
                   "keep_alive": "2m"}
        call = {"request": request, "status": "running", "serialized_message_chars": len(encode(messages))}
        row["calls"].append(call)
        save_report(output, report)
        started = time.perf_counter()
        try:
            response = client._request("/api/chat", request)
            call["response"] = response
            call["metrics"] = {key: response.get(key) for key in METRICS}
            if response.get("done") is not True:
                raise ValueError("Ollama returned an incomplete response; raw output retained.")
            call["status"] = "responded"
            return response
        except Exception as exc:
            call.update(status="error", error=error_details(exc))
            raise
        finally:
            call["wall_seconds"] = round(time.perf_counter() - started, 6)
            save_report(output, report)

    try:
        settings = RelayConfig(max_chars=LIVE_BUDGET, recent_turns=1, retrieve_turns=0)
        with connect(provider, config=settings, memory=MemoryPolicy() if enabled else None) as agent:
            result = agent.invoke(messages=fixture["messages"])
            row["prepared_report"] = result.prepared.report
            row["memory"] = agent.memory.snapshot() if enabled else None
        row["answer"] = json.loads(result.response["message"]["content"])
        row["exact_answer_match"] = row["answer"] == {"answer": fixture["expected_answer"]}
        row["sources_omitted"] = all(index in result.prepared.report["omitted_indices"] for index in (1, 3))
        row["status"] = "evaluated"
    except Exception as exc:
        row.update(status="error", exact_answer_match=False, error=error_details(exc))
    save_report(output, report)


def live_summary(records: list[dict]) -> dict:
    summary = {}
    for enabled, name in ((False, "memory_off"), (True, "memory_on")):
        rows = [row for row in records if row["memory_enabled"] is enabled]
        calls = [call for row in rows for call in row["calls"]]
        summary[name] = {"cases": len(rows), "exact_answers": sum(row.get("exact_answer_match") is True for row in rows),
                         "errors": sum(row["status"] == "error" for row in rows), "model_calls": len(calls),
                         "prompt_eval_count": sum(call.get("metrics", {}).get("prompt_eval_count") or 0 for call in calls),
                         "wall_seconds": round(sum(call.get("wall_seconds", 0) for call in calls), 6)}
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New JSON report path; results are checkpointed.")
    parser.add_argument("--live", action="store_true", help="Add exactly four real calls to an already installed local Ollama model.")
    parser.add_argument("--model", default="qwen2.5:1.5b", help="Already installed model name for --live.")
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--language-version", choices=("0.7", "0.8"), default="0.8",
                        help="v0.7 expectations stay frozen; v0.8 explicitly adds supported wording.")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Choose a new --output path so previous observations are preserved.")
    fixtures = current_language_fixtures(args.language_version)
    report = {
        "schema_version": 1, "evaluation": "synthetic_automatic_memory_integration",
        "started_at": datetime.now(timezone.utc).isoformat(), "library_version": __version__,
        "library_sha256": hashlib.sha256(b"".join(path.name.encode("utf-8") + path.read_bytes()
                                                    for path in sorted((Path(__file__).resolve().parents[1] / "src" / "context_relay").glob("*.py")))).hexdigest(),
        "python": platform.python_version(), "platform": platform.platform(), "status": "running",
        "fixtures": fixtures, "fixture_sha256": hashlib.sha256(encode(fixtures).encode("utf-8")).hexdigest(),
        "language": {"transport": "recording only; no model inference", "records": []},
        "language_version": args.language_version,
        "framing_audit": {"description": "Additional adversarial cases added after the initial 28-case run; results kept separate.",
                          "fixtures": framing_audit_fixtures(), "records": []},
        "live": {"requested": args.live, "budget_chars": LIVE_BUDGET, "records": []},
        "limitations": [
            "Hand-authored examples are not representative production traffic or a statistical benchmark.",
            "Extraction checks match declared deterministic grammar, not arbitrary human meaning.",
            "Passing unsupported-language controls means leaving them unrecorded; their intents remain uncovered.",
            "Remembered user text is not verified fact, a source citation guarantee, or tool authorization.",
            "Obvious secret subjects test a lexical exclusion only; this is not a general privacy or secret detector.",
            "The recording transport demonstrates integration and contains no generated LLM answers.",
            "Optional live inputs deliberately use source turns larger than the cap to isolate memory retention after omission.",
            "Live conditions share the same raw transcript, system prompt, model settings, and character cap; memory consumes part of that cap.",
            "Two live cases, one local model, one sample each cannot establish general answer reliability or latency improvement.",
            "The v0.6 final grounded-recovery evaluation separately completed 0/2 tasks; this new test does not replace or erase that result.",
        ],
    }
    save_report(args.output, report)
    try:
        for fixture in fixtures:
            row = run_language_case(fixture)
            report["language"]["records"].append(row)
            save_report(args.output, report)
        report["language"]["summary"] = language_summary(report["language"]["records"])
        for fixture in framing_audit_fixtures():
            report["framing_audit"]["records"].append(run_language_case(fixture))
        report["framing_audit"]["summary"] = language_summary(report["framing_audit"]["records"])
        report["lifecycle"] = run_lifecycle()
        report["projection"] = projection_comparison()
        save_report(args.output, report)
        if args.live:
            client = LocalOllama(port=args.port, timeout=args.timeout)
            model = client.require_local_model(args.model)
            report["live"].update(model=model, ollama_version=client._request("/api/version"),
                                  model_details=client._request("/api/show", {"model": model}),
                                  fixtures=live_fixtures(), order="off/on for first fixture; on/off for second; no warmup")
            for index, fixture in enumerate(live_fixtures()):
                for enabled in ((False, True) if index == 0 else (True, False)):
                    print(f"{fixture['id']} / memory={'on' if enabled else 'off'} ...", flush=True)
                    run_live_case(client, model, fixture, enabled, report, args.output)
            report["live"]["summary"] = live_summary(report["live"]["records"])
        failures = (any(not row["passed"] for row in report["language"]["records"] + report["framing_audit"]["records"])
                    or not report["lifecycle"]["passed"] or not all(report["projection"]["checks"].values()))
        errors = any(row["status"] == "error" for row in report["live"]["records"])
        report["status"] = "complete_with_failures" if failures or errors else "complete"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_report(args.output, report)
        print(json.dumps({"language": report["language"]["summary"], "lifecycle": report["lifecycle"]["checks"],
                          "framing_audit": report["framing_audit"]["summary"],
                          "live": report["live"].get("summary")}, indent=2))
        print(f"Exact inputs, observations, and any real model outputs: {args.output}")
        return 1 if failures or errors else 0
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        save_report(args.output, report)
        return 130
    except Exception as exc:
        report.update(status="setup_error", error=error_details(exc))
        save_report(args.output, report)
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
