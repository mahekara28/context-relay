# Product direction

Relay is a free Python library for existing AI-agent projects. Its purpose is to
reduce specific failures around long conversations: stale instructions, omitted
task context, unchecked actions and false completion. The optional local UI is
for demonstration and inspection; the reusable library is the product.

There are two integration levels. `auto_connect()` adds basic memory to a
supported client. A configured `ResponsesWorkflow` adds a checked action route,
fresh application evidence, explicit instruction review, destination readback
and bounded history continuation. These choices are visible in
`workflow.protection_report()`; installation alone enables none of the action
contracts. See [the protection profile](docs/PROTECTION_PROFILE.md).

Version 0.21.0a1 makes important boundaries explicit:

- Selected preferences require a host/user decision on every new user turn.
  This prevents unreviewed memory from driving a protected action or repair;
  it is a confirmation gate, not a claim of better language understanding.
- Tool coverage must match the application-declared inventory and explicit
  unchecked exceptions. Required receipt checks and host destination readback
  distinguish an action returning from an outcome being established.
- Source verification is supplied by the host and runs alongside freshness
  checks. The destination still owns atomic version checks and idempotency.
- Long workflows can start fresh native segments, with originals archived and
  omissions disclosed. Task completion remains an explicit host decision.
- Local storage exposes its privacy limits, supports reviewed deletion and
  protects newly created files/directories. Data remains plaintext.

The development priority is this bounded end-to-end workflow, with evidence from
completed tasks, wrong effects, false blocks, requests for review and cost.
Passing deterministic checks is not proof of model quality. In the fresh
instruction diagnostic, the default parser found only 1 of 11 clear changes;
the new assurance mode trades automatic progress for explicit review. Small
local experiments and simulated provider responses do not replace an
organisational pilot or a held-out comparison across context lengths.

The library must remain usable without a paid API, hosted database or mandatory
dashboard. It must preserve source provenance, refuse unsupported states and
avoid repeating an action that may already have happened. It cannot certify
arbitrary language, reasoning, source truth or business decisions. Those limits
should remain visible as integrations expand.

[Release scope](docs/RELEASE-v0.21.md) · [Validation](docs/VALIDATION.md) ·
[Alpha setup](docs/ALPHA.md)
