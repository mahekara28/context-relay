"""Bounded fallback retrieval and whole-turn fit for ranked lookup candidates."""

import copy
import json
import unittest
from unittest.mock import patch

from context_relay import RelayConfig, TokenBudget, connect
from context_relay.models import encode


def transcript():
    messages = [
        {"role": "system", "content": "Answer using original evidence."},
        {"role": "user", "content": "The export format is CSV. " + "x" * 500},
        {"role": "assistant", "content": "A complete export discussion reply. " + "y" * 500},
        {"role": "user", "content": "Another export record requires careful review. " + "z" * 10000},
        {"role": "assistant", "content": "The oversized record's complete reply."},
    ]
    for index in range(6):
        messages.extend([
            {"role": "user", "content": f"Unrelated scheduling note {index}. " + "a" * 500},
            {"role": "assistant", "content": f"Unrelated scheduling reply {index}. " + "b" * 500},
        ])
    messages.append({"role": "user", "content": "Which export format was requested?"})
    return messages


def identity(messages):
    packet = next(message["content"] for message in messages
                  if message["content"].startswith("APPLICATION-RECORDED TASK DATA"))
    return json.loads(packet[packet.index("{"):])["state"]["evidence_history_id"]


def answer(messages, *, source="m1", quote="The export format is CSV."):
    return {"answer": "CSV", "history_id": identity(messages),
            "citations": [{"source_id": source, "quote": quote}]}


class RecoveryLookupTests(unittest.TestCase):
    def connection(self, provider, **kwargs):
        kwargs.setdefault("config", RelayConfig(max_chars=4000, recent_turns=1, retrieve_turns=0))
        agent = connect(provider, **kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_after_one_fallback_bad_quote_retries_feedback_without_more_lookup(self):
        calls = []

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            if len(calls) == 1:
                return answer(messages, source="m999", quote="A fabricated quote")
            if len(calls) == 2:
                return answer(messages, quote="An incorrect paraphrase of the available record")
            return answer(messages)

        # A repeated search would introduce a huge, less useful second record.
        with patch("context_relay.grounding.EvidenceCatalog.search", side_effect=[
            [{"index": 1}], [{"index": 3}]
        ]) as search:
            result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=2)
        self.assertEqual(result.status, "cited")
        self.assertEqual(len(calls), 3)
        self.assertEqual(search.call_count, 1)
        first, second, third = result.report["attempts"]
        self.assertEqual(first["lookup_origin"], "user_request_fallback")
        self.assertEqual(second["action"], "retry_with_feedback")
        self.assertIsNone(second["lookup_origin"])
        self.assertEqual(second["requested_indices"], [])
        self.assertEqual(third["action"], "cited")
        self.assertEqual(result.report["recovered_indices"], [1, 2])
        self.assertTrue(any(row["content"].startswith("[Relay source: m1]\n") for row in calls[1]))
        self.assertFalse(any(row["content"].startswith("[Relay source: m3]\n") for row in calls[2]))

    def test_empty_fallback_search_is_not_repeated_on_later_invalid_citation(self):
        calls = []

        def provider(*, messages):
            calls.append(messages)
            return answer(messages, source="m999", quote="Fabricated")

        with patch("context_relay.grounding.EvidenceCatalog.search", return_value=[]) as search:
            result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=2)
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(len(calls), 3)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(result.report["recovered_indices"], [])

    def test_lower_ranked_oversized_candidate_is_dropped_without_truncating_first_turn(self):
        for origin in ("model_query", "user_request_fallback"):
            with self.subTest(origin=origin):
                calls = []
                original = transcript()
                before = copy.deepcopy(original)

                def provider(*, messages):
                    calls.append(copy.deepcopy(messages))
                    if len(calls) == 1:
                        if origin == "model_query":
                            return {"history_id": identity(messages), "need_evidence": "export format"}
                        return answer(messages, source="m999", quote="Fabricated")
                    return answer(messages)

                with patch("context_relay.grounding.EvidenceCatalog.search",
                           return_value=[{"index": 1}, {"index": 3}]):
                    result = self.connection(provider).run_grounded(messages=original, max_rounds=1)
                self.assertEqual(result.status, "cited")
                self.assertEqual(len(calls), 2)
                self.assertEqual(result.report["attempts"][0]["lookup_origin"], origin)
                self.assertEqual(result.report["attempts"][0]["requested_indices"], [1, 3])
                self.assertEqual(result.report["attempts"][0]["restored_candidate_indices"], [1])
                self.assertEqual(result.report["recovered_indices"], [1, 2])
                for index in (1, 2):
                    rendered = {**original[index], "content": f"[Relay source: m{index}]\n" + original[index]["content"]}
                    self.assertIn(rendered, calls[1])
                self.assertFalse(any(row["content"].startswith("[Relay source: m3]\n") for row in calls[1]))
                self.assertLessEqual(len(encode(calls[1])), 4000)
                self.assertEqual(original, before)
                recovered = result.prepared.recover_evidence([1], history_id=result.prepared.history_id)
                self.assertEqual(recovered.messages, original[1:3])

    def test_ranked_candidate_reduction_also_respects_supplied_counter(self):
        calls = []

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            if len(calls) == 1:
                return {"history_id": identity(messages), "need_evidence": "export format"}
            return answer(messages)

        budget = TokenBudget(4000, lambda messages: len(encode(messages)), counter_name="test-exact-units")
        agent = self.connection(provider, config=RelayConfig(max_chars=16000, recent_turns=1), token_budget=budget)
        with patch("context_relay.grounding.EvidenceCatalog.search", return_value=[{"index": 1}, {"index": 3}]):
            result = agent.run_grounded(messages=transcript(), max_rounds=1)
        self.assertEqual(result.status, "cited")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.report["recovered_indices"], [1, 2])
        self.assertLessEqual(result.prepared.report["token_budget"]["input_tokens"], 4000)
        self.assertEqual(result.prepared.report["token_budget"]["input_tokens"], len(encode(calls[-1])))

    def test_oversized_top_ranked_candidate_stops_with_budget_failure(self):
        calls = []

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            return {"history_id": identity(messages), "need_evidence": "export format"}

        with patch("context_relay.grounding.EvidenceCatalog.search", return_value=[{"index": 3}, {"index": 1}]):
            result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=2)
        self.assertEqual(result.status, "budget_exceeded")
        self.assertEqual(len(calls), 1)
        self.assertIsNone(result.answer)
        self.assertEqual(result.report["recovered_indices"], [])
        self.assertEqual(result.prepared.messages, calls[0])
        self.assertEqual(result.report["attempts"][0]["requested_indices"], [3, 1])

    def test_explicit_omitted_citations_are_all_required_and_never_reduced(self):
        calls = []

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            return {"answer": "Compare these original records", "history_id": identity(messages), "citations": [
                {"source_id": "m1", "quote": "The export format is CSV."},
                {"source_id": "m3", "quote": "Another export record requires careful review."},
            ]}

        with patch("context_relay.grounding.EvidenceCatalog.search", side_effect=AssertionError("Must not search")):
            result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=2)
        self.assertEqual(result.status, "budget_exceeded")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.report["attempts"][0]["lookup_origin"], "omitted_citation")
        self.assertEqual(result.report["attempts"][0]["requested_indices"], [1, 3])
        self.assertEqual(result.report["recovered_indices"], [])
        self.assertIsNone(result.answer)
        self.assertFalse(result.check.valid)
        self.assertEqual(result.prepared.messages, calls[0])


if __name__ == "__main__":
    unittest.main()
