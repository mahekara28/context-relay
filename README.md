# Context Relay

**Keep current instructions and check actions in the Python AI agent you already use.**

**v0.21.0a1 · Local alpha · Python 3.10+ · Zero runtime dependencies · MIT**

Long conversations can leave an agent following an old instruction, acting on
stale data or claiming success after a failed action. Relay keeps supported
preferences outside the model's working context and checks registered actions
against your application's rules. Your project keeps its model, tools and UI.

Relay is a library, not an LLM-training system or a guarantee against context
rot. Basic memory is easy to connect. Stronger protection requires explicit
tools, trusted data, permissions and outcome checks.

## Install locally

From the extracted `relay-v0.21.0a1-public` directory, in your project's Python
environment:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.21.0a1-py3-none-any.whl
python -m context_relay quickstart
```

The quickstart runs offline with a fixed stand-in and prints `CSV -> JSON`. It
tests the connection, not model quality. The wheel is supplied with the source
bundle; this release is not published on PyPI. No API account, hosting or model
download is needed for this check.

## Connect basic memory

```python
from context_relay import auto_connect

with auto_connect(existing_client, conversation="customer-42") as ai:
    response = ai(messages=full_history, model=your_model)
```

Send later calls through the same `ai` connection with the full original,
append-only history. Relay recognises supported Ollama, OpenAI and Anthropic
clients and Python callables. Keep your normal provider options; Anthropic also
needs `max_tokens`. OpenAI uses Chat Completions by default. Async clients use
`async with` and `await`.

The conversation name opts into local persistent storage. Use the same name and
storage location after restart; omit it for temporary memory. Relay leaves your
existing client open. Installing it alone does not intercept other calls or
protect tools. This default connection adds no model calls; your chosen provider retains its
usual costs.

[Beginner setup](docs/GETTING_STARTED.md) ·
[Supported connection options](docs/AUTO_CONNECT.md) ·
[Named memory](docs/NAMED_MEMORY.md)

## Protect important work

The synchronous `ResponsesWorkflow` combines these options for an OpenAI
Responses client or a compatible local endpoint:

| Layer | What your application declares |
| --- | --- |
| Instructions | Fields that require explicit review before each new user turn can proceed |
| Action coverage | Exact tool inventory, checked arguments and any deliberate unchecked exceptions |
| Evidence | Current source values, freshness limits and a host verification callback |
| Effects | Destination idempotency, version checks and permission checks |
| Completion | Required receipt fields plus an optional independent destination readback |
| Long history | Bounded fresh-segment handoff, archived originals and explicit unfinished-task state |

[The protection profile](docs/PROTECTION_PROFILE.md) shows these checks together
using the real file-writing [Verified Release Desk example](examples/verified_release_desk.py).
[The workflow guide](docs/RESPONSES_WORKFLOW.md) explains integration, restart and
reconciliation. These controls apply to the registered execution route; calls
directly to your original functions bypass it.

For high-impact preferences, opt into
`MemoryPolicy(assurance_fields=("export_format",))`. The workflow returns
`review_required` before inference or action. Your application presents the
original request for review, then records an explicit choice:

```python
plan = workflow.review_plan()
# After the host/user explicitly chooses JSON for this request:
workflow.resolve_review(plan, decisions={
    "export_format": {"value": "JSON", "scope": "turn"},
})
result = workflow.resume_review(plan["request_id"])
```

Use `scope="durable"` for a standing preference. `resume_review` continues the
same request without adding another user turn. Every new user turn requires
review of the configured fields, including harmless messages and wording the
parser already understands. Review cannot grant tool permission.

This gate addresses a missed-instruction risk by asking explicitly. It does not
improve language understanding: before this change, default extraction found
only **1 of 11 clear changes** in a fresh, synthetic diagnostic. The gate asks for
review on all 18 diagnostic cases, including five unchanged controls. Those
figures are a small development diagnostic, not market-wide accuracy.
[Instruction assurance and its cost](docs/INSTRUCTION_ASSURANCE.md).

## What still needs your application

- **Truth and meaning:** configured fields, arithmetic and receipt conditions
  can be checked; arbitrary reasoning and business correctness cannot. A source
  or a host verifier can still be wrong.
- **Recovery:** known-value repair works before an effect starts. Relay never
  automatically reruns a started action. Interrupted or uncertain effects need
  destination inspection and reconciliation.
- **Context:** optional handoffs omit earlier native reasoning from the new
  model input. Originals remain archived, but needed details must be retrieved.
  The host explicitly updates or finishes the task.
- **Privacy and scale:** persistent SQLite data is plaintext. New-file access
  restrictions and deletion controls do not provide encryption or guaranteed
  erasure. Storage and lock waits are bounded; distributed reliability is not
  established.

The complete workflow currently handles one synchronous supported function call
per response. Streaming, parallel effects and arbitrary provider-native formats
have separate limits. There is no organisational pilot or established general
context-rot reduction percentage. See [validation](docs/VALIDATION.md) for actual
checks, failures and the distinction between simulated SDK responses and local
model experiments.

[Current release](docs/RELEASE-v0.21.md) ·
[Free local model setup](docs/ALPHA.md) ·
[Long-workflow continuation](docs/WORKFLOW_CONTINUATION.md) ·
[Storage privacy](docs/STORAGE_PRIVACY.md) ·
[Publishing a clean copy](docs/PUBLISHING.md)

The optional inspection UI runs with `python run.py serve` at
`http://127.0.0.1:8765`. It is a local demonstration, not required by the library.
