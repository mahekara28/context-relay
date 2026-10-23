# Independent instruction corpus v0.18

This is an internal, independently authored assistant evaluation. It is not external or human validation. The author read only `examples/evaluate_corrections_v014.py` to learn the schema; implementation files and previous wording corpora were not inspected.

The corpus was frozen before implementation scoring. Its SHA-256 is:

`68e25aa808e0afe6fa103cd9ece355d9d9b661e818524740a287a6b186017f85`

## Composition

| Cohort | Clear | Uncertain | Abstain | Total |
| --- | ---: | ---: | ---: | ---: |
| Supported | 24 | 18 | 18 | 60 |
| Exploratory unsupported | 2 | 2 | 0 | 4 |

The supported cohort has 18 English, 14 Hindi/Hinglish, 14 Spanish, and 14 French cases. Hindi/Hinglish includes six Devanagari and eight Latin-script cases. The exploratory cohort contains two German and two Italian cases.

Every case seeds the same three settings through clear English messages: export format, response language, and answer detail. This makes selective targeting observable while keeping setup consistent. The corpus uses `export_format`, `response_language`, and `response_style`; it does not exercise `output_format`.

## Schema and grading

The file is compatible with the `cases` schema consumed by `evaluate_corrections_v014.py`. Each case has `id`, `kind`, `before`, and `text`. Clear cases add `expected`; uncertain cases add `targets`; abstention cases add neither. Extra `cohort`, `language`, and `tags` fields are descriptive metadata.

Use the existing grading meanings: clear updates change exactly the expected values without pending corrections; uncertainty preserves stored values and flags exactly the specified targets with null replacement values; abstention preserves stored values without pending corrections. Confirm that all three seed preferences were captured and report setup failures explicitly.

The existing evaluator does not separate cohorts. Join its output to the corpus by case ID and report supported and exploratory metrics independently. Never include exploratory cases in a supported-scope success rate. Their labels describe semantic intent, not promised language support.

## Freeze and disclosure

Do not score this corpus until the implementation team declares its first source freeze. Before that freeze, share only counts, schema, protocol, and hashes. Keep individual prompts and item-level findings hidden.

Preserve the first evaluation output and its source hashes. Preserve all failures. Do not relabel, remove, or rewrite cases to make an implementation pass. A genuine corpus defect requires a new corpus version and an explicit audit note that retains the original evidence.

Once the implementation team sees prompts or item-level results, later runs are regression checks on disclosed data, not a new blind holdout. Record that distinction. This finite set cannot establish general multilingual instruction understanding.

