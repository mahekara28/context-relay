"""Evaluate declared content preservation; replay prior repairs without new models.

Fixtures are API-informed internal development cases. The baseline arm below is
this version with format/language checks alone, not a rerun of a historical model.
Manually corrected proposals demonstrate checking/dispatch, not model recovery.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_relay import AutomaticMemory, ContentPolicy, ContextGuard, RowRule, __version__


def digest(data):
    return hashlib.sha256(data).hexdigest()


def contract(case):
    settings = case["policy"]
    rows = tuple(RowRule(**{**row, "fields": tuple(row["fields"])}) for row in settings["rows"])
    return ContentPolicy.from_json(case["source_json"], exact=tuple(settings["exact"]), rows=rows)


def fingerprints():
    return {path.name: digest(path.read_bytes()) for path in sorted((ROOT / "src/context_relay").glob("*.py"))}


def historical_replay(case, corrected, directory):
    policy = contract(case)
    recorded = json.loads(case["proposal_json"])
    replacement = json.loads(corrected["proposal_json"])
    outcomes = []
    for arm in ("settings_only", "protected_content", "protected_content_manual_repair"):
        artifact = directory / (case["id"] + "-" + arm + ".json")
        writes, callbacks = [], []
        with ContextGuard(name="Historical proposal replay") as guard:
            memory = AutomaticMemory(guard.store, guard.session_id)
            memory.process([{"role": "user", "content": "Export as JSON."},
                            {"role": "user", "content": "Reply in Hindi."}])
            options = {"memory_bindings": {"format": "export_format", "language": "response_language"}}
            if arm == "settings_only":
                options["optional_args"] = ("rows", "summary")
            else:
                options.update(content=policy, optional_args=("summary",))
            guard.allow_tool("write_export", **options)

            def execute(tool, arguments):
                assert tool == "write_export"
                with artifact.open("x", encoding="utf-8") as stream:
                    json.dump(arguments, stream, ensure_ascii=False, indent=2)
                writes.append(arguments)
                return {"file": artifact.name}

            def repair(request):
                callbacks.append(request)
                return replacement

            outcome = guard.turn().execute_with_recovery(
                "write_export", recorded, execute,
                repair=repair if arm == "protected_content_manual_repair" else None)
            present = artifact.exists()
            saved = artifact.read_text(encoding="utf-8") if present else None
            valid = policy.check_json(saved).valid if present else None
            assert len(writes) == (1 if present else 0)
            assert guard.store.facts(guard.session_id) == {}
            expected = {"settings_only": ("executed", True, False, 0),
                        "protected_content": ("blocked", False, None, 0),
                        "protected_content_manual_repair": ("repaired", True, True, 1)}[arm]
            observed = (outcome["status"], present, valid, len(callbacks))
            outcomes.append({"arm": arm, "passed": observed == expected,
                             "status": outcome["status"], "executor_calls": len(writes),
                             "repair_callbacks": len(callbacks), "protected_fields_preserved": valid,
                             "artifact": artifact.name if present else None,
                             "artifact_sha256": digest(artifact.read_bytes()) if present else None,
                             "initial_check": outcome["initial_check"], "final_check": outcome["final_check"]})
    return {"id": case["id"], "scope": "Declared source fields only; summary meaning and unprotected totals not graded.",
            "new_model_calls": 0, "manual_repair": True, "outcomes": outcomes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "evaluations/content-integrity-v0.16-cases.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; preserve earlier evidence.")
    artifacts = args.output.with_suffix("").with_name(args.output.stem + "-artifacts")
    if artifacts.exists():
        parser.error("Artifact directory exists; use a fresh output name.")
    corpus_bytes = args.corpus.read_bytes()
    corpus = json.loads(corpus_bytes)
    before = fingerprints()
    rows = []
    for case in corpus["cases"]:
        policy = contract(case)
        snapshot = policy.to_dict()
        checked = policy.check_json(case["proposal_json"])
        repeated = policy.check_json(case["proposal_json"])
        rows.append({"id": case["id"], "expected_allow": case["expected"]["allow"],
                     "actual_allow": checked.valid, "passed": checked.valid == case["expected"]["allow"],
                     "report": checked.report, "policy_unchanged": policy.to_dict() == snapshot,
                     "replay_unchanged": repeated.report == checked.report})
    by_id = {case["id"]: case for case in corpus["cases"]}
    # The manually restored controls also retain historical provenance. Only
    # recorded failed proposals belong in the three-arm failure replay cohort.
    historical = [case for case in corpus["cases"]
                  if "historical_origin" in case and case["id"].endswith("-renamed")]
    artifacts.mkdir(parents=True)
    replays = []
    for case in historical:
        for relative, expected in case["historical_origin"]["sha256"].items():
            assert digest((ROOT / relative).read_bytes()) == expected, relative
        counterpart = by_id[case["id"].replace("-renamed", "-preserved")]
        replays.append(historical_replay(case, counterpart, artifacts))
    after = fingerprints()
    summary = {"cases": len(rows), "passed": sum(row["passed"] for row in rows),
               "expected_allow": sum(row["expected_allow"] for row in rows),
               "expected_block": sum(not row["expected_allow"] for row in rows),
               "false_allows": sum(row["actual_allow"] and not row["expected_allow"] for row in rows),
               "false_blocks": sum(not row["actual_allow"] and row["expected_allow"] for row in rows),
               "state_replay_passed": all(row["policy_unchanged"] and row["replay_unchanged"] for row in rows),
               "historical_cases": len(replays),
               "historical_replay_arms_passed": sum(arm["passed"] for case in replays for arm in case["outcomes"]),
               "historical_replay_arms": sum(len(case["outcomes"]) for case in replays),
               "source_unchanged": before == after, "new_model_calls": 0}
    result = {"schema": 1, "version": __version__,
              "scope": "API-informed internal deterministic development cases and recorded-proposal replay; no new model or organisational pilot.",
              "corpus_sha256": digest(corpus_bytes), "source_sha256": before,
              "evaluator_sha256": digest(Path(__file__).read_bytes()),
              "summary": summary, "cases": rows, "historical_replay": replays}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps(summary, indent=2))
    return 0 if (all(row["passed"] for row in rows) and summary["state_replay_passed"]
                 and summary["source_unchanged"]
                 and summary["historical_replay_arms_passed"] == summary["historical_replay_arms"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
