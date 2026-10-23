# Review corrections before changing memory

An application can register the decisions it cares about, such as the allowed
output formats. Relay then checks the latest original user request for a possible
change. For example, `Use CSV instead of JSON` produces a proposal to change
`output_format` to `csv`.

**Detection never changes recorded memory.** The application displays the
proposal, gets the user's confirmation through its own interface, and explicitly
accepts that proposal. A confirmed change goes into the next prepared request.

```python
from context_relay import ContextGuard, CorrectionField, review_corrections

fields = [CorrectionField("output_format", choices=("json", "csv", "tsv"))]
history = [{"role": "user", "content": "Use CSV instead of JSON."}]

with ContextGuard() as guard:
    guard.remember("output_format", "json")
    prepared = guard.prepare(history)
    review = review_corrections(prepared, fields)
    proposal = review.proposals[0]
    print(proposal["key"], proposal["value"], proposal["evidence"]["quote"])

    # Call this from your application's confirmation handler, after review.
    # Do not put it in an automatic loop over detected proposals.
    # review.confirm(guard, proposal["id"])

    # After confirmation, prepare again before a new model decision:
    # refreshed = guard.prepare(history)
    # response = your_model_call(messages=refreshed.messages)
```

The first version recognises a deliberately small set of English expressions:

- `Use CSV instead of JSON.`
- `Switch from JSON to CSV.`
- `Change output_format to csv.`
- `Set the output format to csv.`
- `Use CSV for output format.`

`Actually,` and `please` can precede those expressions. Values are matched without
case sensitivity and stored using the exact canonical spelling in `choices`.
Aliases can supply a shorter field name:

```python
CorrectionField("deployment_region", choices=("Europe", "Asia"), aliases=("region",))
```

## Connect once and review before a model call

Opt in at the existing model-call boundary. A detected proposal raises
`CorrectionRequired` **before** the provider is called, so the agent does not
proceed using an old recorded requirement while a proposed change is unresolved.
Existing connections without `correction_fields` keep their previous behavior.

```python
from context_relay.connection import connect, CorrectionRequired
from context_relay.corrections import CorrectionField

call_ai = connect(
    existing_model_call,
    correction_fields=[CorrectionField("output_format", choices=("json", "csv", "tsv"))],
)
call_ai.remember("output_format", "json")

try:
    response = call_ai(messages=history)
except CorrectionRequired as pending:
    review = pending.review
    # Present these to your user and return control to your application UI:
    print(review.proposals)

# Later, only in your application's explicit confirmation handler:
# review.confirm(call_ai.guard, selected_proposal_id)
# response = call_ai(messages=history)

# Or, if the user explicitly rejects all suggestions and keeps current memory:
# call_ai.dismiss_corrections(review)
# response = call_ai(messages=history)
```

`call_ai.review_corrections(history)` can inspect proposals without a provider
call. The same local review methods work on async connections; await their model
calls as usual. Dismissal applies only to the same captured history and current
recorded revision, and only for this connection's lifetime. A new message or state
update requires review again. Dismissal leaves both facts and original messages
unchanged; it does not rewrite the user's request or guarantee model compliance.

A confirmed direct change no longer produces a proposal when the identical
request is retried. More ambiguous text may need explicit dismissal after review.
Do not automatically catch and dismiss `CorrectionRequired`; doing so removes
the review boundary that this option is intended to provide.

## What needs review

Each proposal has a `status`, canonical `candidates`, exact source `evidence`, and
the current recorded value. `suggested` means the entire message matched one
recognised imperative for one configured field. It still needs explicit
confirmation. It is a pattern match, not a guarantee about the user's intent.

`needs_review` means Relay did not choose a new value. Examples include a quote
containing a correction, a question, negation, more than one candidate field, or
`Use CSV or TSV instead of JSON`. Your confirmation handler must explicitly choose
one of the proposal's canonical candidates:

```python
# Only after your interface has obtained an explicit choice:
# review.confirm(guard, proposal["id"], value="tsv")
```

Unregistered values cannot be confirmed. A direct request that already matches
the current value is skipped. Multiple proposals do not constitute a batch
update: one accepted proposal advances the state revision. Prepare and review
again before accepting another.

## Boundaries and provenance

The recogniser examines only the **last original user message** captured by
`ContextGuard.prepare`. Earlier user requests, assistant suggestions, and Relay's
synthetic context metadata are not mined for current changes. Your application
must correctly distinguish user input from retrieved or tool content; plain text
cannot establish a speaker's identity or the intent behind an unmarked quote.
When a native tool conversation ends on a tool result awaiting an assistant reply,
correction detection skips that request entirely. The tool result is never
relabelled as user input.

The field schema is application configuration. Do not let an LLM or arbitrary
retrieved text create schemas or call `confirm`. Relay does not provide your
application's login, authorization, or confirmation UI.

Confirmation is bound to the originating guard, session, history ID, and captured
state revision. It uses the store's atomic revision check. If another update or
tool execution changed the revision while the user was reviewing, `ConflictError`
requires preparing and reviewing again. Successful confirmation keeps the exact
source request and proposal metadata in the fact event's `text` field. History
IDs and proposal IDs are provenance identifiers, not credentials.

The recogniser runs locally with Python's standard library. There is no model
download, API key, network call, or automatic model training. Free-form decisions,
arbitrary facts, semantic conflict resolution, and reliable interpretation of
every language or sentence are outside this release's scope.
