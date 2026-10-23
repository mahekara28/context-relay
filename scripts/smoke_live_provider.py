"""Explicitly opt in to two hosted native-history calls; otherwise do nothing.

This is a small service acceptance check, not a model-quality or compaction
benchmark. No packages, models or credentials are installed or downloaded.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import RelayConfig, __version__
from context_relay.native import native_json


MARKER = "RELAY_SMOKE"
FIRST = "Remember the marker RELAY_SMOKE for the next turn. Reply with READY only."
SECOND = "Repeat the marker from the first request, and nothing else."
TOOL_FIRST = "Call echo_marker once with marker RELAY_SMOKE. Wait for the tool result before answering."
TOOL_SCHEMA = {"type": "object", "properties": {"marker": {"type": "string", "enum": [MARKER]}},
               "required": ["marker"], "additionalProperties": False}


def create_client(provider, *, api_key, timeout):
    """Load a real optional SDK only after explicit live opt-in and credential checks."""
    sdk = importlib.import_module(provider)
    http = sdk.DefaultHttpxClient(trust_env=False, follow_redirects=False, timeout=timeout)
    client_type = sdk.OpenAI if provider == "openai" else sdk.Anthropic
    base_url = "https://api.openai.com/v1" if provider == "openai" else "https://api.anthropic.com"
    try:
        return client_type(api_key=api_key, base_url=base_url, http_client=http,
                           max_retries=0, timeout=timeout)
    except BaseException:
        http.close()
        raise


def response_items(response, provider):
    value = response.output if provider == "openai" else response.content
    items = native_json(value, sdk=provider)
    if type(items) is not list or any(type(item) is not dict for item in items):
        raise ValueError("Expected complete native output items.")
    return items


def response_text(response, provider):
    if provider == "openai":
        text = response.output_text
        if type(text) is not str:
            raise ValueError("No complete textual response.")
        return text
    return "".join(block["text"] for block in response_items(response, provider)
                   if block.get("type") == "text" and type(block.get("text")) is str)


def run_replay(client, *, provider, model, max_output_tokens, evidence, exercise="replay", native_memory=False):
    """Use a caller-owned client; tests inject offline stand-ins. No implicit retries."""
    first_prompt = TOOL_FIRST if exercise == "tool" else FIRST
    second_turn, base_instructions = False, None
    if provider == "openai":
        from context_relay.openai import connect_openai
        connection = connect_openai(client, api="responses", native=True, native_memory=native_memory,
                                    config=RelayConfig(max_chars=50000))
        history = [{"role": "user", "content": first_prompt}]
        def request():
            body = {"model": model, "input": history, "store": False,
                    "include": ["reasoning.encrypted_content"], "max_output_tokens": max_output_tokens}
            if base_instructions is not None:
                body["instructions"] = base_instructions
            if exercise == "tool":
                body["tools"] = [{"type": "function", "name": "echo_marker", "description": "Echo the fixed smoke-test marker.",
                                  "parameters": TOOL_SCHEMA, "strict": True}]
                body["tool_choice"] = "none" if second_turn else {"type": "function", "name": "echo_marker"}
            return body
    elif provider == "anthropic":
        from context_relay.anthropic import connect_anthropic
        connection = connect_anthropic(client, native=True, native_memory=native_memory, config=RelayConfig(max_chars=50000))
        history = [{"role": "user", "content": first_prompt}]
        def request():
            body = {"model": model, "messages": history, "max_tokens": max_output_tokens}
            if exercise == "tool":
                body["tools"] = [{"name": "echo_marker", "description": "Echo the fixed smoke-test marker.", "input_schema": TOOL_SCHEMA}]
                # Current Claude families do not all support forced tool use.
                body["tool_choice"] = {"type": "none" if second_turn else "auto"}
            return body
    else:
        raise ValueError("Unsupported provider.")
    with connection:
        if native_memory:
            connection.remember("smoke_marker", MARKER, source="explicit-host-smoke-fixture")
        evidence["stage"] = "first_response"
        first_request = connection.prepare(**request()).request
        evidence["hosted_calls_attempted"] += 1
        first = connection.invoke_result(**first_request)
        evidence["hosted_calls_completed"] += 1
        if ((provider == "openai" and getattr(first.response, "status", None) != "completed")
                or (provider == "anthropic" and getattr(first.response, "stop_reason", None) != ("tool_use" if exercise == "tool" else "end_turn"))):
            raise ValueError("The first response was not a complete text turn.")
        items = response_items(first.response, provider)
        evidence["first_request_sha256"] = first.prepared.history_id
        evidence["replayed_item_types"] = [item.get("type") for item in items]
        evidence["encrypted_reasoning_observed"] = any(
            item.get("type") == "reasoning" and bool(item.get("encrypted_content")) for item in items)
        evidence["signed_thinking_observed"] = any(
            item.get("type") == "thinking" and bool(item.get("signature")) for item in items)
        evidence["compaction_observed"] = any(item.get("type") == "compaction" for item in items)
        evidence["first_memory_report"] = {key: value for key, value in first.prepared.report.items()
                                           if key.startswith("memory_") or key == "prefix_replay_checked"}
        # Replay the exact prepared prefix when explicit native memory inserted
        # an inline system block. Reconstructing from the original input loses it.
        history = first.prepared.request["input" if provider == "openai" else "messages"]
        base_instructions = first.prepared.request.get("instructions")
        if provider == "openai":
            # Keep EVERY item, not just the text used to grade the fixed marker.
            history.extend(first.response.output)
        else:
            history.append({"role": "assistant", "content": first.response.content})
        if exercise == "tool":
            family = "function_call" if provider == "openai" else "tool_use"
            calls = [item for item in items if item.get("type") == family]
            if len(calls) != 1 or calls[0].get("name") != "echo_marker":
                raise ValueError("Expected exactly one permitted echo_marker call.")
            call = calls[0]
            arguments = json.loads(call["arguments"]) if provider == "openai" else call["input"]
            if arguments != {"marker": MARKER}:
                raise ValueError("Tool arguments did not match the fixed smoke-test contract.")
            evidence["local_fixture_tool_executions"] = 1
            if provider == "openai":
                history.append({"type": "function_call_output", "call_id": call["call_id"], "output": MARKER})
                history.append({"role": "user", "content": SECOND})
            else:
                history.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": call["id"], "content": MARKER},
                    {"type": "text", "text": SECOND}]})
        else:
            history.append({"role": "user", "content": SECOND})
        second_turn = True
        evidence["stage"] = "history_replay"
        second_request = connection.prepare(**request()).request
        evidence["hosted_calls_attempted"] += 1
        second = connection.invoke_result(**second_request)
        evidence["hosted_calls_completed"] += 1
        evidence["second_request_sha256"] = second.prepared.history_id
        evidence["second_memory_report"] = {key: value for key, value in second.prepared.report.items()
                                            if key.startswith("memory_") or key == "prefix_replay_checked"}
        evidence["history_replay_accepted"] = True
        if ((provider == "openai" and getattr(second.response, "status", None) != "completed")
                or (provider == "anthropic" and getattr(second.response, "stop_reason", None) != "end_turn")):
            raise ValueError("The replay response was not a complete text turn.")
        evidence["marker_correct"] = response_text(second.response, provider).strip() == MARKER
        evidence["status"] = "PASSED" if evidence["marker_correct"] else "COMPLETED_WITH_WRONG_OUTPUT"
        evidence["stage"] = "finished"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="Explicitly permit two hosted calls, which may incur provider charges.")
    parser.add_argument("--provider", choices=("openai", "anthropic"))
    parser.add_argument("--model", help="An explicit model ID chosen by the operator; no model is selected automatically.")
    parser.add_argument("--exercise", choices=("replay", "tool"), default="replay",
                        help="Optionally replay one model-proposed echo_marker call and its deterministic local result.")
    parser.add_argument("--native-memory", action="store_true", help="Explicitly test projection of one host-recorded marker fact.")
    parser.add_argument("--output", type=Path, required=True, help="New report path; never overwrites existing evidence.")
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    if args.run_live and (not args.provider or not args.model or not args.model.strip()):
        parser.error("Live checks require both --provider and an explicit nonempty --model.")
    if not 1 <= args.max_output_tokens <= 4096 or not 0 < args.timeout <= 120:
        parser.error("Use 1-4096 output tokens and a timeout greater than zero and at most 120 seconds.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve evidence before importing SDKs, reading credentials or calling a service.
    with args.output.open("x", encoding="utf-8") as output:
        evidence = {
            "schema": 1, "relay_version": __version__, "status": "NOT_RUN",
            "provider": args.provider, "model": args.model, "hosted_calls_attempted": 0,
            "exercise": args.exercise, "native_memory_requested": args.native_memory,
            "local_fixture_tool_executions": 0,
            "hosted_calls_completed": 0, "history_replay_accepted": False, "marker_correct": None,
            "encrypted_reasoning_observed": False, "signed_thinking_observed": False,
            "compaction_observed": False, "compaction_requested": False,
            "max_calls": 2, "max_output_tokens_per_call": args.max_output_tokens,
            "timeout_seconds_per_call": args.timeout, "sdk_retries": 0,
            "reason": "Explicit --run-live opt-in was not supplied.",
            "limitations": [
                "Two fixed synthetic text turns, not a production workload or reliability benchmark.",
                "No reasoning/thinking mode or compaction is enabled by this runner; observed fields are reported honestly.",
                "Replayed opaque items, if present, remain unmodified; absence cannot verify signature or encrypted-history support.",
                "Responses, ciphertext, signatures, credentials and provider error messages are not retained.",
                "Model quality, long-context behavior, async and streaming require separate evaluations.",
            ],
        }
        started, client = time.monotonic(), None
        try:
            if args.run_live:
                key_name = "OPENAI_API_KEY" if args.provider == "openai" else "ANTHROPIC_API_KEY"
                key = os.environ.get(key_name)
                if not key or not key.strip():
                    evidence["reason"] = "Required provider credential is absent; no SDK client was created."
                    evidence["status"] = "NOT_RUN_MISSING_CREDENTIAL"
                else:
                    evidence.pop("reason")
                    evidence["sdk_version"] = importlib.metadata.version(args.provider)
                    evidence["stage"] = "client_setup"
                    client = create_client(args.provider, api_key=key, timeout=args.timeout)
                    run_replay(client, provider=args.provider, model=args.model,
                               max_output_tokens=args.max_output_tokens, evidence=evidence,
                               exercise=args.exercise, native_memory=args.native_memory)
        except KeyboardInterrupt:
            evidence["status"] = "INTERRUPTED"
        except Exception as exc:
            evidence["status"] = "ERROR"
            evidence["error_type"] = type(exc).__name__
            # Provider messages can include request or credential data. Never persist them.
            status = getattr(exc, "status_code", None)
            if type(status) is int:
                evidence["http_status"] = status
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as exc:
                    evidence["cleanup_error_type"] = type(exc).__name__
            evidence["seconds"] = round(time.monotonic() - started, 3)
            output.write(json.dumps(evidence, indent=2) + "\n")
            output.flush()
    print(f"{evidence['status']}: {args.output}")
    return 0 if evidence["status"] in ("NOT_RUN", "PASSED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
