"""Task carryover and truthful bounded coverage; local deterministic providers only."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from context_relay import BudgetExceeded, MemoryPolicy, RelayConfig, compile_context, connect
from context_relay.conversation import ConversationBusy, HistoryPolicy, TaskContextLimitExceeded
from context_relay.models import ConflictError, encode


def packet(messages):
    text = messages[-2]["content"]
    return json.loads(text[text.index("{"):])


class TaskRolloverTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "task.sqlite3")
        self.calls = []

    def agent(self, provider=None, **options):
        config = options.pop("config", RelayConfig(db_path=self.path))
        def answer(*, messages, **kwargs):
            self.calls.append(messages)
            return "The first inspection is complete; comparison still needs work."
        agent = connect(provider or answer, config=config, **options)
        self.addCleanup(agent.close)
        return agent

    def test_continued_comparison_keeps_identifiers_with_honest_lineage_coverage(self):
        chat = self.agent().conversation(system="Help complete the user's work.", history_policy=HistoryPolicy(100))
        first = "Inspect invoice A-73, then compare its two line items to receipt B-19. Inspect first and wait before comparison."
        chat.send(first, request_id="first")
        origin = chat.session_id
        chat.send("Continue with the remaining comparison.", request_id="second")
        data = packet(self.calls[-1])
        managed = data["state"]["request_data"]["managed_conversation"]
        self.assertNotEqual(chat.session_id, origin)
        self.assertEqual(managed["task"]["requests"], [{"role": "user", "content": first,
            "source": {"kind": "message", "session_id": origin, "message_index": 1, "request_id": "first"}}])
        self.assertEqual(data["coverage"]["scope"], "active_segment")
        self.assertEqual(data["coverage"]["archived_messages"], 3)
        self.assertEqual(data["coverage"]["archived_originals_included"], 0)
        self.assertEqual(data["coverage"]["lineage_total_messages"], 5)
        self.assertEqual(data["coverage"]["lineage_omitted_messages"], 3)
        self.assertEqual([m for m in self.calls[-1] if m["role"] == "system"], chat.history[:1])
        self.assertFalse(managed["task"]["assistant_outputs_included"])
        self.assertEqual(managed["active_session_id"], chat.session_id)
        self.assertIn("not current preference policy", managed["task_scope"])
        # No private conversation state leaks to unrelated direct calls.
        chat.agent(messages=[{"role": "user", "content": "Independent caller-owned history"}])
        self.assertNotIn("managed_conversation", packet(self.calls[-1])["state"].get("request_data", {}))

    def test_explicit_rollover_lazily_seeds_and_resumes_exact_task(self):
        agent = self.agent()
        chat = agent.conversation()
        chat.send("Inspect AB-123 before replacing part CD-456.", request_id="inspect")
        self.assertIsNone(chat.task_snapshot()["task"])
        successor = chat.rollover(expected_revision=chat.task_snapshot()["revision"])
        resumed = self.agent(session_id=successor).conversation()
        resumed.send("Continue.", request_id="continue")
        request = resumed.task_snapshot()["task"]["requests"][0]
        self.assertEqual(request["source"]["session_id"], agent.session_id)
        self.assertEqual(request["source"]["request_id"], "inspect")
        self.assertIn("CD-456", encode(packet(self.calls[-1])))

    def test_revisions_consolidation_and_later_reversion_do_not_silently_replace_task(self):
        chat = self.agent().conversation(history_policy=HistoryPolicy(100000))
        for text in ("Compare A-1 and B-2.", "Compare A-1 and B-2.", "Use C-3 instead.", "Compare A-1 and B-2."):
            chat.send(text)
        snap = chat.task_snapshot()
        self.assertEqual([r["content"] for r in snap["task"]["requests"]],
                         ["Compare A-1 and B-2.", "Use C-3 instead.", "Compare A-1 and B-2."])
        updated = chat.update_task("Compare A-1 and B-2; C-3 is no longer needed.", expected_revision=snap["revision"])
        self.assertEqual(updated["task"]["task_id"], snap["task"]["task_id"])
        self.assertEqual(updated["task"]["requests"][0]["source"]["kind"], "application")
        with self.assertRaises(ConflictError):
            chat.finish_task(expected_revision=snap["revision"])
        updated["task"]["requests"].clear()
        self.assertEqual(len(chat.task_snapshot()["task"]["requests"]), 1)

    def test_automatic_update_count_limit_rolls_back_new_request_and_keeps_all_old_updates(self):
        chat = self.agent().conversation(history_policy=HistoryPolicy(100000))
        for number in range(32):
            chat.send(f"Include pending item A-{number}.", request_id=f"r-{number}")
        before, history = chat.task_snapshot(), chat.history
        with self.assertRaises(TaskContextLimitExceeded) as caught:
            chat.send("Include pending item A-32.", request_id="r-32")
        self.assertEqual(caught.exception.updates, 33)
        self.assertEqual(chat.task_snapshot(), before)
        self.assertEqual(chat.history, history)
        self.assertIsNone(chat.pending)
        self.assertEqual(len(self.calls), 32)

    def test_budget_failure_can_be_consolidated_before_explicit_retry(self):
        chat = self.agent(config=RelayConfig(db_path=self.path, max_chars=3000, recent_turns=1)).conversation()
        initial = chat.task_snapshot()
        chat.update_task("Long task detail " + "x" * 3000, expected_revision=initial["revision"])
        with self.assertRaises(BudgetExceeded):
            chat.send("Continue.", request_id="retry-me")
        self.assertEqual(self.calls, [])
        self.assertEqual(chat.pending["request_id"], "retry-me")
        chat.update_task("Inspect A-73 and compare it with B-19.", expected_revision=chat.task_snapshot()["revision"])
        chat.retry()
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(chat.pending)
        self.assertEqual(len(chat.task_snapshot()["task"]["requests"]), 1)

    def test_provider_cannot_edit_task_during_inference(self):
        def provider(**kwargs):
            revision = chat.task_snapshot()["revision"]
            for change in (lambda: chat.update_task("Erase original requirement", expected_revision=revision),
                           lambda: chat.finish_task(expected_revision=revision)):
                with self.assertRaises(ConversationBusy):
                    change()
            return "All finished."
        chat = self.agent(provider).conversation(history_policy=HistoryPolicy(100000))
        chat.send("Wait for ledger A-42 before exporting.")
        self.assertEqual(chat.task_snapshot()["task"]["status"], "open")

    def test_deletion_removes_task_payload_and_rejects_stale_plan_after_update(self):
        chat = self.agent().conversation(history_policy=HistoryPolicy(2))
        chat.send("Compare private invoice A-73 with B-19.")
        chat.send("Continue.")
        plan = chat.deletion_plan()
        chat.update_task("Comparison still pending.", expected_revision=chat.task_snapshot()["revision"])
        with self.assertRaisesRegex(ValueError, "changed"):
            chat.delete_history(expected=plan)
        result = chat.delete_history(expected=chat.deletion_plan())
        self.assertEqual(result["deleted"]["conversation_tasks"], 1)
        self.assertEqual(chat._store._connection.execute("SELECT COUNT(*) FROM conversation_tasks").fetchone()[0], 0)

    def test_rollover_one_off_continuation_pauses_without_reviving_old_default(self):
        from context_relay import MemoryClarificationRequired
        chat = self.agent(memory=MemoryPolicy()).conversation(history_policy=HistoryPolicy(2))
        chat.send("Export as CSV.", request_id="standing")
        chat.send("For this export only, use JSON.", request_id="one-off")
        self.assertEqual(chat.agent.memory.effective_snapshot()["entries"][0]["value"], "JSON")
        with self.assertRaises(MemoryClarificationRequired):
            chat.send("Continue.", request_id="ambiguous")
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(chat.agent.memory.effective_snapshot()["unresolved"])
        self.assertEqual(chat.agent.memory.snapshot()["entries"][0]["value"], "CSV")
        self.assertIn("For this export only, use JSON.",
                      [r["content"] for r in chat.task_snapshot()["task"]["requests"]])
        self.assertIsNone(chat.pending)  # Existing clarification response is a completed text turn.
        chat.send("Start an independent new export.", request_id="new-task")
        self.assertEqual(chat.agent.memory.effective_snapshot()["entries"][0]["value"], "CSV")
        self.assertFalse(chat.agent.memory.effective_snapshot()["unresolved"])
        self.assertEqual(len(self.calls), 3)

    def test_manual_rollover_scope_is_blocked_before_any_new_user_arrives(self):
        agent = self.agent(memory=MemoryPolicy())
        chat = agent.conversation()
        chat.send("Export as CSV.")
        chat.send("For this export only, use JSON.")
        successor = chat.rollover(expected_revision=chat.task_snapshot()["revision"])
        resumed = self.agent(session_id=successor, memory=MemoryPolicy())
        snapshot = resumed.memory.effective_snapshot()
        self.assertTrue(snapshot["unresolved"])
        self.assertEqual(snapshot["entries"][0]["value"], None)
        self.assertEqual(resumed.memory.snapshot()["entries"][0]["value"], "CSV")
        # The carried task remains historical evidence, while preference scope is unresolved.
        resumed.conversation().send("Start an independent export.")
        self.assertFalse(resumed.memory.effective_snapshot()["unresolved"])

    def test_async_context_capture_is_independent_for_concurrent_conversations(self):
        async def scenario():
            collected = []
            async def provider(*, messages):
                await asyncio.sleep(0)
                collected.append(packet(messages)["state"]["request_data"]["managed_conversation"])
                return "Waiting."
            one, two = self.agent(provider).conversation(), self.agent(provider).conversation()
            for chat, identifier in ((one, "A-73"), (two, "B-19")):
                chat.update_task(f"Inspect {identifier}.", expected_revision=chat.task_snapshot()["revision"])
            await asyncio.gather(one.send("Continue."), two.send("Continue."))
            self.assertEqual({item["active_session_id"] for item in collected}, {one.session_id, two.session_id})
            contents = [item["task"]["requests"][0]["content"] for item in collected]
            self.assertCountEqual(contents, ["Inspect A-73.", "Inspect B-19."])
        asyncio.run(scenario())


class ManagedCoverageBudgetTests(unittest.TestCase):
    def test_exact_budget_accounting_across_all_varying_digit_boundaries(self):
        for archived in (0, 9, 10, 99):
            with self.subTest(archived=archived):
                messages = []
                for number in range(6):
                    messages.extend([{"role": "user", "content": f"q{number}"},
                                     {"role": "assistant", "content": f"a{number}"}])
                messages.append({"role": "user", "content": "Continue"})
                state = {"request_data": {"managed_conversation": {
                    "archived_messages": archived, "archived_segments": 1, "task": None}}}
                full = compile_context(messages, state=state, recent_turns=1)
                minimum = compile_context(messages[-1:], state=state, recent_turns=1).report["size_chars"]
                # Sweep each possible inclusion threshold; no false fit or false rejection.
                for limit in range(minimum, full.report["size_chars"] + 1):
                    try:
                        plan = compile_context(messages, state=state, recent_turns=1, max_chars=limit)
                    except BudgetExceeded as error:
                        self.assertGreater(error.size_chars, limit)
                        continue
                    self.assertEqual(plan.report["size_chars"], len(encode(plan.messages)))
                    self.assertLessEqual(plan.report["size_chars"], limit)
                    omitted = plan.report["coverage"]["omitted_messages"]
                    self.assertEqual(plan.report["coverage"]["lineage_omitted_messages"], archived + omitted)
                    # One more same-size old turn must not fit if any were excluded.
                    if omitted:
                        next_count = plan.report["coverage"]["included_messages"] + 2
                        next_coverage = dict(plan.report["coverage"], included_messages=next_count,
                                             omitted_messages=omitted - 2,
                                             lineage_omitted_messages=archived + omitted - 2)
                        old_payload = packet(plan.messages)
                        new_content = plan.messages[-2]["content"].split("{", 1)[0] + encode({
                            "coverage": next_coverage, "state": old_payload["state"]})
                        candidate = messages[:2] + plan.messages
                        candidate[-2] = {"role": "user", "content": new_content}
                        self.assertGreater(len(encode(candidate)), limit)

    def test_invalid_archive_counts_do_not_make_misleading_coverage(self):
        for count in (-1, True, "9", None):
            with self.subTest(count=count), self.assertRaises(ValueError):
                compile_context([{"role": "user", "content": "Continue"}], state={
                    "request_data": {"managed_conversation": {"archived_messages": count}}})


if __name__ == "__main__":
    unittest.main()
