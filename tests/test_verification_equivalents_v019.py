"""Explicit finite aliases do not weaken source, arithmetic or content checks."""
import unittest

from context_relay import ContentPolicy
from context_relay.verification import VerificationPolicy, VerificationUnavailable


def sources():
    return {"ledger": {"data": {"format": "JSON", "decision": "approved"},
            "trusted": True, "independence_key": "ledger", "observed_at": 100,
            "max_age_seconds": 60}}


def policy(equivalents=None, claims=None, source_data=None):
    return VerificationPolicy(source_data or sources(), claims or {
        "format": {"source": "ledger", "path": "/format"}}, equivalents=equivalents)


class VerificationEquivalentsTests(unittest.TestCase):
    def test_only_declared_aliases_pass(self):
        contract = policy({"format": [["JSON", "json"]]})
        self.assertTrue(contract.check({"format": "json"}, now=120).valid)
        self.assertTrue(contract.check({"format": "JSON"}, now=120).valid)
        for value in ("Json", " JSON", "json ", "ＪＳＯＮ", "JЅON", "YAML", 1, True, None):
            with self.subTest(value=value):
                self.assertFalse(contract.check({"format": value}, now=120).valid)
        self.assertFalse(policy().check({"format": "json"}, now=120).valid)

    def test_aliases_are_scoped_to_claim_and_top_level(self):
        claims = {"format": {"literal": "JSON"}, "name": {"literal": "JSON"},
                  "nested": {"literal": {"format": "JSON"}}}
        contract = policy({"format": [["JSON", "json"]]}, claims)
        candidate = {"format": "json", "name": "JSON", "nested": {"format": "JSON"}}
        self.assertTrue(contract.check(candidate, now=120).valid)
        candidate["name"] = "json"
        self.assertFalse(contract.check(candidate, now=120).valid)
        candidate["name"] = "JSON"
        candidate["nested"]["format"] = "json"
        self.assertFalse(contract.check(candidate, now=120).valid)

    def test_groups_remain_separate_and_canonical_value_unchanged(self):
        contract = policy({"format": [["JSON", "json"], ["CSV", "csv"]]})
        self.assertFalse(contract.check({"format": "csv"}, now=120).valid)
        self.assertEqual(contract.expected(now=120), {"format": "JSON"})
        contract = policy({"format": [["json", "JSON"]]})
        self.assertEqual(contract.expected(now=120), {"format": "JSON"})

    def test_numeric_and_boolean_claims_cannot_use_string_aliases(self):
        contract = policy({"format": [["1", "one"]]}, {"format": {"literal": 1}})
        self.assertTrue(contract.check({"format": 1}, now=120).valid)
        self.assertFalse(contract.check({"format": "one"}, now=120).valid)
        self.assertFalse(contract.check({"format": "1"}, now=120).valid)
        self.assertFalse(contract.check({"format": True}, now=120).valid)
        contract = policy({"format": [["true", "yes"]]}, {"format": {"literal": True}})
        self.assertFalse(contract.check({"format": "yes"}, now=120).valid)

    def test_invalid_groups_are_rejected(self):
        invalid = [[], {"absent": [["JSON", "json"]]}, {"format": "JSON"},
                   {"format": [["JSON"]]}, {"format": [["JSON", "JSON"]]},
                   {"format": [["JSON", "json"], ["json", "Json"]]},
                   {"format": [["JSON", ""]]}, {"format": [["JSON", "   "]]},
                   {"format": [["JSON", "x" * 201]]}, {"format": [["JSON", 1]]},
                   {"format": [["JSON", True]]}, {"format": [("JSON", "json")]},
                   {"format": [[str(i) for i in range(33)]]},
                   {"format": [[f"a{i}", f"b{i}"] for i in range(33)]}]
        for value in invalid:
            with self.subTest(value=str(value)[:100]):
                with self.assertRaises((ValueError, TypeError)):
                    policy(value)

    def test_same_strings_can_belong_to_different_claim_groups(self):
        contract = policy({"a": [["JSON", "json"]], "b": [["JSON", "JavaScript Object Notation"]]},
                          {"a": {"literal": "JSON"}, "b": {"literal": "JSON"}})
        self.assertTrue(contract.check({"a": "json", "b": "JavaScript Object Notation"}, now=120).valid)
        self.assertFalse(contract.check({"a": "json", "b": "json"}, now=120).valid)

    def test_serialized_old_and_new_contracts(self):
        contract = policy({"format": [["JSON", "json"]]})
        data = contract.to_dict()
        self.assertEqual(data["equivalents"], {"format": [["JSON", "json"]]})
        restored = VerificationPolicy.from_dict(data)
        self.assertEqual(restored.fingerprint, contract.fingerprint)
        self.assertTrue(restored.check({"format": "json"}, now=120).valid)
        old = policy().to_dict()
        del old["equivalents"]
        legacy = VerificationPolicy.from_dict(old)
        self.assertTrue(legacy.check({"format": "JSON"}, now=120).valid)
        self.assertFalse(legacy.check({"format": "json"}, now=120).valid)
        self.assertEqual(legacy.to_dict()["equivalents"], {})

    def test_mutating_alias_inputs_or_serialization_does_not_change_policy(self):
        aliases = {"format": [["JSON", "json"]]}
        contract = policy(aliases)
        original_fingerprint = contract.fingerprint
        aliases["format"][0].append("csv")
        exported = contract.to_dict()
        exported["equivalents"]["format"][0].append("CSV")
        self.assertFalse(contract.check({"format": "csv"}, now=120).valid)
        self.assertFalse(contract.check({"format": "CSV"}, now=120).valid)
        self.assertEqual(contract.fingerprint, original_fingerprint)
        self.assertTrue(contract.check({"format": "json"}, now=120).valid)

    def test_unavailable_sources_still_block_aliases(self):
        aliases = {"format": [["JSON", "json"]]}
        contract = policy(aliases)
        self.assertFalse(contract.check({"format": "json"}, now=161).valid)
        self.assertFalse(contract.check({"format": "json"}, now=99).report["ready"])
        untrusted = sources()
        untrusted["ledger"]["trusted"] = False
        contract = policy(aliases, source_data=untrusted)
        self.assertFalse(contract.check({"format": "json"}, now=120).report["ready"])
        with self.assertRaises(VerificationUnavailable):
            contract.expected(now=120)

    def test_aliases_do_not_relax_source_consensus(self):
        data = sources()
        data["second"] = {**data["ledger"], "data": {"format": "json"}, "independence_key": "second"}
        contract = policy({"format": [["JSON", "json"]]}, {"format": {"consensus": [
            {"source": "ledger", "path": "/format"}, {"source": "second", "path": "/format"}],
            "minimum": 2}}, data)
        self.assertFalse(contract.readiness(now=120).valid)
        self.assertFalse(contract.check({"format": "json"}, now=120).valid)

    def test_metadata_reports_only_scopes_counts(self):
        aliases = {"format": [["JSON", "private alias"], ["CSV", "private csv"]]}
        contract = policy(aliases)
        expected = [{"claim": "format", "groups": 2, "values": 4}]
        report = contract.check({"format": "private alias"}, now=120).report
        self.assertTrue(report["valid"])
        self.assertEqual(report["equivalence_scopes"], expected)
        self.assertEqual(contract.describe()["equivalence_scopes"], expected)
        for text in (str(report), str(contract.describe())):
            self.assertNotIn("private alias", text)
            self.assertNotIn("private csv", text)
            self.assertNotIn("JSON", text)
        report["equivalence_scopes"][0]["values"] = 999
        self.assertEqual(contract.describe()["equivalence_scopes"], expected)
        self.assertFalse(report["semantic_meaning_checked"])
        self.assertFalse(report["source_truth_checked"])

    def test_aliases_do_not_modify_other_policies(self):
        candidate = {"format": "json"}
        contract = policy({"format": [["JSON", "json"]]})
        exact = ContentPolicy({"format": "JSON"}, exact=("/format",))
        self.assertTrue(contract.check(candidate, now=120).valid)
        self.assertFalse(exact.check(candidate).valid)

    def test_declared_aliases_still_require_complete_claim_schema(self):
        contract = policy({"format": [["JSON", "json"]]})
        self.assertFalse(contract.check({}, now=120).valid)
        self.assertFalse(contract.check({"format": "json", "note": "new text"}, now=120).valid)
        self.assertFalse(contract.check_json('{"format":"CSV","format":"json"}', now=120).valid)


if __name__ == "__main__":
    unittest.main()
