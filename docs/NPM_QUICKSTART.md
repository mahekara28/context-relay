# Node CLI and local context service

> This checkout includes the unpublished `0.4.0-dev.0` thread map. For local development setup and automatic discovery behavior, see [Thread map](THREAD_MAP.md). The registry instructions below describe the published beta.

The npm runtime is available as the public `@mahekara28/context-relay` beta. It requires
**Node 22.16 or newer** and npm, with no Python prerequisite. Node may print an
experimental SQLite warning on older supported releases. The existing Python
API continues to use its own database and release version.

## Install from npm

```sh
npm install --global @mahekara28/context-relay@beta
relay --help
relay ui
```

Use `@0.3.0-beta.1` instead of `@beta` to install this exact version. Beta updates
are explicit: rerun the installation command when you want to update.

### Try with npx

```sh
npx @mahekara28/context-relay@beta demo
npx @mahekara28/context-relay@beta ui
```

`npx` downloads the published package into npm's cache and runs its `relay`
executable. It requires no global installation or Git checkout. Use the full
scoped name; the unscoped `context-relay` package is a different project.
Global and npx invocations use the same default local data directory. Add
`--data-dir /path/to/workspace` to keep a separate profile.

To stop the service and remove a global installation:

```sh
relay stop
npm uninstall --global @mahekara28/context-relay
```

Uninstalling the CLI does not delete your saved local workspace.

### Build from source (contributors)

```sh
git clone https://github.com/mahekara28/context-relay.git
cd context-relay
npm ci --ignore-scripts
npm run build
npm pack
npm install --global ./mahekara28-context-relay-0.3.0-beta.1.tgz
```

Alternatively use `node dist/cli.js` from the checkout wherever this guide uses
`relay`. See the [publication procedure](releases/NPM_PUBLISHING.md) for maintainers.

## Try the local workflow

```sh
relay init
relay demo
relay sessions
```

`demo` creates clearly labeled synthetic webhook conversations in the Relay data
directory. It prints a checkpoint ID and an offered handoff ID. Substitute those
IDs below:

```sh
relay checkpoint packet --id CHECKPOINT_ID
relay handoff show --id HANDOFF_ID
relay handoff accept --id HANDOFF_ID --recipient codex
relay status
relay stop
```

Acceptance checks the named recipient, packet digest, captured source revisions,
selected artifact contents and Git HEAD. It does not prove the coding task is
complete. New captured events require a fresh checkpoint and offer.

## Register your own project

```sh
relay project add --root /absolute/path/to/project
relay project list
relay import --file session.json
relay checkpoint create --file checkpoint.json
```

A normalized session contains `projectId`, `harness` (`claude`, `codex`, `opencode`,
`goose` or `manual`), `externalId`, `title`, and `events`. Each event has a stable
`id`, `role`, and `text`; optional fields are `kind` and `timestamp`. Re-importing
identical events is safe. Reusing an event ID for changed content fails the whole
batch. Event text receives best-effort credential redaction before persistence.

A checkpoint contains `projectId`, `title`, `goal`, `sources` (session ID and
expected revision), and optionally `instructions`, `criteria`, `openWork`,
`evidence`, `files`, `parents`, and `resolutions`. Each instruction has a `key`,
`text`, and source event references. Inspect the exported Zod schemas in
[`model.ts`](../packages/relay/src/model.ts) for the complete contract. Files are
project-relative; references do not transfer their bytes.

The packet budget is **UTF-8 bytes**, not model tokens. Required instructions,
goal, criteria and open work must fit. Optional evidence can be omitted with a
count. The contract is `relay.packet/v1`, independently versioned from the
Python task packet.

## MCP

Register the project first, then configure your MCP client to run:

```sh
relay mcp --project PROJECT_ID --harness codex
```

The stdio bridge talks to the same local service as the CLI. It is scoped to that
project and binds handoff acceptance to the configured harness label. Tools:
`relay_sessions`, `relay_session_events`, `relay_checkpoints`, `relay_packet`,
`relay_handoff_inspect`, and `relay_handoff_accept`. A checkpoint resource and
`continue_task` prompt are also available. MCP does not capture transcripts or
inject context automatically. Source text remains untrusted evidence.

## Storage and lifecycle

State is local plaintext SQLite in `~/.context-relay`. Use `--data-dir PATH` or
`CONTEXT_RELAY_HOME` for an isolated profile. Newly created directories and token
files have private modes where supported by the OS. Keep the profile private;
local harness labels are not remote-user authentication.

The service binds only to `127.0.0.1` on an available port. API requests require a
local bearer token and reject foreign Host/Origin headers. The CLI reads its
private connection descriptor. `relay ui` prints a local browser URL containing
the access token in its fragment; treat that URL as a credential. Do not expose
the service through a public proxy. `relay serve` runs in the foreground;
`relay init` starts a background process. `relay doctor` can recover a lock only
when its recorded process no longer exists. It never kills another process.

## Release gates

```sh
npm test
npm run test:package
```

The transport suite uses an actual MCP SDK stdio client. The package gate installs
the tarball into a temporary directory and checks startup, checkpoint creation,
acceptance, shutdown, persisted state and restart. CI covers Linux, macOS and
Windows. Live model evaluation remains separate from these offline checks.

## Browser workspace and connections

Run `relay ui` for the bundled local UI, or `relay ui --no-open` to print its local access URL. Follow the [workspace guide](WORKSPACE.md) for source review, merge decisions, handoff export and optional Nebius drafts. The [connection guide](HARNESS_CONNECTIONS.md) documents native history capture separately from MCP access. Use `relay launch --id HANDOFF_ID` for a configured Claude Code/Codex interactive continuation; use `relay handoff export --id HANDOFF_ID` for Markdown.
