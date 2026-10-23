"""Offline runner tests: no credential, provider or network is contacted."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_live_provider.py"
spec = importlib.util.spec_from_file_location("relay_live_smoke_test", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def text_item(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]}


class LiveProviderRunnerTests(unittest.TestCase):
    def run_cli(self, args, *, client=None, credentials=None):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "report.json"
            with patch.dict(smoke.os.environ, credentials or {}, clear=True), \
                    patch.object(smoke, "create_client", return_value=client) as create, \
                    patch.object(smoke.importlib.metadata, "version", return_value="offline-fixture"), \
                    patch("builtins.print"):
                code = smoke.main(["--output", str(output), *args])
            return code, json.loads(output.read_text()), create

    def test_default_does_not_read_credentials_or_create_client(self):
        original_get = smoke.os.environ.get
        def get(key, default=None):
            if key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
                raise AssertionError("Credential access without opt-in")
            return original_get(key, default)
        with patch.object(smoke.os.environ, "get", side_effect=get):
            code, report, create = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "NOT_RUN")
        self.assertEqual(report["hosted_calls_attempted"], 0)
        create.assert_not_called()

    def test_live_requires_explicit_model_before_reserving_report(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(smoke, "create_client") as create:
            output = Path(folder) / "report.json"
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                smoke.main(["--run-live", "--provider", "openai", "--output", str(output)])
            self.assertFalse(output.exists())
            create.assert_not_called()

    def test_missing_credential_is_not_run(self):
        code, report, create = self.run_cli(["--run-live", "--provider", "openai", "--model", "chosen"])
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "NOT_RUN_MISSING_CREDENTIAL")
        create.assert_not_called()

    def test_openai_replays_every_native_item_without_recording_ciphertext_or_key(self):
        items = [{"type": "reasoning", "summary": [], "encrypted_content": "fixture-ciphertext"}, text_item("READY")]
        create = Mock(side_effect=[SimpleNamespace(status="completed", output=items, output_text="READY"),
                                   SimpleNamespace(status="completed", output=[text_item(smoke.MARKER)], output_text=smoke.MARKER)])
        client = SimpleNamespace(responses=SimpleNamespace(create=create), close=Mock())
        code, report, factory = self.run_cli(["--run-live", "--provider", "openai", "--model", "chosen"],
                                           client=client, credentials={"OPENAI_API_KEY": "private-fixture-key"})
        self.assertEqual(code, 0)
        self.assertEqual(create.call_count, 2)
        second = create.call_args_list[1].kwargs
        self.assertEqual(second["input"][1:-1], items)
        self.assertEqual(second["include"], ["reasoning.encrypted_content"])
        self.assertEqual(report["status"], "PASSED")
        self.assertTrue(report["encrypted_reasoning_observed"])
        self.assertFalse(report["compaction_requested"])
        self.assertNotIn("fixture-ciphertext", json.dumps(report))
        self.assertNotIn("private-fixture-key", json.dumps(report))
        client.close.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["api_key"], "private-fixture-key")

    def test_anthropic_replays_complete_signed_blocks(self):
        items = [{"type": "thinking", "thinking": "fixture", "signature": "fixture-signature"},
                 {"type": "text", "text": "READY"}]
        create = Mock(side_effect=[SimpleNamespace(stop_reason="end_turn", content=items),
                                   SimpleNamespace(stop_reason="end_turn", content=[{"type": "text", "text": smoke.MARKER}])])
        client = SimpleNamespace(messages=SimpleNamespace(create=create), close=Mock())
        code, report, _ = self.run_cli(["--run-live", "--provider", "anthropic", "--model", "chosen"],
                                     client=client, credentials={"ANTHROPIC_API_KEY": "private-fixture-key"})
        self.assertEqual(code, 0)
        self.assertEqual(create.call_args_list[1].kwargs["messages"][1]["content"], items)
        self.assertTrue(report["signed_thinking_observed"])
        self.assertNotIn("fixture-signature", json.dumps(report))

    def test_openai_tool_cycle_uses_actual_call_id_and_two_calls(self):
        call = {"type": "function_call", "call_id": "provider-call", "name": "echo_marker", "arguments": json.dumps({"marker": smoke.MARKER})}
        create = Mock(side_effect=[SimpleNamespace(status="completed", output=[call], output_text=""),
                                   SimpleNamespace(status="completed", output=[text_item(smoke.MARKER)], output_text=smoke.MARKER)])
        client = SimpleNamespace(responses=SimpleNamespace(create=create), close=Mock())
        code, report, _ = self.run_cli(["--run-live", "--provider", "openai", "--model", "chosen", "--exercise", "tool"],
                                     client=client, credentials={"OPENAI_API_KEY": "fixture"})
        self.assertEqual(code, 0)
        self.assertEqual(report["hosted_calls_attempted"], 2)
        self.assertEqual(report["local_fixture_tool_executions"], 1)
        request = create.call_args_list[1].kwargs
        self.assertIn({"type": "function_call_output", "call_id": "provider-call", "output": smoke.MARKER}, request["input"])
        self.assertEqual(request["tool_choice"], "none")

    def test_anthropic_tool_cycle_with_memory_replays_prepared_inline_system_prefix(self):
        blocks = [{"type": "tool_use", "id": "provider-tool", "name": "echo_marker", "input": {"marker": smoke.MARKER}}]
        create = Mock(side_effect=[SimpleNamespace(stop_reason="tool_use", content=blocks),
                                   SimpleNamespace(stop_reason="end_turn", content=[{"type": "text", "text": smoke.MARKER}])])
        client = SimpleNamespace(messages=SimpleNamespace(create=create), close=Mock())
        code, report, _ = self.run_cli(["--run-live", "--provider", "anthropic", "--model", "chosen", "--exercise", "tool", "--native-memory"],
                                     client=client, credentials={"ANTHROPIC_API_KEY": "fixture"})
        self.assertEqual(code, 0, report)
        first, second = [call.kwargs for call in create.call_args_list]
        self.assertTrue(any(row["role"] == "system" for row in first["messages"]))
        self.assertEqual(second["messages"][:len(first["messages"])], first["messages"])
        results = [block for row in second["messages"] if row["role"] == "user" and type(row["content"]) is list
                   for block in row["content"] if block.get("type") == "tool_result"]
        self.assertEqual(results, [{"type": "tool_result", "tool_use_id": "provider-tool", "content": smoke.MARKER}])
        self.assertEqual(report["local_fixture_tool_executions"], 1)

    def test_unexpected_tool_never_executes_or_triggers_second_call(self):
        call = {"type": "function_call", "call_id": "provider-call", "name": "other_tool", "arguments": "{}"}
        create = Mock(return_value=SimpleNamespace(status="completed", output=[call], output_text=""))
        client = SimpleNamespace(responses=SimpleNamespace(create=create), close=Mock())
        code, report, _ = self.run_cli(["--run-live", "--provider", "openai", "--model", "chosen", "--exercise", "tool"],
                                     client=client, credentials={"OPENAI_API_KEY": "fixture"})
        self.assertEqual(code, 1)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(report["local_fixture_tool_executions"], 0)

    def test_replay_validation_failure_does_not_count_a_second_hosted_call(self):
        items = [{"type": "function_call", "call_id": "not-executed", "name": "tool", "arguments": "{}"}]
        create = Mock(return_value=SimpleNamespace(status="completed", output=items, output_text=""))
        client = SimpleNamespace(responses=SimpleNamespace(create=create), close=Mock())
        code, report, _ = self.run_cli(["--run-live", "--provider", "openai", "--model", "chosen"],
                                     client=client, credentials={"OPENAI_API_KEY": "fixture"})
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["hosted_calls_attempted"], 1)
        self.assertEqual(create.call_count, 1)

    def test_provider_error_records_type_not_private_error_message(self):
        create = Mock(side_effect=RuntimeError("private-fixture-key request body"))
        client = SimpleNamespace(responses=SimpleNamespace(create=create), close=Mock())
        _, report, _ = self.run_cli(["--run-live", "--provider", "openai", "--model", "chosen"],
                                   client=client, credentials={"OPENAI_API_KEY": "private-fixture-key"})
        self.assertEqual(report["error_type"], "RuntimeError")
        self.assertNotIn("private-fixture-key", json.dumps(report))
        self.assertEqual(create.call_count, 1)
        client.close.assert_called_once()

    def test_existing_report_prevents_all_live_work(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(smoke, "create_client") as create:
            output = Path(folder) / "report.json"
            output.write_text("old evidence")
            with self.assertRaises(FileExistsError):
                smoke.main(["--run-live", "--provider", "openai", "--model", "chosen", "--output", str(output)])
            create.assert_not_called()
            self.assertEqual(output.read_text(), "old evidence")

    def test_real_factory_pins_official_endpoint_and_disables_retries_proxies_redirects(self):
        sdk = SimpleNamespace(DefaultHttpxClient=Mock(), OpenAI=Mock())
        with patch.object(smoke.importlib, "import_module", return_value=sdk):
            smoke.create_client("openai", api_key="fixture", timeout=12)
        self.assertEqual(sdk.OpenAI.call_args.kwargs["base_url"], "https://api.openai.com/v1")
        self.assertEqual(sdk.OpenAI.call_args.kwargs["max_retries"], 0)
        self.assertEqual(sdk.DefaultHttpxClient.call_args.kwargs, {"trust_env": False, "follow_redirects": False, "timeout": 12})
