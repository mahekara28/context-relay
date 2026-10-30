# Local workspace operations

## Start, inspect, stop

`relay ui` starts this profile's service if needed and opens its loopback browser URL. `relay ui --no-open` prints the same URL for manual opening. Treat that URL as a local access credential. The fragment is removed as soon as the page loads; API requests use an Authorization header.

`relay status` reports the service instance. `relay doctor` diagnoses the profile and can recover a lock whose process no longer exists. `relay stop` closes connections, drains capture jobs at source boundaries and closes SQLite. No other process is killed. Concurrent capture for the same connection is rejected. Service logs live under the data directory and do not include request bodies or provider keys.

Use a dedicated `--data-dir` or `CONTEXT_RELAY_HOME` for separate experiments. Keep the same profile option when launching CLI commands from the UI. Back up the entire data directory **after stopping the service**; SQLite may have WAL/SHM sidecars while running. A newer schema is rejected by an older binary. There is no downgrade migration.

## Review and continuation

1. Add an existing project directory and connect a harness.
2. Discover matching native sessions and choose which to capture.
3. Select event evidence in **Threads**, then open **Review handoff**.
4. Fill the goal and current instructions; each instruction needs a cited event. List acceptance criteria, open work and optional project-relative file paths.
5. Confirm your review, select the recipient and save. The checkpoint is immutable. A failed offer can leave the saved checkpoint available for a new review.
6. In **Handoffs**, copy the continuation prompt or export Markdown. For configured Claude Code/Codex, `relay launch --id HANDOFF_ID` starts a fresh interactive terminal session in the project. It preserves native approvals and does not accept the handoff automatically. On Windows, launch requires a native `.exe` configured with `--executable`; otherwise use the copied prompt.
7. The receiving agent inspects and accepts through MCP. Acceptance is not a successful test result.

Review drafts stay in browser memory, with a navigation-away warning. A page reload discards an unsaved draft. Replacing a draft or changing projects asks before discarding it. Browser state is not an authoritative checkpoint. The UI requires a new explicit review after editing fields or applying a model proposal.

**Freshness has a precise scope:** captured source revisions, selected file digests and Git HEAD. It cannot detect native messages that have not yet been captured. Capture again before a consequential handoff. A revision change blocks acceptance; review a new checkpoint rather than editing the old one. Captured character growth is not a model context-window measurement.

## Merge behavior

Select two or more checkpoints, then **Merge selected**. Relay combines references, criteria, open work and file paths. The first goal is a starting point for review. Conflicting instruction keys require a selected resolution; revise the resulting text and sources as needed. The new checkpoint records parents and resolutions. Original checkpoints, native histories and Git files are preserved.

A new review uses latest **captured** source revisions and checks current files when saved. Users must inspect differences; this is not an automatic semantic merge or Git merge.

## Nebius proposals

The provider is optional and keyless operation is the default. Supply a Token Factory key and a model ID available to your account through the service environment:

```sh
# Set NEBIUS_API_KEY securely in your shell/secret manager first.
export NEBIUS_MODEL='YOUR_AVAILABLE_MODEL_ID'
relay stop   # if this profile is already running
relay ui
```

On PowerShell use `$env:NEBIUS_MODEL = 'YOUR_AVAILABLE_MODEL_ID'`. Relay does not save the API key in SQLite, configuration JSON, browser state or logs. A service already running must restart to inherit new environment variables.

**Preview → explicit consent → request → schema/citation validation → review.** Only the selected, redacted source events are sent to `https://api.tokenfactory.nebius.com/v1/chat/completions`. Their exact payload is displayed before consent. No filenames, full history or unrelated sessions are added automatically. Model output is returned as a proposal, never as a checkpoint or acceptance.

Limits: 40 events, 48,000 UTF-8 input bytes, 3,000 requested output tokens, 30-second timeout, 128,000 response bytes, no automatic retries. A model must support OpenAI-compatible chat completions with JSON-object output. Truncation, malformed JSON, schema errors, duplicate instruction keys and citations outside the selection are rejected. Costs and availability depend on the selected model; Relay does not estimate them. Provider error bodies are not exposed. Sources: [Nebius Token Factory](https://dev.nebius.com/token-factory).

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| No sessions found | Check project root, explicit source, harness installation/API availability and the connection guide. |
| Changed source ID | The native history changed after capture. Export it under a distinct source identity; Relay refuses to rewrite old evidence silently. |
| Capture interrupted | Restart capture; already imported events are idempotent. Review partial errors. |
| Config conflict | Inspect the existing managed MCP entry. Relay refuses to overwrite external edits. |
| Authentication expired after a restart/port change | Run `relay ui` to open the current service URL. |
| Handoff is stale | Recapture, inspect changed source/files, and save a newly reviewed checkpoint. |
| Nebius not configured | Set both environment variables before starting the service; manual review remains available. |
| Browser cannot copy | Relay shows the prompt in a selectable text area. |

The local workspace is intended for one OS user. API tokens and 0700/0600 permissions protect local state where supported, but the database is not encrypted. The package has no cloud sync, multi-user tenancy, scheduled capture or automatic native thread rewriting.
