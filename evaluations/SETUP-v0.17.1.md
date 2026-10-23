> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Version 0.17.1: one basic setup path

This patch simplifies onboarding. `auto_connect(existing_client)` already enabled
basic text memory in v0.17.0; it has not been reimplemented or renamed. The
README now leads with that path, the beginner guide no longer installs v0.11,
and the wheel includes `python -m context_relay quickstart`.

The command runs two calls through the real default `auto_connect` path with a
fixed local stand-in. It reads the generated task-state packet received by that
function and checks the remembered format changes from CSV to JSON. It returns
zero only for that observed sequence. Storage is in memory and is closed at the
end. The runnable example uses this same implementation, avoiding another copy.
No SDK, account, model download, AI inference or network request is involved.

## Scope

The basic path still requires the full original append-only history, one
connection per conversation and requests through the returned connection. Memory
is lost on close unless storage is explicitly configured. Existing provider
options and charges remain the application's responsibility. Tool actions need
separate registered checks. The example does not claim support for native
reasoning, multimodal history or streaming.

Provider adapters, memory parsing, context preparation, content checks and repair
behavior are unchanged. Only package metadata, the CLI and its local demo helper
changed in the installed Python modules. Documentation and the example changed
alongside them. No runtime dependencies were added.

## Verification

- The full existing suite (local-only reference; omitted from public distribution) passed **1,173/1,173 tests**,
  zero skips, errors or failures, with identical before/after source/test hashes.
  Complete log (local-only reference; omitted from public distribution).
- The wheel was installed offline into a fresh directory outside the source tree.
  Its Python modules exactly matched source, it declared zero runtime
  dependencies, and **15/15 SDK/framework checks** passed with simulated
  transports. Installed report (local-only reference; omitted from public distribution).
- A separate installed-core check used Python with all optional SDKs absent.
  The quickstart observed `CSV -> JSON`, returned success and did not load the
  web server. Simulating an unchanged `CSV -> CSV` result returned a failure
  exit code instead of reporting success. Installed check log (local-only reference; omitted from public distribution).
- The updated documentation's local links resolve and all 18 Python snippets
  compile. Independent review confirmed the install path, lifecycle and scope.

No AI inference or hosted API call was made for this patch.
Earlier [v0.17 evidence](RELIABILITY-v0.17.md) remains evidence for that version;
this setup patch adds no model-quality, hosted-provider or organisational claim.
