"""Protocol-preserving context preparation for Ollama's native chat messages.

Only complete text messages are accepted. Calls, their arguments, and tool
results remain native dictionaries; this module never executes a tool or
reinterprets tool output as an instruction or an application-recorded fact.
"""

from __future__ import annotations

from .budget import TokenBudget
from .context import ContextPlan
from .models import clone, encode


def _text(value: object, description: str, *, nonempty: bool = False) -> None:
    if type(value) is not str or (nonempty and not value.strip()):
        raise ValueError(f"{description} must be {'a nonempty' if nonempty else 'a'} string.")


def _validate_calls(value: object, index: int, seen_ids: set[str]) -> list[dict]:
    if type(value) is not list:
        raise ValueError(f"Message {index} tool_calls must be a list of complete call dictionaries.")
    result: list[dict] = []
    for call in value:
        if type(call) is not dict or "function" not in call or not set(call) <= {"function", "id", "type"}:
            raise ValueError(f"Message {index} has unsupported tool-call fields.")
        if "type" in call and call["type"] != "function":
            raise ValueError("Only function tool calls are supported.")
        function = call["function"]
        if (type(function) is not dict or not {"name", "arguments"} <= set(function)
                or not set(function) <= {"name", "arguments", "index", "description"}):
            raise ValueError(f"Message {index} has an invalid tool-call function.")
        _text(function["name"], "Tool function name", nonempty=True)
        if type(function["arguments"]) is not dict:
            raise ValueError("Native Ollama function arguments must be a JSON dictionary.")
        if "index" in function and (type(function["index"]) is not int or function["index"] < 0):
            raise ValueError("Tool function index must be a nonnegative integer.")
        if "description" in function:
            _text(function["description"], "Tool function description")
        if "id" in call:
            _text(call["id"], "Tool call id", nonempty=True)
            if call["id"] in seen_ids:
                raise ValueError("Tool call IDs must be unique within the captured conversation.")
            seen_ids.add(call["id"])
        result.append(call)
    return result


def _validated_ollama_messages(messages: object) -> tuple[list[dict], list[int], list[list[int]]]:
    """Validate native associations and return immutable-by-copy user-led groups.

    Results with IDs match the outstanding call with that ID. Otherwise results
    must appear in issued order and carry the matching tool name. This prevents
    ambiguous reassociation of multiple same-name calls without inventing IDs.
    """
    if type(messages) is not list or not messages:
        raise ValueError("messages must be a nonempty list of native Ollama message dictionaries.")
    # Check JSON before traversing arguments so all nested values, including
    # nonfinite numbers and non-string dictionary keys, are rejected uniformly.
    history = clone(messages)
    prefix: list[int] = []
    turns: list[list[int]] = []
    pending: list[dict] = []
    seen_ids: set[str] = set()
    for index, message in enumerate(history):
        if type(message) is not dict:
            raise ValueError(f"Message {index} must be a dictionary, not an SDK object.")
        role = message.get("role")
        if type(role) is not str or role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"Message {index} has an unsupported native Ollama role.")
        allowed = {"role", "content"}
        if role == "assistant":
            allowed.update({"thinking", "tool_calls"})
        elif role == "tool":
            allowed.update({"tool_name", "tool_call_id"})
        if not set(message) <= allowed:
            raise ValueError(
                f"Message {index} has unsupported fields; images, multimodal payloads, "
                "stream fragments, and opaque provider fields require another adapter."
            )
        if "content" in message:
            _text(message["content"], f"Message {index} content")
        elif role != "assistant" or not message.get("tool_calls"):
            raise ValueError(f"Message {index} requires string content.")
        if "thinking" in message:
            _text(message["thinking"], f"Message {index} thinking")
        if pending and role != "tool":
            raise ValueError("Every tool-call batch must have all its results before the next message.")
        if role == "system":
            if turns:
                raise ValueError("System messages are supported only in the initial prefix.")
            prefix.append(index)
        elif role == "user":
            turns.append([index])
        else:
            if not turns:
                raise ValueError("Conversation turns must start with a user message.")
            turns[-1].append(index)
            if role == "assistant":
                pending = _validate_calls(message.get("tool_calls", []), index, seen_ids)
            else:
                if not pending:
                    raise ValueError(f"Message {index} is a tool result without an outstanding call.")
                for field in ("tool_name", "tool_call_id"):
                    if field in message:
                        _text(message[field], f"Message {index} {field}", nonempty=True)
                if "tool_call_id" in message:
                    matches = [position for position, call in enumerate(pending)
                               if call.get("id") == message["tool_call_id"]]
                    if not matches:
                        raise ValueError(f"Message {index} tool_call_id has no outstanding matching call.")
                    position = matches[0]
                else:
                    if "tool_name" not in message:
                        raise ValueError("A tool result must identify its call using tool_name or tool_call_id.")
                    position = 0
                expected_name = pending[position]["function"]["name"]
                if "tool_name" in message and message["tool_name"] != expected_name:
                    raise ValueError(f"Message {index} tool_name does not match the corresponding call.")
                pending.pop(position)
    if pending:
        raise ValueError("The final tool-call batch is incomplete; append every result before calling the model.")
    if history[-1]["role"] not in {"user", "tool"}:
        raise ValueError("The final message must be a pending user request or a completed tool-result batch.")
    return history, prefix, turns


def compile_ollama_context(
    messages: list[dict], *, state: dict | None = None, max_chars: int = 16000,
    recent_turns: int = 2, pinned: tuple[int, ...] | list[int] = (),
    retrieve_turns: int = 0, query: str | None = None,
    token_budget: TokenBudget | None = None,
) -> ContextPlan:
    """Select complete native Ollama text/tool turns using exact message costs.

    The final message can be a user request or the final result of a completed
    tool batch. Every selected user-led turn retains its complete assistant and
    tool sequence. Synthetic task data is inserted immediately BEFORE the most
    recent user-led turn, never between a call and its result. Original message
    dictionaries are copied without rewriting their fields or argument values.

    ``token_budget`` counts the native message dictionaries; the caller accounts
    separately for tool schemas and other request material using its counter or
    ``overhead_tokens``. This compiler does not estimate a model's tokenizer.
    """
    from .context import _compile_grouped_context

    history, prefix, turns = _validated_ollama_messages(messages)
    plan = _compile_grouped_context(
        history, prefix, turns, data_before_index=turns[-1][0], state=state,
        max_chars=max_chars, recent_turns=recent_turns, pinned=pinned,
        retrieve_turns=retrieve_turns,
        query=query if query is not None else history[turns[-1][0]]["content"],
        token_budget=token_budget,
        search_texts=[message.get("content", "") + " "
                      + encode(message.get("tool_calls", [])) for message in history],
    )
    report = plan.report
    report["protocol"] = "ollama"
    report["tool_protocol_preserved"] = True
    report["data_before_index"] = turns[-1][0]
    return ContextPlan(plan.messages, report)
