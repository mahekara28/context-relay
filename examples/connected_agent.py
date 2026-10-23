"""Configure once; prepare context automatically on each existing-model call.

    python examples/connected_agent.py
    python examples/connected_agent.py --model YOUR_INSTALLED_OLLAMA_MODEL

The default is an explicitly labelled test transport, not an LLM. The optional
Ollama mode makes two real calls using already installed local weights and prints
the actual responses. It never downloads models or contacts a hosted model API.
The application, not Relay, records decisions and maintains the complete history.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import RelayConfig, connect


def conversation() -> list[dict[str, str]]:
    history = [
        {"role": "system", "content": (
            "Help design the user's application. Use the application-recorded "
            "facts as current requirements; earlier conversation may be outdated. "
            "Reply in at most two short sentences about storage and export."
        )},
        {"role": "user", "content": "The application must work offline. Start with JSON exports."},
        {"role": "assistant", "content": "The initial requirements are offline operation and JSON exports."},
    ]
    for number in range(12):
        history.extend([
            {"role": "user", "content": f"Review screen {number + 1}. " +
             "The layout has a title, a short description, and an account list. " * 8},
            {"role": "assistant", "content": f"Screen {number + 1} reviewed; no storage or export decision changed."},
        ])
    history.append({"role": "user", "content": "Summarize the storage and export plan using my current requirements."})
    return history


def test_model_call(*, messages: list[dict], model: str) -> dict:
    """A test double which reports received state; it performs no AI reasoning."""
    data_message = messages[-2]["content"]
    # Only inspect the compiler-created packet in this controlled fixture.
    packet = json.loads(data_message[data_message.index("{"):])
    facts = packet["state"]["facts"]
    return {"message": {"role": "assistant", "content": (
        f"[TEST TRANSPORT; NO LLM] Received {len(messages)} prepared messages. "
        f"Recorded facts: {json.dumps(facts, sort_keys=True)}."
    )}, "model": model}


def response_text(response: dict) -> str:
    message = response.get("message")
    if type(message) is not dict or type(message.get("content")) is not str:
        raise ValueError("The model transport returned no assistant text.")
    if not message["content"].strip():
        raise ValueError("The model transport returned an empty answer.")
    return message["content"]


def run_example(model_call, *, model: str) -> dict:
    history = conversation()
    settings = RelayConfig(max_chars=4000, recent_turns=1, retrieve_turns=2, pinned=(1,))

    with connect(model_call, config=settings) as agent:
        # Record requirements when your application receives confirmed decisions.
        # Relay does not extract these facts from the conversation automatically.
        agent.remember("network_access", False, source="example:confirmed-user")
        agent.remember("output_format", "json", source="example:confirmed-user")

        original = deepcopy(history)
        first_response = agent(messages=history, model=model)
        if history != original:
            raise AssertionError("The connection modified the application's history.")
        first_text = response_text(first_response)
        print("First response:", first_text, sep="\n", flush=True)

        # Your normal application loop owns these additions, not the connection.
        history.append({"role": "assistant", "content": first_text})
        correction = "Correction: export CSV instead of JSON. Keep the app offline. Summarize the updated plan."
        history.append({"role": "user", "content": correction})
        agent.remember("output_format", "csv", source="example:confirmed-user", text=correction)

        # No manual prepare() call: invoke() additionally returns THIS call's turn.
        original = deepcopy(history)
        result = agent.invoke(messages=history, model=model)
        if history != original:
            raise AssertionError("The connection modified the application's history.")
        second_text = response_text(result.response)
        print("\nSecond response after the confirmed correction:", second_text, sep="\n", flush=True)

        report = result.prepared.report
        if report["size_chars"] > settings.max_chars:
            raise AssertionError("The connected request exceeded its character budget.")
        if result.prepared.context["facts"]["output_format"] != "csv":
            raise AssertionError("The connected request did not capture the recorded correction.")
        if not report["omitted_indices"]:
            raise AssertionError("This long-history example should omit some older messages.")

        print(f"\nPrepared automatically: {report['size_chars']:,} / {report['max_chars']:,} characters.")
        print(f"Original messages retained: {len(report['included_indices'])} / {report['total_messages']}.")
        print(f"Original messages omitted: {len(report['omitted_indices'])}.")
        print("Current recorded format supplied to the second call: csv.")
        print("The complete conversation remains in the application; replies were appended by this example.")
        print("The report verifies prepared input, not whether a real model obeyed it.")
        return {"first_response": first_response, "second_response": result.response, "report": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="Use this already installed local Ollama model instead of the test transport.")
    parser.add_argument("--port", type=int, default=11434, help="Loopback Ollama port (default: 11434).")
    args = parser.parse_args(argv)
    try:
        if args.model:
            from ollama_agent import LocalOllama

            client = LocalOllama(port=args.port, timeout=60)
            model_name = client.require_local_model(args.model)

            def existing_model_call(*, messages, model):
                return client._request("/api/chat", {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 256},
                    "keep_alive": "2m",
                })

            print(f"REAL LOCAL MODEL: {model_name}. Two requests; actual responses are printed below.", flush=True)
            run_example(existing_model_call, model=model_name)
        else:
            print("CONNECTION DEMO | Test transport only; no AI inference, model, or network.\n", flush=True)
            run_example(test_model_call, model="test-transport")
        return 0
    except (ValueError, TypeError, RuntimeError, OSError) as exc:
        print(f"Example stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
