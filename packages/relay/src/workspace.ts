import { z } from "zod";
import { AdapterManager } from "./adapters/manager.js";
import { capture } from "./adapters/capture.js";
import {
  discoverLocal,
  type LocalSource,
  type NativeHarness,
  type SourceOptions,
} from "./adapters/local.js";
import { CheckpointInput, RelayError } from "./model.js";
import { RelayStore } from "./store.js";
import { canonical, now, redact } from "./util.js";

const harnesses: NativeHarness[] = ["claude", "codex", "opencode", "goose"];
interface Settings {
  enabled: boolean;
  harnesses: NativeHarness[];
}
interface Observation {
  fingerprint: string | null;
  updatedAt: string | null;
  seenAt: string;
  capturedAt?: string;
  excerptedEvents?: number;
  error: string | null;
  retryAt: number;
}
export interface WorkspaceOptions {
  discover?: typeof discoverLocal;
  capture?: typeof capture;
  intervalMs?: number;
  sources?: Partial<Record<NativeHarness, SourceOptions>>;
}
export class Workspace {
  private settings: Settings;
  private sources: Omit<LocalSource, "sessions">[] = [];
  private timer: NodeJS.Timeout | undefined;
  private task: Promise<void> | undefined;
  private stopped = false;
  private lastScan: string | null = null;
  private error: string | null = null;
  constructor(
    readonly store: RelayStore,
    readonly adapters: AdapterManager,
    readonly options: WorkspaceOptions = {},
  ) {
    this.settings = this.read("settings", { enabled: false, harnesses });
    if (this.settings.enabled) this.scan();
  }
  private read<T>(key: string, fallback: T): T {
    const row = this.store.db
      .prepare("SELECT body FROM workspace_state WHERE key=?")
      .get(key);
    return row ? (JSON.parse(String(row.body)) as T) : fallback;
  }
  private save(key: string, value: unknown): void {
    this.store.db
      .prepare(
        "INSERT INTO workspace_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
      )
      .run(key, canonical(value));
  }
  status() {
    return {
      configured: !!this.store.db
        .prepare("SELECT key FROM workspace_state WHERE key='settings'")
        .get(),
      ...this.settings,
      scanning: !!this.task,
      lastScan: this.lastScan,
      intervalMs: this.options.intervalMs ?? 15000,
      sources: this.sources,
      error: this.error,
    };
  }
  configure(input: unknown) {
    const data = z
      .object({
        enabled: z.boolean(),
        harnesses: z
          .array(z.enum(["claude", "codex", "opencode", "goose"]))
          .optional(),
      })
      .strict()
      .parse(input);
    this.settings = {
      enabled: data.enabled,
      harnesses: data.harnesses ?? this.settings.harnesses,
    };
    this.save("settings", this.settings);
    clearTimeout(this.timer);
    if (data.enabled) this.scan();
    return this.status();
  }
  scan(): void {
    if (this.stopped || this.task || !this.settings.enabled) return;
    clearTimeout(this.timer);
    this.task = this.collect()
      .catch((error) => {
        this.error =
          error instanceof RelayError
            ? redact(error.message)
            : "Local discovery failed. Check source availability.";
      })
      .finally(() => {
        this.task = undefined;
        if (!this.stopped && this.settings.enabled)
          this.timer = setTimeout(
            () => this.scan(),
            this.options.intervalMs ?? 15000,
          );
      });
  }
  async settled() {
    await this.task;
  }
  async close() {
    this.stopped = true;
    clearTimeout(this.timer);
    await this.task;
  }
  private async collect() {
    this.error = null;
    const discover = this.options.discover ?? discoverLocal;
    const results = await Promise.allSettled(
      harnesses.map(async (harness) => {
        if (!this.settings.harnesses.includes(harness))
          return {
            harness,
            state: "paused" as const,
            message: "Discovery paused for this harness.",
            sessions: [],
          };
        return discover(harness, this.options.sources?.[harness]);
      }),
    );
    this.sources = results.map((result, index) => {
      const harness = harnesses[index]!;
      if (result.status === "fulfilled") {
        const { sessions: _sessions, ...status } = result.value;
        return status;
      }
      return {
        harness,
        state:
          harness === "opencode" && !(result.reason instanceof RelayError)
            ? "unavailable"
            : "error",
        message:
          result.reason instanceof RelayError
            ? redact(result.reason.message)
            : harness === "opencode"
              ? "Start OpenCode's local server to discover its sessions."
              : "Harness is unavailable. Check its installation and authentication.",
      };
    });
    for (const [index, result] of results.entries()) {
      if (result.status === "rejected") continue;
      const harness = harnesses[index]!,
        status = this.sources[index]!;
      const { sessions } = result.value;
      let captured = 0;
      for (const native of sessions.sort((a, b) =>
        (b.updatedAt ?? "").localeCompare(a.updatedAt ?? ""),
      )) {
        if (
          this.stopped ||
          !this.settings.enabled ||
          !this.settings.harnesses.includes(harness)
        )
          break;
        try {
          const project = this.store.addProject({ root: native.root });
          let connection = this.adapters
            .connections(project.id)
            .find((c) => c.harness === harness);
          if (!connection)
            connection = this.adapters.connect({
              projectId: project.id,
              harness,
              configureMcp: false,
              ...(native.source ? { source: native.source } : {}),
              ...(native.executable ? { executable: native.executable } : {}),
            });
          if (!connection.capture) continue;
          const { session } = this.store.importSession({
            projectId: project.id,
            harness,
            externalId: native.externalId,
            title: native.title,
            events: [],
          });
          const row = this.store.db
            .prepare("SELECT body FROM observations WHERE session_id=?")
            .get(session.id);
          const old: Observation | null = row
            ? (JSON.parse(String(row.body)) as Observation)
            : null;
          const observation: Observation = {
            fingerprint: old?.fingerprint ?? null,
            updatedAt: native.updatedAt,
            seenAt: now(),
            ...(old?.capturedAt ? { capturedAt: old.capturedAt } : {}),
            excerptedEvents: old?.excerptedEvents ?? 0,
            error: old?.error ?? null,
            retryAt: old?.retryAt ?? 0,
          };
          const needsCapture =
            old?.fingerprint !== native.fingerprint ||
            (!native.updatedAt &&
              Date.now() - Date.parse(old?.capturedAt ?? "1970-01-01") > 60000);
          if (
            needsCapture &&
            captured < 12 &&
            Date.now() >= observation.retryAt
          ) {
            captured++;
            try {
              // Use the discovery source, not a previously configured source's unrelated locator.
              const input = await (this.options.capture ?? capture)(
                {
                  ...connection,
                  ...(native.source ? { source: native.source } : {}),
                  ...(native.executable
                    ? { executable: native.executable }
                    : {}),
                },
                project,
                {
                  externalId: native.externalId,
                  title: native.title,
                  locator: native.locator,
                  harness,
                  projectId: project.id,
                },
                session.eventCount && harness === "codex"
                  ? {
                      afterEventId: this.store.events(
                        session.id,
                        session.eventCount - 1,
                        1,
                      )[0]!.id,
                    }
                  : {},
              );
              if (this.stopped || !this.settings.enabled) break;
              this.store.importSession(input);
              observation.excerptedEvents = Number(
                this.store.db
                  .prepare(
                    "SELECT count(*) AS count FROM events WHERE session_id=? AND json_extract(body, '$.omittedCharacters') IS NOT NULL",
                  )
                  .get(session.id)!.count,
              );
              observation.fingerprint = native.fingerprint;
              observation.capturedAt = now();
              observation.error = null;
              observation.retryAt = 0;
            } catch (error) {
              observation.error =
                error instanceof RelayError
                  ? redact(error.message)
                  : "This session could not be read.";
              observation.retryAt = Date.now() + 60000;
            }
          }
          this.store.db
            .prepare(
              "INSERT INTO observations VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET body=excluded.body",
            )
            .run(session.id, canonical(observation));
        } catch (error) {
          status.state = "error";
          status.message =
            error instanceof RelayError
              ? redact(error.message)
              : "Some project directories could not be indexed.";
        }
      }
    }
    this.lastScan = now();
  }
  graph() {
    const observations = new Map(
      this.store.db
        .prepare("SELECT session_id,body FROM observations")
        .all()
        .map((row) => [
          String(row.session_id),
          JSON.parse(String(row.body)) as Observation,
        ]),
    );
    const sessions = this.store.sessions().map((session) => {
      const observation = observations.get(session.id);
      return {
        ...session,
        activityAt: observation?.updatedAt ?? null,
        captured: observation ? observation.fingerprint !== null : true,
        captureError: observation?.error ?? null,
        captureWarning: observation?.excerptedEvents
          ? `${observation.excerptedEvents} oversized event(s) contain labeled excerpts. Open the native session for full content.`
          : null,
        seenAt: observation?.seenAt ?? null,
      };
    });
    // Freshness reads files/Git. Only evaluate targeted, offered packets; old history stays cheap.
    const relays = this.store
      .handoffs()
      .filter((h) => h.packet.destination)
      .map((h) => ({
        ...h,
        freshness:
          h.status === "offered"
            ? this.store.freshness(this.store.checkpoint(h.checkpointId))
            : { current: true, reasons: [] },
      }));
    return {
      projects: this.store.projects(),
      sessions,
      relays,
      positions: this.read("positions", {}),
      discovery: this.status(),
      connections: this.adapters
        .connections()
        .map(({ receipt: _receipt, ...connection }) => connection),
    };
  }
  position(input: unknown) {
    const data = z
      .object({
        sessionId: z.string(),
        x: z.number().finite().min(-10000).max(10000),
        y: z.number().finite().min(-10000).max(10000),
      })
      .strict()
      .parse(input);
    this.store.session(data.sessionId);
    const positions = this.read<Record<string, { x: number; y: number }>>(
      "positions",
      {},
    );
    positions[data.sessionId] = { x: data.x, y: data.y };
    this.save("positions", positions);
    return positions[data.sessionId];
  }
  relay(input: unknown) {
    const { destinationSessionId, replacesHandoffId, ...draft } =
      CheckpointInput.extend({
        destinationSessionId: z.string().min(1),
        replacesHandoffId: z.string().min(1).optional(),
      }).parse(input);
    const target = this.store.session(destinationSessionId);
    return this.store.transaction(() => {
      if (replacesHandoffId) {
        const previous = this.store.handoff(replacesHandoffId);
        if (
          previous.status !== "offered" ||
          previous.packet.project.id !== draft.projectId ||
          !previous.packet.destination
        )
          throw new RelayError(
            "invalid_replacement",
            "Only a pending connection in this project can be updated.",
            409,
          );
      }
      const checkpoint = this.store.createCheckpoint(draft);
      const handoff = this.store.offer({
        checkpointId: checkpoint.id,
        recipient: target.harness,
        destinationSessionId,
        maxBytes: 1_000_000,
      });
      if (handoff.packet.omitted.evidence)
        throw new RelayError(
          "budget_exceeded",
          "Selected evidence is too large. Remove some messages before relaying.",
        );
      if (replacesHandoffId) this.store.cancel(replacesHandoffId);
      return handoff;
    });
  }
}
