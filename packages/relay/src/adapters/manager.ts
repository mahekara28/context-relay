import { z } from "zod";
import { RelayStore } from "../store.js";
import { RelayError } from "../model.js";
import { canonical, id, now, redact } from "../util.js";
import {
  ConnectionInput,
  type HarnessConnection,
  type DiscoveredSession,
} from "./types.js";
import {
  configureMcp,
  removeMcp,
  mcpCommand,
  type ConfigReceipt,
} from "./config.js";
import { capture, discover } from "./capture.js";
interface SavedConnection extends HarnessConnection {
  receipt: ConfigReceipt | null;
  lastSync: string | null;
  lastError: string | null;
}
export interface CaptureJob {
  id: string;
  connectionId: string;
  state: "running" | "completed" | "failed" | "cancelled" | "interrupted";
  createdAt: string;
  completed: number;
  total: number;
  imported: { sessionId: string; added: number }[];
  errors: { externalId: string; message: string }[];
  error: string | null;
}
export class AdapterManager {
  private busy = new Set<string>();
  private running = new Map<
    string,
    { cancel: boolean; promise: Promise<void> }
  >();
  constructor(readonly store: RelayStore) {
    for (const job of this.jobs())
      if (job.state === "running") {
        job.state = "interrupted";
        job.error =
          "The service stopped during capture. Start a new capture to resume safely.";
        this.saveJob(job);
      }
  }
  jobs(): CaptureJob[] {
    return this.store.db
      .prepare("SELECT body FROM capture_jobs ORDER BY rowid DESC LIMIT 100")
      .all()
      .map((row) => JSON.parse(String(row.body)) as CaptureJob);
  }
  job(jobId: string): CaptureJob {
    const row = this.store.db
      .prepare("SELECT body FROM capture_jobs WHERE id=?")
      .get(jobId);
    if (!row) throw new RelayError("not_found", "Capture job not found.", 404);
    return JSON.parse(String(row.body)) as CaptureJob;
  }
  private saveJob(job: CaptureJob): void {
    this.store.db
      .prepare(
        "INSERT INTO capture_jobs(id,body) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
      )
      .run(job.id, canonical(job));
  }
  cancel(jobId: string): CaptureJob {
    const job = this.job(jobId);
    const running = this.running.get(jobId);
    if (running) running.cancel = true;
    return job;
  }
  async close(): Promise<void> {
    for (const running of this.running.values()) running.cancel = true;
    await Promise.allSettled([...this.running.values()].map((r) => r.promise));
  }
  startSync(connectionId: string, externalIds?: string[]): CaptureJob {
    if (externalIds)
      z.array(z.string().min(1).max(500)).max(200).parse(externalIds);
    this.connection(connectionId);
    if (this.busy.has(connectionId))
      throw new RelayError("capture_busy", "Capture is already running.", 409);
    const job: CaptureJob = {
      id: id(),
      connectionId,
      state: "running",
      createdAt: now(),
      completed: 0,
      total: 0,
      imported: [],
      errors: [],
      error: null,
    };
    this.saveJob(job);
    const running = { cancel: false, promise: Promise.resolve() };
    running.promise = this.sync(
      connectionId,
      externalIds,
      job,
      () => running.cancel,
    )
      .then((result) => {
        job.imported = result.imported;
        job.errors = result.errors;
        job.state = running.cancel ? "cancelled" : "completed";
      })
      .catch((error) => {
        job.state = "failed";
        job.error =
          error instanceof RelayError
            ? redact(error.message)
            : "Capture failed. Check source configuration.";
      })
      .finally(() => {
        this.saveJob(job);
        this.running.delete(job.id);
      });
    this.running.set(job.id, running);
    return job;
  }
  connections(projectId?: string): SavedConnection[] {
    return this.store.db
      .prepare("SELECT body FROM connections ORDER BY rowid")
      .all()
      .map((row) => JSON.parse(String(row.body)) as SavedConnection)
      .filter((row) => !projectId || row.projectId === projectId);
  }
  connection(connectionId: string): SavedConnection {
    const row = this.store.db
      .prepare("SELECT body FROM connections WHERE id=?")
      .get(connectionId);
    if (!row) throw new RelayError("not_found", "Connection not found.", 404);
    return JSON.parse(String(row.body)) as SavedConnection;
  }
  connect(input: unknown): SavedConnection {
    const data = ConnectionInput.parse(input);
    const project = this.store.project(data.projectId);
    const previous = this.connections(project.id).find(
      (c) => c.harness === data.harness,
    );
    if (previous && this.busy.has(previous.id))
      throw new RelayError(
        "capture_busy",
        "Wait for capture before reconnecting.",
        409,
      );
    if (previous?.receipt && !data.configureMcp) removeMcp(previous.receipt);
    const connection: SavedConnection = {
      ...data,
      id: previous?.id ?? id(),
      updatedAt: now(),
      configPath: previous?.configPath ?? null,
      receipt: data.configureMcp ? (previous?.receipt ?? null) : null,
      lastSync: previous?.lastSync ?? null,
      lastError: null,
    };
    const receipt = configureMcp(connection, project, this.store.directory);
    connection.receipt = receipt ?? connection.receipt;
    connection.configPath = connection.receipt?.path ?? null;
    this.store.db
      .prepare(
        "INSERT INTO connections(id,project_id,harness,body) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
      )
      .run(connection.id, project.id, data.harness, canonical(connection));
    return connection;
  }
  disconnect(connectionId: string): { disconnected: true } {
    if (this.busy.has(connectionId))
      throw new RelayError(
        "capture_busy",
        "Wait for capture to finish before disconnecting.",
        409,
      );
    const connection = this.connection(connectionId);
    if (connection.receipt) removeMcp(connection.receipt);
    this.store.db
      .prepare("DELETE FROM connections WHERE id=?")
      .run(connectionId);
    return { disconnected: true };
  }
  async discover(connectionId: string): Promise<DiscoveredSession[]> {
    const connection = this.connection(connectionId);
    return discover(connection, this.store.project(connection.projectId));
  }
  async sync(
    connectionId: string,
    externalIds?: string[],
    job?: CaptureJob,
    cancelled = () => false,
  ): Promise<{
    imported: { sessionId: string; added: number }[];
    errors: { externalId: string; message: string }[];
  }> {
    if (externalIds)
      z.array(z.string().min(1).max(500)).max(200).parse(externalIds);
    if (this.busy.has(connectionId))
      throw new RelayError(
        "capture_busy",
        "Capture is already running for this connection.",
        409,
      );
    this.busy.add(connectionId);
    const connection = this.connection(connectionId);
    const project = this.store.project(connection.projectId);
    try {
      const sessions = await discover(connection, project);
      const selected = externalIds
        ? sessions.filter((s) => externalIds.includes(s.externalId))
        : sessions;
      if (externalIds?.some((id) => !sessions.some((s) => s.externalId === id)))
        throw new RelayError(
          "not_found",
          "A selected source session is no longer available.",
          404,
        );
      if (selected.length > 200)
        throw new RelayError(
          "source_limit",
          "Choose at most 200 sessions per capture.",
        );
      if (job) {
        job.total = selected.length;
        this.saveJob(job);
      }
      const imported: { sessionId: string; added: number }[] = [];
      const errors: { externalId: string; message: string }[] = [];
      for (const session of selected) {
        if (cancelled()) break;
        try {
          const input = await capture(connection, project, session);
          const result = this.store.importSession(input);
          imported.push({ sessionId: result.session.id, added: result.added });
        } catch (error) {
          errors.push({
            externalId: session.externalId,
            message:
              error instanceof RelayError
                ? error.message
                : "Source could not be captured.",
          });
        }
        if (job) {
          job.completed++;
          job.imported = imported;
          job.errors = errors;
          this.saveJob(job);
        }
      }
      connection.lastSync = now();
      connection.lastError = errors.length
        ? `${errors.length} source session(s) need attention.`
        : null;
      this.save(connection);
      return { imported, errors };
    } catch (error) {
      connection.lastError =
        error instanceof RelayError
          ? redact(error.message)
          : "Capture failed. Check the source configuration.";
      this.save(connection);
      throw error;
    } finally {
      this.busy.delete(connectionId);
    }
  }
  command(connectionId: string): string[] {
    return mcpCommand(this.connection(connectionId), this.store.directory);
  }
  private save(connection: SavedConnection): void {
    this.store.db
      .prepare("UPDATE connections SET body=? WHERE id=?")
      .run(canonical(connection), connection.id);
  }
}
