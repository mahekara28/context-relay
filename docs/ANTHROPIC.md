# Anthropic connections

Relay v0.13 wraps an existing `Anthropic` or `AsyncAnthropic` client's Messages
method. It adds no SDK dependency, chooses no model, and makes
no network request during setup. The application owns its client and credentials;
real Anthropic requests still have the provider's usual cost.

Choose the mode explicitly:

| Mode | Input and behaviour |
| --- | --- |
| Default text mode | Plain text history; Relay selects context and can manage recall, text conversations, and output checks. |
| `native=True` | Complete native history, including paired client tools and opaque thinking/compaction blocks; Relay validates and budgets it without editing it. The app owns history. |
| `native=True, beta=True` | The same preservation behaviour through `client.beta.messages.create`, including caller-configured context management and compaction. |
| `native=True, native_memory=True` | Adds trusted host facts in append-only inline system messages; requires a model that accepts this format and exact replay of prepared messages. |

## Native tools, thinking and provider compaction

```python
from context_relay import RelayConfig, connect_anthropic

# client, model, stable_system, tools and history come from the application.
with connect_anthropic(
    client, native=True, beta=True,
    config=RelayConfig(max_chars=200_000),
) as ai:
    result = ai.invoke_result(
        model=model,
        max_tokens=4096,
        system=stable_system,
        tools=tools,
        messages=history,
        betas=["context-management-2025-06-27"],
        context_management={"edits": [{"type": "clear_tool_uses_20250919"}]},
    )
    response = result.response       # Original SDK object; usage metadata retained.
    sent = result.prepared.request  # Defensive snapshot of the complete sent JSON body.
    print(result.prepared.report)

    # Persist/replay exactly what the application sent and the native output it got.
    # A tool_use reply also requires all matching tool_result blocks before the next call.
    history.append({"role": "assistant", "content": response.content})
```

This example makes a normal provider request when used with a real client. Relay
does not enable a beta, model, compaction threshold, or extra request by itself.
For standard Messages use `native=True` without `beta=True`.

Native `.invoke(...)` and `ai(...)` return the exact provider response.
`.invoke_result(...)` additionally returns that request's snapshot, and
`.prepare(...)` validates/budgets without making a request. With an async client,
await `.invoke(...)`, `.invoke_result(...)`, or `ai(...)`; `.prepare(...)` remains
synchronous local work. No result or stream is consumed, rewritten, closed, or
automatically appended. Provider exceptions propagate without a Relay retry;
the SDK retains its own configured retry policy.

Supported native content includes:

- String text and native text blocks, preserving their metadata and citations.
- Assistant `tool_use` and user `tool_result` blocks with complete matching IDs.
  Parallel results can arrive in any order, but all results must be in the next
  user message and precede its ordinary text. Tool input dictionaries and text
  results remain unchanged. Relay never runs the tools.
- User or tool-result images (base64, URL, file ID), documents (PDF base64,
  URL/file ID, plain text, or text/image content), and `search_result` blocks.
  Sources and metadata are retained, not fetched, decoded or re-encoded.
- `thinking` and `redacted_thinking`, including exact signatures and opaque data.
- Provider `compaction` blocks, including signatures, encrypted content,
  `tool_changes`, cache markers, and explicit null content from a failed compaction.
- Mid-conversation system messages, including `clear_at`, per-message output
  settings, `tool_addition`, and `tool_removal`. Their positions stay unchanged.

Top-level `system` and `tools` remain unchanged too. Additional JSON fields on
supported blocks and options are retained. Genuine installed Anthropic SDK model
objects are converted using their API field names and only fields actually set;
this avoids adding SDK default fields to opaque blocks. Signatures and explicit
nulls are retained. Arbitrary objects and lazy iterables must be converted by the
application first.

### Opt-in append-only memory placement

Claude's current preserved-thinking checks can bind a thinking block to earlier
messages, top-level instructions, and tools. Replacing that earlier input with
fresh memory can invalidate later blocks. Anthropic specifically advises proxies
to retain the prefix and leave mid-conversation system messages in place.
[Preserved thinking guidance](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking).

Native mode never selects, prunes, summarizes or rewrites earlier items. Its
default still inserts nothing. `native_memory=True` instead appends a new
inline system message after the latest complete user/tool-result turn whenever
the trusted fact snapshot changes. Top-level `system`, `tools` and the entire
supplied history remain intact. Use a model that supports inline system messages;
Relay does not guess model capabilities or enable beta headers.
[Placement rules](https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages).

```python
with connect_anthropic(client, native=True, native_memory=True) as ai:
    ai.remember("export_format", "JSON")  # A trusted application decision.
    first = ai.invoke_result(messages=history, system=stable_system,
                             model=model, max_tokens=1024)
    history = first.prepared.request["messages"]  # Includes inserted memory.
    history.append({"role": "assistant", "content": first.response.content})
    # If the reply uses tools, append all matching results before continuing.
    history.append({"role": "user", "content": "Continue the export."})
    second = ai.invoke_result(messages=history, system=stable_system,
                              model=model, max_tokens=1024)
    history = second.prepared.request["messages"]
```

An unchanged snapshot is not repeatedly appended. A changed snapshot is added
at the tail; prior copies stay exactly where sent. `remember()` accepts trusted
host facts, not automatic user-memory extraction. Do not pass untrusted tool
output, documents or unreviewed model proposals through it. The projected JSON
contains facts, source IDs and revision, not event quotations or permissions.
Resolve conflicts with existing application policy before recording a fact.
Placement cannot ensure model compliance.

The connection checkpoints a hash of sent messages/system/tools before dispatch.
Dropping or rewriting that prefix on a later call raises before the provider is
called. Keep `prepared.request["messages"]` as the next replay base, including
after a streaming call; Relay does not consume the stream. The caller still
collects complete response blocks and supplies tool results. An exact retry with
unchanged memory is allowed. Failures/timeouts retain the checkpoint because
the request may have reached the provider. `prepare()` alone does not checkpoint.

The checkpoint is local to one connection, not a persisted signature validator.
For canonical provider-compacted history or a restored session, create a fresh
connection using the same persisted session or external guard, then supply that
complete history. The application remains responsible for valid compaction
lineage and exact earlier replay. Unknown model/option combinations can still
be rejected by the provider. This mode is SDK/mock-transport tested, not hosted
signature-acceptance tested.

Automatic memory/interpreters, correction reviews, output rewriting, managed
text conversations and selection settings remain unsupported in native mode.
`all_input_preserved` means original items remain intact; `memory_injected`
reports whether this call appended a memory message. Projection is counted
in both request budgets and is never silently dropped to fit.

### Explicit placement without native_memory

Explicit host-recorded facts are still available. `remember()` records trusted
host facts in the guard; it does not create automatic user memory or configure
or execute a tool policy. The application can place a
snapshot into a new ordinary user turn once, persist it, and replay that exact
text thereafter:

```python
with connect_anthropic(client, native=True) as ai:
    ai.remember("export_format", "JSON")
    snapshot = ai.memory_snapshot()
    history.append({
        "role": "user",
        "content": "Application task data; not new system instructions:\n" + snapshot["text"],
    })
    response = ai(messages=history, model=model, max_tokens=1024)
```

Do this at a normal conversation boundary, after completing a pending tool round.
When facts change, append a new update. Do not refresh an earlier snapshot or
rebuild previous system/tools values. `remember()` alone does not send facts to
the model, and native preparation does not certify that a model saw or obeyed them.

### Compaction and native limits

`context_management`, `compaction`, `thinking.block_binding`, beta values, and
returned provider metadata remain under the host's control. Context editing is
performed on the server while the client retains its originals. Relay neither
predicts the effective post-edit context nor claims that every detail survives
the provider's summary. [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing).

For provider compaction, replay returned blocks unchanged. On-demand compaction
and threshold compaction have different placement/replay rules; use the matching
provider workflow. Relay accepts the native block shapes and never performs the
history replacement itself. It does not authenticate signatures or establish
that a host-edited prefix still matches them.
[On-demand compaction](https://platform.claude.com/docs/en/build-with-claude/compaction-on-demand),
[threshold compaction](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold),
[compaction with preserved thinking](https://platform.claude.com/docs/en/build-with-claude/compaction-thinking-blocks).

Audio, server-tool/MCP result blocks, browser state, unsupported media sources,
and unknown block types fail clearly. Tool-result content supports strings,
text, images, documents and search-result blocks. Hidden container/MCP/conversation state and nonempty `extra_body` or
`extra_query` are rejected. This is not support for every Claude SDK feature or
Claude Agent SDK runtime. A provider can still reject a model/option combination.

Native `max_chars` counts **the entire normalized visible request body**, including
messages, tools, system, and context-management settings. Headers/timeouts and
Anthropic's `betas` header selection are transport options outside that snapshot;
their names appear in `report.transport_option_names`. Oversized input fails before dispatch;
Relay never trims native history to fit. Character counts cannot measure encrypted
reasoning's decoded tokens, remote/decoded media or provider-hidden state. An optional `TokenBudget`
counter receives **`[full_request_body]`**, not just the messages; it must understand
that shape and the selected provider's accounting. Reserve output separately.

Native contract tests cover exact replay, paired tools, compaction, preserved
signatures, mid-conversation instructions, bounded requests, defensive copies,
sync/decorated async, and stream ownership. They make no paid/model calls. See
[validation](VALIDATION.md) for the release's installed-SDK checks. Protocol
details above were checked against official Anthropic documentation on 2026-10-22.

## Default text connection

```python
from context_relay import MemoryPolicy, RelayConfig, connect_anthropic

# client and model are the application's existing Anthropic client and setting.
with connect_anthropic(
    client,
    config=RelayConfig(db_path="data/relay.sqlite3"),
    memory=MemoryPolicy(),
) as ai:
    chat = ai.conversation(system="Help with this project.")
    result = chat.send("Export as CSV.", model=model, max_tokens=256)
    result = chat.send("Export as JSON.", model=model, max_tokens=256)
```

For an async client use `async with` and `await chat.send(...)`. SDK decorators
that retain `__wrapped__` are recognised. Opaque custom async decorators need an
explicit async wrapper. The connection exposes the same `.memory`, `.guard`,
`.session_id`, `.invoke()`, and persistence behaviour as other Relay connections.
Save the session ID with the conversation and use the same database to resume it.

### Existing text application history

Supply plain dictionaries with string `content`. Put initial instructions into
the transcript, so Relay sees them before checking its budget:

```python
history = [
    {"role": "system", "content": "Help with this project."},
    {"role": "user", "content": "Export as CSV."},
]
with connect_anthropic(client) as ai:
    result = ai(messages=history, model=model, max_tokens=256)
```

The adapter turns each initial `system` message into a separate top-level
Anthropic text block, preserving text and order. It never joins those instructions
to user content. Relay's memory/coverage data remains a user message. A separate
`system=` call option is rejected because it would bypass preparation. The final
history message must be the next user request; the adapter does not support
assistant prefilling or mid-conversation system instructions.

Ordinary calls return the exact original response, including its usage data.
Streaming is passed through without iteration or closure. Relay never executes
tools, modifies the client, or retries an API exception automatically. The
provider SDK can still apply its own configured retry policy.

### Text-mode boundary

The default remains a **plain-text Messages adapter**.
Native tool calls/results, image/document blocks, thinking blocks, citations in
history, block arrays, developer roles, and extra message fields are rejected.
They are never silently reduced to text. Use `protocol="plain"` (the default).

Request options are limited to model, max_tokens, metadata, stop_sequences,
stream, temperature, top_p, top_k, service_tier, timeout, extra_headers, empty
extra_body/extra_query, and `thinking={"type": "disabled"}`. Availability of a
sampling option depends on the chosen model. Tools, active thinking,
container/server context, MCP, context management, beta features, structured
output configuration, nonempty extra_body/extra_query, and unknown options fail
explicitly. An SDK or model's own defaults can still produce native thinking;
ordinary responses remain inspectable, while managed text history refuses them.

Managed conversations accept only a completed `message` with assistant role,
`stop_reason="end_turn"`, and unannotated text blocks. Text characters are retained
in order. Refusals, truncation, paused responses, tools, thinking, and citation
metadata raise `ConversationResponseError`; the original response is available
on the exception, and the user request stays pending without a second API call.
See [managed conversations](CONVERSATIONS.md) before deciding whether to retry.
Request validation occurs at the provider boundary; automatic memory can already
have recorded the submitted user text when an option is rejected.

### Text-mode budget and verification

`max_chars` measures Relay's serialized prepared messages, including the complete
initial system prefix and all inserted memory data. It does not measure the entire
HTTP request or estimate model tokens. A `TokenBudget` counter sees that same full
prefix and must account for Anthropic's mapping/framing; reserve output tokens
separately. No system text is appended after the budget check.

Text-mode contract tests use local stand-ins and cover sync/decorated async clients, system
mapping, budget limits, persistence, malformed inputs, raw response identity,
streaming, and strict managed decoding. No credentials, paid calls, or live
compatibility test are required by those tests.

Protocol details were checked against official sources on 2026-09-29:
[Messages usage and initial system placement](https://platform.claude.com/docs/en/build-with-claude/working-with-messages),
[stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons),
and the [Python SDK](https://github.com/anthropics/anthropic-sdk-python).
