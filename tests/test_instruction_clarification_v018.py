"""Uncertainty must stop generation, survive restarts and allow clarification."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from context_relay import (ActionRejected, ConversationAlreadyCompleted,
                          MemoryClarificationRequired, MemoryPolicy, OutputPolicy,
                          RelayConfig, auto_connect)


def user(text):
    return {"role": "user", "content": text}


def extend(history, text):
    return history + [{"role": "assistant", "content": "Recorded."}, user(text)]


def memory_in(messages):
    packet = next(m["content"] for m in messages if m["content"].startswith("APPLICATION-RECORDED TASK DATA\n"))
    return json.loads(packet[packet.index("{"):])["state"]["user_memory"]


class ClarificationTests(unittest.TestCase):
    def test_default_stops_before_provider_and_a_clear_new_instruction_resumes(self):
        calls = []
        with auto_connect(lambda **kw: calls.append(kw) or "done") as ai:
            history = [user("Export as CSV.")]
            first = ai.invoke(messages=history)
            history = extend(history, "Make the export format easier to read.")
            with self.assertRaises(MemoryClarificationRequired) as caught:
                ai(messages=history)
            self.assertEqual(len(calls), 1)
            error = caught.exception
            self.assertEqual(error.targets, ("export_format",))
            self.assertTrue(error.pending)
            error.pending.clear()
            self.assertTrue(error.pending)
            self.assertGreater(error.prepared.revision, first.prepared.revision)
            ai.allow_tool("export", memory_bindings={"format": "export_format"})
            writes = []
            with self.assertRaises(ActionRejected):
                ai.guard.turn().wrap_dispatcher(lambda *args: writes.append(args))("export", {"format": "CSV"})
            self.assertEqual(writes, [])
            history = extend(history, "Export as JSON.")
            current = ai.invoke(messages=history)
            self.assertEqual(len(calls), 2)
            self.assertFalse(current.prepared.memory_snapshot["entries"][0]["disputed"])
            self.assertEqual(current.prepared.memory_snapshot["entries"][0]["value"], "JSON")
            self.assertEqual(ai.guard.store.facts(ai.session_id), {})

    def test_replaying_unclear_input_never_calls_provider_or_duplicates_review(self):
        calls = []
        with auto_connect(lambda **kw: calls.append(kw) or "done") as ai:
            history = [user("Export as CSV.")]
            ai(messages=history)
            history = extend(history, "Make the export format easier to read.")
            for _ in range(2):
                with self.assertRaises(MemoryClarificationRequired):
                    ai(messages=history)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(ai.memory.pending()), 1)
            # Explicit read-only review remains possible while requests are paused.
            ai.review_corrections(history)

    def test_pause_survives_restart_and_same_value_reaffirmation_can_resolve(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "memory.db"))
            calls = []
            model = lambda **kw: calls.append(kw) or "done"
            history = [user("Export as CSV.")]
            with auto_connect(model, config=config) as ai:
                ai(messages=history)
                history = extend(history, "Make the export format easier to read.")
                with self.assertRaises(MemoryClarificationRequired):
                    ai(messages=history)
                session_id = ai.session_id
            with auto_connect(model, config=config, session_id=session_id) as ai:
                with self.assertRaises(MemoryClarificationRequired):
                    ai(messages=history)
                ai(messages=extend(history, "Keep the export format as CSV."))
                self.assertEqual(len(calls), 2)
                self.assertFalse(ai.memory.snapshot()["entries"][0]["disputed"])

    def test_host_can_explicitly_continue_but_tool_memory_stays_disputed(self):
        with auto_connect(lambda **kw: memory_in(kw["messages"]),
                          memory=MemoryPolicy(pause_on_uncertainty=False)) as ai:
            history = [user("Export as CSV.")]
            ai(messages=history)
            result = ai(messages=extend(history, "Make the export format easier to read."))
            self.assertTrue(result["entries"][0]["disputed"])
            ai.allow_tool("export", memory_bindings={"format": "export_format"})
            with self.assertRaises(ActionRejected):
                ai.guard.turn().wrap_dispatcher(lambda *args: self.fail("Executed"))("export", {"format": "CSV"})

    def test_output_retries_do_not_bypass_pending_preference(self):
        calls = []
        with auto_connect(lambda **kw: calls.append(kw) or '{}',
                          output=OutputPolicy(format="json", max_retries=2)) as ai:
            history = [user("Export as CSV.")]
            ai(messages=history)
            with self.assertRaises(MemoryClarificationRequired):
                ai(messages=extend(history, "Make the export format easier to read."))
            self.assertEqual(len(calls), 1)

    def test_grounded_recovery_stops_if_preference_changes_during_first_call(self):
        calls = []
        history = [user("Export as CSV.")]
        def model(*, messages):
            calls.append(messages)
            packet = next(m["content"] for m in messages if m["content"].startswith("APPLICATION-RECORDED TASK DATA\n"))
            state = json.loads(packet[packet.index("{"):])["state"]
            ai.memory.process(extend(history, "Make the export format easier to read."))
            return {"answer": "unknown", "history_id": state["evidence_history_id"], "citations": []}
        with auto_connect(model) as ai:
            with self.assertRaises(MemoryClarificationRequired):
                ai.run_grounded(messages=history, max_rounds=1)
            self.assertEqual(len(calls), 1)

    def test_managed_chat_records_local_question_and_accepts_the_next_answer(self):
        calls = []
        with auto_connect(lambda **kw: calls.append(kw) or {"answer": "done"}) as ai:
            chat = ai.conversation(decoder=lambda response: response["answer"])
            chat.send("Export as CSV.", request_id="start")
            with self.assertRaises(MemoryClarificationRequired) as caught:
                chat.send("Make the export format easier to read.", request_id="change")
            self.assertEqual(caught.exception.request_id, "change")
            self.assertIn("full instruction naming the setting and value", caught.exception.prompt)
            self.assertIn("Examples only, not suggested changes: Export as JSON.", caught.exception.prompt)
            self.assertEqual(len(calls), 1)
            # A bare answer has no inferred setting; the question asks for a full instruction.
            with self.assertRaises(MemoryClarificationRequired):
                chat.send("JSON", request_id="bare-value")
            self.assertEqual(len(calls), 1)
            with self.assertRaises(ConversationAlreadyCompleted):
                chat.send("Make the export format easier to read.", request_id="change")
            chat.send("Export as JSON.", request_id="clarify")
            self.assertEqual(len(calls), 2)
            self.assertEqual(ai.memory.entries()[0]["value"], "JSON")

    def test_completed_local_clarification_survives_managed_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            config = RelayConfig(db_path=str(Path(directory) / "chat.db"))
            calls = []
            model = lambda **kw: calls.append(kw) or "done"
            with auto_connect(model, config=config) as ai:
                chat = ai.conversation()
                chat.send("Export as CSV.", request_id="start")
                with self.assertRaises(MemoryClarificationRequired) as caught:
                    chat.send("Make the export format easier to read.", request_id="change")
                prompt, session_id = caught.exception.prompt, ai.session_id
                self.assertIsNone(chat.pending)
            with auto_connect(model, config=config, session_id=session_id) as ai:
                chat = ai.conversation()
                self.assertIsNone(chat.pending)
                self.assertEqual(chat.history[-1], {"role": "assistant", "content": prompt})
                with self.assertRaises(ConversationAlreadyCompleted) as caught:
                    chat.send("Make the export format easier to read.", request_id="change")
                self.assertEqual(caught.exception.assistant_message["content"], prompt)
                self.assertEqual(len(calls), 1)
                chat.send("Export as JSON.", request_id="clarify")
                self.assertEqual(len(calls), 2)
                self.assertEqual(ai.memory.entries()[0]["value"], "JSON")

    def test_nested_request_pause_does_not_complete_the_outer_chat(self):
        for ownership in ("separate", "shared_guard", "same_connection"):
            for checked in (False, True):
                with self.subTest(ownership=ownership, output=checked):
                    delegate = None
                    calls = []
                    def model(**kwargs):
                        calls.append(kwargs)
                        if delegate is None:
                            return "{}"
                        return delegate(messages=chat.history + [user("Make the export format easier to read.")])
                    policy = OutputPolicy(format="json") if checked else None
                    with auto_connect(model, output=policy) as ai:
                        chat = ai.conversation()
                        chat.send("Export as CSV.", request_id="start")
                        if ownership == "same_connection":
                            delegate = ai
                        else:
                            delegate = auto_connect(lambda **kw: self.fail("Nested provider called"),
                                                    guard=ai.guard if ownership == "shared_guard" else None)
                        try:
                            with self.assertRaises(MemoryClarificationRequired) as caught:
                                chat.send("Continue processing.", request_id="outer")
                            self.assertIsNone(caught.exception.request_id)
                            self.assertEqual(chat.pending["request_id"], "outer")
                            self.assertFalse(chat.pending["in_flight"])
                            self.assertEqual(chat.history[-1], user("Continue processing."))
                            self.assertEqual(len(calls), 2)
                        finally:
                            if delegate is not ai:
                                delegate.close()

    def test_foreign_managed_request_keeps_its_own_completed_request_id(self):
        with auto_connect(lambda **kw: "done") as child:
            inner = child.conversation()
            inner.send("Export as CSV.")
            with auto_connect(lambda **kw: inner.send("Make the export format easier to read.",
                                                      request_id="inner")) as parent:
                outer = parent.conversation()
                with self.assertRaises(MemoryClarificationRequired) as caught:
                    outer.send("Process the delegated report.", request_id="outer")
                self.assertEqual(caught.exception.request_id, "inner")
                self.assertIsNone(inner.pending)
                self.assertEqual(outer.pending["request_id"], "outer")
                self.assertEqual(outer.history, [user("Process the delegated report.")])

    def test_configuration_roundtrip_preserves_pause_and_rejects_nonboolean(self):
        for enabled in (False, True):
            config = RelayConfig(memory=MemoryPolicy(pause_on_uncertainty=enabled))
            self.assertEqual(RelayConfig(**config.to_dict()).memory.pause_on_uncertainty, enabled)
        for invalid in (0, 1, None, "false"):
            with self.assertRaises(ValueError):
                MemoryPolicy(pause_on_uncertainty=invalid)

    def test_captured_memory_snapshot_is_defensive(self):
        with auto_connect(lambda **kw: "done") as ai:
            result = ai.invoke(messages=[user("Export as CSV.")])
            result.prepared.memory_snapshot["entries"].clear()
            ai.memory.forget("export_format")
            self.assertEqual(result.prepared.memory_snapshot["entries"][0]["value"], "CSV")


class AsyncClarificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_managed_chat_pauses_without_an_extra_provider_call(self):
        calls = []
        async def model(**kwargs):
            calls.append(kwargs)
            return "done"
        async with auto_connect(model) as ai:
            chat = ai.conversation()
            await chat.send("Export as CSV.")
            with self.assertRaises(MemoryClarificationRequired):
                await chat.send("Make the export format easier to read.")
            self.assertEqual(len(calls), 1)
            await chat.send("Export as JSON.")
            self.assertEqual(len(calls), 2)


    async def test_async_nested_request_pause_is_not_an_outer_local_answer(self):
        for ownership in ("separate", "shared_guard", "same_connection"):
            for checked in (False, True):
                with self.subTest(ownership=ownership, output=checked):
                    delegate = None
                    calls = []
                    async def model(**kwargs):
                        calls.append(kwargs)
                        if delegate is None:
                            return "{}"
                        return await delegate(messages=chat.history + [user("Make the export format easier to read.")])
                    async def nested_provider(**kwargs):
                        self.fail("Nested provider called")
                    policy = OutputPolicy(format="json") if checked else None
                    async with auto_connect(model, output=policy) as ai:
                        chat = ai.conversation()
                        await chat.send("Export as CSV.", request_id="start")
                        if ownership == "same_connection":
                            delegate = ai
                        else:
                            delegate = auto_connect(nested_provider,
                                                    guard=ai.guard if ownership == "shared_guard" else None)
                        try:
                            with self.assertRaises(MemoryClarificationRequired) as caught:
                                await chat.send("Continue processing.", request_id="outer")
                            self.assertIsNone(caught.exception.request_id)
                            self.assertEqual(chat.pending["request_id"], "outer")
                            self.assertFalse(chat.pending["in_flight"])
                            self.assertEqual(chat.history[-1], user("Continue processing."))
                            self.assertEqual(len(calls), 2)
                        finally:
                            if delegate is not ai:
                                delegate.close()


if __name__ == "__main__":
    unittest.main()
