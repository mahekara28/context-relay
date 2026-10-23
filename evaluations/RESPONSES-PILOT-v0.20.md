> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Actual local Responses workflow self-pilot

This run uses a real local LLM and actual release manifests, through the installed
Relay 0.20.0a1 wheel. It is a small developer-operated pilot, not a customer or
organisational deployment.

- Frozen requests, model settings and oracle (local-only reference; omitted from public distribution)
- Final report (local-only reference; omitted from public distribution) and all model calls (local-only reference; omitted from public distribution)
- First failed run (local-only reference; omitted from public distribution) and independent cause audit (local-only reference; omitted from public distribution)
- [Runnable pilot](run_responses_pilot_v020.py) and [destination application](../examples/release_desk.py)

## Two local profiles, both retained

| Profile | Output token limit | Correct exports | Clarification | Timeouts | Deterministic repairs |
|---|---:|---:|---:|---:|---:|
| qwen2.5:3b, repeat after adapter fix | 2048 | 3/5 | 1 | 2 | 0 |
| qwen2.5:1.5b, smaller profile | 768 | 5/5 | 1 | 0 | 5 |

Both profiles used the same six requests, source data, instructions and oracle.
Both produced zero wrong files and no repeated tool calls. The smaller profile
is a separate, explicitly selected follow-up after the timeouts; no requests
were removed, rewritten, silently retried or counted as successful when blocked.
It demonstrates safe known-value repair helping complete this workflow. It does
not prove a model ranking, a general speedup or unseen-task accuracy.

The smaller profile retains its frozen plan (local-only reference; omitted from public distribution),
full report (local-only reference; omitted from public distribution),
model-call journal (local-only reference; omitted from public distribution) and
exact runner snapshot (local-only reference; omitted from public distribution).
The three-task profile retains its original runner (local-only reference; omitted from public distribution).
Only a CLI output-limit option was added between these harness versions; core
library bytes stayed unchanged and both runs used the same verified wheel.

The 3B audit (local-only reference; omitted from public distribution) and
smaller-model audit (local-only reference; omitted from public distribution) independently checked
the actual destination files and SQLite records, journals, restart behavior,
runner snapshots and installed wheel bytes. Both passed without inference or
tool execution. The [read-only auditor](audit_responses_pilot_v020.py) is included;
rerunning it against retained evidence requires the original local artifacts and
databases. The public bundle omits SQLite databases; run a fresh pilot to create
your own auditable destination state.

The proposal and repair audit (local-only reference; omitted from public distribution) makes
the compact result concrete: the model omitted `format` in all five proposals
and reused the previous `request_id` once. Relay restored the current effective
format and request ID from recorded values. Artifact names, sizes and hashes
were unchanged. “First-attempt completed” includes this deterministic repair
inside the first workflow call; it does not mean the original proposal was correct.

The detailed table below is the 3B repeat, including its failures.

## What ran

The host read the bytes of the preserved Relay 0.19.0 wheel and source archive
once, deriving their filenames, lengths and SHA256 digests. Five requests asked
the model for a manifest export. Another request deliberately made the format
unclear and should ask for clarification without inference or a write.

Each request used the genuine OpenAI SDK against local Ollama Responses, with
qwen2.5:3b, temperature 0, max_output_tokens 2048 and SDK retries disabled.
No paid or hosted inference ran. Current source evidence was reread before each
action; the destination atomically enforced source version and request identity.
The final exact output oracle parses committed documents and requires matching
materialized file bytes. Completion also requires a successful declared receipt.
Tool callback starts and model calls are journaled independently.

| Request | Expected | Observed | Model calls |
|---|---|---|---:|
| standing-csv | CSV | completed correctly | 1 |
| one-export-json | JSON | completed correctly | 1 |
| standing-restored | CSV | completed correctly | 1 |
| unclear-format | clarification | clarification before model | 0 |
| clarified-json | JSON | unfinished | 1 |
| standing-json | JSON | unfinished | 1 |

The report retains failures, original native responses, checked arguments,
reconciliation if needed, tokens reported by the provider, and measured times.
It verifies the imported module hashes as well as source/harness hashes before
and after. The library was imported from the fresh wheel installation outside
the source checkout, and its bytes match the checkout.

## Interpretation

**3/5 exports completed correctly**, with
**1 expected clarification**, **0 wrong committed
documents**, **0 bad or missing physical files**, and
**0 duplicate callback starts**. The first-attempt correct count is
3; successful host recovery, if any, is reported separately.
The run used 5 actual local model calls and
0 repair attempts in returned results.
The restart reused the original session and saved request result without calling
the model or tool again, as recorded in `restart`.

These six short authored requests cannot establish general model reliability,
context-rot prevention, superiority over framework memory, or deployment scale.
The separate framework comparison uses matched recorded proposals to isolate
action checks; it must not be presented as a live-model control arm for this pilot.
The model/provider times include local hardware and cache effects and are not
a performance claim. Reported token totals may omit failed-call usage; the
`usage_missing_calls` field makes that explicit. Source readers and authenticated
identity are supplied by the host, not established by Relay.

The initial run completed zero export tasks. Its four valid proposals revealed
a concrete adapter mismatch, while an empty assistant response revealed a model
failure. All first-run evidence remains available. After fixing explicit typed
metadata compatibility and a separate Hinglish grammar regression, the same
requests and expected results were used for the final run. This is an exposed
regression exercise, not a fresh unseen benchmark.

## Reproduce on your own files

Install Relay and the optional OpenAI SDK, start local Ollama with an available
tool-capable model, and use new output paths. From the source bundle:

```sh
python evaluations/run_responses_pilot_v020.py --directory ./pilot-new --release your-release --artifact ./your-package.whl --artifact ./your-source.tar.gz --model qwen2.5:1.5b --max-output-tokens 768
```

The runner refuses an existing output directory. It does not download models,
repeat provider calls automatically, or repeat a started effect. Interrupted
work is journaled; the frozen recovery policy reads the destination to reconcile
an unknown action and explicitly abandons a pre-action interruption. Generated
files and local SQLite state belong to the chosen directory. Report JSON alone
is not a signed or authenticated record of another person's machine.

In an existing project, the smaller profile uses the normal public call `workflow.send(text, request_id=stable_id, model="qwen2.5:1.5b", temperature=0, max_output_tokens=768)`. Choose limits for the actual task and backend; this measured profile is not a universal model recommendation. The provider's runtime context window and Relay's serialized character budget are different limits. Configure a matching model/token budget for long histories; preservation of local native items does not certify that a backend accepts all of them.
