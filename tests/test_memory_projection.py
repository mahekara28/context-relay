"""Compact model inputs retain decisions and quotes without repeated audit hashes."""

import unittest
import sys
import types
from unittest.mock import patch

from context_relay import (ContextGuard, EvidenceCatalog, MemoryBudgetExceeded,
                           MemoryPolicy, MemoryClarificationRequired, TokenBudget, connect)
from context_relay.models import encode
from context_relay import attach, ConversationResponseError


class MemoryProjectionTests(unittest.TestCase):
    def setUp(self):
        self.agent = connect(lambda **kw: None, memory=MemoryPolicy())
        self.addCleanup(self.agent.close)

    def test_projection_retains_all_values_quotes_and_source_indices(self):
        self.agent(messages=[{"role": "user", "content": "The project owner is Mira. Use CSV for the export format."}])
        audit, view = self.agent.memory.snapshot(), self.agent.memory.context_snapshot()
        self.assertEqual(len(audit["entries"]), len(view["entries"]))
        for original, projected in zip(audit["entries"], view["entries"]):
            self.assertEqual({k: v for k, v in original.items() if k != "source"},
                             {k: v for k, v in projected.items() if k != "source"})
            self.assertEqual(projected["source"], {k: original["source"][k] for k in ("index", "role", "quote")})
            self.assertIn("message_sha256", original["source"])
            self.assertNotIn("message_sha256", projected["source"])
        self.assertLess(len(encode(view)), len(encode(audit)))
        self.assertFalse(view["current_authority"])
        self.assertFalse(view["trusted_instruction"])

    def test_unresolved_values_and_disputes_are_never_dropped(self):
        with self.assertRaises(MemoryClarificationRequired):
            self.agent(messages=[{"role": "user", "content": "Use JSON for the export format."},
                                 {"role": "assistant", "content": "Okay."},
                                 {"role": "user", "content": "The export format is CSV."}])
        view = self.agent.memory.context_snapshot()
        self.assertTrue(view["entries"][0]["disputed"])
        self.assertEqual(view["pending_count"], 1)
        proposed = view["unresolved"][0]
        self.assertEqual(proposed["value"], "CSV")
        self.assertEqual(proposed["targets"], ["export_format"])
        self.assertEqual(proposed["source"]["quote"], "The export format is CSV.")
        self.assertNotIn("id", proposed)
        self.assertIn("id", self.agent.memory.pending()[0])

    def test_model_projection_does_not_mutate_local_audit(self):
        self.agent(messages=[{"role": "user", "content": "The project owner is Mira."}])
        original = self.agent.memory.snapshot()
        projected = self.agent.memory.context_snapshot()
        projected["entries"][0]["source"]["quote"] = "forged"
        self.assertEqual(self.agent.memory.snapshot(), original)

    def test_compacting_cannot_bypass_local_retention_limit(self):
        self.agent(messages=[{"role": "user", "content": "The project owner is Mira."}])
        self.agent.memory.policy = MemoryPolicy(max_chars=10)
        self.assertTrue(self.agent.memory.snapshot()["entries"])
        with self.assertRaises(MemoryBudgetExceeded):
            self.agent.memory.context_snapshot()

    def test_exact_token_counter_receives_compact_view(self):
        seen = []
        def count(messages):
            seen.append(encode(messages))
            return len(encode(messages))
        with connect(lambda **kw: None, memory=MemoryPolicy(), token_budget=TokenBudget(5000, count)) as agent:
            result = agent.invoke(messages=[{"role": "user", "content": "The project owner is Mira."}])
            self.assertTrue(any("Mira" in value and "user_memory" in value for value in seen))
            self.assertNotIn("message_sha256", encode(result.prepared.messages))
            self.assertIn("message_sha256", encode(agent.memory.entries()))

    def test_openai_null_assistant_content_is_not_citeable_tool_text(self):
        messages = [{"role": "user", "content": "Look up the parcel."},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "call1", "content": "Dock-7"}]
        with ContextGuard() as guard:
            prepared = guard.prepare(messages, protocol="openai_chat")
            catalog = EvidenceCatalog(prepared)
            self.assertEqual(catalog.sources[1]["content"], "")
            self.assertEqual(catalog.search("Dock-7")[0]["source_id"], "m2")

    def test_known_ollama_attachment_manages_sdk_text_without_custom_decoder(self):
        # SDK stand-ins exercise class identity/shape only; no SDK or inference.
        class Message:
            __module__ = "ollama._types"
            def model_dump(self, **kwargs):
                return {"role": "assistant", "content": "Recorded."}
        class Client:
            __module__ = "ollama._client"
            def chat(self, *, messages, **kwargs):
                return response
        response = types.SimpleNamespace(message=Message(), done=True, done_reason="stop")
        with patch.dict(sys.modules, {"ollama._client": types.SimpleNamespace(Client=Client),
                                      "ollama._types": types.SimpleNamespace(Message=Message)}):
            with attach(Client(), memory=MemoryPolicy()) as relay:
                chat = relay.agent.conversation()
                self.assertIs(chat.send("Export as CSV."), response)
                self.assertEqual(chat.history[-1], {"role": "assistant", "content": "Recorded."})
                self.assertEqual(relay.memory.entries()[0]["value"], "CSV")

    def test_ollama_managed_decoder_refuses_lost_thinking_and_partial_output(self):
        class Client:
            __module__ = "ollama._client"
            def chat(self, *, messages, **kwargs):
                return response
        for response in (
            {"message": {"role": "assistant", "content": "Part"}, "done": False},
            {"message": {"role": "assistant", "content": "Part"}, "done": True, "done_reason": "length"},
            {"message": {"role": "assistant", "content": "Text", "thinking": "retained elsewhere"}, "done": True},
        ):
            with self.subTest(response=response), patch.dict(sys.modules, {"ollama._client": types.SimpleNamespace(Client=Client)}):
                with attach(Client()) as relay:
                    chat = relay.agent.conversation()
                    with self.assertRaises(ConversationResponseError) as caught:
                        chat.send("Continue.")
                    self.assertIs(caught.exception.response, response)
                    self.assertEqual(len(chat.history), 1)
                    self.assertIsNotNone(chat.pending)


if __name__ == "__main__":
    unittest.main()
