"""Reversible, instance-only attachment to an existing model client.

Importing this module does not import a provider, start a service, or change any
client. ``attach`` deliberately requires an existing instance and one explicit
conversation. Provider responses, including streams, remain provider-owned.
"""

from __future__ import annotations

import functools
import inspect
import sys
import threading
import types
import weakref
from pathlib import Path
from typing import Any

from .budget import TokenBudget
from .config import RelayConfig
from .connection import AsyncConnectedAgent, ConnectedAgent, connect
from .middleware import ContextGuard


_MISSING = object()
_LOCK = threading.RLock()
_ACTIVE: weakref.WeakValueDictionary = weakref.WeakValueDictionary()
_CALL_KEY = "_relay_bound_arguments"


def _ollama_instance(client: Any) -> bool:
    """Identify classes already loaded by the application's optional SDK.

    A class merely named Client is insufficient. Identity comes from the
    provider's loaded defining module; importing Relay never imports Ollama.
    """
    module = sys.modules.get("ollama._client")
    if module is None:
        return False
    for name in ("Client", "AsyncClient"):
        candidate = getattr(module, name, None)
        if (isinstance(candidate, type) and candidate.__module__ == "ollama._client"
                and isinstance(client, candidate)):
            return True
    return False


def _instance(value: Any) -> None:
    if isinstance(value, (type, types.ModuleType)):
        raise TypeError("Attach to one client instance, never a module or class.")


def _target(client: Any, method: str) -> tuple[Any, str, dict, Any]:
    _instance(client)
    if (type(method) is not str or not method or
            any(not part.isidentifier() or part.startswith("_") for part in method.split("."))):
        raise ValueError("method must be a public dotted attribute path, such as 'chat' or 'api.complete'.")
    parts = method.split(".")
    owner = client
    for part in parts[:-1]:
        owner = getattr(owner, part)
        _instance(owner)
    name = parts[-1]
    original = getattr(owner, name)
    if not callable(original):
        raise TypeError("The selected client attribute must be callable.")
    # Restrict mutation to normal writable instance dictionaries. Properties,
    # frozen objects and custom setters cannot be safely patched/restored without
    # invoking arbitrary setter behavior. Reject them before creating a store.
    # Python 3.10 exposes SimpleNamespace's own wrappers for ordinary attribute
    # access; newer versions reuse object's. Subclass overrides remain unsafe.
    baseline = types.SimpleNamespace if isinstance(owner, types.SimpleNamespace) else object
    for attribute in ("__getattribute__", "__setattr__", "__delattr__"):
        implementation = getattr(type(owner), attribute)
        if (implementation is not getattr(object, attribute) and
                implementation is not getattr(baseline, attribute)):
            raise TypeError("This client has custom attribute access; use connect(client.method) instead.")
    try:
        namespace = vars(owner)
    except TypeError as exc:
        raise TypeError("This client has no writable instance dictionary; use connect instead.") from exc
    if type(namespace) is not dict:
        raise TypeError("This client has no ordinary writable instance dictionary; use connect instead.")
    descriptor = inspect.getattr_static(type(owner), name, _MISSING)
    if descriptor is not _MISSING and inspect.isdatadescriptor(descriptor):
        raise TypeError("Descriptor-backed client methods cannot be attached; use connect instead.")
    return owner, name, namespace, original


def _settings(config, *, ollama: bool):
    if config is None:
        return RelayConfig(protocol="ollama" if ollama else "plain")
    if isinstance(config, RelayConfig):
        settings = config
    elif isinstance(config, (str, Path)):
        settings = RelayConfig.from_file(config)
    else:
        raise TypeError("config must be a RelayConfig, a JSON file path, or None.")
    if ollama and settings.protocol != "ollama":
        raise ValueError("An Ollama chat attachment requires RelayConfig(protocol='ollama').")
    return settings


def _message_argument(signature: inspect.Signature) -> tuple[str, bool]:
    parameter = signature.parameters.get("messages")
    if parameter is not None and parameter.kind not in (
            inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
        return "messages", False
    for name, parameter in signature.parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return name, True
    raise TypeError("The selected method must accept messages, or keyword arguments containing messages.")


def _history(value, *, ollama: bool):
    if not ollama:
        return value
    # Match the SDK's common Sequence[Mapping | Message] input at this boundary.
    # Do not consume arbitrary iterators or invoke model_dump on unknown objects.
    if type(value) not in (list, tuple):
        raise ValueError("Ollama attachment messages must be a nonempty list or tuple of dictionaries/Message objects.")
    module = sys.modules.get("ollama._types")
    message_type = getattr(module, "Message", None) if module else None
    valid_type = (isinstance(message_type, type) and message_type.__module__ == "ollama._types")
    return [entry.model_dump(mode="json", exclude_none=True)
            if valid_type and isinstance(entry, message_type) else entry for entry in value]


def _ollama_text_response(response):
    field = response.get if isinstance(response, dict) else lambda key, default=None: getattr(response, key, default)
    if field("done", True) is not True or field("done_reason") not in (None, "stop"):
        raise ValueError("Managed Ollama history requires a finished, nontruncated response.")
    message = response.get("message") if isinstance(response, dict) else getattr(response, "message", None)
    message = _history([message], ollama=True)[0]
    if (type(message) is not dict or set(message) != {"role", "content"}
            or message["role"] != "assistant" or type(message["content"]) is not str):
        raise ValueError("Managed Ollama history requires complete text; tool/thinking/image output needs a native history adapter.")
    return message["content"]


class RelayAttachment:
    """The handle for a single attached client method and conversation.

    Keep this handle for the lifetime of the conversation. ``detach`` restores
    the original instance attribute (or inherited-method lookup), closes only a
    Relay-owned store, and leaves the provider client and streams open.
    """

    def __init__(self, owner, name, original_value, agent, wrapper):
        self._owner = owner
        self._name = name
        self._original_value = original_value
        self._agent = agent
        self._wrapper = wrapper
        self._closed = False

    @property
    def agent(self) -> ConnectedAgent | AsyncConnectedAgent:
        """The connected agent for memory, explicit facts, and advanced calls."""
        return self._agent

    @property
    def session_id(self) -> str:
        return self.agent.session_id

    @property
    def memory(self):
        """The agent's optional automatic-memory controller, or None."""
        return self.agent.memory

    @property
    def attached(self) -> bool:
        return not self._closed and vars(self._owner).get(self._name, _MISSING) is self._wrapper

    def detach(self) -> bool:
        """Restore if still attached; never overwrite a later host replacement.

        Returns True only when this call restored the original attribute. Call
        after outstanding requests finish. A previously captured original bound
        method bypasses attachment, and a captured Relay wrapper is closed here.
        """
        with _LOCK:
            if self._closed:
                return False
            restored = self.attached
            if restored:
                namespace = vars(self._owner)
                if self._original_value is _MISSING:
                    del namespace[self._name]
                else:
                    namespace[self._name] = self._original_value
            self._closed = True
            key = (id(self._owner), self._name)
            if _ACTIVE.get(key) is self:
                del _ACTIVE[key]
            self.agent.close()
            return restored

    def close(self) -> None:
        self.detach()

    def __enter__(self):
        if self._closed:
            raise RuntimeError("This Relay attachment is closed.")
        if not self.attached:
            raise RuntimeError("The attached client method was replaced; close this attachment first.")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.close()


def attach(client, *, method: str | None = None,
           config: RelayConfig | str | Path | None = None,
           guard: ContextGuard | None = None, session_id: str | None = None,
           token_budget: TokenBudget | None = None, correction_fields=(), memory=None,
           memory_interpreter=None, output=None) -> RelayAttachment:
    """Attach once so existing ``client.chat(...)`` calls prepare context.

    Known Ollama Client/AsyncClient instances select ``chat`` and the native
    Ollama protocol automatically. Other clients require an explicit dotted
    method path. Only that instance is changed; no global SDK is patched.
    One attachment represents one conversation, never a shared multi-user client.
    The application continues to retain and supply the full conversation history.
    """
    _instance(client)
    detected = _ollama_instance(client)
    if method is None:
        if not detected:
            raise ValueError("Unknown client type. Supply method='chat' (or another explicit method path).")
        method = "chat"
    is_ollama = detected and method == "chat"
    owner, name, namespace, original = _target(client, method)
    try:
        signature = inspect.signature(original)
    except (ValueError, TypeError) as exc:
        raise TypeError("Attachment needs an inspectable method signature; use connect with an explicit adapter.") from exc
    message_name, message_in_kwargs = _message_argument(signature)
    if (isinstance(original, (ConnectedAgent, AsyncConnectedAgent)) or
            isinstance(getattr(original, "__self__", None), (ConnectedAgent, AsyncConnectedAgent))):
        raise ValueError("This method is already connected to Relay.")
    if any(inspect.isasyncgenfunction(candidate)
           for candidate in (original, getattr(original, "__call__", None))):
        raise TypeError("Direct async-generator methods need an explicit async adapter through connect.")
    is_async = inspect.iscoroutinefunction(original) or inspect.iscoroutinefunction(getattr(original, "__call__", None))
    settings = _settings(config, ollama=is_ollama)

    def invoke_original(messages, kwargs):
        bound = kwargs.pop(_CALL_KEY, None)
        if bound is None:
            return original(messages=messages, **kwargs)
        # This BoundArguments belongs only to this invocation; concurrent calls
        # never share a mutable argument slot or latest-prepared-turn variable.
        if message_in_kwargs:
            bound.arguments[message_name] = {**bound.arguments.get(message_name, {}), "messages": messages}
        else:
            bound.arguments[message_name] = messages
        return original(*bound.args, **bound.kwargs)

    if is_async:
        async def transport(*, messages, **kwargs):
            return await invoke_original(messages, kwargs)
    else:
        def transport(*, messages, **kwargs):
            return invoke_original(messages, kwargs)

    def bind_call(args, kwargs):
        bound = signature.bind(*args, **kwargs)
        if agent.output is not None:
            # Check explicit and default stream/tool options before a provider
            # call, including methods whose arguments are hidden by attachment.
            check_bound = signature.bind(*args, **kwargs)
            check_bound.apply_defaults()
            options = dict(check_bound.arguments)
            for parameter in signature.parameters.values():
                if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                    options.update(check_bound.arguments.get(parameter.name, {}))
            agent.output.validate_options(options)
        if message_in_kwargs:
            messages = bound.arguments.get(message_name, {}).get("messages", _MISSING)
        else:
            messages = bound.arguments.get(message_name, _MISSING)
        if messages is _MISSING or messages is None:
            raise ValueError("Supply the complete conversation in messages on each attached call.")
        return bound, _history(messages, ollama=is_ollama)

    with _LOCK:
        key = (id(owner), name)
        if _ACTIVE.get(key) is not None or getattr(original, "__relay_attachment__", False):
            raise ValueError("This instance method is already attached to Relay; detach it first.")
        # Everything which could reject the patch itself has now been checked.
        agent = connect(transport, config=settings, guard=guard, session_id=session_id,
                        token_budget=token_budget, correction_fields=correction_fields, memory=memory,
                        memory_interpreter=memory_interpreter, output=output)
        if is_ollama:
            agent._response_decoder = _ollama_text_response
        if is_async:
            @functools.wraps(original)
            async def wrapper(*args, **kwargs):
                bound, messages = bind_call(args, kwargs)
                return await agent(messages=messages, **{_CALL_KEY: bound})
        else:
            @functools.wraps(original)
            def wrapper(*args, **kwargs):
                bound, messages = bind_call(args, kwargs)
                return agent(messages=messages, **{_CALL_KEY: bound})
        wrapper.__signature__ = signature
        wrapper.__relay_attachment__ = True
        handle = RelayAttachment(owner, name, namespace.get(name, _MISSING), agent, wrapper)
        namespace[name] = wrapper
        _ACTIVE[key] = handle
        return handle
