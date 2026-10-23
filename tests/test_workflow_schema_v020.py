"""Local schema checks do not rely on provider strict-mode promises."""
import unittest
from context_relay.tool_schema import SchemaViolation, validate_arguments, validate_schema


class WorkflowSchemaTests(unittest.TestCase):
    def test_nested_shapes_and_types_are_enforced(self):
        schema = validate_schema({"type": "object", "properties": {"rows": {
            "type": "array", "items": {"type": "object", "properties": {
                "count": {"type": "integer", "minimum": 0},
                "label": {"type": ["string", "null"], "maxLength": 20}},
                "required": ["count", "label"], "additionalProperties": False}, "maxItems": 2}},
            "required": ["rows"], "additionalProperties": False})
        validate_arguments(schema, {"rows": [{"count": 1, "label": None}]})
        for value in ({"rows": [{"count": True, "label": "x"}]},
                      {"rows": [{"count": -1, "label": "x"}]},
                      {"rows": [{"count": 1, "label": 7}]},
                      {"rows": [{"count": 1, "label": "x", "extra": 1}]},
                      {"rows": [{"count": 1}]}, {"rows": [{"count": 1, "label": "x"}] * 3}):
            with self.subTest(value=value), self.assertRaises(SchemaViolation):
                validate_arguments(schema, value)

    def test_unknown_schema_types_keywords_and_unbounded_shapes_rejected(self):
        for schema in ({"type": "banana"}, {"type": "string", "pattern": ".*"},
                       {"type": "array"}, {"type": "object"},
                       {"type": ["string", "string"]}, {"type": "string", "minimum": 0},
                       {"type": "integer", "minimum": 2, "maximum": 1},
                       {"type": "string", "maxLength": True}):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                validate_schema(schema)

    def test_numeric_enum_equivalents_preserve_boolean_distinction(self):
        schema = validate_schema({"type": ["integer", "boolean"], "enum": [1]})
        validate_arguments(schema, 1.0)
        with self.assertRaises(SchemaViolation):
            validate_arguments(schema, True)

    def test_schema_capture_is_defensive(self):
        source = {"type": "string", "enum": ["allowed"]}
        schema = validate_schema(source)
        source["enum"].append("other")
        with self.assertRaises(SchemaViolation):
            validate_arguments(schema, "other")


if __name__ == "__main__":
    unittest.main()
