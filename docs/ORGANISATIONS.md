# Scoped local sessions for an application

`SessionManager` adds a host-controlled permission boundary, separate SQLite
storage for each `(tenant, subject)` owner, a concurrent-operation cap, and
aggregate metrics. It is a local foundation, not a hosted identity service or
production-scale certification.

```python
from context_relay import Principal, SessionManager

# Your application's authentication/permission system supplies this callback.
def authorize(principal, action):
    return your_permissions.check(principal.tenant, principal.subject, action)

with SessionManager(".relay-users", authorize=authorize, max_concurrent=8) as manager:
    # Construct this from your verified login, never from an LLM or request body.
    principal = Principal(tenant=verified_tenant_id, subject=verified_user_id)
    with manager.connect(client, principal=principal) as ai:
        chat = ai.conversation()
        response = chat.send(user_text, model=your_model)
        saved_session_id = chat.session_id
    print(manager.metrics())
```

Add provider-required options such as Anthropic's `max_tokens`. For an async
client use `async with manager.connect(...)` and `await chat.send(...)`; the
manager itself is a synchronous context manager. This scoped facade accepts
complete plain-text replies only. Streaming and tool replies are unsupported;
other core APIs retain their broader documented protocol support.

The callback must return exactly `True` for `connect`, `call`, `read`, or `manage`.
There is no allow-by-default callback. It is invoked before every exposed
operation and again before returning the result. If access is revoked while a
model is running, its result is withheld. The request already sent and local
state changes cannot be undone by revocation. Permission callback failures also
stop the operation. Keep this callback short and synchronous.

Reconnect with the same authenticated principal and `session_id` to resume.
Foreign and unknown session IDs produce the same access error. The manager
chooses storage paths from a digest of both identity fields; supplied guards or
database paths cannot bypass that selection. The SDK client remains unchanged.

Conversation handles expose send/retry, read-only history/pending/memory views,
archive search, reviewed memory confirmation, and explicit deletion-plan APIs.
`chat.enable_history_index()` explicitly builds the optional owner database's
FTS index under `manage` permission; `search_history(query, mode="terms")` uses it.
Building/rebuilding an index can block writers while that maintenance runs.
With automatic rollover, save **chat.session_id**, which follows the successor.
Deletion needs the separate `manage` permission:

```python
plan = chat.deletion_plan()
# Your app presents the scope and obtains the user's deletion intent.
chat.delete_history(plan)
```

The maximum counts active wrapper operations, including local reads/setup, not
only model calls. Excess work raises `CapacityExceeded` immediately; Relay does
not silently queue or retry billable requests. Cancellation releases capacity.
Finish active operations before closing a connection or manager. The manager
owns all connections it creates until they close, including rollover children;
it does not close your model client.

Metrics contain only numeric counts, active/peak operations and aggregate call
durations. They contain no messages, identity labels, session IDs, response text,
or error details. They measure return from a complete call. Snapshots are
process-local and reset on restart.

## Boundaries before deployment

- Authentication belongs to the host. A `Principal` is not proof of identity;
  constructing one from an untrusted tenant/user field defeats the boundary.
- Keep this manager, its callbacks and files private to trusted server code.
  Python introspection, direct file access or core APIs can bypass a wrapper.
  This is not a sandbox against malicious code in the same process.
- The storage folder needs OS access controls, backup/retention policy and any
  required encryption. Hashed filenames do not encrypt stored messages.
- No cross-user sharing, identity-provider login, distributed quotas or global
  concurrency coordinator is implemented. One file per owner has operational
  limits at large numbers of owners.
- Core APIs outside this manager retain their original local trust model. The
  demo workbench is not an organisation administration/authentication service.
- Tool execution needs the host's own authorization and Relay's explicit guard
  integration; these chat wrappers do not approve arbitrary tool actions.

Run `python examples/evaluate_concurrency.py --output evaluations/my-load.json`
for a bounded thread/SQLite exercise using fixed local responses. It is not an
LLM speed benchmark or evidence of production capacity. See [validation](VALIDATION.md).
