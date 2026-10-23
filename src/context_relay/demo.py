"""Small, explicitly simulated workflows. No external services or models."""
from __future__ import annotations

import json

from .engine import Relay
from .models import BudgetExceeded
from .store import RelayStore


def seed_session(store: RelayStore, name: str, scenario: str) -> dict:
    if scenario not in {"refund", "deployment", "custom"}:
        raise ValueError("Choose refund, deployment, or custom.")
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Session name must contain 1–100 characters.")
    session = store.create_session(name, scenario)
    sid = session["id"]
    if scenario == "refund":
        for tool, resolution in (("issue_refund", "refund"), ("create_replacement", "replacement")):
            store.add_policy(sid, {
                "id": tool, "title": f"Customer must currently request a {resolution}",
                "tool": tool,
                "requirements": [
                    {"key": "resolution", "op": "eq", "value": resolution},
                    {"key": "order_status", "op": "eq", "value": "delivered"},
                ],
                "bindings": {"order_id": "order_id"}, "optional_args": [],
            })
        initial = [
            ("order_id", "ORD-1042", "This conversation concerns order ORD-1042."),
            ("order_status", "delivered", "The order was delivered. This is simulated order data."),
            ("resolution", "refund", "The customer initially requested a refund."),
        ]
        for key, value, text in initial:
            store.record_fact(sid, key, value, text=text, source="demo:initial")
        store.record_fact(sid, "resolution", "replacement",
                          text="Actually, send a replacement. Do not refund this order.",
                          source="demo:correction")
    elif scenario == "deployment":
        store.add_policy(sid, {
            "id": "deploy-staging", "title": "Deploy only when current tests pass and the release is not paused",
            "tool": "deploy",
            "requirements": [
                {"key": "paused", "op": "eq", "value": False},
                {"key": "tests", "op": "eq", "value": "passed"},
            ],
            "bindings": {"service": "service", "environment": "environment"}, "optional_args": [],
        })
        store.add_policy(sid, {
            "id": "inspect-tests", "title": "Inspect tests for the current service and environment",
            "tool": "inspect_tests", "requirements": [],
            "bindings": {"service": "service", "environment": "environment"}, "optional_args": [],
        })
        for key, value, text in [
            ("service", "payments", "Work is scoped to the payments service."),
            ("environment", "staging", "Staging only. Production is outside this task."),
            ("tests", "passed", "The initial simulated test run passed."),
            ("paused", False, "The release was initially ready to proceed."),
        ]:
            store.record_fact(sid, key, value, text=text, source="demo:initial")
        store.record_fact(sid, "tests", "failed", text="A later schema test failed. The earlier pass is superseded.",
                          source="demo:correction")
        store.record_fact(sid, "paused", True, text="Pause deployment until the schema problem has been reviewed.",
                          source="demo:correction")
    return store.get_session(sid)


def display_context(store: RelayStore, session_id: str) -> dict:
    """Keep oversized imported sessions inspectable; execution still has a hard budget."""
    relay = Relay(store)
    try:
        return relay.context(session_id)
    except BudgetExceeded as exc:
        context = relay.context(session_id, max_chars=exc.size_chars)
        context.update(over_budget=True, budget_chars=exc.max_chars)
        return context


def demo_context(store: RelayStore, session_id: str) -> dict:
    session = store.get_session(session_id)
    scenario = session["scenario"]
    current = display_context(store, session_id)
    if scenario not in {"refund", "deployment"}:
        return {
            "description": "Record facts and explicit tool policies to inspect your own workflow.",
            "stale_context": current,
            "proposed_action": {"tool": "example_tool", "arguments": {}, "context_revision": current["revision"]},
            "safe_action": None,
        }
    old_facts, old_sources = {}, {}
    old_revision = 0
    for event in store.events(session_id):
        if event.get("source") == "demo:correction":
            break
        old_revision = max(old_revision, event.get("sequence", 0))
        if event["kind"] == "fact":
            old_facts[event["key"]] = event["value"]
            old_sources[event["key"]] = event["id"]
    stale = {"session_id": session_id, "revision": old_revision, "facts": old_facts, "sources": old_sources}
    stale["text"] = json.dumps(stale, ensure_ascii=False, separators=(",", ":"))
    stale["size_chars"] = len(stale["text"])
    stale["size_bytes"] = len(stale["text"].encode("utf-8"))
    if scenario == "refund":
        proposed = {"tool": "issue_refund", "arguments": {"order_id": "ORD-1042"}, "context_revision": old_revision}
        safe = {"tool": "create_replacement", "arguments": {"order_id": "ORD-1042"}, "context_revision": current["revision"]}
        description = "The customer changed their request. An older context snapshot still asks for a refund."
    else:
        args = {"service": "payments", "environment": "staging"}
        proposed = {"tool": "deploy", "arguments": args, "context_revision": old_revision}
        safe = {"tool": "inspect_tests", "arguments": args, "context_revision": current["revision"]}
        description = "A later test failed and deployment was paused. An older snapshot still says the release is ready."
    return {"description": description, "stale_context": stale, "proposed_action": proposed, "safe_action": safe}


def simulation_result(tool: str, arguments: dict) -> dict:
    if tool == "issue_refund":
        return {"simulated": True, "status": "completed", "message": "Refund simulated. No money was moved.", "order_id": arguments["order_id"]}
    if tool == "create_replacement":
        return {"simulated": True, "status": "completed", "message": "Replacement simulated. No shipment was created.", "order_id": arguments["order_id"]}
    if tool == "deploy":
        return {"simulated": True, "status": "completed", "message": "Deployment simulated. No infrastructure was changed.", **arguments}
    if tool == "inspect_tests":
        return {"simulated": True, "status": "completed", "message": "Test inspection simulated. Inspect the latest recorded result before proceeding.", **arguments}
    raise ValueError("This tool has no simulator. Connect your own executor through the Python library.")


SIMULATED_TOOLS = frozenset({"issue_refund", "create_replacement", "deploy", "inspect_tests"})


def connection_demo() -> list[str]:
    """Check default auto_connect memory using a local stand-in, never inference."""
    from .automatic import auto_connect

    observed = []

    def local_stand_in(*, messages, model):
        # Inspect the generated packet in this controlled fixture only.
        packet = next(message["content"] for message in messages
                      if message["content"].startswith("APPLICATION-RECORDED TASK DATA\n"))
        state = json.loads(packet[packet.index("{"):])["state"]
        preferences = {entry["key"]: entry["value"]
                       for entry in state["user_memory"]["entries"]}
        observed.append(preferences["export_format"])
        return f"Local stand-in received {observed[-1]}."

    history = [{"role": "user", "content": "Use CSV for the export format."}]
    with auto_connect(local_stand_in) as ai:
        reply = ai(messages=history, model="offline stand-in")
        # This stand-in returns text. Decode your real provider's reply as usual.
        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": "Actually, use JSON instead of CSV."})
        ai(messages=history, model="offline stand-in")
    return observed

