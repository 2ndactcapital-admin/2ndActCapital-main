-- ensemblesystemone.structural — reshape ensemblemodels.structural's two
-- tables into "two LLMs + one System One model" (decisions of 2026-10-01).
--
-- Overwrites, does not recreate: ai_ensemble_configs had ZERO rows and
-- ai_judgment_models ONE row (typesafe-jev, 'disabled') when this was written,
-- both verified live. The judgment table is renamed and keeps its data.
--
--   ai_ensemble_configs:
--     review_model_1(_version)   -> model_1(_version)        LLM, platform_model_catalog.model_id
--     review_model_2(_version)   -> model_2(_version)        LLM, platform_model_catalog.model_id
--     comparison_model(_version) -> system_one_model(_version) ai_system_one_models.key (FK)
--     comparison_kind            -> DROPPED (the third slot is ALWAYS System One)
--   ai_judgment_models -> ai_system_one_models, plus:
--     model_route          the model name sent to TypeSafe (pinned, e.g. jev-1.13.0)
--     model_version        now NULL until a real call reports one; it is what
--                          TypeSafe SAID ran, never what we asked for
--     is_default           at most one TRUE row (partial unique index)
--     last_check_detail    the last availability check's outcome, shown in
--                          the picker as the reason an entry is unavailable
--
-- Kept unchanged: the retire-only immutability trigger (its body compares
-- to_jsonb(NEW) vs to_jsonb(OLD) minus is_active/retired_at, so renamed
-- columns need no edit), the one-active-per-task partial unique index, and
-- all four RLS policies on each table (renamed with the table, re-verified
-- by the DO block at the end).
--
-- Applied live via the supabase-2ndact-dev MCP apply_migration tool
-- (app_service has no DDL rights on public); this file is the record.

BEGIN;

-- ── ai_ensemble_configs ─────────────────────────────────────────────────────

ALTER TABLE public.ai_ensemble_configs
    DROP CONSTRAINT ai_ensemble_configs_comparison_kind_chk,
    DROP CONSTRAINT ai_ensemble_configs_comparison_distinct_chk,
    DROP CONSTRAINT ai_ensemble_configs_review_models_distinct_chk;

ALTER TABLE public.ai_ensemble_configs DROP COLUMN comparison_kind;

ALTER TABLE public.ai_ensemble_configs RENAME COLUMN review_model_1 TO model_1;
ALTER TABLE public.ai_ensemble_configs RENAME COLUMN review_model_1_version TO model_1_version;
ALTER TABLE public.ai_ensemble_configs RENAME COLUMN review_model_2 TO model_2;
ALTER TABLE public.ai_ensemble_configs RENAME COLUMN review_model_2_version TO model_2_version;
ALTER TABLE public.ai_ensemble_configs RENAME COLUMN comparison_model TO system_one_model;
ALTER TABLE public.ai_ensemble_configs RENAME COLUMN comparison_model_version TO system_one_model_version;

-- The column NOT NULLs survive the renames; the named CHECK additionally
-- refuses an empty string, which NOT NULL alone would accept.
ALTER TABLE public.ai_ensemble_configs
    ALTER COLUMN model_1 SET NOT NULL,
    ALTER COLUMN model_2 SET NOT NULL,
    ALTER COLUMN system_one_model SET NOT NULL,
    ALTER COLUMN model_1_version SET NOT NULL,
    ALTER COLUMN model_2_version SET NOT NULL,
    ALTER COLUMN system_one_model_version SET NOT NULL,
    ADD CONSTRAINT ai_ensemble_configs_slots_present_chk
        CHECK (btrim(model_1) <> '' AND btrim(model_2) <> '' AND btrim(system_one_model) <> ''),
    ADD CONSTRAINT ai_ensemble_configs_models_distinct_chk
        CHECK (model_1 <> model_2);

-- ── ai_judgment_models -> ai_system_one_models ──────────────────────────────

ALTER TABLE public.ai_judgment_models RENAME TO ai_system_one_models;
ALTER TABLE public.ai_system_one_models RENAME CONSTRAINT ai_judgment_models_pkey TO ai_system_one_models_pkey;
ALTER TABLE public.ai_system_one_models RENAME CONSTRAINT ai_judgment_models_key_key TO ai_system_one_models_key_key;
ALTER TABLE public.ai_system_one_models RENAME CONSTRAINT ai_judgment_models_availability_chk TO ai_system_one_models_availability_chk;
ALTER TABLE public.ai_system_one_models DROP CONSTRAINT ai_judgment_models_available_requires_verification_chk;

ALTER POLICY ai_judgment_models_read ON public.ai_system_one_models RENAME TO ai_system_one_models_read;
ALTER POLICY ai_judgment_models_insert ON public.ai_system_one_models RENAME TO ai_system_one_models_insert;
ALTER POLICY ai_judgment_models_update ON public.ai_system_one_models RENAME TO ai_system_one_models_update;
ALTER POLICY ai_judgment_models_delete ON public.ai_system_one_models RENAME TO ai_system_one_models_delete;

ALTER TABLE public.ai_system_one_models
    ADD COLUMN model_route text,
    ADD COLUMN is_default boolean NOT NULL DEFAULT false,
    ADD COLUMN last_check_detail text,
    ALTER COLUMN model_version DROP NOT NULL;

-- Jev: keep the row, pin it, make it the default. model_version goes NULL —
-- "unknown — never called" was a placeholder, and the version column must
-- only ever hold what TypeSafe itself reported on a successful call.
-- credential_name is a NAME: the secret lives only in Doppler prd_lite_llm,
-- read by the proxy, never by the app.
UPDATE public.ai_system_one_models
SET display_name = 'Jev 1.13.0 (TypeSafe)',
    model_route = 'jev-1.13.0',
    model_version = NULL,
    credential_name = 'TYPESAFE_API_KEY',
    is_default = true,
    availability = 'disabled',
    last_verified_at = NULL,
    last_check_detail = 'Never verified. Run "Verify now" once the proxy''s /typesafe route and TYPESAFE_API_KEY are in place.',
    notes = 'Pinned to jev-1.13.0 for reproducibility (jev-latest silently follows new releases; add it as a separate entry if wanted). Called through the LiteLLM proxy''s /typesafe generic pass-through; the proxy injects TYPESAFE_API_KEY.'
WHERE key = 'typesafe-jev';

ALTER TABLE public.ai_system_one_models
    ALTER COLUMN model_route SET NOT NULL,
    ADD CONSTRAINT ai_system_one_models_route_present_chk CHECK (btrim(model_route) <> ''),
    -- 'available' needs a real successful call: a timestamp AND the version
    -- TypeSafe reported on it.
    ADD CONSTRAINT ai_system_one_models_available_requires_verification_chk
        CHECK (availability <> 'available'
               OR (last_verified_at IS NOT NULL AND model_version IS NOT NULL));

CREATE UNIQUE INDEX ai_system_one_models_one_default
    ON public.ai_system_one_models ((true)) WHERE is_default;

-- The System One slot can only name a System One catalog entry. RESTRICT:
-- an entry an ensemble version ever used cannot be deleted or re-keyed out
-- from under that history — it is disabled instead.
ALTER TABLE public.ai_ensemble_configs
    ADD CONSTRAINT ai_ensemble_configs_system_one_model_fkey
        FOREIGN KEY (system_one_model) REFERENCES public.ai_system_one_models (key)
        ON UPDATE RESTRICT ON DELETE RESTRICT;

-- ── Re-verify: four policies per table, one per operation ──────────────────
DO $$
DECLARE
    t text;
    n int;
    cmds text[];
BEGIN
    FOREACH t IN ARRAY ARRAY['ai_ensemble_configs', 'ai_system_one_models'] LOOP
        SELECT count(*), array_agg(cmd ORDER BY cmd) INTO n, cmds
        FROM pg_policies WHERE schemaname = 'public' AND tablename = t;
        IF n <> 4 OR cmds <> ARRAY['DELETE', 'INSERT', 'SELECT', 'UPDATE'] THEN
            RAISE EXCEPTION '% has policies % (expected exactly DELETE/INSERT/SELECT/UPDATE)', t, cmds;
        END IF;
        IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = ('public.' || t)::regclass) THEN
            RAISE EXCEPTION '% does not have RLS enabled', t;
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgrelid = 'public.ai_ensemble_configs'::regclass
                     AND tgname = 'ai_ensemble_configs_retire_only') THEN
        RAISE EXCEPTION 'retire-only trigger missing after reshape';
    END IF;
END $$;

COMMIT;
