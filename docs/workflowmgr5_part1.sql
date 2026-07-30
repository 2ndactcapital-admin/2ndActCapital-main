-- Workflow Manager Phase 5 — Part 1 seed (granular permissions)
--
-- Adds workflow-manager permission keys to the GLOBAL `permissions` catalog —
-- the same catalog the SOC Profiles / Permission-Sets admin UI renders
-- (GET /admin/permissions) and that services.profiles.user_has_permission
-- enforces against (via profile_permissions / permission_set_permissions).
--
-- These REPLACE the blanket `can_manage_org_settings` gate on the workflow
-- endpoints (Phases 3-4) with four SEPARATELY grantable capabilities:
--   * author_workflows            — author/edit a workflow (library, editor,
--                                   save-a-new-version, version history).
--                                   In this platform's generate-once + save-
--                                   new-version model there is no distinct
--                                   "publish" step beyond save, so publishing
--                                   is covered by author_workflows.
--   * view_workflow_runs          — view the Run Console + drill into a run.
--   * configure_workflow_triggers — view/configure the scheduler / triggers.
--
-- The `permissions` table is GLOBAL (no org_id): name UNIQUE, (resource, action)
-- UNIQUE. `resource` groups the checklist UI; `action` is its row label.
--
-- IMPORTANT: this seeds ONLY the catalog rows. It deliberately does NOT insert
-- any profile_permissions / permission_set_permissions grant, so no existing
-- seeded Profile (Member / Community Member / Adviser / CSA-Ops) silently gains
-- these capabilities — each new permission must be granted deliberately.

INSERT INTO permissions (name, resource, action)
VALUES
    ('author_workflows',            'workflows', 'author'),
    ('view_workflow_runs',          'workflows', 'view_runs'),
    ('configure_workflow_triggers', 'workflows', 'configure_triggers')
ON CONFLICT (name) DO NOTHING;
