"""Immutable offers and explicit fresh acceptance of task context packets."""

from __future__ import annotations

import hashlib
import json
import uuid

from .models import ConflictError, encode, nonempty_string, timestamp
from .organization import AccessDenied, Principal
from .task_packet import compile_task_packet
from .task_service import TaskNotFound, TaskService


class HandoffNotFound(LookupError):
    """The handoff does not exist within the caller's tenant."""


def _digest(packet: dict) -> str:
    return "sha256:" + hashlib.sha256(encode(packet).encode("utf-8")).hexdigest()


class HandoffService:
    def __init__(self, tasks: TaskService):
        self.tasks = tasks
        self.store = tasks.store

    def _row(self, principal: Principal, handoff_id: str):
        row = self.store._connection.execute(
            "SELECT h.* FROM handoffs h JOIN tasks t ON t.id=h.task_id "
            "WHERE h.id=? AND t.tenant=?", (handoff_id, principal.tenant),
        ).fetchone()
        if row is None:
            raise HandoffNotFound("Handoff not found.")
        return row

    def _record(self, row) -> dict:
        return {"id": row["id"], "task_id": row["task_id"], "revision": row["revision"],
                "recipient": row["recipient"], "packet": json.loads(row["packet"]),
                "packet_digest": row["packet_digest"], "status": row["status"],
                "created_at": row["created_at"], "accepted_at": row["accepted_at"]}

    def offer(self, principal: Principal, task_id: str, *, recipient: str,
              max_chars: int = 16000) -> dict:
        self.tasks._check(principal, "handoff.offer")
        if self.tasks.source_class != "host":
            raise AccessDenied("Only a host may offer a handoff.")
        nonempty_string(recipient, "recipient", limit=200)
        task = self.tasks.show(principal, task_id)
        packet = compile_task_packet(task, max_chars=max_chars)
        now = timestamp()
        handoff_id = uuid.uuid4().hex
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            row = self.tasks._row(task_id, principal)
            if row["revision"] != task["revision"]:
                raise ConflictError(task["revision"], row["revision"])
            self.store._connection.execute(
                "INSERT INTO handoffs VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (handoff_id, task_id, task["revision"], recipient, encode(packet),
                 _digest(packet), "offered", now),
            )
        return self.inspect(principal, handoff_id)

    def inspect(self, principal: Principal, handoff_id: str) -> dict:
        self.tasks._check(principal, "handoff.read")
        nonempty_string(handoff_id, "handoff_id", limit=128)
        with self.store._transaction():
            record = self._record(self._row(principal, handoff_id))
        self.tasks._check(principal, "handoff.read")
        return record
