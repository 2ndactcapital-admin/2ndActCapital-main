-- noteextractb1.structural — Part 1: readings, runs, staging, gold set, gold
-- candidates and distribution participants. ALL GLOBAL (no org_id): the note
-- corpus is public SEC reference data shared by every org, exactly like
-- portfolio.reference_filings and portfolio.structured_note_issuers.
--
-- Every table gets FOUR policies (SELECT / INSERT / UPDATE / DELETE): a missing
-- policy and a missing context both look like "zero rows", so no operation is
-- left without one. Writes require SET LOCAL app.is_super_admin = 'true'
-- (services.database.platform_scope); reads are global.
--
-- Nothing here writes to portfolio.securities_global or
-- portfolio.securities_global_note_terms. B1 STAGES; B2 promotes.

-- ═══ 1. Runs: one row per extraction / evaluation / pilot run ═══════════════
CREATE TABLE IF NOT EXISTS portfolio.note_extraction_runs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_kind            text NOT NULL CHECK (run_kind = ANY (ARRAY[
                            'evaluation', 'pilot', 'cascade', 'participant_seed', 'verify'])),
    status              text NOT NULL DEFAULT 'running' CHECK (status = ANY (ARRAY[
                            'running', 'completed', 'stopped_spend_cap', 'failed', 'dry_run'])),
    ensemble_config_id  uuid REFERENCES public.ai_ensemble_configs(id),
    config              jsonb NOT NULL DEFAULT '{}'::jsonb,
    spend_cap_usd       numeric(12, 6) NOT NULL CHECK (spend_cap_usd >= 0),
    spent_usd           numeric(14, 8) NOT NULL DEFAULT 0,
    notes_planned       integer NOT NULL DEFAULT 0,
    notes_done          integer NOT NULL DEFAULT 0,
    stop_reason         text,
    report              jsonb,
    created_by          uuid REFERENCES public.users(id),
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz
);
CREATE INDEX IF NOT EXISTS note_extraction_runs_kind_idx
    ON portfolio.note_extraction_runs (run_kind, started_at DESC);

-- ═══ 2. Readings: EVERY value any source produced for a note's field ═══════
-- Append-only evidence. A reading is never edited; a new reading is added.
CREATE TABLE IF NOT EXISTS portfolio.note_term_readings (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_filing_id uuid NOT NULL REFERENCES portfolio.reference_filings(id),
    note_terms_id       uuid REFERENCES portfolio.securities_global_note_terms(id),
    run_id              uuid REFERENCES portfolio.note_extraction_runs(id),
    field_key           text NOT NULL CHECK (btrim(field_key) <> ''),
    value               jsonb,           -- SQL NULL = the source answered "absent / null"
    value_normalized    text,
    source              text NOT NULL CHECK (source = ANY (ARRAY[
                            'rules', 'edgartools', 'model_1', 'model_2', 'jev',
                            'escalation', 'human'])),
    origin              text NOT NULL DEFAULT 'cascade' CHECK (origin = ANY (ARRAY[
                            'cascade', 'evaluation', 'migrated_correction', 'gold_review'])),
    status              text NOT NULL DEFAULT 'ok' CHECK (status = ANY (ARRAY[
                            'ok', 'failed', 'model_mismatch', 'invalid'])),
    error               text,
    deployment_name     text,            -- the proxy deployment ASKED for (never a raw upstream id)
    provider_model      text,            -- the model version the PROVIDER REPORTED answering
    proxy_model_id      text,            -- x-litellm-model-id: the exact deployment that served it
    ensemble_config_id  uuid REFERENCES public.ai_ensemble_configs(id),
    prompt_version      text,
    prompt_prefix_hash  text,
    reasoning_effort    text,
    source_quote        text,
    quote_verified      boolean,
    value_in_quote      boolean,
    raw_char_start      integer,
    raw_char_end        integer,
    probability         numeric(8, 6),   -- Jev only
    input_tokens        integer,
    output_tokens       integer,
    cached_tokens       integer,
    cost_usd            numeric(14, 8),
    latency_ms          integer,
    call_id             text,            -- groups the readings one provider call produced
    legacy_correction_id uuid,           -- origin='migrated_correction': the moved row's id
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by          uuid REFERENCES public.users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT note_term_readings_span_chk CHECK (
        (raw_char_start IS NULL AND raw_char_end IS NULL)
        OR (raw_char_start >= 0 AND raw_char_end >= raw_char_start)),
    CONSTRAINT note_term_readings_human_chk CHECK (
        source <> 'human' OR created_by IS NOT NULL),
    CONSTRAINT note_term_readings_migrated_chk CHECK (
        origin <> 'migrated_correction' OR legacy_correction_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS note_term_readings_filing_idx
    ON portfolio.note_term_readings (reference_filing_id, field_key);
CREATE INDEX IF NOT EXISTS note_term_readings_run_idx
    ON portfolio.note_term_readings (run_id);
-- A moved correction yields exactly one reading per side; re-running the move
-- can never duplicate them.
CREATE UNIQUE INDEX IF NOT EXISTS note_term_readings_legacy_uq
    ON portfolio.note_term_readings (legacy_correction_id, source)
    WHERE legacy_correction_id IS NOT NULL;

CREATE OR REPLACE FUNCTION portfolio.note_term_readings_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'note_term_readings is append-only: add a new reading instead of editing reading %', OLD.id
        USING ERRCODE = 'check_violation';
END;
$$;
CREATE OR REPLACE TRIGGER note_term_readings_no_update
    BEFORE UPDATE ON portfolio.note_term_readings
    FOR EACH ROW EXECUTE FUNCTION portfolio.note_term_readings_immutable();

-- ═══ 3. Staging: resolved values per note (NOT the security master) ═══════
CREATE TABLE IF NOT EXISTS portfolio.note_extraction_staging (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              uuid NOT NULL REFERENCES portfolio.note_extraction_runs(id),
    reference_filing_id uuid NOT NULL REFERENCES portfolio.reference_filings(id),
    status              text NOT NULL CHECK (status = ANY (ARRAY[
                            'verified', 'needs_review', 'failed'])),
    status_reason       text,
    ensemble_config_id  uuid REFERENCES public.ai_ensemble_configs(id),
    full_tokens_est     integer,
    trimmed_tokens_est  integer,
    disagreement_count  integer NOT NULL DEFAULT 0,
    jev_called          boolean NOT NULL DEFAULT false,
    escalated           boolean NOT NULL DEFAULT false,
    fuller_text_retry   boolean NOT NULL DEFAULT false,
    skip_second_reader_safe boolean,     -- MEASURED only; B1 never skips Model 2
    cost_usd            numeric(14, 8) NOT NULL DEFAULT 0,
    unmatched_participants jsonb NOT NULL DEFAULT '[]'::jsonb,
    detail              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, reference_filing_id)
);

CREATE TABLE IF NOT EXISTS portfolio.note_extraction_staged_fields (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    staging_id          uuid NOT NULL REFERENCES portfolio.note_extraction_staging(id) ON DELETE CASCADE,
    field_key           text NOT NULL,
    resolved_value      jsonb,
    resolution          text NOT NULL CHECK (resolution = ANY (ARRAY[
                            'verified_agreement', 'agreed_null', 'jev', 'escalation',
                            'unresolved'])),
    is_critical         boolean NOT NULL,
    needs_review        boolean NOT NULL,
    winning_reading_id  uuid REFERENCES portfolio.note_term_readings(id),
    source_quote        text,
    raw_char_start      integer,
    raw_char_end        integer,
    probability         numeric(8, 6),
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (staging_id, field_key),
    CONSTRAINT note_extraction_staged_fields_review_chk CHECK (
        resolution <> 'unresolved' OR needs_review = is_critical)
);

-- ═══ 4. Gold set: hand-checked values. ONLY HUMANS WRITE HERE. ═════════════
-- Bi-temporal on the valid axis: a re-review closes the open row and inserts a
-- new one (Rule 3); the partial unique index keeps one open row per field.
CREATE TABLE IF NOT EXISTS portfolio.note_gold_values (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_filing_id uuid NOT NULL REFERENCES portfolio.reference_filings(id),
    field_key           text NOT NULL CHECK (btrim(field_key) <> ''),
    value               jsonb,           -- SQL NULL = reviewer confirmed the field is absent
    action              text NOT NULL CHECK (action = ANY (ARRAY['confirmed', 'corrected', 'absent'])),
    source_reading_id   uuid REFERENCES portfolio.note_term_readings(id),
    source_quote        text,
    raw_char_start      integer,
    raw_char_end        integer,
    notes               text,
    reviewer_id         uuid NOT NULL REFERENCES public.users(id),
    reviewed_at         timestamptz NOT NULL DEFAULT now(),
    valid_from          timestamptz NOT NULL DEFAULT now(),
    valid_to            timestamptz,
    CONSTRAINT note_gold_values_absent_chk CHECK (action <> 'absent' OR value IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS note_gold_values_open_uq
    ON portfolio.note_gold_values (reference_filing_id, field_key) WHERE valid_to IS NULL;

-- The human guard. services/note_extraction/gold.py is the ONLY code that sets
-- app.gold_reviewer_id, and only after the caller passed the super-admin gate
-- as a real signed-in user. Any other path — a model, the cascade, a script
-- that merely has super-admin RLS context — is refused here, in the database.
CREATE OR REPLACE FUNCTION portfolio.note_gold_values_human_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_reviewer text := NULLIF(current_setting('app.gold_reviewer_id', true), '');
BEGIN
    IF v_reviewer IS NULL THEN
        RAISE EXCEPTION 'gold values are written only by a human reviewer through the gold review screen (app.gold_reviewer_id not set)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.reviewer_id::text <> v_reviewer THEN
            RAISE EXCEPTION 'gold value reviewer_id % does not match the signed-in reviewer', NEW.reviewer_id
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        -- Only closing the open row is allowed; the recorded value never changes.
        IF NEW.value IS DISTINCT FROM OLD.value OR NEW.action <> OLD.action
           OR NEW.reviewer_id <> OLD.reviewer_id OR NEW.field_key <> OLD.field_key
           OR NEW.reference_filing_id <> OLD.reference_filing_id
           OR NEW.reviewed_at <> OLD.reviewed_at OR NEW.valid_from <> OLD.valid_from
           OR OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL THEN
            RAISE EXCEPTION 'a gold value is never edited in place: close it (valid_to) and insert a new one'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;  -- DELETE (teardown only; RLS still requires super-admin)
END;
$$;
CREATE OR REPLACE TRIGGER note_gold_values_human_guard_ins
    BEFORE INSERT OR UPDATE ON portfolio.note_gold_values
    FOR EACH ROW EXECUTE FUNCTION portfolio.note_gold_values_human_guard();

-- ═══ 5. Gold candidates: the sampler's proposals ═══════════════════════════
CREATE TABLE IF NOT EXISTS portfolio.note_gold_candidates (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_filing_id uuid NOT NULL UNIQUE REFERENCES portfolio.reference_filings(id),
    sample_batch        text NOT NULL,
    issuer_group        text,
    filing_year         integer,
    product_type        text,
    trap_tags           text[] NOT NULL DEFAULT '{}',
    status              text NOT NULL DEFAULT 'proposed' CHECK (status = ANY (ARRAY[
                            'proposed', 'in_review', 'done', 'skipped'])),
    proposed_at         timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- ═══ 6. Distribution participants: reference data, like the issuer table ══
CREATE TABLE IF NOT EXISTS portfolio.distribution_participants (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name      text NOT NULL CHECK (btrim(canonical_name) <> ''),
    participant_type    text NOT NULL CHECK (participant_type = ANY (ARRAY[
                            'issuer_affiliate', 'distribution_platform', 'dealer',
                            'wealth_manager', 'other'])),
    aliases             text[] NOT NULL DEFAULT '{}',
    status              text NOT NULL DEFAULT 'proposed' CHECK (status = ANY (ARRAY[
                            'proposed', 'active', 'retired'])),
    observed_count      integer NOT NULL DEFAULT 0,
    notes               text,
    created_by          uuid REFERENCES public.users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS distribution_participants_name_uq
    ON portfolio.distribution_participants (lower(canonical_name));

-- ═══ 7. RLS: four policies per table; global read, super-admin writes ═════
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'note_extraction_runs', 'note_term_readings', 'note_extraction_staging',
        'note_extraction_staged_fields', 'note_gold_values', 'note_gold_candidates',
        'distribution_participants']
    LOOP
        EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
        -- Re-runnable without DROP: skip when this table's policies already exist.
        CONTINUE WHEN EXISTS (SELECT 1 FROM pg_policies
                              WHERE schemaname = 'portfolio' AND tablename = t);
        EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (true)',
                       t || '_global_read', t);
        EXECUTE format($p$CREATE POLICY %I ON portfolio.%I FOR INSERT
                       WITH CHECK (current_setting('app.is_super_admin', true) = 'true')$p$,
                       t || '_super_admin_insert', t);
        EXECUTE format($p$CREATE POLICY %I ON portfolio.%I FOR UPDATE
                       USING (current_setting('app.is_super_admin', true) = 'true')
                       WITH CHECK (current_setting('app.is_super_admin', true) = 'true')$p$,
                       t || '_super_admin_update', t);
        EXECUTE format($p$CREATE POLICY %I ON portfolio.%I FOR DELETE
                       USING (current_setting('app.is_super_admin', true) = 'true')$p$,
                       t || '_super_admin_delete', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
    END LOOP;
END;
$$;
