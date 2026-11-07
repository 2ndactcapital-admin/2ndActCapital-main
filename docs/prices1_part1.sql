-- PRICES1 part 1 — end-of-day prices through the vendor-neutral security-data layer.
-- Additive: two columns on the price store, one CHECK widened (keeps every value it admitted before), one new
-- platform table (price coverage with its reason, every operation under RLS), two provider rows (licence facts) and
-- their place in the platform rank for 'eod_price'. Writes NO price, security or org row.
-- Applied by apps/api/scripts/_apply_prices1.py --schema on the operator DSN (the table owner; the Supabase MCP
-- cancels migration text containing DROP). Idempotent: every statement can run twice.

-- (no BEGIN / COMMIT here: _apply_prices1.py runs the whole file in ONE transaction)

-- 1. the price store keeps the UNADJUSTED close in `price`; the source's adjustment factor (adjusted close ÷ close,
--    splits + dividends) and the rest of the bar (open / high / low / volume, as strings) beside it
ALTER TABLE portfolio.securities_global_prices ADD COLUMN IF NOT EXISTS adjustment_factor numeric;
ALTER TABLE portfolio.securities_global_prices ADD COLUMN IF NOT EXISTS bar jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE portfolio.securities_global_prices DROP CONSTRAINT IF EXISTS sec_global_price_adj_factor_chk;
ALTER TABLE portfolio.securities_global_prices ADD CONSTRAINT sec_global_price_adj_factor_chk CHECK (
    adjustment_factor IS NULL OR adjustment_factor > 0);

-- 2. quality kinds: + invalid_value (a zero / negative / unparseable close — never stored) and currency_mismatch
--    (the source quotes a currency other than the security's — never stored)
ALTER TABLE portfolio.data_quality_events DROP CONSTRAINT IF EXISTS data_quality_events_kind_chk;
ALTER TABLE portfolio.data_quality_events ADD CONSTRAINT data_quality_events_kind_chk CHECK (kind = ANY (ARRAY[
    'stale', 'gap', 'outlier', 'disagreement', 'budget_exhausted', 'invalid_value', 'currency_mismatch']));

-- 3. price coverage WITH ITS REASON. securities_global.price_coverage stays the one flag every reader filters on;
--    this table records why (valid axis: a changed finding closes the live row and inserts the new one).
CREATE TABLE IF NOT EXISTS portfolio.security_price_coverage (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  global_security_id  uuid NOT NULL REFERENCES portfolio.securities_global(id),
  status              text NOT NULL,
  provider_key        text,
  reason              text NOT NULL,
  first_date          date,
  last_date           date,
  points              integer NOT NULL DEFAULT 0,
  checked_at          timestamptz NOT NULL DEFAULT now(),
  run_id              uuid,
  owner_scope         text NOT NULL DEFAULT 'platform',
  org_id              uuid,
  created_at          timestamptz NOT NULL DEFAULT now(),
  valid_from          timestamptz NOT NULL DEFAULT now(),
  valid_to            timestamptz,
  system_from         timestamptz NOT NULL DEFAULT now(),
  system_to           timestamptz,
  CONSTRAINT security_price_coverage_status_chk CHECK (status IN ('has_series', 'no_public_source', 'unknown')),
  CONSTRAINT security_price_coverage_platform_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
  CONSTRAINT security_price_coverage_points_chk CHECK (points >= 0),
  CONSTRAINT security_price_coverage_reason_chk CHECK (length(btrim(reason)) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS security_price_coverage_live_uq ON portfolio.security_price_coverage
    (global_security_id) WHERE valid_to IS NULL AND system_to IS NULL;

ALTER TABLE portfolio.security_price_coverage ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS security_price_coverage_read ON portfolio.security_price_coverage;
CREATE POLICY security_price_coverage_read ON portfolio.security_price_coverage FOR SELECT USING (true);
DROP POLICY IF EXISTS security_price_coverage_super_admin_insert ON portfolio.security_price_coverage;
CREATE POLICY security_price_coverage_super_admin_insert ON portfolio.security_price_coverage FOR INSERT WITH CHECK (
    NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS security_price_coverage_super_admin_update ON portfolio.security_price_coverage;
CREATE POLICY security_price_coverage_super_admin_update ON portfolio.security_price_coverage FOR UPDATE USING (
    NULLIF(current_setting('app.is_super_admin', true), '') = 'true') WITH CHECK (
    NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
DROP POLICY IF EXISTS security_price_coverage_super_admin_delete ON portfolio.security_price_coverage;
CREATE POLICY security_price_coverage_super_admin_delete ON portfolio.security_price_coverage FOR DELETE USING (
    NULLIF(current_setting('app.is_super_admin', true), '') = 'true');
GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.security_price_coverage TO app_service;

-- 4. the providers. yahoo_chart: the primary free end-of-day source (keyless). tiingo: the fallback — written,
--    registered NOT CONNECTED until a TIINGO_API_KEY exists in Doppler (none today). Licence facts: Discovery 1b.
INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                      lic_redistribution, retention_after_cancel, attribution, terms_note,
                                      confirmed_on, daily_call_budget)
SELECT 'yahoo_chart', 'Yahoo Finance chart (end-of-day)', 'public_api', 'active', ARRAY['eod_price'], true,
       'not_allowed', false, 'unknown', 'Prices: Yahoo Finance',
       'query1.finance.yahoo.com/v8/finance/chart — unofficial, keyless, no published rate limit. Yahoo''s terms '
       || 'allow personal, non-commercial use only; used for INTERNAL adviser analysis under the owner''s recorded '
       || 'assumption (Joe, 2026-10-03, the market-indicators module) while there are no paid customers. Never shown '
       || 'to a member (staff see it labelled "internal"); never redistributed. A LAUNCH BLOCKER: replace with a '
       || 'licensed vendor (plug in behind the same contract) before any external customer sees a price. Stores the '
       || 'unadjusted close (Yahoo''s close is split-adjusted — the adapter undoes later splits) and the adjustment '
       || 'factor adjclose ÷ close.',
       DATE '2026-11-07', 2000
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_providers WHERE key = 'yahoo_chart' AND valid_to IS NULL
                  AND system_to IS NULL);

INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                      lic_redistribution, retention_after_cancel, attribution, terms_note,
                                      confirmed_on, daily_call_budget)
SELECT 'tiingo', 'Tiingo end-of-day (free tier)', 'public_api', 'not_connected', ARRAY['eod_price'], true,
       'not_allowed', false, 'delete', 'Prices: Tiingo',
       'api.tiingo.com/tiingo/daily/<ticker>/prices — needs TIINGO_API_KEY (absent from Doppler on 2026-11-07, so '
       || 'NOT CONNECTED). Free tier: about 1,000 requests a day and 500 distinct symbols a month, 30+ years of US '
       || 'stocks, ETFs and mutual funds, raw and adjusted close + split factor. Free use is personal / internal; '
       || 'showing or redistributing data needs Tiingo''s commercial licence. Fallback to yahoo_chart.',
       DATE '2026-11-07', 900
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_providers WHERE key = 'tiingo' AND valid_to IS NULL
                  AND system_to IS NULL);

-- platform rank for eod_price: custodian 1 → csv_upload 2 → adviser_entry 3 → platform_entry 4 (unchanged), then the
-- free sources. A custodian / uploaded / entered value still wins; a fresh public close fills the rest.
INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope)
SELECT 'eod_price', 'yahoo_chart', 5, 'platform'
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'eod_price'
                  AND provider_key = 'yahoo_chart' AND valid_to IS NULL AND system_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'eod_price'
                  AND rank = 5 AND valid_to IS NULL AND system_to IS NULL);

INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope)
SELECT 'eod_price', 'tiingo', 6, 'platform'
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'eod_price'
                  AND provider_key = 'tiingo' AND valid_to IS NULL AND system_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'eod_price'
                  AND rank = 6 AND valid_to IS NULL AND system_to IS NULL);
