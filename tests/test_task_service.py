import tempfile
import unittest
from pathlib import Path

from context_relay import ConflictError, RelayStore
from context_relay.organization import AccessDenied, Principal
from context_relay.task_service import TaskNotFound, TaskRequestConflict, TaskService, inspect_git_workspace


class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = RelayStore(Path(self.temporary.name) / "relay.db")
        self.service = TaskService(self.store, authorize=lambda principal, action: action != "denied")
        self.alice = Principal("team-a", "alice")
        self.bob = Principal("team-b", "bob")

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def create(self):
        return self.service.create(self.alice, goal="Finish report", completion_criteria=["Oracle passes"],
                                   workspace={"repository": "context-relay", "root": "/work"})

    def test_create_and_read_persist_across_connections(self):
        task = self.create()
        self.assertEqual(task["revision"], 0)
        with RelayStore(self.store.path) as other:
            second = TaskService(other, authorize=lambda principal, action: True)
            self.assertEqual(second.show(self.alice, task["id"]), task)

    def test_tenant_scope_does_not_reveal_task(self):
        task = self.create()
        with self.assertRaises(TaskNotFound):
            self.service.show(self.bob, task["id"])

    def test_permission_denial(self):
        denied = TaskService(self.store, authorize=lambda principal, action: False)
        with self.assertRaises(AccessDenied):
            denied.create(self.alice, goal="X", completion_criteria=["Y"],
                          workspace={"repository": "repo", "root": "/work"})

    def test_update_is_cas_and_idempotent(self):
        task = self.create()
        updated = self.service.update(self.alice, task["id"], expected_revision=0,
                                      request_id="update-1", changes={"open_work": ["Run oracle"]})
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(self.service.update(self.alice, task["id"], expected_revision=0,
                                             request_id="update-1", changes={"open_work": ["Run oracle"]}), updated)
        with self.assertRaises(ConflictError):
            self.service.update(self.alice, task["id"], expected_revision=0,
                                request_id="update-2", changes={"goal": "Stale"})
        with self.assertRaises(TaskRequestConflict):
            self.service.update(self.alice, task["id"], expected_revision=0,
                                request_id="update-1", changes={"goal": "Different"})
        self.assertEqual(self.service.show(self.alice, task["id"]), updated)

    def test_agent_report_cannot_claim_host_authority(self):
        task = self.create()
        reporting = TaskService(self.store, authorize=lambda principal, action: True,
                                source_class="agent_report")
        with self.assertRaises(AccessDenied):
            reporting.create(self.alice, goal="Fake", completion_criteria=["Anything"],
                             workspace={"repository": "repo", "root": "/work"})
        with self.assertRaises(AccessDenied):
            reporting.update(self.alice, task["id"], expected_revision=0,
                             request_id="claim", changes={"goal": "Done"})
        report = reporting.update(self.alice, task["id"], expected_revision=0,
                                  request_id="report", changes={"open_work": ["I think it works"]})
        self.assertEqual(report["revision"], 1)
        with self.store._transaction():
            event = self.store._connection.execute(
                "SELECT source_class FROM task_events WHERE task_id=? AND revision=1", (task["id"],)
            ).fetchone()
        self.assertEqual(event["source_class"], "agent_report")

    def test_workspace_root_must_be_explicit_absolute_path(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.service.create(self.alice, goal="X", completion_criteria=["Y"],
                                workspace={"repository": "repo", "root": "relative"})

    def test_inspects_real_git_worktree_identity(self):
        repo = Path(__file__).resolve().parents[1]
        identity = inspect_git_workspace(repo)
        self.assertEqual(identity["root"], str(repo))
        self.assertTrue(identity["head"])
        self.assertTrue(identity["repository"])

    def test_instruction_supersession_is_explicit_and_scoped(self):
        task = self.create()
        old = self.service.add_instruction(self.alice, task["id"], expected_revision=0,
                                           request_id="old", text="Use CSV",
                                           source={"kind": "user", "ref": "message-1"})
        old_id = old["instructions"][0]["id"]
        new = self.service.add_instruction(self.alice, task["id"], expected_revision=1,
                                           request_id="new", text="Use JSON",
                                           source={"kind": "user", "ref": "message-2"},
                                           supersedes=[old_id])
        self.assertEqual([item["status"] for item in new["instructions"]], ["superseded", "active"])
        self.assertEqual(self.service.add_instruction(self.alice, task["id"], expected_revision=1,
                                                      request_id="new", text="Use JSON",
                                                      source={"kind": "user", "ref": "message-2"},
                                                      supersedes=[old_id]), new)
        with self.assertRaises(ValueError):
            self.service.add_instruction(self.alice, task["id"], expected_revision=2,
                                         request_id="bad", text="Use XML",
                                         source={"kind": "user", "ref": "message-3"},
                                         supersedes=[old_id])

    def test_reported_evidence_cannot_be_laundered_into_tool_evidence(self):
        task = self.create()
        reporting = TaskService(self.store, authorize=lambda principal, action: True,
                                source_class="agent_report")
        with self.assertRaises(AccessDenied):
            reporting.attach_evidence(self.alice, task["id"], expected_revision=0,
                                      request_id="fake", claim="Tests passed",
                                      source={"kind": "tool", "ref": "pytest"})
        result = reporting.attach_evidence(self.alice, task["id"], expected_revision=0,
                                           request_id="report", claim="I think tests passed",
                                           source={"kind": "agent_report", "ref": "assistant-1"})
        self.assertEqual(result["evidence"][0]["source"]["kind"], "agent_report")
        self.assertEqual(reporting.attach_evidence(self.alice, task["id"], expected_revision=0,
                                                   request_id="report", claim="I think tests passed",
                                                   source={"kind": "agent_report", "ref": "assistant-1"}), result)

    def test_artifact_capture_is_content_addressed_and_workspace_scoped(self):
        root = Path(self.temporary.name)
        (root / "report.json").write_text('{"ok":true}')
        task = self.service.create(self.alice, goal="Finish report", completion_criteria=["Oracle passes"],
                                   workspace={"repository": "example", "root": str(root)})
        result = self.service.capture_artifact(self.alice, task["id"], expected_revision=0,
                                               request_id="capture", path="report.json")
        self.assertEqual(result["artifacts"][0]["size"], 11)
        self.assertTrue(result["artifacts"][0]["digest"].startswith("sha256:"))
        with self.assertRaises(ValueError):
            self.service.capture_artifact(self.alice, task["id"], expected_revision=1,
                                          request_id="escape", path="../outside")

    def test_revision_diff_retains_instruction_source(self):
        task = self.create()
        self.service.add_instruction(self.alice, task["id"], expected_revision=0,
                                     request_id="instruction", text="Use JSON",
                                     source={"kind": "user", "ref": "message-2"})
        difference = self.service.diff(self.alice, task["id"], from_revision=0)
        self.assertEqual(difference["collections"]["instructions"]["added"][0]["source"]["ref"], "message-2")
        self.assertEqual(difference["to_revision"], 1)

    def test_temporary_instruction_expires_at_revision_boundary(self):
        task = self.create()
        instructed = self.service.add_instruction(
            self.alice, task["id"], expected_revision=0, request_id="temporary",
            text="Use a draft footer", source={"kind": "user", "ref": "message-1"},
            expires_at_revision=2)
        self.assertEqual(instructed["instructions"][0]["status"], "active")
        revised = self.service.update(self.alice, task["id"], expected_revision=1,
                                      request_id="next", changes={"open_work": ["Review"]})
        self.assertEqual(revised["instructions"][0]["status"], "expired")

    def test_stale_proposal_is_retained_until_explicit_resolution(self):
        task = self.create()
        current = self.service.update(self.alice, task["id"], expected_revision=0,
                                      request_id="first", changes={"open_work": ["First"]})
        proposal = self.service.propose_update(self.alice, task["id"], expected_revision=0,
                                               request_id="alternative", changes={"open_work": ["Second"]})
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(self.service.show(self.alice, task["id"]), current)
        resolved = self.service.resolve_proposal(self.alice, proposal["id"], decision="apply",
                                                 expected_revision=1, request_id="resolve")
        self.assertEqual(resolved["task"]["open_work"], ["Second"])
        self.assertEqual(resolved["task"]["revision"], 2)

    def test_archive_requires_host_evidence_and_can_reopen(self):
        task = self.create()
        report = TaskService(self.store, authorize=lambda principal, action: True,
                             source_class="agent_report").attach_evidence(
            self.alice, task["id"], expected_revision=0, request_id="report",
            claim="I finished", source={"kind": "agent_report", "ref": "agent"})
        with self.assertRaises(ValueError):
            self.service.archive(self.alice, task["id"], expected_revision=1,
                                 request_id="bad", completion_evidence_ids=[report["evidence"][0]["id"]])
        verified = self.service.attach_evidence(self.alice, task["id"], expected_revision=1,
                                                 request_id="host", claim="Oracle passed",
                                                 source={"kind": "tool", "ref": "oracle-run-1"})
        archived = self.service.archive(self.alice, task["id"], expected_revision=2,
                                        request_id="archive", completion_evidence_ids=[verified["evidence"][1]["id"]])
        self.assertEqual(archived["status"], "archived")
        with self.assertRaises(ValueError):
            self.service.update(self.alice, task["id"], expected_revision=3,
                                request_id="blocked", changes={"goal": "Change"})
        self.assertEqual(self.service.reopen(self.alice, task["id"], expected_revision=3,
                                             request_id="reopen", reason="New work")["status"], "active")

    def test_event_and_evidence_pages_are_bounded(self):
        task = self.create()
        for index in range(3):
            self.service.attach_evidence(self.alice, task["id"], expected_revision=index,
                                         request_id=f"e-{index}", claim=f"Claim {index}",
                                         source={"kind": "file", "ref": f"file-{index}"})
        first = self.service.list_events(self.alice, task["id"], limit=2)
        second = self.service.list_events(self.alice, task["id"],
                                          after_revision=first[-1]["revision"], limit=2)
        self.assertEqual([item["revision"] for item in first + second], [0, 1, 2, 3])
        self.assertEqual(len(self.service.list_evidence(self.alice, task["id"], offset=1, limit=1)), 1)
        with self.assertRaises(ValueError):
            self.service.list_events(self.alice, task["id"], limit=1000)


if __name__ == "__main__":
    unittest.main()
