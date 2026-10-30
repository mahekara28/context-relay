import { z } from "zod";
import { Harness } from "../model.js";
export const ConnectionInput = z
  .object({
    projectId: z.string().min(1),
    harness: Harness.exclude(["manual"]),
    source: z.string().min(1).max(2000).optional(),
    executable: z.string().min(1).max(2000).optional(),
    capture: z.boolean().default(true),
    configureMcp: z.boolean().default(true),
  })
  .strict();
export type HarnessConnection = z.infer<typeof ConnectionInput> & {
  id: string;
  updatedAt: string;
  configPath: string | null;
};
export interface DiscoveredSession {
  externalId: string;
  title: string;
  locator: string;
  harness: HarnessConnection["harness"];
  projectId: string;
}
export const CAPABILITIES = [
  {
    harness: "claude",
    name: "Claude Code",
    discovery: "Project-matched JSONL transcripts",
    capture: "Explicit sync; stable transcript events",
    mcp: "Project .mcp.json",
    delivery: "Reviewed packet file / MCP retrieval",
    launch: false,
  },
  {
    harness: "codex",
    name: "Codex",
    discovery: "App-server thread/list filtered by cwd",
    capture: "Completed turns via app-server",
    mcp: "Project .codex/config.toml",
    delivery: "Reviewed packet file / MCP retrieval",
    launch: false,
  },
  {
    harness: "opencode",
    name: "OpenCode",
    discovery: "Local session API filtered by directory",
    capture: "Completed session messages",
    mcp: "Project opencode.json",
    delivery: "Reviewed packet file / MCP retrieval",
    launch: false,
  },
  {
    harness: "goose",
    name: "Goose",
    discovery: "CLI session list filtered by working directory",
    capture: "CLI JSON session export",
    mcp: "Explicit extension command",
    delivery: "Reviewed packet file / MCP retrieval",
    launch: false,
  },
] as const;
