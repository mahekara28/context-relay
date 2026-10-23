"""Offline acceptance-runner safety plus one bounded real multiprocessing check."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


live = load("relay_deployment_live_tests", ROOT / "examples/validate_live_provider.py")
shared = load("relay_shared_tests", ROOT / "examples/evaluate_shared_deployment.py")


def chat(text, *, stop="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=stop, message=SimpleNamespace(
        role="assistant", content=text))], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20))


class DeploymentValidationTests(unittest.TestCase):
    def run_live(self, method, *, provider="local", calls=1):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=method)),
                                 responses=SimpleNamespace(create=method), messages=SimpleNamespace(create=method))
        evidence = {"samples": [], "calls_attempted": 0, "calls_completed": 0}
        live.run_checks(client, provider=provider, model="offline-fixture", max_calls=calls,
                        max_output_tokens=128, evidence=evidence)
        return evidence

    def cli(self, args=(), *, environment=None, client=None):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with patch.dict(live.os.environ, environment or {}, clear=True), \
                    patch.object(live, "create_client", return_value=client) as factory, \
                    patch.object(live.importlib.metadata, "version", return_value="offline-fixture"), \
                    patch("builtins.print"):
                code = live.main(["--output", str(output), *args])
            return code, json.loads(output.read_text()), factory

    def test_no_opt_in_never_creates_client(self):
        code, report, factory = self.cli()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "NOT_RUN")
        self.assertEqual(report["calls_attempted"], 0)
        factory.assert_not_called()

    def test_absent_hosted_credentials_are_not_a_pass(self):
        for provider in ("openai", "anthropic"):
            with self.subTest(provider=provider):
                code, report, factory = self.cli(["--run-live", "--provider", provider, "--model", "explicit"])
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "NOT_RUN_MISSING_CREDENTIAL")
                self.assertEqual(report["calls_attempted"], 0)
                factory.assert_not_called()

    def test_local_endpoint_rejects_remote_redirect_credentials_and_ambiguous_hosts(self):
        for value in ("https://api.openai.com/v1", "http://localhost:11434/v1", "http://127.0.0.1:11434/v1?key=secret",
                      "http://key@127.0.0.1:11434/v1", "http://127.0.0.1/v1", "http://127.0.0.1:11434/v1#fragment", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                live.local_url(value)
        self.assertEqual(live.local_url("http://127.0.0.1:11434/v1/"), "http://127.0.0.1:11434/v1")
        self.assertEqual(live.local_url("http://[::1]:11434/v1"), "http://[::1]:11434/v1")

    def test_preserved_sdk_response_passes_and_reports_usage(self):
        method = Mock(return_value=chat(json.dumps({"rows": live.CASES[0]["rows"]})))
        evidence = self.run_live(method)
        self.assertEqual(evidence["status"], "PASSED")
        self.assertEqual((evidence["calls_attempted"], method.call_count), (1, 1))
        self.assertEqual(evidence["samples"][0]["usage"]["total_tokens"], 20)
        self.assertFalse(evidence["samples"][0]["semantic_meaning_checked"])
        self.assertNotIn("response", evidence["samples"][0])

    def test_changed_content_is_blocked_not_success_and_never_retried(self):
        rows = json.loads(json.dumps(live.CASES[0]["rows"]))
        rows[0]["quantity"] = 400
        method = Mock(return_value=chat(json.dumps({"rows": rows})))
        evidence = self.run_live(method)
        self.assertEqual(evidence["status"], "COMPLETED_WITH_BLOCKED_OUTPUT")
        self.assertFalse(evidence["samples"][0]["protected_content_valid"])
        self.assertEqual(method.call_count, 1)

    def test_truncated_or_invalid_json_is_blocked_not_success(self):
        for response in (chat("not json"), chat('{"rows": []}', stop="length")):
            with self.subTest(response=response):
                evidence = self.run_live(Mock(return_value=response))
                self.assertEqual(evidence["status"], "COMPLETED_WITH_BLOCKED_OUTPUT")
                self.assertEqual(evidence["calls_attempted"], 1)

    def test_transport_failure_stops_budget_without_retry_or_private_message(self):
        method = Mock(side_effect=RuntimeError("PRIVATE credential request-body"))
        evidence = self.run_live(method, calls=3)
        self.assertEqual(evidence["status"], "ERROR")
        self.assertEqual((evidence["calls_attempted"], evidence["calls_completed"], method.call_count), (1, 0, 1))
        self.assertEqual(evidence["samples"][0]["error_type"], "RuntimeError")
        self.assertNotIn("PRIVATE", json.dumps(evidence))

    def test_all_three_provider_adapters_check_actual_response_shapes(self):
        text = json.dumps({"rows": live.CASES[0]["rows"]})
        responses = SimpleNamespace(status="completed", output=[SimpleNamespace(type="message", role="assistant",
            status="completed", content=[SimpleNamespace(type="output_text", text=text)])])
        anthropic = SimpleNamespace(type="message", role="assistant", stop_reason="end_turn",
                                    content=[SimpleNamespace(type="text", text=text)])
        for provider, response in (("openai", responses), ("anthropic", anthropic), ("local", chat(text))):
            with self.subTest(provider=provider):
                method = Mock(return_value=response)
                self.assertEqual(self.run_live(method, provider=provider)["status"], "PASSED")
                request = method.call_args.kwargs
                self.assertEqual(request["model"], "offline-fixture")
                self.assertEqual(method.call_count, 1)
                if provider == "openai":
                    self.assertFalse(request["store"])
                    self.assertIn("input", request)

    def test_existing_report_is_preserved_before_keys_or_client(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(live, "create_client") as factory:
            output = Path(directory) / "existing.json"
            output.write_text("old evidence")
            with self.assertRaises(FileExistsError):
                live.main(["--output", str(output), "--run-live", "--provider", "openai", "--model", "explicit"])
            self.assertEqual(output.read_text(), "old evidence")
            factory.assert_not_called()

    def test_local_credentials_never_reuse_hosted_key(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(
            return_value=chat(json.dumps({"rows": live.CASES[0]["rows"]}))))), close=Mock())
        code, report, factory = self.cli(["--run-live", "--provider", "local", "--base-url", "http://127.0.0.1:11434/v1",
                                         "--model", "explicit", "--max-calls", "1"],
                                        environment={"OPENAI_API_KEY": "hosted-secret"}, client=client)
        self.assertEqual(code, 0)
        self.assertEqual(factory.call_args.kwargs["api_key"], "relay-local")
        self.assertNotIn("hosted-secret", json.dumps(report))
        client.close.assert_called_once()

    def test_factory_pins_local_transport_controls(self):
        sdk = SimpleNamespace(DefaultHttpxClient=Mock(), OpenAI=Mock())
        with patch.object(live.importlib, "import_module", return_value=sdk):
            live.create_client("local", api_key="local", timeout=12, base_url="http://127.0.0.1:11434/v1")
        self.assertEqual(sdk.OpenAI.call_args.kwargs["max_retries"], 0)
        self.assertEqual(sdk.DefaultHttpxClient.call_args.kwargs, {"trust_env": False, "follow_redirects": False, "timeout": 12})

    def test_missing_or_failed_workers_cannot_be_reported_passed(self):
        good = {"passed": True, "actions": 3, "invariants": {"one": True}, "durations": [0.1, 0.2]}
        self.assertFalse(shared.summarize_workers([good], expected_workers=2)["passed"])
        result = shared.summarize_workers([good, {"passed": False, "error_type": "OperationalError"}], expected_workers=2)
        self.assertFalse(result["passed"])
        self.assertEqual(result["unexpected_error_types"], {"OperationalError": 1})
        self.assertIsNone(shared.latency_summary([])["p99_seconds"])
        self.assertEqual(shared.latency_summary([0.1, 0.2])["p99_seconds"], 0.2)

    def test_spawned_processes_share_database_and_preserve_external_effect_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "load.json"
            completed = subprocess.run([sys.executable, str(ROOT / "examples/evaluate_shared_deployment.py"),
                "--output", str(report), "--workers", "2", "--sessions-per-worker", "2", "--deadline", "30"],
                text=True, capture_output=True, timeout=45)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(report.read_text())
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(result["result"]["isolated_sessions"], 4)
            self.assertTrue(all(result["result"]["coordinator_invariants"].values()))
            self.assertEqual(result["result"]["workers_reported"], 2)
            self.assertEqual(result["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
