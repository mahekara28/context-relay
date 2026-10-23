"""SQLite-backed event history and developer-controlled policies."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import ConflictError, PolicyError, clone, encode, nonempty_string, timestamp, validate_policy, validate_revision
from ._storage_files import permission_status, private_database


class StoreBusy(RuntimeError):
    """One bounded lock wait failed; transactions and actions are never replayed.

    Startup may retry only the journal-mode pragma within the configured wait.
    before_transaction describes this operation only. False means an executor
    may already have run; retrying a non-idempotent action can duplicate effects.
    """
    def __init__(self, timeout_ms: int, phase: str, *, before_transaction: bool):
        self.timeout_ms = timeout_ms
        self.phase = phase
        self.before_transaction = before_transaction
        super().__init__(f"Relay storage is busy during {phase} (lock wait: {timeout_ms} ms).")


def _busy(error):
    if not isinstance(error, sqlite3.OperationalError):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    if type(code) is int:
        return code & 255 in (5, 6)  # SQLITE_BUSY / SQLITE_LOCKED, including extended codes.
    # Python 3.10 lacks sqlite_errorcode. Match only SQLite's known lock messages.
    return str(error) in {"database is locked", "database table is locked", "database schema is locked"}


class RelayStore:
    """A local store. A single instance is safe to share between threads.

    SQLite serializes writes across instances/processes using the same database.
    Callbacks executed by Relay hold a write transaction and should be short.
    """

    def __init__(self, path: str | Path = ":memory:", *, busy_timeout_ms: int = 10000):
        if type(busy_timeout_ms) is not int or not 0 <= busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be an integer from 0 through 60000.")
        self.path = str(path)
        self.busy_timeout_ms = busy_timeout_ms
        self._lock = threading.RLock()
        self._depth = 0
        self._callback_active = False
        self._closed = False
        self._connection = None
        try:
            self._creation_protection = private_database(self.path)
            self._connection = sqlite3.connect(self.path, timeout=busy_timeout_ms / 1000,
                                               isolation_level=None, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            self._connection.execute("PRAGMA secure_delete = ON")
            self._connection.execute("PRAGMA temp_store = MEMORY")
            self._enable_wal()
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported database schema version {version}.")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, scenario TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    sequence INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(session_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS events_session ON events(session_id, sequence);
                CREATE TABLE IF NOT EXISTS facts (
                    session_id TEXT NOT NULL REFERENCES sessions(id), key TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(session_id, key)
                );
                CREATE TABLE IF NOT EXISTS policies (
                    session_id TEXT NOT NULL REFERENCES sessions(id), id TEXT NOT NULL,
                    tool TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(session_id, id), UNIQUE(session_id, tool)
                );
                CREATE TABLE IF NOT EXISTS checks (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS checks_session ON checks(session_id);
                CREATE TABLE IF NOT EXISTS automatic_memory (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
                    protocol TEXT NOT NULL,
                    max_chars INTEGER NOT NULL, history_chars INTEGER NOT NULL DEFAULT 2,
                    next_index INTEGER NOT NULL DEFAULT 0, pending_id TEXT, claim_token TEXT
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    session_id TEXT NOT NULL REFERENCES conversations(session_id),
                    sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS conversation_requests (
                    session_id TEXT NOT NULL REFERENCES conversations(session_id),
                    request_id TEXT NOT NULL, user_sequence INTEGER NOT NULL,
                    assistant_sequence INTEGER, PRIMARY KEY(session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_segments (
                    session_id TEXT PRIMARY KEY REFERENCES conversations(session_id),
                    root_id TEXT NOT NULL REFERENCES conversations(session_id),
                    ordinal INTEGER NOT NULL,
                    previous_session_id TEXT UNIQUE REFERENCES conversations(session_id),
                    UNIQUE(root_id, ordinal)
                );
                PRAGMA user_version = 1;
            """)
        except BaseException as error:
            if self._connection is not None:
                self._connection.close()
            self._closed = True
            if _busy(error):
                raise StoreBusy(busy_timeout_ms, "initialize", before_transaction=True) from error
            raise

    def _enable_wal(self) -> None:
        # SQLite may skip its busy handler when competing connections change
        # journal mode. This startup-only pragma is safe to retry; actions are not.
        deadline = time.monotonic() + self.busy_timeout_ms / 1000
        self._connection.execute("PRAGMA busy_timeout = 0")
        while True:
            try:
                self._connection.execute("PRAGMA journal_mode = WAL").close()
                break
            except sqlite3.OperationalError as error:
                remaining = deadline - time.monotonic()
                if not _busy(error) or remaining <= 0:
                    raise
                time.sleep(min(0.01, remaining))
                if time.monotonic() >= deadline:
                    raise
        # On failure the constructor closes the handle. Restore only on success
        # so cleanup cannot replace the original SQLite error.
        self._connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Bound each acquisition; this does not limit callback/transaction duration."""
        if not self._lock.acquire(timeout=self.busy_timeout_ms / 1000):
            raise StoreBusy(self.busy_timeout_ms, "thread_lock", before_transaction=True)
        try:
            yield
        finally:
            self._lock.release()

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[None]:
        with self._locked():
            if self._closed:
                raise ValueError("RelayStore is closed.")
            outer = self._depth == 0
            if outer:
                try:
                    self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                except sqlite3.OperationalError as error:
                    if _busy(error):
                        raise StoreBusy(self.busy_timeout_ms, "begin", before_transaction=True) from error
                    raise
            self._depth += 1
            phase = "statement"
            try:
                yield
                if outer:
                    phase = "commit"
                    self._connection.execute("COMMIT")
            except BaseException as error:
                if outer and self._connection.in_transaction:
                    try:
                        self._connection.execute("ROLLBACK")
                    except BaseException:
                        # Never return a handle with an unknown live transaction.
                        self._connection.close()
                        self._closed = True
                if isinstance(error, StoreBusy) and error.before_transaction:
                    raise StoreBusy(error.timeout_ms, phase, before_transaction=False) from error
                if _busy(error):
                    raise StoreBusy(self.busy_timeout_ms, phase, before_transaction=False) from error
                raise
            finally:
                self._depth -= 1

    def _assert_mutable(self) -> None:
        if self._callback_active:
            raise ValueError("Executor callbacks must not mutate their RelayStore.")

    def privacy_status(self) -> dict:
        """Describe local protections without exposing paths or stored contents.

        secure_delete overwrites deleted ordinary SQLite content on this
        connection. It is not encryption or guaranteed physical erasure.
        """
        with self._transaction():
            return {"storage": "memory" if self.path == ":memory:" else "sqlite_file",
                    "encrypted_by_relay": False, "process_memory_plaintext": True,
                    "persistent_plaintext": self.path != ":memory:",
                    "creation_protection": self._creation_protection,
                    "permissions": permission_status(self.path),
                    "secure_delete": self._connection.execute("PRAGMA secure_delete").fetchone()[0] == 1,
                    "temporary_storage": "memory" if self._connection.execute("PRAGMA temp_store").fetchone()[0] == 2 else "default_or_file",
                    "journal_mode": self._connection.execute("PRAGMA journal_mode").fetchone()[0],
                    "physical_erasure_guaranteed": False,
                    "external_copies_controlled": False,
                    "automatic_retention": False}

    def checkpoint(self, *, mode: str = "passive") -> dict:
        """Explicit WAL maintenance, never deleting/retrying request records.

        A busy result is reported, not retried. Even successful truncation does
        not erase backups, filesystem snapshots, SSD copies or provider data.
        """
        if type(mode) is not str or mode not in ("passive", "truncate"):
            raise ValueError("Checkpoint mode must be 'passive' or 'truncate'.")
        with self._locked():
            if self._closed:
                raise ValueError("RelayStore is closed.")
            self._assert_mutable()
            if self._depth or self._connection.in_transaction:
                raise ValueError("Checkpoint cannot run inside a transaction or callback.")
            try:
                result = self._connection.execute("PRAGMA wal_checkpoint(" + mode.upper() + ")").fetchone()
            except sqlite3.OperationalError as error:
                if _busy(error):
                    raise StoreBusy(self.busy_timeout_ms, "checkpoint", before_transaction=True) from error
                raise
            busy, frames, copied = tuple(result)
            return {"mode": mode, "busy": bool(busy), "wal_frames": frames, "checkpointed_frames": copied,
                    "wal_available": frames >= 0,
                    "complete": not busy and frames == copied,
                    "wal_truncated": mode == "truncate" and not busy and frames == copied == 0,
                    "logical_records_deleted": 0, "physical_erasure_guaranteed": False}

    def enable_history_index(self) -> bool:
        """Explicitly build and maintain an optional FTS5 archive index.

        Initial indexing is a maintenance write transaction over existing
        messages. Returns False only when SQLite lacks FTS5. Other storage
        errors propagate. Plain history reads never require this index.
        """
        with self._transaction(write=True):
            self._assert_mutable()
            exists = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'conversation_search'").fetchone()
            if exists:
                return True
            try:
                self._connection.execute("CREATE VIRTUAL TABLE conversation_search USING fts5(content)")
            except sqlite3.OperationalError as exc:
                if "no such module: fts5" in str(exc).lower():
                    return False
                raise
            self._connection.execute(
                "INSERT INTO conversation_search(rowid, content) "
                "SELECT rowid, json_extract(payload, '$.content') FROM conversation_messages")
            self._connection.execute("""CREATE TRIGGER conversation_search_insert
                AFTER INSERT ON conversation_messages BEGIN
                INSERT INTO conversation_search(rowid, content)
                VALUES (new.rowid, json_extract(new.payload, '$.content')); END""")
            self._connection.execute("""CREATE TRIGGER conversation_search_delete
                AFTER DELETE ON conversation_messages BEGIN
                DELETE FROM conversation_search WHERE rowid = old.rowid; END""")
            self._connection.execute("""CREATE TRIGGER conversation_search_update
                AFTER UPDATE ON conversation_messages BEGIN
                DELETE FROM conversation_search WHERE rowid = old.rowid;
                INSERT INTO conversation_search(rowid, content)
                VALUES (new.rowid, json_extract(new.payload, '$.content')); END""")
            return True

    @property
    def history_index_enabled(self) -> bool:
        with self._transaction():
            return self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'conversation_search'").fetchone() is not None

    def _session(self, session_id: str) -> dict:
        nonempty_string(session_id, "session_id")
        row = self._connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        return dict(row)

    def create_session(self, name: str, scenario: str = "custom") -> dict:
        nonempty_string(name, "name", limit=200)
        nonempty_string(scenario, "scenario", limit=100)
        with self._transaction(write=True):
            self._assert_mutable()
            session = {"id": str(uuid.uuid4()), "name": name, "scenario": scenario,
                       "revision": 0, "created_at": timestamp()}
            self._connection.execute(
                "INSERT INTO sessions(id, name, scenario, revision, created_at) VALUES (?, ?, ?, ?, ?)",
                tuple(session.values()),
            )
            return session

    def list_sessions(self) -> list[dict]:
        with self._transaction():
            return [dict(row) for row in self._connection.execute("SELECT * FROM sessions ORDER BY rowid DESC")]

    def get_session(self, session_id: str) -> dict:
        with self._transaction():
            return self._session(session_id)

    def _append_event(self, session_id: str, kind: str, payload: dict) -> dict:
        session = self._session(session_id)
        event = {"id": str(uuid.uuid4()), "sequence": session["revision"] + 1,
                 "kind": kind, **clone(payload), "created_at": timestamp()}
        self._connection.execute(
            "INSERT INTO events(id, session_id, sequence, kind, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event["id"], session_id, event["sequence"], kind, encode(event), event["created_at"]),
        )
        self._connection.execute("UPDATE sessions SET revision = ? WHERE id = ?", (event["sequence"], session_id))
        return event

    def record_fact(self, session_id: str, key: str, value, *, text: str = "", source: str = "user",
                    expected_revision: int | None = None) -> dict:
        nonempty_string(key, "key")
        nonempty_string(source, "source")
        if type(text) is not str:
            raise ValueError("text must be a string.")
        value = clone(value)
        if expected_revision is not None:
            validate_revision(expected_revision)
        with self._transaction(write=True):
            self._assert_mutable()
            session = self._session(session_id)
            if expected_revision is not None and expected_revision != session["revision"]:
                raise ConflictError(expected_revision, session["revision"])
            event = self._append_event(session_id, "fact", {"key": key, "value": value, "text": text, "source": source})
            fact = {"value": value, "event_id": event["id"], "sequence": event["sequence"], "source": source, "text": text}
            self._connection.execute(
                "INSERT INTO facts(session_id, key, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id, key) DO UPDATE SET payload = excluded.payload",
                (session_id, key, encode(fact)),
            )
            return event

    def add_policy(self, session_id: str, policy: dict) -> None:
        policy = validate_policy(policy)
        with self._transaction(write=True):
            self._assert_mutable()
            self._session(session_id)
            existing = self._connection.execute(
                "SELECT id FROM policies WHERE session_id = ? AND tool = ?", (session_id, policy["tool"])
            ).fetchone()
            if existing is not None and existing["id"] != policy["id"]:
                raise PolicyError(f"Tool {policy['tool']!r} already has policy {existing['id']!r}; update that policy explicitly.")
            self._connection.execute(
                "INSERT INTO policies(session_id, id, tool, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id, id) DO UPDATE SET tool = excluded.tool, payload = excluded.payload",
                (session_id, policy["id"], policy["tool"], encode(policy)),
            )
            self._append_event(session_id, "policy", {"policy": policy})

    def events(self, session_id: str) -> list[dict]:
        with self._transaction():
            self._session(session_id)
            return [json.loads(row[0]) for row in self._connection.execute(
                "SELECT payload FROM events WHERE session_id = ? ORDER BY sequence", (session_id,))]

    def facts(self, session_id: str) -> dict:
        with self._transaction():
            self._session(session_id)
            return {row["key"]: json.loads(row["payload"]) for row in self._connection.execute(
                "SELECT key, payload FROM facts WHERE session_id = ? ORDER BY key", (session_id,))}

    def policies(self, session_id: str) -> list[dict]:
        with self._transaction():
            self._session(session_id)
            return [json.loads(row[0]) for row in self._connection.execute(
                "SELECT payload FROM policies WHERE session_id = ? ORDER BY tool", (session_id,))]

    def _save_check(self, session_id: str, record: dict) -> None:
        self._assert_mutable()
        self._connection.execute("INSERT INTO checks(id, session_id, payload) VALUES (?, ?, ?)",
                                 (record["id"], session_id, encode(record)))

    def checks(self, session_id: str) -> list[dict]:
        with self._transaction():
            self._session(session_id)
            return [json.loads(row[0]) for row in self._connection.execute(
                "SELECT payload FROM checks WHERE session_id = ? ORDER BY rowid DESC", (session_id,))]

    def _memory_state(self, session_id: str) -> dict | None:
        """Read schema-free recall inside the caller's transaction.

        This table is deliberately separate from authoritative facts/policies.
        AutomaticMemory owns its format, validation, revision and event writes.
        """
        self._session(session_id)
        row = self._connection.execute(
            "SELECT payload FROM automatic_memory WHERE session_id = ?", (session_id,)
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def _save_memory_state(self, session_id: str, state: dict) -> None:
        self._assert_mutable()
        self._session(session_id)
        self._connection.execute(
            "INSERT INTO automatic_memory(session_id, payload) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET payload = excluded.payload",
            (session_id, encode(state)),
        )

    def close(self) -> None:
        with self._locked():
            if self._depth:
                raise ValueError("Cannot close a store during a transaction or executor callback.")
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> "RelayStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()
