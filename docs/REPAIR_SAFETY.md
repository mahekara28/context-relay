# Repair only what failed

A second model proposal is not inherently better. Relay v0.19 narrows what an
action repair is allowed to change and offers a local alternative for values
already known to the application. Neither mode changes authority, refreshes a
stale decision, enables repair by default, or retries a started tool.

## Copy known values without a second generation

```python
outcome = prepared.execute_with_recovery(
    "export", proposed_arguments, existing_dispatcher, repair="known",
)
```

Or use a registered tool:

```python
outcome = tools.bind(prepared).execute_with_recovery(
    "export", proposed_arguments, repair="known",
)
```

`known` repairs failed fact bindings, current undisputed memory bindings and
failed `ContentPolicy.exact` subtrees. It also recomputes failed declared verification claims and restores selected fields in identity-keyed rows when each original identity is unambiguous. It copies the captured authoritative value
for that declared constraint; it does not ask a model to rewrite the proposal.
Other arguments and siblings of a nested exact path stay unchanged. The normal
policy and revision checks must still accept the resulting arguments before
execution. Missing or disputed memory requests clarification rather than guessing.

It stops with `repair_unavailable` when a safe supported replacement cannot be
constructed: missing/changed/duplicate row identities or counts, keyless row repair, unknown/global content failures, unsupported
parent reconstruction, conflicting sources, or a decimal source value that would
lose precision when converted into a Python tool argument. No partially repaired
action is dispatched. Refresh the source or obtain a new reviewed proposal as
appropriate; do not silently retry a stopped action.

## Keep callback repair within its allowed changes

The existing `repair=your_callback` API still accepts a complete replacement
argument dictionary for the same tool. Relay compares it with the original:

- Only bound arguments that failed may change.
- Only failed exact content subtrees may change; nested siblings remain fixed.
- For failed row rules, only selected fields at existing row positions may
  change. Row count, supplied identity keys and unprotected fields stay fixed.
  Keyless rows have index association only; Relay does not infer their identities.
- New, removed or edited unrelated optional arguments cause a block.

Supported finite memory spellings such as `json`/`JSON` and `hindi`/`Hindi`
count as the same value in this comparison and are omitted from `changed_paths`.
This applies only to memory-bound arguments with known equivalents, not general
case folding. `VerificationPolicy.equivalents` can separately declare finite groups of acceptable strings for a named claim. It never enables global case folding or treats numeric strings as numbers. Trusted facts and undeclared custom values stay exact. The final engine checks
the canonical values that will actually reach the tool.

After the comparison, every original policy is applied again. Passing the change
scope does not override content checks, permissions or revision checks. A scope
violation is saved as a `repair_scope_violation` audit record before execution.
The new `repair_scope` diagnostic contains paths and fixed reasons, not changed
values. Existing ordinary check issues and callback requests retain their usual
argument/context data.

This is intentionally stricter than v0.16. A callback that fixed format while
rewriting an unrelated summary or changing optional rows could previously pass.
It now blocks. If legitimate work requires a broader transformation, plan a new
proposal under an explicit application contract instead of widening repair to
arbitrary changes. Complete-output regeneration through `OutputPolicy.max_retries`
is a separate opt-in path; its declared output/content checks are reapplied, but
this action-argument change comparison does not apply to that path.

## Read the outcome

`status` is `executed`, `repaired`, `blocked`, `clarification_required`,
`repair_failed` or `repair_unavailable`. Success includes `result` and `receipt`.
`initial_check` and `final_check` remain available. Additional metadata:

| Field | Meaning |
| --- | --- |
| `repair_mode` | `known`, `callback`, or `None` |
| `repair_attempts` | At most one attempt in this invocation |
| `repair_callback_calls` | Zero for known repair; at most one for callback repair |
| `repair_scope` | Validity, allowed paths, up to 20 changed paths and fixed reasons |
| `repair_unavailable_reason` | Fixed reason for stopping deterministic repair |

These limits are per invocation, not a global model-provider retry budget. The
application owns timeouts for its callback. A callback can itself make multiple
calls unless the application limits it; Relay does not hide that as one model
call. Ordinary executor exceptions propagate as `ToolExecutionError`, with the recorded
failed receipt, and never trigger repair. Interruptions such as `KeyboardInterrupt`,
`SystemExit` and cancellation propagate unchanged after the failed receipt commits.
Even a broken exception formatter or invalid tool result consumes the captured turn;
reusing it cannot execute again while storage is available. The SQLite transaction is not atomic
with external services; use the destination's idempotency controls for crash
recovery after an external effect.

## What this establishes

This prevents a repair from silently changing unrelated declared arguments and
rechecks the protected result. It cannot establish arbitrary prose correctness,
truth of an application-supplied source, or safety of tool code that bypasses the
boundary. An initially allowed unchecked field is still unchecked. A repaired
format/language label does not translate or verify an unprotected summary.

See [content preservation](CONTENT_INTEGRITY.md),
[tool registration](TOOL_REGISTRY.md), and
[measured v0.17 evidence](../evaluations/RELIABILITY-v0.17.md).

## More known repairs in v0.19

A failed verification claim can be replaced with `VerificationPolicy.expected()` only when its application-owned evidence is usable and the replacement survives JSON conversion without losing precision. Good claims and unrelated arguments are left alone. Source expiry, contradictory sources or an incompatible overlapping rule stop repair. One final normal check runs immediately before execution.

For a failed keyed row rule, Relay matches each existing row to the trusted source by its exact identity and copies only selected fields. It preserves the proposed row order and every unprotected field. Missing or duplicate identities, inserted/deleted rows, and order changes under an ordered rule require a new reviewed proposal. This is not permission to reconstruct a table or translate item names.

For a string claim with legitimate alternatives, declare them explicitly:

```python
checks = VerificationPolicy(
    sources=sources, claims=claims,
    equivalents={"status": [["paid", "settled"]]},
)
```

Both strings pass only for that named claim and only when the expected value is in that same group. A known repair still returns the canonical value computed from the source. Application-owned aliases are a contract, not a semantic similarity guess.
