# Continuing a long Responses workflow

`WorkflowHistoryPolicy` lets an existing `ResponsesWorkflow` start a fresh native
segment when its input becomes large. It makes no extra model call. The original
complete segment stays in the local database, and a deterministic checkpoint
carries exact task requests and references into the next segment.

This is **a fresh-context handoff**, not provider compaction or a guarantee that
the model retains its earlier reasoning. Opaque reasoning stays intact in the
archive; it is deliberately absent from the fresh provider input. Relay reports
that omission instead of claiming lossless reasoning continuity. Provider-native
compaction has a different protocol: its canonical output must be replayed as
documented by the provider. See the [OpenAI compaction guide](https://developers.openai.com/api/docs/guides/compaction).

## Connect once

Use the application's existing synchronous native Responses connection, tool
functions, strict schemas and tool contracts. Those contracts still define which
actions and values Relay can check.

```python
from context_relay import ResponsesWorkflow
from context_relay.workflow_history import WorkflowHistoryPolicy

workflow = ResponsesWorkflow(
    agent,
    tools,
    contracts=contracts,
    schemas=schemas,
    instructions="Use the registered tools for the approved task.",
    history_policy=WorkflowHistoryPolicy(
        rollover_after_chars=12_000,
        max_archive_chars=2_000_000,
        max_segments=128,
        max_task_chars=16_000,
        max_task_updates=128,
    ),
)

result = workflow.send(
    "Compare invoices INV-100 and INV-209; preserve their amounts.",
    request_id="comparison-start",
    model="your-configured-model",
)
followup = workflow.send(
    "Continue.", request_id="comparison-next", model="your-configured-model"
)
```

This works with an already running local compatible model endpoint; Relay itself
does not download or start a model. There is no hosted summarization service.
The existing native single-function-call restrictions still apply, including
explicit refusal of unsupported output formats, streams and parallel actions.

Enable the policy when creating the workflow. On restart, use the same durable
session or named conversation and identical configuration, including the policy,
schemas and contracts. The opt-in changes the configuration fingerprint; it
cannot silently be added to a populated legacy workflow with unknown task state.
Without `history_policy`, the earlier bounded-history behavior is unchanged.

## What a handoff preserves

- The same session, trusted fact store, tool contracts, request IDs and completed
  result ledger. Replaying an existing completed request returns its saved result
  without another provider call or effect.
- Durable preferences, disputes and their original user-message provenance.
  User originals remain separate from native segments. Checkpoints and model
  output never become new memory input, and a checkpoint does not reset the
  lifetime of a temporary preference.
- Exact bounded user requests for one open task, including identifiers and
  constraints. Each request has provenance. Receipt references distinguish an
  action that completed from an entire task that is complete.
- Every archived native item, including reasoning, original function arguments,
  repaired-action output and matching call IDs. Full provider response metadata
  remains in the request's saved result.

The next call receives the checkpoint, the current facts/preferences and the new
user request. It does not receive the archived native items or their action-result
payloads. The checkpoint explicitly lists those omissions and retrieval methods.
It grants no permissions and does not promote historical user wording into a
current trusted fact. If the next step depends on an omitted result, the host
must retrieve it and supply the relevant material explicitly.

An `EvidenceSource`, when configured, is still read for each new request and
checked again before the effect. Archiving an old source reference does not make
that source current or correct.

## Task state is explicit and bounded

Substantive user requests are appended to one open task track. Simple
acknowledgements and bare continuations do not finish or replace it. Neither
"all done" in model output nor a successful tool receipt finishes a larger task.

The host can explicitly consolidate or finish that track:

```python
snapshot = workflow.task_snapshot()
workflow.update_task(
    "Remaining: compare INV-209's tax against source revision 42. "
    "Keep all original invoice amounts unchanged.",
    expected_revision=snapshot["revision"],
)

# Only after the application establishes that the whole task is finished:
snapshot = workflow.task_snapshot()
workflow.finish_task(expected_revision=snapshot["revision"])
```

`update_task(..., replace=False)` appends an explicit host update. These edits
are task data, not new permissions. Revisions prevent stale updates, and pending
work blocks edits. A changed task forces a fresh checkpoint before the next
provider call; previously sent history is archived unchanged. The next substantive
request after `finish_task()` opens a new task ID.

Reaching the task character/update bound raises `WorkflowLimitExceeded` before a
new provider call. It does not guess which instruction can be discarded. Host
consolidation remains a deliberate action, not automatic model summarization.

## Inspect omissions and retrieve originals

```python
status = workflow.history_status()
page = workflow.archive_segments(after=-1, limit=20)
for segment in page["segments"]:
    original = workflow.read_archive(segment["segment_id"], max_chars=2_000_000)
    # original["history"] is the complete native segment.
    # original["sha256"] fingerprints its serialized history.

record = workflow.request("comparison-start")
full_response = record["result"]["response"]
```

`history` contains only the active segment. `history_status()` reports archived
segment/item counts, the input budget, whether a token counter is configured,
and the lack of reasoning continuity after a handoff. `archive_segments` returns
metadata with `next_after` and `has_more`; it does not copy large native histories
into the listing. `read_archive` returns one whole segment or refuses an
undersized bound. Retrieval is a host API; it does not automatically add a tool
or issue another model request.

## Input budgets and storage limits

`rollover_after_chars` is a handoff threshold over the whole serialized native
request, including instructions and tools. The connection's `RelayConfig.max_chars`
remains the hard character check. A configured `TokenBudget` can trigger a handoff
earlier; the fresh request must pass the same token check before dispatch.

For native Responses, the token counter receives `[complete_native_request]`.
It must account for the target model's actual framing, tools and instructions.
Use a local tokenizer/counter suitable for that request shape, with the correct
model window and reserved output budget. A plain chat-message counter is not
automatically a valid native-request counter. Relay does not convert characters
to estimated tokens or promise that opaque/remote input costs are measurable.

If even the current task, facts, tools and new request cannot fit, the workflow
fails before inference; it does not prune them or delete original native items.
Checkpointing also has explicit finite limits:

- `max_archive_chars` and `max_segments` bound retained archived segments.
- `max_history_chars` continues to bound active history plus original user text.
- `MemoryPolicy.max_history_chars` separately bounds the original transcript
  processed by memory. `memory=MemoryPolicy(...)` supplies a custom policy.
- `max_requests` bounds the durable request ledger; completed request IDs are
  never silently evicted and reused. Each request/result has its own size bounds.

The archive limit is not a maximum database-file size: request records, facts,
events, checks, SQLite pages and the original user transcript also take space.
There is no infinite-storage claim or automatic retention purge.

Archive or request capacity exhaustion stops safely. Export the originals if
needed, then explicitly delete a fully terminal workflow or start a separately
identified workflow. Starting a separate workflow does not preserve its prior
request-ID deduplication boundary.

## Interrupted work and deletion

A handoff never crosses an unresolved provider request or a function call without
its result. A persisted pending claim blocks further sends after restart or from
another handle. Unknown effects still require explicit destination reconciliation;
neither checkpointing nor request replay reruns them. State changes during model
generation still invalidate the captured action turn.

`deletion_plan()` includes archive contents in its fingerprint and counts.
`delete_history(expected=plan)` requires a current plan and fully terminal work;
it deletes the workflow's archives along with its session data and request ledger.
Sibling sessions remain independent. This is logical SQLite deletion, not secure
erasure of free pages, WAL files, backups or provider-held copies.

The continuation tests exercise a 30-action restart run, exact native archival,
repair metadata, request replay, permission changes, pending reconciliation,
concurrent handles, input/storage limits and deletion. A genuine OpenAI SDK test
uses intercepted HTTP to check fresh-segment serialization and named restart.
These are local tests, not evidence of an organisational pilot or improved model
reasoning quality.
