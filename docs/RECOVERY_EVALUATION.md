# Test evidence recovery with a real local model

`examples/evaluate_recovery.py` measures whether an agent can recover an earlier record and whether it avoids presenting an estimate as a confirmed fact. It uses a model already installed in local Ollama. It does not download weights, contact a hosted model, or execute model-proposed tools.

Read the [actual v0.6 results](../evaluations/RECOVERY-v0.6.md) before drawing an improvement claim. The final recovery run completed **0/2** tasks under its strict answer-and-citation requirements; every raw response and earlier iteration is retained.

## Run it

From the repository directory, inspect the prepared first requests without making model calls:

```sh
python examples/evaluate_recovery.py --model YOUR_INSTALLED_MODEL --dry-run --output evaluations/recovery-dry-run.json
```

Then run the actual evaluation:

```sh
python examples/evaluate_recovery.py --model YOUR_INSTALLED_MODEL --output evaluations/recovery-results.json
```

Choose a local model that supports JSON-schema output. `--timeout` sets the per-request timeout in seconds, default 60 and maximum 600. `--port` selects the local Ollama port, default 11434. The script verifies that the selected model is installed and has local GGUF metadata before requesting inference. It uses loopback HTTP, disables proxies and redirects, and never issues model-download requests.

A dry run stops immediately after each first request is prepared. It stores no invented model response or success score and does not contact Ollama to check its model list.

## Two fixed tasks, three conditions

The fixtures are deliberately small and synthetic:

- **An earlier loading-dock assignment.** A Cobalt shipment manifest contains the requested dock. Six later administrative exchanges compete for the context budget. The task asks for the dock's exact value.
- **An unconfirmed arrival.** A Cedar parcel record contains an estimate but explicitly has no confirmed actual arrival. The correct outcome is `unknown`, even when the estimate is recovered.

Every condition receives the same original conversation, the same application fact (`workflow=logistics`), the same grounding contract, and the same source-label format. No condition receives an answer through application state.

| Condition | Message character budget | Recovery rounds |
| --- | ---: | ---: |
| Full-history reference | 100,000 | 0 |
| Bounded without recovery | 3,000 | 0 |
| Bounded with recovery | 3,000 | Up to 2 |

All conditions use `recent_turns=1` and `retrieve_turns=0`. The two bounded conditions differ only in whether the model may make another request after asking for evidence or failing a citation check. They use the same context-selection algorithm. In the shipped fixtures, the initial bounded requests omit the early record while the full-history reference includes it.

There are six cases. Each condition without recovery makes one model call per task. Recovery can make up to three per task, for at most ten model calls overall. Recovery calls must still fit the same 3,000-character message limit. Finding useful evidence does not guarantee that it can fit; a budget failure is retained in the result.

## How answers are checked

The model returns either a request for missing evidence or an answer with original-message citations. Relay checks the snapshot identity, whether a cited source was actually available, and whether the quoted text exists verbatim in that source. If appropriate and allowed, its recovery loop retrieves matching omitted messages and prepares another request.

The current loop can also search using the original user question after a missing or invalid citation. Its per-attempt `lookup_origin` distinguishes a model query, a quote referring to an omitted source, and this automatic fallback. Earlier iteration results are retained separately; the fixture data, expected answers, and scoring rules were not tuned to the model's responses.

The evaluator then performs a separate check against a fixed, human-authored expected value. A task is counted as completed only when:

- The returned value exactly matches the fixture's expected value and Relay reports `cited`; or
- The expected value is `unknown` and Relay reports `abstained`.

An answer passing the library's citation check can still fail the expected-value check. A real quote is not proof that the quote supports the answer. The evaluator records those two outcomes separately; it does not relabel an unresolved request as success.

The response schema constrains `history_id` to the ID already present in the actual prepared state. This assistance is identical in all conditions and is disclosed in the report. It avoids making the comparison primarily a test of copying a 64-character identifier. **The schema supplies neither the expected answer nor the correct source ID.** All requests and schemas are saved so this can be inspected.

## What is recorded

The JSON report contains complete synthetic fixtures, expected values, a fixture hash, configuration, model metadata, and every actual request and raw response. It also retains the final library status, citation-check report, recovery attempts, exact-value outcome, call count, prompt/output token counts returned by Ollama, and timings. It checkpoints before and after each call, so errors and partial runs remain visible.

Inference uses temperature 0, seed 42, `num_ctx=8192`, and `num_predict=256`. This is a message-character-budget comparison; model tokens are measured from completed responses. The script uses Ollama's [JSON-schema structured output](https://docs.ollama.com/capabilities/structured-outputs) and the token/timing fields documented for [`POST /api/chat`](https://docs.ollama.com/api/chat).

Mode order rotates between fixtures. There is no warmup or repeated sampling. A nonzero exit code indicates an execution/setup error, not merely an incorrect or unresolved model answer. Inspect `task_completed`, the library status, and the raw responses to assess answer quality.

## What these results cannot establish

Two authored examples are a smoke evaluation, not evidence of performance across organizations or models. The full-history reference uses a larger budget, and recovery may spend more total calls, tokens, and time than the one-call control. Model loading, caching, hardware, and ordering affect latency. Fresh session IDs also appear in the captured requests.

This evaluation does not test real external transactions, automatic correction recognition, adversarial prompt injection, or semantic proof of an answer. Keep unsuccessful outputs in published reports. A useful next evaluation would use independently collected workflows, more models and seeds, and separate measurements of task completion, false answers, abstentions, total tokens, and total time.
