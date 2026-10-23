"""Previously missed corrections must affect real guarded execution after restart."""
import json
import tempfile
import unittest
from pathlib import Path

from context_relay import MemoryClarificationRequired, MemoryPolicy, RelayConfig, connect


class CorrectionLifecycleTests(unittest.TestCase):
    def test_new_uncertainty_patterns_block_until_a_later_clear_preference(self):
        cases = (
            ("Export as CSV.",
             "My export format preference has changed; I will explain the replacement shortly.",
             "For every export going forward, JSON is what I want.",
             "export_format", "CSV", "JSON"),
            ("Reply in English.",
             "Switch my response language; the old one no longer works for me.",
             "From this point onward, answer me in Hindi.",
             "response_language", "English", "Hindi"),
        )
        for seed, uncertain, clarification, key, old, new in cases:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as folder:
                config = RelayConfig(db_path=str(Path(folder) / "session.sqlite"))
                output = Path(folder) / "result.json"
                model = lambda *, messages: "Acknowledged."
                with connect(model, config=config, memory=MemoryPolicy()) as first:
                    chat = first.conversation()
                    chat.send(seed)
                    with self.assertRaises(MemoryClarificationRequired):
                        chat.send(uncertain)
                    session = first.session_id
                    self.assertEqual(first.memory.pending()[0]["source"]["quote"], uncertain)
                with connect(model, config=config, memory=MemoryPolicy(), session_id=session) as second:
                    second.allow_tool("save", memory_bindings={"setting": key})
                    calls = []
                    def save(tool, arguments):
                        calls.append(arguments)
                        output.write_text(json.dumps(arguments), encoding="utf-8")
                        return arguments
                    def unexpected_repair(request):
                        self.fail("An unspecified replacement must ask the user, not guess.")
                    outcome = second.guard.turn().execute_with_recovery(
                        "save", {"setting": old}, save, repair=unexpected_repair)
                    self.assertEqual(outcome["status"], "clarification_required")
                    self.assertEqual(outcome["repair_attempts"], 0)
                    self.assertEqual(calls, [])
                    self.assertFalse(output.exists())
                    second.conversation().send(clarification)
                    self.assertEqual(second.memory.pending(), [])
                    entry = second.memory.entries()[0]
                    self.assertEqual(entry["value"], new)
                    self.assertEqual(entry["source"]["quote"], clarification)
                    result = second.guard.turn().execute_with_recovery(
                        "save", {"setting": new.lower()}, save, repair=unexpected_repair)
                    self.assertEqual(result["status"], "executed")
                    self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"setting": new})
                    self.assertEqual(calls, [{"setting": new}])
                    self.assertEqual(second.guard.store.facts(session), {})


if __name__ == "__main__":
    unittest.main()
