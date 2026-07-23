-- SOC Phase 1 — Part 1 DDL + seed
-- Profiles / permission-linking junction tables + seed personas.
--
-- Design notes (from Task 1 discovery):
--   The Sprint-11 action registry (services/action_registry.py, mirrored to
--   assistant_action_catalog) gates each action with a SINGLE flat string:
--   AssistantAction.required_permission (e.g. 'manage_deals'). These strings
--   line up with permissions.name in the RBAC catalog. So the linking tables
--   store a flat `permission_key text` — NOT a resource/action/autonomy object.
--
--   profile_id is a NEW additive persona layer for non-admin users. Super/Org
--   Admin remain on users.role + services/rbac.py helpers, untouched here.

-- ── Junction tables ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile_permissions (
    id             uuid        NOT NULL DEFAULT uuid_generate_v4(),
    org_id         uuid        NOT NULL,
    profile_id     uuid        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    permission_key text        NOT NULL,   -- matches assistant_action_catalog.required_permission / permissions.name
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT profile_permissions_pkey PRIMARY KEY (id),
    CONSTRAINT profile_permissions_profile_key_unique UNIQUE (profile_id, permission_key)
);
CREATE INDEX IF NOT EXISTS profile_permissions_profile_id_idx
    ON profile_permissions (profile_id);

CREATE TABLE IF NOT EXISTS permission_set_permissions (
    id                uuid        NOT NULL DEFAULT uuid_generate_v4(),
    org_id            uuid        NOT NULL,
    permission_set_id uuid        NOT NULL REFERENCES permission_sets(id) ON DELETE CASCADE,
    permission_key    text        NOT NULL,   -- matches assistant_action_catalog.required_permission / permissions.name
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT permission_set_permissions_pkey PRIMARY KEY (id),
    CONSTRAINT permission_set_permissions_set_key_unique UNIQUE (permission_set_id, permission_key)
);
CREATE INDEX IF NOT EXISTS permission_set_permissions_set_id_idx
    ON permission_set_permissions (permission_set_id);

-- ── Seed personas for 2nd Act's org (is_seed = true) ─────────────────────
-- Personas seeded: Member, Community Member, Adviser, CSA / Ops.
--   Mesh End User    — EXCLUDED (no-Mesh rescope; no Mesh anything in the app).
--   Admin            — EXCLUDED (handled by users.role super_admin/org_admin).
INSERT INTO profiles (org_id, name, description, is_seed)
VALUES
    ('00000000-0000-0000-0000-000000000001', 'Member',
     'RIA client / platform member — reads their dashboard, marketplace, portfolio and community; can indicate interest in deals.', true),
    ('00000000-0000-0000-0000-000000000001', 'Community Member',
     'Community-only participant — access to the community area and dashboard, no marketplace/portfolio write.', true),
    ('00000000-0000-0000-0000-000000000001', 'Adviser',
     'Client-facing staff — read/write on member- and deal-facing workflows.', true),
    ('00000000-0000-0000-0000-000000000001', 'CSA / Ops',
     'Client service / operations support — narrower staff access, documents and read visibility, no deal authority.', true)
ON CONFLICT (org_id, name) DO NOTHING;

-- ── Seed starting permission bundles (Org Admin can edit later) ──────────
-- Values are real permissions.name / required_permission strings from the
-- deployed catalog. 'staff' is the staff-gate key used by require_staff.
WITH seed AS (
    SELECT p.id AS profile_id, p.org_id, k.permission_key
    FROM profiles p
    JOIN (VALUES
        -- Member: read across the member surfaces + indicate interest
        ('Member', 'view_dashboard'),
        ('Member', 'view_marketplace'),
        ('Member', 'view_deals'),
        ('Member', 'view_portfolio'),
        ('Member', 'view_community'),
        ('Member', 'view_insurance'),
        ('Member', 'indicate_interest'),
        -- Community Member: community + dashboard only
        ('Community Member', 'view_community'),
        ('Community Member', 'view_dashboard'),
        -- Adviser: client-facing read + write authority
        ('Adviser', 'staff'),
        ('Adviser', 'view_dashboard'),
        ('Adviser', 'view_members'),
        ('Adviser', 'view_portfolio'),
        ('Adviser', 'view_deals'),
        ('Adviser', 'view_marketplace'),
        ('Adviser', 'manage_deals'),
        ('Adviser', 'manage_portfolio'),
        ('Adviser', 'manage_members'),
        ('Adviser', 'score_deal'),
        ('Adviser', 'vote_deal'),
        -- CSA / Ops: narrower staff support set, no deal authority
        ('CSA / Ops', 'staff'),
        ('CSA / Ops', 'view_dashboard'),
        ('CSA / Ops', 'view_members'),
        ('CSA / Ops', 'view_portfolio'),
        ('CSA / Ops', 'view_deals'),
        ('CSA / Ops', 'view_marketplace'),
        ('CSA / Ops', 'manage_documents')
    ) AS k(profile_name, permission_key) ON k.profile_name = p.name
    WHERE p.org_id = '00000000-0000-0000-0000-000000000001'
      AND p.is_seed = true
)
INSERT INTO profile_permissions (org_id, profile_id, permission_key)
SELECT org_id, profile_id, permission_key FROM seed
ON CONFLICT (profile_id, permission_key) DO NOTHING;
