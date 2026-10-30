# Task context across agents

Status: accepted direction for the next experimental task API.

Context Relay will carry the current state of unfinished work between agent
harnesses. A task has a goal, completion criteria, a project scope, a monotonic
revision, current instructions, source evidence, artifact references, and open
work. A handoff captures one immutable task revision and records whether a
recipient accepted it after checking freshness.

The public data contract is versioned independently of the Python package.
The Python task service owns its meaning; a CLI and optional MCP adapter expose
the same operations. MCP discovery does not cause a harness to capture evidence,
inject context automatically, or route its actions through Relay. Each harness
integration reports those capabilities separately.

The legacy session and provider APIs remain supported. The task service uses
additive storage so existing databases can be opened without migrating their
session semantics. Model interpretation may propose updates and summaries, but
the store and handoff path work without a model.

## Trust and scope

The host supplies identity. A model-supplied actor name is metadata and cannot
grant permission, attest a command result, or turn reported completion into a
verified outcome. Every instruction and claim retains a source reference and a
scope. A later instruction supersedes an earlier one only through an explicit,
validated update. Concurrent writers must provide the revision they read;
stale writes fail and disclose the current revision.

A handoff transfers context and artifact references. A path or digest alone
does not transfer file bytes or authenticate their origin. The receiver checks
availability and current state before relying on an artifact. External effects
can outlive a local transaction; an interrupted effect remains unresolved until
destination evidence establishes its outcome.

The first user-facing proof is a coding task investigated in one harness and
completed in another. The evaluation compares this path with a plain Markdown
handoff under the same receiver conditions. Task completion comes from actual
artifact and test checks, not from the agent's report.

## Scope of the first release

Start with a local store, CLI, and stdio MCP server. Add remote access only
after the local path works. Keep shared knowledge recall, general scheduling,
model-native reasoning transfer, and automatic execution outside this contract.

See [architecture](ARCHITECTURE.md) for the existing session APIs. This ADR
describes the new task surface and does not relabel existing alpha evaluations
as cross-harness results.
