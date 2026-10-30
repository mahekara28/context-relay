# Harness connections — npm release 0.2

Relay has two independent integration paths: **capture** reads native session history; **MCP** lets a destination agent inspect and accept a reviewed handoff. Installing an MCP server does not expose a harness's chat history.

| Harness | Capture path | MCP setup | Continuation |
| --- | --- | --- | --- |
| Claude Code | Project-filtered JSONL under `~/.claude/projects` | Project `.mcp.json` | Open a new chat and ask it to inspect the offered handoff |
| Codex | Read-only app-server JSON-RPC; paginated completed turns | Project `.codex/config.toml` | Open a trusted project and a new chat; request the handoff |
| OpenCode | Loopback session/messages HTTP API | Project `opencode.json` | Start a new session and request the handoff |
| Goose | `session list` and `session export --format json` | `.relay/goose-mcp.json` command manifest | Start Goose with the displayed stdio extension command |

These adapters have synthetic protocol/format tests. They are not a certification of every installed harness version. Native APIs and transcript formats can change; unsupported responses produce visible errors. Relay never edits native chat databases. Completed visible messages and tool results are captured; reasoning blocks and unfinished assistant turns are excluded.

## Connect and capture

```sh
relay project add --root /absolute/path/to/project
relay connect --project PROJECT_ID --harness claude
relay connections --project PROJECT_ID
relay discover --id CONNECTION_ID
relay sync --id CONNECTION_ID
```

Use `--capture-only` to leave MCP settings untouched. Use `--source /path/to/claude/projects` for an alternative transcript root, `--executable /path/to/codex` or `--executable /path/to/goose` for another binary, or `--source http://127.0.0.1:4096` for OpenCode. OpenCode must already be serving; if authentication is enabled, set `OPENCODE_SERVER_PASSWORD` (and optionally `OPENCODE_SERVER_USERNAME`) in the Relay service environment before starting it. Secrets are not stored in connection records.

Capture runs as a background job. The CLI waits for its final result and returns a nonzero exit code on a failed job or any session error. The API exposes progress, errors and cooperative cancellation at session boundaries. Restarted jobs are marked interrupted; recapture is idempotent. Histories are capped at 200 sessions per job, 16 MiB per source response and 20,000 normalized events per import. Changed events with previously captured IDs are rejected atomically rather than silently rewriting history.

Capturing is explicit: Relay does not scan your home directory until you connect and request discovery or capture. Project root matching prevents unrelated sessions from being imported. Review source material before exporting it; built-in credential redaction is best effort, not a secret scanner.

## Disconnect

```sh
relay disconnect --id CONNECTION_ID
```

Relay removes only its recorded MCP entry. Other settings remain. If that entry was edited externally, disconnect refuses to overwrite it. Captured history and checkpoints remain available. Reconnecting with `--capture-only` removes the previously managed MCP entry. Goose's manifest is a command reference, not an automatic change to its global extension registry.

## Sources and compatibility contract

- [Claude Code hooks and transcript paths](https://code.claude.com/docs/en/hooks)
- [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server): `initialize`, `thread/list`, `thread/read`, `thread/turns/list` with `itemsView: full`.
- [OpenCode SDK and session API](https://opencode.ai/docs/sdk/): session metadata and completed message parts.
- [Goose CLI commands](https://github.com/aaif-goose/goose/blob/main/documentation/docs/guides/goose-cli-commands.md): JSON list/export support is required.

Relay does not claim to restore hidden model state, automatically merge Git changes, or move files between machines. A handoff carries reviewed instructions, references, file digests and pending work. The receiving agent still verifies its environment and performs the work.
