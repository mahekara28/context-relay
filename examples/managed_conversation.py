"""Offline managed-history example. The transport is simulated, not an LLM."""

from context_relay import MemoryPolicy, connect


def simulated_transport(*, messages):
    """Replace this with your application's existing complete-response call."""
    return {"message": {"role": "assistant", "content": "Simulated response: message received."}}


def main():
    print("SIMULATED TRANSPORT: this example makes no model or network calls.")
    with connect(simulated_transport, memory=MemoryPolicy()) as agent:
        chat = agent.conversation(system="Keep replies brief.")
        chat.send("Use CSV for export format.", request_id="first")
        chat.send("Actually, use JSON instead of CSV.", request_id="second")
        assert agent.memory.entries()[0]["value"] == "JSON"
        assert len(chat.history) == 5
        print("Saved original messages:", len(chat.history))
        print("Remembered format:", agent.memory.entries()[0]["value"])
        print("The application sent only each new message; Relay retained the history.")


if __name__ == "__main__":
    main()
