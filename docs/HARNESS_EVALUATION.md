# Test an improvement before using it

Relay can compare complete workflow results, suggest one configuration change,
and keep the existing configuration when that change fails its checks. It uses
your runner and judge; it does not train a model or rewrite a live agent.

## Try the offline example

From the source checkout, with Python 3.10 or later:

```sh
python examples/harness_improvement.py
python examples/harness_improvement.py --report .relay/export-trial-001.jsonl
```

The [example](../examples/harness_improvement.py) writes actual CSV and JSON files
to temporary directories. A fixed response proposes an old format or changes an
item's name and quantity. Both variants use `auto_connect()` and `protect_tools()`
with the same memory and content checks. The candidate enables `repair="known"`.

The baseline completes **2 of 4** cases; the candidate completes **4 of 4** with
the same four model-callback calls and no wrong writes. Repairs copy known values
and recheck them before writing. The judge reads the files and checks their
format, names, identities and numbers, including the distinction between JSON
`true` and `1`; a successful tool receipt alone does not establish completion.

These are **four synthetic fixtures with predetermined responses**, not a model
quality benchmark, an independent empirical holdout, or an organisational pilot.

## Connect your workflow

With Relay installed, the same example can be run from the checkout in Python:

```python
from context_relay import HarnessBudget, improve_harness
from examples.harness_improvement import (
    export_cases, judge_export, propose_repair, run_export,
)

choice = improve_harness(
    cases=export_cases(),
    baseline={"repair": None},
    propose=propose_repair,
    run=run_export,
    judge=judge_export,
    budget=HarnessBudget(max_runs=8, max_model_calls=8, max_tool_calls=8),
)
print(choice["selected"], choice["config"])
print(choice["decision"])       # accepted, reasons, completion_gain, by_split
print(choice["report"]["usage"])  # runs, model_calls, tool_calls, budget_exhausted
```

All callbacks are synchronous. Their contracts are:

| Callback | Receives | Returns |
| --- | --- | --- |
| `run(input, config, context)` | Copied case input and configuration; fresh working directory and call meters | A bounded JSON result describing observed work |
| `judge(expected, result, trace)` | The private expected result, observed result, and call trace | Exactly `completed`, `wrong_action`, `clarification`, each a boolean |
| `propose(failures, context)` | Failed development records only; a separate working directory and model-call meter | One bounded JSON configuration object |

Each development failure contains `id`, `input`, `expected`, `result`, `score`,
`status` and `trace`. The runner never receives `expected`. The proposer does not
receive holdout records. Its callback runs at most once; if there are no
development failures, Relay keeps the baseline without calling it.

Create cases with the public `HarnessCase(id, input, expected, split=...)` API.
Use `split="development"` for examples available to the proposer and
`split="holdout"` for separate checks. Expected results should come from an
independent specification or trusted source, not the candidate's own answer.
Create a **fresh Relay connection/session inside every runner invocation**.
The evaluator copies JSON and supplies a fresh directory, but cannot reset an
external database, a shared client, or state captured by your callback.

The runner must validate an explicit configuration allowlist before any calls.
The example permits only `{"repair": None}` or `{"repair": "known"}`. A proposed
`disable_checks` setting fails setup; the proposer cannot replace the contracts
through this configuration. Keep those checks application-owned.

## Meter calls and inspect complete results

Wrap provider calls with `context.model_call(function, *args, **kwargs)` and test
tool calls with `context.tool_call(function, *args, **kwargs)`. `context.workdir`
is a fresh `Path`, removed after that run. A call consumes its budget **before**
dispatch, including a call that fails. A metered failure stops further metered
calls in that context, even if your callback catches the exception.

The proposer uses the same shared model-call budget. If it calls a model, use
`context.model_call()` there too and leave room in `max_model_calls`. Its
`context.tool_call()` is forbidden. `max_runs` must fit both workflow variants;
the separate proposal callback does not count as a workflow run.
Set `max_model_calls=0` or `max_tool_calls=0` to forbid that kind of metered call.

`trace` contains `model_calls`, `tool_calls` and `events`. Each event names its
kind and status, with an exception type for a failed call. Check
`choice["report"]["results"]` for each case's score, status and trace, and
`choice["report"]["proposal_trace"]` for proposal calls. Failed or ungraded runs
cannot be accepted. A write followed by an error is not repeated by the harness.

## How selection works

`improve_harness()` runs the baseline once on every case, proposes one change
from its development failures, then tests that candidate on the same cases.
For two configurations you already have, use public `evaluate_harness()` with
`cases, baseline, candidate, run, judge`; it alternates which variant goes first
for each case. Pass its report to public `assess_improvement()`.

Identical inputs cannot appear in both development and holdout, even under
different case IDs. The gate requires at least one completed candidate holdout
task, complete error-free paired scores,
consistent configuration identities and accounting, a positive completion gain,
**no candidate wrong action**, and **no individual completion regression**.
One extra success cannot offset breaking a task the baseline completed.
`decision["by_split"]` shows completion totals and gains separately: an accepted
change can improve development tasks while only maintaining holdout performance.
Clarification and call counts remain visible, but the gate does not require
fewer calls or fewer clarifications; apply additional product limits if needed.

On rejection, `choice["config"]` is the unchanged baseline and `decision.reasons`
explains why. On acceptance, the candidate is returned as data for review.
Neither outcome installs code, changes a live configuration, or proves future
reliability. Keep a real holdout out of the tuner's reach and refresh it over
time; repeatedly tuning against the same checks stops being an independent test.

## Reports and boundaries

`report_path` creates a **new** JSONL file exclusively and flushes progress as it
runs. An existing file is never overwritten. Reports contain hashes, case IDs,
outcomes and call metadata, not raw case inputs, expected values or results.
Use non-sensitive IDs. Hashes are not encryption or an authorization mechanism;
they help detect accidental mismatches. An interrupted journal is incomplete,
not an approved result.

Use explicit test tools and isolated destinations. Do not replay real tool calls
from saved conversations. These are trusted Python callbacks, **not a sandbox**:
direct calls, unwrapped I/O, SDK retries, deadlines and external state remain
application-owned. The runner cannot prevent a callback from accessing other
files or performing live actions. The judge's accuracy and the relevance of
your cases determine what the comparison can establish.
