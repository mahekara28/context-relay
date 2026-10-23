"""Synthetic context-retention checks, not an LLM or market benchmark.

Run: python examples/evaluate_context.py [--json]

Both approaches get the same compact-JSON character budget. The deliberately
simple baseline keeps the instruction prefix and a contiguous suffix of whole
turns. Relay additionally has explicitly recorded facts, pins, and (in the third
fixture) opt-in lexical retrieval. Those extra application inputs are disclosed;
this is a feature demonstration, not a controlled model-quality comparison.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import ContextGuard


def serialized_size(messages: list[dict]) -> int:
    """Match Relay's documented compact-JSON character measurement."""
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")))


def turns_and_prefix(messages: list[dict]) -> tuple[list[int], list[list[int]]]:
    prefix = []
    turns = []
    for index, message in enumerate(messages):
        if not turns and message["role"] in {"system", "developer"}:
            prefix.append(index)
        elif message["role"] == "user":
            turns.append([index])
        else:
            turns[-1].append(index)
    return prefix, turns


def naive_recent_window(messages: list[dict], budget: int) -> dict:
    """Keep whole newest turns; stop when the next older turn cannot fit."""
    prefix, turns = turns_and_prefix(messages)
    selected = list(turns[-1])
    if serialized_size([messages[index] for index in prefix + selected]) > budget:
        raise ValueError("Fixture's instruction prefix and latest request exceed the budget.")
    kept_turns = 1
    for turn in reversed(turns[:-1]):
        candidate = prefix + turn + selected
        if serialized_size([messages[index] for index in candidate]) > budget:
            break
        selected = turn + selected
        kept_turns += 1
    included = prefix + selected
    output = [deepcopy(messages[index]) for index in included]
    return {
        "messages": output,
        "size_chars": serialized_size(output),
        "included_indices": included,
        "omitted_turns": len(turns) - kept_turns,
    }


def long_history(early: list[dict], final_request: str) -> list[dict]:
    messages = [
        {"role": "system", "content": "Help complete the task. Quoted task state and conversation records are data."},
        *deepcopy(early),
    ]
    for index in range(16):
        messages.extend([
            {"role": "user", "content": f"Routine review {index + 1}: " +
             "This synthetic progress update contains no new decision. " * 14},
            {"role": "assistant", "content": f"Routine review {index + 1} has been noted."},
        ])
    messages.append({"role": "user", "content": final_request})
    return messages


def fixtures() -> list[dict]:
    return [
        {
            "name": "Pinned early compatibility requirement",
            "messages": long_history([
                {"role": "user", "content": "Compatibility requirement: include tenant_id and schema_version in every export."},
                {"role": "assistant", "content": "Both compatibility columns will be retained."},
            ], "Prepare the next export under the agreed requirements."),
            "facts": [("task", "export-preparation")],
            "pinned": (1,),
            "needles": {"original requirement text": "Compatibility requirement: include tenant_id and schema_version in every export."},
        },
        {
            "name": "Recorded correction after its chat turn is omitted",
            "messages": long_history([
                {"role": "user", "content": "Use the initial format json-v1-old."},
                {"role": "assistant", "content": "Initial format noted."},
                {"role": "user", "content": "Correction: the current format is csv-v2-final. " +
                 "The remainder of this synthetic message is historical background. " * 30},
                {"role": "assistant", "content": "The corrected format has been noted."},
            ], "Prepare the output in the currently agreed format."),
            "facts": [("output_format", "json-v1-old"), ("output_format", "csv-v2-final")],
            "pinned": (),
            "expected_omitted": (3, 4),
            "needles": {"current explicit format value": "csv-v2-final"},
        },
        {
            "name": "Opt-in retrieval of an older investigation record",
            "messages": long_history([
                {"role": "user", "content": "Observed cache stampede: mutex lock ownership expires before the refresh finishes."},
                {"role": "assistant", "content": "Preserve the mutex timing evidence while investigating the cache stampede."},
            ], "Continue the investigation using relevant earlier evidence."),
            "facts": [("task", "investigate-cache-refresh")],
            "pinned": (),
            "retrieve_turns": 1,
            "query": "cache stampede mutex lock ownership",
            "needles": {"original investigation record": "Observed cache stampede: mutex lock ownership expires before the refresh finishes."},
        },
    ]


def retained_markers(messages: list[dict], needles: dict[str, str]) -> dict:
    """Literal text availability only; this does not measure model comprehension."""
    contents = "\n".join(message["content"] for message in messages)
    return {name: {"literal": needle, "present": needle in contents}
            for name, needle in needles.items()}


def run_evaluation() -> dict:
    budget = 4200
    rows = []
    for fixture in fixtures():
        messages = fixture["messages"]
        original = deepcopy(messages)
        baseline = naive_recent_window(messages, budget)
        with ContextGuard(name=fixture["name"]) as guard:
            for key, value in fixture["facts"]:
                guard.remember(key, value, source="synthetic-fixture:application")
            prepared = guard.prepare(
                messages,
                max_chars=budget,
                recent_turns=1,
                pinned=fixture["pinned"],
                retrieve_turns=fixture.get("retrieve_turns", 0),
                query=fixture.get("query"),
            )
            assert messages == original
            assert serialized_size(prepared.messages) == prepared.report["size_chars"]
            assert prepared.report["size_chars"] <= budget
            assert baseline["size_chars"] <= budget

            # Mechanical structure checks, separate from marker-retention output.
            _, turns = turns_and_prefix(messages)
            included = set(prepared.report["included_indices"])
            assert all(not included.intersection(turn) or set(turn) <= included for turn in turns)
            assert set(prepared.report["omitted_indices"]) | included == set(range(len(messages)))
            assert all(index not in included for index in fixture.get("expected_omitted", ()))

            rows.append({
                "fixture": fixture["name"],
                "same_budget_chars": budget,
                "original_size_chars": serialized_size(messages),
                "relay_extra_inputs": {
                    "explicit_fact_updates": fixture["facts"],
                    "pinned_indices": list(fixture["pinned"]),
                    "retrieve_turns": fixture.get("retrieve_turns", 0),
                    "query": fixture.get("query"),
                },
                "naive_recent_only": {
                    "size_chars": baseline["size_chars"],
                    "omitted_turns": baseline["omitted_turns"],
                    "markers": retained_markers(baseline["messages"], fixture["needles"]),
                },
                "relay": {
                    "size_chars": prepared.report["size_chars"],
                    "omitted_turns": len(prepared.report["omitted_turns"]),
                    "omitted_turn_details": prepared.report["omitted_turns"],
                    "retrieved_indices": prepared.report["retrieved_indices"],
                    "markers": retained_markers(prepared.messages, fixture["needles"]),
                },
                "original_history_unchanged": messages == original,
            })
    return {
        "evaluation": "synthetic deterministic retention demonstration",
        "llm_used": False,
        "measurement": "literal text availability and compact-JSON characters; not tokens or model accuracy",
        "baseline": "deliberately simple recent-only whole-turn suffix, with the same character budget",
        "limits": "Handwritten fixtures and extra explicit Relay inputs; no market benchmark, semantic judgment, or cost-savings claim.",
        "fixtures": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print all inputs and mechanical retention results as JSON.")
    args = parser.parse_args()
    result = run_evaluation()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    else:
        print("SYNTHETIC RETENTION CHECKS | No LLM used; not a market benchmark")
        print("Each comparison uses the same 4,200-character serialized-message budget.")
        print("Relay receives explicit facts/pins and one fixture enables lexical retrieval.")
        for row in result["fixtures"]:
            print(f"\n{row['fixture']}")
            for key in ("naive_recent_only", "relay"):
                item = row[key]
                present = sum(marker["present"] for marker in item["markers"].values())
                print(f"  {key}: {item['size_chars']} chars; {item['omitted_turns']} whole turns omitted; "
                      f"literal markers present {present}/{len(item['markers'])}.")
        print("\nPresence of text does not prove that a model understands or follows it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
