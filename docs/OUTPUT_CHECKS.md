# Check a reply before accepting it

Remembering a requirement does not make a model obey it. `OutputPolicy` lets an
application enforce a few measurable requirements on complete text replies.
Checks run locally, without another model. They are off unless configured.

```python
from context_relay import MemoryPolicy, OutputPolicy, connect

with connect(
    existing_model_call,
    memory=MemoryPolicy(),
    output=OutputPolicy(format="json", required_keys=("name", "age")),
) as agent:
    result = agent.invoke(messages=full_history)
    print(result.response)       # Original provider response, unchanged.
    print(result.output_report)  # Exact checks, attempts and captured revision.
```

Ordinary `agent(...)` and managed `chat.send(...)` also enforce the policy.
`connect_openai`, `connect_anthropic` and `attach` accept the same `output=` option.
Known provider connections supply a strict text decoder. A generic model call
can return a string or a complete plain assistant message envelope; otherwise
set `OutputPolicy(decoder=your_response_to_text, ...)`.

## What can be checked

| Setting | Actual check |
| --- | --- |
| `format="text"` | Nonempty plain text |
| `format="json"` | One JSON object; no code fences, duplicate keys, NaN or infinity; without content, fractional/exponent numbers use Python floats and overflowing values are rejected |
| `required_keys=("name", "age")` | Those top-level JSON keys exist; values and their meaning are not verified |
| `content=policy` | Declared JSON fields/rows match an application-owned source snapshot; other content is not verified |
| `format="csv", csv_columns=("name", "age")` | Exact header/order and matching width for every row, using Python's strict CSV reader |
| `max_chars=64000` | Maximum returned text characters; default 64,000 |
| `max_words=100` | At most 100 whitespace-separated words; not a language-aware word count |

CSV requires explicit columns: arbitrary prose could otherwise pass as valid
one-column CSV. A correctly headed CSV containing zero data rows is accepted.
Quoted commas/newlines are parsed, not counted as extra columns.

To follow a current memory preference:

```python
policy = OutputPolicy(
    format="memory", memory_key="export_format",
    required_keys=("name", "age"), csv_columns=("name", "age"),
)
```

With memory enabled, this selects the JSON or CSV check from the captured,
undisputed memory value. Required keys apply to JSON; columns apply to CSV.
Missing, disputed, or unsupported format values stop before generation. Relay
does not guess a CSV schema or infer a numeric limit from a word such as "brief".
An application must define what it can objectively verify.

## Preserve declared source data

```python
from context_relay import ContentPolicy, OutputPolicy

# The application builds the reference in the expected output shape.
policy = ContentPolicy(reference={"rows": source_rows}, exact=("/rows",))
output = OutputPolicy(format="json", content=policy)
```

Pass `output` to the connection. A translated name, changed amount or missing row
inside `/rows` fails even when the reply is valid JSON. Other fields, such as a
summary outside `/rows`, remain unchecked. Use `RowRule` to protect selected row
fields while allowing explicitly configured transformations such as reordering.

Content requires JSON output. `format="memory"` is supported only when captured
memory resolves to JSON; CSV/text content policies fail before a provider call.
For CSV/text exports, check structured tool arguments before the application
renders them instead. See [content integrity](CONTENT_INTEGRITY.md) for examples,
strict number categories and lossless `from_json()`/`check_json()` methods.

The reference stays in the host policy. Relay does not extract it from messages
or add it to conversation context. The application supplies source data to the
model explicitly when needed. Successful reports expose
`result.output_report.content.report`; content rejections expose
`OutputRejected.content` when the content check ran. These reports identify
protected scopes and retain `semantic_meaning_checked=False`.

## Failure and optional correction

By default there is **one model call and no automatic retry**. An invalid reply
raises `OutputRejected`. Its `response`, `issues`, `attempts`, and `prepared`
attributes support inspection. Managed history keeps the user request pending;
it does not save the rejected reply as an accepted assistant turn.

```python
from context_relay import OutputRejected

try:
    response = chat.send("Export as JSON.", request_id="export-1")
except OutputRejected as failure:
    print(failure.issues)
    # Let application policy decide whether to retry, change the request,
    # or present a failure. The rejected response is available for inspection.
```

Opt into bounded correction with `OutputPolicy(..., max_retries=1)`. Relay can
then make at most two generation calls. The hard maximum is two retries, or
three total calls. Adding a content policy does not enable extra calls. Retry
metadata contains only fixed failure codes and the configured contract, never
the rejected model text or content reference values. If correction needs the
source, application code must supply it explicitly. Original history is unchanged;
memory is processed once per request, and only a passing reply is returned.
Contract metadata and feedback share the ordinary context/token budgets.

Provider errors, cancellation, unsupported responses and state changes are not
automatically retried. Provider SDK retries are separate and remain configured
by the application. Extra Relay retries can incur additional API charges or
local compute time, so they are disabled by default.

When the provider supports constrained JSON generation, combine it with these
checks. For example, an existing Ollama call can also receive `format="json"`
or a JSON schema through the normal provider options. This reduces malformed
generation at the source; Relay still validates the returned reply. Schema
constraints do not guarantee factual accuracy. See [Ollama's structured-output
documentation](https://docs.ollama.com/capabilities/structured-outputs). Schema
material outside prepared messages needs appropriate token-overhead allowance.

## Connect and configure once

The policy is also available in the local JSON config:

```json
{
  "output": {
    "format": "json",
    "required_keys": ["name", "age"],
    "max_retries": 0
  }
}
```

Callables such as a decoder belong in Python's `connect(output=...)`, not a
configuration file. Serialized policies with content include the reference for
host-side persistence; keep that data separate from model feedback. Async
connections perform the same checks and bounded retry
sequence. Requests enabling streaming or tools fail clearly. Unexpected streams
are not consumed or closed; the owner can inspect `OutputRejected.response`.
Truncated, refused, tool, thinking or annotated provider output cannot silently
become accepted plain text through the built-in decoders.

## Limits

Format, required-key and length checks alone can accept a wrong name. A content
policy additionally establishes preservation only within declared source scopes.
It does not establish factual correctness, source freshness, semantic agreement,
a valid business decision or correct unprotected arithmetic/prose. External
actions still need the existing policy/tool guard; an executor can change data
after a checked boundary.
State is checked after generation; a later state change still requires a fresh
execution-time check. Code that bypasses Relay's connection bypasses these checks.

The grounded-citation loop has its own answer schema and checks. Combining
`run_grounded` with an output-enabled connection is rejected explicitly rather
than silently bypassing this policy. Use a separately configured connection for
that workflow. Complete native agent/tool output needs a protocol-aware checker;
this feature checks final plain-text replies only.

See [v0.16 content evidence](../evaluations/CONTENT-v0.16.md) and
[validation guidance](VALIDATION.md) for the scope of local checks.
