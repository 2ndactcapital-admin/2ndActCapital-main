-- ─────────────────────────────────────────────────────────────────────────
-- Headless Multi-Tenant — Sprint 1 — Part 1 SQL — organizations pre-auth
-- carve-out for subdomain → org resolution.
-- ─────────────────────────────────────────────────────────────────────────
-- WHY THIS EXISTS (the real Task-1a discovery):
--
-- The existing GET /theme/public endpoint does an UNAUTHENTICATED
-- `SELECT ... FROM organizations WHERE slug = $1`. It was assumed to have
-- "already solved" pre-auth org lookup under live RLS. It has NOT. It works in
-- production ONLY because the app currently connects as the RLS-BYPASSING
-- `postgres` role (see services/database.py NOTE). Under the real non-bypass
-- `app_service` role — the migration target — the ONLY policy on organizations
-- is:
--
--   organizations_self_or_super  (FOR ALL):
--     id = NULLIF(current_setting('app.current_org_id', true),'')::uuid
--     OR current_setting('app.is_super_admin', true) = 'true'
--
-- A pre-auth request has NEITHER GUC set, so that policy returns ZERO ROWS.
-- Proven empirically against app_service during this sprint: the slug lookup
-- returns nothing, and /theme/public would silently degrade to DEFAULT_SETTINGS
-- once the app is switched to app_service. There is NO separate bypass
-- connection and NO existing SELECT-only carve-out — the only thing making it
-- "work" is the bypass role.
--
-- THE FIX (minimal, principled): a SELECT-only carve-out that opens
-- organizations for reading ONLY during the pre-auth window — i.e. when there
-- is no org context AND the caller is not a super admin. This is exactly the
-- moment the subdomain resolver and the login-branding /theme/public run,
-- before anyone has a token. Org (id, name, slug) is effectively public
-- metadata by design: slugs are about to become live public subdomains
-- (*.ripasso.com) and names/branding already render on the unauthenticated
-- login screen.
--
-- SAFETY PROPERTIES:
--   * FOR SELECT only  → pre-auth INSERT/UPDATE/DELETE stay governed by the
--     existing organizations_self_or_super policy (no pre-auth writes).
--   * Gated to the pre-auth window (no org context, not super admin) → once a
--     member is authenticated (org context set), this policy is INERT and
--     organizations_self_or_super governs: a member still sees ONLY their own
--     org row. Cross-tenant invisibility for authenticated users is preserved.
--   * Permissive policies are OR-combined, so super admins (who match
--     organizations_self_or_super) are unaffected.
--   * Every GUC read is NULLIF/IS-DISTINCT-FROM guarded so a reused pooled
--     backend (whose custom GUC reverts to '' after a prior SET LOCAL)
--     behaves deterministically. See services/database.py for the rationale.
--
-- This is a strict ADDITION. It changes nothing for the bypass-role production
-- path today, and it does not alter authenticated behavior. It only makes the
-- pre-auth resolver genuinely correct under app_service.

ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS organizations_preauth_resolve ON public.organizations;

CREATE POLICY organizations_preauth_resolve
    ON public.organizations
    FOR SELECT
    USING (
        NULLIF(current_setting('app.current_org_id', true), '') IS NULL
        AND current_setting('app.is_super_admin', true) IS DISTINCT FROM 'true'
    );

-- app_service already holds SELECT on organizations; re-granted here for
-- reproducibility (idempotent).
GRANT SELECT ON public.organizations TO app_service;
