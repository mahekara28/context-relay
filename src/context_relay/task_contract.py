"""Dependency-free validation for the portable task snapshot contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .models import clone, validate_json


SCHEMA_VERSION = "task-context/v1alpha1"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


def _object(value: Any, name: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError(f"{name} has missing or unknown fields.")
    return value


def _string(value: Any, name: str, limit: int) -> None:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a nonempty string of at most {limit} characters.")


def _array(value: Any, name: str, limit: int, *, minimum: int = 0) -> list:
    if type(value) is not list or not minimum <= len(value) <= limit:
        raise ValueError(f"{name} must contain {minimum} to {limit} items.")
    return value


def _time(value: Any, name: str) -> None:
    if type(value) is not str:
        raise ValueError(f"{name} must be an ISO 8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO 8601 timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone.")


def validate_source(value: Any) -> dict:
    source = _object(value, "source", {"kind", "ref"})
    if source["kind"] not in ("user", "host", "tool", "file", "agent_report"):
        raise ValueError("source kind is unsupported.")
    _string(source["ref"], "source ref", 1024)
    return clone(source)


def validate_workspace(value: Any) -> dict:
    workspace = _object(value, "workspace", {"repository", "root"}, {"worktree", "head"})
    _string(workspace["repository"], "repository", 1024)
    _string(workspace["root"], "workspace root", 4096)
    for field, limit in (("worktree", 4096), ("head", 128)):
        if field in workspace and workspace[field] is not None:
            _string(workspace[field], field, limit)
    return clone(workspace)


def validate_task_snapshot(value: Any) -> dict:
    """Validate exactly the public v1alpha1 fields without a runtime JSON Schema dependency."""
    validate_json(value, name="task")
    fields = {"schema_version", "id", "revision", "status", "goal", "completion_criteria",
              "workspace", "instructions", "evidence", "artifacts", "open_work",
              "created_at", "updated_at"}
    task = _object(value, "task", fields)
    if task["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported task schema version.")
    _string(task["id"], "task id", 128)
    if type(task["revision"]) is not int or task["revision"] < 0:
        raise ValueError("task revision must be a nonnegative integer.")
    if task["status"] not in ("active", "archived"):
        raise ValueError("task status must be active or archived.")
    _string(task["goal"], "task goal", 4096)
    for criterion in _array(task["completion_criteria"], "completion criteria", 32, minimum=1):
        _string(criterion, "completion criterion", 1024)
    validate_workspace(task["workspace"])
    ids: set[str] = set()
    for instruction in _array(task["instructions"], "instructions", 128):
        _object(instruction, "instruction", {"id", "text", "source", "scope", "status"},
                {"supersedes", "expires_at_revision"})
        _string(instruction["id"], "instruction id", 128)
        if instruction["id"] in ids:
            raise ValueError("instruction ids must be unique.")
        ids.add(instruction["id"])
        _string(instruction["text"], "instruction text", 4096)
        validate_source(instruction["source"])
        if instruction["scope"] not in ("task", "workspace", "repository"):
            raise ValueError("instruction scope is unsupported.")
        if instruction["status"] not in ("active", "superseded", "expired", "conflicted"):
            raise ValueError("instruction status is unsupported.")
        supersedes = _array(instruction.get("supersedes", []), "supersedes", 128)
        for target in supersedes:
            _string(target, "superseded instruction id", 128)
        if len(set(supersedes)) != len(supersedes):
            raise ValueError("supersedes must not contain duplicates.")
        if "expires_at_revision" in instruction and (
            type(instruction["expires_at_revision"]) is not int or instruction["expires_at_revision"] < 1
        ):
            raise ValueError("expires_at_revision must be a positive integer.")
    evidence_ids: set[str] = set()
    for evidence in _array(task["evidence"], "evidence", 256):
        _object(evidence, "evidence", {"id", "claim", "source", "captured_at"}, {"digest"})
        _string(evidence["id"], "evidence id", 128)
        if evidence["id"] in evidence_ids:
            raise ValueError("evidence ids must be unique.")
        evidence_ids.add(evidence["id"])
        _string(evidence["claim"], "evidence claim", 4096)
        validate_source(evidence["source"])
        _time(evidence["captured_at"], "evidence captured_at")
        if "digest" in evidence and (type(evidence["digest"]) is not str or not _DIGEST.fullmatch(evidence["digest"])):
            raise ValueError("evidence digest must be sha256: followed by 64 lowercase hex characters.")
    paths: set[str] = set()
    for artifact in _array(task["artifacts"], "artifacts", 256):
        _object(artifact, "artifact", {"path", "digest", "size"})
        _string(artifact["path"], "artifact path", 4096)
        if artifact["path"] in paths:
            raise ValueError("artifact paths must be unique.")
        paths.add(artifact["path"])
        if type(artifact["digest"]) is not str or not _DIGEST.fullmatch(artifact["digest"]):
            raise ValueError("artifact digest must be sha256: followed by 64 lowercase hex characters.")
        if type(artifact["size"]) is not int or artifact["size"] < 0:
            raise ValueError("artifact size must be a nonnegative integer.")
    for item in _array(task["open_work"], "open work", 128):
        _string(item, "open work item", 1024)
    _time(task["created_at"], "created_at")
    _time(task["updated_at"], "updated_at")
    return clone(task)


@dataclass(frozen=True)
class TaskContext:
    """Validated immutable wrapper around one public task snapshot."""

    data: dict

    @classmethod
    def from_dict(cls, value: dict) -> TaskContext:
        return cls(validate_task_snapshot(value))

    def to_dict(self) -> dict:
        return clone(self.data)
