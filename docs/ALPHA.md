# Relay 0.22.0a3 local alpha

Relay is a library for existing Python agents. Basic connection adds supported
preference memory. The optional [protection profile](PROTECTION_PROFILE.md)
combines explicit instruction review, checked tools, source verification,
destination readback and bounded history continuation.

The package is not published to PyPI. [Release validation](RELEASE-v0.22.0a3.md)
records the checks completed for this patch. Local demonstrations are not a
customer pilot.

## Install the supplied wheel

Use Python 3.10+ in your project's environment. From the public source root:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.22.0a3-py3-none-any.whl
python -m context_relay quickstart
```

The wheel has zero runtime dependencies. The offline quickstart uses a fixed
stand-in: no API key, model, account or hosting. It checks memory wiring, not
model quality. Examples and synthetic evaluation fixtures live in the source
bundle; the wheel contains the library and UI.

For a source checkout with setuptools 77 or later already installed:

```sh
python -m pip install --no-index --no-build-isolation --no-deps .
```

The import name is `context_relay`; the distribution is `context-relay-local`.
Optional SDKs belong to the application. See [beginner setup](GETTING_STARTED.md)
for `auto_connect()` and [the workflow guide](RESPONSES_WORKFLOW.md) for actions.

## Try Verified Release Desk with a free local model

[Verified Release Desk](../examples/verified_release_desk.py) reads actual
artifact files, asks for explicit format review, checks one export action and
reads back its physical output. Its underlying destination enforces
source-version conditions and request-ID uniqueness. The complete library
configuration is explained in [the protection profile](PROTECTION_PROFILE.md).

Use an already running local Ollama server with a supported Responses endpoint
and an already downloaded model that supports the needed tool schema. The
optional OpenAI SDK can connect to that endpoint without an OpenAI account:

```sh
python -m pip install openai==3.36.0
```

From the public source root, replace `YOUR_INSTALLED_MODEL` with your local model
name. This first command presents a review plan without model inference:

```sh
python examples/verified_release_desk.py --directory .relay/alpha-release-desk --release 0.22.0a3 --artifact dist/context_relay_local-0.22.0a3-py3-none-any.whl --request-id demo-001 --message "For this export only, use JSON." --model YOUR_INSTALLED_MODEL
```

Read the request and plan. If you explicitly choose JSON for this export,
repeat the same command with the following two options appended:

```sh
--review-format JSON --review-scope turn
```

That resumes the original request and runs local inference. Use `durable` for a
standing choice. The example never infers the review choice from model output.
It uses a fixed local demonstration identity; replace that with your application's
authentication and authorization before multi-user use.

The example writes real files and SQLite data below the chosen directory and
uses `http://127.0.0.1:11434/v1`. Keep the same request ID for the same intended
operation. Inspect an interrupted effect at the destination; creating a new ID
is not safe recovery. A missing checked intent can leave readback unresolved
after a crash, requiring application-specific reconciliation.

No model is installed or downloaded by Relay. A loopback endpoint alone does not
prove its model runs locally; use local weights and disable cloud features if
offline operation is required. Hardware and electricity still cost resources.
Installing the optional SDK can access the package index. The example imports
Relay from your Python environment; a configured `PYTHONPATH=src` instead uses
the checkout. Keep that distinction in any evaluation report.

## Boundaries to understand

- **Instructions:** default extraction is bounded and can miss clear changes.
  It found only 1 of 11 clear updates in the fresh synthetic diagnostic.
  `MemoryPolicy(assurance_fields=(...))` requires explicit review for selected
  fields on every new user turn, including unchanged messages. That is a
  deliberate confirmation cost, not better language understanding.
- **Coverage and truth:** the application declares its exact tool inventory,
  trusted identity, permissions and sources. Source and destination verifier
  callbacks are host-owned. Checks do not establish arbitrary business truth,
  and calls that bypass the registered route remain unprotected.
- **Recovery:** repair changes supported proposals before effects start. A
  returned callback is not enough for completion: receipt conditions must pass
  and, when configured, destination readback must accept. Unavailable readback
  stays pending. Recovery never automatically repeats a started tool.
- **Context:** optional `WorkflowHistoryPolicy` starts a fresh native segment
  and retains originals in an archive. Prior opaque reasoning is omitted from
  the fresh model input; this is not provider compaction or lossless reasoning
  continuity. Task completion is explicit. All history/storage limits remain.
- **Protocol:** the complete workflow is synchronous and accepts one supported
  client function call per response. Streaming, parallel actions, server tools,
  asynchronous effects and arbitrary mixed compaction/action output are outside
  this integration. Other adapters retain their own documented scope.
- **Storage:** named conversations opt into persistent plaintext SQLite. New
  files/directories receive restricted creation permissions, but existing paths
  need review. Logical deletion, secure-delete settings and WAL checkpointing
  do not guarantee physical erasure. This is single-host storage; long callbacks
  can hold writer locks and distributed reliability is not established.

Schemas use a bounded JSON Schema subset: explicit JSON types, closed objects
with all fields required, arrays, enum/const and simple size/numeric bounds.
Unsupported keywords fail at setup. Final repaired arguments are checked locally.
Host code must still enforce destination idempotency and atomic version checks;
Relay cannot atomically commit an external service with its own receipt.

Use a separate development database or back up existing state before an alpha
upgrade. Assurance state uses memory schema 4, introduced in 0.21; earlier
releases cannot read it. Version 0.22 adds no memory schema migration.
Resuming a workflow requires its identical configuration. A changed protection
profile needs a deliberate migration or a separately identified workflow.

## Optional verification environment

The recorded SDK/framework environment is pinned separately from the core:

| Package | Pinned version |
| --- | --- |
| OpenAI | 3.36.0 |
| Anthropic | 2.0.0 |
| Ollama | 0.6.3 |
| LangChain Core | 1.6.13 |
| LangGraph | 1.2.16 |
| HTTPX / HTTPX2 | 0.28.1 / 2.16.0 |
| Pydantic | 2.14.1 |

[Direct requirements](../requirements-validation.txt) and
[constraints](../requirements-validation-constraints.txt) describe that
verification environment, not mandatory runtime dependencies or a guarantee for
other versions. They are pins, not downloaded artifacts with verified hashes.
In a separate environment, with setuptools installed:

```sh
python -m pip install -r requirements-validation.txt
python -m pip install --no-build-isolation --no-deps .
python -m pip check
python scripts/verify_sdks.py --report local-results/sdk-alpha.json
python -m unittest discover -s tests -p "test_workflow_sdk_v020.py" -v
```

Create the output directory first and choose a new report path. Package
installation may use the network; these compatibility tests simulate transport
responses and do not call models. They do not establish hosted-provider
acceptance or model quality.

The [CI workflow](../.github/workflows/alpha.yml) configures core checks on
Ubuntu/Python 3.10 and 3.12, Windows/Python 3.12, and optional SDK checks on
Ubuntu/Python 3.12. The workflow has no publishing step or model calls.
Read the [actual runner results](https://github.com/mahekara28/context-relay/actions/workflows/alpha.yml)
before claiming that matrix passes. Use the public folder as the GitHub
repository root so its `.github/workflows` directory is discovered.

[Release changes](RELEASE-v0.22.md) · [Validation](VALIDATION.md) ·
[Storage privacy](STORAGE_PRIVACY.md) · [Clean publishing](PUBLISHING.md)
