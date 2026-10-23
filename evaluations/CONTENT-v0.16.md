> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Version 0.16: declared content preservation

Relay now checks application-selected source fields before a tool runs and before
accepting structured JSON output. A proposal with the right format and language
can still be rejected for changing a name, amount, row association or coverage.
The same contract applies after an optional repair. This is deterministic source
preservation, not a claim that all of the model's work is correct.

## Result

| Check | Result |
| --- | --- |
| Internal content cases | 37/37: 12 allowed, 25 blocked |
| False allows / false blocks in those cases | 0 / 0 |
| Repeated checks and immutable policy snapshots | 37/37 |
| Recorded failed proposals replayed | 2 |
| Expected replay outcomes | 6/6 across three arms per proposal |
| Full automated suite | 1,098/1,098; zero skips, errors or failures |
| Installed-wheel actual-SDK integration checks | 15/15; simulated network responses |
| New model calls for this evaluation | 0 |

The final machine-readable report (local-only reference; omitted from public distribution) contains
all case results, source fingerprints, evaluator hash and artifact hashes.
The [37-case corpus](content-integrity-v0.16-cases.json) was written by a separate
agent against the agreed API and reviewed before grading. It is an internal
development set, not a blind test, external benchmark or organisational pilot.
Passing it does not estimate accuracy on arbitrary unseen tasks.

The corpus SHA-256 is
`91682dcb8ce3c14a6848aacac2b2ed78b21068e29284281dadda4648dfe7c24b`.
Its labels were not changed after evaluation. Cases exercise literal names,
JSON types, decimal precision, missing/null distinctions, row identity,
association, order, duplicate multiplicity, complete coverage and explicitly
permitted extra fields. Allowed cases establish only their declared scopes;
unprotected summaries and derived values are not graded as correct.

## Replaying the earlier failed repairs

Both retained v0.14 exports supplied `JSON` and `Hindi` correctly but changed
item names. The development case changed Pencils/Notebooks/Markers; the fresh
case changed Folders/Tape. The new evaluator verifies the hashes of their
original journals, sources and artifacts before replaying those proposals.
Their recorded repair instructions already required preserving the rows.

Each identical recorded bad proposal is run through current v0.16 code with
three configurations, using real temporary JSON writes:

| Configuration | Files written | Files violating declared source fields | Repair callbacks |
| --- | ---: | ---: | ---: |
| Format/language checks alone | 2 | 2 | 0 |
| Add protected content, without repair | 0 | 0 | 0 |
| Add protected content and a manually corrected proposal | 2 | 0 | 2 |

Each permitted export is written exactly once. The first arm uses the current
library without a content policy; it is not a rerun of the old library or model.
The final arm uses a static, manually corrected proposal. It demonstrates that
Relay can reject the bad proposal, recheck a correction and dispatch once. It
does not show that a model can generate a successful repair. No new inference
was performed, and unprotected summary meaning and totals are not graded.

The report identifies each file in
the final artifact directory (local-only reference; omitted from public distribution).

## Retained evaluator correction

The first report (local-only reference; omitted from public distribution) already passed all 37
content cases. Its historical replay selector incorrectly included both the two
recorded failures and two manually restored controls, while applying failure
expectations to all four. Consequently 6/12 replay arms met those expectations.
The exact first evaluator (local-only reference; omitted from public distribution) and its
artifacts are retained. The corrected selector includes only recorded failed
proposals in that replay, producing 6/6 outcomes across two cases. Manually
restored controls remain in the 37-case matrix. Library source and corpus were
unchanged between these runs; the correction is to evaluation cohort selection.

## Regression and installation checks

The full-suite report (local-only reference; omitted from public distribution) and
log (local-only reference; omitted from public distribution) record all 1,098 tests and unchanged source/test
fingerprints throughout the run. The 47 new tests cover the pure content policy,
configuration and execution/output integration. Cases include protected source
mutation isolation, exact numeric parsing, malformed data, stale turns, existing
authorization, bad and good repairs, actual CSV writes, sync/async JSON checks,
canonicalized dispatch values and no retry after an executor starts.

The built wheel was installed offline into a fresh directory outside the source
tree. Installed-wheel SDK evidence (local-only reference; omitted from public distribution) and its
log (local-only reference; omitted from public distribution) record 15/15 passing checks, all installed modules
matching source and zero declared runtime dependencies. The tests use actual
OpenAI, Anthropic, Ollama, LangChain and LangGraph packages with simulated
transports; versions are recorded in the full-suite report. They do not prove
hosted-service acceptance or signature validity.

A separate installed-core smoke run with optional SDKs absent blocked altered
names and a bad repair, allowed one valid repair, preserved exact decimal JSON
through configuration, and rejected rounded provider-stub output. The
[local export example](../examples/protected_export.py) also writes and reads a
real temporary file while counting executions. These checks use no models.

## Scope and use

The application must supply an accurate source snapshot in the expected candidate
shape and select fields to protect. Relay does not choose the source, infer
important fields or compute expected totals. An updated source needs a new
registered policy and a fresh prepared turn. All workers sharing a store must
support the content policy before it is relied on; older binaries do not gain
these checks by reading newer stored data.

Declared fields are compared exactly, including JSON types; row rules can allow
explicit reordering or extra unprotected fields. Fractional JSON numbers retain
precision through `from_json()` and `check_json()`. Already-rounded Python floats
cannot be recovered. Source size, depth and rule count are bounded; `max_rows`
applies to arrays selected by row rules. Exceeding candidate limits fails the
check instead of accepting a truncated prefix.

JSON output can be checked directly. Other rendered formats, such as CSV, can
use protected structured tool arguments, with rendering owned by the application.
The check does not verify an executor's final file, stop a bypassing call, prove
source truth/freshness, validate arbitrary prose, or verify undeclared arithmetic.
The repair callback remains opt-in and may itself incur model costs if the
application chooses an AI implementation. Content checks themselves require no
API, extra model call or runtime dependency.

See the [integration guide](../docs/CONTENT_INTEGRITY.md) for configuration and
scope details. Run the local evaluation from the project directory with a fresh
output name (the evaluator refuses to overwrite earlier reports):

```console
python examples/evaluate_content_integrity.py --output evaluations/my-content-run.json
python examples/protected_export.py
```
