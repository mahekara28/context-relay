import sys
import tempfile
import unittest
from pathlib import Path

from context_relay.connectors import configure_codex, diagnose_codex, remove_codex


class CodexConnectorTests(unittest.TestCase):
    def test_managed_block_round_trips_without_rewriting_unrelated_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".codex/config.toml"
            path.parent.mkdir()
            original = 'model = "user-choice"\n'
            path.write_text(original)
            result = configure_codex(temporary, python_executable=sys.executable)
            self.assertTrue(result["configured"])
            self.assertTrue(result["executable_exists"])
            self.assertIn('[mcp_servers.context_relay]', path.read_text())
            configure_codex(temporary, python_executable=sys.executable)
            remove_codex(temporary)
            self.assertEqual(path.read_text(), original)

    def test_refuses_unmanaged_existing_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".codex/config.toml"
            path.parent.mkdir()
            path.write_text('[mcp_servers.context_relay]\ncommand = "other"\n')
            with self.assertRaises(ValueError):
                configure_codex(temporary, python_executable=sys.executable)
            self.assertFalse(diagnose_codex(temporary)["configured"])


if __name__ == "__main__":
    unittest.main()
