"""Bind a command outcome to one task revision and exact local artifact bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .artifact_resolution import resolve_artifacts
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
