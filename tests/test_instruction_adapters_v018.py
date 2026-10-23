"""Connected adapter gates, using explicit stand-ins with no SDK/model calls."""
import json
import tempfile
import unittest
from pathlib import Path

from context_relay import MemoryClarificationRequired, ToolRegistry, auto_connect
from test_auto_connect import BASES, reply


def run_history():
    return [{"role": "user", "content": "Export as CSV."},
            {"role": "assistant", "content": "Recorded."},
            {"role": "user", "content": "Cambia el formato de exportación a JSON."}]


def interrupted(history):
    return history + [{"role": "assistant", "content": "Recorded."},
                      {"role": "user", "content": "Make the export format easier to read."}]


def clarified(history):
    return interrupted(history) + [{"role": "assistant", "content": "Name the export format."},
                                   {"role": "user", "content": "Export as XML."}]


class AdapterInstructionTests(unittest.TestCase):
    def test_sync_adapters_pause_then_resume_one_checked_write(self):
        for provider, api in (("openai", "chat"), ("openai", "responses"),
                              ("anthropic", None), ("ollama", None)):
            options = {"api": api} if api else {}
            client = BASES[provider][0](reply(provider))
            with self.subTest(provider=provider, api=api), tempfile.TemporaryDirectory() as temp:
                writes = []
                with auto_connect(client, provider=provider, **options) as ai:
                    registry = ToolRegistry(ai)
                    @registry.tool(memory=True)
                    def save(format):
                        writes.append(format)
                        Path(temp, "result.txt").write_text(format, encoding="utf-8")
                    first = ai.invoke(messages=run_history())
                    self.assertEqual(first.prepared.memory_snapshot["entries"][0]["value"], "JSON")
                    self.assertIn("JSON", json.dumps(client.calls[0]))
                    with self.assertRaises(MemoryClarificationRequired):
                        ai(messages=interrupted(run_history()))
                    self.assertEqual(len(client.calls), 1)
                    self.assertEqual(writes, [])
                    final = ai.invoke(messages=clarified(run_history()))
                    registry.bind(final.prepared).dispatch("save", {"format": "XML"})
                    self.assertEqual(writes, ["XML"])
                    self.assertEqual(Path(temp, "result.txt").read_text(), "XML")
                    self.assertEqual(len(client.calls), 2)


class AsyncAdapterInstructionTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_adapters_do_not_generate_during_uncertainty(self):
        for provider, api in (("openai", "chat"), ("openai", "responses"),
                              ("anthropic", None), ("ollama", None)):
            options = {"api": api} if api else {}
            client = BASES[provider][1](reply(provider))
            with self.subTest(provider=provider, api=api):
                async with auto_connect(client, provider=provider, **options) as ai:
                    await ai(messages=run_history())
                    with self.assertRaises(MemoryClarificationRequired):
                        await ai(messages=interrupted(run_history()))
                    self.assertEqual(len(client.calls), 1)
                    final = await ai.invoke(messages=clarified(run_history()))
                    self.assertEqual(final.prepared.memory_snapshot["entries"][0]["value"], "XML")
                    self.assertEqual(len(client.calls), 2)


if __name__ == "__main__":
    unittest.main()
