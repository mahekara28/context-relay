import {
  accessSync,
  constants,
  existsSync,
  lstatSync,
  realpathSync,
  statSync,
  openSync,
  readSync,
  closeSync,
} from "node:fs";
import { homedir } from "node:os";
import { basename, delimiter, join, relative, resolve, sep } from "node:path";
import { type Project, RelayError } from "../model.js";
import { digest } from "../util.js";
import { lines, sourceFiles, codex, goose, openCodeGet } from "./capture.js";
import { object, string, array, visibleText } from "./normalize.js";
import type { HarnessConnection } from "./types.js";

export type NativeHarness = HarnessConnection["harness"];
export interface LocalSession {
  harness: NativeHarness;
  externalId: string;
  title: string;
  root: string;
  locator: string;
  fingerprint: string;
  updatedAt: string | null;
  source?: string;
  executable?: string;
}
export interface LocalSource {
  harness: NativeHarness;
  state: "available" | "unavailable" | "error" | "paused";
  message: string;
  sessions: LocalSession[];
}
export interface SourceOptions {
  source?: string;
  executable?: string;
}
export function executable(name: string): string | undefined {
  const directories = [
    ...(process.env.PATH ?? "").split(delimiter),
    join(homedir(), ".local/bin"),
    join(homedir(), ".bun/bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
  ];
  if (name === "codex" && process.platform === "darwin")
    directories.push("/Applications/Codex.app/Contents/Resources");
  for (const dir of directories.filter(Boolean)) {
    const path = join(dir, name + (process.platform === "win32" ? ".exe" : ""));
    try {
      accessSync(path, constants.X_OK);
      if (statSync(path).isFile()) return path;
    } catch {
      /* Next known executable location. */
    }
  }
  return undefined;
}
const claudeCache = new Map<
  string,
  { fingerprint: string; session: LocalSession | null }
>();
function rootOf(value: unknown): string | null {
  try {
    if (typeof value !== "string" || !value) return null;
    const path = realpathSync(value);
    return statSync(path).isDirectory() ? path : null;
  } catch {
    return null;
  }
}
function time(value: unknown): string | null {
  const date =
    typeof value === "number"
      ? new Date(value < 1e12 ? value * 1000 : value)
      : typeof value === "string"
        ? new Date(value)
        : null;
  return date && Number.isFinite(date.getTime()) ? date.toISOString() : null;
}
export async function discoverLocal(
  harness: NativeHarness,
  options: SourceOptions = {},
): Promise<LocalSource> {
  const sessions: LocalSession[] = [];
  const available = (message: string): LocalSource => ({
    harness,
    state: "available",
    message,
    sessions,
  });
  const unavailable = (message: string): LocalSource => ({
    harness,
    state: "unavailable",
    message,
    sessions,
  });
  if (harness === "claude") {
    const source = resolve(
      options.source ??
        join(
          process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), ".claude"),
          "projects",
        ),
    );
    if (!existsSync(source))
      return unavailable("No Claude Code transcripts found.");
    let skipped = 0;
    const files = sourceFiles(source);
    for (const [index, path] of files.entries()) {
      if (index % 8 === 0)
        await new Promise<void>((resolve) => setImmediate(resolve));
      try {
        if (!lstatSync(path).isFile()) continue;
        const info = statSync(path);
        const fingerprint = `${info.size}:${info.mtimeMs}`;
        let cached = claudeCache.get(path);
        if (!cached || cached.fingerprint !== fingerprint) {
          const fd = openSync(path, "r"),
            buffer = Buffer.alloc(Math.min(info.size, 512 * 1024));
          try {
            readSync(fd, buffer, 0, buffer.length, 0);
          } finally {
            closeSync(fd);
          }
          const chunks = buffer.toString("utf8").split("\n");
          chunks.pop();
          const records = chunks
            .filter(Boolean)
            .map((line) => JSON.parse(line) as unknown);
          const metadata = records
            .map(object)
            .find((r) => typeof r.cwd === "string");
          const root = rootOf(metadata?.cwd);
          let session: LocalSession | null = null;
          if (root && metadata) {
            const nativeId = string(metadata.sessionId);
            session = {
              harness,
              root,
              source,
              locator: path,
              fingerprint,
              externalId:
                nativeId && basename(path) === nativeId + ".jsonl"
                  ? nativeId
                  : relative(source, path).split(sep).join("/"),
              title:
                visibleText(
                  object(
                    records.map(object).find((r) => r.type === "user")?.message,
                  ).content,
                ).slice(0, 120) || "Claude Code session",
              updatedAt: info.mtime.toISOString(),
            };
          }
          if (!session) skipped++;
          cached = { fingerprint, session };
          claudeCache.set(path, cached);
        }
        if (cached.session) sessions.push(cached.session);
      } catch {
        skipped++;
      }
    }
    for (const path of claudeCache.keys())
      if (!existsSync(path)) claudeCache.delete(path);
    return available(
      skipped
        ? `${skipped} unreadable, oversized or incomplete transcript(s) skipped.`
        : "Reading local transcripts.",
    );
  }
  const bin = options.executable ?? executable(harness);
  if (!bin && harness !== "opencode")
    return unavailable(
      `${harness === "codex" ? "Codex" : "Goose"} executable was not found.`,
    );
  const project: Project = {
    id: "discovery",
    name: "Local discovery",
    root: process.cwd(),
    createdAt: "",
  };
  const connection: HarnessConnection = {
    id: "discovery",
    projectId: project.id,
    harness,
    capture: true,
    configureMcp: false,
    updatedAt: "",
    configPath: null,
    ...(bin ? { executable: bin } : {}),
    ...options,
  };
  const add = (
    raw: unknown,
    rootValue: unknown,
    title: string,
    updated: unknown,
  ) => {
    const row = object(raw),
      root = rootOf(rootValue),
      externalId = string(row.id);
    if (root && externalId)
      sessions.push({
        harness,
        root,
        externalId,
        locator: externalId,
        title: title.slice(0, 120) || `${harness} session`,
        fingerprint: digest(raw),
        updatedAt: time(updated),
        ...(bin ? { executable: bin } : {}),
        ...options,
      });
  };
  if (harness === "codex") {
    await codex(connection, project, async (rpc) => {
      let cursor: string | null = null;
      for (let page = 0; page < 20; page++) {
        const result: { data: unknown[]; nextCursor?: string | null } =
          await rpc.request("thread/list", {
            limit: 100,
            cursor,
            sourceKinds: [],
          });
        if (!Array.isArray(result.data))
          throw new RelayError(
            "unsupported_format",
            "Codex returned an unsupported session list.",
          );
        for (const raw of result.data) {
          const row = object(raw);
          add(
            raw,
            row.cwd,
            string(row.name) || string(row.preview),
            row.updatedAt,
          );
        }
        cursor = result.nextCursor ?? null;
        if (!cursor) return;
      }
      throw new RelayError(
        "source_limit",
        "Codex discovery exceeds 2,000 sessions.",
      );
    });
    return available("Reading Codex app-server history.");
  }
  if (harness === "opencode") {
    const projects = await openCodeGet(connection, project, "/project");
    if (!Array.isArray(projects))
      throw new RelayError(
        "unsupported_format",
        "OpenCode returned an unsupported project list.",
      );
    const roots = [
      ...new Set(
        projects
          .map((raw) => rootOf(object(raw).worktree))
          .filter((root): root is string => !!root),
      ),
    ];
    for (const root of roots.slice(0, 100)) {
      const payload = await openCodeGet(
        connection,
        { ...project, root },
        "/session",
      );
      if (!Array.isArray(payload))
        throw new RelayError(
          "unsupported_format",
          "OpenCode returned an unsupported session list.",
        );
      for (const raw of payload) {
        const row = object(raw);
        add(raw, row.directory, string(row.title), object(row.time).updated);
      }
    }
    return available("Connected to the local OpenCode server.");
  }
  const payload = await goose(connection, project, [
    "session",
    "list",
    "--format",
    "json",
    "--limit",
    "200",
  ]);
  for (const raw of Array.isArray(payload)
    ? payload
    : array(object(payload).sessions)) {
    const row = object(raw);
    add(
      raw,
      row.working_dir ?? row.workingDir,
      string(row.name) || string(row.description),
      row.updated_at ?? row.updatedAt,
    );
  }
  return available(
    "Reading Goose session history (up to 200 recent sessions).",
  );
}
