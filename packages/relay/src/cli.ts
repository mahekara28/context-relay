#!/usr/bin/env node
import { parseArgs } from "node:util";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { connect, ensureService, recoverDeadLock } from "./client.js";
import { startService, VERSION } from "./server.js";
import { dataDirectory } from "./util.js";
import { serveMcp } from "./mcp.js";
import {
  RelayError,
  type Checkpoint,
  type Handoff,
  type Project,
  type Session,
} from "./model.js";

const help = `Context Relay ${VERSION}
Review and carry working context between coding sessions.

Usage: relay <command> [options]
  init                          Start a private local workspace
  serve [--port 0]              Run the loopback service in the foreground
  status | stop | doctor        Inspect, stop or diagnose this workspace
  project add --root PATH       Register a local project
  project list                  List registered projects
  connect --project ID --harness claude [--source PATH] [--capture-only]
  connections [--project ID]    List project connections
  discover --id CONNECTION      List matching native sessions
  sync --id CONNECTION          Capture sessions; wait for job results
  disconnect --id CONNECTION    Remove only the managed MCP entry
  sessions [--project ID]        List captured sessions
  import --file session.json    Import normalized source events
  checkpoint create --file FILE Create a reviewed, sourced checkpoint
  checkpoint show --id ID       Inspect a checkpoint
  checkpoint packet --id ID     Compile a bounded continuation packet
  handoff offer --id ID --recipient codex
  handoff show|accept|cancel --id ID [--recipient codex]
  mcp --project ID --harness codex
  demo                          Create an explicitly synthetic local example

Options: --data-dir PATH, --root PATH, --name NAME, --project ID,
         --id ID, --file FILE, --recipient NAME, --harness NAME,
         --max-bytes 24000, --port 0, --help, --version
The npm runtime uses Node 22.16+; it does not require Python.
`;
async function main(): Promise<void> {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      "data-dir": { type: "string" },
      source: { type: "string" },
      executable: { type: "string" },
      "capture-only": { type: "boolean" },
      root: { type: "string" },
      name: { type: "string" },
      project: { type: "string" },
      id: { type: "string" },
      file: { type: "string" },
      recipient: { type: "string" },
      harness: { type: "string" },
      "max-bytes": { type: "string" },
      port: { type: "string" },
      help: { type: "boolean" },
      version: { type: "boolean" },
    },
  });
  const [command, action] = positionals;
  const directory = dataDirectory(values["data-dir"]);
  const print = (value: unknown) => console.log(JSON.stringify(value, null, 2));
  const need = (name: keyof typeof values): string => {
    const value = values[name];
    if (typeof value !== "string" || !value)
      throw new RelayError("missing_option", `--${name} is required.`);
    return value;
  };
  const input = () => {
    const path = resolve(need("file"));
    const text = readFileSync(path, "utf8");
    if (Buffer.byteLength(text) > 4 * 1024 * 1024)
      throw new RelayError(
        "file_too_large",
        "Input exceeds 4 MiB. Import in smaller batches.",
      );
    return JSON.parse(text) as unknown;
  };
  if (values.version) {
    console.log(VERSION);
    return;
  }
  if (values.help || !command || command === "help") {
    console.log(help);
    return;
  }
  if (command === "serve") {
    const port = Number(values.port ?? 0);
    if (!Number.isInteger(port) || port < 0 || port > 65535)
      throw new RelayError(
        "invalid_port",
        "Port must be an integer from 0 through 65535.",
      );
    const service = await startService(directory, port);
    print({ ...service.connection, dataDirectory: directory });
    const shutdown = () => {
      void service.close();
    };
    process.once("SIGINT", shutdown);
    process.once("SIGTERM", shutdown);
    await service.closed;
    return;
  }
  if (command === "doctor") {
    const recovered = recoverDeadLock(directory);
    let service: unknown = null;
    try {
      service = (await connect(directory)).connection;
    } catch {
      /* Doctor works while stopped. */
    }
    print({
      version: VERSION,
      node: process.version,
      dataDirectory: directory,
      service,
      recoveredDeadLock: recovered,
      lockPresent: existsSync(join(directory, "service.lock")),
    });
    return;
  }
  if (command === "status" || command === "stop") {
    const client = await connect(directory);
    print(
      command === "status"
        ? await client.request("/api/health")
        : await client.request("/api/shutdown", {}),
    );
    return;
  }
  const supported = [
    "connect",
    "connections",
    "discover",
    "sync",
    "disconnect",
    "init",
    "project",
    "sessions",
    "import",
    "checkpoint",
    "handoff",
    "mcp",
    "demo",
  ];
  if (!supported.includes(command))
    throw new RelayError(
      "unknown_command",
      `Unknown command: ${command}. Run relay --help.`,
    );
  const client = await ensureService(directory);
  if (command === "connect") {
    print(
      await client.request("/api/connections", {
        projectId: need("project"),
        harness: need("harness"),
        configureMcp: !values["capture-only"],
        ...(values.source ? { source: values.source } : {}),
        ...(values.executable ? { executable: values.executable } : {}),
      }),
    );
    return;
  }
  if (command === "connections") {
    print(
      await client.request(
        "/api/connections" +
          (values.project
            ? "?projectId=" + encodeURIComponent(values.project)
            : ""),
      ),
    );
    return;
  }
  if (command === "discover" || command === "disconnect") {
    print(
      await client.request(
        "/api/connections/" + encodeURIComponent(need("id")) + "/" + command,
        command === "disconnect" ? {} : undefined,
      ),
    );
    return;
  }
  if (command === "sync") {
    let job = await client.request<import("./adapters/manager.js").CaptureJob>(
      "/api/connections/" + encodeURIComponent(need("id")) + "/sync",
      {},
    );
    while (job.state === "running") {
      await new Promise((resolve) => setTimeout(resolve, 300));
      job = await client.request("/api/jobs/" + job.id);
    }
    print(job);
    if (job.state !== "completed" || job.errors.length) process.exitCode = 1;
    return;
  }
  if (command === "init") {
    print({
      ...client.connection,
      dataDirectory: directory,
      next: "relay project add --root /path/to/project",
    });
    return;
  }
  if (command === "project" && action === "add") {
    print(
      await client.request("/api/projects", {
        root: need("root"),
        ...(values.name ? { name: values.name } : {}),
      }),
    );
    return;
  }
  if (command === "project" && action === "list") {
    print(await client.request("/api/projects"));
    return;
  }
  if (command === "sessions") {
    print(
      await client.request(
        "/api/sessions" +
          (values.project
            ? "?projectId=" + encodeURIComponent(values.project)
            : ""),
      ),
    );
    return;
  }
  if (command === "import") {
    print(await client.request("/api/sessions/import", input()));
    return;
  }
  if (command === "checkpoint") {
    if (action === "create") {
      print(await client.request("/api/checkpoints", input()));
      return;
    }
    if (action === "show") {
      print(
        await client.request(
          "/api/checkpoints/" + encodeURIComponent(need("id")),
        ),
      );
      return;
    }
    if (action === "packet") {
      print(
        await client.request(
          "/api/checkpoints/" +
            encodeURIComponent(need("id")) +
            "/packet?maxBytes=" +
            encodeURIComponent(values["max-bytes"] ?? "24000"),
        ),
      );
      return;
    }
  }
  if (command === "handoff") {
    const handoffId = need("id");
    if (action === "offer") {
      print(
        await client.request("/api/handoffs", {
          checkpointId: handoffId,
          recipient: need("recipient"),
          maxBytes: Number(values["max-bytes"] ?? 24000),
        }),
      );
      return;
    }
    if (action === "show") {
      print(
        await client.request("/api/handoffs/" + encodeURIComponent(handoffId)),
      );
      return;
    }
    if (action === "accept") {
      print(
        await client.request(
          "/api/handoffs/" + encodeURIComponent(handoffId) + "/accept",
          { recipient: need("recipient") },
        ),
      );
      return;
    }
    if (action === "cancel") {
      print(
        await client.request(
          "/api/handoffs/" + encodeURIComponent(handoffId) + "/cancel",
          {},
        ),
      );
      return;
    }
  }
  if (command === "mcp") {
    await client.request("/api/projects").then((projects) => {
      if (!(projects as Project[]).some((p) => p.id === need("project")))
        throw new RelayError("not_found", "Register the MCP project first.");
    });
    await serveMcp(client, need("harness"), need("project"));
    return;
  }
  if (command === "demo") {
    const root = join(directory, "demo-project");
    mkdirSync(root, { recursive: true });
    const project = await client.request<Project>("/api/projects", {
      root,
      name: "Example · webhook recovery",
    });
    const imported = await client.request<{ session: Session }>(
      "/api/sessions/import",
      {
        projectId: project.id,
        harness: "claude",
        externalId: "relay-synthetic-demo",
        title: "Example · duplicate webhook investigation",
        events: [
          {
            id: "user-1",
            role: "user",
            text: "Prevent duplicate webhook charges. Keep idempotency keys for 24 hours.",
          },
          {
            id: "user-2",
            role: "user",
            text: "Correction: retain idempotency keys for 7 days.",
          },
          {
            id: "tool-3",
            role: "tool",
            kind: "tool_result",
            text: "Synthetic fixture: duplicate-delivery test is failing; retry implementation remains open.",
          },
        ],
      },
    );
    const session = imported.session;
    const checkpoint = await client.request<Checkpoint>("/api/checkpoints", {
      projectId: project.id,
      title: "Example · resume webhook recovery",
      goal: "Prevent duplicate webhook charges.",
      sources: [{ sessionId: session.id, revision: session.revision }],
      instructions: [
        {
          key: "retention",
          text: "Retain idempotency keys for 7 days.",
          sources: [{ sessionId: session.id, eventId: "user-2" }],
        },
      ],
      criteria: ["Duplicate-delivery checks pass."],
      openWork: ["Implement retry handling.", "Run the webhook tests."],
      evidence: [{ sessionId: session.id, eventId: "tool-3" }],
    });
    const handoff = await client.request<Handoff>("/api/handoffs", {
      checkpointId: checkpoint.id,
      recipient: "codex",
    });
    print({
      synthetic: true,
      projectId: project.id,
      sessionId: session.id,
      checkpointId: checkpoint.id,
      handoffId: handoff.id,
      status: handoff.status,
      note: "No model calls or real task verification. Use handoff accept or the MCP tools to inspect the receipt.",
    });
    return;
  }
  throw new RelayError(
    "unknown_command",
    "Unknown command or action. Run relay --help.",
  );
}
main().catch((error) => {
  console.error(
    JSON.stringify({
      error: {
        code: error instanceof RelayError ? error.code : "failed",
        message: error instanceof Error ? error.message : "Operation failed.",
      },
    }),
  );
  process.exitCode = 1;
});
