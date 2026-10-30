import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from context_relay.cli import main


class TaskCliTests(unittest.TestCase):
    def test_create_then_show_persists_portable_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = str(Path(temporary) / "tasks.db")
            root = str(Path(__file__).resolve().parents[1])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["task", "--db", database, "create", "--goal", "Finish report",
                                       "--criterion", "Oracle passes", "--root", root]), 0)
            created = json.loads(output.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["task", "--db", database, "show", created["id"]]), 0)
            self.assertEqual(json.loads(output.getvalue()), created)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["handoff", "--db", database, "--subject", "sender", "offer",
                                       created["id"], "--recipient", "receiver"]), 0)
            offered = json.loads(output.getvalue())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["handoff", "--db", database, "--subject", "receiver", "accept",
                                       offered["id"]]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "accepted")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["task", "--db", database, "doctor"]), 0)
            self.assertEqual(json.loads(output.getvalue())["tasks"]["active"], 1)


if __name__ == "__main__":
    unittest.main()
