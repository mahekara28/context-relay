"""Validation and exceptions shared by Context Relay's deterministic core."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any


class ConflictError(ValueError):
    """An optimistic revision does not match the current session revision."""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Expected revision {expected}; current revision is {actual}.")


class BudgetExceeded(ValueError):
    """The selected context cannot fit without dropping explicitly selected facts."""

    def __init__(self, size_chars: int, max_chars: int):
        self.size_chars = size_chars
        self.max_chars = max_chars
        super().__init__(f"Context needs {size_chars} characters; budget is {max_chars}.")


class PolicyError(ValueError):
    """A developer-supplied policy is invalid or ambiguous."""


class ToolExecutionError(RuntimeError):
    """A callback failed; the check and committed receipt remain inspectable."""

    def __init__(self, message: str, *, check: dict, receipt: dict):
        self.check = check
        self.receipt = receipt
        super().__init__(message)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def validate_json(value: Any, *, name: str = "value", depth: int = 0) -> None:
    """Accept JSON data only, rejecting lossy conversions and non-finite numbers."""
    if depth > 64:
        raise ValueError(f"{name} exceeds the maximum JSON nesting depth (64).")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{name} must not contain NaN or infinity.")
        return
    if type(value) is list:
        for item in value:
            validate_json(item, name=name, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{name} must contain only string object keys.")
            validate_json(item, name=name, depth=depth + 1)
        return
    raise ValueError(f"{name} must be JSON-compatible (got {type(value).__name__}).")


def encode(value: Any) -> str:
    validate_json(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def clone(value: Any) -> Any:
    return json.loads(encode(value))


def strict_equal(left: Any, right: Any) -> bool:
    """Type-sensitive equality: true, 1, and 1.0 are distinct stored values."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(strict_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def nonempty_string(value: Any, name: str, *, limit: int = 512) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be a nonempty string of at most {limit} characters.")
    return value


def validate_revision(value: Any) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("expected_revision must be a nonnegative integer.")


def validate_policy(policy: dict) -> dict:
    """Normalize a policy without silently accepting misspelled configuration."""
    try:
        if type(policy) is not dict:
            raise ValueError("Policy must be a JSON object.")
        unknown = set(policy) - {"id", "title", "tool", "requirements", "bindings", "memory_bindings", "optional_args", "content", "verification", "outcome"}
        if unknown:
            raise ValueError(f"Unknown policy fields: {', '.join(sorted(map(str, unknown)))}.")
        result = clone(policy)
        for name in ("id", "title", "tool"):
            nonempty_string(result.get(name), f"Policy {name}")
        result.setdefault("requirements", [])
        result.setdefault("bindings", {})
        result.setdefault("optional_args", [])
        if type(result["requirements"]) is not list:
            raise ValueError("Policy requirements must be a list.")
        for requirement in result["requirements"]:
            if type(requirement) is not dict or set(requirement) != {"key", "op", "value"}:
                raise ValueError("Each requirement must contain exactly key, op, and value.")
            nonempty_string(requirement["key"], "Requirement key")
            if requirement["op"] not in ("eq", "ne", "in"):
                raise ValueError("Requirement op must be eq, ne, or in.")
            if requirement["op"] == "in" and type(requirement["value"]) is not list:
                raise ValueError("An 'in' requirement must have a list value.")
        if type(result["bindings"]) is not dict:
            raise ValueError("Policy bindings must be an object.")
        for argument, key in result["bindings"].items():
            nonempty_string(argument, "Bound argument")
            nonempty_string(key, "Binding fact key")
        memory_bindings = result.get("memory_bindings", {})
        if type(memory_bindings) is not dict:
            raise ValueError("Policy memory_bindings must be an object.")
        for argument, key in memory_bindings.items():
            nonempty_string(argument, "Memory-bound argument")
            nonempty_string(key, "Binding memory key")
        if set(memory_bindings) & set(result["bindings"]):
            raise ValueError("An argument cannot bind both a fact and user memory.")
        if type(result["optional_args"]) is not list:
            raise ValueError("Policy optional_args must be a list.")
        for argument in result["optional_args"]:
            nonempty_string(argument, "Optional argument")
        if len(set(result["optional_args"])) != len(result["optional_args"]):
            raise ValueError("Policy optional_args must not contain duplicates.")
        if "outcome" in result:
            from .outcomes import OutcomePolicy
            result["outcome"] = OutcomePolicy.from_dict(result["outcome"]).to_dict()
        content_arguments = set()
        if "content" in result:
            from .content import ContentPolicy
            content = ContentPolicy.from_dict(result["content"])
            content_arguments = set(content.argument_names)
            for argument in content_arguments:
                nonempty_string(argument, "Content-bound argument")
            result["content"] = content.to_dict()
        verification_arguments = set()
        if "verification" in result:
            from .verification import VerificationPolicy
            verification = VerificationPolicy.from_dict(result["verification"])
            verification_arguments = set(verification.argument_names)
            for argument in verification_arguments:
                nonempty_string(argument, "Verified argument")
            result["verification"] = verification.to_dict()
        if set(result["optional_args"]) & (set(result["bindings"]) | set(memory_bindings)
                                           | content_arguments | verification_arguments):
            raise ValueError("A bound argument cannot also be optional.")
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise PolicyError(str(exc)) from exc


def issue(code: str, key: str, message: str, *, expected: Any = None,
          actual: Any = None, event_id: str | None = None) -> dict:
    return {"code": code, "key": key, "message": message, "expected": expected,
            "actual": actual, "event_id": event_id}
