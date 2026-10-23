"""Automatic recall integration: retained inputs, state isolation and lifecycle."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import (
    ActionRejected, BudgetExceeded, ConflictError, ContextGuard, CorrectionField,
    MemoryPolicy, RelayConfig, TokenBudget, connect,
)
from context_relay.cli import main


def history(text="Use CSV for the export format."):
    return [{"role": "user", "content": text}]


def data(prepared):
    message = next(row["content"] for row in prepared.messages
                   if row["content"].startswith("APPLICATION-RECORDED TASK DATA\n"))
    return json.loads(message[message.index("{"):])["state"]


class AutomaticConnectionTests(unittest.TestCase):
    def agent(self, provider=lambda **kwargs: object(), **kwargs):
        agent = connect(provider, **kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_schema_free_memory_is_automatic_before_provider_call(self):
        calls = []
        agent = self.agent(lambda **kw: calls.append(kw), memory=MemoryPolicy())
        result = agent.invoke(messages=history())
        state = data(result.prepared)
        self.assertEqual(state["facts"], {})
        self.assertEqual(state["user_memory"], agent.memory.context_snapshot())
        self.assertIn("CSV", json.dumps(state["user_memory"]))
        self.assertIn("memory_processing", result.prepared.report)
        self.assertEqual(len(calls), 1)

    def test_plain_connection_remains_memory_off(self):
        agent = self.agent()
        result = agent.invoke(messages=history())
        self.assertIsNone(agent.memory)
        self.assertNotIn("user_memory", data(result.prepared))
        self.assertEqual(agent.guard.store.events(agent.session_id), [])

    def test_configured_memory_works_and_explicit_policy_overrides_config(self):
        config = RelayConfig(memory={"mode": "review"})
        reviewing = self.agent(config=config)
        reviewing(messages=history())
        self.assertTrue(reviewing.memory.pending())
        automatic = self.agent(config=config, memory=MemoryPolicy())
        automatic(messages=history())
        self.assertEqual(automatic.memory.pending(), [])
        self.assertIn("CSV", json.dumps(automatic.memory.snapshot()))

    def test_full_history_retry_does_not_rewrite_memory(self):
        agent = self.agent(memory=MemoryPolicy())
        first = agent.invoke(messages=history())
        second = agent.invoke(messages=history())
        self.assertEqual(first.prepared.revision, second.prepared.revision)
        self.assertEqual(second.prepared.report["memory_processing"]["status"], "unchanged")

    def test_old_statement_retained_when_its_original_turn_is_omitted(self):
        transcript = history("The deployment region is eu-west-1.")
        transcript.append({"role": "assistant", "content": "Acknowledged. Additional explanation. " * 80})
        for index in range(15):
            transcript.extend([{"role": "user", "content": f"Discuss example {index}."},
                               {"role": "assistant", "content": "Unrelated discussion. " * 30}])
        transcript.append({"role": "user", "content": "Continue."})
        agent = self.agent(config=RelayConfig(max_chars=2200, recent_turns=1, retrieve_turns=0),
                           memory=MemoryPolicy())
        result = agent.invoke(messages=transcript)
        self.assertNotIn(0, result.prepared.report["included_indices"])
        self.assertIn("eu-west-1", json.dumps(data(result.prepared)["user_memory"]))
        self.assertLessEqual(result.prepared.report["size_chars"], 2200)

    def test_review_corrections_does_not_write_automatic_memory(self):
        agent = self.agent(memory=MemoryPolicy(), correction_fields=[
            CorrectionField("export_format", choices=("CSV", "JSON"))])
        agent.review_corrections(history())
        self.assertEqual(agent.guard.store.events(agent.session_id), [])

    def test_auto_recall_does_not_satisfy_fact_based_tool_permissions(self):
        agent = self.agent(memory=MemoryPolicy())
        agent.allow_tool("release", when={"approval": "approved"})
        result = agent.invoke(messages=history("The approval is approved."))
        self.assertEqual(result.prepared.context["facts"], {})
        with self.assertRaises(ActionRejected):
            result.prepared.wrap(lambda: "executed", tool="release")()

    def test_recovery_refreshes_memory_and_keeps_original_request_revision(self):
        agent = self.agent(memory=MemoryPolicy())
        transcript = history()
        first = agent.invoke(messages=transcript).prepared
        transcript += [{"role": "assistant", "content": "Acknowledged."},
                       {"role": "user", "content": "Actually, use JSON instead of CSV."}]
        agent(messages=transcript)
        replacement = agent.guard.recover_context(first, [0], history_id=first.history_id)
        self.assertGreater(replacement.revision, first.revision)
        self.assertEqual(data(replacement)["user_memory"], agent.memory.context_snapshot())

    def test_revision_change_between_ingest_and_prepare_prevents_model_call(self):
        calls = []
        agent = self.agent(lambda **kw: calls.append(kw), memory=MemoryPolicy())
        process = agent.memory.process

        def competing_write(*args, **kwargs):
            report = process(*args, **kwargs)
            agent.remember("other", "changed")
            return report

        with patch.object(agent.memory, "process", side_effect=competing_write):
            with self.assertRaises(ConflictError):
                agent(messages=history())
        self.assertEqual(calls, [])

    def test_memory_snapshot_included_in_token_accounting_outside_transaction(self):
        captured = []
        agent = None

        def count(messages):
            self.assertEqual(agent.guard.store._depth, 0)
            captured.append(messages)
            return len(json.dumps(messages))

        agent = self.agent(memory=MemoryPolicy(), token_budget=TokenBudget(5000, count))
        result = agent.invoke(messages=history())
        self.assertTrue(any("user_memory" in json.dumps(value) for value in captured))
        self.assertEqual(data(result.prepared)["user_memory"], agent.memory.context_snapshot())

    def test_memory_cannot_bypass_prepared_context_budget(self):
        calls = []
        agent = self.agent(lambda **kw: calls.append(kw), memory=MemoryPolicy(),
                           config=RelayConfig(max_chars=300))
        with self.assertRaises(BudgetExceeded):
            agent(messages=history())
        self.assertEqual(calls, [])
        # Recording a real user request is independent of provider success.
        self.assertIn("CSV", json.dumps(agent.memory.snapshot()))

    def test_separate_connections_have_isolated_automatic_memory(self):
        first, second = self.agent(memory=MemoryPolicy()), self.agent(memory=MemoryPolicy())
        first(messages=history())
        result = second.invoke(messages=history("Continue."))
        self.assertNotIn("CSV", json.dumps(data(result.prepared)["user_memory"]))

    def test_persistent_resume_preserves_recall_and_retry_cursor(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = RelayConfig(db_path=str(Path(folder) / "relay.db"), memory=MemoryPolicy())
            with connect(lambda **kw: None, config=settings) as first:
                response = first.invoke(messages=history())
                revision, session = response.prepared.revision, first.session_id
            with connect(lambda **kw: None, config=settings, session_id=session) as resumed:
                response = resumed.invoke(messages=history())
                self.assertEqual(response.prepared.revision, revision)
                self.assertIn("CSV", json.dumps(data(response.prepared)["user_memory"]))

    def test_cross_session_memory_rejected(self):
        agent = self.agent(memory=MemoryPolicy())
        with ContextGuard() as other:
            with self.assertRaises(ValueError):
                other.prepare(history(), memory=agent.memory)

    def test_invalid_policy_rejected_before_database_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "new" / "relay.db"
            with self.assertRaises(TypeError):
                connect(lambda **kw: None, memory=True, config=RelayConfig(db_path=str(path)))
            self.assertFalse(path.exists())

    def test_async_call_receives_automatic_memory(self):
        async def provider(**kwargs):
            await asyncio.sleep(0)
            return kwargs["messages"]

        agent = self.agent(provider, memory=MemoryPolicy())
        result = asyncio.run(agent.invoke(messages=history()))
        self.assertEqual(result.response, result.prepared.messages)
        self.assertIn("CSV", json.dumps(data(result.prepared)["user_memory"]))

    def test_init_and_nested_memory_config_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "relay.json"
            self.assertEqual(main(["init", str(path), "--memory", "auto", "--protocol", "ollama"]), 0)
            settings = RelayConfig.from_file(path)
            self.assertEqual(settings.memory, MemoryPolicy())
            self.assertEqual(settings.protocol, "ollama")
            self.assertEqual(RelayConfig(**settings.to_dict()), settings)

    def test_nested_configuration_rejects_unknown_invalid_and_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "relay.json"
            for value in (True, "auto", {"mode": "unknown"}, {"max_entries": True}, {"api_key": "secret"}):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    RelayConfig(memory=value)
            path.write_text('{"memory":{"mode":"auto","mode":"review"}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                RelayConfig.from_file(path)


if __name__ == "__main__":
    unittest.main()
