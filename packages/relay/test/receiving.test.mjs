import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  readFileSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:http";
import { WebSocketServer } from "ws";
import { RelayStore } from "../../../dist/store.js";
import { AdapterManager } from "../../../dist/adapters/manager.js";
import { Workspace } from "../../../dist/workspace.js";
import { Receiving } from "../../../dist/receiving.js";
import { inspectCodex, codexEndpoint } from "../../../dist/adapters/live.js";
import { RelayError } from "../../../dist/model.js";

function fixture(t, harness = "codex", options = {}) {
  const dir = mkdtempSync(join(tmpdir(), "relay-delivery-"));
  const root = join(dir, "project");
  mkdirSync(root);
  const store = new RelayStore(join(dir, "data"));
  const adapters = new AdapterManager(store),
    workspace = new Workspace(store, adapters);
  const receiving = new Receiving(store, adapters, {
    intervalMs: 100000,
    ...options,
  });
  const project = store.addProject({ root });
  const source = store.importSession({
    projectId: project.id,
    harness: "manual",
    externalId: "source",
    title: "Source",
    events: [{ id: "u1", role: "user", text: "Preserve the tests." }],
  }).session;
  const target = store.importSession({
    projectId: project.id,
    harness,
    externalId: "native-target",
    title: "Target",
    events: [],
  }).session;
  const offer = () =>
    workspace.relay({
      projectId: project.id,
      title: "Reviewed handoff",
      goal: "Check $() and `quoted` input as plain data.",
      sources: [{ sessionId: source.id, revision: source.revision }],
      evidence: [{ sessionId: source.id, eventId: "u1" }],
      destinationSessionId: target.id,
    });
  t.after(async () => {
    await receiving.close();
    await workspace.close();
    await adapters.close();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  });
  return {
    dir,
    root: project.root,
    store,
    adapters,
    workspace,
    receiving,
    project,
    source,
    target,
    offer,
  };
}

test("native outbox waits for the owning host, sends once, persists, and distinguishes dispatch from acceptance", async (t) => {
  let online = false,
    calls = [];
  const w = fixture(t, "codex", {
    inspectCodex: async () => {
      if (!online) throw new RelayError("receiver_offline", "Waiting for host");
    },
    run: async (bin, args, options) => {
      calls.push({ bin, args, options });
      return { stdout: "", stderr: "" };
    },
  });
  w.adapters.connect({
    projectId: w.project.id,
    harness: "codex",
    executable: "/fixture/codex",
    configureMcp: false,
  });
  const h = w.offer();
  w.receiving.enqueue(h.id);
  await w.receiving.flush();
  assert.equal(w.receiving.get(h.id).state, "queued");
  assert.equal(calls.length, 0);
  online = true;
  await w.receiving.flush();
  assert.equal(w.receiving.get(h.id).state, "sent");
  assert.equal(w.store.handoff(h.id).status, "offered");
  await w.receiving.flush();
  w.receiving.enqueue(h.id, true);
  await w.receiving.flush();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].args[0], "queue");
  assert.equal(
    calls[0].args[calls[0].args.indexOf("--thread") + 1],
    "native-target",
  );
  assert.equal(calls[0].options.cwd, w.root);
  assert.equal(calls[0].options.shell, undefined);
  const file = join(w.dir, "data/deliveries", h.id + ".md");
  assert.match(readFileSync(file, "utf8"), /Preserve the tests/);
  assert.equal(statSync(file).mode & 0o777, 0o600);
  w.store.accept(h.id, "codex", "native-target");
  assert.equal(w.store.handoff(h.id).status, "accepted");
  await w.receiving.close();
  const restarted = new Receiving(w.store, w.adapters, { intervalMs: 100000 });
  assert.equal(restarted.get(h.id).state, "sent");
  await restarted.close();
});

test("ambiguous native dispatch and process interruption never automatically resend", async (t) => {
  let sends = 0;
  const w = fixture(t, "codex", {
    inspectCodex: async () => {},
    run: async () => {
      sends++;
      throw Error("transport disconnected");
    },
  });
  w.adapters.connect({
    projectId: w.project.id,
    harness: "codex",
    executable: "/fixture/codex",
    configureMcp: false,
  });
  const h = w.offer();
  w.receiving.enqueue(h.id);
  await w.receiving.flush();
  assert.equal(w.receiving.get(h.id).state, "uncertain");
  await w.receiving.flush();
  assert.equal(sends, 1);
  w.receiving.enqueue(h.id, true);
  await w.receiving.flush();
  assert.equal(sends, 2);
  const d = { ...w.receiving.get(h.id), state: "sending" };
  w.store.db
    .prepare("UPDATE workspace_state SET body=? WHERE key=?")
    .run(JSON.stringify(d), "delivery:" + h.id);
  await w.receiving.close();
  const restarted = new Receiving(w.store, w.adapters, { intervalMs: 100000 });
  assert.equal(restarted.get(h.id).state, "uncertain");
  await restarted.close();
});

test("cancellation and source edits during receiver probing prevent dispatch", async (t) => {
  let mutate = () => {},
    sends = 0;
  const w = fixture(t, "codex", {
    inspectCodex: async () => mutate(),
    run: async () => {
      sends++;
    },
  });
  w.adapters.connect({
    projectId: w.project.id,
    harness: "codex",
    executable: "/fixture/codex",
    configureMcp: false,
  });
  const cancelled = w.offer();
  w.receiving.enqueue(cancelled.id);
  mutate = () => w.store.cancel(cancelled.id);
  await w.receiving.flush();
  assert.equal(sends, 0);
  assert.equal(w.receiving.get(cancelled.id).state, "blocked");
  const stale = w.offer();
  w.receiving.enqueue(stale.id);
  mutate = () =>
    w.store.importSession({
      projectId: w.project.id,
      harness: "manual",
      externalId: "source",
      title: "Source",
      events: [{ id: "u2", role: "user", text: "Changed requirements." }],
    });
  await w.receiving.flush();
  assert.equal(sends, 0);
  assert.match(w.receiving.get(stale.id).message, /changed/);
});

test("MCP channel claims only its bound native session, with one claim and matching receipts", async (t) => {
  const w = fixture(t, "claude");
  const another = w.store.importSession({
    projectId: w.project.id,
    harness: "claude",
    externalId: "other",
    title: "Other",
    events: [],
  }).session;
  const h = w.offer();
  w.receiving.enqueue(h.id);
  const register = (externalId) =>
    w.receiving.register({
      projectId: w.project.id,
      harness: "claude",
      externalId,
    });
  assert.throws(() => register("missing"), { code: "wrong_destination" });
  const a = register(another.externalId),
    b = register(w.target.externalId);
  assert.equal(w.receiving.next(a.id), null);
  assert.throws(() => register(w.target.externalId), {
    code: "receiver_exists",
  });
  const next = w.receiving.next(b.id);
  assert.equal(next.handoffId, h.id);
  assert.match(next.prompt, /native-target/);
  assert.equal(w.receiving.next(b.id), null);
  assert.throws(
    () =>
      w.receiving.receipt(a.id, {
        handoffId: h.id,
        attemptId: next.attemptId,
        sent: true,
      }),
    { code: "wrong_delivery" },
  );
  w.receiving.receipt(b.id, {
    handoffId: h.id,
    attemptId: next.attemptId,
    sent: true,
  });
  assert.equal(w.receiving.get(h.id).state, "sent");
  assert.equal(w.store.handoff(h.id).status, "offered");
});

test("Codex probe attaches to an existing WebSocket host and refuses unloaded or wrong-project threads", async (t) => {
  let loaded = true,
    root = "/fixture",
    methods = [];
  const server = new WebSocketServer({ host: "127.0.0.1", port: 0 });
  await new Promise((resolve) => server.once("listening", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  server.on("connection", (socket) =>
    socket.on("message", (bytes) => {
      const m = JSON.parse(String(bytes));
      methods.push(m.method);
      if (!m.id) return;
      const result =
        m.method === "thread/loaded/list"
          ? { data: loaded ? ["target"] : [] }
          : m.method === "thread/read"
            ? { thread: { id: "target", cwd: root } }
            : {};
      socket.send(JSON.stringify({ id: m.id, result }));
    }),
  );
  const endpoint = `ws://127.0.0.1:${server.address().port}`;
  await inspectCodex(endpoint, "target", "/fixture");
  loaded = false;
  await assert.rejects(inspectCodex(endpoint, "target", "/fixture"), {
    code: "receiver_offline",
  });
  loaded = true;
  root = "/other";
  await assert.rejects(inspectCodex(endpoint, "target", "/fixture"), {
    code: "wrong_destination",
  });
  assert.ok(
    methods.every((m) =>
      [
        "initialize",
        "initialized",
        "thread/loaded/list",
        "thread/read",
      ].includes(m),
    ),
  );
  assert.throws(() => codexEndpoint("ws://example.com:4500"), {
    code: "invalid_endpoint",
  });
  assert.throws(() => codexEndpoint("ws://127.0.0.1:4500/?token=secret"), {
    code: "invalid_endpoint",
  });
});

test("OpenCode sends to the captured server/session and never retries an ambiguous POST", async (t) => {
  let posts = [],
    status = 204,
    wrong = false;
  const w = fixture(t, "opencode");
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    assert.equal(url.searchParams.get("directory"), w.root);
    if (req.method === "GET") {
      res.setHeader("Content-Type", "application/json");
      res.end(
        JSON.stringify({
          id: "native-target",
          directory: wrong ? "/wrong" : w.root,
        }),
      );
    } else {
      let body = "";
      for await (const b of req) body += b;
      posts.push({ path: url.pathname, body: JSON.parse(body) });
      res.writeHead(status);
      res.end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  w.adapters.connect({
    projectId: w.project.id,
    harness: "opencode",
    source: `http://127.0.0.1:${server.address().port}`,
    configureMcp: false,
  });
  const h = w.offer();
  w.receiving.enqueue(h.id);
  await w.receiving.flush();
  assert.equal(w.receiving.get(h.id).state, "sent");
  assert.equal(posts[0].path, "/session/native-target/prompt_async");
  assert.match(posts[0].body.parts[0].text, /native-target/);
  status = 500;
  const bad = w.offer();
  w.receiving.enqueue(bad.id);
  await w.receiving.flush();
  await w.receiving.flush();
  assert.equal(posts.length, 2);
  assert.equal(w.receiving.get(bad.id).state, "uncertain");
  wrong = true;
  const mismatch = w.offer();
  w.receiving.enqueue(mismatch.id);
  await w.receiving.flush();
  assert.equal(posts.length, 2);
  assert.equal(w.receiving.get(mismatch.id).state, "blocked");
});

test("a real stdio MCP channel pushes a handoff and acknowledges only its bound destination", async (t) => {
  const { startService } = await import("../../../dist/server.js");
  const { RelayClient } = await import("../../../dist/client.js");
  const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
  const { StdioClientTransport } = await import(
    "@modelcontextprotocol/sdk/client/stdio.js"
  );
  const { z } = await import("zod");
  const dir = mkdtempSync(join(tmpdir(), "relay-channel-"));
  mkdirSync(join(dir, "project"));
  const app = await startService(join(dir, "data"));
  const client = new RelayClient(app.connection, app.token);
  const project = app.store.addProject({ root: join(dir, "project") });
  const source = app.store.importSession({
    projectId: project.id,
    harness: "codex",
    externalId: "source",
    title: "Source",
    events: [{ id: "a", role: "user", text: "Keep the tests." }],
  }).session;
  const target = app.store.importSession({
    projectId: project.id,
    harness: "claude",
    externalId: "channel-target",
    title: "Target",
    events: [],
  }).session;
  const other = app.store.importSession({
    projectId: project.id,
    harness: "claude",
    externalId: "channel-other",
    title: "Other",
    events: [],
  }).session;
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [
      join(process.cwd(), "dist/cli.js"),
      "mcp",
      "--data-dir",
      join(dir, "data"),
      "--harness",
      "claude",
      "--project",
      project.id,
      "--destination-id",
      target.externalId,
    ],
    stderr: "pipe",
  });
  const mcp = new Client({ name: "channel-test", version: "1" });
  t.after(async () => {
    await mcp.close();
    await app.close();
    rmSync(dir, { recursive: true, force: true });
  });
  const events = [];
  mcp.setNotificationHandler(
    z.object({
      method: z.literal("notifications/claude/channel"),
      params: z.object({
        content: z.string(),
        meta: z.record(z.string(), z.string()),
      }),
    }),
    (n) => events.push(n),
  );
  await mcp.connect(transport);
  assert.ok(mcp.getServerCapabilities().experimental["claude/channel"]);
  const offer = (destinationSessionId) =>
    client.request("/api/relays", {
      deliver: true,
      projectId: project.id,
      title: "Review",
      goal: "Continue with tests.",
      sources: [{ sessionId: source.id, revision: 1 }],
      evidence: [{ sessionId: source.id, eventId: "a" }],
      destinationSessionId,
    });
  const wrong = await offer(other.id),
    right = await offer(target.id);
  const deadline = Date.now() + 6000;
  while (!events.length && Date.now() < deadline)
    await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(events.length, 1);
  assert.equal(events[0].params.meta.handoff_id, right.id);
  await assert.rejects(client.request(`/api/handoffs/${right.id}/cancel`, {}), {
    code: "already_dispatched",
  });
  const inbox = await mcp.callTool({
    name: "relay_inbox",
    arguments: { destinationExternalId: other.externalId },
  });
  assert.deepEqual(
    JSON.parse(inbox.content[0].text).map((h) => h.id),
    [right.id],
  );
  const denied = await mcp.callTool({
    name: "relay_handoff_accept",
    arguments: { handoffId: wrong.id, destinationExternalId: other.externalId },
  });
  assert.equal(denied.isError, true);
  const accepted = await mcp.callTool({
    name: "relay_handoff_accept",
    arguments: { handoffId: right.id },
  });
  assert.equal(JSON.parse(accepted.content[0].text).status, "accepted");
  assert.equal(app.store.handoff(wrong.id).status, "offered");
});

test("stopping Relay during a host probe leaves the handoff queued without dispatching", async (t) => {
  let release,
    entered,
    sends = 0;
  const started = new Promise((resolve) => (entered = resolve));
  const probe = new Promise((resolve) => (release = resolve));
  const w = fixture(t, "codex", {
    inspectCodex: async () => {
      entered();
      await probe;
    },
    run: async () => {
      sends++;
    },
  });
  w.adapters.connect({
    projectId: w.project.id,
    harness: "codex",
    executable: "/fixture/codex",
    configureMcp: false,
  });
  const h = w.offer();
  w.receiving.enqueue(h.id);
  const pending = w.receiving.flush();
  await started;
  const stopping = w.receiving.close();
  release();
  await pending;
  await stopping;
  assert.equal(sends, 0);
  assert.equal(w.receiving.get(h.id).state, "queued");
});
