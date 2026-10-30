<picture>
  <img src="docs/assets/workspace/hero.svg" alt="Context Relay — keep the work, change the thread. A local CLI, workspace and MCP server for coding context." width="100%" />
</picture>

<p align="center">
  <a href="https://github.com/mahekara28/context-relay/actions/workflows/npm.yml"><img src="https://github.com/mahekara28/context-relay/actions/workflows/npm.yml/badge.svg" alt="npm product checks" /></a>
  <a href="package.json"><img src="https://img.shields.io/badge/Node-22.16%2B-456b55?labelColor=252d29" alt="Node 22.16 or newer" /></a>
  <a href="docs/releases/NPM_RELEASE_PLAN.md"><img src="https://img.shields.io/badge/release-0.3.0--beta.1-c84429?labelColor=252d29" alt="Release 0.3.0 beta 1" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-68716b?labelColor=252d29" alt="MIT license" /></a>
</p>

<p align="center"><strong>Your next coding session should inherit the decisions, not the confusion.</strong></p>

<p align="center">
  <a href="#why-context-relay">Why Relay</a> · <a href="#try-it-locally">Install</a> · <a href="#the-workflow">Workflow</a> · <a href="#where-mcp-fits">Architecture</a> · <a href="#what-is-verified">Verification</a>
</p>

## Why Context Relay?

You've spent hours in a coding thread. Requirements changed. A tool failed. The agent tried three approaches. The context keeps growing, and starting a fresh thread means explaining everything again.

**Context Relay turns that history into a reviewed handoff.** Find a session, select its evidence, capture the current instructions and open work, then continue in a fresh thread—even in another harness.

- **See the history.** Browse captured threads from Claude Code, Codex, OpenCode and Goose, with visible source events and character growth.
- **Keep the corrections.** Review instructions beside the evidence they cite. Merge checkpoints with explicit conflict choices.
- **Continue with a receipt.** Offer an immutable packet to a destination agent through MCP, or export readable Markdown. Check source revisions, recorded files and Git HEAD before acceptance.

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/workspace/threads.png" />
  <source media="(max-width: 600px)" srcset="docs/assets/workspace/mobile-dark.png" />
  <img src="docs/assets/workspace/workflow.gif" alt="Real local workspace: browse a Claude Code thread, review a corrected seven-day retention instruction, offer a Codex handoff, and inspect the architecture." width="100%" />
</picture>
<p align="center"><sub>Actual npm workspace, synthetic example data. No model calls or task execution in this demo.<br /><a href="docs/assets/workspace/README.md">Demo description</a> · <a href="docs/assets/workspace/threads.png">Still image</a> · <a href="scripts/npm/render-demo.mjs">Reproduce the capture</a></sub></p>

## Try it locally

**Node 22.16+ · npm · no Python or model API key required**

The npm package is prepared as `@mahekara28/context-relay`. **It is not published to the npm registry yet.** Install the beta from this checkout:

```sh
git clone https://github.com/mahekara28/context-relay.git
cd context-relay
# While the final beta PR is open:
git switch release/npm-beta
npm ci
npm pack
npm install --global ./mahekara28-context-relay-0.3.0-beta.1.tgz

relay demo
relay ui
```

The package includes the compiled CLI, local service, MCP server, SQLite store and UI assets. After installation, Relay needs no frontend build step or CDN. `relay demo` creates explicitly synthetic sessions in its own demo project. `relay stop` stops the local service.

Use `--data-dir /path/to/workspace` on any command for a separate profile. The default is `~/.context-relay`. [Installation and CLI reference →](docs/NPM_QUICKSTART.md)

## The workflow

| Step | What happens |
| --- | --- |
| **1. Connect** | Register a project and connect a harness. Relay can add a project MCP entry while preserving unrelated settings. |
| **2. Capture** | Discover matching sessions and choose what to import. Background jobs expose progress, failures and cancellation. |
| **3. Review** | Select source events. Write the current goal, cited instructions, acceptance criteria and open work. Optionally generate a draft with Nebius. |
| **4. Merge** | Combine existing checkpoints into a new snapshot. Resolve conflicting instruction keys explicitly. |
| **5. Continue** | Offer the packet to a harness. Open a fresh session, inspect the handoff and accept it through MCP; or use a Markdown export. |

```sh
relay project add --root /absolute/path/to/project
relay connect --project PROJECT_ID --harness claude
relay discover --id CONNECTION_ID
relay sync --id CONNECTION_ID
relay ui
```

For a configured Claude Code or Codex destination, `relay launch --id HANDOFF_ID` opens a fresh interactive session with the continuation prompt and the harness's normal approvals. OpenCode and Goose use the copied prompt or export. Relay never writes into native chat databases.

## Where MCP fits

<img src="docs/assets/workspace/architecture.svg" alt="Harness-specific capture and MCP context access connect to one local service. SQLite stores reviewed state; optional Nebius drafts require review." width="100%" />

**Capture and MCP solve different parts of the workflow.** Native adapters read history. MCP gives agents a standard way to inspect Relay's context and accept an addressed handoff. Installing MCP alone does not make a harness's conversation history available.

| Harness | Capture | MCP configuration | Fresh continuation |
| --- | --- | --- | --- |
| Claude Code | Project-filtered JSONL | `.mcp.json` | Interactive CLI launch or copied prompt |
| Codex | App-server, completed turns | `.codex/config.toml` | Interactive CLI launch or copied prompt |
| OpenCode | Loopback session/messages API | `opencode.json` | Copied prompt or Markdown |
| Goose | CLI JSON list/export | Project extension command manifest | Copied prompt or Markdown |

All four have implemented capture adapters. Native format coverage and limits are explicit in the [connection guide](docs/HARNESS_CONNECTIONS.md); they are not a claim that every harness version has been live-certified.

### Optional Nebius Token Factory

Manual checkpoints work without a key. To request model-assisted drafts, set `NEBIUS_API_KEY` and `NEBIUS_MODEL` in the service environment and restart Relay. The UI previews the exact selected, redacted evidence before sending it. Responses must pass schema and citation validation, then return to the editor for review.

No automatic summarization loop, hidden provider fallback or model-written authoritative state. [Provider setup and data flow →](docs/WORKSPACE.md#nebius-proposals)

## What is verified

- **19 Node tests:** transactional imports, provenance, explicit merge conflicts, packet budgets, stale source/file rejection, recipient checks, adapters, provider validation and launch planning.
- **Real transports:** an MCP stdio client, a JSON-RPC subprocess, a loopback HTTP capture source and an isolated installed Codex app-server initialization/listing check.
- **4 Chromium workflows:** source review → handoff → export, conflict resolution, mobile/dark navigation and unauthenticated entry.
- **Installed package gates:** packed-tarball installation, CLI/service lifecycle, bundled UI assets, authentication and restart persistence on Linux, macOS and Windows CI.

The model quality benchmark remains pending: no live Nebius completion or cross-harness coding result is claimed. Acceptance proves delivery, not successful task completion. [Detailed verification and release boundaries →](docs/releases/NPM_VERIFICATION.md)

## Local by default

Relay binds to `127.0.0.1`, authenticates API requests and rejects foreign origins and host headers. The browser token is passed in a URL fragment, removed immediately and retained in the current tab's session storage. Drafts stay in memory until saved. Files are referenced by path and digest; they are not transferred.

Capture and discovery are explicit. Recognized credentials are redacted before persistence, but this is best effort: inspect sensitive material before sharing it. Nebius receives only evidence you explicitly select and approve. [Workspace operations →](docs/WORKSPACE.md)

## Develop and contribute

```sh
npm ci
npm run typecheck
npm test
npm run test:package
npx playwright install chromium
npm run test:browser
```

The TypeScript product lives in [`packages/relay`](packages/relay). The existing Python research/API implementation remains available with its own database and versioning; see the [Python reference](README.python.md). No automatic migration or shared writable database is implied.

See the [release series](docs/releases/NPM_RELEASE_PLAN.md), [contribution guide](CONTRIBUTING.md) and [license](LICENSE). Harness logos are bundled from the MIT-licensed Lobe Icons package with [attribution](packages/relay/ui/assets/THIRD_PARTY_LICENSE.txt); product names identify integrations and do not imply endorsement.
