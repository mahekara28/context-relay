"""Bounded, literal JSON preservation checks against a developer snapshot.

These checks establish equality of declared data, not factual correctness or
semantic equivalence. JSON floats are parsed as Decimal; Python floats can only
be checked at the precision already present in the supplied Python object.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from .models import encode


_MISSING = object()
_MAX_ISSUES = 20


class _Limit(ValueError):
    pass


def _pointer(path: str) -> tuple[str, ...]:
    if type(path) is not str:
        raise TypeError("JSON pointers must be strings.")
    if len(path) > 512 or (path and not path.startswith("/")):
        raise ValueError("Use a JSON pointer of at most 512 characters.")
    parts = path[1:].split("/") if path else []
    if len(parts) > 32 or any(re.search(r"~(?![01])", part) for part in parts):
        raise ValueError("JSON pointers require canonical escaping and at most 32 tokens.")
    tokens = tuple(part.replace("~1", "/").replace("~0", "~") for part in parts)
    if any(token in {"*", "**"} for token in tokens):
        raise ValueError("Wildcard pointers are not supported.")
    return tokens


def _at(value, tokens):
    for token in tokens:
        if type(value) is dict or isinstance(value, MappingProxyType):
            value = value.get(token, _MISSING)
        elif type(value) in (list, tuple):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                return _MISSING
            # Avoid integer conversion of arbitrarily long index strings.
            if len(token) > len(str(len(value))):
                return _MISSING
            index = int(token)
            value = value[index] if index < len(value) else _MISSING
        else:
            return _MISSING
        if value is _MISSING:
            break
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not supported.")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("Non-finite JSON numbers are not supported.")


def _depth(value, level=0):
    if level > 64:
        raise _Limit("JSON nesting exceeds 64 levels.")
    children = value.values() if type(value) is dict else value if type(value) is list else ()
    for child in children:
        _depth(child, level + 1)


def _parse(text, max_chars):
    if type(text) is not str:
        raise TypeError("JSON input must be a string.")
    if len(text) > max_chars:
        raise _Limit("JSON input exceeds max_chars.")
    # Reject excessive nesting before the JSON decoder can exhaust its stack.
    quoted, escaped, nesting = False, False, 0
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            nesting += 1
            if nesting > 65:
                raise _Limit("JSON nesting exceeds 64 levels.")
        elif char in "]}":
            nesting -= 1
    value = json.loads(text, parse_float=Decimal, parse_constant=_constant, object_pairs_hook=_pairs)
    _depth(value)
    return value


def _canonical(value):
    """Canonical lossless JSON, with float tokens kept distinct from integers."""
    if isinstance(value, Decimal):
        sign, digits, exponent = value.as_tuple()
        if not any(digits):
            return "0e0"
        digits = list(digits)
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        return ("-" if sign else "") + "".join(map(str, digits)) + "e" + str(exponent)
    if type(value) is dict or isinstance(value, MappingProxyType):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + _canonical(value[key])
                               for key in sorted(value)) + "}"
    if type(value) in (list, tuple):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _name(value):
    if type(value) is not str:
        raise TypeError("Row field names must be literal strings.")
    if len(value) > 512:
        raise ValueError("Row field names must be at most 512 characters.")


@dataclass(frozen=True)
class RowRule:
    """Preserve selected row fields, their association, and complete coverage."""

    path: str
    fields: tuple[str, ...]
    key: str | None = None
    ordered: bool = True
    allow_extra_fields: bool = True

    def __post_init__(self):
        _pointer(self.path)
        if type(self.fields) is not tuple:
            raise TypeError("fields must be a tuple of literal row field names.")
        if len(self.fields) > 64:
            raise ValueError("At most 64 fields may be selected per row rule.")
        for name in self.fields:
            _name(name)
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Row fields must be unique.")
        if self.key is not None:
            _name(self.key)
        if not self.fields and self.key is None:
            raise ValueError("A row rule needs a field or identity key to compare.")
        if type(self.ordered) is not bool or type(self.allow_extra_fields) is not bool:
            raise TypeError("Row ordering and extra-field options must be booleans.")

    def to_dict(self):
        return {"path": self.path, "fields": list(self.fields), "key": self.key,
                "ordered": self.ordered, "allow_extra_fields": self.allow_extra_fields}


@dataclass(frozen=True)
class ContentCheck:
    """Immutable result with a fresh, value-free report on every access."""

    valid: bool
    _report_json: str = field(repr=False)

    @property
    def report(self) -> dict:
        return json.loads(self._report_json)


def _rows(value, rule, max_rows):
    """Return comparable rows or one bounded diagnostic; never return values."""
    if type(value) not in (list, tuple):
        return "content_rows_type", None
    if len(value) > max_rows:
        return "content_limit", None
    fields = set(rule.fields) | ({rule.key} if rule.key is not None else set())
    identities, projections = [], []
    seen = set()
    for row in value:
        if type(row) is not dict and not isinstance(row, MappingProxyType):
            return "content_rows_type", None
        if not fields.issubset(row):
            return "content_missing", None
        if not rule.allow_extra_fields and set(row) != fields:
            return "content_rows_fields", None
        if rule.key is not None:
            identity = row[rule.key]
            if type(identity) not in (str, int) or (type(identity) is str and not identity.strip()):
                return "content_rows_identity", None
            identity = type(identity).__name__, identity
            if identity in seen:
                return "content_rows_identity", None
            seen.add(identity)
            identities.append(identity)
        projections.append(_canonical({name: row[name] for name in fields}))
    return None, (tuple(identities), tuple(projections))


@dataclass(frozen=True, init=False, repr=False)
class ContentPolicy:
    """An immutable, bounded source snapshot and explicit preservation rules.

    exact uses RFC 6901 pointers. Row fields are literal object keys, not paths.
    max_rows applies to each configured row array. Unprotected data is ignored.
    """

    exact: tuple[str, ...]
    rows: tuple[RowRule, ...]
    max_chars: int
    max_rows: int
    _reference_json: str
    _reference: Any
    _prepared: tuple
    _fingerprint: str

    def __init__(self, reference, *, exact=(), rows=(), max_chars=256000, max_rows=10000):
        # Preserve the project's strict Python JSON contract before snapshotting.
        self._initialize(encode(reference), exact, rows, max_chars, max_rows)

    @classmethod
    def from_json(cls, reference_text, *, exact=(), rows=(), max_chars=256000, max_rows=10000):
        policy = cls.__new__(cls)
        policy._initialize(reference_text, exact, rows, max_chars, max_rows)
        return policy

    def _initialize(self, reference_json, exact, rows, max_chars, max_rows):
        for name, value, ceiling in (("max_chars", max_chars, 2000000), ("max_rows", max_rows, 100000)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be a positive integer no greater than {ceiling}.")
        if type(exact) is not tuple or type(rows) is not tuple:
            raise TypeError("exact and rows must be tuples.")
        if not 1 <= len(exact) + len(rows) <= 64:
            raise ValueError("Configure between 1 and 64 comparison rules.")
        if any(type(rule) is not RowRule for rule in rows):
            raise TypeError("rows must contain RowRule objects.")
        paths = [(_pointer(path), f"exact:{index}", path, None) for index, path in enumerate(exact)]
        paths += [(_pointer(rule.path), f"rows:{index}", rule.path, rule) for index, rule in enumerate(rows)]
        for index, (tokens, _, _, _) in enumerate(paths):
            for other, _, _, _ in paths[:index]:
                if tokens[:len(other)] == other or other[:len(tokens)] == tokens:
                    raise ValueError("Comparison rule paths must not overlap.")
        try:
            reference = _parse(reference_json, max_chars)
        except (ValueError, TypeError, InvalidOperation, RecursionError) as exc:
            raise ValueError("Reference must be valid, bounded JSON with unique keys.") from exc
        prepared = []
        for tokens, rule_id, path, rule in paths:
            selected = _at(reference, tokens)
            if selected is _MISSING:
                raise ValueError(f"Protected source path is missing: {rule_id}.")
            if rule is not None:
                code, comparison = _rows(selected, rule, max_rows)
                if code:
                    raise ValueError(f"Invalid source rows for {rule_id}: {code}.")
            else:
                comparison = _canonical(selected)
            prepared.append((tokens, rule_id, path, rule, comparison))
        for name, value in (("exact", exact), ("rows", rows), ("max_chars", max_chars), ("max_rows", max_rows),
                            ("_reference_json", reference_json), ("_reference", _freeze(reference)),
                            ("_prepared", tuple(prepared))):
            object.__setattr__(self, name, value)
        fingerprint_data = {"reference": reference, "exact": list(exact), "rows": [row.to_dict() for row in rows],
                            "max_chars": max_chars, "max_rows": max_rows, "schema": 1}
        digest = hashlib.sha256(_canonical(fingerprint_data).encode("utf-8", errors="surrogatepass")).hexdigest()
        object.__setattr__(self, "_fingerprint", digest)

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def argument_names(self) -> frozenset[str]:
        if not isinstance(self._reference, MappingProxyType):
            raise ValueError("Tool argument names require an object reference.")
        return frozenset(name for tokens, *_ in self._prepared for name in (tokens[:1] or self._reference.keys()))

    def describe(self) -> dict:
        return {"schema": 1, "source_fingerprint": self.fingerprint,
                "exact": list(self.exact), "rows": [rule.to_dict() for rule in self.rows],
                "max_chars": self.max_chars, "max_rows": self.max_rows, "semantic_meaning_checked": False}

    def to_dict(self) -> dict:
        return {"schema": 1, "reference_json": self._reference_json, "exact": list(self.exact),
                "rows": [rule.to_dict() for rule in self.rows], "max_chars": self.max_chars, "max_rows": self.max_rows}

    @classmethod
    def from_dict(cls, data):
        encode(data)  # Reject tuples, Decimal, non-finite values and non-JSON keys.
        if type(data) is not dict or set(data) != {"schema", "reference_json", "exact", "rows", "max_chars", "max_rows"}:
            raise ValueError("Invalid serialized content policy fields.")
        if type(data["schema"]) is not int or data["schema"] != 1:
            raise ValueError("Unsupported content policy schema.")
        if type(data["exact"]) is not list or type(data["rows"]) is not list:
            raise TypeError("Serialized rules must be JSON arrays.")
        rules = []
        for item in data["rows"]:
            if type(item) is not dict or set(item) != {"path", "fields", "key", "ordered", "allow_extra_fields"}:
                raise ValueError("Invalid serialized row rule fields.")
            if type(item["fields"]) is not list:
                raise TypeError("Serialized row fields must be a JSON array.")
            rules.append(RowRule(item["path"], tuple(item["fields"]), item["key"], item["ordered"], item["allow_extra_fields"]))
        return cls.from_json(data["reference_json"], exact=tuple(data["exact"]), rows=tuple(rules),
                             max_chars=data["max_chars"], max_rows=data["max_rows"])

    def _result(self, issues, checked):
        report = {"valid": not issues, "source_fingerprint": self.fingerprint, "issues": issues,
                  "checked_scopes": checked, "issue_limit_reached": len(issues) == _MAX_ISSUES,
                  "semantic_meaning_checked": False}
        return ContentCheck(not issues, encode(report))

    def check(self, candidate) -> ContentCheck:
        try:
            text = encode(candidate)
        except (ValueError, TypeError, OverflowError, RecursionError):
            return self._result([{"code": "content_invalid", "path": "", "rule_id": None}], [])
        return self.check_json(text)

    def check_json(self, text) -> ContentCheck:
        try:
            candidate = _parse(text, self.max_chars)
        except (ValueError, TypeError, InvalidOperation, RecursionError) as exc:
            code = "content_limit" if isinstance(exc, _Limit) else "content_invalid"
            return self._result([{"code": code, "path": "", "rule_id": None}], [])
        issues, checked = [], []
        for tokens, rule_id, path, rule, expected in self._prepared:
            scope = {"rule_id": rule_id, "path": path, "kind": "rows" if rule else "exact"}
            if rule is not None:
                scope.update(rule.to_dict())
            checked.append(scope)
            selected = _at(candidate, tokens)
            code = None
            if selected is _MISSING:
                code = "content_missing"
            elif rule is None:
                if _canonical(selected) != expected:
                    code = "content_mismatch"
            else:
                code, actual = _rows(selected, rule, self.max_rows)
                if not code:
                    expected_ids, expected_rows = expected
                    actual_ids, actual_rows = actual
                    if len(expected_rows) != len(actual_rows) or (rule.key is not None and set(expected_ids) != set(actual_ids)):
                        code = "content_rows_coverage"
                    elif rule.key is not None:
                        if rule.ordered and expected_ids != actual_ids:
                            code = "content_rows_order"
                        elif dict(zip(expected_ids, expected_rows)) != dict(zip(actual_ids, actual_rows)):
                            code = "content_mismatch"
                    elif (expected_rows != actual_rows if rule.ordered else Counter(expected_rows) != Counter(actual_rows)):
                        code = "content_mismatch"
            if code:
                issues.append({"code": code, "path": path, "rule_id": rule_id})
                if len(issues) == _MAX_ISSUES:
                    break
        return self._result(issues, checked)
