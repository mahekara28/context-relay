"""Offline demonstration: detect a correction, simulate host review, then retry."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_relay import CorrectionField, CorrectionRequired, connect


def main():
    calls = []

    def test_model(*, messages):
        calls.append(messages)
        return "[TEST TRANSPORT; NO LLM] Model boundary reached with the reviewed context."

    history = [{"role": "system", "content": "Follow the confirmed export requirements."},
               {"role": "user", "content": "Use CSV instead of JSON."}]
    field = CorrectionField("output_format", choices=("json", "csv", "tsv"))
    with connect(test_model, correction_fields=(field,)) as agent:
        agent.remember("output_format", "json")
        try:
            agent(messages=history)
        except CorrectionRequired as request:
            proposal = request.review.proposals[0]
            print("Detected possible change:", proposal["key"], "->", proposal["value"])
            print("Original evidence:", proposal["evidence"]["quote"])
            print("Model calls before review:", len(calls))
            assert not calls
            # This example simulates approval. Real applications must obtain
            # confirmation through their trusted user/application workflow.
            print("DEMO: simulating the host's explicit approval of CSV.")
            request.review.confirm(agent.guard, proposal["id"])
        print(agent(messages=history))
        assert len(calls) == 1
        assert agent.guard.turn().context["facts"]["output_format"] == "csv"


if __name__ == "__main__":
    main()
