"""Bounded model/evidence round trips without external services or inference."""

import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from context_relay import BudgetExceeded, RelayConfig, connect
from context_relay.connection import CorrectionRequired
from context_relay.corrections import CorrectionField


def transcript(*, oversized=False, request="Which export format was requested?"):
    messages = [
        {"role": "system", "content": "Answer using the conversation's original evidence."},
        {"role": "user", "content": "The export format is CSV. UNIQUECSV " + "x" * (10000 if oversized else 500)},
        {"role": "assistant", "content": "An earlier export discussion. " + "y" * 500},
    ]
    for index in range(6):
        messages.extend([
            {"role": "user", "content": f"Unrelated scheduling discussion {index}. " + "a" * 500},
            {"role": "assistant", "content": f"Unrelated scheduling reply {index}. " + "b" * 500},
        ])
    messages.append({"role": "user", "content": request})
    return messages


def task_state(messages):
    packet = next(message["content"] for message in messages
                  if message.get("content", "").startswith("APPLICATION-RECORDED TASK DATA"))
    return json.loads(packet[packet.index("{"):])["state"]


def answer(messages, *, source_id="m1", quote="The export format is CSV.", text="CSV"):
    return {"answer": text, "history_id": task_state(messages)["evidence_history_id"],
            "citations": [{"source_id": source_id, "quote": quote}]}


def request_evidence(messages, query="UNIQUECSV"):
    return {"history_id": task_state(messages)["evidence_history_id"], "need_evidence": query}


class RecoveryLoopTests(unittest.TestCase):
    def test_failed_citation_searches_original_question_without_model_lookup_request(self):
        calls = []

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            if len(calls) == 1:
                return answer(messages, source_id="m999", quote="Fabricated quote", text="JSON")
            return answer(messages)

        result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=1)
        self.assertEqual(result.status, "cited")
        self.assertEqual(result.report["model_calls"], 2)
        self.assertEqual(result.report["recovered_indices"], [1, 2])
        self.assertEqual(result.report["attempts"][0]["lookup_origin"], "user_request_fallback")
        self.assertIn(1, result.prepared.report["required_indices"])
        self.assertFalse(any(row["content"].startswith("[Relay source: m1]\n") for row in calls[0]))
        self.assertTrue(any(row["content"].startswith("[Relay source: m1]\n") for row in calls[1]))

    def connection(self, provider, **kwargs):
        kwargs.setdefault("config", RelayConfig(max_chars=4000, recent_turns=1, retrieve_turns=0))
        agent = connect(provider, **kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_search_recovers_original_whole_turn_then_makes_a_new_cited_call(self):
        calls, responses = [], []
        messages = transcript()
        original = copy.deepcopy(messages)

        def provider(*, messages, **kwargs):
            calls.append((copy.deepcopy(messages), kwargs))
            response = request_evidence(messages) if len(calls) == 1 else answer(messages)
            responses.append(response)
            return response

        agent = self.connection(provider)
        before_events = agent.guard.store.events(agent.session_id)
        result = agent.run_grounded(messages=messages, model="fixture-model", temperature=0)
        self.assertEqual(result.status, "cited")
        self.assertIs(result.response, responses[-1])
        self.assertEqual(result.answer, responses[-1])
        self.assertTrue(result.check.valid)
        self.assertEqual(result.report["model_calls"], 2)
        self.assertEqual(result.report["recovery_rounds"], 1)
        self.assertEqual(result.report["recovered_indices"], [1, 2])
        self.assertTrue({1, 2}.issubset(result.prepared.report["required_indices"]))
        self.assertTrue(result.prepared.report["source_labels"])
        self.assertFalse(any(item["content"].startswith("[Relay source: m1]\n") for item in calls[0][0]))
        self.assertTrue(any(item["content"].startswith("[Relay source: m1]\n") for item in calls[1][0]))
        for call, kwargs in calls:
            self.assertEqual(kwargs, {"model": "fixture-model", "temperature": 0})
            self.assertIn("relay_grounding", task_state(call)["request_data"])
        self.assertEqual(messages, original)
        self.assertEqual(agent.guard.store.events(agent.session_id), before_events)
        recovered = result.prepared.recover_evidence([1], history_id=result.prepared.history_id)
        self.assertEqual(recovered.messages, original[1:3])
        self.assertFalse(result.report["semantic_entailment_checked"])

    def test_valid_quote_of_omitted_source_triggers_recovery_before_acceptance(self):
        calls = []

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            return answer(messages)

        result = self.connection(provider).run_grounded(messages=transcript())
        self.assertEqual(result.status, "cited")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.report["recovered_indices"], [1, 2])
        self.assertEqual(len(result.report["attempts"]), 2)

    def test_current_source_answer_needs_no_recovery(self):
        messages = transcript()
        source_id = f"m{len(messages) - 1}"
        result = self.connection(lambda **kwargs: answer(kwargs["messages"], source_id=source_id,
                                                         quote=messages[-1]["content"], text="The request asks for a format.")
                                 ).run_grounded(messages=messages)
        self.assertEqual(result.status, "cited")
        self.assertEqual(result.report["model_calls"], 1)
        self.assertEqual(result.report["recovery_rounds"], 0)
        self.assertEqual(result.report["recovered_indices"], [])

    def test_exact_unknown_is_abstained_not_cited(self):
        def provider(*, messages):
            return {"answer": "unknown", "history_id": task_state(messages)["evidence_history_id"], "citations": []}

        result = self.connection(provider).run_grounded(messages=transcript())
        self.assertEqual(result.status, "abstained")
        self.assertEqual(result.report["model_calls"], 1)
        self.assertEqual(result.answer["answer"], "unknown")
        self.assertEqual(result.check.report["answer_status"], "abstained")

    def test_no_matching_evidence_stops_without_an_extra_call(self):
        calls = []

        def provider(*, messages):
            calls.append(messages)
            return request_evidence(messages, "absolutelyunmatchedterm")

        result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=4)
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.answer)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.report["model_calls"], 1)
        self.assertEqual(result.report["recovered_indices"], [])

    def test_query_can_never_read_a_foreign_history(self):
        calls = []

        def provider(*, messages):
            calls.append(messages)
            return {"history_id": "another-conversation", "need_evidence": "UNIQUECSV"}

        with patch("context_relay.grounding.EvidenceCatalog.search", side_effect=AssertionError("must not search")):
            result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=1)
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.answer)
        self.assertLessEqual(len(calls), 2)
        self.assertEqual(result.report["recovered_indices"], [])
        self.assertNotIn(1, result.prepared.report["included_indices"])

    def test_invalid_answers_retry_with_feedback_but_stop_at_call_limit(self):
        calls = []

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            return answer(messages, source_id="m999", quote="Invented source")

        result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=2)
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.answer)
        self.assertFalse(result.check.valid)
        self.assertEqual(len(calls), 3)
        self.assertEqual(result.report["model_calls"], 3)
        self.assertEqual(len(result.report["attempts"]), 3)
        self.assertEqual(result.report["recovered_indices"], [1, 2])
        self.assertEqual(result.report["attempts"][0]["lookup_origin"], "user_request_fallback")
        self.assertNotEqual(task_state(calls[0])["request_data"], task_state(calls[1])["request_data"])

    def test_zero_rounds_permits_one_model_call_without_recovery(self):
        calls = []

        def provider(*, messages):
            calls.append(messages)
            return request_evidence(messages)

        result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=0)
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.report["recovered_indices"], [])

    def test_recovery_budget_failure_returns_last_called_snapshot_and_response(self):
        calls, responses = [], []

        def provider(*, messages):
            calls.append(copy.deepcopy(messages))
            response = request_evidence(messages)
            responses.append(response)
            return response

        result = self.connection(provider).run_grounded(messages=transcript(oversized=True))
        self.assertEqual(result.status, "budget_exceeded")
        self.assertIs(result.response, responses[0])
        self.assertIsNone(result.answer)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.report["model_calls"], 1)
        self.assertEqual(result.prepared.messages, calls[0])
        self.assertNotIn(1, result.prepared.report["included_indices"])

    def test_initial_budget_error_raises_without_calling_provider(self):
        calls = []
        agent = self.connection(lambda **kwargs: calls.append(kwargs), config=RelayConfig(max_chars=10))
        with self.assertRaises(BudgetExceeded):
            agent.run_grounded(messages=transcript())
        self.assertEqual(calls, [])

    def test_retry_observes_latest_facts_without_rewriting_the_original_history(self):
        states = []
        agent = None
        messages = transcript()

        def provider(*, messages):
            states.append(task_state(messages))
            if len(states) == 1:
                agent.remember("currency", "EUR", text="Host update while the first request is in flight")
                return request_evidence(messages)
            return answer(messages)

        agent = self.connection(provider)
        agent.remember("currency", "USD")
        result = agent.run_grounded(messages=messages)
        self.assertEqual(result.status, "cited")
        self.assertEqual([state["facts"]["currency"] for state in states], ["USD", "EUR"])
        self.assertEqual(states[0]["evidence_history_id"], states[1]["evidence_history_id"])
        self.assertGreater(states[1]["revision"], states[0]["revision"])

    def test_correction_gate_is_rechecked_after_state_changes_between_calls(self):
        calls = []
        agent = None

        def provider(*, messages):
            calls.append(messages)
            agent.remember("format", "JSON")
            return request_evidence(messages)

        agent = self.connection(provider, correction_fields=[CorrectionField("format", choices=["CSV", "JSON"])])
        agent.remember("format", "CSV")
        with self.assertRaises(CorrectionRequired):
            agent.run_grounded(messages=transcript(request="Use CSV instead of JSON."))
        self.assertEqual(len(calls), 1)

    def test_default_decoder_accepts_json_text_and_custom_decoder_preserves_sdk_identity(self):
        result = self.connection(lambda **kwargs: json.dumps(answer(kwargs["messages"]))).run_grounded(messages=transcript())
        self.assertEqual(result.status, "cited")
        self.assertIsInstance(result.response, str)
        responses = []

        class SDKResponse:
            def __init__(self, value):
                self.value = value

        def provider(*, messages):
            response = SDKResponse(answer(messages))
            responses.append(response)
            return response

        result = self.connection(provider).run_grounded(messages=transcript(), decoder=lambda response: response.value)
        self.assertEqual(result.status, "cited")
        self.assertIs(result.response, responses[-1])
        saved_answer = result.answer
        saved_report = result.report
        result.answer["citations"].clear()
        result.report["attempts"].clear()
        self.assertEqual(result.answer, saved_answer)
        self.assertEqual(result.report, saved_report)

    def test_malformed_json_is_rejected_without_a_successful_answer(self):
        calls = []

        def provider(*, messages):
            calls.append(messages)
            return "not json"

        result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=1)
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.answer)
        self.assertEqual(len(calls), 2)

    def test_provider_exception_propagates_unchanged_without_retry(self):
        failure = RuntimeError("offline model stopped")
        calls = []

        def provider(*, messages):
            calls.append(messages)
            raise failure

        with self.assertRaises(RuntimeError) as raised:
            self.connection(provider).run_grounded(messages=transcript())
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(calls), 1)

    def test_stream_is_rejected_without_iteration(self):
        consumed = []

        def chunks():
            consumed.append(True)
            yield "partial answer"

        stream = chunks()
        self.addCleanup(stream.close)
        with self.assertRaises(TypeError):
            self.connection(lambda **kwargs: stream).run_grounded(messages=transcript())
        self.assertEqual(consumed, [])

    def test_hidden_awaitable_is_rejected_without_execution(self):
        ran = []

        async def delayed():
            ran.append(True)
            return "unused"

        pending = delayed()
        self.addCleanup(pending.close)
        with self.assertRaises(TypeError):
            self.connection(lambda **kwargs: pending).run_grounded(messages=transcript())
        self.assertEqual(ran, [])

    def test_invalid_loop_parameters_and_native_protocol_never_call_provider(self):
        calls = []
        agent = self.connection(lambda **kwargs: calls.append(kwargs))
        for rounds in (-1, 5, True, 1.0, None):
            with self.subTest(max_rounds=rounds), self.assertRaises((TypeError, ValueError)):
                agent.run_grounded(messages=transcript(), max_rounds=rounds)
        for limit in (0, 21, True, 1.0, None):
            with self.subTest(search_limit=limit), self.assertRaises((TypeError, ValueError)):
                agent.run_grounded(messages=transcript(), search_limit=limit)
        for decoder in ("not callable", "", False, 0, []):
            with self.subTest(decoder=decoder), self.assertRaises((TypeError, ValueError)):
                agent.run_grounded(messages=transcript(), decoder=decoder)
        native = self.connection(lambda **kwargs: calls.append(kwargs), config=RelayConfig(protocol="ollama"))
        with self.assertRaises(ValueError):
            native.run_grounded(messages=transcript())
        self.assertEqual(calls, [])

    def test_async_and_generator_decoders_rejected_before_provider_call(self):
        calls = []
        agent = self.connection(lambda **kwargs: calls.append(kwargs))

        async def async_decoder(value):
            return value

        def generator_decoder(value):
            yield value

        async def async_generator_decoder(value):
            yield value

        for decoder in (async_decoder, generator_decoder, async_generator_decoder):
            with self.subTest(decoder=decoder), self.assertRaises(TypeError):
                agent.run_grounded(messages=transcript(), decoder=decoder)
        self.assertEqual(calls, [])

    def test_invalid_evidence_query_shapes_never_search_or_recover(self):
        for query in (None, False, "", "  ", "x" * 513):
            calls = []

            def provider(*, messages):
                calls.append(messages)
                return {"history_id": task_state(messages)["evidence_history_id"], "need_evidence": query}

            with self.subTest(query=query), patch("context_relay.grounding.EvidenceCatalog.search",
                                                 side_effect=AssertionError("must not search")):
                result = self.connection(provider).run_grounded(messages=transcript(), max_rounds=1)
            self.assertEqual(result.status, "unresolved")
            self.assertIsNone(result.answer)
            self.assertLessEqual(len(calls), 2)
            self.assertEqual(result.report["recovered_indices"], [])

    def test_host_and_provider_mutation_cannot_redirect_recovery_to_new_history(self):
        messages = transcript()
        captured = copy.deepcopy(messages)
        calls = []

        def provider(**kwargs):
            incoming = kwargs["messages"]
            calls.append(copy.deepcopy(incoming))
            if len(calls) == 1:
                proposal = request_evidence(incoming)
                incoming.clear()
                messages[1]["content"] = "Forged later history: JSON"
                return proposal
            return answer(incoming)

        result = self.connection(provider).run_grounded(messages=messages)
        self.assertEqual(result.status, "cited")
        evidence = result.prepared.recover_evidence([1], history_id=result.prepared.history_id)
        self.assertEqual(evidence.messages, captured[1:3])


class AsyncRecoveryLoopTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, provider, **kwargs):
        kwargs.setdefault("config", RelayConfig(max_chars=4000, recent_turns=1, retrieve_turns=0))
        agent = connect(provider, **kwargs)
        self.addCleanup(agent.close)
        return agent

    async def test_async_search_recovery_matches_sync_and_keeps_provider_kwargs(self):
        calls = []

        async def provider(*, messages, **kwargs):
            calls.append((copy.deepcopy(messages), kwargs))
            await asyncio.sleep(0)
            return request_evidence(messages) if len(calls) == 1 else answer(messages)

        result = await self.connection(provider).run_grounded(messages=transcript(), model="local")
        self.assertEqual(result.status, "cited")
        self.assertEqual(result.report["model_calls"], 2)
        self.assertEqual(result.report["recovered_indices"], [1, 2])
        self.assertEqual([kwargs for _, kwargs in calls], [{"model": "local"}] * 2)

    async def test_async_abstention_and_call_limit(self):
        async def abstain(*, messages):
            return {"answer": "unknown", "history_id": task_state(messages)["evidence_history_id"], "citations": []}

        result = await self.connection(abstain).run_grounded(messages=transcript())
        self.assertEqual(result.status, "abstained")
        calls = []

        async def invalid(*, messages):
            calls.append(messages)
            return answer(messages, source_id="m999", quote="invented")

        result = await self.connection(invalid).run_grounded(messages=transcript(), max_rounds=1)
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.answer)
        self.assertEqual(len(calls), 2)

    async def test_async_recovery_budget_failure_does_not_make_an_extra_call(self):
        calls = []

        async def provider(*, messages):
            calls.append(messages)
            return request_evidence(messages)

        result = await self.connection(provider).run_grounded(messages=transcript(oversized=True))
        self.assertEqual(result.status, "budget_exceeded")
        self.assertEqual(len(calls), 1)

    async def test_provider_cancellation_propagates_without_retry(self):
        calls = []
        cancellation = asyncio.CancelledError("caller cancelled")

        async def provider(*, messages):
            calls.append(messages)
            raise cancellation

        with self.assertRaises(asyncio.CancelledError) as raised:
            await self.connection(provider).run_grounded(messages=transcript())
        self.assertIs(raised.exception, cancellation)
        self.assertEqual(len(calls), 1)

    async def test_async_stream_is_rejected_without_iteration(self):
        consumed = []

        async def chunks():
            consumed.append(True)
            yield "chunk"

        stream = chunks()
        self.addAsyncCleanup(stream.aclose)

        async def provider(*, messages):
            return stream

        with self.assertRaises(TypeError):
            await self.connection(provider).run_grounded(messages=transcript())
        self.assertEqual(consumed, [])


if __name__ == "__main__":
    unittest.main()
