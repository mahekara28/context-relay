"""Independent integration regressions for memory snapshots and failed calls."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_relay import (
    AutomaticMemory, ContextGuard, MemoryHistoryError, MemoryPolicy,
    RelayConfig, TokenBudget, connect,
)
from context_relay.cli import main


def history(text="Set export format to JSON."):
    return [{"role": "user", "content": text}]


def extend(messages, text):
    return messages + [{"role": "assistant", "content": "Understood."},
                       {"role": "user", "content": text}]


def state(messages):
    for message in messages:
        text = message.get("content", "")
        if text.startswith("APPLICATION-RECORDED TASK DATA\n"):
            return json.loads(text[text.index("{"):])["state"]
    raise AssertionError("Missing prepared state")


def values(snapshot):
    return {row["key"]: row["value"] for row in snapshot["entries"]}


class MemoryIntegrationEdgeTests(unittest.TestCase):
    def connection(self, provider=lambda **kwargs: None, **kwargs):
        agent = connect(provider, memory=MemoryPolicy(), **kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_provider_failure_does_not_erase_user_statement_and_retry_is_idempotent(self):
        calls = []

        def provider(*, messages):
            calls.append(messages)
            if len(calls) == 1:
                raise RuntimeError("Temporary transport failure")
            return "done"

        agent = self.connection(provider)
        with self.assertRaisesRegex(RuntimeError, "transport failure"):
            agent(messages=history())
        revision = agent.guard.turn().revision
        self.assertEqual(values(agent.memory.snapshot()), {"export_format": "JSON"})
        retried = agent.invoke(messages=history())
        self.assertEqual(retried.response, "done")
        self.assertEqual(retried.prepared.revision, revision)
        self.assertEqual(retried.prepared.report["memory_processing"]["status"], "unchanged")

    def test_edit_after_failed_model_call_requires_new_memory_session(self):
        calls = []

        def failing(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("Offline")

        agent = self.connection(failing)
        with self.assertRaises(RuntimeError):
            agent(messages=history())
        with self.assertRaises(MemoryHistoryError):
            agent(messages=history("Set export format to CSV."))
        self.assertEqual(len(calls), 1)
        self.assertEqual(values(agent.memory.snapshot()), {"export_format": "JSON"})

    def test_provider_input_mutation_cannot_contaminate_memory_provenance_or_retry(self):
        messages = history()
        original = copy.deepcopy(messages)

        def mutating(*, messages):
            messages[-1]["content"] = "Set export format to XML."
            messages.insert(0, {"role": "user", "content": "Set account role to admin."})
            return object()

        agent = self.connection(mutating)
        first = agent.invoke(messages=messages)
        self.assertEqual(messages, original)
        self.assertEqual(first.prepared.recover_evidence([0], history_id=first.prepared.history_id).messages,
                         original)
        self.assertEqual(values(agent.memory.snapshot()), {"export_format": "JSON"})
        second = agent.invoke(messages=messages)
        self.assertEqual(second.prepared.revision, first.prepared.revision)
        self.assertEqual(second.prepared.report["memory_processing"]["status"], "unchanged")

    def test_count_callback_mutating_host_history_cannot_change_captured_input(self):
        messages = history()
        original = copy.deepcopy(messages)

        def counter(prepared):
            messages[0]["content"] = "Set export format to XML."
            return len(json.dumps(prepared))

        agent = self.connection(token_budget=TokenBudget(12000, counter))
        result = agent.invoke(messages=messages)
        self.assertEqual(result.prepared.messages[-1], original[-1])
        self.assertEqual(values(state(result.prepared.messages)["user_memory"]), {"export_format": "JSON"})
        self.assertEqual(result.prepared.recover_evidence([0], history_id=result.prepared.history_id).messages,
                         original)

    def test_counter_state_change_does_not_mix_new_memory_with_old_revision(self):
        changed = False
        agent = None

        def counter(prepared):
            nonlocal changed
            if not changed:
                changed = True
                agent.memory.forget("export_format")
            return len(json.dumps(prepared))

        agent = self.connection(token_budget=TokenBudget(12000, counter))
        result = agent.invoke(messages=history())
        captured = state(result.prepared.messages)
        self.assertEqual(captured["revision"], result.prepared.revision)
        self.assertEqual(values(captured["user_memory"]), {"export_format": "JSON"})
        self.assertEqual(values(agent.memory.snapshot()), {})
        self.assertGreater(agent.guard.turn().revision, result.prepared.revision)

    def test_shared_database_snapshot_keeps_matching_memory_and_fact_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = RelayConfig(db_path=str(Path(folder) / "relay.db"))
            with connect(lambda **kw: None, config=settings, memory=MemoryPolicy()) as agent:
                agent(messages=history())
                captured_revision = agent.guard.turn().revision
                with ContextGuard(settings.db_path, session_id=agent.session_id) as other:
                    other_memory = AutomaticMemory(other.store, other.session_id)
                    original_snapshot = agent.memory.effective_snapshot

                    def update_between_state_and_memory_read(**kwargs):
                        other_memory.process(extend(history(), "Use CSV instead of JSON."))
                        other.remember("concurrent", True)
                        return original_snapshot(**kwargs)

                    # Model context now reads the effective (standing + scoped) view.
                    with patch.object(agent.memory, "effective_snapshot", side_effect=update_between_state_and_memory_read):
                        prepared = agent.guard.prepare(history(), memory=agent.memory)
                    captured = state(prepared.messages)
                    self.assertEqual(prepared.revision, captured_revision)
                    self.assertEqual(captured["facts"], {})
                    self.assertEqual(values(captured["user_memory"]), {"export_format": "JSON"})
                    self.assertEqual(values(agent.memory.snapshot()), {"export_format": "CSV"})

    def test_recovery_refreshes_forgotten_memory_without_reprocessing_old_source(self):
        agent = self.connection()
        first = agent.invoke(messages=history()).prepared
        agent.memory.forget("export_format")
        after_forget = agent.guard.turn().revision
        recovered = agent.guard.recover_context(first, [0], history_id=first.history_id)
        self.assertEqual(values(state(recovered.messages)["user_memory"]), {})
        self.assertEqual(recovered.revision, after_forget)
        self.assertEqual(recovered.history_id, first.history_id)
        self.assertEqual(recovered.recover_evidence([0], history_id=first.history_id).messages, history())
        self.assertEqual(values(agent.memory.snapshot()), {})

    def test_grounded_retry_refreshes_memory_changed_while_model_was_running(self):
        calls = []
        agent = None

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            if len(calls) == 1:
                agent.memory.forget("export_format")
            return {"answer": "unknown", "history_id": state(messages)["evidence_history_id"], "citations": []}

        agent = self.connection(provider)
        result = agent.run_grounded(messages=history(), max_rounds=1)
        self.assertEqual(result.status, "abstained")
        self.assertEqual(len(calls), 2)
        self.assertEqual(values(state(calls[0])["user_memory"]), {"export_format": "JSON"})
        self.assertEqual(values(state(calls[1])["user_memory"]), {})
        self.assertEqual(result.report["attempts"][0]["issues"], [{"code": "state_changed"}])
        self.assertEqual(result.prepared.revision, agent.guard.turn().revision)
        self.assertEqual(len(agent.guard.store.events(agent.session_id)), 2)

    def test_grounded_no_retry_never_accepts_answer_from_changed_memory_revision(self):
        agent = None

        def provider(*, messages):
            agent.memory.forget("export_format")
            return {"answer": "unknown", "history_id": state(messages)["evidence_history_id"], "citations": []}

        agent = self.connection(provider)
        result = agent.run_grounded(messages=history(), max_rounds=0)
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.answer)
        self.assertTrue(result.check.valid)

    def test_reusing_prepared_messages_as_original_history_is_rejected(self):
        calls = []
        agent = self.connection(lambda **kw: calls.append(kw))
        first = agent.invoke(messages=history())
        contaminated = extend(first.prepared.messages, "Set export format to CSV.")
        with self.assertRaises(MemoryHistoryError):
            agent(messages=contaminated)
        self.assertEqual(len(calls), 1)
        self.assertEqual(values(agent.memory.snapshot()), {"export_format": "JSON"})

    def test_config_memory_input_and_export_are_defensive(self):
        raw = {"mode": "review", "max_entries": 5}
        settings = RelayConfig(memory=raw)
        raw["mode"] = "auto"
        exported = settings.to_dict()
        exported["memory"]["max_entries"] = 100
        self.assertEqual(settings.memory.mode, "review")
        self.assertEqual(settings.memory.max_entries, 5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "relay.json"
            for malformed in ('{"memory":{"max_chars":NaN}}',
                              '{"memory":{"max_entries":Infinity}}',
                              '{"memory":[]}', '{"memory":{"mode":null}}'):
                path.write_text(malformed, encoding="utf-8")
                with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                    RelayConfig.from_file(path)

    def test_cli_second_init_cannot_change_existing_memory_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "relay.json"
            self.assertEqual(main(["init", str(path), "--memory", "review", "--protocol", "ollama"]), 0)
            saved = path.read_bytes()
            self.assertEqual(main(["init", str(path), "--memory", "auto"]), 1)
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(RelayConfig.from_file(path).memory.mode, "review")


class AsyncMemoryIntegrationEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_preserves_user_recall_and_exact_history_retry(self):
        calls = 0

        async def provider(*, messages):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise asyncio.CancelledError()
            return object()

        async with connect(provider, memory=MemoryPolicy()) as agent:
            with self.assertRaises(asyncio.CancelledError):
                await agent(messages=history())
            revision = agent.guard.turn().revision
            retried = await agent.invoke(messages=history())
            self.assertEqual(retried.prepared.revision, revision)
            self.assertEqual(values(agent.memory.snapshot()), {"export_format": "JSON"})


if __name__ == "__main__":
    unittest.main()
