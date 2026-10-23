"""Offline automatic rollover/index/deletion demo using a TEMPORARY database."""

import tempfile
from pathlib import Path

from context_relay import HistoryPolicy, MemoryPolicy, RelayConfig, connect


def simulated_model(*, messages):
    """A transport stand-in, not an LLM or evidence of model reliability."""
    return "Acknowledged."


def main():
    with tempfile.TemporaryDirectory() as temporary:
        with connect(simulated_model, config=RelayConfig(db_path=str(Path(temporary) / "demo.sqlite3")),
                     memory=MemoryPolicy()) as agent:
            chat = agent.conversation(history_policy=HistoryPolicy(rollover_after_chars=50))
            chat.send("Use JSON for export format.", request_id="first")
            original_id = chat.session_id
            chat.send("Continue with the invoice.", request_id="second")
            assert chat.session_id != original_id
            indexed = chat.agent.guard.store.enable_history_index()
            page = chat.search_history("JSON", mode="terms" if indexed else "literal")
            assert page["messages"][0]["session_id"] == original_id
            assert chat.agent.memory.entries()[0]["value"] == "JSON"
            print("Archived source found; current preference retained.")
            plan = chat.deletion_plan()
            assert len(plan["sessions"]) == 2
            # This is deliberately destructive ONLY in this temporary fixture.
            result = chat.delete_history(expected=plan)
            assert result["deleted"]["sessions"] == 2
            print("Both temporary segments and their logical records deleted.")


if __name__ == "__main__":
    main()
