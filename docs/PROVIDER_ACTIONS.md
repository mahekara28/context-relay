# Protected provider tool calls

Relay v0.19 can decode one completed client function call from OpenAI Chat Completions, OpenAI Responses, Anthropic Messages and Ollama. The original response stays available. Unknown formats, incomplete responses, refusals, multiple actions, streaming, server tools, programmatic callers and namespaced tools are rejected before dispatch. `provider_capabilities("openai_responses")` returns the exact supported scope as data.

For OpenAI Responses and Anthropic, use the native connection so tool and reasoning history stays intact:

```python
from context_relay import auto_connect

# client is your existing OpenAI SDK client; lookup is your existing function.
with auto_connect(client, api="responses", native=True,
                  conversation="customer-42") as agent:
    agent.remember("selected_id", 7)  # Application-owned current state.
    actions = agent.protect_tools(
        {"lookup": lookup},
        contracts={"lookup": {"bindings": {"id": "selected_id"}}},
        repair="known", resume=True,
    )
    result = actions.invoke(model=your_model_id, input=your_input,
                            tools=your_tool_definitions)
    print(result.execution["status"])
    original = result.agent_result.response
```

For an Anthropic client, omit `api="responses"` and supply its normal `messages`, `model`, `max_tokens` and `tools` request arguments. Asynchronous clients use `await actions.invoke(...)`. Every native tool parameter needs a contract or an explicit `unchecked` declaration; native mode does not infer memory from arbitrary provider history. Update application-owned facts before generation. A state change during the provider call invalidates its proposal instead of silently renewing permission.

The application still supplies tool definitions and owns the subsequent tool-result/reasoning replay. Relay performs one checked dispatch, not an automatic tool loop. OpenAI documents function-call arguments and the requirement to carry reasoning items into subsequent tool-result turns in its [function calling guide](https://developers.openai.com/api/docs/guides/function-calling). Relay preserves the provider response; it does not validate the meaning or cryptographic authenticity of opaque reasoning or signatures. Accompanying text is not an approved business answer.

For supported text connections, pass `decoder=ActionDecoder("openai_chat")` or `ActionDecoder("ollama")` to `agent.protect_tools(...)`. These decoders also work independently on genuine SDK response objects or corresponding JSON dictionaries. Raw JSON arguments receive duplicate-key, precision, finiteness and size checks. Already parsed dictionaries cannot recover numeric precision lost before Relay received them.

Named connections can reattach identical contracts with `resume=True`; changed contracts need explicit application migration. Only the harness dispatcher is protected. Keep direct function references and host credentials away from the model's unguarded execution paths.

## Bounded local storage waits

```python
from context_relay import RelayConfig, StoreBusy, auto_connect

settings = RelayConfig(busy_timeout_ms=250)
with auto_connect(model_call, config=settings,
                  conversation="customer-42") as agent:
    # Connect the same protected action inventory and invoke it here.
    pass
```

`busy_timeout_ms` is an integer from 0 to 60,000, with a 10,000 ms default. It bounds each lock acquisition; it is not a deadline for an entire workflow. `StoreBusy.phase` identifies initialization, thread-lock, transaction-begin, statement or commit contention. `before_transaction=True` means this failed storage operation did not start its transaction. It does not certify that earlier operations in the application did nothing. `False` means work may already have started: do not automatically repeat an external action. Use the destination system's idempotency key and reconcile its result.

Named conversations use independent SQLite files, reducing contention between unrelated conversations. One shared database still serializes writes, and a protected callback holds its transaction while it runs. Keep callbacks short. Owner separation belongs to `SessionManager` plus the host's authentication and file permissions. Its capacity accounting is process-local. These changes do not establish a distributed service or production capacity guarantee.

The v0.19 checks use actual installed SDK classes and intercepted HTTP responses, plus real local Python processes and SQLite locks. They test adapter and concurrency behavior without paid calls; they do not establish hosted inference quality or an organisational pilot.
