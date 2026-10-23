"""Changing literal values must not change correction grammar or authority."""
import unittest
from context_relay import AutomaticMemory, ContextGuard, MemoryPolicy


class CorrectionValueGeneralizationTests(unittest.TestCase):
    def test_composed_corrections_across_documented_values(self):
        groups = (
            ("export_format", "Export as {old}.",
             "My export-format preference has changed from {old} to {new}.",
             ("CSV", "JSON", "JSONL", "TSV", "XML", "YAML", "TOML", "Markdown", "HTML", "PDF", "TXT", "XLSX")),
            ("response_language", "Reply in {old}.",
             "The {old} response-language preference is outdated. I now prefer {new} for your responses.",
             ("Arabic", "Bengali", "Chinese", "Dutch", "English", "French", "German", "Hindi", "Italian", "Japanese", "Korean", "Marathi", "Portuguese", "Russian", "Spanish", "Tamil", "Telugu", "Turkish", "Urdu")),
            ("response_style", "Keep responses {old}.",
             "Keep responses {new} by default from now on. My earlier preference for {old} responses is out of date.",
             ("short", "brief", "concise", "detailed")),
        )
        for key, seed, wording, values in groups:
            for index, new in enumerate(values):
                old = values[(index + 1) % len(values)]
                with self.subTest(key=key, new=new), ContextGuard() as guard:
                    memory = AutomaticMemory(guard.store, guard.session_id, policy=MemoryPolicy(max_chars=16000))
                    history = [{"role": "user", "content": seed.format(old=old)},
                               {"role": "user", "content": wording.format(old=old.lower(), new=new.lower())}]
                    memory.process(history)
                    self.assertEqual({row["key"]: row["value"] for row in memory.entries()}, {key: new})
                    self.assertEqual(memory.pending(), [])
                    snapshot = memory.snapshot()
                    memory.process(history)
                    self.assertEqual(memory.snapshot(), snapshot)
                    self.assertEqual(guard.store.facts(guard.session_id), {})
                    source = memory.entries()[0]["source"]
                    self.assertEqual(source["quote"], history[1]["content"][source["start"]:source["end"]])


if __name__ == "__main__":
    unittest.main()
