-- ─────────────────────────────────────────────────────────────────────────
-- RLS Phase 2 — Part 1 SQL — users table carve-out
-- ─────────────────────────────────────────────────────────────────────────
-- This is a RECORD of the policy that was applied to the deployed database
-- BEFORE the Phase-2 application changes (verified live via pg_policies during
-- the sprint). It is captured here for reproducibility; it is NOT re-run by the
-- app. The Phase-2 application work (services/database.py third GUC +
-- main.py middleware ordering) is what makes this policy usable under the
-- non-bypass app_service role.
--
-- The `users` table is the highest-sensitivity table in the app: every
-- request's identity resolution depends on it, and a brand-new user's very
-- first request must be able to read/insert its OWN row BEFORE any org context
-- exists. Hence the three-way OR:
--   1. self / bootstrap : auth0_sub = app.current_auth0_sub  (NO org needed)
--   2. org-scoped listing: org_id  = app.current_org_id      (admin screens)
--   3. platform staff    : app.is_super_admin = 'true'
--
-- Every GUC read is NULLIF(current_setting(..., true), '')-guarded so that a
-- reused pooled connection (whose custom GUC reverts to '' after a prior
-- SET LOCAL) DEFAULT-DENIES with zero rows instead of erroring on ''::uuid.
-- See apps/api/services/database.py for the full rationale.
--
-- Applied as: cmd = ALL, so the identical predicate gates SELECT/UPDATE/DELETE
-- (USING) and INSERT/UPDATE (WITH CHECK) — a new row's auth0_sub must match
-- app.current_auth0_sub (the ensure_user bootstrap INSERT path).

ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS users_bootstrap_and_org_visibility ON public.users;

CREATE POLICY users_bootstrap_and_org_visibility
    ON public.users
    FOR ALL
    USING (
        auth0_sub = NULLIF(current_setting('app.current_auth0_sub', true), '')
        OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        auth0_sub = NULLIF(current_setting('app.current_auth0_sub', true), '')
        OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

-- The non-bypass application role used once the connection is switched off the
-- RLS-bypassing `postgres` role (a separate, manual step — NOT this sprint).
GRANT SELECT, INSERT, UPDATE, DELETE ON public.users TO app_service;
