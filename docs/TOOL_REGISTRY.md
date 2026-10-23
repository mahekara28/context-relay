# Register tools once, then bind each model decision

`ToolRegistry` puts a Python function and its Relay checks in one place. It removes
repeated `allow_tool(...)`, wrapper and dispatcher mappings. It works with a
`ContextGuard` or an existing synchronous/asynchronous connected Relay agent.
There are no extra dependencies or model calls.

The application still chooses what needs protecting. A library cannot infer that
an item name must remain unchanged, whether a payment is approved, or which
function performs a real external action simply from its installation.

## Connect, declare, dispatch

Use the same connected agent that already prepares your model calls:

```python
from context_relay import ContentPolicy, MemoryPolicy, ToolRegistry, connect

# existing_model_call is your application's supported provider-call function.
agent = connect(existing_model_call, memory=MemoryPolicy())
tools = ToolRegistry(agent)

source = {"rows": [{"item": "Folders", "quantity": 4}]}

@tools.tool(
    memory=True,
    content=ContentPolicy(source, exact=("/rows",)),
    unchecked=("summary",),
)
def write_export(format, language, rows, summary=""):
    # Your application's actual writer, using the arguments Relay checked.
    return existing_writer(format=format, language=language, rows=rows, summary=summary)

result = agent.invoke(messages=history)
proposal = decode_tool_call(result.response)  # Your existing provider decoder.

# Bind the exact turn that produced this response; never obtain a fresh turn here.
bound = tools.bind(result.prepared)
result_of_tool = bound.dispatch(proposal["tool"], proposal["arguments"])
```

`existing_model_call`, `existing_writer`, `history` and `decode_tool_call` belong to
the host application. Provider-specific tool schemas and response parsing stay
with that application's existing integration. For a runnable local example with
no provider account, run [`examples/tool_registry.py`](../examples/tool_registry.py).
Own the connection with `with connect(...) as agent:` in your application, or
close it with `agent.close()` in a `finally` block after the final tool dispatch.

Register before requesting a proposal. A successful action advances the session
revision; the next action needs its own newly prepared model decision. Old bound
tools remain stale even if their arguments are changed to match newer settings.

For a plain `ContextGuard`, use `tools = ToolRegistry(guard)` and bind the turn
captured **before** the application's model call. `guard.prepare(...)` and
`guard.turn()` are both accepted. A turn from another guard object is rejected,
even if it opens the same database and session. For an asynchronous connected
agent, await `agent.invoke(...)` and bind its `result.prepared`; registered tools
are still synchronous.

Native OpenAI/Claude integrations use `ToolRegistry(native_agent.guard)`. Capture
`tool_turn = native_agent.guard.turn()` before that native model call, then use
`tools.bind(tool_turn)` for its proposed action. `NativePrepared` is a request
snapshot, not a guard turn, and is intentionally rejected by `bind()`. The host
still supplies the native model with the relevant facts/source data; registering
a tool does not inject them automatically into a native request. For trusted facts
recorded through `native_agent.remember(...)`, use `bindings`, not the automatic
user-memory mapping selected by `memory=True`.

## Explicit checks, fewer duplicated mappings

Every function parameter, including parameters with defaults, must be covered by
at least one check or explicitly listed as `unchecked`:

| Registration option | What it means |
| --- | --- |
| `bindings={"destination": "approved_destination"}` | Match an application-owned fact. |
| `memory_bindings={"locale": "customer_language"}` | Match current, undisputed user memory under an exact key. |
| `memory=True` | Opt into `format -> export_format` and `language -> response_language` for matching parameter names. |
| `content=ContentPolicy(...)` | Preserve the declared reference fields or rows. |
| `when={"approved": True}` | Require an application-owned prerequisite before dispatch. |
| `unchecked=("summary",)` | Explicitly leave this argument's value unconstrained. |

Explicit fact or memory mappings take precedence over the conventional
`memory=True` mapping. Memory matching never grants permissions. Missing or
disputed memory still blocks the action. Content rules can additionally constrain
a fact- or memory-bound argument. The coverage report identifies those combined
checks; it does not imply that every field inside a content-bound argument is
protected unless the content policy actually declares that scope.

Spelling mistakes, uncovered parameters, overlapping checked/unchecked declarations,
variadic signatures and non-JSON defaults are rejected during setup. Type
annotations are not additional value validators. Parameter defaults are captured
and copied; positional-only and keyword-only Python parameters work normally:

```python
# This calls a signature-preserving wrapper for the already captured turn.
protected_writer = bound["write_export"]
result_of_tool = protected_writer("JSON", "Hindi", source["rows"])
```

The two dispatch snippets above are alternatives for one proposal, not two calls
against the same turn. A decorated function raises a helpful error if called
before binding. To keep an existing function, register it once:

```python
tools.register(
    existing_export, name="export", memory=True, unchecked=("summary",),
    content=ContentPolicy(source, exact=("/rows",)),
)
```

The same complete-parameter rule applies. Registration does not replace an
existing policy with the same tool name. Refresh a source snapshot explicitly
before the next model decision:

```python
updated_source = load_latest_source()  # Your application chooses the authority.
tools.register(
    existing_export, name="export", memory=True, unchecked=("summary",),
    content=ContentPolicy(updated_source, exact=("/rows",)), replace=True,
)
```

Supply the full updated registration, including every parameter's coverage.
`replace=True` can update only a name already owned by this registry whose stored
policy still matches the registration. It advances the session revision so old
bound tools cannot execute. A decorated function can also be supplied again to
its own registry under its existing name; Relay retains its original executable
function. It does not take over an unrelated existing policy.
If another API has changed the policy, both binding and replacement reject it;
resolve the competing setup explicitly before continuing.

## Optional repair through the same boundary

```python
action = tools.bind(result.prepared).execute_with_recovery(
    proposal["tool"], proposal["arguments"], repair="known",
)
```

This opts into Relay's deterministic repair from declared known values. It does
not ask another model to invent a new proposal. The captured turn's normal repair
rules still apply, and the result is checked again before a tool runs. Callable
repairs and `repair=None` remain supported. See
[`MEMORY_ACTIONS.md`](MEMORY_ACTIONS.md) for the repair scope and reports.

Missing checked parameters can reach those repair rules. Missing required
**unchecked** parameters fail before execution: Relay has no trusted value with
which to fill them. Defaults are included both in the first proposal and in a
callable repair. A repair that drops an unchecked required parameter fails before
the actual function is called. Final dispatch never inserts defaults after a
proposal has been checked. If a whole-root content repair omits a function
parameter, dispatch fails before calling the original function; supply a complete
source contract including defaults. A tool that has already started is never retried.

## Make coverage visible

```python
coverage = tools.coverage(["write_export", "delete_file"])
assert coverage["unregistered_tools"] == ["delete_file"]
assert coverage["inventory_fully_registered"] is False
```

The report lists registered names, each parameter's fact/memory/content checks,
unchecked parameters, content scope metadata, and names missing from the supplied
inventory. It does not include the content reference values. With no supplied
inventory, `inventory_fully_registered` is `None` rather than claiming completeness.

The supplied inventory is the application's list; Relay cannot discover every
possible side effect in arbitrary Python code. Original function references,
separate dispatchers, direct SDK actions and deliberately bypassed wrappers remain
outside this boundary. Pass only bound tools into the agent's execution route.
`unchecked` content, derived calculations, free-form prose, source truth/freshness
and a writer's later internal transformations still need application-level checks.
The registry simplifies setup; it does not establish organisation-wide reliability
or make arbitrary tool code transactional.
