import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  appendFileSync,
  existsSync,
  rmSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { RelayStore } from "../../../dist/store.js";
import { AdapterManager } from "../../../dist/adapters/manager.js";
import { Workspace } from "../../../dist/workspace.js";
import { discoverLocal } from "../../../dist/adapters/local.js";
import { startService } from "../../../dist/server.js";
import { RelayClient } from "../../../dist/client.js";
import { launchPlan } from "../../../dist/delivery.js";

function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), "relay-map-")),
    root = join(dir, "project"),
    source = join(dir, "claude");
  mkdirSync(root);
  mkdirSync(source);
  const store = new RelayStore(join(dir, "data")),
    adapters = new AdapterManager(store);
  const line = (id, text) =>
    JSON.stringify({
      type: "user",
      uuid: id,
      sessionId: "native-a",
      cwd: root,
      message: { content: text },
    }) + "\n";
  writeFileSync(join(source, "native-a.jsonl"), line("u1", "Keep seven days."));
  const discover = async (h) =>
    h === "claude"
      ? discoverLocal(h, { source })
      : { harness: h, state: "unavailable", message: "Fixture", sessions: [] };
  const workspace = new Workspace(store, adapters, {
    discover,
    intervalMs: 100000,
  });
  t.after(async () => {
    await workspace.close();
    await adapters.close();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });
  return { dir, root, source, store, adapters, workspace, line, discover };
}
test("automatic discovery creates projects and threads, skips unchanged captures, picks up appends and persists pause", async (t) => {
  const w = fixture(t);
  w.workspace.configure({ enabled: true });
  await w.workspace.settled();
  let graph = w.workspace.graph();
  assert.equal(graph.projects.length, 1);
  assert.equal(graph.sessions.length, 1);
  assert.equal(graph.sessions[0].eventCount, 1);
  assert.equal(graph.sessions[0].captured, true);
  assert.equal(existsSync(join(w.root, ".mcp.json")), false);
  assert.equal(w.adapters.connections()[0].configureMcp, false);
  w.workspace.scan();
  await w.workspace.settled();
  assert.equal(w.store.sessions()[0].revision, 1);
  appendFileSync(
    join(w.source, "native-a.jsonl"),
    w.line("u2", "Actually keep fourteen days."),
  );
  w.workspace.scan();
  await w.workspace.settled();
  assert.equal(w.store.sessions()[0].revision, 2);
  assert.equal(w.store.events(graph.sessions[0].id).length, 2);
  w.workspace.configure({ enabled: false });
  appendFileSync(
    join(w.source, "native-a.jsonl"),
    w.line("u3", "Not yet indexed."),
  );
  w.workspace.scan();
  await w.workspace.settled();
  assert.equal(w.store.sessions()[0].eventCount, 2);
  await w.workspace.close();
  const restarted = new Workspace(w.store, w.adapters, {
    discover: w.discover,
  });
  assert.equal(restarted.status().enabled, false);
  assert.equal(restarted.status().configured, true);
  await restarted.close();
});
test("malformed transcript does not prevent healthy thread discovery", async (t) => {
  const w = fixture(t);
  writeFileSync(join(w.source, "bad.jsonl"), "not json\n");
  const result = await discoverLocal("claude", { source: w.source });
  assert.equal(result.sessions.length, 1);
  assert.match(result.message, /1 unreadable/);
});
test("targeted multi-source handoff is atomic, validates target and revision, then becomes stale on new source context", async (t) => {
  const w = fixture(t);
  w.workspace.configure({ enabled: true });
  await w.workspace.settled();
  w.workspace.configure({ enabled: false });
  const source = w.store.sessions()[0],
    p = w.store.projects()[0];
  const second = w.store.importSession({
    projectId: p.id,
    harness: "goose",
    externalId: "native-b",
    title: "Tests",
    events: [{ id: "g1", role: "assistant", text: "Two tests fail." }],
  }).session;
  const target = w.store.importSession({
    projectId: p.id,
    harness: "codex",
    externalId: "native-c",
    title: "Implement",
    events: [],
  }).session;
  const draft = {
    projectId: p.id,
    title: "Continue",
    goal: "Fix failing tests with 14-day retention.",
    sources: [
      { sessionId: source.id, revision: 1 },
      { sessionId: second.id, revision: 1 },
    ],
    evidence: [
      { sessionId: source.id, eventId: "u1" },
      { sessionId: second.id, eventId: "g1" },
    ],
    destinationSessionId: target.id,
  };
  assert.throws(
    () => w.workspace.relay({ ...draft, destinationSessionId: source.id }),
    { code: "self_relay" },
  );
  assert.equal(w.store.checkpoints().length, 0);
  assert.throws(
    () =>
      w.workspace.relay({
        ...draft,
        sources: [{ sessionId: source.id, revision: 0 }],
      }),
    { code: "stale_source" },
  );
  assert.equal(w.store.checkpoints().length, 0);
  const h = w.workspace.relay(draft);
  assert.equal(h.packet.destination.externalId, "native-c");
  assert.equal(h.packet.evidence.length, 2);
  assert.throws(() => w.store.accept(h.id, "codex", "wrong-thread"), {
    code: "wrong_destination",
  });
  assert.throws(() => launchPlan(h, p), { code: "existing_destination" });
  assert.equal(w.store.accept(h.id, "codex", "native-c").status, "accepted");
  const pending = w.workspace.relay(draft);
  w.store.importSession({
    projectId: p.id,
    harness: "claude",
    externalId: source.externalId,
    title: source.title,
    events: [{ id: "u-new", role: "user", text: "New decision." }],
  });
  assert.equal(
    w.workspace.graph().relays.find((r) => r.id === pending.id).freshness
      .current,
    false,
  );
  assert.throws(() => w.store.accept(pending.id, "codex", "native-c"), {
    code: "stale_source",
  });
  assert.equal(w.store.cancel(pending.id).status, "cancelled");
});
test("graph API is authenticated; node positions survive restart and invalid coordinates are rejected", async (t) => {
  const dir = mkdtempSync(join(tmpdir(), "relay-graph-http-"));
  const root = join(dir, "project");
  mkdirSync(root);
  let app = await startService(join(dir, "data"));
  let client = new RelayClient(app.connection, app.token);
  t.after(async () => {
    await app.close();
    rmSync(dir, { recursive: true, force: true });
  });
  assert.equal((await fetch(app.connection.url + "/api/graph")).status, 401);
  const p = app.store.addProject({ root });
  const s = app.store.importSession({
    projectId: p.id,
    harness: "codex",
    externalId: "native",
    title: "Test",
    events: [],
  }).session;
  await client.request("/api/graph/position", {
    sessionId: s.id,
    x: 180,
    y: -20,
  });
  await assert.rejects(() =>
    client.request("/api/graph/position", { sessionId: s.id, x: 100000, y: 0 }),
  );
  await client.request("/api/workspace", { enabled: false });
  await app.close();
  app = await startService(join(dir, "data"));
  client = new RelayClient(app.connection, app.token);
  assert.deepEqual((await client.request("/api/graph")).positions[s.id], {
    x: 180,
    y: -20,
  });
});
test("a failed harness does not prevent others from being indexed; failed captures remain visible and retry is bounded", async (t) => {
  const w = fixture(t);
  await w.workspace.close();
  let captures = 0;
  const workspace = new Workspace(w.store, w.adapters, {
    discover: async (h) => {
      if (h === "codex") throw Error("Offline");
      return w.discover(h);
    },
    capture: async () => {
      captures++;
      throw Error("Unreadable");
    },
    intervalMs: 100000,
  });
  t.after(() => workspace.close());
  workspace.configure({ enabled: true });
  await workspace.settled();
  assert.equal(workspace.graph().sessions.length, 1);
  assert.match(workspace.graph().sessions[0].captureError, /could not be read/);
  assert.equal(workspace.graph().sessions[0].captured, false);
  workspace.scan();
  await workspace.settled();
  assert.equal(captures, 1);
  assert.equal(
    workspace.status().sources.find((s) => s.harness === "codex").state,
    "error",
  );
});

test("updating a pending connection preserves history and rolls back an invalid replacement", async (t) => {
  const w = fixture(t);
  w.workspace.configure({ enabled: true });
  await w.workspace.settled();
  w.workspace.configure({ enabled: false });
  const source = w.store.sessions()[0],
    p = w.store.projects()[0];
  const target = (externalId) =>
    w.store.importSession({
      projectId: p.id,
      harness: "codex",
      externalId,
      title: externalId,
      events: [],
    }).session;
  const b = target("thread-b"),
    c = target("thread-c"),
    draft = {
      projectId: p.id,
      title: "Continue",
      goal: "Continue task",
      sources: [{ sessionId: source.id, revision: 1 }],
      evidence: [{ sessionId: source.id, eventId: "u1" }],
      destinationSessionId: b.id,
    };
  const first = w.workspace.relay(draft);
  assert.throws(
    () =>
      w.workspace.relay({
        ...draft,
        destinationSessionId: source.id,
        replacesHandoffId: first.id,
      }),
    { code: "self_relay" },
  );
  assert.equal(w.store.handoff(first.id).status, "offered");
  assert.equal(w.store.checkpoints().length, 1);
  const updated = w.workspace.relay({
    ...draft,
    destinationSessionId: c.id,
    replacesHandoffId: first.id,
  });
  assert.equal(w.store.handoff(first.id).status, "cancelled");
  assert.equal(updated.packet.destination.sessionId, c.id);
});

test("Codex capture pages items, excludes active turns, and retains the legacy store fallback", async () => {
  const { captureCodexHistory } = await import(
    "../../../dist/adapters/capture.js"
  );
  const { RelayError } = await import("../../../dist/model.js");
  const calls = [];
  const rpc = {
    request: async (method, params) => {
      calls.push({ method, params });
      if (method === "thread/turns/list")
        return {
          data: [
            { id: "done", status: "completed" },
            { id: "active", status: "inProgress" },
          ],
        };
      return {
        data: [
          {
            turnId: "done",
            item: {
              id: "u1",
              type: "userMessage",
              content: [{ type: "text", text: "Carry this." }],
            },
          },
          {
            turnId: "active",
            item: { id: "a1", type: "agentMessage", text: "Unfinished" },
          },
        ],
      };
    },
  };
  const events = await captureCodexHistory(rpc, "thread");
  assert.equal(events.length, 1);
  assert.equal(events[0].text, "Carry this.");
  assert.equal(calls[0].params.itemsView, "notLoaded");
  assert.equal(calls[1].params.limit, 20);
  const legacy = {
    request: async (method, params) => {
      if (method === "thread/items/list")
        throw new RelayError("harness_rpc_-32601", "Unsupported");
      return {
        data: [
          {
            id: "done",
            status: "completed",
            ...(params.itemsView === "full"
              ? {
                  items: [
                    { id: "a1", type: "agentMessage", text: "Legacy result" },
                  ],
                }
              : {}),
          },
        ],
      };
    },
  };
  assert.equal(
    (await captureCodexHistory(legacy, "thread"))[0].text,
    "Legacy result",
  );
});

test("oversized source events remain reviewable with an explicit omission count", async () => {
  const { codexEvents } = await import("../../../dist/adapters/normalize.js");
  const [event] = codexEvents([
    {
      id: "turn",
      status: "completed",
      items: [{ id: "item", type: "agentMessage", text: "x".repeat(120000) }],
    },
  ]);
  assert.equal(event.omittedCharacters, 24000);
  assert(event.text.length < 100000);
  assert.match(event.text, /24000 characters omitted/);
});

test("incremental Codex capture stops at the known event, retains its content check and returns chronological events", async () => {
  const { captureCodexHistory } = await import(
    "../../../dist/adapters/capture.js"
  );
  let itemPages = 0;
  const rpc = {
    request: async (method, params) => {
      if (method === "thread/turns/list")
        return { data: [{ id: "t", status: "completed" }] };
      itemPages++;
      assert.equal(params.sortDirection, "desc");
      return {
        data: [
          {
            turnId: "t",
            item: { id: "new", type: "agentMessage", text: "New result" },
          },
          {
            turnId: "t",
            item: { id: "known", type: "agentMessage", text: "Known result" },
          },
        ],
        nextCursor: "older-history",
      };
    },
  };
  const events = await captureCodexHistory(rpc, "thread", 20, "t:known");
  assert.equal(itemPages, 1);
  assert.deepEqual(
    events.map((e) => e.id),
    ["t:known", "t:new"],
  );
});

test("Claude streams histories over 16 MiB, marks oversized events and ignores the unfinished tail", async (t) => {
  const w = fixture(t);
  const { captureClaudeHistory } = await import(
    "../../../dist/adapters/capture.js"
  );
  const path = join(w.source, "long.jsonl");
  const large = "x".repeat(9 * 1024 * 1024);
  writeFileSync(path, w.line("first", "Start here."));
  appendFileSync(path, w.line("large-1", large));
  appendFileSync(path, w.line("large-2", large));
  appendFileSync(path, '{"unfinished":');
  const events = await captureClaudeHistory(
    path,
    w.store.addProject({ root: w.root }).root,
  );
  assert.equal(events.length, 3);
  assert.equal(events[0].id, "first");
  assert.equal(events[1].omittedCharacters, large.length - 96000);
  assert(events.every((e) => e.text.length <= 100000));
});
