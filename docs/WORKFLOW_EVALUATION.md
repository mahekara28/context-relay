> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Completed-work evaluation

The v0.14 example, `examples/evaluate_completed_work.py`, measures whether an
optional repair helps an agent finish the task after a rejected proposal. It is
a synthetic local export task. No organisational project has been supplied.
The earlier v0.13 procedure and results remain below, and its executable script,
source snapshots, journals and generated files remain unchanged.

## v0.14 frozen comparison protocol

| Arm | Initial model input | Execution |
|---|---|---|
| `recent_whole_turns` | System prefix plus a contiguous suffix of complete turns | Schema validation, then fixed export function |
| `relay_action_checks` | Relay-selected context with observed memory | Schema validation and explicit format/language checks |
| `relay_action_checks_repair` | **The same initial proposal** as the checked arm | Same checks; at most one optional model repair before execution |

One baseline call and one Relay call are made per case. The Relay proposal is
reused in the two checked arms, rather than asking the model independently and
confusing different initial answers with the effect of repair. Before inference,
the evaluator prepares one SQLite state, closes it, and makes two independent
backups. It verifies identical prepared messages, memory and captured revisions
after reopening them. A write in one branch cannot advance the other branch's
revision. Baseline/Relay initial call order alternates by case.

The host explicitly binds `format` to `export_format` and `language` to
`response_language`. It does not inject grading answers into memory. Parsed raw
proposal values are preserved. Known case/outer-whitespace variants, such as
` json ` and `JSON`, use the library's finite enum normalization. Actual executor
arguments are recorded. The baseline serializer accepts the same variants so
casing is not counted as a spurious baseline failure. There is no fuzzy matching
or automatic interpretation of arbitrary unknown values.

The checked arms use `prepared.execute_with_recovery`. The repair callback is
enabled only in the third arm. It sees the rejected arguments, structured
rejection issues and the current observed memory with provenance. It receives
neither expected labels nor an independently supplied "correct answer". It may
return replacement arguments for the same fixed `write_export` tool. The library
checks the replacement again before invoking the executor. There is no second
repair, implicit SDK retry, or retry after a tool exception.

Missing, disputed or unresolved memory requests clarification instead of invoking
the repair model. The evaluator records that request but does not invent a user
answer. Such a task remains incomplete. A repair exception, malformed output,
budget failure or rejected replacement also remains incomplete. Clear but wrong
memory can still permit an incorrect action or steer a repair toward the wrong
answer.

The initial input cap is 3,600 serialized-message characters. The repair cap is
8,000 characters and is separately reported. Both exclude the common output
schema/options. Repair uses the same model and generation settings, but adds
an extra inference request and potentially more input. Any improvement would
describe the combined checks-and-repair system; it would not show that memory
alone improved the model at equal cost. Physical calls are counted once, while
arm summaries explicitly attribute the shared initial call to each comparison.
The maximum is two initial calls plus one repair per case.

The six earlier cases are available as a **development** dataset. They cover
buried preferences, corrections, tentative alternatives, quotations and compound
wording. They are known examples, not held-out evidence. A separate externally
authored fixture may be supplied only after the implementation and evaluation
mechanics have been frozen. The fixture's `fresh_holdout` label is an author
declaration; the runner cannot itself certify independence. Development and
fresh results must be reported separately. `--case-id` records an explicitly
selected subset before inference; it must not be used to hide failed cases
after a run.

Prepare all six development cases without contacting a model:

```sh
python examples/evaluate_completed_work.py --model qwen2.5:3b --output evaluations/my-completed-work-dry.jsonl
```

Inference requires the explicit flag and a fresh output path:

```sh
python examples/evaluate_completed_work.py --run-local --model qwen2.5:3b --output evaluations/my-completed-work-local.jsonl --max-calls 18
```

Only already installed local GGUF weights are accepted. Nothing is downloaded,
and no hosted provider is called. Optional `--case-id buried_preference` may be
repeated to choose a predeclared subset. `--cases path/to/cases.json` loads a
separate fixture with this shape:

```json
{
  "dataset": "fresh_holdout",
  "cases": [{
    "id": "opaque-case-id",
    "history": [{"role": "user", "content": "The original conversation goes here."}],
    "csv": "item,units,unit_price\nExample,2,3\n",
    "expected": {"format": "JSON", "language": "English"}
  }]
}
```

This schema illustration is not a held-out case. A missing system prefix gets
the fixed sales-report instruction. A source CSV may vary between cases and
contains `item,units,unit_price` columns. The model proposes every row and its
`total=units*unit_price`; the host uses fixed file paths and serialization.
It never executes generated code.

For a case with a genuinely unresolved user preference, use
`"expected": {"clarification_required": true}` instead of format/language labels.
This does not invent a target value. The grader counts a correct clarification
separately from a completed task, and any exported output in that case is wrong.
Expected labels remain excluded from runtime inputs and repair callbacks.

The baseline's fixed export-argument schema has no structured clarification or
no-action response. Relay can instead stop through its host-side uncertainty
check. The ambiguous case therefore validates that routing and counts its
outcome; it is not a fair comparison of the models' ability to ask clarifying
questions. Do not use the clarification count to claim model superiority.

The journal records startup script/helper/core/fixture hashes, exact source and
fixture snapshots, prepared contexts, every actual request and raw response,
initial/repaired proposals, checks, actual executor arguments, and persisted
file contents/hashes. Output paths and snapshots cannot be overwritten. Helper
snapshots matter because this example reuses the v0.13 task/serialization/grading
helpers. Local temporary databases are removed after each case; model requests,
memory snapshots and source-linked check evidence remain in the journal.

Post-execution grading reads the actual output files and reports:

- Complete correct tasks, and incorrect writes that still executed.
- Wrong proposals blocked, and correct proposals falsely blocked.
- Correct output withheld at the end, clarification requests, and errors.
- Correct clarification for ambiguous cases, and unnecessary clarification on
  cases with declared clear targets. A correct clarification is not task completion.
- Initial calls, repair callbacks, actual extra model calls and observed tokens.

Executor starts and successful returns are counted separately. A tool failure
after a partial file write retains that file's contents/hash, the execution
receipt and the repair count when applicable. It is an incomplete write and
an error, never a successful task or a claim that no side effect happened.
Invalid UTF-8 fragments are retained losslessly as base64 with a decode-error
flag; inaccessible files record a read error instead of hiding the tool failure.

A repaired task may both block an initial wrong proposal and complete correctly.
Two rejected wrong proposals count as two blocked proposals, not two tasks.
A blocked correct proposal followed by a successful repair still counts as a
false block. Row arithmetic and the summary's writing script are checked only
by the grader; the gate enforces the two declared preferences. The language
check is not a linguistic quality assessment. Shared local caches and a small
single-seed dataset do not establish production latency, cost savings, or
reliability. Hosted checks are outside this evaluation.

No v0.14 model result is claimed here before the frozen local run is completed.

## Archived v0.13 three-arm protocol

This is a synthetic export workflow, not an organisational pilot. It evaluates
three distinct things: selected recent context, automatic memory, and explicit
enforcement of memory values at a file-writing boundary.

The six histories, source CSV, expected outcomes and generation settings are
fixed before inference. They retain the six v0.12 scenarios: buried preferences,
corrections toward and away from defaults, tentative alternatives, quoted
instructions and the previously missed compound correction. Historical v0.12
journals, source reconstruction and artifacts remain unchanged.

| Arm | Context sent to the model | File-writing boundary |
|---|---|---|
| `recent_whole_turns` | System prefix and a contiguous suffix of complete turns | Validated schema, direct write |
| `relay_memory` | Relay's frozen selected context and observed user memory | Validated schema, direct write |
| `relay_action_gate` | Byte-identical prompt/options to the memory arm, separate model call | Same schema validation, then `prepared.wrap` |

The developer explicitly registers `format -> export_format` and
`language -> response_language` memory bindings. The gate consults the current
recorded AutomaticMemory values and source references. It never reads expected
grading labels or invents missing user constraints. Missing, disputed or
mismatching memory can block an action. This is an explicit host decision to
enforce these two preferences, not blanket authorisation for arbitrary tools.

One Relay context is prepared per case before either Relay model call. Both
calls consume its frozen messages directly. A successful gated execution can
advance the state revision; the remaining ungated call still uses the original
messages and never silently refreshes. Three-arm order rotates across cases.
Each case/arm has its own output directory. Remote LangSmith tracing is disabled
for this local example. The reusable framework adapter leaves host tracing
configuration under the host's control.

There are 18 planned initial calls, one per case/arm, with zero repairs or
inference retries. Character cap, model, temperature, seed, output schema and
generation limits are shared. The cap is compact JSON of actual sent role/content
messages; the common output schema and options are excluded. It is not a token
measurement or proof of equal provider processing cost.
All arms share one local model process and its prompt cache. In particular,
repeating the identical Relay prompt can reuse cached work. Rotating order
does not eliminate this effect; wall times and provider-reported token counts
are observations, not an independent latency comparison or evidence of a
speed improvement from the gate.

Grading happens only after an attempt has finished. A blocked proposal never
creates a report or summary. The journal separates:

- Completed correct tasks from workflows that wrote an incorrect file.
- Wrong actions prevented by the gate from correct outputs that it withheld.
- Incorrect actions that still executed, including mistakes in rows or language.
- Initial model calls, extra calls, action attempts, executions and rejections.

Prevention is not completion: a blocked task stays incomplete. The gate checks
declared format and language against recorded memory; it does not validate row
math or whether the summary is actually in the declared language. The independent
grader rereads generated files, checks row arithmetic, file format, and the
language label plus writing script. That script check is not a translation-quality
assessment. A wrong memory entry can still permit a wrong action or block a
correct one, which is why both outcomes remain visible.

Prepare all inputs without contacting Ollama:

```sh
python examples/evaluate_agent_workflow.py --dry-run --model qwen2.5:3b --output evaluations/my-three-arm-dry-run.jsonl
```

After reviewing the frozen protocol and inputs, run with an already installed
local model and a fresh output path:

```sh
python examples/evaluate_agent_workflow.py --model qwen2.5:3b --output evaluations/my-three-arm-run.jsonl
```

The script verifies local GGUF model metadata and never downloads weights.
Optional `--artifacts` chooses a new output directory; otherwise files persist
beside the journal. A byte-exact script snapshot is saved at startup, with source
and fixture SHA256 values in the manifest. Every request, attempt and grading
record is appended and flushed. Existing reports, source snapshots and artifact
directories are never overwritten. Model errors remain visible and are not
retried. One synthetic dataset and small local model cannot establish real-world
reliability, organisational adoption, cost savings or superiority to other
memory products or provider-native compaction.

## Recorded v0.13 result

The frozen run used the already installed `qwen2.5:3b` model and completed all
18 initial inference attempts with no timeout, inference error, repair or retry.
The raw journal (local-only reference; omitted from public distribution),
exact executed script (local-only reference; omitted from public distribution),
frozen dry run (local-only reference; omitted from public distribution) and
post-run checks (local-only reference; omitted from public distribution)
are retained.

| Observed outcome | Recent whole turns | Relay memory | Relay action gate |
|---|---:|---:|---:|
| Correct complete tasks | 2/6 | 2/6 | 2/6 |
| Incorrect writes executed | 4 | 4 | 1 |
| Wrong proposals blocked | 0 | 0 | 3 |
| Correct proposals withheld | 0 | 0 | 0 |
| Report/summary sets written | 6 | 6 | 3 |
| Initial model calls | 6 | 6 | 6 |
| Extra model calls | 0 | 0 | 0 |

There was no task-completion improvement. The gate prevented three incorrect
writes and permitted one incorrect write. In the compound-correction case,
the default recognizer retained earlier CSV/English preferences even though
the user had subsequently requested JSON/Hindi. The gate enforced that stale
recorded state. Memory matched the expected latest preferences in five of the
six cases; a gate cannot recover a correction the memory layer missed.

The check verified unchanged script/core hashes across the run, identical
recorded memory/gate request bytes in every case, and all 30 actual generated
output files against their recorded hashes. Blocked directories contained only
the input CSV. Files are actual retained outputs in
`evaluations/agent-workflow-v0.13-local-artifacts`, not reconstructed examples.
Zero correct proposals were withheld in this small set; that is not a general
false-rejection-rate estimate. The optional LLM memory interpreter was not used
in this workflow, and hosted-provider checks were not run.
