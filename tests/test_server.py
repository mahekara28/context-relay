from __future__ import annotations

import http.client
import json
import threading
import unittest

from context_relay import RelayStore
from context_relay.server import make_server


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
        self.assertEqual(self.request("POST", "/api/sessions", raw="x" * 131073)[0], 413)

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


if __name__ == "__main__":
    unittest.main()
