"""Offline archival example: simulated transport, no model/API calls."""

import tempfile
from pathlib import Path

from context_relay import MemoryPolicy, RelayConfig, connect


def simulated_transport(*, messages):
    return "Simulated acknowledgement."


def main():
    print("SIMULATED TRANSPORT: no network or model inference.")
    with tempfile.TemporaryDirectory() as directory:
        config = RelayConfig(db_path=str(Path(directory) / "relay.sqlite3"))
        with connect(simulated_transport, config=config, memory=MemoryPolicy()) as agent:
            chat = agent.conversation(system="Keep replies brief.")
            chat.send("Export as CSV.", request_id="first")
            original_id = chat.session_id
            revision = agent.guard.store.get_session(original_id)["revision"]
            successor = chat.rollover(expected_revision=revision)

        with connect(simulated_transport, config=config, memory=MemoryPolicy(), session_id=successor) as agent:
            chat = agent.conversation()
            assert len(chat.history) == 1  # Only the original system prefix.
            assert agent.memory.entries()[0]["value"] == "CSV"
            chat.send("Export as JSON.", request_id="second")
            matches = chat.search_history("CSV")
            assert matches["messages"][0]["session_id"] == original_id
            print("Current format:", agent.memory.entries()[0]["value"])
            print("Archived original:", matches["messages"][0]["message"]["content"])
            print("Current transcript messages:", len(chat.history))
            print("Old originals remain in SQLite and can be paged or searched.")


if __name__ == "__main__":
    main()
