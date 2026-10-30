"""Host-authorized task state and revisioned local event history."""

from __future__ import annotations

import json
import uuid
from typing import Callable

from .models import encode, nonempty_string, timestamp
from .organization import AccessDenied, Principal
from .store import RelayStore
from .task_contract import SCHEMA_VERSION, validate_task_snapshot, validate_workspace


class TaskNotFound(LookupError):
    """A task does not exist or is outside the principal's tenant."""


class TaskService:
    """The host supplies identity and authorization; a model cannot self-attest either."""

    def __init__(self, store: RelayStore, *, authorize: Callable[[Principal, str], bool]):
        if not isinstance(store, RelayStore) or not callable(authorize):
            raise TypeError("TaskService requires a RelayStore and synchronous authorization callback.")
        self.store = store
        self.authorize = authorize

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
        nonempty_string(goal, "goal", limit=4096)
        now = timestamp()
        task = validate_task_snapshot({
            "schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "revision": 0,
            "status": "active", "goal": goal, "completion_criteria": completion_criteria,
            "workspace": validate_workspace(workspace), "instructions": [], "evidence": [],
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
                 "host", encode({"goal": goal}), now),
            )
        return task

    def show(self, principal: Principal, task_id: str) -> dict:
        self._check(principal, "task.read")
        nonempty_string(task_id, "task_id", limit=128)
        with self.store._transaction():
            task = json.loads(self._row(task_id, principal)["payload"])
        self._check(principal, "task.read")
        return validate_task_snapshot(task)
