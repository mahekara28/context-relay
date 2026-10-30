import WebSocket from "ws";
import { homedir } from "node:os";
import { realpathSync } from "node:fs";
import { join, isAbsolute, resolve } from "node:path";
import { RelayError } from "../model.js";

export function sameDirectory(a: unknown, b: string): boolean {
  if (typeof a !== "string" || !isAbsolute(a)) return false;
  const canonical = (p: string) => {
    try {
      return realpathSync(p);
    } catch {
      return resolve(p);
    }
  };
  return canonical(a) === canonical(b);
}

/** Attach to a running host. Never start a second server over a live thread. */
export function codexEndpoint(input = "unix://"): string {
  if (input.startsWith("unix://")) {
    const path =
      input.slice(7) ||
      join(
        process.env.CODEX_HOME ?? join(homedir(), ".codex"),
        "app-server-control",
        "app-server-control.sock",
      );
    if (!isAbsolute(path) || /[\r\n]/.test(path))
      throw new RelayError(
        "invalid_endpoint",
        "Use an absolute local socket path.",
      );
    return "unix://" + path;
  }
  let url: URL;
  try {
    url = new URL(input);
  } catch {
    throw new RelayError(
      "invalid_endpoint",
      "Use a local ws:// origin or an absolute unix:// socket.",
    );
  }
  if (
    url.protocol !== "ws:" ||
    !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  )
    throw new RelayError(
      "invalid_endpoint",
      "Use a local Codex ws:// origin or unix:// socket.",
    );
  return url.origin;
}

export async function inspectCodex(
  endpoint: string,
  externalId: string,
  root: string,
): Promise<void> {
  const address = codexEndpoint(endpoint);
  const socket = address.startsWith("unix://")
    ? new WebSocket("ws+unix://" + address.slice(7) + ":/", {
        handshakeTimeout: 2500,
        maxPayload: 4 * 1024 * 1024,
      })
    : new WebSocket(address, {
        handshakeTimeout: 2500,
        maxPayload: 4 * 1024 * 1024,
      });
  const pending = new Map<
    number,
    { resolve: (value: any) => void; reject: (error: Error) => void }
  >();
  let serial = 0;
  const error = () =>
    new RelayError(
      "receiver_offline",
      "Open this thread in a Codex host connected to the selected app-server. Relay will wait for it.",
      409,
    );
  let timer: NodeJS.Timeout | undefined;
  try {
    await new Promise<void>((resolve, reject) => {
      socket.once("open", resolve);
      socket.once("error", () => reject(error()));
    });
    socket.on("error", () => {
      for (const p of pending.values()) p.reject(error());
    });
    socket.on("close", () => {
      for (const p of pending.values()) p.reject(error());
    });
    socket.on("message", (data) => {
      try {
        const message = JSON.parse(String(data));
        // Inspection never grants approvals or invokes a turn.
        if (message.method && message.id !== undefined) {
          socket.send(
            JSON.stringify({
              id: message.id,
              error: {
                code: -32601,
                message: "Relay inspection cannot authorize execution.",
              },
            }),
          );
          return;
        }
        const p = pending.get(message.id);
        if (!p) return;
        pending.delete(message.id);
        if (message.error) p.reject(error());
        else p.resolve(message.result);
      } catch {
        for (const p of pending.values()) p.reject(error());
      }
    });
    const request = (method: string, params: unknown): Promise<any> =>
      new Promise((resolve, reject) => {
        const id = ++serial;
        pending.set(id, { resolve, reject });
        socket.send(JSON.stringify({ id, method, params }));
      });
    timer = setTimeout(() => {
      for (const p of pending.values()) p.reject(error());
      socket.terminate();
    }, 5000);
    await request("initialize", {
      clientInfo: { name: "context_relay_delivery", version: "0.4.0-dev.0" },
      capabilities: {},
    });
    socket.send(JSON.stringify({ method: "initialized" }));
    let cursor: string | null = null;
    let found = false;
    for (let page = 0; page < 20; page++) {
      const loaded = await request("thread/loaded/list", {
        limit: 100,
        cursor,
      });
      if (Array.isArray(loaded.data) && loaded.data.includes(externalId)) {
        found = true;
        break;
      }
      cursor = loaded.nextCursor ?? null;
      if (!cursor) break;
    }
    if (!found) throw error();
    const result = await request("thread/read", { threadId: externalId });
    if (!sameDirectory(result.thread?.cwd, root))
      throw new RelayError(
        "wrong_destination",
        "The live thread belongs to a different project.",
        409,
      );
  } finally {
    clearTimeout(timer);
    socket.close();
  }
}
