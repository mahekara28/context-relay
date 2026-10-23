"""Offline OpenAI-shaped connection example; the stand-in is NOT a model.

Replace the local client with your existing OpenAI/AsyncOpenAI client and chosen
model in an application. That application's API requests may incur charges.
"""

from types import SimpleNamespace

from context_relay import MemoryPolicy, connect_openai


def main():
    requests = []

    def local_transport(**request):
        requests.append(request)
        return {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Offline stand-in response; no inference performed."
        }}]}

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=local_transport)))
    with connect_openai(client, memory=MemoryPolicy()) as ai:
        chat = ai.conversation()
        chat.send("Use CSV for the export format.", model="host-chosen-model")
        response = chat.send("Actually, use JSON for the export format.", model="host-chosen-model")
        print(response["choices"][0]["message"]["content"])
        print(f"Prepared {len(requests)} local requests; protocol={ai.config.protocol}.")
        print("Current memory:", ai.memory.snapshot())


if __name__ == "__main__":
    main()
