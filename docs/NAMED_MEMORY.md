# Reopen a conversation by name

Choose an application-owned name once. Relay finds the stored session automatically:

```python
from context_relay import auto_connect

with auto_connect(your_model_call, conversation="support-42") as agent:
    chat = agent.conversation()
    chat.send("Export as JSON.")

# After closing the app or restarting Python:
with auto_connect(your_model_call, conversation="support-42") as agent:
    chat = agent.conversation()
    chat.send("Prepare the next export.")  # Stored history and memory are restored.
```

Named conversations use `.relay/sessions` relative to the working directory. Set `storage="/your/private/data"` to make that location independent of the working directory. `namespace="billing"` separates one application's names from another's. Use the same directory, namespace and name on restart. Names are hashed into safe filenames; a name is not a path. `named_storage_path(...)` lets the host inspect the resolved database location without creating it.

Each named conversation has its own SQLite database, reducing contention between unrelated conversations. Simultaneous openers select one canonical root inside a transaction. Reopening follows completed history rollover to its latest segment. Pending requests and unresolved crash claims stay pending: reopening never silently repeats a provider call.

`agent.conversation()` manages the transcript. If the application instead calls `agent(messages=...)` directly, it still owns the full message history and must provide the appropriate continuation. Named persistence saves Relay's state; it cannot recover messages that the host never supplied or retained. Native provider replay still requires the host's complete native history.

Existing `auto_connect(client)` calls remain temporary, preserving the previous API and avoiding surprise disk storage. A name explicitly opts into saving. Named mode cannot be combined with an external guard, session ID, or configured `db_path`; those are separate explicit storage APIs.

For a shared application, derive identity from authentication and use `SessionManager.connect(client, principal=principal, conversation="ticket-42")`. The manager places that name inside the authenticated owner's storage and checks permissions on each exposed operation. A plain namespace/name is not authentication. Keep the storage directory private to the host application; path validation is not a sandbox against another local process that can replace files.

To delete a managed conversation, review `chat.deletion_plan()` and pass that unchanged plan to `chat.delete_history(expected=plan)`. Deletion removes the whole linked history, memory and named-session mapping. Reopening the name creates an empty conversation. Pending/in-flight requests block deletion. This is logical deletion, not erasure of backups, free pages or data already sent to a provider.

Tool functions can be reattached to an identical stored contract after restart:

```python
actions = agent.protect_tools({"save_export": save_export}, resume=True)
```

`resume=True` accepts the identical contract without changing its revision. Changed contracts are refused; review and explicitly replace their policy using the existing registry/guard API. Resumption does not grant an old proposal new permission, and a source with an expired timestamp still fails verification.
