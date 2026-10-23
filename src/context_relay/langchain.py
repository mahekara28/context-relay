"""Optional LangChain/LangGraph message integration, without global patches.

Framework imports occur only when requested. Canonical text/function messages
use Relay's existing protocol; selected framework messages retain their metadata.
"""

from __future__ import annotations

import inspect
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from .config import RelayConfig
from .connection import AgentResult, _reject_awaitable, connect
from .models import clone, encode, nonempty_string
from .openai import _validated_openai_chat_messages


def _argument_fingerprint(text):
    if type(text) is not str:
        raise ValueError("Raw tool arguments must be JSON text.")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Raw tool arguments contain duplicate keys.")
            result[key] = value
        return result

    parsed = clone(json.loads(text, object_pairs_hook=unique_pairs))
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _load_messages():
    try:
        from langchain_core import messages
    except ImportError as exc:
        raise ImportError("LangChain integration needs the optional langchain-core package already installed.") from exc
    return messages


def _load_runnable():
    try:
        from langchain_core.runnables import RunnableLambda
    except ImportError as exc:
        raise ImportError("as_runnable() needs the optional langchain-core package already installed.") from exc
    return RunnableLambda


def _normalise(messages, types):
    if type(messages) not in (list, tuple) or not messages:
        raise ValueError("Supply the complete history as a nonempty list/tuple of supported LangChain messages.")
    allowed_types = (types.SystemMessage, types.HumanMessage, types.AIMessage, types.ToolMessage, types.ChatMessage)
    canonical = []
    captured = []
    tool_names = {}
    for index, message in enumerate(messages):
        if type(message) not in allowed_types:
            raise TypeError(f"Message {index} must be a complete standard LangChain message; chunks/custom messages are unsupported.")
        # Preserve IDs, response/usage metadata, tool artifacts and status on the
        # selected framework input. Never mutate graph state or caller messages.
        message = deepcopy(message)
        row = message.model_dump()
        common = {"type", "content", "id", "name", "additional_kwargs", "response_metadata"}
        if type(message) is types.AIMessage:
            allowed = common | {"tool_calls", "invalid_tool_calls", "usage_metadata", "example"}
            role = "assistant"
        elif type(message) is types.ToolMessage:
            allowed = common | {"tool_call_id", "artifact", "status"}
            role = "tool"
        elif type(message) is types.ChatMessage:
            allowed = common | {"role"}
            role = row.get("role")
            if role not in {"system", "developer", "user", "assistant"}:
                raise ValueError("ChatMessage role must be system, developer, user, or assistant.")
        else:
            allowed = common | {"example"}
            role = "system" if type(message) is types.SystemMessage else "user"
        if set(row) - allowed:
            raise ValueError(f"Message {index} has unsupported extra fields: {', '.join(sorted(set(row) - allowed))}.")
        if row.get("example"):
            raise ValueError("Example-tagged messages need a dedicated adapter.")
        if row.get("invalid_tool_calls"):
            raise ValueError("Invalid or partial tool calls cannot be used as complete history.")
        content = row.get("content")
        calls = row.get("tool_calls", [])
        if type(calls) is not list:
            raise ValueError("tool_calls must be a list of complete standardized calls.")
        if type(content) is list and not content and calls:
            content = ""  # A tool-only AIMessage has no text to flatten.
        if type(content) is not str:
            raise ValueError("Only string text and empty tool-call content are supported; content blocks/images/reasoning are not flattened.")
        native = {"role": role, "content": content}
        name = row.get("name")
        if name is not None:
            nonempty_string(name, "message name")
            if role != "tool":
                native["name"] = name
        if calls:
            if type(calls) is not list or role != "assistant":
                raise ValueError("tool_calls must be complete AIMessage calls.")
            native_calls = []
            for call in calls:
                if (type(call) is not dict or not {"name", "args", "id"} <= set(call)
                        or set(call) - {"name", "args", "id", "type"}
                        or call.get("type", "tool_call") != "tool_call"):
                    raise ValueError("Each tool call needs name, args, id and optional type='tool_call'.")
                nonempty_string(call["id"], "tool call id")
                nonempty_string(call["name"], "tool name")
                if type(call["args"]) is not dict:
                    raise ValueError("Tool-call args must be a complete JSON dictionary.")
                native_calls.append({"id": call["id"], "type": "function",
                                     "function": {"name": call["name"], "arguments": encode(call["args"])}})
                tool_names[call["id"]] = call["name"]
            native["tool_calls"] = native_calls
        additional = row.get("additional_kwargs", {})
        if type(additional) is not dict:
            raise ValueError("additional_kwargs must be a dictionary.")
        # Some providers retain a duplicate raw function-call representation.
        # Accept it only when it agrees with standardized calls. Any other hidden
        # provider payload (reasoning/signatures/refusals/etc.) is rejected.
        remaining = {key: value for key, value in additional.items() if value is not None}
        raw_calls = remaining.pop("tool_calls", None)
        if raw_calls is not None:
            if not calls or type(raw_calls) is not list or len(raw_calls) != len(calls):
                raise ValueError("Raw tool calls must agree with standardized AIMessage.tool_calls.")
            for raw, expected in zip(raw_calls, native.get("tool_calls", [])):
                if (type(raw) is not dict or set(raw) != {"id", "type", "function"}
                        or raw["id"] != expected["id"] or raw["type"] != "function"
                        or type(raw["function"]) is not dict or set(raw["function"]) != {"name", "arguments"}
                        or raw["function"]["name"] != expected["function"]["name"]):
                    raise ValueError("Raw tool calls disagree with standardized calls.")
                try:
                    equivalent = (_argument_fingerprint(raw["function"]["arguments"])
                                  == _argument_fingerprint(expected["function"]["arguments"]))
                except (ValueError, TypeError):
                    equivalent = False
                if not equivalent:
                    raise ValueError("Raw tool-call arguments disagree with standardized calls.")
        if remaining:
            raise ValueError("Unsupported additional_kwargs payload; reasoning/provider fields are not silently dropped.")
        if role == "tool":
            native["tool_call_id"] = row.get("tool_call_id")
            if name is not None and tool_names.get(native["tool_call_id"]) != name:
                raise ValueError("ToolMessage.name must match its associated function name.")
            if row.get("status", "success") not in {"success", "error"}:
                raise ValueError("ToolMessage.status must be success or error.")
        canonical.append(native)
        captured.append(message)
    # Validate original roles, all call/result IDs and complete indivisible turns
    # before any memory ingestion or provider request.
    _validated_openai_chat_messages(canonical)
    return canonical, captured


class LangChainPrepared:
    """Framework inputs plus this call's exact immutable-by-copy Relay plan."""

    def __init__(self, prepared, messages):
        self.prepared = prepared
        self._messages = messages

    @property
    def messages(self):
        return deepcopy(self._messages)

    @property
    def report(self):
        report = self.prepared.report
        report["framework"] = "langchain"
        report["budget_representation"] = "canonical_text_and_function_messages"
        return report


class LangChainResponseError(TypeError):
    """A graph node needs a complete AIMessage; the original response is retained."""

    def __init__(self, response):
        self.response = response
        super().__init__("LangGraph message nodes need a complete AIMessage response; no stream was consumed.")


class LangChainRelay:
    """One conversation's existing model behind Relay preparation.

    The original model/graph owns inference, responses, tools and checkpointing.
    This bridge neither appends model responses nor rewrites graph state.
    """

    def __init__(self, model, connection, types, *, thread_id=None):
        self._model, self._connection, self._types = model, connection, types
        self._thread_id = thread_id

    @property
    def guard(self):
        return self._connection.guard

    @property
    def memory(self):
        return self._connection.memory

    @property
    def session_id(self):
        return self._connection.session_id

    def _render(self, prepared, originals):
        report = prepared.report
        included = report["included_indices"]
        before = report["data_before_index"]
        insertion = sum(index < before for index in included)
        planned = prepared.messages
        if len(planned) != len(included) + 1:
            raise RuntimeError("Unexpected Relay plan shape; no framework request was made.")
        messages = [originals[index] for index in included]
        messages.insert(insertion, self._types.HumanMessage(content=planned[insertion]["content"]))
        return LangChainPrepared(prepared, messages)

    def prepare(self, messages):
        self._connection._require_open()
        canonical, originals = _normalise(messages, self._types)
        return self._render(self._connection._prepare(canonical), originals)

    async def aprepare(self, messages):
        self._connection._require_open()
        canonical, originals = _normalise(messages, self._types)
        return self._render(await self._connection._prepare_async(canonical), originals)

    def _config(self, config, *, graph=False):
        if config is not None and type(config) is not dict:
            raise TypeError("Runnable config must be a dictionary or None.")
        if graph and self._thread_id is None:
            raise ValueError("Graph nodes require connect_langchain(..., thread_id='one-conversation-id').")
        if self._thread_id is not None:
            configurable = (config or {}).get("configurable", {})
            if type(configurable) is not dict or configurable.get("thread_id") != self._thread_id:
                raise ValueError("Runnable config.thread_id must match this bridge's conversation; use a separate bridge per thread.")

    def invoke_result(self, input, config=None, **kwargs):
        self._config(config)
        provider = getattr(self._model, "invoke", None)
        if not callable(provider):
            raise TypeError("This model does not expose invoke(); use ainvoke().")
        if inspect.iscoroutinefunction(provider):
            raise TypeError("invoke() must be synchronous; use the model's ainvoke().")
        prepared = self.prepare(input)
        response = provider(prepared.messages, config=config, **kwargs)
        return AgentResult(_reject_awaitable(response), prepared.prepared)

    def invoke(self, input, config=None, **kwargs):
        return self.invoke_result(input, config=config, **kwargs).response

    async def ainvoke_result(self, input, config=None, **kwargs):
        self._config(config)
        provider = getattr(self._model, "ainvoke", None)
        if not callable(provider):
            raise TypeError("This model does not expose ainvoke(); no thread fallback is implicit.")
        prepared = await self.aprepare(input)
        pending = provider(prepared.messages, config=config, **kwargs)
        if not inspect.isawaitable(pending):
            raise TypeError("Model ainvoke() must return an awaitable.")
        return AgentResult(_reject_awaitable(await pending), prepared.prepared)

    async def ainvoke(self, input, config=None, **kwargs):
        return (await self.ainvoke_result(input, config=config, **kwargs)).response

    def _graph_input(self, state, config):
        self._config(config, graph=True)
        if type(state) is not dict or "messages" not in state:
            raise ValueError("Graph state must contain the complete original messages list.")
        return state["messages"]

    def _delta(self, response):
        if type(response) is not self._types.AIMessage:
            raise LangChainResponseError(response)
        return {"messages": [response]}

    def node(self, state, config=None):
        """LangGraph node: return one new response, never prepared history."""
        return self._delta(self.invoke(self._graph_input(state, config), config=config))

    async def anode(self, state, config=None):
        return self._delta(await self.ainvoke(self._graph_input(state, config), config=config))

    def as_runnable(self):
        """A real optional RunnableLambda; its streaming yields a complete output."""
        self._connection._require_open()
        return _load_runnable()(self.invoke, afunc=self.ainvoke, name="ContextRelay")

    def close(self):
        self._connection.close()

    def __enter__(self):
        self._connection._require_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.close()


def connect_langchain(model, *, config=None, guard=None, session_id=None, memory=None,
                      memory_interpreter=None, token_budget=None, correction_fields=(),
                      output=None, thread_id: str | None = None) -> LangChainRelay:
    """Connect an existing chat model or tool-bound Runnable to one conversation.

    Call bind_tools()/with_config() on the model first. This adapter handles full
    framework message history only, not strings, prompt objects or agent-state
    dictionaries. Use node/anode inside a LangGraph state graph. Incremental
    streaming and Relay OutputPolicy are unsupported rather than silently ignored.
    """
    if isinstance(model, LangChainRelay):
        raise ValueError("This model is already connected to Relay.")
    if not any(callable(getattr(model, name, None)) for name in ("invoke", "ainvoke")):
        raise TypeError("model must expose invoke() and/or ainvoke().")
    if thread_id is not None:
        nonempty_string(thread_id, "thread_id")
    if config is None:
        settings = RelayConfig()
    elif isinstance(config, RelayConfig):
        settings = config
    elif isinstance(config, (str, Path)):
        settings = RelayConfig.from_file(config)
    else:
        raise TypeError("config must be a RelayConfig, JSON file path, or None.")
    if output is not None or settings.output is not None:
        raise ValueError("LangChain integration does not yet support Relay OutputPolicy; no check was silently disabled.")
    if settings.protocol not in {"plain", "openai_chat"}:
        raise ValueError("LangChain integration uses the canonical openai_chat text/function protocol.")
    settings = replace(settings, protocol="openai_chat")
    types = _load_messages()
    # No provider is called through this private connection: it supplies the
    # shared memory/preparation/lifecycle implementation for both sync and async.
    connection = connect(lambda *, messages: messages, config=settings, guard=guard,
                         session_id=session_id, memory=memory, memory_interpreter=memory_interpreter,
                         token_budget=token_budget, correction_fields=correction_fields)
    return LangChainRelay(model, connection, types, thread_id=thread_id)
