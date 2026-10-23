"""Small model-independent integration helpers for agent harnesses."""

from __future__ import annotations

from collections.abc import Callable

from .engine import Relay
from .models import nonempty_string, validate_revision


def guarded_tool_call(relay: Relay, session_id: str, context: dict,
                      tool_call: dict, executor: Callable) -> dict:
    """Execute a model-proposed tool call using the harness's original snapshot.

    Keep ``context`` in harness-owned state after calling ``relay.context()``.
    The model supplies only ``{"tool": ..., "arguments": {...}}``; it cannot
    choose the revision used for execution. This helper validates structure and
    session scope, but does not cryptographically authenticate the snapshot.
    Facts, policies, and snapshot selection remain controlled by the harness.

    Return the result of ``relay.execute()``. A changed revision refreshes or
    blocks the action before ``executor(tool, arguments)`` can run.
    """
    nonempty_string(session_id, "session_id")
    if type(context) is not dict:
        raise ValueError("context must be the harness-owned context snapshot object.")
    if type(context.get("session_id")) is not str or context["session_id"] != session_id:
        raise ValueError("Context session_id must exactly match the target session.")
    try:
        validate_revision(context.get("revision"))
    except ValueError as exc:
        raise ValueError("Context revision must be a nonnegative integer from the harness-owned snapshot.") from exc
    if type(tool_call) is not dict or set(tool_call) != {"tool", "arguments"}:
        raise ValueError("Model tool_call must contain exactly tool and arguments; the harness supplies context_revision.")
    return relay.execute(session_id, {
        "tool": tool_call["tool"],
        "arguments": tool_call["arguments"],
        "context_revision": context["revision"],
    }, executor)
