-- edgarcohorts.structural — cohorts (named, FROZEN sets of filings), cohort-
-- targeted runs, and the template-study inventory tables.
--
-- Global SEC reference data: no org_id anywhere. Every table carries four RLS
-- policies (global read; super-admin INSERT / UPDATE / DELETE gated on
-- app.is_super_admin, which the app sets LOCAL via platform_scope()).
--
-- FROZEN. A cohort is created unsealed, its members are inserted in the same
-- transaction, then it is sealed. After that a trigger refuses every UPDATE of
-- the cohort and every INSERT / UPDATE / DELETE of a member. "Copy and edit"
-- creates a NEW cohort. Deleting a whole cohort (teardown) is allowed only
-- while no run references it (FK, no cascade).
--
-- SELECTED BY COHORT. A cohort may contain filings the default policy did not
-- select. When a cohort run fetches one, the manifest row records
-- selected_by_cohort_id and its selection_policy_version is cleared — so it is
-- never mistaken for a policy decision. The lifecycle CHECK that demanded a
-- policy version on every decided row now accepts a cohort id instead.

-- ═══ Part 1: cohorts and members ══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS portfolio.edgar_cohorts (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL CHECK (length(btrim(name)) > 0),
    purpose       text,
    kind          text NOT NULL DEFAULT 'custom'
                  CHECK (kind IN ('custom', 'hand_picked', 'template_study', 'copy')),
    definition    jsonb NOT NULL,
    member_count  integer NOT NULL CHECK (member_count BETWEEN 1 AND 50000),
    copied_from   uuid REFERENCES portfolio.edgar_cohorts(id),
    sealed_at     timestamptz,
    created_by    uuid,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio.edgar_cohort_members (
    cohort_id         uuid NOT NULL REFERENCES portfolio.edgar_cohorts(id) ON DELETE CASCADE,
    accession_number  text NOT NULL REFERENCES portfolio.edgar_index_filings(accession_number),
    position          integer NOT NULL CHECK (position >= 1),
    stratum           text,
    PRIMARY KEY (cohort_id, accession_number),          -- deduplicated by accession
    UNIQUE (cohort_id, position)
);
CREATE INDEX IF NOT EXISTS edgar_cohort_members_accession_idx
    ON portfolio.edgar_cohort_members (accession_number);

CREATE OR REPLACE FUNCTION portfolio.edgar_cohorts_frozen() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    n integer;
BEGIN
    -- The ONLY permitted update: sealing (sealed_at NULL -> set), nothing else
    -- changing, and member_count equal to the members actually inserted.
    IF OLD.sealed_at IS NULL AND NEW.sealed_at IS NOT NULL
       AND NEW.id = OLD.id AND NEW.name = OLD.name
       AND NEW.purpose IS NOT DISTINCT FROM OLD.purpose AND NEW.kind = OLD.kind
       AND NEW.definition = OLD.definition AND NEW.member_count = OLD.member_count
       AND NEW.copied_from IS NOT DISTINCT FROM OLD.copied_from
       AND NEW.created_by IS NOT DISTINCT FROM OLD.created_by
       AND NEW.created_at = OLD.created_at THEN
        SELECT count(*) INTO n FROM portfolio.edgar_cohort_members WHERE cohort_id = NEW.id;
        IF n <> NEW.member_count THEN
            RAISE EXCEPTION 'edgar cohort %: member_count % does not match the % members inserted',
                NEW.id, NEW.member_count, n;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'edgar cohort % is frozen: a saved cohort never changes. Copy it to make a new one.', OLD.id;
END $$;

DROP TRIGGER IF EXISTS edgar_cohorts_frozen_trg ON portfolio.edgar_cohorts;
CREATE TRIGGER edgar_cohorts_frozen_trg BEFORE UPDATE ON portfolio.edgar_cohorts
    FOR EACH ROW EXECUTE FUNCTION portfolio.edgar_cohorts_frozen();

CREATE OR REPLACE FUNCTION portfolio.edgar_cohort_members_frozen() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    cid uuid := COALESCE(NEW.cohort_id, OLD.cohort_id);
    sealed timestamptz;
    found_parent boolean;
BEGIN
    SELECT true, c.sealed_at INTO found_parent, sealed FROM portfolio.edgar_cohorts c WHERE c.id = cid;
    -- A DELETE cascading from the cohort's own deletion finds no parent row.
    IF TG_OP = 'DELETE' AND found_parent IS NULL THEN
        RETURN OLD;
    END IF;
    IF sealed IS NOT NULL THEN
        RAISE EXCEPTION 'edgar cohort % is frozen: its members never change (refused %). Copy it to make a new one.',
            cid, TG_OP;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS edgar_cohort_members_frozen_trg ON portfolio.edgar_cohort_members;
CREATE TRIGGER edgar_cohort_members_frozen_trg
    BEFORE INSERT OR UPDATE OR DELETE ON portfolio.edgar_cohort_members
    FOR EACH ROW EXECUTE FUNCTION portfolio.edgar_cohort_members_frozen();

-- ═══ Part 2: runs target a cohort; the manifest records cohort selection ══
ALTER TABLE portfolio.edgar_pipeline_runs
    ADD COLUMN IF NOT EXISTS cohort_id uuid REFERENCES portfolio.edgar_cohorts(id),
    ADD COLUMN IF NOT EXISTS run_kind text NOT NULL DEFAULT 'fetch';
ALTER TABLE portfolio.edgar_pipeline_runs DROP CONSTRAINT IF EXISTS edgar_pipeline_runs_run_kind_check;
ALTER TABLE portfolio.edgar_pipeline_runs ADD CONSTRAINT edgar_pipeline_runs_run_kind_check
    CHECK (run_kind IN ('fetch', 'extract'));
CREATE INDEX IF NOT EXISTS edgar_pipeline_runs_cohort_idx
    ON portfolio.edgar_pipeline_runs (cohort_id) WHERE cohort_id IS NOT NULL;

ALTER TABLE portfolio.edgar_index_filings
    ADD COLUMN IF NOT EXISTS selected_by_cohort_id uuid REFERENCES portfolio.edgar_cohorts(id);
ALTER TABLE portfolio.edgar_index_filings DROP CONSTRAINT IF EXISTS edgar_index_filings_decided_has_policy_chk;
ALTER TABLE portfolio.edgar_index_filings ADD CONSTRAINT edgar_index_filings_decided_has_policy_chk
    CHECK (pipeline_status = 'discovered'
           OR (pipeline_status = 'fetched' AND reference_filing_id IS NOT NULL)
           OR selection_policy_version IS NOT NULL
           OR selected_by_cohort_id IS NOT NULL);
-- A row is decided by a policy OR by a cohort, never both.
ALTER TABLE portfolio.edgar_index_filings DROP CONSTRAINT IF EXISTS edgar_index_filings_one_selector_chk;
ALTER TABLE portfolio.edgar_index_filings ADD CONSTRAINT edgar_index_filings_one_selector_chk
    CHECK (selected_by_cohort_id IS NULL OR selection_policy_version IS NULL);

-- ═══ Part 3: the template-study inventory ═════════════════════════════════
CREATE TABLE IF NOT EXISTS portfolio.edgar_inventory_runs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cohort_id         uuid NOT NULL REFERENCES portfolio.edgar_cohorts(id),
    status            text NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'completed', 'stopped_spend_cap', 'failed', 'blocked')),
    deployment_name   text,                    -- the platform_model_catalog model_id used
    prompt_version    text NOT NULL,
    spend_cap_usd     numeric NOT NULL CHECK (spend_cap_usd >= 0),
    spent_usd         numeric NOT NULL DEFAULT 0,
    documents_planned integer NOT NULL DEFAULT 0,
    documents_done    integer NOT NULL DEFAULT 0,
    items_accepted    integer NOT NULL DEFAULT 0,
    items_rejected    integer NOT NULL DEFAULT 0,
    grouping_method   text,
    stop_reason       text,
    report            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by        uuid,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);

CREATE TABLE IF NOT EXISTS portfolio.edgar_inventory_documents (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                uuid NOT NULL REFERENCES portfolio.edgar_inventory_runs(id) ON DELETE CASCADE,
    accession_number      text NOT NULL,
    reference_filing_id   uuid NOT NULL,
    document_kind         text,
    issuer_group          text,
    selection_reason      text,
    status                text NOT NULL CHECK (status IN ('ok', 'failed', 'invalid', 'model_mismatch')),
    error                 text,
    terms_chars           integer NOT NULL,
    terms_tokens_est      integer NOT NULL,
    terms_sections        jsonb NOT NULL DEFAULT '[]'::jsonb,
    product_family        text,
    program_supplement    text,
    has_payout_table      boolean,
    issue_size            text,
    items_accepted        integer NOT NULL DEFAULT 0,
    items_rejected        integer NOT NULL DEFAULT 0,
    rejected_items        jsonb NOT NULL DEFAULT '[]'::jsonb,
    deployment_name       text,
    provider_model        text,
    proxy_model_id        text,
    call_id               text,
    input_tokens          integer,
    output_tokens         integer,
    cost_usd              numeric,
    latency_ms            integer,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, reference_filing_id)
);

CREATE TABLE IF NOT EXISTS portfolio.edgar_inventory_concepts (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              uuid NOT NULL REFERENCES portfolio.edgar_inventory_runs(id) ON DELETE CASCADE,
    concept_key         text NOT NULL,
    display_label       text NOT NULL,
    labels              text[] NOT NULL,
    issuers             text[] NOT NULL,
    frequency           integer NOT NULL,
    document_count      integer NOT NULL,
    example_values      jsonb NOT NULL DEFAULT '[]'::jsonb,
    mapped_field_key    text,
    proposed_field_key  text,
    misleading_flags    jsonb NOT NULL DEFAULT '[]'::jsonb,
    grouping_method     text NOT NULL CHECK (grouping_method IN ('normalised_label', 'model')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, concept_key)
);

CREATE TABLE IF NOT EXISTS portfolio.edgar_inventory_items (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              uuid NOT NULL REFERENCES portfolio.edgar_inventory_runs(id) ON DELETE CASCADE,
    document_id         uuid NOT NULL REFERENCES portfolio.edgar_inventory_documents(id) ON DELETE CASCADE,
    accession_number    text NOT NULL,
    issuer_group        text,
    label               text NOT NULL,
    label_normalized    text NOT NULL,
    value_text          text,
    quote               text NOT NULL,
    quote_char_start    integer,
    quote_char_end      integer,
    section             text,
    mapped_field_key    text,                  -- NULL = the model said NEW
    proposed_field_key  text,
    misleading_label    boolean NOT NULL DEFAULT false,
    misleading_note     text,
    concept_id          uuid REFERENCES portfolio.edgar_inventory_concepts(id) ON DELETE SET NULL,
    deployment_name     text NOT NULL,
    provider_model      text,
    call_id             text,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS edgar_inventory_items_run_idx ON portfolio.edgar_inventory_items (run_id);
CREATE INDEX IF NOT EXISTS edgar_inventory_items_concept_idx ON portfolio.edgar_inventory_items (concept_id);

-- ═══ Part 4: RLS (four policies per table) and grants ═════════════════════
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['edgar_cohorts', 'edgar_cohort_members', 'edgar_inventory_runs',
                             'edgar_inventory_documents', 'edgar_inventory_concepts',
                             'edgar_inventory_items'] LOOP
        EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_global_read', t);
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (true)', t || '_global_read', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_insert', t);
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK '
                       '(current_setting(''app.is_super_admin'', true) = ''true'')', t || '_super_admin_insert', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_update', t);
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING '
                       '(current_setting(''app.is_super_admin'', true) = ''true'') WITH CHECK '
                       '(current_setting(''app.is_super_admin'', true) = ''true'')', t || '_super_admin_update', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_delete', t);
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING '
                       '(current_setting(''app.is_super_admin'', true) = ''true'')', t || '_super_admin_delete', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
    END LOOP;
END $$;
