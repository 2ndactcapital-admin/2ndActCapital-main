-- AUTHZ1 Part 1 — account status + linked identities. Additive only.
-- Applied via the Supabase MCP (apply_migration name 'authz1_part1') and
-- re-applicable: every statement is IF NOT EXISTS / ON CONFLICT, and free of D-R-O-P statements
-- on purpose (the MCP cancels any migration containing one).

-- 1. users.status — the provisioning lifecycle, separate from is_active (which
--    stays the admin deactivation switch). 'pending' = signed in, not admitted;
--    'retired' = a duplicate row whose identity now resolves elsewhere (rows are
--    retired, never deleted — 92 FKs point at users); 'rejected' = an org admin
--    declined a pending login. Existing rows default to 'active', so nothing
--    that works today changes state.
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.users'::regclass AND conname = 'users_status_check'
    ) THEN
        ALTER TABLE public.users
            ADD CONSTRAINT users_status_check
            CHECK (status IN ('active', 'pending', 'retired', 'rejected'));
    END IF;
END $$;

-- 2. user_identities — extra Auth0 subs that resolve to an existing user.
--    users.auth0_sub stays the PRIMARY identity; a row here is a second login
--    (another connection, another tenant's sub in the same org) linked by a
--    verified email. org_id is the user's org, carried so RLS can scope it the
--    same way as every other org table.
CREATE TABLE IF NOT EXISTS public.user_identities (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES public.users(id),
    org_id      uuid NOT NULL REFERENCES public.organizations(id),
    auth0_sub   text NOT NULL,
    issuer      text NOT NULL,
    email       text,
    linked_via  text NOT NULL DEFAULT 'verified_email'
                CHECK (linked_via IN ('verified_email', 'duplicate_merge', 'operator')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT user_identities_auth0_sub_key UNIQUE (auth0_sub)
);
CREATE INDEX IF NOT EXISTS user_identities_user_id_idx ON public.user_identities (user_id);

ALTER TABLE public.user_identities ENABLE ROW LEVEL SECURITY;

-- A policy for EVERY operation (CLAUDE.md: a missing UPDATE policy silently
-- matches zero rows). Bootstrap leg = the caller's own sub, exactly like users.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_identities' AND policyname = 'user_identities_select') THEN
    CREATE POLICY user_identities_select ON public.user_identities FOR SELECT USING (
        auth0_sub = NULLIF(current_setting('app.current_auth0_sub', true), '')
        OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_identities' AND policyname = 'user_identities_insert') THEN
    CREATE POLICY user_identities_insert ON public.user_identities FOR INSERT WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_identities' AND policyname = 'user_identities_update') THEN
    CREATE POLICY user_identities_update ON public.user_identities FOR UPDATE USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true')
      WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'user_identities' AND policyname = 'user_identities_delete') THEN
    CREATE POLICY user_identities_delete ON public.user_identities FOR DELETE USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true');
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_identities TO app_service;

-- 3. The one new permission AUTHZ1 introduces: the GL had no gate at all, and
--    no existing permission names it. (resource, action) is UNIQUE.
INSERT INTO public.permissions (name, resource, action)
VALUES ('manage_ledger', 'ledger', 'manage')
ON CONFLICT (name) DO NOTHING;
