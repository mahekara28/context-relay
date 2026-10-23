"""Add Context Relay around an existing Python tool, without running the UI.

Run from the source checkout:
    python examples/existing_project.py

This is a deterministic integration example. No AI model, deployment service,
network request, or paid API is used. The proposals below stand in for the tool
calls your existing model integration would return.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Makes this example runnable from a source checkout without installation.
# An installed application only needs the import below.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_relay import ActionRejected, ContextGuard


# These functions represent code that already exists in your project.
# Context Relay does not require changing their implementation.
executed_calls: list[dict[str, str]] = []


def existing_deploy(service: str, environment: str = "staging") -> dict:
    """Simulate the existing deployment function; never deploy anything."""
    call = {"service": service, "environment": environment}
    executed_calls.append(call)
    return {"simulated": True, "status": "completed", **call}


def existing_dispatcher(tool: str, arguments: dict) -> dict:
    """An existing explicit tool registry is also supported."""
    registry = {"deploy": existing_deploy}
    return registry[tool](**arguments)


def main() -> None:
    print("Context Relay: library integration, with simulated tools only.\n")

    with ContextGuard(name="Existing deployment workflow") as guard:
        guard.remember("service", "payments", source="release-config")
        guard.remember("environment", "staging", source="release-config")
        guard.remember("paused", False, source="release-config")
        guard.allow_tool(
            "deploy",
            when={"paused": False},
            bindings={"service": "service", "environment": "environment"},
            title="Deploy only to the currently requested environment",
        )

        # Capture the context BEFORE asking the model to plan an action.
        # Add turn.context_text to your existing model request as task data.
        # Keep this turn in your application; do not let the model replace it.
        old_turn = guard.turn()
        print(f"1. The agent received state revision {old_turn.revision}.")

        # While the agent is thinking, the human changes a decision.
        guard.remember(
            "environment",
            "production",
            source="release-manager",
            text="Use production for this release; staging is no longer the target.",
        )

        # Your existing code would call existing_deploy("payments").
        # Add the wrapper and call it with the same arguments instead.
        # The wrapper includes the function's default environment="staging"
        # in the check, even though the caller omitted that argument.
        deploy_from_old_turn = old_turn.wrap(existing_deploy, tool="deploy")
        try:
            deploy_from_old_turn("payments")
        except ActionRejected as error:
            print(f"2. Old plan rejected: {error.decision}.")
            print("   The simulated deployment function was never called.")
            print(
                "   Current environment:",
                error.recovery_context["facts"]["environment"],
            )
        else:
            raise AssertionError("The outdated deployment must be rejected.")

        assert executed_calls == []

        # Ask the model again using a NEW turn's context, then execute its NEW
        # proposal. Here we provide that proposal explicitly, without a model.
        new_turn = guard.turn()
        deploy_from_new_turn = new_turn.wrap(existing_deploy, tool="deploy")
        result = deploy_from_new_turn("payments", environment="production")
        assert result["simulated"] is True
        assert executed_calls == [{"service": "payments", "environment": "production"}]
        print("3. A fresh, corrected plan passed:")
        print(json.dumps(result, indent=2))

        # A successful call writes a receipt and advances the state revision.
        # Make a fresh turn for the next model request. Keeping an old wrapper
        # around is not permission to keep executing against an old snapshot.
        try:
            deploy_from_new_turn("payments", environment="production")
        except ActionRejected as error:
            assert error.decision == "refresh"
            print("4. Reusing that turn asks for fresh context; no second call ran.")
        else:
            raise AssertionError("A completed turn must become stale.")

        # Alternative integration: wrap your existing (tool, arguments)
        # dispatcher instead of each individual function.
        # Another explicit human request permits another simulated deployment.
        guard.remember(
            "environment",
            "staging",
            source="release-manager",
            text="For the next release, target staging.",
        )
        dispatcher_turn = guard.turn()
        guarded_dispatch = dispatcher_turn.wrap_dispatcher(existing_dispatcher)
        next_proposal = {
            "tool": "deploy",
            "arguments": {"service": "payments", "environment": "staging"},
        }
        result = guarded_dispatch(next_proposal["tool"], next_proposal["arguments"])
        assert result["environment"] == "staging"
        print("5. The existing dispatcher works through the same checks.")
        print("\nNo web server, model connection, or external deployment was used.")


if __name__ == "__main__":
    main()
