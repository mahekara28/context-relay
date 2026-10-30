import sqlite3
import tempfile
import unittest
from pathlib import Path

from context_relay import RelayStore


class TaskMigrationTests(unittest.TestCase):
    def test_old_session_survives_additive_task_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "relay.db"
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA user_version = 1")
                connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT NOT NULL, scenario TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
                connection.execute("INSERT INTO sessions VALUES ('old', 'Old', 'demo', 0, '2026-01-01T00:00:00Z')")
                connection.commit()
            finally:
                connection.close()
            store = RelayStore(path)
            try:
                with store._transaction():
                    self.assertEqual(store._connection.execute("PRAGMA user_version").fetchone()[0], 4)
                    self.assertEqual(store._connection.execute("SELECT name FROM sessions WHERE id='old'").fetchone()[0], "Old")
                    self.assertIsNotNone(store._connection.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchone())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
