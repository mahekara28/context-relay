# Experimental task handoff

This source-checkout preview carries unfinished work between local harnesses.
The bundled v0.22.0a3 wheel predates it. Install from this checkout with Python
3.11 or newer:

```sh
python -m pip install '.[mcp]'
python -m context_relay task --db .relay/tasks.db doctor
```

Create a task in the Git worktree and copy the returned `id`:

```sh
python -m context_relay task --db .relay/tasks.db create \
  --goal 'Produce a JSON net-revenue report by region' \
  --criterion 'The independent report oracle passes' --root .
```

Record the original and revised instructions with their actual source refs.
Use the returned instruction ID from the first command in the second command:

```sh
python -m context_relay task --db .relay/tasks.db instruct TASK_ID \
  --expected-revision 0 --request-id instruction-csv \
  --text 'Use CSV' --source-kind user --source-ref message-1
python -m context_relay task --db .relay/tasks.db instruct TASK_ID \
  --expected-revision 1 --request-id instruction-json \
  --text 'Use JSON' --source-kind user --source-ref message-2 \
  --supersedes OLD_INSTRUCTION_ID
```

The current revision is now 2. Offer it to a named local harness, then accept
it from that harness identity:

```sh
python -m context_relay handoff --db .relay/tasks.db --subject sender offer TASK_ID --recipient receiver
python -m context_relay handoff --db .relay/tasks.db --subject receiver accept HANDOFF_ID
python -m context_relay task --db .relay/tasks.db diagnose TASK_ID
```

The accepted packet contains the active JSON instruction and its source; the
superseded CSV instruction remains in task history. `--subject` is a local
harness label, not authentication. Keep the SQLite file private to the OS user.
The host supplies real identity and policy when embedding the Python API.

Connect the optional MCP server to a local harness with `connector claude
configure` or `connector codex configure`; see [MCP setup](MCP_TASK_SERVER.md).
The connector commands edit project files only. The generated files contain
absolute paths and are ignored by Git. Claude Code is not required to run the
offline checks.

The [coding continuation fixture](../evaluations/continuation/README.md) and
the two-client tests exercise the changed-instruction handoff. They do not
measure live model performance. A live Claude Code to Codex run and a matched
Markdown baseline remain required before claiming a cross-harness improvement.
MCP registration alone does not capture tool results, inject the packet into a
model, or verify completion. Host-run criterion checks and file effect intents
are explicit Python APIs in `task_verification.py` and `task_effects.py`.
