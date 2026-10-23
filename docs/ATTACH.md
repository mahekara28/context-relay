# Attach Relay to an existing client

`attach()` adds Relay to the model client you already use. Your existing
`client.chat(...)` calls then prepare context automatically. You do not replace
each model call or call `prepare()` manually.

Installation does not change a running application. A developer still adds one
attachment during conversation setup, then keeps the returned handle until that
conversation ends. Create a separate client and attachment for each conversation.

## Ollama: connect once

Use an already configured Ollama Python client and an already installed model:

```python
from ollama import Client
from context_relay import attach, MemoryPolicy

client = Client(host="http://localhost:11434")
relay = attach(client, memory=MemoryPolicy(mode="auto"))

# Existing call sites continue to use the same client.
history = [{"role": "user", "content": "Set export format to JSON."}]
response = client.chat(model="YOUR_INSTALLED_MODEL", messages=history)
history.append(response.message)
history.append({"role": "user", "content": "Use CSV instead of JSON."})
response = client.chat(model="YOUR_INSTALLED_MODEL", messages=history)

print(relay.memory.snapshot())  # Inspect the local user-stated recall.
relay.close()                   # Restores client.chat; does not close client.
```

The first line after client creation enables the attachment and opt-in memory.
No predefined `export_format` field is needed. The memory recognizer supports
specific direct statements and corrections; it does not understand every piece
of conversation. See [automatic memory](AUTOMATIC_MEMORY.md) for recognized language,
pending changes, provenance, undo, and forgetting.

Omitting `memory` leaves automatic memory off unless enabled by your config.
The model call still gets bounded context preparation. An attachment neither
downloads a model nor changes the client's host, credentials or provider options.

## Configuration and async calls

Known Ollama `Client` and `AsyncClient` instances automatically use the `chat`
method and native Ollama message protocol. An explicit config must specify
`protocol="ollama"`; incompatible settings fail at attachment time.

```python
from context_relay import attach, RelayConfig, MemoryPolicy

settings = RelayConfig(
    protocol="ollama", max_chars=16000, recent_turns=2, retrieve_turns=2,
    db_path="./relay.db",
)
with attach(client, config=settings, memory=MemoryPolicy()) as relay:
    response = client.chat("YOUR_INSTALLED_MODEL", history, stream=True)
    for chunk in response:  # Your application owns the stream.
        print(chunk.message.content, end="")
```

Positional `model`/`messages` and keyword calls both work. Parameters such as
`tools`, `think`, `options`, `format`, and `stream` keep their values. The exact
provider response or stream object is returned without consumption or retries.
Keep the attachment open until outstanding model calls finish. The provider
client and response streams remain yours to close.

```python
from ollama import AsyncClient

client = AsyncClient()
async with attach(client, memory=MemoryPolicy()) as relay:
    response = await client.chat("YOUR_INSTALLED_MODEL", history)
```

Save `relay.session_id` with your conversation to resume it using the same
database and `session_id=...`. Continue supplying the complete original history.
Automatic memory enforces its append-only history contract; a different user,
conversation, edited prefix or branch needs a separate session.

## Other Python clients

Choose the actual method explicitly when a client is not recognized:

```python
relay = attach(my_client, method="complete", memory=MemoryPolicy())
# Or a nested resource:
relay = attach(my_client, method="api.complete", memory=MemoryPolicy())
```

The selected method must have an inspectable signature accepting `messages`.
Its input must match the selected Relay protocol. An explicit path is a hook,
not a new provider protocol adapter: plain text methods work with the default
protocol, and unsupported native fields fail validation. Methods with positional
arguments, positional-only arguments, or a `**kwargs` messages argument are
supported. Sync and declared async methods keep their calling style. Direct
async-generator methods require an adapter through `connect()`.

Only ordinary writable instance methods can be attached. Modules, classes,
properties, custom attribute access, and objects without an instance dictionary
are rejected before Relay creates a database. Use `connect()` with a small
explicit adapter for those objects.

## Lifecycle and exact scope

- `relay.agent` exposes the connected agent for explicit facts, guards, and
  advanced calls. `relay.memory` is its optional automatic-memory controller.
- `relay.attached` reports whether the instance still uses Relay's wrapper.
- `relay.detach()` restores the previous instance attribute or inherited method
  lookup and returns `True` when it did so. `relay.close()` also detaches.
- A later application replacement of that method is preserved on detach. Close
  the old attachment before attaching again.
- Only one active attachment can own a given instance method. No class or module
  global is patched. Other clients continue unchanged.
- A bound method saved **before** attaching still calls the original method.
  Attach before registering callbacks or handing the client to an agent. A
  previously captured Relay wrapper raises after its attachment closes.
- A shared client still means shared Relay state. Relay cannot infer conversation
  identity from an arbitrary provider call. Use one client per conversation.
- Automatic memory reads the history you supply. The attachment does not record
  assistant responses, maintain your history list, or execute tools for you.

## Message compatibility and evidence

For detected Ollama clients, `messages` may be a list or tuple of dictionaries
and SDK `ollama.Message` objects. Relay normalizes only the actual loaded SDK
message class using `model_dump(mode="json", exclude_none=True)`; unknown object
conversion methods are never invoked. All resulting fields are validated by the
native protocol. Supported text, thinking, calls and tool results are preserved.
Images and unsupported fields fail clearly rather than being silently discarded.
The supplied history is not modified.

This boundary was checked against the current official
[Ollama Python client source](https://github.com/ollama/ollama-python/blob/main/ollama/_client.py)
and [official SDK examples](https://github.com/ollama/ollama-python), accessed
2026-09-29. Unit tests use clearly identified stand-ins with realistic signatures,
sync/async behavior, native messages and streams. The bundled Python environment
did not have the optional Ollama SDK installed during this implementation;
those tests are not proof of a live SDK integration. A no-dependency example is
available at `examples/attached_agent.py`; its default transport is explicitly
simulated. This feature reduces connection work; it does not prove better model
accuracy or eliminate context rot.
