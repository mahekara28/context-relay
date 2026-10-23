"""Opt-in, named local sessions without storing a random session ID in the host.

The selected directory belongs to the application. Path checks reject existing
symlinks; they do not sandbox another local process that can replace files.
"""
from __future__ import annotations

import hashlib
import stat
from dataclasses import replace
from pathlib import Path

from .config import RelayConfig
from .models import encode, nonempty_string
from .store import RelayStore
from ._storage_files import private_directory


def _name(value, label):
    nonempty_string(value, label, limit=200)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must contain valid UTF-8 text.") from exc
    if "\x00" in value:
        raise ValueError(f"{label} must not contain NUL characters.")


def named_storage_path(conversation: str, *, storage: str | Path | None = None,
                       namespace: str = "default") -> Path:
    """Return a stable, resolved database path without creating files.

    Names are exact application-owned labels, not paths or credentials. A
    namespace separates labels within this storage directory, not permissions.
    """
    _name(conversation, "conversation")
    _name(namespace, "namespace")
    if storage is None:
        storage = Path(".relay") / "sessions"
    elif not isinstance(storage, (str, Path)):
        raise TypeError("storage must be a directory path or None.")
    elif isinstance(storage, str) and (not storage.strip() or "\x00" in storage):
        raise ValueError("storage must be a nonempty directory path without NUL characters.")
    directory = Path(storage).resolve()
    digest = hashlib.sha256(encode([namespace, conversation]).encode("utf-8")).hexdigest()
    database = directory / (digest + ".sqlite3")
    if database.resolve().parent != directory:
        raise ValueError("Named database must stay within the selected directory.")
    for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
        # Sidecars disappear during ordinary SQLite shutdown. One lstat-style
        # observation avoids racing exists()/is_file() or resolving a removed WAL.
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError("Named database and journal paths must not be symlinks.")
        if not stat.S_ISREG(mode):
            raise ValueError("Named database and journal paths must be regular files.")
    return database


def named_connection_config(config: RelayConfig, conversation: str, *,
                            storage: str | Path | None = None,
                            namespace: str = "default") -> tuple[RelayConfig, str]:
    """Create/resume one canonical lineage; returned connection owns its store.

    Startup uses a short independent store lifetime. Root creation and lookup
    hold SQLite's write transaction, so simultaneous openers select one root.
    No history request is retried or crash claim released while reopening.
    """
    if not isinstance(config, RelayConfig):
        raise TypeError("config must be a RelayConfig.")
    if config.db_path != ":memory:":
        raise ValueError("Named conversations own db_path; configure storage instead.")
    database = named_storage_path(conversation, storage=storage, namespace=namespace)
    private_directory(database.parent)
    # Check again after directory creation; never follow an existing sidecar link.
    database = named_storage_path(conversation, storage=database.parent, namespace=namespace)
    with RelayStore(database, busy_timeout_ms=config.busy_timeout_ms) as store:
        with store._transaction(write=True):
            store._connection.execute("""CREATE TABLE IF NOT EXISTS named_connection (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                root_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE
            )""")
            row = store._connection.execute(
                "SELECT root_session_id FROM named_connection WHERE singleton = 1").fetchone()
            if row is None:
                root_id = store.create_session(config.name)["id"]
                store._connection.execute(
                    "INSERT INTO named_connection(singleton, root_session_id) VALUES (1, ?)", (root_id,))
            else:
                root_id = row["root_session_id"]
                store.get_session(root_id)
            tail = store._connection.execute(
                "SELECT session_id FROM conversation_segments WHERE root_id = ? ORDER BY ordinal DESC LIMIT 1",
                (root_id,)).fetchone()
            session_id = tail["session_id"] if tail is not None else root_id
    return replace(config, db_path=str(database)), session_id
