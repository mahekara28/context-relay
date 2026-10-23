# Check remembered requirements before running a tool

> v0.17 narrows callback changes and adds `repair="known"` for local copies of
> already known values. See [repair safety and migration](REPAIR_SAFETY.md).
> The callback examples below remain valid, but unrelated edits now block.

`memory_bindings` adds constraints to existing developer-controlled tool policies.
It compares specific action arguments with current, undisputed user memory before
calling your tool. For example, a model proposing CSV when memory says JSON is
blocked before your export function runs. Checking makes no model calls. An
optional single repair callback can correct a rejected argument proposal.
`content=ContentPolicy(...)` additionally protects declared source fields before
execution, including after repair; see [content integrity](CONTENT_INTEGRITY.md).

```python
from context_relay import ActionRejected, auto_connect

with auto_connect(existing_client) as ai:
    ai.allow_tool(
        "write_export",
        memory_bindings={"format": "export_format"},
        optional_args=("rows",),
    )
    result = ai.invoke(model=your_model, messages=full_history)
    # Your existing provider-specific decoder returns a JSON argument dictionary.
    arguments = decode_export_arguments(result.response)
    try:
        result.prepared.wrap(write_export, tool="write_export")(**arguments)
    except ActionRejected as error:
        show_or_log(error.verdict)
```

`write_export(format, rows)` is your own synchronous tool. This example uses a
text connection with automatic memory. Include provider-required options in
`invoke()`, such as `max_tokens` for Claude. Keep the wrapper and
captured turn in application code; the model supplies only arguments. Register
policies before preparing the model call. Each successful guarded execution
advances the revision, so a new action needs a newly prepared turn. The library
never chooses or runs a tool on its own. LangChain's `invoke_result()` exposes the
underlying turn as `result.prepared.prepared`; use the same wrapper there.

## Exact behavior

- Missing memory, a pending dispute, a missing argument or a mismatched value
  blocks execution. Rejections identify the memory key and its source when known.
- Recognized spellings on `export_format`, `output_format`, `response_language`
  and `response_style` ignore case and outer whitespace. For example, `json`
  becomes `JSON` before the actual tool runs. Accepted changes appear in the
  check's `argument_normalizations`; receipts record executed values. Unknown
  values, custom memory keys and trusted fact bindings retain strict JSON
  equality. This finite mapping does not translate, infer synonyms or validate
  the generated content. Declare a separate content policy for source fields
  that must be preserved. See [the mapping](../src/context_relay/memory_values.py).
- An argument cannot be simultaneously optional, fact-bound or memory-bound.
- Content-protected top-level arguments are required and cannot also be optional.
- Existing `when` requirements and trusted `bindings` still apply. Memory does
  not populate trusted facts, authorize another tool, or override permissions.
- Checking and executing use the same existing SQLite transaction and revision
  check. A separate `relay.check()` is diagnostic; it is not an execution permit.
- `optional_args` are allowed without value constraints. Validate their content
  in the tool as your application requires.

Use these bindings for preferences such as export format, language labels and
selected presentation options. Do not use inferred memory as proof of identity,
consent, access rights, payment approval or verified facts. The application still
owns authorization and its tool allowlist.

## Preserve source fields alongside remembered preferences

```python
from context_relay import ContentPolicy

# source_rows is the application's validated source in the expected output shape.
policy = ContentPolicy(reference={"rows": source_rows}, exact=("/rows",))
ai.allow_tool(
    "write_export",
    content=policy,
    memory_bindings={"format": "export_format", "language": "response_language"},
    optional_args=("summary",),
)
```

Register this before preparing the request. `/rows` must match the source
snapshot, while `summary` remains unprotected. A correct JSON/Hindi label cannot
make a changed item name pass this policy. Structured arguments may be checked
before your tool renders CSV or text; the rendered file is not checked here.

The snapshot stays in the host policy, separate from facts and user memory.
Provide source data to the model explicitly if it needs that data to produce
the export. Replacing the registered tool policy advances the revision and
invalidates older prepared turns. See [content integrity](CONTENT_INTEGRITY.md)
for selected row fields, optional reordering, exact decimals and scope limits.

## Ask for one corrected proposal before executing

Use `execute_with_recovery()` instead of `wrap()` when your harness can obtain a
new argument proposal after a rejection. The existing tool has not run when the
repair callback starts. This works on `GuardedTurn` and `PreparedTurn`; for native
provider connections, capture `ai.guard.turn()` **before** the model call and use
that turn. Native `result.prepared` is a request snapshot, not a tool wrapper.

```python
def execute_export(tool, arguments):
    if tool != "write_export":
        raise ValueError("Unsupported tool")
    return write_export(**arguments)

with auto_connect(existing_client) as ai:
    ai.allow_tool("write_export", memory_bindings={"format": "export_format"},
                  optional_args=("rows",))
    result = ai.invoke(model=your_model, messages=full_history)
    arguments = decode_export_arguments(result.response)
    outcome = result.prepared.execute_with_recovery(
        "write_export",
        arguments,
        execute_export,
        repair=propose_corrected_export,  # Your sync callback; returns only arguments.
    )

    if outcome["status"] == "clarification_required":
        show_or_log(outcome["final_check"])  # Ask the user; nothing was executed.
```

`propose_corrected_export(request)` is application code. It can call your existing
model with its own timeout, input/output budget and provider handling, or use a
deterministic repair. Relay passes a JSON object with `tool`, `arguments`,
`rejection`, `context` (current trusted facts) and `user_memory` (the current
separate recall snapshot, including source references and disputes). The callback
must return a JSON **argument dictionary**, not a full tool call or a revision.
Treat recalled user text as data, and do not dispatch tools inside this callback.
The snapshot is not an already-budgeted provider request. Content diagnostics
contain codes, paths and a fingerprint, not reference values. Relay does not add
the content source to the repair packet; a callback that needs it must receive
it explicitly from application code.

Known binding mismatches, missing bound arguments and content mismatches permit
repair. Missing or disputed bound memory returns `clarification_required` without
calling the callback. Stale context, failed permissions, unknown tools, unknown arguments or
missing trusted facts stay blocked. If an authorization or stale-context issue
also exists, the result is `blocked` rather than a clarification request. Omitting
`repair` keeps checking enabled and makes no repair call.

Relay captures current state and memory together, releases its database
transaction, then calls repair at most once **per invocation**. It checks the new
arguments using the same tool policy, content snapshot and captured revision
before execution. A concurrent state change blocks execution; Relay never silently refreshes the
proposal or relaxes a policy. No callback can choose a different tool via the
returned dictionary. A second rejected proposal is returned to the harness.

The outcome has `status`, `executed`, `repair_attempts`, `initial_check` and
`final_check`. Status is `executed` for initial success, `repaired` for success
after the callback, `blocked`, `clarification_required`, `repair_failed`, or `repair_unavailable`.
Successful execution also returns `result` and `receipt`. An attempted repair
adds `repair_context_revision`; an exception or invalid callback return adds
`error_type` and no tool is run. Original callback exception text is not included
in that report. Invalid configuration can still raise before this flow begins.

An exception after the executor starts raises `ToolExecutionError` with its
check and failed receipt. It is **never** treated as a reason to repair or retry,
even if tool code raises `ActionRejected`. External side effects may already
have occurred. This method is synchronous, does not provide durable idempotency
or a provider retry budget, and does not keep a repair counter across separate
invocations. Persist the outcome in your application if you need that audit.

## What this cannot establish

A matching `language="Hindi"` label does not prove the accompanying paragraph is
Hindi. Matching a filename or format does not establish factual correctness or
safe content. A content policy checks only declared fields against its snapshot;
it does not establish source truth, freshness or correctness of unprotected
prose and derived values. Reports keep `semantic_meaning_checked=False`. An
executor can still change data after the checked boundary. A missed correction
can leave an old value undisputed; this check can then enforce that old value.
Use explicit trusted fact bindings when a
requirement must come from an authoritative application record.

This is an opt-in execution check, not a guarantee that the model understands or
obeys every instruction. It requires complete structured arguments and the
existing synchronous tool wrapper; it does not validate arbitrary streamed text.
The execution transaction cannot roll back an external service's side effects.
Keep idempotency and external transaction controls in the owning service.

The local pilot reports blocked actions separately from successful tasks. A
blocked wrong export is prevention, not a completed export.

See [v0.16 content evidence](../evaluations/CONTENT-v0.16.md) and
[validation guidance](VALIDATION.md) for the scope of local checks.
