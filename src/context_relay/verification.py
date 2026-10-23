"""Bounded structured claims checked against application-owned source snapshots.

Source trust, independence and business rules are application assertions. Passing
these checks does not establish general truth or validate arbitrary prose.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, localcontext
from fractions import Fraction
from types import MappingProxyType

from .content import _MISSING, _at, _freeze, _parse, _pointer
from .models import encode, nonempty_string


class _Unavailable(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerificationCheck:
    valid: bool
    _report_json: str = field(repr=False)

    @property
    def report(self):
        return json.loads(self._report_json)


class VerificationUnavailable(ValueError):
    def __init__(self, report):
        self._report_json = encode(report)
        super().__init__("Verification sources or rules are unavailable.")

    @property
    def report(self):
        return json.loads(self._report_json)


def _finite(value, name, *, positive=False):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and (not positive or value > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite {'positive ' if positive else ''}number.")
    return value


def _number(value):
    if type(value) not in (int, Decimal, Fraction):
        raise _Unavailable("verification_number_type")
    if isinstance(value, Decimal):
        if not value.is_finite() or len(value.as_tuple().digits) > 2048 or abs(value.adjusted()) > 2048:
            raise _Unavailable("verification_number_limit")
    value = Fraction(value)
    if value.numerator.bit_length() > 2048 or value.denominator.bit_length() > 2048:
        raise _Unavailable("verification_number_limit")
    return value


def _normal(value):
    """Convert final rational results to exact finite decimal JSON numbers."""
    if isinstance(value, Fraction):
        rest = value.denominator
        for divisor in (2, 5):
            while rest % divisor == 0:
                rest //= divisor
        if rest != 1:
            raise _Unavailable("verification_nonterminating_number")
        if value.denominator == 1:
            return value.numerator
        with localcontext() as context:
            context.prec = 2048
            return Decimal(value.numerator) / Decimal(value.denominator)
    if type(value) is int or isinstance(value, Decimal):
        _number(value)
    if type(value) is dict or isinstance(value, MappingProxyType):
        return {key: _normal(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_normal(item) for item in value]
    return value


def _same(left, right):
    numeric = (int, Decimal, Fraction)
    if type(left) in numeric and type(right) in numeric:
        return _number(left) == _number(right)
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _json_safe(value):
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        number = float(value)
        if not math.isfinite(number) or Decimal(str(number)) != value:
            raise _Unavailable("verification_json_precision")
        return number
    if type(value) is dict:
        return {key: _json_safe(item) for key, item in value.items()}
    if type(value) is list:
        return [_json_safe(item) for item in value]
    return value


_ARITY = {"add": (2, 64), "multiply": (2, 64), "subtract": (2, 2), "divide": (2, 2),
          "round": (2, 2), "eq": (2, 2), "lt": (2, 2), "lte": (2, 2),
          "gt": (2, 2), "gte": (2, 2), "and": (2, 64), "or": (2, 64),
          "not": (1, 1), "if": (3, 3)}


@dataclass(frozen=True, init=False, repr=False)
class VerificationPolicy:
    """Immutable source snapshots and explicit, top-level structured claims."""
    max_chars: int
    max_nodes: int
    _serialized: str
    _sources: object
    _claims: object
    _references: object
    _equivalents: object
    _fingerprint: str

    def __init__(self, sources, claims, *, max_chars=256000, max_nodes=512, equivalents=None):
        for name, value, ceiling in (("max_chars", max_chars, 2000000), ("max_nodes", max_nodes, 4096)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be a positive integer no greater than {ceiling}.")
        if type(sources) is not dict or not 1 <= len(sources) <= 128:
            raise ValueError("sources must contain between 1 and 128 named snapshots.")
        if type(claims) is not dict or not 1 <= len(claims) <= 64:
            raise ValueError("claims must contain between 1 and 64 named expressions.")
        equivalents = {} if equivalents is None else equivalents
        if type(equivalents) is not dict or set(equivalents) - set(claims):
            raise ValueError("equivalents must map existing claim names to string groups.")
        prepared_equivalents = {}
        for claim, groups in equivalents.items():
            if type(groups) is not list or len(groups) > 32:
                raise ValueError("Each claim permits at most 32 equivalence groups.")
            aliases = {}
            for index, group in enumerate(groups):
                if type(group) is not list or not 2 <= len(group) <= 32:
                    raise ValueError("Each equivalence group needs between 2 and 32 strings.")
                for value in group:
                    nonempty_string(value, "equivalent string", limit=200)
                    if value in aliases:
                        raise ValueError("Equivalent strings must be unique within and across each claim's groups.")
                    aliases[value] = index
            prepared_equivalents[claim] = MappingProxyType(aliases)
        data = {"schema": 1, "sources": sources, "claims": claims,
                "max_chars": max_chars, "max_nodes": max_nodes, "equivalents": equivalents}
        serialized = encode(data)
        if len(serialized) > max_chars:
            raise ValueError("Verification policy exceeds max_chars.")
        snapshot = _parse(serialized, max_chars)
        prepared_sources = {}
        for name, source in snapshot["sources"].items():
            nonempty_string(name, "source name", limit=80)
            required = {"data", "independence_key", "observed_at", "max_age_seconds"}
            if type(source) is not dict or set(source) not in (required, required | {"trusted"}):
                raise ValueError("Each source needs data, independence_key, observed_at and max_age_seconds.")
            nonempty_string(source["independence_key"], "independence_key", limit=200)
            if type(source.get("trusted", False)) is not bool:
                raise TypeError("trusted must be a boolean.")
            # Validate the original JSON numeric metadata, before Decimal parsing.
            original = sources[name]
            _finite(original["observed_at"], "observed_at")
            _finite(original["max_age_seconds"], "max_age_seconds", positive=True)
            prepared_sources[name] = _freeze({**source, "trusted": source.get("trusted", False)})
        count = 0
        references = {}

        def compile_expression(expression, refs, depth=0):
            nonlocal count
            count += 1
            if count > max_nodes or depth > 32:
                raise ValueError("Verification expressions exceed their node or depth limit.")
            if type(expression) is not dict:
                raise ValueError("Each expression must be an explicit literal, source, consensus or operation.")
            keys = set(expression)
            if keys == {"literal"}:
                return ("literal", _freeze(expression["literal"]))
            if keys == {"source", "path"}:
                name = expression["source"]
                if type(name) is not str or name not in prepared_sources:
                    raise ValueError("Expression references an unknown source.")
                path = _pointer(expression["path"])
                refs.add((name, path))
                return ("source", name, path)
            if keys == {"consensus", "minimum"}:
                items, minimum = expression["consensus"], expression["minimum"]
                if type(items) is not list or not 2 <= len(items) <= 64:
                    raise ValueError("Consensus needs between 2 and 64 source references.")
                if type(minimum) is not int or not 2 <= minimum <= len(items):
                    raise ValueError("Consensus minimum must be an integer from 2 to reference count.")
                if any(type(item) is not dict or set(item) != {"source", "path"} for item in items):
                    raise ValueError("Consensus accepts only source references.")
                parts = tuple(compile_expression(item, refs, depth + 1) for item in items)
                return ("consensus", minimum, parts)
            if keys != {"op", "args"} or type(expression["op"]) is not str or expression["op"] not in _ARITY:
                raise ValueError("Unsupported verification operation or expression fields.")
            op, args = expression["op"], expression["args"]
            low, high = _ARITY[op]
            if type(args) is not list or not low <= len(args) <= high:
                raise ValueError("Invalid verification operation arity.")
            return (op, *(compile_expression(item, refs, depth + 1) for item in args))

        prepared_claims = {}
        for name, expression in snapshot["claims"].items():
            nonempty_string(name, "claim name", limit=200)
            refs = set()
            prepared_claims[name] = compile_expression(expression, refs)
            references[name] = tuple(sorted(refs))
        for name, value in (("max_chars", max_chars), ("max_nodes", max_nodes), ("_serialized", serialized),
                            ("_sources", MappingProxyType(prepared_sources)),
                            ("_claims", MappingProxyType(prepared_claims)),
                            ("_references", MappingProxyType(references)),
                            ("_equivalents", MappingProxyType(prepared_equivalents)),
                            ("_fingerprint", hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8", errors="surrogatepass")).hexdigest())):
            object.__setattr__(self, name, value)

    @property
    def fingerprint(self):
        return self._fingerprint

    @property
    def argument_names(self):
        return tuple(self._claims)

    def to_dict(self):
        return json.loads(self._serialized)

    @classmethod
    def from_dict(cls, data):
        encode(data)
        required = {"schema", "sources", "claims", "max_chars", "max_nodes"}
        if type(data) is not dict or set(data) not in (required, required | {"equivalents"}):
            raise ValueError("Invalid serialized verification policy fields.")
        if type(data["schema"]) is not int or data["schema"] != 1:
            raise ValueError("Unsupported verification policy schema.")
        return cls(data["sources"], data["claims"], max_chars=data["max_chars"], max_nodes=data["max_nodes"],
                   equivalents=data.get("equivalents"))

    def _equivalence_scopes(self):
        return [{"claim": claim, "groups": len(set(aliases.values())), "values": len(aliases)}
                for claim, aliases in self._equivalents.items()]

    def describe(self):
        return {"schema": 1, "policy_fingerprint": self.fingerprint, "claims": list(self._claims),
                "source_ids": sorted({name for refs in self._references.values() for name, _ in refs}),
                "max_chars": self.max_chars, "max_nodes": self.max_nodes,
                "extra_fields_allowed": False, "semantic_meaning_checked": False,
                "equivalence_scopes": self._equivalence_scopes(),
                "source_truth_checked": False, "source_independence_asserted_by": "application"}

    def _evaluate(self, expression):
        op, *args = expression
        if op == "literal":
            return args[0]
        if op == "source":
            return _at(self._sources[args[0]]["data"], args[1])
        if op == "consensus":
            minimum, refs = args
            origins = {self._sources[ref[1]]["independence_key"] for ref in refs}
            if len(origins) < minimum:
                raise _Unavailable("verification_independence")
            values = [_normal(self._evaluate(ref)) for ref in refs]
            if any(not _same(values[0], item) for item in values[1:]):
                raise _Unavailable("verification_disagreement")
            return values[0]
        if op == "if":
            condition = self._evaluate(args[0])
            if type(condition) is not bool:
                raise _Unavailable("verification_boolean_type")
            return self._evaluate(args[1] if condition else args[2])
        values = [self._evaluate(arg) for arg in args]
        if op in {"and", "or", "not"}:
            if any(type(value) is not bool for value in values):
                raise _Unavailable("verification_boolean_type")
            return all(values) if op == "and" else any(values) if op == "or" else not values[0]
        if op == "eq":
            return _same(_normal(values[0]), _normal(values[1]))
        numbers = [_number(value) for value in values]
        if op in {"lt", "lte", "gt", "gte"}:
            left, right = numbers
            return {"lt": left < right, "lte": left <= right, "gt": left > right, "gte": left >= right}[op]
        if op == "add":
            result = sum(numbers)
        elif op == "multiply":
            result = Fraction(1)
            for number in numbers:
                result = _number(result * number)
        elif op == "subtract":
            result = numbers[0] - numbers[1]
        elif op == "divide":
            if not numbers[1]:
                raise _Unavailable("verification_divide_by_zero")
            result = numbers[0] / numbers[1]
        else:  # round uses decimal places and exact ties-to-even rational rounding.
            places = values[1]
            if type(places) is not int or not -24 <= places <= 24:
                raise _Unavailable("verification_round_places")
            result = round(numbers[0], places)
        return _number(result)

    def _resolve(self, now):
        if now is None:
            now = time.time()
        _finite(now, "now")
        now = Fraction(str(now))
        values, issues = {}, []
        for claim, expression in self._claims.items():
            try:
                for name, path in self._references[claim]:
                    source = self._sources[name]
                    if not source["trusted"]:
                        raise _Unavailable("verification_untrusted_source")
                    observed, max_age = Fraction(str(source["observed_at"])), Fraction(str(source["max_age_seconds"]))
                    if observed > now:
                        raise _Unavailable("verification_future_source")
                    if now - observed > max_age:
                        raise _Unavailable("verification_stale_source")
                    if _at(source["data"], path) is _MISSING:
                        raise _Unavailable("verification_missing_source_path")
                values[claim] = _normal(self._evaluate(expression))
            except _Unavailable as exc:
                issues.append({"code": exc.code, "claim": claim})
        return values, issues

    def _result(self, issues, ready, checked=()):
        return VerificationCheck(not issues, encode({"valid": not issues, "ready": ready,
            "issues": issues, "checked_claims": list(checked), "unchecked_fields": [],
            "policy_fingerprint": self.fingerprint, "semantic_meaning_checked": False,
            "equivalence_scopes": self._equivalence_scopes(),
            "source_truth_checked": False, "source_independence_asserted_by": "application"}))

    def readiness(self, *, now=None):
        values, issues = self._resolve(now)
        return self._result(issues, not issues)

    def expected(self, *, now=None):
        values, issues = self._resolve(now)
        if issues:
            raise VerificationUnavailable(self._result(issues, False).report)
        try:
            return _json_safe(values)
        except _Unavailable as exc:
            raise VerificationUnavailable(self._result([{"code": exc.code, "claim": None}], True).report) from None

    def check(self, candidate, *, now=None):
        try:
            text = encode(candidate)
        except (ValueError, TypeError, OverflowError, RecursionError, DecimalException):
            _, issues = self._resolve(now)
            return self._result(issues + [{"code": "verification_invalid_json", "claim": None}], not issues)
        return self.check_json(text, now=now)

    def check_json(self, raw, *, now=None):
        values, issues = self._resolve(now)
        ready = not issues
        try:
            candidate = _parse(raw, self.max_chars)
            if type(candidate) is not dict:
                raise ValueError("Claims must be a JSON object.")
        except (ValueError, TypeError, OverflowError, RecursionError, DecimalException):
            return self._result(issues + [{"code": "verification_invalid_json", "claim": None}], ready)
        if set(candidate) - set(self._claims):
            issues.append({"code": "verification_extra_fields", "claim": None})
        for claim in self._claims:
            if claim not in candidate:
                issues.append({"code": "verification_missing_claim", "claim": claim})
            elif claim in values:
                try:
                    actual, expected = candidate[claim], values[claim]
                    equal = _same(actual, expected)
                    if not equal and type(actual) is str and type(expected) is str:
                        aliases = self._equivalents.get(claim, {})
                        equal = actual in aliases and expected in aliases and aliases[actual] == aliases[expected]
                except _Unavailable:
                    equal = False
                if not equal:
                    issues.append({"code": "verification_mismatch", "claim": claim})
        return self._result(issues, ready, values)
