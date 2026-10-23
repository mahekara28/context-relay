"""Pilot plumbing example. Fixed responses and synthetic cases; NOT a real pilot."""

import argparse
import json
from pathlib import Path

from context_relay import RelayConfig
from context_relay.pilot import run_pilot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    # Replace both functions with your existing model call and context builder.
    # This fixed response deliberately fails one case in BOTH modes.
    def model(*, messages):
        return "JSON"

    def existing_baseline(messages, *, max_chars):
        return messages

    cases = [
        {"id": "synthetic-json", "messages": [{"role": "user", "content": "Export as JSON. Which format?"}], "expected": "JSON"},
        {"id": "synthetic-csv", "messages": [{"role": "user", "content": "Export as CSV. Which format?"}], "expected": "CSV"},
    ]
    summary = run_pilot(cases, model_call=model, baseline_prepare=existing_baseline,
                        baseline_description="Toy full-history function; not a real project's baseline",
                        model_description="Fixed function that always returns JSON; no inference",
                        dataset_kind="synthetic", report_path=args.output,
                        config=RelayConfig(max_chars=4000), include_content=True)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
