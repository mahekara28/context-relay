# Try Relay in one real workflow

This kit gives another developer a runnable starting point and a way to record
whether Relay helps. It runs locally for free. The bundled cases are synthetic;
**no outside developer or organisation has completed this pilot yet**.

## Start with the offline example

From the repository, with Python 3.10 or newer:

```sh
python examples/instruction_pilot.py --report results/my-first-pilot.jsonl
```

No API key, model download or third-party package is needed. The example writes
exports into fresh temporary directories, reads them back, then removes those
directories. The main journal retains hashes, scores and metered call counts.
A companion `my-first-pilot.observations.jsonl` retains clarification requests,
scripted review decisions and repairs after each run. Failed runs have unknown
review counts, never assumed zeros. Start records without finish records show
an interrupted run. Keep both journals; existing reports are never overwritten.

Each example runs twice:

- **Baseline:** application memory set by a scripted reviewer, an ordinary
  validator, and repair from known source values.
- **Relay:** the same source rows and model proposals, with instruction memory,
  registered tool checks, and known-value repair.

The baseline already protects the action. Both variants must preserve row IDs,
names and quantities. A wrong file is a wrong action even if the tool reported
success. Blocking a task does not count as completing it. A clarification can
lead to a separately supplied user reply and then a completed export. Unknown
row identities remain unresolved rather than being guessed.

The proposals and reviewer replies are fixed test data. No model accuracy,
human time saving or advantage over a framework can be inferred from this run.
The baseline's reviewed values are explicit inputs; they are not inferred by
another language parser. The temporary export exception is limited to one user
turn. An arbitrary report or project lifetime requires a clarification; Relay
does not infer when that work ends.

## Connect a participant's workflow

Copy [the example](../examples/instruction_pilot.py) and change one workflow at
a time. An export, ticket update or document-generation workflow is enough.

1. Replace `pilot_cases()` with 10–20 small, redacted cases from the workflow.
   Include corrections, unchanged requests, ambiguity and previous failures.
   Write expected outcomes before running either variant. Keep some unfamiliar
   cases aside until the implementation is fixed.
2. Replace the fixed proposal in `provider()` with the project's existing model
   call and decoder. It must return `{"tool": "save_export", "arguments": ...}`
   for this example. Supply the same authoritative source data to both variants.
   Model calls must go through `context.model_call(...)` to be counted.
3. Replace `_ordinary_validator()` and the baseline memory with the project's
   actual current setup. Keep its existing framework memory, retries and
   validation; record those settings. Do not weaken it to make Relay win.
4. Replace the export tool with an isolated test destination. Meter effects with
   `context.tool_call(...)`. Replace file readback with a check of the actual
   destination. A replay can repeat side effects: use disposable test data and
   the destination's own idempotency controls.
5. Replace scripted reviewer choices with choices from the user. Count every
   question and record actual review time. Never confirm an LLM's suggestion
   automatically or copy grading answers into the model's input.

`evaluate_harness()` already alternates variant order, uses separate temporary
working directories and enforces shared metered call ceilings. Expected labels
go to the judge only. Calls hidden inside an SDK, SDK retries, callback timeouts
and external state remain the integration's responsibility. For a text-only
workflow, the existing [paired pilot runner](PILOT.md#use-the-paired-runner-with-your-actual-baseline)
may be a smaller integration.

## Optional local instruction interpretation

Use an installed local Ollama model for suggestions the finite rules cannot
parse. It makes an additional local generation call for eligible unmatched
messages. The normal path remains free of model calls.

```python
from context_relay import MemoryInterpreter, auto_connect

interpreter = MemoryInterpreter.from_ollama(model="qwen2.5:3b", timeout=120)
with auto_connect(existing_client, memory_interpreter=interpreter) as agent:
    result = agent(messages=original_history, model=your_existing_model)
```

The named model must already be installed. Relay does not pull a model or use
paid APIs for interpretation. Local hardware can still be slow. Suggestions
remain pending and may require your UI to handle `MemoryClarificationRequired`;
this snippet is only the connection. See [local interpretation](LOCAL_INTERPRETATION.md)
for review, limits and failure handling. A local model can return no suggestion
or a wrong suggestion; structural validation does not prove understanding.

## Record the outcome

Use the [participant record](../evaluations/pilot-participant-template.json).
Record the project and baseline, Relay/Python/framework/model versions, dataset
origin, integration time, review time, completed tasks, wrong actions, unresolved
tasks, model/tool calls and latency. Include errors and timeouts in the totals.
The template is a record to fill in, not an uploaded telemetry form. Nothing is
automatically sent to Relay's author.

Compare per-case outcomes before totals. Investigate every new wrong action or
previously completed task that regresses. A tie is a valid result. Prefer the
existing setup if Relay adds overhead without a demonstrated benefit. Keep the
case set and baseline fixed for the comparison; collect another untouched set
before claiming improvement after tuning.

Share a minimal reproduction with synthetic data for any failure. Exclude API
keys, private transcripts and personal data. Publish the measured results and
their limits, including an unsuccessful pilot, rather than a general reliability
claim based only on passing tests.
