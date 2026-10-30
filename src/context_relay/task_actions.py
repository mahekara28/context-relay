"""Host-scoped file action checks against a captured task revision."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import ConflictError, nonempty_string, validate_revision
from .organization import Principal
from .task_service import MAX_ARTIFACT_BYTES, TaskService


def check_file_action(tasks: TaskService, principal: Principal, task_id: str, *,
                      expected_revision: int, operation: str, path: str,
                      allowed_operations: set[str]) -> dict:
    """Check scope and current bytes; this is a proposal check, not execution."""
    validate_revision(expected_revision)
    nonempty_string(path, "path", limit=4096)
    if operation not in ("write_file", "delete_file"):
        raise ValueError("Unsupported file action.")
    if type(allowed_operations) is not set or operation not in allowed_operations:
        raise PermissionError("Host policy did not allow this file action.")
    task = tasks.show(principal, task_id)
    if task["revision"] != expected_revision:
        raise ConflictError(expected_revision, task["revision"])
    if task["status"] != "active":
        raise ValueError("Archived tasks cannot perform file actions.")
    root = Path(task["workspace"]["root"]).resolve(strict=True)
    candidate = (root / path).resolve()
    if Path(path).is_absolute() or not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Action path must stay inside the task workspace.")
    if candidate.exists() and not candidate.is_file():
        raise ValueError("Action target must be a file or a missing file.")
    if operation == "delete_file" and not candidate.is_file():
        raise ValueError("Delete target must exist.")
    digest = None
    if candidate.is_file():
        if candidate.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("Action target exceeds the 16 MiB limit.")
        digest = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    return {"task_id": task_id, "task_revision": expected_revision,
            "operation": operation, "path": candidate.relative_to(root).as_posix(),
            "precondition_digest": digest}
