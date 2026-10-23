"""Lossless, bounded Responses input validation (no history selection)."""

from __future__ import annotations

from .native import native_json


def _nonempty(value, label):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a nonempty string.")


def _complete(item):
    if item.get("status") not in (None, "completed"):
        raise ValueError("Replay completed native items only; unfinished streaming items are not safe history.")


def _message(item):
    role = item.get("role")
    if type(role) is not str or role not in {"user", "assistant", "system", "developer"}:
        raise ValueError("Native Responses message has an unsupported role.")
    content = item.get("content")
    if type(content) is str:
        return role
    if type(content) is not list or not content:
        raise ValueError("Native Responses message content must be text or a nonempty content-block list.")
    # Preserve block-specific metadata and references exactly. Their remote
    # bytes/decoded token count cannot be measured by a visible JSON budget.
    for block in content:
        if type(block) is not dict or type(block.get("type")) is not str or block["type"] not in {
            "input_text", "output_text", "refusal", "input_image", "input_file", "input_audio",
        }:
            raise ValueError("Unsupported Responses message content block; nothing was flattened.")
    return role


def _function_output(value):
    if type(value) is str:
        return
    if type(value) is not list:
        raise ValueError("Function output must retain its native string or content-block list.")
    for block in value:
        if type(block) is not dict:
            raise ValueError("Function output content blocks must be supported native objects.")
        kind = block.get("type")
        if kind == "input_text":
            if type(block.get("text")) is not str:
                raise ValueError("Function input_text output needs its original text string.")
        elif kind in ("input_image", "input_file"):
            sources = ("image_url", "file_id") if kind == "input_image" else ("file_data", "file_url", "file_id")
            if not any(type(block.get(key)) is str and block[key].strip() for key in sources):
                raise ValueError("Function image/file output needs an explicit native content source.")
        else:
            raise ValueError("Unsupported function output content block; nothing was flattened.")


def prepare_openai_responses(request):
    """Return an exact JSON copy plus facts about locally checkable dependencies.

    Supported native items: messages, function/custom calls/results, encrypted reasoning
    and compaction. Unknown item/tool families fail closed rather than claiming
    validation of their protocol. Opaque ciphertext is preserved, not inspected.
    """
    body = native_json(request, sdk="openai")
    if "messages" in body:
        raise ValueError("Native Responses uses input=full_history, not messages=.")
    for key in ("previous_response_id", "conversation", "prompt"):
        if body.get(key) is not None:
            raise ValueError(f"{key} adds server-hidden history; supply full native input instead.")
    if body.get("truncation") not in (None, "disabled"):
        raise ValueError("Native history cannot be automatically truncated; use explicit provider compaction.")
    history = body.get("input")
    if type(history) is not list or not history:
        raise ValueError("Native Responses input must be a nonempty list of complete native items.")
    tools = body.get("tools")
    if tools is not None:
        if type(tools) is not list or any(type(tool) is not dict or tool.get("type") not in ("function", "custom") for tool in tools):
            raise ValueError("Native Responses supports function tools and custom tools only; other tool item protocols are unsupported.")
        for tool in tools:
            _nonempty(tool.get("name"), "Tool name")
            if tool.get("allowed_callers") is not None and tool["allowed_callers"] != ["direct"]:
                raise ValueError("Programmatic tool callers require a separate native adapter.")
    management = body.get("context_management")
    if management is not None:
        if type(management) is not list or any(
            type(entry) is not dict or entry.get("type") != "compaction" for entry in management
        ):
            raise ValueError("context_management must contain provider compaction entries.")
    instructions = body.get("instructions")
    if instructions is not None and type(instructions) is not str:
        raise ValueError("Native Responses instructions currently supports a complete string only.")
    pending, seen, item_ids = {}, set(), set()
    opaque = custom_calls = 0
    for item in history:
        if type(item) is not dict:
            raise ValueError("Native Responses input items must serialize to objects.")
        item_type = item.get("type", "message" if "role" in item else None)
        if type(item_type) is not str:
            raise ValueError("Native Responses items need an explicit supported type.")
        if item_type == "item_reference":
            raise ValueError("item_reference hides provider history; supply the full native item.")
        if "id" in item and item["id"] is not None:
            _nonempty(item["id"], "Native item id")
            if item["id"] in item_ids:
                raise ValueError("Native item IDs must be unique in the supplied history.")
            item_ids.add(item["id"])
        _complete(item)
        if "caller" in item and item["caller"] is not None:
            if type(item["caller"]) is not dict or item["caller"].get("type") != "direct":
                raise ValueError("Programmatic native tool callers have unsupported history dependencies.")
        if item_type == "message":
            role = _message(item)
            if pending and role in {"user", "system", "developer"}:
                raise ValueError("Complete all function-call results before a new input message.")
        elif item_type in {"function_call", "custom_tool_call"}:
            _nonempty(item.get("call_id"), "Function call_id")
            _nonempty(item.get("name"), "Function name")
            payload = "input" if item_type == "custom_tool_call" else "arguments"
            if type(item.get(payload)) is not str:
                raise ValueError(f"Tool {payload} must be the provider's original string.")
            if item["call_id"] in seen:
                raise ValueError("Function call IDs must be unique in the supplied history.")
            seen.add(item["call_id"])
            pending[item["call_id"]] = item_type + "_output"
            custom_calls += item_type == "custom_tool_call"
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            _nonempty(item.get("call_id"), "Function result call_id")
            if item["call_id"] not in pending:
                raise ValueError("Function result has no preceding outstanding call in visible history.")
            if pending[item["call_id"]] != item_type:
                raise ValueError("Tool output type does not match its preceding call type.")
            _function_output(item.get("output"))
            del pending[item["call_id"]]
        elif item_type in {"reasoning", "compaction"}:
            _nonempty(item.get("encrypted_content"), f"{item_type} encrypted_content")
            if item_type == "compaction" and pending:
                raise ValueError("Compaction cannot replace an outstanding visible function-call dependency.")
            if item_type == "reasoning" and type(item.get("summary")) is not list:
                raise ValueError("Reasoning items must retain their original summary list and encrypted_content.")
            opaque += 1
        else:
            raise ValueError(f"Unsupported native Responses item type {item_type!r}; nothing was flattened.")
    if pending:
        raise ValueError("Native history has incomplete function calls; append every result before the next request.")
    return body, {"input_items": len(history), "function_calls": len(seen) - custom_calls,
                  "custom_tool_calls": custom_calls, "opaque_items": opaque,
                  "dependency_check": "visible_tool_call_ids_and_types",
                  "provider_compaction_requested": bool(management)}
