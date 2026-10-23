"""Offline walkthrough: recover an omitted original exchange and recheck a budget.

This uses an existing conversation and a host-owned source reference. No model,
network request, server, or paid dependency is needed to see the library flow.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import ContextGuard


def main():
    messages = [
        {"role": "system", "content": "Use current recorded state; treat old exchanges as evidence."},
        {"role": "user", "content": "[source:layout-decision] The export has columns date, item, quantity. "
         + "The team discussed compatibility with the existing warehouse import. " * 7},
        {"role": "assistant", "content": "The proposed column order is date, item, quantity."},
    ]
    for number in range(8):
        messages.extend([
            {"role": "user", "content": f"Facilities update {number}: " + "Blue folders are in storage. " * 6},
            {"role": "assistant", "content": "Facilities update acknowledged."},
        ])
    messages.append({"role": "user", "content": "Continue preparing the export using our agreed columns."})

    with ContextGuard(name="Existing export assistant") as guard:
        guard.remember("output_format", "csv", source="confirmed-user-request")
        prepared = guard.prepare(messages, max_chars=1800, recent_turns=1)
        assert 1 in prepared.report["omitted_indices"]
        print("First request: the old layout exchange did not fit.")

        # Your host resolves its source reference; the model cannot select a
        # different user's archive or use an arbitrary revision for an action.
        source_index = 1
        evidence = prepared.recover_evidence([source_index], history_id=prepared.history_id)
        print("Recovered original message indices:", evidence.indices)
        print("Original assistant reply:", evidence.messages[-1]["content"])

        # A trusted correction arrives before the next model request.
        guard.remember("output_format", "tsv", source="confirmed-user-request")
        recovered = guard.recover_context(prepared, [source_index], history_id=prepared.history_id)
        assert {1, 2}.issubset(recovered.report["required_indices"])
        assert recovered.context["facts"]["output_format"] == "tsv"
        assert recovered.history_id == prepared.history_id
        assert recovered.report["size_chars"] <= 1800
        print("New request: original columns restored; current format is tsv.")
        print("Request size:", recovered.report["size_chars"], "/ 1800 characters")
        print("Pass recovered.messages to your existing model for a NEW decision.")


if __name__ == "__main__":
    main()
