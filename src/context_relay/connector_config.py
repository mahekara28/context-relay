"""Reversible project-local configuration edits with optimistic file checks."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class ConfigChanged(RuntimeError):
    """The config changed since the caller read it; no bytes were replaced."""


def file_state(path: str | Path) -> tuple[str | None, bytes | None]:
    target = Path(path)
    if target.is_symlink():
        raise ValueError("Refusing to edit a symlinked configuration file.")
    if not target.exists():
        return None, None
    if not target.is_file():
        raise ValueError("Configuration path is not a regular file.")
    data = target.read_bytes()
    return hashlib.sha256(data).hexdigest(), data


def replace_if_unchanged(path: str | Path, *, expected_digest: str | None,
                         content: bytes | None) -> str | None:
    """Atomically replace or remove exactly the file state that was inspected."""
    target = Path(path)
    actual, old_data = file_state(target)
    if actual != expected_digest:
        raise ConfigChanged("Configuration changed; inspect it before retrying.")
    if content is None:
        if old_data is not None:
            target.unlink()
        return None
    if type(content) is not bytes or len(content) > 1024 * 1024:
        raise ValueError("Configuration must be at most 1 MiB of bytes.")
    if old_data == content:
        return actual
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".relay-config-", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(stream.name, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck immediately before replacement; concurrent writers can still race
        # between this check and os.replace, so callers must serialize project edits.
        if file_state(target)[0] != expected_digest:
            raise ConfigChanged("Configuration changed; inspect it before retrying.")
        os.replace(temporary, target)
        return hashlib.sha256(content).hexdigest()
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
