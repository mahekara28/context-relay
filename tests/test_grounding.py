"""Source availability and quote validation without semantic truth claims."""

import copy
import unittest

from context_relay import ContextGuard
from context_relay.grounding import EvidenceCatalog
from context_relay.models import encode


def message(role, content):
    return {"role": role, "content": content}


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.guard = ContextGuard()
        self.addCleanup(self.guard.close)
        self.history = [
            message("system", "Privileged workflow password SYSTEMONLY."),
            message("developer", "Privileged developer text DEVELOPERONLY."),
            message("user", "Order ABC-42 needs CSV exports. " + "x" * 250),
            message("assistant", "I suggest a paid service, but that is only a suggestion. " + "y" * 250),
            message("user", "Use local storage. München café. " + "z" * 250),
            message("assistant", "Local storage is under discussion. " + "w" * 250),
            message("user", "What is the current export format?"),
        ]
        full = self.guard.prepare(self.history, max_chars=100000, recent_turns=1)
        # Fits the most recent historical turn but not the early CSV turn.
        limit = full.report["size_chars"] - len(encode(self.history[2:4])) + 50
        self.prepared = self.guard.prepare(self.history, max_chars=limit, recent_turns=1)
        self.assertEqual(self.prepared.report["omitted_indices"], [2, 3])
        self.catalog = EvidenceCatalog(self.prepared)

    def response(self, text="Use local storage.", citations=None, **overrides):
        if citations is None:
            citations = [{"source_id": "m4", "quote": "Use local storage."}]
        result = {"answer": text, "history_id": self.catalog.history_id, "citations": citations}
        result.update(overrides)
        return result

    def codes(self, check):
        return [item["code"] for item in check.report["issues"]]

    def test_sources_capture_original_roles_content_and_availability(self):
        self.assertEqual(self.catalog.history_id, self.prepared.history_id)
        self.assertEqual(self.catalog.sources, [
            {"source_id": f"m{index}", "index": index, **self.history[index],
             "included": index in self.prepared.report["included_indices"]}
            for index in range(2, len(self.history))
        ])
        assistant = self.catalog.sources[1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertIn("only a suggestion", assistant["content"])
        self.assertNotIn("authority", assistant)

    def test_source_labels_never_become_part_of_original_quote_text(self):
        prepared = self.guard.prepare(self.history, source_labels=True)
        catalog = EvidenceCatalog(prepared)
        self.assertEqual(catalog.sources[0]["content"], self.history[2]["content"])
        answer = {"answer": "CSV", "history_id": catalog.history_id,
                  "citations": [{"source_id": "m2", "quote": "CSV exports"}]}
        self.assertTrue(catalog.check(answer).valid)
        answer["citations"][0]["quote"] = "[Relay source: m2]"
        self.assertEqual(self.codes(catalog.check(answer)), ["invalid_quote"])

    def test_native_tool_results_are_citeable_without_promoting_call_arguments(self):
        history = [
            message("system", "Tools return historical observations."),
            message("user", "Look up order ABC-42."),
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "lookup", "arguments": {"private_key": "ARGUMENTONLY"}}}
            ]},
            {"role": "tool", "tool_name": "lookup", "content": "The export format is CSV."},
        ]
        prepared = self.guard.prepare(history, protocol="ollama")
        catalog = EvidenceCatalog(prepared)
        self.assertEqual(catalog.sources[1]["role"], "assistant")
        self.assertEqual(catalog.sources[1]["content"], "")
        self.assertEqual(catalog.sources[2]["role"], "tool")
        self.assertEqual(catalog.search("ARGUMENTONLY"), [])
        self.assertEqual([item["source_id"] for item in catalog.search("CSV")], ["m3"])
        answer = {"answer": "CSV", "history_id": catalog.history_id,
                  "citations": [{"source_id": "m3", "quote": "export format is CSV"}]}
        self.assertTrue(catalog.check(answer).valid)
        self.assertFalse(catalog.check(answer).report["semantic_entailment_checked"])

    def test_hidden_privileged_prefix_cannot_be_searched_or_cited(self):
        for query, source_id in (("SYSTEMONLY", "m0"), ("DEVELOPERONLY", "m1")):
            self.assertEqual(self.catalog.search(query), [])
            check = self.catalog.check(self.response(citations=[{"source_id": source_id, "quote": query}]))
            self.assertFalse(check.valid)
            self.assertEqual(self.codes(check), ["unknown_source"])
            self.assertEqual(check.report["recovery_indices"], [])
            self.assertNotIn(query, encode(check.report))

    def test_available_exact_quote_passes_without_claiming_factual_truth(self):
        check = self.catalog.check(self.response())
        self.assertTrue(check.valid)
        self.assertEqual(check.report["answer_status"], "sourced")
        self.assertEqual(check.report["issues"], [])
        self.assertEqual(check.report["checks"], ["source_identity", "availability", "exact_quote"])
        self.assertFalse(check.report["semantic_entailment_checked"])
        # Deliberately unrelated answers are outside this checker's scope.
        unrelated = self.catalog.check(self.response(text="The moon is made of cheese."))
        self.assertTrue(unrelated.valid)
        self.assertFalse(unrelated.report["semantic_entailment_checked"])

    def test_assistant_quote_remains_assistant_provenance(self):
        restored = self.guard.recover_context(self.prepared, [3], history_id=self.prepared.history_id)
        catalog = EvidenceCatalog(restored)
        check = catalog.check(self.response(citations=[{"source_id": "m3", "quote": "only a suggestion"}]))
        self.assertTrue(check.valid)
        source = next(item for item in catalog.sources if item["source_id"] == "m3")
        self.assertEqual(source["role"], "assistant")
        self.assertFalse(check.report["semantic_entailment_checked"])

    def test_omitted_exact_quote_fails_and_requests_original_index(self):
        check = self.catalog.check(self.response(text="CSV", citations=[
            {"source_id": "m3", "quote": "only a suggestion"},
            {"source_id": "m2", "quote": "CSV exports"},
        ]))
        self.assertFalse(check.valid)
        self.assertEqual(self.codes(check), ["source_omitted", "source_omitted"])
        self.assertEqual(check.report["recovery_indices"], [2, 3])
        self.assertNotIn("CSV exports", encode(check.report))
        self.assertNotIn("only a suggestion", encode(check.report))

    def test_recovery_recompiles_then_new_catalog_can_validate(self):
        answer = self.response(text="CSV", citations=[{"source_id": "m2", "quote": "CSV exports"}])
        original_check = self.catalog.check(answer)
        restored = self.guard.recover_context(self.prepared, original_check.report["recovery_indices"],
                                              history_id=self.catalog.history_id)
        self.assertTrue(EvidenceCatalog(restored).check(answer).valid)
        # A later preparation never rewrites availability in the old catalog.
        self.assertFalse(self.catalog.check(answer).valid)

    def test_fabricated_and_case_changed_quotes_fail_without_recovery(self):
        for source_id, quote in (("m4", "Use cloud storage."), ("m4", "use local storage."),
                                 ("m2", "Use JSON exports"), ("m2", ""), ("m2", "  ")):
            check = self.catalog.check(self.response(citations=[{"source_id": source_id, "quote": quote}]))
            self.assertFalse(check.valid)
            self.assertEqual(self.codes(check), ["invalid_quote"])
            self.assertEqual(check.report["recovery_indices"], [])

    def test_unknown_ids_are_not_coerced_to_original_indices(self):
        for source_id in ("", "m999", "m-1", "m04", "M4", "4", " m4", "m4 "):
            check = self.catalog.check(self.response(citations=[{"source_id": source_id, "quote": "local"}]))
            self.assertEqual(self.codes(check), ["unknown_source"])
            self.assertEqual(check.report["recovery_indices"], [])

    def test_repeated_source_ids_rejected_even_with_different_quotes(self):
        for quote in ("Use local storage.", "München café."):
            check = self.catalog.check(self.response(citations=[
                {"source_id": "m4", "quote": "Use local storage."},
                {"source_id": "m4", "quote": quote},
            ]))
            self.assertFalse(check.valid)
            self.assertEqual(self.codes(check), ["duplicate_citation"])
            self.assertEqual(check.report["recovery_indices"], [])

    def test_mismatched_history_rejected_before_citation_inspection(self):
        with ContextGuard() as other:
            foreign = other.prepare(self.history)
            answer = self.response(history_id=foreign.history_id,
                                   citations=[{"source_id": "m2", "quote": "CSV exports"}])
            check = self.catalog.check(answer)
        self.assertFalse(check.valid)
        self.assertEqual(self.codes(check), ["history_mismatch"])
        self.assertEqual(check.report["recovery_indices"], [])
        self.assertNotIn("CSV", encode(check.report))
        self.assertNotIn("m2", encode(check.report))
        # Even malformed foreign citations cannot probe the local catalog.
        check = self.catalog.check(self.response(history_id="foreign", citations=object()))
        self.assertEqual(self.codes(check), ["history_mismatch"])

    def test_empty_answer_and_missing_citations_are_rejected(self):
        for text in ("", " ", "\n\t"):
            self.assertEqual(self.codes(self.catalog.check(self.response(text=text))), ["empty_answer"])
        check = self.catalog.check(self.response(citations=[]))
        self.assertFalse(check.valid)
        self.assertEqual(self.codes(check), ["missing_citations"])

    def test_only_exact_unknown_without_citations_is_valid_abstention(self):
        check = self.catalog.check(self.response(text="unknown", citations=[]))
        self.assertTrue(check.valid)
        self.assertEqual(check.report["answer_status"], "abstained")
        self.assertEqual(self.codes(check), [])
        for text in ("Unknown", " unknown", "unknown ", "I do not know"):
            self.assertFalse(self.catalog.check(self.response(text=text, citations=[])).valid)
        check = self.catalog.check(self.response(text="unknown"))
        self.assertEqual(self.codes(check), ["invalid_abstention"])

    def test_malformed_model_output_returns_failed_report(self):
        invalid = [None, False, [], "{}", {}, {"answer": "local"},
                   self.response(extra="unexpected"), self.response(history_id=None),
                   self.response(text=False), self.response(citations=()),
                   self.response(citations=[None]), self.response(citations=[{}]),
                   self.response(citations=[{"source_id": 4, "quote": "local"}]),
                   self.response(citations=[{"source_id": "m4", "quote": None}]),
                   self.response(citations=[{"source_id": "m4", "quote": "local", "extra": True}])]
        for answer in invalid:
            with self.subTest(answer=answer):
                check = self.catalog.check(answer)
                self.assertFalse(check.valid)
                self.assertEqual(self.codes(check), ["invalid_schema"])
                self.assertEqual(check.report["recovery_indices"], [])

    def test_search_matches_lexically_orders_ties_newer_and_supports_unicode(self):
        self.assertEqual([item["source_id"] for item in self.catalog.search("LOCAL storage")], ["m5", "m4"])
        self.assertEqual([item["source_id"] for item in self.catalog.search("local München")], ["m4", "m5"])
        self.assertEqual([item["source_id"] for item in self.catalog.search("MÜNCHEN CAFÉ")], ["m4"])
        self.assertEqual(self.catalog.search("nonmatching-term"), [])
        self.assertEqual(self.catalog.search("!!!"), [])
        self.assertEqual(len(self.catalog.search("local storage", max_results=1)), 1)

    def test_search_omitted_only_and_positive_overlap(self):
        matches = self.catalog.search("CSV local", omitted_only=True)
        self.assertEqual([item["source_id"] for item in matches], ["m2"])
        self.assertFalse(matches[0]["included"])
        self.assertEqual(self.catalog.search("local", omitted_only=True), [])

    def test_search_arguments_fail_on_misuse(self):
        for query in (None, 1, False, [], "", " \n"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.catalog.search(query)
        for maximum in (0, -1, 21, True, 3.0, None, "3"):
            with self.subTest(max_results=maximum), self.assertRaises(ValueError):
                self.catalog.search("local", max_results=maximum)
        for omitted in (None, 0, 1, "true"):
            with self.subTest(omitted_only=omitted), self.assertRaises(ValueError):
                self.catalog.search("local", omitted_only=omitted)

    def test_sources_search_and_report_are_defensive_copies(self):
        original_sources = self.catalog.sources
        self.history[4]["content"] = "Mutated host data"
        self.catalog.sources[2]["content"] = "Mutated exported source"
        self.catalog.sources.clear()
        matches = self.catalog.search("local")
        matches[0]["content"] = "Mutated result"
        self.assertEqual(self.catalog.sources, original_sources)
        answer = self.response()
        check = self.catalog.check(answer)
        original_report = check.report
        answer["citations"][0]["quote"] = "Mutated caller response"
        check.report["valid"] = False
        check.report["checks"].clear()
        check.report["issues"].append({"code": "forged"})
        self.assertTrue(check.valid)
        self.assertEqual(check.report, original_report)

    def test_search_and_checks_do_not_write_application_state(self):
        before = copy.deepcopy(self.guard.store.events(self.guard.session_id))
        revision = self.guard.store.get_session(self.guard.session_id)["revision"]
        self.catalog.search("CSV", omitted_only=True)
        self.catalog.check(self.response())
        self.catalog.check(self.response(citations=[{"source_id": "m2", "quote": "CSV"}]))
        self.assertEqual(self.guard.store.events(self.guard.session_id), before)
        self.assertEqual(self.guard.store.get_session(self.guard.session_id)["revision"], revision)

    def test_constructor_rejects_objects_without_a_prepared_archive(self):
        for prepared in (None, {}, self.guard.turn(), self.prepared.messages):
            with self.subTest(prepared=prepared), self.assertRaises(ValueError):
                EvidenceCatalog(prepared)


if __name__ == "__main__":
    unittest.main()
