# npm beta verification

Release candidate: **0.3.0-beta.1**. These results describe implementation checks, not a claim of improved model performance.

| Gate | Evidence |
| --- | --- |
| Core/transport/provider tests | 19 Node tests, including transactional rollback, provenance, merge decisions, UTF-8 budget, digest tampering, stale sources/files, project/recipient isolation, auth/origin/host rejection, real MCP stdio client and malformed model responses |
| Adapter checks | Synthetic Claude JSONL and subagent identity; real local HTTP server for OpenCode; JSON-RPC subprocess; Goose format and unavailable-executable cases; reversible JSON/TOML configuration; interrupted job recovery |
| Installed Codex check | Real local binary initialized and returned an empty project-filtered thread list using an isolated CODEX_HOME. No user's normal profile read and no model request sent. This does not exercise real historical turns. |
| Browser | Four Chromium workflows against real SQLite/HTTP: review/save/export, merge with conflict choice, mobile/dark navigation, unauthenticated entry; source text is escaped |
| Package | Tarball installed into a fresh temporary prefix; CLI, demo, packet, receipt, stop/restart, bundled HTML/logo and unauthorized API checks |
| CI | Node 22.16 and 24 on Linux, Node 24 on macOS/Windows; Chromium on Linux; existing Python checks retained |
| Visual evidence | Reproducible screenshots and GIF from the actual local UI with synthetic demo data; light desktop and dark mobile inspected |

## Pending before stable 1.0

- Live, version-recorded history capture against all four harness installations, including representative large sessions.
- A paid Nebius proposal run with a model available to the maintainer. Fixture tests prove request/validation behavior, not a model's reasoning quality.
- Independent cross-harness task-result evaluation: identical starting repo, held-out task, Markdown baseline versus reviewed packet, receiver run, artifact/test verifier, and reported cost/latency. No quality uplift number is claimed in this beta.
- npm scope ownership/access check and a deliberate publish action. Until then, install the tarball from this checkout or the CI artifact.

## Release review

The runtime PR reached main; the adapter and workspace PRs were merged into intermediate stacked bases. The final `release/npm-beta` PR brings the complete product and packaging fix into main, preserving published history. Verify its **final head** checks and npm artifact before merging. Do not rewrite open-PR history to manufacture contribution counts.

The beta is a complete local review-and-handoff workflow. It does not restore hidden model state, move files across machines, claim uncaptured native history is current, or treat accepted handoffs as successful task execution.
