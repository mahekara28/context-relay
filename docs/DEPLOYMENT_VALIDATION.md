> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Validate a deployment before relying on it

Relay v0.17 includes executable checks for shared local storage and real provider responses. These are useful acceptance checks, not evidence of an organisational pilot. They do not send customer data, install models, buy API credits or run a hosted service automatically.

## Version 0.19 local storage and action adapters

The v0.19 scaling report (local-only reference; omitted from public distribution)
records six fresh runs: one, four and eight independent Python processes, each
using either a shared SQLite file or independent named-conversation files.
All 208 jobs completed: 208 incorrect proposals were blocked, 208 correct effects
were checked on disk, and 208 replays were blocked (624 action attempts).
No worker was missing; no busy result or unexpected error was omitted. Source
fingerprints remained unchanged. Separate tests deliberately hold locks to verify
bounded `StoreBusy` errors, including a commit failure after an external effect.

At eight workers, p99 for a whole three-action job was 629.32 ms in the shared
file run and 49.85 ms in the named-file run. These are short, single-machine
observations, not model latencies, a service objective or distributed proof.
The earlier pre-diagnostic-fix run remains in `local-scaling-v0.19-verified.json`;
the linked final report uses the corrected receipt code. Named files reduce
contention between independent conversations; they do not
make concurrent writes to the same conversation free of contention.

Reproduce with a new report filename:

```sh
python scripts/evaluate_local_scaling_v019.py --directory another-scaling-data --output another-scaling-run.json
```

[Provider actions and bounded wait handling](PROVIDER_ACTIONS.md) documents
supported complete call formats and the distinction between failures before a
transaction and failures where an effect may already have happened. Do not
blindly retry an external action after the latter.

## Shared SQLite exercise

Run from the source release with Relay installed, or use the script directly; it locates the sibling source tree:

```sh
python examples/evaluate_shared_deployment.py --output shared-4workers.json --workers 4 --sessions-per-worker 36
```

This starts four **independent Python processes using spawn**, including on Windows. All action sessions use one SQLite file with separate connections. Each session has its own protected source value. The runner checks that:

- A value from a different session is blocked before its callback runs.
- The right value writes one real temporary file and one successful receipt.
- Reusing the old captured turn is blocked.
- Contenders using the same session and revision produce exactly one callback, one file and one successful receipt.
- A callback that writes and then raises creates one failed receipt; Relay does not repair or repeat it, and the old turn cannot execute again.
- A different owner cannot resume a session through `SessionManager`, and a denied host authorization prevents access.

The owner checks use `SessionManager`'s separate owner databases. They are **not** a claim that raw `ContextGuard` performs authentication. Keep the database and guard inside the trusted server, and derive `Principal` from your authentication system, never from a model proposal.

Results contain source hashes, Python/SQLite/platform versions, observed errors, individual invariants, completed action attempts per second, and p50/p95/p99 nearest-rank latency. An action attempt includes a checked rejection as well as a successful dispatch. Latency samples are mixed operation groups: contention; isolated open/block/write/replay; failure/replay; and ownership denials. They are not individual SQL query timings or model response latency. Setup is reported separately; throughput is reported both with process startup and after the synchronized start gate. A missing or crashed worker fails the run.

The defaults are four workers and 25 sessions each. Bounds are 1-16 workers, 1-250 sessions per worker, at most 2,000 isolated sessions, and a 1-300 second child-process deadline. Temporary databases and effect files are checked before cleanup; the retained report contains their verification counts and outcomes. A new output filename is required, so a later run cannot overwrite earlier evidence.

SQLite serializes writes. Relay holds a write transaction while running a checked callback, so keep callbacks short. These measurements do not establish a distributed capacity limit or service-level objective. A process crash between an external effect and its receipt remains possible; the external system still needs its own idempotency or transaction control. Upgrade all workers sharing a store when deploying a new policy capability.

## Real provider content checks

The runner is inactive unless `--run-live` is supplied:

```sh
python examples/validate_live_provider.py --output provider-not-run.json
```

It records `NOT_RUN`, with zero calls. Missing hosted credentials record `NOT_RUN_MISSING_CREDENTIAL`, never `PASSED`.

To use an already-running free local Ollama service through its OpenAI-compatible endpoint:

```sh
python examples/validate_live_provider.py --run-live --provider local --base-url http://127.0.0.1:11434/v1 --model qwen2.5:1.5b --max-calls 3 --max-output-tokens 256 --timeout 60 --output local-content-check.json
```

This requires the optional `openai` SDK and a model you already have. No model download is attempted. Choose your own explicit installed model ID. A loopback endpoint may itself proxy a hosted service: the operator must verify the model runs locally and whether that service bills requests; the URL alone cannot prove this. Local URLs must contain a literal loopback IP, explicit port and `/v1` path, without credentials, query parameters or redirects. A local service that requires a credential can use `RELAY_LOCAL_API_KEY`; hosted API credentials are never reused locally.

Hosted examples require your existing account, explicit model choice and environment credential, and may incur provider charges:

```sh
python examples/validate_live_provider.py --run-live --provider openai --model YOUR_MODEL_ID --max-calls 3 --output openai-content-check.json
python examples/validate_live_provider.py --run-live --provider anthropic --model YOUR_MODEL_ID --max-calls 3 --output anthropic-content-check.json
```

Set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in your environment. Do not put credentials in a command, report or source file. The hosted client factory is shared with [the native replay smoke runner](../scripts/smoke_live_provider.py). It uses fixed official endpoints, no environment proxies or redirects, and zero SDK retries. The content runner also disables Relay output retries. Calls are bounded to 1-12; the default is three synthetic cases, with 512 maximum output tokens per call and a 30-second SDK transport timeout. The timeout is not a strict process wall-clock deadline. A transport error stops the remaining calls without retry.

The runner exercises the actual Relay provider adapter and `OutputPolicy` with exact protected rows. Its fixed cases contain names, integer amounts, IDs, Unicode and instruction-looking text inside source data. Valid JSON with changed data is blocked. Truncated or unsupported responses are blocked. A block counts as incomplete model work, not a successful answer.

Reports retain request hashes, response token usage when available, latency, check outcomes and error **types**. They do not retain raw responses, credentials, provider error messages, reasoning, signatures or source/customer documents. A provider reports `PASSED` only when every planned response is accepted and the source tree remains unchanged. A model mistake yields `COMPLETED_WITH_BLOCKED_OUTPUT`; a transport or runner error yields `ERROR`. This does not test native reasoning or tool history, streaming, long conversations, open-ended factual accuracy or an organisation's workload. Use [the existing native replay smoke runner](../scripts/smoke_live_provider.py) separately for its supported history cycles.

OpenAI request parameters are documented in the [official Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create): this runner supplies explicit `model`, `input`, `store=False` and `max_output_tokens` through Relay's text adapter. Local compatibility is not evidence of hosted-provider behavior.

## Verified v0.17 measurements

These measurements were rerun after the final bound-value alias correction. On this Windows machine, all three profiles passed with unchanged source hashes. Each used 144 isolated sessions plus a shared contested session and one callback-failure session per worker. There were **1,361 checked action attempts** across the three profiles, zero unexpected errors, and zero failed invariants. Each profile produced exactly one contested callback, file and successful receipt.

| Workers / report | Isolated sessions | Action attempts | Actions/s, including spawn | Group p50 ms | Group p95 ms | Group p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 (local-only reference; omitted from public distribution) | 144 | 437 | 100.8 | 24.7 | 30.8 | 38.4 |
| 4 (local-only reference; omitted from public distribution) | 144 | 452 | 113.6 | 31.6 | 104.1 | 324.2 |
| 8 (local-only reference; omitted from public distribution) | 144 | 472 | 99.1 | 30.0 | 287.6 | 1406.2 |

The percentiles describe the mixed operation groups explained above. The p99 tail grew from 38.4 ms at one worker to 1.41 seconds at eight workers, despite every invariant passing. More workers did not produce proportional throughput. This single-machine result supports the checked local concurrency behavior; it does not establish a production capacity target. Reproduce these profiles with `(workers, sessions-per-worker)` set to `(1, 144)`, `(4, 36)` and `(8, 18)`, always using new output filenames. These are short individual measurements subject to machine activity and cache state; changes from the earlier candidate measurements cannot be attributed to the alias fix.

The verified local provider report (local-only reference; omitted from public distribution) passed all three synthetic checks using the installed local `qwen2.5:1.5b` model through the OpenAI-compatible endpoint. All three actual responses preserved the declared rows. There were three requests, zero retries, 1,287 prompt tokens and 201 completion tokens as reported by the local service. Recorded provider durations were 15.34, 7.72 and 9.81 seconds; these are observations, not a service latency promise. The report records unchanged source hashes and version `0.17.0`.

All four verified reports contain the same complete `source_sha256` map, matching the final source files. SHA-256 of that map serialized as UTF-8 JSON with sorted keys and compact separators is `51fd2accba342b8648d73f993d08f34bceaf339a3a2230b0acd039fa91322195`. The per-file hashes in the reports distinguish this source from the earlier candidate with the same version number.

The hosted preflight report (local-only reference; omitted from public distribution) recorded `credential_present: false` for both OpenAI and Anthropic, `model: null`, `NOT_RUN`, and zero SDK/provider requests. It checks availability at that time only. No hosted calls were added for this verification. Hosted-provider behavior remains unvalidated here, and there is still no organisational pilot.

## Retained development evidence

The reports whose names end in `-final.json` refer to the **earlier candidate before the bound-value alias fix**, not the final verified source: one worker (local-only reference; omitted from public distribution), four workers (local-only reference; omitted from public distribution), eight workers (local-only reference; omitted from public distribution), and three local provider responses (local-only reference; omitted from public distribution). They passed their recorded checks, but the separate completed-work evaluation subsequently found that repair scope comparison treated known bound equivalents such as `json` and `JSON` as different. The core comparison was corrected and all four checks above were rerun against the resulting source. Earlier reports remain unchanged.

The first local `qwen2.5:3b` attempt reached its 30-second transport timeout on its first request. The runner stopped with one attempted call and zero completed calls. That is a failed acceptance check, retained in the original report (local-only reference; omitted from public distribution); it is not hidden by a later run.

A second development run used local `qwen2.5:1.5b` with a 90-second timeout and 256-token output cap. All three real responses preserved the protected data, but source files changed during the run. Its report (local-only reference; omitted from public distribution) therefore records `SOURCE_CHANGED`, not a frozen release pass. The development runner snapshot (local-only reference; omitted from public distribution) is retained; the final runner also marks a live in-progress check as `RUNNING` instead of leaving its initial `NOT_RUN` label.

The preliminary shared-process run (local-only reference; omitted from public distribution) used four workers and 32 isolated sessions: all 116 action attempts met their expected outcomes. Its source was still the development tree; final release measurements are recorded separately.

A production pilot still requires a willing organisation, its actual baseline, representative workloads, agreed privacy handling and real observations. The existing [pilot workflow](../examples/pilot_workflow.py) and [pilot runner](../src/context_relay/pilot.py) can compare a supplied baseline with Relay. Record completed tasks, incorrect effects, human interventions, provider calls and latency; do not count a blocked task as completed. Neither synthetic tests nor a few local model responses can replace that evidence.
