import tempfile
import unittest
from pathlib import Path

from context_relay.connector_config import ConfigChanged, file_state, replace_if_unchanged


class ConnectorConfigTests(unittest.TestCase):
    def test_atomic_edit_and_remove_preserve_inspected_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".mcp.json"
            first = replace_if_unchanged(path, expected_digest=None, content=b'{"mcpServers":{}}\n')
            self.assertEqual(file_state(path)[0], first)
            with self.assertRaises(ConfigChanged):
                replace_if_unchanged(path, expected_digest=None, content=b'changed')
            second = replace_if_unchanged(path, expected_digest=first, content=b'{"mcpServers":{"relay":{}}}\n')
            self.assertNotEqual(first, second)
            replace_if_unchanged(path, expected_digest=second, content=None)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
