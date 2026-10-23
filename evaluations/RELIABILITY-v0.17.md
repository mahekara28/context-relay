> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Version 0.17: repair boundaries, integration and deployment evidence

This release makes action repair narrower, provides local restoration from known
values, and consolidates tool registration. It also adds measured shared-storage
and real local-provider checks. Hosted acceptance and organisational reliability
are still unproven: neither has been substituted with a passing unit test.

## Repair comparison with v0.16

The same three synthetic proposals and callback results were run against the
previously installed v0.16 wheel and current v0.17 source. Each allowed action
writes a real JSON file, which is graded against the original unrelated data.

| Callback behavior | v0.16 | v0.17 |
| --- | --- | --- |
| Fix format but alter unchecked row quantity | Wrote changed data | Blocked before writer |
| Fix protected nested name but alter sibling note | Wrote changed data | Blocked before writer |
| Fix only the failed format | Wrote once, unrelated data preserved | Wrote once, unrelated data preserved |

Baseline report (local-only reference; omitted from public distribution),
current report (local-only reference; omitted from public distribution), and
identical evaluation driver (local-only reference; omitted from public distribution) are retained with
module/driver/artifact hashes. These are known development regressions with
static callbacks, not evidence that a model reliably repairs its own mistakes.

`repair="known"` can copy trusted bindings, current undisputed memory and failed
exact source subtrees without a callback or model call. Unsupported reconstruction,
conflicting sources and loss of decimal precision stop before dispatch. Every
result still passes normal policy/revision checks. Scoped callback repair blocks
unrelated argument changes. See [repair behavior](../docs/REPAIR_SAFETY.md).

## Registration and review

`ToolRegistry` validates full signature coverage, snapshots defaults, detects
policy typos, provides explicit conventional memory mappings and binds tools to
the original prepared turn. Replacing an owned registration advances its revision;
old bound tools cannot execute. Coverage reports show unchecked arguments and
unregistered tools in the host-supplied inventory. Bypassing calls remain outside
this boundary. [Integration guide](../docs/TOOL_REGISTRY.md).

Independent review found two defects before freeze: a missing default could be
reinserted after content checking, and inconsistent lock order could deadlock a
registration against an executing tool reading coverage. Both were fixed and
covered by dedicated regressions. Final execution now validates completeness
without adding defaults, and registration/binding use consistent store-to-registry
lock order. The final review found no further concrete issue in its bounded scope.

## Automated and installed-package verification

The full suite (local-only reference; omitted from public distribution) passed **1,173/1,173 tests**, zero skips,
failures or errors. Its log (local-only reference; omitted from public distribution) and identical before/after
source/test fingerprints are retained. The 75 new tests comprise 30 repair-safety,
28 tool-registry, 13 deployment-runner and four workflow-grading tests. The old
repair-tampering regression now requires blocking the unrelated row mutation.

The offline-installed wheel passed **15/15 actual-SDK checks** outside the source
tree, with all installed modules matching source. These use simulated network
transports, not hosted provider responses. Report (local-only reference; omitted from public distribution) and
log (local-only reference; omitted from public distribution). SDK and Python versions are in the full-suite
report. The package declares zero runtime dependencies.

A separate installed-core smoke check ran with every optional SDK absent. It
blocked content drift and an unrelated callback edit, repaired known values with
zero callbacks, executed once, verified exact receipt/writer argument equality,
checked configuration/decimal preservation and reported uncovered tools.

## Shared local deployment measurements

Each profile uses one shared SQLite action database and independent processes
started with Windows spawn. Every worker opens its own connections. The cases
exercise isolated protected sources, wrong-content denials, real file writes,
stale-turn replay, a shared-revision race, failed callbacks and owner denials.
All **1,361 action attempts across 432 isolated sessions** met their expected
outcomes; no unexpected errors or failed invariants occurred. Each profile
produced exactly one contested callback, effect file and successful receipt.

| Profile | Isolated sessions | Action attempts | Attempts/s including process startup | Group p95 | Group p99 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 workers (local-only reference; omitted from public distribution) | 144 | 437 | 100.8 | 0.031 s | 0.038 s |
| 4 workers (local-only reference; omitted from public distribution) | 144 | 452 | 113.6 | 0.104 s | 0.324 s |
| 8 workers (local-only reference; omitted from public distribution) | 144 | 472 | 99.1 | 0.288 s | 1.406 s |

An attempt includes rejected actions and ownership checks, not just successful
writes. Latencies measure mixed operation groups; most isolated groups contain
open/block/write/replay work. They are not per-SQL-query or model latency.
The database/session setup phase is reported separately. Profiles ran sequentially
after the automated suite, with no simultaneous inference started by this task.
The table is one final-source observation per profile on one four-logical-CPU Windows machine,
using short filesystem callbacks, not a production capacity estimate or soak test.

The growing p99 tail matters: SQLite serializes writes and Relay holds the write
transaction across a checked callback. This does not scale linearly with workers.
Owner checks use SessionManager's separate owner databases; raw ContextGuard is
a trusted-host API, not authentication. A crash between an external effect and
its receipt still requires destination idempotency. See
[deployment guide](../docs/DEPLOYMENT_VALIDATION.md) for runner limits and interpretation.

## Actual local provider acceptance and hosted readiness

The verified local-provider run (local-only reference; omitted from public distribution) accepted
**3/3 actual qwen2.5:1.5b responses** through the OpenAI-compatible loopback
endpoint and Relay's output/content adapter. Retries were disabled and source
hashes stayed unchanged. Recorded latencies were 15.34, 7.72 and 9.81 seconds;
token usage was 1,287 prompt tokens and 201 completion tokens. This verifies three
fixed synthetic copy tasks, not hosted OpenAI or Anthropic behavior.

The hosted preflight (local-only reference; omitted from public distribution) records both credentials
absent, no model selected, zero SDK/provider calls and `NOT_RUN`. Credentials are
not retained. Actual hosted acceptance still requires the operator's account,
explicit model choice and permission for any provider charges; this work stayed
local and free of paid API calls.

Retained development evidence includes:

- First local attempt (local-only reference; omitted from public distribution): qwen2.5:3b timed out
  on its first request after roughly 30 seconds; zero completed calls, no retry.
- Second local attempt (local-only reference; omitted from public distribution): all three responses
  were accepted, but source changed during development, so its status is
  `SOURCE_CHANGED`, not a passing frozen-release run.
- Preliminary load (local-only reference; omitted from public distribution): four workers,
  32 isolated sessions and 116 expected action outcomes before final freeze.

The small local acceptance test does not test streaming, native reasoning/tool
history, long-context behavior, general factual accuracy, or a customer's workload.


## Whole tasks with real local model proposals

The first raw-JSON run (local-only reference; omitted from public distribution) produced
**0/8 completed declared tasks in every arm**. The model returned unsupported
argument envelopes. Protected arms stopped before writing or attempting repair;
the unchecked arm wrote eight contract-violating files. This failure is retained
with the original evaluator (local-only reference; omitted from public distribution).

For the second run (local-only reference; omitted from public distribution), the
local provider was given the declared argument envelope as a JSON schema. It
contains field types, not the correct source rows or target setting values.
Fixtures, prompts and scorer remained the same. This is an adaptation after
seeing a failure, not an unseen evaluation or an unbiased general improvement
estimate. The exact evaluator used (local-only reference; omitted from public distribution)
and raw synthetic responses are preserved.

Each task shares one initial proposal across four execution configurations.
Allowed tools write actual JSON files; the grader checks the saved rows and
canonical format/language labels. A block is incomplete work. The run made
15 actual local calls: eight initial proposals and seven repair proposals.

| Execution configuration | Completed declared tasks | Contract-violating writes | Blocked tasks | Additional model calls |
| --- | ---: | ---: | ---: | ---: |
| Unchecked writer | 1/8 | 7 | 0 | 0 |
| Checks, no repair | 1/8 | 0 | 7 | 0 |
| Checks plus known-value repair | 8/8 | 0 | 0 | 0 |
| Checks plus one model repair | 1/8 | 0 | 7 | 7 |

Two initial proposals changed source strings, including translated item names.
One used English when Hindi was requested. Four used the language code `en`,
which this contract's finite English/Hindi labels do not accept. Those four are
compatibility failures, not evidence that the code means the wrong language.
The unchecked grader requires canonical labels; it does not claim every
noncanonical spelling is semantically wrong. Known repair copies the recorded
setting and protected source values; it is not model understanding.

This experiment also caught an overly strict scope comparison: an otherwise
allowed `json` to `JSON` normalization could be flagged as unrelated drift while
fixing source content. The final library compares supported finite memory
equivalents before declaring a scope violation. Trusted facts and custom values
remain strict, and final canonical content checks still run. Four added tests
cover this correction.

The final-source replay (local-only reference; omitted from public distribution) reuses all
15 recorded responses, writes fresh files and produces the same totals above.
It makes **zero new model calls** and retains both source and recording hashes
and its replay driver (local-only reference; omitted from public distribution). Removing the false
scope issue did not make the bad model repairs valid: their remaining content or
setting problems still blocked execution. The current evaluator also normalizes
accidental Windows line endings; executable syntax and grading are unchanged.

These are eight synthetic development tasks on one small local model. The
initial model gets the full original history, so this is a repair comparison,
not a long-context compaction benchmark. The scorer does not check note meaning,
rendered language, downstream rendering, arbitrary arithmetic or source truth.
It cannot establish organisational benefit.

## Evidence versions and unresolved external validation

Files named `*-final.json` predate the finite-memory repair-scope fix; they are
retained candidate evidence. The current release uses `*-verified.json` reports
and the verified recorded-proposal replay. The original
1,169-test run (local-only reference; omitted from public distribution),
installed-wheel check (local-only reference; omitted from public distribution) and
repair comparison (local-only reference; omitted from public distribution) are preserved. Candidate and
verified load profiles both passed; timings varied materially between runs and
are not evidence that the alias fix improved storage performance.

An actual organisation must supply a workflow, baseline, acceptable checks and
privacy requirements before a meaningful pilot can run. Hosted-provider
acceptance also remains not run. The supplied runners make both measurable;
passing synthetic/local checks does not complete either claim.
