"""Durable intent records for explicitly authorized local file effects."""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from .models import ConflictError, nonempty_string, timestamp
from .organization import AccessDenied, Principal
from .task_actions import check_file_action
from .task_service import MAX_ARTIFACT_BYTES, TaskService


class TaskEffectService:
    def __init__(self, tasks: TaskService):
        self.tasks = tasks
        self.store = tasks.store

    @staticmethod
    def _record(row) -> dict:
        return {key: row[key] for key in ("id", "task_id", "destination_key", "task_revision",
                                          "operation", "path", "precondition_digest",
                                          "desired_digest", "status", "created_at", "updated_at")}

    def prepare(self, principal: Principal, task_id: str, *, expected_revision: int,
                destination_key: str, operation: str, path: str,
                content: bytes | None = None,
                allowed_operations: set[str]) -> dict:
        """Persist an intent before an effect; no file bytes are stored in SQLite."""
        self.tasks._check(principal, "effect.prepare")
        if self.tasks.source_class != "host":
            raise AccessDenied("Only the host may prepare external effects.")
        nonempty_string(destination_key, "destination_key", limit=128)
        if operation == "write_file":
            if type(content) is not bytes or len(content) > MAX_ARTIFACT_BYTES:
                raise ValueError("write_file content must be bytes of at most 16 MiB.")
            desired_digest = "sha256:" + hashlib.sha256(content).hexdigest()
        elif operation == "delete_file":
            if content is not None:
                raise ValueError("delete_file does not accept content.")
            desired_digest = None
        else:
            raise ValueError("Unsupported effect operation.")
        checked = check_file_action(self.tasks, principal, task_id,
                                    expected_revision=expected_revision, operation=operation,
                                    path=path, allowed_operations=allowed_operations)
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            task = self.tasks._row(task_id, principal)
            if task["revision"] != expected_revision:
                raise ConflictError(expected_revision, task["revision"])
            existing = self.store._connection.execute(
                "SELECT * FROM task_effects WHERE task_id=? AND destination_key=?",
                (task_id, destination_key),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                if any(record[field] != expected for field, expected in (
                    ("task_revision", expected_revision), ("operation", operation),
                    ("path", checked["path"]),
                    ("precondition_digest", checked["precondition_digest"]),
                    ("desired_digest", desired_digest),
                )):
                    raise ValueError("destination_key was already used for a different effect.")
                return record
            now = timestamp()
            effect_id = uuid.uuid4().hex
            self.store._connection.execute(
                "INSERT INTO task_effects VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, ?)",
                (effect_id, task_id, destination_key, expected_revision, operation,
                 checked["path"], checked["precondition_digest"], desired_digest, now, now),
            )
            return self._record(self.store._connection.execute(
                "SELECT * FROM task_effects WHERE id=?", (effect_id,)
            ).fetchone())

    def inspect(self, principal: Principal, effect_id: str) -> dict:
        self.tasks._check(principal, "effect.read")
        with self.store._transaction():
            row = self.store._connection.execute(
                "SELECT e.* FROM task_effects e JOIN tasks t ON t.id=e.task_id "
                "WHERE e.id=? AND t.tenant=?", (effect_id, principal.tenant),
            ).fetchone()
            if row is None:
                raise LookupError("Effect not found.")
            return self._record(row)

    def execute(self, principal: Principal, effect_id: str, *, content: bytes | None = None) -> dict:
        """Claim in SQLite, perform one file effect outside its lock, then record outcome."""
        self.tasks._check(principal, "effect.execute")
        if self.tasks.source_class != "host":
            raise AccessDenied("Only the host may execute effects.")
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            effect = self.inspect(principal, effect_id)
            if effect["status"] == "applied":
                return effect
            if effect["status"] != "prepared":
                raise ValueError("Effect was already started; reconcile it before any retry.")
            task = self.tasks._row(effect["task_id"], principal)
            if task["revision"] != effect["task_revision"] or task["status"] != "active":
                raise ConflictError(effect["task_revision"], task["revision"])
            if effect["operation"] == "write_file":
                if type(content) is not bytes or "sha256:" + hashlib.sha256(content).hexdigest() != effect["desired_digest"]:
                    raise ValueError("Write content does not match the prepared digest.")
            elif content is not None:
                raise ValueError("Delete effects do not accept content.")
            self.store._connection.execute(
                "UPDATE task_effects SET status='running', updated_at=? WHERE id=?",
                (timestamp(), effect_id),
            )
        # No SQLite transaction is held while the filesystem callback runs.
        root = self.tasks.show(principal, effect["task_id"])["workspace"]["root"]
        try:
            _apply_file_effect(root, effect, content)
        except EffectPreconditionChanged:
            status = "rejected"
        except BaseException:
            status = "unresolved"
            raise
        else:
            status = "applied"
        finally:
            with self.store._transaction(write=True):
                self.store._connection.execute(
                    "UPDATE task_effects SET status=?, updated_at=? WHERE id=? AND status='running'",
                    (locals().get("status", "unresolved"), timestamp(), effect_id),
                )
        return self.inspect(principal, effect_id)


class EffectPreconditionChanged(RuntimeError):
    """The destination changed before execution; the callback did not run."""


def _apply_file_effect(root: str, effect: dict, content: bytes | None) -> None:
    base = Path(root).resolve(strict=True)
    target = (base / effect["path"]).resolve()
    if not target.is_relative_to(base) or target == base:
        raise EffectPreconditionChanged("Destination escaped the workspace.")
    actual = None
    if target.is_file():
        if target.stat().st_size > MAX_ARTIFACT_BYTES:
            raise EffectPreconditionChanged("Destination is too large.")
        actual = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    elif target.exists():
        raise EffectPreconditionChanged("Destination is not a regular file.")
    if actual != effect["precondition_digest"]:
        raise EffectPreconditionChanged("Destination bytes changed since preparation.")
    if effect["operation"] == "delete_file":
        target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".relay-effect-", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
