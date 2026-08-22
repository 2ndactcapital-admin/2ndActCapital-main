-- ─────────────────────────────────────────────────────────────────────────
-- Payoff DSL — Part 1 SQL — versioned note-terms extension + field registry
-- ─────────────────────────────────────────────────────────────────────────
-- SCHEMA ONLY. No extraction logic ships with this migration and nothing here
-- reads portfolio.reference_filings.extracted_text. This defines WHERE
-- extracted structured-note terms will live; the extraction sprint fills it.
--
-- THE VERSIONING DECISION (recorded here so it cannot drift back):
--   securities_global_note_terms is NOT a 1:1 extension of securities_global.
--   One global_security_id legitimately has MANY terms rows over its life:
--     - preliminary terms from the FWP
--     - final terms from the 424B2 that priced it
--     - occasionally a restated/corrected 424B2
--   The DELTA between preliminary and final (a barrier that got worse at
--   pricing, a cap that shrank) is itself the signal the comparison model
--   exists to surface. Collapsing these into one row destroys it. Hence the
--   uniqueness key is (global_security_id, terms_status, reference_filing_id)
--   among current rows — deliberately NOT unique on global_security_id.
--
-- Both tables are GLOBAL public reference data derived from public SEC
-- filings: NO org_id column, and the same four-policy RLS shape as
-- portfolio.reference_filings / portfolio.securities_global (global read,
-- super-admin write). Four separate policies, never a single FOR ALL.
--
-- Underlyings are NOT referenced from these tables. A note's underlyings hang
-- off portfolio.securities_global_relationships (relationship_type
-- 'underlying_of', with link_state resolved/unresolved/ambiguous). A worst-of
-- basket has N underlyings and some may never resolve to a security row, so a
-- direct FK could not represent it.
--
-- Every monetary or percentage value is `numeric` — never float/double
-- precision, anywhere terms touch money. Python reads these as Decimal.
-- ─────────────────────────────────────────────────────────────────────────


-- ═══════════════════════════════════════════════════════════════════════════
-- TABLE 1 — portfolio.securities_global_note_terms
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS portfolio.securities_global_note_terms (
    id                     uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    global_security_id     uuid NOT NULL REFERENCES portfolio.securities_global(id),
    reference_filing_id    uuid REFERENCES portfolio.reference_filings(id),

    -- Which filing generation these terms came from. HAZARD FIELD: reading a
    -- preliminary FWP as 'final' silently presents indicative terms as priced
    -- terms.
    terms_status           text NOT NULL,

    -- ── Classification ────────────────────────────────────────────────────
    product_archetype      text,   -- buffered_note|autocallable|reverse_convertible|
                                   -- digital|principal_protected|other

    -- HAZARD FIELD. buffer vs floor is the single most-misread term in a
    -- structured note. A 10% BUFFER absorbs the first 10% of loss (investor
    -- loses nothing until -10%). A 90% FLOOR caps the loss at 10% (investor
    -- takes losses immediately, but no worse than -10%). Same two numbers,
    -- opposite payoffs. Both are commonly worded as "10% downside
    -- protection" in marketing copy.
    protection_type        text,   -- buffer|floor|none

    -- HAZARD FIELD. 'basket' (weighted average of underlyings) vs 'worst_of'
    -- (payoff tracks the single worst performer) are arithmetically clean to
    -- confuse and wildly different in risk.
    basket_type            text,   -- single|basket|worst_of

    -- HAZARD FIELD. A price-return underlying strips dividends; total-return
    -- keeps them. Materially changes expected payoff over a multi-year tenor.
    return_basis           text,   -- price|total_return

    -- HAZARD FIELD. A decrement index subtracts a fixed annual drag (e.g.
    -- 5%/yr) from the index level. Missing this makes the note look far more
    -- attractive than it is. NOT NULL DEFAULT false so an unextracted note is
    -- explicitly "not a decrement index" rather than an ambiguous NULL —
    -- field_status carries whether that was actually verified.
    is_decrement_index     boolean NOT NULL DEFAULT false,

    -- ── Economic terms (all numeric — Decimal in Python, never float) ─────
    notional_currency      text,
    protection_pct         numeric,  -- buffer OR floor percentage; meaning is
                                     -- determined by protection_type above
    cap_pct                numeric,
    participation_rate     numeric,
    coupon_rate            numeric,
    coupon_barrier_pct     numeric,
    autocall_barrier_pct   numeric,

    -- HAZARD FIELD. Observation frequency drives the number of autocall
    -- chances; monthly vs annual is a large difference in expected life.
    autocall_frequency     text,     -- monthly|quarterly|annual|none

    has_no_call_period     boolean,
    no_call_months         integer,
    initial_valuation_date date,
    final_valuation_date   date,
    tenor_years            numeric,

    -- ── Per-field extraction state (the four-state model) ─────────────────
    -- Maps field_key -> one of: extracted | not_applicable |
    -- extraction_failed | not_in_template.
    --
    -- LIMITATION, STATED EXPLICITLY RATHER THAN SILENTLY SKIPPED: Postgres
    -- cannot practically CHECK per-key enum values inside a jsonb column. A
    -- constraint would need to iterate the object's values, which is not
    -- IMMUTABLE-safe in a CHECK. This is therefore enforced at the
    -- APPLICATION layer by models.note_terms.validate_field_status(), which
    -- every writer must call. The database will accept an invalid state
    -- string here; the application is the gate.
    field_status           jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- ── Extraction provenance ─────────────────────────────────────────────
    extraction_confidence  text,     -- high|needs_review|low
    source_char_start      integer,  -- offset into reference_filings.extracted_text
    source_char_end        integer,

    -- ── Bitemporal — exact convention from portfolio.securities_global ────
    valid_from             timestamptz NOT NULL DEFAULT now(),
    valid_to               timestamptz,
    system_from            timestamptz NOT NULL DEFAULT now(),
    system_to              timestamptz,

    CONSTRAINT sec_global_note_terms_status_chk
        CHECK (terms_status IN ('preliminary', 'final', 'restated')),
    CONSTRAINT sec_global_note_terms_archetype_chk
        CHECK (product_archetype IS NULL OR product_archetype IN (
            'buffered_note', 'autocallable', 'reverse_convertible',
            'digital', 'principal_protected', 'other')),
    CONSTRAINT sec_global_note_terms_protection_chk
        CHECK (protection_type IS NULL OR protection_type IN ('buffer', 'floor', 'none')),
    CONSTRAINT sec_global_note_terms_basket_chk
        CHECK (basket_type IS NULL OR basket_type IN ('single', 'basket', 'worst_of')),
    CONSTRAINT sec_global_note_terms_return_basis_chk
        CHECK (return_basis IS NULL OR return_basis IN ('price', 'total_return')),
    CONSTRAINT sec_global_note_terms_autocall_freq_chk
        CHECK (autocall_frequency IS NULL OR autocall_frequency IN (
            'monthly', 'quarterly', 'annual', 'none')),
    CONSTRAINT sec_global_note_terms_confidence_chk
        CHECK (extraction_confidence IS NULL
               OR extraction_confidence IN ('high', 'needs_review', 'low'))
);

-- Current-rows-only uniqueness, per the A1 partial-unique pattern.
--
-- NOT unique on global_security_id alone — that is precisely the 1:1 mistake
-- this table exists to avoid. A security may hold a 'preliminary' row and a
-- 'final' row simultaneously, each pointing at its own filing.
--
-- NULLS NOT DISTINCT (PG15+; server is 17.6) so that two current rows with the
-- same (security, status) and NO reference_filing_id still collide. Without it
-- NULL filing ids compare as distinct and un-sourced duplicates slip through.
CREATE UNIQUE INDEX IF NOT EXISTS sec_global_note_terms_current_unique
    ON portfolio.securities_global_note_terms
       (global_security_id, terms_status, reference_filing_id)
    NULLS NOT DISTINCT
    WHERE system_to IS NULL AND valid_to IS NULL;

CREATE INDEX IF NOT EXISTS sec_global_note_terms_current_security_idx
    ON portfolio.securities_global_note_terms (global_security_id)
    WHERE system_to IS NULL AND valid_to IS NULL;

CREATE INDEX IF NOT EXISTS sec_global_note_terms_filing_idx
    ON portfolio.securities_global_note_terms (reference_filing_id)
    WHERE reference_filing_id IS NOT NULL;

-- ── RLS — four-policy global shape, copied verbatim from
--    portfolio.securities_global (Task 1d). Global read, super-admin write.
--    Four separate policies, NOT one FOR ALL. ─────────────────────────────
ALTER TABLE portfolio.securities_global_note_terms ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS securities_global_note_terms_global_read
    ON portfolio.securities_global_note_terms;
DROP POLICY IF EXISTS securities_global_note_terms_super_admin_insert
    ON portfolio.securities_global_note_terms;
DROP POLICY IF EXISTS securities_global_note_terms_super_admin_update
    ON portfolio.securities_global_note_terms;
DROP POLICY IF EXISTS securities_global_note_terms_super_admin_delete
    ON portfolio.securities_global_note_terms;

CREATE POLICY securities_global_note_terms_global_read
    ON portfolio.securities_global_note_terms
    FOR SELECT
    USING (true);

CREATE POLICY securities_global_note_terms_super_admin_insert
    ON portfolio.securities_global_note_terms
    FOR INSERT
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY securities_global_note_terms_super_admin_update
    ON portfolio.securities_global_note_terms
    FOR UPDATE
    USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY securities_global_note_terms_super_admin_delete
    ON portfolio.securities_global_note_terms
    FOR DELETE
    USING (current_setting('app.is_super_admin', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE
    ON portfolio.securities_global_note_terms TO app_service;


-- ═══════════════════════════════════════════════════════════════════════════
-- TABLE 2 — portfolio.note_terms_field_registry
-- ═══════════════════════════════════════════════════════════════════════════
-- Governs WHAT may be extracted into securities_global_note_terms, and which
-- fields are dangerous enough to warrant separate treatment.
--
-- WHY THIS EXISTS: a NULL in a terms row is three different facts wearing one
-- hat. coupon_barrier_pct is NULL on a principal-protected note because the
-- field is INAPPLICABLE. It is NULL on an autocallable we haven't processed
-- because it is UNRESOLVED. It is NULL on a note whose barrier table defeated
-- the parser because extraction FAILED. Those are not the same and must not
-- collapse. applies_to_archetypes answers the first statically; the
-- field_status jsonb on each terms row answers the other two per-row.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS portfolio.note_terms_field_registry (
    field_key             text PRIMARY KEY,
    display_label         text NOT NULL,
    data_type             text NOT NULL,   -- numeric|text|boolean|date
    -- Which product_archetype values this field is meaningful for.
    -- NULL means it applies to ALL archetypes. An empty array would mean
    -- "applies to none" and is rejected.
    applies_to_archetypes text[],
    -- The ~6 fields where a misread is catastrophic AND arithmetically clean
    -- (the wrong answer looks just as plausible as the right one, so nothing
    -- downstream trips). The extraction sprint gives these fields their own
    -- verification path.
    hazard_field          boolean NOT NULL DEFAULT false,
    created_at            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT note_terms_field_registry_data_type_chk
        CHECK (data_type IN ('numeric', 'text', 'boolean', 'date')),
    CONSTRAINT note_terms_field_registry_archetypes_chk
        CHECK (applies_to_archetypes IS NULL
               OR cardinality(applies_to_archetypes) > 0)
);

CREATE INDEX IF NOT EXISTS note_terms_field_registry_hazard_idx
    ON portfolio.note_terms_field_registry (hazard_field)
    WHERE hazard_field;

-- ── RLS — identical four-policy global shape. ─────────────────────────────
ALTER TABLE portfolio.note_terms_field_registry ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS note_terms_field_registry_global_read
    ON portfolio.note_terms_field_registry;
DROP POLICY IF EXISTS note_terms_field_registry_super_admin_insert
    ON portfolio.note_terms_field_registry;
DROP POLICY IF EXISTS note_terms_field_registry_super_admin_update
    ON portfolio.note_terms_field_registry;
DROP POLICY IF EXISTS note_terms_field_registry_super_admin_delete
    ON portfolio.note_terms_field_registry;

CREATE POLICY note_terms_field_registry_global_read
    ON portfolio.note_terms_field_registry
    FOR SELECT
    USING (true);

CREATE POLICY note_terms_field_registry_super_admin_insert
    ON portfolio.note_terms_field_registry
    FOR INSERT
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY note_terms_field_registry_super_admin_update
    ON portfolio.note_terms_field_registry
    FOR UPDATE
    USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY note_terms_field_registry_super_admin_delete
    ON portfolio.note_terms_field_registry
    FOR DELETE
    USING (current_setting('app.is_super_admin', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE
    ON portfolio.note_terms_field_registry TO app_service;


-- ═══════════════════════════════════════════════════════════════════════════
-- SEED — one row per extractable term column on securities_global_note_terms
-- ═══════════════════════════════════════════════════════════════════════════
-- 19 rows. Deliberately EXCLUDED from the registry:
--   id, valid_from, valid_to, system_from, system_to  — bitemporal/audit
--   global_security_id, reference_filing_id           — id/linkage, not
--                                                       extracted from prose
--   field_status                                      — it IS the registry's
--                                                       per-row companion;
--                                                       registering it would
--                                                       be self-referential
--   extraction_confidence, source_char_start,
--   source_char_end                                   — extraction provenance
--                                                       about the row, not a
--                                                       term of the note
--
-- no_call_months is `integer` in the table but registers as data_type
-- 'numeric' — the registry's four types describe extraction/coercion shape,
-- and integer coerces through the same numeric path.
--
-- Idempotent: re-running updates the governing columns in place so the
-- registry converges on this file rather than silently drifting.

INSERT INTO portfolio.note_terms_field_registry
    (field_key, display_label, data_type, applies_to_archetypes, hazard_field)
VALUES
    -- ── HAZARD FIELDS (6) ────────────────────────────────────────────────
    ('terms_status', 'Terms Status', 'text', NULL, true),
    ('protection_type', 'Protection Type', 'text', NULL, true),
    ('basket_type', 'Basket Type', 'text', NULL, true),
    ('return_basis', 'Return Basis', 'text', NULL, true),
    ('is_decrement_index', 'Decrement Index', 'boolean', NULL, true),
    ('autocall_frequency', 'Autocall Frequency', 'text',
        ARRAY['autocallable'], true),

    -- ── Classification ───────────────────────────────────────────────────
    ('product_archetype', 'Product Archetype', 'text', NULL, false),

    -- ── Economics ────────────────────────────────────────────────────────
    ('notional_currency', 'Notional Currency', 'text', NULL, false),
    ('protection_pct', 'Protection %', 'numeric',
        ARRAY['buffered_note', 'autocallable', 'reverse_convertible',
              'digital', 'principal_protected'], false),
    ('cap_pct', 'Cap %', 'numeric',
        ARRAY['buffered_note', 'digital', 'principal_protected'], false),
    ('participation_rate', 'Participation Rate', 'numeric',
        ARRAY['buffered_note', 'digital', 'principal_protected'], false),
    ('coupon_rate', 'Coupon Rate', 'numeric',
        ARRAY['autocallable', 'reverse_convertible'], false),
    ('coupon_barrier_pct', 'Coupon Barrier %', 'numeric',
        ARRAY['autocallable', 'reverse_convertible'], false),
    ('autocall_barrier_pct', 'Autocall Barrier %', 'numeric',
        ARRAY['autocallable'], false),

    -- ── Call schedule ────────────────────────────────────────────────────
    ('has_no_call_period', 'Has No-Call Period', 'boolean',
        ARRAY['autocallable'], false),
    ('no_call_months', 'No-Call Period (Months)', 'numeric',
        ARRAY['autocallable'], false),

    -- ── Dates / tenor ────────────────────────────────────────────────────
    ('initial_valuation_date', 'Initial Valuation Date', 'date', NULL, false),
    ('final_valuation_date', 'Final Valuation Date', 'date', NULL, false),
    ('tenor_years', 'Tenor (Years)', 'numeric', NULL, false)
ON CONFLICT (field_key) DO UPDATE SET
    display_label         = EXCLUDED.display_label,
    data_type             = EXCLUDED.data_type,
    applies_to_archetypes = EXCLUDED.applies_to_archetypes,
    hazard_field          = EXCLUDED.hazard_field;
