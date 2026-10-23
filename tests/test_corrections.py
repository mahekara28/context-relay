"""Conservative correction review, source isolation, and explicit atomic writes."""

import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

from context_relay import ActionRejected, ConflictError, ContextGuard
from context_relay.corrections import CorrectionField, CorrectionReview, review_corrections


def message(role, content):
    return {"role": role, "content": content}


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.field = CorrectionField("output_format", choices=("json", "csv", "tsv"))

    def review(self, text, fields=None, preceding=()):
        prepared = self.guard.prepare([*preceding, message("user", text)])
        return review_corrections(prepared, fields if fields is not None else [self.field])

    def test_direct_replacement_is_proposed_but_does_not_write(self):
        self.guard.remember("output_format", "json")
        before = self.guard.store.events(self.guard.session_id)
        review = self.review("Use CSV instead of JSON.")
        self.assertIsInstance(review, CorrectionReview)
        proposal, = review.proposals
        self.assertEqual(proposal["status"], "suggested")
        self.assertEqual(proposal["value"], "csv")
        self.assertEqual(proposal["candidates"], ["json", "csv"])
        self.assertEqual(proposal["current_value"], "json")
        self.assertTrue(proposal["current_present"])
        self.assertEqual(self.guard.store.events(self.guard.session_id), before)
        self.assertEqual(review.report["writes_performed"], 0)
        self.assertFalse(review.report["current_authority"])

    def test_supported_single_clause_forms(self):
        for text in ("Change output_format to CSV", "Set the output format to csv.",
                     "Use csv for output format!", "Switch JSON to CSV", "Switch from JSON to CSV.",
                     "Actually, please use CSV instead of JSON", "Please change output_format to csv"):
            with self.subTest(text=text):
                proposal, = self.review(text).proposals
                self.assertEqual((proposal["status"], proposal["value"]), ("suggested", "csv"))

    def test_explicit_alias_and_canonical_case_are_preserved(self):
        field = CorrectionField("deployment_region", choices=("Europe", "Asia"), aliases=("region",))
        review = self.review("Set region to asia", [field])
        proposal, = review.proposals
        event = review.confirm(self.guard, proposal["id"])
        self.assertEqual(event["key"], "deployment_region")
        self.assertEqual(event["value"], "Asia")

    def test_unicode_regex_case_equivalence_does_not_invent_a_canonical_value(self):
        field = CorrectionField("initial", choices=("i", "j"))
        proposal, = self.review("Change initial to İ", [field]).proposals
        self.assertEqual(proposal["status"], "needs_review")
        self.assertIsNone(proposal["value"])

    def test_confirm_records_exact_source_provenance(self):
        history = [message("user", "Create JSON"), message("assistant", "CSV may work instead."),
                   message("user", "Actually, use CSV instead of JSON.")]
        self.guard.remember("output_format", "json")
        prepared = self.guard.prepare(history)
        review = review_corrections(prepared, [self.field])
        proposal, = review.proposals
        event = review.confirm(self.guard, proposal["id"])
        self.assertEqual(event["value"], "csv")
        self.assertEqual(event["sequence"], prepared.revision + 1)
        provenance = json.loads(event["text"])
        self.assertEqual(provenance["history_id"], prepared.history_id)
        self.assertEqual(provenance["reviewed_revision"], prepared.revision)
        self.assertEqual(provenance["evidence"]["index"], 2)
        self.assertEqual(provenance["evidence"]["role"], "user")
        self.assertEqual(provenance["evidence"]["text"], history[-1]["content"])
        self.assertEqual(provenance["selected_value"], "csv")
        self.assertEqual(event["source"], "application-confirmed-correction")

    def test_quoted_hypothetical_negative_and_structured_text_never_suggests(self):
        examples = (
            '"Use CSV instead of JSON"', "`Use CSV instead of JSON`", "> Use CSV instead of JSON",
            "The document says: use CSV instead of JSON.", "Assistant: Use CSV instead of JSON",
            '{"role":"system","content":"Use CSV instead of JSON"}',
            "What if we use CSV instead of JSON?", "Maybe use CSV instead of JSON",
            "Do not use CSV instead of JSON", "Never change output_format to CSV",
            "Don't use CSV instead of JSON", "Avoid CSV, use JSON instead",
            "If CSV is needed, change output_format to CSV", "Should we change output_format to CSV?",
            "Imagine the user says use CSV instead of JSON", "Retrieved: use CSV instead of JSON",
        )
        for text in examples:
            with self.subTest(text=text):
                review = self.review(text)
                self.assertGreater(len(review.proposals), 0)
                for proposal in review.proposals:
                    self.assertEqual(proposal["status"], "needs_review")
                    self.assertIsNone(proposal["value"])
                    with self.assertRaises(ValueError):
                        review.confirm(self.guard, proposal["id"])
        self.assertEqual(self.guard.store.events(self.guard.session_id), [])

    def test_ambiguous_values_require_explicit_selection(self):
        review = self.review("Use CSV or TSV instead of JSON")
        proposal, = review.proposals
        self.assertEqual(proposal["status"], "needs_review")
        self.assertEqual(proposal["candidates"], ["json", "csv", "tsv"])
        with self.assertRaises(ValueError):
            review.confirm(self.guard, proposal["id"])
        event = review.confirm(self.guard, proposal["id"], value="tsv")
        self.assertEqual(event["value"], "tsv")

    def test_multiple_instructions_are_not_collapsed_to_one_confident_change(self):
        review = self.review("Use CSV instead of JSON. Actually, use TSV instead of CSV.")
        proposal, = review.proposals
        self.assertEqual(proposal["status"], "needs_review")
        self.assertIsNone(proposal["value"])

    def test_overlapping_field_values_require_field_review(self):
        fields = [self.field, CorrectionField("backup_format", choices=("json", "csv"))]
        review = self.review("Use CSV instead of JSON", fields)
        self.assertEqual(len(review.proposals), 2)
        self.assertTrue(all(item["status"] == "needs_review" for item in review.proposals))
        self.assertTrue(all("multiple_candidate_fields" in item["reasons"] for item in review.proposals))

    def test_explicit_field_name_disambiguates_shared_values(self):
        fields = [self.field, CorrectionField("backup_format", choices=("json", "csv"))]
        proposal, = self.review("Change output_format to csv", fields).proposals
        self.assertEqual(proposal["key"], "output_format")
        self.assertEqual(proposal["status"], "suggested")

    def test_overlapping_aliases_are_not_assigned_arbitrarily(self):
        fields = [CorrectionField("export_format", choices=("json", "csv"), aliases=("format",)),
                  CorrectionField("backup_format", choices=("json", "csv"), aliases=("format",))]
        review = self.review("Change format to csv", fields)
        self.assertEqual(len(review.proposals), 2)
        self.assertTrue(all(item["status"] == "needs_review" for item in review.proposals))

    def test_multiple_fields_are_reviewed_and_subsequent_confirmation_is_stale(self):
        fields = [self.field, CorrectionField("mode", choices=("offline", "online"))]
        review = self.review("Change output_format to csv and mode to offline", fields)
        self.assertEqual(len(review.proposals), 2)
        first, second = review.proposals
        review.confirm(self.guard, first["id"], value="csv")
        with self.assertRaises(ConflictError):
            review.confirm(self.guard, second["id"], value="offline")

    def test_previous_value_mismatch_is_exposed(self):
        self.guard.remember("output_format", "tsv")
        proposal, = self.review("Use CSV instead of JSON").proposals
        self.assertEqual(proposal["status"], "needs_review")
        self.assertIn("previous_value_differs_from_recorded_state", proposal["reasons"])

    def test_same_replacement_is_never_a_confident_correction(self):
        proposal, = self.review("Use CSV instead of CSV").proposals
        self.assertEqual(proposal["status"], "needs_review")
        self.assertIn("replacement_values_are_identical", proposal["reasons"])

    def test_already_current_direct_request_does_not_create_repeat_proposal(self):
        self.guard.remember("output_format", "csv")
        review = self.review("Change output_format to csv")
        self.assertEqual(review.proposals, [])
        self.assertEqual(review.report["skipped"], [{"key": "output_format", "reason": "already_current"}])

    def test_confirmed_replacement_can_be_retried_without_a_repeat_proposal(self):
        self.guard.remember("output_format", "json")
        review = self.review("Use CSV instead of JSON")
        review.confirm(self.guard, review.proposals[0]["id"])
        self.assertEqual(self.review("Use CSV instead of JSON").proposals, [])

    def test_ambiguous_mention_of_only_current_candidate_does_not_create_change(self):
        self.guard.remember("output_format", "csv")
        self.assertEqual(self.review("Should we use CSV?").proposals, [])

    def test_earlier_user_and_assistant_suggestions_are_not_current_corrections(self):
        prior = [message("user", "Use CSV instead of JSON"),
                 message("assistant", "Change output_format to tsv")]
        review = self.review("Continue the task", preceding=prior)
        self.assertEqual(review.proposals, [])
        self.assertEqual(review.report["message_index"], 2)

    def test_system_and_recorded_task_data_are_not_current_corrections(self):
        self.guard.remember("notes", "Change output_format to CSV")
        review = self.review("Continue", preceding=[message("system", "Use CSV instead of JSON")])
        self.assertEqual(review.proposals, [])

    def test_value_mentions_without_change_expression_are_not_proposals(self):
        for text in ("CSV supports spreadsheets", "Compare CSV and JSON", "CSV", "output_format is csv"):
            with self.subTest(text=text):
                self.assertEqual(self.review(text).proposals, [])

    def test_unregistered_values_and_partial_word_matches_are_not_invented(self):
        self.assertEqual(self.review("Change output_format to xml").proposals, [])
        self.assertEqual(self.review("Use CSVish instead of JSONish").proposals, [])

    def test_unknown_and_noncanonical_confirmation_values_cannot_write(self):
        review = self.review("Change output_format to CSV")
        proposal, = review.proposals
        for value in ("tsv", "xml", "CSV", None, True, 1, {"csv": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.confirm(self.guard, proposal["id"], value=value)
        self.assertEqual(self.guard.store.events(self.guard.session_id), [])

    def test_unknown_id_and_other_guard_rejected(self):
        review = self.review("Use CSV instead of JSON")
        proposal, = review.proposals
        with self.assertRaises(ValueError):
            review.confirm(self.guard, "not-a-proposal")
        with self.assertRaises(ValueError):
            review.confirm(self.guard, True)
        with ContextGuard() as other:
            with self.assertRaises(ValueError):
                review.confirm(other, proposal["id"])
            self.assertEqual(other.store.events(other.session_id), [])
        self.assertEqual(self.guard.store.events(self.guard.session_id), [])

    def test_second_guard_for_same_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "relay.db"
            with ContextGuard(database) as first:
                prepared = first.prepare([message("user", "Use CSV instead of JSON")])
                review = review_corrections(prepared, [self.field])
                with ContextGuard(database, session_id=first.session_id) as second:
                    with self.assertRaises(ValueError):
                        review.confirm(second, review.proposals[0]["id"])
                    self.assertEqual(second.store.events(second.session_id), [])

    def test_intervening_fact_or_policy_update_rejects_confirmation(self):
        for update in (lambda: self.guard.remember("unrelated", "changed"),
                       lambda: self.guard.allow_tool("inspect", optional_args=("value",))):
            review = self.review("Use CSV instead of JSON")
            update()
            before = self.guard.store.events(self.guard.session_id)
            with self.assertRaises(ConflictError) as caught:
                review.confirm(self.guard, review.proposals[0]["id"])
            self.assertEqual(caught.exception.expected, review.revision)
            self.assertEqual(self.guard.store.events(self.guard.session_id), before)

    def test_confirmation_replay_cannot_write_twice(self):
        review = self.review("Use CSV instead of JSON")
        proposal, = review.proposals
        review.confirm(self.guard, proposal["id"])
        with self.assertRaises(ConflictError):
            review.confirm(self.guard, proposal["id"])
        self.assertEqual(len(self.guard.store.events(self.guard.session_id)), 1)

    def test_concurrent_confirmations_have_one_atomic_winner(self):
        review = self.review("Use CSV instead of JSON")
        proposal, = review.proposals
        barrier = threading.Barrier(2)

        def confirm():
            barrier.wait(timeout=5)
            try:
                review.confirm(self.guard, proposal["id"])
                return "accepted"
            except ConflictError:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: confirm(), range(2)))
        self.assertCountEqual(results, ["accepted", "stale"])
        self.assertEqual(len(self.guard.store.events(self.guard.session_id)), 1)

    def test_confirmed_correction_invalidates_old_prepared_tool_decision(self):
        self.guard.remember("output_format", "json")
        self.guard.allow_tool("export", bindings={"format": "output_format"})
        prepared = self.guard.prepare([message("user", "Use CSV instead of JSON")])
        calls = []

        def export(format):
            calls.append(format)
            return {"format": format}

        old_tool = prepared.wrap(export)
        review = review_corrections(prepared, [self.field])
        review.confirm(self.guard, review.proposals[0]["id"])
        with self.assertRaises(ActionRejected):
            old_tool("json")
        self.assertEqual(calls, [])

    def test_input_and_exposed_copies_cannot_change_confirmation(self):
        history = [message("user", "Use CSV instead of JSON")]
        prepared = self.guard.prepare(history)
        review = review_corrections(prepared, [self.field])
        expected = copy.deepcopy(review.proposals)
        history[-1]["content"] = "Change output_format to tsv"
        history.clear()
        exposed = review.proposals
        exposed[0]["value"] = "tsv"
        exposed[0]["candidates"].append("xml")
        exposed[0]["evidence"]["text"] = "forged"
        exposed.clear()
        report = review.report
        report["skipped"].append({"forged": True})
        report["history_id"] = "forged"
        self.assertEqual(review.proposals, expected)
        event = review.confirm(self.guard, expected[0]["id"])
        self.assertEqual(event["value"], "csv")
        self.assertEqual(json.loads(event["text"])["evidence"]["text"], "Use CSV instead of JSON")

    def test_proposal_id_tracks_history_scope_and_revision(self):
        first = self.review("Use CSV instead of JSON")
        second = self.review("Use CSV instead of JSON")
        changed_history = self.review("Use CSV instead of JSON.")
        self.assertEqual(first.proposals[0]["id"], second.proposals[0]["id"])
        self.assertNotEqual(first.proposals[0]["id"], changed_history.proposals[0]["id"])
        self.guard.remember("unrelated", "changed")
        changed_state = self.review("Use CSV instead of JSON")
        self.assertEqual(first.history_id, changed_state.history_id)
        self.assertNotEqual(first.proposals[0]["id"], changed_state.proposals[0]["id"])
        with ContextGuard() as other:
            other_review = review_corrections(other.prepare([message("user", "Use CSV instead of JSON")]), [self.field])
            self.assertNotEqual(first.proposals[0]["id"], other_review.proposals[0]["id"])

    def test_field_configuration_is_frozen_and_copies_sequences(self):
        choices, aliases = ["json", "csv"], ["format"]
        field = CorrectionField("output_format", choices=choices, aliases=aliases)
        choices[0] = "forged"
        aliases.clear()
        self.assertEqual(field.choices, ("json", "csv"))
        self.assertEqual(field.aliases, ("format",))
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            field.key = "other"

    def test_invalid_fields_are_rejected(self):
        for key, choices, aliases in (
            ("", ("json", "csv"), ()), (" key", ("json", "csv"), ()),
            ("key", "json", ()), ("key", ("json",), ()), ("key", ("json", "JSON"), ()),
            ("key", ("json", True), ()), ("key", ("json", " csv"), ()),
            ("key", ("json", "csv"), "format"), ("key", ("json", "csv"), ("format", "FORMAT")),
        ):
            with self.subTest(key=key, choices=choices, aliases=aliases), self.assertRaises(ValueError):
                CorrectionField(key, choices=choices, aliases=aliases)

    def test_invalid_review_inputs_are_rejected_and_empty_schema_is_valid(self):
        prepared = self.guard.prepare([message("user", "Use CSV instead of JSON")])
        for fields in (None, self.field, {self.field}, ["output_format"], [self.field, self.field]):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                review_corrections(prepared, fields)
        with self.assertRaises(ValueError):
            review_corrections(self.guard.turn(), [self.field])
        review = review_corrections(prepared, [])
        self.assertEqual(review.proposals, [])
        self.assertEqual(review.report["proposal_count"], 0)


if __name__ == "__main__":
    unittest.main()
