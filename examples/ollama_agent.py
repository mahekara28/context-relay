"""Optional local-model example using an already installed Ollama model.

Requirements: Ollama running locally and a downloaded local model that supports
JSON-schema output. This script does not install Ollama, pull models, or call a
hosted API. Disable Ollama cloud features if you require fully offline inference.
Local inference uses your own computer's RAM, compute, and electricity.

From the repository root:
    python examples/ollama_agent.py --list-models
    python examples/ollama_agent.py --model YOUR_INSTALLED_MODEL

The model proposes a tool using a captured context snapshot. The harness then
records a changed customer request before execution, rejects the old proposal,
and asks the model again with refreshed context. All tool actions are simulated.
Malformed proposals are rejected; model success is not assumed or fabricated.

API references: https://docs.ollama.com/api/chat and https://docs.ollama.com/api/tags
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import Relay, RelayStore, guarded_tool_call
from harness_demo import create_workflow, record_correction, simulated_executor


PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": ["issue_refund", "create_replacement"]},
        "arguments": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
    "required": ["tool", "arguments"],
    "additionalProperties": False,
}


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, "Redirects are disabled for this local-only example.", headers, fp)


class LocalOllama:
    def __init__(self, *, port: int = 11434, timeout: float = 120):
        if not 1 <= port <= 65535 or not 0 < timeout <= 600:
            raise ValueError("port must be 1-65535 and timeout must be greater than 0 and at most 600 seconds.")
        self.base_url = f"http://127.0.0.1:{port}"
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())

    def _request(self, path: str, payload: dict | None = None) -> dict:
        encoded = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=encoded,
                                         headers={"Content-Type": "application/json", "Accept": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("Ollama response exceeded the example's 2 MB limit.")
        result = json.loads(data.decode("utf-8"))
        if type(result) is not dict:
            raise ValueError("Expected a JSON object from Ollama.")
        if result.get("error"):
            raise RuntimeError(f"Ollama: {result['error']}")
        return result

    def installed_models(self) -> list[dict]:
        models = self._request("/api/tags").get("models")
        if type(models) is not list or any(type(model) is not dict for model in models):
            raise ValueError("Ollama returned an invalid model list.")
        return models

    def require_local_model(self, model_name: str) -> str:
        models = self.installed_models()
        selected = next((model for model in models if model.get("name") == model_name), None)
        if selected is None:
            selected = next((model for model in models if model.get("name") == model_name + ":latest"), None)
        if selected is None:
            raise ValueError("That model is not installed. Use --list-models. This example never downloads models.")
        name = selected["name"]
        if "cloud" in name.lower() or selected.get("remote_model") or selected.get("remote_host"):
            raise ValueError("Cloud models are not supported by this local-only example.")
        details = self._request("/api/show", {"model": name})
        if details.get("remote_model") or details.get("remote_host"):
            raise ValueError("This model routes to a remote service; choose downloaded local weights.")
        metadata = details.get("details")
        if type(metadata) is not dict or metadata.get("format") != "gguf" or not details.get("model_info"):
            raise ValueError("Could not verify downloaded local GGUF model metadata; no inference was requested.")
        return name

    def propose(self, model_name: str, context: dict, previous_issues: list | None = None) -> dict:
        task = {
            "current_context": {"facts": context["facts"], "sources": context["sources"]},
            "instruction": "Choose the tool matching the recorded current resolution. Use the recorded order_id.",
            "previous_check_issues": previous_issues or [],
        }
        response = self._request("/api/chat", {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You propose one customer-support tool call. Return only JSON matching the provided schema. Never supply a context_revision. Tool execution is separately controlled by the harness."},
                {"role": "user", "content": json.dumps(task, ensure_ascii=True)},
            ],
            "format": PROPOSAL_SCHEMA,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 1024},
            "keep_alive": "2m",
        })
        message = response.get("message")
        if type(message) is not dict or type(message.get("content")) is not str:
            raise ValueError("Ollama returned no text proposal.")
        proposal = json.loads(message["content"])
        if type(proposal) is not dict:
            raise ValueError("Model proposal must be a JSON object.")
        return proposal


def run_agent(client: LocalOllama, model_name: str) -> dict:
    with RelayStore() as store:
        relay = Relay(store)
        session_id = create_workflow(store)
        original_context = relay.context(session_id)
        initial_proposal = client.propose(model_name, original_context)

        # Simulate a user correction arriving after the model saw the old state.
        record_correction(store, session_id)
        initial_result = guarded_tool_call(relay, session_id, original_context,
                                           initial_proposal, simulated_executor)
        if initial_result["executed"]:
            raise RuntimeError("The old snapshot unexpectedly executed.")

        refreshed_context = initial_result["check"]["recovery_context"]
        revised_proposal = client.propose(model_name, refreshed_context, initial_result["check"]["issues"])
        revised_result = guarded_tool_call(relay, session_id, refreshed_context,
                                           revised_proposal, simulated_executor)
        return {
            "mode": "local Ollama inference; all tool effects simulated",
            "model": model_name,
            "initial_proposal": initial_proposal,
            "initial_result": initial_result,
            "revised_proposal": revised_proposal,
            "revised_result": revised_result,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-models", action="store_true", help="List models already available from local Ollama; downloads nothing.")
    group.add_argument("--model", help="Name of an already downloaded local model that supports structured JSON output.")
    parser.add_argument("--port", type=int, default=11434, help="Loopback Ollama port (default: 11434).")
    parser.add_argument("--timeout", type=float, default=120, help="Per-request timeout in seconds, maximum 600 (default: 120).")
    parser.add_argument("--json", action="store_true", help="Print complete model proposals, checks, and simulated receipts.")
    args = parser.parse_args(argv)
    try:
        client = LocalOllama(port=args.port, timeout=args.timeout)
        if args.list_models:
            models = client.installed_models()
            if not models:
                print("No models are installed. This example does not download models.")
            for model in models:
                print(model.get("name", "(unnamed model)"))
            return 0
        name = client.require_local_model(args.model)
        if not args.json:
            print(f"Requesting real proposals from local Ollama model {name}. All tool actions are simulated.", flush=True)
        report = run_agent(client, name)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=True))
        else:
            for label, proposal_key, result_key in (("Old snapshot", "initial_proposal", "initial_result"),
                                                    ("Refreshed snapshot", "revised_proposal", "revised_result")):
                result = report[result_key]
                print(f"{label}: model proposed {report[proposal_key]['tool']!r}; {result['check']['decision'].upper()}; executed={result['executed']}.")
                for problem in result["check"]["issues"]:
                    print(f"  {problem['code']}: {problem['message']}")
            print("No money moved and no shipment was created.")
        return 0 if report["revised_result"]["executed"] else 2
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Example stopped without executing an unvalidated tool: {exc}", file=sys.stderr)
        print("Use a running local Ollama instance and an already downloaded model; run --help for details.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
