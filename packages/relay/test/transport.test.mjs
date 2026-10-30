import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { request } from "node:http";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { startService } from "../../../dist/server.js";
import { RelayClient } from "../../../dist/client.js";

async function service(t) {
  const directory = mkdtempSync(join(tmpdir(), "relay-transport-"));
  const app = await startService(directory);
  const client = new RelayClient(app.connection, app.token);
  t.after(async () => {
    await app.close();
    rmSync(directory, { recursive: true, force: true });
  });
  return { directory, app, client };
}
test("loopback service rejects unauthenticated, cross-origin, DNS rebinding and malformed requests", async (t) => {
  const { app, client } = await service(t);
  const url = app.connection.url;
  assert.equal((await fetch(url + "/api/projects")).status, 401);
  assert.equal(
    (
      await fetch(url + "/api/projects", {
        headers: {
          Authorization: `Bearer ${app.token}`,
          Origin: "https://evil.example",
        },
      })
    ).status,
    403,
  );
  const hostileHost = await new Promise((resolve, reject) => {
    const req = request(
      url + "/api/projects",
      {
        headers: { Authorization: `Bearer ${app.token}`, Host: "evil.example" },
      },
      (res) => {
        res.resume();
        resolve(res.statusCode);
      },
    );
    req.on("error", reject);
    req.end();
  });
  assert.equal(hostileHost, 403);
  assert.equal(
    (
      await fetch(url + "/api/projects", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${app.token}`,
          "Content-Type": "application/json",
        },
        body: "not json",
      })
    ).status,
    400,
  );
  assert.deepEqual(await client.request("/api/projects"), []);
  assert.equal((await client.request("/api/health")).name, "context-relay");
});
test("a real stdio MCP client retrieves and accepts only its project and recipient handoff", async (t) => {
  const { directory, client } = await service(t);
  const root = join(directory, "project");
  mkdirSync(root);
  const project = await client.request("/api/projects", { root });
  const imported = await client.request("/api/sessions/import", {
    projectId: project.id,
    harness: "claude",
    externalId: "fixture",
    title: "Fixture",
    events: [{ id: "u1", role: "user", text: "Use JSON." }],
  });
  const checkpoint = await client.request("/api/checkpoints", {
    projectId: project.id,
    title: "Continue",
    goal: "Produce JSON",
    sources: [{ sessionId: imported.session.id, revision: 1 }],
    instructions: [
      {
        key: "format",
        text: "Use JSON.",
        sources: [{ sessionId: imported.session.id, eventId: "u1" }],
      },
    ],
  });
  const handoff = await client.request("/api/handoffs", {
    checkpointId: checkpoint.id,
    recipient: "codex",
  });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [
      fileURLToPath(new URL("../../../dist/cli.js", import.meta.url)),
      "mcp",
      "--data-dir",
      directory,
      "--project",
      project.id,
      "--harness",
      "codex",
    ],
    stderr: "pipe",
  });
  const mcp = new Client({ name: "relay-test", version: "1.0.0" });
  await mcp.connect(transport);
  t.after(() => mcp.close());
  assert.equal((await mcp.listTools()).tools.length, 6);
  const packet = await mcp.callTool({
    name: "relay_packet",
    arguments: { checkpointId: checkpoint.id },
  });
  assert.equal(packet.isError, undefined);
  assert.match(packet.content[0].text, /Use JSON/);
  const accepted = await mcp.callTool({
    name: "relay_handoff_accept",
    arguments: { handoffId: handoff.id },
  });
  assert.equal(JSON.parse(accepted.content[0].text).status, "accepted");
  const resource = await mcp.readResource({
    uri: `relay://checkpoint/${checkpoint.id}`,
  });
  assert.match(resource.contents[0].text, /Use JSON/);
  const otherRoot = join(directory, "other");
  mkdirSync(otherRoot);
  const other = await client.request("/api/projects", { root: otherRoot });
  const otherSession = await client.request("/api/sessions/import", {
    projectId: other.id,
    harness: "manual",
    externalId: "private",
    title: "Private",
    events: [],
  });
  const rejected = await mcp.callTool({
    name: "relay_session_events",
    arguments: { sessionId: otherSession.session.id },
  });
  assert.equal(rejected.isError, true);
});
