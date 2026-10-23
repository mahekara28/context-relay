"""Literal, lossless source preservation; no provider or model required."""

import copy
import json
import unittest
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext

from context_relay.content import ContentCheck, ContentPolicy, RowRule


class ContentPolicyTests(unittest.TestCase):
    def assert_code(self, check, code):
        self.assertIsInstance(check, ContentCheck)
        self.assertFalse(check.valid)
        self.assertIn(code, {issue["code"] for issue in check.report["issues"]})

    def test_exact_scope_preserves_original_historical_names_and_dates(self):
        reference = {"record": {"place": "British India", "name": "Lakshmibai", "date": "1857-05-10"}, "note": "original"}
        policy = ContentPolicy(reference, exact=("/record",))
        candidate = copy.deepcopy(reference)
        candidate["note"] = "unprotected annotation"
        self.assertTrue(policy.check(candidate).valid)
        for key, changed in (("place", "India"), ("name", "lakshmibai"), ("date", "1857-10-05")):
            with self.subTest(key=key):
                altered = copy.deepcopy(candidate)
                altered["record"][key] = changed
                self.assert_code(policy.check(altered), "content_mismatch")
        self.assertFalse(policy.describe()["semantic_meaning_checked"])

    def test_missing_null_empty_and_types_remain_distinct(self):
        for value, wrong in ((None, ""), (True, 1), (1, 1.0), (1.0, 1), (False, 0), ("1", 1)):
            with self.subTest(value=value, wrong=wrong):
                policy = ContentPolicy({"v": value}, exact=("/v",))
                self.assertTrue(policy.check({"v": value}).valid)
                self.assert_code(policy.check({"v": wrong}), "content_mismatch")
                self.assert_code(policy.check({}), "content_missing")

    def test_exact_subtree_requires_complete_same_shape(self):
        policy = ContentPolicy({"v": {"a": [1, 2]}}, exact=("/v",))
        for candidate in ({"v": {"a": [2, 1]}}, {"v": {"a": [1, 2, 3]}},
                          {"v": {"a": [1, 2], "b": 0}}, {"v": []}):
            self.assert_code(policy.check(candidate), "content_mismatch")

    def test_lossless_float_tokens_reject_sub_ulp_and_underflow_changes(self):
        cases = (("1.0000000000000000000000000001", "1.0"),
                 ("1e-1000", "0.0"), ("0.100000000000000000001", "0.1"),
                 ("9007199254740992.1", "9007199254740992.0"))
        for original, changed in cases:
            with self.subTest(original=original):
                policy = ContentPolicy.from_json('{"n":' + original + '}', exact=("/n",))
                self.assertTrue(policy.check_json('{"n":' + original + '}').valid)
                self.assert_code(policy.check_json('{"n":' + changed + '}'), "content_mismatch")
                restored = ContentPolicy.from_dict(policy.to_dict())
                self.assertEqual(restored.fingerprint, policy.fingerprint)
                self.assertEqual(restored.to_dict()["reference_json"], policy.to_dict()["reference_json"])
                self.assert_code(restored.check_json('{"n":' + changed + '}'), "content_mismatch")

    def test_decimal_comparison_does_not_round_in_small_decimal_context(self):
        with localcontext() as context:
            context.prec = 2
            policy = ContentPolicy.from_json('{"n":123456789.123456789}', exact=("/n",))
            self.assertTrue(policy.check_json('{"n":123456789.1234567890}').valid)
            self.assert_code(policy.check_json('{"n":123456789.123456788}'), "content_mismatch")
        float_policy = ContentPolicy.from_json("1.00", exact=("",))
        self.assertTrue(float_policy.check_json("1e0").valid)
        self.assert_code(float_policy.check_json("1"), "content_mismatch")

    def test_python_inputs_are_only_checked_at_received_precision(self):
        policy = ContentPolicy({"n": 1.0}, exact=("/n",))
        self.assertTrue(policy.check({"n": float("1.00000000000000000001")}).valid)
        self.assert_code(policy.check_json('{"n":1.00000000000000000001}'), "content_mismatch")
        self.assert_code(policy.check({"n": Decimal("1.0")}), "content_invalid")
        with self.assertRaises(ValueError):
            ContentPolicy({"n": Decimal("1.0")}, exact=("/n",))

    def test_duplicate_keys_nonfinite_and_malformed_json_are_rejected(self):
        policy = ContentPolicy({"n": 1}, exact=("/n",))
        malformed = ('{"n":1,"n":1}', '{"n":1,"extra":{"x":0,"x":0}}',
                     '{"n":NaN}', '{"n":Infinity}', '{"n":-Infinity}', '{"n":1} trailing', "{", "")
        for text in malformed:
            with self.subTest(text=text):
                self.assert_code(policy.check_json(text), "content_invalid")
                with self.assertRaises(ValueError):
                    ContentPolicy.from_json(text, exact=("/n",))
        for candidate in (float("inf"), float("nan"), {1: "value"}, (1,), {1}, object()):
            self.assert_code(policy.check(candidate), "content_invalid")
        self.assert_code(policy.check_json(b'{"n":1}'), "content_invalid")

    def test_huge_but_finite_exponents_stay_decimal_and_do_not_overflow(self):
        policy = ContentPolicy.from_json('{"n":1e10000}', exact=("/n",))
        self.assertTrue(policy.check_json('{"n":10e9999}').valid)
        self.assert_code(policy.check_json('{"n":1e9999}'), "content_mismatch")

    def test_row_field_names_are_literal_not_pointers(self):
        reference = {"rows": [{"id": "r", "birth/date": "1900-01-01", "~name": "Original"}]}
        policy = ContentPolicy(reference, rows=(RowRule("/rows", ("birth/date", "~name"), key="id"),))
        self.assertTrue(policy.check(reference).valid)
        changed = {"rows": [{"id": "r", "birth": {"date": "1900-01-01"}, "~name": "Original"}]}
        self.assert_code(policy.check(changed), "content_missing")

    def test_keyed_rows_preserve_coverage_association_and_order(self):
        reference = {"rows": [{"id": "a", "name": "First", "score": 1}, {"id": "b", "name": "Second", "score": 2}]}
        policy = ContentPolicy(reference, rows=(RowRule("/rows", ("name", "score"), key="id"),))
        self.assertTrue(policy.check(reference).valid)
        self.assert_code(policy.check({"rows": list(reversed(reference["rows"]))}), "content_rows_order")
        for rows in (reference["rows"][:1], reference["rows"] + [{"id": "c", "name": "Third", "score": 3}]):
            self.assert_code(policy.check({"rows": rows}), "content_rows_coverage")
        swapped = [{"id": "a", "name": "Second", "score": 2}, {"id": "b", "name": "First", "score": 1}]
        self.assert_code(policy.check({"rows": swapped}), "content_mismatch")

    def test_keyed_unordered_rows_still_preserve_identity_value_association(self):
        reference = {"rows": [{"id": 1, "name": "First"}, {"id": "1", "name": "Second"}]}
        policy = ContentPolicy(reference, rows=(RowRule("/rows", ("name",), key="id", ordered=False),))
        self.assertTrue(policy.check({"rows": list(reversed(reference["rows"]))}).valid)
        wrong = {"rows": [{"id": "1", "name": "First"}, {"id": 1, "name": "Second"}]}
        self.assert_code(policy.check(wrong), "content_mismatch")
        self.assert_code(policy.check({"rows": [{"id": 2, "name": "First"}, reference["rows"][1]]}), "content_rows_coverage")

    def test_identity_key_is_implicitly_preserved_and_can_be_only_projection(self):
        policy = ContentPolicy({"rows": [{"id": "a", "note": "unprotected"}]},
                               rows=(RowRule("/rows", (), key="id"),))
        self.assertTrue(policy.check({"rows": [{"id": "a", "note": "different"}]}).valid)
        self.assert_code(policy.check({"rows": [{"id": "b"}]}), "content_rows_coverage")

    def test_invalid_or_duplicate_identities_fail_source_and_candidate(self):
        policy = ContentPolicy({"rows": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]},
                               rows=(RowRule("/rows", ("v",), key="id"),))
        for identity in ("", " \n", True, False, 1.0, None, [], {}):
            rows = [{"id": identity, "v": 1}, {"id": "b", "v": 2}]
            with self.subTest(identity=identity):
                self.assert_code(policy.check({"rows": rows}), "content_rows_identity")
                with self.assertRaises(ValueError):
                    ContentPolicy({"rows": rows}, rows=policy.rows)
        duplicate = {"rows": [{"id": "a", "v": 1}, {"id": "a", "v": 2}]}
        self.assert_code(policy.check(duplicate), "content_rows_identity")
        with self.assertRaises(ValueError):
            ContentPolicy(duplicate, rows=policy.rows)

    def test_unkeyed_ordered_rows_compare_positions(self):
        policy = ContentPolicy({"rows": [{"v": 1}, {"v": 2}]}, rows=(RowRule("/rows", ("v",)),))
        self.assertTrue(policy.check({"rows": [{"v": 1}, {"v": 2}]}).valid)
        self.assert_code(policy.check({"rows": [{"v": 2}, {"v": 1}]}), "content_mismatch")

    def test_unordered_projection_is_multiset_of_whole_rows(self):
        rows = [{"name": "A", "n": 1}, {"name": "B", "n": 2}, {"name": "A", "n": 1}]
        policy = ContentPolicy({"rows": rows}, rows=(RowRule("/rows", ("name", "n"), ordered=False),))
        self.assertTrue(policy.check({"rows": list(reversed(rows))}).valid)
        for changed in ([{"name": "A", "n": 2}, {"name": "B", "n": 1}, rows[2]],
                        [rows[0], rows[1], rows[1]], [{"name": "A", "n": True}, rows[1], rows[2]]):
            self.assert_code(policy.check({"rows": changed}), "content_mismatch")

    def test_row_shape_fields_missing_and_extra_fields(self):
        strict = ContentPolicy({"rows": [{"id": "a", "v": None}]},
                               rows=(RowRule("/rows", ("v",), key="id", allow_extra_fields=False),))
        self.assertTrue(strict.check({"rows": [{"v": None, "id": "a"}]}).valid)
        self.assert_code(strict.check({"rows": [{"id": "a", "v": None, "extra": 1}]}), "content_rows_fields")
        self.assert_code(strict.check({"rows": [{"id": "a"}]}), "content_missing")
        for rows in ({}, [None], [1], "text"):
            self.assert_code(strict.check({"rows": rows}), "content_rows_type")
        with self.assertRaises(ValueError):
            ContentPolicy({"rows": [{"id": "a", "v": 1, "extra": 2}]}, rows=strict.rows)

    def test_decimal_row_projection_is_lossless(self):
        policy = ContentPolicy.from_json('{"rows":[{"id":"a","v":0.0000000000000000000001}]}',
                                         rows=(RowRule("/rows", ("v",), key="id", ordered=False),))
        self.assertTrue(policy.check_json('{"rows":[{"v":1e-22,"id":"a"}]}').valid)
        self.assert_code(policy.check_json('{"rows":[{"id":"a","v":0.0}]}'), "content_mismatch")

    def test_pointer_escaping_object_keys_and_argument_names(self):
        reference = {"a/b": {"~key": ["protected"]}, "extra": "unprotected"}
        policy = ContentPolicy(reference, exact=("/a~1b/~0key/0",))
        self.assertTrue(policy.check(reference).valid)
        self.assertEqual(policy.argument_names, frozenset({"a/b"}))
        whole = ContentPolicy(reference, exact=("",))
        self.assertEqual(whole.argument_names, frozenset(reference))
        scalar = ContentPolicy("protected", exact=("",))
        self.assertTrue(scalar.check("protected").valid)
        with self.assertRaises(ValueError):
            _ = scalar.argument_names
        self.assertTrue(ContentPolicy({"": 1}, exact=("/",)).check({"": 1}).valid)

    def test_invalid_pointers_and_overlaps_fail_before_checking(self):
        reference = {"items": [{"v": 1}], "other": 2}
        for pointer in ("items", "#/items", "/items/~2", "/items/~", "/items/*", "/items/-1", "/items/00", "/missing", "/" + "x" * 512, "/x" * 33):
            with self.subTest(pointer=pointer), self.assertRaises((ValueError, TypeError)):
                ContentPolicy(reference, exact=(pointer,))
        for exact, rows in ((("", "/other"), ()), (("/items", "/items"), ()),
                            (("/items/0/v",), (RowRule("/items", ("v",)),))):
            with self.assertRaises(ValueError):
                ContentPolicy(reference, exact=exact, rows=rows)

    def test_source_validation_rules_and_limits_are_eager(self):
        for kwargs in ({}, {"exact": ()}, {"exact": ["/v"]}, {"exact": ("/v",), "max_chars": True},
                       {"exact": ("/v",), "max_chars": 2000001}, {"exact": ("/v",), "max_rows": 0},
                       {"exact": ("/v",), "max_rows": 100001}, {"rows": ({"path": "/v"},)}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                ContentPolicy({"v": 1}, **kwargs)
        for fields, key in (((), None), (("v", "v"), None), (["v"], None), ((1,), None)):
            with self.assertRaises((ValueError, TypeError)):
                RowRule("/rows", fields, key=key)
        for kwargs in ({"ordered": 1}, {"allow_extra_fields": "yes"}, {"key": 1}):
            with self.assertRaises((ValueError, TypeError)):
                RowRule("/rows", ("v",), **kwargs)
        with self.assertRaises(ValueError):
            ContentPolicy({"rows": [{}]}, rows=(RowRule("/rows", ("v",)),))
        with self.assertRaises(ValueError):
            ContentPolicy({"v": 0}, exact=("/v",), max_chars=1)

    def test_candidate_character_row_and_depth_limits_never_allow_truncated_data(self):
        policy = ContentPolicy({"rows": [{"v": 1}]}, rows=(RowRule("/rows", ("v",)),), max_chars=100, max_rows=2)
        self.assert_code(policy.check({"rows": [{"v": 1}] * 3}), "content_limit")
        self.assert_code(policy.check({"rows": [{"v": 1}], "other": "x" * 101}), "content_limit")
        with self.assertRaises(ValueError):
            ContentPolicy({"rows": [{"v": 1}] * 3}, rows=policy.rows, max_rows=2)
        nested = "[" * 65 + "0" + "]" * 65
        self.assert_code(ContentPolicy(0, exact=("",)).check_json(nested), "content_limit")
        with self.assertRaises(ValueError):
            ContentPolicy.from_json(nested, exact=("",))

    def test_report_caps_issues_without_turning_failure_into_success(self):
        reference = {f"f{i}": i for i in range(64)}
        policy = ContentPolicy(reference, exact=tuple("/" + key for key in reference))
        check = policy.check({})
        self.assertFalse(check.valid)
        self.assertEqual(len(check.report["issues"]), 20)
        self.assertTrue(check.report["issue_limit_reached"])
        with self.assertRaises(ValueError):
            ContentPolicy({**reference, "too_many": 65}, exact=tuple("/" + key for key in reference) + ("/too_many",))

    def test_mutations_cannot_change_captured_source_rules_or_reports(self):
        reference = {"rows": [{"id": "a", "v": [1, 2]}]}
        rule = RowRule("/rows", ("v",), key="id")
        policy = ContentPolicy(reference, rows=(rule,))
        fingerprint = policy.fingerprint
        reference["rows"][0]["v"].append(3)
        self.assert_code(policy.check(reference), "content_mismatch")
        self.assertTrue(policy.check({"rows": [{"id": "a", "v": [1, 2]}]}).valid)
        for obj, name, value in ((rule, "ordered", False), (policy, "max_chars", 1)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, name, value)
        serialized = policy.to_dict()
        serialized["rows"][0]["fields"].append("other")
        described = policy.describe()
        described["rows"][0]["fields"].clear()
        check = policy.check(reference)
        check.report["issues"].clear()
        self.assertFalse(check.valid)
        self.assertTrue(check.report["issues"])
        self.assertEqual(policy.fingerprint, fingerprint)

    def test_metadata_and_reports_never_expose_reference_candidate_or_identities(self):
        source_secret = "source-private-value"
        candidate_secret = "candidate-private-value"
        policy = ContentPolicy({"rows": [{"id": "private-row-identity", "v": source_secret}]},
                               rows=(RowRule("/rows", ("v",), key="id"),))
        check = policy.check({"rows": [{"id": "private-row-identity", "v": candidate_secret}]})
        self.assertFalse(check.valid)
        metadata = json.dumps({"description": policy.describe(), "report": check.report})
        for secret in (source_secret, candidate_secret, "private-row-identity"):
            self.assertNotIn(secret, metadata)
        self.assertTrue(all(set(issue) == {"code", "path", "rule_id"} for issue in check.report["issues"]))
        self.assertEqual(check.report["checked_scopes"], [{"rule_id": "rows:0", "path": "/rows", "kind": "rows",
                                                         "fields": ["v"], "key": "id", "ordered": True,
                                                         "allow_extra_fields": True}])
        check.report["checked_scopes"][0]["fields"].clear()
        self.assertEqual(check.report["checked_scopes"][0]["fields"], ["v"])
        self.assertEqual(policy.rows[0].fields, ("v",))

    def test_serialized_rows_round_trip_and_reject_unknown_or_non_json_fields(self):
        policy = ContentPolicy({"rows": [{"id": "a", "v": 1}]},
                               rows=(RowRule("/rows", ("v",), key="id", ordered=False, allow_extra_fields=False),))
        wire = json.loads(json.dumps(policy.to_dict()))
        restored = ContentPolicy.from_dict(wire)
        self.assertEqual(restored.fingerprint, policy.fingerprint)
        self.assertTrue(restored.check({"rows": [{"v": 1, "id": "a"}]}).valid)
        for field, wrong in (("fields", ("v",)), ("ordered", 1), ("key", True), ("extra", False)):
            altered = copy.deepcopy(wire)
            altered["rows"][0][field] = wrong
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                ContentPolicy.from_dict(altered)

    def test_serialization_is_strict_json_and_fingerprint_is_stable(self):
        policy = ContentPolicy.from_json('{"b":2,"a":1.000000000000000000001}', exact=("/a",))
        wire = json.loads(json.dumps(policy.to_dict()))
        self.assertEqual(ContentPolicy.from_dict(wire).fingerprint, policy.fingerprint)
        reordered = ContentPolicy.from_json('{"a":1.000000000000000000001,"b":2}', exact=("/a",))
        self.assertEqual(reordered.fingerprint, policy.fingerprint)
        for field, wrong in (("schema", True), ("schema", 2), ("exact", ("/a",)), ("rows", {}), ("reference_json", 1)):
            altered = dict(wire)
            altered[field] = wrong
            with self.subTest(field=field, wrong=wrong), self.assertRaises((ValueError, TypeError)):
                ContentPolicy.from_dict(altered)
        with self.assertRaises(ValueError):
            ContentPolicy.from_dict({**wire, "extra": 1})
        with self.assertRaises(ValueError):
            ContentPolicy.from_dict({key: value for key, value in wire.items() if key != "max_rows"})


if __name__ == "__main__":
    unittest.main()
