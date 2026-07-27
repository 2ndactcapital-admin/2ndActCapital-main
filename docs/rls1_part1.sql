-- RLS Phase 1 — Part 1 SQL (pilot policy on trusted_contacts)
--
-- Canonical, idempotent DDL for the ONE pilot table this sprint proves the
-- mechanism on. This reflects the DEPLOYED state after hardening.
--
-- Context:
--   * A non-bypass role `app_service` exists (rolbypassrls=false). It is NOT the
--     live connection — DATABASE_URL still points at the RLS-bypassing `postgres`
--     role. Policies are only evaluated for non-bypass roles, so this file has
--     ZERO effect on current production behavior.
--   * RLS is already ENABLED on trusted_contacts (as on ~74 other org_id tables).
--     Only trusted_contacts gets a POLICY in this sprint.
--
-- The application sets two transaction-local GUCs on every connection
-- (services/database.py, via SET LOCAL / set_config(..., is_local => true)):
--     app.current_org_id  — the caller's org UUID, or '' when no context
--     app.is_super_admin   — 'true' | 'false'
--
-- IMPORTANT — why NULLIF: custom "placeholder" GUCs (app.*) revert to '' (empty
-- string), NOT NULL, after a SET LOCAL commits on a pooled connection that is
-- later reused. A naive predicate `org_id = current_setting(...)::uuid` would
-- then raise `invalid input syntax for type uuid: ""` on that reused connection
-- when no org context is set — breaking the required safe default-deny ("return
-- ZERO rows, not an error"). NULLIF(current_setting(...), '') maps both '' and
-- NULL to NULL, so the org branch matches nothing. A real UUID still casts.

ALTER TABLE public.trusted_contacts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS trusted_contacts_org_isolation ON public.trusted_contacts;

CREATE POLICY trusted_contacts_org_isolation ON public.trusted_contacts
  FOR ALL
  USING (
    org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
    OR current_setting('app.is_super_admin', true) = 'true'
  )
  WITH CHECK (
    org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
    OR current_setting('app.is_super_admin', true) = 'true'
  );
