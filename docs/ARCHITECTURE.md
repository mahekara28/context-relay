# Architecture and integration contract

The model proposes. The harness owns context provenance and tool execution. Relay stores the task record and checks the proposal at that boundary.

```text
Trusted user/application update
              |
              v
    SQLite events + current facts + policies
              |
       Relay.context()
              |
     snapshot retained by harness
              |
              v
      model proposes tool + arguments
              |
       guarded_tool_call()
              |
        current-state check
         /             \
 block / refresh       allow
       |                 |
 current context      executor callback
 + evidence              |
       |           success/failure receipt
    re-plan
```

## State model

Each session has a monotonic integer revision. Fact changes, policy changes, and execution receipts append an event and advance the revision. Checks are recorded separately and do not advance it. Fact updates overwrite the active value while preserving the original events. Different sessions do not share facts.

`record_fact(..., expected_revision=N)` offers optimistic concurrency control; a stale editor raises `ConflictError`. A source label and text are caller-supplied descriptions. They do not authenticate the caller. Policies must come from trusted code or an authorised operator, never directly from a model proposal.

The context compiler serializes only current facts and their source references. Policy history and tool receipts remain available through the store but are not automatically added to model context. Your harness can selectively retrieve original event text when useful. If textual evidence is sent to a model, preserve its status as data, not higher-priority instructions.

## Before the model call

`ContextGuard.prepare(messages, ...)` captures one state snapshot, compiles a plain-text conversation with that state, and returns a `PreparedTurn`. Its `.messages` and `.report` are defensive copies, and its tool wrappers keep the same captured revision. A concurrent state change after capture cannot silently relabel that request as current.

The compiler preserves initial system/developer instructions, required recent turns, pins, current recorded state, and the pending user request. Other whole turns fill the remaining budget. It adds an explicitly labelled user-role data message containing state and exact coverage counts before the pending request. It neither promotes source text to system authority nor infers semantic supersession. Unsupported provider-native tool and multimodal payloads are rejected.

Per-message costs and per-turn costs are precomputed; optional selection checks do not repeatedly serialize the whole history. Final serialization confirms the exact character budget including JSON and coverage metadata. This is not a provider token estimate. The host retains the untouched original transcript. See [context preparation](CONTEXT.md) for the input contract and limitations.

## Reusable middleware

`ContextGuard` owns one local session and store. It records facts/policies and captures a `GuardedTurn` before the model request. A turn wraps existing functions or dispatchers, returns JSON-compatible tool results directly, and raises `ActionRejected` on block/refresh. Wrapped functions keep their calling convention; defaults are checked and execution uses the checked argument copies. See [integration](INTEGRATION.md).

The wrapper supports synchronous, fixed signatures; variadic functions need an adapter or dispatcher. Awaitable/generator functions are rejected. The high-level turn budget can be reduced but cannot exceed the core execution ceiling of 12,000 characters.

## API overview

| Entry point | Result |
| --- | --- |
| `RelayStore(path)` | SQLite-backed store; use as a context manager or call `close()` |
| `create_session(name)` | Session including its unique `id` |
| `record_fact(sid, key, value, source=..., text=..., expected_revision=...)` | New fact event |
| `add_policy(sid, policy)` | Registers or replaces a policy by ID and appends its event |
| `events`, `facts`, `policies`, `checks` | Inspection data for the session |
| `Relay(store).context(sid, max_chars=12000, keys=None)` | Snapshot with revision, values, source references, serialized text, and size |
| `audit_context(sid, candidate)` | `ok`, differences, and restored context |
| `check(sid, action)` | Recorded allow/block/refresh decision, issues, and recovery context |
| `execute(sid, action, executor)` | Decision plus execution result and receipt when invoked |
| `guarded_tool_call(relay, sid, captured_context, tool_call, executor)` | Trusted-revision adapter around `execute` |

The low-level `action` envelope includes `tool`, `arguments`, and `context_revision`. Only use it inside trusted application code. The adapter takes a model envelope containing exactly `tool` and `arguments` and obtains the revision from the harness-owned snapshot. Never let the model supply or rewrite `captured_context`.

`executor(tool_name, arguments)` must return a JSON-serializable value. An exception or non-serializable result raises `ToolExecutionError` and records a failure receipt. The exception's original cause is chained. If a side effect occurred before an exception, Relay cannot undo it. A non-serializable result can also occur after the executor completed; inspect the receipt and reconcile instead of blindly retrying.

## Policy example

```json
{
  "id": "deploy-staging",
  "title": "Only deploy the current service after tests pass",
  "tool": "deploy",
  "requirements": [
    {"key": "paused", "op": "eq", "value": false},
    {"key": "tests", "op": "eq", "value": "passed"}
  ],
  "bindings": {"service": "service", "environment": "environment"},
  "optional_args": []
}
```

All requirements must pass. `in` accepts a list of permitted values. A missing fact fails even for `ne`. A binding requires an argument and compares its exact JSON value with the latest fact. Booleans, integers, floats, and strings are distinct (`true` does not equal `1`). Additional arguments fail unless explicitly listed in `optional_args`.

Only one policy exists per tool per session. Updating the same policy ID replaces it and advances the revision. An empty requirements/bindings policy deliberately permits that tool with no arguments. The developer is responsible for choosing meaningful prerequisites.

## Transaction boundaries

SQLite uses WAL and serializes writers. One store instance uses a reentrant lock for thread access. `execute` checks, invokes the callback, and records the result under a write transaction. Competing updates through the same database cannot slip between the check and callback. A callback must not write through its store or depend on another writer completing; keep it short to avoid lock timeouts.

This protects local recorded state. It does not lock external systems, provide distributed atomicity, coordinate disconnected database copies, or replace authentication and authorisation. For real external tools, use an allowlisted dispatcher, operation IDs, idempotent APIs where available, and an explicit recovery process. Long-running/distributed tools need a different reservation/commit design before production use.

## Workbench HTTP API

The server binds to `127.0.0.1` and validates Host and Origin. It serves its own static assets, uses no CDN, and requires `Content-Type: application/json` plus `X-Relay-Client: workbench` on POST requests. Request bodies are capped at 128 KiB. These browser protections are not authentication against other local processes.

| Route | Purpose |
| --- | --- |
| `GET /api/health` | Version and local/simulation status |
| `GET/POST /api/sessions` | List or create sessions |
| `GET /api/sessions/{id}` | Consistent state, history, context, and demo data |
| `POST /api/sessions/{id}/facts` | Record a fact with optional expected revision |
| `POST /api/sessions/{id}/policies` | Register/update a policy |
| `POST /api/sessions/{id}/check` | Check a low-level action envelope |
| `POST /api/sessions/{id}/audit` | Audit the snapshot in `candidate` |
| `POST /api/sessions/{id}/simulate` | Guard and execute one of four local demo callbacks |
| `GET /api/sessions/{id}/export` | Export the complete session as JSON |

Custom policies can be checked in the workbench. Arbitrary custom tool execution is deliberately a Python integration concern; the workbench only simulates its four built-in demo tools.

Workbench fact updates validate the default context budget before committing. Oversized state introduced through the Python API remains inspectable and exportable in the UI, but compilation/guarded execution still enforce their configured budget. The UI does not silently truncate that state.
