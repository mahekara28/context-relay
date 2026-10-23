"""A loopback-only workbench server; not an internet-facing deployment server."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from . import BudgetExceeded, ConflictError, Relay, RelayStore, ToolExecutionError, __version__
from .demo import SIMULATED_TOOLS, demo_context, display_context, seed_session, simulation_result

LOG = logging.getLogger("context_relay.server")
MAX_REQUEST = 131072

QUICKSTART = '''from context_relay import Relay, RelayStore, guarded_tool_call

store = RelayStore("agent-state.db")
relay = Relay(store)
session = store.create_session("Order support")
sid = session["id"]

store.record_fact(sid, "order_id", "ORD-1042", source="order-service")
store.record_fact(sid, "resolution", "refund", source="customer")
store.add_policy(sid, {
    "id": "refund-policy", "title": "Honor the current request",
    "tool": "issue_refund",
    "requirements": [{"key": "resolution", "op": "eq", "value": "refund"}],
    "bindings": {"order_id": "order_id"},
})

context = relay.context(sid)
# Supply context["text"] to your agent. Keep its revision in the harness.
store.record_fact(sid, "resolution", "replacement", source="customer",
                  text="Changed my mind. Send a replacement.")

proposal = {"tool": "issue_refund", "arguments": {"order_id": "ORD-1042"}}

def run_tool(tool, arguments):
    # Replace with your own trusted, allowlisted dispatcher.
    return {"simulated": True, "tool": tool, "arguments": arguments}

result = guarded_tool_call(relay, sid, context, proposal, run_tool)
assert result["check"]["decision"] == "block"
assert result["executed"] is False
# Restore result["check"]["recovery_context"] and request a new proposal.
# The model supplies only tool + arguments, never the context revision.
store.close()
'''


def detail(store: RelayStore, sid: str) -> dict:
    with store._transaction():
        return {
            "session": store.get_session(sid),
            "events": store.events(sid),
            "facts": store.facts(sid),
            "policies": store.policies(sid),
            "checks": store.checks(sid),
            "context": display_context(store, sid),
            "demo": demo_context(store, sid),
        }


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, store: RelayStore):
        self.store = store
        self.relay = Relay(store)
        super().__init__(("127.0.0.1", port), WorkbenchHandler)


class WorkbenchHandler(BaseHTTPRequestHandler):
    server: WorkbenchServer
    server_version = f"ContextRelay/{__version__}"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.debug(fmt, *args)

    def _headers(self, status: int, content_type: str, length: int, **extra: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in extra.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()

    def _json(self, status: int, value: Any, **headers: str) -> None:
        payload = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(payload), **headers)
        self.wfile.write(payload)

    def _validate_request(self) -> None:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "").lower()
        if host not in hosts:
            raise APIError(403, "invalid_host", "The workbench accepts loopback requests only.")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in hosts}:
            raise APIError(403, "invalid_origin", "Cross-origin requests are not allowed.")

    def _body(self) -> dict:
        if self.headers.get("X-Relay-Client") != "workbench":
            raise APIError(403, "missing_client_header", "Set X-Relay-Client: workbench.")
        if self.headers.get_content_type() != "application/json":
            raise APIError(415, "content_type", "Send an application/json request.")
        if self.headers.get("Transfer-Encoding"):
            raise APIError(400, "transfer_encoding", "Chunked requests are not supported.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise APIError(400, "content_length", "Invalid Content-Length.") from None
        if length < 0:
            raise APIError(400, "content_length", "Invalid Content-Length.")
        if length > MAX_REQUEST:
            self.close_connection = True
            raise APIError(413, "request_too_large", "Request exceeds 128 KiB.")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise APIError(400, "incomplete_body", "Request body is incomplete.")
        def invalid_constant(value: str) -> None:
            raise ValueError(f"{value} is not a JSON value.")
        try:
            body = json.loads(raw, parse_constant=invalid_constant)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise APIError(400, "invalid_json", "Request body must be valid JSON.") from None
        if not isinstance(body, dict):
            raise APIError(400, "invalid_body", "Request body must be a JSON object.")
        return body

    def _dispatch(self, method: str) -> None:
        try:
            self._validate_request()
            self._route(method)
        except APIError as exc:
            self._json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except ConflictError as exc:
            self._json(409, {"error": {"code": "revision_conflict", "message": str(exc)}})
        except BudgetExceeded as exc:
            self._json(422, {"error": {"code": "context_budget", "message": str(exc)}})
        except ToolExecutionError as exc:
            self._json(422, {"error": {"code": "tool_failed", "message": str(exc)}, "check": exc.check, "receipt": exc.receipt})
        except KeyError:
            self._json(404, {"error": {"code": "not_found", "message": "Session or resource was not found."}})
        except (ValueError, TypeError, UnicodeError) as exc:
            self._json(400, {"error": {"code": "validation", "message": str(exc)}})
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception:
            LOG.exception("Workbench request failed")
            self._json(500, {"error": {"code": "internal_error", "message": "An internal error occurred. See the local server log."}})

    def _route(self, method: str) -> None:
        path = urlsplit(self.path).path
        store, relay = self.server.store, self.server.relay
        if method == "GET" and path == "/api/health":
            return self._json(200, {"name": "Context Relay", "version": __version__, "mode": "local", "model_connected": False})
        if method == "GET" and path == "/api/docs":
            return self._json(200, {
                "quickstart": QUICKSTART,
                "capabilities": ["Versioned facts and evidence", "Explicit tool policies", "Structured context audits", "Point-of-use action checks", "Local SQLite persistence"],
                "limitations": ["No automatic interpretation of natural-language instructions", "No model connected in the workbench", "Context checks cover registered facts and policies only", "The local server is not a public hosting server", "External effects need their own permissions and idempotency"],
            })
        if path == "/api/sessions":
            if method == "GET":
                return self._json(200, {"sessions": store.list_sessions()})
            if method == "POST":
                data = self._body()
                name, scenario = data.get("name", "Untitled session"), data.get("scenario", "custom")
                if not isinstance(name, str) or not isinstance(scenario, str):
                    raise ValueError("Name and scenario must be strings.")
                session = seed_session(store, name, scenario)
                return self._json(201, detail(store, session["id"]))
        match = re.fullmatch(r"/api/sessions/([A-Za-z0-9_-]+)(?:/(facts|policies|check|audit|simulate|export))?", path)
        if match:
            sid, operation = match.groups()
            store.get_session(sid)
            if method == "GET" and operation in {None, "export"}:
                headers = {"Content_Disposition": f'attachment; filename="relay-{sid}.json"'} if operation else {}
                return self._json(200, detail(store, sid), **headers)
            if method == "POST":
                data = self._body()
                if operation == "facts":
                    if "key" not in data or "value" not in data:
                        raise ValueError("A fact needs key and value.")
                    # Do not commit a UI edit that makes the default working context unusable.
                    with store._transaction(write=True):
                        store.record_fact(sid, data["key"], data["value"], text=data.get("text", ""),
                                          source="workbench:user", expected_revision=data.get("expected_revision"))
                        relay.context(sid)
                    return self._json(200, detail(store, sid))
                if operation == "policies":
                    store.add_policy(sid, data)
                    return self._json(200, detail(store, sid))
                if operation == "check":
                    return self._json(200, relay.check(sid, data))
                if operation == "audit":
                    if not isinstance(data.get("candidate"), dict):
                        raise ValueError("Provide a structured candidate context object.")
                    return self._json(200, relay.audit_context(sid, data["candidate"]))
                if operation == "simulate":
                    tool = data.get("tool")
                    if tool not in SIMULATED_TOOLS:
                        raise ValueError("This tool has no built-in simulator. Use the Python executor integration.")
                    output = relay.execute(sid, data, simulation_result)
                    return self._json(200, {**output, "simulated": True})
        if method == "GET":
            assets = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                      "/styles.css": ("styles.css", "text/css"), "/app.js": ("app.js", "text/javascript")}
            if path in assets:
                name, mime = assets[path]
                payload = files("context_relay").joinpath("web", name).read_bytes()
                self._headers(200, mime + "; charset=utf-8", len(payload))
                self.wfile.write(payload)
                return
            if path == "/favicon.ico":
                self._headers(204, "image/x-icon", 0)
                return
        raise APIError(404, "not_found", "This endpoint does not exist.")

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:
        self._json(405, {"error": {"code": "method_not_allowed", "message": "Cross-origin access is disabled."}})


def make_server(store: RelayStore, port: int = 8765, *, seed: bool = True) -> WorkbenchServer:
    if seed and not store.list_sessions():
        seed_session(store, "Order resolution", "refund")
        seed_session(store, "Payments migration", "deployment")
    return WorkbenchServer(port, store)
