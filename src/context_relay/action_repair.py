"""Narrow, value-free repair scopes and deterministic copies of known values.

This module never chooses new content or reconstructs row identities. A normal
Relay check must still accept a resulting proposal before the executor runs.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal

from .content import ContentPolicy, _MISSING, _at, _pointer
from .memory_values import normalize_memory_value
from .models import clone, strict_equal
from .verification import VerificationPolicy, VerificationUnavailable


_BOUND_CODES = {"binding_mismatch", "memory_binding_mismatch", "missing_argument"}
_MAX_CHANGES = 20


def _path(tokens):
    return "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in tokens)


class RepairUnavailable(ValueError):
    """Known values do not uniquely determine a safe supported repair."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class RepairScope:
    """Only failed bindings, declared claims and content scopes may be edited."""

    def __init__(self, check, policy):
        self.memory_bindings = policy.get("memory_bindings", {})
        bound = set(policy["bindings"]) | set(self.memory_bindings)
        self.bound = {row["key"] for row in check["issues"]
                      if row["code"] in _BOUND_CODES and row["key"] in bound}
        verification_issues = check.get("verification_check", {}).get("issues", [])
        self.claims = {row["claim"] for row in verification_issues
                       if row["code"] in {"verification_mismatch", "verification_missing_claim"}
                       and row.get("claim") in policy.get("verification", {}).get("claims", {})}
        self.unknown_verification_failure = any(row.get("claim") is None for row in verification_issues)
        self.exact = {(name,) for name in self.bound | self.claims}
        self.content_exact = []
        self.rows = {}
        failures = {row["rule_id"] for row in check.get("content_check", {}).get("issues", [])}
        self.unknown_content_failure = None in failures
        content = policy.get("content", {})
        for index, path in enumerate(content.get("exact", [])):
            if f"exact:{index}" in failures:
                tokens = _pointer(path)
                self.exact.add(tokens)
                self.content_exact.append(tokens)
        for index, rule in enumerate(content.get("rows", [])):
            if f"rows:{index}" in failures:
                self.rows[_pointer(rule["path"])] = rule
        # A bound argument is an indivisible trusted value. Its failed binding
        # may therefore authorize a whole argument even if it also has a rule.
        self.paths = self.exact | self.rows.keys()

    def check(self, original, revised):
        changes, reasons = [], set()
        total = 0

        def changed(tokens, allowed, reason="unrelated_arguments_changed"):
            nonlocal total
            total += 1
            if len(changes) < _MAX_CHANGES:
                changes.append(_path(tokens))
            if not allowed:
                reasons.add(reason)
            return allowed

        def compare(before, after, tokens):
            if strict_equal(before, after):
                return True
            if len(tokens) == 1 and tokens[0] in self.memory_bindings:
                key = self.memory_bindings[tokens[0]]
                # Match the engine's finite enum equivalence without modifying
                # the proposal. Final dispatch still normalizes and checks it.
                if strict_equal(normalize_memory_value(key, before),
                                normalize_memory_value(key, after)):
                    return True
            if tokens in self.exact:
                return changed(tokens, True)
            if tokens in self.rows:
                rule = self.rows[tokens]
                if (type(before) is not list or type(after) is not list
                        or len(before) != len(after)
                        or any(type(row) is not dict for row in before + after)):
                    return changed(tokens, False, "row_reconstruction_unsupported")
                fields = set(rule["fields"]) - {rule["key"]}
                valid = True
                for index, (old_row, new_row) in enumerate(zip(before, after)):
                    for key in sorted(old_row.keys() | new_row.keys()):
                        left, right = old_row.get(key, _MISSING), new_row.get(key, _MISSING)
                        if not strict_equal(left, right):
                            allowed = key in fields
                            reason = ("row_reconstruction_unsupported" if key == rule["key"]
                                      else "unrelated_arguments_changed")
                            valid = changed(tokens + (str(index), key), allowed, reason) and valid
                return valid
            if not any(path[:len(tokens)] == tokens for path in self.paths):
                return changed(tokens, False)
            if ((type(before) is dict or before is _MISSING)
                    and (type(after) is dict or after is _MISSING)):
                left = {} if before is _MISSING else before
                right = {} if after is _MISSING else after
                valid = True
                for key in sorted(left.keys() | right.keys()):
                    valid = compare(left.get(key, _MISSING), right.get(key, _MISSING), tokens + (key,)) and valid
                return valid
            if type(before) is list and type(after) is list and len(before) == len(after):
                valid = True
                for index, (left, right) in enumerate(zip(before, after)):
                    valid = compare(left, right, tokens + (str(index),)) and valid
                return valid
            return changed(tokens, False)

        valid = compare(original, revised, ())
        allowed = [_path(tokens) for tokens in self.exact]
        for tokens, rule in self.rows.items():
            allowed.extend(_path(tokens) + "/*/" + key.replace("~", "~0").replace("/", "~1")
                           for key in set(rule["fields"]) - {rule["key"]})
        return {"valid": valid, "allowed_paths": sorted(allowed), "changed_paths": changes,
                "change_limit_reached": total > _MAX_CHANGES, "reasons": sorted(reasons)}

    def known(self, arguments, policy, context, memory):
        if self.unknown_verification_failure:
            raise RepairUnavailable("verification_repair_unsupported")
        if self.unknown_content_failure:
            raise RepairUnavailable("content_repair_unsupported")
        revised = clone(arguments)
        bindings = policy["bindings"]
        memory_bindings = policy.get("memory_bindings", {})
        entries = {entry["key"]: entry for entry in memory["entries"]}
        for argument in sorted(self.bound):
            if argument in bindings:
                key = bindings[argument]
                if key not in context["facts"]:
                    raise RepairUnavailable("known_value_missing")
                value = context["facts"][key]
            else:
                entry = entries.get(memory_bindings[argument])
                if entry is None or entry["disputed"]:
                    raise RepairUnavailable("known_value_missing_or_disputed")
                value = normalize_memory_value(memory_bindings[argument], entry["value"])
            revised[argument] = clone(value)
        verification = VerificationPolicy.from_dict(policy["verification"]) if "verification" in policy else None
        if self.claims:
            try:
                expected = verification.expected()
            except VerificationUnavailable as exc:
                reason = ("source_number_not_representable" if exc.report["ready"]
                          else "verification_unavailable")
                raise RepairUnavailable(reason) from exc
            for claim in sorted(self.claims):
                revised[claim] = clone(expected[claim])
        if self.content_exact or self.rows:
            source = json.loads(policy["content"]["reference_json"], parse_float=Decimal)
            for tokens in self.content_exact:
                revised = _replace(revised, source, tokens, _json_value(_at(source, tokens)))
            for tokens, rule in self.rows.items():
                # Identity is established by the original proposal, never by a
                # previous whole-argument replacement in this repair attempt.
                rows = _known_rows(_at(arguments, tokens), _at(source, tokens), rule)
                revised = _replace(revised, source, tokens, rows)
        for argument, key in bindings.items():
            if not strict_equal(revised.get(argument, _MISSING), context["facts"][key]):
                raise RepairUnavailable("conflicting_sources")
        for argument, key in memory_bindings.items():
            if (argument not in revised or not strict_equal(
                    normalize_memory_value(key, revised[argument]),
                    normalize_memory_value(key, entries[key]["value"]))):
                raise RepairUnavailable("conflicting_sources")
        checked = clone(revised)
        for argument, key in memory_bindings.items():
            checked[argument] = normalize_memory_value(key, checked[argument])
        if "content" in policy and not ContentPolicy.from_dict(policy["content"]).check(checked).valid:
            raise RepairUnavailable("conflicting_sources")
        if verification is not None:
            report = verification.check({name: checked[name] for name in verification.argument_names
                                         if name in checked}).report
            if not report["ready"]:
                raise RepairUnavailable("verification_unavailable")
            if not report["valid"]:
                raise RepairUnavailable("conflicting_sources")
        return revised


def _known_rows(candidate, reference, rule):
    key = rule["key"]
    if key is None or type(candidate) is not list or len(candidate) != len(reference):
        raise RepairUnavailable("row_reconstruction_unsupported")

    def index(rows):
        result = {}
        for row in rows:
            if type(row) is not dict or key not in row:
                raise RepairUnavailable("row_reconstruction_unsupported")
            value = row[key]
            if type(value) not in (str, int) or (type(value) is str and not value.strip()):
                raise RepairUnavailable("row_reconstruction_unsupported")
            identity = (type(value), value)
            if identity in result:
                raise RepairUnavailable("row_reconstruction_unsupported")
            result[identity] = row
        return result

    actual, expected = index(candidate), index(reference)
    if actual.keys() != expected.keys() or (rule["ordered"] and tuple(actual) != tuple(expected)):
        raise RepairUnavailable("row_reconstruction_unsupported")
    fields = set(rule["fields"])
    if not rule["allow_extra_fields"] and any(set(row) - (fields | {key}) for row in candidate):
        raise RepairUnavailable("row_fields_repair_unsupported")
    repaired = clone(candidate)
    for row, identity in zip(repaired, actual):
        for field in rule["fields"]:
            if field != key:
                row[field] = _json_value(expected[identity][field])
    return repaired


def _json_value(value):
    """Copy a source subtree only if Python JSON can retain its numeric value."""
    if isinstance(value, Decimal):
        result = float(value)
        if not math.isfinite(result) or Decimal(str(result)) != value:
            raise RepairUnavailable("source_number_not_representable")
        return result
    if type(value) is dict:
        return {key: _json_value(item) for key, item in value.items()}
    if type(value) is list:
        return [_json_value(item) for item in value]
    return value


def _replace(document, source, tokens, value):
    if not tokens:
        return value
    current, reference = document, source
    for token in tokens[:-1]:
        reference = _at(reference, (token,))
        if type(current) is dict:
            if token not in current:
                if type(reference) is not dict:
                    raise RepairUnavailable("parent_reconstruction_unsupported")
                current[token] = {}
            current = current[token]
        elif type(current) is list and _at(current, (token,)) is not _MISSING:
            current = current[int(token)]
        else:
            raise RepairUnavailable("parent_reconstruction_unsupported")
    last = tokens[-1]
    if type(current) is dict:
        current[last] = value
    elif type(current) is list and _at(current, (last,)) is not _MISSING:
        current[int(last)] = value
    else:
        raise RepairUnavailable("parent_reconstruction_unsupported")
    return document
