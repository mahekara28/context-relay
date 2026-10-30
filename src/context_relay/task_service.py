"""Host-authorized task state and revisioned local event history."""

from __future__ import annotations

import json
import hashlib
import subprocess
import uuid
from pathlib import Path
from typing import Callable

from .models import ConflictError, clone, encode, nonempty_string, timestamp, validate_revision
from .organization import AccessDenied, Principal
from .store import RelayStore
from .task_contract import SCHEMA_VERSION, validate_task_snapshot, validate_workspace


class TaskNotFound(LookupError):
    """A task does not exist or is outside the principal's tenant."""


class TaskRequestConflict(ValueError):
    """An idempotency key was reused for different request content."""


def inspect_git_workspace(root: str | Path) -> dict:
    """Capture a local worktree identity without contacting a remote server."""
    path = Path(root).resolve(strict=True)
    if not path.is_dir():
        raise ValueError("workspace root must be a directory.")

    def git(*arguments: str) -> str:
        try:
            result = subprocess.run(["git", "-C", str(path), *arguments],
                                    capture_output=True, text=True, timeout=5, check=True)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ValueError("workspace must be a readable Git worktree with an origin remote.") from error
        return result.stdout.strip()

    worktree = Path(git("rev-parse", "--show-toplevel")).resolve(strict=True)
    origin = git("remote", "get-url", "origin")
    head = git("rev-parse", "HEAD")
    if not origin or not head:
        raise ValueError("workspace origin and HEAD must be available.")
    return validate_workspace({"repository": origin, "root": str(path),
                               "worktree": str(worktree), "head": head})


class TaskService:
    """The host supplies identity and authorization; a model cannot self-attest either."""

    def __init__(self, store: RelayStore, *, authorize: Callable[[Principal, str], bool],
                 source_class: str = "host"):
        if not isinstance(store, RelayStore) or not callable(authorize):
            raise TypeError("TaskService requires a RelayStore and synchronous authorization callback.")
        if source_class not in ("host", "agent_report"):
            raise ValueError("source_class must be host or agent_report.")
        self.store = store
        self.authorize = authorize
        self.source_class = source_class

    def _check(self, principal: Principal, operation: str) -> None:
        if not isinstance(principal, Principal):
            raise TypeError("principal must be supplied by the host as a Principal.")
        if self.authorize(principal, operation) is not True:
            raise AccessDenied("Access denied.")

    def _row(self, task_id: str, principal: Principal):
        row = self.store._connection.execute(
            "SELECT * FROM tasks WHERE id = ? AND tenant = ?", (task_id, principal.tenant)
        ).fetchone()
        if row is None:
            raise TaskNotFound("Task not found.")
        return row

    def create(self, principal: Principal, *, goal: str, completion_criteria: list[str],
               workspace: dict, open_work: list[str] | None = None) -> dict:
        self._check(principal, "task.create")
        if self.source_class != "host":
            raise AccessDenied("Only the host may create a task.")
        nonempty_string(goal, "goal", limit=4096)
        workspace = validate_workspace(workspace)
        if not Path(workspace["root"]).is_absolute():
            raise ValueError("workspace root must be an absolute path.")
        if workspace.get("worktree") is not None and not Path(workspace["worktree"]).is_absolute():
            raise ValueError("workspace worktree must be an absolute path.")
        now = timestamp()
        task = validate_task_snapshot({
            "schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "revision": 0,
            "status": "active", "goal": goal, "completion_criteria": completion_criteria,
            "workspace": workspace, "instructions": [], "evidence": [],
            "artifacts": [], "open_work": [] if open_work is None else open_work,
            "created_at": now, "updated_at": now,
        })
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            self.store._connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task["id"], principal.tenant, 0, "active", encode(task), now, now),
            )
            self.store._connection.execute(
                "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, task["id"], 0, "create", principal.subject,
                 self.source_class, encode({"goal": goal}), now),
            )
        return task

    def show(self, principal: Principal, task_id: str) -> dict:
        self._check(principal, "task.read")
        nonempty_string(task_id, "task_id", limit=128)
        with self.store._transaction():
            task = json.loads(self._row(task_id, principal)["payload"])
        self._check(principal, "task.read")
        return validate_task_snapshot(task)

    def update(self, principal: Principal, task_id: str, *, expected_revision: int,
               request_id: str, changes: dict) -> dict:
        """Apply one bounded patch exactly once at a caller-observed revision."""
        self._check(principal, "task.update")
        nonempty_string(task_id, "task_id", limit=128)
        nonempty_string(request_id, "request_id", limit=128)
        validate_revision(expected_revision)
        if type(changes) is not dict or not changes or set(changes) - {"goal", "completion_criteria", "open_work"}:
            raise ValueError("changes must contain only goal, completion_criteria, or open_work.")
        if self.source_class == "agent_report" and set(changes) != {"open_work"}:
            raise AccessDenied("Agent reports may update only open work.")
        changes = clone(changes)
        fingerprint = hashlib.sha256(json.dumps(
            {"principal": principal.subject, "source_class": self.source_class,
             "expected_revision": expected_revision, "changes": changes},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            row = self._row(task_id, principal)
            previous = self.store._connection.execute(
                "SELECT request_digest, result_payload FROM task_mutations WHERE task_id=? AND request_id=?",
                (task_id, request_id),
            ).fetchone()
            if previous is not None:
                if previous["request_digest"] != fingerprint:
                    raise TaskRequestConflict("request_id was already used for a different task update.")
                return json.loads(previous["result_payload"])
            if row["revision"] != expected_revision:
                raise ConflictError(expected_revision, row["revision"])
            if row["status"] != "active":
                raise ValueError("Archived tasks must be reopened before updating.")
            task = json.loads(row["payload"])
            task.update(changes)
            task["revision"] += 1
            task["updated_at"] = timestamp()
            task = validate_task_snapshot(task)
            self.store._connection.execute(
                "UPDATE tasks SET revision=?, payload=?, updated_at=? WHERE id=?",
                (task["revision"], encode(task), task["updated_at"], task_id),
            )
            self.store._connection.execute(
                "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, task_id, task["revision"], "update", principal.subject,
                 self.source_class, encode(changes), task["updated_at"]),
            )
            self.store._connection.execute(
                "INSERT INTO task_mutations VALUES (?, ?, ?, ?)",
                (task_id, request_id, fingerprint, encode(task)),
            )
        return task
