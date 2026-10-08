GOLD SET — build the hand-checked answer key for structured-note extraction:
a stratified top-7-bank sample with trap cases, GPT-5-mini pre-fill, a fast
review screen, and a scorer that grades any extraction run against it.
6 tasks + verification.

The approved field list (docs/NOTE_FIELDS.md, NOTE_FIELDS v3) is live as of the
notefields sprint (verify 156/156). Before any reader pair or Jev can be chosen,
and before B2 extracts at scale, we need ~50 notes whose correct values Joe has
checked by hand. This sprint builds everything needed to produce and use that
set. It does NOT run the evaluation of candidate models and does NOT touch
securities_global_note_terms.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (checked live 2026-10-08):
- portfolio.note_gold_candidates exists: id, reference_filing_id, sample_batch,
  issuer_group, filing_year, product_type, trap_tags text[], status, proposed_at,
  updated_at. status CHECK: proposed | in_review | done | skipped. 0 rows.
- portfolio.note_gold_values exists: id, reference_filing_id, field_key, value
  jsonb, action, source_reading_id, source_quote, raw_char_start/end, notes,
  reviewer_id, reviewed_at, valid_from, valid_to, metadata. action CHECK:
  confirmed | corrected | absent; absent requires value IS NULL. 0 rows.
- portfolio.edgar_cohorts kind CHECK: custom | hand_picked | template_study |
  copy; member_count 1..50000. edgar_cohort_members has a stratum column.
- portfolio.note_extraction_runs run_kind CHECK: evaluation | pilot | cascade |
  participant_seed | verify. status CHECK: running | completed |
  stopped_spend_cap | failed | dry_run.
- The registry (note_terms_field_registry) is the schema: section, is_critical,
  value_shape (scalar | range | list), extraction_method (model | rules |
  derived), synonyms, retired_at. Lists: underlyings, observation_schedule,
  distribution. Rules use the generated label dictionary notefields.labels.v1.
- About 25% of fetched 424B2s are FINAL pricing supplements; the rest are
  preliminaries, term sheets and supplements (document_kind is set at fetch).
  A sample must therefore be over-fetched and then filtered to finals.
- Top seven banks by selected volume, by credit group: JPMorgan, Morgan
  Stanley, UBS, Goldman Sachs, Citigroup, Bank of America, Barclays.
  Filing dates span 2019-01-02 to 2026-10-01.
- gpt-5-mini is public_data_only (OpenAI project with data sharing on) and is a
  reasoning model: it needs max_tokens of at least 32000 or it returns empty.
- Claude models are ruled out for this work. No OpenRouter. All calls go
  through the LiteLLM proxy; provider keys live only in Doppler prd_lite_llm.
- Baselines (do NOT re-run except verify_notefields.py, below):
  verify_edgarpipelinea 102/102, verify_noteextractb1 113/113 (+3 later),
  verify_edgarcohorts 157/157, verify_edgarinventory_v2 136/136,
  verify_notefields 156/156.
- Two indexes were dropped live on 2026-10-08 to save space and are NOT yet in
  a repo migration: portfolio.edgar_index_filings_form_date_idx and
  portfolio.edgar_index_filings_status_idx (both covered by wider indexes).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a script
finished. Never wait for one. Run every command synchronously in the foreground.
Do NOT fetch documents or call any model for real in this sprint — build the
commands; the operator runs them. Write the verify script but DO NOT RUN IT; the
operator runs it. RLS policies in the same migration as any new table (four
policies, the global-table pattern). Never print a secret value. Column names
come from the live database, not this prompt. UI: light theme, whites and
creams, matching the existing admin screens.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. What gold-review UI and API already exist from B1 (routes, components,
      endpoints, permission keys), and what they read and write. State exactly
      what must change for the v3 registry: ranges, the three list fields,
      derived fields, retired fields hidden.
  1b. What scoring or comparison code exists (the B1 harness, list comparison
      from notefields) that the scorer in Task 5 can reuse.
  1c. How a cohort is built, fetched and classified today (services/
      edgar_cohorts.py, edgar_pipeline.py), and where document_kind and the
      issuer credit group come from, so the sampler reuses them.
  1d. Which trap signals can be detected from fetched text with rules alone
      (worst-of, daily/continuous barrier, issuer call, digital/fixed payout,
      buffer vs barrier, price-return underlying, "up to" commission, a named
      distribution agent, a hypothetical-example table). Report the detector
      for each and any that need the pre-fill readings instead.

=== TASK 2: REPO MIGRATION FOR THE LIVE INDEX DROPS ===
Add a migration that does DROP INDEX IF EXISTS for the two indexes above, so the
repo matches the live database. Idempotent; it is already applied live.

=== TASK 3: THE GOLD SAMPLER ===
apps/api/scripts/build_gold_set.py, three steps, each a separate command and
each safe to re-run:
  --plan --size 300 --name "<name>"
      Build an edgar cohort stratified by bank (the seven above) x era
      (2019-20, 2021-22, 2023-24, 2025-26) — 28 strata, equal allocation,
      stratum recorded on each member, random but seeded (--seed, recorded in
      the cohort definition). Only selected 424B2 filings; deduplicated by
      accession. Prints the plan; writes the cohort. The operator then fetches
      it with the existing pipeline.
  --propose <cohort_id> --target 50 --batch "<sample_batch>"
      From that cohort's FETCHED filings with document_kind pricing_supplement
      only, propose note_gold_candidates: at least 6 per bank, at least 1 per
      era per bank where available, and at least 3 notes for each trap tag in
      1d where available. Record issuer_group, filing_year, product_type and
      trap_tags. Report shortfalls per stratum and per trap tag rather than
      padding. Never propose a filing already in another gold batch.
  --report <sample_batch>
      Coverage table: bank x era, trap tags, product types, review status.

=== TASK 4: PRE-FILL AND THE REVIEW SCREEN ===
  - apps/api/scripts/prefill_gold.py --batch <sample_batch> --model gpt-5-mini
    --max-tokens 32000 --spend-cap <usd> [--dry-run]: one extraction run per
    batch (add run_kind 'gold_prefill' to the CHECK in this sprint's
    migration), using the existing reader + rules + derivations + self-checks
    on the v3 registry. Readings carry run_id. Respects public_data_only and the
    unpriced-model guard. Dry-run prints notes, estimated tokens and cost.
  - Review screen (extend the B1 gold screen; super-admin only, the same
    permission it uses today): one note at a time. Left: the document text with
    the selected field's quote highlighted. Right: fields grouped by section,
    critical and economics/distribution fields first and expanded, the rest
    collapsed. Each field shows the pre-filled value, its quote, and whether it
    came from rules, the model, or a derivation. Actions per field: Confirm,
    Correct (edit value; for a range, min/max; for a list, edit rows), Absent.
    Keyboard: J/K next/previous field, C confirm, A absent, E edit, N next note.
    No "confirm all" — each field is a deliberate decision. Progress: fields
    done / total for the note and notes done / total for the batch. Saving
    writes note_gold_values (source_reading_id set when confirming a reading)
    and moves the candidate proposed -> in_review -> done; Skip sets skipped
    with a reason.
  - A corrected value supersedes the old one by valid_to / valid_from, never by
    update in place.
  - Anchoring note, shown on the screen and in PROJECT_STATUS: pre-fill comes
    from gpt-5-mini, so gpt-5-mini's own scores against this set are biased
    upward and must be reported with that caveat.

=== TASK 5: THE SCORER ===
apps/api/scripts/score_against_gold.py --run <run_id> [--batch <sample_batch>]
[--fields critical|core|all]: compares a run's resolved values with the current
gold values (valid_to IS NULL) on the notes both cover. Per field: correct,
wrong, missed (gold has a value, run has none), false (gold absent, run has a
value), accuracy. Ranges compare min and max; lists use the notefields list
comparison. Normalisation is the same as the cascade's. Prints a per-field
table, the critical-field accuracy, and the gate result: PASS when every
critical field is at or above 95% on at least 40 reviewed notes, otherwise the
fields below the gate. Writes the report to the run's report jsonb. Never
scores a field with fewer than 10 reviewed notes; reports it as "too few".

=== TASK 6: STATUS ===
Update docs/PROJECT_STATUS.md: the gold workflow (plan -> fetch -> propose ->
pre-fill -> review -> score), the anchoring caveat, the index-drop migration.

=== VERIFICATION: apps/api/scripts/verify_goldset.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from Doppler
over HTTPS at startup. Mock all model calls and all EDGAR/R2 fetches; real spend
$0. Wrap setup, teardown and every section so an exception is a [FAIL], never a
crash; teardown at start and end, deleting in FK child-before-parent order read
from pg_constraint, then re-reading every touched table to confirm the exact
pre-test count. Any table with an immutability trigger (note_term_readings):
disable/re-enable around the scoped delete, and fail loudly if rows remain.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; the helper asserts
    isinstance(passed, bool).
  - EVERY fixture value must satisfy the table's live CHECK and FK constraints —
    read them from pg_constraint before writing fixtures. Never invent a status.
  - Read pg_constraint.contype (and any "char" catalog column) as ::text.
  - Never derive a unique value from a slice of a fixture UUID.
  - Set RLS context on EVERY read; a read without it returns empty silently.
  - Expected counts derived from what the test created, never hard-coded; real
    samples deduplicated by accession and excluding fixtures.
  - An empty list must never make a check pass: assert non-emptiness first.
  - Every reading the test writes carries the test's run_id (the notefields
    verify leaked 211 readings with run_id NULL).

Assertions:
  [Y] Task 1's four findings reported
  [Y] The index-drop migration exists and is idempotent (runs twice cleanly)
  [Y] --plan on fixture filings yields 28 strata with equal allocation, the
      seed recorded, no duplicate accession, only selected 424B2s; the same seed
      gives the same cohort
  [Y] --propose uses only fetched final pricing supplements, meets the per-bank
      and per-era minimums on a fixture pool that allows it, reports (not pads)
      a shortfall on one that doesn't, and never re-proposes a filing already in
      another batch
  [Y] Each trap detector fires on a crafted snippet and stays quiet on a clean one
  [Y] Pre-fill (mocked) writes readings with run_id under run_kind
      'gold_prefill'; a flagged public_data_only model is accepted here; a model
      with no price and no manual price is refused; dry-run writes nothing
  [Y] Review API: confirm, correct (scalar, range, list) and absent each write
      the right note_gold_values row; absent stores NULL; a correction
      supersedes via valid_to/valid_from and the old row is still readable
  [Y] Permission gate on the identical request: an org admin is refused, the
      super-admin is admitted; the refusal leaves note_gold_values unchanged
  [Y] Candidate status moves proposed -> in_review -> done; skip records a reason
  [Y] Scorer on fixtures: correct / wrong / missed / false counted exactly;
      range and reordered-list agreement scored as correct; a field with fewer
      than 10 notes is "too few"; the 95% gate passes and fails on crafted sets
  [Y] Regression: verify_notefields.py run ONCE as a flat subprocess (180s
      timeout) still reports 0 FAIL
  [Y] securities_global_note_terms row count unchanged
  [Y] Teardown leaves zero fixture rows and every touched table at its pre-test
      count
