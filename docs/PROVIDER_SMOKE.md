# Opt-in hosted-provider smoke check

Hosted provider checks for this release are **NOT RUN**. The normal test suite
uses real optional SDKs with simulated transports. It cannot prove that a real
service accepts encrypted reasoning, signed thinking or compaction history.

`scripts/smoke_live_provider.py` provides a separate, deliberately small check.
Without `--run-live` it writes a `NOT_RUN` report and makes no network request,
does not read credentials, and does not create an SDK client:

```sh
python scripts/smoke_live_provider.py --output evaluations/my-hosted-check-not-run.json
```

An operator who chooses to incur any provider charges can explicitly select a
provider and model, with their credential already configured in `OPENAI_API_KEY`
or `ANTHROPIC_API_KEY`. Do not paste credentials into commands or reports.
Install the optional provider SDK yourself; this runner installs nothing.

```sh
python scripts/smoke_live_provider.py --run-live --provider openai --model YOUR_MODEL --output evaluations/my-openai-check.json
python scripts/smoke_live_provider.py --run-live --provider anthropic --model YOUR_MODEL --output evaluations/my-anthropic-check.json
```

Each run makes at most two synchronous inference attempts: give the model a
fixed marker, then replay every complete native output item and ask for that
marker. It preserves actual SDK objects until Relay serializes the request.
The report distinguishes service acceptance from returning the correct marker.
No response text, ciphertext, signatures, keys or provider exception messages
are saved. Existing report paths are rejected before any provider work.

`--exercise tool` instead asks the model for one `echo_marker` call, validates
its exact fixed arguments, returns a deterministic local string using the
provider's actual call ID, and replays the complete call/result sequence. No
arbitrary function, code, path or external tool can execute. The second request
disables further tool selection. A missing, additional or unexpected call fails
visibly with no retry. Claude uses automatic tool selection because current
model families differ in support for forced tools; a model declining the tool
does not count as a successful tool-cycle check. These shapes follow the
[OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling)
and [Claude tool definitions](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools).

`--native-memory` explicitly records one trusted host fixture (`smoke_marker`)
and tests native memory projection. It replays the first prepared prefix, which
retains Claude's inserted inline system message. It does not extract arbitrary
user memory or grant tool permissions. Claude models used for this option must
support inline system messages; a service rejection is retained as an error.
Neither option enables live calls without `--run-live`, a model and credentials.
The runner validates a prepared body before dispatch. A later dispatch report
can therefore say `memory_injected: false` and `memory_present: true`: the
unchanged snapshot was already included by the earlier preparation.

The defaults cap each response at 256 output tokens and each request at 30
seconds, with no SDK retries. `--max-output-tokens` accepts 1–4096 and `--timeout`
accepts up to 120 seconds. These limits do not calculate or promise a monetary
cost. The runner uses the official public endpoint with proxy environment
settings and redirects disabled; custom gateways and Azure endpoints are
outside this check.

This runner does **not** enable model reasoning, Anthropic thinking or provider
compaction. It records whether opaque items happened to be returned. A plain
text-only pass cannot validate signed or encrypted history. It also does not
test long conversations, general tool execution, async calls, streaming, production
quality or organisational adoption. Those require separately specified tests
and explicit permission before making any paid calls.

Offline runner tests inject stand-in clients and verify opt-in, credential
handling, exact native replay, bounded calls, preserved failure evidence and
the absence of credentials from reports. They are not hosted-service evidence.
