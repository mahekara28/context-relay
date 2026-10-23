"""One setup call with a fixed local function; no SDK, model, or network."""

from context_relay import auto_connect


def main():
    requests = []

    def existing_model_call(*, messages):
        requests.append(messages)
        return "Fixed offline response; no AI inference."

    with auto_connect(existing_model_call) as ai:
        chat = ai.conversation()
        chat.send("Export as CSV.")
        chat.send("Export as JSON.")
        print("Current remembered format:", ai.memory.entries()[0]["value"])
        print(f"Prepared {len(requests)} offline requests through one connection.")


if __name__ == "__main__":
    main()
