import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { randomBytes, timingSafeEqual } from "node:crypto";
import {
  closeSync,
  existsSync,
  lstatSync,
  openSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { RelayError } from "./model.js";
import { RelayStore } from "./store.js";
import { id, privateDirectory } from "./util.js";

export const VERSION = "0.1.0-alpha.1";
export interface Connection {
  url: string;
  pid: number;
  instance: string;
  version: string;
}
export function secret(directory: string): string {
  privateDirectory(directory);
  const path = join(directory, "access-token");
  if (existsSync(path) && lstatSync(path).isSymbolicLink())
    throw new RelayError("unsafe_path", "Access token cannot be a symlink.");
  try {
    writeFileSync(path, randomBytes(32).toString("hex"), {
      flag: "wx",
      mode: 0o600,
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
  const token = readFileSync(path, "utf8").trim();
  if (!/^[a-f0-9]{64}$/.test(token))
    throw new RelayError("invalid_token", "Local access token is invalid.");
  return token;
}
export async function readBody(request: IncomingMessage): Promise<unknown> {
  if (
    !String(request.headers["content-type"] ?? "").startsWith(
      "application/json",
    )
  )
    throw new RelayError("content_type", "Use application/json.", 415);
  let bytes = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    bytes += chunk.length;
    if (bytes > 4 * 1024 * 1024)
      throw new RelayError("body_too_large", "Request exceeds 4 MiB.", 413);
    chunks.push(Buffer.from(chunk));
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new RelayError("invalid_json", "Request is not valid JSON.");
  }
}
export function json(
  response: ServerResponse,
  status: number,
  value: unknown,
): void {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(JSON.stringify(value));
}
export function errorResponse(response: ServerResponse, error: unknown): void {
  if (error instanceof RelayError)
    json(response, error.status, {
      error: { code: error.code, message: error.message },
    });
  else if (error instanceof z.ZodError)
    json(response, 400, {
      error: {
        code: "invalid_input",
        message: error.issues
          .map((i) => `${i.path.join(".") || "input"}: ${i.message}`)
          .join("; "),
      },
    });
  else if (
    error instanceof Error &&
    "code" in error &&
    ["ENOENT", "ENOTDIR", "EACCES"].includes(String(error.code))
  )
    json(response, 400, {
      error: {
        code: "file_unavailable",
        message: "The selected local file or directory is unavailable.",
      },
    });
  else {
    console.error(
      "Relay request failed:",
      error instanceof Error ? error.name : "unknown",
    );
    json(response, 500, {
      error: {
        code: "internal",
        message: "The operation failed. Check local service diagnostics.",
      },
    });
  }
}
export interface Service {
  connection: Connection;
  token: string;
  store: RelayStore;
  close: () => Promise<void>;
  closed: Promise<void>;
}
export async function startService(
  directory: string,
  port = 0,
): Promise<Service> {
  privateDirectory(directory);
  const instance = id();
  const lock = join(directory, "service.lock");
  const lockData = JSON.stringify({ pid: process.pid, instance });
  try {
    closeSync(openSync(lock, "wx", 0o600));
    writeFileSync(lock, lockData);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    throw new RelayError(
      "service_locked",
      "A service lock already exists. Run relay doctor; do not start two writers.",
    );
  }
  let store: RelayStore;
  const cleanupFiles = () => {
    if (existsSync(lock) && readFileSync(lock, "utf8") === lockData) {
      unlinkSync(lock);
      const descriptor = join(directory, "connection.json");
      if (existsSync(descriptor)) unlinkSync(descriptor);
    }
  };
  let token: string;
  try {
    token = secret(directory);
    store = new RelayStore(directory);
  } catch (error) {
    cleanupFiles();
    throw error;
  }
  let finish!: () => void;
  const closed = new Promise<void>((resolve) => {
    finish = resolve;
  });
  let url = "";
  let closing = false;
  const close = async () => {
    if (closing) return closed;
    closing = true;
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
    cleanupFiles();
    finish();
  };
  const server = createServer(async (request, response) => {
    try {
      const expectedHost = new URL(url).host;
      if (request.headers.host !== expectedHost)
        throw new RelayError(
          "invalid_host",
          "Use the loopback URL printed by Relay.",
          403,
        );
      if (request.headers.origin && request.headers.origin !== url)
        throw new RelayError(
          "invalid_origin",
          "Cross-origin requests are not allowed.",
          403,
        );
      const parsed = new URL(request.url ?? "/", url);
      const path = parsed.pathname;
      const method = request.method;
      if (path === "/" && method === "GET") {
        json(response, 200, {
          name: "Context Relay",
          version: VERSION,
          message: "Local context service. Use the relay CLI or MCP server.",
        });
        return;
      }
      const supplied = String(request.headers.authorization ?? "").replace(
        /^Bearer /,
        "",
      );
      if (
        Buffer.byteLength(supplied) !== Buffer.byteLength(token) ||
        !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))
      )
        throw new RelayError(
          "unauthorized",
          "A valid local Relay access token is required.",
          401,
        );
      if (method === "GET" && path === "/api/health") {
        json(response, 200, {
          name: "context-relay",
          version: VERSION,
          instance,
          pid: process.pid,
        });
        return;
      }
      if (path === "/api/projects") {
        if (method === "GET") {
          json(response, 200, store.projects());
          return;
        }
        if (method === "POST") {
          json(response, 201, store.addProject(await readBody(request)));
          return;
        }
      }
      if (path === "/api/sessions" && method === "GET") {
        json(
          response,
          200,
          store.sessions(parsed.searchParams.get("projectId") ?? undefined),
        );
        return;
      }
      if (path === "/api/sessions/import" && method === "POST") {
        json(response, 200, store.importSession(await readBody(request)));
        return;
      }
      let match = path.match(/^\/api\/sessions\/([^/]+)(?:\/(events))?$/);
      if (match && method === "GET") {
        json(
          response,
          200,
          match[2]
            ? store.events(
                match[1]!,
                Number(parsed.searchParams.get("offset") ?? 0),
                Number(parsed.searchParams.get("limit") ?? 200),
              )
            : store.session(match[1]!),
        );
        return;
      }
      if (path === "/api/checkpoints") {
        if (method === "GET") {
          json(
            response,
            200,
            store.checkpoints(
              parsed.searchParams.get("projectId") ?? undefined,
            ),
          );
          return;
        }
        if (method === "POST") {
          json(response, 201, store.createCheckpoint(await readBody(request)));
          return;
        }
      }
      match = path.match(
        /^\/api\/checkpoints\/([^/]+)(?:\/(packet|freshness))?$/,
      );
      if (match && method === "GET") {
        const checkpoint = store.checkpoint(match[1]!);
        json(
          response,
          200,
          match[2] === "packet"
            ? store.packet(
                checkpoint.id,
                Number(parsed.searchParams.get("maxBytes") ?? 24000),
              )
            : match[2] === "freshness"
              ? store.freshness(checkpoint)
              : checkpoint,
        );
        return;
      }
      if (path === "/api/handoffs") {
        if (method === "GET") {
          json(response, 200, store.handoffs());
          return;
        }
        if (method === "POST") {
          json(response, 201, store.offer(await readBody(request)));
          return;
        }
      }
      match = path.match(/^\/api\/handoffs\/([^/]+)(?:\/(accept|cancel))?$/);
      if (match) {
        if (method === "GET" && !match[2]) {
          json(response, 200, store.handoff(match[1]!));
          return;
        }
        if (method === "POST" && match[2] === "accept") {
          const body = z
            .object({ recipient: z.string().min(1).max(50) })
            .strict()
            .parse(await readBody(request));
          json(response, 200, store.accept(match[1]!, body.recipient));
          return;
        }
        if (method === "POST" && match[2] === "cancel") {
          json(response, 200, store.cancel(match[1]!));
          return;
        }
      }
      if (path === "/api/shutdown" && method === "POST") {
        json(response, 200, { stopping: true });
        setTimeout(() => {
          void close();
        }, 30);
        return;
      }
      throw new RelayError("not_found", "Route not found.", 404);
    } catch (error) {
      if (!response.headersSent) errorResponse(response, error);
      else response.end();
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  try {
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(port, "127.0.0.1", resolve);
    });
  } catch (error) {
    store.close();
    cleanupFiles();
    throw error;
  }
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("No loopback address.");
  url = `http://127.0.0.1:${address.port}`;
  const connection = { url, pid: process.pid, instance, version: VERSION };
  const descriptor = join(directory, "connection.json");
  if (existsSync(descriptor) && lstatSync(descriptor).isSymbolicLink()) {
    await close();
    throw new RelayError(
      "unsafe_path",
      "Connection descriptor cannot be a symlink.",
    );
  }
  writeFileSync(descriptor, JSON.stringify(connection), { mode: 0o600 });
  return { connection, token, store, close, closed };
}
