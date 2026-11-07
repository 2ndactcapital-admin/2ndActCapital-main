-- SECMASTER2 part 1 — SEC mutual-fund share classes in the security master, FinanceDatabase category groups.
-- Additive: two CHECKs widened (each keeps every value it admitted before), one shape CHECK, one partial unique
-- index, one provider row, one rank row. Writes NO security, identifier, attribute or org row.
-- Applied by apps/api/scripts/_apply_secmaster2.py --schema on the operator DSN (the table owner; the Supabase MCP
-- cancels migration text containing DROP). Idempotent: every statement can run twice.

-- (no BEGIN / COMMIT here: _apply_secmaster2.py runs the whole file in ONE transaction)

-- 1. identifier types: + the SEC's series and share-class ids (company_tickers_mf.json: S000006027 / C000100045)
ALTER TABLE portfolio.securities_global_identifiers DROP CONSTRAINT IF EXISTS sec_global_ident_type_chk;
ALTER TABLE portfolio.securities_global_identifiers ADD CONSTRAINT sec_global_ident_type_chk CHECK (id_type = ANY (
    ARRAY['cusip', 'isin', 'ticker', 'sedol', 'figi', 'lei', 'internal', 'sec_series_id', 'sec_class_id']));

ALTER TABLE portfolio.securities_global_identifiers DROP CONSTRAINT IF EXISTS sec_global_ident_sec_shape_chk;
ALTER TABLE portfolio.securities_global_identifiers ADD CONSTRAINT sec_global_ident_sec_shape_chk CHECK (
    (id_type <> 'sec_series_id' OR id_value ~ '^S[0-9]{9}$')
    AND (id_type <> 'sec_class_id' OR id_value ~ '^C[0-9]{9}$'));

-- a share class is ONE security: one live row per class id (a series id is shared by its classes — not unique)
CREATE UNIQUE INDEX IF NOT EXISTS uq_sec_global_ident_sec_class
    ON portfolio.securities_global_identifiers (id_value)
    WHERE id_type = 'sec_class_id' AND valid_to IS NULL AND system_to IS NULL;

-- 2. reference fields: + fund_category_group (FinanceDatabase's category_group, kept beside its category: "Emerging
--    Markets" is filed under Equities AND Fixed Income) and fund_structure (mutual_fund / money_market / etf)
ALTER TABLE portfolio.security_reference_attributes DROP CONSTRAINT IF EXISTS security_reference_attributes_field_chk;
ALTER TABLE portfolio.security_reference_attributes ADD CONSTRAINT security_reference_attributes_field_chk CHECK (
    field = ANY (ARRAY['exchange', 'cik', 'sector', 'industry', 'industry_group', 'country', 'is_etf', 'fund_category',
                       'listing_status', 'first_seen', 'last_seen', 'absent_runs', 'fund_category_group',
                       'fund_structure']));

ALTER TABLE portfolio.security_reference_attributes DROP CONSTRAINT IF EXISTS security_reference_attributes_structure_chk;
ALTER TABLE portfolio.security_reference_attributes ADD CONSTRAINT security_reference_attributes_structure_chk CHECK (
    field <> 'fund_structure' OR value = ANY (ARRAY['mutual_fund', 'money_market', 'etf']));

-- 3. the provider (licence facts) and its place in the platform rank for 'reference' (after the five of SECMASTER1)
INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                      lic_redistribution, retention_after_cancel, attribution, terms_note,
                                      confirmed_on, daily_call_budget)
SELECT 'sec_mutual_funds', 'SEC mutual fund tickers (series + share class)', 'regulator_filing', 'active',
       ARRAY['reference'], true, 'allowed', true, 'keep', 'Source: U.S. Securities and Exchange Commission',
       'sec.gov/files/company_tickers_mf.json — one row per registered-fund share class (CIK, series id, class id, '
       || 'ticker). U.S. government public record (17 U.S.C. 105): may be shown and redistributed. Fetched under the '
       || 'SEC fair-access policy: a declared User-Agent naming the company and a company contact (EDGAR_USER_AGENT, '
       || 'never a personal address), at most 10 requests per second, through services/edgar_fetch. The file carries '
       || 'NO fund names.',
       DATE '2026-11-07', 20
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_providers WHERE key = 'sec_mutual_funds' AND valid_to IS NULL
                  AND system_to IS NULL);

INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope)
SELECT 'reference', 'sec_mutual_funds', 6, 'platform'
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'reference'
                  AND provider_key = 'sec_mutual_funds' AND valid_to IS NULL AND system_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'reference'
                  AND rank = 6 AND valid_to IS NULL AND system_to IS NULL);

