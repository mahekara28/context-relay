"""Attach to existing call sites and remember direct changes locally.

    python examples/attached_agent.py
    python examples/attached_agent.py --model YOUR_INSTALLED_OLLAMA_MODEL

Default mode is a labelled test transport, not an LLM. Optional real mode needs
an already installed Ollama SDK and model; this script installs/downloads nothing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay.attachment import attach
from context_relay.memory import MemoryPolicy


class DemonstrationClient:
    """An explicit local test transport with no model inference or network."""

    def chat(self, model="", messages=None, *, stream=False):
        if stream:
            raise ValueError("This demonstration transport provides complete responses only.")
        return {"message": {"role": "assistant", "content": (
            f"[TEST TRANSPORT; NO LLM] Received {len(messages)} prepared messages."
        )}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Use this already installed model through an already installed Ollama SDK")
    args = parser.parse_args()
    if args.model:
        try:
            from ollama import Client
        except ImportError:
            parser.error("Real mode requires an already installed Ollama Python SDK; no package was installed.")
        client = Client(host="http://localhost:11434", timeout=60)
        attach_options = {}
        model = args.model
        print(f"REAL LOCAL INFERENCE using existing model: {model}", flush=True)
    else:
        client = DemonstrationClient()
        attach_options = {"method": "chat"}
        model = "labelled-test-transport"
        print("TEST TRANSPORT ONLY; this run performs no model inference.", flush=True)

    try:
        with attach(client, memory=MemoryPolicy(mode="auto"), **attach_options) as relay:
            history = [{"role": "user", "content": "Set export format to JSON."}]
            first = client.chat(model, history)
            assistant = first.message if args.model else first["message"]
            history.extend([assistant, {"role": "user", "content": "Use CSV instead of JSON."}])
            second = client.chat(model=model, messages=history)
            print("Current local user-stated memory:")
            print(json.dumps(relay.memory.snapshot(), indent=2))
            content = second.message.content if args.model else second["message"]["content"]
            print("Provider response:", content)
            print("Session:", relay.session_id)
    finally:
        if args.model:
            client.close()


if __name__ == "__main__":
    main()
