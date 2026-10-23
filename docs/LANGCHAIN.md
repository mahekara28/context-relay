> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Use Relay inside LangChain and LangGraph

This optional adapter prepares a chat model's input while your framework keeps
its model, tools, response objects, callbacks and graph checkpoints. It accepts
complete LangChain message histories. It does not modify a model or framework
global. Core Relay still has no required framework dependency.

This bridge uses the text/tool selection path and can enable automatic user
recall. The separate v0.12 OpenAI/Anthropic `native=True` connectors do not expand
this bridge's message boundary: signed thinking and provider compaction blocks
must use the matching native provider adapter, with application-owned history.

The adapter follows the current official [message API](https://docs.langchain.com/oss/python/langchain/messages),
[RunnableLambda API](https://reference.langchain.com/python/langchain-core/runnables/base/RunnableLambda),
and [LangGraph state/node pattern](https://docs.langchain.com/oss/python/langgraph/quickstart),
checked on 2026-09-30. These APIs are bounded below rather than advertised as
support for every LangChain component.

## Existing chat model

Use your already configured model. Bind its tools before connecting Relay.
Place Relay at the final chat-model boundary. Wrapping an entire agent or a
chain such as `RunnableWithMessageHistory` that adds another history after Relay
would leave that extra context outside Relay's view; those arrangements are not
supported by this adapter.

```python
from langchain_core.messages import AIMessage, HumanMessage
from context_relay import MemoryPolicy
from context_relay.langchain import connect_langchain

# model = your existing chat model, optionally model.bind_tools(your_tools)
relay = connect_langchain(model, memory=MemoryPolicy())

messages = [HumanMessage("Set export format to JSON.")]
response = relay.invoke(messages, config={"tags": ["your-existing-tag"]})
messages.extend([response, HumanMessage("Use CSV instead of JSON.")])
result = relay.invoke_result(messages)

assert isinstance(result.response, AIMessage)  # The original model response.
print(result.prepared.report)                # This specific request's plan.
relay.close()                               # Leaves the model open.
```

`invoke` returns the original model result. `invoke_result` returns an
`AgentResult` containing that result and its `PreparedTurn`. `ainvoke` and
`ainvoke_result` use the model's async method and preserve cancellation. The
adapter does not retry transport errors or consume response streams.
Close the bridge after its outstanding calls finish.

Use `relay.prepare(messages)` if you only want preparation. The returned
`LangChainPrepared` has copied framework `.messages`, the exact `.prepared`
Relay turn, and a `.report`. `aprepare` also supports an optional memory
interpreter without blocking the async event loop. There is no shared last-turn
variable that concurrent calls could overwrite.

Full original histories still belong to your application/framework. Never append
the synthetic prepared messages to history. Append the actual model response.
One bridge belongs to one conversation; make another bridge for another user.

## Runnable composition

```python
chain = relay.as_runnable()
response = chain.invoke(messages)
response = await chain.ainvoke(messages)
```

`as_runnable()` constructs the framework's actual `RunnableLambda` through a lazy
import. Its input must be a list/tuple of supported message objects. A prompt
template's `ChatPromptValue`, a string prompt, and an agent-state dictionary are
not accepted automatically; pass their supported complete message history.
`RunnableLambda.stream()` yields a completed result; token-by-token streaming
and `AIMessageChunk` histories are outside this adapter's scope. Batch methods
inherit the framework's concurrency behavior: do not batch different
conversations through one bridge with shared memory.

## LangGraph message state

Bind a bridge to one explicit graph thread before registering its node:

```python
from langgraph.graph import StateGraph, MessagesState, START, END

thread_id = "support-case-123"
relay = connect_langchain(model, memory=MemoryPolicy(), thread_id=thread_id)
builder = StateGraph(MessagesState)
builder.add_node("model", relay.node)  # Use relay.anode for an async graph node.
builder.add_edge(START, "model")
builder.add_edge("model", END)
graph = builder.compile()

result = graph.invoke(
    {"messages": [HumanMessage("Set export format to CSV.")]},
    config={"configurable": {"thread_id": thread_id}},
)
```

The node returns only `{"messages": [new_response]}`. LangGraph's reducer adds
the response to original history; Relay's shortened input and synthetic task
data never replace checkpoint history. An existing graph can keep its normal
tool node and routing around this model node. Every assistant tool-call batch
must have all results before the next model invocation.

`node` and `anode` require an explicitly configured `thread_id`. A missing or
different `config["configurable"]["thread_id"]` is rejected before memory
processing or inference. This is an accidental-mixing check, not authentication
or tenant authorization. Build a separate bridge per conversation and let your
application authenticate and route users. Relay does not create a checkpointer.
Without one, supply the complete accumulated history each time. When resuming
Relay's database, retain both `session_id` and the graph thread mapping.

## Supported message boundary

| Accepted | Treatment |
| --- | --- |
| `SystemMessage`, `HumanMessage`, `AIMessage` with string content | Preserve roles, names, IDs and supported metadata. |
| `ChatMessage` with system/developer/user/assistant role | Preserve the explicit source role; system/developer stay in the prefix. |
| Complete standardized `AIMessage.tool_calls` | Preserve names, JSON args, call IDs and order. |
| `ToolMessage` with string result | Preserve call ID, name, status, artifact and metadata. |
| Empty content list on a tool-only AI message | Represent no text in the canonical plan; keep the original framework object. |
| Raw duplicate function calls in `additional_kwargs` | Allow only when they match the standardized calls. |

Messages are copied before preparation. Whole selected exchanges remain intact,
including parallel tool results with matching IDs. Framework artifacts and
response/usage metadata remain on selected copies; they are not promoted to
authoritative facts or extracted as user memory. Memory reads only normalized
original user text.

Content blocks (including images, audio, text-block lists and reasoning),
reasoning signatures or other non-null provider fields in `additional_kwargs`,
invalid/partial calls, unknown custom message types, and extra message fields
are rejected. Nothing is flattened or silently removed to make it fit. The graph
node also requires a complete `AIMessage` response; an error retains the original
response for the host to inspect and close.

## Budgets and limitations

Relay uses its existing `openai_chat` text/function representation internally;
this does **not** connect an OpenAI provider. Character limits and token counters
see that canonical representation, including selected content, names, call
arguments, call/result IDs and Relay task data. Framework bookkeeping fields
(message IDs, response/usage metadata, tool artifact/status) are retained on the
framework objects but are outside that representation. Tool schemas, provider
framing and any provider-specific use of bookkeeping need caller accounting;
the adapter does not claim an exact hosted-provider token count.

`RelayConfig` can use `plain` (upgraded internally) or `openai_chat`. A different
protocol fails. Configured or explicit `OutputPolicy` fails clearly: this adapter
does not currently run Relay's response-validation/recovery policy around native
framework results. Tool execution, graph checkpoints, model configuration and
framework tracing remain with the host. Existing tracing configuration may
receive model inputs including memory; Relay does not change tracing settings.

Version 0.12 also tests genuine `langchain-core` 1.6.13 and LangGraph 1.2.16
classes, including `RunnableLambda` and compiled graph invocation. The
SDK verification (local-only reference; omitted from public distribution) uses controlled
responses; it establishes integration behavior, not model intelligence. Earlier
framework-shaped stand-in tests remain for focused edge cases.

A separate [local workflow example](PILOT.md) uses actual LangGraph, the Ollama SDK
and installed Qwen 2.5 3B weights to read a CSV and write report files. It achieved
2/6 complete tasks with Relay and 2/6 with its example recent-history baseline.
All failures are published; the run does not establish better model accuracy or
production readiness. It does not compare provider-native compaction. The library
keeps host tracing settings unchanged; this local evaluation script explicitly
disables remote LangSmith tracing for subsequent runs.

`examples/langchain_agent.py` remains a smaller integration demonstration with
a labelled fixed local response function. Neither example installs packages or
downloads models. Optional framework packages must already be available.
