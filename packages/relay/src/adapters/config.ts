import {
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { RelayError, type Project } from "../model.js";
import { canonical, digest, id } from "../util.js";
import { type HarnessConnection } from "./types.js";
export interface ConfigReceipt {
  path: string;
  format: "json" | "toml";
  section: string;
  value: unknown;
}
const begin = "# BEGIN context-relay npm";
const end = "# END context-relay npm";
function read(path: string): string | null {
  if (existsSync(dirname(path)) && lstatSync(dirname(path)).isSymbolicLink())
    throw new RelayError(
      "unsafe_config",
      "Configuration directory cannot be a symlink.",
    );
  if (!existsSync(path)) return null;
  if (lstatSync(path).isSymbolicLink())
    throw new RelayError(
      "unsafe_config",
      "Configuration paths cannot be symlinks.",
    );
  return readFileSync(path, "utf8");
}
function replace(path: string, previous: string | null, next: string): void {
  mkdirSync(dirname(path), { recursive: true });
  if (read(path) !== previous)
    throw new RelayError(
      "config_changed",
      "Harness configuration changed during review. Retry after inspection.",
      409,
    );
  const temporary = path + ".relay-" + id();
  try {
    writeFileSync(temporary, next, { mode: 0o600, flag: "wx" });
    if (read(path) !== previous)
      throw new RelayError(
        "config_changed",
        "Harness configuration changed during write.",
        409,
      );
    renameSync(temporary, path);
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
}
export function mcpCommand(
  connection: HarnessConnection,
  directory: string,
): string[] {
  return [
    process.execPath,
    fileURLToPath(new URL("../cli.js", import.meta.url)),
    "mcp",
    "--data-dir",
    directory,
    "--project",
    connection.projectId,
    "--harness",
    connection.harness,
  ];
}
function jsonDocument(text: string | null): Record<string, unknown> {
  try {
    const value: unknown = text === null ? {} : JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value))
      throw new Error();
    return value as Record<string, unknown>;
  } catch {
    throw new RelayError(
      "invalid_config",
      "Existing harness configuration must be a JSON object.",
    );
  }
}
export function configureMcp(
  connection: HarnessConnection,
  project: Project,
  directory: string,
): ConfigReceipt | null {
  if (!connection.configureMcp) return null;
  const command = mcpCommand(connection, directory);
  if (connection.harness === "codex") {
    const path = join(project.root, ".codex", "config.toml");
    const previous = read(path);
    const text = previous ?? "";
    const block = `${begin}\n[mcp_servers.context_relay]\ncommand = ${JSON.stringify(command[0])}\nargs = ${JSON.stringify(command.slice(1))}\n${end}`;
    const start = text.indexOf(begin);
    const finish = text.indexOf(end);
    if (start >= 0 || finish >= 0) {
      if (
        start < 0 ||
        finish < start ||
        text.slice(start, finish + end.length) !== block
      )
        throw new RelayError(
          "config_conflict",
          "An existing Relay MCP block differs. Inspect it before reconnecting.",
          409,
        );
    } else {
      if (
        /\[mcp_servers\.(?:context_relay|"context_relay"|'context_relay')\]/.test(
          text,
        )
      )
        throw new RelayError(
          "config_conflict",
          "A context_relay MCP entry already exists outside the managed block.",
          409,
        );
      replace(
        path,
        previous,
        text + (text.endsWith("\n") || !text ? "" : "\n") + "\n" + block + "\n",
      );
    }
    return { path, format: "toml", section: "context_relay", value: block };
  }
  if (
    connection.harness === "opencode" &&
    existsSync(join(project.root, "opencode.jsonc"))
  )
    throw new RelayError(
      "config_conflict",
      "This project uses opencode.jsonc. Use capture-only and add the MCP command manually to preserve its comments and settings.",
      409,
    );
  const goose = connection.harness === "goose";
  const path = join(
    project.root,
    goose
      ? ".relay/goose-mcp.json"
      : connection.harness === "claude"
        ? ".mcp.json"
        : "opencode.json",
  );
  const section = connection.harness === "opencode" ? "mcp" : "mcpServers";
  const previous = read(path);
  const document = jsonDocument(previous);
  const entry =
    connection.harness === "opencode"
      ? { type: "local", command, enabled: true }
      : { type: "stdio", command: command[0], args: command.slice(1) };
  const existing = document[section] ?? {};
  if (!existing || typeof existing !== "object" || Array.isArray(existing))
    throw new RelayError(
      "invalid_config",
      "MCP configuration section must be an object.",
    );
  const servers = existing as Record<string, unknown>;
  if (
    servers.context_relay !== undefined &&
    digest(servers.context_relay) !== digest(entry)
  )
    throw new RelayError(
      "config_conflict",
      "An existing context_relay entry differs. Inspect it before reconnecting.",
      409,
    );
  servers.context_relay = entry;
  document[section] = servers;
  replace(path, previous, JSON.stringify(document, null, 2) + "\n");
  return { path, format: "json", section, value: entry };
}
export function removeMcp(receipt: ConfigReceipt): void {
  const previous = read(receipt.path);
  if (previous === null) return;
  if (receipt.format === "toml") {
    const start = previous.indexOf(begin);
    const finish = previous.indexOf(end);
    if (start < 0 && finish < 0) return;
    if (
      start < 0 ||
      finish < start ||
      previous.slice(start, finish + end.length) !== receipt.value
    )
      throw new RelayError(
        "config_changed",
        "Managed MCP block was edited; inspect it before removal.",
        409,
      );
    replace(
      receipt.path,
      previous,
      previous.slice(0, start) +
        previous.slice(finish + end.length).replace(/^\r?\n/, ""),
    );
    return;
  }
  const document = jsonDocument(previous);
  const section = document[receipt.section];
  if (!section || typeof section !== "object" || Array.isArray(section))
    throw new RelayError("config_changed", "MCP section changed.");
  const servers = section as Record<string, unknown>;
  if (servers.context_relay === undefined) return;
  if (canonical(servers.context_relay) !== canonical(receipt.value))
    throw new RelayError(
      "config_changed",
      "Managed MCP entry was edited; inspect it before removal.",
      409,
    );
  delete servers.context_relay;
  if (Object.keys(servers).length === 0) delete document[receipt.section];
  replace(receipt.path, previous, JSON.stringify(document, null, 2) + "\n");
}
