# Context Relay 0.19

Relay is a local Python library you connect to an existing agent. It keeps
supported current instructions available, checks proposed actions against
application-owned rules, and can repair known mistakes before anything runs.
This release improves all five requested areas; it does not turn arbitrary AI
reasoning into verified truth.

## What changed

1. **Check calculations and decisions.** `VerificationPolicy` recomputes declared
   claims, checks thresholds and independent-source agreement, and rejects
   untrusted, expired or conflicting evidence. Use it with `OutputPolicy` or
   tool contracts. [Evidence and rules](VERIFICATION.md).
2. **Connect a tool inventory once.** `agent.protect_tools(...)` registers functions
   and their checks together, rejects uncovered parameters during setup, and
   dispatches one proposal against the state captured before generation.
   [Action setup](ACTION_HARNESS.md).
3. **Finish more repairable tasks.** `repair="known"` can restore recorded settings,
   computed claim values and protected fields in uniquely identified rows.
   Explicit string alternatives avoid unnecessary rejection. Repairs cannot
   change unrelated arguments and must pass every check again. Failure receipts
   also survive interrupted tools and broken exception formatting.
   [Repair boundaries](REPAIR_SAFETY.md).
4. **Remember after restart.** `auto_connect(client, conversation="ticket-42")`
   saves state and automatically finds the current session when reopened.
   `resume=True` reattaches identical tool contracts. Unnamed connections remain
   temporary. [Storage, history and deletion](NAMED_MEMORY.md).
5. **Handle more provider actions and local concurrency.** OpenAI Chat/Responses,
   Anthropic and Ollama have explicit complete-action decoders. Native OpenAI
   and Anthropic harnesses preserve full responses and opaque history.
   `busy_timeout_ms` bounds individual storage waits; `StoreBusy` distinguishes
   failures before a transaction from failures after work may have begun.
   [Compatibility and deployment handling](PROVIDER_ACTIONS.md).

## Install locally

Extract the release ZIP and run from its `context-relay` folder:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.19.0-py3-none-any.whl
python -m context_relay quickstart
```

The core has zero runtime dependencies. No account, subscription, model download
or hosted service is needed. A connected external model still has its own
running costs. This release is not published on PyPI.

## Try the complete workflow

```sh
python examples/refund_desk.py --output refund-desk-report.json
```

The small offline Refund Desk writes actual temporary receipts from scripted
proposals. It tests correct work, wrong or missing amounts, changed export
settings, source problems, stale permissions, malicious repairs, interrupted
execution and restart. It grades the resulting receipt against a separate
literal answer, so a rejected task is not reported as completed. No real refunds
or AI calls are made. A deliberately wrong-source control separately demonstrates
what the configured checks cannot establish.

## Verified results

- **1543 source tests passed**, zero skips, with unchanged source and tests.
- **22 installed SDK checks passed** using simulated responses; core and
  the refund app also ran without optional SDKs.
- **14 refund scenarios behaved as expected**: 5 tasks completed, 7 stopped,
  1 requested clarification and 1 stopped after an interrupted effect.
  The separate wrong-source control exposed one incorrect result as intended.
- **624 local concurrent action attempts** produced 208 correct effects,
  blocked 208 wrong proposals and blocked 208 replays.

No paid calls or hosted inference were used. See [the validation record](VALIDATION.md)
for methods, individual reports and limits.

## Boundaries that still matter

- The application must supply trustworthy data and reviewed rules. Two wrong
  sources can agree, and a bad business rule can be evaluated correctly.
  Arbitrary prose, reasoning and unspecified business decisions are not verified.
- Only the supplied dispatcher is protected. Relay cannot infer your company's
  permissions or prevent arbitrary direct Python calls outside its boundary.
- Repair needs a defensible replacement. When evidence is insufficient, stopping
  or asking for clarification is the correct outcome, not successful completion.
- Native history remains application-owned. Streams, multiple simultaneous tools
  and unsupported provider action formats are refused by this action harness.
- SQLite and these load tests concern one host. Production deployment and an
  organisational pilot remain separate validation work. External effects need
  destination-side idempotency if the process dies or the database commit fails.

See [validation](VALIDATION.md) for recorded results and retained failures.
