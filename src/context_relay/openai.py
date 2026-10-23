"""Optional OpenAI adapters; no SDK import, credentials, network, or model defaults."""

from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

from .budget import TokenBudget
from .config import RelayConfig
from .context import ContextPlan, _compile_grouped_context
from .models import clone, encode
from .protocols import _text


def _validated_openai_chat_messages(messages: object) -> tuple[list[dict], list[int], list[list[int]]]:
    """Validate complete text/function-tool turns without rewriting provider data."""
    if type(messages) is not list or not messages:
        raise ValueError("messages must be a nonempty list of OpenAI message dictionaries.")
    history = clone(messages)
    prefix, turns = [], []
    pending, seen = set(), set()
    for index, message in enumerate(history):
        if type(message) is not dict:
            raise ValueError(f"Message {index} must be a dictionary, not an SDK object.")
        role = message.get("role")
        if type(role) is not str or role not in {"system", "developer", "user", "assistant", "tool"}:
            raise ValueError(f"Message {index} has an unsupported OpenAI Chat role.")
        allowed = {"role", "content", "tool_call_id"} if role == "tool" else {"role", "content", "name"}
        if role == "assistant":
            allowed |= {"tool_calls", "refusal"}
        if not set(message) <= allowed:
            raise ValueError(f"Message {index} contains unsupported OpenAI fields; only text and function tools are supported.")
        if "name" in message:
            _text(message["name"], "Message name", nonempty=True)
        content = message.get("content")
        if content is not None or role != "assistant":
            _text(content, f"Message {index} content")
        if "refusal" in message and message["refusal"] is not None:
            _text(message["refusal"], "Assistant refusal", nonempty=True)
        calls = message.get("tool_calls")
        if calls is not None and (type(calls) is not list or not calls):
            raise ValueError("tool_calls must be a nonempty list or None.")
        if role == "assistant" and content is None and not calls and not message.get("refusal"):
            raise ValueError("An assistant message needs text, a refusal, or complete function calls.")
        if pending and role != "tool":
            raise ValueError("Every function-call batch requires all results before the next message.")
        if role in {"system", "developer"}:
            if turns:
                raise ValueError("System/developer messages must remain in the initial prefix.")
            prefix.append(index)
        elif role == "user":
            turns.append([index])
        else:
            if not turns:
                raise ValueError("Conversation turns must start with a user message.")
            turns[-1].append(index)
            if role == "tool":
                call_id = message.get("tool_call_id")
                _text(call_id, "Tool result tool_call_id", nonempty=True)
                if call_id not in pending:
                    raise ValueError("Tool result has no outstanding matching call ID.")
                pending.remove(call_id)
            else:
                for call in calls or ():
                    if type(call) is not dict or set(call) != {"id", "type", "function"} or call["type"] != "function":
                        raise ValueError("Only complete function tool calls with id/type/function are supported.")
                    _text(call["id"], "Function call ID", nonempty=True)
                    function = call["function"]
                    if type(function) is not dict or set(function) != {"name", "arguments"}:
                        raise ValueError("Function calls need exactly name and arguments.")
                    _text(function["name"], "Function name", nonempty=True)
                    # The provider emits a JSON string, which can itself be invalid
                    # JSON. Retain it exactly; validation/execution belongs to the host.
                    _text(function["arguments"], "OpenAI function arguments")
                    if call["id"] in seen:
                        raise ValueError("Function call IDs must be unique within the transcript.")
                    pending.add(call["id"])
                    seen.add(call["id"])
    if pending:
        raise ValueError("The final function-call batch is incomplete; append all tool results first.")
    if history[-1]["role"] not in {"user", "tool"}:
        raise ValueError("The final message must be a pending user request or a completed tool-result batch.")
    return history, prefix, turns


def compile_openai_chat_context(
    messages: list[dict], *, state: dict | None = None, max_chars: int = 16000,
    recent_turns: int = 2, pinned: tuple[int, ...] | list[int] = (),
    retrieve_turns: int = 0, query: str | None = None,
    token_budget: TokenBudget | None = None,
) -> ContextPlan:
    """Use the shared compiler, preserving complete native text/function turns."""
    history, prefix, turns = _validated_openai_chat_messages(messages)
    plan = _compile_grouped_context(
        history, prefix, turns, data_before_index=turns[-1][0], state=state,
        max_chars=max_chars, recent_turns=recent_turns, pinned=pinned,
        retrieve_turns=retrieve_turns,
        query=history[turns[-1][0]]["content"] if query is None else query,
        token_budget=token_budget,
        search_texts=[(message.get("content") or "") + " " + encode(message.get("tool_calls"))
                      for message in history],
    )
    report = plan.report
    report.update(protocol="openai_chat", tool_protocol_preserved=True, data_before_index=turns[-1][0])
    return ContextPlan(plan.messages, report)


def _validate_options(api: str, options: dict) -> None:
    """Reject features that could replace the prepared input or add unseen state."""
    if options.get("extra_body"):
        raise ValueError("OpenAI extra_body is unsupported because it can override Relay's prepared input.")
    if "input" in options:
        raise ValueError("Pass the full text history as messages=; Relay supplies Responses input=.")
    for key in ("previous_response_id", "conversation", "prompt"):
        if options.get(key) is not None:
            raise ValueError(f"{key} can add unseen provider context; supply the complete history to Relay instead.")
    if options.get("context_management") or options.get("truncation") not in (None, "disabled"):
        raise ValueError("Provider compaction/truncation is unsupported; Relay must control the selected context.")
    if api == "responses":
        for key in ("tools", "tool_choice", "parallel_tool_calls"):
            if key in options and options[key] not in (None, [], "none", False):
                raise ValueError("The Responses adapter currently supports plain text, not tool/reasoning-item histories.")
    else:
        if options.get("functions") or options.get("function_call"):
            raise ValueError("Legacy function_call history is unsupported; use function tools with tool_call_id.")
        tools = options.get("tools")
        if tools is not None and (type(tools) not in (list, tuple)
                                  or any(type(tool) is not dict or tool.get("type") != "function" for tool in tools)):
            raise ValueError("OpenAI Chat tools must be a list or tuple of function-tool definitions.")


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _decode_chat_text(response) -> str:
    choices = _get(response, "choices")
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        raise ValueError("Managed OpenAI history requires exactly one completed text choice.")
    choice = choices[0]
    message = _get(choice, "message")
    if (_get(choice, "finish_reason") != "stop" or _get(message, "role") != "assistant"
            or any(_get(message, key) for key in ("tool_calls", "function_call", "refusal", "audio", "annotations"))):
        raise ValueError("Managed OpenAI history cannot reduce tool, refusal, annotated, audio, or unfinished responses to text.")
    result = _get(message, "content")
    _text(result, "Completed assistant text", nonempty=True)
    return result


def _decode_responses_text(response) -> str:
    output = _get(response, "output")
    if _get(response, "status") != "completed" or not isinstance(output, (list, tuple)) or not output:
        raise ValueError("Managed OpenAI history requires a completed text response.")
    parts = []
    for item in output:
        if (_get(item, "type") != "message" or _get(item, "role") != "assistant"
                or _get(item, "status") != "completed"):
            raise ValueError("Native reasoning/tool items require a Responses item adapter; nothing was flattened.")
        content = _get(item, "content")
        if not isinstance(content, (list, tuple)) or not content:
            raise ValueError("Responses messages need completed output_text content.")
        for part in content:
            if _get(part, "type") != "output_text" or _get(part, "annotations"):
                raise ValueError("Only unannotated output_text can be appended to managed plain-text history.")
            value = _get(part, "text")
            _text(value, "Response output text")
            parts.append(value)
    result = "".join(parts)
    _text(result, "Completed response text", nonempty=True)
    return result


def connect_openai(client, *, api: str = "chat", config: RelayConfig | str | Path | None = None,
                   guard=None, session_id: str | None = None, token_budget: TokenBudget | None = None,
                   correction_fields=(), memory=None, memory_interpreter=None, output=None,
                   native: bool = False, native_memory: bool = False):
    """Connect an existing OpenAI/AsyncOpenAI client without importing its SDK.

    Legacy APIs accept ``messages=full_history`` on the returned Relay connection.
    Chat supports text and native function-tool messages. Responses maps a plain
    text history to ``input=``; native items and hidden server conversations are
    deliberately unsupported. Credentials, chosen model, response processing,
    ``api='responses', native=True`` instead accepts ``input=full_native_history``
    and preserves the entire native request including encrypted reasoning and
    compaction items. It validates visible function-call dependencies and exact
    request budgets, with no history pruning. ``native_memory=True`` projects
    trusted remember() facts into instructions without editing input items.
    Streams and provider lifetime remain owned by the caller. No request is
    made during connection, and provider responses are returned unchanged.
    """
    from .connection import _Connection, connect

    if api not in ("chat", "responses"):
        raise ValueError("api must be 'chat' or 'responses'.")
    if type(native) is not bool or type(native_memory) is not bool:
        raise TypeError("native and native_memory must be booleans.")
    if native_memory and not native:
        raise ValueError("native_memory=True requires native=True.")
    if native:
        if api != "responses":
            raise ValueError("native=True requires api='responses'.")
        from .native import connect_native
        from .native_openai import prepare_openai_responses

        try:
            provider = client.responses.create
        except AttributeError as exc:
            raise TypeError("client must expose responses.create().") from exc
        return connect_native(
            provider, prepare_request=prepare_openai_responses, protocol="openai_responses_native",
            config=config, guard=guard, session_id=session_id, token_budget=token_budget,
            correction_fields=correction_fields, memory=memory,
            memory_interpreter=memory_interpreter, output=output,
            native_memory=native_memory,
        )
    if config is None:
        settings = RelayConfig()
    elif isinstance(config, RelayConfig):
        settings = config
    elif isinstance(config, (str, Path)):
        settings = RelayConfig.from_file(config)
    else:
        raise TypeError("config must be a RelayConfig, JSON file path, or None.")
    protocol = "openai_chat" if api == "chat" else "plain"
    if settings.protocol not in {"plain", protocol}:
        raise ValueError(f"This OpenAI adapter requires protocol={protocol!r}.")
    settings = replace(settings, protocol=protocol)
    try:
        provider = client.chat.completions.create if api == "chat" else client.responses.create
    except AttributeError as exc:
        raise TypeError(f"client must expose {'chat.completions' if api == 'chat' else 'responses'}.create().") from exc
    if not callable(provider):
        raise TypeError("The selected OpenAI create attribute must be callable.")
    if isinstance(provider, _Connection) or isinstance(getattr(provider, "__self__", None), _Connection):
        raise ValueError("The selected OpenAI method is already connected to Relay.")
    candidates = tuple(inspect.unwrap(candidate) for candidate in (provider, getattr(provider, "__call__", None)))
    if any(inspect.isasyncgenfunction(candidate) for candidate in candidates):
        raise TypeError("Direct async-generator providers need an explicit async adapter.")
    parameter = "messages" if api == "chat" else "input"

    def request(messages, options):
        _validate_options(api, options)
        return provider(**{parameter: messages}, **options)

    if any(inspect.iscoroutinefunction(candidate) for candidate in candidates):
        async def call(*, messages, **options):
            return await request(messages, options)
    else:
        def call(*, messages, **options):
            return request(messages, options)

    agent = connect(call, config=settings, guard=guard, session_id=session_id,
                    token_budget=token_budget, correction_fields=correction_fields, memory=memory,
                    memory_interpreter=memory_interpreter, output=output)
    agent._response_decoder = _decode_chat_text if api == "chat" else _decode_responses_text
    return agent
