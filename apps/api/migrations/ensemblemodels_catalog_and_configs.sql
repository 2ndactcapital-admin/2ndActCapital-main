-- SUPERSEDED IN PART by ensemblesystemone_reshape.sql (2026-10-01): columns
-- renamed, comparison_kind dropped, ai_judgment_models -> ai_system_one_models.
-- Kept unchanged as the record of what ensemblemodels.structural applied.
--
-- ensemblemodels.structural — judgment-model registry + versioned ensemble
-- selections for the structured-note hazard ensemble.
--
-- Both tables are GLOBAL (no org_id): structured-note data lives in the
-- portfolio schema with no org axis, so no org's settings can govern it. Same
-- shape as platform_model_catalog — and org_settings could not hold this
-- anyway: its org_id is NOT NULL with an FK to organizations, so a
-- platform-level (org_id NULL) row is refused by the schema before RLS is
-- even consulted (ensemblemodels Task 1e).
--
-- RLS: four separate policies, copied verbatim from platform_model_catalog
-- (SELECT USING (true); INSERT/UPDATE/DELETE gated on app.is_super_admin) —
-- never a single FOR ALL.
--
-- availability reuses platform_model_catalog's three-state vocabulary
-- (available / deprecated / disabled — litellmavailability.structural), never
-- a second one. A model that has never been successfully called is
-- 'disabled', and the CHECK below makes "never mark anything available on the
-- strength of a credential existing" mechanical: 'available' requires a real
-- last_verified_at.
--
-- Applied live via the supabase-2ndact-dev MCP `apply_migration` tool
-- (app_service has no CREATE on public), so this file is the record of what is
-- deployed, not itself the apply path.

BEGIN;

-- ── ai_judgment_models ──────────────────────────────────────────────────────

CREATE TABLE public.ai_judgment_models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key text NOT NULL UNIQUE,
    display_name text NOT NULL,
    provider text NOT NULL,
    model_version text NOT NULL,
    credential_name text,            -- a Doppler secret NAME, never a value
    availability text NOT NULL DEFAULT 'disabled',
    last_verified_at timestamptz,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ai_judgment_models_availability_chk
        CHECK (availability IN ('available', 'deprecated', 'disabled')),
    CONSTRAINT ai_judgment_models_available_requires_verification_chk
        CHECK (availability <> 'available' OR last_verified_at IS NOT NULL)
);

ALTER TABLE public.ai_judgment_models ENABLE ROW LEVEL SECURITY;

CREATE POLICY ai_judgment_models_read ON public.ai_judgment_models
    FOR SELECT USING (true);

CREATE POLICY ai_judgment_models_insert ON public.ai_judgment_models
    FOR INSERT WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY ai_judgment_models_update ON public.ai_judgment_models
    FOR UPDATE USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY ai_judgment_models_delete ON public.ai_judgment_models
    FOR DELETE USING (current_setting('app.is_super_admin', true) = 'true');

-- ── ai_ensemble_configs ─────────────────────────────────────────────────────
-- Immutable, versioned selections. A new selection is a new row; the only
-- permitted UPDATE is retiring the active row (trigger below). The *_version
-- columns snapshot the exact upstream model each slot resolved to at
-- activation: a LiteLLM model group name ('claude-sonnet') can be repointed
-- at a different upstream later, and without the snapshot a historical
-- ensemble would silently change meaning.

CREATE TABLE public.ai_ensemble_configs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_key text NOT NULL,
    review_model_1 text NOT NULL,
    review_model_1_version text NOT NULL,
    review_model_2 text NOT NULL,
    review_model_2_version text NOT NULL,
    comparison_model text NOT NULL,
    comparison_model_version text NOT NULL,
    comparison_kind text NOT NULL,
    is_active boolean NOT NULL DEFAULT false,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz,
    retired_at timestamptz,
    notes text,
    CONSTRAINT ai_ensemble_configs_comparison_kind_chk
        CHECK (comparison_kind IN ('llm', 'judgment')),
    CONSTRAINT ai_ensemble_configs_review_models_distinct_chk
        CHECK (review_model_1 <> review_model_2),
    CONSTRAINT ai_ensemble_configs_comparison_distinct_chk
        CHECK (comparison_model NOT IN (review_model_1, review_model_2)),
    CONSTRAINT ai_ensemble_configs_active_not_retired_chk
        CHECK (NOT is_active OR retired_at IS NULL)
);

CREATE UNIQUE INDEX ai_ensemble_configs_one_active_per_task
    ON public.ai_ensemble_configs (task_key) WHERE is_active;

CREATE INDEX ai_ensemble_configs_task_created
    ON public.ai_ensemble_configs (task_key, created_at DESC);

ALTER TABLE public.ai_ensemble_configs ENABLE ROW LEVEL SECURITY;

CREATE POLICY ai_ensemble_configs_read ON public.ai_ensemble_configs
    FOR SELECT USING (true);

CREATE POLICY ai_ensemble_configs_insert ON public.ai_ensemble_configs
    FOR INSERT WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY ai_ensemble_configs_update ON public.ai_ensemble_configs
    FOR UPDATE USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY ai_ensemble_configs_delete ON public.ai_ensemble_configs
    FOR DELETE USING (current_setting('app.is_super_admin', true) = 'true');

-- The only permitted UPDATE: an active row is retired (is_active true->false,
-- retired_at set), with every other column byte-for-byte unchanged. BEFORE
-- UPDATE, so it fires ahead of the CHECKs — it returns NEW only on the one
-- legal transition and raises on everything else.
CREATE OR REPLACE FUNCTION public.ai_ensemble_configs_retire_only()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
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
        'ai_ensemble_configs row % is immutable: the only permitted update is retiring an active row (is_active true->false with retired_at set). A new selection is a new row.',
        OLD.id
        USING ERRCODE = 'check_violation',
              CONSTRAINT = 'ai_ensemble_configs_retire_only';
END;
$$;

CREATE TRIGGER ai_ensemble_configs_retire_only
    BEFORE UPDATE ON public.ai_ensemble_configs
    FOR EACH ROW EXECUTE FUNCTION public.ai_ensemble_configs_retire_only();

-- The app reaches these through FastAPI only. Supabase's default grants would
-- otherwise expose them to anon/authenticated via PostgREST.
REVOKE ALL ON public.ai_judgment_models FROM anon, authenticated;
REVOKE ALL ON public.ai_ensemble_configs FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ai_judgment_models TO app_service;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ai_ensemble_configs TO app_service;

-- ── Seed: Jev ───────────────────────────────────────────────────────────────
-- 'disabled', last_verified_at NULL: no call to Jev has ever succeeded, and no
-- Jev/TypeSafe credential name exists in Doppler (prd, dev, prd_lite_llm —
-- checked by name only, ensemblemodels Task 1d). model_version is the honest
-- "unknown" rather than an invented version string.
INSERT INTO public.ai_judgment_models
    (key, display_name, provider, model_version, credential_name, availability,
     last_verified_at, notes)
VALUES
    ('typesafe-jev', 'Jev (TypeSafe)', 'typesafe', 'unknown — never called',
     NULL, 'disabled', NULL,
     'Seeded by ensemblemodels.structural. Not callable: no Jev/TypeSafe '
     'credential exists in Doppler, and no request to it has ever succeeded. '
     'Cannot be a LiteLLM model on v1.96.2 with its probability output intact '
     '(see docs/PROJECT_STATUS.md). Flip to available only after a real call '
     'succeeds and last_verified_at is set.');

COMMIT;
