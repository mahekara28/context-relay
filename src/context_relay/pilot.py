"""Bounded paired text-workflow evaluation; never invents a baseline or pilot."""

from __future__ import annotations

import hashlib
import inspect
import time
from pathlib import Path

from .config import RelayConfig
from .connection import connect
from .context import _validated_messages
from .memory import MemoryPolicy
from .models import BudgetExceeded, clone, encode, nonempty_string, timestamp


def _sync(value):
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("The pilot runner requires synchronous callbacks.")
    return value


def _digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def run_pilot(cases, *, model_call, baseline_prepare, baseline_description,
              model_description, dataset_kind, report_path, config=None,
              model_options=None, score=None, repeats=1, max_calls=40,
              include_content=False, max_response_chars=64000):
    """Compare a host-supplied baseline with Relay using one shared model call.

    Each case is exactly {id, messages, expected}; expected is withheld from
    preprocessing/provider calls. baseline_prepare(messages, max_chars=...) must
    implement the project's actual setup for a real pilot. Both modes share the
    same serialized-character cap/model options. Each case/repeat has fresh
    Relay memory; mode order alternates. No automatic retries or model downloads.

    model_call(messages=..., **options) returns complete text. Optional
    score(text, expected) returns a nonempty dictionary of boolean criteria;
    default scoring is exact text after trimming outer whitespace. Failed,
    blocked, oversized, ungraded and incomplete answers never count as complete.

    A new JSONL report is flushed at every start/result. Raw messages, labels and
    answers require include_content=True; hashes/counts are the privacy default.
    Recorded model descriptions are supplied by the host, not independently
    verified. Manual maintenance effort remains null for human measurement.
    """
    for value, name in ((baseline_description, "baseline_description"),
                        (model_description, "model_description")):
        nonempty_string(value, name, limit=2000)
    if dataset_kind not in ("synthetic", "real_workflow"):
        raise ValueError("dataset_kind must explicitly be synthetic or real_workflow.")
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError("repeats must be from 1 to 20.")
    if type(max_calls) is not int or not 1 <= max_calls <= 2000:
        raise ValueError("max_calls must be from 1 to 2000.")
    if type(include_content) is not bool:
        raise TypeError("include_content must be boolean.")
    if type(max_response_chars) is not int or not 1 <= max_response_chars <= 2000000:
        raise ValueError("max_response_chars must be from 1 to 2000000.")
    if not callable(model_call) or not callable(baseline_prepare) or score is not None and not callable(score):
        raise TypeError("Supply synchronous model/baseline callbacks and an optional scorer.")
    settings = RelayConfig() if config is None else config
    if (not isinstance(settings, RelayConfig) or settings.protocol != "plain"
            or settings.db_path != ":memory:" or settings.output is not None):
        raise ValueError("Pilot config requires plain text, in-memory cases and no output retry/check policy.")
    options = {} if model_options is None else clone(model_options)
    if type(options) is not dict or "messages" in options or len(encode(options)) > 100000:
        raise ValueError("model_options must be a bounded JSON object without messages.")
    if options.get("stream", False) is not False:
        raise ValueError("The pilot requires complete text, not streaming.")
    if type(cases) is not list or not 1 <= len(cases) <= 100:
        raise ValueError("Supply from 1 to 100 labelled cases.")
    if len(cases) * repeats * 2 > max_calls:
        raise ValueError("Planned paired calls exceed max_calls; no model was called.")
    captured, ids = [], set()
    for case in cases:
        if type(case) is not dict or set(case) != {"id", "messages", "expected"}:
            raise ValueError("Each case needs exactly id, messages and expected.")
        nonempty_string(case["id"], "case id", limit=200)
        nonempty_string(case["expected"], "expected", limit=64000)
        if case["id"] in ids or len(encode(case)) > 2000000:
            raise ValueError("Case IDs must be unique and each case must fit 2000000 characters.")
        _validated_messages(case["messages"])
        ids.add(case["id"])
        captured.append(clone(case))
    totals = {mode: dict(attempts=0, completed=0, failed=0, provider_calls=0,
                        pending_reviews=0, elapsed_seconds=0.0, request_chars=0)
              for mode in ("baseline", "relay")}
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as report:
        def write(record):
            report.write(encode(record) + "\n")
            report.flush()

        from . import __version__
        write({"event": "manifest", "version": __version__, "created_at": timestamp(),
               "dataset_kind": dataset_kind, "case_sha256": _digest(captured),
               "cases": len(captured), "repeats": repeats, "planned_max_calls": len(captured) * repeats * 2,
               "baseline_description": baseline_description, "model_description": model_description,
               "model_options_sha256": _digest(options), "config": settings.to_dict(),
               "memory": (settings.memory or MemoryPolicy()).to_dict(),
               "include_content": include_content,
               "scoring": "custom boolean criteria" if score else "exact text, outer whitespace trimmed",
               "limitations": ["Only plain-text replay; no live users, tools, or side effects are evaluated.",
                               "A real_workflow label is a host declaration, not independent verification.",
                               "Character counts are not token counts. Callback timeouts belong to the host.",
                               "Manual maintenance effort requires human recording; it is not assumed zero."]})
        for repeat in range(repeats):
            for index, case in enumerate(captured):
                order = ("baseline", "relay") if (repeat + index) % 2 == 0 else ("relay", "baseline")
                for mode in order:
                    row = {"event": "result", "case_id": case["id"], "repeat": repeat, "mode": mode,
                           "completed": False, "criteria": {}, "provider_calls": 0,
                           "request_chars": 0, "pending_reviews": 0,
                           "manual_interventions": None, "maintenance_seconds": None}
                    write({"event": "started", "case_id": case["id"], "repeat": repeat, "mode": mode})
                    started = time.perf_counter()

                    def measured(*, messages, **kwargs):
                        request = _validated_messages(messages)[0]
                        size = len(encode(request))
                        row.update(request_chars=size, prompt_sha256=_digest(request))
                        if include_content:
                            row["messages"] = request
                        if size > settings.max_chars:
                            raise BudgetExceeded(size, settings.max_chars)
                        row["provider_calls"] += 1
                        value = _sync(model_call(messages=clone(request), **clone(kwargs)))
                        if type(value) is not str or not value.strip():
                            raise TypeError("model_call must decode a completed provider response to plain text.")
                        row.update(response_chars=len(value), response_sha256=_digest(value))
                        if include_content:
                            row.update(response=value[:max_response_chars], response_truncated=len(value) > max_response_chars)
                        if len(value) > max_response_chars:
                            raise ValueError("Response exceeded max_response_chars; it was not scored.")
                        return value

                    try:
                        if mode == "baseline":
                            request = _sync(baseline_prepare(clone(case["messages"]), max_chars=settings.max_chars))
                            response = measured(messages=request, **options)
                        else:
                            with connect(measured, config=settings, memory=settings.memory or MemoryPolicy()) as agent:
                                try:
                                    response = agent(messages=clone(case["messages"]), **options)
                                finally:
                                    row["pending_reviews"] = len(agent.memory.pending())
                        criteria = (_sync(score(response, case["expected"])) if score
                                    else {"exact_answer": response.strip() == case["expected"].strip()})
                        if (type(criteria) is not dict or not 1 <= len(criteria) <= 64
                                or any(type(key) is not str or not 1 <= len(key) <= 200 or type(value) is not bool
                                       for key, value in criteria.items())):
                            raise ValueError("score must return 1 to 64 named boolean criteria.")
                        row.update(criteria=criteria, completed=all(criteria.values()))
                    except Exception as exc:
                        row["error_type"] = type(exc).__name__
                        if include_content:
                            row["error"] = str(exc)[:2000]
                    row["elapsed_seconds"] = time.perf_counter() - started
                    if include_content:
                        row["expected"] = case["expected"]
                    write(row)
                    total = totals[mode]
                    total["attempts"] += 1
                    total["completed" if row["completed"] else "failed"] += 1
                    for key in ("provider_calls", "pending_reviews", "elapsed_seconds", "request_chars"):
                        total[key] += row[key]
        summary = {"event": "summary", "dataset_kind": dataset_kind, "totals": totals,
                   "manual_effort_measured": False, "comparison_budget_chars": settings.max_chars}
        write(summary)
        return clone(summary)
