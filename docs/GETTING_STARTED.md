# Add Relay to your Python project

Relay helps your existing agent keep supported user instructions available when
its conversation gets long. For example, after “Use CSV” changes to “Actually,
use JSON,” Relay includes the current JSON preference in later model requests.
Your app and model stay in place.

## 1. Install and check

Use your project's Python 3.10+ environment. In a terminal, clone the repository
and install the current bundled wheel:

```sh
git clone https://github.com/mahekara28/context-relay.git
cd context-relay
python -m pip install --no-index --no-deps dist/context_relay_local-0.23.0a1-py3-none-any.whl
python -m context_relay quickstart
```

The check runs entirely offline: no AI account, SDK, model download or network
call. Relay has no required runtime dependencies. The bundled wheel is the
installer; this release is not published on PyPI.

If you cloned the source repository and do not have the wheel, install from the
folder containing `pyproject.toml` instead:

```sh
python -m pip install .
python -m context_relay quickstart
```

Source installation may download Python build tools. Use the bundled wheel for
an installation that needs no network access.

## 2. Connect your existing client once

```python
from context_relay import auto_connect

with auto_connect(existing_client, conversation="customer-42") as ai:
    response = ai(messages=full_history, model=your_model)
    # Keep this connection open for this conversation's later calls.
```

`existing_client`, `full_history` and `your_model` are values your project already
uses. Relay recognises supported Ollama, OpenAI and Anthropic clients, plus plain
Python functions that accept `messages`. Use the returned `ai` for model calls.
Calls made directly through the original client bypass Relay.

Keep your usual provider options. Anthropic also needs `max_tokens=...` on each
call. OpenAI selects Chat Completions by default. An asynchronous client uses
`async with` and `await ai(...)`. See [supported connectors](AUTO_CONNECT.md) for
other request formats and custom clients.

## 3. Continue the conversation normally

Keep your application's **full original history**, and append each new user
message and assistant reply using your existing response decoder. Pass that full
list to every `ai(messages=...)` call. Do not replace it with Relay's shortened
messages, trim old entries, or edit messages it has already processed.

Use one Relay connection per conversation, not one connection for all users or a
new connection on every message. The `with` block closes Relay's storage when the
conversation ends; it leaves your existing provider client open. If you cannot
use a `with` block, call `ai.close()` after all its requests and actions finish.
The conversation name saves Relay memory locally and restores it after restart.
Use one stable name per conversation and the same private storage location.
Omit the name for temporary memory. [Named storage](NAMED_MEMORY.md) explains
custom directories and deletion.

For a runnable example after installation:

```sh
python examples/simple_connection.py
```

It prints the CSV → JSON correction received by a clearly labelled local stand-in
function. It makes no model calls and does not measure an AI model's accuracy.

## When Relay needs clarification

A detected dispute now raises `MemoryClarificationRequired` before the next
model call. Catch it, display `question.prompt` to the user, and append that
question as assistant text to your original history. Append the user's clear
answer next, for example “Export as JSON”, then call Relay again.
[Copyable example and managed-chat option](INSTRUCTION_CLARIFICATION.md).

## What this setup covers

Basic memory is on without a configuration file. Relay recognises supported
instructions and prepares context before each connected call. It does not
understand every sentence or guarantee that the model follows its memory.
[Supported wording and uncertainty](MEMORY_LANGUAGE.md) explain these limits.

Protecting actions such as file exports is a separate, explicit step: register
your functions and the fields to check together with [agent.protect_tools](ACTION_HARNESS.md).
Installing or connecting Relay alone does not protect every action in a project.

This quickstart uses ordinary text history. Native reasoning/tool formats,
streaming and images need their documented adapters and handling; this example
makes no guarantees for them. Your model still has its usual running costs;
Relay's basic setup adds no AI calls. See [validation](VALIDATION.md) for what has
actually been tested.

For an agent that writes files or changes business data, use the
[protection profile](PROTECTION_PROFILE.md) and
[complete Responses workflow](RESPONSES_WORKFLOW.md). Basic memory recognises
only supported wording. The optional assurance mode deliberately requires an
explicit review of selected fields on every new user turn, then resumes the
same request. It is a separate setup choice, not enabled by the short example
above. Tools still need explicit checks and a trustworthy source.
