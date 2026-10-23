"""Opt-in, bounded, append-only text transcripts around a connected model.

SQLite transactions finish before inference. A persisted claim prevents implicit
replays after failure or restart; retry is always an explicit application action.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from contextlib import nullcontext
from pathlib import Path

from .connection import MemoryClarificationRequired
from .models import ConflictError, clone, encode, nonempty_string, validate_revision


_LIVE_LOCK = threading.RLock()
_LIVE: dict[tuple[str, str], str] = {}
_TASK_MAX_CHARS = 16000
_TASK_MAX_UPDATES = 32
_TASK_SCAN_LIMIT = 512
_TASK_CONTROL = re.compile(
    r"(?:ok(?:ay)?|thanks|thank you|got it|(?:please )?(?:continue|proceed|go on|keep going)"
    r"(?: with (?:the )?(?:remaining|rest of the) (?:work|task|comparison|analysis|checks?|steps?))?)[.! ]*",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class HistoryPolicy:
    """Opt-in rollover of completed text turns; never deletes old evidence."""

    rollover_after_chars: int = 100000

    def __post_init__(self):
        if type(self.rollover_after_chars) is not int or not 2 <= self.rollover_after_chars <= 20000000:
            raise ValueError("rollover_after_chars must be an integer from 2 to 20000000.")


class ConversationPending(RuntimeError):
    """A retained user request must be retried before sending another message."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        super().__init__(f"Request {request_id!r} is pending. Inspect pending and call retry() explicitly.")


class ConversationBusy(ConversationPending):
    """Inference is in flight, or a crashed process left an unresolved claim."""


class ConversationArchived(RuntimeError):
    """This segment is retained for evidence; continue in its successor session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Conversation is archived. Resume successor session {session_id!r}.")


class ConversationAlreadyCompleted(RuntimeError):
    """This request ID is already committed; the provider was not called again."""

    def __init__(self, request_id: str, assistant_message: dict):
        self.request_id = request_id
        self._assistant_message = clone(assistant_message)
        super().__init__(f"Request {request_id!r} is already complete; inspect assistant_message.")

    @property
    def assistant_message(self) -> dict:
        return clone(self._assistant_message)


class ConversationLimitExceeded(ValueError):
    """The original transcript would exceed its configured storage bound."""

    def __init__(self, size_chars: int, max_chars: int, *, response=None):
        self.size_chars, self.max_chars, self.response = size_chars, max_chars, response
        super().__init__(f"Conversation needs {size_chars} serialized characters; limit is {max_chars}.")


class TaskContextLimitExceeded(ValueError):
    """An open task cannot be carried without exceeding its explicit bounds."""

    def __init__(self, *, size_chars=None, updates=None, scan_limit=False):
        self.size_chars, self.updates = size_chars, updates
        self.max_chars, self.max_updates = _TASK_MAX_CHARS, _TASK_MAX_UPDATES
        self.scan_limit = scan_limit
        super().__init__("Task context exceeds its bounded carryover. Use update_task() to consolidate "
                         "the current task, or finish_task() after it is actually complete.")


class ConversationResponseError(TypeError):
    """A complete response could not be decoded as plain assistant text.

    The original response is available for host inspection/cleanup. Relay does
    not consume streams. The user request remains pending, without an automatic
    second provider call.
    """

    def __init__(self, response, request_id: str):
        self.response, self.request_id = response, request_id
        super().__init__("Conversation needs complete plain assistant text; supply a synchronous decoder.")


def _text_response(response) -> str:
    if type(response) is str:
        return response
    if type(response) is dict:
        message = response.get("message")
        if (type(message) is dict and set(message) == {"role", "content"}
                and message["role"] == "assistant"):
            return message["content"]
    raise TypeError("The default decoder accepts a string or a plain assistant message envelope.")


def _validate_kwargs(kwargs: dict) -> None:
    if "stream" in kwargs and kwargs["stream"] is not False:
        raise ValueError("Managed conversations need stream=False and a complete response.")
    unsupported = {"messages", "tools", "tool_choice", "functions", "function_call"} & kwargs.keys()
    if unsupported:
        raise ValueError(f"Managed text conversations do not accept: {', '.join(sorted(unsupported))}.")


class _Conversation:
    _async = False

    def __init__(self, agent, *, system: str | None = None, decoder=None,
                 max_history_chars: int | None = None, history_policy: HistoryPolicy | None = None):
        from .connection import AsyncConnectedAgent, ConnectedAgent
        expected = AsyncConnectedAgent if self._async else ConnectedAgent
        if not isinstance(agent, expected):
            raise TypeError(f"Use {'AsyncConversation' if isinstance(agent, AsyncConnectedAgent) else 'Conversation'} with its matching connected agent.")
        agent._require_open()
        if history_policy is not None and not isinstance(history_policy, HistoryPolicy):
            raise TypeError("history_policy must be a HistoryPolicy or None.")
        if history_policy is not None and (agent.guard.store.path == ":memory:" or agent.config.pinned):
            raise ValueError("Automatic rollover needs a persistent database and no pinned message indices.")
        if system is not None:
            nonempty_string(system, "system", limit=20000000)
        selected = decoder if decoder is not None else getattr(agent, "_response_decoder", None)
        selected = _text_response if selected is None else selected
        if (not callable(selected) or any(inspect.iscoroutinefunction(item) or inspect.isasyncgenfunction(item)
                                         or inspect.isgeneratorfunction(item)
                                         for item in (selected, getattr(selected, "__call__", None)))):
            raise TypeError("decoder must be a synchronous callable returning plain text.")
        ceiling = agent.memory.policy.max_history_chars if agent.memory is not None else 20000000
        if ceiling < 2:
            raise ValueError("Managed conversations need a memory history limit of at least 2 characters.")
        if max_history_chars is not None and (type(max_history_chars) is not int
                                              or not 2 <= max_history_chars <= ceiling):
            raise ValueError(f"max_history_chars must be an integer from 2 to {ceiling}.")
        self.agent, self._decoder = agent, selected
        self._owner_agent, self.history_policy = agent, history_policy
        self._run_lock = threading.Lock()
        self._store = agent.guard.store
        database = (f"memory:{id(self._store)}" if self._store.path == ":memory:"
                    else os.path.normcase(str(Path(self._store.path).resolve())))
        self._key = (database, agent.session_id)
        with self._store._transaction(write=True):
            self._store._assert_mutable()
            row = self._row()
            if row is None:
                memory_state = self._store._memory_state(self.session_id)
                if memory_state is not None and memory_state["processed_count"]:
                    raise ValueError("Managed conversations need a new session; this session already consumed application-owned history.")
                limit = max_history_chars if max_history_chars is not None else min(2000000, ceiling)
                self._store._connection.execute(
                    "INSERT INTO conversations(session_id, protocol, max_chars) VALUES (?, ?, ?)",
                    (self.session_id, agent.config.protocol, limit))
                if system is not None:
                    self._append({"role": "system", "content": system})
            else:
                if row["protocol"] != agent.config.protocol:
                    raise ValueError("A resumed conversation must keep its original protocol.")
                if row["max_chars"] > ceiling:
                    raise ValueError("Stored conversation limit exceeds the current memory policy limit.")
                if max_history_chars is not None and row["max_chars"] != max_history_chars:
                    raise ValueError("A resumed conversation keeps its original max_history_chars.")
                if system is not None and self._message(0) != {"role": "system", "content": system}:
                    raise ValueError("A resumed conversation must keep its original system message.")
            self._store._connection.execute(
                "INSERT OR IGNORE INTO conversation_segments(session_id, root_id, ordinal) VALUES (?, ?, 0)",
                (self.session_id, self.session_id))
            self._store._connection.execute("""CREATE TABLE IF NOT EXISTS conversation_tasks (
                root_id TEXT PRIMARY KEY REFERENCES conversations(session_id) ON DELETE CASCADE,
                payload TEXT NOT NULL
            )""")
            if history_policy is not None and history_policy.rollover_after_chars > self._row()["max_chars"]:
                raise ValueError("rollover_after_chars cannot exceed max_history_chars.")

    def _advance(self, text, request_id, kwargs):
        """Roll only before a new request, keeping old guards session-bound."""
        if self.history_policy is None or text is None:
            return
        self._owner_agent._require_open()
        _validate_kwargs(kwargs)
        nonempty_string(text, "text", limit=20000000)
        if request_id is not None:
            nonempty_string(request_id, "request_id", limit=128)
        if self.agent.memory is not None and len(text) > self.agent.memory.policy.max_message_chars:
            raise ValueError("text exceeds the configured memory max_message_chars.")
        with _LIVE_LOCK, self._store._transaction(write=True):
            row, segment = self._row(), self._segment()
            if request_id is not None and self._store._connection.execute(
                    "SELECT 1 FROM conversation_segments s JOIN conversation_requests r "
                    "ON r.session_id = s.session_id WHERE s.root_id = ? AND s.ordinal <= ? AND r.request_id = ?",
                    (segment["root_id"], segment["ordinal"], request_id)).fetchone():
                return  # _start preserves the original deduplication/error contract.
            if row["pending_id"] is not None or row["claim_token"] is not None or self._key in _LIVE:
                return  # _start exposes pending/busy; never archive an incomplete turn.
            prefix = self._message(0)
            prefix = [prefix] if prefix is not None and prefix["role"] == "system" else []
            if row["next_index"] <= len(prefix):
                return  # Do not create endless empty segments for an oversized turn.
            projected = row["history_chars"] + len(encode({"role": "user", "content": text})) + 1
            if row["history_chars"] < self.history_policy.rollover_after_chars and projected <= row["max_chars"]:
                return
            if self._store.policies(self.session_id):
                raise ValueError("Automatic rollover cannot carry tool permissions; use explicit rollover and review policies.")
            fresh_size = len(encode(prefix + [{"role": "user", "content": text}]))
            if fresh_size > row["max_chars"]:
                raise ConversationLimitExceeded(fresh_size, row["max_chars"])
            successor = self.rollover(expected_revision=self._store._session(self.session_id)["revision"])
        # A separate guard preserves all previously prepared turns' identity.
        # A reconnect failure leaves a recoverable archived->successor link.
        replacement = self._owner_agent._conversation_successor(self.agent, successor)
        previous = self.agent
        self.agent, self._store = replacement, replacement.guard.store
        self._key = (self._key[0], successor)
        if previous is not self._owner_agent:
            self._owner_agent._conversation_children.discard(previous)
            previous.close()

    def close(self):
        """Close automatically created successors; leave the caller's agent open."""
        if self.agent is not self._owner_agent:
            self._owner_agent._conversation_children.discard(self.agent)
            self.agent.close()

    def __enter__(self):
        self.agent._require_open()
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def session_id(self) -> str:
        return self.agent.session_id

    def _row(self):
        return self._store._connection.execute(
            "SELECT * FROM conversations WHERE session_id = ?", (self.session_id,)).fetchone()

    def _message(self, index: int, session_id: str | None = None) -> dict | None:
        row = self._store._connection.execute(
            "SELECT payload FROM conversation_messages WHERE session_id = ? AND sequence = ?",
            (self.session_id if session_id is None else session_id, index)).fetchone()
        return None if row is None else json.loads(row[0])

    def _history(self) -> list[dict]:
        return [json.loads(row[0]) for row in self._store._connection.execute(
            "SELECT payload FROM conversation_messages WHERE session_id = ? ORDER BY sequence",
            (self.session_id,))]

    @property
    def history(self) -> list[dict]:
        """A fresh copy of full originals, including a retained pending user."""
        self.agent._require_open()
        with self._store._transaction():
            self._store._session(self.session_id)
            return self._history()

    @property
    def pending(self) -> dict | None:
        self.agent._require_open()
        with self._store._transaction():
            self._store._session(self.session_id)
            row = self._row()
            if row["pending_id"] is None:
                return None
            return {"request_id": row["pending_id"], "in_flight": row["claim_token"] is not None,
                    "message": self._message(row["next_index"] - 1)}

    @property
    def max_history_chars(self) -> int:
        with self._store._transaction():
            return self._row()["max_chars"]

    def _segment(self):
        row = self._store._connection.execute(
            "SELECT * FROM conversation_segments WHERE session_id = ?", (self.session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown managed conversation: {self.session_id}")
        return row

    @property
    def next_session_id(self) -> str | None:
        """The next segment, if this managed conversation was explicitly archived."""
        self.agent._require_open()
        with self._store._transaction():
            row = self._store._connection.execute(
                "SELECT session_id FROM conversation_segments WHERE previous_session_id = ?",
                (self.session_id,)).fetchone()
            return None if row is None else row[0]

    def _task_record(self):
        row = self._store._connection.execute(
            "SELECT payload FROM conversation_tasks WHERE root_id = ?", (self._segment()["root_id"],)).fetchone()
        return None if row is None else json.loads(row["payload"])

    @staticmethod
    def _task_empty():
        return {"schema": 1, "task_id": None, "status": "idle", "requests": [],
                "completion_inferred": False, "assistant_outputs_included": False}

    @staticmethod
    def _task_validate(task):
        size, count = len(encode(task)), len(task["requests"])
        if size > _TASK_MAX_CHARS or count > _TASK_MAX_UPDATES:
            raise TaskContextLimitExceeded(size_chars=size, updates=count)

    def _task_store(self, task):
        self._task_validate(task)
        self._store._connection.execute(
            "INSERT INTO conversation_tasks(root_id, payload) VALUES (?, ?) "
            "ON CONFLICT(root_id) DO UPDATE SET payload = excluded.payload",
            (self._segment()["root_id"], encode(task)))

    def _task_append(self, task, text, source):
        # Pure acknowledgements/continuations neither finish nor replace a task.
        # Other user text is exact evidence, never promoted to trusted facts.
        if _TASK_CONTROL.fullmatch(text.strip()):
            return False
        if task["status"] != "open":
            task.update(task_id=str(uuid.uuid4()), status="open", requests=[])
        if task["requests"] and task["requests"][-1]["content"] == text:
            return False  # Only consecutive duplicates; later reversions matter.
        task["requests"].append({"role": "user", "content": text, "source": source})
        self._task_validate(task)
        return True

    def _ensure_task(self):
        task = self._task_record()
        if task is not None:
            return task
        task = self._task_empty()
        segment = self._segment()
        rows = self._store._connection.execute(
            "SELECT s.session_id, m.sequence, m.payload, r.request_id FROM conversation_segments s "
            "JOIN conversation_messages m ON m.session_id = s.session_id "
            "LEFT JOIN conversation_requests r ON r.session_id = s.session_id AND r.user_sequence = m.sequence "
            "WHERE s.root_id = ? AND s.ordinal <= ? AND json_extract(m.payload, '$.role') = 'user' "
            "ORDER BY s.ordinal, m.sequence LIMIT ?",
            (segment["root_id"], segment["ordinal"], _TASK_SCAN_LIMIT + 1))
        for index, row in enumerate(rows):
            if index == _TASK_SCAN_LIMIT:
                raise TaskContextLimitExceeded(scan_limit=True)
            self._task_append(task, json.loads(row["payload"])["content"], {
                "kind": "message", "session_id": row["session_id"],
                "message_index": row["sequence"], "request_id": row["request_id"],
            })
        self._task_store(task)
        return task

    def task_snapshot(self):
        """Read the one open task track and revision for explicit host updates.

        Automatic capture preserves exact user requests, not inferred completion
        or assistant progress. Acknowledgements do not complete a task. Task
        tracking starts with automatic rollover, explicit rollover, or update_task.
        """
        self.agent._require_open()
        with self._store._transaction():
            if self.next_session_id is not None:
                raise ConversationArchived(self.next_session_id)
            return {"task": self._task_record(), "revision": self._store._session(self.session_id)["revision"],
                    "max_chars": _TASK_MAX_CHARS, "max_updates": _TASK_MAX_UPDATES}

    def _task_editable(self, expected_revision, *, allow_pending):
        validate_revision(expected_revision)
        self._store._assert_mutable()
        session, row = self._store._session(self.session_id), self._row()
        if session["revision"] != expected_revision:
            raise ConflictError(expected_revision, session["revision"])
        if self.next_session_id is not None:
            raise ConversationArchived(self.next_session_id)
        if self._key in _LIVE or row["claim_token"] is not None:
            raise ConversationBusy(row["pending_id"])
        if not allow_pending and row["pending_id"] is not None:
            raise ConversationPending(row["pending_id"])

    def update_task(self, text: str, *, expected_revision: int, replace: bool = True):
        """Explicitly replace/consolidate task context, or append a host update.

        This is application-maintained task data, never a permission or trusted
        fact. Consolidation can repair an over-budget pending request before the
        host explicitly retries it; edits during inference are refused.
        """
        self.agent._require_open()
        nonempty_string(text, "task text", limit=_TASK_MAX_CHARS)
        if type(replace) is not bool:
            raise TypeError("replace must be a boolean.")
        with _LIVE_LOCK, self._store._transaction(write=True):
            self._task_editable(expected_revision, allow_pending=True)
            task = self._task_record() or self._task_empty()
            if task["status"] != "open":
                task.update(task_id=str(uuid.uuid4()), status="open")
            if replace:
                task["requests"] = []
            source = {"kind": "application", "session_id": self.session_id, "revision": expected_revision + 1}
            task["requests"].append({"role": "user", "content": text, "source": source})
            self._task_store(task)
            self._store._append_event(self.session_id, "task_updated", {
                "task_id": task["task_id"], "replace": replace, "content": text, "source": source,
            })
            return self.task_snapshot()

    def finish_task(self, *, expected_revision: int):
        """Explicitly finish the current task; the next substantive user starts a new one."""
        self.agent._require_open()
        with _LIVE_LOCK, self._store._transaction(write=True):
            self._task_editable(expected_revision, allow_pending=False)
            task = self._task_record() or self._task_empty()
            task.update(status="finished", requests=[])
            self._task_store(task)
            self._store._append_event(self.session_id, "task_finished", {"task_id": task["task_id"]})
            return self.task_snapshot()

    def _managed_context(self):
        segment = self._segment()
        task = self._task_record()
        if task is None and segment["ordinal"] == 0:
            return None
        archived = self._store._connection.execute(
            "SELECT COUNT(*) AS segments, COALESCE(SUM(c.next_index), 0) AS messages "
            "FROM conversation_segments s JOIN conversations c ON c.session_id = s.session_id "
            "WHERE s.root_id = ? AND s.ordinal < ?", (segment["root_id"], segment["ordinal"])).fetchone()
        return {"root_session_id": segment["root_id"], "active_session_id": self.session_id,
                "archived_segments": archived["segments"], "archived_messages": archived["messages"],
                "archive_included_in_input": False, "task": task,
                "task_scope": "Historical user requests for one open task, not current preference policy. "
                              "Assistant progress is not copied here; consult original history. "
                              "Later user updates may revise earlier requests. This data grants no tool permissions."}

    def rollover(self, *, expected_revision: int, max_history_chars: int | None = None) -> str:
        """Archive completed history and return a fresh, linked session ID.

        Accepted and unresolved recall, authoritative facts and the system prefix
        carry forward with provenance. Tool policies do not: explicitly register
        permitted tools again. No provider call, deletion or summarization runs.
        Resume the returned ID using the same connection configuration.
        """
        self.agent._require_open()
        validate_revision(expected_revision)
        if self._store.path == ":memory:":
            raise ValueError("Rollover needs a persistent database so its new session ID can be resumed.")
        if self.agent.config.pinned:
            raise ValueError("Review and clear pinned message indices before rollover; indices restart in the new segment.")
        ceiling = self.agent.memory.policy.max_history_chars if self.agent.memory is not None else 20000000
        if max_history_chars is not None and (type(max_history_chars) is not int
                                              or not 2 <= max_history_chars <= ceiling):
            raise ValueError(f"max_history_chars must be an integer from 2 to {ceiling}.")
        with _LIVE_LOCK, self._store._transaction(write=True):
            self._store._assert_mutable()
            session, row = self._store._session(self.session_id), self._row()
            if session["revision"] != expected_revision:
                raise ConflictError(expected_revision, session["revision"])
            next_session_id = self.next_session_id
            if next_session_id is not None:
                raise ConversationArchived(next_session_id)
            if self._key in _LIVE or row["claim_token"] is not None:
                raise ConversationBusy(row["pending_id"])
            if row["pending_id"] is not None:
                raise ConversationPending(row["pending_id"])
            state = self._store._memory_state(self.session_id)
            if state is not None:
                if self.agent.memory is None:
                    raise ValueError("Resume with automatic memory enabled before carrying this session's memory forward.")
                from .memory import _digest
                history, count = self._history(), state["processed_count"]
                if (state["protocol"] not in (None, self.agent.config.protocol)
                        or len(history) < count or _digest(history[:count]) != state["prefix_hash"]):
                    from .memory import MemoryHistoryError
                    raise MemoryHistoryError("Stored memory does not match this managed transcript; rollover cannot reset an unrelated cursor.")

                def retain_origin(value):
                    if type(value) is dict:
                        source = value.get("source")
                        if type(source) is dict and "history_id" in source:
                            source.setdefault("session_id", self.session_id)
                        for child in value.values():
                            retain_origin(child)
                    elif type(value) is list:
                        for child in value:
                            retain_origin(child)

                retain_origin(state)
                if "turn_scope" in state:
                    state["previous_turn_scope"] = state.pop("turn_scope")
                state.update(protocol=None, processed_count=0, prefix_hash=_digest([]))
                self.agent.memory._check_budget(state)
            task = self._ensure_task()
            segment = self._segment()
            successor = self._store.create_session(session["name"], session["scenario"])["id"]
            limit = row["max_chars"] if max_history_chars is None else max_history_chars
            if limit > ceiling:
                raise ValueError("New history limit exceeds the current memory policy limit.")
            prefix = self._message(0)
            prefix = [prefix] if prefix is not None and prefix["role"] == "system" else []
            size = len(encode(prefix))
            if size > limit:
                raise ConversationLimitExceeded(size, limit)
            self._store._connection.execute(
                "INSERT INTO conversations(session_id, protocol, max_chars, history_chars, next_index) VALUES (?, ?, ?, ?, ?)",
                (successor, row["protocol"], limit, size, len(prefix)))
            if prefix:
                self._store._connection.execute(
                    "INSERT INTO conversation_messages(session_id, sequence, payload) VALUES (?, 0, ?)",
                    (successor, encode(prefix[0])))
            self._store._connection.execute(
                "INSERT INTO conversation_segments(session_id, root_id, ordinal, previous_session_id) VALUES (?, ?, ?, ?)",
                (successor, segment["root_id"], segment["ordinal"] + 1, self.session_id))
            facts = self._store.facts(self.session_id)
            for key, fact in facts.items():
                self._store.record_fact(successor, key, fact["value"], text=fact["text"], source=fact["source"])
            if state is not None:
                self._store._save_memory_state(successor, state)
            self._store._append_event(successor, "conversation_rollover", {
                "previous_session_id": self.session_id, "source_revision": expected_revision,
                "fact_sources": {key: fact["event_id"] for key, fact in facts.items()},
                "memory_carried": state is not None, "copied_tool_policies": False,
                "task_carried": task["status"] == "open", "task_id": task["task_id"],
                "archived_tool_policy_count": len(self._store.policies(self.session_id)),
            })
            self._store._append_event(self.session_id, "conversation_archived", {"next_session_id": successor})
            return successor

    def history_page(self, *, after: tuple[int, int] | list[int] | None = None,
                     limit: int = 50, max_chars: int = 100000) -> dict:
        """Read exact originals from this segment and its linked ancestors.

        next_cursor is an opaque pair suitable for the next call's after value.
        Limits bound rows and serialized returned messages, not database size.
        """
        return self._page(after=after, limit=limit, max_chars=max_chars)

    def search_history(self, query: str, *, after: tuple[int, int] | list[int] | None = None,
                       limit: int = 20, scan_limit: int = 1000, max_chars: int = 100000,
                       mode: str = "literal") -> dict:
        """Bounded literal, case-insensitive text search, without embeddings.

        Follow next_cursor even when no messages matched: each call scans at
        most scan_limit rows. Returned matches are original, untrusted evidence.
        """
        nonempty_string(query, "query", limit=1000)
        if type(scan_limit) is not int or not 1 <= scan_limit <= 10000:
            raise ValueError("scan_limit must be an integer from 1 to 10000.")
        if mode not in ("literal", "terms"):
            raise ValueError("mode must be 'literal' or 'terms'.")
        if mode == "terms":
            terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
            if not terms:
                raise ValueError("Indexed search needs at least one word or number.")
            if not self._store.history_index_enabled:
                raise ValueError("Enable the archive index first, or use mode='literal'.")
            return self._page(after=after, limit=limit, max_chars=max_chars, scan_limit=scan_limit,
                              indexed_query=" AND ".join('"' + word + '"' for word in terms))
        return self._page(after=after, limit=limit, max_chars=max_chars,
                          query=query.casefold(), scan_limit=scan_limit)

    def _page(self, *, after, limit: int, max_chars: int, query=None, scan_limit=None,
              indexed_query=None) -> dict:
        self.agent._require_open()
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000.")
        if type(max_chars) is not int or not 2 <= max_chars <= 20000000:
            raise ValueError("max_chars must be an integer from 2 to 20000000.")
        if after is not None and (type(after) not in (tuple, list) or len(after) != 2
                                  or any(type(item) is not int or item < 0 for item in after)):
            raise ValueError("after must be a returned cursor pair of nonnegative integers.")
        ordinal, index = (-1, -1) if after is None else after
        with self._store._transaction():
            segment = self._segment()
            if ordinal > segment["ordinal"]:
                raise ValueError("Cursor points beyond this conversation segment.")
            source = ("conversation_segments s JOIN conversation_messages m ON m.session_id = s.session_id"
                      if indexed_query is None else
                      "conversation_search JOIN conversation_messages m ON m.rowid = conversation_search.rowid "
                      "JOIN conversation_segments s ON s.session_id = m.session_id")
            parameters = (segment["root_id"], segment["ordinal"], ordinal, ordinal, index, (scan_limit or limit) + 1)
            if indexed_query is not None:
                parameters = (indexed_query, *parameters)
            rows = self._store._connection.execute(
                "SELECT s.ordinal, m.session_id, m.sequence, m.payload FROM " + source + " WHERE "
                + ("conversation_search MATCH ? AND " if indexed_query is not None else "")
                + "s.root_id = ? AND s.ordinal <= ? AND (s.ordinal > ? OR (s.ordinal = ? AND m.sequence > ?)) "
                "ORDER BY s.ordinal, m.sequence LIMIT ?", parameters)
            result, size, scanned, cursor, more = [], 2, 0, None, False
            for row in rows:
                if scanned >= (scan_limit or limit) or len(result) == limit:
                    more = True
                    break
                message = json.loads(row["payload"])
                match = query is None or query in message["content"].casefold()
                if match:
                    item = {"session_id": row["session_id"], "index": row["sequence"], "message": message}
                    cost = len(encode(item)) + (1 if result else 0)
                    if size + cost > max_chars:
                        if not result:
                            raise ConversationLimitExceeded(size + cost, max_chars)
                        more = True
                        break
                    result.append(item)
                    size += cost
                scanned += 1
                cursor = [row["ordinal"], row["sequence"]]
            return {"messages": result, "next_cursor": cursor if more else None, "scanned": scanned}

    def _deletion_plan(self):
        root = self._segment()["root_id"]
        sessions = [dict(row) for row in self._store._connection.execute(
            "SELECT s.session_id, s.ordinal, a.created_at, a.revision, c.next_index, c.history_chars, "
            "c.pending_id, c.claim_token, (SELECT COUNT(*) FROM checks k WHERE k.session_id = s.session_id) AS checks_count "
            "FROM conversation_segments s "
            "JOIN sessions a ON a.id = s.session_id JOIN conversations c ON c.session_id = s.session_id "
            "WHERE s.root_id = ? ORDER BY s.ordinal", (root,))]
        return {"root_id": root, "sessions": sessions}

    def deletion_plan(self) -> dict:
        """Preview the entire linked lineage, including successors of an old segment."""
        self.agent._require_open()
        with self._store._transaction():
            return self._deletion_plan()

    def delete_history(self, *, expected: dict) -> dict:
        """Explicit logical deletion of a completed lineage and all its stored payloads.

        Requires an unchanged deletion_plan(). Pending/in-flight requests block
        deletion. This cannot erase backups, SQLite free pages/WAL bytes or data
        already sent to a provider. Old guards subsequently fail on an unknown
        session. There is no automatic age-based deletion or partial pruning.
        """
        self.agent._require_open()
        if type(expected) is not dict:
            raise TypeError("expected must be the dictionary returned by deletion_plan().")
        expected = clone(expected)
        with _LIVE_LOCK, self._store._transaction(write=True):
            self._store._assert_mutable()
            actual = self._deletion_plan()
            if expected != actual:
                raise ValueError("Conversation changed since the deletion plan; obtain and review a fresh plan.")
            for session in actual["sessions"]:
                key = (self._key[0], session["session_id"])
                if session["claim_token"] is not None or key in _LIVE:
                    raise ConversationBusy(session["pending_id"])
                if session["pending_id"] is not None:
                    raise ConversationPending(session["pending_id"])
            ids = [session["session_id"] for session in actual["sessions"]]
            counts = {"conversation_tasks": self._store._connection.execute(
                "DELETE FROM conversation_tasks WHERE root_id = ?", (actual["root_id"],)).rowcount}
            # Delete links first: every provenance-bearing session is removed
            # together, so no survivor points at a pruned source segment.
            self._store._connection.execute("DELETE FROM conversation_segments WHERE root_id = ?", (actual["root_id"],))
            for table in ("conversation_requests", "conversation_messages", "conversations", "automatic_memory",
                          "checks", "policies", "facts", "events", "sessions"):
                key = "id" if table == "sessions" else "session_id"
                counts[table] = self._store._connection.executemany(
                    "DELETE FROM " + table + " WHERE " + key + " = ?", ((value,) for value in ids)).rowcount
            if self._store.history_index_enabled:
                # FTS5 ordinary deletion leaves old token postings plus tombstones.
                # Rebuild from surviving content to remove reconstructable tokens
                # from live shadow rows too. This explicit maintenance can be slow.
                self._store._connection.execute(
                    "INSERT INTO conversation_search(conversation_search) VALUES ('rebuild')")
            return {"root_id": actual["root_id"], "deleted": counts}

    def _append(self, message: dict) -> int:
        """Append one immutable row and update O(1) cost accounting."""
        row, payload = self._row(), encode(message)
        size = row["history_chars"] + len(payload) + (1 if row["next_index"] else 0)
        if size > row["max_chars"]:
            raise ConversationLimitExceeded(size, row["max_chars"])
        self._store._connection.execute(
            "INSERT INTO conversation_messages(session_id, sequence, payload) VALUES (?, ?, ?)",
            (self.session_id, row["next_index"], payload))
        self._store._connection.execute(
            "UPDATE conversations SET history_chars = ?, next_index = next_index + 1 WHERE session_id = ?",
            (size, self.session_id))
        return row["next_index"]

    def _start(self, text: str | None, request_id: str | None, kwargs: dict, *, recover_inflight=False):
        self.agent._require_open()
        _validate_kwargs(kwargs)
        if type(recover_inflight) is not bool:
            raise ValueError("recover_inflight must be a boolean.")
        if text is not None:
            nonempty_string(text, "text", limit=20000000)
            if self.agent.memory is not None and len(text) > self.agent.memory.policy.max_message_chars:
                raise ValueError("text exceeds the configured memory max_message_chars.")
            request_id = str(uuid.uuid4()) if request_id is None else request_id
            nonempty_string(request_id, "request_id", limit=128)
        token = str(uuid.uuid4())
        with _LIVE_LOCK:
            with self._store._transaction(write=True):
                self._store._assert_mutable()
                self._store._session(self.session_id)
                row = self._row()
                next_session_id = self.next_session_id
                if next_session_id is not None:
                    raise ConversationArchived(next_session_id)
                if text is not None:
                    segment = self._segment()
                    old = self._store._connection.execute(
                        "SELECT r.* FROM conversation_segments s JOIN conversation_requests r ON r.session_id = s.session_id "
                        "WHERE s.root_id = ? AND s.ordinal <= ? AND r.request_id = ?",
                        (segment["root_id"], segment["ordinal"], request_id)).fetchone()
                    if old is not None:
                        if self._message(old["user_sequence"], old["session_id"])["content"] != text:
                            raise ValueError("A request_id cannot be reused for different text.")
                        if old["assistant_sequence"] is not None:
                            raise ConversationAlreadyCompleted(request_id, self._message(old["assistant_sequence"], old["session_id"]))
                if self._key in _LIVE or (row["claim_token"] is not None and not recover_inflight):
                    raise ConversationBusy(row["pending_id"])
                if text is None:
                    if row["pending_id"] is None:
                        raise ValueError("There is no pending request to retry.")
                    request_id = row["pending_id"]
                else:
                    if row["pending_id"] is not None:
                        raise ConversationPending(row["pending_id"])
                    task = (self._ensure_task() if self.history_policy is not None or self._task_record() is not None
                            else None)
                    index = self._append({"role": "user", "content": text})
                    if task is not None:
                        source = {"kind": "message", "session_id": self.session_id,
                                  "message_index": index, "request_id": request_id}
                        if self._task_append(task, text, source):
                            self._task_store(task)
                            self._store._append_event(self.session_id, "task_request", {
                                "task_id": task["task_id"], "source": source,
                            })
                    self._store._connection.execute(
                        "INSERT INTO conversation_requests(session_id, request_id, user_sequence) VALUES (?, ?, ?)",
                        (self.session_id, request_id, index))
                self._store._connection.execute(
                    "UPDATE conversations SET pending_id = ?, claim_token = ? WHERE session_id = ?",
                    (request_id, token, self.session_id))
                history = self._history()
                managed_context = self._managed_context()
            _LIVE[self._key] = token
        return token, request_id, history, managed_context

    def _finish(self, token: str, request_id: str, response):
        try:
            text = self._decoder(response)
            if inspect.iscoroutine(text):
                text.close()
            if type(text) is not str or not text.strip():
                raise TypeError("decoder must return nonempty plain assistant text.")
        except Exception as exc:
            raise ConversationResponseError(response, request_id) from exc
        try:
            self._finish_text(token, request_id, text)
        except ConversationLimitExceeded as exc:
            exc.response = response
            raise
        return response

    def _finish_text(self, token: str, request_id: str, text: str) -> None:
        with self._store._transaction(write=True):
            self._store._assert_mutable()
            if self._row()["claim_token"] != token:
                raise ConversationBusy(request_id)
            try:
                index = self._append({"role": "assistant", "content": text})
            except ConversationLimitExceeded as exc:
                exc.response = {"role": "assistant", "content": text}
                raise
            self._store._connection.execute(
                "UPDATE conversation_requests SET assistant_sequence = ? WHERE session_id = ? AND request_id = ?",
                (index, self.session_id, request_id))
            self._store._connection.execute(
                "UPDATE conversations SET pending_id = NULL, claim_token = NULL WHERE session_id = ?",
                (self.session_id,))

    def _release(self, token: str) -> None:
        try:
            with self._store._transaction(write=True):
                self._store._connection.execute(
                    "UPDATE conversations SET claim_token = NULL WHERE session_id = ? AND claim_token = ?",
                    (self.session_id, token))
        finally:
            with _LIVE_LOCK:
                if _LIVE.get(self._key) == token:
                    del _LIVE[self._key]

    def _begin(self, text, request_id, kwargs, recover_inflight):
        if not self._run_lock.acquire(blocking=False):
            raise ConversationBusy(request_id)
        try:
            self._advance(text, request_id, kwargs)
            return self._start(text, request_id, kwargs, recover_inflight=recover_inflight)
        except BaseException:
            self._run_lock.release()
            raise

    def _end(self, token):
        try:
            self._release(token)
        finally:
            self._run_lock.release()


class Conversation(_Conversation):
    """A connected synchronous model with a durable, bounded text transcript."""

    def _run(self, text, request_id, kwargs, *, recover_inflight=False):
        token, request_id, history, context = self._begin(text, request_id, kwargs, recover_inflight)
        try:
            with (self.agent._conversation_context(context) if context is not None else nullcontext()):
                return self._finish(token, request_id, self.agent(messages=history, **kwargs))
        except MemoryClarificationRequired as exc:
            if exc._connection is not self.agent or not exc._local_pause:
                raise
            # A local clarification completes this turn, allowing a new answer.
            # Do not call the provider decoder or retry the blocked request.
            self._finish_text(token, request_id, exc.prompt)
            exc.request_id = request_id
            raise
        finally:
            self._end(token)

    def send(self, text: str, *, request_id: str | None = None, **kwargs):
        """Append a user message and return the original complete response.

        Retrying a completed request ID never bills a second model call. A failed
        request remains pending; send does not automatically retry it.
        """
        if text is None:
            raise ValueError("text must be a nonempty string.")
        return self._run(text, request_id, kwargs)

    def retry(self, *, recover_inflight: bool = False, **kwargs):
        """Retry the pending original; supply model options again.

        recover_inflight=True is an explicit crash recovery override. Ensure the
        old worker has stopped first: its provider call may already have run.
        Active calls in this process remain blocked even with this override.
        """
        return self._run(None, None, kwargs, recover_inflight=recover_inflight)


class AsyncConversation(_Conversation):
    """Async equivalent; cancellation retains the user and releases its claim."""

    _async = True

    async def _run(self, text, request_id, kwargs, *, recover_inflight=False):
        token, request_id, history, context = self._begin(text, request_id, kwargs, recover_inflight)
        try:
            with (self.agent._conversation_context(context) if context is not None else nullcontext()):
                return self._finish(token, request_id, await self.agent(messages=history, **kwargs))
        except MemoryClarificationRequired as exc:
            if exc._connection is not self.agent or not exc._local_pause:
                raise
            # A local clarification completes this turn, allowing a new answer.
            # Do not call the provider decoder or retry the blocked request.
            self._finish_text(token, request_id, exc.prompt)
            exc.request_id = request_id
            raise
        finally:
            self._end(token)

    async def send(self, text: str, *, request_id: str | None = None, **kwargs):
        if text is None:
            raise ValueError("text must be a nonempty string.")
        return await self._run(text, request_id, kwargs)

    async def retry(self, *, recover_inflight: bool = False, **kwargs):
        return await self._run(None, None, kwargs, recover_inflight=recover_inflight)
