> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# v0.14 correction and uncertainty evidence

v0.14 improves several direct request forms and makes some unsupported known
settings visibly uncertain. It still misses most clear changes in this new
synthetic set. A clarification flag is a request for help, not a successful
interpretation of the new value.

A separate collaborating agent authored 40 cases: 16 clear changes, eight
requests whose value needs clarification, and 16 misleading or irrelevant
controls. The corpus was frozen before scoring and kept from the parser
implementer until the first implementation was frozen. This is author-separated
development evidence, not an external human or organisational benchmark.

| Evaluated source | Clear automatic updates | Correct uncertainty | Correct abstention | Wrong active updates |
| --- | ---: | ---: | ---: | ---: |
| v0.13 baseline | 1/16 | 0/8 | 15/16 | 8 |
| Frozen v0.14, before results were seen | 5/16 | 6/8 | 15/16 | 2 |
| v0.14 after the narrow safety fix | 5/16 | 6/8 | 16/16 | 0 |

The last row is a **post-holdout development regression, not unseen evidence**.
After the first run exposed two false promotions, the implementer tightened
generic declaration subjects and sample/metatext framing. No positive grammar
was added from the new holdout to make its missed requests pass. The original
source bytes, corpus and every raw report remain available. The lower wrong-update
count measures fixes to observed examples; it does not prove general safety.

The two exposed errors were a statement about a preference becoming an invented
field, and a framed sample becoming an active `following` field. Both now leave
active memory unchanged. Simple custom declarations such as `The sample size is
20` and explicit custom setters such as `Set the timeout for exports to 30 seconds`
remain available. More complicated prose can still be missed.

All three runs used the same grading rule: equivalence for case in the finite
known preference vocabulary, plus a separate exact-canonical score. The exact
canonical update counts were also 1, 5 and 5. There were no model calls or
interpreter confirmations. Source quotations, replay and authority checks passed
in every run. No false clarification alarms were observed in these 40 cases;
this small result is not a general false-positive estimate.

Raw evidence:

- [Frozen corpus](corrections-v0.14-holdout.json).
- v0.13 baseline (local-only reference; omitted from public distribution).
- First frozen v0.14 run (local-only reference; omitted from public distribution).
- Post-holdout safety regression (local-only reference; omitted from public distribution).
- Exact first frozen parser bytes (local-only reference; omitted from public distribution).

## What changed operationally

An eligible unparsed correction naming an existing setting can create
`reason="unparsed_correction"`, `value=null`, with the exact source and captured
old value. The old value remains visible but disputed. A memory-bound action
check blocks it; this does not prevent a caller from bypassing the check.
`confirm()` rejects the null record. A later concrete instruction or explicit
dismissal is needed. A reviewed concrete proposal resolves only earlier
uncertainty IDs captured with it, after checking their basis, and leaves later
flags intact.

Unsupported single-token literals in standard finite preference fields require
review. Unresolved descriptions such as `the same as last time` cannot silently
become an active format or language through the generic setter. Custom fields
remain schema-free. Finite value normalization handles recognized case and
surrounding whitespace without changing source quotes or applying fuzzy matching
to custom values. The optional interpreter's prompt is unchanged, and historical
model failures have not been rescored.

The first null uncertainty record upgrades the session's automatic-memory state
to schema 2. v0.14 reads schema 1 and 2; older versions reject schema 2 rather than
confirming a null placeholder. No automatic downgrade occurs after resolution.
Upgrade all workers sharing the database. See [usage and migration details](../docs/MEMORY_LANGUAGE.md#clarification-when-the-setting-is-known-but-the-new-value-is-not).

The focused memory suite passed 185 tests at this source freeze. New tests cover
budget rollback, exact sources, restart/replay/undo, malformed state rejection,
older-version schema rejection, newer-flag preservation, generic-fallback bypasses
and the post-holdout misleading-subject regressions. Test counts are not model
accuracy or evidence of organisational benefit.

## Remaining limits

Only five of 16 clear requests were automatically applied; two of eight requests
needing clarification were still missed. A correction that neither parses nor
triggers uncertainty can leave stale memory undisputed. The new flag therefore
reduces one failure mode without eliminating it. Implicit references, uncommon
wording, conditional intent and domain-specific values still need application
review or more evidence. Real model-following and completed-work results are
reported separately in [the validation report](../docs/VALIDATION.md).

Fingerprints:

- Corpus: `518f1c117c00c988fa534b80de7e5272f87150cc411facd27548d494a4daaaf5`.
- First frozen `memory.py`: `9dbc4cf6f070a72b7c216c1dd3489cff2fcd179f691693c4ef08c3e0d7dba766`.
- Post-safety `memory.py`: `930b648950a0829029d077cede4dce40a8979c3111c454f30646e01e2c0d89ad`.
- Unchanged `interpretation.py`: `4b38aa1999619c1c4a32fc8579c670e2f0b2fbaad706a5f4f8d77d7ec30741d0`.
- `memory_values.py`: `a3130519d280666da5c34547157546f7bd8a58c61d7a9a04824dd1faa523d3fc`.
