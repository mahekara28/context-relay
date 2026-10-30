import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  existsSync,
  lstatSync,
  readFileSync,
  readdirSync,
  realpathSync,
  statSync,
} from "node:fs";
import { homedir } from "node:os";
import { basename, join, relative, resolve, sep } from "node:path";
import { RelayError, type Project, type SessionImport } from "../model.js";
import { type HarnessConnection, type DiscoveredSession } from "./types.js";
import { JsonLineRpc } from "./rpc.js";
import {
  array,
  object,
  string,
  claudeEvents,
  codexEvents,
  openCodeEvents,
  gooseEvents,
} from "./normalize.js";

const execute = promisify(execFile);
function sameRoot(value: unknown, root: string): boolean {
  try {
    return typeof value === "string" && realpathSync(value) === root;
  } catch {
    return false;
  }
}
function lines(path: string): unknown[] {
  if (
    lstatSync(path).isSymbolicLink() ||
    statSync(path).size > 16 * 1024 * 1024
  )
    throw new RelayError(
      "source_limit",
      "Transcript must be a regular file at most 16 MiB.",
    );
  const text = readFileSync(path, "utf8");
  const chunks = text.split("\n");
  // A writer may have left an incomplete final line; never import it early.
  if (chunks.at(-1) !== "") chunks.pop();
  return chunks.filter(Boolean).map((line) => {
    try {
      return JSON.parse(line);
    } catch {
      throw new RelayError(
        "malformed_transcript",
        "Transcript contains an invalid complete JSONL record.",
      );
    }
  });
}
function sourceFiles(directory: string): string[] {
  if (!existsSync(directory)) return [];
  if (lstatSync(directory).isSymbolicLink())
    throw new RelayError("unsafe_source", "Source root cannot be a symlink.");
  const paths: string[] = [];
  let visited = 0;
  const walk = (path: string, depth: number) => {
    if (depth > 2) return;
    for (const entry of readdirSync(path, { withFileTypes: true })) {
      if (++visited > 10000)
        throw new RelayError(
          "source_limit",
          "Source directory is too large. Choose a narrower source root.",
        );
      const child = join(path, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) walk(child, depth + 1);
      else if (entry.isFile() && entry.name.endsWith(".jsonl"))
        paths.push(child);
    }
  };
  walk(directory, 0);
  return paths;
}
async function codex<T>(
  connection: HarnessConnection,
  project: Project,
  operation: (rpc: JsonLineRpc) => Promise<T>,
): Promise<T> {
  const rpc = new JsonLineRpc(
    connection.executable ?? "codex",
    ["app-server", "--listen", "stdio://"],
    project.root,
  );
  try {
    await rpc.request("initialize", {
      clientInfo: {
        name: "context_relay",
        title: "Context Relay capture",
        version: "0.2.0-alpha.1",
      },
      capabilities: {},
    });
    rpc.notify("initialized");
    return await operation(rpc);
  } finally {
    rpc.close();
  }
}
function localEndpoint(source?: string): URL {
  const url = new URL(source ?? "http://127.0.0.1:4096");
  if (
    url.protocol !== "http:" ||
    !["127.0.0.1", "[::1]", "localhost"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw new RelayError(
      "invalid_endpoint",
      "OpenCode source must be a loopback HTTP origin without credentials.",
    );
  return url;
}
async function openCodeGet(
  connection: HarnessConnection,
  project: Project,
  path: string,
): Promise<unknown> {
  const base = localEndpoint(connection.source);
  const url = new URL(path, base);
  url.searchParams.set("directory", project.root);
  const password = process.env.OPENCODE_SERVER_PASSWORD;
  const username = process.env.OPENCODE_SERVER_USERNAME ?? "opencode";
  const response = await fetch(url, {
    headers: password
      ? {
          Authorization:
            "Basic " +
            Buffer.from(`${username}:${password}`).toString("base64"),
        }
      : {},
    signal: AbortSignal.timeout(10000),
    redirect: "error",
  });
  if (!response.ok)
    throw new RelayError(
      "harness_http",
      `OpenCode returned HTTP ${response.status}. Check the endpoint and service credentials.`,
    );
  const reader = response.body?.getReader();
  if (!reader)
    throw new RelayError(
      "harness_protocol",
      "OpenCode returned no response body.",
    );
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    bytes += chunk.value.byteLength;
    if (bytes > 16 * 1024 * 1024) {
      await reader.cancel();
      throw new RelayError(
        "harness_response_too_large",
        "OpenCode response exceeds 16 MiB.",
      );
    }
    chunks.push(chunk.value);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new RelayError("harness_protocol", "OpenCode returned invalid JSON.");
  }
}
async function goose(
  connection: HarnessConnection,
  project: Project,
  args: string[],
): Promise<unknown> {
  try {
    const { stdout } = await execute(connection.executable ?? "goose", args, {
      cwd: project.root,
      timeout: 15000,
      maxBuffer: 16 * 1024 * 1024,
      windowsHide: true,
    });
    return JSON.parse(stdout);
  } catch {
    throw new RelayError(
      "harness_unavailable",
      "Goose capture failed. Check the executable path and JSON export support.",
    );
  }
}
export async function discover(
  connection: HarnessConnection,
  project: Project,
): Promise<DiscoveredSession[]> {
  if (!connection.capture) return [];
  const make = (
    externalId: string,
    title: string,
    locator = externalId,
  ): DiscoveredSession => ({
    externalId,
    title: title.slice(0, 300) || "Untitled session",
    locator,
    harness: connection.harness,
    projectId: project.id,
  });
  if (connection.harness === "claude") {
    const directory = resolve(
      connection.source ?? join(homedir(), ".claude", "projects"),
    );
    const found: DiscoveredSession[] = [];
    for (const path of sourceFiles(directory)) {
      const records = lines(path);
      const metadata = records
        .map(object)
        .find((r) => sameRoot(r.cwd, project.root));
      if (!metadata) continue;
      const nativeId = string(metadata.sessionId);
      const sessionId =
        nativeId && basename(path) === nativeId + ".jsonl"
          ? nativeId
          : relative(directory, path).split(sep).join("/");
      const firstUser = claudeEvents(records).find(
        (event) => event.role === "user",
      );
      found.push(
        make(
          sessionId,
          firstUser?.text.slice(0, 120) ?? "Claude Code session",
          path,
        ),
      );
    }
    return found;
  }
  if (connection.harness === "codex")
    return codex(connection, project, async (rpc) => {
      const found: DiscoveredSession[] = [];
      let cursor: string | null = null;
      for (let page = 0; page < 20; page++) {
        const result: { data: unknown[]; nextCursor?: string | null } =
          await rpc.request("thread/list", {
            cwd: project.root,
            limit: 100,
            cursor,
          });
        for (const raw of result.data) {
          const thread = object(raw);
          if (!sameRoot(thread.cwd, project.root)) continue;
          if (string(thread.id))
            found.push(
              make(
                string(thread.id),
                string(thread.name) || string(thread.preview),
              ),
            );
        }
        cursor = result.nextCursor ?? null;
        if (!cursor) return found;
      }
      throw new RelayError(
        "source_limit",
        "More than 2,000 threads match. Narrow the source project.",
      );
    });
  if (connection.harness === "opencode") {
    const payload = await openCodeGet(connection, project, "/session");
    if (!Array.isArray(payload))
      throw new RelayError(
        "unsupported_format",
        "OpenCode session API response changed.",
      );
    return payload
      .map(object)
      .filter((s) => sameRoot(s.directory, project.root) && string(s.id))
      .map((s) => make(string(s.id), string(s.title)));
  }
  const payload = await goose(connection, project, [
    "session",
    "list",
    "--format",
    "json",
    "--working_dir",
    project.root,
    "--limit",
    "200",
  ]);
  const sessions = Array.isArray(payload)
    ? payload
    : array(object(payload).sessions);
  return sessions
    .map(object)
    .filter(
      (s) =>
        sameRoot(s.working_dir ?? s.workingDir, project.root) && string(s.id),
    )
    .map((s) => make(string(s.id), string(s.name) || string(s.description)));
}
export async function capture(
  connection: HarnessConnection,
  project: Project,
  session: DiscoveredSession,
): Promise<SessionImport> {
  let events: SessionImport["events"];
  if (connection.harness === "claude") {
    const records = lines(session.locator);
    if (!records.map(object).some((r) => sameRoot(r.cwd, project.root)))
      throw new RelayError(
        "project_mismatch",
        "Transcript no longer matches the selected project.",
      );
    events = claudeEvents(records);
  } else if (connection.harness === "codex")
    events = await codex(connection, project, async (rpc) => {
      const result = await rpc.request<{ thread: { cwd?: string } }>(
        "thread/read",
        { threadId: session.externalId },
      );
      if (!sameRoot(result.thread.cwd, project.root))
        throw new RelayError(
          "project_mismatch",
          "Codex thread belongs to another project.",
        );
      const turns: unknown[] = [];
      let cursor: string | null = null;
      for (let page = 0; page < 100; page++) {
        const part: { data: unknown[]; nextCursor?: string | null } =
          await rpc.request("thread/turns/list", {
            threadId: session.externalId,
            limit: 100,
            cursor,
            sortDirection: "asc",
            itemsView: "full",
          });
        turns.push(...part.data);
        cursor = part.nextCursor ?? null;
        if (!cursor) return codexEvents(turns);
      }
      throw new RelayError(
        "source_limit",
        "Thread exceeds 10,000 turns. Capture a smaller source export.",
      );
    });
  else if (connection.harness === "opencode") {
    const info = object(
      await openCodeGet(
        connection,
        project,
        "/session/" + encodeURIComponent(session.externalId),
      ),
    );
    if (!sameRoot(info.directory, project.root))
      throw new RelayError(
        "project_mismatch",
        "OpenCode session belongs to another project.",
      );
    const messages = await openCodeGet(
      connection,
      project,
      "/session/" + encodeURIComponent(session.externalId) + "/message",
    );
    if (!Array.isArray(messages))
      throw new RelayError(
        "unsupported_format",
        "OpenCode messages API response changed.",
      );
    events = openCodeEvents(messages);
  } else {
    const exported = await goose(connection, project, [
      "session",
      "export",
      "--session-id",
      session.externalId,
      "--format",
      "json",
    ]);
    const metadata = object(exported);
    if (!sameRoot(metadata.working_dir ?? metadata.workingDir, project.root))
      throw new RelayError(
        "project_mismatch",
        "Goose export belongs to another project or has no working directory.",
      );
    events = gooseEvents(exported);
  }
  return {
    projectId: project.id,
    harness: connection.harness,
    externalId: session.externalId,
    title: session.title,
    events,
  };
}
