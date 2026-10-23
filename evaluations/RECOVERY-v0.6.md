> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# v0.6 recovery results: failures included

The final local run used the installed `qwen2.5:1.5b` model on two fixed synthetic logistics tasks. It made eight actual model calls across six cases, with no transport errors. The fixtures, expected answers, model settings, and scoring rules were kept fixed while the recovery implementation was improved.

| Final condition | Completed tasks | Model calls | Total reported prompt tokens |
| --- | ---: | ---: | ---: |
| Full history, larger context cap | 1/2 | 2 | 3,426 |
| Bounded context, no recovery | 0/2 | 2 | 1,341 |
| Bounded context, recovery enabled | 0/2 | 4 | 2,668 |

**This run does not demonstrate improved task completion.** All failures remain in the final raw report (local-only reference; omitted from public distribution). See [the method](../docs/RECOVERY_EVALUATION.md) for the full comparison design and schema assistance.

## What worked and what did not

For the buried loading-dock task, the bounded model initially invented `Dock 12` and a quote. The source check rejected the fabricated quote. Automatic lookup restored original messages 1 and 2, within the same 3,000-character cap. The model then answered the correct value, `Dock-7`, but repeatedly added text that was not an exact substring of the source. The final result stayed `unresolved`; a correct value with an invalid quote was not counted as completed.

For the unconfirmed-arrival task, the model invented an arrival date. The original evidence could not fit as a required complete exchange within the configured cap, even after reducing ranked lookup candidates. The result was `budget_exceeded`, with no over-budget request sent. Full history correctly abstained on this task. Refusing the unsupported answer is useful failure handling, but it is not completion of the missing workflow.

The library's source checker verifies identity, input availability, and exact text. Even a passing citation would not establish semantic support or factual truth. No tool transactions ran, no facts were automatically approved, and this was not a production workload.

## Development iterations

All three local runs are retained instead of selecting only a favourable result:

| Iteration | Change under examination | Completed recovery tasks |
| --- | --- | ---: |
| Initial loop (local-only reference; omitted from public distribution) | Model-requested lookup and validation feedback | 1/2 |
| Automatic lookup (local-only reference; omitted from public distribution) | Search the original question after failed citations | 0/2 |
| Bounded lookup refinement (local-only reference; omitted from public distribution) | Limit fallback repetition and reduce ranked candidates when space is insufficient | 0/2 |

The initial loop's successful retry did not perform explicit source recovery: feedback changed the selection budget and allowed the small earlier record to fit. The final loop explicitly recovered it, but the model's quote still failed. These are distinct behaviours, and the audit reports show the difference.

Source fingerprints in the latter reports identify the tested development revisions. A later no-match bookkeeping fix prevents repeating an empty fallback search; deterministic tests cover it, and it does not affect these positive-match fixtures.

Two fixtures, one small model, fresh session IDs, and a single repeat per implementation cannot establish a general performance claim. Recovery also spent more calls and total tokens than the bounded control. Recorded latency is affected by hardware, model loading, caching, and other local work; no speed advantage is claimed.

The next evidence needed is a larger independently reviewed task set, multiple models, and a consenting real workflow with domain-specific outcome checks. Better record retention alone does not solve model reasoning or citation generation.
