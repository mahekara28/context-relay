> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Instruction recognition and clarification — v0.18.0

This local release broadens a finite grammar and pauses generation for detected
disputes. It does not solve natural-language understanding in general.

## What changed

- Broader English indirect corrections, personal preferences, same-message
  references, combined language/style instructions and rejection/deferral forms.
- Bounded Hindi/Hinglish, Spanish and French instructions for export/output
  format, response language and response style. Exact original evidence is kept.
- Default `MemoryPolicy.pause_on_uncertainty=True` stops the next connected model
  call for disputed existing preferences. Lower-level inspection stays available.
- Managed text conversations store a local question, complete its request once
  and accept a clear follow-up. Nested agents cannot complete the wrong request.
- The next grounded retry checks its prepared memory too. Checked tools still
  reject disputed memory even if an application explicitly disables the pause.
- Whitespace-heavy input performance is covered without rewriting source text.

## Wording evidence: first unseen run versus exposed regressions

A separate agent authored and froze 64 cases without reading the implementation
or old corpora. This was an internal test author, not external customer testing.
The 60 supported-language cases contain 24 clear corrections, 18 unclear changes
and 18 non-instructions. All initial memory seeds were captured correctly.

| Supported cases | v0.17.1 baseline | First v0.18 freeze, unseen | Final exposed regression |
| --- | ---: | ---: | ---: |
| Correct clear updates | 0/24 | 1/24 | 24/24 |
| Exact uncertainty targets | 0/18 | 4/18 | 18/18 |
| Correct abstentions | 18/18 | 17/18 | 18/18 |
| Wrong active updates | 0 | 0 | 0 |
| False clarification flags | 0 | 1 | 0 |

The weak first run is retained. Its failures guided further changes, so the final
60/60 is **development regression coverage, not unseen accuracy**. No case text,
labels or expected answers were changed. Corpus SHA-256:
`68e25aa808e0afe6fa103cd9ece355d9d9b661e818524740a287a6b186017f85`.
Source spans, replay stability and separation from authoritative facts passed.

Four exploratory German/Italian instructions remain unrecognized in both
versions (0/2 clear, 0/2 uncertain). Those can leave old preferences active. They
are separate from the supported score, remain in every raw report, and are not
claimed as supported languages. This is concrete evidence of a remaining limit.

[Protocol](instructions-v0.18-independent-protocol.md),
[frozen corpus](instructions-v0.18-independent.json),
baseline (local-only reference; omitted from public distribution),
first freeze (local-only reference; omitted from public distribution),
final source regression (local-only reference; omitted from public distribution).
The intermediate `instructions-v0.18-exposed-regression.json` and
`instructions-v0.18-verified-regression.json` are also retained.

## Connection-to-action fixtures, not model task-success rates

The replay uses a deterministic stand-in that reads only Relay's injected memory
and returns those values as proposed arguments. It intentionally ignores the
user transcript. A registered tool writes a local JSON file. An uncertain case
must pause before generation; a scripted full clarification then resumes it.
Expected labels are used only to grade, not supplied as tool arguments.

| Fixture outcome | v0.17.1 | v0.18.0 |
| --- | ---: | ---: |
| Passing memory/action fixtures | 18/60 | 60/60 |
| Writes violating fixture expectations | 42 | 0 |
| Clarification requests | 0 | 18 |
| Calls before required clarification | 18 | 0 |
| Setup stand-in calls | 60 | 60 |
| Task stand-in calls, including after clarification | 60 | 60 |

These are deliberately artificial integration fixtures. A real model may read
the user instruction correctly even when old memory is stale, or ignore correct
memory. Temporary requests must affect the current response without rewriting
standing preferences; this replay checks only standing memory. It cannot grade
real task completion, model reasoning, long-context benefits or customer impact.
Zero actual model/network calls were made.

The final baseline and new-version reports use the **same evaluator hash**:
baseline replay (local-only reference; omitted from public distribution),
new replay (local-only reference; omitted from public distribution),
[runner](../examples/evaluate_instructions_v018.py).
The original `instruction-tasks-v0.18-baseline.json` used expected tool arguments
and is retained as a superseded oracle-gate check. The intermediate context-reader
baseline is also retained. Neither is mixed into the final comparison. The first current-source replay,
`instruction-tasks-v0.18-verified.json`, passed 55/60: five style clarifications
remained paused on “Use detailed answers.” The parser was fixed for that bounded
imperative family, with negative controls and resumption tests. The final replay
uses the same evaluator and unchanged scripted replies; the failed run remains.

## Release verification

- **1280/1280 tests passed**, no skips, errors or failures;
  source/test fingerprints stayed unchanged during the final run.
- **15/15 installed SDK/framework checks passed** with
  simulated transports. Installed library bytes match source. This checks SDK
  compatibility, not hosted model acceptance or opaque signature validity.
- A separate installed-core check runs without optional SDKs. It checks the
  offline quickstart, multilingual correction, pause and clarification/resumption.
- All default behavior remains local, with zero required runtime dependencies.

Full suite (local-only reference; omitted from public distribution),
installed SDK checks (local-only reference; omitted from public distribution).
Earlier development/verified suites ran during edits. The development run
exposed migration/test failures; the next passed its checks but changed source
fingerprints during the clarification fix. Both remain recorded and are excluded
from final release evidence. The older action-repair evaluation explicitly disables the
new generation pause to keep isolating its existing post-proposal comparisons.

## Integration changes and remaining limits

Catch `MemoryClarificationRequired` and display its prompt. For application-owned
history, append the question and later full instruction to the original history.
Managed chat does that local-question bookkeeping itself. A bare value such as
`JSON` is not globally inferred; the prompt requests a full setting and value.
The supplied prompt is English; an application can localize from its target keys.
[Upgrade guide](../docs/INSTRUCTION_CLARIFICATION.md).

Unknown phrasing, dialects, sarcasm, arbitrary cross-turn references, unsupported
languages and custom business meaning can still be missed. A finite parser
cannot detect every instruction it does not understand. A local/model interpreter
may propose extra recall for explicit review; it is not automatically enabled.
Recognition is not model obedience or correctness. Native mode, bypassed calls,
unchecked actions, storage limits and organisational reliability retain their
separate documented boundaries. No paid service or customer pilot was used.
