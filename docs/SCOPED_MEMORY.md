# Standing preferences and one-time instructions

Relay distinguishes a standing preference from an explicit exception for the current request. For example, `Export as CSV.` sets the standing format. `For this export only, use JSON.` makes the effective format JSON for that request without replacing the standing CSV preference. A later independent export returns to CSV.

```python
from context_relay import auto_connect

with auto_connect(existing_model_call) as agent:
    history = [
        {"role": "user", "content": "Export as CSV."},
        {"role": "user", "content": "For this export only, use JSON."},
    ]
    result = agent.invoke(messages=history)
    standing = agent.memory.snapshot()       # CSV
    current = agent.memory.effective_snapshot()  # JSON, scope="turn"
```

The model prompt, configured memory-bound action checks, output checks and deterministic repair use the same effective preference. Appending an independent user request expires the exception and advances the revision, so a proposal from the earlier context cannot execute under the new state. Replaying the same unchanged input does not expire its exception. Application facts and permissions remain separate: user instructions cannot grant a host permission or replace an authoritative amount.

Direct settings can contain quoted values, including `Set the export format to "JSON".` and `Set the project name to "North; South".` Quoted or reported instructions are not extracted as commands. Exact original source spans remain available for inspection.

Unclear or conflicting preferences pause rather than choosing one. Relay does not infer when an arbitrary task, session or calendar-day exception ends. Unsupported lifetimes need clarification. A recognized bare continuation after a one-time exception also needs clarification when its lifetime is uncertain. This is intentionally more conservative than guessing whether the earlier action finished.

`entries()` and `snapshot()` expose standing memory. `effective_snapshot()` includes current overrides and disputes; `context_snapshot()` uses that effective view. A durable unresolved conflict is not silently settled by a temporary exception. `forget(key)` removes both standing and active scoped recall. Forgetting is not secure deletion of historical audit events or transcript text.

Scoped state uses memory schema 3. Keep a database backup before upgrading; older Relay versions must not open and enforce a database containing newer scoped state. A scope-changing memory event cannot be undone into a different request; append a clear new instruction instead.

## Unfinished work across history rollover

Managed conversations that enable `HistoryPolicy` retain one bounded open task: exact user requests and updates, with source references. Acknowledgements and assistant replies do not mark it finished. This task record is not an independent grant of tool authority. Old assistant progress remains in the searchable archive, and coverage reports distinguish the active segment from omitted archived messages.

The task record permits at most 32 user updates and 16,000 serialized characters. It never silently evicts an unfinished task when full. The host can explicitly consolidate with `chat.update_task(text, expected_revision=...)` or finish with `chat.finish_task(expected_revision=...)`. Inspect `chat.task_snapshot()` first. These operations are also available on owner-scoped conversations and recheck authorization. An idle pending request can be consolidated before an explicit retry; an in-flight request cannot.

This is one bounded task record, not a general project manager or a guarantee of understanding arbitrary language. A model can still misunderstand supplied context. Register action checks for the actual data and permissions that matter.
