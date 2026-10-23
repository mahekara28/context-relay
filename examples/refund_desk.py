"""Offline Refund Desk: scripted proposals, real files, explicit local oracles.

This exercises configured boundaries, not model quality or a customer pilot.
Run after installing Relay: python examples/refund_desk.py --output report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

import context_relay
from context_relay import (ActionHarness, ActionRejected, MemoryClarificationRequired,
                           VerificationPolicy, auto_connect)
from context_relay.actions import ActionProposalError


# The outcome oracle is deliberately literal, not calculated by VerificationPolicy.
ORACLE = {"order_id": "O-41", "amount": 20, "approved": True,
          "format": "JSON", "note": "Customer requested refund."}
HISTORY = [{"role": "user", "content": "Export as JSON."}]
SCENARIOS = {
    "correct": ("executed", 1, 0, 1),
    "wrong_amount_repaired": ("repaired", 1, 1, 1),
    "missing_amount_repaired": ("repaired", 1, 1, 1),
    "stale_format_repaired": ("repaired", 1, 1, 1),
    "unknown_tool": ("proposal_rejected", 0, 0, 1),
    "duplicate_json": ("proposal_rejected", 0, 0, 1),
    "untrusted_source": ("blocked", 0, 0, 1),
    "stale_source": ("blocked", 0, 0, 1),
    "disagreeing_sources": ("blocked", 0, 0, 1),
    "state_changed_during_proposal": ("blocked", 0, 0, 1),
    "repair_changes_note": ("blocked", 0, 1, 1),
    "effect_then_interrupt": ("interrupted", 1, 0, 1),
    "named_restart": ("executed", 1, 0, 1),
    "unclear_preference": ("clarification_required", 0, 0, 0),
}
LIMITATION = "two_wrong_trusted_sources_agree"


def _source_hashes():
    directory = Path(context_relay.__file__).resolve().parent
    files = sorted(directory.rglob("*.py"))
    rows = [(path.relative_to(directory).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in files]
    return {"package_sha256": hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest(),
            "package_python_files": len(rows),
            "example_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _policy(case):
    now = time.time()
    sources = {name: {"data": {"units": 2, "unit_price": 10, "limit": 50},
                     "trusted": True, "independence_key": name,
                     "observed_at": now, "max_age_seconds": 3600}
               for name in ("ledger", "warehouse")}
    if case == "untrusted_source":
        sources["ledger"]["trusted"] = False
    elif case == "stale_source":
        sources["ledger"]["observed_at"] = now - 7200
    elif case == "disagreeing_sources":
        sources["warehouse"]["data"]["unit_price"] = 11
    elif case == LIMITATION:
        for source in sources.values():
            source["data"].update(unit_price=1000, limit=5000)
    def consensus(path):
        return {"consensus": [{"source": name, "path": path} for name in sources], "minimum": 2}
    amount = {"op": "multiply", "args": [consensus("/units"), consensus("/unit_price")]}
    return VerificationPolicy(sources, {"amount": amount,
        "approved": {"op": "lte", "args": [amount, consensus("/limit")]}})


def _receipt_files(directory):
    receipts = []
    for path in sorted(directory.glob("receipt-*.json")):
        raw = path.read_bytes()
        receipts.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                         "data": json.loads(raw.decode("utf-8"))})
    return receipts


def _run_case(case, directory):
    directory.mkdir()
    arguments = dict(ORACLE)
    if case in {"wrong_amount_repaired", "repair_changes_note", LIMITATION}:
        arguments["amount"] = 2000
    elif case == "missing_amount_repaired":
        arguments.pop("amount")
    elif case == "stale_format_repaired":
        arguments["format"] = "CSV"
    proposal = {"tool": "delete_all" if case == "unknown_tool" else "refund", "arguments": arguments}
    if case == "duplicate_json":
        proposal = json.dumps(proposal).replace('"amount": 20', '"amount": 2000, "amount": 20')
    policy = _policy(case)
    calls = []
    effects_started = 0
    report = {"name": case, "status": "error", "scripted_proposal_calls": 0,
              "proposals_generated": 0, "real_model_calls": 0, "network_calls": 0,
              "repair_attempts": 0, "repair_callback_calls": 0, "clarification_requests": 0,
              "replay_attempts": 0, "replay_blocked": None, "resumed_session": None,
              "resume_policy_revision_unchanged": None, "issues": [], "verification_issues": [], "receipt_statuses": []}
    agent = None
    def provider(*, messages, **options):
        calls.append(1)
        if case == "state_changed_during_proposal":
            agent.remember("can_refund", False)
        return proposal
    def refund(order_id, amount, approved, format, note):
        nonlocal effects_started
        effects_started += 1
        path = directory / f"receipt-{effects_started:02}.json"
        payload = {"order_id": order_id, "amount": amount, "approved": approved,
                   "format": format, "note": note}
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        if case == "effect_then_interrupt":
            raise KeyboardInterrupt("Simulated interruption after the receipt was written")
        return {"receipt": path.name}
    contracts = {"refund": {"when": {"can_refund": True}, "bindings": {"order_id": "order_id"},
                            "verification": policy, "unchecked": ("note",)}}
    repair = "known"
    if case == "repair_changes_note":
        def repair(request):
            return {**request["arguments"], "amount": 20, "note": "Unexpected replacement"}
    try:
        with ExitStack() as stack:
            options = ({"conversation": "refund-desk", "storage": directory / "sessions"}
                       if case == "named_restart" else {})
            agent = stack.enter_context(auto_connect(provider, **options))
            agent.remember("order_id", "O-41")
            agent.remember("can_refund", True)
            agent.memory.process(HISTORY)
            harness = ActionHarness(agent, {"refund": refund}, contracts=contracts, repair=repair)
            if case == "named_restart":
                original_id = agent.session_id
                revision = agent.guard.turn().revision
                agent.close()
                agent = stack.enter_context(auto_connect(provider, **options))
                report["resumed_session"] = agent.session_id == original_id
                harness = ActionHarness(agent, {"refund": refund}, contracts=contracts, repair=repair, resume=True)
                report["resume_policy_revision_unchanged"] = agent.guard.turn().revision == revision
            captured = agent.guard.turn()
            history = HISTORY
            if case == "unclear_preference":
                history = HISTORY + [{"role": "assistant", "content": "Recorded."},
                                     {"role": "user", "content": "Make the export format easier to read."}]
            try:
                result = harness.invoke(messages=history)
                execution = result.execution
                report.update(status=execution["status"], repair_attempts=execution["repair_attempts"],
                              repair_callback_calls=execution["repair_callback_calls"],
                              issues=[row["code"] for row in execution["final_check"]["issues"]],
                              verification_issues=sorted({row["code"] for row in
                                  execution["final_check"].get("verification_check", {}).get("issues", [])}))
            except ActionProposalError as error:
                report.update(status="proposal_rejected", issues=[error.code])
            except MemoryClarificationRequired:
                report.update(status="clarification_required", clarification_requests=1)
            except KeyboardInterrupt:
                if case != "effect_then_interrupt":
                    raise
                report.update(status="interrupted", replay_attempts=1, replay_blocked=False)
                # Deliberately attack the SAME captured revision, never obtain a fresh permission.
                try:
                    captured.wrap(refund, tool="refund")(**arguments)
                except ActionRejected:
                    report["replay_blocked"] = True
                except KeyboardInterrupt:
                    pass  # An actual second effect is counted below as a failure.
            report["receipt_statuses"] = [event["status"] for event in
                agent.guard.store.events(agent.session_id) if event["kind"] == "tool_result"]
    except Exception as error:
        report.update(status="error", error_type=type(error).__name__)
    report.update(scripted_proposal_calls=len(calls), proposals_generated=len(calls),
                  effects_started=effects_started, receipts=_receipt_files(directory))
    return report


def _literal_match(data, expected):
    return (type(data) is dict and set(data) == set(expected)
            and all(type(data[key]) is type(value) and data[key] == value
                    for key, value in expected.items()))


def _correct_receipt(receipt):
    # Type-sensitive comparison prevents true/1 or changed numeric representation.
    return _literal_match(receipt.get("data") if type(receipt) is dict else None, ORACLE)


def _grade(row):
    name = row.get("name")
    if name not in SCENARIOS:
        return False
    status, count, repairs, calls = SCENARIOS[name]
    receipts = row.get("receipts", [])
    count_fields = ("effects_started", "repair_attempts", "repair_callback_calls", "scripted_proposal_calls",
                    "proposals_generated", "real_model_calls", "network_calls", "clarification_requests", "replay_attempts")
    if any(type(row.get(key)) is not int or row[key] < 0 for key in count_fields):
        return False
    valid = (row.get("status") == status and type(receipts) is list and len(receipts) == count
             and all(_correct_receipt(receipt) for receipt in receipts)
             and row.get("effects_started") == count and row.get("repair_attempts") == repairs
             and row.get("scripted_proposal_calls") == calls and row.get("proposals_generated") == calls
             and row.get("real_model_calls") == 0 and row.get("network_calls") == 0
             and row.get("clarification_requests") == int(name == "unclear_preference")
             and row.get("repair_callback_calls") == int(name == "repair_changes_note")
             and row.get("replay_attempts") == int(name == "effect_then_interrupt")
             and row.get("receipt_statuses") == (["failed"] if name == "effect_then_interrupt"
                 else ["succeeded"] if status in {"executed", "repaired"} else []))
    required_issue = {"unknown_tool": "unknown_action_tool", "duplicate_json": "invalid_action_proposal",
                      "state_changed_during_proposal": "stale_revision", "repair_changes_note": "repair_scope_violation"}
    if name in required_issue:
        valid = valid and required_issue[name] in row.get("issues", [])
    source_issue = {"untrusted_source": "verification_untrusted_source", "stale_source": "verification_stale_source",
                    "disagreeing_sources": "verification_disagreement"}
    if name in source_issue:
        valid = valid and source_issue[name] in row.get("verification_issues", [])
    if name == "effect_then_interrupt":
        valid = valid and row.get("replay_attempts") == 1 and row.get("replay_blocked") is True \
                and row.get("receipt_statuses") == ["failed"]
    elif name == "named_restart":
        valid = valid and row.get("resumed_session") is True and row.get("resume_policy_revision_unchanged") is True
    if name == "repair_changes_note":
        valid = valid and row.get("repair_callback_calls") == 1 and "repair_scope_violation" in row.get("issues", [])
    return bool(valid)


def _summary(cases, limitation):
    names = [row.get("name") for row in cases]
    complete = len(names) == len(SCENARIOS) and set(names) == set(SCENARIOS)
    passed = sum(_grade(row) for row in cases)
    supported_receipts = [receipt for row in cases for receipt in row.get("receipts", [])]
    limitation_receipts = limitation.get("receipts", [])
    return {"required_cases": len(SCENARIOS), "reported_cases": len(cases), "manifest_complete": complete,
            "boundary_cases_passed": passed, "boundary_cases_failed": len(cases) - passed,
            "all_supported_boundaries_pass": complete and passed == len(SCENARIOS),
            "completed_tasks": sum(row.get("status") in {"executed", "repaired"} and _grade(row) for row in cases),
            "blocked_tasks": sum(row.get("status") in {"blocked", "proposal_rejected"} for row in cases),
            "clarification_requests": sum(row.get("clarification_requests", 0) for row in cases),
            "interrupted_tasks": sum(row.get("status") == "interrupted" for row in cases),
            "unfinished_tasks": sum(row.get("status") not in {"executed", "repaired"} for row in cases),
            "repair_attempts": sum(row.get("repair_attempts", 0) for row in cases),
            "repair_callback_calls": sum(row.get("repair_callback_calls", 0) for row in cases),
            "scripted_proposal_calls": sum(row.get("scripted_proposal_calls", 0) for row in cases) + limitation.get("scripted_proposal_calls", 0),
            "real_model_calls": sum(row.get("real_model_calls", 0) for row in cases) + limitation.get("real_model_calls", 0),
            "network_calls": sum(row.get("network_calls", 0) for row in cases) + limitation.get("network_calls", 0),
            "correct_effects_supported": sum(_correct_receipt(receipt) for receipt in supported_receipts),
            "incorrect_effects_supported": sum(not _correct_receipt(receipt) for receipt in supported_receipts),
            "incorrect_effects_known_limitation": sum(not _correct_receipt(receipt) for receipt in limitation_receipts),
            "incorrect_effects_total": sum(not _correct_receipt(receipt) for receipt in supported_receipts + limitation_receipts)}


def _valid_receipt_record(receipt):
    if type(receipt) is not dict or set(receipt) != {"file", "sha256", "data"}:
        return False
    if type(receipt["file"]) is not str or not re.fullmatch(r"receipt-[0-9]{2}\.json", receipt["file"]):
        return False
    raw = json.dumps(receipt["data"], ensure_ascii=False, sort_keys=True).encode("utf-8")
    return receipt["sha256"] == hashlib.sha256(raw).hexdigest()


def _valid_source_record(source):
    return (type(source) is dict and set(source) == {"package_sha256", "package_python_files", "example_sha256"}
            and type(source["package_python_files"]) is int and source["package_python_files"] > 0
            and all(type(source[key]) is str and re.fullmatch(r"[0-9a-f]{64}", source[key])
                    for key in ("package_sha256", "example_sha256")))


def validate_report(report):
    """Recompute literal outcomes and completeness; stored pass flags are not trusted."""
    try:
        cases, limitation = report["cases"], report["known_limitation"]
        if type(cases) is not list or any(type(row) is not dict for row in cases) or type(limitation) is not dict:
            return False
        if any(row.get("boundary_passed") is not _grade(row) for row in cases):
            return False
        if any(type(row.get("receipts")) is not list or any(not _valid_receipt_record(item) for item in row["receipts"])
               for row in cases + [limitation]):
            return False
        if (type(report.get("schema")) is not int or report["schema"] != 1
                or not _literal_match(report.get("oracle"), ORACLE)
                or report.get("required_scenarios") != list(SCENARIOS)
                or not _valid_source_record(report.get("source_before"))
                or not _valid_source_record(report.get("source_after"))):
            return False
        expected = _summary(cases, limitation)
        control = (limitation.get("name") == LIMITATION and limitation.get("status") == "executed"
                   and limitation.get("counted_as_supported_success") is False
                   and len(limitation.get("receipts", [])) == 1
                   and _literal_match(limitation["receipts"][0]["data"], {**ORACLE, "amount": 2000})
                   and all(type(limitation.get(key)) is int for key in ("effects_started", "scripted_proposal_calls",
                       "proposals_generated", "repair_attempts", "repair_callback_calls", "real_model_calls", "network_calls",
                       "clarification_requests", "replay_attempts"))
                   and limitation.get("effects_started") == 1 and limitation.get("receipt_statuses") == ["succeeded"]
                   and limitation.get("scripted_proposal_calls") == 1 and limitation.get("proposals_generated") == 1
                   and limitation.get("repair_attempts") == 0 and limitation.get("repair_callback_calls") == 0
                   and limitation.get("clarification_requests") == 0 and limitation.get("replay_attempts") == 0
                   and limitation.get("real_model_calls") == 0 and limitation.get("network_calls") == 0)
        return (json.dumps(report["summary"], sort_keys=True) == json.dumps(expected, sort_keys=True)
                and expected["all_supported_boundaries_pass"]
                and expected["real_model_calls"] == 0 and expected["network_calls"] == 0
                and report["source_before"] == report["source_after"] and control
                and report.get("model_quality_evidence") is False and report.get("customer_pilot") is False
                and report.get("validation_passed", True) is True)
    except (KeyError, TypeError, ValueError):
        return False


def run_suite():
    """Run every supported case and a visible wrong-source limitation control."""
    before = _source_hashes()
    with tempfile.TemporaryDirectory(prefix="relay-refund-desk-") as temporary:
        directory = Path(temporary)
        cases = [_run_case(name, directory / name) for name in SCENARIOS]
        for row in cases:
            row["boundary_passed"] = _grade(row)
        limitation = _run_case(LIMITATION, directory / "known-limitation")
    limitation.update(counted_as_supported_success=False,
        explanation="Two incorrect application-trusted sources agree. Relay cannot establish external truth; the independent literal oracle catches the wrong refund.")
    report = {"schema": 1, "application": "Offline Refund Desk", "oracle": dict(ORACLE),
              "required_scenarios": list(SCENARIOS), "cases": cases, "known_limitation": limitation,
              "summary": _summary(cases, limitation), "source_before": before, "source_after": _source_hashes(),
              "model_quality_evidence": False, "customer_pilot": False,
              "scope": "Scripted proposals and real temporary receipt files; no network, model inference or real refunds."}
    report["validation_passed"] = validate_report(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New JSON report path; existing reports are never overwritten.")
    args = parser.parse_args(argv)
    report = run_suite()
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        print(json.dumps({"report": str(args.output.resolve()), **report["summary"]}))
    return 0 if report["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
