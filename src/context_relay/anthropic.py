"""Optional Anthropic text/native adapters; no keys, network, or model defaults."""

from __future__ import annotations

import inspect
from pathlib import Path

from .config import RelayConfig
from .protocols import _text


_OPTIONS = frozenset({
    "model", "max_tokens", "metadata", "stop_sequences", "stream", "temperature",
    "top_p", "top_k", "service_tier", "timeout", "extra_headers", "extra_query",
    "extra_body", "thinking",
})


def _request_input(messages: list[dict], options: dict) -> dict:
    if "system" in options:
        raise ValueError("Put system instructions in the initial messages prefix or conversation(system=...); Relay must budget them.")
    unsupported = options.keys() - _OPTIONS
    if unsupported:
        raise ValueError(f"Unsupported Anthropic text options: {', '.join(sorted(unsupported))}.")
    if options.get("extra_body") or options.get("extra_query"):
        raise ValueError("Extra request fields could override Relay's prepared input and are unsupported.")
    if "thinking" in options and options["thinking"] != {"type": "disabled"}:
        raise ValueError("The text adapter accepts only thinking={'type': 'disabled'}; native thinking needs its own history adapter.")
    system, history = [], []
    for message in messages:
        if message["role"] == "system":
            _text(message["content"], "System instructions", nonempty=True)
            system.append({"type": "text", "text": message["content"]})
        elif message["role"] == "developer":
            raise ValueError("The Anthropic text adapter does not reinterpret developer roles; use an explicit system prefix.")
        else:
            history.append(message)
    return {"messages": history, **({"system": system} if system else {})}


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _decode_anthropic_text(response) -> str:
    """Append only complete text; never flatten thinking, tools, or citations."""
    content = _get(response, "content")
    if (_get(response, "type") != "message" or _get(response, "role") != "assistant"
            or _get(response, "stop_reason") != "end_turn"
            or _get(response, "stop_sequence") is not None or _get(response, "stop_details")
            or not isinstance(content, (list, tuple)) or not content):
        raise ValueError("Managed Anthropic history requires a completed assistant text message with stop_reason='end_turn'.")
    parts = []
    for block in content:
        if (_get(block, "type") != "text" or _get(block, "citations") or _get(block, "annotations")
                or isinstance(block, dict) and not set(block) <= {"type", "text", "citations"}):
            raise ValueError("Thinking, tools, citations, and other native blocks cannot be reduced to managed plain-text history.")
        value = _get(block, "text")
        _text(value, "Response text")
        parts.append(value)
    result = "".join(parts)
    _text(result, "Completed response text", nonempty=True)
    return result


def connect_anthropic(client, *, native: bool = False, beta: bool = False, native_memory: bool = False,
                      config: RelayConfig | str | Path | None = None,
                      guard=None, session_id: str | None = None, token_budget=None,
                      correction_fields=(), memory=None, memory_interpreter=None, output=None):
    """Connect an existing Anthropic/AsyncAnthropic client's messages.create.

    By default supply plain role/content dictionaries, including initial system messages.
    The shared compiler budgets all text before this adapter maps those system
    messages to the provider's top-level system blocks. A separate system=
    request option is rejected. Native tools, thinking, multimodal blocks, and
    hidden provider context require another adapter; nothing is flattened.

    With ``native=True``, preserve complete native Messages input without
    selection. ``native_memory=True`` appends trusted remember() facts in a new
    inline system message on models supporting that format. Replay the prepared
    messages on later calls; signed history and top-level system are never edited.
    ``beta=True`` selects the beta
    Messages endpoint for provider context management and compaction options.
    The native connection provides explicit request preparation and state
    snapshots; managed text conversations and automatic output checks are not
    compatible with opaque native history.

    The caller supplies model/max_tokens and owns the client, credentials,
    responses, and streams. Ordinary calls return the original object without
    iteration, retry, or inspection. Managed conversations have a strict text
    decoder. No request or SDK import happens while connecting.
    """
    from .connection import _Connection, connect

    if type(native) is not bool or type(beta) is not bool or type(native_memory) is not bool:
        raise TypeError("native, beta and native_memory must be booleans.")
    if native_memory and not native:
        raise ValueError("native_memory=True requires native=True.")
    if beta and not native:
        raise ValueError("beta=True requires native=True; the text adapter has a smaller explicit scope.")
    if config is None:
        settings = RelayConfig()
    elif isinstance(config, RelayConfig):
        settings = config
    elif isinstance(config, (str, Path)):
        settings = RelayConfig.from_file(config)
    else:
        raise TypeError("config must be a RelayConfig, JSON file path, or None.")
    if settings.protocol != "plain":
        raise ValueError("The Anthropic text adapter requires protocol='plain'.")
    try:
        provider = client.beta.messages.create if beta else client.messages.create
    except AttributeError as exc:
        raise TypeError(f"client must expose {'beta.' if beta else ''}messages.create().") from exc
    if not callable(provider):
        raise TypeError("client.messages.create must be callable.")
    if isinstance(provider, _Connection) or isinstance(getattr(provider, "__self__", None), _Connection):
        raise ValueError("This Anthropic method is already connected to Relay.")
    if native:
        from .native import connect_native
        from .native_anthropic import prepare_anthropic_request
        return connect_native(
            provider, prepare_request=prepare_anthropic_request, protocol="anthropic_messages",
            config=settings, guard=guard, session_id=session_id, token_budget=token_budget,
            correction_fields=correction_fields, memory=memory, memory_interpreter=memory_interpreter,
            output=output, native_memory=native_memory)
    candidates = tuple(inspect.unwrap(item) for item in (provider, getattr(provider, "__call__", None)))
    if any(inspect.isasyncgenfunction(item) for item in candidates):
        raise TypeError("Direct async-generator providers need an explicit async adapter.")

    def request(messages, options):
        return provider(**_request_input(messages, options), **options)

    if any(inspect.iscoroutinefunction(item) for item in candidates):
        async def call(*, messages, **options):
            return await request(messages, options)
    else:
        def call(*, messages, **options):
            return request(messages, options)

    agent = connect(call, config=settings, guard=guard, session_id=session_id,
                    token_budget=token_budget, correction_fields=correction_fields, memory=memory,
                    memory_interpreter=memory_interpreter, output=output)
    agent._response_decoder = _decode_anthropic_text
    return agent
