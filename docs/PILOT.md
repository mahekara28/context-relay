> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Prepare a real workflow comparison

No real project was supplied for this release. The library includes a paired
runner, a fixed-function practice example and an actual local model workflow on
synthetic data. These are preparation/evaluation tools, not a completed
organisational pilot.

## Current v0.14 completed-work evaluation

The new `examples/evaluate_completed_work.py` compares a recent-history baseline,
Relay action checks, and the same checks with at most one optional repair. The
two checked arms reuse one initial proposal in isolated execution states. A
repair sees only observed memory and rejection evidence. Missing or disputed
memory requests clarification without another model call or an invented user
answer. Completed work, wrong writes, false blocks, clarification and extra
inference calls are counted separately.

The baseline's fixed export schema has no structured clarification/no-action
option. The ambiguous case checks Relay's host-side routing, not a fair
comparison of model clarification ability.

The earlier six cases are explicitly development examples. Separately authored
whole-task cases are supplied through `--cases` after implementation freeze and
are reported separately. The [v0.14 protocol](WORKFLOW_EVALUATION.md#v014-frozen-comparison-protocol)
describes fixture boundaries, shared initial calls, input budgets and limitations.
No model call occurs by default:

```sh
python examples/evaluate_completed_work.py --model qwen2.5:3b --output evaluations/my-completed-work-dry.jsonl
```

Use `--run-local` only after reviewing the frozen cases/protocol. There are at
most three calls per case, including the optional repair. Repair has a larger
declared input cap and is not an equal-cost comparison to one-call execution.
This is still a synthetic task, not a customer deployment or organisational pilot.

## Archived v0.13 workflow

`examples/evaluate_agent_workflow.py` uses genuine LangChain/LangGraph and the
Ollama SDK with already installed local weights. Each graph reads a real
synthetic sales CSV and asks the model for a report plan and rows. The guarded
arm checks the proposal before writing; permitted writes produce CSV/JSON plus
a summary, which are independently graded. The host uses fixed file
paths and serialization; it never executes model-generated code.

The [v0.13 protocol](WORKFLOW_EVALUATION.md#archived-v013-three-arm-protocol) compares three arms with eighteen
planned calls and zero repairs. The two Relay arms use byte-identical prepared
prompts. Prevention, false rejection and completed work are separate outcomes.

The recorded v0.13 run (local-only reference; omitted from public distribution) completed
18 model calls. Every arm completed **2/6 tasks correctly**. The explicit gate
blocked three wrong writes, withheld no correct proposals, and allowed one
wrong write because the compound correction had left stale memory. Baseline
and memory-only arms each executed four wrong writes. There was no overall
task-completion gain. See the [results and limitations](WORKFLOW_EVALUATION.md#recorded-v013-result)
and artifact/request checks (local-only reference; omitted from public distribution).

## Archived v0.12 evaluation

The v0.12 run made twelve inference attempts across six declared cases, once per
arm: Relay automatic memory and an example contiguous recent-whole-turn selector.
Both use a 3,600-character serialized-message cap, the same model/prompt/schema,
temperature 0, seed 2718, `num_ctx=8192` and `num_predict=700`. Order alternates;
each arm/case has fresh state. Retrieval is disabled in the Relay arm. Expected
answers are for grading, not model input. There are no implicit model retries.

| Observed result | Recent whole turns | Relay memory |
| --- | ---: | ---: |
| Correct complete tasks | 2/6 | 2/6 |
| Requested export format | 2/6 | 3/6 |
| Correct rows and arithmetic | 6/6 | 6/6 |
| Language label plus script check | 3/6 | 3/6 |
| Reported total input tokens | 3,612 | 4,284 |

All twelve workflows wrote files, but only four filesets met every declared
criterion. The other eight remain unsuccessful tasks. The language check uses
the label and presence/absence of Devanagari characters; it is not a linguistic
quality or translation assessment. There is **no overall task-completion gain**
in this small run. Higher input-token use in the Relay arm is reported, not hidden.
An unexplained roughly eight-hour elapsed-time anomaly appears in both host and
Ollama timing metadata for one request; latency comparison is unusable.

The baseline is explicitly an example policy, not an organisation's existing
setup, another memory product or provider-native compaction. This one synthetic
workflow/model/seed cannot establish real-world reliability, cost savings or
maintenance effort. Raw prompts, outputs, artifacts and failures are in the
local journal (local-only reference; omitted from public distribution).

The temporary working directories were removed after evaluation. For inspection,
the release includes 24 reconstructed report/summary files (local-only reference; omitted from public distribution),
each verified against its original recorded SHA256. The journal's text reader
normalized Windows newlines; restoring CRLF where needed reproduces the original
bytes. These are labeled reconstructions from retained evidence, not another run.

A retrospective diagnostic (local-only reference; omitted from public distribution)
found correct recorded preferences in 5/6 Relay cases. In three of those cases,
the model still chose wrong preference labels. Compound wording was not
recognized and left stale CSV/English preferences instead of JSON/Hindi. This
separates failures to capture a correction from failures to follow available
memory. It used the existing journal and made no new inference calls.

The v0.12 inference run used the preceding script version, whose
reconstructed source (local-only reference; omitted from public distribution) and
amendment provenance (local-only reference; omitted from public distribution) are
retained. That historical source was reconstructed from the known post-launch
tracing edit, not hashed at startup. No v0.12 model calls were repeated after
that example-only change. Its twelve-call procedure is archived and is not the
current example's execution plan.

## Reproduce the archived v0.13 example

Run with an installed local model and a new path:

```sh
python examples/evaluate_agent_workflow.py --model qwen2.5:3b --output evaluations/my-local-workflow.jsonl
```

Use `--dry-run` to inspect eighteen prepared prompts without inference. The
script never installs SDKs or downloads models, and explicitly disables remote
LangSmith tracing. It saves an exact script snapshot and script/core/fixture
hashes at startup. Actual generated files persist beside the journal. See the
[full protocol and limitations](WORKFLOW_EVALUATION.md) before interpreting results.

## Use the paired runner with your actual baseline

Start with one existing text workflow whose expected outcomes can be reviewed.
Record consented, appropriately redacted transcripts containing both ordinary
requests and corrections. Define expected answers or independent grading criteria
before running. Keep failures and ambiguous cases in the dataset.

```python
from context_relay import RelayConfig, run_pilot

summary = run_pilot(
    cases,  # [{"id": "case-1", "messages": original_history, "expected": expected_text}]
    model_call=your_existing_text_model_call,
    baseline_prepare=your_existing_context_builder,
    baseline_description="Describe the actual current summary/memory setup",
    model_description="Record the exact model and generation settings",
    model_options=your_shared_model_options,
    dataset_kind="real_workflow",
    report_path="pilot-001.jsonl",
    config=RelayConfig(max_chars=16000),
    repeats=2,
    max_calls=40,
)
```

The model callback accepts `messages=...` plus your shared options and returns
complete text. Decode your provider's response in that callback. The baseline
callback receives `(messages, max_chars=...)` and returns the messages your
current system would send under that limit. Use an adapter if your existing
interface differs. Both callbacks must be synchronous; set provider timeouts in
your application. Only plain-text histories are supported by this runner.

Both modes use the same model function/options and character cap. The existing
baseline stays under your control; Relay does not replace it with a weak canned
comparator. An oversized request is a visible failure before inference. These
are character counts, not tokenizer measurements; matching characters alone does
not establish equal model-token use. Capture provider token usage separately if
that is an evaluation criterion.

The runner alternates mode order by case/repeat and uses fresh Relay memory per
case. It processes the original history once; it does not simulate a live user's
future reactions or intervening tool actions. Expected answers are supplied only
to scoring, never to the provider or preprocessing callbacks. Default scoring is
exact text with outer whitespace removed. For multiple requirements, supply a
scorer returning a nonempty dictionary of named booleans:

```python
def grade(response_text, expected_text):
    return {"correct_output": response_text.strip() == expected_text.strip()}
```

All criteria must pass for completion. A failure, blocked call, exception, invalid
response or oversized answer remains incomplete. No model retries run implicitly.
The preflight call cap limits the planned paired runs. The host callback must
also control any retries inside its SDK.

## Evidence and manual effort

A new JSONL file records the manifest, every started/result pair and a summary.
Each record is flushed so an interrupted run retains partial evidence. Existing
reports are never overwritten or silently resumed. Dataset labels and model
descriptions are host declarations; the runner cannot verify their authenticity.

The default records hashes, sizes, timings, criteria and error types. It does not
record raw prompts, labels, responses or exception messages. `include_content=True`
explicitly enables bounded raw local evidence, including unsuccessful answers.
Review access and privacy before enabling it for real transcripts. Hashes are not
anonymization guarantees for predictable input, and opaque case IDs should contain
no private content.
Caller-supplied descriptions, configuration names and custom criterion names
also remain report metadata; do not put private information in those labels.

`manual_interventions` and `maintenance_seconds` remain null until reviewers
measure them. Do not interpret them as zero. For each mode/case, a human should
record the number of corrections/confirmations, minutes spent maintaining memory,
whether any required instruction was missed and whether the task actually ended
correctly. Include setup effort and false alarms. Compare the same rubric in both
modes; report sample size and unsuccessful tasks alongside any improvement.

The runner measures callback time and serialized request size; it does not infer
token savings, cost savings, production latency or societal impact from them.

Run `python examples/pilot_workflow.py --output evaluations/my-practice.jsonl` for
an offline plumbing check. Its fixed function always answers JSON, so the CSV
case intentionally fails in both modes. It proves that the runner preserves a
failure, not that Relay improves a model.
