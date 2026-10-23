"""Small, explicit loopback transport for optional Ollama interpretation."""

from __future__ import annotations

import http.client
import ipaddress
import math
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from .interpretation import MemoryInterpretationError, _load_json
from .models import encode


def _endpoint(value: str) -> tuple[str, int]:
    if type(value) is not str or any(character.isspace() for character in value):
        raise ValueError("endpoint must be an HTTP loopback URL without whitespace.")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port if parsed.port is not None else 11434
        if (parsed.scheme != "http" or not host or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or parsed.username is not None
                or parsed.password is not None or not 1 <= port <= 65535):
            raise ValueError
        # Resolve this name ourselves; never consult DNS or proxy environment.
        host = "127.0.0.1" if host.casefold() == "localhost" else host
        if not ipaddress.ip_address(host).is_loopback or "%" in host:
            raise ValueError
    except ValueError as error:
        raise ValueError("endpoint must be http://127.0.0.1:PORT, http://[::1]:PORT, or localhost; remote endpoints are not supported.") from error
    return host, port


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MemoryInterpretationError("Local interpretation timed out. No change was saved; retry explicitly or increase timeout.")
    return remaining


def _stop_socket(connection_socket: socket.socket) -> None:
    try:
        connection_socket.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _post(host: str, port: int, path: str, body: dict, deadline: float, limit: int) -> dict:
    connection = http.client.HTTPConnection(host, port, timeout=_remaining(deadline))
    watchdog = None
    try:
        connection.connect()
        # A socket timeout alone is an inactivity timeout. This watchdog also
        # bounds headers or chunked bodies trickled in just before each timeout.
        watchdog = threading.Timer(_remaining(deadline), _stop_socket, (connection.sock,))
        watchdog.daemon = True
        watchdog.start()
        connection.request("POST", path, body=encode(body).encode("utf-8"), headers={
            "Content-Type": "application/json", "Accept": "application/json", "Connection": "close"})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise MemoryInterpretationError("Local interpretation refused an HTTP redirect; check the Ollama endpoint.")
        if response.status != 200:
            hint = ("Select an installed local model from `ollama list`; Relay does not download models."
                    if response.status == 404 else
                    "Check that the local daemon and selected model support /api/show and structured /api/chat requests.")
            raise MemoryInterpretationError(f"Local Ollama returned HTTP {response.status}. {hint} No change was saved.")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise MemoryInterpretationError("Local Ollama returned an unsupported content encoding.")
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise MemoryInterpretationError("Local Ollama must return application/json without streaming.")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isascii() or not length.isdigit() or int(length) > limit):
            raise MemoryInterpretationError("Local Ollama response exceeds its byte limit or has an invalid length.")
        transfer = response.getheader("Transfer-Encoding")
        if transfer is not None and (transfer.lower() != "chunked" or length is not None):
            raise MemoryInterpretationError("Local Ollama returned ambiguous or unsupported HTTP framing.")
        raw = response.read(limit + 1)
        _remaining(deadline)
        if len(raw) > limit:
            raise MemoryInterpretationError("Local Ollama response exceeds its byte limit.")
        if length is not None and len(raw) != int(length):
            raise MemoryInterpretationError("Local Ollama returned an incomplete HTTP response.")
        result = _load_json(raw.decode("utf-8"))
        if type(result) is not dict:
            raise MemoryInterpretationError("Local Ollama must return a JSON response object.")
        if "error" in result:
            raise MemoryInterpretationError("Local Ollama reported an error; inspect its local logs. No change was saved.")
        return result
    except MemoryInterpretationError:
        raise
    except (OSError, http.client.HTTPException, UnicodeError) as error:
        if time.monotonic() >= deadline or isinstance(error, TimeoutError):
            raise MemoryInterpretationError("Local interpretation timed out. No change was saved; retry explicitly or increase timeout.") from error
        raise MemoryInterpretationError("Could not read a valid local Ollama response. Check the daemon and endpoint; no change was saved.") from error
    finally:
        if watchdog is not None:
            watchdog.cancel()
        connection.close()


def ollama_model_call(*, model: str, endpoint: str, timeout: float, max_input_chars: int,
                      max_output_chars: int, max_proposals: int, max_tokens: int):
    """Construct an internal model callable; initialization performs no I/O."""
    if (type(model) is not str or not 1 <= len(model) <= 200
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:/-]*", model)
            or re.search(r"(?:^|[-:/.])cloud(?:$|[-:/.])", model, re.I)):
        raise ValueError("model must name an installed local Ollama model, without a cloud tag.")
    host, port = _endpoint(endpoint)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.05 <= timeout <= 300:
        raise ValueError("timeout must be a finite number from 0.05 to 300 seconds.")
    for name, value, maximum in (("max_input_chars", max_input_chars, 1000000), ("max_tokens", max_tokens, 8192)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}.")
    schema = {"type": "array", "maxItems": max_proposals, "items": {
        "type": "object", "additionalProperties": False, "required": ["key", "value", "quote"],
        "properties": {"key": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,79}$"},
                       "value": {"type": "string", "minLength": 1, "maxLength": 256},
                       "quote": {"type": "string", "minLength": 1, "maxLength": max_input_chars}}}}

    def call(*, messages: list[dict]) -> str:
        if len(messages[-1]["content"]) > max_input_chars:
            raise MemoryInterpretationError("Interpretation input exceeds max_input_chars. No input was sent; reduce retained memory or raise the limit explicitly.")
        deadline = time.monotonic() + timeout
        # A local daemon may proxy cloud aliases. Check metadata before sending
        # any user text, every time; do not cache permission across alias changes.
        metadata = _post(host, port, "/api/show", {"model": model, "verbose": False}, deadline, 262144)
        if (metadata.get("remote_model") or metadata.get("remote_host")
                or type(metadata.get("model_info")) is not dict or not metadata["model_info"]):
            raise MemoryInterpretationError("The selected model is remote or lacks local model metadata. Select an installed local model; no user text was sent.")
        result = _post(host, port, "/api/chat", {
            "model": model, "messages": messages, "stream": False, "think": False,
            "truncate": False, "shift": False, "format": schema,
            "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 8192},
        }, deadline, 8 * max_output_chars + 16384)
        message = result.get("message")
        if (result.get("remote_model") or result.get("remote_host") or result.get("done") is not True
                or result.get("done_reason") != "stop" or type(message) is not dict
                or message.get("role") != "assistant" or type(message.get("content")) is not str
                or message.get("tool_calls") or message.get("refusal")):
            raise MemoryInterpretationError("Local Ollama returned remote, incomplete, refused, or non-text output. No change was saved.")
        return message["content"]

    return call
