# Connect the tool list once

`auto_connect()` prepares memory for model calls. `agent.protect_tools()` also connects a supplied tool inventory to checks and dispatch. The harness binds every proposal to the exact state that the model saw, validates the proposed tool and arguments, then executes at most one tool. It never captures a fresh permission revision just to make an old proposal pass.

```python
from context_relay import auto_connect

def save_export(format):
    # Your existing application function.
    return {"saved_as": format}

with auto_connect(your_model_call) as agent:
    actions = agent.protect_tools({"save_export": save_export})
    result = actions.invoke(messages=[
        {"role": "system", "content":
         'Propose one action as JSON: {"tool":"save_export","arguments":{"format":"JSON"}}.'},
        {"role": "user", "content": "Export as JSON."},
    ])
    print(result.execution["status"])
```

Conventional parameters `format` and `language` automatically bind to the corresponding user memory. Other parameters need application-owned rules in one `contracts` mapping:

```python
actions = agent.protect_tools(
    {"refund": refund},
    contracts={"refund": {
        "bindings": {"order_id": "current_order"},
        "verification": refund_checks,
        "unchecked": ("note",),
    }},
    repair="known",  # Optional deterministic repair before any action starts.
)
```

The same mapping supports `when`, `memory_bindings`, `content` and the existing tool registry options. Every parameter, including a default, must be covered or explicitly declared unchecked. A bad inventory fails atomically; it does not leave half of the tools enrolled. `actions.coverage()` shows the declared protection. Register and refresh business data before asking the model for proposals.

By default the provider must return exactly one plain JSON object containing `tool` and `arguments`, or the equivalent Python dictionary. Duplicate fields, unknown fields, non-finite or lossy numbers, missing fields, extra calls and unsupported envelopes fail before an action. Supply a synchronous `decoder` for a provider-specific envelope; its result must still satisfy the same strict proposal contract. The original provider response is retained as `result.agent_result.response`. Provider tool definitions and the application's prompts remain application-owned.

For an asynchronous model connection, use `await actions.invoke(...)`. Registered tools remain synchronous bounded functions; this is not an async task scheduler. A tool failure propagates instead of starting a repair or another call after the effect may have happened. A rejected proposal produces an inspectable execution report rather than quietly performing a different action.

Only calls routed through the harness are protected. Original Python function references remain callable; Relay is not a Python sandbox and cannot infer every company's business permissions. Do not give the model a separate unguarded dispatcher. Native provider replay connections use the same `agent.protect_tools(...)` entry point with explicit native contracts; see [provider actions](PROVIDER_ACTIONS.md). The generic `ActionHarness` class remains for prepared text connections.

For named persistent connections, `resume=True` reattaches functions only when their stored contracts are identical. A different contract fails setup atomically; resuming does not rewrite it or renew an old proposal. See [named memory](NAMED_MEMORY.md).
