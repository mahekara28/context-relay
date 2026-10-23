"""Offline Anthropic-shaped connection; the local stand-in is NOT an AI model.

Replace it with your existing Anthropic/AsyncAnthropic client in an application.
That application's real provider requests may incur charges.
"""

from types import SimpleNamespace

from context_relay import MemoryPolicy, connect_anthropic


def main():
    requests = []

    def local_transport(**request):
        requests.append(request)
        return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Offline response; no inference performed."}]}

    client = SimpleNamespace(messages=SimpleNamespace(create=local_transport))
    with connect_anthropic(client, memory=MemoryPolicy()) as ai:
        chat = ai.conversation(system="Help with this project.")
        chat.send("Export as CSV.", model="host-chosen-model", max_tokens=128)
        result = chat.send("Export as JSON.", model="host-chosen-model", max_tokens=128)
        print(result["content"][0]["text"])
        print(f"Prepared {len(requests)} local requests; system text was included in Relay's budget.")
        print("Current memory:", ai.memory.snapshot())


if __name__ == "__main__":
    main()
