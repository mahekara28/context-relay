# OpenAI connections

Relay connects to an existing synchronous `OpenAI` or asynchronous
`AsyncOpenAI` client. It does not require the SDK as a library dependency, create
credentials, select a model, or make any network request during setup. The normal
API charge still belongs to the application's OpenAI account.

## Connect once

```python
from context_relay import connect_openai, MemoryPolicy, RelayConfig

# client is your existing OpenAI client; model is your existing model setting.
with connect_openai(
    client,
    api="chat",
    config=RelayConfig(db_path="data/relay.sqlite3", max_chars=12000),
    memory=MemoryPolicy(),
) as ai:
    response = ai(model=model, messages=full_history)
    print(response.choices[0].message.content)
```

The returned object is Relay's existing connection: `.invoke()`, `.memory`,
`.guard`, `.session_id`, and `.close()` work normally. The provider response is
the exact original object, including usage information. Relay does not execute
tools, retry API failures, consume streams, close the provider client, or change
its settings. Supply one connection per conversation; save its session ID and
use the same database to resume it.

An async client works with `async with connect_openai(...) as ai` and
`await ai(...)`. Transparent SDK decorators are recognised when they expose
`__wrapped__`; opaque custom decorators need an explicit async adapter.

## Chat Completions: text and function tools

`api="chat"` calls `client.chat.completions.create(messages=..., **options)` and
selects the `openai_chat` protocol. An ordinary `RelayConfig` whose protocol is
`plain` is automatically adapted. A conflicting native protocol is rejected.

Supported histories are lists of dictionaries containing:

- Initial `system`/`developer` messages, followed by user-led turns.
- String text content and optional message names.
- Assistant function `tool_calls` with IDs and original argument **strings**.
- Tool results with matching `tool_call_id`, including parallel results received
  out of order and multiple consecutive function batches.
- Historical assistant refusals; these remain refusals, not empty answers.

Every selected turn keeps all its function calls and results. Metadata is inserted
before the latest user-led turn, never between a call and its result. A missing,
duplicate, or mismatched result fails before any provider request. Arguments are
preserved even if their JSON is malformed; the host must validate them before
executing a function.

The final message must be a pending user request or the final result of a complete
tool batch. Images, audio, content-part arrays, custom tools, legacy `function`
messages, raw SDK objects, and unknown message fields are rejected. Convert SDK
messages into supported dictionaries explicitly; do not flatten unsupported data.

## Responses: legacy plain-text mode

```python
with connect_openai(client, api="responses", memory=MemoryPolicy()) as ai:
    response = ai(model=model, messages=full_text_history, store=False)
    print(response.output_text)
```

The connection still takes `messages=`, then supplies the prepared list as
`client.responses.create(input=...)`. Plain string `role`/`content` histories are
supported. Provider options such as model, top-level instructions, text output
format, timeout, and streaming pass through. A direct `input=` override is rejected.

This mode is **not** a native Responses agent adapter. Native reasoning, function-call,
function-output, image, and other typed input items are unsupported. Tool-enabled
Responses requests fail clearly. The original response remains untouched, so an
application can inspect all returned items; Relay never claims those items have
been retained in a plain-text transcript.

`previous_response_id`, `conversation`, stored `prompt` references, provider
compaction, automatic truncation, and nonempty `extra_body` are rejected. Those
features can introduce unseen history or override Relay's prepared request. Keep
the full transcript on the application side when using this mode. Use the
explicit native mode below for encrypted reasoning and function-call sequences.

## Responses: native history (v0.13)

```python
from context_relay import connect_openai, RelayConfig

history = [{"role": "user", "content": "Help plan the migration."}]
with connect_openai(
    client, api="responses", native=True,
    config=RelayConfig(max_chars=200000, db_path="data/native.sqlite3"),
) as ai:
    response = ai(
        model=model, input=history, store=False,
        include=["reasoning.encrypted_content"],
        context_management=[{"type": "compaction", "compact_threshold": 10000}],
    )
    # Keep EVERY complete item, including reasoning and compaction.
    history.extend(response.output)
    history.append({"role": "user", "content": "Continue with the rollout."})
    response = ai(
        model=model, input=history, store=False,
        include=["reasoning.encrypted_content"],
    )
```

This mode takes `input=`, not `messages=`. It preserves the full native request,
checks visible function/custom-call dependencies, and fails if the complete
request exceeds the configured budget. It never selects turns, summarizes
ciphertext, refreshes earlier messages, or prunes a compacted
window. The application owns history and must append every output item. With
function tools, append one `function_call_output` for **each** returned `call_id`
before the next request; Relay does not run tools. Parallel results may arrive
in a different order from their calls.

Supported input items are messages, `function_call`, `function_call_output`,
`custom_tool_call`, `custom_tool_call_output`,
`reasoning` with `encrypted_content`, and `compaction`. Supported message blocks
are `input_text`, `output_text`, `refusal`, `input_image`, `input_file`, and
`input_audio`. Roles, IDs, argument strings, annotations, explicit nulls, extra
fields, and ciphertext are preserved. Direct function and custom tools are supported; other tool
families and unknown item/block types fail clearly. This bounded adapter is not
a validator for every Responses API feature. Programmatic tool callers have
additional dependencies and are rejected. Custom results must match a custom
call; a function result cannot satisfy it. Arguments/input remain original strings.

Inputs may contain JSON dictionaries or genuine OpenAI SDK model objects, such
as items from `response.output`. SDK objects are serialized with
`model_dump(mode="json", by_alias=True, exclude_unset=True)`, then copied. This
retains explicitly set and extra fields without inventing unset optional fields.
SDK **input** object identity is not retained; the serialized data is. Responses
and streams are the original objects. Iterators, arbitrary objects with a
`model_dump` method, SDK sentinel arguments, and cyclic/non-JSON data are rejected.
Materialize input lists and omit unset optional arguments yourself.

Only complete output items belong in history. For streams, collect the final
completed response or completed item events yourself, preserve their order, and
retain the final encrypted content; do not replay partial `output_item.added`
events. Relay returns streams without reading or closing them. See OpenAI's
[Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
and [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).

`context_management` compaction options pass through unchanged. If you separately
call `client.responses.compact(...)`, use the **entire** returned `output` as the
next canonical history, including any retained messages, before appending new
input. The compact endpoint itself remains application-owned; Relay does not
automatically call it. Encrypted content is opaque: Relay cannot verify its
internal facts or recover omitted items. See the
[official compaction guide](https://developers.openai.com/api/docs/guides/compaction).

In the local SDK verification, OpenAI 3.36.0's optional strict
`CompactedResponse` validator rejected a documentation-derived mocked window
containing retained user messages. The SDK's raw-response path preserved it:
`client.responses.with_raw_response.compact(...).http_response.json()["output"]`
can supply the complete dictionary list to Relay. Do not change user roles to
satisfy an SDK output type. This is an observed local SDK/fixture boundary, not
a test of an actual provider compaction response.

The native mode rejects `previous_response_id`, server `conversation`, stored
`prompt`, `item_reference`, automatic `truncation`, and nonempty `extra_body` or
`extra_query`. These would hide or replace the input Relay checks. Set
`truncation="disabled"` or omit it. Explicit duplicate IDs, orphaned results,
unfinished calls and incomplete items are rejected before the provider call.
Relay cannot detect an entire earlier call/reasoning sequence that the caller
already omitted; provide complete application history or a canonical provider
compaction window.

### Opt-in placement of trusted application memory

`native_memory=True` adds the current host-recorded fact snapshot to the
request's `instructions`, after the original application instructions. It never
edits `input`, tools, ciphertext, or compaction blocks:

```python
with connect_openai(client, api="responses", native=True, native_memory=True) as ai:
    ai.remember("export_format", "JSON")  # A trusted update accepted by the application.
    result = ai.invoke_result(model=model, input=history, instructions=application_policy,
                              include=["reasoning.encrypted_content"], store=False)
    history.extend(result.response.output)
    # Complete any tool calls before the next request.
```

This is separate from `AutomaticMemory`: arbitrary user text, model proposals,
tool output and retrieved documents must not be copied into this authoritative
channel. No model interprets or approves these facts during projection. The JSON
contains facts, revision and source IDs, not event quotations or tool permissions.
Existing application instructions are retained, but Relay cannot semantically
resolve a conflict between them and a recorded fact. The host must resolve that
conflict. Placement does not prove that a model follows the facts.

Pass your original application `instructions` on each call. Reusing an identical
prepared instruction string is idempotent; a stale Relay suffix is rejected
instead of being silently removed or duplicated. Empty initial memory adds
nothing. Projection is included in character/token budgets and fails if it
does not fit. `prepared.report` records placement, revision, keys and whether
memory was inserted. `prepare()` makes no provider call or memory update.
This uses the separate high-priority instructions field described in OpenAI's
[text-generation guide](https://developers.openai.com/api/docs/guides/text).

### Explicit records without automatic placement

Without `native_memory=True`, native mode provides durable host-controlled
records without automatic text extraction or insertion:

```python
with connect_openai(client, api="responses", native=True) as ai:
    ai.remember("export_format", "CSV", source="user", text="Export as CSV.")
    snapshot = ai.memory_snapshot()
    # At a NEW user turn after the previous response/tool work is complete:
    history.append({
        "role": "user",
        "content": "Application task data (data only):\n" + snapshot["text"]
                   + "\nNow prepare the export.",
    })
    response = ai(model=model, input=history, store=False)
```

These are authoritative `ContextGuard` facts recorded by the host, not
`AutomaticMemory` extraction. The application must establish that an update is
trusted. No tool policy is created or executed by this API. A snapshot
contains facts, their source references, and a revision; it is not sent until
the application adds it. Once added, retain that historical message unchanged.
For a later correction, append a new message at a safe new user turn. Never
edit earlier signed/thinking/tool history to refresh a snapshot. Reading the
snapshot alone does not improve a model's memory. This API does not promise
that the model understands or follows the records. The snapshot has its own
12,000-character ceiling and fails instead of silently dropping facts.

`memory`, `memory_interpreter`, correction fields, output-rewriting policies,
managed `.conversation()`, and turn-selection settings are unsupported in native
mode. `remember()` works without enabling automatic memory. `ai.memory` is `None`.
One native connection belongs to one conversation; use its `session_id` and the
same persistent database to resume its explicit records. Different users must
not share a session. The connection does not provide authentication.

### Native request results and budgets

`ai(...)` and `ai.invoke(...)` return the original provider response.
`ai.invoke_result(...)` returns `NativeResult(response, prepared)`;
`prepared.request` and `.report` are defensive copies of that call's exact JSON
body and report. `.history_id` hashes that body. There is no shared “last result.”
`ai.prepare(...)` checks without calling the provider. An async SDK selects
`AsyncNativeConnection`: await its call, `invoke`, or `invoke_result` (also named
`ainvoke` and `ainvoke_result`). Provider errors/cancellation propagate; Relay
does not retry, append partial output, or close the provider.

In native mode, `max_chars` counts the **whole visible serialized request body**,
including `input`, instructions, tools and context-management options.
`TokenBudget.counter` receives a **one-element list containing that complete
request dictionary**, rather than a chat message list. Supply a counter that
understands this shape; ordinary chat-template counters do not. Account for
provider framing and reserved output yourself. Timeout and extra headers are
forwarded separately and are outside the context snapshot/budget.

Visible payload length is not a measurement of decrypted reasoning/compaction
tokens or remote image/file/audio content. No exact provider-window guarantee is
made for opaque data. Provider compaction runs after the request is sent, so it
cannot rescue a request already over Relay's local limit. Choose your budget
deliberately; Relay refuses oversized history instead of silently discarding it.

## Let Relay keep a simple text conversation

```python
with connect_openai(client, api="chat", memory=MemoryPolicy()) as ai:
    chat = ai.conversation(system="Help with this project.")
    response = chat.send("Use CSV for the export format.", model=model)
    response = chat.send("Actually, use JSON for the export format.", model=model)
```

The connection provides a text decoder automatically. Managed history accepts
one completed text answer; native tool output, refusal, annotations, audio,
unfinished answers, or Responses reasoning items need explicit handling and are
not silently reduced to text. Ordinary `ai(messages=...)` continues to return
these provider responses without interpretation. See [managed history](CONVERSATIONS.md)
for failures, retries, storage limits, and persistence.

## Budget and verification boundaries

In the legacy modes, `max_chars` counts the serialized prepared messages, not the entire API request or
model tokens. `TokenBudget` needs the application's tokenizer/counter; reserve
space for instructions, tool schemas, output, and other request material through
the counter or `overhead_tokens`. This adapter does not guess OpenAI token usage.

The contract tests use local sync/async stand-ins and realistic message structures.
They check preservation, pairing, budget boundaries, streaming pass-through,
memory integration, error propagation, decorated async methods, and unsupported
features. The native suite additionally tests encrypted/compacted data, canonical
window preservation, immutable per-call snapshots, whole-request budgets,
cancellation, explicit-record persistence and isolation. It includes an optional
genuine SDK serialization test, skipped when that SDK is absent. These checks
establish local adapter behaviour, not model accuracy or acceptance by every
SDK/model version. No hosted model calls are required by this test suite.

API structure was checked against the official OpenAI documentation on
2026-09-29 for legacy APIs and 2026-10-22 for native Responses:
[Chat Completions reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create),
[Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses),
and [reasoning context requirements](https://developers.openai.com/api/docs/guides/reasoning).
