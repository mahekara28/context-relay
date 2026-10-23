# Keep disputed preferences out of the next model call

Relay accepts recognized clear changes locally. If it detects a correction to
an existing setting but cannot settle the value, it marks that setting disputed
and pauses connected text generation. This uses no model, download or paid API.

For example, “Export as CSV” followed by “Make the export format easier to read”
does not silently continue with CSV. Your app receives a clarification question.
A clear “Export as JSON” resolves it; later requests carry JSON. Existing checked
tools also reject disputed memory, independently of the generation pause.

## Existing integrations: catch one exception

```python
from context_relay import MemoryClarificationRequired, auto_connect

with auto_connect(existing_client) as ai:
    try:
        response = ai(messages=full_history, model=your_model)
    except MemoryClarificationRequired as question:
        print(question.prompt)  # Display this through your application's UI.
        full_history.append({"role": "assistant", "content": question.prompt})
```

Keep `ai` open for the conversation, just as before. Append the user's next
answer to that same full original history and call again. Do not retry the same
unclear message expecting a different result. Reply with a full instruction such
as “Export as JSON”; an isolated “JSON” is not inferred as a global preference.
`question.targets` lists disputed keys, `question.pending` returns captured
review evidence, and `question.prepared` retains the blocked turn's snapshot.
The snapshot does not become current again if the database later changes.

Your app still owns provider response decoding and its interface. The library
does not send messages on the user's behalf. The prompt is English and uses fixed
examples; your UI can localize it from `targets`. It does not ask a model to guess
or translate the correction.

## Managed plain-text chats

```python
from context_relay import MemoryClarificationRequired, auto_connect

# A runnable offline stand-in: no SDK or model calls.
with auto_connect(lambda **kwargs: "Recorded.") as ai:
    chat = ai.conversation()
    chat.send("Export as CSV.")
    try:
        chat.send("Make the export format easier to read.", request_id="change-1")
    except MemoryClarificationRequired as question:
        print(question.prompt)
    chat.send("Export as JSON.", request_id="answer-1")
    assert ai.memory.entries()[0]["value"] == "JSON"
```

A local clarification completes that managed request and stores its assistant
question. The next new answer is allowed. Reusing `change-1` returns the existing
completed-request error, avoiding another call. Normal API/decoder failures
remain pending and follow the existing retry rules. A pause raised inside an
unrelated/nested agent is not converted into this chat's own clarification.
Storage limits still apply to local questions; a full database/history must be
handled through the normal retention or pending-request controls.

Async connections use `async with`, `await ai(...)` and `await chat.send(...)`;
the exception and memory policy are the same. A grounded retry also checks the
current prepared snapshot before its next call; a pause there does not mean
that earlier calls in that workflow never happened.

## Scope and migration

- The default is `MemoryPolicy(pause_on_uncertainty=True)`. `auto_connect`
  enables basic memory; `connect` still requires memory to be enabled.
- Existing applications that intentionally manage disputes themselves can use
  `MemoryPolicy(pause_on_uncertainty=False)`. This allows generation to continue
  with dispute metadata; checked tools still block the disputed setting.
- `AutomaticMemory.process` and `ContextGuard.prepare` are lower-level inspection
  APIs and do not enforce this connection pause. Calls bypassing Relay are not
  intercepted. Native opaque provider mode has its separate integration contract.
- A fresh review proposal with no existing preference does not trigger this
  disputed-existing-value pause. Review/confirm/dismiss remains available.
- Existing persisted transcripts are not silently re-parsed. New statements use
  the new grammar; repeated old history stays consumed. Save database/session ID
  to resume persistence. RAM remains the default.

Recognition covers bounded English, Hindi/Hinglish, Spanish and French wording
for the four standard preferences. Supported language names are preference
values, not proof that instructions in every such language are understood.
Quotations, translations, reported speech, hypotheticals and temporary requests
must not overwrite standing preferences. Conflicting or partly understood
instructions can require clarification instead of a guessed update.

A completely unrecognized instruction may still evade detection. Sarcasm,
arbitrary cross-turn references, dialects, unsupported languages and custom
business meaning are not generally understood. The optional interpreter can
suggest more recall for explicit review; it is not enabled or paid for by this
change. Relay also cannot guarantee that a model follows a correct instruction.

See [validation](VALIDATION.md) for the retained first unseen run, exposed
regression results and scripted whole-task checks. Those are local tests, not
organisational or hosted-model reliability evidence.
