-- PERMS1 part 1 — the org_admin role's baseline (2026-11-04).
--
-- Grants every EXISTING org_admin role (one per org, roles (org_id, name) UNIQUE) the nine keys of
-- services.rbac.PERMS1_ORG_ADMIN_ADDED. rbac.ensure_org_admin_role grants the same list to an org_admin
-- role created later. Additive and idempotent (ON CONFLICT DO NOTHING); no approve_* key; no user,
-- profile or permission set is touched. Every key is already in the permissions catalog (checked live
-- 2026-11-04) — the INSERT ... SELECT joins on it, so a missing key would grant nothing rather than fail.
--
-- Run as the operator (role_permissions writes need platform scope).

BEGIN;
SELECT set_config('app.is_super_admin', 'true', true);

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM roles r
  JOIN permissions p ON p.name IN (
       'view_portfolio', 'view_members', 'view_dashboard', 'view_deals', 'view_workflow_runs',
       'author_workflows', 'configure_workflow_triggers', 'manage_deals', 'manage_members')
 WHERE r.name = 'org_admin'
ON CONFLICT DO NOTHING;

COMMIT;
