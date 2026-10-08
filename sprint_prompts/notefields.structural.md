NOTE FIELDS v3 — the approved structured-note schema: registry, structured
lists, rules for labeled fields, self-checks, and two model guardrails.
6 tasks + verification.

Joe approved the field list on 2026-10-08 (docs/NOTE_FIELDS.md, copied in Task 2
from NOTE_FIELDS_v3.md). It was built from an inventory of 22 pricing supplements
across 21 issuers. This sprint makes the extraction stack use it. It does NOT run
extraction at scale and does NOT touch securities_global_note_terms.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (checked live 2026-10-08):
- portfolio.note_terms_field_registry has ONLY: field_key, display_label,
  data_type, applies_to_archetypes, hazard_field, created_at. 19 rows.
- B1 stores everything ONE ROW PER FIELD KEY: portfolio.note_term_readings
  (field_key, value, value_normalized, source_quote, raw_char_start/end, ...),
  portfolio.note_extraction_staged_fields (field_key, resolved_value, resolution,
  is_critical, needs_review, ...), portfolio.note_gold_values (field_key, value,
  action, ...). New scalar fields therefore need registry rows, not new columns.
- portfolio.distribution_participants exists (canonical_name, participant_type,
  aliases, status, observed_count).
- The reader schema is GENERATED from the registry (services/note_extraction
  schema.build_field_specs), extended with B1's own extension fields. After this
  sprint the registry is the ONLY source: B1's hard-coded extension list goes.
- portfolio.securities_global_note_terms is the old fixed-column production
  table. Out of scope: promotion to it is B2.
- Inventory run aa1c8c7c-239a-4057-9fb3-2c911c9c2bc5 (portfolio.edgar_inventory_
  concepts / _items) holds every label each bank used, with quotes. It is the
  source for synonyms and the per-bank label dictionary.
- services/note_extraction/quote_match.py (TextIndex) is the shared, whitespace-,
  ellipsis- and spacing-tolerant quote matcher. Reuse it.
- Claude models are ruled out for this work. No OpenRouter. Provider keys live
  only in Doppler prd_lite_llm; all calls go through the proxy.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a script
finished. Never wait for one. Run every command synchronously in the foreground.
Write the verify script but DO NOT RUN IT; the operator runs it. RLS policy in the
same migration as any new table (four policies, the global-table pattern). Never
print a secret value. Column names come from the live database, not this prompt.

DECISIONS (Joe) — do not re-litigate:
A. initial_valuation_date is not a separate field: it is pricing_date. A rule
   flags (needs_review) any note whose filing states a different initial date.
B. Estimated value stored both as $ per $1,000 and % of principal; the stated one
   is extracted, the other derived and marked derived.
C. "Up to" / "as low as" / "not less than" amounts are a MIN and MAX, with the
   bound wording kept as evidence; a single amount sets min = max.
D. No further fields.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every place that reads note_terms_field_registry or B1's extension field
      list (schema builder, rules, trimming, cascade, gold screen, harness).
  1b. How readings/staged/gold values store non-scalar values today (if at all),
      so the three list fields below fit without breaking existing rows.
  1c. Existing rows referencing any field_key this sprint renames or retires
      (readings, staged fields, gold values, the 58 migrated legacy readings).
      Nothing may be orphaned: renames get a mapping and a data migration.
  1d. Which platform_model_catalog entries have no known price on the proxy.

=== TASK 2: THE REGISTRY BECOMES THE SCHEMA ===
Copy NOTE_FIELDS_v3.md (operator places it at docs/NOTE_FIELDS.md) into the repo.
Extend note_terms_field_registry with: description (the meaning, used verbatim in
reader prompts and mapping), section, is_critical, value_shape ('scalar' | 'range'
| 'list'), unit, synonyms text[], trap_rule, extraction_method ('model' | 'rules'
| 'derived'), derived_from, retired_at. Then load EVERY field in docs/NOTE_FIELDS.md
with those properties, exactly as the document states (critical = the ★ fields;
section 9 fields are extraction_method 'rules'; tenor and max loss are 'derived').
Retire, don't delete, any field the document drops; map renamed keys (e.g.
coupon_rate -> coupon_rate_pa, autocall_barrier_pct -> autocall_level_pct,
protection_pct -> buffer_pct / barrier_pct per protection_type, tenor_years ->
tenor_months) and migrate existing readings/staged/gold rows to the new keys with
the old key kept in metadata.
Remove B1's hard-coded extension field list; the schema builder reads only the
registry. A buffer and a barrier must remain separate fields.

=== TASK 3: THREE STRUCTURED LISTS ===
'list' fields hold an array of objects, validated by Pydantic, stored where 1b says
values live (jsonb if needed, added in this sprint with a migration):
  - underlyings: name, ticker, kind, weight_pct, initial_level, return_basis,
    fx_treatment
  - observation_schedule: observation_date, payment_date, coupon_barrier_pct,
    coupon_amount, call_level_pct, call_amount
  - distribution: participant name (matched to distribution_participants by
    canonical name or alias; unmatched names reported, never dropped), role,
    fee_min_pct, fee_max_pct
Comparison in code for lists: same members (order-insensitive where order has no
meaning), each member's fields compared with the same normalisation as scalars.

=== TASK 4: RULES, DERIVATIONS, SELF-CHECKS ===
  - Rules extract every extraction_method='rules' field and the labeled scalar
    fields (dates, CUSIP, ISIN, estimated value, price to public, commission,
    fee-based price, proceeds, issue size, denomination). The per-bank label
    dictionary is GENERATED from inventory run aa1c8c7c (labels per concept per
    issuer group) into a versioned file the rules load, not hand-written. The
    mapping check's synonym table (_SYNONYMS in edgar_inventory.py) is generated
    from the registry's synonyms column instead of being hard-coded.
  - Derivations: tenor_months, max_principal_loss_pct, and the estimated-value
    unit not stated (decision B), each marked derived.
  - Ranges (decision C): parse min/max and keep the bound wording as the quote.
  - Self-checks, each producing needs_review with a reason, never silently fixing:
    price - total fees ≈ proceeds to issuer; estimated value < price;
    observation dates fall between pricing and maturity; initial valuation date
    differs from pricing date (decision A); recomputed payoff disagrees with the
    filing's hypothetical example table, when one is present.

=== TASK 5: TWO MODEL GUARDRAILS ===
  - platform_model_catalog gains public_data_only boolean (default false). A
    model with it set may be used ONLY for global/public-data task keys (the note
    extraction ensemble, the EDGAR inventory); org-level model pickers and every
    org-scoped AI call refuse it. Set it true for the OpenAI models (the OpenAI
    project shares data with OpenAI for free usage).
  - Bulk runs (pilot, evaluation, inventory, B2's extraction) refuse any model whose
    price the proxy does not know, unless a manual price is entered on the catalog
    entry (add manual_input_cost_per_mtok / manual_output_cost_per_mtok). The spend
    cap uses the manual price when the proxy has none.

=== TASK 6: STATUS ===
Update docs/PROJECT_STATUS.md: the approved field list, the renames and migrated
row counts, the generated label dictionary, the guardrails.

=== VERIFICATION: apps/api/scripts/verify_notefields.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from Doppler
over HTTPS at startup. Mock all model calls; real spend $0. Wrap setup, teardown and
every section so an exception is a [FAIL], never a crash; teardown at start and end.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; the helper asserts
    isinstance(passed, bool).
  - EVERY fixture value must satisfy the table's live CHECK and FK constraints —
    read them from pg_constraint before writing fixtures. Never invent a status.
  - Read pg_constraint.contype (and any "char" catalog column) as ::text; asyncpg
    returns "char" as bytes, and comparisons against 'f' or 'c' silently fail.
  - Never derive a unique value from a slice of a fixture UUID.
  - Set RLS context on EVERY read; a read without it returns empty silently.
  - Expected counts derived from what the test created, never hard-coded; any real
    sample deduplicated by accession number and excluding fixtures.
  - An empty list must never make a check pass: assert non-emptiness first.

Assertions:
  [Y] Task 1's four findings reported
  [Y] Every field in docs/NOTE_FIELDS.md is in the registry with section,
      is_critical, value_shape, description; the critical set equals the ★ set
  [Y] No field_key is orphaned: every reading, staged field and gold value points
      at a registry key (live or retired-with-mapping); migrated counts printed
  [Y] The generated reader schema equals the registry (no hard-coded list remains);
      buffer and barrier are distinct fields
  [Y] Each list field validates a good fixture and rejects a malformed one; list
      comparison treats reordered equal members as agreement
  [Y] An unknown distribution participant is reported, not dropped
  [Y] Rules on fixture snippets extract each labeled field, using a label taken
      from the generated dictionary for at least three different banks
  [Y] Derivations: tenor, max loss, and the missing estimated-value unit are
      computed and marked derived
  [Y] Ranges: "up to 2.50%" -> min NULL, max 2.50, bound wording kept as the
      quote; "as low as $977.50" -> min 977.50, max NULL; "$977.50" alone ->
      min = max = 977.50
  [Y] Each self-check fires on a crafted inconsistency and stays quiet on a
      consistent fixture; each produces needs_review with a reason
  [Y] public_data_only: an org-scoped call and an org model picker refuse a
      flagged model; the note ensemble accepts it
  [Y] A bulk run refuses a model with no proxy price and no manual price; with a
      manual price the spend cap uses it
  [Y] securities_global_note_terms row count unchanged
  [Y] Teardown leaves zero fixture rows
