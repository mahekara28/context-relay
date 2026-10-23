"""Opt-in correction review stops existing model calls without automatic writes."""

import asyncio
import copy
import unittest

from context_relay import ConflictError, RelayConfig
from context_relay.connection import CorrectionRequired, connect
from context_relay.corrections import CorrectionField


def history(content="Use CSV instead of JSON"):
    return [{"role": "user", "content": content}]


class CorrectionConnectionTests(unittest.TestCase):
    def setUp(self):
        self.field = CorrectionField("output_format", choices=("json", "csv", "tsv"))
        self.calls = []

    def connection(self, **kwargs):
        def provider(*, messages, **options):
            self.calls.append((copy.deepcopy(messages), options))
            return {"response": "done"}

        agent = connect(provider, **kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_configured_correction_stops_before_model_and_does_not_write(self):
        agent = self.connection(correction_fields=(self.field,))
        agent.remember("output_format", "json")
        before = agent.guard.store.events(agent.session_id)
        with self.assertRaises(CorrectionRequired) as caught:
            agent(messages=history(), model="local-model")
        review = caught.exception.review
        self.assertEqual(review.proposals[0]["value"], "csv")
        self.assertEqual(self.calls, [])
        self.assertEqual(agent.guard.store.events(agent.session_id), before)
        self.assertEqual(agent.guard.turn().context["facts"]["output_format"], "json")

    def test_invoke_also_gates_and_confirmed_retry_uses_new_state(self):
        agent = self.connection(correction_fields=(self.field,))
        agent.remember("output_format", "json")
        with self.assertRaises(CorrectionRequired) as caught:
            agent.invoke(messages=history())
        review = caught.exception.review
        event = review.confirm(agent.guard, review.proposals[0]["id"])
        result = agent.invoke(messages=history(), temperature=0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result.prepared.revision, event["sequence"])
        self.assertEqual(result.prepared.context["facts"]["output_format"], "csv")
        self.assertEqual(result.response, {"response": "done"})
        self.assertEqual(self.calls[0][1], {"temperature": 0})
        self.assertTrue(any('"output_format":"csv"' in item["content"] for item in self.calls[0][0]))

    def test_inspection_does_not_call_provider_or_write(self):
        agent = self.connection(correction_fields=(self.field,))
        review = agent.review_corrections(history())
        self.assertEqual(review.proposals[0]["value"], "csv")
        self.assertEqual(self.calls, [])
        self.assertEqual(agent.guard.store.events(agent.session_id), [])

    def test_explicit_dismissal_preserves_facts_and_allows_exact_retry(self):
        agent = self.connection(correction_fields=(self.field,))
        agent.remember("output_format", "json")
        request = history('The report quotes: "Use CSV instead of JSON"')
        with self.assertRaises(CorrectionRequired) as caught:
            agent(messages=request)
        review = caught.exception.review
        self.assertEqual(review.proposals[0]["status"], "needs_review")
        before = agent.guard.store.events(agent.session_id)
        agent.dismiss_corrections(review)
        self.assertEqual(agent.guard.store.events(agent.session_id), before)
        result = agent.invoke(messages=request)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result.prepared.context["facts"]["output_format"], "json")
        self.assertEqual(agent.guard.store.events(agent.session_id), before)

    def test_changed_history_invalidates_dismissal(self):
        agent = self.connection(correction_fields=(self.field,))
        review = agent.review_corrections(history())
        agent.dismiss_corrections(review)
        agent(messages=history())
        with self.assertRaises(CorrectionRequired):
            agent(messages=history("Use CSV instead of JSON."))
        self.assertEqual(len(self.calls), 1)

    def test_changed_recorded_revision_invalidates_dismissal(self):
        agent = self.connection(correction_fields=(self.field,))
        review = agent.review_corrections(history())
        agent.dismiss_corrections(review)
        agent.remember("other_setting", "changed")
        with self.assertRaises(CorrectionRequired) as caught:
            agent(messages=history())
        self.assertGreater(caught.exception.review.revision, review.revision)
        self.assertEqual(self.calls, [])

    def test_cannot_dismiss_stale_or_foreign_review(self):
        agent = self.connection(correction_fields=(self.field,))
        other = self.connection(correction_fields=(self.field,))
        review = agent.review_corrections(history())
        with self.assertRaises(ValueError):
            other.dismiss_corrections(review)
        with self.assertRaises(ValueError):
            agent.dismiss_corrections(object())
        agent.remember("other_setting", "changed")
        with self.assertRaises(ConflictError):
            agent.dismiss_corrections(review)
        with self.assertRaises(CorrectionRequired):
            agent(messages=history())
        self.assertEqual(self.calls, [])

    def test_default_connection_keeps_existing_behavior(self):
        agent = self.connection()
        response = agent(messages=history())
        self.assertEqual(response, {"response": "done"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(agent.guard.store.events(agent.session_id), [])
        self.assertEqual(agent.review_corrections(history()).proposals, [])

    def test_schema_sequence_is_captured_at_connection(self):
        fields = [self.field]
        agent = self.connection(correction_fields=fields)
        fields.clear()
        with self.assertRaises(CorrectionRequired):
            agent(messages=history())
        self.assertEqual(self.calls, [])

    def test_modifying_exposed_review_does_not_bypass_gate(self):
        agent = self.connection(correction_fields=(self.field,))
        review = agent.review_corrections(history())
        exposed = review.proposals
        exposed.clear()
        with self.assertRaises(CorrectionRequired):
            agent(messages=history())
        self.assertEqual(agent.guard.store.events(agent.session_id), [])

    def test_no_proposal_for_assistant_suggestion_when_user_requests_continue(self):
        agent = self.connection(correction_fields=(self.field,))
        transcript = [
            {"role": "user", "content": "Use JSON."},
            {"role": "assistant", "content": "Use CSV instead of JSON"},
            {"role": "user", "content": "Continue"},
        ]
        agent(messages=transcript)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(agent.guard.store.events(agent.session_id), [])

    def test_native_tool_result_is_never_detected_as_user_correction(self):
        agent = self.connection(config=RelayConfig(protocol="ollama"), correction_fields=(self.field,))
        transcript = [
            {"role": "user", "content": "Use CSV instead of JSON"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search", "arguments": {"query": "formats"}}}
            ]},
            {"role": "tool", "tool_name": "search", "content": "Use TSV instead of JSON"},
        ]
        review = agent.review_corrections(transcript)
        self.assertEqual(review.proposals, [])
        self.assertFalse(review.report["inspected_user_message"])
        agent(messages=transcript)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0][-1]["role"], "tool")
        self.assertEqual(agent.guard.store.events(agent.session_id), [])

    def test_invalid_correction_field_configuration_is_rejected_early(self):
        for fields in (None, self.field, ["output_format"]):
            with self.subTest(fields=fields), self.assertRaises(TypeError):
                self.connection(correction_fields=fields)
        for fields in ([self.field, self.field],
                       [self.field, CorrectionField("OUTPUT_FORMAT", choices=("json", "csv"))]):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.connection(correction_fields=fields)
        self.assertEqual(self.calls, [])

    def test_closed_connection_rejects_review_and_dismissal(self):
        agent = self.connection(correction_fields=(self.field,))
        review = agent.review_corrections(history())
        agent.close()
        with self.assertRaises(RuntimeError):
            agent.review_corrections(history())
        with self.assertRaises(RuntimeError):
            agent.dismiss_corrections(review)


class AsyncCorrectionConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.field = CorrectionField("output_format", choices=("json", "csv"))
        self.calls = []

        async def provider(*, messages, **kwargs):
            self.calls.append((copy.deepcopy(messages), kwargs))
            await asyncio.sleep(0)
            return {"response": "async done"}

        self.agent = connect(provider, correction_fields=(self.field,))
        self.addCleanup(self.agent.close)

    async def test_async_gate_calls_no_provider_and_confirmed_retry_works(self):
        self.agent.remember("output_format", "json")
        before = self.agent.guard.store.events(self.agent.session_id)
        with self.assertRaises(CorrectionRequired) as caught:
            await self.agent(messages=history())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.agent.guard.store.events(self.agent.session_id), before)
        review = caught.exception.review
        review.confirm(self.agent.guard, review.proposals[0]["id"])
        result = await self.agent.invoke(messages=history(), temperature=0)
        self.assertEqual(result.response, {"response": "async done"})
        self.assertEqual(result.prepared.context["facts"]["output_format"], "csv")
        self.assertEqual(len(self.calls), 1)

    async def test_async_dismissal_and_revision_invalidation_match_sync(self):
        request = history("Should we use CSV instead of JSON?")
        with self.assertRaises(CorrectionRequired) as caught:
            await self.agent.invoke(messages=request)
        self.agent.dismiss_corrections(caught.exception.review)
        await self.agent(messages=request)
        self.agent.remember("other_setting", "changed")
        with self.assertRaises(CorrectionRequired):
            await self.agent(messages=request)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
