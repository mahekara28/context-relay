# Keep tool conversations intact

An agent often asks a tool for information before answering. That leaves a sequence such as:

**User asks about an order → AI calls `lookup_order` → tool returns the order → AI answers.**

Removing just the tool call or just its result can make the next AI request invalid or misleading. Relay's native Ollama adapter keeps that entire user-led exchange together. It either includes the exchange or reports that it was omitted.

## Connect once

Use the same connection interface with the Ollama protocol selected:

```python
from context_relay import RelayConfig, connect

settings = RelayConfig(protocol="ollama", max_chars=12000, recent_turns=2)

with connect(existing_ollama_chat, config=settings) as agent:
    result = agent.invoke(
        messages=full_native_history,
        model="your-installed-tool-capable-model",
        tools=your_tool_schemas,
        stream=False,
    )
    response = result.response
    print(result.prepared.report)
```

The names `existing_ollama_chat`, `full_native_history`, and `your_tool_schemas` stand for objects your project already supplies. Model settings, tool schemas, and the provider's response pass through unchanged. An existing asynchronous model function works through `await agent.invoke(...)` too.

For file configuration, add `"protocol": "ollama"` to `relay.json`. The default remains `"plain"` for ordinary text conversations.

Relay does not execute a returned tool call. Continue using your existing dispatcher, or use `result.prepared.wrap(...)` to apply Relay's registered action policies. Use the prepared turn from that specific request so a later correction can invalidate an older proposed action.

## Example native history

The next request can end after the tool results, when the model is ready to continue its answer:

```python
history = [
    {"role": "system", "content": "Help with order tracking."},
    {"role": "user", "content": "Where is order A17?"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "function": {
                "name": "lookup_order",
                "arguments": {"order_id": "A17"},
            }
        }],
    },
    {
        "role": "tool",
        "tool_name": "lookup_order",
        "content": '{"status":"at depot"}',
    },
]
```

You can also prepare this history directly:

```python
from context_relay import compile_ollama_context

plan = compile_ollama_context(history, max_chars=12000, recent_turns=1)
response = existing_ollama_chat(messages=plan.messages, model="your-model")
```

Keep the full original history in your application. Append real assistant messages and real tool results there, not to Relay's shortened message list. SDK message objects need conversion to ordinary JSON-compatible dictionaries first; for the Ollama Python SDK, use `message.model_dump(exclude_none=True)`.

## What is preserved and checked

Relay preserves accepted message dictionaries, whitespace, argument order, nested argument types, tool call IDs, function indices, and assistant `thinking` text. It does not convert a tool call into ordinary prose. Application state and omission counts appear in a separate user-data message immediately before the latest user-led exchange, never between a call and its result.

The adapter accepts:

- Initial `system` messages and user-led exchanges containing `assistant` and `tool` messages.
- Plain string content. An assistant issuing tool calls may omit `content`.
- Complete single or parallel tool-call batches, including several consecutive batches in one exchange.
- Native function argument dictionaries, optional function `index` and `description`, optional call `id`, and optional `type: "function"`.
- Tool results associated by `tool_call_id`, or by `tool_name` in the issued order. IDs, when supplied, must be unique throughout the captured conversation. A result with both ID and name must match both.
- A final pending user message, or the final tool result of a completed batch.

For same-name parallel calls without IDs, the host must preserve the issued order of results. Relay can check names and structure; it cannot prove which external execution produced a result. ID-bearing results may arrive out of order and are associated with their matching outstanding call.

Missing results, orphan results, unknown IDs, inconsistent names, unsupported fields, multimodal content, and incomplete stream fragments cause an error before the model is called. Gather streaming assistant fields into a complete message and append every tool result first. Images and other provider protocols need their own adapters. `developer` messages are accepted by the plain compiler, not this native Ollama adapter.

This supported subset follows Ollama's [tool-calling documentation](https://docs.ollama.com/capabilities/tool-calling) and [native message and tool-call types](https://github.com/ollama/ollama/blob/main/api/types.go), checked September 29, 2026. Providers may accept additional fields that this adapter deliberately does not yet support.

## Budgets cover the actual messages

The character budget measures the exact serialized native message list, including calls, argument dictionaries, results, reasoning text, and Relay's task-data message. Required material that does not fit raises an error. Optional exchanges are never partly truncated.

If you supply a `TokenBudget`, its counter receives those same complete native messages. Configure a tokenizer and chat template that understand that format. Tool schemas, model instructions supplied outside `messages`, and other request fields must be included in your counter's request accounting or reserved through `overhead_tokens`. The message character budget does not cover those separate request fields.

Pinning any tool result retains its complete user-led exchange. Optional lexical retrieval also searches function names and JSON arguments; this is word overlap, not an assessment that a tool result is true or current.

## Plain-text source labels

For evidence-based answers using the plain protocol, `compile_context(..., source_labels=True)` adds a marker such as `[Relay source: m4]` before rendered user or assistant text. The number is the original message position, even when earlier messages were omitted. System/developer instructions and the original archive stay unchanged. Marker overhead counts toward character and token budgets.

These are citation hints, not authentication or a guarantee that a claim is supported. Quote validation still needs the original evidence. Source labels are not currently supported for native Ollama tool histories because the adapter preserves their message content unchanged.
