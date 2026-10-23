# Let a local model suggest memory changes

Relay's built-in rules handle a limited set of instructions without calling a
model. A local interpreter can suggest changes when a message falls outside those
rules, including unfamiliar English and other languages. It costs no API credits:
inference runs through your installed Ollama model and uses your computer's resources.

The model is a second reader, not a source of approval. A suggestion always waits
for review. An exact quote proves where the suggestion came from; it does not
prove that the model understood the request correctly.

## Connect it

Start Ollama and run `ollama list` to see models already installed. Choose one of
those names explicitly. Relay does not install Ollama, download models, sign into
an account, or fall back to a hosted service.

```python
from context_relay import MemoryInterpreter

interpreter = MemoryInterpreter.from_ollama(
    model="qwen2.5:3b",  # Example only: use a model you have already installed.
)
```

Pass `memory_interpreter=interpreter` when creating your Relay connection. The
connection to your main agent stays the same: the extraction model can be local
even when the agent uses another provider. See [connection options](CONNECT.md)
and [automatic memory](AUTOMATIC_MEMORY.md).

Construction makes no request. When an eligible, unmatched user message arrives,
Relay checks the selected model's metadata and makes one separate extraction call.
It sends that message and a compact list of remembered settings, not the entire
conversation. Recognized instructions continue to use the normal rule path.

## Review before accepting

For example, Relay remembers `delivery_window = evening`. The user writes:

> Make deliveries early in the day.

The model may suggest `delivery_window = morning`, with the user's exact words
attached. Relay keeps the old entry disputed while that change awaits review.
The application can show it through `agent.memory.pending()` and, after a person
checks the value and the source, call `agent.memory.confirm(proposal_id)`.

Do not automatically confirm every model proposal or use a confidence score as
approval. Rejection and clarification are valid outcomes. A returned `[]` means
the model offered no suggestion; it does not confirm that an old value is correct.
Existing disputes stay unresolved. For workflows that must review every fresh
instruction before a protected action, use [instruction assurance](INSTRUCTION_ASSURANCE.md).

Temporary exceptions use Relay's scope handling. This interpreter's schema only
represents durable preferences: it must not turn “just for this report” into a
permanent setting. Known quoted examples, reported or hypothetical statements,
and sensitive credential patterns remain excluded by the memory layer. Those
guards are finite, so review is still required for unfamiliar wording.

## Bounds and failures

```python
interpreter = MemoryInterpreter.from_ollama(
    model="qwen2.5:3b",
    endpoint="http://127.0.0.1:11434",
    timeout=120.0,
    max_input_chars=12000,
    max_output_chars=4096,
    max_proposals=4,
    max_tokens=768,
    review_unmatched=True,
)
```

| Setting | Meaning |
| --- | --- |
| `timeout` | Total network deadline across metadata and generation, including slow response headers or bodies. Range: 0.05–300 seconds. |
| `max_input_chars` | Maximum serialized message/index/known-settings envelope. Oversized input is rejected before any request; it is never silently cut down. Range: 1–1,000,000. |
| `max_output_chars` | Maximum generated JSON text and validated proposal payload. Range: 1–2,000,000. |
| `max_proposals` | Maximum suggestions in one response. Range: 1–64. |
| `max_tokens` | Generation cap sent to Ollama. Range: 1–8,192. A response that hits its generation limit is rejected. |
| `review_unmatched` | Allow more unmatched user wording to reach review-only interpretation. It does not grant authority or bypass scope and framing guards. |

The transport uses JSON schema output, temperature zero, an 8,192-token context
request, and requests disabled truncation and context shifting. A character limit
is not an exact token limit, especially across languages. Use a recent Ollama
version that honors these flags; older servers may ignore unknown options.
Reducing the input bound is safer than assuming a larger character limit will fit.
The helper does not measure semantic correctness or prove that every input token
was retained by a server.

The 120-second default allows for a cold local model; it is a ceiling, not a
target. Set a shorter budget if your application must respond quickly, and measure
your chosen model on the hardware that will run it. Enabling this optional feature
adds latency and computation; Relay's default rule path makes no model call.

Only `http://localhost`, numeric IPv4 loopback addresses, and IPv6 `::1` endpoints
are accepted. `localhost` is pinned to `127.0.0.1`. Proxy variables, API keys and
`OLLAMA_HOST` do not change this helper's destination. HTTP redirects are refused.
Model metadata is capped at 256 KiB; chat responses are capped at eight times
`max_output_chars` plus 16 KiB for the JSON envelope and metadata.

Malformed JSON, duplicate object keys, non-finite values, invented or repeated
quotes, incomplete responses, tool calls, remote-model metadata and refusals are
rejected. A transport failure raises `MemoryInterpretationError`; that processing
batch is not saved or consumed. Surface the error to the caller, then retry
explicitly after fixing the problem. Relay does not retry, skip the failed message,
or quietly reuse its old setting as a successful interpretation.

## Keep the daemon local too

A loopback address alone is insufficient: Ollama can forward cloud model aliases.
Before sending user text, this helper checks `/api/show` for local model metadata
and refuses remote models. Cloud-tagged model names are rejected at construction.
For an additional daemon-level boundary, disable Ollama's cloud features with
`OLLAMA_NO_CLOUD=1` and restart Ollama, following its
[local-only configuration guide](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).

Relay trusts the local daemon to report and execute its model configuration. It
cannot stop a modified daemon or another local process from forwarding data,
changing a model after the metadata check, or recording requests. Use a daemon you
control. A timeout closes Relay's connection; how quickly inference stops is up to
Ollama.

## Bring an existing extraction call

`MemoryInterpreter.from_model(...)` remains available for an application-owned,
synchronous model callable. Set `review_unmatched=True` explicitly if you want the
broader eligibility. Its default stays conservative for existing integrations.
That callable owns its timeout, network policy and costs; the loopback transport
bounds above apply only to `from_ollama`.

Never pass the Relay-wrapped agent call back into its own interpreter. The built-in
Ollama helper makes a direct HTTP call, so it does not recursively invoke Relay.

Ollama references: [chat API](https://docs.ollama.com/api/chat),
[structured outputs](https://docs.ollama.com/capabilities/structured-outputs), and
[request fields](https://github.com/ollama/ollama/blob/main/api/types.go).
