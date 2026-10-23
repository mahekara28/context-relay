# Let Relay keep the conversation

`agent.conversation()` stores original user messages and complete assistant text
in the agent's local SQLite database. Send only the new text on each turn. Relay
loads the original history, updates optional memory, prepares bounded context,
calls your existing provider, and saves the response's text.

For configurable automatic rollover, optional indexed archive search and
explicit whole-conversation deletion, see [history management](HISTORY_MANAGEMENT.md).

This API supports **plain text chats**. It does not operate an autonomous agent,
execute tools, accumulate streaming chunks, or retain images/reasoning/tool-call
state. Existing `agent(messages=full_history)` calls remain available for those
applications to manage their own supported transcripts.

## Connect once

```python
from context_relay import MemoryPolicy, RelayConfig, connect

# existing_model_call accepts messages=... and returns a complete text response.
with connect(
    existing_model_call,
    config=RelayConfig(db_path="relay.sqlite3"),
    memory=MemoryPolicy(),
) as agent:
    chat = agent.conversation(system="Answer briefly.")
    response = chat.send("Use CSV for export format.", request_id="message-1")
    response = chat.send("Actually, use JSON instead of CSV.", request_id="message-2")
    print(chat.history)
    session_id = chat.session_id  # Keep this ID with the user's conversation.
```

`send()` returns the original provider response, preserving object identity.
It stores only the original user's text and decoded assistant text. The optional
system message is stored once. Model-visible Relay metadata is never written
back into the original transcript.

The default decoder accepts a string or a dictionary such as
`{"message": {"role": "assistant", "content": "Hello"}}`. For other responses,
pass a synchronous `decoder=response_to_plain_text` to `conversation()`. The
decoder must return nonempty text and reject tool calls or other content that
cannot safely be represented as plain assistant text. A provider connection may
supply its own decoder; a decoder passed to `conversation()` takes precedence.
OpenAI connections and recognized Ollama client attachments supply text-only
decoders. With `attach(ollama_client)`, use `attachment.agent.conversation()`.
A generic `connect(ollama_client.chat)` still needs an explicit SDK response
decoder; an HTTP JSON response with the envelope above works directly. No
provider SDK is imported by the conversation module.

An asynchronous connection returns an `AsyncConversation`:

```python
chat = async_agent.conversation()
response = await chat.send("Use JSON for export format.")
```

## Resume without rebuilding history

```python
with connect(
    existing_model_call,
    config=RelayConfig(db_path="relay.sqlite3"),
    memory=MemoryPolicy(),
    session_id=saved_session_id,
) as agent:
    chat = agent.conversation()
    response = chat.send("Continue with the chosen format.")
```

Reuse the database, session ID and original protocol. A resumed conversation
keeps its system message and storage limit. Starting a connection without the
session ID creates a separate conversation. The default in-memory database does
not survive process exit. Each user/conversation needs its own session; sessions
are not authentication or an access-control boundary.

Do not switch between managed and application-owned history in the same session.
A managed conversation cannot be created over an already-consumed external
memory transcript. Direct `agent(...)` calls cannot be tracked by this API.

## Local clarification

With automatic memory enabled, a disputed existing preference raises
`MemoryClarificationRequired` before the next model call. This local question
is saved as the assistant reply and completes the request, so the user can send
a new full instruction. Catch the exception and display its `prompt`. Do not
retry its completed request ID. [Example and scope](INSTRUCTION_CLARIFICATION.md).

## Failures and retries

Relay commits one pending user message **before** calling the provider. If
preparation (other than the local clarification above), the API, decoding, or cancellation fails, that message stays saved.
`chat.pending` exposes its request ID, text, and whether a provider claim remains.
Sending another user message is blocked until the pending request is resolved.

```python
try:
    response = chat.send("Continue", request_id="message-3", model=chosen_model)
except ConnectionError:
    print(chat.pending)
    # Retry only when your application's retry policy decides to do so.
    response = chat.retry(model=chosen_model)
```

Use `await chat.retry(...)` for async calls. Supply provider options again: Relay
does not persist credentials, callables, or arbitrary request keyword objects.
Provider errors propagate unchanged. `ConversationResponseError.response` exposes
an unsupported provider response for application inspection or stream cleanup;
Relay does not consume it. A decoder failure does not cause an automatic retry.

Use a stable `request_id` from your UI/job when request replay is possible. A
completed duplicate raises `ConversationAlreadyCompleted`; its
`assistant_message` contains the committed assistant text, and the provider is
not called again. Reusing the ID with different user text raises `ValueError`.
If an ID is omitted, Relay generates one; it is visible in `pending` after a
failure. Repeating `send()` never implicitly retries a pending request.

The database has a persistent in-flight claim. Another worker cannot normally
run the same pending request. If a process crashes during inference, Relay
cannot tell whether the provider finished. After confirming that worker stopped,
explicitly call `chat.retry(recover_inflight=True)` to replace the stale claim.
This **may repeat an already-run provider call**. There is no exactly-once billing
guarantee across crashes/network failures. Active calls in the same process
remain blocked even with the override. A superseded worker cannot append its
late response.

There is no `discard_pending()` operation: removing a user message after memory
processed it would break the append-only evidence chain. To abandon a pending
conversation, start a new session. Finish active calls before closing the agent.

## Storage and efficiency

The transcript stores one immutable SQLite row per message. Appending does not
rewrite earlier messages; stored character accounting is updated in constant
time. Preparing a model request still reads the bounded original history. No
database transaction is held while inference runs.

`max_history_chars` bounds compact JSON for the full original transcript,
including roles, text, separators and the optional system message. The default
is 2,000,000 characters, or a lower automatic-memory history limit. An explicit
limit cannot exceed the memory policy's history cap. The maximum without memory
is 20,000,000. This storage limit differs from `RelayConfig.max_chars`, which
limits each prepared model request.

If a new user message cannot fit, it is rejected before storage or a model call.
If the returned assistant text cannot fit, `ConversationLimitExceeded` exposes
the original `response`; the user stays pending and no partial assistant text is
saved. Request a shorter reply on an explicit retry or start a new session. Relay
never deletes original evidence automatically to make room. Memory extraction
limits and context limits still apply; managing history does not fix limited
language understanding or guarantee that a model follows remembered information.

## Continue when a completed conversation fills up

Use `rollover()` to start a fresh segment while retaining the old originals.
Rollover requires a persistent database; a fresh connection cannot resume a
session from another connection's `:memory:` database.

```python
revision = agent.guard.store.get_session(chat.session_id)["revision"]
next_session_id = chat.rollover(expected_revision=revision)

# Keep next_session_id with the user's conversation record. Use the same
# persistent database, provider setup, memory settings and protocol to resume.
with connect(
    existing_model_call,
    config=RelayConfig(db_path="relay.sqlite3"),
    memory=MemoryPolicy(),
    session_id=next_session_id,
) as next_agent:
    next_chat = next_agent.conversation()
    response = next_chat.send("Continue with the chosen format.")
```

The new active transcript starts with the exact original system message, if one
exists. Relay copies the bounded accepted memory **and unresolved conflicts**,
including their original source session, message index, quote and audit hashes.
It also carries explicit application-recorded facts, recording their original
event IDs in a `conversation_rollover` event. Recall stays separate from these
authoritative facts. An old memory event can be inspected in its original
session; `undo()` does not reach across sessions.

**Tool permissions are not copied.** Register allowed tools explicitly on the new
agent after reviewing its purpose. The rollover event records how many policies
remain archived. Existing message-index pins must be reviewed and cleared before
rollover because indices restart in the new transcript. Keep provider-specific
system instructions, output checks and any other connection options in your
application's normal connection setup when resuming.

Old messages are never rewritten or deleted. The previous `chat.history` stays
available; `chat.next_session_id` identifies its successor. Further sends through
that old conversation raise `ConversationArchived`, whose `session_id` points to
the next segment. A repeated completed `request_id` from any ancestor is still
rejected before calling the provider. Separate, unrelated sessions keep separate
request identities.

Manual `rollover()` is an explicit operation at a completed turn boundary. The
optional `HistoryPolicy` can trigger rollover before a later send. Pending requests
and in-flight or unresolved crash claims block it. A stale revision, incompatible
memory cursor, too-small system-prefix budget or memory-cap failure rolls the
whole operation back. There is no automatic trimming of memory or loss of pending
conflicts to make it fit. Optional `max_history_chars=...` sets the next segment's
bound; omitted, it keeps the previous bound. Carrying source session IDs adds a
small amount of metadata and can fail if memory was already exactly at its cap.

This keeps each **active transcript bounded**. It does not bound the entire
database: archived originals still consume disk space. Rolling over does not
make unlimited distinct preferences fit within the active memory budget. Use
explicit memory review/forget operations for obsolete entries, or deliberately
adjust limits. `forget()` removes active recall while preserving audit evidence.

## Retrieve older originals without loading the whole archive

```python
page = next_chat.history_page(limit=50)
if page["next_cursor"] is not None:
    more = next_chat.history_page(after=page["next_cursor"], limit=50)

matches = next_chat.search_history("invoice", limit=20, scan_limit=1000)
```

Each result has `messages`, `next_cursor`, and `scanned`. Each message includes
its original `session_id`, `index`, and exact `message` object. Pass a non-`None`
cursor back as `after` to continue; stop when it is `None`. Pages include only
the current segment and its linked ancestors, never unrelated conversations or
later segments. Reading an archived segment does not expose its future turns.

`max_chars` defaults to 100,000 serialized characters for returned message
records. A page ends before overflowing it. If even its first matching message
cannot fit, `ConversationLimitExceeded` lets the caller deliberately choose a
larger bound; original messages are never silently truncated. Both APIs use
keyset pagination and indexed chain/message identity rather than loading all
archived text into Python.

Default search is a literal, case-insensitive scan of message text, including Unicode.
It processes at most `scan_limit` messages plus a one-row lookahead per call;
**follow `next_cursor` even when no matches were found**. This bounds work per
page but is not a full-text index or semantic retrieval system. Search results
are untrusted original evidence; Relay does not automatically inject them as
instructions or turn them into permissions. The application chooses how to use
retrieved evidence within its model's budget.

To build the optional FTS5 archive index and request `mode="terms"`, see
[indexed search and its different matching rules](HISTORY_MANAGEMENT.md#optional-indexed-archive-search).

See [`examples/managed_conversation.py`](../examples/managed_conversation.py) for
an offline integration example using a clearly labelled simulated transport.
