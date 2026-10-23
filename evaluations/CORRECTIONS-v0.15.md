> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# v0.15 correction recognition and safety evidence

This release repairs the original 40 known wording cases and adds a bounded
message-local correction planner. The planner combines a named setting, old-value
evidence, a replacement and linked support clauses before applying any change.
It can distinguish a changed setting from another setting the user says to keep.
References such as `that` need an explicit compatible target in the same message;
they do not authorize guessing from distant conversation history.

## Development sequence and retained failures

The v0.14 post-safety implementation recognized 5/16 clear corrections and flagged
6/8 unclear requests in its 40-case corpus. That corpus was already known at the
start of this release. The first v0.15 implementation passed all 40 known cases.

Before that first implementation was frozen, a separate collaborating agent
wrote 78 additional cases without reading the parser or earlier examples. The
author received the evaluator's schema and canonical seed vocabulary. Root
checked that every seed produced the intended existing values against the
immutable v0.14 installation. The implementer saw neither these cases nor their
results before the first freeze. This is an internal agent-authored evaluation,
not external human or organisational evidence.

The first unseen result was poor: **0/36 clear updates, 0/18 correct uncertainty
flags and 22/24 correct abstentions**. It made one wrong active update and one
false clarification alarm. Its support for the known 40 cases did not generalize
to this new wording. Both the failed report and the exact parser snapshot remain
available. The corpus author then audited every failed case against its seed and
label; no case was removed, relabeled or excluded.

After exposure, the implementation changed structurally instead of adding an
optional model call. The message-local planner treats assignments, historical
values, reaffirmations and unresolved replacements as different operations.
Every clause must be supported before a composed automatic update is eligible.
Quoted, temporary, hypothetical and reported requests retain their guards.

### Original known 40 cases

| Evaluated source | Clear updates | Correct uncertainty | Correct abstention | Wrong active updates | False alarms |
| --- | ---: | ---: | ---: | ---: | ---: |
| v0.14 post-safety baseline | 5/16 | 6/8 | 16/16 | 0 | 0 |
| First v0.15 implementation | 16/16 | 8/8 | 16/16 | 0 | 0 |
| Final v0.15 development regression | 16/16 | 8/8 | 16/16 | 0 | 0 |

### Additional 78 cases

| Evaluated source | Clear updates | Correct uncertainty | Correct abstention | Wrong active updates | False alarms |
| --- | ---: | ---: | ---: | ---: | ---: |
| v0.14 baseline | 0/36 | 0/18 | 22/24 | 1 | 1 |
| First unseen v0.15 run | 0/36 | 0/18 | 22/24 | 1 | 1 |
| Final v0.15 post-exposure regression | 36/36 | 18/18 | 24/24 | 0 | 0 |

**The final results are development regressions on exposed cases, not a new
unseen accuracy estimate.** There is no claim that arbitrary English, other
languages or domain-specific jargon can always be understood. Reproducing these
cases establishes the listed behavior and protects it from regressions.

All case reports check exact source spans, stable replay and unchanged trusted
facts. Concrete updates also check canonical spelling. The unchanged scorer uses
`MemoryPolicy(max_chars=16000)` to isolate recognition from small storage limits;
the current unit regression additionally runs all 118 cases at the default
memory budget. No inference, hosted requests or paid services are used.

## Safety and integration checks

The changes preserve confirmation for conflicting declarations and mismatched
old values. Historical descriptions cannot silently become current preferences.
Exact existing custom field names take precedence over wrapper words such as
`saved`, and custom values are not normalized as response-style phrases.
Ambiguous requests with identifiable targets ask for clarification instead of
choosing a target. Linked support text cannot become a new unrelated memory key.

A repeated or same-value interpreter suggestion cannot hide a new uncertain
request. Approving an earlier proposal cannot clear a later unresolved change.
The optional interpreter remains review-only. No parser result creates trusted
application facts or authorization.

Tests include 35 substitutions across documented format, language and style
values; these reuse known grammatical forms and are development coverage.
Lifecycle checks cover forget/dismiss/restart and consumed-history replay.
An integration test persists an unclear correction, reopens the database, blocks
a real export action, accepts a later explicit clarification and writes the
correct artifact. This uses a local stub provider, not a new model trial.

A review after the first passing 1,049-test full run reproduced two additional
faults: value-first historical wording could still promote an old preference,
and composed custom values ending in `Again` could be truncated. Those faults
were fixed with dedicated regressions before the final release. For composed
custom names such as `Born Again`, the safe result is clarification, preserving
the old value as disputed; the parser does not guess or truncate the new name.
Established explicit scope markers such as `from now on` and `going forward`
retain their existing interpretation. The earlier
passing run (local-only reference; omitted from public distribution),
log (local-only reference; omitted from public distribution), and exact
parser (local-only reference; omitted from public distribution) /
planner (local-only reference; omitted from public distribution) bytes are retained. A passing
suite only covers its assertions; it is not proof that undiscovered cases work.

The full suite passed **1051/1051 tests**, with zero skips, failures or errors. Source/test fingerprints stayed unchanged throughout the run. The built wheel then passed **15/15 actual-SDK checks** outside the source tree, using simulated transports. Every installed module matched source bytes. A separate isolated installed-core smoke check ran without any optional SDK: it recognized a composed correction, blocked a mismatched action, repaired its arguments once, stopped on uncertainty and resumed after explicit clarification. No new local or hosted model requests were made.

The three updated memory guides also passed [27 local documented-example and
boundary checks](docs-examples-v0.15-final.json) against the final source. Their
three interpreter callbacks return static fixtures; they make no model calls.
The earlier 23-check report (local-only reference; omitted from public distribution) is retained separately.

## Local processing overhead

Each timing run processes the 78 exposed cases five times after one unrecorded
warm-up: 390 measurements per version. The timer covers one `process()` call
including in-memory SQLite persistence and source handling; initial seeding is
excluded. The two versions run sequentially with the same script and corpus.

| Version | Median | 95th percentile | Maximum |
| --- | ---: | ---: | ---: |
| v0.14 | 0.639 ms | 1.474 ms | 3.290 ms |
| v0.15 | 1.230 ms | 2.981 ms | 4.197 ms |

The broader parser costs additional local work. This is not an equal-work speed
comparison: v0.14 skips most of these requests, while v0.15 saves corrections or
uncertainty records. These small in-memory measurements do not predict disk,
concurrent, large-history or production performance. There is no added default
model call. Raw baseline (local-only reference; omitted from public distribution),
raw final timing (local-only reference; omitted from public distribution), and
exact timing script (local-only reference; omitted from public distribution) are retained.

## Reproduction

From this source checkout, use its installed Python environment:

```sh
python examples/evaluate_corrections_v014.py --corpus evaluations/corrections-v0.14-holdout.json --source src --output known-reproduction.json
python examples/evaluate_corrections_v014.py --corpus evaluations/corrections-v0.15-fresh.json --source src --output exposed-reproduction.json
```

The evaluator refuses to overwrite a result. The v0.14-named evaluator is
intentionally unchanged so all versions use the same grading rules. Its exact
bytes are retained in the grader snapshot (local-only reference; omitted from public distribution).

Retained evidence:

- [Known 40-case corpus](corrections-v0.14-holdout.json), v0.14 result (local-only reference; omitted from public distribution), first v0.15 result (local-only reference; omitted from public distribution), final development result (local-only reference; omitted from public distribution).
- [Additional 78-case corpus](corrections-v0.15-fresh.json), v0.14 baseline (local-only reference; omitted from public distribution), first unseen v0.15 result (local-only reference; omitted from public distribution), post-exposure development result (local-only reference; omitted from public distribution).
- Original first-frozen parser (local-only reference; omitted from public distribution), [corpus author's label audit](CORRECTIONS-v0.15-label-audit.md).
- Full suite (local-only reference; omitted from public distribution), full log (local-only reference; omitted from public distribution), installed-wheel SDK checks (local-only reference; omitted from public distribution).

## Operational boundary

The library still needs a supported connection and application-owned action
checks. Uncertainty can block a checked action; it cannot stop a caller bypassing
the check or prove the correctness of generated prose. Memory remains bounded,
and unknown wording can still fall outside both parsing and uncertainty rules.
These tests do not establish a universal solution to context rot.

An upgrade does not reprocess consumed history or resurrect forgotten/dismissed
entries. Correct a stale pre-upgrade setting with a new explicit user instruction.
Schema 2 is unchanged; all processes sharing a session should run a supported
version. The release adds no runtime dependencies and no default model calls.
The local-model completed-work measurements in [VALIDATION](../docs/VALIDATION.md)
belong to v0.14 and have not been rerun or presented as v0.15 model evidence.

## Fingerprints

- `corrections-v0.15-fresh.json`: `714d39e42742d0eb844a5b4c7a748437cd2df7d12b38ec7e7125ae8809b6b7fe`.
- `memory-v0.15-first-frozen.py.txt`: `48d06fa3dc4f24ae4acb8ed5042938a8b0dcc7b4d99d17fcc86d1b37fa623e09`.
- `corrections-v0.15-grader.py.txt`: `9e765a528c3bd4116d31a0f549fd323a3faa493e63b41777c1960f10019a4b78`.
- Final `memory.py`: `28a39affd90062a05b7bacfaa1dfed834ad7573592a438fa02502e0f567b01a0`.
- Final `memory_plan.py`: `78187b63fc238ca398bcdcb5c6aca8599a08d6f234b2e99753abada6aaf7513f`.
