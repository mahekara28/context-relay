# npm product release series

The npm product carries reviewed working context between coding sessions. It runs
on Node without a Python prerequisite. The existing Python API remains available;
its versioned state and handoff checks are reference semantics, not a shared
writable database. The Node workspace has an independently versioned schema.

## Release PRs

1. **0.1.0-alpha.1 — local runtime** (`release/npm-runtime`): packaged CLI,
   private SQLite workspace, normalized sessions, sourced checkpoints, immutable
   handoffs, authenticated loopback service and real MCP stdio integration.
2. **0.2.0-alpha.1 — harness connections** (`release/harness-adapters`): read-only
   discovery/import adapters for Claude Code, Codex, OpenCode and Goose; incremental
   capture; reversible project MCP configuration; explicit delivery capabilities.
3. **0.3.0-beta.1 — local workspace** (`release/local-workspace`): responsive UI
   for real stored sessions, context growth, checkpoint review, merge conflicts,
   destination selection and handoff receipts; optional Nebius interpretation;
   packaged browser tests, documentation and reproducible release assets.

PRs are stacked in that order. A release is ready to review when its behavior
passes tests against the installed npm tarball. Publishing to npm is a separate
release operation; documentation must not claim an unpublished package works
through the public registry.

## Product contract

- The CLI, browser and MCP use one local service and one authoritative store.
- Capture adapters preserve source identity and event ordering; re-import is
  idempotent and changed source events cannot silently overwrite earlier evidence.
- A checkpoint is an explicit, immutable selection of goals, instructions,
  source references, file state and open work. Model output is a proposal.
- Handoff acceptance checks recipient, integrity, source revision and file state.
  Acceptance does not mark the underlying task complete.
- Merging creates a new snapshot and records resolutions; native histories and
  Git worktrees are never silently merged or rewritten.
- Only selected local projects and explicitly configured sources are scanned.
- Capture, import, MCP retrieval and destination launch are separate capabilities.
  No adapter is advertised as live-tested without an actual run.
- The UI reports captured event/character growth. Model-window usage is shown
  only when the harness provides trustworthy measurements.
- Local operation works without an API key. Remote interpretation is explicit,
  bounded, redacted on a best-effort basis, and previewed before approval.

## Gates

- Type checks; transaction, provenance, budget, freshness and isolation tests.
- Real MCP client/server transport tests, HTTP authentication/origin checks.
- Adapter fixtures with duplicates, malformed records and incremental updates.
- Tarball installation and CLI lifecycle checks on Linux, macOS and Windows.
- Browser workflow checks using a real local service; light/dark/mobile review.
- No automatic paid model calls, npm publication, PR merges or history rewrites.

## Progress

- Runtime implemented: [PR #7](https://github.com/mahekara28/context-relay/pull/7).
- Harness connections implemented: [PR #8](https://github.com/mahekara28/context-relay/pull/8).
- Workspace, optional proposals, launch/export and release documentation implemented: [PR #9](https://github.com/mahekara28/context-relay/pull/9).
- Public npm publication and live-model quality evaluation are separate pending release activities; see [verification](NPM_VERIFICATION.md).
