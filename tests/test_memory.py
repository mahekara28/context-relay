"""Durable memory behavior and failure boundaries, without simulated model claims."""

import concurrent.futures
import hashlib
from pathlib import Path
import tempfile
import unittest

from context_relay.evidence import EvidenceArchive
from context_relay.memory import (
    AutomaticMemory, MemoryBudgetExceeded, MemoryConflictError,
    MemoryHistoryError, MemoryPolicy,
)
from context_relay.models import ConflictError, encode
from context_relay.store import RelayStore


def user(text):
    return {"role": "user", "content": text}


def extended(history, text):
    return history + [{"role": "assistant", "content": "Acknowledged."}, user(text)]


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = RelayStore()
        self.session = self.store.create_session("Memory test")["id"]
        self.memory = AutomaticMemory(self.store, self.session)

    def tearDown(self):
        self.store.close()

    def values(self, memory=None):
        return {e["key"]: e["value"] for e in (memory or self.memory).snapshot()["entries"]}

    def test_field_free_statements_and_imperatives(self):
        report = self.memory.process([user("The deployment region is eu-west-1.\nUse CSV for the export format.\nMy name is Ada.")])
        self.assertEqual(self.values(), {"deployment_region": "eu-west-1", "export_format": "CSV", "my_name": "Ada"})
        self.assertEqual(report["memory_entries"], 3)
        self.assertEqual(report["revision"], 1)

    def test_explicit_setter_and_named_preference(self):
        self.memory.process([user("Please set the output format to JSON. I prefer brief responses for the response style.")])
        # v0.14 recognizes the finite style instead of storing surrounding prose.
        self.assertEqual(self.values(), {"output_format": "JSON", "response_style": "brief"})

    def test_unique_old_value_replacement_is_automatic(self):
        history = [user("Use CSV for the export format.")]
        self.memory.process(history)
        report = self.memory.process(extended(history, "Actually, use JSON instead of CSV."))
        self.assertEqual(self.values(), {"export_format": "JSON"})
        self.assertEqual(report["changes"], [{"key": "export_format", "operation": "updated"}])
        self.assertEqual(self.memory.pending(), [])

    def test_explicit_declaration_correction_is_automatic(self):
        history = [user("The deployment region is us-east-1.")]
        self.memory.process(history)
        self.memory.process(extended(history, "Actually, the deployment region is eu-west-1."))
        self.assertEqual(self.values()["deployment_region"], "eu-west-1")

    def test_conflicting_unmarked_declaration_is_disputed(self):
        history = [user("The deployment region is us-east-1.")]
        self.memory.process(history)
        self.memory.process(extended(history, "The deployment region is eu-west-1."))
        snapshot = self.memory.snapshot()
        self.assertEqual(self.values()["deployment_region"], "us-east-1")
        self.assertTrue(snapshot["entries"][0]["disputed"])
        self.assertEqual(snapshot["pending_count"], 1)
        self.assertEqual(snapshot["unresolved"][0]["value"], "eu-west-1")
        self.assertEqual(snapshot["unresolved"][0]["reason"], "conflicting_declaration")

    def test_explicit_override_resolves_older_direct_dispute(self):
        history = [user("The export format is CSV.")]
        self.memory.process(history)
        history = extended(history, "The export format is JSON.")
        self.memory.process(history)
        self.memory.process(extended(history, "Actually, use TSV for the export format."))
        self.assertEqual(self.values(), {"export_format": "TSV"})
        self.assertEqual(self.memory.pending(), [])

    def test_reaffirmed_existing_value_resolves_dispute(self):
        history = [user("The export format is CSV.")]
        self.memory.process(history)
        history = extended(history, "The export format is JSON.")
        self.memory.process(history)
        self.memory.process(extended(history, "Use CSV for the export format."))
        self.assertEqual(self.values(), {"export_format": "CSV"})
        self.assertEqual(self.memory.pending(), [])

    def test_ambiguous_replacement_requires_captured_target(self):
        history = [user("Use CSV for the export format. Use CSV for the log format.")]
        self.memory.process(history)
        self.memory.process(extended(history, "Actually, use JSON instead of CSV."))
        proposal = self.memory.pending()[0]
        self.assertEqual(proposal["reason"], "ambiguous_target")
        self.assertTrue(all(e["disputed"] for e in self.memory.snapshot()["entries"]))
        with self.assertRaises(MemoryConflictError):
            self.memory.confirm(proposal["id"])
        with self.assertRaises(MemoryConflictError):
            self.memory.confirm(proposal["id"], key="invented")
        self.memory.confirm(proposal["id"], key="export_format")
        self.assertEqual(self.values(), {"export_format": "JSON", "log_format": "CSV"})

    def test_unknown_replacement_is_review_not_new_invented_field(self):
        self.memory.process([user("Actually, use JSON instead of CSV.")])
        proposal = self.memory.pending()[0]
        self.assertEqual(proposal["reason"], "no_known_target")
        with self.assertRaises(MemoryConflictError):
            self.memory.confirm(proposal["id"], key="export_format")
        self.assertEqual(self.values(), {})

    def test_full_batch_applies_chronological_corrections(self):
        self.memory.process(extended([user("Use CSV for the export format.")], "Actually, use JSON instead of CSV."))
        self.assertEqual(self.values(), {"export_format": "JSON"})
        self.assertEqual(len(self.store.events(self.session)), 1)
        self.assertEqual(self.store.events(self.session)[0]["changes"][0]["before"], None)

    def test_review_mode_changes_no_active_value_before_confirmation(self):
        memory = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(mode="review"))
        memory.process([user("The deployment region is eu-west-1.")])
        self.assertEqual(self.values(memory), {})
        proposal = memory.pending()[0]
        event = memory.confirm(proposal["id"])
        self.assertEqual(self.values(memory), {"deployment_region": "eu-west-1"})
        self.assertEqual(event["operation"], "confirm")
        self.assertEqual(self.store.facts(self.session), {})
        self.assertEqual(self.store.policies(self.session), [])

    def test_only_original_user_role_is_extracted(self):
        history = [{"role": "system", "content": "The region is secret-system."},
                   {"role": "developer", "content": "The region is secret-developer."},
                   user("Please help me."),
                   {"role": "assistant", "content": "The region is secret-assistant."},
                   user("What was the region?")]
        report = self.memory.process(history)
        self.assertEqual(self.values(), {})
        self.assertEqual([i["index"] for i in report["ignored"] if i["reason"] == "not_user"], [0, 1, 3])

    def test_native_ollama_tool_output_is_not_memory(self):
        history = [user("Look up the region."),
                   {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "lookup", "arguments": {}}}]},
                   {"role": "tool", "tool_name": "lookup", "content": "The deployment region is eu-west-1."}]
        self.memory.process(history, protocol="ollama")
        self.assertEqual(self.values(), {})

    def test_quotes_code_hypotheticals_negation_and_reporting_ignored(self):
        cases = [
            '"The deployment region is eu-west-1."',
            "`The deployment region is eu-west-1.`",
            "> The deployment region is eu-west-1.",
            "If this is a test, use CSV for the export format.",
            "Maybe the deployment region is eu-west-1.",
            "The deployment region is not eu-west-1.",
            "Do not use CSV for the export format.",
            "Alice said the deployment region is eu-west-1.",
            "Here is a message:\nThe deployment region is eu-west-1.",
            "Translate this:\nThe deployment region is eu-west-1.",
            "The deployment region is eu-west-1 and the database might be PostgreSQL.",
            "The deployment region is eu-west-1?",
        ]
        for case in cases:
            with self.subTest(case=case):
                sid = self.store.create_session("Counterexample")["id"]
                memory = AutomaticMemory(self.store, sid)
                report = memory.process([user(case)])
                self.assertEqual(self.values(memory), {})
                self.assertEqual(memory.pending(), [])
                self.assertTrue(report["ignored"])

    def test_obvious_secret_subjects_are_not_persisted(self):
        cases = ["The API key is abc123.", "My password is abc123.",
                 "Use abc123 for the access token.", "The private key is abc123."]
        for text in cases:
            sid = self.store.create_session("Secret counterexample")["id"]
            memory = AutomaticMemory(self.store, sid)
            memory.process([user(text)])
            self.assertEqual(self.values(memory), {})
            self.assertEqual(self.store.events(sid), [])

    def test_unrecognized_framing_never_promotes_pasted_sentence(self):
        frames = [
            "Is the following correct?", "Here are the notes to review:",
            "Can you validate this statement?", "For the demo:",
            "The PDF contains this sentence.", "The customer claims the following.",
            "Do a syntax check on this.", "Check the assertion below.",
            "Copied content starts here:", "Someone sent this yesterday.",
        ]
        for frame in frames:
            with self.subTest(frame=frame):
                sid = self.store.create_session("Framing regression")["id"]
                memory = AutomaticMemory(self.store, sid)
                report = memory.process([user(frame + "\nThe export format is CSV.")])
                self.assertEqual(self.values(memory), {})
                self.assertEqual(memory.pending(), [])
                self.assertTrue(report["ignored"])

    def test_unknown_trailing_context_makes_whole_message_ineligible(self):
        report = self.memory.process([user("The export format is CSV. Does that sound right?")])
        self.assertEqual(self.values(), {})
        self.assertEqual(report["ignored"], [{"index": 0, "reason": "mixed_or_unsupported_context"}])

    def test_mixed_message_does_not_partially_apply_earlier_candidates(self):
        self.memory.process([user("The deployment region is eu-west-1. Use CSV for the export format. Thanks for helping.")])
        self.assertEqual(self.values(), {})
        self.assertEqual(self.store.events(self.session), [])

    def test_unsupported_natural_statement_is_reported_without_claiming_memory(self):
        report = self.memory.process([user("I like explanations that get to the point.")])
        self.assertEqual(report["ignored"], [{"index": 0, "reason": "unsupported_statement"}])
        self.assertEqual(self.values(), {})

    def test_exact_provenance_and_source_spans(self):
        text = "  The deployment region is eu-west-1.\n Use CSV for the export format. "
        history = [user(text)]
        self.memory.process(history)
        entry = self.memory.snapshot()["entries"][1]
        source = entry["source"]
        self.assertEqual(source["quote"], text[source["start"]:source["end"]])
        self.assertEqual(source["index"], 0)
        self.assertEqual(source["role"], "user")
        self.assertEqual(source["history_id"], EvidenceArchive(history, scope_id=self.session).history_id)
        self.assertEqual(source["message_sha256"], hashlib.sha256(encode(history[0]).encode("utf-8")).hexdigest())
        self.assertNotIn("text", source)
        self.assertEqual(self.store.events(self.session)[0]["changes"][1]["after"]["source"]["text"], text)

    def test_snapshots_reports_and_inputs_are_defensive(self):
        history = [user("The deployment region is eu-west-1.")]
        report = self.memory.process(history)
        report["changes"].clear()
        history[0]["content"] = "corrupted"
        snapshot = self.memory.snapshot()
        snapshot["entries"][0]["value"] = "corrupted"
        self.assertEqual(self.values(), {"deployment_region": "eu-west-1"})

    def test_replaying_same_history_is_idempotent(self):
        history = [user("The deployment region is eu-west-1.")]
        first = self.memory.process(history)
        second = self.memory.process(history)
        self.assertEqual(second["status"], "unchanged")
        self.assertIsNone(second["event_id"])
        self.assertEqual(second["revision"], first["revision"])

    def test_repeated_same_value_and_ignored_messages_do_not_advance_revision(self):
        history = [user("The deployment region is eu-west-1.")]
        first = self.memory.process(history)
        history = extended(history, "The deployment region is eu-west-1.")
        second = self.memory.process(history)
        third = self.memory.process(extended(history, "Thanks for helping."))
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(second["revision"], third["revision"])

    def test_prefix_edit_truncation_and_branch_rejected(self):
        initial = [user("The deployment region is eu-west-1.")]
        history = extended(initial, "Continue working.")
        self.memory.process(history)
        cases = [initial, [user("The deployment region is us-east-1.")],
                 [history[0], history[1], user("Different branch.")]]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(MemoryHistoryError):
                self.memory.process(case)
        self.assertEqual(self.values()["deployment_region"], "eu-west-1")

    def test_protocol_switch_rejected(self):
        history = [user("The deployment region is eu-west-1.")]
        self.memory.process(history)
        with self.assertRaises(MemoryHistoryError):
            self.memory.process(history, protocol="ollama")

    def test_restart_resumes_cursor_and_latest_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.sqlite3"
            with RelayStore(path) as store:
                sid = store.create_session("Persistent")["id"]
                memory = AutomaticMemory(store, sid)
                history = [user("Use CSV for the export format.")]
                memory.process(history)
                history = extended(history, "Actually, use JSON instead of CSV.")
                memory.process(history)
            with RelayStore(path) as store:
                memory = AutomaticMemory(store, sid)
                result = memory.process(history)
                self.assertEqual(result["status"], "unchanged")
                self.assertEqual(self.values(memory), {"export_format": "JSON"})
                with self.assertRaises(MemoryHistoryError):
                    memory.process(history[:1])

    def test_sessions_are_isolated(self):
        self.memory.process([user("The deployment region is eu-west-1.")])
        other = AutomaticMemory(self.store, self.store.create_session("Other")["id"])
        other.process([user("The deployment region is us-east-1.")])
        self.assertEqual(self.values(other), {"deployment_region": "us-east-1"})
        self.assertEqual(self.values(), {"deployment_region": "eu-west-1"})

    def test_entry_cap_aborts_whole_batch_and_cursor(self):
        memory = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(max_entries=1))
        history = [user("The deployment region is eu-west-1. Use CSV for the export format.")]
        with self.assertRaises(MemoryBudgetExceeded):
            memory.process(history)
        self.assertEqual(self.values(memory), {})
        self.assertEqual(self.store.get_session(self.session)["revision"], 0)
        self.assertEqual(self.store.events(self.session), [])
        result = self.memory.process(history)
        self.assertEqual(result["memory_entries"], 2)

    def test_character_cap_aborts_without_silent_eviction(self):
        memory = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(max_chars=200))
        history = [user("The deployment region is eu-west-1.")]
        with self.assertRaises(MemoryBudgetExceeded):
            memory.process(history)
        self.assertEqual(self.values(memory), {})
        self.assertEqual(self.store.events(self.session), [])
        self.memory.process(history)
        self.assertEqual(self.values(), {"deployment_region": "eu-west-1"})

    def test_pending_queue_counts_against_entry_budget(self):
        memory = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(mode="review", max_entries=1))
        history = [user("The deployment region is eu-west-1. Use CSV for the export format.")]
        with self.assertRaises(MemoryBudgetExceeded):
            memory.process(history)
        self.assertEqual(memory.pending(), [])

    def test_pending_provenance_counts_against_character_budget(self):
        memory = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(mode="review", max_chars=250))
        with self.assertRaises(MemoryBudgetExceeded):
            memory.process([user("The deployment region is eu-west-1.")])
        self.assertEqual(memory.pending(), [])

    def test_input_limits_are_atomic(self):
        history = extended([user("The deployment region is eu-west-1.")], "A" * 100)
        policies = [MemoryPolicy(max_message_chars=80), MemoryPolicy(max_new_messages=2), MemoryPolicy(max_history_chars=100)]
        for policy in policies:
            with self.subTest(policy=policy):
                memory = AutomaticMemory(self.store, self.session, policy=policy)
                with self.assertRaises(MemoryBudgetExceeded):
                    memory.process(history)
                self.assertEqual(self.values(memory), {})
                self.assertEqual(self.store.events(self.session), [])

    def test_lower_resumed_budget_blocks_snapshot_but_allows_explicit_cleanup(self):
        self.memory.process([user("The deployment region is eu-west-1. Use CSV for the export format. My name is Ada.")])
        smaller = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(max_entries=1))
        with self.assertRaises(MemoryBudgetExceeded):
            smaller.snapshot(for_context=True)
        self.assertEqual(len(smaller.snapshot()["entries"]), 3)
        self.assertEqual(len(smaller.entries()), 3)
        smaller.forget("my_name")
        with self.assertRaises(MemoryBudgetExceeded):
            smaller.snapshot(for_context=True)
        smaller.forget("deployment_region")
        self.assertEqual(self.values(smaller), {"export_format": "CSV"})

    def test_full_entry_inspection_is_defensive(self):
        self.memory.process([user("The deployment region is eu-west-1.")])
        entries = self.memory.entries()
        self.assertEqual(entries[0]["source"]["text"], "The deployment region is eu-west-1.")
        entries[0]["source"]["text"] = "changed"
        entries[0]["value"] = "changed"
        self.assertEqual(self.memory.entries()[0]["value"], "eu-west-1")

    def test_dismissed_pending_does_not_return_on_replay(self):
        memory = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(mode="review"))
        history = [user("The deployment region is eu-west-1.")]
        memory.process(history)
        memory.dismiss(memory.pending()[0]["id"])
        memory.process(history)
        self.assertEqual(memory.pending(), [])
        self.assertEqual(self.values(memory), {})

    def test_forget_removes_active_and_related_pending_but_not_audit(self):
        history = [user("The deployment region is eu-west-1.")]
        self.memory.process(history)
        history = extended(history, "The deployment region is us-east-1.")
        self.memory.process(history)
        self.memory.forget("deployment_region")
        self.memory.process(history)
        self.assertEqual(self.values(), {})
        self.assertEqual(self.memory.pending(), [])
        self.assertEqual(len(self.store.events(self.session)), 3)
        self.memory.process(extended(history, "The deployment region is ap-south-1."))
        self.assertEqual(self.values(), {"deployment_region": "ap-south-1"})

    def test_confirmation_refuses_stale_basis(self):
        history = [user("The deployment region is eu-west-1.")]
        self.memory.process(history)
        history = extended(history, "The deployment region is us-east-1.")
        self.memory.process(history)
        first = self.memory.pending()[0]
        history = extended(history, "The deployment region is ap-south-1.")
        self.memory.process(history)
        second = self.memory.pending()[1]
        self.memory.confirm(first["id"])
        with self.assertRaises(MemoryConflictError):
            self.memory.confirm(second["id"])
        self.assertEqual(self.values(), {"deployment_region": "us-east-1"})

    def test_duplicate_pending_does_not_fill_queue(self):
        memory = AutomaticMemory(self.store, self.session, policy=MemoryPolicy(mode="review"))
        history = [user("The deployment region is eu-west-1.")]
        first = memory.process(history)
        second = memory.process(extended(history, "The deployment region is eu-west-1."))
        self.assertEqual(len(memory.pending()), 1)
        self.assertEqual(first["revision"], second["revision"])

    def test_undo_process_restores_prior_value_without_replaying_correction(self):
        history = [user("Use CSV for the export format.")]
        self.memory.process(history)
        history = extended(history, "Actually, use JSON instead of CSV.")
        result = self.memory.process(history)
        self.memory.undo(result["event_id"])
        self.assertEqual(self.values(), {"export_format": "CSV"})
        self.memory.process(history)
        self.assertEqual(self.values(), {"export_format": "CSV"})
        with self.assertRaises(MemoryConflictError):
            self.memory.undo(result["event_id"])

    def test_undo_initial_add_and_forget(self):
        history = [user("Use CSV for the export format.")]
        result = self.memory.process(history)
        forgotten = self.memory.forget("export_format")
        self.memory.undo(forgotten["id"])
        self.assertEqual(self.values(), {"export_format": "CSV"})
        with self.assertRaises(MemoryConflictError):
            self.memory.undo(result["event_id"])

    def test_undo_confirmation_restores_reviewable_proposal(self):
        history = [user("The deployment region is eu-west-1.")]
        self.memory.process(history)
        self.memory.process(extended(history, "The deployment region is us-east-1."))
        proposal = self.memory.pending()[0]
        event = self.memory.confirm(proposal["id"])
        self.memory.undo(event["id"])
        self.assertEqual(self.values(), {"deployment_region": "eu-west-1"})
        self.assertEqual(self.memory.pending()[0]["id"], proposal["id"])
        self.memory.confirm(proposal["id"])
        self.assertEqual(self.values(), {"deployment_region": "us-east-1"})

    def test_undo_refuses_overwriting_later_memory(self):
        history = [user("Use CSV for the export format.")]
        first = self.memory.process(history)
        self.memory.process(extended(history, "Use JSON for the export format."))
        with self.assertRaises(MemoryConflictError):
            self.memory.undo(first["event_id"])
        self.assertEqual(self.values(), {"export_format": "JSON"})

    def test_undo_other_session_or_nonmemory_event_rejected(self):
        event = self.store.record_fact(self.session, "format", "CSV")
        with self.assertRaises(KeyError):
            self.memory.undo(event["id"])
        other = AutomaticMemory(self.store, self.store.create_session("Other")["id"])
        report = other.process([user("The deployment region is eu-west-1.")])
        with self.assertRaises(KeyError):
            self.memory.undo(report["event_id"])

    def test_revision_guard_prevents_stale_batch(self):
        self.store.record_fact(self.session, "format", "CSV")
        with self.assertRaises(ConflictError):
            self.memory.process([user("The deployment region is eu-west-1.")], expected_revision=0)
        self.assertEqual(self.values(), {})
        self.memory.process([user("The deployment region is eu-west-1.")], expected_revision=1)
        with self.assertRaises(ConflictError):
            self.memory.forget("deployment_region", expected_revision=1)

    def test_mutation_inside_executor_callback_is_forbidden(self):
        self.store._callback_active = True
        try:
            with self.assertRaises(ValueError):
                self.memory.process([user("The deployment region is eu-west-1.")])
        finally:
            self.store._callback_active = False
        self.assertEqual(self.values(), {})

    def test_concurrent_stores_consume_same_prefix_once(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shared.sqlite3"
            with RelayStore(path) as first_store, RelayStore(path) as second_store:
                sid = first_store.create_session("Shared")["id"]
                memories = [AutomaticMemory(first_store, sid), AutomaticMemory(second_store, sid)]
                history = [user("The deployment region is eu-west-1.")]
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda memory: memory.process(history), memories))
                self.assertEqual(sorted(r["status"] for r in results), ["processed", "unchanged"])
                self.assertEqual(len(first_store.events(sid)), 1)

    def test_policy_types_bounds_and_immutability(self):
        for kwargs in ({"mode": "magic"}, {"mode": False}, {"max_entries": True},
                       {"max_chars": 0}, {"max_new_messages": -1}, {"max_message_chars": 1.5},
                       {"max_history_chars": 20000001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                MemoryPolicy(**kwargs)
        policy = MemoryPolicy()
        with self.assertRaises(AttributeError):
            policy.mode = "review"
        exported = policy.to_dict()
        exported["mode"] = "review"
        self.assertEqual(policy.mode, "auto")

    def test_invalid_constructor_and_message_shape(self):
        with self.assertRaises(TypeError):
            AutomaticMemory(object(), self.session)
        with self.assertRaises(TypeError):
            AutomaticMemory(self.store, self.session, policy={})
        with self.assertRaises(KeyError):
            AutomaticMemory(self.store, "missing")
        with self.assertRaises(ValueError):
            self.memory.process([{"role": "user", "content": [{"text": "example"}]}])
        with self.assertRaises(ValueError):
            self.memory.process([user("hello")], expected_revision=True)


if __name__ == "__main__":
    unittest.main()
