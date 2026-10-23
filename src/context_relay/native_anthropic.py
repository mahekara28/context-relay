"""Validate visible Anthropic input without editing signed or paired history."""

from __future__ import annotations

from .protocols import _text


def _blocks(content, label, *, allow_empty=False):
    if type(content) is str:
        return [{"type": "text", "text": content}]
    if type(content) is not list or not content and not allow_empty:
        raise ValueError(f"{label} must be text or a nonempty list of native blocks.")
    for block in content:
        if type(block) is not dict or type(block.get("type")) is not str:
            raise ValueError(f"{label} contains an invalid native block.")
    return content


def _text_blocks(content, label, *, allow_empty=False):
    for block in _blocks(content, label, allow_empty=allow_empty):
        if block["type"] != "text":
            raise ValueError(f"{label} supports text blocks only; multimodal and reference blocks need another adapter.")
        _text(block.get("text"), label)


def _media(block):
    """Check source shape, never fetch, decode, or re-encode media bytes."""
    source = block.get("source")
    if type(source) is not dict:
        raise ValueError("Native image/document blocks need an explicit source object.")
    kind = source.get("type")
    if type(kind) is not str:
        raise ValueError("Native media source type must be a supported string.")
    if kind in {"url", "file"}:
        _text(source.get("url" if kind == "url" else "file_id"), "Media source", nonempty=True)
    elif kind == "base64":
        types = ({"image/jpeg", "image/png", "image/gif", "image/webp"}
                 if block["type"] == "image" else {"application/pdf"})
        if type(source.get("media_type")) is not str or source["media_type"] not in types:
            raise ValueError("Unsupported native media type.")
        _text(source.get("data"), "Base64 media data", nonempty=True)
    elif block["type"] == "document" and kind == "text":
        if source.get("media_type") != "text/plain":
            raise ValueError("Text documents require media_type='text/plain'.")
        _text(source.get("data"), "Document text")
    elif block["type"] == "document" and kind == "content":
        for child in _blocks(source.get("content"), "Document source content"):
            if child["type"] == "text":
                _text(child.get("text"), "Document source text")
            elif child["type"] == "image":
                _media(child)
            else:
                raise ValueError("Document content sources support text and images only.")
    else:
        raise ValueError("Unsupported native image/document source type.")


def _input_block(block):
    kind = block["type"]
    if kind == "text":
        _text(block.get("text"), "Native input text")
    elif kind in {"image", "document"}:
        _media(block)
    elif kind == "search_result":
        _text(block.get("source"), "Search result source", nonempty=True)
        _text(block.get("title"), "Search result title")
        if type(block.get("content")) is not list:
            raise ValueError("Search result content must be a list of native text blocks.")
        _text_blocks(block.get("content"), "Search result content")
    else:
        raise ValueError(f"Unsupported native input block {kind!r}; nothing was flattened.")


def prepare_anthropic_request(request: dict) -> tuple[dict, dict]:
    """Return a defensive JSON request and a preservation report.

    This does not claim to authenticate signatures or reproduce provider-side
    compaction. The complete visible request remains unchanged. Unknown fields
    on supported blocks/options survive; unsupported block types fail clearly.
    """
    from .native import native_json

    body = native_json(request, sdk="anthropic")
    messages = body.get("messages")
    if type(messages) is not list or not messages:
        raise ValueError("Native Anthropic calls need a nonempty messages list.")
    for key in ("container", "mcp_servers", "conversation", "previous_response_id"):
        if body.get(key) is not None:
            raise ValueError(f"{key} can add server-held state; this adapter requires visible native history.")
    if "system" in body:
        _text_blocks(body["system"], "Top-level system")
    if "tools" in body:
        if type(body["tools"]) is not list:
            raise ValueError("Native tools must be a list of JSON definitions.")
        for tool in body["tools"]:
            if type(tool) is not dict:
                raise ValueError("Native tools must be JSON definitions.")
            _text(tool.get("name"), "Tool name", nonempty=True)
    for key in ("thinking", "context_management", "compaction", "output_config"):
        if key in body and type(body[key]) is not dict:
            raise ValueError(f"{key} must be a JSON object when supplied.")

    pending, seen = set(), set()
    counts = {kind: 0 for kind in ("tool_use", "tool_result", "thinking", "redacted_thinking", "compaction",
                                  "image", "document", "search_result")}
    for index, message in enumerate(messages):
        if type(message) is not dict or message.get("role") not in {"user", "assistant", "system"}:
            raise ValueError(f"Message {index} has an unsupported native Anthropic role.")
        role = message["role"]
        if role == "system" and index == 0:
            raise ValueError("Initial instructions belong in top-level system=; native mode never moves messages.")
        content = _blocks(message.get("content"), f"Message {index} content",
                          allow_empty=role == "system" and bool(message.get("output_config")))
        if index == 0 and role == "assistant" and content[0]["type"] != "compaction":
            raise ValueError("History must start with a user message or a provider compaction block.")
        if pending and role != "user":
            raise ValueError("Each tool-use batch needs an immediate user message containing all its results.")
        had_pending, found_result, ordinary_content = bool(pending), False, False
        for block in content:
            kind = block["type"]
            if kind in counts:
                counts[kind] += 1
            if kind == "text":
                _text(block.get("text"), "Native text")
                ordinary_content = True
            elif kind in {"image", "document", "search_result"}:
                if role != "user":
                    raise ValueError("Native image/document/search_result input belongs to user messages or tool results.")
                _input_block(block)
                ordinary_content = True
            elif kind in {"thinking", "redacted_thinking"}:
                if role != "assistant":
                    raise ValueError("Thinking blocks belong to assistant messages.")
                if kind == "thinking":
                    _text(block.get("thinking"), "Thinking text")
                    _text(block.get("signature"), "Thinking signature", nonempty=True)
                else:
                    _text(block.get("data"), "Redacted thinking data", nonempty=True)
                ordinary_content = True
            elif kind == "tool_use":
                if role != "assistant":
                    raise ValueError("tool_use belongs to an assistant message.")
                call_id = block.get("id")
                _text(call_id, "Tool call ID", nonempty=True)
                _text(block.get("name"), "Tool call name", nonempty=True)
                if type(block.get("input")) is not dict:
                    raise ValueError("Native tool input must be a JSON object.")
                if call_id in seen:
                    raise ValueError("Tool call IDs must be unique within the supplied history.")
                seen.add(call_id)
                pending.add(call_id)
            elif kind == "tool_result":
                if role != "user" or ordinary_content:
                    raise ValueError("tool_result blocks must precede other content in the responding user message.")
                call_id = block.get("tool_use_id")
                _text(call_id, "Tool result ID", nonempty=True)
                if call_id not in pending:
                    raise ValueError("Tool result has no outstanding matching tool_use ID.")
                if "content" in block:
                    for child in _blocks(block["content"], "Tool result content", allow_empty=True):
                        _input_block(child)
                if "is_error" in block and type(block["is_error"]) is not bool:
                    raise ValueError("Tool result is_error must be boolean.")
                pending.remove(call_id)
                found_result = True
            elif kind == "compaction":
                if pending or role == "system":
                    raise ValueError("Compaction cannot interrupt an unfinished client tool round.")
                if "content" in block and block["content"] is not None:
                    _text(block["content"], "Compaction content", nonempty=True)
                for field in ("signature", "encrypted_content"):
                    if field in block and block[field] is not None:
                        _text(block[field], f"Compaction {field}", nonempty=True)
                ordinary_content = True
            elif kind in {"tool_addition", "tool_removal"}:
                if role != "system" or type(block.get("tool")) is not dict:
                    raise ValueError("Tool availability changes require a native system message and tool object.")
                ordinary_content = True
            else:
                raise ValueError(f"Unsupported native block {kind!r}; no block was flattened or removed.")
        if had_pending and (pending or not found_result):
            raise ValueError("Return every result from the previous tool-use batch in the same user message.")
    if pending:
        raise ValueError("Append all outstanding tool results before another provider call.")

    return body, {
        "protocol": "anthropic_messages", "preparation": "preserve_native",
        "input_unchanged": True, "memory_injected": False, "pruned_messages": 0,
        "tool_pairs_validated": True, "native_block_counts": counts,
        "provider_context_management_requested": "context_management" in body,
        "provider_compaction_requested": "compaction" in body,
        "provider_effective_context_known": False,
    }
