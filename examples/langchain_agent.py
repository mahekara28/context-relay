"""Real optional framework wiring with a deterministic local test transport.

    python examples/langchain_agent.py
    python examples/langchain_agent.py --graph

Requires already installed langchain-core, plus langgraph for --graph. No package
or model is downloaded. The response function is NOT an LLM or accuracy test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", action="store_true", help="Also exercise an installed LangGraph MessagesState graph")
    args = parser.parse_args()
    try:
        from langchain_core.messages import AIMessage, HumanMessage
        from langchain_core.runnables import RunnableLambda
        if args.graph:
            from langgraph.graph import StateGraph, MessagesState, START, END
    except ImportError as exc:
        print(f"Optional framework is unavailable: {exc}. Nothing was installed or downloaded.", file=sys.stderr)
        return 2

    from context_relay import MemoryPolicy
    from context_relay.langchain import connect_langchain

    def demonstration_response(messages):
        packet = next(message.content for message in messages
                      if isinstance(message.content, str) and message.content.startswith("APPLICATION-RECORDED TASK DATA\n"))
        data = json.loads(packet[packet.index("{"):])["state"]["user_memory"]
        values = {entry["key"]: entry["value"] for entry in data["entries"]}
        return AIMessage(content=f"[TEST TRANSPORT; NO LLM] Received user memory: {json.dumps(values)}")

    model = RunnableLambda(demonstration_response)
    print("ACTUAL OPTIONAL FRAMEWORK; deterministic test response, no model inference.")
    thread_id = "local-demo"
    with connect_langchain(model, memory=MemoryPolicy(), thread_id=thread_id if args.graph else None) as relay:
        messages = [HumanMessage("Set export format to JSON.")]
        if args.graph:
            builder = StateGraph(MessagesState)
            builder.add_node("model", relay.node)
            builder.add_edge(START, "model")
            builder.add_edge("model", END)
            graph = builder.compile()
            config = {"configurable": {"thread_id": thread_id}}
            first = graph.invoke({"messages": messages}, config=config)
            messages = first["messages"] + [HumanMessage("Use CSV instead of JSON.")]
            second = graph.invoke({"messages": messages}, config=config)
            print(second["messages"][-1].content)
        else:
            chain = relay.as_runnable()
            first = chain.invoke(messages)
            messages.extend([first, HumanMessage("Use CSV instead of JSON.")])
            print(chain.invoke(messages).content)
        print("Session:", relay.session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
