# Checked claims and decisions

`VerificationPolicy` checks structured JSON claims against application-owned evidence and deterministic rules. It can recompute a total, compare a decision with a threshold, require independent sources to agree, and reject expired or untrusted evidence. It does not judge arbitrary reasoning or establish that the source or business rule is true.

```python
import time
from context_relay import VerificationPolicy, OutputPolicy, auto_connect

ledger = {"data": {"quantity": 2, "price": 10, "limit": 15},
          "trusted": True, "independence_key": "billing-system",
          "observed_at": time.time(), "max_age_seconds": 300}
total = {"op": "multiply", "args": [
    {"source": "ledger", "path": "/quantity"},
    {"source": "ledger", "path": "/price"}]}
checks = VerificationPolicy(sources={"ledger": ledger}, claims={
    "total": total,
    "approved": {"op": "lte", "args": [total, {"source": "ledger", "path": "/limit"}]},
})
agent = auto_connect(your_model_call, output=OutputPolicy(
    format="json", verification=checks, max_retries=1))
# The accepted object is {"total": 20, "approved": false}.
# An incorrect total, approval, or extra unverified prose is rejected.
```

`VerificationPolicy.check()` checks a Python object; `check_json()` checks strict JSON with lossless decimal parsing. `readiness()` checks that the referenced evidence and rules are usable. `expected()` computes a known replacement for deterministic repair; it refuses values that cannot be represented faithfully as ordinary JSON data. `to_dict()` / `from_dict()` persist the policy without executable code. Source data and rules are snapshotted.

The model still needs the relevant data and rules in the application request. `describe()` exposes only scope metadata and a fingerprint, so private source fields are not automatically sent to a provider.

An output policy checks the whole object: missing or extra claims fail. Tool policies check only their declared claim arguments; other arguments still need bindings, content checks, or an explicit unchecked declaration. Tool verdicts expose this scope. Source age is checked again immediately before a protected tool executes. If evidence becomes unavailable during a model call, output retries stop: another model call cannot refresh the application's source.

A source needs `trusted=True`, a nonempty `independence_key`, a finite observation time and a positive maximum age. Trust and independence labels come from the application, never the model. Copies of the same origin cannot count as independent confirmation. Disagreeing sources fail rather than picking whichever answer happens to win a vote. Replace the policy when the application obtains a fresh snapshot.

The expression language supports source references, literals, arithmetic, comparisons, boolean operations and conditional decisions. Arithmetic uses bounded exact rational operations and exact decimal comparisons; nonterminating final values require explicit rounding, with ties rounded to even; numeric strings and booleans are not accepted as numbers. No expression executes Python or a source's instructions. Read the API validation errors for malformed rules rather than silently accepting a partial policy.

Remaining boundary: two incorrect sources can agree; a mistaken business rule can be evaluated correctly. Relay cannot discover those errors from the supplied contract alone. Its reports keep `semantic_meaning_checked=False`. Use an authoritative application data source, reviewed rules, and human review for decisions without a deterministic acceptance criterion.

Grounded citation responses separately reject duplicate JSON keys, non-finite values, excessive nesting, and input over 64,000 characters. Citation validity establishes provenance, not factual or reasoning correctness.

Declared top-level string alternatives can be supplied as
`equivalents={"status": [["approved", "APPROVED"]]}`. Only members of the same
explicit group compare equally. Numeric coercion, fuzzy text and global case
folding are not enabled. `expected()` still returns the canonical source value.
