> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Validation and next evaluation

## Version 0.23.0a1: instruction understanding and pilot tools

See the [release validation](RELEASE-v0.23.0a1.md) for the frozen wording
comparison, local-model experiment, complete-task checks and package validation.
The [pilot kit](PILOT_KIT.md) is ready for an outside participant; preparing it is
not evidence of an organisational pilot. Earlier numerical results below are
historical observations on their named versions and corpora.

## Version 0.22.0a3: early HTTP rejection

See the [patch validation](RELEASE-v0.22.0a3.md) for the Windows connection-abort
failure found by hosted CI and the bounded connection shutdown fix.

## Version 0.22.0a2: instruction and recovery fixes

See the [patch validation and upgrade notes](RELEASE-v0.22.0a2.md) for mixed-message
memory, chronological scope handling and retained executed arguments during
reconciliation. Earlier results below keep their original release scope.

## Version 0.22.0a1: compatibility and evaluated improvements

See the [0.22 release validation](RELEASE-v0.22.md#validation) for the new local
results, reproduction commands and hosted CI link. The sections below retain
their historical scope; statements about CI not having run describe those
earlier snapshots.

## Version 0.21.0a1: explicit instruction review and checked continuation

The frozen source run (local-only reference; omitted from public distribution) ran
**1,768 tests: 1,767 passed and one POSIX-permission test was skipped on Windows**.
There were no failures or errors. Source/test fingerprints matched before and
after (`d12b87bcfa034f18ef0eb4c2a964fae77c1de70da0be091069af70a22482c625`).
This was Python 3.12.14 on Windows, with the actual optional SDKs listed in the
report. SDK wire tests simulate transports; they do not establish acceptance
by hosted models. The configured GitHub CI matrix has not run remotely.

The new instruction safeguard is explicitly **review coverage, not improved
language recognition**. Before implementation, the default extractor recognized
only **1 of 11 clear changes** in a separately frozen 18-case corpus, and did
not flag its two unclear cases. Five cases were unchanged controls. With
`assurance_fields`, all 18 cases require a source/revision-bound host decision,
including the five harmless controls. This prevents the selected unreviewed
settings from driving a checked action, at the cost of reviewing every new user
turn. It does not automatically understand unfamiliar language. The fixture is
published at `tests/fixtures/instruction-assurance-v021.json`, SHA-256
`48d0edc1193bb35fa0cfaeefe76198a20c9373ab0cf044ad4d78e64b976fd97f`.

The unchanged strong-baseline comparison (local-only reference; omitted from public distribution)
was rerun with this source: real LangGraph checkpoint memory and an ordinary
validator versus the same workflow using Relay guards. Each arm completed
**8 requests correctly**, blocked 3, requested clarification once and performed
2 deterministic repairs. Neither made a wrong effect in the supported cases;
**both made one wrong export in the deliberately incorrect-source control**.
All 13 sends, including that wrong result, remain in the accounting. Both arms
receive the same normalized instructions and recorded proposals. There are no
model calls, no language-understanding comparison and no demonstrated
superiority or speed benefit.

Cross-feature regressions exercise changed source bytes, false success receipts,
unavailable destination readback, stale review plans, scope restoration,
concurrent review/recovery, restart and permission checks. The independent
recovery audit caught a loss of retained checked arguments during reconciliation;
the implementation was fixed and the original test passes. These checks establish
specific boundaries, not a general reliability percentage.

The fresh installed wheel (local-only reference; omitted from public distribution)
ran **146 checks: 145 passed and the same POSIX-only check was skipped**.
It was imported outside the source checkout, has zero runtime dependencies and
matches every source module before and after the checks. Actual SDK/framework
code uses simulated transports here. A separate isolated Python run without
optional SDKs passed the offline quickstart, memory clarification, named restart,
known-value repair and explicit instruction review.

The 96-operation continuation comparison (local-only reference; omitted from public distribution)
uses scripted responses and real local CSV/JSON files. With handoff enabled,
**72/72 required files completed correctly**: 64 on the first attempt and eight
after explicit readback/reconciliation of an interrupted write. Eight unclear
requests and 16 source/permission failures did not execute. There were 54
handoffs, no wrong or repeated action starts, and eight named restart checks
made no additional provider or tool calls. An
independently implemented local checker (local-only reference; omitted from public distribution)
checked the real files, original provider blocks, database records and archive
hashes without importing Relay or the runner.

With handoff disabled, the identical guarded workflow completed 4/72 files,
then encountered 84 preparation-budget failures; eight requests asked for
clarification. This demonstrates continuation within this selected character
budget, **not a comparison against another compaction strategy** or better LLM
reasoning. The stronger LangGraph/validator comparison above remains parity.
No model or network calls were made in the continuation comparison. The host
explicitly finished eight task batches; archived reasoning is omitted from the
new model input.

The installed scripted file pilot (local-only reference; omitted from public distribution)
then exported metadata from the actual final wheel: **8/8 correct exports**,
nine review gates, one deliberately withheld review, two deterministic repairs,
zero wrong files and zero repeated tool starts. Review choices were authored by
the host before execution. This verifies the complete example's mechanisms;
there are zero actual model calls in this profile.

The actual local-model profile (local-only reference; omitted from public distribution)
used the installed final wheel, OpenAI SDK 3.36.0, Ollama 0.40.1 and the already
installed `qwen2.5:1.5b` model. It completed **8/8 exports correctly** with zero
wrong/missing files, errors, timeouts or repeated tool starts. All nine requests
stopped for explicit host review; one deliberately withheld decision made no
model or tool call. There were **eight actual model calls and eight deterministic
repairs**, with no additional repair model calls. All original attempts are
retained. This demonstrates successful checked repair in one selected workflow,
not eight independently correct model proposals or automatic interpretation of
the user's intent.

The profile used 384 maximum output tokens, temperature zero, a 120-second
timeout and zero SDK retries. The eight model calls took 358.74 seconds in total
on this local machine; this is an observation, not a speed benchmark. Host review
choices were frozen before inference. Explicit host task completion triggered
fresh-segment refreshes, so this run alone is not a long-context reasoning test.
The final wheel SHA-256 is
`15a8d7966172aedcdc53e03abc4288dd62ae29b7ad9ee2f8a764365f69c5f7e3`.

Separately implemented local checkers passed **546 consistency checks per pilot**,
reading the files, SQLite records, original provider responses and frozen source
snapshots without importing the application or Relay. Both profiles preserve
seven complete archive segments, and named restart/replay adds zero model or
tool calls. Source, installed module and wheel bytes match. These are development
checks, not an external certification. The local model repairs added seven
missing `format` arguments and restored two empty artifact lists; one request
needed both. The resulting filenames, sizes and hashes match the actual source
artifact. The earlier checker draft's presentation-field assumption was corrected
and its first audit record retained; no product code or pilot result was changed.

The clean, allowlisted public staging copy passed the same **1,767 tests with one
POSIX-only skip**, with the same unchanged source/test fingerprint. It contains
the required nine synthetic fixture files, runnable examples and evaluation
scripts, and no raw local run reports. The final public distribution is checked
byte-for-byte against this tested copy for library, tests, examples and fixtures;
final documentation adds the completed results. ZIP integrity and public manifest
hashes are checked separately. The bundled wheel is unchanged from the local
pilot. Public summaries remain author-reported evidence: omitted raw records
cannot be independently audited from the public bundle alone. Reproduction
commands are in [the v0.21 evaluation guide](../evaluations/RELIABILITY-v021.md).

The release remains an alpha: host-owned source
verification and destination idempotency, explicit review, bounded plaintext
storage, incomplete provider coverage and no organisational pilot remain material
limitations. See [the protection profile](PROTECTION_PROFILE.md) and
[public-alpha boundaries](ALPHA.md).

## Version 0.20.0a1: scoped instructions and a complete action workflow

The final source run (local-only reference; omitted from public distribution)
passed **1676/1676 tests**, zero skips/errors/failures,
with identical source and test fingerprints before and after. The [fresh installed
wheel](../evaluations/installed-sdk-v0.20.0a1-final.json) passed **54/54
checks** outside the checkout using actual SDK/framework code with simulated
transports, plus workflow lifecycle and real local-file destination checks.
Its module bytes match the final source. A separate isolated run passed the
quickstart, memory, named restart and repair checks without optional SDKs.
Core runtime dependencies remain zero.

The [matched framework comparison](../evaluations/FRAMEWORK-v0.20.md) uses real
LangGraph 1.2.16 checkpoint memory and the same normalized instructions,
recorded proposals, source/version checks and idempotent destination in both
arms. Each completes **8 requests correctly**, blocks 3, asks for clarification
once and performs 2 known-value repairs. Both have zero wrong effects in the
supported cases. **Both also produce one wrong export from an intentionally
incorrect trusted source.** The control remains counted as a wrong result.
This is selected action-boundary parity with a strong ordinary validator, not
evidence of superiority, language extraction accuracy, or reduced maintenance.
There are no actual model/network calls in that comparison.

The [actual local self-pilot](../evaluations/RESPONSES-PILOT-v0.20.md) uses the
installed final wheel, OpenAI SDK 3.36.0 and Ollama 0.40.1 with qwen2.5:3b.
It exports real preserved release-artifact metadata to actual JSON/CSV files.
With the frozen six requests, it records **3/5 correctly completed
export tasks**, **1 expected clarification**,
**0 wrong committed documents** and
**0 incorrect or missing materialized files**.
It makes 5 actual local model calls, with
0 returned-result repair attempts and
0 duplicate tool callback starts.
Restart replay makes 0 additional model calls and
0 additional tool callback calls.
Blocked or unfinished requests do not count as successful work. This is one
small developer self-pilot, not organisational validation, hosted inference,
a long-context evaluation, a statistical reliability percentage or a speed claim.

A separately frozen smaller-model profile (local-only reference; omitted from public distribution)
then used the same six requests, instructions and output oracle with
**qwen2.5:1.5b and max_output_tokens=768**. It completed **5/5 exports correctly**,
asked for the expected clarification before inference, and recorded zero wrong
documents, missing/incorrect physical files or duplicate tool starts. All five
proposals required one deterministic repair from recorded values; there were
five model calls and no repair model calls. This is the useful observed milestone:
checked repair helped finish the requested work. It does not erase the 3B
profile's **two timeouts and 3/5 completion**, and these are two selected local
profiles, not a controlled model comparison or a general accuracy claim.

Independent read-only audits of the 3B run (local-only reference; omitted from public distribution)
and smaller-model run (local-only reference; omitted from public distribution)
passed against actual destination files, local SQLite records, call journals,
runner snapshots and installed wheel bytes. The retained-proposal audit (local-only reference; omitted from public distribution)
shows what helped: all five compact-model proposals omitted the export format,
and the last reused an older request ID. Relay supplied the current known values;
artifact names, sizes and hashes stayed unchanged. These repairs happened inside
the first workflow attempt, before any tool ran. “First attempt” therefore does
not mean that the raw model proposal was already correct.

Failures are retained. The first candidate suite (local-only reference; omitted from public distribution)
exposed a Hinglish scope regression and a wall-time failure during simultaneous
local inference. The grammar was fixed without editing the frozen corpus.
The unchanged deadline checks and final full suite passed without simultaneous
inference; earlier timeouts are not retroactively passes. The first pilot (local-only reference; omitted from public distribution)
completed **0/5 exports** and produced no effects: four otherwise valid proposals
were rejected because of unsupported Ollama metadata, and one model response
proposed no action. The three typed metadata fields are now supported and tested;
the final pilot retains the same six requests, model settings and output oracle.
Repeated development cases are exposed regressions, not unseen model accuracy.

[Release behavior](RELEASE-v0.20.md), [complete integration](RESPONSES_WORKFLOW.md),
and [public-alpha boundaries](ALPHA.md) describe the supported synchronous
one-action path. Host identities, source truth, external conditional writes,
retention and permissions remain application responsibilities. CI is configured
but has not run on GitHub; the verified host here is Windows/Python 3.12.14.
There is no organisational pilot or zero-risk guarantee.

## Version 0.19 verification, actions and persistence

The final source run (local-only reference; omitted from public distribution) passed
**1543/1543 tests**, with zero skips, errors or failures and
unchanged source/test fingerprints. The installed wheel (local-only reference; omitted from public distribution)
passed **22/22 actual SDK/framework checks** using simulated
HTTP transports outside the source checkout. A separate installed run with optional
SDKs absent verified basic memory, clarification, named restart, identical tool
contract resumption and computed action repair. Core runtime dependencies remain zero.

The Refund Desk source report (local-only reference; omitted from public distribution)
and installed-wheel report (local-only reference; omitted from public distribution)
both pass **14/14 expected boundary scenarios**, with matching source/example
hashes. These comprise **5 completed tasks, 7 rejected tasks, 1 clarification
and 1 interrupted task**. Six real local receipt files have the expected data;
none of the supported scenarios writes incorrect data. The interrupted effect
is not counted as completed and its old proposal cannot run again.
There are four pre-execution repair attempts, including one rejected repair that
changes an unrelated note; only three repairable tasks complete.

A separate control intentionally supplies **two agreeing but incorrect trusted
sources**. It writes one incorrect refund, which the independent literal oracle
identifies. That result is visibly included in the overall incorrect-effect
count and excluded from supported successes. Relay checks the supplied contract;
it cannot establish external truth. There are 14 scripted proposal calls across
all cases including that control, **zero actual model/network calls**, and no
organisational pilot or model-quality claim. Eleven example/report consistency
tests and 13 independent malformed-report probes prevent missing cases or
rejections from silently becoming completion evidence; reports are not signed
or authenticated evidence.

The final scaling experiment (local-only reference; omitted from public distribution)
uses independent local Python processes with shared and named SQLite files at
one, four and eight workers. All **624 action attempts** meet their expected
outcomes: **208 correct effects, 208 wrong proposals blocked and 208 replays
blocked**. No workers or busy errors are omitted. The source fingerprints match
the release. At eight workers, three-action-job p99 was 629.32 ms shared and
49.85 ms named in this short run. [Methods and limits](DEPLOYMENT_VALIDATION.md)
explain why this does not establish production or distributed capacity.

Adversarial review found and fixed replay holes after interruptions and broken
exception diagnostics, including unusual string subclasses. Regression tests
require a committed failure receipt and exactly one effect before rejecting
replay. Invalid result encoding is handled the same way. A hard process death
or failed database commit still needs destination-side idempotency; no safe
library-only exactly-once claim is made for external effects.

Earlier phase reports and the pre-final scaling report are retained. Some early
phase runs failed because of outdated test fixtures or a misspelled module name;
they are not final-release passes. The final reports above supersede them for
this source. [Release usage and remaining boundaries](RELEASE-v0.19.md).

## Version 0.18 instruction handling

Connected automatic memory now pauses the next model call when an existing
preference is disputed. Recognition expands bounded indirect English,
Hindi/Hinglish, Spanish and French preferences; managed text chats record a local
clarification and resume after a clear answer.

The first independent internal test scored only **1/24 clear changes, 4/18
uncertainties and 17/18 abstentions**. Its failures were retained and used for
improvement. Final **60/60 supported cases** pass as exposed regressions, not
unseen accuracy. Four exploratory German/Italian cases still fail detection.

Final source passed **1280/1280 tests** and the installed wheel passed
**15/15 actual-SDK checks with simulated transports**. The synthetic
memory/action replay passes **60/60 fixtures** with no writes that violate
fixture expectations. It uses a scripted context reader, not an LLM, and is not
an organisational pilot or a measure of real model task completion.
[Full evidence, first failures and limits](../evaluations/INSTRUCTIONS-v0.18.md),
[upgrade guide](INSTRUCTION_CLARIFICATION.md).

## Version 0.17.1 setup patch

The existing `auto_connect(existing_client)` path is now the primary onboarding
example. The beginner guide uses the current local installer. A new installed
`python -m context_relay quickstart` command verifies that the default connection
carries a CSV-to-JSON correction into a local stand-in's received context.

All **1,173 existing tests** and **15 installed SDK/framework checks** passed.
A separate installed check with optional SDKs absent verified both the successful
quickstart and a nonzero exit code for a simulated failed check. No new AI or
hosted-provider calls were made. Memory recognition, provider adapters, context
preparation and action protection are unchanged from 0.17.0.
[Patch scope and evidence](../evaluations/SETUP-v0.17.1.md).

## Version 0.17 observations

Action repair now restricts changes to the failed supported fields. An optional
`repair="known"` mode copies trusted settings and exact protected source data
without a second model call. Normal policy, revision and content checks still
apply. Known memory spellings such as `json` and `JSON` compare equivalently;
trusted facts and custom values remain exact. Started tools are never retried.
`ToolRegistry` consolidates function registration, argument checks and revision
binding. It reports unchecked and missing tool coverage, but cannot protect a
call that bypasses it or infer an application's intended checks.

The final source passed **1,173/1,173 tests**, zero skips, errors or failures.
The installed wheel passed **15/15 actual-SDK tests with simulated transports**
outside the source tree, and a separate smoke check ran without optional SDKs.
The package has zero runtime dependencies. Reports retain unchanged source/test
fingerprints and installed/source byte comparisons.

Shared SQLite profiles with one, four and eight processes passed all **1,361
expected action outcomes across 432 isolated sessions**. These counts include
rejections and ownership checks. Final measured group p99 latency rose from
38 ms to 1.41 seconds; this is a short local exercise, not a capacity guarantee.
Three actual local-model responses also passed the output/content adapter check.

An eight-task local experiment first failed to produce supported argument
shapes: all four configurations completed **0/8** tasks. After adding an explicit
provider argument schema, known-value repair completed **8/8** declared tasks;
checks alone and one model repair completed **1/8** each. Every protected arm
had zero contract-violating writes. Four unchecked failures were unsupported
`en` language-code labels, not wrong-language meaning. Two changed source strings
and one used the wrong language. These are inspected synthetic development
cases, not a customer pilot or long-context benefit estimate. The final-source
replay reused recorded proposals with zero new model calls and identical totals.
Both original trials and the scope-normalization defect they exposed are retained.

[Detailed protocol, counts and limitations](../evaluations/RELIABILITY-v0.17.md),
full suite (local-only reference; omitted from public distribution),
installed package (local-only reference; omitted from public distribution),
[repair guide](REPAIR_SAFETY.md), and
[deployment runner and measurements](DEPLOYMENT_VALIDATION.md).

Hosted-provider credentials were absent and no paid calls were made. Hosted
acceptance and organisational reliability remain **unvalidated**. The supplied
runners prepare those checks; a real workflow and deployment are still needed.
Earlier reports named `*-final.json` are retained pre-normalization candidates;
the current release uses `*-verified.json` reports and recorded-proposal replay.

## Version 0.16 observations

Relay can now protect application-selected names, numbers and rows against a
source snapshot, in addition to checking settings. The contract is applied
before execution and again after an optional repair. It also works with complete
JSON replies. These local checks make no model calls and do not establish general
factual or semantic correctness.

All **37 internal content cases** pass: 12 allowed and 25 blocked, with no false
allows or false blocks in this set. Replaying two recorded bad v0.14 repairs
shows that format/language checks alone permit both renamed exports; adding the
content contract blocks both before writing. Supplying manually corrected
proposals then produces two valid exports, each written once. This is recorded
proposal replay with static corrections, not a new model-recovery measurement.

The first evaluator accidentally included manually restored controls in its
failed-proposal replay cohort. Its 37 content cases already passed, but only
6/12 replay arms met the mistaken expectations. The corrected cohort gives 6/6
across the two actual failures. Original reports, the first evaluator and
artifacts remain available. Neither library source nor case labels changed
between those runs. [Protocol, raw reports and scoped outcomes](../evaluations/CONTENT-v0.16.md).

The full suite passed **1,098/1,098 tests**, zero skips, errors or failures, with
unchanged source/test fingerprints. The offline-installed wheel passed **15/15
actual-SDK checks** using simulated transports outside the source tree; installed
modules matched source and the package has no runtime dependencies. An additional
installed-core smoke check ran with optional SDKs absent. It blocked changed
content and a bad repair, executed one valid repair and checked exact decimal
JSON through configuration and connected output. No new model calls were made.

Full-suite report (local-only reference; omitted from public distribution),
log (local-only reference; omitted from public distribution), and
installed-wheel SDK report (local-only reference; omitted from public distribution).
Hosted-service acceptance and organisational benefit remain unmeasured.

Applications choose the source snapshot and protected fields; undeclared prose
and arithmetic remain unchecked. Tool rendering after dispatch is application
code. All workers sharing a store must upgrade before relying on these new
policies. The [content integration guide](CONTENT_INTEGRITY.md) explains exact
scope, decimal handling, bounded inputs and source updates. The earlier memory
recognition and model measurements below remain historical evidence.

## Version 0.15 observations

The correction changes pass all **118 now-known wording cases**: **52/52 clear
updates**, **26/26 clarification cases** and **40/40 misleading controls**. There
are no wrong active updates or false clarification alarms in these sets. Exact
source spans, replay stability and unchanged trusted facts pass for every case.
These are development regressions, not a universal language-accuracy claim.

The original 40-case corpus improved from 5/16 to 16/16 clear changes and from
6/8 to 8/8 required clarification flags. A separate agent authored 78 additional
cases before the first parser freeze. **The first unseen run failed to generalize:
0/36 clear changes, 0/18 correct clarification flags, 22/24 correct abstentions,
one wrong active update and one false alarm.** All original labels and failures
are retained. The final passing results came after exposure and tuning; no new
unseen score is claimed. [Protocol, before/after results and raw evidence](../evaluations/CORRECTIONS-v0.15.md).

The new message-local planner combines named preferences, old-value evidence,
linked support clauses and explicit replacements. It preserves declaration
review, exact custom field identity and ambiguous-target clarification. Existing
quote, hypothetical, temporary-use and authority boundaries remain in force.
A new unclear request cannot be hidden by a repeated interpreter suggestion.
Upgrade applies only to newly consumed messages; historical forget/dismiss
choices are not replayed. Memory schema 2 is unchanged.

The full suite passed **1051/1051 tests**, with zero skips, failures or errors. Source/test fingerprints stayed unchanged throughout the run. The built wheel then passed **15/15 actual-SDK checks** outside the source tree, using simulated transports. Every installed module matched source bytes. A separate isolated installed-core smoke check ran without any optional SDK: it recognized a composed correction, blocked a mismatched action, repaired its arguments once, stopped on uncertainty and resumed after explicit clarification. No new local or hosted model requests were made.

Full-suite report (local-only reference; omitted from public distribution),
log (local-only reference; omitted from public distribution), and
installed-wheel SDK evidence (local-only reference; omitted from public distribution).
The actual SDK versions in this run are recorded in the full-suite report.
Mock transports establish local integration behavior, not hosted acceptance.

Thirty-five literal substitutions check the known grammatical forms across
12 formats, 19 languages and four styles. Separate tests exercise persisted
clarification, blocked actions and a later successful file write. These use
local fixtures/stubs. All 118 corpus cases also run under the default memory
budget in the unit suite; the unchanged standalone grader uses 16,000 characters.

No organisational pilot or new model trial was performed for v0.15. The
completed-work model measurements below are historical v0.14 results. Unknown
wording can still escape both interpretation and uncertainty detection. Finishing
these regression cases does not prove that Relay prevents all context rot.

## Version 0.14 observations

The final automated suite passed **1,016/1,016 tests**, with zero skips, failures
or errors, on Windows/Python 3.12.14. Source/test fingerprints were identical
before and after the run. Machine-readable result (local-only reference; omitted from public distribution)
and full log (local-only reference; omitted from public distribution) are retained.

The separate installed-SDK checks (local-only reference; omitted from public distribution)
passed **15/15**, zero skips. They use OpenAI 3.36.0, Anthropic 2.0.0, Ollama
0.6.3, langchain-core 1.6.13 and LangGraph 1.2.16 with simulated HTTPX/HTTPX2
responses. The environment freeze (local-only reference; omitted from public distribution)
is retained. These establish adapter behavior with actual SDKs; hosted-service
acceptance and organisational reliability remain untested.

The built wheel was installed offline into a fresh consumer directory without
installing dependencies. Its installed-wheel SDK tests (local-only reference; omitted from public distribution)
passed **15/15**, zero skips, from outside this source tree. All installed Python
modules matched the frozen source bytes. A separate isolated `-I -S` smoke check
ran with every optional SDK absent: it blocked the first mismatched action,
repaired its arguments once, canonicalized `json` to `JSON`, and invoked the
executor exactly once. Package metadata declares zero runtime dependencies.
Installed-wheel test log (local-only reference; omitted from public distribution).

This release adds clarification-only uncertainty, finite preference spelling
normalization, and one optional pre-execution repair callback. The model-facing
library remains dependency-free and makes no new model calls by default. A repair
callback is application-owned and can add inference work when explicitly supplied.

Forty new synthetic wording cases were authored by the root agent and hidden
from the correction implementer until its parser was frozen. The root also
reviewed code paths before freeze; this is an internal evaluation, not external
human/customer evidence. Casing equivalence within declared preference enums is
graded separately from exact canonical spelling. All five accepted v0.14 values
also matched the canonical spelling.

| Wording outcome | v0.13 baseline | Frozen v0.14 | After observed safety fixes |
| --- | --- | --- | --- |
| Clear automatic corrections | 1/16 | 5/16 | 5/16 |
| Correct clarification-only flags | 0/8 | 6/8 | 6/8 |
| Misleading controls left unchanged | 15/16 | 15/16 | 16/16 |
| Incorrect active entries | 8 | 2 | 0 |
| False clarification alarms on controls | 0 | 0 | 0 |

The first frozen result exposed two false promotions through generic declaration
parsing: a relational preference became an unrelated field, and sample framing
became a stored field. A narrow subsequent fix rejects those declaration/framing
paths; it does not add positive grammar for the missed cases. **The last column
is a post-holdout regression, not unseen evidence.** Original cases, baseline,
frozen report and exact pre-fix parser bytes remain available. Source spans,
replay stability and unchanged authority passed for all cases. No model or
interpreter calls were used for this wording check.

[Corpus](../evaluations/corrections-v0.14-holdout.json),
baseline (local-only reference; omitted from public distribution),
frozen result (local-only reference; omitted from public distribution),
post-fix result (local-only reference; omitted from public distribution), and
pre-fix source (local-only reference; omitted from public distribution) are retained.
The two missed unclear changes still leave old memory undisputed. Uncertainty
detection is bounded and incomplete; a zero-error regression is not a general
accuracy guarantee.

Known format/language/style spellings are canonicalized before tool execution,
with the original and executed spelling recorded. Trusted fact requirements and
custom memory keys stay strict. Action repair is synchronous and limited to one
callback per invocation; missing/disputed memory requests clarification, stale
state and failed permissions remain blocked, and a started executor is never
retried. Tests use actual files and concurrent database updates, including errors
after partial effects. A matching argument does not establish correct prose or
correct report rows. [Integration and boundaries](MEMORY_ACTIONS.md).

Writing the first clarification-only record migrates that session to memory
schema 2. Version 0.14 reads schema 1; older v0.13 processes reject schema 2 rather
than treating null as a confirmable value. Use the same supported version for
workers sharing a session. Managed-history rollover preserves uncertainty and
its original source; a later explicit clarification can resolve it before acting.

No hosted provider requests or organisational pilot have been performed.

### Completed tasks with an actual local model

The frozen evaluator made **17 physical requests** to the already installed
Qwen2.5:3b model: seven on three predeclared development cases, then ten on four
new cases authored after the final parser and evaluator freeze. No model was
downloaded, no paid API was used, and there were no inference retries. This is a
small, internal synthetic export workflow, not an organisational pilot.

The new set contains three concrete export tasks and one unresolved preference
that should request clarification. The two checked arms share exactly the same
initial proposal in isolated copies of the same state; only the repair arm can
make one additional call per case.

| Fresh-set outcome | Recent-turn baseline | Relay checks | Relay checks + repair |
| --- | ---: | ---: | ---: |
| Correct completed concrete tasks | 1/3 | 1/3 | 2/3 |
| Incorrect writes across all four cases | 3 | 0 | 1 |
| Correct clarification on the unresolved case | 0/1 | 1/1 | 1/1 |
| Extra model calls | 0 | 0 | 2 |
| False blocks / request errors | 0 / 0 | 0 / 0 | 0 / 0 |

One repair turned a blocked proposal into a correctly completed export. The
other repair passed the format/language checks but translated item names that
the task explicitly required unchanged, so it produced an incorrect write.
The successful repair case protected an existing preference against a quoted
alternative; it is not evidence that every new correction can be recovered.
The clarification case stayed incomplete and made no repair call. The baseline's
fixed output schema has no clarification action, so that row demonstrates host
routing, not superior model clarification judgment.

On the three development cases, completions were 0/3, 1/3 and 1/3; incorrect
writes were 2, 1 and 2. The single repair added a wrong write and no completion.
One baseline request timed out at the declared 90-second limit and was retained
without retry; timing/cold-start differences therefore confound the development
comparison. The compound wording case still enforced an outdated preference.

Repair remains **optional and off by default**. It can finish a blocked task,
but it can also introduce wrong content in arguments that the application's
policy does not validate. This release does not show a general reliability,
latency or cost advantage. Initial requests were capped at 3,600 serialized
message characters, repairs at 8,000; the extra input and inference are part of
the measured intervention. Three additional repair calls were used overall.
Reported model time totals 829.804 seconds; token totals exclude usage unavailable
for the timed-out request. Neither run had false blocks or unnecessary
clarifications under the declared grading rules.

The post-run integrity report (local-only reference; omitted from public distribution)
checks all 17 request/result pairs, 21 graded arm outcomes, the shared initial
proposals, exact repair inputs and all 30 persisted artifact files. The
exact audit script (local-only reference; omitted from public distribution) is retained
as a maintainer-workspace snapshot, including its original path setup. Raw
development (local-only reference; omitted from public distribution) and
fresh (local-only reference; omitted from public distribution) journals, exact
source/helper/fixture snapshots and generated files are retained. The corpus is
internally authored and small; language grading uses a label and script check,
not translation-quality assessment. See [the protocol](WORKFLOW_EVALUATION.md)
and [pilot guide](PILOT.md) for reproduction and limits.

## Version 0.13 observations

The final automated suite passed **943/943 tests**, with zero skips, failures or
errors, on Windows/Python 3.12.14 in the installed-SDK environment. Source/test
fingerprints were identical before and after the run. Machine-readable results (local-only reference; omitted from public distribution)
and the full log (local-only reference; omitted from public distribution) are retained.

The separate actual-SDK checks (local-only reference; omitted from public distribution)
passed **15/15**, zero skips. They use OpenAI 3.36.0, Anthropic 2.0.0, Ollama
0.6.3, langchain-core 1.6.13 and LangGraph 1.2.16, with simulated HTTPX/HTTPX2
transports. The environment freeze (local-only reference; omitted from public distribution)
is retained. These tests establish local adapter/SDK behavior, not real hosted
service acceptance or model reliability.

The built wheel was installed into a separate directory without installing any
dependencies. Its 15/15 installed-SDK checks (local-only reference; omitted from public distribution)
passed from outside the source tree with zero skips. Every installed Python
module matched the source bytes, and package metadata declares zero runtime
dependencies. An isolated `-I -S` check also imported the installed core with all
optional SDKs absent and blocked a mismatched memory-bound action before its
side effect. Installed-check log (local-only reference; omitted from public distribution).

The correction implementer was not shown the root-authored 32-case corpus until
its parser was frozen. On the same cases, the v0.12 rules applied 0/16 clear
changes; v0.13 applied **3/16**, proposed **1/4** required reviews, abstained on
**12/12** misleading controls and made zero wrong active updates. Exact source
provenance passed. These synthetic cases are agent-authored, not an external
human/customer benchmark. [Raw before/after reports and source fingerprints](../evaluations/CORRECTIONS-v0.13.md)
remain distinct from the old-set development result (11/12).

The unchanged optional interpreter made **8 actual local Qwen2.5:3b calls** on
eligible cases. It added two exact clear suggestions and one exact review
suggestion; all remained pending and none were confirmed. Combined with rules,
5/16 clear cases were applied or proposed exactly, but only 3/16 were applied.
Two case mismatches (`json`, `hindi`) and three empty model replies remain
failures. Twelve misleading controls made no model calls. The raw journal,
options, source hashes and replay checks are linked in the correction report.
This is evidence of a limited review helper, not reliable general understanding.

`memory_bindings` extends the existing tool execution boundary: configured
arguments must match current undisputed recall before a synchronous tool runs.
Checks share the existing transaction/revision guard; memory cannot override
trusted requirements or authorize another tool. Tests cover real file-write
prevention, stale/disputed/missing values, persistence and custom memory limits.
It does not validate arbitrary meaning, and a missed correction can cause an
old preference to be enforced. [Integration and limits](MEMORY_ACTIONS.md).

Native memory placement is now opt-in. OpenAI uses separate `instructions`;
Claude appends an inline system message and checks that later calls replay the
sent prefix exactly. Trusted facts still come from the application. More native
custom-tool/media families have local serialization checks; hosted provider
acceptance and signatures remain unverified. No paid provider calls or real
organisational pilot have been performed.

An actual local Qwen2.5:3b workflow made **18/18 planned calls**, without errors,
timeouts, retries or repair calls. The six synthetic export cases used the same
history fixtures in three arms. Each arm completed **2/6 tasks correctly**:

| Outcome | Recent-turn baseline | Relay memory | Relay memory + action checks |
| --- | --- | --- | --- |
| Correct complete tasks | 2/6 | 2/6 | 2/6 |
| Wrong actions executed | 4 | 4 | 1 |
| Wrong proposals blocked | 0 | 0 | 3 |
| Correct proposals withheld | 0 | 0 | 0 |

The gate allowed one incorrect export because an unrecognised compound correction
left the old CSV/English settings in memory. A matching remembered value therefore
did not imply a correct action. Blocked proposals are incomplete tasks, not
successes. Relay memory and gated arms received byte-identical prompts/options;
they were independent model calls, and prompt-cache reuse confounds timing. No
latency or task-completion improvement is claimed. [Protocol, actual requests,
responses, persistent output files and frozen source](PILOT.md) are retained.
This is a small local synthetic experiment, not an organisational pilot.

The [hosted smoke runner](PROVIDER_SMOKE.md) supports explicit two-call replay,
tool-cycle and native-memory checks. Its release report is **NOT_RUN** with zero
hosted calls. Offline SDK acceptance tests cannot establish real provider acceptance.

Reporting correction: the v0.12 h06 fixture contains U+2014 (an em dash), not
U+FFFD. Its console display was misread in the previous release. The historical
result is **1/12**, not 1/11. Original raw/derived files are retained; the
[historical report](../evaluations/CORRECTIONS-v0.12.md) now explains the erratum.

## Version 0.12 observations

The final suite passed **877/877 tests with zero skips, failures or errors** on
Windows/Python 3.12.14 in the installed-SDK environment: 52.375 seconds in unittest,
53.164 seconds including discovery/reporting. The machine-readable report (local-only reference; omitted from public distribution)
records package versions and identical source/test fingerprints before and after;
the full log (local-only reference; omitted from public distribution) is retained. No model inference
is part of this automated suite. Real local-model checks are reported separately.

The built wheel was then installed into a separate directory with no dependency
installation. All 13 installed-SDK checks passed (local-only reference; omitted from public distribution)
with zero skips/errors from outside the source tree. Every installed Python
module matched the frozen source byte for byte; wheel metadata declares zero
runtime dependencies. The installed-check log (local-only reference; omitted from public distribution)
is retained. These still use mock provider transports, not hosted inference.

The unseen correction check is the main caution: the frozen parser applied
**1/12 clear corrections**,
proposed 2/3 required reviews and abstained on 8/9
controls. It incorrectly promoted `it=JSON` once. A targeted **post-holdout**
pronoun safety fix achieved 9/9 abstentions and zero observed wrong updates,
while clear recall stayed 1/12 cases. That rerun is no longer unseen evidence. The
70/70 implementer-authored development score must not be used as general
natural-language accuracy. [Corpora, raw results, misses and source fingerprints](../evaluations/CORRECTIONS-v0.12.md)
are published separately; the earlier failed reports remain unchanged.

Six actual local `qwen2.5:3b` extraction calls used the existing interpreter
prompt with a generic JSON schema, temperature 0, seed 17, 4,096 context tokens
and a 512-token generation cap. They produced **3/4 exact clear candidates**
and **2/2 ambiguous abstentions**. The fourth candidate lowercased `JSON` to
`json`; it remains a failed exact check. Four additional misleading controls
were filtered without inference. Every suggestion stayed pending, source quotes
matched, active values stayed unchanged, and replay added no model call. The
journal (local-only reference; omitted from public distribution) includes complete raw
synthetic requests, responses, reported tokens and per-call durations. This is
one model/seed and a small authored set, not general semantic accuracy or a
controlled improvement over the different v0.9 cases.

Native provider tests check whole visible-request budgets, SDK-object
serialization, exact opaque-field replay, tool dependency validation, request
override rejection, async/cancellation behavior and response/stream ownership.
Native mode never inserts memory or prunes history. It cannot authenticate
thinking signatures, decode encrypted state or measure provider-hidden context.
Automatic memory remains on the separate text paths.

The installed-SDK verification (local-only reference; omitted from public distribution)
passed **13/13 tests, zero skips**, in 41.328 seconds on Windows/Python 3.12.14.
It used these exact versions:

| Optional package | Tested version |
| --- | --- |
| OpenAI | 3.36.0 |
| Anthropic | 2.0.0 |
| Ollama | 0.6.3 |
| langchain-core | 1.6.13 |
| LangGraph | 1.2.16 |

HTTPX 0.28.1, HTTPX2 2.16.0 and Pydantic 2.14.1 were also installed; the
full environment freeze (local-only reference; omitted from public distribution)
is retained. These are real SDK/framework classes with **mock HTTP transports**,
not hosted-model calls. Checks cover serialization, native replay, response and
stream objects, automatic routing, Ollama attachment and framework invocation.
They cannot verify provider acceptance, signed-state validity or every version.
The initial unsuccessful run (local-only reference; omitted from public distribution)
is preserved. Tests also exposed malformed SDK fixtures and a beta-header/body
distinction, which were corrected before the final run. A docs-derived compact
fixture containing a retained user item fails OpenAI 3.36.0's strict typed response
validation; the actual SDK raw-response path preserves it. This simulated fixture
does not establish a hosted API defect; see [the exact caveat](OPENAI.md).

The actual local LangGraph/Ollama workflow comparison (local-only reference; omitted from public distribution)
completed **2/6 correct tasks in each arm**. All twelve attempts produced files,
but the other eight outputs failed at least one declared requirement. Relay
matched the requested format in 3/6 cases versus 2/6 for the example's recent-history
baseline; both matched the language-label/script check in 3/6 and rows in 6/6.
This is no measured improvement in overall task completion. An unexplained
approximately eight-hour elapsed-time anomaly makes latency comparison unusable.
The retrospective diagnostic found correct recorded preferences in 5/6 Relay
cases; three still produced wrong model preference labels despite that memory.
The compound-language case left stale preferences in memory. Recording a fact
does not ensure that a model follows it.
The baseline is an example recent-whole-turn selector, not a customer's setup or
native provider compaction. See [pilot scope and limitations](PILOT.md).

## Version 0.11 observations

Version 0.11.0 on Windows/Python 3.12.14: **793 automated tests passed** in
30.477 seconds. The 112 added tests cover expanded correction grammar, automatic
rollover/indexed search/logical deletion, framework integration, scoped access
and concurrency, paired pilot evaluation and combinations of those features.
Review and tests found and fixed duplicate from/to field parsing, FTS token
remnants after deletion, resource ownership, revocation/error disclosure and
pilot scoring/measurement defects. The first full run exposed a redundant setup
authorization check; configuration loading now occurs inside the authorized
operation, and the complete final suite passes.

The frozen 65-case **authored development corpus** changed from 37/65 to 65/65:
23 clear named updates, 6 review-required outcomes, 33 excluded/unsupported
statements and 3 regression controls. Passing the exclusion cases means memory
was not promoted, not that the sentence was understood. Exact corpus and parser
fingerprints, including the unsuccessful baseline, are preserved in the raw
before (local-only reference; omitted from public distribution) and
final (local-only reference; omitted from public distribution) reports. These runs preceded
the version bump, so their version field remains 0.10.0. No new LLM accuracy or
general language-understanding result is claimed.

A synthetic search check (local-only reference; omitted from public distribution) used 10,000
messages and one rare token. Literal search decoded 10,000 rows; indexed terms
search decoded one matched row, with the FTS query plan recorded. The modes have
different matching rules and this fixture favors a selective term. It is not a
production speedup measurement; index construction/rebuild can block writers.

The local concurrency exercise (local-only reference; omitted from public distribution)
completed **96/96 conversations** across separate 1-, 4- and 8-thread runs, each
with 32 conversations, four owners/databases and two fixed-function responses.
It exercised actual SQLite, memory and scoped operations, with raw durations,
failures and numeric metrics recorded. No model, network, cross-process workers
or production traffic were involved. This does not establish service capacity.

The pilot practice journal (local-only reference; omitted from public distribution) is
deliberately synthetic: a fixed JSON answer succeeds in one case and fails the
CSV case in **both** modes (1/2 each). The runner preserves that failure and
records unknown manual effort as null. No real project was supplied and no real
organisational pilot has been conducted. See [pilot setup](PILOT.md) for plugging
in the actual baseline, consented cases and shared model settings.

LangChain/LangGraph and provider contracts use labelled stand-ins. The optional
framework is absent locally, so live framework/SDK compatibility remains
unverified. No paid APIs, model downloads or additional model-inference calls
were required by this release. Optional framework dependencies are imported only
when that integration is used; the core still has zero runtime dependencies.

Scope limits remain: context-only corrections need review, automatic rollover
does not preserve every unfinished task detail, deletion is not secure physical
erasure, and scoped sessions require host authentication/private filesystem
access. Concurrency limits and metrics are process-local. The earlier actual
model failures below remain published.

## Version 0.10 observations

Version 0.10.0 check on Windows with Python 3.12.14: **681 automated tests passed**
in 23.636 seconds. The 32 added tests exercise `auto_connect`: loaded SDK identity
detection, explicit custom providers, memory defaults/configuration/disable,
sync/async and decorated functions, no client mutation, response and stream
ownership, isolated sessions on a shared client, persistence/resume, native
protocol boundaries, optional-policy forwarding and rejection before storage
creation. Review found a decorator hiding an existing Relay connection; a
regression now verifies that it cannot create a second wrapping connection.

The offline `automatic_connection.py` example ran two fixed-function calls and
retained the user's CSV-to-JSON change. This demonstrates setup and memory
plumbing, not AI inference. The SDKs are not installed in this validation
environment, so provider detection and routing use explicitly labelled stand-ins.
No hosted API requests, model downloads, or additional model evaluations were
made for this setup-only change. The v0.9 real-model failures below remain
applicable limitations; easier connection does not improve interpretation.

`auto_connect` adds no runtime dependencies or background service. Basic memory
is on for this new shortcut; existing explicit connection defaults are unchanged.
Provider detection happens at setup and does not probe endpoints, read credentials
or modify SDK clients. Full append-only history or managed text conversations
and one Relay session per conversation remain necessary. See
[automatic connection](AUTO_CONNECT.md) for the supported scope.

## Version 0.9 observations

Version 0.9.0 check on Windows with Python 3.12.14: **649 automated tests passed**. The 101 added checks cover optional reviewed interpretation, cancellation and concurrent changes, output contracts and bounded retries, linked archival and request replay protection, Anthropic text mapping/decoding, and combinations of those features. The source suite completed in 22.699 seconds; this is not a runtime performance benchmark. One earlier full run had a Windows loopback socket abort in an existing workbench test. The focused server suite and final complete suite passed without changing code or adding retries.

The default deterministic memory regression retained **37/37 language, 8/8 framing, and 13/13 lifecycle checks**. Those grammar controls do not establish semantic understanding. Its raw report (local-only reference; omitted from public distribution) preceded the final interpreter-prompt revision; the complete final suite covers that revision.

The new real-model evaluation deliberately retains unsuccessful outcomes. Across three development/final runs, **11 actual local calls** used installed Qwen 2.5 1.5B or 3B models. In the final run, the reviewed-interpretation helper missed **both** intended changes; no guessed value became active memory. With provider-native JSON constraints, the model answered the archived-owner question correctly and the original evidence remained searchable. This is one successful archive/output workflow, not evidence that interpretation, long-term memory or context rot is solved. See [all v0.9 results and limits](../evaluations/LIMITS-v0.9.md).

The output checker also blocked two prose answers that violated the JSON contract during the initial run. Those blocked-but-unfinished requests count as failures to complete the task. Native generation constraints complement validation; they do not establish factual correctness. The optional interpretation helper remains experimental and off by default.

OpenAI, Anthropic and Ollama SDK connections use labelled stand-ins in automated tests. No hosted API or live SDK verification was performed. Real inference uses Ollama's local HTTP API, with no model downloads or paid requests. The library adds no runtime dependencies; optional interpretation and correction retries add calls only when enabled.

## Earlier release observations

Version 0.8.0 check on Windows with Python 3.12.14: **548 automated tests passed**. The 91 added checks cover the expanded English grammar, compact memory projection, managed text-history persistence and failures, request-ID replay protection, sync/async cancellation, OpenAI message validation and adapters, and integration across those features. Run the source suite with `src` on `PYTHONPATH`, or install the package first.

The v0.8 release evaluation passed **37/37 declared-language cases, 8/8 framing checks, and 13/13 lifecycle checks**. One previously unsupported phrasing now has an explicitly changed expectation, and nine added cases are separately labelled; the original v0.7 corpus and raw reports remain available. Unsupported-language controls still measure safe omission, not understanding.

A controlled fixture with 12 remembered values and one unresolved conflict used **1,877 characters of model-facing memory instead of 4,369** (57.04% fewer). All values, conflict details and exact source quotes remained. Full audit metadata stays local. This measures serialized characters in one fixture, not token savings, faster execution or better answers; memory/storage caps still apply.

Four actual local Qwen 2.5 1.5B calls again produced **2/2 exact answers with memory versus 0/2 without**, with no transport errors. A separate two-call managed-conversation smoke report (local-only reference; omitted from public distribution) verifies database resume, automatic correction, original response preservation and blocking a completed duplicate without a third provider call. These are small synthetic checks, not production reliability evidence. See [the v0.8 analysis](../evaluations/MEMORY-v0.8.md) and raw memory results (local-only reference; omitted from public distribution).

OpenAI and Ollama SDK integration tests use labelled client/response stand-ins; neither SDK is installed here, and no hosted API request was made. The real inference above uses Ollama's local HTTP API. OpenAI Chat text/function-call history and Responses text-only support are implemented, but live SDK/API compatibility remains unverified. Managed conversations accept complete plain text, not streaming, tool workflows or reasoning state. There are no new runtime dependencies or model calls for memory extraction.

Version 0.7.0 check on Windows with Python 3.12.14: **457 automated tests passed**. The 114 new tests cover reversible instance attachment, existing positional/keyword call sites, unchanged provider responses/streams, sync/async behavior, automatic memory without a field schema, exact provenance, durable replay prevention, restart/resume, pending disputes, review/forget/undo, atomic budgets, and memory isolation from tool permissions. Independent integration checks exercise two-store SQLite snapshot consistency, provider failures/cancellation, unchanged original evidence, and refreshed memory on grounded retries.

The optional Ollama Python SDK is not installed in this environment. Attachment tests use explicitly labelled SDK-signature/class stand-ins; they do not establish live compatibility across SDK versions. The real model evaluation uses the local Ollama HTTP API. Official [Ollama Python client documentation](https://github.com/ollama/ollama-python) and [client source](https://github.com/ollama/ollama-python/blob/main/ollama/_client.py) informed the adapter.

The final v0.7 evaluation passed **28/28 declared-language cases, 8/8 additional framing checks, and 13/13 lifecycle checks**. Two of the 28 cases intentionally verify that unsupported natural phrasing is left unrecorded; passing those checks does not mean that intent was understood. The added framing audit first exposed 8 false memory captures; those failed results remain published alongside the fix.

Four actual local Qwen 2.5 1.5B calls on the finished library produced **2/2 exact answers with memory enabled versus 0/2 without it**, with no transport errors. Both settings used the same original histories and 5,000-character cap; the source turns deliberately exceeded the cap to isolate memory retention. This is a small synthetic smoke comparison, not general accuracy, latency, market-superiority or production-readiness evidence. Read [the complete v0.7 analysis](../evaluations/MEMORY-v0.7.md) and raw release results (local-only reference; omitted from public distribution).

Automatic memory still uses a limited English grammar. Mixed prose can be skipped; unresolved corrections stay visible for review; caps can stop requests; original full history and one conversation per attachment remain application responsibilities. State revisions do not advance for unrelated ignored input, so the host still controls request ordering and cancellation.

Version 0.6.0 check on Windows with Python 3.12.14: **343 automated tests passed**. New coverage includes conservative schema-guided correction proposals, explicit confirmation/dismissal and stale-review rejection, source identity and literal quote validation, bounded synchronous/asynchronous recovery, automatic lookup after failed citations, stopping without enough evidence, request-budget rechecks, ranked-candidate reduction, stale responses, sanitized retry feedback, stream cleanup, and complete native Ollama tool exchanges. Existing v0.5 connection and earlier core checks remain passing.

`examples/correction_review.py` passed with a clearly labelled test transport: it detected a JSON-to-CSV request, made zero model calls before review, and proceeded only after simulated host approval. No model inference or implicit approval is claimed by that example. The v0.6 [recovery evaluation](RECOVERY_EVALUATION.md) separately records actual model outputs and unsuccessful tasks.

The final v0.6 local Qwen 2.5 1.5B run completed **0/2** tasks with bounded recovery, **0/2** without recovery, and **1/2** with the larger full-history reference. Recovery restored the buried record but rejected the model's incorrect quote; the other case stopped at a required-evidence budget failure. This is not evidence of improved accuracy. [All three development runs and their limitations](../evaluations/RECOVERY-v0.6.md) are published, including the initial run's 1/2 recovery result.

Version 0.5.0 check on Windows with Python 3.12.14: **198 automated tests passed**, including 33 connection tests and 15 configuration/setup tests. The new checks cover automatic preparation on repeated model calls, synchronous and asynchronous clients, preserved provider responses/options, unconsumed streams, cancellation/errors without retries, per-call context under concurrency, separate conversation sessions, explicit resumption, guard ownership, strict JSON configuration, and non-overwriting setup. Unsupported call signatures fail before creating a database.

The connected-agent example passed with its clearly labelled offline test transport. Two additional real local Qwen 2.5 1.5B requests also completed through one connection: no manual preparation calls were needed in the model loop, the host's JSON-to-CSV correction reached the second request, and the actual second response mentioned CSV and offline operation. Exact inputs and responses are in the v0.5 integration smoke report (local-only reference; omitted from public distribution). The report checks integration and input handling; it is not a new accuracy benchmark or an automatic-fact-extraction test.

Version 0.4.0 check on Windows with Python 3.12.14: **150 automated tests passed**. This includes 28 token-budget tests, 24 evidence-recovery tests, and 8 local-evaluator tests. Checks cover full-message counting and reserves, independent character/token caps, invalid counters, immutable preparation options, exact whole-turn recovery, session/history mismatch, current-state recompilation, unchanged old action revisions, and strict evaluation scoring/error persistence.

The offline recovery example restored an omitted exchange within the same 1,800-character cap while retaining a newer recorded format correction. The example makes no model call. The new APIs remain dependency-free and preserve the existing plain-text protocol boundary. Token counters are tested using controlled implementations and a tokenizer test double; this release does not claim an end-to-end live Hugging Face tokenizer/server count match.

An actual local Ollama run used `qwen2.5:1.5b` for **15 responses across five synthetic tasks**. Exact answer plus exact source-label completion was **2/5 with Relay retrieval, 1/5 with the recent-history control, and 1/5 with full history**. All modes received the same recorded facts and explicit pins. Answer-only scores were 4/5, 3/5, and 4/5 respectively. There were no transport errors, and every wrong answer or citation remains in the bundled raw report (local-only reference; omitted from public distribution). See the [results and limitations](../evaluations/README.md) and [evaluation design](LOCAL_EVALUATION.md).

This small one-model run is not evidence of market superiority or a context-rot reduction percentage. It exercises selection and model answer/citation behaviour, not a production tool workflow, the new recovery loop end to end, or the optional token counter against Ollama. The same 3,200-character comparison cap is used for both bounded modes; Ollama reports actual tokens after each call.

Version 0.3.0 check on Windows with Python 3.12.14: **90 automated tests passed**. The 19 context-preparation tests cover exact serialized-character limits, required-content failure, complete-turn pins, unchanged instruction roles, model-visible omission counts, unsupported protocols, defensive copies, opt-in retrieval, and prepared tools bound to the same state revision.

The context-window example retains its explicitly pinned requirement and recorded current decision, leaves the full input unchanged, and blocks a tool after a later correction. `examples/evaluate_context.py` reports three synthetic retention fixtures against a simple recent-only baseline with the same 4,200-character ceiling. Extra Relay inputs (facts, pins, and an optional retrieval query) are disclosed. These checks show literal information availability and budget compliance; they do not measure model comprehension, reduced hallucination, task completion, or improvement over existing market products.

Version 0.2.0 library check on Windows with Python 3.12.14: **71 automated tests passed**, including 26 reusable middleware tests. The existing-project example passed without a web server or model. Wrappers were checked for default arguments, positional-only/keyword-only signatures, cloned values, captured revisions, stale-action rejection, persistence, and unsupported asynchronous functions.

The earlier v0.1 browser checks exercised stale-action blocking, recovery, corrected simulation, custom sessions/facts/policies, typed boolean updates, malformed JSON feedback, session persistence across reload, desktop layout, and a 390-pixel mobile viewport with no page-wide horizontal overflow. No browser console errors were observed in those checks. The deterministic CLI and lower-level harness example also passed.

The standard-library unittest suite exercises core state, policy enforcement, the harness adapter, and the local HTTP server. It includes tests for changed requirements, omitted facts, source mismatches, unsupported arguments/tools, stale and future revisions, cross-session context, rollback, callback failure, persistence, and concurrency.

The snapshot regression uses two SQLite connections: one commits a correction while a detail response is being assembled. It verifies that the response consistently represents one revision, then that the correction is visible to the next read.

The deterministic harness demonstration deliberately supplies an old proposal. That tests whether the guard enforces its contract. It is not a measurement of how often a real model forgets a correction, and its pass rate must not be advertised as a context-rot reduction score.

The original Ollama tool-proposal example remains optional. Its transport/proposal path is checked with mocked responses; the separate v0.4 context-selection evaluation above was actually run with an installed local model. Model installation is never required for importing the library or running the deterministic tests.

## Evaluation to run with a real workflow

Use one concrete workflow with independently defined expected outcomes. Keep model, prompt, tools, history, and generation settings fixed across runs. Include many non-conflicting cases as well as changes and omissions.

Compare:

1. Full available history with the existing provider/harness behaviour.
2. Your existing summary or memory solution.
3. The same baseline plus Relay's explicit task record and execution guard.

Include a customer changing an instruction, a scope changing from staging to production, a task cancelled while the model generates, an omitted prerequisite, stale evidence, and irrelevant state changes. Include untrusted text that tries to promote itself into a state or policy update; the trusted update path must reject that promotion.

Measure separately:

- Wrong actions proposed by the model and wrong actions actually executed.
- Correct actions incorrectly blocked or forced to refresh.
- Whether the agent reaches the correct outcome after receiving recovery context.
- Number of retries, input size, model latency, guard latency, and operator effort to maintain facts/policies.
- Failure handling after a real tool times out or returns an ambiguous result.

Publish the dataset, prompts, model/version, settings, hardware, sample count, failures, and raw results with any improvement claim. Count blocked-but-unfinished tasks as unfinished, not successful. Assess the extra invalidation caused by the conservative whole-session revision rule.

## Before organisational deployment

This version is meant for local experimentation and integration development. Team operation needs access control, retention/export/deletion design, domain-specific policy review, workload measurements, and recovery/idempotency tests for the actual tools. A distributed execution design should be evaluated before using long-running callbacks or multiple databases. These are future product requirements, not features claimed by v0.2.
