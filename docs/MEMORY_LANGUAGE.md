> Public derivative: omitted local evidence links are labelled below. The original document and raw evidence remain unchanged in the private working copy. This copy does not independently reproduce historical results.

# Everyday wording supported by automatic memory

## Version 0.18: correction recognition and clarification

The free local parser now handles more indirect English instructions and bounded
Hindi/Hinglish, Spanish and French instructions for export/output format, reply
language and reply style. This is finite grammar support, not general translation.

**Behavior change:** connected text calls with automatic memory now raise
`MemoryClarificationRequired` when an existing preference is disputed, before the
next model call. Handle the exception and show its `prompt`; provide a new full
instruction to resolve the setting. Managed conversations store the local
question and accept the next answer without retrying the blocked request.
[Clarification and migration guide](INSTRUCTION_CLARIFICATION.md).


Automatic memory now recognises several common instructions without a developer
defining fields first. Enable `MemoryPolicy()` on a connection. Recognition runs
locally, uses no extra dependencies, and makes no model or network calls.

```python
from context_relay import MemoryPolicy, connect

with connect(call_model, memory=MemoryPolicy()) as agent:
    response = agent(messages=[
        {"role": "user", "content": "Thanks! Please keep responses brief. Reply in Hindi."},
    ])
    print(agent.memory.entries())
```

`call_model` is the application's existing compatible model-calling function.
The new grammar changes what can be recognised; it does not expand the provider
protocols accepted by that function.

## Short instructions and stable memory keys

| User statement | Memory key | Stored value |
| --- | --- | --- |
| `I prefer brief answers.` | `response_style` | `brief` |
| `Keep responses short.` | `response_style` | `short` |
| `Reply in Hindi.` | `response_language` | `Hindi` |
| `Respond in English.` | `response_language` | `English` |
| `Export as CSV.` | `export_format` | `CSV` |
| `Actually, export as JSON.` | `export_format` | `JSON` |
| `I want brief answers.` | `response_style` | `brief` |
| `I need responses to be short.` | `response_style` | `short` |
| `Make your replies concise.` | `response_style` | `concise` |
| `I want answers in Hindi.` | `response_language` | `Hindi` |
| `Switch the response language to French.` | `response_language` | `French` |
| `Export the results as XML.` | `export_format` | `XML` |
| `I want the output in JSON.` | `export_format` by default; see output aliases below | `JSON` |
| `Give me brief answers.` | `response_style` | `brief` |
| `Answer succinctly.` | `response_style` | `concise` |
| `Keep your replies to the point.` | `response_style` | `concise` |
| `Use Hindi for your replies.` | `response_language` | `Hindi` |
| `Send the results as JSON.` | Existing sole output alias; otherwise `export_format` | `JSON` |

These are specific shortcuts to named fields, not guesses based on whichever
memory happens to exist. `Export as JSON` updates `export_format` even when the
conversation also contains a different `log_format` preference.

Recognised values, case-insensitively:

- Style: `short`, `brief`, `concise`, `detailed`. `answer(s)`, `response(s)` and
  `reply/replies` work with `I want/need/prefer ...`, `Keep ...` and `Make ...`.
  Forms such as `I need a concise response` and `I want the replies to be brief`
  are also supported.
- Language: Arabic, Bengali, Chinese, Dutch, English, French, German, Hindi,
  Italian, Japanese, Korean, Marathi, Portuguese, Russian, Spanish, Tamil,
  Telugu, Turkish and Urdu.
- Export format: CSV, JSON, JSONL, TSV, XML, YAML, TOML, Markdown, HTML, PDF, TXT
  and XLSX.

In v0.14, finite format/language/style values use canonical spelling: `json`
becomes `JSON`, `hindi` becomes `Hindi`, and `Brief` becomes `brief`. Only case
and surrounding whitespace are normalized, and only for the named finite
preference keys. Custom keys and unknown values keep their exact spelling.
A small explicit grammar mapping also handles
style phrases: `briefly` becomes `brief`; `concisely`, `succinctly` and
`to the point`/`to-the-point` become `concise`; `in detail` becomes `detailed`.
Thus `Reply in detail` now records a style, never a language. The original quote
and offsets remain unchanged. This is not general synonym inference.

The small vocabulary prevents `Export as needed` from becoming a format.
Unrecognised instructions about `answers`, `responses` or `replies`, such as
`Use Python for your replies`, no longer fall through into invented generic
fields such as `your_replies`. A fully named unfamiliar literal such as
`Set the response language to Esperanto.` or `Set the export format to Parquet.`
now creates `unrecognized_value_needs_review`; confirm the actual supported
choice before it becomes active. Generic prose such as `Set the export format
to the same as last time` cannot bypass the finite preference vocabulary.
It asks for clarification when that setting is already known. Custom fields
remain schema-free; these limits are specific to standard preference keys.

## Polite messages and clear corrections

Standalone `Thanks`, `Thank you`, `Okay`, `OK` and `Please`, with an optional
period or exclamation mark, can appear beside supported statements. `Okay,`,
`Thanks,` and `Thank you,` can also precede a statement, as can `Please` and
`Actually,`. A trailing `, please` is accepted. Each stored source quote remains
an exact slice of the original message, including its original punctuation.

Examples:

```text
Thanks! Please reply in Hindi. Export as CSV, please.
Okay, actually, export as JSON.
From now on, please answer briefly.
I need you to reply in Hindi.
Send the output as JSON going forward.
```

`I want you to` and `I need you to` can wrap an otherwise supported request.
`From now on` and `Going forward` can precede it or appear at the end. These are
fixed request forms; Relay does not remove arbitrary surrounding prose.

In v0.13, `I'd like`, `I would like`, `I'd prefer`, `let's`/`let us`, and direct
`Can/Could/Would/Will you ...?` requests can wrap supported settings. `On second
thought`, `I changed my mind:` and `Scratch that—` are recognised correction
prefixes. Straight and curly apostrophes work in the supported contractions;
actual quotation marks, code and reported speech remain excluded. General
questions such as `Why did you change exports?` are not requests to update memory.

v0.14 also composes `Can/Could we`, `I've changed my mind`, `from here on`,
`future/later` subject qualifiers and common named-setting clause orders:

```text
Could we have all future exports in JSON, please?
For exports, let's use JSON from now on.
I've changed my mind: export as JSON.
Would you switch the export format over to JSON?
Please make Hindi our default response language.
Let's move the response language from English to Hindi.
Keep the export format as JSON; make the response language Hindi.
```

Named language, style and format settings share a bounded assignment grammar:

```text
Could you use Hindi for your replies from now on?
I'd like the export as JSON.
For future exports, make YAML the default.
The response language should be Marathi.
Response language = Japanese.
Please replace CSV with JSON as the export format.
Use JSON for exports, not CSV.
```

Replacement and explicit contrast forms check the named old value, just like
`instead of` below. They do not treat a bare negative instruction as a new value.
Unrecognised negation-plus-replacement wording can still be skipped. The grammar
does not infer that arbitrary synonyms, relative descriptions or long explanations
mean one of its setting values.

Complete clauses can be joined by `and` or semicolons, with exact source offsets:

```text
Keep JSON for exports; leave the log format as CSV and reply in Hindi.
```

Every clause must be recognised before any deterministic change from that message
is applied. `Export as JSON and maybe reply in Hindi` therefore changes neither
field. Existing review policy, old-value checks and declaration-conflict handling
still apply to each accepted clause. Temporary scope such as `for this response`,
`for today`, `for now` or `this time` is excluded from durable memory.

## Named corrections across a complete message

v0.15 adds bounded forms for known aliases, reordered requests, and explanations
linked to a named setting. For example, after the matching old values are stored:

```text
My export-format preference has changed from JSON to CSV.
I want English to replace Hindi as the language of your replies to me from now on.
Please correct my saved response style: brief replaces detailed.
```

Words such as `saved`, `standing` and `usual` can describe a recognised setting;
`export-format`, `reply language` and `language of your replies` have specific
supported mappings. This does not infer arbitrary aliases for custom fields.

A reference can also be grounded explicitly inside the same original user
message:

```text
You have CSV recorded as my export format; please correct that to JSON.
I no longer want CSV as my output format. Please remember JSON as the replacement.
My export format should change to JSON as a permanent preference, while my output format stays CSV.
```

The first two messages name the field before referring to `that` or `the
replacement`. The last updates only `export_format`; its unchanged
`output_format` is not a second correction. Supported explanations of an old
value or an unchanged entry are part of the request, not new fields such as
`existing_entry`. Returning to an earlier value is a new correction when it
differs from the current value; repeating the current value is a reaffirmation.

A supporting clause does not turn a plain conflicting declaration into an
explicit correction: the existing declaration-conflict review rule still
applies. `My old export format is JSON.` and `JSON is my earlier export format.`
describe historical values; neither selects a new current export preference.
If a reference leaves two
compatible named settings in scope, Relay requires clarification rather than
choosing one. Only an unambiguous reference to an explicitly named setting can
support an automatic change.

**Every clause must be understood before automatic changes are applied.** A
recognised correction plus an unsupported explanation does not authorize a
partial update. A supported explanation must be linked to the named setting;
arbitrary surrounding prose, quoted instructions and hypotheticals remain
excluded. These local references do not search previous turns for an antecedent.
The separate [recent-turn shorthand](#output-aliases-and-short-references)
continues to require review.

Custom fields retain their literal identity. If ordinary setters have already
created both `saved_delivery_window` and `delivery_window`, then
`Change the saved delivery window from morning to evening.` names the exact
existing `saved_delivery_window`; Relay does not strip `saved` and silently
redirect it to `delivery_window`. The same identity rule applies to an existing
custom name with an `old_` prefix. A custom replacement such as `evening` is
stored as written. Relative descriptions, unspecified alternatives and outside
references are not semantic guesses about custom values.

Composed custom corrections have a narrower literal grammar than ordinary
setters. For example, with a known `project_name`, `Set the project name to
Born Again. Please remember that.` requires clarification rather than shortening
the value to `Born`; the composed value `Selected By Default` likewise requires
clarification. Established `from now on` and `going forward` suffixes still act
as request-scope markers, so a generic setter ending in those words does not
guarantee they will be retained as part of a literal custom value. Support for a
custom field does not mean every phrasing of its value can be applied automatically.

A setting-status explanation does not create a separate preference. For example,
with only `export_format` stored, `My saved export format is wrong.` disputes that
setting; it
does not create a `my_saved_export_format` field with the value `wrong`.

## Clarification when the setting is known but the new value is not

An eligible direct request can now mark an existing setting uncertain rather
than silently leaving its old value undisputed:

```text
User: Export as CSV.
User: Please make future exports much easier to read.
```

Relay retains `export_format=CSV` as historical recall, marks it `disputed=true`
in the snapshot, and adds a pending record with `value=null`,
`reason="unparsed_correction"`, the captured old value/basis, and the exact user
message as evidence. `source.method` is `uncertain`. It has not guessed JSON or
another replacement. The existing entry remains visible so the application can
explain what needs clarification. An action check bound to that memory key blocks
while it is disputed; applications that bypass those checks are not protected.

Inspect `memory.pending()` even when the processing report has no active-value
`changes`. `memory.confirm()` explicitly rejects clarification-only records.
Ask for a clear instruction, append the actual reply to the original conversation,
or explicitly `dismiss()` a mistaken uncertainty flag. In automatic mode a later
clear instruction, including a reaffirmation such as `Export as CSV`, resolves
the earlier flag. In review mode, the new concrete proposal captures earlier
uncertainty IDs; confirming it clears only those captured flags after checking
their basis. A later uncertainty remains unresolved. Undo, replay, persistence,
rollover, audit events and the existing entry/character budgets still apply.

For example, these messages identify a correction without selecting a new value:

```text
My recorded response language needs correcting. I haven't decided what to replace it with.
My export format is fine. My output format needs replacing as a standing preference, but I have not selected the new format.
```

The first flags only the existing `response_language`. The second flags only
the existing `output_format`; it expressly preserves `export_format`. An
offline recommendation or a list of alternatives is not a concrete replacement.
When a supported compound request has an unresolved correction, its clear part
is not applied separately; affected known settings remain for clarification.

Repeating a concrete request in review mode can reuse an identical pending
proposal only when it captures the same uncertainty context. If another unclear
request has arrived meanwhile, the newer concrete proposal must capture that
new flag. Confirming the older proposal cannot resolve the later uncertainty.
Repeating the complete already-processed history remains a no-op.

An optional interpreter cannot hide this problem by returning an empty result
or proposing an unrelated field: the explicitly named known setting still gets
an uncertainty record. A relevant interpreter suggestion instead remains a
normal value proposal awaiting review. If an unclear compound request names
multiple existing settings, the recognized part is not applied on its own;
affected known settings can all be marked uncertain.

The same quotation, reported-speech, hypothetical, secret and temporary-scope
boundaries apply. Mentioning a setting in an explanation does not automatically
dispute it. This is still a restricted recognition layer: unfamiliar wording or
implicit references may be missed, while an unclear genuine request can create
a conservative clarification requirement. Uncertainty records never create a
setting when there is no known target.

The first clarification-only record upgrades that session's automatic-memory
state from schema 1 to schema 2. v0.14 and v0.15 read both; v0.13 rejects schema 2
before it can incorrectly confirm a null value. The session never automatically
downgrades after dismissal or resolution. Upgrade every worker sharing the
database to a schema-2-compatible version before enabling clarification records;
do not mix incompatible writers or attempt an automatic downgrade.

An explicit named correction can include the previous value:

```text
Export as JSON instead of CSV.
Reply in Hindi instead of English.
I prefer brief answers instead of detailed responses.
Change the response style from detailed to brief.
Change the response language from English to Hindi.
Switch exports from CSV to JSON.
```

The previous value must match the currently stored value of that named field,
ignoring letter case. If it does not match, or that field does not yet exist,
Relay queues an `old_value_mismatch` proposal and keeps the current memory.
Review that proposal, or provide a fresh direct instruction such as
`Export as JSON.` There is no fuzzy comparison between `brief` and
`brief responses`.

`MemoryPolicy(mode="review")` still requires confirmation before a new shortcut
changes active memory. The automatic mode accepts clear supported setters.

`from ... to ...` checks the named old value before applying the change. An
unsupported transition such as `Change the export format from CSV to Parquet`
is not automatically applied; an existing export setting can be marked uncertain.
It cannot fall through and invent a field called `export_format_from_csv`. Use
the direct form `Set the export format to Parquet` to obtain a concrete literal
proposal for review. A literal custom subject containing
the separate word `from` is now excluded as ambiguous transition syntax.

## Output aliases and short references

`I want/need/prefer the output in/as JSON` and `Give/Send/Return the output,
results or data in/as JSON` use the existing `export_format` or
`output_format` record when exactly one exists. With neither, it uses the
standard `export_format` key. If both fields exist, Relay queues an
`ambiguous_target` proposal, marks both disputed, and requires the application
to select the intended key during confirmation. It does not pick whichever
field was mentioned most recently. Explicit field setters keep their original
names: `Set the output format to JSON` still names `output_format`.
The v0.13 named grammar uses the same ambiguity rule for `Use JSON for the output`
and corresponding `results`/`data` wording. It does not create a separate `output`
field through the generic setter fallback.

There is also a narrow **review-only** local shortcut:

```text
User: Keep responses detailed.
Assistant: Acknowledged.
User: Make it brief.
```

`Make it brief`, `Keep them concise`, and relative requests such as `Make it
shorter` may produce a `contextual_needs_review` proposal. The same narrow path
handles `Switch to Hindi` after a named language instruction and `JSON instead,
please` after a named export/output instruction. The old value stays active but
disputed until confirmation. This uses
no model call. Its exact quote and `source.method = "contextual"` remain visible
in the audit and model context.

The immediately preceding user message must contain just one supported named
statement, apart from narrow courtesy fragments. Its named target and value
must match current undisputed memory. Reaffirming an identical older value now
qualifies even though Relay keeps the original memory source. Multiple
preferences, an intervening user request, a missing/forgotten value, and memory
whose source belongs to an archived segment do not qualify. A prior contextual
request also cannot serve as the next named antecedent.

Relative values such as `shorter` or `less verbose` stay literal in the proposal;
Relay does not decide that they mean an absolute `brief` setting. Review can
accept or dismiss the proposal, or the user can state an exact desired setting.
A contextual proposal never automatically resolves another dispute. A later
named instruction such as `I want brief answers` can resolve it normally.

## What remains unsupported

Automatically applying new values still requires every non-courtesy fragment in
a user message to be a supported statement or a supported setting-linked
explanation. Unknown surrounding prose can frame
the following words as a quotation, test fixture or hypothetical. Relay therefore
does not apply only the convenient sentence. Eligible direct corrections naming
existing settings may create the clarification-only records described above.
For example, these are deliberately not saved:

```text
For the demo:
Export as CSV.

Reply in Hindi. Does that sound right?
Actually, CSV please.
Make it brief.  (without the narrow recent style antecedent above)
```

Even `Thanks for helping` remains outside the narrow courtesy whitelist.
General questions, quoted/code text, unsupported negatives, hypotheticals,
reported statements and unsupported surrounding prose retain conservative
handling. Only the complete request and contrast forms above bypass those
exclusions. This is English instruction grammar; it is not an
understanding engine for instructions written in the listed response languages.

Skipped statements appear in the processing report. Review the report during
integration; a successful model response does not mean every user preference
was recognised. Recognition does not verify a statement's truth or guarantee
that a downstream model will follow it.

The added tests cover recognised wording, unchanged original source spans,
review mode, named correction targets, old-value mismatches, unknown prose and
the eight previously identified framing regressions. These are deterministic
behaviour checks, not a new model-accuracy benchmark.

For v0.11 we froze a 65-case development corpus before changing the parser.
The previous parser matched 37 expected outcomes; the updated parser matched
65: 23 named updates, 6 review outcomes, 33 excluded/unsupported cases and 3
existing regressions. Exact source slices stayed intact. This is a small,
manually authored development set, **not** held-out evidence of natural-language
accuracy. The optional model interpreter's earlier failed tests remain valid;
these new results involve no LLM. See [the raw corpus](../evaluations/corrections-v0.11-corpus.json),
baseline (local-only reference; omitted from public distribution) and
final result (local-only reference; omitted from public distribution).

For v0.12, the implementer froze a separate 70-case development corpus before
changing the parser. The previous parser matched 32 expected outcomes; the new
parser matched 70: 24 named updates, 13 review outcomes and 33
excluded/unsupported cases. One baseline failure was an invented `your_replies`
field, not simply a missed instruction. These are development-set results,
not held-out or general language accuracy. See the [frozen development corpus](../evaluations/corrections-v0.12-development-corpus.json),
baseline (local-only reference; omitted from public distribution) and
final development result (local-only reference; omitted from public distribution).
An independently authored unseen set applied only **1/12 clear updates**, with
2/3 required reviews and 8/9 abstentions. It exposed a pronoun being treated as
a named field (`it=JSON`). A targeted post-holdout safety fix now excludes bare
pronoun subjects from named setters; the separate contextual path still requires
review. The regression then achieved 9/9 abstentions and zero wrong updates,
while clear recall stayed 1/12. This rerun is no longer unseen evidence. See
[the raw runs, misses and fingerprints](../evaluations/CORRECTIONS-v0.12.md).

The earlier 1/11 denominator was a reporting mistake: code-point inspection now
confirms that case `h06` contains a valid U+2014 em dash. A console rendering issue
was mistaken for malformed input. The original corpus and raw results were never
changed; see the explicit erratum (local-only reference; omitted from public distribution).
That old set is now development material. The v0.13 parser matches 11/12 clear
updates, 3/3 review outcomes and 9/9 abstentions on it, with zero wrong active
updates. The remaining clear miss combines rejection of the old value with a
separate shorthand replacement. These are regression results after studying the
old cases, **not new unseen language accuracy**. The new report (local-only reference; omitted from public distribution)
records current source fingerprints.

The separate frozen v0.13 check is less encouraging: **3/16 clear updates**,
1/4 required reviews and 12/12 abstentions, versus 0/16, 0/4 and 12/12 for v0.12
on the same new cases. The parser was not tuned after seeing this result. The
optional local interpreter supplied two additional exact clear candidates for
review, not automatic application. See [the v0.13 evidence and limits](../evaluations/CORRECTIONS-v0.13.md).

A separate frozen v0.14 set of 40 cases produced **5/16 clear automatic updates**,
6/8 correct uncertainty flags, 15/16 abstentions and two wrong active updates.
A narrow safety fix after inspecting those failures left the first two counts
unchanged and reached 16/16 abstentions with no wrong active updates. That final
rerun is a **post-holdout development regression**, not an unseen score. The
remaining missed corrections can still leave stale memory undisputed. See the
[v0.14 report, preserved source and raw results](../evaluations/CORRECTIONS-v0.14.md).

The v0.15 work retains the first frozen-parser failure report and the unchanged
internally authored corpus. Runs after inspecting those cases are development
and regression evidence, not new unseen accuracy. The grammar and its
deterministic checks make no model calls. See [v0.15 correction evidence](../evaluations/CORRECTIONS-v0.15.md)
and [validation scope](VALIDATION.md). Upgrading does not reinterpret an already
processed conversation prefix; only newly appended original messages use the
new grammar.
