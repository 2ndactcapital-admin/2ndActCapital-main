# NOTEFIX1 — discovery findings (2026-10-13)

Read-only. No model was called; no document was fetched that wasn't already stored. All four sections
were produced against the LIVE database and R2 corpus, not invented after the fact.

## 1a. Where one field's error becomes a whole-reading failure

The cascade's validation chain, in call order:

1. `readers.read` builds the prompt (`readers.build_messages`) and calls the proxy.
2. The raw response text is handed to `schema.parse_reader_output(specs, content)`.
3. **(old behaviour)** `parse_reader_output` ran `schema.pydantic_model(specs).model_validate_json(text)` —
   ONE combined Pydantic model covering every field at once. A single field's `ValidationError` (e.g. a
   range-shaped answer for a field the registry still called `scalar`) raised for the WHOLE object.
   `parse_reader_output` returned `(None, error)`; `readers.read` turned that into `call.status = 'invalid'`,
   and `call.usable` became `False` for the entire call — every other field's correctly-read value was
   discarded with it.
4. `cascade.run_note` and `prefill.prefill_note` both call `readers.read` and both treat an unusable call
   as contributing nothing to that note's resolved values (only the independent rules/EdgarTools/derived
   sources survive). `services.note_extraction.schema.build_field_specs` / `normalize` / the cascade's
   comparison step (`compare.py`) and the review API (`routers/note_extraction_admin.py`) never re-validate
   a reading after it is written — they trust the stored row — so the bug was entirely upstream, inside
   `parse_reader_output`.

This is the ONLY place a single field's shape failed the whole reading. Fixed (Task 3): validation is now
per field (`schema._parse_one_field`, `schema._field_model`); the response is only wholly rejected when it
is not parseable JSON at all.

## 1b. How the review screen picks a pre-fill run

`services/note_extraction/gold.py::prefill_runs_for` already selects, per note, every `gold_prefill` run
that produced a reading for that note, ordered `ORDER BY r.started_at DESC`:

```sql
SELECT DISTINCT r.id, r.config->>'model' AS model, r.started_at
  FROM portfolio.note_extraction_runs r
  JOIN portfolio.note_term_readings t ON t.run_id = r.id
 WHERE r.run_kind = 'gold_prefill' AND t.reference_filing_id = $1
 ORDER BY r.started_at DESC
```

`routers/note_extraction_admin.py::gold_note` takes `prefill_runs[0]` as `run_id` and builds
`_prefill_by_field` from readings carrying exactly that run's id. A second `gold_prefill` run for the same
batch is picked up with **no code change**: every note in the batch gets a fresh reading under the new
run's id, that run's `started_at` is later, so it sorts first. This is what makes Task 5's `--supersede`
safe to add without touching the review path at all.

## 1c. Self-checks that read the fields changing shape

- `checks.estimated_value_below_price` already calls `schema.as_range` on `estimated_value_pct` /
  `estimated_value_per_1000` and needed no change.
- `checks.fees_reconcile` read `price_to_public_pct`, `total_fees_pct` and `proceeds_to_issuer_pct` as three
  bare scalars (`_d(...)`). With the latter two now ranges (Task 2), it was rewritten to compare INTERVALS:
  the implied proceeds interval `[price - fees.max, price - fees.min]` (price stays scalar) against the
  stated proceeds range `[proceeds.min, proceeds.max]`, each open-ended bound filled with `[0, price]`
  (fees and proceeds can't be negative or exceed the full price). The finding fires only when the two
  intervals share no point within `FEES_TOLERANCE_PCT` — i.e. only when NO feasible combination of a fee
  value and a proceeds value is consistent with price. A wholly scalar note (every range's `min == max`)
  reduces exactly to the old equality check.

## 1d. Why the hypothetical-example detector misses (run over the 138 fetched finals)

Ran `traps._hypothetical` (old version, before this sprint's fix) over every FETCHED FINAL pricing
supplement in cohort `54024e47-096c-4120-bcf3-e3403ad2ef7b` (308 members, 138 fetched finals — confirmed
live, matching the task prompt's count). Real R2 text, no model call.

**Old hit rate: 1/138.** The detector required `rules.find_hypothetical_rows` to find ≥2 lines, inside a
6000-char window starting at the FIRST occurrence of the bare word "hypothetical" anywhere in the filing,
where the line's first token is a signed percent and the same line also carries a `$` amount. Real filings
put many "hypothetical" mentions in risk-factor boilerplate well before any actual table, and the real
table's rows are rarely laid out as single lines matching that exact shape.

**Real per-bank heading / column-header wording actually used** (sampled across all 138):

| Bank | Representative wording found |
|---|---|
| JPMorgan | "hypothetical total return or hypothetical payment at maturity"; "hypothetical returns and hypothetical payments shown above"; **no inline worked table in the extracted text** — only risk-factor boilerplate ("hypothetical components: (1) a fixed-income debt component..."). A miss here is a correct quiet result, not a bug. |
| Morgan Stanley | "Hypothetical Examples"; "Hypothetical Downside Threshold Level:"; "hypothetical initial index value" |
| UBS | "Hypothetical Examples of How the Notes Might Perform"; "hypothetical terms"; "hypothetical investment in the least performing underlying" |
| Goldman Sachs | "Hypothetical Examples" / "HYPOTHETICAL EXAMPLES"; "hypothetical examples shown below do not take into account" |
| Citigroup | "Hypothetical final underlying value"; "Hypothetical underlying return"; "hypothetical initial underlying value" |
| Bank of America | "Hypothetical Payout Profile and Examples of Payments at Maturity"; "hypothetical Starting Value of 100"; "hypothetical Ending Values of the Least Performing Underlying" |
| Barclays | "Hypothetical Initial Value of each Reference Asset: 100"; "hypothetical examples below do not take into account any tax" |

**Bound ("up to" / "at least" / "as low as") wording near the five economics fields**, same corpus:
`price_to_public_pct` 2 hits (both Goldman Sachs, both "a discount from the initial price to public of up
to 1.0xx% of the principal amount" — a DEALER RESALE DISCOUNT, not the field's own value: stays scalar);
`structuring_fee_pct` 6 hits ("structuring fee of up to $8...", Morgan Stanley / Goldman Sachs / Bank of
America); `other_fees_pct` 24 hits (mostly Bank of America "referral fee of up to $X per $1,000"); `total_
fees_pct` and `proceeds_to_issuer_pct` 0 hits in this specific cohort (widened to range anyway per Task 2 —
the real incident field, `structuring_fee_pct`, already confirms the shape occurs in the wild, and
`total_fees_pct` is the sum of range-shaped components so it must be able to carry one itself).

**Fix (Task 4):** `traps._HYPOTHETICAL_WORDING` widens the trap detector to the real heading / column-
header families above, while leaving the stricter numeric-row parser (`rules.find_hypothetical_rows`) in
place for the self-check that actually needs parsed rows. **New hit rate: 132/138** — JPMorgan 18/18,
Morgan Stanley 27/30, UBS 13/13, Goldman Sachs 7/9, Citigroup 17/17, Bank of America 29/29, Barclays 21/22.
The six remaining misses had none of the widened wording in their extracted text either; the trap module's
own documented tolerance ("a false positive costs one extra note; a false negative hides a trap") means a
generous hit (e.g. JPMorgan's boilerplate-only mentions) is an acceptable cost, not a new bug.
