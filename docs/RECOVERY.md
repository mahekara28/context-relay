# Let an agent request missing evidence

`agent.run_grounded(...)` is an opt-in loop for plain-text conversations. It can search omitted original messages, restore whole exchanges, make another model request, and check the returned source references. It never runs business tools or turns a model's answer into a saved fact.

The normal connected call returns your provider response unchanged; optional
output checks can reject it or perform explicitly configured retries. Use this
separate method when your application wants a citation-schema answer and can
handle an unresolved outcome. A connection with an enabled `OutputPolicy`
rejects `run_grounded()` before preparation or a provider call: the two response
schemas cannot silently bypass one another. Use a separately configured
connection for grounded answers rather than disabling a policy behind callers.

## Connect and call

```python
import json
from context_relay import connect

# Example decoder for an Ollama /api/chat response. Adapt to your own client.
def decode(response):
    return json.loads(response["message"]["content"])

with connect(existing_model_call) as agent:
    result = agent.run_grounded(
        messages=history,
        decoder=decode,
        max_rounds=2,
        search_limit=2,
        model="your-existing-model",
    )
    print(result.status)
    print(result.answer)
    print(result.report)
```

Your model callable still receives `messages=` and your other keyword arguments. A synchronous decoder converts its completed response to a dictionary. For a callable already returning that dictionary, or a JSON string, omit the decoder. With an async connection use `await agent.run_grounded(...)`; the decoder itself remains synchronous.

An optional memory interpreter runs once during initial preparation, with its
normal review-only rules. Async grounding uses the connection's async preparation
path, so synchronous interpretation runs off the event loop. Cancelling while
interpretation is still running prevents its late memory commit and the answer
provider call; Python cannot stop the underlying callback itself. Recovery
retries reuse the captured originals and do not repeat interpretation.

## What happens inside

1. Relay prepares the request with source labels such as `[Relay source: m1]` and a small response contract. Both are included in its character/token accounting. The original archived messages remain unchanged.
2. The model returns an answer with exact quotes, or requests evidence using search words.
3. A lookup searches only omitted original sources in that conversation. Positive word matches can be restored as mandatory whole turns. The next request includes the latest recorded facts and is budgeted again.
4. Relay checks that source IDs belong to this history, were supplied in the request, and contain the quoted text. An exact quote from an omitted source can also trigger recovery. If a citation is missing, fabricated, or points to an unknown source, Relay can search omitted history using the original user question even when the model did not ask for help. It never uses the fabricated quote as the search query.
5. The loop stops with a result or when its retry limit is reached. A saved-state change during a model call forces a fresh decision within that same limit.

The model-facing data includes the captured `evidence_history_id`. Its allowed response shapes are:

```json
{"history_id": "COPY_CAPTURED_ID", "need_evidence": "Cobalt shipment loading dock"}
```

or:

```json
{"answer": "Dock-7", "history_id": "COPY_CAPTURED_ID", "citations": [{"source_id": "m1", "quote": "The loading dock is Dock-7."}]}
```

The example quote is illustrative: the actual quote must exist in the cited original message. Source labels are generated from original message indices; they do not make the speaker authoritative. System/developer instructions are not citeable sources. Historical assistant suggestions remain assistant suggestions.

The response contract is application-supplied request data, not a replacement for your system instructions. Configure a compatible model prompt/client. A model may ignore it or return malformed JSON; those failures remain visible.

## Interpret the result

| Status | Meaning |
| --- | --- |
| `cited` | The response has valid source IDs and exact available quotes, and captured state was still current at the final check. |
| `abstained` | The model explicitly returned `answer: "unknown"` with no citations. |
| `unresolved` | The output failed checks, the lookup found no omitted match, a history ID was wrong, or the retry limit was exhausted. |
| `budget_exceeded` | A recovery/retry could not fit required evidence or fresh state. No extra over-budget model call was made. |

**`cited` is not a factual-correctness or semantic-support guarantee.** A real quote can be irrelevant, obsolete, or misunderstood. Your domain-specific checks must decide whether the answer actually follows from it. `abstained` is not completion of a task that needed a known value.

`result.response` preserves the final original provider response. `result.prepared` belongs to that exact call. `result.check` holds the literal source-check result when one was made. `result.answer` is a defensive copy of the accepted schema output, or `None` when unresolved. Always inspect `status`; a stale response can pass literal source checks while remaining unresolved.

The report records model-call count, recovery rounds, restored original indices, per-call input size, revisions, timings, and validation issues. Reports retain model-supplied invalid source IDs for audit, but retry feedback contains only fixed issue codes and numeric citation positions. Model-written citation text is not promoted into application instructions.

## Limits and failure handling

- `max_rounds` is the number of additional requests, from 0 through 4. The default allows at most three model calls total. `search_limit` is 1 through 20 source matches per lookup; matching is lexical, not semantic. The automatic fallback from the original question runs at most once per loop. If all ranked lookup candidates cannot fit, Relay tries fewer candidates, preserving complete turns. Explicit cited sources remain mandatory and are not reduced this way.
- The application retains full history. Missing information that never existed there cannot be recovered. Lookups do not access the web or another user's conversation.
- Provider errors and async cancellation propagate without automatic transport retries. Initial preparation overflow raises directly; overflow after a response returns `budget_exceeded`.
- Complete responses are required. Unsupported streams/deferred responses raise `UnsupportedResponseError`; sync streams with a normal `close()` are closed, and the async loop awaits `aclose()` when available. A synchronous path cannot await an async-only stream: the exception exposes `.response` so its owner can release it on the proper event loop.
- The loop currently uses the plain protocol. Native Ollama tool-history preparation is a separate supported path; combining native tool exchanges with this grounded-answer loop requires a future protocol adapter. No external actions are executed by recovery.
- Each retry can use tokens, time, and any normal provider fees. The free local evaluation uses already installed Ollama weights; the library never downloads a model or enables a paid service.

See [correction review](CORRECTIONS.md), [source checks](GROUNDING.md), [native tool histories](PROTOCOLS.md), and [the actual local recovery evaluation](RECOVERY_EVALUATION.md).
