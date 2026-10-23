# History without repeated manual cleanup

`HistoryPolicy` can continue a completed plain-text conversation in a fresh
segment automatically. Old originals remain searchable; current memory and
explicit facts carry forward with their source references. No model summarizes
the archive, and rollover itself makes no provider call.

Carry-forward covers the system prefix, recorded memory and explicit facts.
Other details of an unfinished task can leave the active prompt after rollover.
For workflows that depend on those details, record essential task state or
retrieve the relevant originals before asking the model to continue. Rollover
alone does not understand and preserve every part of an ongoing discussion.

```python
from context_relay import HistoryPolicy, MemoryPolicy, RelayConfig, connect

with connect(
    existing_model_call,
    config=RelayConfig(db_path="relay.sqlite3"),
    memory=MemoryPolicy(),
) as agent:
    chat = agent.conversation(
        system="Answer briefly.",
        max_history_chars=200000,
        history_policy=HistoryPolicy(rollover_after_chars=100000),
    )
    chat.send("Use JSON for export format.")
    chat.send("Continue with the report.")
    save_conversation_id(chat.session_id)  # Save after each send, including failures.
```

The threshold counts compact JSON characters in the active original transcript.
On the **next new send**, Relay checks whether the prior completed transcript has
reached the threshold, or whether the new user message cannot fit. If needed it
archives that segment and connects a successor using the same provider callable,
protocol, memory policy/interpreter, output checks and decoder. Async connections
use `await chat.send(...)` with the same policy. Configure the policy again when
resuming; it is application configuration, not a stored credential or callback.

Leave headroom between the rollover threshold and `max_history_chars` for the
next complete reply. Relay cannot predict the model's response size: an oversized
assistant response still raises `ConversationLimitExceeded`, retains the pending
user, and needs explicit recovery. No rollover occurs mid-response. No automatic
retry or deletion is introduced. A user message too large for a fresh segment
fails without creating an empty successor.

`chat.session_id` and `chat.agent` always describe the active segment. The original
`agent` remains bound to its original session, so previously prepared tool calls
cannot silently acquire a new identity. Use `chat.agent` for new explicit facts
or memory review after rollover. Closing the original agent closes its current
successor connections; `chat.close()` closes only its automatically created
successor. Older successor connections are closed as they are replaced. Do not
close an owner during an active call.

Persistent SQLite storage is required. Pinned message indices prevent automatic
rollover. Registered tool permissions also block it: use explicit `rollover()`
and review/register the new segment's permissions yourself. Permissions are
never copied automatically. A pending or in-flight request cannot roll over.
Repeated completed request IDs are still rejected across the lineage before a
provider call, including when the active segment is ready to roll over.

One live `chat` object serializes sends by rejecting overlap as
`ConversationBusy`. Across workers, a stale connection may receive
`ConversationArchived` with the successor ID and must deliberately resume it.
If opening a successor connection fails after the archive commits, the old
segment's `next_session_id` still identifies the recoverable successor. Persist
the active ID in your host application's conversation record; Relay does not
update an external application database for you.

## Optional indexed archive search

Literal substring search remains available without an index. For larger
archives, explicitly build the optional local SQLite FTS5 index once:

```python
indexed = chat.agent.guard.store.enable_history_index()
mode = "terms" if indexed else "literal"
page = chat.search_history("invoice", mode=mode, limit=20)
while page["next_cursor"] is not None:
    page = chat.search_history("invoice", mode=mode, after=page["next_cursor"])
```

`enable_history_index()` backfills existing messages in a write transaction and
installs transactional triggers for future message inserts and deletions. It is
an explicit maintenance operation: large initial indexes can take time and block
writes. Calling it again is a cheap check. It returns `False` when SQLite lacks
FTS5; disk/locking/schema errors propagate. The index adds disk usage and some
write cost, and persists across restarts.

`mode="terms"` uses the FTS5 index to match **all complete words/numbers**, without
requiring their original order. It does not mean substring or semantic search.
For example, `blue invoice` can match `Invoice with a blue label`, but `voice`
does not match `invoice`. FTS5's default Unicode tokenizer handles case and some
diacritics; its behavior is different from Python's literal `casefold()` scan.
Operators and punctuation supplied by a caller are never evaluated as raw FTS
syntax. Results remain exact, untrusted originals scoped to the active segment
and its ancestors. An archived segment cannot search its future successors.

The application chooses literal fallback explicitly because these modes have
different matching rules. Requesting terms mode without an enabled index raises
an error. Both modes retain row/return-character limits and cursor pagination.
In terms mode, `scanned` counts matched candidate rows read by Relay, not all work
done internally by SQLite. Common words can still match many index entries and
require sorting; neither a result limit nor an index guarantees fixed latency.

See [the reproducible synthetic search measurement](../examples/measure_history_search.py).
It compares a rare-token query on a generated corpus and records SQLite's query
plan. It is not a production workload or a guarantee for all queries.

## Explicit retention and deletion

Archives grow until your application deliberately deletes them. Review the whole
lineage first, then delete it with the same unchanged plan:

```python
plan = chat.deletion_plan()
# Your application applies its retention rules or presents this to its owner.
# plan includes all linked session IDs, creation times and state revisions.
result = chat.delete_history(expected=plan)
```

This is a destructive API; the snippet's second call should happen only when the
application has actually decided to delete that conversation. The example
[managed_history.py](../examples/managed_history.py) demonstrates it only in a
temporary database populated by a simulated provider.

Deletion removes the **entire linked lineage**, including successors if called
from an old segment. It removes stored messages, request IDs, accepted/pending
memory, facts, policies, events, check records and corresponding search-index
entries in one transaction. With indexing enabled, it also rebuilds the index
from surviving text to remove old token postings from its live internal rows.
This can take time and block writes for a large surviving archive. Normal FTS5
deletion alone can retain reconstructable tokens; see the
[SQLite FTS5 documentation](https://sqlite.org/fts5.html#the_secure_delete_configuration_option).
It leaves unrelated conversations intact. Partial
segment pruning is not offered: it would leave surviving memory/audit records
pointing at deleted original evidence.

Any changed revision, transcript length, pending request or claim invalidates the
plan or blocks deletion. Tool execution uses the database write boundary, so a
simultaneous guarded callback must finish before deletion can proceed; deletion
inside an executor callback is rejected. Prepared but unexecuted guards fail on
the now-unknown session. An application-owned external operation that bypasses
Relay's guard cannot be tracked or undone by this library.

**This is logical application-data deletion, not secure physical erasure.**
SQLite free pages, journals/WAL, OS snapshots, backups, provider logs and copies
already exported by an application may retain bytes. The database file may not
shrink. Hosts need separate backup-retention and storage-erasure controls; do not
claim a regulatory erasure guarantee from this method. No payload-bearing
deletion event is retained inside the deleted lineage. The returned counts can
be logged by the host according to its own retention policy.

There is no background age-based deletion scheduler, global disk quota or
distributed retention worker in Relay. An application can use `created_at` in
the preview and its own retention policy to select a completed conversation,
then deliberately call this API. Active-memory limits still apply independently.
