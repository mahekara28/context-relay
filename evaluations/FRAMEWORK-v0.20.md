> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Strong framework comparison

The two arms reached **parity on the frozen action-boundary cases**. Relay did
not outperform the ordinary validator. This comparison supports a narrower
claim: Relay can package checks and known-value repair that an application can
also implement using framework state and ordinary validation.

Both arms use the installed **LangGraph 1.2.16**, `StateGraph`, and
`InMemorySaver`. They use the same `ReleaseDesk` SQLite destination and real
JSON/CSV files, source freshness checks, permission checks, request replay
handling, destination idempotency, and explicit reconciliation after an
interrupted write. The ordinary validator has no Relay imports or validation
helpers. The Relay arm uses public `ContextGuard`, `ToolRegistry`, captured
turns, fact bindings, `OutcomePolicy`, and `repair="known"`.

The fixture was frozen before implementing the runner:

- [Frozen cases and independent output oracle](framework-v020-fixtures.json)
- SHA256: `855ecf9d56ea3aa57cda08bf0aacdf05f29161c714457291b44f89dad41ee463`
- [Runner](compare_framework_v020.py) and [ordinary validator](framework_baseline_v020.py)
- Final retained report after compatibility fixes (local-only reference; omitted from public distribution)

Earlier first/final reports remain retained as results from their recorded
source revisions. The `final-r2` report is the comparison against the final
`0.20.0a1` source, with matching before/after source fingerprints.

Each arm receives identical recorded proposals and prepared input data,
including standing preferences, current-turn overrides, source version,
approved artifacts, and a repair budget of one. Input and proposal hashes are
checked against the frozen fixture and across arms. Instruction events are
already normalized by the host in both arms. This deliberately isolates action
protection; it does **not** compare natural-language extraction, context-window
management, or complete native Responses integration.

## Results

| Metric, per arm | Ordinary validator | Relay |
|---|---:|---:|
| New requests / total operations | 13 / 16 | 13 / 16 |
| Correctly completed requests | 8 | 8 |
| Expected blocks | 3 | 3 |
| Clarification requests | 1 | 1 |
| Known-value repairs | 2 | 2 |
| Wrong effects in supported cases | 0 | 0 |
| Repair-induced wrong effects | 0 | 0 |
| Duplicate writes or destination calls | 0 | 0 |
| Recorded proposal calls | 12 | 12 |
| Actual model / network calls | 0 / 0 | 0 / 0 |
| Wrong effects from deliberately wrong trusted source | **1** | **1** |

There are nine selected cases: durable changes, temporary format and reversion,
corrupted immutable artifact fields, stale source data, revoked permission,
ambiguity followed by clarification, an unsupported extra argument, interruption
after a committed write followed by replay/reconciliation, and the wrong-source
control. The three expected blocks and the initial clarification are not counted
as completed work. The interrupted write counts as completed only after explicit
destination reconciliation. Replaying an existing request adds no proposal or
destination call.

The negative control supplies the same incorrect byte count as trusted source
data to both arms. Both report success and export that incorrect count. An
independent literal source oracle counts each export as wrong. Thus each arm
reports nine successful receipts but only eight correct completed requests.
The negative control remains included in the total wrong-effect count.

## How to reproduce

From the repository root, use Python with Relay and LangGraph installed. Set
`PYTHONPATH=src` when testing the local source. Output and data paths must be new:

```powershell
$env:PYTHONPATH = 'src'
python evaluations/compare_framework_v020.py `
  --data-dir ./comparison-data-new `
  --output ./comparison-report-new.json
python -m unittest discover -s tests -p test_framework_comparison_v020.py -v
```

The retained run used the existing local `work/sdk-venv-verified` environment.
The report records installed package versions, source hashes before and after
execution, every request/operation, checkpoint counts, actual destination calls,
file readbacks, and p50/p95/p99 wall-clock latency. The validator recomputes
counts and checks real files against their hashes and the separate output oracle.
It rejects missing cases, requests, operations or effects, edited proposals,
inconsistent counts, and a hidden wrong-source failure.

## Limits

- This is a small development comparison with curated proposals, not unseen LLM
  behavior, an organisational pilot, or a measured reduction in maintenance.
- Both arms receive normalized scope information. Language understanding could
  still fail before either action check gets the correct preference.
- `InMemorySaver` really checkpoints execution, but its state is process-local.
  This run does not establish restart, distributed, or production durability.
- The application supplies freshness checks, replay bookkeeping, reconciliation,
  and destination transactions to both arms. Their success is not credited as a
  unique Relay feature.
- One short run executes the ordinary-validator arm first. Latency includes local
  graph, SQLite and filesystem overhead, excludes LLM/network time, and is not a
  statistically controlled performance claim.
- The artifact metadata is a frozen test record; no actual package binary is
  audited. The destination writes real manifest files, and the grader checks
  their content independently of the runtime's trusted source.
- File verification uses the absolute paths recorded by the run. Keep the
  companion `framework-comparison-v020-final-artifacts-r2` directory; copying only
  the JSON report to another machine does not reproduce or revalidate the run.
- A correct receipt only means the declared receipt predicate passed. The
  wrong-source control demonstrates that neither setup establishes source truth.
