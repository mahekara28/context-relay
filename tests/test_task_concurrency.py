import multiprocessing
import tempfile
import unittest
from pathlib import Path

from context_relay import ConflictError, RelayStore
from context_relay.organization import Principal
from context_relay.task_service import TaskService


def _competing_update(path, task_id, request_id, ready, start, results):
    store = RelayStore(path)
    try:
        service = TaskService(store, authorize=lambda principal, action: True)
        ready.put(request_id)
        if not start.wait(10):
            results.put((request_id, "timeout"))
            return
        try:
            service.update(Principal("team", request_id), task_id, expected_revision=0,
                           request_id=request_id, changes={"open_work": [request_id]})
            results.put((request_id, "committed"))
        except ConflictError:
            results.put((request_id, "conflict"))
    finally:
        store.close()


class TaskConcurrencyTests(unittest.TestCase):
    def test_two_processes_cannot_both_commit_same_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "tasks.db")
            store = RelayStore(path)
            try:
                task = TaskService(store, authorize=lambda principal, action: True).create(
                    Principal("team", "owner"), goal="Finish", completion_criteria=["Done"],
                    workspace={"repository": "example", "root": temporary})
                context = multiprocessing.get_context("spawn")
                ready, results, start = context.Queue(), context.Queue(), context.Event()
                workers = [context.Process(target=_competing_update,
                                           args=(path, task["id"], name, ready, start, results))
                           for name in ("writer-a", "writer-b")]
                for worker in workers:
                    worker.start()
                for _ in workers:
                    ready.get(timeout=15)
                start.set()
                outcomes = [results.get(timeout=15)[1] for _ in workers]
                for worker in workers:
                    worker.join(timeout=15)
                    self.assertEqual(worker.exitcode, 0)
                self.assertCountEqual(outcomes, ["committed", "conflict"])
                with store._transaction():
                    self.assertEqual(store._connection.execute(
                        "SELECT COUNT(*) FROM task_events WHERE task_id=?", (task["id"],)
                    ).fetchone()[0], 2)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
