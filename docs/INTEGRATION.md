> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Add Context Relay to an existing Python project

Context Relay is an importable library. Your application keeps its existing model client, tool functions, and user interface. In v0.3, use `guard.prepare(messages, ...)` before a model call to select bounded context, then `prepared.wrap(...)` or `prepared.wrap_dispatcher(...)` for the resulting tool proposal. See [context preparation](CONTEXT.md) for this combined path. **The Context Relay workbench and web server are optional.**

The examples below cover the lower-level tool wrapper for applications that already build their own model context. They remain supported.

The library keeps explicit facts outside the conversation and compares proposed actions with their latest values. It can stop an action based on an outdated decision even when the model has forgotten the correction. It does not automatically read every conversation, discover every important fact, or make a model immune to context rot.

## Install locally

Requires Python 3.10 or later. From the project directory, install the included wheel into your application's Python environment:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.6.0-py3-none-any.whl
```

Use the full path to the wheel when running that command elsewhere. There are no runtime dependencies or required services. The distribution is named `context-relay-local`; Python imports use `context_relay`. This release has not been published to PyPI.

You can run the example directly from the downloaded source without installing anything:

```sh
python examples/existing_project.py
```

That example uses synthetic proposals and simulated deployment functions. It does not call an AI model or deploy a service.

## Where it fits

```text
Your application records the latest decisions
                       |
             Capture a context turn
                       |
       Your existing model receives that context
                       |
            The model proposes a tool call
                       |
         Context Relay checks the proposal
                    /     \
             allowed       rejected or stale
                |                  |
       Your tool runs       Give the model fresh context
                |           and ask for a new proposal
         Receipt recorded
```

The model proposes the action; your application owns the state, policies, captured turn, and actual tool execution.

## Option 1: wrap an existing function

Suppose this function is already in your application. This documentation uses a simulated body so the example cannot deploy anything:

```python
def deploy(service, environment="staging"):
    return {"simulated": True, "service": service, "environment": environment}
```

In your project, keep your function's real implementation unchanged.

Set up the facts and the permitted tool:

```python
from context_relay import ActionRejected, ContextGuard

guard = ContextGuard("agent-state.db", name="Release assistant")
guard.remember("service", "payments", source="release-config")
guard.remember("environment", "staging", source="release-manager")
guard.remember("paused", False, source="release-manager")

guard.allow_tool(
    "deploy",
    when={"paused": False},
    bindings={"service": "service", "environment": "environment"},
)
```

`when` says which facts must equal particular values. `bindings` maps a tool argument name to the fact it must match. Here the tool may run only when deployment is not paused, and both its arguments must match the current recorded values.

Capture the turn **before your model request**, then add its context to the request you already make:

```python
turn = guard.turn()
context_for_your_model = turn.context_text
# Include context_for_your_model as task data in your existing model request.
# Keep `turn` in your own application, outside model-generated output.

guarded_deploy = turn.wrap(deploy, tool="deploy")

# In your tool handler, replace deploy(**arguments) with:
result = guarded_deploy(**arguments)
```

`arguments` in this snippet is the argument dictionary returned by your existing model integration. No particular model SDK or framework is required. The wrapper accepts positional and keyword arguments, includes declared function defaults in the check, and returns your function's JSON-compatible result directly. Results are copied; custom Python objects and their identity are not preserved.

For example, calling `guarded_deploy("payments")` checks the default `environment="staging"`. The omitted default is not a way to bypass an environment binding.

## Option 2: wrap your tool dispatcher

If your project already has one function that dispatches tool calls, keep that function:

```python
def dispatch(tool, arguments):
    return existing_tool_registrytool (local-only reference; omitted from public distribution)
```

Capture a turn before asking the model for a proposal, then wrap the dispatcher:

```python
turn = guard.turn()
# Send turn.context_text with your existing model request.
# `proposal` is the new tool call returned by that request.
checked_dispatch = turn.wrap_dispatcher(dispatch)
result = checked_dispatch(proposal["tool"], proposal["arguments"])
```

Register a policy for each tool that may run. Unknown tools and unexpected arguments are rejected. Dispatcher calls must explicitly include all bound arguments; unlike the function wrapper, a generic dispatcher cannot inspect defaults inside the functions it selects.

## Handle a changed decision

Update state when your application receives a correction or a new authoritative result:

```python
guard.remember(
    "environment",
    "production",
    source="release-manager",
    text="Use production for this release; staging is no longer the target.",
)
```

An earlier turn now carries an outdated state revision. Calling a tool from that turn raises `ActionRejected`, before the tool executes:

```python
try:
    result = guarded_deploy(**arguments)
except ActionRejected as error:
    decision = error.decision          # "block" or "refresh"
    reasons = error.verdict["issues"]
    current_state = error.recovery_context
    # Return the reasons/current state to your agent loop.
    # Capture guard.turn(), request a NEW model proposal, and check it again.
```

`block` means the proposal violates a rule or is invalid. `refresh` means its captured revision is stale without another blocking issue. Neither exception means the tool ran. Do not catch the exception and call the original function anyway.

**Do not attach an old proposal to a new turn just to make it pass.** Refresh the context and ask the model to reconsider. A successful tool call also records a receipt and advances the revision, so the next model request needs a new turn. This initial API deliberately executes one checked action at a time; it does not automatically retry tools or execute a model's whole batch.

## Keep state between runs

`ContextGuard()` uses an in-memory database by default. Pass a database path to persist state. Save `guard.session_id` in your application's existing workflow or conversation record, then resume that session later:

```python
with ContextGuard("agent-state.db", session_id=saved_session_id) as guard:
    turn = guard.turn()
    # Continue your agent workflow.
```

The `with` form closes the database when your workflow exits. If managing its lifetime yourself, call `guard.close()` when finished. Leaving out `session_id` creates a new session. Reopening the database alone does not select an earlier conversation. The lower-level `guard.store` and `guard.relay` APIs remain available for events, audit history, and detailed policies.

## What your project must supply

- **Facts:** Current decisions and relevant state from your application. A `source` string is a useful provenance label, not proof that the value is authentic. Do not treat arbitrary model output as authoritative state.
- **Rules:** Which facts permit a tool and which arguments must match them. Arguments listed in `optional_args` are permitted without a fact binding; give tools only the flexibility they need.
- **Model requests:** Insert `turn.context_text` into your existing request as task data. Instructions embedded in fact text should not override your application's instructions.
- **Tool execution:** Route relevant actions through the wrapper. Direct calls to the original function bypass the library.
- **Recovery:** When rejected, return current evidence to the agent and obtain a new proposal, or stop the workflow for human input.

## Current boundaries

The convenience wrappers support ordinary synchronous Python functions with fixed signatures, and synchronous `(tool, arguments)` dispatchers. Variadic functions (`*args` or `**kwargs`), coroutine functions, and generator functions are not supported by the function wrapper. This release has no native asynchronous SDK adapter, browser extension, or automatic attachment to ChatGPT/Claude chats. A framework integration must route its tool execution through a supported wrapper.

Facts, checked arguments, and successful results must be JSON-compatible. Checks use exact values, not model-based semantic judgments. Unknown facts or tools cannot be inferred automatically. The middleware has a 12,000-character context ceiling; `turn(max_chars=...)` can reduce that capture budget but cannot exceed the execution ceiling. Exceeding a budget raises an error rather than silently dropping facts. A context snapshot returned by `turn.context` is a defensive copy; editing it cannot relabel the captured turn.

The local execution guard serializes state changes while its callback runs. Keep callbacks short, and record new Relay state after the callback returns rather than writing to the same store from inside it. This is not a distributed transaction manager: an external side effect cannot be undone if a callback later fails, and repeated fresh requests still need the application's own idempotency controls. Callback failures and invalid results are separate from `ActionRejected` and may occur after the tool started executing.

The workbench is simply one way to inspect the same library. Production use of the package does not require launching it.
