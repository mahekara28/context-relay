import { spawn } from "node:child_process";
import {
  closeSync,
  existsSync,
  openSync,
  lstatSync,
  readFileSync,
  unlinkSync,
} from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { RelayError } from "./model.js";
import { secret, VERSION, type Connection } from "./server.js";
import { privateDirectory } from "./util.js";

export class RelayClient {
  constructor(
    readonly connection: Connection,
    readonly token: string,
  ) {
    const url = new URL(connection.url);
    if (
      url.protocol !== "http:" ||
      url.hostname !== "127.0.0.1" ||
      url.pathname !== "/" ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      throw new RelayError(
        "invalid_connection",
        "Local service descriptor has an invalid URL.",
      );
  }
  async request<T = unknown>(path: string, body?: unknown): Promise<T> {
    if (!path.startsWith("/api/"))
      throw new RelayError("invalid_path", "Relay API path required.");
    const response = await fetch(this.connection.url + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(15000),
      redirect: "error",
    });
    const data = (await response.json()) as {
      error?: { code: string; message: string };
    };
    if (!response.ok)
      throw new RelayError(
        data.error?.code ?? "service_error",
        data.error?.message ?? "Local service request failed.",
        response.status,
      );
    return data as T;
  }
}
export async function connect(directory: string): Promise<RelayClient> {
  const connection = JSON.parse(
    readFileSync(join(directory, "connection.json"), "utf8"),
  ) as Connection;
  const client = new RelayClient(connection, secret(directory));
  const health = await client.request<{ instance: string; name: string }>(
    "/api/health",
  );
  if (
    health.instance !== connection.instance ||
    health.name !== "context-relay"
  )
    throw new RelayError(
      "wrong_service",
      "The local service identity does not match.",
    );
  return client;
}
/** Only remove a dead process's lock. Never signal or kill an existing process. */
export function recoverDeadLock(directory: string): boolean {
  if (!existsSync(directory)) return false;
  const recovery = join(directory, "recovery.lock");
  let fd: number;
  try {
    fd = openSync(recovery, "wx", 0o600);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") return false;
    throw error;
  }
  try {
    return recoverLocked(directory);
  } finally {
    closeSync(fd);
    unlinkSync(recovery);
  }
}
function recoverLocked(directory: string): boolean {
  const path = join(directory, "service.lock");
  if (!existsSync(path)) return false;
  let lock: { pid?: number };
  try {
    lock = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new RelayError(
      "invalid_lock",
      "Service lock is unreadable; inspect it before recovery.",
    );
  }
  if (!Number.isSafeInteger(lock.pid) || lock.pid! <= 0)
    throw new RelayError(
      "invalid_lock",
      "Service lock has an invalid process ID.",
    );
  try {
    process.kill(lock.pid!, 0);
    return false;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ESRCH") return false;
  }
  unlinkSync(path);
  const connection = join(directory, "connection.json");
  if (existsSync(connection)) unlinkSync(connection);
  return true;
}
export async function ensureService(directory: string): Promise<RelayClient> {
  let existing: RelayClient | undefined;
  try {
    existing = await connect(directory);
  } catch {
    /* Start only this profile's service. */
  }
  if (existing) {
    if (existing.connection.version !== VERSION)
      throw new RelayError(
        "service_version",
        `This profile is running Relay ${existing.connection.version}. Run relay stop with the same --data-dir, then relay ui to use ${VERSION}.`,
      );
    return existing;
  }
  privateDirectory(directory);
  recoverDeadLock(directory);
  const logPath = join(directory, "service.log");
  if (existsSync(logPath) && lstatSync(logPath).isSymbolicLink())
    throw new RelayError("unsafe_path", "Service log cannot be a symlink.");
  const log = openSync(logPath, "a", 0o600);
  const child = spawn(
    process.execPath,
    [
      fileURLToPath(new URL("./cli.js", import.meta.url)),
      "serve",
      "--data-dir",
      directory,
      "--port",
      "0",
    ],
    { detached: true, stdio: ["ignore", log, log], windowsHide: true },
  );
  child.on("error", () => {});
  child.unref();
  closeSync(log);
  for (let attempt = 0; attempt < 50; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    try {
      return await connect(directory);
    } catch {
      /* The descriptor is written after listen. */
    }
  }
  throw new RelayError(
    "startup_failed",
    "Local service did not start. Run relay doctor and inspect service.log in this data directory.",
  );
}
