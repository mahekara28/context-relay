"""Host-authorized task state and revisioned local event history."""

from __future__ import annotations

import json
import hashlib
import subprocess
import uuid
from pathlib import Path
from typing import Callable

from .models import ConflictError, clone, encode, nonempty_string, timestamp, validate_revision
from .organization import AccessDenied, Principal
from .store import RelayStore
from .task_contract import SCHEMA_VERSION, validate_source, validate_task_snapshot, validate_workspace


class TaskNotFound(LookupError):
    """A task does not exist or is outside the principal's tenant."""


class TaskRequestConflict(ValueError):
    """An idempotency key was reused for different request content."""


MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


def _artifact_manifest(root: str, relative_path: str) -> dict:
    nonempty_string(relative_path, "artifact path", limit=4096)
    base = Path(root).resolve(strict=True)
    candidate = (base / relative_path).resolve()
    if not candidate.is_relative_to(base) or not candidate.is_file():
        raise ValueError("artifact must be a regular file inside the task workspace.")
    name = candidate.relative_to(base).as_posix()
    digest = hashlib.sha256()
    size = 0
    with candidate.open("rb") as stream:
        while block := stream.read(65536):
            size += len(block)
            if size > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact exceeds the 16 MiB capture limit.")
            digest.update(block)
    return {"path": name, "digest": "sha256:" + digest.hexdigest(), "size": size}


def inspect_git_workspace(root: str | Path) -> dict:
    """Capture a local worktree identity without contacting a remote server."""
    path = Path(root).resolve(strict=True)
    if not path.is_dir():
        raise ValueError("workspace root must be a directory.")

    def git(*arguments: str) -> str:
        try:
            result = subprocess.run(["git", "-C", str(path), *arguments],
                                    capture_output=True, text=True, timeout=5, check=True)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ValueError("workspace must be a readable Git worktree with an origin remote.") from error
        return result.stdout.strip()

    worktree = Path(git("rev-parse", "--show-toplevel")).resolve(strict=True)
    origin = git("remote", "get-url", "origin")
    head = git("rev-parse", "HEAD")
    if not origin or not head:
        raise ValueError("workspace origin and HEAD must be available.")
    return validate_workspace({"repository": origin, "root": str(path),
                               "worktree": str(worktree), "head": head})


class TaskService:
    """The host supplies identity and authorization; a model cannot self-attest either."""

    def __init__(self, store: RelayStore, *, authorize: Callable[[Principal, str], bool],
                 source_class: str = "host"):
        if not isinstance(store, RelayStore) or not callable(authorize):
            raise TypeError("TaskService requires a RelayStore and synchronous authorization callback.")
        if source_class not in ("host", "agent_report"):
            raise ValueError("source_class must be host or agent_report.")
        self.store = store
        self.authorize = authorize
        self.source_class = source_class

    def _check(self, principal: Principal, operation: str) -> None:
        if not isinstance(principal, Principal):
            raise TypeError("principal must be supplied by the host as a Principal.")
        if self.authorize(principal, operation) is not True:
            raise AccessDenied("Access denied.")

    def _row(self, task_id: str, principal: Principal):
        row = self.store._connection.execute(
            "SELECT * FROM tasks WHERE id = ? AND tenant = ?", (task_id, principal.tenant)
        ).fetchone()
        if row is None:
            raise TaskNotFound("Task not found.")
        return row

    def create(self, principal: Principal, *, goal: str, completion_criteria: list[str],
               workspace: dict, open_work: list[str] | None = None) -> dict:
        self._check(principal, "task.create")
        if self.source_class != "host":
            raise AccessDenied("Only the host may create a task.")
        nonempty_string(goal, "goal", limit=4096)
        workspace = validate_workspace(workspace)
        if not Path(workspace["root"]).is_absolute():
            raise ValueError("workspace root must be an absolute path.")
        if workspace.get("worktree") is not None and not Path(workspace["worktree"]).is_absolute():
            raise ValueError("workspace worktree must be an absolute path.")
        now = timestamp()
        task = validate_task_snapshot({
            "schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "revision": 0,
            "status": "active", "goal": goal, "completion_criteria": completion_criteria,
            "workspace": workspace, "instructions": [], "evidence": [],
            "artifacts": [], "open_work": [] if open_work is None else open_work,
            "created_at": now, "updated_at": now,
        })
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            self.store._connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task["id"], principal.tenant, 0, "active", encode(task), now, now),
            )
            self.store._connection.execute(
                "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, task["id"], 0, "create", principal.subject,
                 self.source_class, encode({"goal": goal}), now),
            )
            self.store._connection.execute("INSERT INTO task_revisions VALUES (?, ?, ?)",
                                           (task["id"], 0, encode(task)))
        return task

    def show(self, principal: Principal, task_id: str) -> dict:
        self._check(principal, "task.read")
        nonempty_string(task_id, "task_id", limit=128)
        with self.store._transaction():
            task = json.loads(self._row(task_id, principal)["payload"])
        self._check(principal, "task.read")
        return validate_task_snapshot(task)

    def update(self, principal: Principal, task_id: str, *, expected_revision: int,
               request_id: str, changes: dict) -> dict:
        """Apply one bounded patch exactly once at a caller-observed revision."""
        self._check(principal, "task.update")
        nonempty_string(task_id, "task_id", limit=128)
        nonempty_string(request_id, "request_id", limit=128)
        validate_revision(expected_revision)
        if type(changes) is not dict or not changes or set(changes) - {"goal", "completion_criteria", "open_work"}:
            raise ValueError("changes must contain only goal, completion_criteria, or open_work.")
        if self.source_class == "agent_report" and set(changes) != {"open_work"}:
            raise AccessDenied("Agent reports may update only open work.")
        changes = clone(changes)
        return self._mutate(principal, task_id, expected_revision=expected_revision,
                            request_id=request_id, operation="update", payload=changes,
                            transform=lambda task: task.update(changes))

    def _mutate(self, principal: Principal, task_id: str, *, expected_revision: int,
                request_id: str, operation: str, payload: dict, transform: Callable[[dict], None],
                allow_archived: bool = False) -> dict:
        """Serialize one task transition and its replay receipt in one transaction."""
        self._check(principal, f"task.{operation}")
        nonempty_string(task_id, "task_id", limit=128)
        nonempty_string(request_id, "request_id", limit=128)
        validate_revision(expected_revision)
        fingerprint = hashlib.sha256(json.dumps(
            {"principal": principal.subject, "source_class": self.source_class,
             "expected_revision": expected_revision, "operation": operation, "payload": payload},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            row = self._row(task_id, principal)
            previous = self.store._connection.execute(
                "SELECT request_digest, result_payload FROM task_mutations WHERE task_id=? AND request_id=?",
                (task_id, request_id),
            ).fetchone()
            if previous is not None:
                if previous["request_digest"] != fingerprint:
                    raise TaskRequestConflict("request_id was already used for a different task update.")
                return json.loads(previous["result_payload"])
            if row["revision"] != expected_revision:
                raise ConflictError(expected_revision, row["revision"])
            if row["status"] != "active" and not allow_archived:
                raise ValueError("Archived tasks must be reopened before updating.")
            task = json.loads(row["payload"])
            transform(task)
            task["revision"] += 1
            for instruction in task["instructions"]:
                if instruction["status"] == "active" and instruction.get("expires_at_revision", 2**63) <= task["revision"]:
                    instruction["status"] = "expired"
            task["updated_at"] = timestamp()
            task = validate_task_snapshot(task)
            self.store._connection.execute(
                "UPDATE tasks SET revision=?, status=?, payload=?, updated_at=? WHERE id=?",
                (task["revision"], task["status"], encode(task), task["updated_at"], task_id),
            )
            self.store._connection.execute(
                "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, task_id, task["revision"], operation, principal.subject,
                 self.source_class, encode(payload), task["updated_at"]),
            )
            self.store._connection.execute(
                "INSERT INTO task_mutations VALUES (?, ?, ?, ?)",
                (task_id, request_id, fingerprint, encode(task)),
            )
            self.store._connection.execute("INSERT INTO task_revisions VALUES (?, ?, ?)",
                                           (task_id, task["revision"], encode(task)))
        return task

    def diff(self, principal: Principal, task_id: str, *, from_revision: int,
             to_revision: int | None = None) -> dict:
        """Show changes between stored snapshots, retaining source references."""
        self._check(principal, "task.read")
        validate_revision(from_revision)
        if to_revision is not None:
            validate_revision(to_revision)
        with self.store._transaction():
            row = self._row(task_id, principal)
            if to_revision is None:
                to_revision = row["revision"]
            snapshots = {}
            for revision in (from_revision, to_revision):
                found = self.store._connection.execute(
                    "SELECT payload FROM task_revisions WHERE task_id=? AND revision=?",
                    (task_id, revision),
                ).fetchone()
                if found is None:
                    raise ValueError(f"Task revision {revision} is unavailable.")
                snapshots[revision] = json.loads(found["payload"])
        self._check(principal, "task.read")
        before, after = snapshots[from_revision], snapshots[to_revision]
        fields = {}
        for name in ("status", "goal", "completion_criteria", "workspace", "open_work"):
            if before[name] != after[name]:
                fields[name] = {"before": before[name], "after": after[name]}
        collections = {}
        for name, key in (("instructions", "id"), ("evidence", "id"), ("artifacts", "path")):
            old = {item[key]: item for item in before[name]}
            new = {item[key]: item for item in after[name]}
            collections[name] = {"added": [item for item in after[name] if item[key] not in old],
                                 "changed": [item for item in after[name]
                                             if item[key] in old and old[item[key]] != item],
                                 "removed": [item for item in before[name] if item[key] not in new]}
        return {"task_id": task_id, "from_revision": from_revision,
                "to_revision": to_revision, "fields": fields, "collections": collections}

    def propose_update(self, principal: Principal, task_id: str, *, expected_revision: int,
                       request_id: str, changes: dict) -> dict:
        """Retain a stale proposal without applying it or pretending it won the race."""
        self._check(principal, "task.propose")
        validate_revision(expected_revision)
        nonempty_string(request_id, "request_id", limit=128)
        if type(changes) is not dict or not changes or set(changes) - {"goal", "completion_criteria", "open_work"}:
            raise ValueError("proposal changes contain unsupported fields.")
        if self.source_class == "agent_report" and set(changes) != {"open_work"}:
            raise AccessDenied("Agent reports may propose only open work.")
        changes = clone(changes)
        with self.store._transaction(write=True):
            self.store._assert_mutable()
            row = self._row(task_id, principal)
            if row["revision"] <= expected_revision:
                raise ValueError("Conflict proposals require a stale expected revision.")
            prior = self.store._connection.execute(
                "SELECT * FROM task_conflicts WHERE task_id=? AND request_id=?", (task_id, request_id)
            ).fetchone()
            if prior is None:
                now = timestamp()
                proposal_id = uuid.uuid4().hex
                self.store._connection.execute(
                    "INSERT INTO task_conflicts VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)",
                    (proposal_id, task_id, request_id, expected_revision, row["revision"],
                     principal.subject, self.source_class, encode(changes), now),
                )
                prior = self.store._connection.execute(
                    "SELECT * FROM task_conflicts WHERE id=?", (proposal_id,)
                ).fetchone()
            elif json.loads(prior["changes"]) != changes or prior["proposer"] != principal.subject:
                raise TaskRequestConflict("request_id was already used for a different proposal.")
            return self._proposal_record(prior)

    @staticmethod
    def _proposal_record(row) -> dict:
        return {"id": row["id"], "task_id": row["task_id"],
                "expected_revision": row["expected_revision"],
                "observed_revision": row["observed_revision"],
                "changes": json.loads(row["changes"]), "status": row["status"],
                "proposer": row["proposer"], "resolver": row["resolver"]}

    def resolve_proposal(self, principal: Principal, proposal_id: str, *,
                         decision: str, expected_revision: int,
                         request_id: str) -> dict:
        """Apply or reject a conflict explicitly; applying is atomic with task revision."""
        self._check(principal, "task.resolve")
        if self.source_class != "host":
            raise AccessDenied("Only the host may resolve conflicting proposals.")
        if decision not in ("apply", "reject"):
            raise ValueError("decision must be apply or reject.")
        validate_revision(expected_revision)
        nonempty_string(proposal_id, "proposal_id", limit=128)
        nonempty_string(request_id, "request_id", limit=128)
        with self.store._transaction():
            row = self.store._connection.execute(
                "SELECT c.* FROM task_conflicts c JOIN tasks t ON t.id=c.task_id "
                "WHERE c.id=? AND t.tenant=?", (proposal_id, principal.tenant),
            ).fetchone()
            if row is None:
                raise TaskNotFound("Proposal not found.")
            proposal = self._proposal_record(row)
        if proposal["status"] != "pending":
            if proposal["status"] == "rejected" and decision == "reject":
                return {"proposal_id": proposal_id, "decision": "reject"}
            if proposal["status"] != "applied" or decision != "apply":
                raise ValueError("Proposal has already been resolved differently.")
        if decision == "reject":
            with self.store._transaction(write=True):
                task = self._row(proposal["task_id"], principal)
                if task["revision"] != expected_revision:
                    raise ConflictError(expected_revision, task["revision"])
                self.store._connection.execute(
                    "UPDATE task_conflicts SET status='rejected', resolver=?, resolved_at=? "
                    "WHERE id=? AND status='pending'", (principal.subject, timestamp(), proposal_id),
                )
            return {"proposal_id": proposal_id, "decision": "reject"}

        def apply(task: dict) -> None:
            pending = self.store._connection.execute(
                "SELECT status FROM task_conflicts WHERE id=?", (proposal_id,)
            ).fetchone()
            if pending is None or pending["status"] != "pending":
                raise ValueError("Proposal has already been resolved.")
            task.update(proposal["changes"])
            self.store._connection.execute(
                "UPDATE task_conflicts SET status='applied', resolver=?, resolved_at=? WHERE id=?",
                (principal.subject, timestamp(), proposal_id),
            )

        result = self._mutate(principal, proposal["task_id"], expected_revision=expected_revision,
                              request_id=request_id, operation="resolve", payload={"proposal_id": proposal_id},
                              transform=apply)
        return {"proposal_id": proposal_id, "decision": "apply", "task": result}

    def archive(self, principal: Principal, task_id: str, *, expected_revision: int,
                request_id: str, completion_evidence_ids: list[str]) -> dict:
        """Close a task only with named non-agent evidence; this is a host decision."""
        if self.source_class != "host":
            raise AccessDenied("Only the host may archive a task.")
        if type(completion_evidence_ids) is not list or not completion_evidence_ids:
            raise ValueError("Archiving requires at least one evidence id.")
        for evidence_id in completion_evidence_ids:
            nonempty_string(evidence_id, "evidence id", limit=128)
        if len(set(completion_evidence_ids)) != len(completion_evidence_ids):
            raise ValueError("completion evidence ids must be unique.")

        def transform(task: dict) -> None:
            evidence = {item["id"]: item for item in task["evidence"]}
            for evidence_id in completion_evidence_ids:
                item = evidence.get(evidence_id)
                if item is None or item["source"]["kind"] == "agent_report":
                    raise ValueError("Completion evidence must be present and not model-reported.")
            task["status"] = "archived"

        return self._mutate(principal, task_id, expected_revision=expected_revision,
                            request_id=request_id, operation="archive",
                            payload={"completion_evidence_ids": completion_evidence_ids},
                            transform=transform)

    def reopen(self, principal: Principal, task_id: str, *, expected_revision: int,
               request_id: str, reason: str) -> dict:
        if self.source_class != "host":
            raise AccessDenied("Only the host may reopen a task.")
        nonempty_string(reason, "reason", limit=1024)

        def transform(task: dict) -> None:
            if task["status"] != "archived":
                raise ValueError("Only an archived task can be reopened.")
            task["status"] = "active"

        return self._mutate(principal, task_id, expected_revision=expected_revision,
                            request_id=request_id, operation="reopen", payload={"reason": reason},
                            transform=transform, allow_archived=True)

    def add_instruction(self, principal: Principal, task_id: str, *, expected_revision: int,
                        request_id: str, text: str, source: dict, scope: str = "task",
                        supersedes: list[str] | None = None,
                        expires_at_revision: int | None = None) -> dict:
        """Record an instruction; any supersession names active predecessors exactly."""
        if self.source_class != "host":
            raise AccessDenied("Only the host may record authoritative instructions.")
        _ = nonempty_string(text, "instruction text", limit=4096)
        validate_revision(expected_revision)
        source = validate_source(source)
        if source["kind"] not in ("user", "host", "file"):
            raise ValueError("Authoritative instructions require a user, host, or file source.")
        if scope not in ("task", "workspace", "repository"):
            raise ValueError("instruction scope is unsupported.")
        if supersedes is None:
            supersedes = []
        if type(supersedes) is not list or len(set(supersedes)) != len(supersedes):
            raise ValueError("supersedes must be a list of distinct instruction ids.")
        for predecessor in supersedes:
            nonempty_string(predecessor, "superseded instruction id", limit=128)
        payload = {"text": text, "source": source, "scope": scope, "supersedes": supersedes}
        if expires_at_revision is not None:
            if type(expires_at_revision) is not int or expires_at_revision <= expected_revision + 1:
                raise ValueError("expires_at_revision must follow the instruction creation revision.")
            payload["expires_at_revision"] = expires_at_revision

        def transform(task: dict) -> None:
            predecessors = {item["id"]: item for item in task["instructions"]}
            for predecessor in supersedes:
                item = predecessors.get(predecessor)
                if item is None or item["status"] != "active" or item["scope"] != scope:
                    raise ValueError("A superseded instruction must be active and share the new scope.")
            for predecessor in supersedes:
                predecessors[predecessor]["status"] = "superseded"
            item = {"id": uuid.uuid4().hex, "text": text,
                    "source": source, "scope": scope, "status": "active",
                    "supersedes": supersedes}
            if expires_at_revision is not None:
                item["expires_at_revision"] = expires_at_revision
            task["instructions"].append(item)

        return self._mutate(principal, task_id, expected_revision=expected_revision,
                            request_id=request_id, operation="instruction", payload=payload,
                            transform=transform)

    def attach_evidence(self, principal: Principal, task_id: str, *, expected_revision: int,
                        request_id: str, claim: str, source: dict,
                        digest: str | None = None) -> dict:
        """Attach a bounded claim with provenance; agent reports remain labeled as reports."""
        nonempty_string(claim, "evidence claim", limit=4096)
        source = validate_source(source)
        if self.source_class == "agent_report" and source["kind"] != "agent_report":
            raise AccessDenied("Agent reports cannot attest host, tool, or file evidence.")
        if self.source_class == "host" and source["kind"] == "agent_report":
            raise ValueError("Use an agent_report service to label an agent claim.")
        payload = {"claim": claim, "source": source}
        if digest is not None:
            payload["digest"] = digest

        def transform(task: dict) -> None:
            item = {"id": uuid.uuid4().hex, "claim": claim, "source": source,
                    "captured_at": timestamp()}
            if digest is not None:
                item["digest"] = digest
            task["evidence"].append(item)

        return self._mutate(principal, task_id, expected_revision=expected_revision,
                            request_id=request_id, operation="evidence", payload=payload,
                            transform=transform)

    def capture_artifact(self, principal: Principal, task_id: str, *, expected_revision: int,
                         request_id: str, path: str) -> dict:
        """Record file bytes by digest; the receiver must verify them independently."""
        if self.source_class != "host":
            raise AccessDenied("Only the host may capture artifact bytes.")
        task = self.show(principal, task_id)
        manifest = _artifact_manifest(task["workspace"]["root"], path)

        def transform(current: dict) -> None:
            if current["workspace"] != task["workspace"]:
                raise ValueError("Task workspace changed during artifact capture.")
            current["artifacts"] = [item for item in current["artifacts"]
                                    if item["path"] != manifest["path"]]
            current["artifacts"].append(manifest)
            current["artifacts"].sort(key=lambda item: item["path"])

        return self._mutate(principal, task_id, expected_revision=expected_revision,
                            request_id=request_id, operation="artifact", payload=manifest,
                            transform=transform)
