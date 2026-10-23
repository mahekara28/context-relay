# Launch copy for the public alpha

Replace `[repository link]` with your GitHub URL after publishing. These are drafts;
Relay has not posted them anywhere.

## X

I'm building Relay, a free local Python library that helps AI agents check current instructions before acting, carry work across long sessions, and verify results. Public alpha: explicit safeguards, visible limits, reproducible tests. [repository link]

## LinkedIn

An AI agent can remember an instruction and still do the wrong thing.

I've been building Relay, a free local Python library that connects to an existing
agent. It helps check selected settings and tool arguments before an action, and
lets the application verify the actual result afterward.

The new public alpha brings those pieces together in one workflow: explicit review
for critical settings, durable conversation state, bounded history handoffs,
declared tool coverage, and recovery that doesn't automatically repeat an action.

The practical example prepares a release manifest from real local file bytes and
checks the saved output. Tests, a scripted comparison, and a small local-model
evaluation are documented with their limitations.

This is an early library, not a claim that context rot is solved. The strongest
protection needs application-specific rules and review; hosted-provider behavior
and organisational use still need independent validation.

I'm looking for developers with a real workflow where an agent forgets a changed
instruction or reports success without checking its work. I'd like to test whether
Relay helps in that workflow and measure the extra effort it introduces.

[repository link]

## Claims to keep accurate

- Say **public alpha**, and link the current validation report.
- The default wording recognizer still misses unfamiliar instructions. Explicit
  review is a safeguard around that limitation, not improved language accuracy.
- Only configured tools and fields routed through Relay are checked.
- A fresh history segment preserves selected task state and archive access; it
  does not preserve every detail of model reasoning in the next model input.
- Local evaluations are not an organisational pilot or proof of superiority over
  a framework with its own memory and validators.
- Local storage is plaintext. Restrictive permissions are not encryption.

Link the final [validation report](VALIDATION.md) when discussing results. No
claim of zero risk, universal compatibility, or perfect understanding is justified.
