-- REVIEWQ1 part 1 — the Security master Review queue's choices, Known discrepancies and alternate identifiers.
-- Additive: two CHECKs widened (old literals kept), one column with a constant default (metadata only), one new
-- append-only platform table. Writes NO review item, security or identifier row.
-- Applied by apps/api/scripts/_apply_reviewq1.py --schema on the operator DSN (the table owner). Idempotent.
-- (no BEGIN / COMMIT here: _apply_reviewq1.py runs the whole file in ONE transaction)

-- 1. statuses: the three choices (old 'resolved' / 'dismissed' kept for the legacy decision route and history)
ALTER TABLE portfolio.security_master_review DROP CONSTRAINT IF EXISTS security_master_review_status_chk;
ALTER TABLE portfolio.security_master_review ADD CONSTRAINT security_master_review_status_chk
  CHECK (status IN ('open', 'resolved', 'dismissed', 'used_incoming', 'kept_existing', 'kept_both'));

-- 2. kinds: the list loader's misses (a list member's identifier that maps to a master security / to none)
ALTER TABLE portfolio.security_master_review DROP CONSTRAINT IF EXISTS security_master_review_kind_chk;
ALTER TABLE portfolio.security_master_review ADD CONSTRAINT security_master_review_kind_chk
  CHECK (kind IN ('ticker_conflict', 'ticker_cusip_conflict', 'identifier_conflict', 'type_mismatch',
                  'exchange_mismatch', 'ambiguous_match', 'ticker_moved', '13f_deleted', '13f_conflict',
                  'list_match', 'add_to_master'));

-- the Known discrepancies lookup (natural key of a decided item)
CREATE INDEX IF NOT EXISTS security_master_review_known_ix ON portfolio.security_master_review
  (kind, subject_key, field, incoming_value, provider_key)
  WHERE status IN ('kept_existing', 'kept_both') AND valid_to IS NULL AND system_to IS NULL;

-- 3. Keep both: an ALTERNATE identifier beside the existing one. Value uniqueness (uq_sec_global_ident_issuer_assigned:
--    one live cusip / isin / figi / lei value on ONE security) is unchanged and already right.
ALTER TABLE portfolio.securities_global_identifiers
  ADD COLUMN IF NOT EXISTS is_alternate boolean NOT NULL DEFAULT false;

-- 4. "seen again": one append-only row per later source sighting of a Known item's natural key
CREATE TABLE IF NOT EXISTS portfolio.security_master_review_sightings (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind                text NOT NULL,
  subject_key         text NOT NULL,
  field               text NOT NULL DEFAULT '',
  incoming_value      text NOT NULL DEFAULT '',
  provider_key        text NOT NULL,
  global_security_id  uuid,
  review_id           uuid,
  run_id              uuid,
  seen_at             timestamptz NOT NULL DEFAULT now(),
  owner_scope         text NOT NULL DEFAULT 'platform',
  org_id              uuid,
  CONSTRAINT security_master_review_sightings_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL)
);
CREATE INDEX IF NOT EXISTS security_master_review_sightings_key_ix ON portfolio.security_master_review_sightings
  (kind, subject_key, field, incoming_value, provider_key, seen_at);

-- RLS — every operation, Super Admin only (like the review table itself: its SELECT is Super Admin too)
DO $$
DECLARE t text := 'security_master_review_sightings';
BEGIN
  EXECUTE format('ALTER TABLE portfolio.%I ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_select', t);
  EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR SELECT USING ('
                 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_select', t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_insert', t);
  EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR INSERT WITH CHECK ('
                 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_insert', t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_update', t);
  EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR UPDATE USING ('
                 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'') WITH CHECK ('
                 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_update', t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON portfolio.%I', t || '_super_admin_delete', t);
  EXECUTE format('CREATE POLICY %I ON portfolio.%I FOR DELETE USING ('
                 'NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')', t || '_super_admin_delete', t);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.%I TO app_service', t);
END $$;
