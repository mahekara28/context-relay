# Coding continuation development fixture

This is a disclosed development fixture, not a blind benchmark. The receiver
must change `reporting.py` to satisfy the task below. The included starting
implementation is deliberately stale, and the independent oracle in
`test_expected.py` must fail before the change.

Task: produce a JSON report of net revenue by region from the supplied rows.
Exclude internal accounts, subtract refunds, and reject unsupported formats.
The former CSV preference is superseded by the current JSON requirement.

Run `python evaluations/continuation/run_baseline.py` from the repository root.
The runner checks the oracle in a fresh copied workspace and expects a failure.
It makes no model calls. Later evaluations must keep the receiver model, task
input, workspace and completion oracle constant across comparison arms.
