-- workflowpermsfix.structural — Part 1
-- ============================================================================
-- Closes the ZERO-GRANTS gap on the three workflow permissions
--   author_workflows / view_workflow_runs / configure_workflow_triggers
-- confirmed by schedulerdiscovery.lowrisk (docs/WORKFLOW_SCHEDULER_DISCOVERY_
-- FINDINGS.md §2.4): all three exist in the global `permissions` catalog and
-- have ZERO rows in role_permissions, profile_permissions AND
-- permission_set_permissions, so only a super_admin can reach any of the nine
-- workflow endpoints.
--
-- Modelled directly on the view_portfolio / manage_portfolio precedent, which
-- is granted on BOTH axes. Three measured facts shape the deviations, each
-- called out inline:
--
--   (a) There is no `org_admin` row in `roles`. The RBAC role vocabulary is
--       super_admin / admin / advisor / member / … ; `org_admin` exists only as
--       a `users.role` TEXT value. The precedent's admin-tier role is `admin`,
--       so `admin` is what the role axis gets.
--   (b) routers/workflows._require_workflow_permission consults ONLY the
--       profile axis (services.profiles.user_has_permission = profile_permissions
--       ∪ permission_set_permissions). The role-axis grants below are for parity
--       with the precedent and for services.rbac.has_permission consumers; they
--       cannot by themselves unblock anybody on the workflow endpoints.
--   (c) All three real `org_admin` users carry profile_id IS NULL, and no
--       "Org Admin" profile exists — so granting only to Adviser / CSA Ops would
--       leave every real org_admin still 403'd and the gap intact. This script
--       seeds the Org Admin profile the router's own comment presumes
--       ("everyone else — including an Org Admin — must hold permission_key via
--       their Profile") and assigns it to org_admin rows that have no profile.
--       Purely ADDITIVE: profile_id IS NULL grants zero permissions today.
--
-- Idempotent. Safe to re-run.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. The Org Admin profile (org-scoped; 2nd Act only — Hollisworks has zero
--    users, zero roles and zero profiles, exactly as the portfolio precedent
--    does, so nothing is seeded there).
-- ---------------------------------------------------------------------------
INSERT INTO profiles (org_id, name, description, is_seed)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Org Admin',
    'Org-level administrator. Authors workflow definitions, configures '
    'triggers and watches the run console. Additive only — assigning it '
    'never removes a capability.',
    true
)
ON CONFLICT (org_id, name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. PROFILE axis — the axis the workflow gate actually reads.
--
--    author_workflows + configure_workflow_triggers: Org Admin only. Authoring
--    a BPMN definition decides what the platform may do autonomously, and a
--    trigger decides when it fires; both are org-governance acts, narrower than
--    manage_portfolio (which Adviser holds because an adviser manages a client's
--    portfolio, not the org's governance).
--
--    view_workflow_runs: broader, mirroring view_portfolio's wider profile list
--    — Org Admin + Adviser + CSA / Ops. Deliberately NOT `Member` or
--    `Community Member`, even though view_portfolio includes `Member`:
--    view_portfolio is member-facing (a member's own holdings), whereas the run
--    console is mounted under /admin/*, lists EVERY run in the org including
--    other people's, and exposes error_detail and started_by identity.
-- ---------------------------------------------------------------------------
INSERT INTO profile_permissions (org_id, profile_id, permission_key)
SELECT p.org_id, p.id, g.permission_key
FROM (VALUES
    ('Org Admin',  'author_workflows'),
    ('Org Admin',  'configure_workflow_triggers'),
    ('Org Admin',  'view_workflow_runs'),
    ('Adviser',    'view_workflow_runs'),
    ('CSA / Ops',  'view_workflow_runs')
) AS g(profile_name, permission_key)
JOIN profiles p
  ON p.name = g.profile_name
 AND p.org_id = '00000000-0000-0000-0000-000000000001'
-- Never grant a key that is not in the global catalog.
JOIN permissions pm ON pm.name = g.permission_key
ON CONFLICT (profile_id, permission_key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. ROLE axis — parity with the view_portfolio / manage_portfolio precedent.
--
--    author_workflows + configure_workflow_triggers -> admin, super_admin
--      (manage_portfolio's list minus `advisor`, per the reasoning above).
--    view_workflow_runs -> admin, super_admin, advisor, investment_staff,
--      support_staff — every STAFF role, mirroring view_portfolio's read-only
--      breadth but excluding `member` for the same /admin/* reason as §2.
-- ---------------------------------------------------------------------------
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, pm.id
FROM (VALUES
    ('admin',            'author_workflows'),
    ('super_admin',      'author_workflows'),
    ('admin',            'configure_workflow_triggers'),
    ('super_admin',      'configure_workflow_triggers'),
    ('admin',            'view_workflow_runs'),
    ('super_admin',      'view_workflow_runs'),
    ('advisor',          'view_workflow_runs'),
    ('investment_staff', 'view_workflow_runs'),
    ('support_staff',    'view_workflow_runs')
) AS g(role_name, permission_name)
JOIN roles r
  ON r.name = g.role_name
 AND r.org_id = '00000000-0000-0000-0000-000000000001'
JOIN permissions pm ON pm.name = g.permission_name
ON CONFLICT (role_id, permission_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 4. Give the real org_admin users the profile the gate requires.
--
--    Without this the grants above change nothing for any real user: the gate
--    is profile-only and all three org_admin rows have profile_id IS NULL.
--    Strictly additive and strictly scoped — an org_admin who already has a
--    profile is left alone, and no other role is touched.
-- ---------------------------------------------------------------------------
UPDATE users u
SET profile_id = p.id,
    updated_at = now()
FROM profiles p
WHERE p.org_id = '00000000-0000-0000-0000-000000000001'
  AND p.name = 'Org Admin'
  AND u.org_id = p.org_id
  AND u.role = 'org_admin'
  AND u.profile_id IS NULL;

COMMIT;
