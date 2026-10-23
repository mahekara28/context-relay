"""Evidence contracts through real Relay connections; providers are offline stubs."""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

from context_relay import (ContentPolicy, OutputPolicy, OutputRejected,
                           VerificationPolicy, connect)

HISTORY = [{"role": "user", "content": "Calculate the invoice and approve only within our limit."}]


def contract(**source_options):
    source = {"data": {"units": 2, "price": 10, "limit": 15}, "trusted": True,
              "independence_key": "billing-ledger", "observed_at": time.time(),
              "max_age_seconds": 3600, **source_options}
    total = {"op": "multiply", "args": [{"source": "ledger", "path": "/units"},
                                          {"source": "ledger", "path": "/price"}]}
    return VerificationPolicy(sources={"ledger": source}, claims={
        "total": total, "approved": {"op": "lte", "args": [total, {"source": "ledger", "path": "/limit"}]}})


class VerifiedOutputTests(unittest.TestCase):
    def agent(self, provider, **kw):
        agent = connect(provider, **kw)
        self.addCleanup(agent.close)
        return agent

    def test_correct_result_reports_bounded_checks(self):
        text = '{"total":20,"approved":false}'
        result = self.agent(lambda **kw: text, output=OutputPolicy(format="json", verification=contract())).invoke(messages=HISTORY)
        self.assertIs(result.response, text)
        self.assertTrue(result.output_report.verification.valid)
        self.assertFalse(result.output_report.semantic_meaning_checked)
        self.assertIn("verified_claims", result.output_report.checks)

    def test_wrong_total_decision_extra_prose_and_bool_numeric_cannot_pass(self):
        for text in ('{"total":2000,"approved":false}', '{"total":20,"approved":true}',
                     '{"total":20,"approved":false,"explanation":"Everyone should buy this"}',
                     '{"total":true,"approved":false}', '{"total":20,"total":2000,"approved":false}'):
            with self.subTest(text=text), self.assertRaises(OutputRejected):
                self.agent(lambda **kw: text, output=OutputPolicy(format="json", verification=contract()))(messages=HISTORY)

    def test_bad_sources_stop_before_provider_even_when_retries_enabled(self):
        for opts in ({"trusted": False}, {"observed_at": time.time() - 7200}, {"observed_at": time.time() + 3600}):
            calls = []
            with self.subTest(opts=opts), self.assertRaises(OutputRejected) as caught:
                self.agent(lambda **kw: calls.append(kw), output=OutputPolicy(format="json", verification=contract(**opts), max_retries=1))(messages=HISTORY)
            self.assertEqual(calls, [])
            self.assertEqual(caught.exception.attempts, 0)
            self.assertFalse(caught.exception.verification.report["ready"])

    def test_source_expiry_during_provider_stops_without_retry(self):
        now = time.time()
        policy = contract(observed_at=now, max_age_seconds=1)
        calls = []
        def provider(**kw):
            calls.append(kw)
            clock.return_value = now + 2
            return '{"total":20,"approved":false}'
        with patch("context_relay.verification.time.time", return_value=now) as clock:
            with self.assertRaises(OutputRejected) as caught:
                self.agent(provider, output=OutputPolicy(format="json", verification=policy, max_retries=1))(messages=HISTORY)
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.issues, ("verification_unavailable",))

    def test_bad_candidate_gets_one_checked_retry(self):
        calls = []
        def provider(**kw):
            calls.append(kw)
            return '{"total":2000,"approved":true}' if len(calls) == 1 else '{"total":20,"approved":false}'
        result = self.agent(provider, output=OutputPolicy(format="json", verification=contract(), max_retries=1)).invoke(messages=HISTORY)
        self.assertEqual(result.output_report.attempts, 2)
        self.assertIn("verification_failed", json.dumps(calls[-1]))
        self.assertNotIn('2000', json.dumps(calls[-1]))

    def test_content_and_verification_both_apply(self):
        source = {"total": 20, "approved": False}
        policy = OutputPolicy(format="json", content=ContentPolicy(reference=source, exact=("/total",)), verification=contract())
        with self.assertRaises(OutputRejected):
            self.agent(lambda **kw: '{"total":20,"approved":true}', output=policy)(messages=HISTORY)

    def test_content_failure_cannot_be_overridden_by_valid_claims(self):
        policy = OutputPolicy(format="json", content=ContentPolicy(reference={"total": 99}, exact=("/total",)), verification=contract())
        with self.assertRaises(OutputRejected) as caught:
            self.agent(lambda **kw: '{"total":20,"approved":false}', output=policy)(messages=HISTORY)
        self.assertFalse(caught.exception.content.valid)

    def test_serialized_output_policy_preserves_contract(self):
        policy = OutputPolicy(format="json", verification=contract())
        restored = OutputPolicy(**policy.to_dict())
        self.assertEqual(restored.to_dict(), policy.to_dict())
        self.assertTrue(restored.verification.check({"total": 20, "approved": False}).valid)
        with self.assertRaises(ValueError):
            OutputPolicy(verification=contract())

    def test_async_flow_checks_before_return(self):
        async def run():
            async def provider(**kw): return '{"total":2000,"approved":true}'
            agent = self.agent(provider, output=OutputPolicy(format="json", verification=contract()))
            with self.assertRaises(OutputRejected):
                await agent(messages=HISTORY)
        asyncio.run(run())
