# Easier setup with auto_connect

**v0.18 behavior change:** disputed existing memory pauses the next text model
call. Catch `MemoryClarificationRequired` and display its prompt.
[Clarification and upgrade guide](INSTRUCTION_CLARIFICATION.md).

Pass the AI client your Python project already uses. In the default text mode,
Relay chooses the supported connector and enables its basic automatic memory:

```python
from context_relay import auto_connect

with auto_connect(client) as ai:
    response = ai(messages=full_history, model=your_model)
    # Keep this connection open for the conversation's later requests too.
```

`client`, `full_history` and `your_model` are your application's existing values.
The example fits Ollama and OpenAI Chat requests. Anthropic also needs your
`max_tokens` option on each call. Model choices and other provider settings stay
with your application. `await ai(...)` works with an asynchronous client.

There is no required configuration file, provider selection, MemoryPolicy import,
account setup, or model-discovery request. This setup enables deterministic
memory only. Extra AI interpretation calls and output-repair retries stay off.
The shortcut changes setup, not how accurately Relay understands language.

## What happens automatically

Relay recognises existing instances of these already loaded Python SDK classes:

| Client | Selected connector |
| --- | --- |
| Ollama `Client` / `AsyncClient` | Ollama text and tool-message dictionaries |
| OpenAI `OpenAI` / `AsyncOpenAI` | Chat Completions text and function-tool histories |
| Anthropic `Anthropic` / `AsyncAnthropic` | Plain-text Messages adapter |
| Ordinary Python function accepting `messages` | Plain-text function connection |

Detection checks loaded SDK class identities, including subclasses. A class name
or a similarly named method alone is insufficient. Relay does not import an SDK,
read credentials, call a model, or modify your client during setup. The existing
SDK still uses its own credentials and normal provider charges.

OpenAI's [official SDK documentation](https://developers.openai.com/api/docs/libraries)
shows the client supplied by the host application. Installed-SDK checks exercise
detection, serialization and response objects with local mock HTTP transports.
They do not verify hosted-service acceptance or every SDK version. See
[the exact validation scope](VALIDATION.md).

Requests through `ai` use Relay. Calls made directly through the original client
continue independently. If an existing Ollama project must keep its original
`client.chat(...)` calls, [attach](ATTACH.md) provides that separate option.

## Preserve provider-native agent history

Choose native mode explicitly when the application already uses OpenAI Responses
items or Anthropic tool/thinking/compaction blocks:

```python
from context_relay import RelayConfig, auto_connect

with auto_connect(openai_client, api="responses", native=True,
                  config=RelayConfig(max_chars=200000)) as ai:
    response = ai(model=your_model, input=complete_native_history)
```

For Anthropic use `auto_connect(anthropic_client, native=True)`, pass
`messages=complete_native_history` and `max_tokens=...` per request. Add
`beta=True` to select `client.beta.messages.create`; beta headers, model choices
and context-management options remain explicit application settings. OpenAI
native mode requires `api="responses"`; `native=True` is unsupported for Chat
Completions, Ollama and plain functions. `beta=True` requires native Anthropic.

Native mode preserves the complete supported JSON request, including genuine
SDK input objects after serialization. It never selects turns or rewrites earlier
input. Its default inserts no memory. The application stores and replays every complete
output item, supplies matching tool results, and owns any provider compaction.
Unknown item families and incomplete visible tool exchanges fail before dispatch.
Opaque state is retained, not decrypted or authenticated.

The native default has automatic memory off. Explicit `memory=True`, a
`MemoryPolicy`, an interpreter, output rewriting and selection settings are
rejected. A memory policy in a shared config is also rejected unless you
explicitly disable it with `memory=False`. Use `remember()` and
`memory_snapshot()` for explicit placement, or opt into `native_memory=True`
to place trusted `remember()` facts automatically. These facts are separate
from extracted user preferences: the host must accept them as authoritative.
Do not copy untrusted documents/tool output or pending model suggestions into
that channel. Without the opt-in, `remember()` alone sends nothing.

OpenAI projects into `instructions` while keeping `input` exact; pass your
original application instructions each time. Claude appends an inline system
message after a complete user/tool-result turn on models supporting that
format. For Claude, replay `result.prepared.request["messages"]` plus complete
provider output and new input. A changed or omitted prior prefix fails before
dispatch. Never refresh a snapshot inside earlier signed history. After provider
compaction, use a fresh native connection with the same recorded session and the
canonical returned history. This reduces manual placement; it does not take over
native history, authenticate signatures, or guarantee model instruction following.

`ai(...)`/`invoke()` return the original provider result; `invoke_result()` adds
the exact request snapshot. `prepare()` validates without a provider call.
The character cap covers the full visible body, including tools and instructions;
exceeding it stops the request. A native `TokenBudget` counter receives
`[full_request_body]`. It must implement provider-specific counting and cannot
infer decoded opaque-state tokens from ciphertext. See [OpenAI](OPENAI.md) and
[Anthropic](ANTHROPIC.md) for the supported families and ownership rules.

## Send only new text

For a simple chat in the default text mode, Relay can maintain the conversation:

```python
with auto_connect(client) as ai:
    chat = ai.conversation()
    response = chat.send("Export as CSV.", model=your_model)
    response = chat.send("Export as JSON.", model=your_model)
```

Add `max_tokens=...` to both calls for Anthropic. This mode needs complete text
replies; tools, streaming, images and reasoning blocks need application-managed
history. Otherwise, direct calls require the **full original append-only history**
each time, not just the latest message or an already shortened history.

Create one connection per independent conversation. Two connections can reuse
the same unmodified SDK client while retaining separate Relay sessions. Closing
Relay closes its own storage and leaves the SDK client open. Use `async with`
and `await chat.send(...)` for asynchronous managed conversations.

## Optional choices

- **OpenAI Responses text mode:** `auto_connect(client, api="responses")`. The caller still
  supplies `messages`; the existing adapter maps plain text to `input`. This
  explicitly selects a different endpoint. Use `native=True` for supported native
  item histories. No endpoint is probed or silently substituted.
- **Compatible custom client:** `auto_connect(client, provider="openai")`,
  `provider="anthropic"`, or `provider="ollama"`. The client must implement the
  selected supported format. Unknown clients produce a clear error otherwise.
- **Custom callable object or bound method:**
  `auto_connect(model_call, provider="plain")`. This explicitly chooses plain
  message dictionaries; it does not invent an adapter for an unknown SDK.
- **Disable memory:** `auto_connect(client, memory=False)`, including when memory
  was enabled by your config. A `MemoryPolicy` value supplies a custom policy.
- **Configure limits/storage:** pass `config=RelayConfig(...)` or a JSON file path.
  With `memory=True` (the text-mode default), a configured memory policy is preserved; if
  none exists, Relay enables its default policy. The older `connect` and `attach`
  memory defaults have not changed.

Memory stays in RAM unless you configure a database file. To resume after a
restart, save `ai.session_id` and pass it with the same database when reconnecting.
See [conversation persistence](CONVERSATIONS.md). Relay does not guess which user
or conversation a request belongs to.

All existing budget, memory-language, review and provider-format limits remain.
Ollama requests here use lists of dictionaries; SDK `Message` objects/tuples are
accepted only by the separate attachment interface. Unknown formats are rejected
instead of discarding fields. See [supported wording](MEMORY_LANGUAGE.md) and [optional interpretation](MEMORY_INTERPRETATION.md)
for language limits; [the beginner guide](GETTING_STARTED.md) gives the shortest setup.

Everyday wording remains limited: the unseen v0.12 check applied only **1/11
valid clear corrections** (1/12 including one malformed fixture). In text mode,
an optional `memory_interpreter=` can use an
existing model to propose eligible unfamiliar changes; it is off by default and
every proposal needs confirmation. The [local configuration and runnable six-call
example](MEMORY_INTERPRETATION.md) include structured output and preserve failed
results. This adds inference time and does not make arbitrary language automatic.

For a runnable demonstration with no model, SDK or network, install the wheel
and run `python examples/automatic_connection.py`. Its transport is explicitly a
fixed local function, not AI inference.
