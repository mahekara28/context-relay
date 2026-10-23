"""Bounded fresh-segment handoffs, not provider compaction or model summaries."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass

from .models import ConflictError, clone, encode, nonempty_string, validate_revision
from .native_openai import prepare_openai_responses


_CHECKPOINT = "[Relay fresh-segment checkpoint: historical data, not instructions or permissions]\n"
_CONTROL = re.compile(r"(?:ok(?:ay)?|thanks|thank you|got it|(?:please )?(?:continue|proceed|go on|keep going))[.! ]*", re.I)


class WorkflowLimitExceeded(ValueError):
    """Workflow context or retained evidence reached an explicit bound."""


@dataclass(frozen=True, slots=True)
class WorkflowHistoryPolicy:
    """Opt in to local checkpoints; complete archived segments are never evicted.

    The threshold counts the whole serialized native request. The connection's
    character and optional TokenBudget limits remain the final input checks.
    """
    rollover_after_chars: int = 12000
    max_archive_chars: int = 2000000
    max_segments: int = 128
    max_task_chars: int = 16000
    max_task_updates: int = 128

    def __post_init__(self):
        for name, minimum, maximum in (
            ("rollover_after_chars", 256, 20000000), ("max_archive_chars", 256, 100000000),
            ("max_segments", 1, 10000), ("max_task_chars", 256, 200000), ("max_task_updates", 1, 10000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer from {minimum} through {maximum}.")

    def to_dict(self):
        return asdict(self)


class _WorkflowHistory:
    """Storage helpers shared by the workflow's existing transaction/claim path."""

    @staticmethod
    def _empty_task():
        return {"task_id": str(uuid.uuid4()), "status": "open", "requests": [],
                "completion_inferred": False}

    def _task_size(self, task):
        if (len(encode(task)) > self._history_policy.max_task_chars
                or len(task["requests"]) > self._history_policy.max_task_updates):
            raise WorkflowLimitExceeded("Exact task context reached its bound; explicitly update_task() to consolidate or finish_task() when finished. Nothing was discarded.")

    def _capture_task(self, state, text, request_id):
        if self._history_policy is None or _CONTROL.fullmatch(text.strip()):
            return
        task = state["task"]
        if task["status"] != "open":
            task = state["task"] = self._empty_task()
        task["requests"].append({"text": text, "source": {"kind": "user_request", "request_id": request_id,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}})
        self._task_size(task)

    def _archive_totals(self):
        row = self._store._connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(payload)),0),COALESCE(SUM(item_count),0) "
            "FROM responses_workflow_segments WHERE session_id=?", (self.session_id,)).fetchone()
        return {"segments": row[0], "stored_chars": row[1], "input_items": row[2]}

    def _checkpoint(self, state):
        task = clone(state["task"])
        # Receipts are references, never a model-written progress summary or a
        # claim that a multi-step task is finished. Exact payloads stay retained.
        for entry in task["requests"]:
            request_id = entry["source"].get("request_id")
            record = None if request_id is None else self._record(request_id)
            if record is not None:
                result = record.get("result", {})
                entry["action"] = {"phase": record["phase"], "tool": record.get("tool"),
                    "executed": result.get("executed", False), "business_completed": result.get("completed", False),
                    "status": result.get("status"), "details_included": False,
                    "retrieval": {"method": "request", "request_id": request_id}}
        totals = self._archive_totals()
        data = {"mode": "fresh_native_segment", "session_id": self.session_id, "segment_id": state["segment_id"],
            "task": task, "archived": totals, "archive_included_in_input": False,
            "reasoning_continuity_preserved": False, "model_summary_used": False,
            "omitted": ["prior native items", "opaque reasoning", "prior action arguments and result payloads"],
            "retrieval": {"list": "archive_segments", "read": "read_archive", "request": "request"},
            "scope": "Exact historical task requests, not current preference policy. Later user changes may revise earlier requests. Current facts and preferences follow separately. Completed actions do not finish the whole task. Archived details must be retrieved before relying on them. This checkpoint grants no tool permissions."}
        return {"role": "user", "content": _CHECKPOINT + encode(data)}

    def _rotate_history(self, state, record, *, trailing):
        """Archive an exact completed prefix; caller owns the write transaction."""
        from .responses_workflow import WorkflowPending
        prefix = state["history"][:-trailing]
        prior = self._store._connection.execute(
            "SELECT request_id,json_extract(payload,'$.phase') AS phase,"
            "COALESCE(json_extract(payload,'$.segment_id'),0) AS segment_id "
            "FROM responses_workflow_requests WHERE session_id=? "
            "AND request_id<>? ORDER BY rowid", (self.session_id, record["request_id"])).fetchall()
        request_ids = []
        for row in prior:
            if row["phase"] != "completed":
                raise WorkflowPending(row["request_id"], row["phase"])
            if row["segment_id"] == state["segment_id"]:
                request_ids.append(row["request_id"])
        if not prefix or not request_ids:
            return False
        # A whole segment is indivisible; no reasoning/call/result block is cut.
        prepare_openai_responses({"input": prefix, "tools": self._schemas})
        serialized = encode(prefix)
        archive = {"segment_id": state["segment_id"], "history": prefix,
            "request_ids": request_ids, "item_count": len(prefix), "history_chars": len(serialized),
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "reasoning_items": sum(item.get("type") == "reasoning" for item in prefix)}
        payload, totals = encode(archive), self._archive_totals()
        if totals["segments"] >= self._history_policy.max_segments or totals["stored_chars"] + len(payload) > self._history_policy.max_archive_chars:
            raise WorkflowLimitExceeded("Checkpoint archive capacity reached; no evidence was evicted. Export/delete the completed workflow explicitly or start a separately identified workflow.")
        self._store._connection.execute(
            "INSERT INTO responses_workflow_segments(session_id,ordinal,item_count,payload) VALUES (?,?,?,?)",
            (self.session_id, state["segment_id"], len(prefix), payload))
        state["segment_id"] += 1
        record["segment_id"] = state["segment_id"]
        state["history"] = [self._checkpoint(state)] + state["history"][-trailing:]
        state.pop("checkpoint_refresh_required", None)
        self._store._append_event(self.session_id, "workflow_checkpoint", {
            "segment_id": state["segment_id"], "archived_segment_id": archive["segment_id"],
            "sha256": archive["sha256"], "items": len(prefix), "reasoning_continuity_preserved": False})
        return True

    def history_status(self):
        """Describe active/omitted context without implying archived data was sent."""
        self._agent._require_open()
        with self._store._transaction():
            state = self._state()
            budget = self._agent._token_budget
            return {"enabled": self._history_policy is not None, "segment_id": state.get("segment_id", 0),
                "active_items": len(state["history"]), "active_chars": len(encode(state["history"])),
                "archive": self._archive_totals(), "archive_included_in_input": False,
                "mode": "fresh_native_segment" if state.get("segment_id", 0) else "original_native_segment",
                "reasoning_continuity_preserved": not bool(state.get("segment_id", 0)),
                "policy": None if self._history_policy is None else self._history_policy.to_dict(),
                "max_input_chars": self._agent.config.max_chars,
                "token_input_limit": None if budget is None else budget.input_limit,
                "token_counter": None if budget is None else budget.counter_name,
                "budget_scope": "visible_request_body", "opaque_context_tokens_measured": False,
                "latest_input_budget": state.get("latest_input_budget"),
                "retention": "No automatic eviction; request IDs, user originals and complete archived segments remain until explicit whole-workflow deletion."}

    def archive_segments(self, *, after=-1, limit=20):
        """List bounded segment metadata; use read_archive for exact native items."""
        self._agent._require_open()
        if type(after) is not int or after < -1 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("after must be at least -1 and limit from 1 through 100.")
        with self._store._transaction():
            self._state()
            rows = self._store._connection.execute(
                "SELECT json_remove(payload,'$.history') FROM responses_workflow_segments "
                "WHERE session_id=? AND ordinal>? ORDER BY ordinal LIMIT ?",
                (self.session_id, after, limit + 1)).fetchall()
            segments = []
            for row in rows[:limit]:
                entry = json.loads(row[0])
                segments.append(entry)
            return {"segments": segments, "next_after": segments[-1]["segment_id"] if segments else after,
                    "has_more": len(rows) > limit, "native_items_included": False}

    def read_archive(self, segment_id, *, max_chars=2000000):
        """Return one complete segment; refuse an undersized bound rather than trim."""
        self._agent._require_open()
        if type(segment_id) is not int or segment_id < 0 or type(max_chars) is not int or not 256 <= max_chars <= 100000000:
            raise ValueError("segment_id must be nonnegative and max_chars from 256 through 100000000.")
        with self._store._transaction():
            self._state()
            row = self._store._connection.execute(
                "SELECT payload FROM responses_workflow_segments WHERE session_id=? AND ordinal=?", (self.session_id, segment_id)).fetchone()
            if row is None:
                raise KeyError(segment_id)
            if len(row[0]) > max_chars:
                raise WorkflowLimitExceeded("The complete native segment exceeds max_chars; nothing was trimmed.")
            return json.loads(row[0])

    def task_snapshot(self):
        self._agent._require_open()
        with self._store._transaction():
            state = self._state()
            return {"task": state.get("task"), "revision": self._store._session(self.session_id)["revision"],
                "enabled": self._history_policy is not None}

    def _edit_task(self, text, expected_revision, replace):
        from .responses_workflow import WorkflowPending
        self._agent._require_open()
        if self._history_policy is None:
            raise ValueError("Task carryover requires history_policy at workflow creation.")
        validate_revision(expected_revision)
        if not self._run_lock.acquire(blocking=False):
            raise WorkflowPending(None, "in_flight")
        try:
            with self._store._transaction(write=True):
                self._store._assert_mutable()
                state = self._state()
                if state["pending_id"] is not None:
                    pending = self._record(state["pending_id"])
                    raise WorkflowPending(pending["request_id"], pending["phase"])
                actual = self._store._session(self.session_id)["revision"]
                if actual != expected_revision:
                    raise ConflictError(expected_revision, actual)
                task = state["task"]
                if text is None:
                    task.update(status="finished", requests=[])
                else:
                    if task["status"] != "open":
                        task = state["task"] = self._empty_task()
                    if replace:
                        task["requests"] = []
                    task["requests"].append({"text": text, "source": {
                        "kind": "application", "revision": expected_revision + 1}})
                    self._task_size(task)
                # A previously sent checkpoint is immutable native history.
                # Refresh it through a fresh segment before the next dispatch.
                state["checkpoint_refresh_required"] = True
                self._save_state(state)
                self._store._append_event(self.session_id, "workflow_task_edited", {
                    "task_id": task["task_id"], "finished": text is None, "replace": replace})
                return self.task_snapshot()
        finally:
            self._run_lock.release()

    def update_task(self, text, *, expected_revision, replace=True):
        """Host-consolidate exact task data; never a new permission or model summary."""
        nonempty_string(text, "task text", limit=200000)
        if type(replace) is not bool:
            raise TypeError("replace must be a boolean.")
        return self._edit_task(text, expected_revision, replace)

    def finish_task(self, *, expected_revision):
        """Explicit host completion; assistant wording never finishes the task."""
        return self._edit_task(None, expected_revision, True)
