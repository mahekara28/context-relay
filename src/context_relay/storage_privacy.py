"""Explicit retention review through existing authorized deletion boundaries."""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from .models import clone, strict_equal


def _instant(value: str) -> datetime:
    if type(value) is not str:
        raise ValueError("Retention timestamps must be timezone-aware ISO 8601 strings.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid retention timestamp.") from error
    if parsed.utcoffset() is None:
        raise ValueError("Retention timestamps must include a timezone.")
    return parsed.astimezone(timezone.utc)


class HistoryRetention:
    """Explicit review/grace period, not automatic expiry or a new permission.

    Works through public deletion_plan/delete_history methods, including scoped
    SessionManager facades. It never unwraps an owner or deletes individual
    request receipts. Plans are host-owned records, not signed credentials.
    """

    def __init__(self, history, *, grace_seconds: int = 0):
        if type(grace_seconds) is not int or not 0 <= grace_seconds <= 315360000:
            raise ValueError("grace_seconds must be an integer from 0 through 315360000.")
        preview, delete = getattr(history, "deletion_plan", None), getattr(history, "delete_history", None)
        if (not callable(preview) or not callable(delete)
                or inspect.iscoroutinefunction(preview) or inspect.iscoroutinefunction(delete)):
            raise TypeError("HistoryRetention needs public deletion_plan() and delete_history() methods.")
        signature = inspect.signature(delete)
        try:
            signature.bind(expected={})
            positional = False
        except TypeError:
            try:
                signature.bind({})
            except TypeError as error:
                raise TypeError("Unsupported delete_history signature.") from error
            positional = True
        self._history, self._positional = history, positional
        self.grace_seconds = grace_seconds

    def plan(self) -> dict:
        """Review one complete lineage/workflow; retain this plan until deletion."""
        snapshot = clone(self._history.deletion_plan())
        if type(snapshot) is not dict:
            raise TypeError("deletion_plan() must return a dictionary.")
        reviewed = datetime.now(timezone.utc)
        return {"schema": 1, "scope": "entire_history", "grace_seconds": self.grace_seconds,
                "reviewed_at": reviewed.isoformat(),
                "delete_after": (reviewed + timedelta(seconds=self.grace_seconds)).isoformat(),
                "deletion": snapshot,
                "effects": {"logical_deletion_only": True, "replay_records_removed": True,
                            "external_copies_erased": False}}

    def delete(self, *, expected: dict) -> dict:
        """Delete only unchanged, resolved history through its existing API.

        Removing a whole history also removes completed replay records. Retire
        its request/session IDs and keep destination idempotency independently.
        No checkpoint, provider retry, reconciliation or permission grant runs.
        """
        if type(expected) is not dict:
            raise TypeError("expected must be a retained HistoryRetention plan.")
        plan = clone(expected)
        if (set(plan) != {"schema", "scope", "grace_seconds", "reviewed_at", "delete_after", "deletion", "effects"}
                or type(plan["schema"]) is not int or plan["schema"] != 1
                or plan["scope"] != "entire_history"
                or type(plan["grace_seconds"]) is not int or plan["grace_seconds"] != self.grace_seconds
                or type(plan["deletion"]) is not dict
                or not strict_equal(plan["effects"], {"logical_deletion_only": True,
                    "replay_records_removed": True, "external_copies_erased": False})):
            raise ValueError("Invalid retention plan or changed grace period.")
        reviewed, deadline = _instant(plan["reviewed_at"]), _instant(plan["delete_after"])
        now = datetime.now(timezone.utc)
        if deadline != reviewed + timedelta(seconds=self.grace_seconds) or reviewed > now:
            raise ValueError("Invalid retention review interval.")
        if now < deadline:
            raise ValueError("Retention grace period has not elapsed.")
        # Preview invokes current read/manage authorization. Deletion below
        # rechecks manage authorization and pending state inside its own lock.
        if not strict_equal(self._history.deletion_plan(), plan["deletion"]):
            raise ValueError("History changed since review; obtain and review a fresh retention plan.")
        delete = self._history.delete_history
        deleted = delete(plan["deletion"]) if self._positional else delete(expected=plan["deletion"])
        return {"deleted": deleted, "logical_deletion_only": True, "external_copies_erased": False}
