"""Receiver-side resolution of artifact references in a task packet."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .task_service import MAX_ARTIFACT_BYTES


def resolve_artifacts(packet: dict, *, root: str | Path) -> list[dict]:
    """Check bytes in an explicit recipient worktree; a sender path is never trusted."""
    if type(packet) is not dict or type(packet.get("artifacts")) is not list:
        raise ValueError("packet must contain an artifact list.")
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise ValueError("recipient root must be a directory.")
    results = []
    for item in packet["artifacts"]:
        if type(item) is not dict or type(item.get("path")) is not str or type(item.get("digest")) is not str:
            raise ValueError("artifact reference is malformed.")
        name = item["path"]
        candidate = (base / name).resolve()
        if Path(name).is_absolute() or not candidate.is_relative_to(base):
            status = "unsafe"
        elif not candidate.is_file():
            status = "missing"
        else:
            digest = hashlib.sha256()
            size = 0
            with candidate.open("rb") as stream:
                while block := stream.read(65536):
                    size += len(block)
                    if size > MAX_ARTIFACT_BYTES:
                        break
                    digest.update(block)
            if size > MAX_ARTIFACT_BYTES:
                status = "too_large"
            elif size != item.get("size") or "sha256:" + digest.hexdigest() != item["digest"]:
                status = "changed"
            else:
                status = "ok"
        results.append({"path": name, "status": status})
    return results
