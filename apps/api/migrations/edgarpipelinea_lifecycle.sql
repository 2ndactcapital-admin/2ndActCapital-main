-- edgarpipelinea.structural — status lifecycle, primary issuer, selection
-- policies, pipeline runs, the job lease, and the fetch-to-R2 columns.
--
-- Applied on top of edgarindex_discovery_tables.sql. Applied live via the
-- supabase-2ndact-dev MCP `apply_migration` tool, in the parts named below
-- (app_service has no CREATE on portfolio), so this file is the record of what
-- is deployed, not itself the apply path. The one-off DATA steps (backfill,
-- linking the 201 existing documents, policy v1, the VERIFY FIXTURE delete)
-- are at the end, marked DATA.
--
-- Every new table is GLOBAL (no org_id) and carries the same four RLS policies
-- as the discovery tables: SELECT USING (true); INSERT/UPDATE/DELETE gated on
-- app.is_super_admin. Writers set it with SET LOCAL inside a transaction
-- (services.database.platform_scope), never a session-level SET.

BEGIN;

-- ═══ Part 1: the manifest lifecycle ═════════════════════════════════════════

-- The FULL lifecycle, sprint B's statuses included, so sprint B never has to
-- alter this CHECK:
--   discovered -> selected | not_selected
--   selected -> fetched | fetch_failed
--   fetched -> not_pricing_supplement | prefilter_skipped | ready_for_extraction
--   (sprint B) ready_for_extraction -> extraction_submitted ->
--              extracted | needs_review | extraction_failed
-- 'fetched' is both a resting state (the 201 pre-existing documents, linked
-- without re-download, keep it until sprint B classifies them) and the
-- momentary state between download and classification.
ALTER TABLE portfolio.edgar_index_filings
    DROP CONSTRAINT edgar_index_filings_status_chk;
ALTER TABLE portfolio.edgar_index_filings
    ADD CONSTRAINT edgar_index_filings_status_chk CHECK (pipeline_status = ANY (ARRAY[
        'discovered', 'selected', 'not_selected',
        'fetched', 'fetch_failed',
        'not_pricing_supplement', 'prefilter_skipped', 'ready_for_extraction',
        'extraction_submitted', 'extracted', 'needs_review', 'extraction_failed'
    ]::text[]));

-- status_reason already exists (created by hand with the table).
ALTER TABLE portfolio.edgar_index_filings
    ADD COLUMN document_kind text,
    ADD COLUMN selection_policy_version integer,
    ADD COLUMN attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN last_attempt_at timestamptz,
    ADD COLUMN next_attempt_at timestamptz,
    ADD COLUMN reference_filing_id uuid,
    ADD COLUMN detected_cusip text,
    ADD COLUMN fetched_at timestamptz,
    ADD COLUMN primary_issuer_cik text;

ALTER TABLE portfolio.edgar_index_filings
    ADD CONSTRAINT edgar_index_filings_document_kind_chk CHECK (
        document_kind IS NULL OR document_kind = ANY (ARRAY[
            'pricing_supplement', 'preliminary_pricing_supplement',
            'product_supplement', 'underlying_supplement', 'term_sheet', 'other'
        ]::text[])),
    ADD CONSTRAINT edgar_index_filings_attempt_count_chk CHECK (attempt_count >= 0),
    -- Only a CUSIP whose check digit validated is ever stored; the shape check
    -- here is the backstop, the check-digit test lives in services/edgar_pipeline.
    ADD CONSTRAINT edgar_index_filings_cusip_format_chk CHECK (
        detected_cusip IS NULL OR detected_cusip ~ '^[0-9A-Z*@#]{8}[0-9]$'),
    ADD CONSTRAINT edgar_index_filings_reference_filing_fkey
        FOREIGN KEY (reference_filing_id)
        REFERENCES portfolio.reference_filings(id) ON DELETE SET NULL,
    -- A decided row (anything past 'discovered') names the policy that decided it.
    ADD CONSTRAINT edgar_index_filings_decided_has_policy_chk CHECK (
        pipeline_status = 'discovered'
        OR pipeline_status = 'fetched' AND reference_filing_id IS NOT NULL
        OR selection_policy_version IS NOT NULL);

-- Filtering by issuer group resolves to primary_issuer_cik = ANY(group's ciks),
-- so this index is what makes the filter fast on the full table.
CREATE INDEX edgar_index_filings_primary_issuer_idx
    ON portfolio.edgar_index_filings (primary_issuer_cik, filing_date DESC);
-- The fetch queue: newest selected first, and failed rows whose retry is due.
CREATE INDEX edgar_index_filings_queue_idx
    ON portfolio.edgar_index_filings (pipeline_status, filing_date DESC, accession_number DESC);
CREATE INDEX edgar_index_filings_quarter_status_idx
    ON portfolio.edgar_index_filings (index_quarter, pipeline_status);
CREATE INDEX edgar_index_filings_form_date_idx
    ON portfolio.edgar_index_filings (form_type, filing_date DESC);
-- Sort indexes for the monitoring grid (applied as a second MCP migration,
-- edgarpipelinea_sort_indexes). Each matches its ORDER BY exactly —
-- (category, filing_date DESC, accession DESC) forward, or the mirror image
-- backward — so a sorted page on the full ~717K-row table is an index walk,
-- not a sort. form_date_idx above is now a redundant prefix of form_sort_idx;
-- it was left in place because the MCP apply path cancels any DROP.
CREATE INDEX edgar_index_filings_form_sort_idx
    ON portfolio.edgar_index_filings (form_type, filing_date DESC, accession_number DESC);
CREATE INDEX edgar_index_filings_kind_sort_idx
    ON portfolio.edgar_index_filings (document_kind, filing_date DESC, accession_number DESC);
CREATE INDEX edgar_index_filings_quarter_sort_idx
    ON portfolio.edgar_index_filings (index_quarter, filing_date DESC, accession_number DESC);

-- ═══ Part 2: primary_issuer_cik ═════════════════════════════════════════════

-- The listed filer with role 'issuer' if one is listed, else any listed issuer
-- (i.e. a guarantor-role row), else NULL. Ties: include_status yes before
-- review before no, then the lowest CIK — deterministic, so a recompute that
-- changes nothing writes nothing.
CREATE FUNCTION portfolio.edgar_primary_issuer_cik(p_accession text)
RETURNS text
LANGUAGE sql STABLE
SET search_path TO 'pg_catalog', 'portfolio'
AS $$
    SELECT i.filer_cik
    FROM portfolio.edgar_index_filing_filers ff
    JOIN portfolio.structured_note_issuers i ON i.filer_cik = ff.cik
    WHERE ff.accession_number = p_accession
    ORDER BY (i.filer_role = 'issuer') DESC,
             CASE i.include_status WHEN 'yes' THEN 0 WHEN 'review' THEN 1 ELSE 2 END,
             length(i.filer_cik), i.filer_cik
    LIMIT 1
$$;

-- Recompute for every filing that LISTS any of p_ciks. Runs as the invoker, so
-- the UPDATE policy (app.is_super_admin) still applies — no SECURITY DEFINER.
CREATE FUNCTION portfolio.edgar_recompute_primary_issuer(p_ciks text[])
RETURNS integer
LANGUAGE plpgsql
SET search_path TO 'pg_catalog', 'portfolio'
AS $$
DECLARE
    n integer;
BEGIN
    UPDATE portfolio.edgar_index_filings f
       SET primary_issuer_cik = portfolio.edgar_primary_issuer_cik(f.accession_number)
     WHERE f.accession_number IN (
               SELECT DISTINCT ff.accession_number
               FROM portfolio.edgar_index_filing_filers ff
               WHERE ff.cik = ANY (p_ciks))
       AND f.primary_issuer_cik IS DISTINCT FROM
           portfolio.edgar_primary_issuer_cik(f.accession_number);
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;

-- Any change to the issuer table recomputes the filings that list the changed
-- CIKs — whoever makes the change (the admin screen, MCP, a script). One
-- trigger per event: transition tables cannot be shared across events.
CREATE FUNCTION portfolio.structured_note_issuers_recompute()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'pg_catalog', 'portfolio'
AS $$
DECLARE
    ciks text[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT array_agg(DISTINCT filer_cik) INTO ciks FROM new_rows;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT array_agg(DISTINCT filer_cik) INTO ciks FROM old_rows;
    ELSE
        SELECT array_agg(DISTINCT c) INTO ciks FROM (
            SELECT filer_cik AS c FROM new_rows
            UNION SELECT filer_cik FROM old_rows) u;
    END IF;
    IF ciks IS NOT NULL THEN
        PERFORM portfolio.edgar_recompute_primary_issuer(ciks);
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER structured_note_issuers_recompute_ins
    AFTER INSERT ON portfolio.structured_note_issuers
    REFERENCING NEW TABLE AS new_rows
    FOR EACH STATEMENT EXECUTE FUNCTION portfolio.structured_note_issuers_recompute();
CREATE TRIGGER structured_note_issuers_recompute_upd
    AFTER UPDATE ON portfolio.structured_note_issuers
    REFERENCING NEW TABLE AS new_rows OLD TABLE AS old_rows
    FOR EACH STATEMENT EXECUTE FUNCTION portfolio.structured_note_issuers_recompute();
CREATE TRIGGER structured_note_issuers_recompute_del
    AFTER DELETE ON portfolio.structured_note_issuers
    REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT EXECUTE FUNCTION portfolio.structured_note_issuers_recompute();

GRANT EXECUTE ON FUNCTION portfolio.edgar_primary_issuer_cik(text) TO app_service;
GRANT EXECUTE ON FUNCTION portfolio.edgar_recompute_primary_issuer(text[]) TO app_service;

-- ═══ Part 3: selection policies (versioned, immutable) ══════════════════════

-- Same pattern as public.ai_ensemble_configs: a new version is a new row; the
-- only permitted UPDATE is retiring an active row.
CREATE TABLE portfolio.edgar_selection_policies (
    version integer PRIMARY KEY CHECK (version >= 1),
    rules jsonb NOT NULL,
    description text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    CONSTRAINT edgar_selection_policies_retired_chk
        CHECK (is_active = (retired_at IS NULL))
);
CREATE UNIQUE INDEX edgar_selection_policies_one_active
    ON portfolio.edgar_selection_policies ((true)) WHERE is_active;

CREATE FUNCTION portfolio.edgar_selection_policies_retire_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO 'pg_catalog', 'portfolio'
AS $$
BEGIN
    IF OLD.is_active IS TRUE
       AND NEW.is_active IS FALSE
       AND NEW.retired_at IS NOT NULL
       AND (to_jsonb(NEW) - 'is_active' - 'retired_at')
           = (to_jsonb(OLD) - 'is_active' - 'retired_at')
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
        'edgar_selection_policies version % is immutable: the only permitted update is retiring an active row (is_active true->false with retired_at set). A new rule set is a new version.',
        OLD.version
        USING ERRCODE = 'check_violation',
              CONSTRAINT = 'edgar_selection_policies_retire_only';
END;
$$;
CREATE TRIGGER edgar_selection_policies_retire_only
    BEFORE UPDATE ON portfolio.edgar_selection_policies
    FOR EACH ROW EXECUTE FUNCTION portfolio.edgar_selection_policies_retire_only();

ALTER TABLE portfolio.edgar_index_filings
    ADD CONSTRAINT edgar_index_filings_selection_policy_fkey
        FOREIGN KEY (selection_policy_version)
        REFERENCES portfolio.edgar_selection_policies(version);

-- ═══ Part 4: pipeline runs + the job lease ══════════════════════════════════

CREATE TABLE portfolio.edgar_pipeline_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_source text NOT NULL CHECK (trigger_source = ANY (ARRAY[
        'nightly', 'manual', 'cli', 'verify']::text[])),
    requested_by uuid,
    workflow_run_id uuid,
    status text NOT NULL CHECK (status = ANY (ARRAY[
        'launching', 'launched', 'launch_failed', 'refused',
        'running', 'succeeded', 'failed']::text[])),
    stages text[] NOT NULL DEFAULT ARRAY['discover', 'select', 'fetch']::text[],
    fetch_cap integer NOT NULL CHECK (fetch_cap >= 0),
    runtime_cap_seconds integer NOT NULL CHECK (runtime_cap_seconds > 0),
    render_service_id text,
    render_job_id text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    discovery_days integer NOT NULL DEFAULT 0,
    discovered_new integer NOT NULL DEFAULT 0,
    selected integer NOT NULL DEFAULT 0,
    not_selected integer NOT NULL DEFAULT 0,
    fetch_attempted integer NOT NULL DEFAULT 0,
    fetched integer NOT NULL DEFAULT 0,
    fetch_failed integer NOT NULL DEFAULT 0,
    ready_for_extraction integer NOT NULL DEFAULT 0,
    not_pricing_supplement integer NOT NULL DEFAULT 0,
    prefilter_skipped integer NOT NULL DEFAULT 0,
    bytes_uploaded bigint NOT NULL DEFAULT 0,
    sec_requests integer NOT NULL DEFAULT 0,
    stop_reason text,
    error text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX edgar_pipeline_runs_requested_idx
    ON portfolio.edgar_pipeline_runs (requested_at DESC);

-- One row per lease name. A job holds it by writing its run id with an expiry
-- and renews it as it works; a crashed job's lease simply expires. Deliberately
-- NOT a session-level advisory lock: under the transaction pooler the session
-- can be handed to another client.
CREATE TABLE portfolio.edgar_pipeline_lease (
    lease_name text PRIMARY KEY,
    holder text NOT NULL,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    renewed_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

-- ═══ Part 5: fetch-to-R2 columns on the document store ══════════════════════

-- reference_filings stays the ONE store for fetched documents. New rows keep
-- the raw document gzipped in R2 (content_encoding 'gzip') and their text
-- gzipped in R2 at text_r2_key; extracted_text stays NULL for them (sprint B
-- decides what trimmed text belongs in the database). The 201 pre-existing
-- rows are 'identity' — raw HTML in R2, text in extracted_text.
ALTER TABLE portfolio.reference_filings
    ADD COLUMN content_encoding text NOT NULL DEFAULT 'identity',
    ADD COLUMN compressed_byte_size bigint,
    ADD COLUMN text_r2_key text,
    ADD COLUMN text_byte_size bigint,
    ADD COLUMN text_compressed_byte_size bigint;
ALTER TABLE portfolio.reference_filings
    ADD CONSTRAINT reference_filings_content_encoding_check
        CHECK (content_encoding = ANY (ARRAY['identity', 'gzip']::text[]));

-- ═══ Part 6: RLS + grants on the new tables ═════════════════════════════════

ALTER TABLE portfolio.edgar_selection_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio.edgar_pipeline_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio.edgar_pipeline_lease ENABLE ROW LEVEL SECURITY;

CREATE POLICY edgar_selection_policies_global_read ON portfolio.edgar_selection_policies
    FOR SELECT USING (true);
CREATE POLICY edgar_selection_policies_super_admin_insert ON portfolio.edgar_selection_policies
    FOR INSERT WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_selection_policies_super_admin_update ON portfolio.edgar_selection_policies
    FOR UPDATE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text))
    WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_selection_policies_super_admin_delete ON portfolio.edgar_selection_policies
    FOR DELETE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text));

CREATE POLICY edgar_pipeline_runs_global_read ON portfolio.edgar_pipeline_runs
    FOR SELECT USING (true);
CREATE POLICY edgar_pipeline_runs_super_admin_insert ON portfolio.edgar_pipeline_runs
    FOR INSERT WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_pipeline_runs_super_admin_update ON portfolio.edgar_pipeline_runs
    FOR UPDATE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text))
    WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_pipeline_runs_super_admin_delete ON portfolio.edgar_pipeline_runs
    FOR DELETE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text));

CREATE POLICY edgar_pipeline_lease_global_read ON portfolio.edgar_pipeline_lease
    FOR SELECT USING (true);
CREATE POLICY edgar_pipeline_lease_super_admin_insert ON portfolio.edgar_pipeline_lease
    FOR INSERT WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_pipeline_lease_super_admin_update ON portfolio.edgar_pipeline_lease
    FOR UPDATE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text))
    WITH CHECK ((current_setting('app.is_super_admin'::text, true) = 'true'::text));
CREATE POLICY edgar_pipeline_lease_super_admin_delete ON portfolio.edgar_pipeline_lease
    FOR DELETE USING ((current_setting('app.is_super_admin'::text, true) = 'true'::text));

GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.edgar_selection_policies TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.edgar_pipeline_runs TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.edgar_pipeline_lease TO app_service;

-- ═══ Part 7: the browsing view, now on primary_issuer_cik ═══════════════════

-- Same columns in the same order (CREATE OR REPLACE can only append), but the
-- per-row LATERAL issuer lookup becomes a join on the stored primary issuer —
-- the same rule (issuer role first, include 'yes' first), computed once.
CREATE OR REPLACE VIEW portfolio.v_edgar_filings_explorer WITH (security_invoker = true) AS
 SELECT f.accession_number,
    f.form_type,
    f.filing_date,
    f.index_quarter,
    f.pipeline_status,
    i.issuer_group,
    i.credit_entity,
    i.include_status,
    f.filer_count,
    names.filer_names,
    (((('https://www.sec.gov/Archives/'::text || regexp_replace(f.submission_path, '[^/]+\.txt$'::text, ''::text)) || replace(f.accession_number, '-'::text, ''::text)) || '/'::text) || f.accession_number) || '-index.htm'::text AS sec_filing_url,
    f.status_reason,
    f.document_kind,
    f.primary_issuer_cik,
    f.detected_cusip,
    f.reference_filing_id
   FROM portfolio.edgar_index_filings f
     LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
     LEFT JOIN LATERAL ( SELECT string_agg(ff.company_name, '; '::text ORDER BY ff.company_name) AS filer_names
           FROM portfolio.edgar_index_filing_filers ff
          WHERE ff.accession_number = f.accession_number) names ON true;

COMMIT;

-- ═══ DATA (one-off, applied after the schema) ═══════════════════════════════

-- Policy v1. Rules are data: the selection service reads them from this row.
INSERT INTO portfolio.edgar_selection_policies (version, rules, description)
VALUES (1,
    '{"form_types": ["424B2"],
      "issuer_include_status": ["yes", "review"],
      "filing_date_from": "2019-01-01",
      "order": "filing_date_desc"}'::jsonb,
    'v1: 424B2 only; primary issuer included (yes or review); filed 2019-01-01 or later; newest first.');

-- Backfill primary_issuer_cik, one quarter at a time (applied per quarter).
--   UPDATE portfolio.edgar_index_filings f
--      SET primary_issuer_cik = portfolio.edgar_primary_issuer_cik(f.accession_number)
--    WHERE f.index_quarter = '<YYYYQn>';

-- Link the pre-existing fetched documents to their manifest rows and mark them
-- fetched WITHOUT re-downloading. selection_policy_version stays NULL for these
-- rows: no policy decided them (the decided_has_policy CHECK allows exactly
-- this — 'fetched' with a reference_filing_id).
UPDATE portfolio.edgar_index_filings f
   SET pipeline_status = 'fetched',
       status_reason = 'pre-existing reference_filings document (2025Q1 corpus), linked without re-download',
       reference_filing_id = r.id,
       fetched_at = r.created_at
  FROM portfolio.reference_filings r
 WHERE r.accession_number = f.accession_number
   AND f.pipeline_status = 'discovered';

-- The leftover test record. Task 1e: nothing references it
-- (securities_global_note_terms has 0 rows pointing at it; it has no manifest
-- row; code only creates or filters it).
DELETE FROM portfolio.reference_filings
 WHERE filer_name = 'VERIFY FIXTURE' AND accession_number = '9999999999-99-999999';

-- The nightly workflow (DATA, applied via MCP 2026-10-01). Hollisworks platform
-- org, owned by its super_admin user (the engine's super-admin bypass covers
-- the action's required_permission). Start -> Service Task -> End: no User Task,
-- and the step's autonomy_tier is 2 — an effective tier of 1 would SUSPEND the
-- step for a human approval, which is a human step by another name.
--
-- The trigger is created INACTIVE: the deployed build does not yet register
-- edgar.launch_pipeline_job, and the engine resolves an unregistered key to a
-- silent no-op "completed" step. Activate it on /admin/workflows/triggers once
-- this branch is deployed. 06:00 UTC is after EDGAR publishes the previous
-- day's daily index.
WITH d AS (
  INSERT INTO workflow_definitions (org_id, name, description, created_by)
  VALUES ('bb347258-8f28-4f49-8cc9-e29ccad82884', 'EDGAR pipeline — nightly',
          'Launches one EDGAR pipeline job (incremental discovery, selection, fetch to R2) as a Render one-off job and returns immediately. Automated steps only: no human step, so a run never waits on a person and never blocks the next night.',
          'be8165db-84d7-4e69-91d3-62f603530824')
  RETURNING id),
v AS (
  INSERT INTO workflow_versions (workflow_definition_id, org_id, version_number, bpmn_xml, change_summary, is_current, created_by)
  SELECT d.id, 'bb347258-8f28-4f49-8cc9-e29ccad82884', 1,
   '<?xml version="1.0" encoding="UTF-8"?><bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:twoa="http://2ndactcapital.com/bpmn/ext" id="D_edgar_pipeline_nightly" targetNamespace="http://2ndactcapital.com/bpmn"><bpmn:process id="edgar_pipeline_nightly" isExecutable="true"><bpmn:startEvent id="edgar_start"><bpmn:outgoing>edgar_f1</bpmn:outgoing></bpmn:startEvent><bpmn:serviceTask id="edgar_launch_job" name="Launch EDGAR pipeline job"><bpmn:extensionElements><twoa:governance actionRegistryKey="edgar.launch_pipeline_job"/></bpmn:extensionElements><bpmn:incoming>edgar_f1</bpmn:incoming><bpmn:outgoing>edgar_f2</bpmn:outgoing></bpmn:serviceTask><bpmn:endEvent id="edgar_end"><bpmn:incoming>edgar_f2</bpmn:incoming></bpmn:endEvent><bpmn:sequenceFlow id="edgar_f1" sourceRef="edgar_start" targetRef="edgar_launch_job"/><bpmn:sequenceFlow id="edgar_f2" sourceRef="edgar_launch_job" targetRef="edgar_end"/></bpmn:process></bpmn:definitions>',
   'v1: Start -> launch EDGAR pipeline job (Service Task) -> End', true, 'be8165db-84d7-4e69-91d3-62f603530824'
  FROM d RETURNING id, workflow_definition_id),
s AS (
  INSERT INTO workflow_steps (workflow_version_id, org_id, step_key, step_type, autonomy_tier, action_registry_key, display_name)
  SELECT v.id, 'bb347258-8f28-4f49-8cc9-e29ccad82884', 'edgar_launch_job', 'service', 2, 'edgar.launch_pipeline_job', 'Launch EDGAR pipeline job'
  FROM v RETURNING id)
INSERT INTO workflow_triggers (workflow_definition_id, org_id, trigger_type, schedule_cron, timezone, is_active, created_by)
SELECT v.workflow_definition_id, 'bb347258-8f28-4f49-8cc9-e29ccad82884', 'scheduled', '0 6 * * *', 'UTC', false, 'be8165db-84d7-4e69-91d3-62f603530824'
FROM v;
