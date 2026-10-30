import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { writeFileSync, existsSync, lstatSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";
import { AdapterManager } from "./adapters/manager.js";
import { executable } from "./adapters/local.js";
import { codexEndpoint, inspectCodex, sameDirectory } from "./adapters/live.js";
import { openCodeGet, openCodePost } from "./adapters/capture.js";
import { type HarnessConnection } from "./adapters/types.js";
import { continuationPrompt, markdownPacket } from "./delivery.js";
import { type Handoff, type Session, RelayError } from "./model.js";
import { RelayStore } from "./store.js";
import { canonical, id, now, privateDirectory } from "./util.js";

const run = promisify(execFile);
const quote = (text: string) => "'" + text.replaceAll("'", "'\\''") + "'";
export interface Delivery {
  handoffId: string;
  state: "queued" | "sending" | "sent" | "blocked" | "uncertain";
  message: string;
  updatedAt: string;
  transport: string | null;
  attemptId: string | null;
}
interface Receiver {
  id: string;
  sessionId: string;
  seen: number;
}
export interface DeliveryOptions {
  intervalMs?: number;
  inspectCodex?: typeof inspectCodex;
  run?: typeof run;
}

/** Durable outbox. Only failures known to precede dispatch are retried automatically. */
export class Receiving {
  private receivers = new Map<string, Receiver>();
  private timer: NodeJS.Timeout | undefined;
  private task: Promise<void> | undefined;
  private stopped = false;
  constructor(
    readonly store: RelayStore,
    readonly adapters: AdapterManager,
    readonly options: DeliveryOptions = {},
  ) {
    for (const d of this.list())
      if (d.state === "sending")
        this.save({
          ...d,
          state: "uncertain",
          message:
            "Relay restarted during dispatch. Check the destination before retrying.",
        });
    this.schedule();
  }
  private read<T>(key: string): T | null {
    const row = this.store.db
      .prepare("SELECT body FROM workspace_state WHERE key=?")
      .get(key);
    return row ? (JSON.parse(String(row.body)) as T) : null;
  }
  private write(key: string, value: unknown) {
    this.store.db
      .prepare(
        "INSERT INTO workspace_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
      )
      .run(key, canonical(value));
  }
  get(handoffId: string): Delivery | null {
    return this.read("delivery:" + handoffId);
  }
  assertUndispatched(handoffId: string) {
    const delivery = this.get(handoffId);
    if (delivery && ["sending", "sent", "uncertain"].includes(delivery.state))
      throw new RelayError(
        "already_dispatched",
        "This message may already be in the destination chat. It cannot be recalled or rewired.",
        409,
      );
  }
  list(): Delivery[] {
    return this.store.db
      .prepare("SELECT body FROM workspace_state WHERE key LIKE 'delivery:%'")
      .all()
      .map((r) => JSON.parse(String(r.body)) as Delivery);
  }
  private save(d: Delivery) {
    d.updatedAt = now();
    this.write("delivery:" + d.handoffId, d);
    return d;
  }
  private fresh(handoffId: string): Handoff {
    const h = this.store.handoff(handoffId);
    if (h.status !== "offered" || !h.packet.destination)
      throw new RelayError(
        "handoff_closed",
        "This connection is no longer awaiting delivery.",
        409,
      );
    const freshness = this.store.freshness(
      this.store.checkpoint(h.checkpointId),
    );
    if (!freshness.current)
      throw new RelayError(
        "stale_source",
        "Source context changed. Review an updated handoff before sending.",
        409,
      );
    return h;
  }
  enqueue(handoffId: string, retry = false) {
    this.fresh(handoffId);
    const previous = this.get(handoffId);
    if (
      previous &&
      (!retry || ["queued", "sending", "sent"].includes(previous.state))
    )
      return previous;
    return this.save({
      handoffId,
      state: "queued",
      message: "Waiting for the destination receiver.",
      updatedAt: now(),
      transport: null,
      attemptId: null,
    });
  }
  private connection(s: Session): HarnessConnection {
    return (
      this.adapters
        .connections(s.projectId)
        .find((c) => c.harness === s.harness) ?? {
        id: "delivery",
        projectId: s.projectId,
        harness: s.harness as HarnessConnection["harness"],
        capture: false,
        configureMcp: false,
        updatedAt: "",
        configPath: null,
      }
    );
  }
  private endpoint(s: Session) {
    return (
      this.read<{ endpoint: string }>("receiver:" + s.id)?.endpoint ?? "unix://"
    );
  }
  configure(sessionId: string, input: unknown) {
    const s = this.store.session(sessionId);
    if (s.harness !== "codex")
      throw new RelayError(
        "invalid_receiver",
        "Custom app-server endpoints apply to Codex.",
      );
    const { endpoint } = z
      .object({ endpoint: z.string().max(2000) })
      .strict()
      .parse(input);
    this.write("receiver:" + s.id, { endpoint: codexEndpoint(endpoint) });
    return this.info(sessionId);
  }
  info(sessionId: string) {
    const s = this.store.session(sessionId);
    const live = [...this.receivers.values()].some(
      (r) => r.sessionId === s.id && Date.now() - r.seen < 8000,
    );
    const command = [
      process.execPath,
      fileURLToPath(new URL("./cli.js", import.meta.url)),
      "receive",
      "--data-dir",
      this.store.directory,
      "--id",
      s.id,
    ]
      .map(quote)
      .join(" ");
    return {
      harness: s.harness,
      mode:
        s.harness === "claude"
          ? "channel"
          : ["codex", "opencode"].includes(s.harness)
            ? "native"
            : "manual",
      online: s.harness === "claude" ? live : null,
      endpoint: s.harness === "codex" ? this.endpoint(s) : null,
      command: s.harness === "claude" ? command : null,
      message:
        s.harness === "claude"
          ? live
            ? "Session receiver connected."
            : "Resume this Claude Code thread once with the Relay channel enabled. Queued handoffs then arrive automatically. Claude currently requires its development-channel opt-in for custom channels."
          : s.harness === "codex"
            ? "Delivery uses the running host’s queue. The target must be open on this app-server; a separate desktop host cannot be silently attached."
            : s.harness === "opencode"
              ? "Relay sends to the same local OpenCode server used for capture. Keep the destination open on that server."
              : "This harness currently supports manual MCP retrieval. Direct delivery is unavailable.",
    };
  }
  register(input: unknown) {
    const data = z
      .object({
        projectId: z.string(),
        harness: z.literal("claude"),
        externalId: z.string(),
      })
      .strict()
      .parse(input);
    const s = this.store
      .sessions(data.projectId)
      .find(
        (s) => s.harness === data.harness && s.externalId === data.externalId,
      );
    if (!s)
      throw new RelayError(
        "wrong_destination",
        "The receiver must match a captured session in this project.",
        409,
      );
    if (
      [...this.receivers.values()].some(
        (r) => r.sessionId === s.id && Date.now() - r.seen < 8000,
      )
    )
      throw new RelayError(
        "receiver_exists",
        "This thread already has a live Relay receiver.",
        409,
      );
    const receiver = { id: id(), sessionId: s.id, seen: Date.now() };
    this.receivers.set(receiver.id, receiver);
    return { id: receiver.id };
  }
  next(receiverId: string) {
    if (this.stopped) return null;
    const r = this.receivers.get(receiverId);
    if (!r)
      throw new RelayError(
        "receiver_expired",
        "Reconnect the session receiver.",
        404,
      );
    r.seen = Date.now();
    for (const d of this.list()) {
      if (d.state !== "queued") continue;
      const h = this.store.handoff(d.handoffId);
      if (h.packet.destination?.sessionId !== r.sessionId) continue;
      try {
        this.fresh(h.id);
      } catch (error) {
        this.block(d, error);
        continue;
      }
      const attemptId = id();
      this.save({
        ...d,
        state: "sending",
        transport: "Claude MCP channel",
        attemptId,
        message: "Dispatching to the destination’s MCP channel.",
      });
      return { handoffId: h.id, attemptId, prompt: continuationPrompt(h) };
    }
    return null;
  }
  receipt(receiverId: string, input: unknown) {
    const r = this.receivers.get(receiverId);
    if (!r)
      throw new RelayError(
        "receiver_expired",
        "Reconnect the session receiver.",
        404,
      );
    const data = z
      .object({
        handoffId: z.string(),
        attemptId: z.string(),
        sent: z.boolean(),
      })
      .strict()
      .parse(input);
    const h = this.store.handoff(data.handoffId),
      d = this.get(h.id);
    if (
      h.packet.destination?.sessionId !== r.sessionId ||
      !d ||
      d.attemptId !== data.attemptId ||
      !["sending", "sent"].includes(d.state)
    )
      throw new RelayError(
        "wrong_delivery",
        "The receipt does not match this dispatch.",
        409,
      );
    return this.save({
      ...d,
      state: data.sent ? "sent" : "uncertain",
      message: data.sent
        ? "Sent through MCP. Waiting for the agent to inspect and acknowledge it."
        : "Channel delivery could not be confirmed. Check the destination before retrying.",
    });
  }
  private block(d: Delivery, error: unknown) {
    return this.save({
      ...d,
      state: "blocked",
      message:
        error instanceof RelayError
          ? error.message
          : "Delivery needs attention.",
    });
  }
  private payload(h: Handoff): string {
    const directory = join(this.store.directory, "deliveries");
    privateDirectory(directory);
    const path = join(directory, h.id + ".md");
    if (existsSync(path) && lstatSync(path).isSymbolicLink())
      throw new RelayError(
        "unsafe_path",
        "Delivery packet cannot be a symlink.",
      );
    writeFileSync(path, markdownPacket(h), { mode: 0o600 });
    const ack = [
      process.execPath,
      fileURLToPath(new URL("./cli.js", import.meta.url)),
      "handoff",
      "accept",
      "--data-dir",
      this.store.directory,
      "--id",
      h.id,
      "--recipient",
      h.recipient,
      "--destination-id",
      h.packet.destination!.externalId,
    ]
      .map(quote)
      .join(" ");
    return (
      continuationPrompt(h) +
      `\nIf Relay MCP tools are not loaded in this existing chat, read the local handoff file ${JSON.stringify(path)}. After verifying the packet and only in the intended native session, acknowledge it with this local command (which checks freshness):\n${ack}\nContinue only after acknowledgement succeeds. Keep your normal permissions and approvals.`
    );
  }
  private async dispatch(d: Delivery) {
    let h: Handoff;
    try {
      h = this.fresh(d.handoffId);
    } catch (error) {
      this.block(d, error);
      return;
    }
    const s = this.store.session(h.packet.destination!.sessionId);
    if (s.harness === "claude") return; // The bound stdio channel claims its own queue.
    if (!["codex", "opencode"].includes(s.harness)) {
      this.block(
        d,
        new RelayError(
          "unsupported",
          "Direct delivery is not available for this harness. Use the manual MCP fallback.",
        ),
      );
      return;
    }
    const connection = this.connection(s),
      project = this.store.project(s.projectId);
    let bin: string | undefined;
    try {
      if (s.harness === "codex") {
        bin = connection.executable ?? executable("codex");
        if (!bin)
          throw new RelayError(
            "receiver_offline",
            "Install Codex with the queue command to enable direct delivery.",
          );
        await (this.options.inspectCodex ?? inspectCodex)(
          this.endpoint(s),
          s.externalId,
          project.root,
        );
      } else {
        const native = (await openCodeGet(
          connection,
          project,
          "/session/" + encodeURIComponent(s.externalId),
        )) as { id?: string; directory?: string };
        if (
          native.id !== s.externalId ||
          !sameDirectory(native.directory, project.root)
        )
          throw new RelayError(
            "wrong_destination",
            "The OpenCode session does not match the destination project.",
          );
      }
    } catch (error) {
      if (
        error instanceof RelayError &&
        ["wrong_destination", "invalid_endpoint"].includes(error.code)
      )
        this.block(d, error);
      else if (Date.now() - Date.parse(d.updatedAt) > 10000 || !d.transport)
        this.save({
          ...d,
          transport: s.harness,
          message:
            error instanceof RelayError
              ? error.message
              : "The receiving harness is offline. Waiting to reconnect.",
        });
      return;
    }
    if (this.stopped) return;
    // Revalidate after asynchronous probes: cancellation, rewiring and stale sources win.
    try {
      h = this.fresh(h.id);
    } catch (error) {
      this.block(d, error);
      return;
    }
    const prompt = this.payload(h);
    const sent = {
      ...d,
      state: "sending" as const,
      transport:
        s.harness === "codex" ? "Codex native queue" : "OpenCode session API",
      attemptId: id(),
    };
    this.save(sent);
    try {
      if (s.harness === "codex")
        await (this.options.run ?? run)(
          bin!,
          [
            "queue",
            "--remote",
            codexEndpoint(this.endpoint(s)),
            "--thread",
            s.externalId,
            "--message",
            prompt,
          ],
          {
            cwd: project.root,
            timeout: 15000,
            maxBuffer: 256 * 1024,
            windowsHide: true,
          },
        );
      else
        await openCodePost(
          connection,
          project,
          "/session/" + encodeURIComponent(s.externalId) + "/prompt_async",
          { parts: [{ type: "text", text: prompt }] },
        );
      this.save({
        ...sent,
        state: "sent",
        message:
          "The harness accepted the handoff into its queue. Waiting for the agent’s acknowledgement.",
      });
    } catch {
      this.save({
        ...sent,
        state: "uncertain",
        message:
          "The harness did not confirm dispatch. Check the destination before retrying; Relay will not send a duplicate automatically.",
      });
    }
  }
  private schedule() {
    if (!this.stopped)
      this.timer = setTimeout(() => {
        void this.flush().finally(() => this.schedule());
      }, this.options.intervalMs ?? 2000);
  }
  async flush() {
    if (this.task) return this.task;
    this.task = (async () => {
      for (const d of this.list()) {
        if (this.stopped) break;
        if (
          d.state === "sending" &&
          Date.now() - Date.parse(d.updatedAt) > 30000
        )
          this.save({
            ...d,
            state: "uncertain",
            message:
              "Dispatch confirmation timed out. Check the destination before retrying.",
          });
        if (d.state === "queued") {
          try {
            await this.dispatch(d);
          } catch (error) {
            this.block(d, error);
          }
        }
      }
      for (const [key, r] of this.receivers)
        if (Date.now() - r.seen > 30000) this.receivers.delete(key);
    })().finally(() => {
      this.task = undefined;
    });
    return this.task;
  }
  async close() {
    this.stopped = true;
    clearTimeout(this.timer);
    await this.task;
  }
}
