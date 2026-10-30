# npm beta verification

Beta release: **0.3.0-beta.1**. These results describe implementation checks, not a claim of improved model performance.

| Gate | Evidence |
| --- | --- |
| Core/transport/provider tests | 19 Node tests, including transactional rollback, provenance, merge decisions, UTF-8 budget, digest tampering, stale sources/files, project/recipient isolation, auth/origin/host rejection, real MCP stdio client and malformed model responses |
| Adapter checks | Synthetic Claude JSONL and subagent identity; real local HTTP server for OpenCode; JSON-RPC subprocess; Goose format and unavailable-executable cases; reversible JSON/TOML configuration; interrupted job recovery |
| Installed Codex check | Real local binary initialized and returned an empty project-filtered thread list using an isolated CODEX_HOME. No user's normal profile read and no model request sent. This does not exercise real historical turns. |
| Browser | Four Chromium workflows against real SQLite/HTTP: review/save/export, merge with conflict choice, mobile/dark navigation, unauthenticated entry; source text is escaped |
| Package | Tarball installed globally into a fresh temporary prefix with anonymous npm configuration; global command and npm exec/npx invocation, CLI, demo, packet, receipt, stop/restart, bundled HTML/logo and unauthorized API checks |
| CI | Node 22.16 and 24 on Linux, Node 24 on macOS/Windows; Chromium on Linux; existing Python checks retained |
| Visual evidence | Reproducible screenshots and GIF from the actual local UI with synthetic demo data; light desktop and dark mobile inspected |

## Pending before stable 1.0

- Live, version-recorded history capture against all four harness installations, including representative large sessions.
- A paid Nebius proposal run with a model available to the maintainer. Fixture tests prove request/validation behavior, not a model's reasoning quality.
- Independent cross-harness task-result evaluation: identical starting repo, held-out task, Markdown baseline versus reviewed packet, receiver run, artifact/test verifier, and reported cost/latency. No quality uplift number is claimed in this beta.

## Release review

The complete product and packaging fix reached `main` through [PR #10](https://github.com/mahekara28/context-relay/pull/10), preserving the stacked release history. [PR #11](https://github.com/mahekara28/context-relay/pull/11) added the public website. npm distribution uses the `beta` tag and the [publication procedure](NPM_PUBLISHING.md); registry installation must be verified before its documentation is deployed. Published commit history stays intact.

The beta is a complete local review-and-handoff workflow. It does not restore hidden model state, move files across machines, claim uncaptured native history is current, or treat accepted handoffs as successful task execution.
