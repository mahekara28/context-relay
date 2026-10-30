import { z } from "zod";

export const Harness = z.enum([
  "claude",
  "codex",
  "opencode",
  "goose",
  "manual",
]);
export type HarnessName = z.infer<typeof Harness>;
export const Ref = z
  .object({
    sessionId: z.string().min(1).max(100),
    eventId: z.string().min(1).max(300),
  })
  .strict();
export const EventInput = z
  .object({
    id: z.string().min(1).max(300),
    role: z.enum(["user", "assistant", "tool", "system"]),
    text: z.string().min(1).max(100_000),
    kind: z
      .enum(["message", "tool_result", "compaction", "note"])
      .default("message"),
    timestamp: z.string().max(100).optional(),
  })
  .strict();
export type RelayEvent = z.infer<typeof EventInput>;
export const SessionInput = z
  .object({
    projectId: z.string().min(1),
    harness: Harness,
    externalId: z.string().min(1).max(500),
    title: z.string().min(1).max(300),
    events: z.array(EventInput).max(20_000),
  })
  .strict();
export type SessionImport = z.infer<typeof SessionInput>;
export const Instruction = z
  .object({
    key: z
      .string()
      .min(1)
      .max(100)
      .regex(/^[a-z0-9_.-]+$/),
    text: z.string().min(1).max(4000),
    sources: z.array(Ref).min(1).max(30),
  })
  .strict();
export const CheckpointInput = z
  .object({
    projectId: z.string().min(1),
    title: z.string().min(1).max(200),
    goal: z.string().min(1).max(4000),
    sources: z
      .array(
        z
          .object({
            sessionId: z.string().min(1),
            revision: z.number().int().nonnegative(),
          })
          .strict(),
      )
      .min(1)
      .max(20),
    instructions: z.array(Instruction).max(100).default([]),
    criteria: z.array(z.string().min(1).max(2000)).max(50).default([]),
    openWork: z.array(z.string().min(1).max(2000)).max(100).default([]),
    evidence: z.array(Ref).max(100).default([]),
    files: z.array(z.string().min(1).max(1000)).max(100).default([]),
    parents: z.array(z.string().min(1)).max(20).default([]),
    resolutions: z.record(z.string(), z.string().min(1).max(4000)).default({}),
  })
  .strict();
export type CheckpointDraft = z.infer<typeof CheckpointInput>;
export interface Project {
  id: string;
  name: string;
  root: string;
  createdAt: string;
}
export interface Session {
  id: string;
  projectId: string;
  harness: HarnessName;
  externalId: string;
  title: string;
  revision: number;
  eventCount: number;
  characters: number;
  updatedAt: string;
}
export interface Artifact {
  path: string;
  sha256: string;
  bytes: number;
}
export interface Checkpoint extends Omit<CheckpointDraft, "files"> {
  version: "relay.checkpoint/v1";
  id: string;
  createdAt: string;
  artifacts: Artifact[];
  git: { head: string | null; branch: string | null };
  digest: string;
}
export interface Packet {
  version: "relay.packet/v1";
  checkpointId: string;
  checkpointDigest: string;
  project: { id: string; name: string };
  goal: string;
  instructions: z.infer<typeof Instruction>[];
  criteria: string[];
  openWork: string[];
  sources: CheckpointDraft["sources"];
  artifacts: Artifact[];
  git: Checkpoint["git"];
  evidence: {
    ref: z.infer<typeof Ref>;
    text: string;
    role: RelayEvent["role"];
  }[];
  omitted: { evidence: number };
  trust: string;
}
export interface Handoff {
  id: string;
  checkpointId: string;
  recipient: HarnessName;
  packet: Packet;
  digest: string;
  status: "offered" | "accepted" | "cancelled";
  createdAt: string;
  acceptedAt: string | null;
}
export class RelayError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 400,
  ) {
    super(message);
    this.name = "RelayError";
  }
}
