"""Explicit lineage deletion and optional indexed archive retrieval."""

import tempfile
import sqlite3
import unittest
from pathlib import Path

from context_relay import MemoryPolicy, RelayConfig, connect
from context_relay.conversation import ConversationBusy, ConversationPending, HistoryPolicy


class HistoryRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = RelayConfig(db_path=str(Path(temporary.name) / "relay.sqlite3"))
        self.agent = self.make_agent()
        self.chat = self.agent.conversation(history_policy=HistoryPolicy(50))

    def make_agent(self, **options):
        agent = connect(lambda **kwargs: "Done.", config=self.config, memory=MemoryPolicy(), **options)
        self.addCleanup(agent.close)
        return agent

    def enable_index(self):
        if not self.agent.guard.store.enable_history_index():
            self.skipTest("Bundled SQLite has no FTS5")

    def test_index_backfills_old_rows_and_maintains_new_rows(self):
        self.chat.send("Invoice oceanic1")
        self.enable_index()
        self.assertTrue(self.agent.guard.store.enable_history_index())
        self.chat.send("Invoice oceanic2")
        results = self.chat.search_history("invoice", mode="terms")
        self.assertEqual([r["message"]["content"] for r in results["messages"]],
                         ["Invoice oceanic1", "Invoice oceanic2"])
        self.assertEqual(results["scanned"], 2)

    def test_index_search_is_word_and_not_substring_search(self):
        self.enable_index()
        self.chat.send("Invoice blue report")
        self.assertEqual(len(self.chat.search_history("blue invoice", mode="terms")["messages"]), 1)
        self.assertEqual(self.chat.search_history("voice", mode="terms")["messages"], [])
        self.assertEqual(len(self.chat.search_history("voice")["messages"]), 1)

    def test_fts_operators_cannot_escape_the_requested_terms(self):
        self.enable_index()
        self.chat.send("Alpha secret")
        self.assertEqual(self.chat.search_history('alpha OR missing', mode="terms")["messages"], [])
        self.assertEqual(self.chat.search_history('alpha" NOT missing*', mode="terms")["messages"], [])
        with self.assertRaises(ValueError):
            self.chat.search_history('***', mode="terms")

    def test_indexed_pages_respect_scope_and_old_segment_boundary(self):
        self.enable_index()
        old_id = self.chat.session_id
        self.chat.send("Unique invoice old")
        old_chat = self.agent.conversation()
        self.chat.send("Unique invoice new")
        other = self.make_agent().conversation()
        other.send("Unique invoice secret")
        page = self.chat.search_history("unique invoice", mode="terms", limit=1)
        self.assertEqual(page["messages"][0]["session_id"], old_id)
        more = self.chat.search_history("unique invoice", mode="terms", limit=1, after=page["next_cursor"])
        self.assertEqual(more["messages"][0]["message"]["content"], "Unique invoice new")
        self.assertIsNone(more["next_cursor"])
        self.assertEqual(len(old_chat.search_history("unique", mode="terms")["messages"]), 1)

    def test_index_requires_explicit_setup_literal_fallback_available(self):
        self.chat.send("Invoice")
        self.assertFalse(self.agent.guard.store.history_index_enabled)
        with self.assertRaisesRegex(ValueError, "Enable"):
            self.chat.search_history("invoice", mode="terms")
        self.assertEqual(len(self.chat.search_history("invoice")["messages"]), 1)

    def test_delete_removes_entire_lineage_and_all_payload_tables(self):
        self.enable_index()
        self.chat.send("Use JSON for export format.")
        self.agent.remember("owner", "Mira")
        self.chat.send("Continue")
        successor = self.chat.agent
        successor.allow_tool("publish")
        successor.guard.relay.check(successor.session_id, {
            "tool": "publish", "arguments": {},
            "context_revision": successor.guard.store.get_session(successor.session_id)["revision"],
        })
        root = self.agent.session_id
        # Deleting from the old segment still explicitly previews all successors.
        old = self.agent.conversation()
        plan = old.deletion_plan()
        self.assertEqual(plan["root_id"], root)
        self.assertEqual(len(plan["sessions"]), 2)
        removed = old.delete_history(expected=plan)
        self.assertEqual(removed["deleted"]["sessions"], 2)
        self.assertGreater(removed["deleted"]["events"], 0)
        self.assertGreater(removed["deleted"]["automatic_memory"], 0)
        self.assertGreater(removed["deleted"]["checks"], 0)
        for table in ("sessions", "conversation_segments", "conversation_messages", "conversation_requests",
                      "automatic_memory", "facts", "policies", "checks", "events", "conversation_search"):
            self.assertEqual(self.agent.guard.store._connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)
        with self.assertRaises(KeyError):
            successor.guard.turn()

    def test_delete_leaves_unrelated_sessions_and_their_index_intact(self):
        self.enable_index()
        self.chat.send("Delete unique phrase")
        other = self.make_agent().conversation()
        other.send("Preserve unique phrase")
        self.chat.delete_history(expected=self.chat.deletion_plan())
        self.assertEqual(len(other.search_history("unique", mode="terms")["messages"]), 1)
        self.assertEqual(len(other.history), 2)

    def test_delete_removes_old_tokens_from_live_fts_shadow_rows(self):
        self.enable_index()
        self.chat.send("secretuniquezebraword")
        other = self.make_agent().conversation()
        other.send("Preserve unrelated original")
        connection = self.agent.guard.store._connection
        before = b"".join(row[0] for row in connection.execute("SELECT block FROM conversation_search_data"))
        self.assertIn(b"secretuniquezebraword", before)
        self.chat.delete_history(expected=self.chat.deletion_plan())
        after = b"".join(row[0] for row in connection.execute("SELECT block FROM conversation_search_data"))
        self.assertNotIn(b"secretuniquezebraword", after)
        self.assertEqual(len(other.search_history("unrelated", mode="terms")["messages"]), 1)

    def test_stale_plan_rejects_completed_turn_even_if_no_revision_change(self):
        chat = self.agent.conversation()
        plan = chat.deletion_plan()
        chat.send("Neutral chatter")
        with self.assertRaisesRegex(ValueError, "changed"):
            chat.delete_history(expected=plan)
        self.assertEqual(len(chat.history), 2)

    def test_stale_plan_rejects_memory_or_policy_changes(self):
        plan = self.chat.deletion_plan()
        self.agent.remember("owner", "Mira")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.chat.delete_history(expected=plan)

    def test_stale_plan_rejects_new_check_records(self):
        plan = self.chat.deletion_plan()
        self.agent.guard.relay.check(self.agent.session_id, {"tool": "unknown", "arguments": {}, "context_revision": 0})
        with self.assertRaisesRegex(ValueError, "changed"):
            self.chat.delete_history(expected=plan)

    def test_no_fts_module_returns_false_without_affecting_history(self):
        store = self.agent.guard.store
        underlying = store._connection
        class NoFTS:
            def __getattr__(self, key):
                return getattr(underlying, key)
            def execute(self, statement, *args):
                if statement.startswith("CREATE VIRTUAL TABLE"):
                    raise sqlite3.OperationalError("no such module: fts5")
                return underlying.execute(statement, *args)
        store._connection = NoFTS()
        try:
            self.assertFalse(store.enable_history_index())
            self.assertFalse(store.history_index_enabled)
            self.chat.send("Invoice")
            self.assertEqual(len(self.chat.search_history("invoice")["messages"]), 1)
        finally:
            store._connection = underlying

    def test_pending_and_unresolved_claims_block_deletion(self):
        def failure(**kwargs):
            raise ConnectionError("offline")
        self.agent._model_call = failure
        with self.assertRaises(ConnectionError):
            self.chat.send("Pending")
        with self.assertRaises(ConversationPending):
            self.chat.delete_history(expected=self.chat.deletion_plan())
        with self.agent.guard.store._transaction(write=True):
            self.agent.guard.store._connection.execute("UPDATE conversations SET claim_token = 'orphan'")
        with self.assertRaises(ConversationBusy):
            self.chat.delete_history(expected=self.chat.deletion_plan())

    def test_inflight_provider_cannot_delete_its_lineage(self):
        def provider(**kwargs):
            with self.assertRaises(ConversationBusy):
                self.chat.delete_history(expected=self.chat.deletion_plan())
            return "Done."
        self.agent._model_call = provider
        self.chat.send("First")
        self.assertEqual(len(self.chat.history), 2)

    def test_prepared_tool_fails_after_deletion_before_external_execution(self):
        calls = []
        self.agent.allow_tool("publish")
        prepared = self.agent.guard.turn()
        wrapped = prepared.wrap(lambda: calls.append(True), tool="publish")
        self.chat.delete_history(expected=self.chat.deletion_plan())
        with self.assertRaises(KeyError):
            wrapped()
        self.assertEqual(calls, [])
        with self.assertRaises(KeyError):
            _ = self.chat.history
        with self.assertRaises(KeyError):
            self.chat.deletion_plan()

    def test_executor_callback_cannot_delete_its_store(self):
        self.agent.allow_tool("publish")
        def callback():
            with self.assertRaisesRegex(ValueError, "callbacks"):
                self.chat.delete_history(expected=self.chat.deletion_plan())
            return {"done": True}
        self.agent.guard.turn().wrap(callback, tool="publish")()
        self.assertEqual(len(self.chat.deletion_plan()["sessions"]), 1)

    def test_delete_rolls_back_if_any_table_fails(self):
        self.enable_index()
        self.chat.send("Do not lose this original")
        self.agent.remember("owner", "Mira")
        connection = self.agent.guard.store._connection
        connection.execute("CREATE TRIGGER stop_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'blocked'); END")
        with self.assertRaises(Exception):
            self.chat.delete_history(expected=self.chat.deletion_plan())
        self.assertEqual(len(self.chat.history), 2)
        self.assertEqual(len(self.chat.search_history("original", mode="terms")["messages"]), 1)
        self.assertEqual(len(self.chat.deletion_plan()["sessions"]), 1)


if __name__ == "__main__":
    unittest.main()
