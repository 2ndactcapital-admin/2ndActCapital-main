-- Sprint 25 — open-set document-type classifier: schema + model-override seed.
--
-- Part 1 SQL for the sprint. Two things:
--   1. doc_category_proposals — the "AI proposes, human ratifies" review queue.
--      The classifier NEVER auto-inserts a new canonical category into
--      reference_data; it drops a proposal here for a human to ratify/reject.
--   2. ai.model.document_classifier — a task-specific model override seeded on
--      the default org, following the EXACT Mini-Bedrock convention (category
--      'ai', is_public=false, JSON-encoded scalar). This is one of the two
--      places a literal model string may appear (the other is DEFAULT_SETTINGS
--      in apps/api/services/org_settings.py) — it IS seed data, not call-site
--      logic. Its default equals ai.model.default's Haiku value; an org_admin
--      may override it per-org to a stronger classifier model.

-- ── Review queue ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS doc_category_proposals (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id          uuid NOT NULL,
    proposed_code   text,           -- classifier's suggested snake_case code (may be null)
    proposed_label  text NOT NULL,  -- human-readable category the classifier proposed
    reasoning       text,           -- why the classifier believes this is a new type
    confidence      numeric,        -- classifier confidence 0-1
    source_excerpt  text,           -- short excerpt of the classified document text
    status          text NOT NULL DEFAULT 'pending',  -- pending | ratified | rejected
    created_at      timestamptz NOT NULL DEFAULT now(),
    reviewed_by     uuid,
    reviewed_at     timestamptz
);

CREATE INDEX IF NOT EXISTS doc_category_proposals_org_status_idx
    ON doc_category_proposals (org_id, status);

-- ── Task-specific model override (mini-bedrock convention) ──────────────────
INSERT INTO org_settings (org_id, setting_key, setting_value, category, is_public)
VALUES (
    '00000000-0000-0000-0000-000000000001'::uuid,
    'ai.model.document_classifier',
    '"claude-haiku-4-5-20251001"'::jsonb,
    'ai',
    false
)
ON CONFLICT (org_id, setting_key) DO UPDATE
    SET setting_value = EXCLUDED.setting_value,
        category      = EXCLUDED.category,
        updated_at    = now();
