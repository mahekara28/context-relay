from __future__ import annotations

import http.client
import io
import json
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from context_relay import RelayStore
from context_relay.server import (
    MAX_REQUEST,
    REJECTION_DRAIN_BYTES,
    REJECTION_DRAIN_SECONDS,
    WorkbenchHandler,
    make_server,
)


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.server = make_server(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        defaults = {"Content-Type": "application/json", "X-Relay-Client": "workbench"}
        if headers:
            defaults.update(headers)
        payload = raw if raw is not None else (json.dumps(body) if body is not None else None)
        conn.request(method, path, payload, defaults)
        response = conn.getresponse()
        data = response.read()
        result = json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data
        status, response_headers = response.status, dict(response.getheaders())
        conn.close()
        return status, result, response_headers

    def create(self, scenario="refund"):
        status, detail, _ = self.request("POST", "/api/sessions", {"name": "test", "scenario": scenario})
        self.assertEqual(status, 201)
        return detail

    def watch_request_completion(self, before_drain=None):
        completed = threading.Event()

        class TrackedHandler(WorkbenchHandler):
            def _drain_rejected_body(self):
                if before_drain is not None:
                    self.wfile.flush()
                    before_drain.wait(timeout=2)
                super()._drain_rejected_body()

            def finish(self):
                try:
                    super().finish()
                finally:
                    completed.set()

        self.server.RequestHandlerClass = TrackedHandler
        return completed

    def rejected_headers(self, headers):
        client = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        request_headers = {
            "Host": f"127.0.0.1:{self.port}",
            "Content-Type": "application/json",
            "X-Relay-Client": "workbench",
            **headers,
        }
        request = "POST /api/sessions HTTP/1.1\r\n" + "".join(
            f"{key}: {value}\r\n" for key, value in request_headers.items()
        ) + "\r\n"
        try:
            client.sendall(request.encode("ascii"))
            response = http.client.HTTPResponse(client)
            response.begin()
            result = response.status, json.loads(response.read()), dict(response.getheaders())
            response.close()
            return client, result
        except Exception:
            client.close()
            raise

    def test_health_and_seeded_sessions(self):
        status, data, _ = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(data["model_connected"])
        _, data, _ = self.request("GET", "/api/sessions")
        self.assertEqual(len(data["sessions"]), 2)

    def test_refund_guided_flow_and_receipt(self):
        data = self.create()
        sid = data["session"]["id"]
        base = f"/api/sessions/{sid}"
        _, verdict, _ = self.request("POST", base + "/check", data["demo"]["proposed_action"])
        self.assertEqual(verdict["decision"], "block")
        self.assertTrue(any(x["code"] == "requirement_failed" for x in verdict["issues"]))
        _, audit, _ = self.request("POST", base + "/audit", {"candidate": data["demo"]["stale_context"]})
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["restored_context"]["facts"]["resolution"], "replacement")
        _, blocked, _ = self.request("POST", base + "/simulate", data["demo"]["proposed_action"])
        self.assertFalse(blocked["executed"])
        _, allowed, _ = self.request("POST", base + "/simulate", data["demo"]["safe_action"])
        self.assertTrue(allowed["executed"])
        self.assertTrue(allowed["result"]["simulated"])
        _, updated, _ = self.request("GET", base)
        self.assertEqual(updated["events"][-1]["kind"], "tool_result")
        self.assertGreater(updated["context"]["revision"], data["context"]["revision"])

    def test_deployment_does_not_turn_failed_tests_into_passes(self):
        data = self.create("deployment")
        sid = data["session"]["id"]
        _, verdict, _ = self.request("POST", f"/api/sessions/{sid}/simulate", data["demo"]["proposed_action"])
        self.assertFalse(verdict["executed"])
        _, safe, _ = self.request("POST", f"/api/sessions/{sid}/simulate", data["demo"]["safe_action"])
        self.assertTrue(safe["executed"])
        self.assertEqual(self.store.facts(sid)["tests"]["value"], "failed")
        self.assertIs(self.store.facts(sid)["paused"]["value"], True)

    def test_optimistic_fact_write_returns_conflict_without_overwrite(self):
        data = self.create()
        sid, revision = data["session"]["id"], data["context"]["revision"]
        path = f"/api/sessions/{sid}/facts"
        status, _, _ = self.request("POST", path, {"key": "resolution", "value": "refund", "expected_revision": revision})
        self.assertEqual(status, 200)
        status, result, _ = self.request("POST", path, {"key": "resolution", "value": "replacement", "expected_revision": revision})
        self.assertEqual(status, 409)
        self.assertEqual(result["error"]["code"], "revision_conflict")
        self.assertEqual(self.store.facts(sid)["resolution"]["value"], "refund")

    def test_large_fact_rolls_back_and_session_remains_accessible(self):
        data = self.create("custom")
        sid = data["session"]["id"]
        status, result, _ = self.request("POST", f"/api/sessions/{sid}/facts", {"key": "large", "value": "x" * 12500})
        self.assertEqual(status, 422)
        self.assertEqual(result["error"]["code"], "context_budget")
        self.assertEqual(self.store.get_session(sid)["revision"], 0)
        self.assertEqual(self.store.facts(sid), {})
        self.assertEqual(self.request("GET", f"/api/sessions/{sid}")[0], 200)

    def test_large_python_import_is_inspectable_and_exportable(self):
        data = self.create("custom")
        sid = data["session"]["id"]
        self.store.record_fact(sid, "large", "x" * 12500)
        status, detail, _ = self.request("GET", f"/api/sessions/{sid}")
        self.assertEqual(status, 200)
        self.assertTrue(detail["context"]["over_budget"])
        status, _, headers = self.request("GET", f"/api/sessions/{sid}/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_rebinding_and_cross_origin_are_rejected(self):
        for headers in ({"Host": f"attacker.example:{self.port}"}, {"Origin": "https://attacker.example"}):
            self.assertEqual(self.request("GET", "/api/sessions", headers=headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/sessions", {}, {"X-Relay-Client": ""})[0], 403)
        self.assertEqual(self.request("OPTIONS", "/api/sessions")[0], 405)

    def test_invalid_input_does_not_create_sessions(self):
        count = len(self.store.list_sessions())
        for raw in ("[]", "{", '{"name":NaN}', '{"name":true}', '{"name":"x","scenario":"invalid"}'):
            self.assertEqual(self.request("POST", "/api/sessions", raw=raw)[0], 400)
        self.assertEqual(len(self.store.list_sessions()), count)

    def test_content_type_and_payload_limits(self):
        self.assertEqual(self.request("POST", "/api/sessions", {}, {"Content-Type": "text/plain"})[0], 415)
        count = len(self.store.list_sessions())
        status, data, headers = self.request("POST", "/api/sessions", raw="x" * (MAX_REQUEST + 1))
        self.assertEqual(status, 413)
        self.assertEqual(data["error"]["code"], "request_too_large")
        self.assertEqual(headers["Connection"], "close")
        self.assertEqual(len(self.store.list_sessions()), count)

    def test_large_uploads_rejected_before_body_parsing_return_the_error(self):
        count = len(self.store.list_sessions())
        cases = (
            ("POST", "/api/sessions", {"Host": f"attacker.example:{self.port}"}, 403),
            ("POST", "/api/sessions", {"Origin": "https://attacker.example"}, 403),
            ("POST", "/api/sessions", {"X-Relay-Client": ""}, 403),
            ("POST", "/api/sessions", {"Content-Type": "text/plain"}, 415),
            ("POST", "/api/sessions", {"Transfer-Encoding": "chunked"}, 400),
            ("POST", "/api/not-found", {}, 404),
            ("OPTIONS", "/api/sessions", {}, 405),
        )
        for method, path, headers, expected in cases:
            with self.subTest(method=method, path=path, headers=headers):
                status, data, response_headers = self.request(
                    method, path, headers=headers, raw="x" * (MAX_REQUEST + 1)
                )
                self.assertEqual(status, expected)
                self.assertIn("error", data)
                self.assertEqual(response_headers["Connection"], "close")
        self.assertEqual(len(self.store.list_sessions()), count)

    def test_rejected_upload_can_arrive_after_the_error_response(self):
        before_drain = threading.Event()
        completed = self.watch_request_completion(before_drain)
        try:
            client, (status, data, headers) = self.rejected_headers({"Content-Length": MAX_REQUEST + 1})
            with client:
                self.assertEqual(status, 413)
                self.assertEqual(data["error"]["code"], "request_too_large")
                self.assertEqual(headers["Connection"], "close")
                # Queue an in-flight tail after receiving the response, then stop
                # the upload. Start the production drain after that handoff.
                client.sendall(b"x" * 1024)
                client.shutdown(socket.SHUT_WR)
                before_drain.set()
                self.assertTrue(completed.wait(1), "Rejected request did not finish after upload EOF")
        finally:
            before_drain.set()

    def test_missing_or_huge_rejected_body_does_not_hold_the_handler_open(self):
        cases = (
            ({"Content-Length": MAX_REQUEST + 1}, 413),
            ({"Content-Length": 10**12}, 413),
            ({"Content-Length": "invalid"}, 400),
            ({"Content-Type": "text/plain"}, 415),
        )
        count = len(self.store.list_sessions())
        for headers, expected in cases:
            with self.subTest(headers=headers):
                completed = self.watch_request_completion()
                client, (status, data, response_headers) = self.rejected_headers(headers)
                with client:
                    self.assertEqual(status, expected)
                    self.assertIn("error", data)
                    self.assertEqual(response_headers["Connection"], "close")
                    self.assertTrue(completed.wait(REJECTION_DRAIN_SECONDS + 1))
        self.assertEqual(len(self.store.list_sessions()), count)

    def test_continuous_slow_rejected_body_does_not_extend_the_deadline(self):
        completed = self.watch_request_completion()

        def upload():
            while not completed.is_set():
                try:
                    client.sendall(b"x")
                except OSError:
                    return
                completed.wait(REJECTION_DRAIN_SECONDS / 16)

        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as client:
            client.sendall((
                "POST /api/sessions HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.port}\r\n"
                "Content-Type: application/json\r\n"
                "X-Relay-Client: workbench\r\n"
                "Content-Length: 1000000000000\r\n\r\n"
            ).encode("ascii"))
            sender = threading.Thread(target=upload, daemon=True)
            sender.start()
            try:
                response = http.client.HTTPResponse(client)
                response.begin()
                self.assertEqual(response.status, 413)
                self.assertEqual(json.loads(response.read())["error"]["code"], "request_too_large")
                response.close()
                self.assertTrue(completed.wait(REJECTION_DRAIN_SECONDS + 1))
            finally:
                completed.set()
                sender.join(timeout=1)
            self.assertFalse(sender.is_alive())

    def test_custom_policy_and_wrong_order_binding(self):
        data = self.create("custom")
        sid = data["session"]["id"]
        base = f"/api/sessions/{sid}"
        self.request("POST", base + "/facts", {"key": "order_id", "value": "A"})
        status, detail, _ = self.request("POST", base + "/policies", {
            "id": "read", "title": "Current order only", "tool": "read_order", "bindings": {"order": "order_id"}})
        self.assertEqual(status, 200)
        _, verdict, _ = self.request("POST", base + "/check", {"tool": "read_order", "arguments": {"order": "B"},
                                                                "context_revision": detail["context"]["revision"]})
        self.assertEqual(verdict["decision"], "block")
        self.assertEqual(verdict["issues"][0]["code"], "binding_mismatch")

    def test_unknown_session_and_path_traversal(self):
        self.assertEqual(self.request("GET", "/api/sessions/not-a-session")[0], 404)
        for path in ("/../pyproject.toml", "/%2e%2e/README.md", "/src/context_relay/server.py"):
            self.assertEqual(self.request("GET", path)[0], 404)


class RejectedBodyDrainTests(unittest.TestCase):
    def handler(self):
        handler = object.__new__(WorkbenchHandler)
        handler.connection = Mock()
        handler.wfile = io.BytesIO()
        handler.rfile = Mock()
        handler._headers = Mock()
        handler._body_consumed = False
        return handler

    def test_response_is_sent_and_half_closed_before_reading_more_input(self):
        handler = self.handler()

        def read(size):
            self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "too large"})
            handler.connection.shutdown.assert_called_once_with(socket.SHUT_WR)
            return b""

        handler.rfile.read1.side_effect = read
        handler._json(413, {"error": "too large"})
        self.assertTrue(handler.close_connection)
        self.assertEqual(handler._headers.call_args.kwargs["Connection"], "close")

    def test_drain_has_a_byte_cap_even_when_the_sender_never_stops(self):
        handler = self.handler()
        handler.rfile.read1.side_effect = lambda size: b"x" * size
        with patch("context_relay.server.time.monotonic", return_value=0):
            handler._json(413, {})
        sizes = [call.args[0] for call in handler.rfile.read1.call_args_list]
        self.assertEqual(sum(sizes), REJECTION_DRAIN_BYTES)
        self.assertLessEqual(max(sizes), 65536)

    def test_drain_uses_one_deadline_instead_of_resetting_the_timeout(self):
        handler = self.handler()
        handler.rfile.read1.return_value = b"x"
        with patch("context_relay.server.time.monotonic", side_effect=[0, 0.01, 0.1, 0.2, 0.26]):
            handler._json(413, {})
        self.assertEqual(handler.rfile.read1.call_count, 3)
        timeouts = [call.args[0] for call in handler.connection.settimeout.call_args_list]
        self.assertGreater(timeouts[0], timeouts[1])
        self.assertGreater(timeouts[1], timeouts[2])

    def test_read_timeout_does_not_replace_the_error_response(self):
        handler = self.handler()
        handler.rfile.read1.side_effect = TimeoutError
        handler._json(413, {"error": "too large"})
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "too large"})

    def test_consumed_body_does_not_need_a_rejection_drain(self):
        handler = self.handler()
        handler._body_consumed = True
        handler._json(400, {})
        handler.rfile.read1.assert_not_called()
        handler.connection.shutdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
