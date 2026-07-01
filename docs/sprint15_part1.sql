-- Sprint 15 Part 1 Migration
-- Run in Supabase before deploying Sprint 15 code.

-- 1. Add ownership_pct column to entity_relationships
ALTER TABLE entity_relationships
  ADD COLUMN IF NOT EXISTS ownership_pct numeric;

-- 2. Create entity_groups table
CREATE TABLE IF NOT EXISTS entity_groups (
  id            uuid NOT NULL DEFAULT uuid_generate_v4(),
  org_id        uuid NOT NULL,
  name          text NOT NULL,
  description   text,
  created_by    uuid REFERENCES users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id)
);

-- 3. Create entity_group_members table
CREATE TABLE IF NOT EXISTS entity_group_members (
  id            uuid NOT NULL DEFAULT uuid_generate_v4(),
  org_id        uuid NOT NULL,
  group_id      uuid NOT NULL REFERENCES entity_groups(id) ON DELETE CASCADE,
  entity_id     uuid NOT NULL REFERENCES entities(id),
  added_by      uuid REFERENCES users(id),
  added_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (group_id, entity_id)
);

-- 4. Add indexes
CREATE INDEX IF NOT EXISTS idx_entity_relationships_from ON entity_relationships(from_entity_id, org_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_entity_relationships_to ON entity_relationships(to_entity_id, org_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_entity_groups_org ON entity_groups(org_id);
CREATE INDEX IF NOT EXISTS idx_entity_group_members_group ON entity_group_members(group_id);
CREATE INDEX IF NOT EXISTS idx_entity_group_members_entity ON entity_group_members(entity_id);
