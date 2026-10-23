# Relay 0.20.0a1

This alpha focuses on a complete, bounded action workflow and evidence of its
behavior. The library stays local and dependency-free; applications keep their
existing clients, tools, user interface and permission system.

## Changes

- Standing preferences and explicit current-request exceptions are separate.
  Effective memory is shared by prompts, action checks, output validation and
  known-value repair. Quoted values stay literal. Supported Hinglish temporary
  style instructions preserve the future default. Uncertain lifetimes pause.
- Managed text rollover retains bounded unfinished task requests and updates,
  with explicit host completion or consolidation. Assistant acknowledgements
  do not silently finish tasks. Capacity limits stop rather than evict work.
- `ResponsesWorkflow` combines owner-scoped persistent sessions, supported native
  history, protected Python tools, runtime schemas, fresh evidence and business
  outcome checks. Known repair changes only failed supported fields and rechecks
  the whole proposal before the original function runs.
- Completed request IDs replay saved results. A provider interruption needs
  explicit abandonment; an uncertain effect needs destination reconciliation.
  Neither path silently repeats a tool. Deletion requires an unchanged plan and
  fully terminal work; sibling sessions are isolated.
- The real Release Desk example atomically enforces source version, permission
  and destination request IDs, writes actual manifests, and can recover a failed
  file materialization from the committed document.

## Install and use

Use the wheel in the release ZIP:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.20.0a1-py3-none-any.whl
python -m context_relay quickstart
```

Then follow [basic connection](GETTING_STARTED.md) or [the complete action
workflow](RESPONSES_WORKFLOW.md). The latter needs explicit application tools,
schemas, source readers, identity and permission checks; installation alone
cannot protect arbitrary functions.

## Evidence and boundaries

The [validation record](VALIDATION.md) includes the complete source run,
installed-wheel checks, [strong framework comparison](../evaluations/FRAMEWORK-v0.20.md)
and actual local pilot. Earlier failed candidate runs remain available.
The comparison shows parity with a competent ordinary validator; it does not
establish a quality advantage or reduced development effort.

This release is prepared for public alpha use, not a zero-risk or universal
reliability claim. The new workflow is synchronous and limited to one supported
Responses function call. A model can return no useful action, a trusted source
can be wrong, and configured receipt checks cover only declared business facts.
SQLite is a local store with bounded capacity and waits, not a distributed
service. The host still owns identity, source correctness, external idempotency,
permissions, retention and backup security. There is no organisational pilot
or live hosted-provider inference result in this release.

[Alpha deployment boundaries and CI status](ALPHA.md) explain these limits.
New APIs and storage schemas remain subject to change during alpha. Expansion
should follow actual adopter needs and complete workflow tests, rather than an
untested list of integrations.
