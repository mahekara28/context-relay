# Original-source checks and recovery candidates

An agent can write a plausible answer and attach a source that it never received,
or quote words that do not appear in the original conversation. Relay can check
those specific mistakes before your application accepts the response.

`EvidenceCatalog` captures the sources from a `PreparedTurn`. Every source keeps
its original message index, role, content, and whether it was included in that
prepared request. A source ID such as `m4` means original message index 4. IDs
are meaningful only alongside that prepared turn's `history_id`.

```python
from context_relay import ContextGuard
from context_relay.grounding import EvidenceCatalog

history = [
    {"role": "user", "content": "Use CSV exports."},
    {"role": "assistant", "content": "CSV is the requested format."},
    {"role": "user", "content": "Which export format did I request?"},
]

with ContextGuard() as guard:
    prepared = guard.prepare(history, source_labels=True)
    catalog = EvidenceCatalog(prepared)

    # The model must return this exact shape. This is an example response.
    response = {
        "answer": "CSV",
        "history_id": catalog.history_id,
        "citations": [{"source_id": "m0", "quote": "Use CSV exports."}],
    }
    checked = catalog.check(response)
    assert checked.valid
    print(checked.report)
```

The catalog is a host-side API. `source_labels=True` adds source IDs to included
plain-text messages and the history ID to the prepared task data. Those additions
count toward the context budget. Original archive text remains unchanged, so
generated source labels cannot be cited as original evidence. Your model prompt
must also request the response shape shown above. Do not append unbudgeted labels
or an entire source catalog to an already prepared request. The full catalog can
contain omitted material that the model has not received.

## What passes

A normal answer must have at least one citation. Each citation must refer to a
distinct, included source and contain a nonblank exact substring of its original
content. Capitalization and whitespace must match. Extra response/citation fields
are rejected. Repeating a source ID is rejected even with a different quote.

The exact response `{"answer": "unknown", "history_id": "...", "citations": []}`
is a valid **abstention**, with `answer_status="abstained"`. It is not counted as a
sourced answer. An empty answer is invalid. `unknown` with citations is invalid.

System and developer instructions are excluded from the catalog. User and
assistant messages retain their original roles. Native Ollama tool messages
likewise retain their tool provenance. Tool-call arguments and hidden thinking
are not treated as source content. A call-only assistant message has empty source
content and cannot supply a nonblank quote. An assistant suggestion is never
promoted into a fact by quoting it. Source-labelled model requests currently
support the plain protocol; the read-only catalog also works on native Ollama
prepared history for host-side checks.

## Finding omitted evidence

```python
candidates = catalog.search("CSV export", max_results=3, omitted_only=True)
# Each candidate contains source_id, index, role, content, and included.
```

Search uses case-insensitive lexical word overlap; more shared distinct words
rank first, with newer messages first on ties. It requires a positive match. It
does not use embeddings or an LLM and cannot guarantee semantic relevance.
Search does not write state, restore context, or change any decision.

If a response cites an omitted source with a correct literal quote, its check
fails with `source_omitted`. `recovery_indices` identifies those omitted original
messages. Your agent can ask the guard to restore them and make a **new model
call**, subject to the same budget and whole-turn requirements:

```python
checked = catalog.check(response)
indices = checked.report["recovery_indices"]
if indices:
    refreshed = guard.recover_context(
        prepared, indices, history_id=catalog.history_id
    )
    # Make a new model request with refreshed.messages.
    # Check that new response using EvidenceCatalog(refreshed).
```

This snippet belongs inside the open guard's lifetime. If required original
evidence cannot fit, recovery raises a budget error instead of silently
shortening a quote. Search can also supply candidate indices before an answer
exists. Keep retries bounded in the host and abstain when evidence is missing.

## Exact boundary

`valid=True` establishes **source identity, availability, and a literal quote**.
It does **not** establish that the quote supports the answer, that the source is
true, that an old statement remains current, or that an action is authorized.
For example, an unrelated answer with a real quote can pass these structural
checks. The report explicitly sets `semantic_entailment_checked=False`.

An answer from a different history is rejected before citation lookup, with no
original source text in its error report. History IDs are provenance markers,
not credentials or access controls. The application must keep each session's
catalog private to its own workflow.

Malformed model output returns a failed check report rather than a validation
exception. Invalid host API arguments, such as an empty search query or a
non-prepared catalog constructor argument, raise `ValueError`.
