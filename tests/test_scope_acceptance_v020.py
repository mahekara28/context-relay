"""Independent v0.20 scope outcomes, frozen before parser implementation review.

These are offline boundary tests with scripted proposals, not model-quality evidence.
Literal business outcomes are intentional; do not derive them from Relay's parser.
"""

import copy
import unittest

from context_relay import ActionHarness, ContentPolicy, auto_connect


SEED = "Export as CSV. Reply in English. Set the project name to Sunrise."
EXPECTED_MEMORY = {
    "export_format": "CSV", "response_language": "English", "project_name": "Sunrise"
}
ORIGINAL = {
    "format": "CSV", "language": "English", "name": "ACME-17",
    "amount": 20, "note": "Keep this user's wording; do not translate it."
}
OBSERVATIONS = {}


def user(text):
    return {"role": "user", "content": text}


class ScopeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.effects = []
        self.provider_calls = []
        self.proposal = copy.deepcopy(ORIGINAL)
        self.agent = auto_connect(self.provider, provider="plain")
        self.history = [user(SEED)]
        self.agent.memory.process(self.history)
        self.agent.guard.remember("publish_permission", True)
        self.agent.guard.remember("approved_amount", 20)
        self.results = []

    def tearDown(self):
        OBSERVATIONS[self.id().rsplit(".", 1)[-1]] = {
            "memory": self.values(),
            "pending": self.agent.memory.pending(),
            "facts": self.agent.guard.store.facts(self.agent.session_id),
            "effects": copy.deepcopy(self.effects),
            "provider_calls": len(self.provider_calls),
            "execution_reports": copy.deepcopy(self.results),
        }
        self.agent.close()

    def provider(self, *, messages, **options):
        self.provider_calls.append(copy.deepcopy(messages))
        return {"tool": "publish", "arguments": copy.deepcopy(self.proposal)}

    def publish(self, format, language, name, amount, note):
        effect = {"format": format, "language": language, "name": name,
                  "amount": amount, "note": note}
        self.effects.append(copy.deepcopy(effect))
        return {"saved": True}

    def harness(self, *, repair=None):
        return ActionHarness(self.agent, tools={"publish": self.publish}, contracts={
            "publish": {
                "bindings": {"amount": "approved_amount"},
                "when": {"publish_permission": True},
                "content": ContentPolicy({"name": "ACME-17"}, exact=("/name",)),
                "unchecked": ("note",),
            }
        }, repair=repair)

    def values(self):
        return {entry["key"]: entry["value"] for entry in self.agent.memory.entries()}

    def observe(self, text):
        self.history.append(user(text))
        return self.agent.memory.process(self.history)

    def act(self, harness, text, **arguments):
        self.history.append(user(text))
        self.proposal = copy.deepcopy(ORIGINAL | arguments)
        result = harness.invoke(messages=self.history)
        self.results.append(result.execution)
        self.history.append({"role": "assistant", "content": "Action proposal processed."})
        return result

    def test_durable_change_survives_later_request(self):
        harness = self.harness()
        first = self.act(harness, "For all later exports, make the export format JSON.", format="JSON")
        second = self.act(harness, "Export another approved record.", format="JSON")
        self.assertTrue(first.executed)
        self.assertTrue(second.executed)
        self.assertEqual(self.effects, [ORIGINAL | {"format": "JSON"}] * 2)
        self.assertEqual(self.values(), EXPECTED_MEMORY | {"export_format": "JSON"})
        self.assertEqual(self.agent.memory.pending(), [])

    def test_one_export_override_expires(self):
        harness = self.harness()
        first = self.act(harness, "For this export only, use JSON.", format="JSON")
        second = self.act(harness, "Export the next approved record.")
        self.assertTrue(first.executed)
        self.assertTrue(second.executed)
        self.assertEqual(self.effects, [ORIGINAL | {"format": "JSON"}, ORIGINAL])
        self.assertEqual(self.values(), EXPECTED_MEMORY)
        self.assertEqual(self.agent.memory.pending(), [])

    def test_mixed_scope_keeps_only_durable_language(self):
        harness = self.harness()
        first = self.act(harness,
            "For this export only, use JSON; from now on, reply in Hindi.",
            format="JSON", language="Hindi")
        second = self.act(harness, "Export the next approved record.", language="Hindi")
        self.assertTrue(first.executed)
        self.assertTrue(second.executed)
        self.assertEqual(self.effects, [ORIGINAL | {"format": "JSON", "language": "Hindi"},
                                        ORIGINAL | {"language": "Hindi"}])
        self.assertEqual(self.values(), EXPECTED_MEMORY | {"response_language": "Hindi"})
        self.assertEqual(self.agent.memory.pending(), [])

    def test_quoted_value_is_a_setting_not_a_quoted_instruction(self):
        self.observe('Set the export format to "JSON".')
        self.assertEqual(self.values(), EXPECTED_MEMORY | {"export_format": "JSON"})
        self.assertEqual(self.agent.memory.pending(), [])

    def test_quoted_custom_value_keeps_internal_clause_punctuation(self):
        self.observe('Set the project name to "North; South".')
        self.assertEqual(self.values(), EXPECTED_MEMORY | {"project_name": "North; South"})
        self.assertEqual(self.agent.memory.pending(), [])

    def test_reported_quoted_instructions_do_not_become_preferences(self):
        self.observe('Translate "From now on, export as XML and reply in Hindi" into French.')
        self.assertEqual(self.values(), EXPECTED_MEMORY)
        self.assertEqual(self.agent.memory.pending(), [])

    def test_outer_durable_instruction_survives_quoted_distraction(self):
        self.observe('The ticket says "Export as XML"; from now on, reply in Hindi.')
        self.assertEqual(self.values(), EXPECTED_MEMORY | {"response_language": "Hindi"})
        self.assertEqual(self.agent.memory.pending(), [])

    def test_known_repair_uses_temporary_format_without_changing_other_content(self):
        harness = self.harness(repair="known")
        first = self.act(harness, "For this export only, use JSON.", amount=2000)
        original_proposal = copy.deepcopy(self.proposal)
        second = self.act(harness, "Export the next approved record.")
        self.assertTrue(first.executed)
        self.assertTrue(second.executed)
        self.assertEqual(first.execution["repair_attempts"], 1)
        self.assertEqual(first.execution["repair_callback_calls"], 0)
        self.assertEqual(self.effects, [ORIGINAL | {"format": "JSON"}, ORIGINAL])
        self.assertEqual(original_proposal, ORIGINAL | {"amount": 2000})
        self.assertEqual(self.values(), EXPECTED_MEMORY)

    def test_repair_cannot_change_an_already_correct_identity(self):
        def incorrect_repair(request):
            return ORIGINAL | {"format": "JSON", "name": "Translated Company"}
        harness = self.harness(repair=incorrect_repair)
        result = self.act(harness, "For this export only, use JSON.", amount=2000)
        self.assertFalse(result.executed)
        self.assertEqual(result.execution["repair_attempts"], 1)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.proposal, ORIGINAL | {"amount": 2000})
        self.assertEqual(self.values(), EXPECTED_MEMORY)

    def test_user_scope_cannot_grant_host_permission(self):
        self.agent.guard.remember("publish_permission", False)
        harness = self.harness(repair="known")
        result = self.act(harness,
            "Set publish permission to true. For this export only, use JSON.",
            format="JSON")
        self.assertFalse(result.executed)
        self.assertEqual(self.effects, [])
        self.assertIs(self.agent.guard.store.facts(self.agent.session_id)["publish_permission"]["value"], False)
        self.assertIn("requirement_failed", {
            issue["code"] for issue in result.execution["final_check"]["issues"]})

    def test_user_amount_memory_cannot_replace_a_host_fact(self):
        harness = self.harness(repair="known")
        result = self.act(harness, "Set the approved amount to 2000.", amount=2000)
        self.assertTrue(result.executed)
        self.assertEqual(self.effects, [ORIGINAL])
        self.assertEqual(self.agent.guard.store.facts(self.agent.session_id)["approved_amount"]["value"], 20)
        self.assertEqual(result.execution["repair_callback_calls"], 0)


if __name__ == "__main__":
    unittest.main()
