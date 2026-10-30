import { createHash, randomUUID } from "node:crypto";
import {
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  statSync,
} from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { execFileSync } from "node:child_process";
import { RelayError, type Artifact } from "./model.js";

export const id = () => randomUUID();
export const now = () => new Date().toISOString();
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value !== null && typeof value === "object")
    return (
      "{" +
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([k, v]) => JSON.stringify(k) + ":" + canonical(v))
        .join(",") +
      "}"
    );
  return JSON.stringify(value);
}
export function digest(value: unknown): string {
  return (
    "sha256:" + createHash("sha256").update(canonical(value)).digest("hex")
  );
}
export function dataDirectory(explicit?: string): string {
  return resolve(
    explicit ??
      process.env.CONTEXT_RELAY_HOME ??
      join(homedir(), ".context-relay"),
  );
}
export function privateDirectory(path: string): void {
  if (
    existsSync(path) &&
    (lstatSync(path).isSymbolicLink() || !statSync(path).isDirectory())
  )
    throw new RelayError(
      "unsafe_path",
      "Data directory must be a real directory.",
    );
  mkdirSync(path, { recursive: true, mode: 0o700 });
}
export function realDirectory(path: string): string {
  const root = realpathSync(resolve(path));
  if (!statSync(root).isDirectory())
    throw new RelayError(
      "invalid_project",
      "Project root must be a directory.",
    );
  return root;
}
export function projectFile(root: string, file: string): string {
  if (isAbsolute(file))
    throw new RelayError(
      "outside_project",
      "Use a project-relative file path.",
    );
  const target = realpathSync(resolve(root, file));
  const rel = relative(root, target);
  if (rel === ".." || rel.startsWith(".." + sep) || isAbsolute(rel))
    throw new RelayError(
      "outside_project",
      "File escapes the selected project.",
    );
  return target;
}
export function artifact(root: string, file: string): Artifact {
  const target = projectFile(root, file);
  const info = statSync(target);
  if (!info.isFile() || info.size > 10 * 1024 * 1024)
    throw new RelayError(
      "invalid_artifact",
      "Artifacts must be files no larger than 10 MiB.",
    );
  const content = readFileSync(target);
  return {
    path: relative(root, target).split(sep).join("/"),
    sha256: "sha256:" + createHash("sha256").update(content).digest("hex"),
    bytes: content.length,
  };
}
export function gitState(root: string): {
  head: string | null;
  branch: string | null;
} {
  const run = (args: string[]) => {
    try {
      return (
        execFileSync("git", ["-C", root, ...args], {
          encoding: "utf8",
          stdio: ["ignore", "pipe", "ignore"],
          timeout: 3000,
        }).trim() || null
      );
    } catch {
      return null;
    }
  };
  return {
    head: run(["rev-parse", "--verify", "HEAD"]),
    branch: run(["branch", "--show-current"]),
  };
}
/** Best-effort redaction before persistence. Does not guarantee removal of every secret. */
export function redact(text: string): string {
  return text
    .replace(
      /\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,})\b/g,
      "[REDACTED]",
    )
    .replace(
      /(\b(?:authorization\s*:\s*bearer|api[_-]?key\s*[:=]|token\s*[:=]|password\s*[:=])\s*)["']?[^\s"',;]+/gi,
      "$1[REDACTED]",
    );
}
