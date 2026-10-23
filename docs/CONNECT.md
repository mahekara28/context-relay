> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Install, configure, connect

For the shortest setup, use [`auto_connect(client)`](AUTO_CONNECT.md). It selects
a supported connector and enables basic memory without a configuration file.
The explicit configuration and connection interfaces below remain available.

Connect Relay to the function your Python project already uses to ask its AI for a response. After that, each call through the connection prepares the conversation automatically. You do not need to call `prepare()` yourself every time.

Version 0.9 adds [final output checks](OUTPUT_CHECKS.md), [optional memory interpretation](MEMORY_INTERPRETATION.md), [explicit conversation rollover](CONVERSATIONS.md), and [Anthropic text connections](ANTHROPIC.md). Existing [client attachment](ATTACH.md), [automatic user memory](AUTOMATIC_MEMORY.md), [OpenAI connections](OPENAI.md), and [evidence recovery](RECOVERY.md) remain available. For the shortest setup, start with [the beginner guide](GETTING_STARTED.md).

Think of it as putting an organiser between your app and its AI:

**Your app's conversation → Relay selects context → your existing AI → the usual response.**

Relay runs inside your project. It needs no account, dashboard, separate server, or paid API. Your existing model's costs still apply if you use a paid model; an already installed local model can run without an API bill.

## 1. Install in your project's Python environment

The release includes a wheel in `dist/`. Install that file:

```sh
python -m pip install --no-index --no-deps path/to/context_relay_local-0.11.0-py3-none-any.whl
```

The package is not yet on PyPI. Replace the example path with your downloaded file's path. Installation from this wheel does not need a network connection.

## 2. Create the settings file

From your project directory:

```sh
context-relay init
```

This creates `relay.json`; an existing file is never overwritten. To choose another path, use `context-relay init config/relay.json`.

The defaults are:

```json
{
  "max_chars": 16000,
  "recent_turns": 2,
  "retrieve_turns": 2,
  "pinned": [],
  "db_path": ":memory:",
  "name": "Connected agent",
  "protocol": "plain",
  "memory": null,
  "output": null
}
```

`max_chars` limits the prepared conversation's serialized character count. It is not a model-token limit. `recent_turns` keeps the latest request and recent exchanges. `retrieve_turns` lets simple word matching prioritize up to that many older exchanges; it does not understand meaning or decide which old statement is still true. `pinned` contains original message positions whose whole exchanges must stay. The default database lives in memory and disappears when the connection closes.

The file is read when you connect, so reconnect after editing it. A relative `db_path` in the file is resolved beside that file. Unknown settings or invalid values cause an error instead of being silently ignored.

Required material that cannot fit causes an error before your model is called. Relay does not quietly remove a protected requirement to fit the budget.

To generate a config with automatic memory enabled, use `context-relay init --memory auto`. Add `--protocol ollama` for a native Ollama client attachment. The resulting `memory` object contains validated `MemoryPolicy` settings, including entry, character, and extraction limits. Unknown/duplicate nested keys are rejected too.

## 3. Connect your existing model function once

Suppose your project currently calls:

```python
response = existing_model_call(messages=history, model="your-model")
```

Connect that same function and route requests through the returned object:

```python
from context_relay import connect

with connect(existing_model_call, config="relay.json") as agent:
    # Record requirements when your application receives confirmed decisions.
    agent.remember("network_access", False, source="project-owner")

    response = agent(messages=history, model="your-model")
    # Use response exactly as your project did before.
```

`existing_model_call`, `history`, and `"your-model"` stand for your project's function, full conversation, and chosen model. They are not extra services supplied by Relay. The function must accept `messages` as a keyword argument. Other keyword arguments, such as `model`, continue to your function. Relay returns its response object unchanged.

Calls made directly to `existing_model_call` still bypass Relay. Installing the package alone cannot intercept the rest of your application. Configure and connect the central model function used by your agent loop.

Keep the connection open for that conversation's lifetime. Each later `agent(messages=history, ...)` call automatically selects context using the configured limits and the latest recorded facts. There is no shared "last response" or "last prepared turn" to accidentally associate with a different request.

## No file is required

You can keep configuration in Python:

```python
from context_relay import RelayConfig, connect

settings = RelayConfig(max_chars=8000, recent_turns=2, retrieve_turns=2)

with connect(existing_model_call, config=settings) as agent:
    response = agent(messages=history, model="your-model")
```

`connect(existing_model_call)` uses the defaults. Explicit Python configuration is useful when your project already has its own settings system.

For automatic schema-free recall, pass `memory=MemoryPolicy()` to `connect`, or set `RelayConfig(memory=MemoryPolicy())`. An explicit non-None policy on `connect` overrides the configured policy. To disable it, configure `memory=None` and omit the argument. Memory updates run before each model call; retries of the same original transcript are idempotent. See the memory guide for recognised phrasing and unresolved conflicts.

For eligible unfamiliar wording, pass `memory_interpreter=MemoryInterpreter.from_model(existing_local_model_call, ...)` in Python. Every interpreted proposal requires review; deterministic supported statements never invoke this fallback. The callable and its decoder stay out of the JSON config. See [optional interpretation](MEMORY_INTERPRETATION.md) for the input filter, exact-source checks and model-call costs. This is off by default.

For final complete-text checks, pass `output=OutputPolicy(...)` or a data-only `output` configuration. Formats are `text`, `json` (strict object, optional required keys), `csv` (required exact headers and row widths), or `memory` (an undisputed remembered JSON/CSV choice). Character and optional whitespace-word limits also apply. `max_retries` defaults to 0 and cannot exceed 2; enabling it may make extra provider calls. Checks concern structure and length, not meaning. See [output checks](OUTPUT_CHECKS.md) for decoders, `OutputRejected`, and unsupported streaming/tool responses.

## What your app still supplies

Relay automates preparing messages on every connected call. Your app still chooses how to handle three things:

- **Conversation storage.** Direct `agent(messages=history)` calls need the original history each time, including the current user request and earlier real replies. For plain text chats, `chat = agent.conversation()` stores that history locally; then use `chat.send(new_text, **provider_options)`. Do not switch history modes within one session. Neither mode replaces original messages with the shorter prepared input.
- **Authoritative decisions and permissions.** Use `agent.remember(...)` for the explicit facts used by tool policies. Opt-in automatic memory handles supported user statements separately, without a predefined field schema; it never grants a tool permission. Unsupported or ambiguous language still needs attention.
- **The actual model and tools.** Keep using your existing provider, model SDK, or local runtime. Relay does not install a model or automatically wrap every tool in the project.

For example, when a user changes JSON exports to CSV:

```python
# Your app has already added the user's correction to its complete history.
agent.remember("output_format", "csv", source="confirmed-user-request")
response = agent(messages=history, model="your-model")
```

That current decision is included automatically in later connected requests. This helps keep the input useful; it does not guarantee that the AI's answer will be correct.

## Supported conversations and provider adapters

The default `plain` protocol accepts text messages:

```python
history = [
    {"role": "system", "content": "Help the user build their application."},
    {"role": "user", "content": "The app must work offline."},
    {"role": "assistant", "content": "Understood."},
    {"role": "user", "content": "Design its storage."},
]
```

Each dictionary must have exactly `role` and string `content`. System/developer messages form an optional initial prefix, followed by user-led exchanges and a final user request. Native tool-call/result messages need a supported adapter; images, audio, and unsupported provider structures are rejected before the model call.

For native Ollama text/tool exchanges, set `protocol` to `ollama` in the config. The [Ollama adapter](PROTOCOLS.md) preserves complete calls/results and native fields. For an existing OpenAI client, `connect_openai(client, api="chat", ...)` selects `openai_chat` and preserves complete function exchanges. `api="responses"` maps explicit plain-text history to Responses input; native reasoning and tool item sequences are unsupported. [`connect_anthropic(client, ...)`](ANTHROPIC.md) maps plain-text histories to Messages requests, keeping the initial system prefix separate; native tool/thinking/image/citation formats are unsupported. OpenAI and Anthropic validation uses local stand-ins, with live SDK/API compatibility unverified. The grounded-answer recovery loop uses `plain` and cannot be combined with an enabled output policy on that connection.

If your existing SDK uses another request shape, connect a small function that accepts Relay's plain messages and translates them into your SDK's request. Do not discard tool results or image content just to make an unsupported transcript fit this interface. The adapter must account for that extra content and its budget. Connection APIs do not patch SDK methods; `attach()` explicitly replaces one instance method. None connects automatically to the ChatGPT or Claude websites.

With output checking disabled, the response, including a streaming object, passes through without being consumed. Your app reads or iterates it as usual. Output checking requires complete text; it can retry a rejected format only when explicitly configured. Provider exceptions are not automatically retried by Relay, although your provider SDK may have its own retry settings.

If a decorator hides your function's async behavior, wrap it in an explicit `async def` adapter that awaits the provider. Direct async-generator functions also need an adapter; an async function that returns a provider's streaming object is supported.

## Async projects

Use your existing async function with `async with` and `await`:

```python
from context_relay import connect

async def run_turn(history):
    async with connect(existing_async_model_call) as agent:
        agent.remember("network_access", False, source="project-owner")
        return await agent(messages=history, model="your-model")
```

For a multi-turn conversation, keep that async connection open around the whole loop. The wrapper awaits your model function. Deterministic preparation stays inline; a large history or expensive counter can still occupy the event loop. When optional interpretation is enabled, preparation runs in a worker thread. Cancellation signals that worker to stop before a late memory commit, but cannot forcibly stop a running synchronous model call or undo a completed commit. Set a timeout on that callable and test workload costs before high-concurrency use.

## Inspect one request or guard its actions

Use `invoke()` when you also need the exact prepared turn for the response:

```python
with connect(existing_model_call, config="relay.json") as agent:
    agent.remember("output_format", "csv", source="confirmed-user-request")
    result = agent.invoke(messages=history, model="your-model")

    response = result.response       # The original model response object.
    prepared = result.prepared       # Context captured for THIS call.
    print(prepared.report["omitted_indices"])
    print(prepared.report["size_chars"])
```

With an async connection, use `result = await agent.invoke(...)`. The report shows which original messages were retained or omitted. See [context preparation](CONTEXT.md) for token budgets, evidence recovery, and complete report fields.

For guarded tool execution, register the policy before calling the model, then bind the tool to the `prepared` object returned for that call:

```python
with connect(existing_model_call, config="relay.json") as agent:
    agent.remember("output_format", "csv", source="confirmed-user-request")
    agent.allow_tool("export_data", bindings={"output_format": "output_format"})

    result = agent.invoke(messages=history, model="your-model")
    checked_export = result.prepared.wrap(export_data, tool="export_data")
    # Your tool handler validates the proposal's shape, then calls checked_export.
```

`export_data` is your existing synchronous function. A later recorded correction makes an older action stale; it must be reconsidered using a new model request. Do not execute a tool proposal against another call's prepared turn. The [tool integration guide](INTEGRATION.md) covers supported signatures, `ActionRejected`, and application-managed idempotency. The connection's `guard` property exposes the underlying `ContextGuard` for advanced operations such as `recover_context()`.

## Token limits and saved conversations

For a model-token cap, pass a `TokenBudget` through Python when connecting:

```python
with connect(existing_model_call, config="relay.json", token_budget=budget) as agent:
    response = agent(messages=history, model="your-model")
```

Here `budget` is a `TokenBudget` with the correct counter for your model and message template. Both character and token limits then apply. Counters are Python callables, so they do not belong in the JSON file. See [the token-budget guide](CONTEXT.md#add-a-model-token-budget) before choosing a counter or output reserve.

For facts and policies to survive restarts, set a file path in `db_path` and save `agent.session_id` alongside your application's conversation record:

```python
settings = RelayConfig(db_path="agent-state.db")

with connect(existing_model_call, config=settings) as agent:
    saved_session_id = agent.session_id
    agent.remember("network_access", False, source="project-owner")
    # Persist saved_session_id in your application's conversation record.

# Later, resume only that same conversation with the same database:
with connect(existing_model_call, config=settings, session_id=saved_session_id) as agent:
    response = agent(messages=history, model="your-model")
```

Without `session_id`, a connection creates a new session. Use a separate session for each independent conversation; your app controls ownership and access. Direct calls store state and guard events while your app keeps the full transcript. The optional `agent.conversation()` also stores original text history in that database; reconnect, create the conversation again, and send the next text. See [managed conversations](CONVERSATIONS.md) for request IDs, pending calls, limits and crash recovery. Use a context manager or call `agent.close()` when finished; closing Relay does not close your model client.

At a completed turn boundary, `chat.rollover(expected_revision=revision)` creates a linked segment and returns its new session ID. Reconnect using that ID and the same persistent database, protocol, memory/interpreter and output settings. Bounded memory, unresolved proposals and facts carry forward; tool policies must be registered again. Old originals remain accessible through `history_page()` and `search_history()`. This bounds active history and each read, not total archived disk usage; it does not remove memory caps or delete evidence.

## Run a complete example

From the downloaded source directory:

```sh
python examples/connected_agent.py
```

This runs two connected calls using a clearly labelled test function, with no AI inference or network access. It demonstrates automatic context selection and a recorded correction. It is an integration demonstration, not proof of model accuracy.

The release also contains two actual local calls through this connection (local-only reference; omitted from public distribution). They retain the exact forwarded inputs and model responses; the application explicitly supplied the original requirements and correction.

To make two real calls through an already running local Ollama installation:

```sh
python examples/connected_agent.py --model YOUR_INSTALLED_MODEL
```

The example verifies that the model is already installed locally. It never downloads one, uses no hosted model API, limits each answer to 256 generated tokens, and prints the actual returned text. Model failures and wrong answers remain visible; the script does not replace them with expected responses.
