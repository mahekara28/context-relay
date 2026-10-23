# Relay 0.22.0a1

This release fixes the observed Python and Windows compatibility failures,
addresses two concurrent-startup races, and adds an offline way to evaluate
workflow improvements. It remains a public alpha with no core dependencies.

## What changed

- Ordinary `SimpleNamespace` clients attach correctly on Python 3.10. Clients
  with custom attribute handlers are still rejected before opening storage.
- Concurrent SQLite openers wait within one deadline when enabling WAL. Only
  that startup operation retries; actions and transactions are never replayed.
- Named storage no longer rejects a valid Windows path when a second process
  creates its directory during path resolution. Hashed filenames, file checks
  and symlink rejection remain in place.
- `evaluate_harness()` compares two configurations on complete tasks.
  `improve_harness()` proposes one change from development failures, tests it,
  and returns the original configuration if the candidate fails the gate.
- Model and tool calls have shared budgets, including proposal model calls.
  A failed metered callback prevents subsequent dispatch in that run. A zero
  budget forbids that type of call.
- Copied development inputs cannot count as holdout cases. Selection requires
  complete paired scores, no wrong candidate action or individual completion
  regression, a completion gain, and at least one completed holdout task.
  Development and holdout results are reported separately.

See [the evaluation guide](HARNESS_EVALUATION.md) for the API and its boundaries.
Configuration selection does not change live policies or train a model.

## Validation

Local checks on Windows, using the final source:

| Environment | Result |
| --- | --- |
| Python 3.10.11, core environment | 1,818 tests: 1,753 passed, 65 optional SDK tests skipped |
| Python 3.12.14, pinned SDK environment | 1,818 tests: 1,817 passed, one POSIX-only permission test skipped |
| Dedicated actual-SDK checks | 15 passed; responses use simulated transports |
| New evaluator and export integration tests | 43 passed on both Python versions |
| Clean installed wheel | Offline quickstart and all 43 harness tests passed |
| Repeated simultaneous two-process startup | 40/40 passed on 3.10; 20/20 passed on 3.12 |

The Windows race regression was also checked against the old implementation,
where it fails. Storage checks exercise a real held SQLite reader lock, release
and recovery, deadline exhaustion, zero timeout and closed failed connections.

The offline export example uses predetermined model proposals and actual
temporary files. Both configurations use the same protection contracts. The
baseline completes 2/4 cases and known-value repair completes 4/4, with four
model callbacks per configuration and no wrong writes. This demonstrates the
mechanism; it does not measure model accuracy or superiority over another
framework. The public fixtures are not an unseen empirical holdout.

Check [the release pull request](https://github.com/mahekara28/context-relay/pull/1)
for hosted Linux, Windows and SDK CI results. Local passes are separate from
those results. No hosted model calls or organisational pilot were performed.

From a checkout with its environment installed:

```sh
python -m unittest discover -s tests -v
python examples/harness_improvement.py
python scripts/verify_sdks.py --report local-results/sdk-v022.json
```

Set `PYTHONPATH=src` when running the source suite without installing the package.
The last command requires the pinned optional environment in
`requirements-validation.txt` and a new report path. Model inference is not used.

## Package

Install the new wheel from the cloned repository:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.22.0a1-py3-none-any.whl
python -m context_relay quickstart
```

The wheel is built with Python 3.12.14 and setuptools 84.2.1, using
`SOURCE_DATE_EPOCH=1792713600`. Its SHA-256 is listed in `dist/SHA256SUMS`.
Two independent local builds produced identical wheel bytes, and all 57
packaged library/assets files match the tested source byte for byte.
The original 0.21 wheel and `PUBLIC_MANIFEST.json` remain historical artifacts;
they do not describe the current source. No memory schema migration is added
in 0.22; the evaluation API uses separate, opt-in JSONL files.

## Limits that remain

Correction recognition still misses unfamiliar language. Evaluation quality
depends on the cases, independent checks and trusted sources supplied by the
application. Call budgets cover metered calls, not token prices, SDK-internal
retries, callback deadlines or unwrapped I/O. Python callbacks are trusted code,
not sandboxed. SQLite remains single-host storage, and these startup stress
checks do not establish production capacity or distributed reliability.
