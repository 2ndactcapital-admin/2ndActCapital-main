# Gold set — Task 1 discovery findings (goldset.structural, 2026-10-08)

Read from the code at the start of the sprint and from the live database
(`pg_constraint`, `pg_policies`, `pg_trigger`, counts). The verify
(`apps/api/scripts/verify_goldset.py`, section "Task 1") re-checks the facts
each finding rests on.

## 1a. The B1 gold review UI and API, and what had to change for v3

**What existed (noteextractb1).**

- API, `apps/api/routers/note_extraction_admin.py`, Super Admin only.
  The gate is `_require_super_admin` → `services.rbac.is_super_admin(load_principal(...))`.
  The permission envelope names `super_admin` for both read and write. There is no granular permission key.
  - `GET /admin/note-extraction/gold/candidates?status=` → `gold.list_candidates`. It reads
    `note_gold_candidates` joined to `reference_filings`. The response envelope's `editable` was `[]`.
  - `GET /admin/note-extraction/gold/notes/{id}` returns the TRIMMED text (`trim.trim`), every reading
    (`gold.readings_for`: every `note_term_readings` row except `__call__`), the current gold values, and
    `fields` (the registry specs).
  - `PUT /admin/note-extraction/gold/notes/{id}/fields/{key}` takes `{action, value, source_reading_id, notes}`
    with `extra='forbid'`, and writes through `gold.record_gold_value`. That function sets the
    transaction-local `app.gold_reviewer_id` the BEFORE trigger `note_gold_values_human_guard` demands. It
    closes the open row (valid_to) and inserts a new one, appends a `human` reading, and moves the candidate
    `proposed → in_review`.
- UI: `apps/web/app/admin/note-extraction/gold/page.js` → `components/admin/GoldReviewScreen.jsx`.
  - The screen is three columns: candidate list, trimmed text, and every field with every reading.
  - Each field has Confirm-per-reading, a single free-text Correct box, and Absent.
  - A list is corrected as raw JSON. A range is corrected as the string `min..max`.
  - The write gate is `lib/noteGoldGates.mjs::canWriteGold`, and it fails closed.
- Writes: `note_gold_values` (human-guarded; bi-temporal), `note_term_readings` ('human'), and
  `note_gold_candidates.status`.

**What had to change for the v3 registry.**

- Ranges: correct `min` / `max` / `bound` separately. They are stored canonically as `{min, max, bound}`,
  with `min <= max` validated (`RangeValue`). Before this change, `normalize()` accepted `min > max`.
- Lists: `underlyings`, `observation_schedule` and `distribution` are edited as ROWS. The member fields
  come from the server (the `LIST_MEMBER_MODELS` Pydantic models → `fields[].members`), not from raw JSON.
  An empty list is refused; the reviewer marks the field absent instead.
- Derived fields (`tenor_months`, `max_principal_loss_pct`): the reviewer sees that the value came from a
  derivation. The v3 spec carries `extraction_method`, and the pre-fill's `derived` reading is labelled.
- Retired fields: `build_field_specs` already skips retired rows. The B1 note view still returned readings
  and gold values on retired keys, so they are now filtered to live keys. A PUT on a retired key is a 422.
- Missing for the new workflow: there was no batch filter, no per-note or per-batch progress, no `done`
  transition, and no skip with a reason (the table has no reason column). The quote highlight against the
  document text and the pre-fill reading per field (by run) were also missing. All of these were added.

## 1b. Scoring and comparison code the scorer reuses

- `services/note_extraction/schema.py::normalize(spec, value)` is the cascade's own normalisation:
  - numbers are quantised to 4 dp;
  - dates are ISO;
  - enums are lower-cased and vocabulary-checked;
  - ranges become `"min..max"` (bound ignored);
  - lists become `normalize_list`: members are validated, each member field is normalised like a scalar,
    and members are sorted by identity, so a reordered list agrees.
  `lists_agree` wraps it. The scorer calls `normalize` for every comparison and has no second comparator.
- `services/note_extraction/metrics.py::field_metrics` is the B1 harness. It counts accuracy, null rate and
  invented rate per candidate model over gold pairs. It has no wrong / missed / false split, no per-field
  note minimum and no gate, so the scorer (`scoring.py`) is new code built on `normalize`. The harness is
  unchanged.
- `scripts/run_note_extraction_eval.py::load_gold` reads current gold (`valid_to IS NULL`). The scorer's
  loader does the same, under `platform_scope`.

## 1c. How a cohort is built, fetched and classified

- `services/edgar_cohorts.py`:
  - definitions are normalised;
  - `build_members` runs a seeded `hashtextextended(accession, seed)` order per stratum;
  - `_insert_frozen` inserts the cohort, its members (via `unnest` — RLS refuses COPY) and the seal in one
    `platform_scope` transaction.
  - Triggers freeze the cohort afterwards.
  - Its built-in eras are 2019-2021 / 2022-2023 / 2024-2026. These are NOT the gold set's four two-year
    eras, so the gold plan builds its own stratified member list and reuses `_insert_frozen` with
    `kind='custom'`. The definition records `source: 'gold_plan'`, the banks, the eras, the per-stratum
    count and the seed.
- Fetch: `edgar_pipeline.launch_pipeline_run(cohort_id=...)` → `_cohort_queue` takes members whose status
  is in `('discovered','selected','not_selected')`. `fetch_one` stores the HTML in R2 and sets
  `pipeline_status`, `document_kind` and `reference_filing_id`.
- `document_kind` comes from `edgar_pipeline.classify_document` (title phrase in the opening text;
  a preliminary marker wins). `decide_status` turns a final `pricing_supplement` into
  `ready_for_extraction`, or into `prefilter_skipped` when the keyword prefilter fails.
- Live 424B2 counts at sprint time:
  - 537,335 `selected`;
  - 146 `ready_for_extraction` + 67 `prefilter_skipped` (final pricing supplements);
  - 158 preliminary, 24 + 3 other, 8 product supplement, 2 term sheet.
- The issuer credit group is `structured_note_issuers.issuer_group`, joined on
  `edgar_index_filings.primary_issuer_cik = structured_note_issuers.filer_cik` (one issuer per filing).
  All seven banks exist under exactly the names JPMorgan, Morgan Stanley, UBS, Goldman Sachs, Citigroup,
  Bank of America, Barclays.
- "Selected" means: the active selection policy chose the row, so `selection_policy_version IS NOT NULL`
  and the status is past `discovered` / `not_selected`.
- `note_gold_candidates.reference_filing_id` is UNIQUE, so the database itself refuses a filing in two
  batches.

## 1d. Trap detectors (`services/note_extraction/traps.py`)

All nine are rules over the fetched text; each fires on a crafted snippet and stays quiet on a clean one
(verify).

| tag | detector |
|---|---|
| `worst_of` | "worst-of", "worst / least / lowest performing" |
| `daily_or_continuous_barrier` | "any (scheduled) trading day during", "daily closing / observation", "at any time during", intraday, continuous monitoring — **proxy**: that the wording monitors the BARRIER needs the pre-fill's `barrier_observation` |
| `issuer_call` | "redeem / call the notes … at our option / in our sole discretion", "optional (early) redemption", "issuer call", "callable at the option of the issuer" |
| `digital_fixed_payout` | "digital", "fixed payment / return / payout amount", "contingent (minimum) return", "digital return" |
| `buffer_vs_barrier` | both "buffer" and "barrier" stated, or an ambiguous protection term (downside threshold, threshold / trigger level, knock-in, protection level) |
| `price_return_underlying` | "price return", "does not reflect / include dividends", "without dividends" |
| `up_to_commission` | a commission / underwriting discount / selling concession word within one sentence of "up to / not more than / will not exceed / a maximum of" + an amount |
| `named_distribution_agent` | "distribution / placement / selling agent", "acting as dealer", "will act as dealer / distributor", or a named platform (iCapital, InspereX, Incapital, Halo, SIMON) — **proxy**: that the agent is UNAFFILIATED with the issuer needs the pre-fill's `distribution` list (role `distribution_agent`) |
| `hypothetical_example_table` | the rules engine's own `find_hypothetical_rows` finds at least two rows (percent + $ amount after a "hypothetical" heading) |

The B1 sampler's broader keyword tags (`gold.TRAP_PATTERNS`, used by `sample_gold_set.py`) are unchanged.
