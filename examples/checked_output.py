"""Offline stand-in demonstrates bounded correction; no LLM or API calls."""

from context_relay import OutputPolicy, connect


def main():
    replies = iter(["Here is the data: Ada", '{"name":"Ada"}'])
    calls = []

    def simulated_model(*, messages):
        calls.append(messages)
        return next(replies)

    with connect(simulated_model, output=OutputPolicy(
        format="json", required_keys=("name",), max_retries=1,
    )) as agent:
        result = agent.invoke(messages=[{"role": "user", "content": "Return a JSON object for Ada."}])
        assert result.output_report.attempts == len(calls) == 2
        print("SIMULATED responses; no model inference.")
        print(result.response)
        print(result.output_report)


if __name__ == "__main__":
    main()
