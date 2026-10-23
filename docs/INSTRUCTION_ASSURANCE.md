# Explicit confirmation for important preferences

Relay can miss an instruction written in unfamiliar language. A dangerous
consequence is then repairing a valid new proposal back to an old remembered
setting. The opt-in instruction assurance mode prevents that particular path
by requiring an explicit review of selected preferences after every new user
turn. It does **not** claim to understand the missed sentence.

Normal `MemoryPolicy()` behavior is unchanged. Enable this only for fields
where confirmation effort is justified:

```python
from context_relay import AutomaticMemory, MemoryPolicy

memory = AutomaticMemory(
    existing_guard.store,
    existing_guard.session_id,
    policy=MemoryPolicy(assurance_fields=("export_format",)),
)
memory.process(original_messages, protocol="plain")
plan = memory.review_plan()
```

Use the history protocol appropriate to the integration; it cannot change in
the same session. `original_messages` must include the unchanged original
prefix and the new user input. `existing_guard` is the application's existing
`ContextGuard`. This example does not call a model.

`plan` contains the actual latest user message, current suggestions, scopes and
disputes, plus a session/revision/history fingerprint. Show that data in your
application's authenticated review UI. The host must obtain explicit choices
for **every protected field**, even if the choice is to keep its current value:

```python
# This dictionary represents an explicit reviewer choice, not an automatic
# default or a model's interpretation of the message.
choices = {"export_format": {"value": "JSON", "scope": "turn"}}
memory.resolve_review(plan, decisions=choices)
```

`scope="turn"` selects a temporary built-in preference for the current user
turn and keeps the standing default. `scope="durable"` selects the standing
value and removes that field's current override. Custom memory keys support
durable review only; finite built-in preferences require a supported canonical
value. A current-scope conflict can be resolved by a turn choice. An independent
durable dispute needs an explicit durable decision; a temporary choice cannot
quietly erase it.

If the source, history, memory or session revision changes while the review is
open, resolution raises `MemoryConflictError`. Display a fresh plan and ask for
new choices. Do not automatically resubmit old choices. Resolution records an
audit event; the source quote is the reviewed message, while the chosen value
comes from the host review and is labelled `method="structured_review"`.

## What is blocked

Until reviewed, `effective_snapshot()` exposes each protected field with
`value=None`, `disputed=True` and `reason="confirmation_required"`. Existing
memory-bound actions and known-value repair therefore cannot silently use its
old value. Applications must register those checks, for example:

```python
existing_guard.allow_tool(
    "export_manifest",
    memory_bindings={"format": "export_format"},
)
```

Feed original user input into memory **before** capturing the guarded turn.
Calls that bypass Relay, arguments without a memory binding, and user updates
never supplied to Relay are outside this protection. `snapshot()` and
`entries()` deliberately remain operator views of standing memory; do not use
them as an approval check or copy those values directly into an effect.

Even a clearly recognized new instruction requires review in this mode.
Repeated history and a supported tool continuation preserve approval. A new
user message, including a harmless acknowledgement, requires fresh review.
After a temporary override expires, the standing value also needs confirmation
before it can drive another protected action.

Protection is stored with the session. Reopening it with a default
`AutomaticMemory` cannot disable the gate. Adding protected fields invalidates
the previous approval; removing them is not an implicit configuration change.
Use a new session when deliberately choosing a different protection boundary.
Forgetting a value does not remove its protection. Approval cannot be restored
by undo or copied into a successor session during rollover. The new memory
schema is version 4; older Relay releases reject it rather than ignoring the
gate. Back up experimental databases before upgrades or downgrades.

## Measured scope and remaining cost

The [18-case synthetic fixture](../tests/fixtures/instruction-assurance-v021.json)
was frozen before implementing this mode. Its SHA-256 is
`48d0edc1193bb35fa0cfaeefe76198a20c9373ab0cf044ad4d78e64b976fd97f`.
It includes indirect wording, German, Japanese, Arabic, Hindi/Hinglish,
temporary requests, quoted instructions, negation and harmless messages.

The default parser produced the expected current value in **1 of 11 clear
change cases** and did not flag either of the **2 unclear cases**. All **5
unchanged controls** remained unchanged. This is a small adversarial development
diagnostic, not an estimate of language accuracy in a real population.

With assurance enabled, **all 18 cases require explicit review** and expose no
usable protected value before it. That includes **5 unnecessary reviews on
unchanged controls**. This is confirmation-gate coverage, not 18 understood
sentences or 18 completed tasks. No model/network calls or additional grammar
rules were used. The baseline and regression results retain failures separately;
the fixture becomes exposed regression coverage after this implementation.

The host still authenticates the reviewer and decides whether their choice is
correct. Automatically confirming an LLM's interpretation defeats the purpose.
Review changes only user-stated preferences, never trusted facts or tool
permissions. It does not verify external truth, business reasoning, source
quality, arbitrary generated content or destination effects. Pair it with
application permissions, fresh authoritative data, checked results and
destination idempotency/version conditions where needed.

Assurance records and exact source quotes count against the existing memory
limits. Long messages or many protected fields can require larger configured
limits; exceeding a limit aborts the update atomically. The gate runs locally
without an extra model or API call, but the human review and integration effort
are real costs.
