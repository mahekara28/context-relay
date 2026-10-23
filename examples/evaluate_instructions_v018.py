"""Offline memory-bound fixtures: context memory -> scripted proposal -> checked write.

A stand-in reads only Relay memory, deliberately ignoring the user transcript.
Clarification replies are scripted. This tests plumbing, not LLM performance.
It does not test interpreting or honoring current-turn temporary instructions.
"""
import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALUES = {"export_format": "JSON", "response_language": "Hindi", "response_style": "detailed"}
REPLIES = {"export_format": "Export as JSON.", "response_language": "Reply in Hindi.",
           "response_style": "Use detailed answers."}


def run_case(case, api):
    calls, writes = [], []
    initial, expected, pauses, errors = {}, {}, [], []
    setup_calls, wrong_calls = 0, 0
    task_started, clarified, blocked = False, False, False
    reason, actual, artifact_sha256 = None, None, None
    phase = "setup"
    history = []
    for text in case["before"]:
        history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": "Recorded."}])

    def fail(failure_reason, exc=None):
        nonlocal reason
        if reason is None:
            reason = failure_reason
        if exc is not None:
            errors.append({"phase": phase, "type": type(exc).__name__, "message": str(exc)})

    def note_pause(exc, invocation_start):
        pauses.append({"targets": list(exc.targets), "provider_boundary_calls": len(calls) - invocation_start})

    def context_reader(*, messages):
        calls.append(messages)
        packet = next(message["content"] for message in messages
                      if message["content"].startswith("APPLICATION-RECORDED TASK DATA\n"))
        memory = json.loads(packet[packet.index("{"):])["state"]["user_memory"]
        return {row["key"]: row["value"] for row in memory["entries"] if row["key"] in VALUES}

    try:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory, "result.json")
            try:
                with api.auto_connect(context_reader, memory=api.MemoryPolicy(max_chars=16000)) as ai:
                    registry = api.ToolRegistry(ai)

                    @registry.tool(memory_bindings={key: key for key in VALUES})
                    def write_result(export_format, response_language, response_style):
                        row = dict(export_format=export_format, response_language=response_language,
                                   response_style=response_style)
                        writes.append(row)
                        artifact.write_text(json.dumps(row, sort_keys=True), encoding="utf-8")
                        return row

                    ai(messages=history[:-1])
                    initial = {row["key"]: row["value"] for row in ai.memory.entries()}
                    expected = initial | (case.get("expected") or {})
                    setup_calls = len(calls)
                    task_started = True
                    history.append({"role": "user", "content": case["text"]})
                    pause_type = getattr(api, "MemoryClarificationRequired", ())
                    phase = "initial_invoke"
                    invocation_start = len(calls)
                    try:
                        try:
                            result = ai.invoke(messages=history)
                        finally:
                            if case["kind"] == "uncertain":
                                wrong_calls = len(calls) - invocation_start
                    except pause_type as exc:
                        note_pause(exc, invocation_start)
                        if case["kind"] != "uncertain" or set(exc.targets) != set(case["targets"]):
                            fail("unexpected_pause")
                        elif len(calls) != invocation_start or writes:
                            fail("side_effect_before_clarification")
                        else:
                            clarified = True
                            history += [{"role": "assistant", "content": exc.prompt},
                                        {"role": "user", "content": " ".join(REPLIES[key] for key in case["targets"])}]
                            expected.update({key: VALUES[key] for key in case["targets"]})
                            phase = "resume_invoke"
                            invocation_start = len(calls)
                            try:
                                result = ai.invoke(messages=history)
                            except pause_type as repeated:
                                note_pause(repeated, invocation_start)
                                fail("repeated_clarification")
                    if reason is None:
                        # This is a synthetic write of remembered settings, not a user-task completion.
                        phase = "dispatch"
                        try:
                            registry.bind(result.prepared).dispatch("write_result", result.response)
                        except api.ActionRejected as exc:
                            blocked = True
                            fail("action_rejected", exc)
                    phase = "cleanup"
            except Exception as exc:
                fail(phase + "_error", exc)
            finally:
                if not task_started:
                    setup_calls = len(calls)
                try:
                    if artifact.exists():
                        raw_artifact = artifact.read_bytes()
                        artifact_sha256 = hashlib.sha256(raw_artifact).hexdigest()
                        actual = json.loads(raw_artifact)
                except Exception as exc:
                    fail("artifact_read_error", exc)
    except Exception as exc:
        fail(phase + "_error", exc)
    if not task_started:
        setup_calls = len(calls)
    requires_clarification = case["kind"] == "uncertain"
    fixture_passed = (reason is None and actual == expected and len(writes) == 1
                      and not wrong_calls and not blocked
                      and (not requires_clarification or clarified))
    if not fixture_passed and reason is None:
        reason = "missing_clarification" if requires_clarification and not clarified else "write_mismatch"
    return {"id": case["id"], "kind": case["kind"], "fixture_passed": fixture_passed,
            "reason": reason, "errors": errors, "targets": pauses[0]["targets"] if pauses else [],
            "pauses": pauses, "clarified": clarified, "clarification_requests": len(pauses),
            "provider_boundary_calls": len(calls), "setup_calls": setup_calls,
            "task_calls": len(calls) - setup_calls, "calls_before_required_clarification": wrong_calls,
            "calls_before_pause": sum(pause["provider_boundary_calls"] for pause in pauses),
            "blocked_write": blocked, "writes": writes, "initial": initial, "expected": expected,
            "actual": actual,
            "wrong_writes": sum(row != expected or blocked or (requires_clarification and not clarified)
                                for row in writes),
            "artifact_sha256": artifact_sha256}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT/"src")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Preserve previous evidence: output already exists.")
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    import context_relay as api
    assert Path(api.__file__).resolve().is_relative_to(source)
    raw = args.corpus.read_bytes()
    cases = [case for case in json.loads(raw)["cases"] if case.get("cohort") == "supported"]
    started = time.perf_counter()
    rows = [run_case(case, api) for case in cases]
    summary = {"fixtures": len(rows), "fixtures_passed": sum(row["fixture_passed"] for row in rows),
               "provider_boundary_calls": sum(row["provider_boundary_calls"] for row in rows),
               "setup_calls": sum(row["setup_calls"] for row in rows),
               "task_calls": sum(row["task_calls"] for row in rows),
               "clarification_requests": sum(row["clarification_requests"] for row in rows),
               "wrong_writes": sum(row["wrong_writes"] for row in rows),
               "blocked_writes": sum(row["blocked_write"] for row in rows),
               "calls_before_pause": sum(row["calls_before_pause"] for row in rows),
               "calls_before_required_clarification": sum(row["calls_before_required_clarification"] for row in rows)}
    result = {"schema": 1, "version": api.__version__, "corpus_sha256": hashlib.sha256(raw).hexdigest(),
              "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_sha256": {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted((source/"context_relay").rglob("*.py"))},
              "model_behavior": "deterministic context-reader proposals; scripted clarification replies",
              "actual_model_calls": 0, "network_calls": 0,
              "limitations": "The stand-in reads only Relay memory and ignores the user transcript. Fixture success measures a checked write of remembered settings. It does not test interpreting or honoring temporary user instructions, actual proposal generation, model errors, or customer task success.",
              "seconds": round(time.perf_counter()-started, 3), "summary": summary, "cases": rows}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=True)
        stream.write("\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
