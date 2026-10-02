-- edgarindex — the four EDGAR discovery objects that were created BY HAND via
-- MCP before edgarpipelinea.structural, recorded here so the repo matches the
-- database.
--
-- Generated from the live database on 2026-10-01 (pg_get_constraintdef,
-- pg_get_indexdef, pg_get_viewdef, pg_policies, relacl), NOT written from
-- memory. This is the state BEFORE edgarpipelinea; that sprint's changes
-- (lifecycle CHECK, new columns, new tables) are in
-- edgarpipelinea_lifecycle.sql, which is applied on top of this file.
--
--   portfolio.edgar_index_filings        one row per unique 424B2/FWP filing
--   portfolio.edgar_index_filing_filers  every company listed on each filing
--   portfolio.structured_note_issuers    33 issuer/guarantor filers (seeded by hand)
--   portfolio.v_edgar_filings_explorer   read-only browsing view
--
-- RLS: four policies per table, copied from portfolio.reference_filings —
-- SELECT USING (true); INSERT/UPDATE/DELETE gated on app.is_super_admin. CIK is
-- unpadded text. The tables are GLOBAL (public SEC data, no org_id).
--
-- The 33 structured_note_issuers rows and the 716,953 manifest rows are data,
-- not schema; they were loaded by scripts/load_edgar_index.py and by hand and
-- are not reproduced here.

BEGIN;

-- ── edgar_index_filings ────────────────────────────────────────────────────
CREATE TABLE portfolio.edgar_index_filings (
    accession_number text NOT NULL,
    form_type text NOT NULL,
    filing_date date NOT NULL,
    index_quarter text NOT NULL,
    submission_path text NOT NULL,
    filer_count integer NOT NULL,
    pipeline_status text NOT NULL DEFAULT 'discovered'::text,
    status_reason text,
    loaded_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT edgar_index_filings_pkey PRIMARY KEY (accession_number),
    CONSTRAINT edgar_index_filings_accession_format_chk
        CHECK ((accession_number ~ '^\d{10}-\d{2}-\d{6}$'::text)),
    CONSTRAINT edgar_index_filings_filer_count_chk CHECK ((filer_count >= 1)),
    CONSTRAINT edgar_index_filings_quarter_format_chk
        CHECK ((index_quarter ~ '^\d{4}Q[1-4]$'::text)),
    CONSTRAINT edgar_index_filings_status_chk
        CHECK ((pipeline_status = 'discovered'::text))
);
CREATE INDEX edgar_index_filings_date_idx
    ON portfolio.edgar_index_filings USING btree (filing_date);
CREATE INDEX edgar_index_filings_status_idx
    ON portfolio.edgar_index_filings USING btree (pipeline_status, filing_date);
COMMENT ON TABLE portfolio.edgar_index_filings IS
    'One row per unique EDGAR filing (424B2 / FWP) from the quarterly master index. Discovery stage only: metadata, no documents. pipeline_status is extended by sprint A.';

-- ── edgar_index_filing_filers ──────────────────────────────────────────────
CREATE TABLE portfolio.edgar_index_filing_filers (
    accession_number text NOT NULL,
    cik text NOT NULL,
    company_name text NOT NULL,
    CONSTRAINT edgar_index_filing_filers_pkey PRIMARY KEY (accession_number, cik),
    CONSTRAINT edgar_index_filing_filers_accession_number_fkey
        FOREIGN KEY (accession_number)
        REFERENCES portfolio.edgar_index_filings(accession_number) ON DELETE CASCADE,
    CONSTRAINT edgar_index_filing_filers_cik_format_chk CHECK ((cik ~ '^\d+$'::text))
);
CREATE INDEX edgar_index_filing_filers_cik_idx
    ON portfolio.edgar_index_filing_filers USING btree (cik);
COMMENT ON TABLE portfolio.edgar_index_filing_filers IS
    'Every company the index lists on a filing. A parent and its finance subsidiary are both listed on co-issued notes.';

-- ── structured_note_issuers ────────────────────────────────────────────────
CREATE TABLE portfolio.structured_note_issuers (
    filer_cik text NOT NULL,
    issuer_group text NOT NULL,
    filer_name text NOT NULL,
    filer_role text NOT NULL,
    credit_entity text NOT NULL,
    include_status text NOT NULL,
    index_rows_2024_2026 integer,
    notes text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    CONSTRAINT structured_note_issuers_pkey PRIMARY KEY (filer_cik),
    CONSTRAINT structured_note_issuers_cik_format_chk CHECK ((filer_cik ~ '^\d+$'::text)),
    CONSTRAINT structured_note_issuers_include_chk
        CHECK ((include_status = ANY (ARRAY['yes'::text, 'review'::text, 'no'::text]))),
    CONSTRAINT structured_note_issuers_role_chk
        CHECK ((filer_role = ANY (ARRAY['issuer'::text, 'guarantor'::text])))
);
COMMENT ON TABLE portfolio.structured_note_issuers IS
    'Structured-note filers and the credit standing behind their notes. The mapping is a default; each note''s guarantor is confirmed from its own text at extraction.';

-- ── RLS: four policies per table ───────────────────────────────────────────
ALTER TABLE portfolio.edgar_index_filings ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio.edgar_index_filing_filers ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio.structured_note_issuers ENABLE ROW LEVEL SECURITY;

CREATE POLICY edgar_index_filings_global_read ON portfolio.edgar_index_filings
    FOR SELECT USING (true);
CREATE POLICY edgar_index_filings_super_admin_insert ON portfolio.edgar_index_filings
    FOR INSERT WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_index_filings_super_admin_update ON portfolio.edgar_index_filings
    FOR UPDATE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text))
    WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_index_filings_super_admin_delete ON portfolio.edgar_index_filings
    FOR DELETE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text));

CREATE POLICY edgar_index_filing_filers_global_read ON portfolio.edgar_index_filing_filers
    FOR SELECT USING (true);
CREATE POLICY edgar_index_filing_filers_super_admin_insert ON portfolio.edgar_index_filing_filers
    FOR INSERT WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_index_filing_filers_super_admin_update ON portfolio.edgar_index_filing_filers
    FOR UPDATE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text))
    WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_index_filing_filers_super_admin_delete ON portfolio.edgar_index_filing_filers
    FOR DELETE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text));

CREATE POLICY structured_note_issuers_global_read ON portfolio.structured_note_issuers
    FOR SELECT USING (true);
CREATE POLICY structured_note_issuers_super_admin_insert ON portfolio.structured_note_issuers
    FOR INSERT WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY structured_note_issuers_super_admin_update ON portfolio.structured_note_issuers
    FOR UPDATE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text))
    WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY structured_note_issuers_super_admin_delete ON portfolio.structured_note_issuers
    FOR DELETE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text));

GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.edgar_index_filings TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.edgar_index_filing_filers TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.structured_note_issuers TO app_service;

-- ── v_edgar_filings_explorer ───────────────────────────────────────────────
-- security_invoker = true: a view owned by postgres (rolbypassrls) would
-- otherwise read its base tables with RLS bypassed (the fee39 leak).
CREATE VIEW portfolio.v_edgar_filings_explorer WITH (security_invoker = true) AS
 SELECT f.accession_number,
    f.form_type,
    f.filing_date,
    f.index_quarter,
    f.pipeline_status,
    iss.issuer_group,
    iss.credit_entity,
    iss.include_status,
    f.filer_count,
    names.filer_names,
    (((('https://www.sec.gov/Archives/'::text || regexp_replace(f.submission_path, '[^/]+\.txt$'::text, ''::text)) || replace(f.accession_number, '-'::text, ''::text)) || '/'::text) || f.accession_number) || '-index.htm'::text AS sec_filing_url
   FROM portfolio.edgar_index_filings f
     LEFT JOIN LATERAL ( SELECT i.issuer_group,
            i.credit_entity,
            i.include_status
           FROM portfolio.edgar_index_filing_filers ff
             JOIN portfolio.structured_note_issuers i ON i.filer_cik = ff.cik
          WHERE ff.accession_number = f.accession_number
          ORDER BY (i.filer_role = 'issuer'::text) DESC, (i.include_status = 'yes'::text) DESC
         LIMIT 1) iss ON true
     LEFT JOIN LATERAL ( SELECT string_agg(ff.company_name, '; '::text ORDER BY ff.company_name) AS filer_names
           FROM portfolio.edgar_index_filing_filers ff
          WHERE ff.accession_number = f.accession_number) names ON true;
COMMENT ON VIEW portfolio.v_edgar_filings_explorer IS
    'Read-only browsing view: one row per filing with its issuer group, credit entity, all listed companies, and a link to the filing on sec.gov.';
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.v_edgar_filings_explorer TO app_service;

COMMIT;
