"""Independent ordinary validator for the bounded release-manifest comparison.

Standard library only: no Relay imports, policies, parsers, validators or repair.
The caller owns framework memory, freshness, destination transactions and replay.
"""
from copy import deepcopy
import json


FIELDS = {"request_id", "source_version", "format", "release", "artifacts"}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def validate_proposal(proposal, expected, *, may_export, repair_budget=1):
    """One known-value repair, followed by independent schema/value checks.

    Unexpected fields/tools are refused. Correct replacements come exclusively
    from the supplied captured policy; no candidate value becomes authority.
    """
    blocked = {"status": "blocked", "completed": False, "executed": False, "repair_attempts": 0}
    if (type(proposal) is not dict or set(proposal) != {"tool", "arguments"}
            or proposal["tool"] != "export_manifest" or type(proposal["arguments"]) is not dict
            or set(proposal["arguments"]) != FIELDS or may_export is not True):
        return {**blocked, "reason": "unsupported_or_forbidden"}
    arguments = deepcopy(proposal["arguments"])
    mismatches = [key for key in FIELDS if canonical(arguments[key]) != canonical(expected[key])]
    repaired = 0
    if mismatches:
        if repair_budget != 1:
            return {**blocked, "reason": "value_mismatch"}
        for key in mismatches:
            arguments[key] = deepcopy(expected[key])
        repaired = 1
    rows = arguments["artifacts"]
    valid = (all(type(arguments[key]) is str and arguments[key] for key in FIELDS - {"artifacts"})
             and arguments["format"] in {"JSON", "CSV"} and type(rows) is list and 1 <= len(rows) <= 20)
    if valid:
        valid = all(type(row) is dict and set(row) == {"filename", "bytes", "sha256"}
            and type(row["filename"]) is str and bool(row["filename"])
            and type(row["bytes"]) is int and row["bytes"] >= 0
            and type(row["sha256"]) is str and len(row["sha256"]) == 64
            and all(character in "0123456789abcdef" for character in row["sha256"])
            for row in rows)
    if not valid or canonical(arguments) != canonical(expected):
        return {**blocked, "reason": "invalid_final_arguments", "repair_attempts": repaired}
    return {"status": "ready", "arguments": arguments, "repair_attempts": repaired}
