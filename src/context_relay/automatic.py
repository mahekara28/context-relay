"""One setup entry point; provider detection never imports or contacts an SDK."""

from __future__ import annotations

import functools
import inspect
import sys
import types
from dataclasses import replace
from pathlib import Path

from .anthropic import connect_anthropic
from .attachment import _ollama_text_response
from .config import RelayConfig
from .connection import _Connection, connect
from .memory import MemoryPolicy
from .native import NativeConnection
from .openai import connect_openai


_CLIENTS = {
    "openai": ("OpenAI", "AsyncOpenAI"),
    "anthropic": ("Anthropic", "AsyncAnthropic"),
    "ollama": ("Client", "AsyncClient"),
}
_DEFAULT_MEMORY = object()


def _provider(client):
    matches = set()
    for provider, names in _CLIENTS.items():
        modules = (provider, provider + "._client")
        for module_name in modules:
            module = sys.modules.get(module_name)
            if module is None:
                continue
            for name in names:
                candidate = vars(module).get(name)
                if (isinstance(candidate, type) and candidate.__module__ in modules
                        and isinstance(client, candidate)):
                    matches.add(provider)
    if len(matches) > 1:
        raise ValueError("Ambiguous SDK client type; supply an explicit adapter to connect().")
    return next(iter(matches), None)


def _unwrapped(call):
    def connected(value):
        return (isinstance(value, (_Connection, NativeConnection))
                or isinstance(getattr(value, "__self__", None), (_Connection, NativeConnection))
                or getattr(value, "__relay_attachment__", False))

    candidates = tuple(inspect.unwrap(item, stop=connected)
                       for item in (call, getattr(call, "__call__", None)))
    if connected(call) or any(connected(item) for item in candidates):
        raise ValueError("This callable is already connected or attached to Relay.")
    return candidates


def auto_connect(client, *, provider="auto", api=None, memory=_DEFAULT_MEMORY,
                 native=False, beta=False, native_memory=False,
                 config=None, guard=None, session_id=None, token_budget=None,
                 correction_fields=(), memory_interpreter=None, output=None,
                 conversation: str | None = None, storage: str | Path | None = None, namespace: str = "default"):
    """Recognise an existing client and enable basic memory in one setup call.

    Supports loaded Ollama, OpenAI and Anthropic SDK client instances, plus
    ordinary functions accepting plain ``messages``. Unknown compatible
    clients require explicit provider='openai'/'anthropic'/'ollama'; arbitrary
    callable objects/bound methods require provider='plain'. No class-name/attribute guessing,
    SDK imports, client patching, model discovery, or provider calls occur here.

    OpenAI selects Chat Completions by default; api='responses' explicitly selects
    its limited text adapter. Calls go through the returned connection. Existing
    direct client calls remain independent. Models and provider options are
    still supplied on calls, and provider responses retain their identity.

    Text mode defaults to memory=True: configured memory or a default MemoryPolicy; False disables
    it; a MemoryPolicy overrides configuration. Each connection owns one fresh
    conversation unless resumed with session_id and its database. Set
    conversation="name" to persist and resume automatically in storage (default
    .relay/sessions), optionally isolated by namespace. Named persistence cannot
    be combined with guard, session_id or config.db_path. Close it at
    conversation end; the existing provider client remains owned by the app.

    Native mode is explicit for OpenAI Responses or Anthropic Messages. It
    preserves the complete provider input and defaults to no automatic memory.
    Configured/enabled memory is rejected, never silently ignored; memory=False
    explicitly disables a configured policy. beta=True selects native Anthropic's
    beta Messages endpoint. Separately, native_memory=True projects trusted
    application remember() facts into native instructions; it does not extract
    user preferences. Claude requires replay of prepared.request['messages'].
    """
    if isinstance(client, (type, types.ModuleType)):
        raise TypeError("Supply an existing client instance or model function, not a class/module.")
    client_candidates = _unwrapped(client)
    if type(native) is not bool or type(beta) is not bool or type(native_memory) is not bool:
        raise TypeError("native, beta and native_memory must be booleans.")
    if native_memory and not native:
        raise ValueError("native_memory=True requires native=True.")
    if type(provider) is not str or provider not in {"auto", "openai", "anthropic", "ollama", "plain"}:
        raise ValueError("provider must be auto, openai, anthropic, ollama, or plain.")
    detected = _provider(client)
    if provider == "auto":
        provider = detected or ("plain" if inspect.isfunction(client) else None)
        if provider is None:
            raise TypeError("Unknown client. Set provider='openai', 'anthropic', 'ollama', or 'plain', or use connect() with an adapter.")
    elif detected is not None and detected != provider:
        raise ValueError(f"Detected {detected} client does not match provider={provider!r}.")
    if api is not None and (provider != "openai" or api not in ("chat", "responses")):
        raise ValueError("api is only supported for OpenAI: 'chat' or 'responses'.")
    if native and not (provider == "anthropic" or provider == "openai" and api == "responses"):
        raise ValueError("native=True requires Anthropic or OpenAI with api='responses'.")
    if beta and not (native and provider == "anthropic"):
        raise ValueError("beta=True requires native Anthropic Messages.")
    if memory is _DEFAULT_MEMORY:
        memory = None if native else True
    if type(memory) is not bool and not isinstance(memory, MemoryPolicy) and not (native and memory is None):
        raise TypeError("memory must be True, False, or a MemoryPolicy.")
    if native and (memory is True or isinstance(memory, MemoryPolicy)):
        raise ValueError("Native mode cannot inject automatic memory; use explicit remember()/memory_snapshot().")
    if config is None:
        settings = RelayConfig()
    elif isinstance(config, RelayConfig):
        settings = config
    elif isinstance(config, (str, Path)):
        settings = RelayConfig.from_file(config)
    else:
        raise TypeError("config must be a RelayConfig, JSON file path, or None.")
    policy = ((settings.memory or MemoryPolicy()) if memory is True else
              None if memory is False else settings.memory if memory is None else memory)
    settings = replace(settings, memory=policy)
    if conversation is None:
        if storage is not None or namespace != "default":
            raise ValueError("storage and namespace require a named conversation.")
    else:
        if guard is not None or session_id is not None:
            raise ValueError("Named conversations own their guard and session_id; omit those options.")
        from .persistence import named_connection_config
        settings, session_id = named_connection_config(
            settings, conversation, storage=storage, namespace=namespace)
    options = dict(config=settings, guard=guard, session_id=session_id, token_budget=token_budget,
                   correction_fields=correction_fields, memory_interpreter=memory_interpreter, output=output)
    if provider == "plain":
        if any(inspect.isasyncgenfunction(item) for item in client_candidates):
            raise TypeError("Direct async-generator providers need an explicit async adapter through connect().")
        if (any(inspect.iscoroutinefunction(item) for item in client_candidates)
                and not any(inspect.iscoroutinefunction(item)
                            for item in (client, getattr(client, "__call__", None)))):
            @functools.wraps(client)
            async def plain_call(*args, **kwargs):
                return await client(*args, **kwargs)
            return connect(plain_call, **options)
        return connect(client, **options)
    if provider == "openai":
        method = "responses.create" if api == "responses" else "chat.completions.create"
    else:
        method = ("beta.messages.create" if beta else "messages.create") if provider == "anthropic" else "chat"
    call = client
    try:
        for part in method.split("."):
            call = getattr(call, part)
    except AttributeError as exc:
        raise TypeError(f"The {provider} client must expose {method}().") from exc
    if not callable(call):
        raise TypeError(f"The {provider} client must expose callable {method}().")
    candidates = _unwrapped(call)
    if provider == "openai":
        return connect_openai(client, api=api or "chat", native=native, native_memory=native_memory, **options)
    if provider == "anthropic":
        return connect_anthropic(client, native=native, beta=beta, native_memory=native_memory, **options)
    if settings.protocol not in {"plain", "ollama"}:
        raise ValueError("Ollama requires protocol='ollama' or the default 'plain'.")
    options["config"] = replace(settings, protocol="ollama")
    if any(inspect.isasyncgenfunction(item) for item in candidates):
        raise TypeError("Direct async-generator providers need an explicit async adapter through connect().")
    if any(inspect.iscoroutinefunction(item) for item in candidates):
        async def transport(*, messages, **kwargs):
            return await call(messages=messages, **kwargs)
    else:
        def transport(*, messages, **kwargs):
            return call(messages=messages, **kwargs)
    agent = connect(transport, **options)
    agent._response_decoder = _ollama_text_response
    return agent
