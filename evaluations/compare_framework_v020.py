"""Matched recorded proposals: LangGraph ordinary validation versus Relay guards.

Both arms use the real LangGraph StateGraph/InMemorySaver and the same real
SQLite/file destination. This is not a language-extraction or inference test.
Run with a Python environment containing LangGraph and the local Relay source.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
from importlib.metadata import version
import importlib.util
import io
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from framework_baseline_v020 import canonical, validate_proposal

FIXTURE = HERE / "framework-v020-fixtures.json"
FIXTURE_SHA256 = "855ecf9d56ea3aa57cda08bf0aacdf05f29161c714457291b44f89dad41ee463"
ARMS = ("langgraph_ordinary_validator", "langgraph_relay_guard")
_spec = importlib.util.spec_from_file_location("comparison_release_desk", ROOT / "examples" / "release_desk.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load_fixtures():
    if hashlib.sha256(FIXTURE.read_bytes()).hexdigest() != FIXTURE_SHA256:
        raise ValueError("The frozen comparison fixture changed; create a separately named revision.")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def source_fingerprints():
    paths = [Path(__file__), HERE / "framework_baseline_v020.py", FIXTURE,
             ROOT / "examples" / "release_desk.py", *sorted((ROOT / "src" / "context_relay").glob("*.py"))]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


class BeforeEffectDenied(RuntimeError):
    pass


class AfterWriteInterrupted(RuntimeError):
    pass


class GraphState(TypedDict, total=False):
    operation: dict
    standing: str
    unresolved: bool
    requests: dict
    result: dict
    route: str
    captured: dict
    expected: dict


class OrdinaryAdapter:
    def capture(self, expected, facts):
        pass

    def execute(self, proposal, expected, facts, effect):
        result = validate_proposal(proposal, expected, may_export=facts["may_export"], repair_budget=1)
        if result["status"] != "ready":
            return result
        receipt = effect(**result["arguments"])
        return {"status": "completed" if receipt.get("ok") is True else "blocked",
                "executed": True, "completed": receipt.get("ok") is True,
                "repair_attempts": result["repair_attempts"], "receipt": receipt}

    def close(self):
        pass


class RelayAdapter:
    def __init__(self, effect):
        from context_relay import ContextGuard, OutcomePolicy, ToolRegistry
        self.guard = ContextGuard(name="Matched framework comparison")
        self.registry = ToolRegistry(self.guard)
        self.registry.register(effect, name="export_manifest", memory=False,
            when={"may_export": True}, bindings={key: "expected_" + key for key in
                ("request_id", "source_version", "format", "release", "artifacts")},
            outcome=OutcomePolicy(required={"/ok": True}))

    def capture(self, expected, facts):
        self.guard.remember("may_export", facts["may_export"])
        for key, value in expected.items():
            self.guard.remember("expected_" + key, value)
        self.turn = self.guard.turn()

    def execute(self, proposal, expected, facts, effect):
        # The same small canonical proposal envelope is used by both arms.
        if (type(proposal) is not dict or set(proposal) != {"tool", "arguments"}
                or proposal["tool"] != "export_manifest" or type(proposal["arguments"]) is not dict):
            return {"status": "blocked", "executed": False, "completed": False, "repair_attempts": 0}
        try:
            result = self.registry.bind(self.turn).execute_with_recovery(
                proposal["tool"], proposal["arguments"], repair="known")
        except (TypeError, ValueError):
            # These are pre-dispatch signature errors. Callback failures are
            # ToolExecutionError and deliberately handled outside this catch.
            return {"status": "blocked", "executed": False, "completed": False, "repair_attempts": 0}
        return {"status": "completed" if result.get("completed") else "blocked",
                "executed": result["executed"], "completed": result.get("completed", False),
                "repair_attempts": result["repair_attempts"], "receipt": result.get("result")}

    def close(self):
        self.guard.close()


class CaseWorkflow:
    """Equal application scaffolding, checkpoint memory and replay on both arms."""
    def __init__(self, case, arm, directory):
        self.case, self.arm = case, arm
        self.desk = release.ReleaseDesk(directory)
        self.set_source(case["initial_source"])
        self.proposals, self.destination_calls, self.operations = [], [], []
        self.active = None
        self.adapter = OrdinaryAdapter() if arm == ARMS[0] else RelayAdapter(self.effect)
        builder = StateGraph(GraphState)
        builder.add_node("prepare", self.prepare)
        builder.add_node("recorded_proposal", self.propose)
        builder.add_node("check_and_execute", self.execute)
        builder.add_edge(START, "prepare")
        builder.add_conditional_edges("prepare", lambda state: state["route"],
                                      {"end": END, "propose": "recorded_proposal"})
        builder.add_edge("recorded_proposal", "check_and_execute")
        builder.add_edge("check_and_execute", END)
        self.graph = builder.compile(checkpointer=InMemorySaver())
        self.config = {"configurable": {"thread_id": case["id"]}}

    def set_source(self, source):
        return self.desk.set_source(source["approved_release"], source["approved_artifacts"],
                                    may_export=source["may_export"])

    def prepare(self, state):
        operation = state["operation"]
        request_id = operation["request_id"]
        requests = deepcopy(state.get("requests", {}))
        if operation["operation"] == "reconcile":
            record = requests[request_id]
            if record["status"] != "pending":
                raise ValueError("Only an interrupted destination call can be reconciled.")
            receipt = self.desk.reconcile(request_id)
            result = {"status": "completed" if receipt.get("ok") is True else "blocked",
                      "completed": receipt.get("ok") is True, "executed": False, "receipt": receipt,
                      "repair_attempts": record.get("repair_attempts", 0), "reconciled": True}
            requests[request_id] = {**record, **result}
            return {"requests": requests, "result": result, "route": "end"}
        if request_id in requests:
            # Checkpointer state is the source of request replay protection in
            # BOTH arms. Destination uniqueness independently protects writes.
            return {"result": {**requests[request_id], "replayed": True}, "route": "end"}
        if operation["operation"] != "send":
            raise ValueError("Cannot replay an unknown request.")
        event = operation["instruction_event"]
        standing = state.get("standing", self.case["initial_standing"])
        unresolved = state.get("unresolved", False)
        if event["scope"] == "durable":
            standing, unresolved = event["value"], False
        elif event["scope"] == "ambiguous":
            unresolved = True
        effective = event["value"] if event["scope"] == "turn" else standing
        if unresolved:
            result = {"status": "clarification", "completed": False, "executed": False, "repair_attempts": 0}
            requests[request_id] = result
            return {"standing": standing, "unresolved": True, "requests": requests, "result": result, "route": "end"}
        captured = self.desk.read_evidence()
        expected = {"request_id": request_id, "source_version": captured["version"], "format": effective,
                    "release": captured["facts"]["approved_release"], "artifacts": captured["facts"]["approved_artifacts"]}
        self.adapter.capture(expected, captured["facts"])
        requests[request_id] = {"status": "prepared", "completed": False, "executed": False, "repair_attempts": 0}
        return {"standing": standing, "unresolved": False, "requests": requests,
                "captured": captured, "expected": expected, "route": "propose"}

    def propose(self, state):
        operation, captured = state["operation"], state["captured"]
        model_input = {"text": operation["text"], "request_id": operation["request_id"],
            "standing_format": state["standing"], "effective_format": state["expected"]["format"],
            "source": {"version": captured["version"], "facts": captured["facts"]}, "repair_budget": 1}
        self.proposals.append({"request_id": operation["request_id"], "input": model_input,
            "input_sha256": digest(model_input), "proposal": deepcopy(operation["proposal"]),
            "proposal_sha256": digest(operation["proposal"])})
        if "after_proposal_source" in operation:
            self.set_source(operation["after_proposal_source"])
        return {}

    def effect(self, request_id, source_version, format, release, artifacts):
        current = self.desk.read_evidence()
        captured = self.active["captured"]
        if (current["version"] != captured["version"]
                or canonical(current["facts"]) != canonical(captured["facts"])
                or current["facts"]["may_export"] is not True):
            raise BeforeEffectDenied("Current source or permission differs from the captured source.")
        arguments = dict(request_id=request_id, source_version=source_version, format=format,
                         release=release, artifacts=artifacts)
        self.destination_calls.append(deepcopy(arguments))
        receipt = self.desk.export_manifest(**arguments)
        if self.active["operation"].get("fail_after_write"):
            raise AfterWriteInterrupted("Injected interruption after the destination committed and wrote its file.")
        return receipt

    def execute(self, state):
        self.active = state
        try:
            result = self.adapter.execute(state["operation"]["proposal"], state["expected"],
                                          state["captured"]["facts"], self.effect)
        except Exception as error:
            cause = error.__cause__ or error
            if isinstance(cause, BeforeEffectDenied):
                result = {"status": "blocked", "executed": False, "completed": False, "repair_attempts": 0}
            elif isinstance(cause, AfterWriteInterrupted):
                result = {"status": "pending", "executed": False, "completed": False, "repair_attempts": 0}
            else:
                result = {"status": "error", "executed": False, "completed": False, "repair_attempts": 0,
                          "error_type": type(error).__name__, "message": str(error)[:1000]}
        finally:
            self.active = None
        requests = deepcopy(state["requests"])
        requests[state["operation"]["request_id"]] = result
        return {"requests": requests, "result": result}

    def run(self):
        try:
            for operation in self.case["steps"]:
                started = time.perf_counter()
                try:
                    state = self.graph.invoke({"operation": deepcopy(operation)}, self.config)
                    result = state["result"]
                except Exception as error:
                    result = {"status": "error", "completed": False, "executed": False,
                              "error_type": type(error).__name__, "message": str(error)[:1000]}
                self.operations.append({"operation": operation["operation"], "request_id": operation["request_id"],
                    "expected": operation["expected"], "result": result,
                    "elapsed_ms": (time.perf_counter() - started) * 1000})
            checkpoint = self.graph.get_state(self.config)
            documents = self.desk.documents()
            for document in documents:
                filename = release.digest(document["request_id"]) + "." + document["receipt"]["format"].lower()
                path = self.desk.directory / filename
                document["file"] = str(path)
                document["file_text"] = path.read_text(encoding="utf-8")
                document["file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            return {"case_id": self.case["id"], "kind": self.case["kind"], "arm": self.arm,
                    "operations": self.operations, "requests": checkpoint.values.get("requests", {}),
                    "checkpoint_count": len(list(self.graph.get_state_history(self.config))),
                    "proposal_calls": self.proposals, "destination_calls": self.destination_calls,
                    "documents": documents}
        finally:
            self.adapter.close()


def document_matches(document, expected):
    try:
        if document["receipt"]["format"] != expected["format"] or document["file_text"] != document["document"]:
            return False
        if expected["format"] == "JSON":
            return canonical(json.loads(document["document"])) == canonical(
                {"release": expected["release"], "artifacts": expected["artifacts"]})
        rows = list(csv.reader(io.StringIO(document["document"])))
        return rows == [["release", "filename", "bytes", "sha256"], *[
            [expected["release"], row["filename"], str(row["bytes"]), row["sha256"]] for row in expected["artifacts"]]]
    except (KeyError, TypeError, ValueError):
        return False


def summarize(cases, fixtures):
    oracle = {case["id"]: case for case in fixtures["cases"]}
    summary = {"send_tasks": 0, "operations": 0, "correct_completed_tasks": 0,
        "reported_completed_tasks": 0, "wrong_effects": 0, "supported_wrong_effects": 0,
        "known_limitation_wrong_effects": 0, "false_blocks": 0, "blocked_tasks": 0, "clarification_requests": 0,
        "repair_attempts": 0, "repair_induced_wrong_effects": 0, "duplicate_effects": 0,
        "duplicate_destination_calls": 0, "proposal_calls": 0, "model_calls": 0,
        "destination_calls": 0, "unexpected_errors": 0, "boundary_mismatches": 0}
    timings = []
    for result in cases:
        case = oracle[result["case_id"]]
        summary["send_tasks"] += sum(step["operation"] == "send" for step in case["steps"])
        summary["operations"] += len(result["operations"])
        summary["proposal_calls"] += len(result["proposal_calls"])
        summary["destination_calls"] += len(result["destination_calls"])
        summary["duplicate_destination_calls"] += len(result["destination_calls"]) - len({call["request_id"] for call in result["destination_calls"]})
        summary["duplicate_effects"] += len(result["documents"]) - len({doc["request_id"] for doc in result["documents"]})
        correct = set()
        for doc in result["documents"]:
            expected = case["expected_documents"].get(doc["request_id"])
            if expected is not None and document_matches(doc, expected):
                correct.add(doc["request_id"])
            else:
                summary["wrong_effects"] += 1
                summary["supported_wrong_effects" if case["kind"] == "supported" else "known_limitation_wrong_effects"] += 1
                if result["requests"].get(doc["request_id"], {}).get("repair_attempts", 0):
                    summary["repair_induced_wrong_effects"] += 1
        for request_id, request in result["requests"].items():
            summary["reported_completed_tasks"] += request.get("completed") is True
            summary["correct_completed_tasks"] += request.get("completed") is True and request_id in correct
            summary["repair_attempts"] += request.get("repair_attempts", 0)
            summary["blocked_tasks"] += request["status"] == "blocked"
            summary["clarification_requests"] += request["status"] == "clarification"
            if request_id in case["expected_documents"] and request["status"] in {"blocked", "clarification"}:
                summary["false_blocks"] += 1
        for operation, expected in zip(result["operations"], case["steps"]):
            timings.append(operation["elapsed_ms"])
            summary["unexpected_errors"] += operation["result"]["status"] == "error"
            summary["boundary_mismatches"] += operation["result"]["status"] != expected["expected"]
            if "expected_repairs" in expected:
                summary["boundary_mismatches"] += operation["result"].get("repair_attempts") != expected["expected_repairs"]
    ordered = sorted(timings)
    summary["latency_ms"] = {"count": len(ordered), "p50": statistics.median(ordered),
        "p95": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
        "p99": ordered[max(0, math.ceil(0.99 * len(ordered)) - 1)], "total": sum(ordered)}
    return summary


def expected_proposals(case):
    """Reconstruct the public input manifest solely from the frozen fixture."""
    standing, unresolved, source = case["initial_standing"], False, case["initial_source"]
    proposals = []
    for step in case["steps"]:
        if step["operation"] != "send":
            continue
        event = step["instruction_event"]
        if event["scope"] == "durable":
            standing, unresolved = event["value"], False
        elif event["scope"] == "ambiguous":
            unresolved = True
        if unresolved:
            continue
        model_input = {"text": step["text"], "request_id": step["request_id"],
            "standing_format": standing, "effective_format": event["value"] if event["scope"] == "turn" else standing,
            "source": {"version": digest(source), "facts": source}, "repair_budget": 1}
        proposals.append({"request_id": step["request_id"], "input": model_input,
            "input_sha256": digest(model_input), "proposal": step["proposal"], "proposal_sha256": digest(step["proposal"])})
        source = step.get("after_proposal_source", source)
    return proposals


def validate_report(report):
    """Recompute results against the frozen oracle, including the known failure."""
    try:
        fixtures = load_fixtures()
        if (report["schema"] != 1 or report["fixture_sha256"] != FIXTURE_SHA256
                or report["source_before"] != report["source_after"]
                or set(report["arms"]) != set(ARMS)):
            return False
        ids = [case["id"] for case in fixtures["cases"]]
        for arm in ARMS:
            results = report["arms"][arm]["cases"]
            if [case["case_id"] for case in results] != ids or any(case["arm"] != arm for case in results):
                return False
            if any(len(result["operations"]) != len(case["steps"]) or result["checkpoint_count"] < 2
                   for result, case in zip(results, fixtures["cases"])):
                return False
            for result, case in zip(results, fixtures["cases"]):
                final_status = {step["request_id"]: step["expected"] for step in case["steps"]}
                if set(result["requests"]) != set(final_status):
                    return False
                if any(record["status"] != final_status[key]
                       or record.get("completed") is not (final_status[key] == "completed")
                       for key, record in result["requests"].items()):
                    return False
                if (sorted(doc["request_id"] for doc in result["documents"]) != sorted(case["expected_documents"])
                        or sorted(call["request_id"] for call in result["destination_calls"]) != sorted(case["expected_documents"])
                        or canonical(result["proposal_calls"]) != canonical(expected_proposals(case))):
                    return False
                for operation, step in zip(result["operations"], case["steps"]):
                    if any(operation[key] != step[key] for key in ("operation", "request_id", "expected")):
                        return False
                    if (type(operation["elapsed_ms"]) not in (int, float)
                            or not math.isfinite(operation["elapsed_ms"]) or operation["elapsed_ms"] < 0):
                        return False
                for document in result["documents"]:
                    data = Path(document["file"]).read_bytes()
                    actual_hash = hashlib.sha256(data).hexdigest()
                    if (data.decode("utf-8") != document["file_text"] or actual_hash != document["file_sha256"]
                            or actual_hash != document["receipt"]["document_sha256"]):
                        return False
            recomputed = summarize(results, fixtures)
            if canonical(recomputed) != canonical(report["arms"][arm]["summary"]):
                return False
            if (recomputed["boundary_mismatches"] or recomputed["unexpected_errors"]
                    or recomputed["supported_wrong_effects"] or recomputed["known_limitation_wrong_effects"] != 1
                    or recomputed["duplicate_effects"] or recomputed["duplicate_destination_calls"]):
                return False
        for left, right in zip(report["arms"][ARMS[0]]["cases"], report["arms"][ARMS[1]]["cases"]):
            if canonical(left["proposal_calls"]) != canonical(right["proposal_calls"]):
                return False
        return True
    except (KeyError, TypeError, ValueError, OSError):
        return False


def run_comparison(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    fixtures, before = load_fixtures(), source_fingerprints()
    report = {"schema": 1, "study": "matched recorded proposals; no inference", "fixture_sha256": FIXTURE_SHA256,
        "python": platform.python_version(), "packages": {name: version(name) for name in ("langgraph", "langgraph-checkpoint")},
        "source_before": before, "data_directory": str(directory), "arms": {},
        "limitations": ["Host-normalized instruction events; no language extraction comparison.",
            "Recorded proposals are supplied, not sampled from an LLM; network and model calls are zero.",
            "InMemorySaver checkpoints are real but process-local; no crash/distributed durability claim.",
            "Same application freshness, reconciliation and destination idempotency scaffolding in both arms.",
            "Nine selected development cases; no representative population or performance superiority claim.",
            "Wrong trusted source intentionally produces a wrong export in both arms."]}
    for arm in ARMS:
        cases = [CaseWorkflow(case, arm, directory / arm / case["id"]).run() for case in fixtures["cases"]]
        report["arms"][arm] = {"cases": cases, "summary": summarize(cases, fixtures)}
    report["source_after"] = source_fingerprints()
    report["valid"] = validate_report(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="New directory for real local destinations")
    parser.add_argument("--output", type=Path, required=True, help="New report path; never overwrite evidence")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; use a new report path.")
    report = run_comparison(args.data_dir)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"valid": report["valid"], "arms": {arm: value["summary"] for arm, value in report["arms"].items()}}, indent=2))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
