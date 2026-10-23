# Automatic user memory

## Version 0.22.0a2: mixed messages and history import

Bounded acknowledgments such as `Thanks, this will help.` no longer cancel a
complete preference update in the same message. An unhandled possible qualifier
can instead dispute a recognised existing target, preventing repair from using an
obsolete value. Quoted, reported and conditional material remains excluded.
Scope changes are processed in user-message order whether history arrives as one
batch or incrementally. Recognition still uses finite grammar.

Previously consumed history is not reinterpreted on upgrade. Restate an affected
instruction or explicitly review it; replay must not restore forgotten or dismissed
memory. Read the [patch upgrade notes](RELEASE-v0.22.0a2.md#upgrade).

## Version 0.18: correction recognition and clarification

The free local parser now handles more indirect English instructions and bounded
Hindi/Hinglish, Spanish and French instructions for export/output format, reply
language and reply style. This is finite grammar support, not general translation.

**Behavior change:** connected text calls with automatic memory now raise
`MemoryClarificationRequired` when an existing preference is disputed, before the
next model call. Handle the exception and show its `prompt`; provide a new full
instruction to resolve the setting. Managed conversations store the local
question and accept the next answer without retrying the blocked request.
[Clarification and migration guide](INSTRUCTION_CLARIFICATION.md).


Relay can now remember supported user statements without a developer listing
every possible field or confirming every clear update. The default extraction
uses deterministic Python code: no model download, paid API, embedding service
or network call. An optional model interpreter can suggest additional recall,
but its proposals always need review before acceptance.

It is **user-stated recall**, not a database of verified facts. It is separate
from `guard.remember(...)` and tool policies. A stored sentence cannot grant a
tool permission or overwrite an application-recorded fact.

## Connect once

```python
from context_relay import MemoryPolicy, RelayConfig, connect

with connect(
    your_model_call,
    config=RelayConfig(db_path="relay.sqlite3"),
    memory=MemoryPolicy(),
) as agent:
    history = [{"role": "user", "content": "Use CSV for the export format."}]
    response = agent(messages=history)
```

Pass the complete **original** conversation on each call. Add the real assistant
response and the next user message to that list before calling again. Do not
replace your history with the shorter messages Relay sent to the model.
For plain text chats, [managed conversations](CONVERSATIONS.md) can keep this
history for you: create `chat = agent.conversation()` and use `chat.send(text)`.

If the next user message is `Actually, use JSON instead of CSV.`, Relay can update
the remembered export format automatically when CSV belongs to exactly one
known memory key. No `CorrectionField` is needed for this recall.

`MemoryPolicy` can also be placed in `RelayConfig(memory=MemoryPolicy())`. Existing
connections without that setting keep their previous behavior. A persistent
database needs the same saved `session_id` on reconnect; creating another
connection without that ID starts another conversation.

## What is recognized

| User's complete message | Result |
| --- | --- |
| `The deployment region is eu-west-1.` | Remembers `deployment_region = eu-west-1`. |
| `My name is Ada.` | Remembers `my_name = Ada`. |
| `Use CSV for the export format.` | Remembers or explicitly updates `export_format = CSV`. |
| `Please set the output format to JSON.` | Remembers or explicitly updates `output_format = JSON`. |
| `I prefer brief responses for the response style.` | Remembers or explicitly updates `response_style = brief`. |
| `I prefer brief answers.` | Remembers or explicitly updates `response_style = brief`. |
| `Reply in Hindi.` | Remembers or explicitly updates `response_language = Hindi`. |
| `Export as CSV. Thanks!` | Remembers or explicitly updates `export_format = CSV`. |
| `I want the output in JSON.` | Updates the sole known `export_format`/`output_format`, defaults to `export_format`, or queues review when both exist. |
| `I need responses to be short.` | Remembers or explicitly updates `response_style = short`. |
| `Change the export format from CSV to JSON.` | Updates `export_format` only if the old CSV value matches; otherwise queues review. |
| `Make it brief.` immediately after a qualifying style instruction | Queues a review-only contextual proposal; never guesses an automatic change. |
| `Actually, the deployment region is us-east-1.` | Explicitly updates that named subject. |
| `Actually, use JSON instead of CSV.` | Updates the unique known key whose current value is CSV; otherwise queues review. |
| `The deployment region is us-east-1.` when a different region is already remembered | Queues a conflicting declaration for review. |

`Remember that ...` is an optional prefix on supported forms. Field names come
from the subject in the sentence: Relay removes a leading `the`, lowercases the
name, and turns spaces, hyphens and underscores into one underscore. For generic
custom fields, `my` and `our` remain part of the name. Standard preference
shortcuts have their documented mappings; an exact existing custom field takes
precedence over dropping descriptive words from its name.

Multiple supported sentences or lines in the same message are processed in
order. A batch containing an initial value and a clear later correction retains
the corrected value. Repeating the same value does not create another memory
change event.

This is a **limited English grammar**, not general understanding. For example,
`Actually, CSV please` is missed by default. `Make it brief` only gets a local,
review-required proposal when the immediately preceding user message is a single
supported style statement matching current undisputed style memory. Reaffirming
that same value can qualify even when its original stored source is older.
Other uses remain unsupported. Subject names use
Latin letters, numbers, spaces, hyphens and underscores; values can contain other
text. The [wording guide](MEMORY_LANGUAGE.md) lists the supported style, language
and export-format shortcuts, courtesy phrases, and named corrections such as
`Export as JSON instead of CSV.` A mismatched named old value becomes a proposal;
Relay does not guess another target or treat different wording as synonyms.
Unrecognised `from ... to ...` syntax is skipped instead of creating a new field
whose name accidentally includes the old value. Named style, language and
format instructions support additional `I want/need`, `make` and `switch` forms;
see the wording guide for exact boundaries and output-alias ambiguity handling.

v0.15 also recognises bounded corrections whose field and value appear in
different clauses of one original message. For example, after `Export as CSV.`:

```text
You have CSV recorded as my export format; please correct that to JSON.
```

This explicit local reference can update the named field automatically. A
message such as `My recorded response language needs correcting. I haven't
decided what to replace it with.` instead flags the known language for
clarification and retains its old value. Supported explanations and unchanged
sibling fields are understood as part of the complete request; arbitrary prose
is not ignored to extract a convenient clause. See [named corrections and
custom-field identity](MEMORY_LANGUAGE.md#named-corrections-across-a-complete-message).

Supporting clauses do not bypass review for a plain declaration that conflicts
with an existing value. Historical descriptions do not select current values,
and an ambiguous reference among compatible named settings requires
clarification. A status statement such as `My saved export format is wrong.`
targets the existing setting rather than creating a field for the phrase.
Custom values that resemble request qualifiers can also require clarification
in a composed message. Established request-scope markers still have their
documented grammatical meaning; not every suffix is guaranteed literal value
text. The wording guide explains this custom-value boundary.

## Optional interpretation for unfamiliar wording

Pass `memory_interpreter=MemoryInterpreter.from_model(existing_local_model_call,
...)` when connecting to ask your existing model for proposals on eligible
unfamiliar user requests. The helper builds an extraction prompt and validates
bounded results against exact source quotes. It creates no client and downloads
no model; configure the existing synchronous callable and its response decoder.

This fallback is off by default. Supported deterministic wording and already
processed messages make no interpretation call. Only newly appended messages
that pass a conservative English request filter are eligible. Quoted, reported,
hypothetical and other excluded content stays excluded; this does not make
arbitrary text understandable.

**Every interpreted proposal waits for confirmation, even in automatic mode.**
A proposed change marks an affected old value disputed. The proposal can be
wrong despite its exact quote. Model work runs outside SQLite transactions;
failed or malformed results leave the batch and cursor unchanged, with no
automatic retry. A local model avoids a paid API, while any remote callable keeps
its normal costs. See [optional interpretation](MEMORY_INTERPRETATION.md) for
configuration, validation, concurrency and async cancellation.

## Conservative boundaries

Only original `user` messages are eligible. System/developer messages,
assistant output, native tool results, thinking and tool-call arguments are not
extracted. Quotes, code markers, blockquotes, unsupported negatives, hypotheticals
and reported speech are excluded conservatively. Compound clauses are supported
only when every clause is recognized; no partial deterministic update is applied.
An eligible unclear request naming known settings may instead create
clarification-only records. See [supported wording](MEMORY_LANGUAGE.md).

**Deterministic extraction requires every non-courtesy fragment in a message to
be a supported statement or a supported setting-linked explanation.** Exact
courtesy forms such as `Thanks!` and `Thank you.` can accompany a supported
statement. This prevents a question such
as `Can you validate this statement?` followed by `The export format is CSV.`
from silently becoming the user's own preference. It also means a genuine
statement followed by `Thanks for helping` is currently skipped. The processing
report exposes `mixed_or_unsupported_context`; there is no claim that skipped
messages contain nothing useful.

Obvious secret subjects such as `password`, `API key`, `private key`, `access
token` and `credentials` are excluded. This is a narrow lexical check, **not a
sensitive-data detector**. Other personal or confidential user content may still
be saved locally. Enable this feature only for conversations your application
intends to remember.

These boundaries reduce mistakes; they do not prove a sentence is a true,
current, first-person assertion. Two statements that both fit the grammar may
still need human interpretation. The original user text remains the source.

## Inspect, review and correct memory

```python
snapshot = agent.memory.snapshot()   # accepted + unresolved recall with audit metadata
model_view = agent.memory.context_snapshot()  # smaller view used in prepared requests
records = agent.memory.entries()     # accepted records with full source text
pending = agent.memory.pending()     # proposals with full source text and basis

if pending:
    proposal = pending[0]  # Show this record for review.
    if proposal["value"] is None:
        print("Clarification needed:", proposal["source"]["quote"])
        # Append the user's actual clarified instruction to the conversation,
        # or explicitly dismiss a mistaken flag. Do not confirm a null record.
        # agent.memory.dismiss(proposal["id"])
    else:
        print("Proposed value:", proposal["value"])
        # Only after the user/operator approves this concrete proposal:
        # event = agent.memory.confirm(proposal["id"])

# Explicit operator actions:
# agent.memory.dismiss(proposal_id)
# agent.memory.forget("export_format")
# agent.memory.undo(event_id)
```

For a replacement with multiple possible targets, confirmation must name one
captured key: `confirm(proposal_id, key="export_format")`. A replacement with no
known target cannot be confirmed by inventing a key; dismiss it and append a
new statement naming the subject. Confirmation refuses a proposal whose
captured target value changed in the meantime.

A pending conflict marks the old active entry as `disputed: true`. Contextual
style proposals use reason `contextual_needs_review` and provenance method
`contextual`; they need confirmation even in automatic mode. The model's
memory data includes the unresolved proposed value, exact quote and reason,
so the previous value is not presented as an undisputed current answer. A later
clear statement explicitly naming the same subject resolves its older direct
proposal automatically. Ambiguity involving several keys remains for review.

Use `MemoryPolicy(mode="review")` if your application wants every recognized
new value to become a proposal first. Approval still creates user-stated recall,
not an authoritative fact or permission.

Clarification-only records have `value=null` and
`reason="unparsed_correction"`; `confirm()` rejects them. They require a real
clarified instruction or explicit dismissal. A concrete reviewed proposal can
resolve only the earlier uncertainty IDs it captured, after verifying their
basis. A repeated request does not let an older approval erase a later unclear
change. Repeating an interpreter suggestion equal to the active value, or
deduplicating it against an older proposal, likewise cannot silently resolve a
new uncertainty flag. See [interpretation safeguards](MEMORY_INTERPRETATION.md#repeated-requests-and-uncertainty).

The first clarification-only record upgrades that session to memory schema 2.
v0.14 and v0.15 read schemas 1 and 2; v0.13 rejects schema 2. Resolution does not
downgrade the session. All workers sharing the database must support its schema.

`forget` removes an active entry and pending proposals targeting it. Replaying
the old transcript cannot recreate it. A genuinely new user statement may add
it again. `undo` reverses a memory event only while all affected items still
match that event; it refuses to overwrite later changes. It never rewinds the
consumed conversation prefix.

Forgetting, dismissing and undoing are **not secure deletion**. Original source
text and previous values remain in the local audit event history. Deliberate
[whole-lineage deletion](HISTORY_MANAGEMENT.md) can remove stored conversation
and audit payloads, but does not securely erase filesystem/WAL/backup copies.
[Scoped sessions](ORGANISATIONS.md) add host-supplied permission checks; core
memory objects alone do not enforce user access. Encryption, authentication and
retention scheduling remain responsibilities of the host application.

## Provenance and storage

Each retained value records:

- The exact source quote and its character offsets in the original message.
- The original message index and `user` role.
- A session-scoped history hash and a hash of the original message.
- The full original user message in local storage and audit events.

`snapshot()` includes source offsets, hashes and record IDs for inspection;
`entries()` and `pending()` also expose the full source messages. Prepared model
requests use `context_snapshot()`: it retains every accepted and unresolved value,
dispute, reason, exact source quote, message index and role, plus an interpreter
marker or source-session ID when applicable, while omitting local
IDs, hashes and offsets. This reduces metadata sent to the model without ranking,
summarising or dropping memories. Hashes establish which captured input a record
came from; they are not authentication or proof of truth.

Memory uses a separate SQLite table and `memory` events in the same session
ledger. A successful batch of memory/proposal changes increments the session
revision once. Merely consuming unrelated or repeated input advances the durable
transcript cursor without invalidating the session's prepared actions. Memory
changes do invalidate actions bound to an older session revision.

## Resuming and history changes

Relay persists a count and hash of the consumed original prefix. Every later
call must include that unchanged prefix, followed by new original messages.
Replaying the same complete history is a no-op even after a restart. Replaying
older declarations never replaces a later correction or restores forgotten data.

Editing a consumed message, pruning the prefix, changing the protocol or using
a different conversation branch raises `MemoryHistoryError`. Start a separate
session for a changed branch. Direct connections rely on your application's full
transcript; optional managed text conversations store it in Relay's local database.
There is no hidden transcript discovery or automatic access to an unrelated chat.
Upgrading the recognition grammar does not reprocess an already consumed prefix.
That includes statements missed by an older version: append a new original
clarification to use the newer grammar rather than editing or replaying history.

Managed text conversations support explicit `rollover(expected_revision=...)`
at a completed turn boundary. It returns a new session ID in the same persistent
database, carrying accepted memory, unresolved proposals and original source
identity forward. Earlier messages remain available through paginated history
reads/search. This starts a new bounded transcript; it does not expand active
memory caps, delete old evidence, or bound the whole database's disk use. Tool
policies must be registered again. See [managed conversations](CONVERSATIONS.md).

Memory processing commits **before** context compilation and the provider call.
If compilation or the provider later fails, the learned user statement and cursor
remain committed. Retrying with the same original history is idempotent. A memory
input/budget/revision failure, by contrast, commits neither values, proposals,
events nor cursor.

## Limits and failures

Default policy:

```python
MemoryPolicy(
    mode="auto",
    max_entries=64,
    max_chars=4000,
    max_message_chars=16000,
    max_new_messages=256,
    max_history_chars=2000000,
)
```

`max_entries` counts accepted and pending entries together. `max_chars` counts
the complete audit snapshot as compact JSON, including source quotes, hashes, dispute
flags and unresolved proposals. It is a character budget, not a token budget;
the character limit may be reached before 64 entries. The smaller model view
also consumes the surrounding context compiler's normal character/token budget.
Reducing model metadata does not enlarge these storage limits.

The remaining caps bound individual new user messages, newly appended original
messages of all roles, and the serialized complete original history. Reaching
a cap raises `MemoryBudgetExceeded` and aborts the entire new batch. Relay does
not silently discard an older memory, truncate an input or consume only the
beginning of an over-limit batch.

When a restarted app lowers its policy limits, `snapshot()` and `entries()`
remain available for inspection. `context_snapshot()` and
`snapshot(for_context=True)` refuse over-limit recall. Explicit `forget` and `dismiss` may reduce the
state one item at a time, even while it remains over the new limit. Adding,
confirming or undoing still requires the resulting state to fit.

All mutation methods accept optional `expected_revision=...` to reject stale
operator actions. SQLite serializes batches across connections; two workers
processing the same original prefix cannot apply it twice.

## Standalone use

The extraction core can be used without a connected model:

```python
from context_relay import AutomaticMemory, MemoryPolicy, RelayStore

with RelayStore("relay.sqlite3") as store:
    session = store.create_session("Support conversation")
    memory = AutomaticMemory(store, session["id"], policy=MemoryPolicy())
    report = memory.process([
        {"role": "user", "content": "The deployment region is eu-west-1."},
    ])
    print(report["changes"])
    print(memory.snapshot())
```

`process(..., protocol="ollama")` also accepts validated native Ollama text and
tool messages. `protocol="openai_chat"` accepts supported OpenAI Chat Completions
text/function histories. Only original user text is extracted in either case.
Other protocols and multimodal messages need their own adapter; they are not
flattened silently.

The current tests establish deterministic extraction and storage behavior.
They do not prove that any model will use recalled text correctly or that
context rot is solved. See [v0.15 correction evidence](../evaluations/CORRECTIONS-v0.15.md)
and [validation scope](VALIDATION.md). The first frozen-parser failures are
preserved; subsequent runs on the exposed corpus are development measurements,
not unseen language-accuracy estimates.
