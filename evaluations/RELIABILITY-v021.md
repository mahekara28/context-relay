# Reproducing the v0.21 checks

The current results and retained failures are summarized in
[VALIDATION.md](../docs/VALIDATION.md). These are development checks, not an
organisational trial, representative accuracy estimate or performance claim.
Keep generated reports and application data private unless reviewed for release.

## Core and installed package

Install the wheel in a separate Python environment, then run:

```sh
python -m context_relay quickstart
python -m unittest discover -s tests -v
```

Without optional SDKs, their tests skip. The published validation environment
is specified by `requirements-validation.txt` and its constraints file. Its
SDK tests intercept HTTP transports; they do not call hosted models. On Windows,
the POSIX permission check skips; on POSIX, the Windows DACL check skips.
The local validation used Windows and Python 3.12.14. Other CI jobs are configured,
not claimed to have run.

## Matched framework comparison

With the optional validation environment installed, from the repository root:

```sh
python evaluations/compare_framework_v020.py --data-dir evaluations/my-framework-data --output evaluations/my-framework-report.json
```

This uses the unchanged v0.20 fixture with the installed v0.21 library. Both
arms run real LangGraph memory, identical normalized instructions and recorded
proposals, and the same application destination/freshness checks. One intentionally
wrong trusted source is expected to produce a wrong result in both arms. It
remains a failure of the requested business outcome, not a success because a
test expected it.

## Long-session mechanism

```sh
python evaluations/run_workflow_continuation_v021.py --directory evaluations/my-continuation --operations 96
```

The two arms use the same guarded workflow with fresh-segment handoff enabled
or disabled. Both write real local files from scripted proposals. All requests,
failures, interrupted writes, explicit host reconciliations, task completions and
restarts remain in the report. This comparison measures a bounded continuation
mechanism, not language understanding or an alternative summarizer. It uses a
character limit; it does not establish a model's token-window limit.

## Complete file example: scripted and actual local model

Choose an existing file as the artifact. The runner reads its bytes, preserves
a local snapshot, and freezes the requests and explicit host review choices
before execution. Output directories must be new.

```sh
python evaluations/run_reliability_pilot_v021.py --directory evaluations/my-scripted-pilot --mode scripted --release 0.21.0a1 --artifact dist/context_relay_local-0.21.0a1-py3-none-any.whl
python evaluations/run_reliability_pilot_v021.py --directory evaluations/my-local-pilot --mode ollama --release 0.21.0a1 --artifact dist/context_relay_local-0.21.0a1-py3-none-any.whl
```

Ollama mode requires an already-running local Ollama endpoint, the optional
OpenAI SDK, and the already-installed `qwen2.5:1.5b` model. The runner downloads
nothing and refuses a missing or marked cloud model. It uses 384 output tokens,
temperature zero, a 120-second timeout and no SDK retries. There are eight
possible inference attempts across nine requests; one review is deliberately
withheld. The scripted profile is separate and has no actual inference.

Reports count correct completed exports separately from blocked, unfinished,
missing and wrong results. They retain provider attempts, tool starts, raw
responses, actual files, deterministic repairs, history transitions and restart
behavior. Approvals are frozen host choices, not autonomous language extraction.
The host explicitly finishes each verified independent export, which can request
a fresh history segment before its size threshold. Unknown effects stop later
cases rather than being retried automatically.

The CLI prints accounting rather than declaring universal success. Inspect
`report.json`, every error and the actual output files. A crash before the report
is finalized can leave partial evidence. Raw journals contain local paths and
conversation data and are intentionally excluded from the public distribution.
