"""Explicit completed-provider tool proposals and native captured-turn dispatch.

These adapters decode one client function call, not a provider agent loop. Text,
thinking and reasoning remain original, unverified provider data. Applications
own schemas, tool-result history, opaque reasoning replay and any subsequent call.
"""
from __future__ import annotations

import json
import sys

from .actions import ActionProposalError, _ActionHarnessBase, _proposal
from .models import clone, encode
from .native import AsyncNativeConnection, NativeConnection, native_json


_MAX_ENVELOPE_CHARS = 256000
_PROVIDERS = ("openai_chat", "openai_responses", "anthropic", "ollama")
_NATIVE_PROVIDERS = {"openai_responses_native": "openai_responses", "anthropic_messages": "anthropic"}


def provider_capabilities(provider):
    """Return defensive data describing this adapter's deliberately bounded scope."""
    if type(provider) is not str or provider not in _PROVIDERS:
        raise ValueError("Choose openai_chat, openai_responses, anthropic or ollama explicitly.")
    return {
        "provider": provider, "max_client_actions": 1, "streaming": False,
        "server_tools": False, "async_function_calls": False,
        "programmatic_calls": False, "namespaced_calls": False,
        "native_harness": provider in _NATIVE_PROVIDERS.values(),
        "preserves_original_response": True, "automatic_tool_loop": False,
        "history_replay": "application_owned", "tool_schemas": "application_owned",
        "ancillary_semantics_checked": False, "source_truth_checked": False,
        "direct_calls_protected": False,
        "ancillary_types": {
            "openai_chat": ["text"], "openai_responses": ["message", "reasoning"],
            "anthropic": ["text", "thinking", "redacted_thinking"], "ollama": ["text", "thinking"],
        }[provider],
        "annotated_text": False, "max_envelope_chars": _MAX_ENVELOPE_CHARS,
        "max_proposal_chars": 64000,
        "number_scope": "Strict raw JSON arguments; already parsed dictionaries use their existing JSON values.",
    }


def _shape(value, required=(), optional=()):
    if type(value) is not dict or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError("Unsupported provider item fields.")
    return value


def _text(value, *, empty=False):
    if type(value) is not str or (not empty and not value):
        raise ValueError("Expected provider text.")


def _items(value):
    if type(value) is not list or len(value) > 128:
        raise ValueError("Expected a bounded complete item list.")
    return value


def _completed(value):
    if value is not None and value != "completed":
        raise ValueError("Provider item has not completed.")


def _empty(value):
    if value is not None and value != []:
        raise ValueError("This optional provider format is unsupported.")


def _direct(call):
    if call.get("caller") is not None and call["caller"] != {"type": "direct"}:
        raise ValueError("Programmatic tool callers are unsupported.")
    if call.get("namespace") is not None or call.get("toolset_name") is not None:
        raise ValueError("Namespaced tools are unsupported.")
    if call.get("async") is not None and call["async"] is not False:
        raise ValueError("Asynchronous provider function calls are unsupported.")


def _call(name, arguments, *, raw):
    _text(name)
    if raw:
        _text(arguments)
        # Keep the provider's original decimal tokens until the shared strict
        # parser has checked duplicates, depth, finiteness and precision.
        return _proposal('{"tool":' + json.dumps(name) + ',"arguments":' + arguments + '}')
    return _proposal({"tool": name, "arguments": arguments})


def _one(calls):
    if len(calls) != 1:
        raise ValueError("Exactly one completed client function is supported.")
    return calls[0]


def _chat(body):
    _shape(body, ("choices",), ("id", "created", "model", "object", "metadata", "moderation", "service_tier", "system_fingerprint", "usage"))
    if body.get("object", "chat.completion") != "chat.completion" or body.get("moderation") is not None:
        raise ValueError("Unsupported Chat completion envelope.")
    choice = _one(_items(body["choices"]))
    _shape(choice, ("message", "finish_reason"), ("index", "logprobs"))
    if choice["finish_reason"] != "tool_calls" or ("index" in choice and (type(choice["index"]) is not int or choice["index"] != 0)):
        raise ValueError("Chat completion did not finish with one action.")
    msg = _shape(choice["message"], ("role", "tool_calls"), ("content", "refusal", "annotations", "audio", "function_call"))
    if msg["role"] != "assistant" or any(msg.get(key) is not None for key in ("refusal", "audio", "function_call")):
        raise ValueError("Refused or unsupported Chat message.")
    if msg.get("content") is not None:
        _text(msg["content"], empty=True)
    _empty(msg.get("annotations"))
    call = _shape(_one(_items(msg["tool_calls"])), ("id", "type", "function"))
    _text(call["id"])
    if call["type"] != "function":
        raise ValueError("Only client function tools are supported.")
    function = _shape(call["function"], ("name", "arguments"))
    return _call(function["name"], function["arguments"], raw=True)


def _reasoning(item):
    _shape(item, ("id", "type", "summary"), ("content", "encrypted_content", "status"))
    _text(item["id"])
    _completed(item.get("status"))
    for summary in _items(item["summary"]):
        _shape(summary, ("type", "text"))
        if summary["type"] != "summary_text":
            raise ValueError("Unsupported reasoning summary.")
        _text(summary["text"], empty=True)
    if item.get("content") is not None:
        for content in _items(item["content"]):
            _shape(content, ("type", "text"))
            if content["type"] != "reasoning_text":
                raise ValueError("Unsupported reasoning content.")
            _text(content["text"], empty=True)
    if item.get("encrypted_content") is not None:
        _text(item["encrypted_content"])


def _response_message(item):
    _shape(item, ("id", "type", "role", "status", "content"), ("phase",))
    _text(item["id"])
    if item["role"] != "assistant" or item["status"] != "completed" or item.get("phase") not in (None, "commentary", "final_answer"):
        raise ValueError("Unsupported Responses message.")
    for content in _items(item["content"]):
        _shape(content, ("type", "text", "annotations"), ("logprobs",))
        if content["type"] != "output_text":
            raise ValueError("Refused or unsupported Responses content.")
        _text(content["text"], empty=True)
        _empty(content["annotations"])
        _empty(content.get("logprobs"))


def _responses(body):
    _shape(body, ("output", "status"), (
        "id", "access_programs", "created_at", "error", "incomplete_details", "instructions", "metadata", "model", "object",
        "parallel_tool_calls", "temperature", "tool_choice", "tools", "top_p", "background", "completed_at", "conversation",
        "max_output_tokens", "max_tool_calls", "moderation", "previous_response_id", "prompt", "prompt_cache_diagnostics",
        "prompt_cache_key", "prompt_cache_options", "prompt_cache_retention", "reasoning", "safety_identifier", "service_tier",
        "text", "top_logprobs", "truncation", "usage", "user", "store", "presence_penalty", "frequency_penalty"))
    # Ollama's Responses endpoint reports these settings as metadata. They do
    # not authorize extra calls: the actual output still passes _one below.
    for key in ("store", "parallel_tool_calls"):
        if key in body and type(body[key]) is not bool:
            raise ValueError("Expected boolean Responses metadata.")
    for key in ("presence_penalty", "frequency_penalty"):
        if key in body and type(body[key]) not in (int, float):
            raise ValueError("Expected numeric Responses penalty metadata.")
    if (body["status"] != "completed" or body.get("object", "response") != "response"
            or any(body.get(key) is not None for key in ("error", "incomplete_details", "moderation"))):
        raise ValueError("Responses result is incomplete or unsupported.")
    calls = []
    for item in _items(body["output"]):
        if type(item) is not dict:
            raise ValueError("Unsupported Responses output item.")
        kind = item.get("type")
        if kind == "function_call":
            _shape(item, ("type", "name", "arguments", "call_id"), ("id", "status", "async", "caller", "namespace"))
            _text(item["call_id"])
            if item.get("id") is not None:
                _text(item["id"])
            _completed(item.get("status"))
            _direct(item)
            calls.append(_call(item["name"], item["arguments"], raw=True))
        elif kind == "reasoning":
            _reasoning(item)
        elif kind == "message":
            _response_message(item)
        else:
            raise ValueError("Unsupported or mixed Responses action type.")
    return _one(calls)


def _anthropic(body):
    _shape(body, ("type", "role", "stop_reason", "content"), ("id", "container", "diagnostics", "model", "stop_sequence", "stop_details", "usage"))
    if (body["type"] != "message" or body["role"] != "assistant" or body["stop_reason"] != "tool_use"
            or body.get("stop_sequence") is not None or body.get("stop_details") is not None):
        raise ValueError("Anthropic response did not finish with a client tool.")
    calls = []
    for item in _items(body["content"]):
        if type(item) is not dict:
            raise ValueError("Unsupported Anthropic content.")
        kind = item.get("type")
        if kind == "tool_use":
            _shape(item, ("type", "id", "name", "input"), ("caller", "toolset_name"))
            _text(item["id"])
            _direct(item)
            calls.append(_call(item["name"], item["input"], raw=False))
        elif kind == "text":
            _shape(item, ("type", "text"), ("citations",))
            _text(item["text"], empty=True)
            _empty(item.get("citations"))
        elif kind == "thinking":
            _shape(item, ("type", "thinking", "signature"))
            _text(item["thinking"], empty=True)
            _text(item["signature"])
        elif kind == "redacted_thinking":
            _shape(item, ("type", "data"))
            _text(item["data"])
        else:
            raise ValueError("Unsupported or mixed Anthropic action type.")
    return _one(calls)


def _ollama(body):
    _shape(body, ("done", "message"), ("model", "created_at", "done_reason", "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration", "logprobs"))
    if body["done"] is not True or body.get("done_reason") not in (None, "stop"):
        raise ValueError("Ollama response is incomplete.")
    msg = _shape(body["message"], ("role", "tool_calls"), ("content", "thinking", "images", "tool_name"))
    if msg["role"] != "assistant" or msg.get("tool_name") is not None:
        raise ValueError("Unsupported Ollama message.")
    for key in ("content", "thinking"):
        if msg.get(key) is not None:
            _text(msg[key], empty=True)
    _empty(msg.get("images"))
    call = _shape(_one(_items(msg["tool_calls"])), ("function",))
    function = _shape(call["function"], ("name", "arguments"))
    return _call(function["name"], function["arguments"], raw=False)


class ActionDecoder:
    """Decode exactly one supported completed provider call, without executing it."""

    def __init__(self, provider):
        provider_capabilities(provider)
        self._provider = provider

    @property
    def provider(self):
        return self._provider

    def __call__(self, response):
        try:
            if self._provider == "ollama":
                base = getattr(sys.modules.get("ollama._types"), "SubscriptableBaseModel", None)
                if type(response) is dict:
                    body = clone(response)
                elif isinstance(base, type) and isinstance(response, base):
                    body = clone(response.model_dump(mode="json", by_alias=True, exclude_unset=True))
                else:
                    raise TypeError("Expected an Ollama JSON dictionary or genuine loaded SDK response.")
            else:
                body = native_json(response, sdk="anthropic" if self._provider == "anthropic" else "openai")
            if len(encode(body)) > _MAX_ENVELOPE_CHARS:
                raise ValueError("Provider envelope exceeds the character limit.")
            return {"openai_chat": _chat, "openai_responses": _responses,
                    "anthropic": _anthropic, "ollama": _ollama}[self._provider](body)
        except Exception as exc:
            raise ActionProposalError(response) from exc


class NativeActionHarness(_ActionHarnessBase):
    """Dispatch one native provider proposal under the turn captured before it.

    Native histories are not reduced to text or rewritten. Every tool parameter
    needs an explicit contract or unchecked declaration; no automatic-memory
    field names are inferred. The host supplies schemas and replays native items.
    """

    def __init__(self, agent, tools: dict, *, contracts: dict | None = None,
                 decoder=None, repair=None, resume=False):
        if not isinstance(agent, (NativeConnection, AsyncNativeConnection)):
            raise TypeError("NativeActionHarness requires a native Relay connection.")
        agent._require_open()
        if agent._protocol not in _NATIVE_PROVIDERS:
            raise ValueError("No native action adapter exists for this protocol.")
        decoder = ActionDecoder(_NATIVE_PROVIDERS[agent._protocol]) if decoder is None else decoder
        self._initialize(agent, tools, contracts=contracts, decoder=decoder, repair=repair, resume=resume, memory=False)
        self._async = isinstance(agent, AsyncNativeConnection)

    def invoke(self, **request):
        """Generate and dispatch once; await the return for an async connection."""
        self._agent._require_open()
        if "stream" in request and request["stream"] is not False:
            raise ValueError("NativeActionHarness requires complete proposals with stream=False.")
        if self._async:
            return self._invoke_async(request)
        turn = self._agent.guard.turn()
        return self._finish(self._agent.invoke_result(**request), turn)

    async def _invoke_async(self, request):
        self._agent._require_open()
        turn = self._agent.guard.turn()
        return self._finish(await self._agent.invoke_result(**request), turn)
