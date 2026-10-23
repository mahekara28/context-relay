"""Content snapshots survive data-only setup without widening tool permission."""
import json
from pathlib import Path
import tempfile
import unittest

from context_relay import ContentPolicy, ContextGuard, OutputPolicy, PolicyError, RelayConfig


class ContentConfigurationTests(unittest.TestCase):
    def test_file_configuration_round_trip_preserves_exact_decimal_source(self):
        source = '{"rows":[{"amount":0.10000000000000001}]}'
        policy = ContentPolicy.from_json(source, exact=("/rows",))
        config = RelayConfig(output=OutputPolicy(format="json", content=policy))
        exported = config.to_dict()
        self.assertIsInstance(exported["output"]["content"], dict)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.json"
            path.write_text(json.dumps(exported), encoding="utf-8")
            restored = RelayConfig.from_file(path)
        self.assertEqual(restored.output.content.fingerprint, policy.fingerprint)
        self.assertTrue(restored.output.content.check_json(source).valid)
        self.assertFalse(restored.output.content.check_json('{"rows":[{"amount":0.1}]}').valid)
        exported["output"]["content"]["reference_json"] = '{"rows":[]}'
        self.assertTrue(config.output.content.check_json(source).valid)
        with self.assertRaises(ValueError):
            OutputPolicy(format="json", decoder=lambda response: response, content=policy).to_dict()

    def test_content_argument_and_invalid_stored_contract_are_checked_before_registration(self):
        policy = ContentPolicy({"rows": [{"name": "Source"}]}, exact=("/rows",))
        with ContextGuard() as guard:
            before = guard.store.get_session(guard.session_id)["revision"]
            with self.assertRaises(PolicyError):
                guard.allow_tool("export", content=policy, optional_args=("rows",))
            self.assertEqual(guard.store.get_session(guard.session_id)["revision"], before)
            guard.allow_tool("export", content=policy, optional_args=("summary",))
            stored = guard.store.policies(guard.session_id)[0]
            before = guard.store.get_session(guard.session_id)["revision"]
            stored["content"]["unexpected"] = True
            with self.assertRaises(PolicyError):
                guard.store.add_policy(guard.session_id, stored)
            self.assertEqual(guard.store.get_session(guard.session_id)["revision"], before)
            calls = []
            result = guard.turn().execute_with_recovery("export", {"rows": [{"name": "Source"}], "summary": "Allowed"},
                                                       lambda tool, arguments: calls.append(arguments) or arguments)
            self.assertTrue(result["executed"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(guard.store.facts(guard.session_id), {})


if __name__ == "__main__":
    unittest.main()
