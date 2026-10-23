# One complete Responses workflow

`ResponsesWorkflow` connects one synchronous OpenAI Responses client to durable native history, user preferences, protected Python tools, fresh application evidence and checked business receipts. The same integration can use a local Ollama server's supported Responses endpoint. OpenAI SDK tests in this repository use simulated HTTP responses; the separately recorded local pilot uses actual local inference.

Start with [the runnable Release Desk example](../examples/release_desk.py). It reads approved release metadata, asks the model for one export action, checks it, writes a real JSON or CSV document and records the destination receipt. Its SQLite destination enforces source-version preconditions and request-ID uniqueness atomically. The files are reproducible views of committed documents, so a failed file write can be reconciled without repeating the action.

```python
from context_relay import EvidenceSource, Principal, SessionManager

with SessionManager(".relay/owners", authorize=your_permission_check) as manager:
    with manager.connect(
        existing_openai_client,
        principal=Principal(authenticated_tenant, authenticated_user),
        conversation="release-export",
        api="responses", native=True,
    ) as session:
        workflow = session.responses_workflow(
            {"export_manifest": export_manifest},
            contracts=tool_contracts,
            schemas=tool_schemas,
            evidence=EvidenceSource(read_current_source, max_age_seconds=30),
            instructions="Export the approved manifest using current workflow data.",
        )
        result = workflow.send("For this export only, use JSON.",
                               request_id=your_stable_request_id, model=your_model)
        print(result.completed, result.business_status)
```

The host supplies authenticated identity, a real permission callback, tools, schemas and checks. The callback must return exactly `True` for the appropriate `connect`, `manage`, `call`, `read` and `execute` operations. Scoped calls check permission before inference, before the original tool and before returning results. Relay does not implement login or sandbox arbitrary Python. Keep the manager, destination and filesystem inside the trusted application boundary.

Each contract declares argument checks and an `OutcomePolicy(required={"/ok": True})` or stronger domain-specific receipt conditions. Provider strict mode is not trusted as a local validator: a bounded schema subset is enforced on the final repaired and normalized arguments before the original tool runs. The subset supports primitive/null types, strict objects, arrays, enum/const, simple numeric and length bounds. Unsupported keywords such as `$ref`, `anyOf` and regular-expression `pattern` fail at setup. This is not a complete JSON Schema implementation.

`read_current_source()` returns exactly `{"version": "revision-id", "facts": {...}, "observed_at": unix_seconds}`. Facts are trusted application data, not text extracted from model output. Relay refreshes them before inference, then reads again immediately before dispatch. A changed version **or** changed facts stops the old proposal, even inside its allowed age. Its fact-key schema cannot silently lose a permission field on a later request. `workflow_source_version`, `workflow_request_id` and `workflow_evidence_keys` are reserved metadata; bind tool parameters to the first two for destination version checks and idempotency.

A source read cannot close the final race with a separate service. The destination must atomically enforce the version precondition and request ID, as Release Desk does. A reader can also be wrong or return stale data with a fresh timestamp; Relay cannot establish external truth from the reader's assertion.

## Optional protection and review

Version 0.21 accepts `memory=MemoryPolicy(...)`,
`coverage=CoveragePolicy(...)`, `outcome_verifier=OutcomeVerifier(...)` and
`history_policy=WorkflowHistoryPolicy(...)` when creating the workflow.
`EvidenceSource` also accepts `verify=callback, verifier_id="stable-host-id"`.
The [combined profile](PROTECTION_PROFILE.md) shows real source-file and
destination-file checks. `workflow.protection_report()` describes what is
configured; it does not certify source truth or discover unregistered calls.

For selected high-impact preferences, use
`memory=MemoryPolicy(assurance_fields=("export_format",))`. Each new user turn
returns `status="review_required"` before inference or an effect until the host
explicitly reviews all configured fields:

```python
plan = workflow.review_plan()
# Obtain the user's/host's explicit choice first; do not auto-accept old memory.
workflow.resolve_review(plan, decisions={
    "export_format": {"value": "JSON", "scope": "turn"},
})
result = workflow.resume_review(plan["request_id"])
```

Use `scope="durable"` for a standing preference. The saved request remains
`review_pending`; `resolve_review` records a decision and `resume_review`
continues its original provider options without appending new user text. Do
not call `send()` with a new ID to resume this review: that creates a new turn
and requires review again. Changed state invalidates stale plans. Review cannot
grant permission. It deliberately also stops on harmless messages; the parser's
language coverage has not become universal. See [instruction assurance](INSTRUCTION_ASSURANCE.md).

An `OutcomeVerifier` reads the actual destination using the request ID, supplied
receipt and checked arguments. It must not perform the action again. Only
exactly `True` accepts the readback; a false result prevents completion, and an
unavailable readback returns `verification_pending` while retaining
`action_pending` for explicit reconciliation. Both source and destination
verifiers are application code whose identity and independence Relay cannot
authenticate.

## Results and recovery

`executed` describes callback execution. `completed` means the declared receipt conditions passed and any configured destination verifier accepted. Business status is `succeeded`, `not_satisfied`, `unknown`, `unchecked` or `not_started`; a returned `{"ok": false}` cannot become a completed task. The legacy low-level receipt `status="succeeded"` still means the callback returned; use its `outcome` or the workflow's `completed` for business status. Missing result fields and exceptions are never proof of success.

A completed workflow record can contain a declined or unknown business result: record phase and business completion are deliberately separate. Sending the same request ID with the same text/options returns its saved result without another model or tool call. Reusing the ID for different input is an error. Business failures never trigger an automatic second effect.

A model/network interruption retains `provider_pending`; explicitly inspect and abandon that request before starting new work. An exception after a tool starts retains `action_pending`. Look up the request at the destination and call `workflow.reconcile(request_id, result=destination_receipt, expected_phase="action_pending")`. Reconciliation records a host-obtained result and never calls the original tool. It does not authenticate an arbitrary receipt: the host must match it to the destination request. Do not invent a successful receipt to clear a pending operation.

Only explicit new user text enters automatic memory. Prior native reasoning, signatures and function items remain original data. Corrected arguments appear in the subsequent function result alongside the original proposal. A supported native function result is linked to its original call ID. Unknown/mixed output formats fail before effects. The tested Ollama Responses envelope can include typed `store`, `presence_penalty` and `frequency_penalty` metadata; these are retained and do not change tool permissions. The local pilot uses normal OpenAI SDK parsing. OpenAI 3.36.0's optional strict response validation rejects Ollama 0.40.1's omitted `cache_write_tokens` usage field; Relay does not invent that field or disable a caller's strict setting. A changed resume configuration, missing authorization callback, competing request or stale captured revision cannot silently acquire permission.

## Bounds

This integration accepts one completed client function call per explicit request; no automatic agent loop, streaming, asynchronous effects, server tools, arbitrary provider compaction output or background Responses jobs. Without a history policy it preserves the supported native history in the active segment and stops at configured limits. Defaults are 2,000,000 serialized history characters and 256 requests; the model input also has the connection's smaller `max_chars` limit.

Opt-in `WorkflowHistoryPolicy` can archive a complete segment and start a fresh
one, carrying bounded exact task requests and receipt references. It discloses
that old reasoning and result details are omitted from the new input; retrieve
needed originals explicitly. This is not provider-native compaction or preserved
reasoning continuity. `update_task()` and `finish_task()` are explicit host
operations, and archive/user-history/request limits still stop work. See
[long-workflow continuation](WORKFLOW_CONTINUATION.md). Keep identical policies,
verifier IDs and contracts when reopening a saved workflow; they affect its
configuration fingerprint.

`plan = workflow.deletion_plan()` previews the session revision, stored request counts and fingerprint. After reviewing it, `workflow.delete_history(expected=plan)` deletes that workflow's local session data only when the plan is unchanged and every request is terminal. Pending effects must be reconciled first. Named reconnection then creates a new session. Old handles fail.

Storage is local SQLite, with plaintext history/receipts and process-local manager metrics. New-file creation protections, SQLite secure deletion and explicit WAL checkpointing reduce some ordinary exposure; they do not provide encryption or guaranteed erasure of provider copies, backups, free pages or WAL remnants. External effects cannot be atomically committed with Relay's receipt. The contract checks specific failures; it does not guarantee arbitrary reasoning, universal language understanding, unlimited scaling or zero risk. See [storage privacy](STORAGE_PRIVACY.md).
