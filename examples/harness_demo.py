"""Run a deterministic Context Relay harness example, with no model or network.

From the repository root:
    python examples/harness_demo.py
    python examples/harness_demo.py --json

The harness records an explicit user correction after capturing context. A
hard-coded proposal illustrates an old model response; it is not AI inference.
The guarded adapter rejects that proposal, restores current state, and permits
a simulated replacement. No refund or shipment is actually created.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running this source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import Relay, RelayStore, guarded_tool_call


def create_workflow(store: RelayStore) -> str:
    """Only the harness creates policies and records authoritative facts."""
    session_id = store.create_session("Example customer support", scenario="refund")["id"]
    for tool, resolution in (("issue_refund", "refund"), ("create_replacement", "replacement")):
        store.add_policy(session_id, {
            "id": tool,
            "title": f"Current customer request must be {resolution}",
            "tool": tool,
            "requirements": [{"key": "resolution", "op": "eq", "value": resolution}],
            "bindings": {"order_id": "order_id"},
            "optional_args": [],
        })
    store.record_fact(session_id, "order_id", "DEMO-1042", source="example:order_service",
                      text="This example uses synthetic order DEMO-1042.")
    store.record_fact(session_id, "resolution", "refund", source="example:user",
                      text="Please refund my order.")
    return session_id


def record_correction(store: RelayStore, session_id: str) -> None:
    store.record_fact(session_id, "resolution", "replacement", source="example:user",
                      text="Actually, send a replacement. Do not refund my order.")


def simulated_executor(tool: str, arguments: dict) -> dict:
    """This executor performs no external actions."""
    if tool not in {"issue_refund", "create_replacement"}:
        raise ValueError("No simulator exists for that tool.")
    return {"simulated": True, "tool": tool, "order_id": arguments["order_id"],
            "message": "Simulation complete. No money moved and no shipment was created."}


def run_demo() -> dict:
    calls = []

    def execute(tool, arguments):
        calls.append({"tool": tool, "arguments": arguments})
        return simulated_executor(tool, arguments)

    with RelayStore() as store:
        relay = Relay(store)
        session_id = create_workflow(store)

        # Store this snapshot in the harness, separately from anything a model returns.
        captured_context = relay.context(session_id)
        stale_proposal = {"tool": "issue_refund", "arguments": {"order_id": "DEMO-1042"}}

        # A new user instruction arrives while the model is working from old context.
        record_correction(store, session_id)
        first = guarded_tool_call(relay, session_id, captured_context, stale_proposal, execute)

        # A real integration would give this restored context back to its model.
        restored_context = first["check"]["recovery_context"]
        revised_proposal = {"tool": "create_replacement", "arguments": {"order_id": "DEMO-1042"}}
        second = guarded_tool_call(relay, session_id, restored_context, revised_proposal, execute)

        if first["executed"] or not second["executed"] or len(calls) != 1:
            raise RuntimeError("The expected guard behavior was not observed.")
        return {
            "mode": "deterministic example; no model connected",
            "initial_context_revision": captured_context["revision"],
            "restored_context_revision": restored_context["revision"],
            "stale_attempt": first,
            "refreshed_attempt": second,
            "simulated_tool_calls": calls,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Print the complete checks, evidence, and simulated receipt as JSON.")
    args = parser.parse_args(argv)
    report = run_demo()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print("DETERMINISTIC HARNESS DEMO | No model, API key, or network")
        print(f"1. Captured refund request at revision {report['initial_context_revision']}.")
        print(f"2. Customer changed to replacement at revision {report['restored_context_revision']}.")
        first = report["stale_attempt"]
        print(f"3. Old refund proposal: {first['check']['decision'].upper()}; callback executed: {first['executed']}.")
        for problem in first["check"]["issues"]:
            print(f"   {problem['code']}: {problem['message']}")
        second = report["refreshed_attempt"]
        print(f"4. Refreshed replacement proposal: {second['check']['decision'].upper()}; callback executed: {second['executed']}.")
        print(second["result"]["message"])
        print("Use --json to inspect actual evidence IDs, revisions, and the execution receipt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
