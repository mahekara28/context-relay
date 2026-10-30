import json
import sys
import tempfile
import unittest
from pathlib import Path

from context_relay.connectors import configure_claude, diagnose_claude, remove_claude


class ClaudeConnectorTests(unittest.TestCase):
    def test_configure_and_remove_preserve_other_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".mcp.json"
            path.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}))
            result = configure_claude(temporary, python_executable=sys.executable)
            self.assertTrue(result["configured"])
            configured = json.loads(path.read_text())
            self.assertEqual(configured["mcpServers"]["other"], {"command": "other"})
            self.assertEqual(configured["mcpServers"]["context_relay"]["args"][2], "mcp")
            self.assertTrue(diagnose_claude(temporary)["executable_exists"])
            remove_claude(temporary)
            self.assertEqual(json.loads(path.read_text()), {"mcpServers": {"other": {"command": "other"}}})

    def test_refuses_to_replace_user_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".mcp.json"
            path.write_text(json.dumps({"mcpServers": {"context_relay": {"command": "other"}}}))
            with self.assertRaises(ValueError):
                configure_claude(temporary, python_executable=sys.executable)
            self.assertEqual(json.loads(path.read_text())["mcpServers"]["context_relay"], {"command": "other"})


if __name__ == "__main__":
    unittest.main()
