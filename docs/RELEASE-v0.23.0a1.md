# Relay 0.23.0a1

This alpha improves instruction handling, adds optional local interpretation,
and provides a pilot kit that measures completed work. It does not claim to
solve context rot or understand every instruction.

## What changed

- More bounded English, Hindi and Hinglish requests are recognized, including
  quoted Hindi values. Conflicting values in one message require clarification
  instead of silently choosing the last value.
- One-turn exceptions preserve the standing default. An unspecified report,
  task or project lifetime stays unresolved, even beside a clear default in the
  same message. Reported, quoted and conditional wording cannot become an active
  exception by losing its prefix during parsing.
- `MemoryInterpreter.from_ollama()` connects to an explicitly chosen, installed
  local model. Unmatched eligible wording can produce source-quoted suggestions
  for review. No suggestion is automatically confirmed. Requests have input,
  output and time bounds, reject cloud aliases and redirects, and do not retry
  or download models. See [setup and limits](LOCAL_INTERPRETATION.md).
- The [pilot kit](PILOT_KIT.md) compares Relay with reviewed application memory,
  an ordinary validator and known-value repair. Both write real temporary files;
  the judge reads them back. Interrupted runs retain evidence, missing counters
  stay unknown, and blocking a task never counts as completing it.
- The wording evaluator retains every model proposal. Conflicting suggestions,
  deleted memory and unreadable state cannot disappear into a favorable score.

The core still has no runtime dependencies or extra model calls by default.
Local interpretation is opt-in and costs computation and latency.

## Measured instruction decisions

The same frozen, synthetic 60-case corpus and evaluator were run against
`139d3c4` (0.22.0a3) and this source. The parser implementer did not receive the
corpus. It is agent-authored, not an independent human benchmark. Results are
now exposed regression evidence; they are not a reusable fresh holdout.

| Rules only | 0.22.0a3 | 0.23.0a1 |
| --- | ---: | ---: |
| Correct decisions, development | 25/30 | 27/30 |
| Correct decisions, held-aside cases | 17/30 | 18/30 |
| Correct decisions, all cases | 42/60 | 45/60 |
| Clear changes and one-turn exceptions applied correctly | 21/32 | 21/32 |
| Required clarifications raised correctly | 5/12 | 8/12 |
| Unchanged controls preserved | 16/16 | 16/16 |
| Incorrect standing-memory mutations | 1 | 0 |

The gain here is mainly safer clarification, not broader automatic recognition.
Of the 15 decisions still incorrect, four changes were missed, seven clear
changes needed review, and four required clarifications were missed. A pending
review is safer than a wrong write, but it is not a completed instruction.
Use explicit assurance for fields that must be reviewed before an action.

The [protocol](../evaluations/instructions-v023-protocol.md),
[corpus](../evaluations/instructions-v023.json) and
[selected results with hashes](../evaluations/instructions-v023-results.json)
are included. The runner saves complete results and an incremental journal.

## Local model and complete tasks

A real local Ollama 0.40.1 / `qwen2.5:3b` development probe produced useful
suggestions in **3 of 4** examples. The Hinglish example returned no suggestion.
Requests took **24.438–64.219 seconds** on this machine. Those were direct
interpretation probes, not completed tasks or held-out accuracy estimates.
An earlier exploratory corpus run used changing source and a shorter timeout;
its empty results and timeouts are disclosed in the results summary and are
excluded from a before/after quality comparison. The local model has not been
shown to improve this corpus's completion rate.

The offline pilot runs nine export workflows per variant:

| Pilot measure | Reviewed baseline | Relay |
| --- | ---: | ---: |
| Completed tasks | 8/9 | 8/9 |
| Wrong writes | 0 | 0 |
| Unresolved tasks | 1 | 1 |
| Metered model boundaries / tool writes | 10 / 9 | 10 / 9 |
| Clarification requests | 1 | 4 |
| Scripted review decisions | 10 | 3 |
| Repair attempts | 8 | 9 |

The unresolved case contains an unknown row identity; neither variant guesses
it. Proposals and reviewer replies are fixed examples. The review counts are
scripted decisions, not measured human effort. This is a tie in completion,
not evidence that Relay outperforms a framework. No outside participant or
organisation has run the pilot yet.

## Regression validation

Final local Windows runs on the released source:

| Environment | Result |
| --- | --- |
| Python 3.12.14, pinned optional SDKs | 1,897 tests: 1,896 passed, one POSIX-only skip |
| Python 3.10.11, core environment | 1,897 tests: 1,832 passed, 64 optional SDK skips and one POSIX-only skip |
| Clean installed wheel, outside the checkout | Offline quickstart and 38 instruction, transport and pilot tests passed |

The 52 tests in the four new test modules cover instruction boundaries, local
HTTP behavior, evaluator integrity and completed pilot artifacts. Two historical
tests now explicitly require clarification for unsupported report lifetimes;
their source corpus was preserved. Earlier runs exposed those changed
expectations and two scheduling timeouts. The final full runs passed without
relaxing the concurrency tests or their time limits.

Two wheel builds are byte-identical with Python 3.12.14, setuptools 84.2.1 and
`SOURCE_DATE_EPOCH=1792800000`. All 58 packaged code/assets files match the tested
source. Runtime dependencies remain empty. The wheel hash is recorded in
`dist/SHA256SUMS`; older wheel checksums are unchanged.

These results are local. Inspect the corresponding commit in the
[hosted checks](https://github.com/mahekara28/context-relay/actions/workflows/alpha.yml)
after pushing; no successful hosted run is claimed here.

## Upgrade and use

From the updated repository, in your application's Python environment:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.23.0a1-py3-none-any.whl
python -m context_relay quickstart
python examples/instruction_pilot.py --report results/my-pilot.jsonl
```

Restart application processes. This release adds no database schema migration
and does not reprocess previously consumed history. Restate an affected
instruction in a new user message or use explicit review; upgrading cannot
undo an earlier export. The broader clarification rules may now pause requests
that older versions silently ignored. Handle `MemoryClarificationRequired` in
your application before enabling actions.

Provider compatibility tests still use simulated responses. Hosted-provider
acceptance, arbitrary language understanding and busy distributed deployments
remain unproven. Destination idempotency, source trust and independent result
verification remain application responsibilities.
