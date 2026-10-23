"""Clarification must survive managed-history rollover and resolve before action."""
import tempfile
import unittest
from pathlib import Path
from context_relay import MemoryClarificationRequired, MemoryPolicy, RelayConfig, connect


class V014IntegrationTests(unittest.TestCase):
    def test_uncertainty_rollover_and_clarification_then_canonical_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            config=RelayConfig(db_path=str(Path(folder)/"session.sqlite"))
            def model(*,messages):
                return "Acknowledged."
            with connect(model,config=config,memory=MemoryPolicy()) as first:
                chat=first.conversation()
                chat.send("Export as CSV.")
                with self.assertRaises(MemoryClarificationRequired):
                    chat.send("Change export format to the other option.")
                self.assertTrue(first.memory.snapshot()["entries"][0]["disputed"])
                original=first.session_id
                successor=chat.rollover(expected_revision=first.guard.turn().revision)
            with connect(model,config=config,memory=MemoryPolicy(),session_id=successor) as second:
                pending=second.memory.pending()
                self.assertEqual(len(pending),1)
                self.assertIsNone(pending[0]["value"])
                self.assertEqual(pending[0]["source"]["session_id"],original)
                second.guard.allow_tool("export",memory_bindings={"format":"export_format"})
                calls=[]
                executor=lambda tool,args:calls.append(args) or args
                def must_not_repair(request):
                    self.fail("Uncertainty must ask for clarification without a repair call")
                outcome=second.guard.turn().execute_with_recovery("export",{"format":"csv"},executor,repair=must_not_repair)
                self.assertEqual(outcome["status"],"clarification_required")
                self.assertEqual(outcome["repair_attempts"],0)
                self.assertEqual(calls,[])
                second.conversation().send("Export as JSON.")
                self.assertEqual(second.memory.pending(),[])
                outcome=second.guard.turn().execute_with_recovery("export",{"format":"json"},executor,repair=must_not_repair)
                self.assertEqual(outcome["status"],"executed")
                self.assertEqual(calls,[{"format":"JSON"}])


if __name__ == "__main__":
    unittest.main()
