> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Automatic memory evaluation, v0.7

This evaluation checks two distinct things: whether the library automatically
retains a supported user statement, and whether a real model uses that retained
statement when the original conversation turn no longer fits. The first does
not establish the second.

`examples/evaluate_automatic_memory.py` records exact
original inputs, prepared requests, active memory, pending reviews, source spans,
checks, timings, and any actual model response. It does not alter the scorer to
accept approximate matches.

## Observed results, 2026-09-29

| Run | Frozen language cases | Added framing audit | Lifecycle checks | Actual model calls |
| --- | --- | --- | --- | --- |
| Initial deterministic | 28/28 | Not yet added | 13/13 | 0 |
| Initial live comparison | 28/28 | Not yet added | 13/13 | 4 |
| Independent audit before fix | 28/28 | **0/8** | 13/13 | 0 |
| Deterministic after fix | 28/28 | **8/8** | 13/13 | 0 |
| Finished release, including live comparison | 28/28 | **8/8** | 13/13 | 4 |

The corresponding complete reports are `memory-v0.7-initial.json`,
`memory-v0.7-live.json`, `memory-v0.7-audit-before.json`, and
`memory-v0.7-final.json`. The final release run is in
`memory-v0.7-release.json` (local-only reference; omitted from public distribution). Each records the tested library source SHA-256.
The frozen language cases produced 16 expected active key/value entries; all 16
were present and no extra entry was present. This exact-entry precision/coverage
result applies to these fixtures and their declared grammar only.

The independent audit is material. Messages such as “Is the following correct?”
followed by “The export format is CSV” initially caused CSV to be stored even
though the user was asking about a claim. The same occurred with notes, demo,
PDF, and reported-customer preambles. All eight failure cases are preserved. The
implementation now requires every nonempty sentence/line of a candidate-bearing
user message to match the direct-statement grammar before automatically saving
any statement from that message. This makes all eight cases pass, but also means
a useful preference mixed with unrelated prose can be missed. It is a deliberate
limit to automatic capture, not complete language understanding.

The four real calls used the already installed `qwen2.5:1.5b` model via local
Ollama. Both original source turns were omitted in every request.

| Fixture | Expected current value | Memory off | Memory on |
| --- | --- | --- | --- |
| Export format correction | TSV | `json` (incorrect) | `TSV` (correct) |
| Project owner correction | Mira Sen | `unknown` (incorrect) | `Mira Sen` (correct) |

That is **0/2 versus 2/2 exact answers** in this small artificial comparison, with
zero transport errors. The memory-off requests used 1,500 aggregate observed
prompt tokens and memory-on requests used 1,917. Recorded wall time was 28.039
seconds versus 14.569 seconds, but the run did not control loading/caching and is
not evidence that adding memory makes generation faster.

The initial live run preceded the additional framing hardening. Its raw results
and source fingerprint have not been rewritten. After source code was frozen,
the release run repeated the same four real calls and again produced **0/2 with
memory off and 2/2 with memory on**, with no transport errors. That run used
1,497 versus 1,905 aggregate observed prompt tokens and 24.636 versus 14.701
seconds. Concurrent verification, model loading and caching make those timings
unsuitable for a speed claim. No broader production effectiveness is established.

## Frozen language cases

There are 28 named, hand-authored examples, frozen before the first execution
against v0.7. They cover ordinary declarations, names, custom fields, values with
spaces or punctuation, explicit changes, conflicting unmarked declarations,
assistant/system text, quotations, code examples, conditionals, questions,
negation, reported speech, and obvious secret subjects.

Two cases deliberately express an intent outside the declared grammar: “I prefer
brief answers” and “Actually, CSV please.” Correctly leaving these unrecorded is
a control result, not evidence that Relay understood or fulfilled the intent.
Coverage of those natural requests remains absent.

The deterministic run uses a recording transport. It does not call a model or
invent one. Each case compares the exact automatically produced active key/value
entries and number of pending reviews with independently written expectations.
It also checks that the entries appear in the actual prepared request, carry an
exact span from an original user message, consume the shared context budget, and
do not become authoritative application facts.

The lifecycle run checks correction retention after source omission, repeated
requests, revision stability, persistence across close/resume, edited-history
rejection before a provider call, explicit forgetting, session isolation, and
optional review/confirmation. These are integration checks, not an LLM benchmark.

The eight additional framing cases are stored and scored separately so their
post-review addition is visible. They are not retroactively included in the
initial 28-case precision result.

## Optional real model comparison

```console
python examples/evaluate_automatic_memory.py --output evaluations/memory-local.json
python examples/evaluate_automatic_memory.py --live --model qwen2.5:1.5b --output evaluations/memory-live.json
```

The live option verifies an already installed local Ollama model and makes four
actual calls. It never downloads a model, uses a paid API, or silently substitutes
a recording transport for inference.

Two synthetic histories contain an initial preference, an explicit correction,
then unrelated office exchanges. One asks for the latest export format; the other
asks for the latest project owner. Each runs once with memory off and once with
memory on. Both conditions receive the same complete original transcript,
instructions, model settings, and 5,000-character budget. Lexical retrieval is off.
The order is off/on for the first fixture, on/off for the second.

The two original source turns deliberately contain long assistant acknowledgments
that make each whole turn larger than the context cap. This isolates recall after
source omission. The recording transport verifies those source indices are
omitted; the live report checks the same condition. This artificial setup is
useful for a controlled check, and is not representative market traffic.

The expected answer is never manually written to Relay's memory or authoritative
facts. It appears only in original user statements and in whatever the library
automatically extracts from them. The expected-value scorer runs outside the
model request. The output JSON schema contains no expected answer or choices.

Live correctness requires exact equality of the complete returned answer object
to the expected value. Raw outputs and failures are retained. Source retention is
measured independently from answer correctness. One sample per fixture, a small
local model, and timing influenced by loading/caching cannot establish general
reliability, statistical superiority, or a performance improvement.

## Earlier evidence remains relevant

The final v0.6 grounded-evidence recovery experiment completed **0 of 2 tasks**.
Its report remains in `recovery-qwen2.5-1.5b-v0.6.0-final.json`, with its analysis
in `RECOVERY-v0.6.md`. A successful memory-retention test would test a different
behavior and would not erase those failures or prove exact-citation recovery is
solved.

Automatic recall also does not make user statements true, authorize external
actions, or understand arbitrary corrections. Its lexical secret-subject filter
is not a general secret detector. Local persistence can still contain sensitive
user text, and the application still owns its conversation and deletion policy.
