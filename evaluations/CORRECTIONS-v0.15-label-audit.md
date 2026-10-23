# v0.15 first-exposure corpus label audit

Audited 2026-10-23 by the corpus-author task agent after the first parser freeze and scoring. The author read the frozen corpus and first evaluation report, and did not inspect or edit implementation source. No external sources, external models, or new model API calls were used.

**Verdict: retain all 78 original labels.** All 36 clear misses, all 18 uncertainty misses, and both failed abstention controls were individually reviewed below. No label changes, exclusions, or adjusted scores are warranted. This is the author's explicit fairness review, not a claim of independent third-party adjudication.

## Preserved evidence

- Original corpus: `work/corrections-v015-blind.json`.
- Evaluation corpus copy: `outputs/context-relay/evaluations/corrections-v0.15-fresh.json`.
- Both corpus files have SHA-256 `714d39e42742d0eb844a5b4c7a748437cd2df7d12b38ec7e7125ae8809b6b7fe`.
- First report: `outputs/context-relay/evaluations/corrections-v0.15-fresh-first.json`, SHA-256 `49488346f824ecb20b9c1ae9a0c22c46711ca3f6306aea51ce321133f92f7e05`.
- First report records `context_relay/memory.py` SHA-256 `48d06fa3dc4f24ae4acb8ed5042938a8b0dcc7b4d99d17fcc86d1b37fa623e09`.
- First score remains 0/36 clear, 0/18 uncertainty, 22/24 abstention; one wrong active update and one false alarm. Source spans, replay, and authority checks pass.
- The corpus is internally authored, not an external benchmark. It was hidden for the first score. Since its exposure, reruns are development/regression measurements and must not be described as unseen generalization.

The observed seeded values are correct, including CSV -> JSON -> CSV and detailed -> short -> detailed histories. Missing or incorrect seeds do not explain these failures. The grader merges expected changes with the seeded baseline, so an unmentioned or explicitly unchanged sibling must remain unchanged.

## Boundary decisions

1. **Old values and reaffirmation:** a value mentioned as old, replaced, or no longer preferred is context for the selected replacement, not a competing new assignment. Conversely, a statement that the current value remains correct requests no mutation or pending clarification. Cases clear-32 and clear-36 deliberately return to a value used earlier in history; that does not make the latest correction redundant. Abstain-14, which passed, confirms an already-current value.
2. **Unchanged siblings:** clear-35 changes only export_format and expressly retains output_format. Uncertain-11 disputes only export_format and response_language while affirming response_style. Uncertain-18 disputes only output_format while affirming export_format. These target scopes are explicit.
3. **Explanatory sentences:** explanation can identify the old value, establish durability, or supply an antecedent for 'that' or 'it'. It is part of the same user's request. It does not authorize unrelated generic fields. In abstain-13, 'The existing entry is correct' explains the English reaffirmation; it is not an existing_entry preference.
4. **Custom slots:** delivery_window is seeded under its exact known key. The named replacements evening, afternoon, and night are literal, single-word values with no normalization ambiguity. Surrounding phrases such as 'as the standing preference' describe durability rather than being part of the value. Uncertain-12 and uncertain-13 refer to the same named slot without supplying a replacement, so they require uncertainty rather than a fabricated literal.
5. **Ambiguous alternatives including the existing value:** uncertain-06 and uncertain-07 name two alternatives and explicitly withhold the final choice. The fact that one candidate equals the baseline does not select it. The active value remains unchanged while the requested revision is unresolved. This follows the stated corpus contract for an identifiable update with an ambiguous replacement.
6. **Polite requests and ownership:** 'Could you ...?' and 'I'd like ...' are direct requests to the assistant in these cases. 'For future exports' in clear-03 inherits the active user's export setting and explicitly sets its default. There is no quoted speaker, conditional antecedent, temporary limit, or other owner in the clear cases.

## Individual review: clear cases

All IDs in this section have the `v015-` prefix. Every row retains `kind=clear` and the original expected values. The first report leaves all active values unchanged; clear-26 and clear-29 also create incomplete/unnecessary uncertainty flags, while the other 34 clear cases create no flag.

| Case | Fairness rationale |
| --- | --- |
| clear-01 | 'save JSON', 'my usual export format', and 'from now on' identify value, field, ownership, and durability. |
| clear-02 | Explicitly replaces the saved CSV export preference with JSON permanently. |
| clear-03 | Directly makes JSON the default for future exports, replacing CSV. |
| clear-04 | Declares the user's export-format preference changed from JSON to CSV. |
| clear-05 | Separates the old CSV export choice from the now-standing JSON choice. |
| clear-06 | Names the recorded export field and asks to correct that entry to JSON. |
| clear-07 | Requests remembering JSON rather than CSV as the user's export format. |
| clear-08 | Requests a switch to CSV for exports and expressly saves it as an ongoing preference. |
| clear-09 | English is the grammatical subject replacing Hindi for the assistant's replies from now on. |
| clear-10 | Names the language used in replies and gives Hindi as the correction going forward. |
| clear-11 | Marks English as outdated and supplies Hindi for the assistant's responses. |
| clear-12 | Polite request makes English the standing response language; the second sentence identifies Hindi as old. |
| clear-13 | Specifies Hindi for all future replies, replacing English, and explicitly requests remembering it. |
| clear-14 | Explicitly changes the remembered reply language from Hindi to English. |
| clear-15 | Explicitly changes the standing output format from CSV to JSON. |
| clear-16 | Declares CSV as the new output-format preference instead of JSON. |
| clear-17 | Replaces the saved output format with CSV; the explanatory clause identifies JSON as old. |
| clear-18 | Selects JSON as the output format and requests saving it for future conversations. |
| clear-19 | Sets detailed as response style from now on and rejects the old short style. |
| clear-20 | Selects concise as the usual response style and requests remembering the change. |
| clear-21 | Names the saved response-style field and explicitly states brief replaces detailed. |
| clear-22 | Identifies the response-style entry and gives detailed as its correct value, rejecting concise. |
| clear-23 | Explicitly replaces brief response style with detailed responses going forward. |
| clear-24 | Gives short as the new default; the next sentence explains why detailed is stale. |
| clear-25 | Two explicit replacements are scoped to separate fields: exports -> JSON and replies -> Hindi. |
| clear-26 | Two direct future-default updates are scoped to output_format -> CSV and response_style -> concise. |
| clear-27 | The shared 'saved reply preferences' antecedent scopes language -> English and style -> detailed. |
| clear-28 | Four semicolon-separated permanent corrections explicitly name every field and its replacement. |
| clear-29 | Explicitly replaces delivery_window morning with the literal evening as a standing preference. |
| clear-30 | Explicitly corrects saved delivery_window from morning to the literal afternoon. |
| clear-31 | Marks delivery_window outdated and selects the literal night as its default, replacing morning. |
| clear-32 | The current seeded export value is CSV; the latest message selects JSON again for future sessions. |
| clear-33 | Rejects CSV for output_format and supplies JSON as the remembered replacement in the next sentence. |
| clear-34 | Requests an update to remembered responses and identifies Hindi as their language from now on. |
| clear-35 | Selects JSON permanently for export_format and explicitly preserves CSV for output_format. |
| clear-36 | The current seeded response style is detailed; the latest message restores short as the default. |

## Individual review: uncertain cases

Every row retains `kind=uncertain` and the original targets. Each identifies a requested correction or revision but omits a uniquely selected replacement. All 18 preserve active values in the first report, but none emits the required uncertainty flag.

| Case | Fairness rationale and target scope |
| --- | --- |
| uncertain-01 | Saved export_format is explicitly wrong; its replacement has not been supplied. |
| uncertain-02 | Recorded response_language needs correction; replacement is undecided. |
| uncertain-03 | Requests an output_format change to an offline reference and explicitly acknowledges its value is absent here. |
| uncertain-04 | Requests a response_style change based on unshared paper examples. |
| uncertain-05 | Requests replacing export_format but withholds which private alternative to use. |
| uncertain-06 | Requests a response_language update but gives English or Hindi without a selection. |
| uncertain-07 | Requests revising output_format but explicitly has not selected between CSV and JSON. |
| uncertain-08 | Requests correcting response_style but has not chosen short or detailed. |
| uncertain-09 | Explicitly disputes export_format and response_language, withholding both replacements. |
| uncertain-10 | Explicitly requests standing changes to output_format and response_style, withholding both values. |
| uncertain-11 | Explicitly marks export_format and response_language outdated; response_style is explicitly affirmed. |
| uncertain-12 | Requests updating delivery_window, rejects the current window, and says the replacement will arrive later. |
| uncertain-13 | Rejects morning for delivery_window and requests a change, but the replacement is undecided. |
| uncertain-14 | Explicitly disputes response_style and refers to a mentor's recommendation that has not been described. |
| uncertain-15 | Requests fixing saved export_format and expressly cannot yet supply its replacement. |
| uncertain-16 | Explicit permanent response_language update has an undecided replacement language. |
| uncertain-17 | Rejects detailed as the default response_style and requests an update plus clarification before choosing. |
| uncertain-18 | Explicitly preserves export_format and requests replacing only output_format, without choosing its new value. |

## Individual review: failed abstention controls

| Case | Fairness rationale | Observed failure |
| --- | --- | --- |
| abstain-11 | JSON applies to 'this one answer only'; the saved output_format must remain CSV. No durable preference is disputed. | Active output_format remains CSV, but an unparsed_correction flag creates a false alarm. |
| abstain-13 | English remains the desired response_language and the existing entry is explicitly correct. No new preference is stated. | Adds the unrelated active field existing_entry='correct'. |

## Shared structures to address

These are linguistic failure groups inferred from the cases and output, not implementation diagnoses:

- **Field identity through ordinary phrasing:** hyphenated field names; 'exports', 'reply language', and 'language of your replies'; possessives and modifiers such as saved, recorded, usual, and standing.
- **Correction intent beyond one imperative template:** save, remember, replace, make, switch, correct, outdated, no longer, and polite or declarative correction forms.
- **Direction of replacement:** from-old-to-new, new-replaces-old, new-rather-than-old, and old-is-stale/new-is-current must identify the selected replacement without treating the old value as a competing assignment.
- **Local references across clauses and sentences:** 'that', 'it', 'the replacement', and shared reply-preference context refer to settings already identified in the same message.
- **Compound scope:** each field needs its own selected value or missing-value status; an expressly unchanged sibling must neither block another field's correction nor become disputed itself.
- **Unresolved correction intent:** a named setting plus explicit revision/rejection and missing, unavailable, or unchosen replacement calls for targeted uncertainty. A mentioned baseline value is not enough to resolve that request.
- **Control boundaries:** a temporary task instruction with an affirmed saved default needs no pending flag; a plain explanatory reaffirmation must not become a new generic key/value preference.

The first score measures a narrow parser's coverage gap on this internally authored wording set. Conservative non-updates avoid many wrong writes, but they do not satisfy the expected correction-capture or uncertainty behavior. Fixes should be judged against both the retained capture labels and retained abstention controls.
