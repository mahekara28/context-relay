"""Explicit coverage requirements for a host-declared tool inventory."""
from __future__ import annotations

from dataclasses import dataclass
import json

from .models import clone, encode, nonempty_string


class CoverageError(ValueError):
    """The declared execution route does not meet its coverage requirements."""
    def __init__(self, report):
        self.report = clone(report)
        super().__init__("Protection coverage is incomplete: " + ", ".join(item["code"] for item in report["issues"]))


@dataclass(frozen=True, init=False)
class CoveragePolicy:
    """Require exact tool inventory, explicit unchecked exceptions and outcomes.

    This audits declarations, not arbitrary Python call paths or semantic truth.
    Empty unchecked exceptions are the default; host-approved exceptions remain
    visible. Neither a name match nor a covered parameter proves its full content.
    """
    _serialized: str

    def __init__(self, inventory, *, allow_unchecked=None, require_outcomes=True):
        if isinstance(inventory, (str, bytes)):
            raise TypeError("inventory must contain tool names.")
        names = tuple(inventory)
        if not 1 <= len(names) <= 128:
            raise ValueError("inventory must contain 1 through 128 tool names.")
        for name in names:
            nonempty_string(name, "tool name")
        if len(set(names)) != len(names):
            raise ValueError("inventory must not contain duplicates.")
        if type(require_outcomes) is not bool:
            raise TypeError("require_outcomes must be a boolean.")
        exceptions = {} if allow_unchecked is None else allow_unchecked
        if type(exceptions) is not dict or set(exceptions) - set(names):
            raise ValueError("allow_unchecked must map inventory tools to explicit parameter names.")
        copied = {}
        for name, values in exceptions.items():
            if isinstance(values, (str, bytes)):
                raise TypeError("Unchecked exceptions must be collections of parameter names.")
            values = tuple(values)
            if len(values) > 128:
                raise ValueError("Too many unchecked exceptions.")
            for value in values:
                nonempty_string(value, "unchecked parameter")
            if len(set(values)) != len(values):
                raise ValueError("Unchecked exceptions must not contain duplicates.")
            copied[name] = sorted(values)
        serialized = encode({"inventory": sorted(names), "allow_unchecked": copied,
                             "require_outcomes": require_outcomes})
        if len(serialized) > 16000:
            raise ValueError("Coverage policy exceeds 16000 characters.")
        object.__setattr__(self, "_serialized", serialized)

    def to_dict(self):
        return json.loads(self._serialized)

    @property
    def inventory(self):
        return tuple(self.to_dict()["inventory"])

    def check(self, coverage):
        policy = self.to_dict()
        expected, actual = set(policy["inventory"]), set(coverage["registered_tools"])
        issues = []
        supplied = coverage.get("provided_tools")
        if supplied is not None and set(supplied) != expected:
            issues.append({"code": "inventory_declaration_changed", "tools": sorted(supplied)})
        for code, names in (("missing_tools", expected - actual), ("unexpected_tools", actual - expected)):
            if names:
                issues.append({"code": code, "tools": sorted(names)})
        for name in sorted(actual & expected):
            report = coverage["tools"][name]
            unchecked = set(report["unchecked"])
            permitted = set(policy["allow_unchecked"].get(name, ()))
            if unchecked != permitted:
                issues.append({"code": "unchecked_scope_changed", "tool": name,
                               "unapproved": sorted(unchecked - permitted), "unused_exceptions": sorted(permitted - unchecked)})
            if policy["require_outcomes"] and "outcome" not in report:
                issues.append({"code": "missing_outcome_check", "tool": name})
        if coverage.get("stale_contracts"):
            issues.append({"code": "stale_contracts", "tools": coverage["stale_contracts"]})
        return {"requirements_satisfied": not issues, "issues": issues, "requirements": policy,
                "direct_calls_protected": False, "scope": "declared_registered_route",
                "source_truth_verified": False, "coverage": clone(coverage)}

    def require(self, coverage):
        report = self.check(coverage)
        if report["issues"]:
            raise CoverageError(report)
        return report
