# Instruction understanding: 0.23 evaluation protocol

The 60-case [corpus](instructions-v023.json) was frozen on 2026-10-24 before
running the updated parser against it. SHA-256:
`95eb86212cf6dc30704ccd8623a40efd8f24c85ac198f976a5c473a0883325f0`.
There are 30 development and 30 holdout examples in English, Hindi and Hinglish.
The author knew Relay's public behavior and earlier limitations. These are
agent-authored synthetic cases, **not an independent human benchmark**.

The parser implementer did not receive this corpus. Neither expected values nor
case labels are passed to memory or the optional interpreter. Cases include
durable changes, ambiguity, explicit one-turn exceptions, unspecified task
lifetimes, reported instructions, negation and harmless mentions. No case may
be deleted or relabeled to improve the score. After inspection, a holdout case
becomes exposed regression evidence; another untouched set is needed for a
new generalization claim.

## Decisions and suggestions

The same runner imports an explicitly selected source checkout. The before
baseline is the immutable `139d3c44df7882750db59c1ed87a6a45da7bfb53` source.
Each case starts with CSV exports, English replies and brief responses. A
recognized change must produce the expected effective values without unrelated
mutations. A one-turn exception must preserve standing values and expire on an
independent request. An unknown task lifetime must request clarification: this
does not count as understanding or completing that task.

The runner reports missed changes, missed clarification, unnecessary review,
wrong values, errors and incorrect standing-memory mutations separately.
Optional model suggestions are scored separately and **never auto-approved**.
An exact quote establishes provenance; it does not establish that an inferred
meaning is correct. A schema-valid but wrong suggestion remains a failure.

```sh
python examples/evaluate_instructions_v023.py --output results/rules.json
python examples/evaluate_instructions_v023.py --source /path/to/baseline/src --output results/before.json
# Requires an already installed local model; makes additional local model calls.
python examples/evaluate_instructions_v023.py --model qwen2.5:3b --output results/local-model.json
```

Reports include hashes of the corpus, runner and imported source. Existing
reports are not overwritten. Schema 2 retains every interpreted proposal as a
list: contradictory or additional suggestions cannot collapse into a correct
last value. Missing state is recorded as unknown, not as zero mutations.
A companion `rules.json.jsonl` journal is flushed after each completed case;
an interrupted run retains earlier results. The final JSON is written only
after all selected cases finish. Check its completion and unchanged-source flags.
Report errors and timeouts alongside successes. Model timing depends on the
host; a small local run does not establish production throughput.

## Completed work and an outside pilot

Instruction decisions are only one layer. The companion pilot kit runs paired
workflows with real temporary export files, preserved source rows, an ordinary
validator in the baseline, and independent artifact readback. It counts
completed work, wrong actions, clarification and metered calls separately.
Fixed model proposals and scripted reviewer decisions are labeled simulations.

An outside participant must replace the example workflow and baseline with
their real setup, record model/runtime versions, collect unfamiliar cases before
tuning, and include human review time. No outside participant has run this kit
yet. See the [pilot guide](../docs/PILOT_KIT.md) for the runnable workflow and
reporting steps.
