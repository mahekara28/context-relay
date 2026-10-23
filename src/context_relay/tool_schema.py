"""A bounded explicit JSON Schema subset for final native tool arguments.

This is not a general JSON Schema engine. Unknown types and keywords are rejected
at setup rather than delegated to provider-side strict-mode assumptions.
"""
from __future__ import annotations
import math

from .models import clone, encode


_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_KEYS = frozenset({"type", "properties", "required", "additionalProperties", "items", "enum", "const",
                   "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "description", "title"})


class SchemaViolation(ValueError):
    def __init__(self, path, reason):
        self.path, self.reason = path, reason
        super().__init__(f"Tool arguments violate the supported schema at {path or '/'}: {reason}.")


def _types(schema):
    value = schema.get("type")
    return value if type(value) is list else [value]


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _equal(left, right):
    # JSON Schema treats 1 and 1.0 as the same number, but never True as 1.
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def validate_schema(schema):
    captured = clone(schema)
    if len(encode(captured)) > 32000:
        raise ValueError("Tool parameter schema exceeds 32000 characters.")
    nodes = 0
    def visit(node, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 256 or depth > 16:
            raise ValueError("Tool schema exceeds 256 nodes or 16 nesting levels.")
        if type(node) is not dict or set(node) - _KEYS:
            raise ValueError("Unsupported tool schema keyword or shape.")
        kinds = _types(node)
        if (not kinds or any(type(kind) is not str or kind not in _TYPES for kind in kinds)
                or len(kinds) != len(set(kinds))):
            raise ValueError("Tool schema requires known, unique JSON types.")
        for key in ("description", "title"):
            if key in node and type(node[key]) is not str:
                raise ValueError(key + " must be text.")
        if "object" in kinds:
            props, required = node.get("properties"), node.get("required")
            if (type(props) is not dict or len(props) > 64 or type(required) is not list
                    or any(type(key) is not str for key in required)
                    or len(required) != len(set(required)) or set(required) != set(props)
                    or node.get("additionalProperties") is not False):
                raise ValueError("Objects require all declared fields and additionalProperties=False (at most 64 fields).")
            for child in props.values():
                visit(child, depth + 1)
        elif {"properties", "required", "additionalProperties"} & node.keys():
            raise ValueError("Object keywords require type object.")
        if "array" in kinds:
            if "items" not in node:
                raise ValueError("Arrays require an items schema.")
            visit(node["items"], depth + 1)
        elif {"items", "minItems", "maxItems"} & node.keys():
            raise ValueError("Array keywords require type array.")
        if {"minLength", "maxLength"} & node.keys() and "string" not in kinds:
            raise ValueError("String length keywords require type string.")
        for low, high in (("minLength", "maxLength"), ("minItems", "maxItems")):
            for key in (low, high):
                if key in node and (type(node[key]) is not int or not 0 <= node[key] <= 1000000):
                    raise ValueError(key + " must be a nonnegative integer no greater than 1000000.")
            if low in node and high in node and node[low] > node[high]:
                raise ValueError("Schema lower bound exceeds its upper bound.")
        for key in ("minimum", "maximum"):
            if key in node and (not set(kinds) & {"integer", "number"} or not _number(node[key])):
                raise ValueError(key + " requires a finite numeric type and bound.")
        if "minimum" in node and "maximum" in node and node["minimum"] > node["maximum"]:
            raise ValueError("Schema lower bound exceeds its upper bound.")
        if "enum" in node and (type(node["enum"]) is not list or not 1 <= len(node["enum"]) <= 128):
            raise ValueError("enum requires 1 through 128 JSON values.")
    visit(captured, 0)
    return captured


def validate_arguments(schema, arguments):
    """Check final repaired/normalized arguments; no coercion or model call."""
    arguments = clone(arguments)
    nodes = 0
    def visit(node, value, path, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 4096 or depth > 16:
            raise SchemaViolation(path, "validation_limit")
        matches = {"object": type(value) is dict, "array": type(value) is list,
                   "string": type(value) is str, "boolean": type(value) is bool,
                   "null": value is None, "number": _number(value),
                   "integer": type(value) is int or type(value) is float and _number(value) and value.is_integer()}
        if not any(matches[kind] for kind in _types(node)):
            raise SchemaViolation(path, "type")
        if "enum" in node and not any(_equal(value, item) for item in node["enum"]):
            raise SchemaViolation(path, "enum")
        if "const" in node and not _equal(value, node["const"]):
            raise SchemaViolation(path, "const")
        if type(value) is dict:
            if set(value) != set(node["properties"]):
                raise SchemaViolation(path, "fields")
            for key, item in value.items():
                visit(node["properties"][key], item, path + "/" + key.replace("~", "~0").replace("/", "~1"), depth + 1)
        elif type(value) is list:
            if not node.get("minItems", 0) <= len(value) <= node.get("maxItems", 1000000):
                raise SchemaViolation(path, "array_length")
            for index, item in enumerate(value):
                visit(node["items"], item, path + "/" + str(index), depth + 1)
        elif type(value) is str:
            if not node.get("minLength", 0) <= len(value) <= node.get("maxLength", 1000000):
                raise SchemaViolation(path, "string_length")
        elif type(value) in (int, float):
            if value < node.get("minimum", value) or value > node.get("maximum", value):
                raise SchemaViolation(path, "number_bound")
    visit(schema, arguments, "", 0)
