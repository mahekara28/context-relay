# Measure model behaviour locally

`examples/evaluate_local_model.py` asks an installed local model to answer five
small tasks using three context selection modes. Unlike the deterministic
library tests, the answers come from an actual model. The script records wrong
answers as wrong; it does not substitute a fixed answer or count a blocked tool
call as successful task completion.

This is a **synthetic smoke evaluation**, not evidence of general effectiveness
against context rot. It has five hand-authored fixtures, no production traffic,
and no claim of statistical significance.

The first actual run and all failures are bundled in [the results folder](../evaluations/README.md).

## Run it

Requirements: Python 3.10+, a running local Ollama instance, and an already
downloaded GGUF model that supports JSON-schema output. There are no extra Python
dependencies. This script never downloads a model, uses a paid API, or executes
tools proposed by the model. Local inference uses your hardware and electricity.

From the project directory, inspect the inputs first:

```console
python examples/evaluate_local_model.py --model YOUR_INSTALLED_MODEL --output eval-dry-run.json --dry-run
```

Then run inference using the exact installed model name:

```console
python examples/evaluate_local_model.py --model YOUR_INSTALLED_MODEL --output eval-results.json
```

The default is 15 sequential calls: five tasks times three modes, one repeat.
The timeout defaults to 60 seconds **per HTTP request**, not for the entire run.
Choose `--timeout 120` for slower hardware. `--repeats 3` repeats the complete
comparison. `--fixtures buried_record environment_scope` runs a smaller subset;
`--fixtures all` is the default.

The client contacts only `127.0.0.1` (Ollama port 11434 by default), disables HTTP
proxies and redirects, verifies the selected name is installed, rejects cloud
model metadata, and requires local GGUF metadata before requesting inference.
The dry run does not contact Ollama at all. The script does not install Ollama or
change its settings.

## What is compared

| Mode | Inputs and selection |
| --- | --- |
| `full_plus_state` | All original history plus the application's explicit current state. It is a deliberately over-budget reference, not a matched-cost competitor. |
| `recent_plus_state` | Initial instructions, pending request, explicit host pins, and newest whole turns that fit 3,200 serialized JSON characters, including state and coverage metadata. |
| `relay` | The same history, state, pins, and 3,200-character budget, using `ContextGuard.prepare()` with two lexical retrieval candidates before newest-turn filling. |

The recent control uses Relay's public context compiler with retrieval disabled.
This isolates the choice of which history to retain instead of confounding it
with a second serializer or inconsistent budget accounting. Characters are not
tokens; actual prompt token counts are recorded when Ollama reports them.

**Every mode receives the same extra information from the host.** The host
explicitly records the latest correction in the correction fixture. It pins the
early constraint in the constraint fixture for both budgeted selectors. These
two fixtures are controls: successful answers do not show that Relay inferred
the correction or discovered the important constraint itself. In the buried
record and scope fixtures, the requested value appears only in the original
conversation. The **host also supplies the retrieval query**; Relay does not
generate it automatically. The buried-record and scope queries use target words
from the final task and contain neither requested answer. The correction query
additionally includes `replacement correction`, using the correction already
supplied in the shared application state. These retrieval settings are disclosed
in each prepared input; they are not evidence of automatic correction detection.

The tasks cover:

- A refund request changed to replacement, with explicit current state supplied.
- A loading dock recorded before many unrelated turns.
- Different approved regions for staging and production.
- An early format constraint deliberately pinned by the application.
- A delivery estimate that does not establish an actual arrival date; the
  correct answer is `unknown`.

These fixtures intentionally make relevant-record retention observable. They
are not a held-out, representative distribution of customer conversations.

## Read the results

Each model must return `{"answer": "...", "evidence": ["record-label"]}`.
The scorer checks both the exact requested value and the exact evidence-label
set. Unknown answers require an empty evidence list. Extra fields, malformed
JSON, duplicate evidence labels, wrong values, or wrong evidence fail task
completion. Answers and evidence are scored separately as well.

The JSON report includes every exact prepared request, expected answer, context
selection report, prompt hash, raw model response, parsed proposal, individual
scores, actual call order, and per-call errors. Expected answers are stored for
the reader/scorer but are **not sent in the model request**. Host-recorded state
and pins are disclosed separately from the original history.

It also records Ollama's version and model metadata, Python/platform details,
request options, wall-clock latency, and provider-reported prompt/output token
counts and durations. Ollama durations are in nanoseconds; `wall_seconds` is in
seconds. Missing provider metrics remain null, not estimated values.

Generation uses temperature 0, a fixed seed within each repeat, an 8,192-token
requested context window, and a 256-token output cap. Mode order rotates by task
and repeat. There is **no warm-up call**: the first call can include model loading,
and later calls can benefit from caching. The report preserves load/cache metrics
when available. Timing differences from this small run do not establish a speed
advantage. Model templates, hardware, and provider behaviour can vary even with
the same seed.

Reports are written before and after every call. A timeout or malformed provider
envelope is recorded and subsequent cases are attempted; a model's valid but
incorrect answer is a completed response with a failed score. Exit code 0 means
the run completed without infrastructure errors, **not that every answer was
correct**. Errors return 1 and interruption returns 130; partial reports remain
available. A timeout can leave the local server finishing its current inference.

Use the per-task raw records to understand failure modes before quoting an
aggregate. A useful next evaluation would use consenting real workflow traces,
more task types and models, independently reviewed expected answers, and
repeated runs. Those results are not implied by this example.

The request and metrics follow the official [Ollama chat API](https://docs.ollama.com/api/chat).
