PAYOFF DSL + VERSIONED NOTE-TERMS EXTENSION.
5 tasks + verification.

WHAT THIS IS: a constrained schema for structured-note payoff terms
(barriers, caps, autocall schedules) keyed to portfolio.securities_global,
plus the field registry that governs what can be extracted. This sprint
creates SCHEMA ONLY. No LLM extraction, no term-sheet parsing. That is
the next sprint (extraction + hazard fields), which depends on this one.

WHAT THIS IS NOT — DO NOT BUILD THESE:
  - Any extraction logic or LLM calls.
  - Any read of portfolio.reference_filings content — this sprint does
    not process filings, only defines where extracted terms WILL live.
  - A 1:1 extension table. Terms are VERSIONED — see Task 2. A note has
    an FWP terms row and a separate 424B2 terms row; they must coexist.
  - Any change to Chancery's document_field_corrections table. That is
    a separate sprint (corrections polymorphism) and is a PREREQUISITE
    for the extraction sprint, not this one.

STANDING RULES: org_id never from request body; Decimal for money (use
numeric in Postgres, Decimal in Python — never float, anywhere terms
touch a monetary or percentage value); RLS policy in the same migration
as the table; no hardcoded hex or brand strings; verify scripts are
pass/fail only.

THERE IS NO HUMAN AVAILABLE. Report discovery findings, then continue
immediately in the same response. If uncertain whether to continue,
continue. The exceptions are the explicit STOP/BLOCKED gates below.


=== TASK 1: DISCOVER — do not assume ===

Read the real current schema, report, THEN CONTINUE IMMEDIATELY.

  1a. Confirm the exact column list and constraints on
      portfolio.securities_global (security_type CHECK values,
      especially confirming 'structured_note' and 'index' are present).
      Do not assume this document's earlier description is current —
      read live.

  1b. Confirm portfolio.securities_global_relationships exists with
      link_state, raw_underlying_text, and the resolved-requires-target
      CHECK constraint. This sprint's note-terms rows will reference
      underlyings THROUGH this table, not with a direct FK — confirm
      that's still the right join path.

  1c. Report the bitemporal column convention used across the portfolio
      schema (valid_from/valid_to/system_from/system_to) and copy it
      EXACTLY. Do not invent a different convention.

  1d. Report the four-policy global RLS shape from
      portfolio.securities_global verbatim (policy names, USING/CHECK
      expressions). Copy it exactly for the new table.

  1e. Check whether anything already reads or writes a table named
      note_terms, payoff_terms, or similar, anywhere in apps/api. If
      found, STOP and report BLOCKED — this would mean prior undocumented
      work exists and needs reconciling before this sprint proceeds.


=== TASK 2: SCHEMA — versioned note-terms extension ===

Create `portfolio.securities_global_note_terms`.

THE VERSIONING DECISION (this was previously specified inconsistently
across design drafts — v6 said "1:1 extension table," v7 corrected this
to "versioned." VERSIONED IS CORRECT. Do not build 1:1.):

A single global_security_id can have MULTIPLE terms rows over its life:
  - Preliminary terms from an FWP filing
  - Final terms from the 424B2 that priced it
  - Occasionally a corrected/restated 424B2

These must NOT collapse into one row. The gap between offered and final
terms (e.g. a worse barrier at pricing than in the preliminary) is
itself a signal the comparison model is built to surface. Collapsing
it destroys the signal.

Columns:
  id                    uuid pk default uuid_generate_v4()
  global_security_id    uuid not null references portfolio.securities_global(id)
  reference_filing_id   uuid references portfolio.reference_filings(id)
  terms_status          text not null   -- 'preliminary' | 'final' | 'restated'

  product_archetype     text            -- 'buffered_note' | 'autocallable' |
                                         -- 'reverse_convertible' | 'digital' |
                                         -- 'principal_protected' | 'other'
  protection_type       text            -- 'buffer' | 'floor' | 'none'
                                         -- NOTE: buffer vs floor is THE field
                                         -- most likely to be misread. See
                                         -- registry note below.
  basket_type           text            -- 'single' | 'basket' | 'worst_of'
  return_basis          text            -- 'price' | 'total_return'
  is_decrement_index    boolean not null default false

  notional_currency     text
  protection_pct        numeric         -- buffer or floor percentage
  cap_pct                numeric
  participation_rate     numeric
  coupon_rate            numeric
  coupon_barrier_pct     numeric
  autocall_barrier_pct   numeric
  autocall_frequency     text           -- 'monthly'|'quarterly'|'annual'|'none'
  has_no_call_period     boolean
  no_call_months         integer
  initial_valuation_date date
  final_valuation_date   date
  tenor_years            numeric

  field_status           jsonb not null default '{}'::jsonb

  extraction_confidence  text            -- 'high' | 'needs_review' | 'low'
  source_char_start      integer         -- offset into reference_filings.extracted_text
  source_char_end        integer

  valid_from  timestamptz not null default now()
  valid_to    timestamptz
  system_from timestamptz not null default now()
  system_to   timestamptz

CHECK constraints:
  terms_status IN ('preliminary','final','restated')
  protection_type IN ('buffer','floor','none')
  basket_type IN ('single','basket','worst_of')
  return_basis IN ('price','total_return')
  extraction_confidence IN ('high','needs_review','low') OR extraction_confidence IS NULL

Unique (current rows only, per the A1 partial-unique pattern):
  (global_security_id, terms_status, reference_filing_id)
    WHERE system_to IS NULL AND valid_to IS NULL
  -- this is NOT unique on global_security_id alone — that would be the
  -- 1:1 mistake this task exists to avoid

Index:
  (global_security_id) WHERE system_to IS NULL AND valid_to IS NULL

RLS: copy the EXACT four-policy shape from Task 1d. Global read,
super-admin write. This table is public reference data extended from
public filings — same governance as reference_filings.


=== TASK 3: FIELD REGISTRY — the four-state model ===

Create `portfolio.note_terms_field_registry`:
  field_key            text primary key   -- e.g. 'protection_pct'
  display_label         text not null
  data_type             text not null      -- 'numeric'|'text'|'boolean'|'date'
  applies_to_archetypes text[]             -- which product_archetype values
                                           -- this field is meaningful for;
                                           -- NULL means applies to all
  hazard_field          boolean not null default false
                                           -- true for the ~6 fields where
                                           -- misreads are catastrophic and
                                           -- cheap-but-arithmetically-clean:
                                           -- protection_type (buffer/floor),
                                           -- basket_type (basket/worst_of),
                                           -- return_basis, is_decrement_index,
                                           -- autocall_frequency, terms_status
  created_at            timestamptz not null default now()

Seed it with rows for every column added in Task 2 (excluding audit/
bitemporal/id columns). Mark the six hazard fields listed above as
hazard_field = true.

WHY THIS EXISTS: a field can be inapplicable (no coupon_barrier_pct on
a principal-protected note), unresolved (not yet extracted), or wrong
— these are different facts and must not collapse to NULL. The
`field_status` jsonb column on note_terms carries this per-row, per-
field:
  {"protection_pct": "extracted", "coupon_rate": "not_applicable", ...}
Valid values per key: 'extracted' | 'not_applicable' | 'extraction_failed'
| 'not_in_template'. This is enforced at the APPLICATION layer this
sprint (document it in a docstring) — a jsonb CHECK constraint enforcing
per-key enum values is impractical in Postgres; note this limitation
explicitly rather than silently skipping validation.

RLS: same four-policy global shape.


=== TASK 4: PYTHON MODELS (no extraction logic) ===

apps/api/models/note_terms.py — dataclasses or Pydantic models mirroring
the schema. Decimal for every numeric field. Do NOT write extraction
functions — only the data shapes and a validate_field_status() helper
that checks a field_status dict's values against the four allowed
states and raises on anything else.


=== TASK 5: UPDATE PROJECT STATUS ===

Update docs/PROJECT_STATUS.md: tables created, the versioning decision
recorded explicitly (so it cannot drift back to 1:1 in a future sprint),
field registry seeded count.


=== VERIFICATION: apps/api/scripts/verify_notetermsdsl.py ===

Pass/fail only. No prompts. Idempotent. Teardown at start AND end.
Use APP_SERVICE_DATABASE_URL for RLS checks — confirm it connects
before relying on it (a prior sprint's RLS checks silently fell back
to SET ROLE when this credential was broken; do not repeat that
silently — if APP_SERVICE_DATABASE_URL fails to connect, FAIL this
verify script loudly rather than falling back).

  [ ] Both new tables exist, RLS enabled, exactly 4 policies each,
      policies are SELECT/INSERT/UPDATE/DELETE not FOR ALL
  [ ] NO org_id column on either table (assert absence)
  [ ] VERSIONING PROOF (the core assertion of this sprint): insert TWO
      note_terms rows for the SAME global_security_id — one
      terms_status='preliminary', one terms_status='final' — and assert
      BOTH persist as separate rows. This is the assertion that proves
      the 1:1 mistake was not made.
  [ ] Unique constraint rejects a duplicate (global_security_id,
      terms_status, reference_filing_id) — assert the rejection
  [ ] Unique constraint does NOT reject a second DIFFERENT terms_status
      for the same security (re-assert the versioning property from
      the other direction)
  [ ] field_status validator: valid four-state dict passes,
      a dict containing a fifth invalid state value raises
  [ ] Field registry has exactly 6 rows with hazard_field=true and
      their field_keys match the list in Task 3 exactly
  [ ] Global read works under app_service with no org context set
  [ ] NEGATIVE: insert under app_service without is_super_admin is
      rejected
  [ ] All numeric term columns are Postgres `numeric` type, not float
      or double precision (query information_schema.columns and assert)
