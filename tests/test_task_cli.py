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
                self.assertEqual(main(["task", "--db", database, "instruct", created["id"],
                                       "--expected-revision", "0", "--request-id", "old",
                                       "--text", "Use CSV", "--source-kind", "user",
                                       "--source-ref", "message-1"]), 0)
            old_instruction = json.loads(output.getvalue())["instructions"][0]["id"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["task", "--db", database, "instruct", created["id"],
                                       "--expected-revision", "1", "--request-id", "new",
                                       "--text", "Use JSON", "--source-kind", "user",
                                       "--source-ref", "message-2", "--supersedes", old_instruction]), 0)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["handoff", "--db", database, "--subject", "sender", "offer",
                                       created["id"], "--recipient", "receiver"]), 0)
            offered = json.loads(output.getvalue())
            self.assertEqual([item["text"] for item in offered["packet"]["instructions"]], ["Use JSON"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["handoff", "--db", database, "--subject", "receiver", "accept",
                                       offered["id"]]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "accepted")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["task", "--db", database, "doctor"]), 0)
            self.assertEqual(json.loads(output.getvalue())["tasks"]["active"], 1)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["task", "--db", database, "diagnose", created["id"]]), 0)
            self.assertEqual(json.loads(output.getvalue())["verification"]["stored"], False)


if __name__ == "__main__":
    unittest.main()
