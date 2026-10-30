"""Run a local task handoff and print the resulting state. No model or network."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from context_relay import RelayStore
from context_relay.handoffs import HandoffService
from context_relay.organization import Principal
from context_relay.task_service import TaskService, inspect_git_workspace


def run_demo() -> dict:
    sender = Principal("readme-demo", "sender")
    receiver = Principal("readme-demo", "receiver")
    workspace = inspect_git_workspace(Path(__file__).resolve().parents[1])
    with tempfile.TemporaryDirectory(prefix="relay-demo-") as directory:
        store = RelayStore(Path(directory) / "tasks.db")
        try:
            tasks = TaskService(
                store,
                authorize=lambda principal, action: principal in (sender, receiver),
            )
            handoffs = HandoffService(tasks)
            created = tasks.create(
                sender,
                goal="Produce a regional revenue report",
                completion_criteria=["The independent report oracle passes"],
                workspace=workspace,
                open_work=["Implement JSON export", "Run the report oracle"],
            )
            original = tasks.add_instruction(
                sender,
                created["id"],
                expected_revision=0,
                request_id="csv",
                text="Use CSV",
                source={"kind": "user", "ref": "message-1"},
            )
            corrected = tasks.add_instruction(
                sender,
                created["id"],
                expected_revision=original["revision"],
                request_id="json",
                text="Use JSON",
                source={"kind": "user", "ref": "message-2"},
                supersedes=[original["instructions"][0]["id"]],
            )
            offered = handoffs.offer(sender, created["id"], recipient=receiver.subject)
            accepted = handoffs.accept(receiver, offered["id"])
            diagnosis = tasks.task_diagnostics(receiver, created["id"])
            history = tasks.show(receiver, created["id"])
            packet = accepted["packet"]
            current = packet["instructions"]
            superseded = [
                entry
                for entry in history["instructions"]
                if entry["status"] == "superseded"
            ]
            if (
                accepted["status"] != "accepted"
                or packet["revision"] != 2
                or [entry["text"] for entry in current] != ["Use JSON"]
                or [entry["text"] for entry in superseded] != ["Use CSV"]
                or diagnosis["verification"]["stored"] is not False
            ):
                raise RuntimeError(
                    "The demo did not produce the expected handoff state."
                )
            # Keep the transcript portable: no random IDs, timestamps or local paths.
            report = {
                "original": {
                    "revision": original["revision"],
                    "text": original["instructions"][0]["text"],
                },
                "correction": {
                    "revision": corrected["revision"],
                    "previous": superseded[0]["text"],
                    "current": current[0]["text"],
                    "source": current[0]["source"],
                },
                "handoff": {
                    "sender": sender.subject,
                    "recipient": accepted["recipient"],
                    "revision": accepted["revision"],
                    "status": accepted["status"],
                },
                "packet": {
                    "revision": packet["revision"],
                    "goal": packet["goal"],
                    "instructions": [
                        {"text": current[0]["text"], "source": current[0]["source"]}
                    ],
                    "open_work": packet["open_work"],
                },
                "verification": diagnosis["verification"],
            }
            report["transcript"] = transcript(report)
            return report
        finally:
            store.close()


def transcript(result: dict) -> list[dict[str, str]]:
    """The same transcript is printed in the terminal and replayed in the README."""
    change, handoff = result["correction"], result["handoff"]
    verification = (
        "not recorded" if not result["verification"]["stored"] else "recorded"
    )
    return [
        {"kind": "heading", "text": "01  Record the instruction"},
        {
            "kind": "output",
            "text": f"    {result['original']['text']}  /  revision {result['original']['revision']}",
        },
        {"kind": "blank", "text": ""},
        {"kind": "heading", "text": "02  Apply the correction"},
        {"kind": "removed", "text": f"  - {change['previous']}   superseded"},
        {"kind": "added", "text": f"  + {change['current']}  active"},
        {
            "kind": "output",
            "text": f"    source: {change['source']['kind']}:{change['source']['ref']}",
        },
        {"kind": "blank", "text": ""},
        {"kind": "heading", "text": "03  Hand off the current state"},
        {
            "kind": "output",
            "text": f"    {handoff['sender']} -> {handoff['recipient']}  /  revision {handoff['revision']}",
        },
        {"kind": "accepted", "text": f"    handoff: {handoff['status']}"},
        {
            "kind": "output",
            "text": f"    instruction: {result['packet']['instructions'][0]['text']}",
        },
        {"kind": "output", "text": f"    verification: {verification}"},
        {"kind": "blank", "text": ""},
        {"kind": "note", "text": "Local example. No model or network calls."},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the checked state and transcript as JSON",
    )
    args = parser.parse_args()
    result = run_demo()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("\n".join(line["text"] for line in result["transcript"]))


if __name__ == "__main__":
    main()
