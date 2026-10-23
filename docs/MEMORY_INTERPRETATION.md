> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Optional interpretation of unclear wording

Relay's usual memory rules remain fast, local Python code. For additional wording,
you can connect an existing model to suggest what a user meant. This is optional:
**every interpreted suggestion waits for confirmation**, even in automatic mode.

In v0.14, a plausible unsupported correction naming an existing setting can also
create a clarification-only record: `value=null`, `reason="unparsed_correction"`,
and exact source evidence. This does not require a model call. The active value
remains visible but disputed, and a memory-bound action check blocks on it.
An interpreter returning `[]` or proposing an unrelated key does not clear that
uncertainty. `confirm()` rejects null placeholders; append an explicit clarified
instruction or dismiss a mistaken flag. See [memory uncertainty and migration](MEMORY_LANGUAGE.md#clarification-when-the-setting-is-known-but-the-new-value-is-not).

Recognized finite preferences now normalize case and surrounding whitespace
after selecting a target, so an interpreted `export_format="json"` proposal is
stored as `JSON`; `response_language="hindi"` becomes `Hindi`. Source quotes stay
exact. Other values and custom fields are not fuzzily normalized. This addresses
the specific spelling-equivalence issue prospectively; historical exact-match
failures and model journals are not rescored or rewritten.

The v0.14 wording/uncertainty evaluation made no model calls. Its first frozen
parser recognised 5/16 clear changes and correctly flagged 6/8 requests needing
clarification, with two wrong active updates. A subsequent narrow safety fix
removed the observed false promotions; that rerun is development evidence after
seeing the failures. It does not demonstrate that model interpretation improved.
Read [the v0.14 limits and evidence](../evaluations/CORRECTIONS-v0.14.md).

v0.15 expands the local grammar for known aliases, reordered corrections and
explicit references within one original user message. Supported setting-linked
explanations can accompany a correction, but every clause must be understood
before automatic changes are applied. This does not make an interpreted guess
automatic or turn an unclear custom value into a semantic match. See
[supported correction wording](MEMORY_LANGUAGE.md#named-corrections-across-a-complete-message).
Conflicting declarations retain their review requirement even with supporting
clauses. Historical values and unresolved references among compatible named
settings do not become automatic corrections.
The [v0.15 evidence](../evaluations/CORRECTIONS-v0.15.md) preserves the first
frozen-parser failures; later runs on the exposed cases are development results.
They do not demonstrate better model interpretation or new unseen accuracy.
See [validation scope](VALIDATION.md).

This helper is **experimental**. A six-call v0.12 local Qwen 2.5 3B check produced
3/4 exact clear candidates and abstained on 2/2 ambiguous cases. The fourth clear
suggestion had the right key but lowercased `JSON` to `json`; that exact-score
failure is retained. All suggestions stayed pending, and four additional
misleading controls made no model call. This small synthetic set is not a
reliability estimate. The earlier v0.9 run missed both intended changes; an
earlier attempt also proposed the wrong key. Different cases/settings prevent a
direct improvement comparison. Read [all current results](../evaluations/CORRECTIONS-v0.12.md)
and [historical failures](../evaluations/LIMITS-v0.9.md).

The v0.13 follow-up on 32 independently authored cases made eight actual local
calls with the same prompt/schema/options. Of six clear requests sent to the
model, two produced exact pending values, two abstained and two differed in
letter case. Of two ambiguous-reference review cases, one produced the expected
pending value and one abstained. Including the parser's three clear successes,
that covers 5/16 clear cases, but only 3/16 were applied automatically. Every
model suggestion still needed review. All 12 abstention controls made zero model
calls. Read the [full v0.13 results](../evaluations/CORRECTIONS-v0.13.md); these
small synthetic checks do not establish general language accuracy.

The v0.13 local grammar recognises additional everyday phrases and can propose
narrow contextual changes without invoking this helper. Those deterministic
results do not establish that the model interpreter improved; its prompt and
model-response validation remain unchanged in this release. The deterministic
v0.12 unseen set applied only 1/12 clear corrections. That set is now development
material; its new score is not an unseen estimate. The earlier 1/11 denominator
incorrectly excluded a valid em dash because of console rendering; the
erratum (local-only reference; omitted from public distribution) preserves the original
corpus and raw report hashes.

For example, after `Keep responses detailed.`, the message
`I want the response style changed to brief.` can produce a proposed change to
`response_style = brief`. The existing value remains active and marked disputed
until your application or user confirms the proposal. A proposal is user recall,
never a verified fact, instruction authority, or permission to run tools.

## Repeated requests and uncertainty

An interpreter's suggestion does not resolve uncertainty just because it names
the right key. If a new unclear request targets `export_format`, an empty result,
an unrelated suggestion, or a suggestion equal to its active value cannot make
that request disappear. A newly retained proposal for a different concrete value
can represent the request, but it still requires confirmation. If a repeated
suggestion is deduplicated against an older proposal, Relay retains the new
clarification requirement separately.

Concrete proposals capture the earlier uncertainty IDs they can resolve.
Suppose a JSON proposal is pending and the user then makes another unclear
export-format request. Confirming that older JSON proposal cannot clear the
later uncertainty. A new concrete request in review mode captures the newer
uncertainty context rather than silently reusing an approval with an older
basis. Inspect the current `pending()` records before choosing an action.

Null clarification records cannot be confirmed. Append the user's actual clear
instruction or explicitly dismiss a mistaken flag. Review, same-value handling,
deduplication and finite-value normalization do not grant a model suggestion
authority over tool permissions or application-recorded facts.

These safeguards use the existing schema-2 uncertainty records and confirmation
API. v0.14 and v0.15 read schemas 1 and 2; older schema-1-only workers must not
write a migrated session. An upgrade does not reinterpret already processed
history or rerun the interpreter for previously consumed messages.

## Reuse your existing local model

The helper writes the extraction prompt, parses the JSON, and checks each exact
source quotation. It does not create an HTTP client, download a model, or install
an SDK. Supply the synchronous model function you already use and, when needed,
a decoder for its response object:

```python
from context_relay import MemoryInterpreter, MemoryPolicy, RelayConfig, connect

interpreter = MemoryInterpreter.from_model(
    existing_local_model_call,             # accepts messages=... and model options
    decoder=read_text_from_model_response,  # omit if your callable returns a string
    model_options={"model": "your-already-installed-model"},
)

with connect(
    your_main_model_call,
    config=RelayConfig(db_path="relay.sqlite3"),
    memory=MemoryPolicy(),
    memory_interpreter=interpreter,
) as agent:
    chat = agent.conversation()
    chat.send("Keep responses detailed.")
    chat.send("I want the response style changed to brief.")
    for proposal in agent.memory.pending():
        print(proposal["value"], proposal["source"]["quote"])
        if proposal["value"] is None:
            print("Ask the user to clarify this setting.")
            # Append their actual clarified instruction, or explicitly dismiss
            # a mistaken flag. Null records cannot be confirmed.
            # agent.memory.dismiss(proposal["id"])
            continue
        # Show the proposal to a user/operator, then confirm their actual choice:
        # agent.memory.confirm(proposal["id"])
        # or agent.memory.dismiss(proposal["id"])
```

These callables belong to your application; they are placeholders, not bundled
model implementations. Use the underlying model client as the extraction callable,
not a Relay connection using this same interpreter, which would recurse. Keep any
model timeout on the original client. A remote extraction model can incur its own
API costs; an already running local model needs no paid API.

For an Ollama-backed callback, the tested structured-output configuration is:

```python
schema = {
    "type": "array", "maxItems": 4,
    "items": {
        "type": "object",
        "properties": {key: {"type": "string"} for key in ("key", "value", "quote")},
        "required": ["key", "value", "quote"], "additionalProperties": False,
    },
}
interpreter = MemoryInterpreter.from_model(
    existing_local_model_call,  # Return complete text, or provide a decoder.
    model_options={
        "stream": False, "format": schema,
        "options": {"temperature": 0, "seed": 17, "num_predict": 512, "num_ctx": 4096},
    },
)
```

These are Ollama request options; another provider needs its own equivalent.
The callback must already select an installed local model and enforce a timeout.
Structured output constrains shape, not interpretation. For a complete runnable
version using only Python's standard library and a verified installed local
model, run from the source tree with `src` on `PYTHONPATH`:

```sh
python examples/evaluate_interpretation_v012.py --model qwen2.5:3b --output evaluations/my-interpretation-run.jsonl
```

It makes at most six calls, refuses to overwrite a report, saves raw synthetic
requests/responses, and checks that replay makes no extra extraction call. It
never downloads a model or confirms a proposal. The recorded calls took roughly
3.4–43.7 seconds on the evaluation machine; do not assume free local inference
is fast. Raw timing/token metadata (local-only reference; omitted from public distribution)
is retained alongside the failed casing check.

The extraction model must return a JSON array of objects with exactly `key`,
`value`, and `quote`. An empty array means no proposal. Quotes must be exact,
unique substrings of the source; Relay calculates character offsets itself.
Markdown fences, invented quotations, oversized outputs, and unknown fields fail
validation. Relay makes no automatic repair or retry call.

The prompt distinguishes input-envelope labels from remembered settings and asks
the model to reuse an existing setting's exact key when proposing a change. It
includes one unrelated example of this mapping. These instructions can help a
small model, but they do not verify its interpretation: it can still miss a
preference or suggest the wrong key or value. Exact quotes and operator review
remain necessary.

## Supply your own interpreter

`MemoryInterpreter(callback)` also accepts an application's own synchronous
function. Its input is a dictionary containing:

- `text`: one newly appended, eligible original user message.
- `index`: its original index in the current conversation segment.
- `known_memory`: bounded accepted `key`, `value`, and `disputed` records.

Return a list of dictionaries containing exactly `key`, `value`, `quote`, `start`,
and `end`. `text[start:end]` must equal `quote`; the offsets count Python string
characters. Keys are lowercase ASCII identifiers up to 80 characters and values
are nonempty strings up to 256 characters. Defaults allow at most four proposals
and 4,096 serialized output characters per callback. Accepted and pending results
also share the existing `MemoryPolicy` caps.

## Calls and failure behavior

The helper is **not a fallback for every missed sentence**. The six model calls
above used these eligible inputs, each with an existing setting:

| Input sent to the model | Result in this one run |
| --- | --- |
| `I need future exports delivered as JSON.` | Pending `export_format=json`; exact casing check failed. |
| `I want the response style changed to brief.` | Pending `response_style=brief`. |
| `Please switch the language of future replies over to Hindi.` | Pending `response_language=Hindi`. |
| `I want weekly deliveries moved into the afternoon.` | Pending `delivery_window=afternoon`. |
| `Use JSON for it.` with two possible settings | No proposal. |
| `Make one of those JSON.` with two possible settings | No proposal. |

In v0.13, direct polite requests such as `Could you use Hindi for your replies
from now on?` are recognised locally. An unfamiliar direct request such as
`Could you make the tone warmer going forward?` can now reach an explicitly
configured interpreter. The callback still receives the full original text and
every result remains pending. Quoted, reported, hypothetical, temporary and
unsupported negative wording stays excluded. These filters still miss real user
intentions. The historical six-call result does not validate model interpretation
of the newly eligible wording; new callback tests check routing and review rules,
not model accuracy.

- Supported deterministic wording never calls the interpreter. Replayed messages,
  including previously missed or dismissed wording, never call it again.
- Only newly unsupported direct requests such as `Keep it like before, but
  shorter.` are eligible. Supported forms such as `Answer briefly` and a
  qualifying `Make it shorter` are handled locally without a model call.
  Fixed request wrappers (`I need you to`, `Going forward`, `I'd like`, `Could
  you ...?`) can also pass the eligibility filter, but the callback still receives
  the full original text. Direct unfamiliar compound descriptions such as `Keep
  the tone warm and informal` can reach review without being split into invented
  settings. Quotes, code, unsupported negatives, hypotheticals, reported speech,
  secret-related wording, general questions and unknown surrounding prose remain
  conservatively excluded. This
  is still a restricted English input filter, not general language understanding
  or a comprehensive sensitive-data detector.
- An empty result consumes the input. If no known setting requires clarification,
  it is reported as `interpreter_no_proposal`; otherwise the processing report's
  `pending_ids` identifies the new clarification-only records.
  A callback failure or malformed result raises `MemoryInterpretationError` and
  leaves the entire batch's memory and cursor unchanged. Retry explicitly after
  fixing the cause; callback side effects and model charges cannot be rolled back.
- Model work runs outside SQLite transactions. If another worker changes the
  session revision or memory cursor meanwhile, Relay rejects the stale commit.
  It does not repeat extraction automatically. Concurrent duplicate preparations
  can both run extraction, although only one can commit that history.
- Async connections run interpretation preparation in a worker thread. Cancelling
  a request prevents a later memory commit when cancellation is observed before
  saving; an already completed commit remains. A running synchronous model call
  cannot be forcibly cancelled and may keep working until its own timeout.

Confirmed entries keep `source.method = "interpreter"` and the original quotation.
Exact provenance proves where the suggestion came from; it does **not** prove that
the model interpreted the quotation correctly. Review is the boundary that
prevents a guess from silently replacing active memory.
