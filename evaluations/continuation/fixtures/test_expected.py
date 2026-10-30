"""Task oracle: artifact bytes and changed requirements, not agent self-report."""

import json
import tempfile
import unittest
from pathlib import Path

from reporting import export_report


ROWS = [
    {"region": "west", "account": "customer", "amount": 120, "refund": 20},
    {"region": "east", "account": "customer", "amount": 70, "refund": 5},
    {"region": "west", "account": "internal", "amount": 1000, "refund": 0},
    {"region": "west", "account": "customer", "amount": 30, "refund": 0},
]


class ReportOracle(unittest.TestCase):
    def test_current_json_net_revenue(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            export_report(ROWS, path, format="JSON")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"east": 65, "west": 130})

    def test_unsupported_format_does_not_create_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.xml"
            with self.assertRaises(ValueError):
                export_report(ROWS, path, format="XML")
            self.assertFalse(path.exists())
