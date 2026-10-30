"""Bind a command outcome to one task revision and exact local artifact bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .artifact_resolution import resolve_artifacts
from .command_receipt import run_command
from .models import encode
from .task_contract import validate_task_snapshot


def bind_command_receipt(snapshot: dict, receipt: dict, *, root: str) -> dict:
    task = validate_task_snapshot(snapshot)
    if type(receipt) is not dict or receipt.get("cwd") != str(Path(root).resolve()):
        raise ValueError("Command receipt must be captured in the recipient workspace root.")
    if not task["artifacts"]:
        raise ValueError("A verification requires at least one captured artifact.")
    references = resolve_artifacts({"artifacts": task["artifacts"]}, root=root)
    outcome = "verified" if (receipt.get("status") == "exited" and
                             receipt.get("exit_code") == 0 and
                             all(item["status"] == "ok" for item in references)) else "failed"
    digest = "sha256:" + hashlib.sha256(encode(receipt).encode("utf-8")).hexdigest()
    return {"task_id": task["id"], "task_revision": task["revision"],
            "receipt_digest": digest, "command_digest": receipt.get("command_digest"),
            "artifacts": task["artifacts"], "artifact_checks": references,
            "outcome": outcome}


def verify_task_criteria(snapshot: dict, *, root: str,
                         commands: dict[str, list[str]]) -> dict:
    """Run host-selected checks for every criterion; no model-reported result counts."""
    task = validate_task_snapshot(snapshot)
    criteria = task["completion_criteria"]
    if len(set(criteria)) != len(criteria):
        raise ValueError("Completion criteria must be unique for command verification.")
    if type(commands) is not dict or set(commands) != set(criteria):
        raise ValueError("Provide exactly one verification command for each completion criterion.")
    checks = []
    for criterion in criteria:
        receipt = run_command(commands[criterion], cwd=root)
        binding = bind_command_receipt(task, receipt, root=root)
        checks.append({"criterion": criterion, "receipt": receipt, "binding": binding})
    return {"task_id": task["id"], "task_revision": task["revision"],
            "passed": all(item["binding"]["outcome"] == "verified" for item in checks),
            "checks": checks}
