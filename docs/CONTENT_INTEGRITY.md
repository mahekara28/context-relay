# Preserve declared source data in structured output

Correct format and language labels do not ensure correct contents. `ContentPolicy`
compares declared JSON fields with an application-owned source snapshot. It can
reject a translated item name, changed amount, missing row or swapped row values
before a tool runs, including after an optional repair. These are local checks;
they make no model calls and do not judge arbitrary factual correctness.

The application selects the source, builds the expected output shape, and states
which parts must survive unchanged. Relay does not extract a source from chat,
infer which fields matter, rename fields or calculate expected totals.

## Protect an export before execution

```python
from context_relay import ContentPolicy, auto_connect

# Loaded and validated by your application. Monetary strings preserve display precision.
source_rows = [
    {"item": "Pencils", "units": 18, "unit_price": "3.50", "total": "63.00"},
    {"item": "Notebooks", "units": 7, "unit_price": "12.00", "total": "84.00"},
]
policy = ContentPolicy(reference={"rows": source_rows}, exact=("/rows",))

with auto_connect(existing_client) as ai:
    ai.allow_tool(
        "write_export",
        content=policy,
        memory_bindings={"format": "export_format", "language": "response_language"},
        optional_args=("summary",),
    )
    # Your application supplies the source in full_history if the model needs it.
    result = ai.invoke(model=your_model, messages=full_history)
    arguments = decode_export_arguments(result.response)
    result.prepared.wrap(write_export, tool="write_export")(**arguments)
```

`write_export(format, language, rows, summary=None)` and the decoder belong to
your application. Current, undisputed memory must provide both bound preferences.
The model supplies arguments; application code owns the policy and captured turn.
Register the policy before preparing the request. A mismatch raises
`ActionRejected` before `write_export` runs.

Here `/rows` preserves the entire array, row order, row fields and literal
values. JSON object key order does not matter. `summary` may vary because it is
outside that protected subtree and is explicitly permitted by the tool policy.
Content-protected top-level arguments are required: `rows` must not also appear
in `optional_args`. Existing authorization requirements and fact/memory bindings
still apply. Content checks grant no permission to run a tool.

The policy takes a snapshot; later changes to `source_rows` do not update it.
When the application source changes, create and register a replacement policy.
That tool-policy change advances the session revision, so an older prepared turn
cannot execute against the replacement silently. Prepare a new turn. The policy
does not poll the source or establish that the snapshot is still current.

## Choose whole subtrees or selected row fields

`exact` contains JSON pointers to complete subtrees. Arrays preserve order and
multiplicity; objects preserve their key sets and values. Missing values differ
from explicit `null`. Paths use JSON Pointer escaping (`~1` for `/`, `~0` for `~`),
with no wildcards. Source and candidate use the same paths and field names.

Use a `RowRule` when selected fields must remain together but the application
allows additional row fields, such as a separate translated display label:

```python
from context_relay import ContentPolicy, RowRule

rows_policy = ContentPolicy(
    reference={"rows": source_rows},
    rows=(RowRule(
        path="/rows",
        fields=("item", "units", "unit_price", "total"),
    ),),
)
```

`fields` is a tuple of literal row object key names, not nested paths. Every
selected field must be present in every row. Values are compared together as
whole projected rows, so independently preserved column values cannot hide a
changed association. `allow_extra_fields=True` is the default; extra fields are
not checked. Set it to `False` to require exactly the selected fields and any
identity key. Do not also add `exact=("/rows",)` to this policy: overlapping
protected paths, including parent/child paths, are rejected.

Row ordering is preserved by default. To allow reordering explicitly while
tracking rows by application-provided identifiers:

```python
identified_rows = [
    {"line_id": "a", "item": "Pencils", "units": 18},
    {"line_id": "b", "item": "Pencils", "units": 7},
]
reorder_policy = ContentPolicy(
    reference={"rows": identified_rows},
    rows=(RowRule(
        path="/rows", fields=("item", "units"), key="line_id", ordered=False,
    ),),
)
```

`key` is also a literal field name. It is included in comparison even when it is
not listed in `fields`. Identities must be unique, nonblank strings or integers;
booleans are invalid, and the string `"1"` differs from integer `1`. Every source
identity must appear exactly once in the candidate with its protected values.
`ordered=True` additionally requires the original identity order.

Without a key, `ordered=False` compares a multiset of whole projected rows. It
permits reordering but preserves duplicate counts: two identical source rows
require two identical candidate rows. It does not infer an identity from a name.

A policy needs at least one actual rule. Invalid or missing protected source
paths, invalid source rows and overlapping rules fail during configuration.
Input size is bounded (`max_chars=256000` by default). `max_rows=10000` applies
to each array selected by a row rule; exact subtrees are bounded by input size
and depth. Exceeding a candidate bound fails the check rather than dropping data.

## Preserve JSON types and decimal values

Equality is literal within declared scopes:

| Source and candidate | Result |
| --- | --- |
| Integer `1` and fractional number `1.0` | Different JSON number categories |
| Fractional numbers `2.50` and `2.5` | Equal numeric values |
| Boolean `true` and number `1` | Different types |
| String `"2.50"` and string `"2.5"` | Different strings |
| `"Pencils"` and a translation, changed case or extra space | Different strings |

Strings receive no trimming, case folding or Unicode normalization. Numerical
value equality does not preserve decimal display formatting; use strings when
the number of displayed decimal places is itself a requirement.

Use raw JSON methods to avoid an intermediate binary floating-point conversion:

```python
decimal_policy = ContentPolicy.from_json(
    '{"rows":[{"unit_price":2.50,"units":1}]}',
    exact=("/rows",),
)
assert decimal_policy.check_json(
    '{"rows":[{"unit_price":2.5,"units":1}]}'
).valid
assert not decimal_policy.check_json(
    '{"rows":[{"unit_price":2.5,"units":1.0}]}'
).valid
```

`from_json()` and `check_json()` parse fractional JSON numbers losslessly.
`ContentPolicy(reference=...)` and `check(candidate)` accept ordinary Python
JSON-compatible data; they cannot recover precision already lost in a Python
`float`. Tool argument dictionaries have this same limit. Use monetary strings
or scaled integers when an argument path must preserve exact monetary data.
Python `Decimal` objects are not accepted as ordinary JSON dictionary values;
use the raw JSON methods instead. Duplicate JSON keys and nonfinite numbers are
rejected.

## Check complete JSON replies

```python
from context_relay import OutputPolicy, connect

with connect(
    existing_model_call,
    output=OutputPolicy(format="json", content=policy),
) as agent:
    result = agent.invoke(messages=full_history)
    print(result.output_report.content.report)
```

The same reference shape is compared with the returned JSON object. The output
checker preserves fractional JSON numbers while checking protected content.
`format="memory"` also works when the captured preference resolves to JSON.
With content configured, CSV/text output or a memory preference resolving to CSV
is rejected before the provider call. For CSV or text exports, check structured
tool arguments first, then let the application tool render them. Relay does not
check the tool's eventual rendered file with this policy.

## Repair, reporting and source handling

Action recovery still permits at most one optional repair callback per
`execute_with_recovery()` invocation. A content mismatch can trigger that callback
when no authorization, stale-state or other nonrepairable issue also blocks it.
Relay checks the new proposal against the same content policy and captured
revision before dispatch. An exception after the executor starts never triggers
repair or an execution retry. See [memory action checks](MEMORY_ACTIONS.md).

Version 0.17 additionally restricts action repair to failed supported scopes and
provides `repair="known"` for exact source restoration. See
[repair safety](REPAIR_SAFETY.md) for conservative row-rule behavior and limits.

Output correction retains its existing opt-in `max_retries` budget; adding
content does not enable retries. See [output checks](OUTPUT_CHECKS.md).

Content reports contain fixed issue codes, paths, protected scopes and a source
fingerprint, with `semantic_meaning_checked=False`. They do not contain reference
values. `policy.check(candidate).report` and `policy.check_json(text).report`
provide standalone diagnostics. Tool verdicts expose `content_check`; output
results expose `output_report.content`, and content failures expose
`OutputRejected.content` when the content check ran.

The reference lives in the application-owned policy, not in trusted facts, user
memory or conversation context. Content metadata sent to a model does not
include the reference values. The action repair callback still receives its
ordinary proposal and fact/memory snapshots, but Relay does not append the
content source to them. If repair needs original data, application code must
provide that source explicitly. `policy.to_dict()` and serialized output/tool
policies do include the reference for host-side persistence; store them as
application data, not as feedback to the model.

These checks establish preservation against the declared snapshot. They do not
establish source truth or freshness, validate unprotected summary prose, verify
undeclared derived arithmetic, or stop an executor from changing values after
dispatch. Protect application-computed totals explicitly when they matter.
Code that bypasses the guard also bypasses its checks. Upgrade every worker
sharing a store before relying on content policies: older Relay binaries do not
implement this check even if they can read the stored policy.

See the [v0.16 content evidence](../evaluations/CONTENT-v0.16.md) for scoped local
evaluation and [validation guidance](VALIDATION.md) for what each check establishes.
