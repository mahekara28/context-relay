import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path

from context_relay import ConflictError, RelayStore, StoreBusy
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


def _die_inside_task_transaction(path, task_id):
    store = RelayStore(path)
    service = TaskService(store, authorize=lambda principal, action: True)

    def before_commit(task):
        task["open_work"] = ["Uncommitted"]
        store._connection.execute(
            "INSERT INTO task_mutations VALUES (?, ?, ?, ?)",
            (task_id, "crash", "uncommitted", "{}"),
        )
        os._exit(7)

    service._mutate(Principal("team", "owner"), task_id, expected_revision=0,
                    request_id="crash", operation="test_crash", payload={}, transform=before_commit)


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

    def test_busy_write_is_bounded_and_does_not_consume_request_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "tasks.db")
            owner = Principal("team", "owner")
            first = RelayStore(path)
            second = RelayStore(path, busy_timeout_ms=20)
            try:
                task = TaskService(first, authorize=lambda principal, action: True).create(
                    owner, goal="Finish", completion_criteria=["Done"],
                    workspace={"repository": "example", "root": temporary})
                first._connection.execute("BEGIN IMMEDIATE")
                try:
                    with self.assertRaises(StoreBusy) as caught:
                        TaskService(second, authorize=lambda principal, action: True).update(
                            owner, task["id"], expected_revision=0, request_id="once",
                            changes={"open_work": ["Review"]})
                    self.assertTrue(caught.exception.before_transaction)
                finally:
                    first._connection.execute("ROLLBACK")
                changed = TaskService(second, authorize=lambda principal, action: True).update(
                    owner, task["id"], expected_revision=0, request_id="once",
                    changes={"open_work": ["Review"]})
                self.assertEqual(changed["revision"], 1)
                with first._transaction():
                    self.assertEqual(first._connection.execute(
                        "SELECT COUNT(*) FROM task_mutations WHERE task_id=?", (task["id"],)
                    ).fetchone()[0], 1)
            finally:
                second.close()
                first.close()

    def test_process_death_rolls_back_task_mutation_and_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "tasks.db")
            owner = Principal("team", "owner")
            store = RelayStore(path)
            try:
                task = TaskService(store, authorize=lambda principal, action: True).create(
                    owner, goal="Finish", completion_criteria=["Done"],
                    workspace={"repository": "example", "root": temporary})
                child = multiprocessing.get_context("spawn").Process(
                    target=_die_inside_task_transaction, args=(path, task["id"]))
                child.start()
                child.join(timeout=15)
                self.assertEqual(child.exitcode, 7)
                self.assertEqual(TaskService(store, authorize=lambda principal, action: True).show(
                    owner, task["id"])["revision"], 0)
                with store._transaction():
                    self.assertEqual(store._connection.execute(
                        "SELECT COUNT(*) FROM task_mutations WHERE task_id=?", (task["id"],)
                    ).fetchone()[0], 0)
                result = TaskService(store, authorize=lambda principal, action: True).update(
                    owner, task["id"], expected_revision=0, request_id="crash",
                    changes={"open_work": ["Recovered"]})
                self.assertEqual(result["revision"], 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
