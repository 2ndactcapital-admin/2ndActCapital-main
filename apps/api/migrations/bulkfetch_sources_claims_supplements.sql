-- bulkfetch.structural — Part A: sources as configuration, bulk-runner claims,
-- product-supplement links. ADDITIVE ONLY (no DROP): CREATE TABLE / ADD COLUMN /
-- policies / one seed row. Global (non-org) tables, same RLS pattern as
-- portfolio.reference_filings and portfolio.edgar_index_filings: everyone may
-- read, only an app.is_super_admin = 'true' transaction may write — all four
-- operations get a policy (a table missing its UPDATE policy silently matches
-- zero rows; see CLAUDE.md "row not found is the symptom of TWO bugs").

-- ── 1. Sources: filers and form types are data, not code (Task 2) ──────────
CREATE TABLE IF NOT EXISTS portfolio.edgar_sources (
    name                 text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]*$'),
    description          text NOT NULL CHECK (btrim(description) <> ''),
    form_types           text[] NOT NULL CHECK (cardinality(form_types) > 0),
    -- where the filer list comes from: the structured-note issuer table
    -- (filtered by include_status), or an explicit CIK list on this row.
    filer_source         text NOT NULL CHECK (filer_source IN ('structured_note_issuers', 'explicit')),
    filer_include_status text[],
    filer_ciks           text[],
    -- a NAMED classifier from services.edgar_bulk.CLASSIFIERS (code is code;
    -- which classifier a source uses is data)
    classifier           text NOT NULL CHECK (btrim(classifier) <> ''),
    is_active            boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT edgar_sources_explicit_ciks_chk
        CHECK (filer_source <> 'explicit' OR COALESCE(cardinality(filer_ciks), 0) > 0),
    CONSTRAINT edgar_sources_issuer_status_chk
        CHECK (filer_source <> 'structured_note_issuers' OR COALESCE(cardinality(filer_include_status), 0) > 0)
);

ALTER TABLE portfolio.edgar_sources ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = 'edgar_sources'
                 AND policyname = 'edgar_sources_global_read') THEN
    CREATE POLICY edgar_sources_global_read ON portfolio.edgar_sources FOR SELECT USING (true);
    CREATE POLICY edgar_sources_super_admin_insert ON portfolio.edgar_sources FOR INSERT
        WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
    CREATE POLICY edgar_sources_super_admin_update ON portfolio.edgar_sources FOR UPDATE
        USING (current_setting('app.is_super_admin', true) = 'true')
        WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
    CREATE POLICY edgar_sources_super_admin_delete ON portfolio.edgar_sources FOR DELETE
        USING (current_setting('app.is_super_admin', true) = 'true');
  END IF;
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.edgar_sources TO app_service;

INSERT INTO portfolio.edgar_sources
    (name, description, form_types, filer_source, filer_include_status, classifier)
VALUES ('structured_notes',
        'Bank-issued structured notes: 424B2 pricing supplements from the structured-note issuer table '
        '(include_status yes or review), classified by the free cover-page rules.',
        ARRAY['424B2'], 'structured_note_issuers', ARRAY['yes', 'review'], 'structured_note_cover')
ON CONFLICT (name) DO NOTHING;

-- ── 2. Bulk-runner claims on the manifest (Task 4: resumable, two at once) ─
-- A claim is a lease on ONE row: claimed_by = the run, claimed_until = when it
-- lapses (a crashed runner's claims simply expire). The nightly queue skips
-- claimed rows too (services.edgar_pipeline._queue).
ALTER TABLE portfolio.edgar_index_filings ADD COLUMN IF NOT EXISTS claimed_by uuid;
ALTER TABLE portfolio.edgar_index_filings ADD COLUMN IF NOT EXISTS claimed_until timestamptz;
CREATE INDEX IF NOT EXISTS edgar_index_filings_claimed_idx
    ON portfolio.edgar_index_filings (claimed_until) WHERE claimed_until IS NOT NULL;

-- ── 3. Note -> product supplement links (Task 5) ───────────────────────────
-- One row per (note, supplement kind). The supplement itself, once fetched,
-- is an ordinary portfolio.reference_filings row; distinct supplements are
-- stored ONCE and linked from every note that references them.
CREATE TABLE IF NOT EXISTS portfolio.reference_filing_supplements (
    id                              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_filing_id             uuid NOT NULL REFERENCES portfolio.reference_filings(id) ON DELETE CASCADE,
    supplement_kind                 text NOT NULL DEFAULT 'product_supplement'
                                        CHECK (supplement_kind IN ('product_supplement')),
    resolution_method               text NOT NULL CHECK (resolution_method IN ('link', 'name_date', 'unresolved')),
    supplement_accession            text CHECK (supplement_accession IS NULL
                                                OR supplement_accession ~ '^\d{10}-\d{2}-\d{6}$'),
    supplement_cik                  text CHECK (supplement_cik IS NULL OR supplement_cik ~ '^\d+$'),
    supplement_document             text,
    supplement_url                  text,
    supplement_reference_filing_id  uuid REFERENCES portfolio.reference_filings(id) ON DELETE SET NULL,
    reference_text                  text,
    created_at                      timestamptz NOT NULL DEFAULT now(),
    updated_at                      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reference_filing_supplements_one_per_kind UNIQUE (reference_filing_id, supplement_kind),
    CONSTRAINT reference_filing_supplements_resolved_chk
        CHECK ((resolution_method = 'unresolved') = (supplement_accession IS NULL))
);
CREATE INDEX IF NOT EXISTS reference_filing_supplements_accession_idx
    ON portfolio.reference_filing_supplements (supplement_accession);

ALTER TABLE portfolio.reference_filing_supplements ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'portfolio'
                 AND tablename = 'reference_filing_supplements'
                 AND policyname = 'reference_filing_supplements_global_read') THEN
    CREATE POLICY reference_filing_supplements_global_read ON portfolio.reference_filing_supplements
        FOR SELECT USING (true);
    CREATE POLICY reference_filing_supplements_super_admin_insert ON portfolio.reference_filing_supplements
        FOR INSERT WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
    CREATE POLICY reference_filing_supplements_super_admin_update ON portfolio.reference_filing_supplements
        FOR UPDATE USING (current_setting('app.is_super_admin', true) = 'true')
        WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
    CREATE POLICY reference_filing_supplements_super_admin_delete ON portfolio.reference_filing_supplements
        FOR DELETE USING (current_setting('app.is_super_admin', true) = 'true');
  END IF;
END $$;
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.reference_filing_supplements TO app_service;
