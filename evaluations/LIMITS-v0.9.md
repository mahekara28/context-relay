> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# v0.9: remaining-limit checks, including failures

The final source passes **649 automated tests**. Those tests verify library
contracts using controlled responses. Actual model usefulness was checked
separately with installed local models; the interpretation results were poor.
Both successful and unsuccessful observations are preserved.

## Real observations, 2026-09-29

| Run | Interpretation result | Archive/output workflow | Actual calls |
| --- | --- | --- | --- |
| Initial 1.5B run (local-only reference; omitted from public distribution) | Returned no suggestion: 0/1 expected changes | After rollover, two prose replies failed the JSON check; task unfinished | 4 |
| 3B plus native JSON (local-only reference; omitted from public distribution) | Proposed `text = Make it brief.` instead of the expected setting: 0/1 | Correct owner in valid JSON; archived original retrievable | 3 |
| Final 3B, clarified prompt, native JSON (local-only reference; omitted from public distribution) | Returned no suggestions for both requests: 0/2 | Correct owner in valid JSON; archived original retrievable | 4 |

The total is **11 actual local model calls**. Nothing was substituted with a
simulated model in these reports. Native JSON constraints and a larger model
changed together in the second run; this is not a controlled comparison that
isolates either change's effect. The final run also changes the interpretation
prompt and adds a case. No statistical, speed or market-superiority claim follows.

## What interpretation failed to do

The first case starts with `Keep responses detailed.`, then `Make it brief.`.
The independent expected proposal is `response_style = brief`. The initial
small model returned an empty array. The initial 3B model instead mistook the
request envelope's `text` label for a memory key. That wrong proposal stayed
pending; it was never confirmed or promoted into active memory.

The helper prompt was clarified to distinguish input envelope fields from
actual memory keys, reuse an existing key for changes, and extract a replacement
value rather than copy the whole utterance. It includes one unrelated
delivery-window example. The final run added a separate case chosen before
execution: `Export as CSV.` followed by `I want the output in JSON.`, expecting
`export_format = JSON`. Both final model responses were empty arrays. The
original active values remained unchanged, and replay made no additional
interpretation call. These are safe failure behaviours, not successful recall.

Source-span validation, review, cancellation and replay protection are verified
by deterministic tests. **Reliable automatic interpretation is still unresolved
with these tested models/configurations.** The helper stays optional and
experimental. A larger or different model needs its own evaluation; these
results do not justify automatically accepting its suggestions.

## What worked for archival and output checking

A managed conversation receives `The project owner is Mira.` and stores its
complete assistant reply. Explicit rollover creates a linked new session in the
same persistent database. Only the original system prefix remains in the active
transcript; current memory retains Mira and the original source session/index.
The application reconnects and asks who the owner is.

In the initial run the model answered correctly in prose, but ignored the JSON
format twice. Relay rejected both replies and stopped at the configured retry
limit. The task is counted as unfinished, not rescued by a lenient scorer.

With the native Ollama JSON schema option, the second and final runs returned
exactly `{"owner":"Mira"}`. Search found the original archived user message, and
only the accepted reply entered the new transcript. Expected answer scoring is
separate from `OutputPolicy`: the checker verifies JSON structure and required
keys, not the truth of the name.

This tiny workflow establishes the integration path. It does not measure
large-scale performance, automatic archival, full-text search quality, or
unlimited memory. Rollover is explicit, archived disk usage can grow, active
memory stays capped, and tool permissions must be registered for the new session.

## Reproduce and inspect

The evaluator checkpoints exact prompts, payloads, responses, source fingerprints,
memory records, original transcripts, errors and timings. It never downloads
weights or uses a hosted model. Pick an already installed local model and a new
report path:

```console
python examples/evaluate_limits.py --model qwen2.5:3b --native-json --output evaluations/my-limits-run.json
```

The current evaluator runs two interpretation cases and one archive/output
workflow, at most six generation calls including bounded correction retries.
Omit `--native-json` to leave answer generation unconstrained. Its interpretation
schema remains the same. Examples and expectations were not altered to accept
wrong keys, approximate values or invalid JSON after seeing model responses.

The first two reports used source SHA-256
`b760c511f91af75f95ca37a7b083bfd0e0f608f5cb1a46bc1a294e46fa45eb93`.
The final report uses
`dd62cfaca99552ea3f9366e50df8e9fa388763c43476f460535a813d98b28881`.
The historical raw files retain their original versions, case counts and limits.

The deterministic memory regression (local-only reference; omitted from public distribution) retained the
37 language, 8 framing and 13 lifecycle checks before the final prompt edit.
The final complete test suite covers the clarified prompt and rejects a model
copying its demonstration quote as evidence for a different user's message.

## Other boundaries

Anthropic text mapping, system-budget handling and strict response decoding
passed local contract tests. OpenAI/Ollama/Anthropic SDK stand-ins are not live
SDK/API compatibility evidence. No paid API was called.

Formatting checks cannot establish semantic agreement or correct reasoning.
The earlier [grounded-recovery failures](RECOVERY-v0.6.md) also remain published.
A small successful archive workflow does not erase those failures or establish
that context rot has been solved.
