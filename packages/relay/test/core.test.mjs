import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { RelayStore } from "../../../dist/store.js";

function workspace(t) {
  const root = mkdtempSync(join(tmpdir(), "relay-core-"));
  const projectRoot = join(root, "project");
  mkdirSync(projectRoot);
  const store = new RelayStore(join(root, "data"));
  t.after(() => {
    store.close();
    rmSync(root, { recursive: true, force: true });
  });
  const project = store.addProject({ root: projectRoot, name: "Example" });
  const input = {
    projectId: project.id,
    harness: "claude",
    externalId: "source-1",
    title: "Investigate retries",
    events: [
      { id: "a", role: "user", text: "Use 24 hours." },
      { id: "b", role: "user", text: "Correction: use 7 days." },
    ],
  };
  const { session } = store.importSession(input);
  const draft = {
    projectId: project.id,
    title: "Continue",
    goal: "Avoid duplicate charges",
    sources: [{ sessionId: session.id, revision: session.revision }],
    instructions: [
      {
        key: "retention",
        text: "Use 7 days.",
        sources: [{ sessionId: session.id, eventId: "b" }],
      },
    ],
    openWork: ["Implement and check"],
    evidence: [{ sessionId: session.id, eventId: "b" }],
  };
  return { root, store, project, projectRoot, input, session, draft };
}
test("incremental imports are idempotent; changed source events roll back the batch", (t) => {
  const w = workspace(t);
  assert.equal(w.store.importSession(w.input).added, 0);
  assert.throws(
    () =>
      w.store.importSession({
        ...w.input,
        events: [
          { id: "c", role: "tool", text: "A new result" },
          { id: "a", role: "user", text: "Edited history" },
        ],
      }),
    { code: "source_changed" },
  );
  assert.equal(w.store.session(w.session.id).eventCount, 2);
  assert.equal(w.store.session(w.session.id).revision, 1);
  assert.equal(
    w.store.importSession({
      ...w.input,
      events: [{ id: "c", role: "tool", text: "A new result" }],
    }).session.revision,
    2,
  );
  assert.throws(() => w.store.createCheckpoint(w.draft), {
    code: "stale_source",
  });
});
test("checkpoint sources cannot cross projects or cite nonexistent events", (t) => {
  const w = workspace(t);
  const second = join(w.root, "other");
  mkdirSync(second);
  const other = w.store.addProject({ root: second });
  assert.throws(
    () => w.store.createCheckpoint({ ...w.draft, projectId: other.id }),
    { code: "project_mismatch" },
  );
  assert.throws(
    () =>
      w.store.createCheckpoint({
        ...w.draft,
        evidence: [{ sessionId: w.session.id, eventId: "missing" }],
      }),
    { code: "not_found" },
  );
  assert.throws(
    () =>
      w.store.createCheckpoint({
        ...w.draft,
        instructions: [
          ...w.draft.instructions,
          { ...w.draft.instructions[0], text: "24 hours" },
        ],
      }),
    { code: "unresolved_conflict" },
  );
});
test("handoff checks recipient, file state and immutable packet integrity", (t) => {
  const w = workspace(t);
  writeFileSync(join(w.projectRoot, "result.txt"), "before");
  const checkpoint = w.store.createCheckpoint({
    ...w.draft,
    files: ["result.txt"],
  });
  const handoff = w.store.offer({
    checkpointId: checkpoint.id,
    recipient: "codex",
  });
  assert.throws(() => w.store.accept(handoff.id, "goose"), {
    code: "wrong_recipient",
  });
  writeFileSync(join(w.projectRoot, "result.txt"), "after");
  assert.throws(() => w.store.accept(handoff.id, "codex"), {
    code: "stale_source",
  });
  writeFileSync(join(w.projectRoot, "result.txt"), "before");
  const accepted = w.store.accept(handoff.id, "codex");
  assert.equal(accepted.status, "accepted");
  assert.deepEqual(w.store.accept(handoff.id, "codex"), accepted);
  assert.throws(() => w.store.cancel(handoff.id), { code: "already_accepted" });
  handoff.packet.goal = "tampered";
  w.store.db
    .prepare("UPDATE handoffs SET body=? WHERE id=?")
    .run(JSON.stringify(handoff), handoff.id);
  assert.throws(() => w.store.handoff(handoff.id), { code: "integrity" });
});
test("source advance invalidates an offer; cancellation is terminal", (t) => {
  const w = workspace(t);
  const checkpoint = w.store.createCheckpoint(w.draft);
  const handoff = w.store.offer({
    checkpointId: checkpoint.id,
    recipient: "codex",
  });
  w.store.importSession({
    ...w.input,
    events: [{ id: "c", role: "user", text: "Another correction" }],
  });
  assert.throws(() => w.store.accept(handoff.id, "codex"), {
    code: "stale_source",
  });
  w.store.cancel(handoff.id);
  assert.throws(() => w.store.accept(handoff.id, "codex"), {
    code: "cancelled",
  });
});
test("conflicting parent instructions need an explicit resolution", (t) => {
  const w = workspace(t);
  const first = w.store.createCheckpoint(w.draft);
  const second = w.store.createCheckpoint({
    ...w.draft,
    instructions: [{ ...w.draft.instructions[0], text: "24 hours" }],
  });
  const merged = { ...w.draft, parents: [first.id, second.id] };
  assert.throws(() => w.store.createCheckpoint(merged), {
    code: "unresolved_conflict",
  });
  const resolved = w.store.createCheckpoint({
    ...merged,
    resolutions: { retention: "Use 7 days." },
  });
  assert.deepEqual(resolved.parents, [first.id, second.id]);
  assert.equal(w.store.checkpoint(second.id).instructions[0].text, "24 hours");
});
test("packets preserve required instructions and disclose evidence omitted by a UTF-8 byte budget", (t) => {
  const w = workspace(t);
  const updated = w.store.importSession({
    ...w.input,
    events: [{ id: "long", role: "tool", text: "界".repeat(1000) }],
  });
  const checkpoint = w.store.createCheckpoint({
    ...w.draft,
    sources: [{ sessionId: w.session.id, revision: updated.session.revision }],
    evidence: [{ sessionId: w.session.id, eventId: "long" }],
  });
  const packet = w.store.packet(checkpoint.id, 2000);
  assert.equal(packet.instructions[0].text, "Use 7 days.");
  assert.equal(packet.omitted.evidence, 1);
  assert.equal(packet.evidence.length, 0);
  assert.throws(() => w.store.packet(checkpoint.id, 512), {
    code: "budget_exceeded",
  });
});
test("artifacts cannot escape the project and recognized credentials are redacted before persistence", (t) => {
  const w = workspace(t);
  writeFileSync(join(w.root, "outside.txt"), "private");
  assert.throws(
    () => w.store.createCheckpoint({ ...w.draft, files: ["../outside.txt"] }),
    { code: "outside_project" },
  );
  w.store.importSession({
    ...w.input,
    events: [
      { id: "secret", role: "user", text: "api_key=example-secret-value" },
    ],
  });
  assert.equal(
    w.store.event(w.session.id, "secret").text,
    "api_key=[REDACTED]",
  );
});
test("SQLite state survives restart and refuses unknown future schema versions", (t) => {
  const w = workspace(t);
  const checkpoint = w.store.createCheckpoint(w.draft);
  const second = new RelayStore(join(w.root, "data"));
  assert.equal(second.checkpoint(checkpoint.id).digest, checkpoint.digest);
  second.close();
  w.store.db.exec("PRAGMA user_version=999");
  assert.throws(() => new RelayStore(join(w.root, "data")), {
    code: "newer_database",
  });
});
