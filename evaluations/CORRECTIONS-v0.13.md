> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# v0.13 correction evidence

The grammar covers more direct request forms, but unfamiliar wording remains a
major gap. A collaborating agent authored 32 synthetic cases independently of the
parser implementer. The corpus was frozen before scoring and hidden from the
implementer until the parser source was frozen. No parser changes followed this
evaluation. This is an author-separated development check, not an external human
or customer benchmark.

| Frozen parser | Clear updates | Required reviews | Abstention controls | Wrong active updates |
| --- | ---: | ---: | ---: | ---: |
| v0.12 | 0/16 | 0/4 | 12/12 | 0 |
| v0.13 | 3/16 | 1/4 | 12/12 | 0 |

Exact source provenance remained intact. The recognised clear cases were `n02`,
`n03` and `n15`; `n19` produced the required review. Misses included `Could we`,
`I've changed my mind`, `from here on`, and unfamiliar clause order. The parser
was not extended to make this set pass after its results were known. Zero wrong
active updates in 32 examples is not a general safety or recall guarantee.

Raw evidence: frozen cases (local-only reference; omitted from public distribution),
v0.12 baseline (local-only reference; omitted from public distribution),
frozen v0.13 result (local-only reference; omitted from public distribution).

The former v0.12 holdout was deliberately reused as development material.
Its current regression result is 11/12 clear updates, 3/3 reviews and 9/9
abstentions, with no wrong active updates. This does not measure unseen accuracy.
The development report (local-only reference; omitted from public distribution)
records the source fingerprints; its version field precedes the release bump.
The remaining miss combines an old-value rejection with a separate shorthand
replacement. A prior v0.12 reporting error excluded a valid em dash from the
denominator. The correct original result was 1/12, not 1/11; the
explicit erratum (local-only reference; omitted from public distribution) retains all original
corpus/report data and explains the console-rendering mistake.

## Actual local-model suggestions

After blind parser scoring, the unchanged optional `MemoryInterpreter` was
evaluated on the same 32 cases. The cases were inspected before this run, but
the prompt, schema and model options were unchanged from v0.12. This is a
declared follow-up evaluation, not a second independent unseen parser result.

Installed `qwen2.5:3b` local GGUF weights, Ollama 0.40.1, temperature 0, seed 17,
`num_ctx=4096` and `num_predict=512` were used. There were exactly eight calls,
with 90-second request timeouts, no retries, downloads, paid APIs or automatic
confirmation. Expected values were never included in the model input. Full
request checkpoints and raw responses were recorded before scoring.

| Outcome on all 32 cases | Result |
| --- | ---: |
| Clear values applied by deterministic rules | 3/16 |
| Additional clear cases with exact pending suggestions | 2/16 |
| Clear cases covered by either mechanism | 5/16 |
| Required review cases with exact pending suggestions | 2/4 |
| Abstention controls without any change/proposal | 12/12 |
| Model calls made for abstention controls | 0 |

The 5/16 figure is **not automatic correction success**: two cases still need
review. Of six clear requests that reached the model, two produced exact pending
values, two returned an empty array, and two used lowercase `json`/`hindi` rather
than the exact expected `JSON`/`Hindi`. Those casing differences remain failed
exact checks; no normalization was added after seeing them. Of two review cases
sent to the model, one produced the expected `Hindi` suggestion and the other
abstained. The other successful review came from the local contextual rule.

All model suggestions stayed pending, source quotations were exact, no policy
facts were created, and replay produced no additional calls. The parser and
interpreter source hashes were unchanged through the run. The eight calls took
84.342028 seconds in total on this machine, including loading and contention;
individual calls ranged from about 2.25 to 39.71 seconds. These observations are
not a production latency estimate. The 12 misleading controls exercised the
Python eligibility filter, not the model's resistance to misleading input.

See the complete journal (local-only reference; omitted from public distribution),
derived summary (local-only reference; omitted from public distribution) and
[reusable local runner](../examples/evaluate_interpretation_v013.py). The runner
refuses to overwrite a report, enforces its explicit call cap and preserves
failures. Five offline scoring tests distinguish active-rule success from
pending suggestions and reject wrong keys, extra/duplicate proposals, incomplete
compound changes and false abstentions.

Source fingerprints for both evaluations:

- `memory.py`: `d25236549c63150a19eb954f189afd53e6c233667be012ebe6ff77f087b80b48`.
- `interpretation.py`: `4b38aa1999619c1c4a32fc8579c670e2f0b2fbaad706a5f4f8d77d7ec30741d0`.
- Frozen 32-case corpus: `e418f8e2789654770944d7a3203c64945145947e734e3b892d299a3123af27ac`.
