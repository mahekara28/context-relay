"""Real loopback HTTP tests; no Ollama install or model download is required."""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from context_relay.interpretation import MemoryInterpretationError, MemoryInterpreter
from context_relay.memory import AutomaticMemory
from context_relay.store import RelayStore


def chat(content="[]", **changes):
    return {"message": {"role": "assistant", "content": content},
            "done": True, "done_reason": "stop", **changes}


class LocalInterpreterTests(unittest.TestCase):
    def setUp(self):
        owner = self
        self.requests = []
        self.behaviors = {}
        self.release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, body, dict(self.headers)))
                behavior = owner.behaviors.get(self.path, {})
                default = {"model_info": {"general.architecture": "test"}} if self.path == "/api/show" else chat()
                payload = behavior.get("body", default)
                encoded = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                try:
                    if behavior.get("stall"):
                        owner.release.wait(3)
                    if behavior.get("trickle_headers"):
                        self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Slow: ")
                        self.wfile.flush()
                        while not owner.release.wait(0.02):
                            self.wfile.write(b"x")
                            self.wfile.flush()
                        return
                    self.send_response(behavior.get("status", 200))
                    self.send_header("Content-Type", behavior.get("content_type", "application/json"))
                    if behavior.get("length", True):
                        self.send_header("Content-Length", str(behavior.get("declared_length", len(encoded))))
                    for name, value in behavior.get("headers", {}).items():
                        self.send_header(name, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if behavior.get("trickle_body"):
                        for byte in encoded:
                            if owner.release.wait(0.02):
                                break
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                    else:
                        self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionError, OSError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def interpreter(self, **options):
        return MemoryInterpreter.from_ollama(model="already-installed:3b", endpoint=self.endpoint, **options)

    def suggest(self, interpreter=None, text="Make deliveries early in the day."):
        return (interpreter or self.interpreter()).suggest(text=text, index=3,
            known_memory=[{"key": "delivery_window", "value": "evening", "disputed": False}])

    def test_construction_is_offline_and_empty_result_is_valid(self):
        with patch("context_relay.local_interpreter.http.client.HTTPConnection") as connection:
            interpreter = self.interpreter()
            connection.assert_not_called()
        self.assertTrue(interpreter.review_unmatched)
        self.assertEqual(self.suggest(interpreter), [])
        self.assertEqual([path for path, _, _ in self.requests], ["/api/show", "/api/chat"])
        payload = self.requests[-1][1]
        self.assertEqual(payload["model"], "already-installed:3b")
        self.assertIs(payload["stream"], False)
        self.assertIs(payload["think"], False)
        self.assertIs(payload["truncate"], False)
        self.assertIs(payload["shift"], False)
        self.assertEqual(payload["options"], {"temperature": 0, "num_predict": 768, "num_ctx": 8192})
        self.assertEqual(payload["format"]["items"]["required"], ["key", "value", "quote"])
        self.assertFalse(payload["format"]["items"]["additionalProperties"])
        self.assertEqual(json.loads(payload["messages"][1]["content"])["text"], "Make deliveries early in the day.")

    def test_quotes_use_exact_unicode_character_offsets(self):
        source = "  Ab delivery subah karna. सुबह भेजें।  "
        quote = "सुबह भेजें।"
        self.behaviors["/api/chat"] = {"body": chat(json.dumps([
            {"key": "delivery_window", "value": "morning", "quote": quote}], ensure_ascii=False))}
        result = self.suggest(text=source)[0]
        self.assertEqual(result["start"], source.index(quote))
        self.assertEqual(source[result["start"]:result["end"]], quote)

    def test_model_change_needs_review_and_old_value_is_disputed(self):
        text = "Make deliveries early in the day."
        self.behaviors["/api/chat"] = {"body": chat(json.dumps([
            {"key": "delivery_window", "value": "morning", "quote": text}]))}
        with RelayStore() as store:
            session = store.create_session("local model")['id']
            memory = AutomaticMemory(store, session, interpreter=self.interpreter())
            history = [{"role": "user", "content": "The delivery window is evening."}]
            memory.process(history)
            self.assertEqual(self.requests, [])
            memory.process(history + [{"role": "user", "content": text}])
            pending = memory.pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["reason"], "interpreted_needs_review")
            self.assertEqual(memory.snapshot()["entries"][0]["value"], "evening")
            self.assertTrue(memory.snapshot()["entries"][0]["disputed"])
            memory.confirm(pending[0]["id"])
            self.assertEqual(memory.snapshot()["entries"][0]["value"], "morning")

    def test_invalid_model_output_never_creates_a_proposal(self):
        invalid = ["not JSON", "```json\n[]\n```", "{}", "[NaN]",
            '[{"key":"delivery_window","key":"language","value":"morning","quote":"Make deliveries early in the day."}]',
            '[{"key":"delivery_window","value":"morning","quote":"imagined instruction"}]',
            '[{"key":"delivery_window","value":"morning","quote":"Make deliveries early in the day.","confidence":1}]',
            '[{"key":"Delivery","value":"morning","quote":"Make deliveries early in the day."}]',
            '[{"key":"delivery_window","value":1,"quote":"Make deliveries early in the day."}]']
        for content in invalid:
            with self.subTest(content=content):
                self.behaviors["/api/chat"] = {"body": chat(content)}
                with self.assertRaises(MemoryInterpretationError):
                    self.suggest()

    def test_repeated_quote_is_not_guessed(self):
        self.behaviors["/api/chat"] = {"body": chat('[{"key":"response_style","value":"brief","quote":"brief"}]')}
        with self.assertRaisesRegex(MemoryInterpretationError, "exactly once"):
            self.suggest(text="Make it brief, brief.")

    def test_remote_or_unknown_metadata_prevents_sending_user_text(self):
        for metadata in ({}, {"model_info": {}}, {"model_info": {"x": 1}, "remote_host": "https://ollama.com"},
                         {"model_info": {"x": 1}, "remote_model": "other"}):
            with self.subTest(metadata=metadata):
                self.requests.clear()
                self.behaviors["/api/show"] = {"body": metadata}
                with self.assertRaisesRegex(MemoryInterpretationError, "no user text was sent"):
                    self.suggest()
                self.assertEqual([path for path, _, _ in self.requests], ["/api/show"])

    def test_metadata_is_rechecked_when_alias_changes(self):
        interpreter = self.interpreter()
        self.suggest(interpreter)
        self.behaviors["/api/show"] = {"body": {"model_info": {"x": 1}, "remote_host": "cloud"}}
        with self.assertRaises(MemoryInterpretationError):
            self.suggest(interpreter)
        self.assertEqual([path for path, _, _ in self.requests], ["/api/show", "/api/chat", "/api/show"])

    def test_remote_incomplete_refused_and_tool_output_is_rejected(self):
        for response in (chat(done=False), chat(done_reason="length"), chat(remote_host="cloud"),
                         chat(message={"role": "user", "content": "[]"}),
                         chat(message={"role": "assistant", "content": "[]", "refusal": "No"}),
                         chat(message={"role": "assistant", "content": "[]", "tool_calls": [{"name": "run"}]}),
                         {"done": True}, chat(error="model failed")):
            with self.subTest(response=response):
                self.behaviors["/api/chat"] = {"body": response}
                with self.assertRaises(MemoryInterpretationError):
                    self.suggest()

    def test_redirects_are_not_followed(self):
        self.behaviors["/api/show"] = {"status": 307, "headers": {"Location": self.endpoint + "/api/chat"}}
        with self.assertRaisesRegex(MemoryInterpretationError, "redirect"):
            self.suggest()
        self.assertEqual(len(self.requests), 1)

    def test_missing_model_message_is_actionable_without_model_pull(self):
        self.behaviors["/api/show"] = {"status": 404, "body": {"error": "missing"}}
        with self.assertRaisesRegex(MemoryInterpretationError, "ollama list.*does not download"):
            self.suggest()
        self.assertEqual([path for path, _, _ in self.requests], ["/api/show"])

    def test_input_cap_checks_before_any_request(self):
        with self.assertRaisesRegex(MemoryInterpretationError, "max_input_chars.*No input was sent"):
            self.suggest(self.interpreter(max_input_chars=20))
        self.assertEqual(self.requests, [])

    def test_response_and_decoded_output_caps(self):
        for behavior in ({"body": b"x" * 20000}, {"body": b"x" * 20000, "length": False},
                         {"body": chat("[]" + " " * 101)}):
            with self.subTest(behavior=tuple(behavior)):
                self.behaviors["/api/chat"] = behavior
                with self.assertRaises(MemoryInterpretationError):
                    self.suggest(self.interpreter(max_output_chars=100))

    def test_malformed_http_payloads(self):
        for behavior in ({"body": b"\xff"}, {"body": b'{"model_info":{},"model_info":{"x":1}}'},
                         {"body": b"[]"}, {"content_type": "application/x-ndjson"},
                         {"headers": {"Content-Encoding": "gzip"}}, {"body": b'{"model_info": NaN}'},
                         {"body": b'{"model_info": {"number": 1e400}}'},
                         {"headers": {"Transfer-Encoding": "chunked"}}, {"declared_length": 1000}):
            with self.subTest(behavior=behavior):
                self.behaviors["/api/show"] = behavior
                with self.assertRaises(MemoryInterpretationError):
                    self.suggest()

    def test_total_deadline_stops_stalled_and_trickled_responses(self):
        for mode in ("stall", "trickle_headers", "trickle_body"):
            with self.subTest(mode=mode):
                self.behaviors["/api/show"] = {mode: True}
                start = time.monotonic()
                with self.assertRaisesRegex(MemoryInterpretationError, "timed out"):
                    self.suggest(self.interpreter(timeout=0.2))
                self.assertLess(time.monotonic() - start, 2)

    def test_failure_leaves_history_unconsumed_for_explicit_retry(self):
        with RelayStore() as store:
            memory = AutomaticMemory(store, store.create_session("retry")['id'], interpreter=self.interpreter())
            history = [{"role": "user", "content": "Make deliveries early in the day."}]
            self.behaviors["/api/show"] = {"status": 503}
            with self.assertRaises(MemoryInterpretationError):
                memory.process(history)
            self.assertEqual(memory.pending(), [])
            self.assertEqual(memory.snapshot()["entries"], [])
            self.behaviors.clear()
            self.assertEqual(memory.process(history)["processed_indices"], [0])
            self.assertEqual([path for path, _, _ in self.requests], ["/api/show", "/api/show", "/api/chat"])

    def test_proxy_environment_cannot_redirect_local_text(self):
        with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1",
                                      "OLLAMA_HOST": "https://example.com", "OLLAMA_API_KEY": "do-not-send"}):
            self.assertEqual(self.suggest(), [])
        for _, _, headers in self.requests:
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("Proxy-Authorization", headers)


class LocalConfigurationTests(unittest.TestCase):
    def test_invalid_configuration_is_rejected_without_network(self):
        invalid = [{"model": "model:cloud"}, {"model": "model:3b-cloud"}, {"model": ""}, {"model": "bad\nname"},
            {"endpoint": "https://127.0.0.1:11434"}, {"endpoint": "http://example.com"},
            {"endpoint": "http://127.0.0.1:11434/api/chat"}, {"endpoint": "http://127.0.0.1:0"},
            {"endpoint": "http://user:pass@localhost:11434"}, {"endpoint": "http://localhost:11434?token=x"},
            {"endpoint": "http://localhost:11434/#x"}, {"endpoint": "http://[::1%25eth0]:11434"},
            {"timeout": True}, {"timeout": float("nan")}, {"timeout": 0}, {"timeout": 301},
            {"max_input_chars": True}, {"max_input_chars": 0}, {"max_tokens": -1}, {"max_tokens": 8193},
            {"max_proposals": True}, {"max_output_chars": 0}, {"review_unmatched": "yes"}]
        with patch("context_relay.local_interpreter.http.client.HTTPConnection") as connection:
            for options in invalid:
                with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                    MemoryInterpreter.from_ollama(**({"model": "test:3b"} | options))
            connection.assert_not_called()

    def test_generic_interpreters_preserve_conservative_default(self):
        self.assertFalse(MemoryInterpreter(lambda request: []).review_unmatched)
        self.assertFalse(MemoryInterpreter.from_model(lambda **kwargs: "[]").review_unmatched)
        self.assertTrue(MemoryInterpreter.from_model(lambda **kwargs: "[]", review_unmatched=True).review_unmatched)
        with self.assertRaises(TypeError):
            MemoryInterpreter(lambda request: [], review_unmatched=1)


if __name__ == "__main__":
    unittest.main()
