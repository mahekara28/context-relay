"""Retain the exposed v0.18 corpus as regression coverage, never unseen evidence."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

import context_relay as api

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
from evaluate_corrections_v014 import evaluate


class InstructionCorpusRegressionTests(unittest.TestCase):
    def test_supported_frozen_corpus_after_exposure(self):
        raw = (ROOT / "evaluations/instructions-v0.18-independent.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "68e25aa808e0afe6fa103cd9ece355d9d9b661e818524740a287a6b186017f85")
        cases = [case for case in json.loads(raw)["cases"] if case["cohort"] == "supported"]
        self.assertEqual(len(cases), 60)
        checks = {"clear": "correct_automatic_update", "uncertain": "correct_uncertainty",
                  "abstain": "correct_abstention"}
        for case in cases:
            with self.subTest(case=case["id"]):
                row = evaluate(case, api)
                self.assertTrue(row[checks[case["kind"]]], row)
                self.assertFalse(row["wrong_active_update"])
                self.assertTrue(row["source_spans_exact"] and row["replay_unchanged"] and row["authority_unchanged"])


    def test_style_clarification_resumes_with_a_finite_use_instruction(self):
        for style in ("detailed", "short", "concise"):
            with self.subTest(style=style), api.auto_connect(lambda **kw: "Recorded.") as ai:
                chat = ai.conversation()
                chat.send("I prefer brief answers.")
                with self.assertRaises(api.MemoryClarificationRequired):
                    chat.send("Change response style to the other option.")
                chat.send(f"Use {style} answers.")
                self.assertEqual(ai.memory.entries()[0]["value"], style)
                self.assertFalse(ai.memory.pending())

    def test_style_use_keeps_quotation_temporary_and_negation_boundaries(self):
        for text in ('Translate "Use detailed answers" into Hindi.',
                     "Do not use detailed answers.",
                     "For this answer only, use detailed answers."):
            with self.subTest(text=text), api.auto_connect(lambda **kw: "Recorded.") as ai:
                chat = ai.conversation()
                chat.send("I prefer brief answers.")
                chat.send(text)
                self.assertEqual(ai.memory.entries()[0]["value"], "brief")
                self.assertFalse(ai.memory.pending())


if __name__ == "__main__":
    unittest.main()
