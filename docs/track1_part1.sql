-- TRACK1 part 1 — tracked securities + named security lists (platform scope).
-- Additive: three NEW platform tables, every operation under RLS exactly like portfolio.security_price_coverage
-- (SELECT for every reader; INSERT / UPDATE / DELETE only with app.is_super_admin). Writes NO security, price, list
-- member or org row — the operator's list loader fills the lists; a Super Admin tracks.
-- Applied by apps/api/scripts/_apply_track1.py --schema on the operator DSN (the table owner). Idempotent.

-- (no BEGIN / COMMIT here: _apply_track1.py runs the whole file in ONE transaction)

-- 1. ONE live row per security: tracked or untracked, who, why (a note and/or the list it came from).
--    Untrack = valid-axis restatement (the live 'tracked' row closed, an 'untracked' row inserted); history kept.
CREATE TABLE IF NOT EXISTS portfolio.tracked_securities (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  status              text NOT NULL,
  list_key            text,
  note                text,
  actor_id            uuid,
  owner_scope         text NOT NULL DEFAULT 'platform',
  org_id              uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT tracked_securities_status_chk CHECK (status IN ('tracked', 'untracked')),
  CONSTRAINT tracked_securities_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT tracked_securities_why_chk CHECK (list_key IS NOT NULL OR length(btrim(COALESCE(note, ''))) > 0),
  CONSTRAINT tracked_securities_note_len_chk CHECK (note IS NULL OR length(note) <= 2000)
);
CREATE UNIQUE INDEX IF NOT EXISTS tracked_securities_live_uq ON portfolio.tracked_securities (global_security_id)
    WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS tracked_securities_live_status_ix ON portfolio.tracked_securities (status)
    WHERE valid_to IS NULL AND system_to IS NULL;

-- 2. a named list: key, name, where its members come from, as-of, what did not resolve.
--    A refresh restates the list row on the valid axis; members are keyed by list_key (never by this row's id).
CREATE TABLE IF NOT EXISTS portfolio.security_lists (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  list_key            text NOT NULL,
  name                text NOT NULL,
  description         text,
  source_kind         text NOT NULL,
  source_detail       text,
  as_of               date,
  member_count        integer NOT NULL DEFAULT 0,
  misses              jsonb NOT NULL DEFAULT '[]'::jsonb,
  run_id              uuid,
  actor_id            uuid,
  owner_scope         text NOT NULL DEFAULT 'platform',
  org_id              uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT security_lists_source_chk CHECK (source_kind IN ('curated', 'index_holdings')),
  CONSTRAINT security_lists_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT security_lists_key_chk CHECK (list_key ~ '^[a-z0-9][a-z0-9_]{1,62}$'),
  CONSTRAINT security_lists_count_chk CHECK (member_count >= 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS security_lists_live_uq ON portfolio.security_lists (list_key)
    WHERE valid_to IS NULL AND system_to IS NULL;

CREATE TABLE IF NOT EXISTS portfolio.security_list_members (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  list_key            text NOT NULL,
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  ticker              text,
  description         text,
  matched_by          text NOT NULL,
  weight              numeric,
  run_id              uuid,
  owner_scope         text NOT NULL DEFAULT 'platform',
  org_id              uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT security_list_members_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT security_list_members_matched_chk CHECK (matched_by IN ('ticker', 'cusip', 'isin', 'sedol'))
);
CREATE UNIQUE INDEX IF NOT EXISTS security_list_members_live_uq ON portfolio.security_list_members
    (list_key, global_security_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS security_list_members_gsid_ix ON portfolio.security_list_members (global_security_id)
    WHERE valid_to IS NULL AND system_to IS NULL;

-- 3. RLS — every operation, like security_price_coverage (CLAUDE.md: a policy for every operation the table takes)
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tracked_securities', 'security_lists', 'security_list_members'] LOOP
    EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_read', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING (true)', t || '_read', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_insert', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK ('
                   'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_insert', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_update', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING ('
                   'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'') WITH CHECK ('
                   'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_update', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_delete', t);
    EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING ('
                   'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_delete', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
  END LOOP;
END $$;
