# Prepare context before your model call

Context Relay is a library inside your existing Python application. It prepares the messages that your model will receive, keeps explicitly recorded current decisions visible, and tells your application which conversation turns were omitted. The optional tool guard can then check an action against the same captured state.

This is intended to reduce avoidable context loss in long agent conversations. It does not train the model or guarantee that the model will follow every retained instruction. No dashboard, paid API, additional model, or Relay server is required.

## The simple idea

Suppose a user first requests a JSON export, later changes it to CSV, and then discusses many unrelated details. A recent-only conversation window can drop both the original export requirements and the correction.

Your application records the current format with `guard.remember(...)`. Before its next model call, it asks `guard.prepare(...)` to assemble a bounded request. Relay includes that recorded state, preserves the instruction prefix and selected important turns, and reports omissions. Your application sends the prepared messages through the model client it already uses.

**The application must record the correction first.** Relay does not automatically understand the conversation and discover that CSV replaced JSON. An authoritative form submission, confirmed user instruction, or validated application event can supply that update.

## Add it to an existing model loop

```python
from context_relay import ContextGuard

guard = ContextGuard("agent-state.db", name="Export assistant")

# Your application handles a confirmed change in the user's request.
guard.remember(
    "output_format",
    "csv",
    source="confirmed-user-request",
    text="Correction: use CSV. JSON is no longer accepted.",
)

# `messages` is your application's existing complete plain-text conversation.
prepared = guard.prepare(
    messages,
    max_chars=16_000,
    recent_turns=2,
    pinned=(1,),  # Original message index; its entire turn is retained.
)

# Replace only the messages argument in your existing model request:
response = your_existing_model_call(messages=prepared.messages)

# Keep this report in your own logs or surface a useful coverage indicator.
coverage = prepared.report
```

`your_existing_model_call` stands for your existing provider integration; it is not a function supplied by Relay. The full original `messages` list is left unchanged. Keep the original conversation in your application's normal storage, since the prepared request contains only the selected context.

The current API accepts a list of dictionaries containing exactly `role` and string `content`. Supported roles are `system`, `developer`, `user`, and `assistant`. System/developer instructions must form a prefix. The conversation then consists of user-led turns with their assistant replies, ending in the current user request. Provider-specific tool-call objects, tool-result roles, images, audio, and other multimodal content need a separate adapter and are rejected by this version.

## What survives preparation

- The initial system/developer instruction prefix.
- The latest user request and the configured recent turns.
- Entire turns containing indices listed in `pinned`, so a protected request does not lose its associated assistant reply.
- A data block containing the captured current facts, their source event IDs, state revision, and an indication of omitted context. It is inserted immediately before the latest request.
- Additional older whole turns when the budget permits. Optional lexical retrieval can prioritize older matching turns.

Fact values and source labels remain application data, not new system instructions. A label supplied through `source` is not authentication. Do not promote untrusted model output or retrieved documents into authoritative state merely because Relay can store them. Formatting state as a data block is not a complete prompt-injection defense.

The library does not silently shorten required instructions, required turns, or current recorded state to make them fit. If that required content is too large, it raises `BudgetExceeded`. Your application must then choose a larger supported budget or explicitly reduce what it treats as required. It should not silently continue with partial critical state.

`recent_turns` includes the final pending user request as one turn. For example, `recent_turns=2` protects that request and the preceding user-led turn with its assistant replies. It must be at least 1. Optional older turns are tried newest first by default; a turn that cannot fit is skipped so a smaller older turn may still be retained. The final output preserves the selected messages' chronological order.

## Read the coverage report

```python
print(prepared.report["size_chars"])
print(prepared.report["max_chars"])
print(prepared.report["included_indices"])
print(prepared.report["omitted_indices"])
```

| Field | Meaning |
| --- | --- |
| `size_chars` | Size of the complete prepared message list, including Relay's data block. |
| `max_chars` | The requested serialized-message character ceiling. |
| `original_size_chars` | Size of the original message list under the same measurement. |
| `total_messages` | Number of original messages supplied to preparation. |
| `included_indices` / `omitted_indices` | Original message positions retained or excluded. These do not count the new Relay data block as an original message. |
| `required_indices` | Original messages protected by the prefix, recent/latest-turn, and pin requirements. |
| `retrieved_indices` | Original messages selected through optional lexical retrieval. |
| `omitted_turns` | A list of omitted whole turns, each with its original indices and omission reason. Use `len(...)` for the count. |
| `state_revision` | Revision of the current state captured for this prepared request. |
| `history_id` | On `PreparedTurn`: fingerprint of the captured original transcript and session; separate from state revision. |
| `token_budget` | Present only when supplied: counted input tokens, limit, reserves, overhead, and counter name. |
| `recovery` | On recovered requests: source history ID, requested indices, and whole-turn indices restored. |

The size calculation is `len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")))`. This is a reproducible **character** budget, not a model token count, request byte count, or price estimate. Your provider may add framing or other request fields; separately check its actual token limits. Pinned indices refer to the supplied original list, so update them if your application changes that list's structure.

## Add a model-token budget

Pass a synchronous counter for the **complete chat-formatted message list** used by your model:

```python
from context_relay import TokenBudget, make_hf_token_counter

# `tokenizer` is your application's already loaded, matching local tokenizer.
budget = TokenBudget(
    8192,
    make_hf_token_counter(tokenizer),
    reserve_output_tokens=1024,
    overhead_tokens=0,
    counter_name="my-model-chat-template",
)
prepared = guard.prepare(messages, max_chars=32_000, token_budget=budget)
print(prepared.report["token_budget"])
```

The optional helper invokes `tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=False)` and counts the returned flat token-ID list. It imports and downloads nothing. Supply your own counter when your model uses a different template or additional fields. The [Transformers chat-template documentation](https://huggingface.co/docs/transformers/main/chat_templating) explains why role framing and generation markers matter. A tokenizer for a different model, or a mismatched server template, does not establish your actual limit.

The permitted input is `max_tokens - reserve_output_tokens - overhead_tokens`. All values are validated; input capacity must remain positive. Use overhead for request content outside the message list, such as a tool or structured-output schema. The generation request's output cap must agree with the reserve you chose. Counts include Relay's state and coverage message. Both character and token caps apply; required material over the token limit raises `TokenBudgetExceeded` with the measured count and limit. Optional whole turns that do not fit are skipped.

Counters must return a nonnegative integer, not a boolean, coroutine, generator, or estimate disguised as an exact count. They receive defensive message copies and should be deterministic and side-effect-free. Counting errors stop preparation. No counter means no token guarantee. Reports label the measurement `caller_supplied_counter`; Relay cannot independently verify it against a remote server.

The default character-only selector retains its efficient cached character accounting. Token-aware selection may run your counter on the required set, each candidate that fits the character budget, and the final result. Whole-chat tokenization can therefore become expensive for very long histories. Measure this integration's latency; there is no assumed constant-cost token counter or speedup.

## Recover omitted original evidence

`guard.prepare()` captures one in-memory archive of the original transcript. Edits to the caller's list or to returned messages cannot redirect later recovery. Archive IDs include the session and original messages; identical scoped histories have the same ID. They detect mismatched snapshots and are not authentication or cryptographic proof of the speaker.

Your application identifies source indices using its own source lookup or the omission report. To inspect an exchange:

```python
evidence = prepared.recover_evidence(
    [source_index], history_id=prepared.history_id, max_chars=8000,
)
print(evidence.indices)   # Complete original turn, in chronological order.
print(evidence.messages)  # Exact original role/content dictionaries.
```

Selecting an assistant reply includes its originating user message and all replies in that turn. Requests are deduplicated and sorted. A wrong history ID raises `EvidenceMismatchError`. The packet is never shortened: `BudgetExceeded` is raised when it exceeds the exact serialized-message character cap.

`evidence.as_message()` is an optional **user-role historical data packet**. Original indices and roles are nested inside its JSON `entries`, and `current_authority` is false. Do not turn historical assistant suggestions or quoted instructions into new system instructions or confirmed current facts. This framing alone does not make hostile text safe. **Appending this packet to a prepared request invalidates that request's previous size report**; budget the complete request again.

The preferred bounded path restores the originals through the compiler:

```python
recovered = guard.recover_context(
    prepared, [source_index], history_id=prepared.history_id,
)
new_response = your_existing_model_call(messages=recovered.messages)
# Wrap the tools for this NEW decision with recovered.wrap(...).
```

This returns a new `PreparedTurn`: original roles and chronology are preserved, the selected whole turns become mandatory, existing pins/selection options/budgets are retained, and the latest recorded facts are captured. You may explicitly override `max_chars` or supply another `token_budget`. Required content exceeding either cap raises. `None` retains the existing token budget; it does not silently disable it. Recompilation cannot make a stale fact true, change tool permissions, or refresh an old proposal. A different guard/session cannot recover this turn.

The archive stays with the prepared object, so storage is linear in the full transcript and remains in memory until the object is released. It is not automatically persisted to SQLite, encrypted, or shared across workers. Keep your normal durable conversation store and tenant access checks. For compiler-only usage, `EvidenceArchive(messages, scope_id=...)` provides the same read-only recovery without a guard.

## Optional retrieval

For tasks where some older text is relevant, opt in to lexical matching:

```python
prepared = guard.prepare(
    messages,
    max_chars=16_000,
    recent_turns=2,
    retrieve_turns=1,
    query="cache stampede mutex lock ownership",
)
```

Retrieval is off by default. It uses text matching, not embeddings, a paid API, or semantic understanding. A matching word is not proof of relevance or truth. Retrieval does not make an obsolete decision current; use recorded facts for confirmed current decisions and pins for requirements that must survive. Inspect `retrieved_indices` and `omitted_indices` when deciding whether enough evidence is available.

## Use the same snapshot for tool checks

If the model can call tools, use the same prepared object to wrap those tools. That keeps the state used to build its input tied to the state revision checked before execution.

```python
guard.allow_tool("export_data", bindings={"output_format": "output_format"})

prepared = guard.prepare(messages, max_chars=16_000, pinned=(1,))
guarded_export = prepared.wrap(export_data, tool="export_data")

proposal = your_existing_model_call(messages=prepared.messages)
# In your tool handler, invoke guarded_export instead of export_data:
result = guarded_export(**proposal["arguments"])
```

`export_data` and the proposal shape in this snippet represent your existing function and model integration. The model is not allowed to choose the captured revision. Your application owns `prepared` and its wrappers.

If a new decision arrives after preparation, `guard.remember(...)` advances state. An action proposed from the older prepared context can then raise `ActionRejected` without executing its callback. Return the reasons and current evidence to your agent loop, prepare a new request, and obtain a new proposal. Do not relabel an old proposal with a fresh revision just to pass the check.

`prepared.wrap_dispatcher(existing_dispatcher)` supports a synchronous `(tool_name, arguments)` dispatcher. The wrappers retain the limitations described in [the integration guide](INTEGRATION.md): supported synchronous Python functions, JSON-compatible results, explicit policies, one checked action at a time, and application-managed idempotency for external side effects. The 12,000-character state ceiling enforced by the action guard is separate from the prepared-message budget, which may be larger.

## Try the examples

From the source directory:

```sh
python examples/context_window.py
python examples/context_window.py --json
python examples/evaluate_context.py
python examples/evaluate_context.py --json
```

The context-window example builds a long synthetic conversation, pins an early compatibility requirement, records a later format correction, and prepares a smaller request. It confirms that the original transcript remains unchanged and that a still-later correction blocks a tool associated with the old prepared request. No model, real export, or network call runs.

The evaluation compares three synthetic fixtures against a deliberately simple recent-only whole-turn baseline using the same character budget. It reports literal text availability, serialized size, and omitted turn counts. Relay receives disclosed extra application inputs such as explicit facts and pins; one fixture opts in to lexical retrieval. This is a mechanical feature demonstration, not a market benchmark or a measurement of reasoning, accuracy, hallucinations, latency, or API savings.

For a useful real-agent evaluation, use the same model and task set for both approaches, record which application inputs each receives, and check actual completed outcomes against independently specified requirements. Retaining the right text is a necessary input property for many tasks; it does not establish that a model used that text correctly.

## What remains the application's job

Relay cannot protect a decision that was never recorded or pinned, infer missing evidence from an incomplete transcript, or ensure that free-form model answers are correct. It does not automatically inspect a repository, synchronize another user's chats, or attach itself to ChatGPT or Claude. Your application decides which state is authoritative, stores the full history, passes the prepared messages to its model, and routes relevant tool calls through the wrappers.

The first release is local Python middleware with deterministic context preparation and action checks. Training-data collection and model fine-tuning are outside this feature's scope.
