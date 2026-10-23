from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import RelayStore
from context_relay.server import detail


class DetailSnapshotTests(unittest.TestCase):
    def test_detail_remains_coherent_when_another_connection_commits_mid_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.sqlite3"
            with RelayStore(path) as reader, RelayStore(path) as writer:
                sid = reader.create_session("Concurrent snapshot")["id"]
                original_event = reader.record_fact(sid, "intent", "refund")
                original_revision = reader.get_session(sid)["revision"]
                first_read = threading.Event()
                write_finished = threading.Event()
                failures = []

                def update_fact():
                    try:
                        if not first_read.wait(timeout=5):
                            raise AssertionError("The detail read did not start.")
                        writer.record_fact(
                            sid, "intent", "replacement",
                            expected_revision=original_revision,
                        )
                    except BaseException as exc:
                        failures.append(exc)
                    finally:
                        write_finished.set()

                original_get_session = reader.get_session

                def read_then_allow_write(session_id):
                    session = original_get_session(session_id)
                    if not first_read.is_set():
                        # WAL permits a different connection to commit while the
                        # detail transaction retains its earlier read snapshot.
                        first_read.set()
                        if not write_finished.wait(timeout=5):
                            raise AssertionError("Concurrent write did not finish.")
                    return session

                thread = threading.Thread(target=update_fact, daemon=True)
                thread.start()
                try:
                    with patch.object(reader, "get_session", side_effect=read_then_allow_write):
                        snapshot = detail(reader, sid)
                finally:
                    first_read.set()
                    thread.join(timeout=12)

                self.assertFalse(thread.is_alive(), "Concurrent writer is still running.")
                self.assertEqual(failures, [])
                self.assertEqual(snapshot["session"]["revision"], original_revision)
                self.assertEqual(snapshot["events"], [original_event])
                self.assertEqual(snapshot["facts"]["intent"]["value"], "refund")
                self.assertEqual(snapshot["context"]["revision"], original_revision)
                self.assertEqual(snapshot["context"]["facts"], {"intent": "refund"})
                self.assertEqual(snapshot["context"]["sources"], {"intent": original_event["id"]})
                self.assertEqual(snapshot["demo"]["stale_context"]["revision"], original_revision)
                self.assertEqual(snapshot["demo"]["stale_context"]["facts"], {"intent": "refund"})

                # The competing write really committed; it was not merely
                # delayed until after detail() returned.
                self.assertEqual(reader.get_session(sid)["revision"], original_revision + 1)
                self.assertEqual(reader.facts(sid)["intent"]["value"], "replacement")


if __name__ == "__main__":
    unittest.main()
