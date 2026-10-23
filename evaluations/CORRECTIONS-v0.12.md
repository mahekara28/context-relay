> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# v0.12 correction evidence and limits

The main limitation remains missed everyday wording. A separate collaborating
agent authored 24 synthetic cases and kept them hidden from the parser implementer
until the source was frozen. This is author-separated development evidence, not
an external human or customer benchmark. On that set,
the parser applied **1/12 clear updates**, proposed **2/3 required reviews**, and
correctly abstained on **8/9 controls**. One control incorrectly created a field
named `it`. Development-set success did not generalize to these paraphrases.

## Deterministic parser

| Source evaluated | Clear updates | Required reviews | Abstention controls | Wrong active updates |
| --- | ---: | ---: | ---: | ---: |
| Unchanged v0.11 wheel | 0/12 | 0/3 | 8/9 | 2 |
| Frozen v0.12 implementation, before seeing cases | 1/12 | 2/3 | 8/9 | 1 |
| Post-holdout pronoun safety fix | 1/12 | 2/3 | 9/9 | 0 |

The last row is a **post-holdout regression**, not an unseen result. After the
failure was inspected, bare pronoun targets such as `it`, `them`, and `that` were
excluded from generic named setters. They cannot become arbitrary memory keys;
the separate contextual path remains review-only. No missed clear wording was
added to the grammar after this evaluation. Exact provenance checks passed in
all rows. Zero observed wrong updates in this small set is not a general guarantee.

Misses include contractions, polite questions, `let us`-style requests, default
changes, replacement phrasing and multiple clauses. **v0.13 reporting correction:**
the earlier text and derived fixture-quality analysis (local-only reference; omitted from public distribution)
incorrectly classified `h06` as malformed. Code-point inspection confirms a valid
U+2014 em dash, not U+FFFD. Console output had rendered the punctuation misleadingly.
The correct denominator is all 12 clear cases; `h10` was the only successful clear
update. The unchanged v0.11 baseline is 0/12. The original corpus, raw reports and
erroneous derived analysis are retained for audit; the
erratum (local-only reference; omitted from public distribution) records the actual code points and
corpus fingerprint. No expected outcome or original report was changed.

Raw evidence: corpus (local-only reference; omitted from public distribution),
v0.11 baseline (local-only reference; omitted from public distribution),
frozen v0.12 (local-only reference; omitted from public distribution),
post-holdout safety regression (local-only reference; omitted from public distribution).
The runner is [evaluate_correction_holdout.py](../examples/evaluate_correction_holdout.py).

The separate implementer-authored development corpus moved from **32/70 to 70/70**:
24 named updates, 13 review outcomes and 33 excluded/unsupported controls. Passing
an exclusion means no memory was created, not that the request was understood.
Its [corpus](corrections-v0.12-development-corpus.json),
baseline (local-only reference; omitted from public distribution),
first run (local-only reference; omitted from public distribution) and
final run (local-only reference; omitted from public distribution) remain separate from the holdout.

Fingerprints:

- Frozen pre-holdout `memory.py`: `a9b9e3373218487b7e965b0feaf38ce8683675f635a49033d939e1f83f7c92a0`.
- Post-holdout safety `memory.py`: `529fd3029ff5420c9c3756b4b33577a0f83a472155b58e32f7706b31c845183e`.
- Holdout corpus: `080c4acbdb466588ca21e876a16528261a5b01f02628346d3c0287d4101b2ff6`.
- Development corpus: `5aa870b79b1ba9778ba4fe7b9b036e2e22172d443fbd53abae627965b9ed15a8`.

## Optional local-model interpretation

The existing `MemoryInterpreter.from_model` prompt was tested without changes
using installed `qwen2.5:3b`, Ollama 0.40.1, temperature 0, seed 17, `num_ctx=4096`
and `num_predict=512`. The model received a generic JSON-array schema and the
original text/current memory. Expected answers were used only for scoring.
All ten cases were declared before inference; only six were eligible for a model
call. No retries, answer-generation calls, downloads or paid API calls occurred.

- **3/4 exact clear candidates.** The remaining suggestion used the correct
  `export_format` key and value `json`, but the declared expected value was `JSON`.
  That casing mismatch remains a failed exact check; the scorer was not relaxed.
- **2/2 ambiguous cases returned no proposal.** These are only two examples,
  not proof that the model always handles ambiguity correctly.
- **4/4 misleading controls were excluded without inference.** Quoted,
  conditional, reported and example text exercised the Python filter, not the model.
- Every proposed change remained pending for review; active values and policy
  facts stayed unchanged. Source spans were exact. Replaying each case made no
  additional extraction call. No confirmation was simulated.

| Local call | Wall seconds | Reported prompt tokens | Reported output tokens |
| --- | ---: | ---: | ---: |
| Export paraphrase (casing failure) | 43.733 | 313 | 35 |
| Style paraphrase | 11.953 | 315 | 36 |
| Language paraphrase | 11.034 | 316 | 39 |
| Custom delivery setting | 11.433 | 315 | 37 |
| Ambiguous pronoun | 3.670 | 324 | 2 |
| Unspecified member | 3.428 | 325 | 2 |

These are one-machine observations, including model loading and local contention;
they are not production latency or token-saving estimates. The helper adds model
work and still uses a restricted eligibility filter. Different wording, models,
settings and domains need their own evaluation. The earlier [v0.9 failures](LIMITS-v0.9.md)
remain relevant; the new corpus/configuration is different, so this is not a
controlled 0/2-to-3/4 improvement measurement.

See the [frozen interpretation corpus](interpretation-v0.12-corpus.json),
full request/response journal (local-only reference; omitted from public distribution) and
[local runner](../examples/evaluate_interpretation_v012.py). Reports preceding the
release version bump retain `0.11.0`; source fingerprints identify the actual code.
