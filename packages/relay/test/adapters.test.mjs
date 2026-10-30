import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:http";
import { RelayStore } from "../../../dist/store.js";
import { AdapterManager } from "../../../dist/adapters/manager.js";
import { codexEvents, gooseEvents } from "../../../dist/adapters/normalize.js";
import { JsonLineRpc } from "../../../dist/adapters/rpc.js";
function workspace(t) {
  const root = mkdtempSync(join(tmpdir(), "relay-adapters-"));
  const projectRoot = join(root, "project");
  mkdirSync(projectRoot);
  const store = new RelayStore(join(root, "data"));
  const manager = new AdapterManager(store);
  const project = store.addProject({ root: projectRoot });
  t.after(async () => {
    await manager.close();
    store.close();
    rmSync(root, { recursive: true, force: true });
  });
  return { root, projectRoot, store, manager, project };
}
async function finish(manager, job) {
  for (let n = 0; n < 200 && job.state === "running"; n++) {
    await new Promise((r) => setTimeout(r, 10));
    job = manager.job(job.id);
  }
  assert.notEqual(job.state, "running");
  return job;
}
test("Claude discovery scopes projects, preserves subagents, ignores partial writes and captures idempotently", async (t) => {
  const w = workspace(t);
  const source = join(w.root, "transcripts");
  mkdirSync(source);
  const record = (uuid, text) => ({
    type: "user",
    uuid,
    sessionId: "parent",
    cwd: w.projectRoot,
    message: { content: text },
  });
  writeFileSync(
    join(source, "parent.jsonl"),
    JSON.stringify(record("one", "Keep seven days")) + '\n{"unfinished":',
  );
  writeFileSync(
    join(source, "agent-child.jsonl"),
    JSON.stringify(record("two", "Subagent result")) + "\n",
  );
  writeFileSync(
    join(source, "other.jsonl"),
    JSON.stringify({ ...record("three", "Other project"), cwd: w.root }) + "\n",
  );
  const c = w.manager.connect({
    projectId: w.project.id,
    harness: "claude",
    source,
    configureMcp: false,
  });
  const found = await w.manager.discover(c.id);
  assert.equal(found.length, 2);
  assert.equal(new Set(found.map((s) => s.externalId)).size, 2);
  let job = await finish(w.manager, w.manager.startSync(c.id));
  assert.equal(job.state, "completed");
  assert.equal(job.imported.length, 2);
  assert.equal(job.completed, 2);
  job = await finish(w.manager, w.manager.startSync(c.id));
  assert(job.imported.every((s) => s.added === 0));
  assert.equal(w.store.sessions(w.project.id).length, 2);
});
test("managed JSON and TOML preserve other settings and refuse removal after edits", (t) => {
  const w = workspace(t);
  const path = join(w.projectRoot, ".mcp.json");
  writeFileSync(
    path,
    JSON.stringify({ mcpServers: { existing: { command: "keep" } } }),
  );
  const c = w.manager.connect({
    projectId: w.project.id,
    harness: "claude",
    capture: false,
  });
  assert.equal(
    JSON.parse(readFileSync(path)).mcpServers.existing.command,
    "keep",
  );
  const data = JSON.parse(readFileSync(path));
  data.mcpServers.context_relay.args.push("--edited");
  writeFileSync(path, JSON.stringify(data));
  assert.throws(() => w.manager.disconnect(c.id), { code: "config_changed" });
  data.mcpServers.context_relay = c.receipt.value;
  writeFileSync(path, JSON.stringify(data));
  w.manager.disconnect(c.id);
  assert.deepEqual(JSON.parse(readFileSync(path)), {
    mcpServers: { existing: { command: "keep" } },
  });
  mkdirSync(join(w.projectRoot, ".codex"));
  const toml = join(w.projectRoot, ".codex/config.toml");
  writeFileSync(toml, 'model = "keep"\n');
  const codex = w.manager.connect({
    projectId: w.project.id,
    harness: "codex",
    capture: false,
  });
  assert.match(readFileSync(toml, "utf8"), /mcp_servers.context_relay/);
  w.manager.disconnect(codex.id);
  assert.match(readFileSync(toml, "utf8"), /model = "keep"/);
});
test(
  "configuration rejects a symlink directory even if the config does not exist",
  { skip: process.platform === "win32" },
  (t) => {
    const w = workspace(t);
    symlinkSync(w.root, join(w.projectRoot, ".codex"), "dir");
    assert.throws(
      () => w.manager.connect({ projectId: w.project.id, harness: "codex" }),
      { code: "unsafe_config" },
    );
  },
);
test("OpenCode HTTP capture excludes unfinished messages and other project sessions", async (t) => {
  const w = workspace(t);
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    assert.equal(url.searchParams.get("directory"), w.project.root);
    res.setHeader("content-type", "application/json");
    const payload =
      url.pathname === "/session"
        ? [
            { id: "s1", directory: w.projectRoot, title: "Fix" },
            { id: "s2", directory: w.root },
          ]
        : url.pathname === "/session/s1"
          ? { directory: w.projectRoot }
          : [
              {
                info: { id: "m1", role: "user" },
                parts: [{ type: "text", text: "Retain seven days" }],
              },
              {
                info: { id: "m2", role: "assistant", time: {} },
                parts: [{ type: "text", text: "partial" }],
              },
              {
                info: { id: "m3", role: "assistant", time: { completed: 123 } },
                parts: [
                  { type: "reasoning", text: "private" },
                  { type: "text", text: "Done" },
                ],
              },
            ];
    res.end(JSON.stringify(payload));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  t.after(() => new Promise((r) => server.close(r)));
  const c = w.manager.connect({
    projectId: w.project.id,
    harness: "opencode",
    source: `http://127.0.0.1:${server.address().port}`,
    configureMcp: false,
  });
  const job = await finish(w.manager, w.manager.startSync(c.id));
  assert.equal(job.errors.length, 0);
  assert.equal(job.imported.length, 1);
  assert.equal(w.store.events(job.imported[0].sessionId).length, 2);
});
test("Codex RPC performs a real stdio exchange and denies execution requests", async (t) => {
  const w = workspace(t);
  const script = join(w.root, "rpc.mjs");
  writeFileSync(
    script,
    `import readline from 'node:readline'; const send=x=>console.log(JSON.stringify(x)); readline.createInterface({input:process.stdin}).on('line',line=>{const m=JSON.parse(line);if(m.method==='initialize') {send({jsonrpc:'2.0',id:99,method:'exec',params:{}});send({jsonrpc:'2.0',id:m.id,result:{ready:true}});} if(m.id===99) process.exit(m.error?.code===-32601?0:1);});`,
  );
  const rpc = new JsonLineRpc(process.execPath, [script], w.projectRoot);
  t.after(() => rpc.close());
  assert.deepEqual(await rpc.request("initialize", {}), { ready: true });
  const events = codexEvents([
    {
      id: "t",
      status: "completed",
      items: [
        {
          id: "u",
          type: "userMessage",
          content: [{ type: "text", text: "Goal" }],
        },
        { id: "a", type: "agentMessage", text: "Result" },
        { id: "r", type: "reasoning", text: "private" },
      ],
    },
    {
      id: "t2",
      status: "inProgress",
      items: [{ id: "a", type: "agentMessage", text: "partial" }],
    },
  ]);
  assert.equal(events.length, 2);
  assert.equal(
    gooseEvents({
      messages: [{ role: "user", content: [{ type: "text", text: "Goal" }] }],
    })[0].text,
    "Goal",
  );
});
test("interrupted jobs are recovered visibly, and failed discovery is never successful capture", async (t) => {
  const w = workspace(t);
  w.store.db
    .prepare("INSERT INTO capture_jobs(id,body) VALUES (?,?)")
    .run("old", JSON.stringify({ id: "old", state: "running" }));
  const recovered = new AdapterManager(w.store);
  assert.equal(recovered.job("old").state, "interrupted");
  const c = w.manager.connect({
    projectId: w.project.id,
    harness: "goose",
    executable: join(w.root, "missing-goose"),
    configureMcp: false,
  });
  const job = await finish(w.manager, w.manager.startSync(c.id));
  assert.equal(job.state, "failed");
  assert.equal(job.imported.length, 0);
  assert.match(job.error, /Goose/);
});
