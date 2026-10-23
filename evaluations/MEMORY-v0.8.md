> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Memory and connection checks, v0.8

The finished v0.8.0 library passed 548 automated tests on Windows with Python
3.12.14. This report separates deterministic integration checks from actual
model behaviour. It makes no claim of universal context-rot prevention.

## Recorded results, 2026-09-29

The release report (local-only reference; omitted from public distribution) retains exact inputs, prepared
requests, source quotes, model responses, settings, timings and failures.

| Check | Observed result |
| --- | --- |
| Declared-language fixtures | 37/37 matched their explicit expectations |
| Separate framing audit | 8/8 left reported/questioned statements unrecorded |
| Memory lifecycle | 13/13 checks passed |
| Actual Qwen 2.5 1.5B answers, memory off | 0/2 exact |
| Actual Qwen 2.5 1.5B answers, memory on | 2/2 exact |
| Model transport errors | 0 across four calls |

The language set is versioned. The frozen v0.7 function remains available via
`--language-version 0.7`. For v0.8, the expected result for “I prefer brief
answers” changes explicitly to `response_style=brief`; nine additional cases
cover courtesy wording, short style/language/export statements, and exclusions.
One control still expects “Actually, CSV please” to be missed. Passing the
complete set does not mean Relay understands arbitrary English or other
languages. The 23 expected active key/value entries matched exactly within
these hand-written fixtures only.

The earlier initial development report (local-only reference; omitted from public distribution) records the
same deterministic checks before the version was bumped. Its `library_version`
therefore remains 0.7.0 and its source fingerprint differs. It has not been
rewritten or presented as the final release.

## Smaller memory sent to the model

A controlled fixture contains 12 accepted values and one unresolved conflict.
The full audit snapshot occupies 4,369 serialized characters; the model-facing
view occupies 1,877, a **57.04% reduction in that memory block**. The complete
prepared request shrinks from 5,570 to 2,916 characters with identical selected
original history. All values, conflict details, source roles/indices and exact
quotes remain. Hashes, review IDs and detailed audit positions stay local.

This is not lossy summarization, automatic eviction or a larger storage limit.
It removes audit metadata the model does not need. No token, latency or accuracy
improvement is inferred from this character comparison.

## Actual model behaviour

The four calls used already installed `qwen2.5:1.5b` through local Ollama 0.34.2.
The same two synthetic histories and 5,000-character cap were used with memory
off/on. Source turns deliberately exceed the cap so that retaining a preference
in automatic memory can be checked separately from retrieving its full turn.
Expected answers enter only through original user statements and Relay's own
extraction; the scorer does not supply them in the model request.

Memory off/on used 1,501/1,650 aggregate observed prompt tokens. Recorded wall
times were 35.234/11.848 seconds; loading, caching and concurrent verification
were uncontrolled, so these times are **not evidence of a speed improvement**.
Two cases, one small model and one sample per condition cannot establish general
effectiveness or superiority over another memory product.

A separate managed-conversation report (local-only reference; omitted from public distribution)
records two real local calls. The application sends “Export as CSV. Thanks!”,
closes the connection, resumes from its database/session ID, then sends
“Actually, use JSON instead of CSV.” Relay restores the original history,
updates memory to JSON and preserves both original provider response objects.
Repeating the completed request ID is blocked without a third call. All six
integration checks pass; this is not an answer-quality benchmark or a test of
the Ollama Python SDK.

## Reproduce the memory comparison

From the source directory, choose a new report path:

```console
python examples/evaluate_automatic_memory.py --output evaluations/my-v08-check.json
python examples/evaluate_automatic_memory.py --live --model qwen2.5:1.5b --output evaluations/my-v08-live.json
```

The first command does not run a model. The second verifies local installed
weights and makes four calls; it never downloads a model or uses a paid API.

Both final real-inference reports identify library source SHA-256:

```text
9a7a63b521411a21e4b3ad1fb7fb6214f3fd789bc62b102bd9708144b20a1fe1
```

## Remaining limits

Memory recognition is a conservative English parser, not an LLM. Ambiguous
corrections can need review, unsupported prose can be missed, and a model can
still misuse retained information. History and memory are bounded; Relay does
not silently prune evidence to avoid a limit.

The new OpenAI adapters passed local contract tests using labelled stand-ins.
There was no installed OpenAI SDK or live hosted API verification. Responses
support is text-only; native reasoning/tool-item history is not implemented.
Managed conversations also require complete plain-text replies.

The final v0.6 grounded-recovery run completed **0/2 tasks**. Its
[published failures](RECOVERY-v0.6.md) remain relevant: succeeding at memory
retention does not establish correct evidence citation or task completion.
