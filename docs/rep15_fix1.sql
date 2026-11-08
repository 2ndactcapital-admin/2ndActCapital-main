-- rep15 fix 1 — portfolio.provided_model_guard() rewritten one branch per table (CREATE OR REPLACE only; the three
-- triggers that call it are unchanged). The rep15_part1 version raised 42703 'record "new" has no field
-- "parent_version_id"' on every INSERT into portfolio.model_version_positions (and "version_id" on every
-- model_sleeves INSERT): every org's model upload confirm, draft, copy and sleeve write answered 500.
-- Applied by: python3 apps/api/scripts/_apply_rep15.py --fix1   (operator DSN; idempotent; writes no rows)
CREATE OR REPLACE FUNCTION portfolio.provided_model_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE src text; vid uuid; mid uuid;
BEGIN
  -- rep15 fix 1: one branch per table. PL/pgSQL resolves EVERY NEW.<field> in an expression against the firing
  -- table's row type, so a single CASE naming both parent_version_id and version_id raised 42703 ("record new has
  -- no field ...") on EVERY insert into model_version_positions / model_sleeves — every org's upload confirm.
  IF TG_TABLE_NAME = 'model_versions' THEN
    mid := NEW.model_id;
  ELSIF TG_TABLE_NAME = 'model_sleeves' THEN
    vid := NEW.parent_version_id;
    SELECT model_id INTO mid FROM portfolio.model_versions WHERE id = vid AND system_to IS NULL;
  ELSE
    vid := NEW.version_id;
    SELECT model_id INTO mid FROM portfolio.model_versions WHERE id = vid AND system_to IS NULL;
  END IF;
  SELECT source INTO src FROM portfolio.models WHERE id = mid AND system_to IS NULL;
  IF src IS DISTINCT FROM 'provided' THEN
    RETURN NEW;
  END IF;
  IF TG_TABLE_NAME = 'model_versions' THEN
    IF TG_OP = 'INSERT' AND NEW.system_to IS NULL AND NEW.origin <> 'subscription' THEN
      RAISE EXCEPTION 'a provided model is read-only — its versions come only from the provider (copy it to change it)'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_TABLE_NAME = 'model_sleeves' THEN
    RAISE EXCEPTION 'a provided model has no sleeves of its own' USING ERRCODE = '42501';
  END IF;
  IF COALESCE(current_setting('models.provided_import', true), '') <> 'on' THEN
    RAISE EXCEPTION 'a provided model is read-only — its positions come only from the provider (copy it to change it)'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;
