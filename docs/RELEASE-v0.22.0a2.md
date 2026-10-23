# Relay 0.22.0a2

This patch addresses three reproduced failures in instruction memory and action
recovery. It keeps the same local, dependency-free core and public APIs.

## What changed

- A clear format correction followed by a recognised acknowledgment no longer disappears.
  In the reproduced case, `Export as JSON. Thanks, this will help.` replaces the
  old CSV preference, so known-value repair does not restore that obsolete value.
  Unhandled qualifying text disputes the recognised existing target instead of
  leaving an obsolete value available for repair. Quoted examples and conditional
  instructions are not promoted into preferences.
- Scoped instructions are processed in conversation order, including when an
  application first supplies an existing chat. Unsupported task/session lifetimes
  retain their disputes, and one-turn exceptions expire or require clarification
  on continuation consistently across batch and incremental ingestion.
- Each started workflow action retains a compact link to its own checked receipt.
  After a committed tool exception or interrupted destination readback,
  reconciliation can recover the actual arguments, even after restart. Native
  history includes those checked arguments alongside the original proposal.
  Reconciliation never calls the original tool again.

## Upgrade

From the updated repository, in the environment used by your application:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.22.0a2-py3-none-any.whl
python -m context_relay quickstart
```

Restart application processes after installation. Existing database tables and
workflow configuration fingerprints remain compatible; there is no schema
migration or automatic history rewrite.

An older version may already have consumed an instruction without remembering
it. Reopening the same session intentionally does not reprocess that history:
doing so would also risk restoring dismissed or forgotten information. Restate
an affected preference in a new user message, or use the application's explicit
instruction review. This patch does not undo an export made under an old setting.

The new action link applies to actions started by this version. Older
`verification_pending` records can reuse their retained receipt. A pending
operation without a reliable link keeps its arguments unknown; Relay never
borrows another action's receipt or treats the model's original proposal as
executed data. Establish the actual destination outcome before reconciling.

## Validation

Regression cases cover mixed messages, stale repair, batch/history equivalence,
scoped continuation, restarted recovery, concurrent reconciliation and output
limits. The new 17-test regression suite was also run against the original source,
where it reproduced the earlier failures. It includes safety controls that already
passed on that source.

Local Windows checks on the final source:

| Environment | Result |
| --- | --- |
| Python 3.10.11, core environment | 1,835 tests: 1,770 passed, 65 optional SDK skips |
| Python 3.12.14, pinned SDK environment | 1,835 tests: 1,834 passed, one POSIX-only skip |
| Independent history delivery comparison | 192 semantic comparisons matched |
| Clean installed wheel, outside the checkout | Offline quickstart and all 17 new regression tests passed |

The subsequent hosted Windows check for commit `34bc759` exposed an intermittent
oversized-request connection abort in the local workbench server. The Linux and
optional SDK push checks passed. See the [0.22.0a3 fix and validation](RELEASE-v0.22.0a3.md)
for that additional failure; the local results above retain their original scope.

SDK tests use simulated transports; no hosted model calls were made. The full
suite checks source behavior, not general language accuracy. The existing
18-case language diagnostic is unchanged: 1/11 clear changes recognised, 0/2
unclear cases flagged, and 5/5 unchanged controls preserved. This patch does not
claim to resolve those broader interpretation limits.

Two local wheel builds are byte-identical with Python 3.12.14, setuptools 84.2.1
and `SOURCE_DATE_EPOCH=1792713600`. All 57 packaged library/assets files match the
tested source. The wheel SHA-256 is listed in `dist/SHA256SUMS`.

## Remaining boundaries

The parser still uses finite language rules. These fixes do not establish general
language understanding, an organisational pilot or hosted-model acceptance.
External effects and SQLite commits are not one atomic transaction: an abrupt
process or commit failure can still leave an unknown effect. Destination
duplicate-prevention and trusted readback remain application responsibilities.
