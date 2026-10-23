# Relay 0.21.0a1

This alpha makes the remaining uncertainty explicit: require a decision before
important remembered preferences drive an action, check the declared execution
route, verify application evidence and read back outcomes. The main integration
is a reusable Python library; the local example and UI are optional.

## Changes

- **Instruction assurance:** `MemoryPolicy(assurance_fields=(...))` masks
  unreviewed values in the effective execution view. Each new user turn requires
  an explicit decision for those fields. A workflow returns `review_required`;
  the host uses `review_plan()`, `resolve_review()` and `resume_review()` to
  continue the same request. A turn-scoped decision preserves the durable
  default. Review cannot grant permissions or approve a stale plan.
- **Declared coverage:** `CoveragePolicy` requires an exact tool inventory,
  explicit unchecked exceptions and receipt checks. `protection_report()`
  shows the configured boundary. Direct calls to original functions remain
  outside it.
- **Source and outcome checks:** `EvidenceSource(verify=..., verifier_id=...)`
  adds a host verification step to current-source reads. `OutcomeVerifier`
  reads the destination separately from the supplied receipt. Unavailable
  readback keeps the action pending; no effect is automatically repeated.
- **Long-workflow continuation:** `WorkflowHistoryPolicy` archives complete
  native segments and starts a fresh context with exact bounded task requests
  and receipt references. Omissions are reported. The host can retrieve the
  originals, consolidate the task or explicitly finish it.
- **Storage and distribution:** new storage has restricted creation
  permissions; SQLite secure deletion, explicit checkpointing and reviewed
  retention expose their limits. A public-release allowlist excludes local raw
  runs, journals, destinations and databases while preserving originals locally.

Follow [the protection profile](PROTECTION_PROFILE.md) for the combined setup,
[instruction assurance](INSTRUCTION_ASSURANCE.md) for review semantics,
[continuation](WORKFLOW_CONTINUATION.md) for archival boundaries and
[storage privacy](STORAGE_PRIVACY.md) for plaintext/deletion limits.

## What has not been solved

The default parser recognised only 1 of 11 clear changes in the fresh synthetic
instruction diagnostic frozen before this work. Assurance asks for review on
all 18 cases, including five unchanged controls. This is a deliberate safety
tradeoff, not improved NLP or a population-level accuracy estimate. Default
memory behavior is preserved when assurance is not selected.

Relay does not verify arbitrary reasoning or business truth. Source timestamps,
verifier identities and callback implementations are application assertions.
Two incorrect sources or verifiers can agree. The destination still needs
atomic conditional writes and idempotency. A fresh native segment does not
preserve the model's earlier reasoning in its active context.

The workflow remains synchronous with one supported function call per response.
Storage is bounded, local and plaintext; logical deletion is not guaranteed
physical erasure. No hosted-provider validation, organisational pilot, general
context-rot reduction or distributed-capacity result is claimed here.

## Upgrade and validation

Install the supplied `context_relay_local-0.21.0a1-py3-none-any.whl` with the
[alpha installation instructions](ALPHA.md). Core runtime dependencies remain
empty. Preserve the same configuration when reopening an existing workflow;
new assurance, coverage, verifier and history options affect its fingerprint.
Use a separately identified workflow for a changed profile instead of silently
changing a populated one. Keep destination idempotency across that boundary.

Assurance introduces memory schema 4. Older versions cannot read that state;
keep workers on a compatible version and do not assume downgrade is safe.
Existing file/directory permissions are not automatically rewritten. Back up
local state before an alpha upgrade and review the storage guide.

Source and clean public-copy suites each ran 1,768 checks: 1,767 passed and one
POSIX-only permission check skipped on Windows. The installed wheel passed 145
checks with the same one skip, plus isolated checks without optional SDKs. The
96-operation scripted continuation profile completed all 72 required files;
the installed local-model profile completed 8/8 exports with eight known-value
repairs and no repeated tool starts. These are selected development workloads,
not an organisational reliability claim. [VALIDATION.md](VALIDATION.md) records
the methods, review/reconciliation effort, baseline parity, known failures and
distinction between simulated SDK transports and actual local inference.

The public distribution contains labelled summaries and exposed synthetic
fixtures. Raw local evidence is deliberately omitted; modified Markdown copies
are marked as derivatives. See [publishing](PUBLISHING.md).
