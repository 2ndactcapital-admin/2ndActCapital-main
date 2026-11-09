-- CASH03 — the obligation-matched ladder and after-tax instrument selection (part 1: catalog).
--
-- Apply:  python3 apps/api/scripts/_apply_cash03.py --all   (operator DSN, one transaction; idempotent)
--
-- WHAT THIS IS. Two FREE data providers registered through the marketdata1 provider registry, with licence facts,
-- SHIPPED SWITCHED OFF (status 'not_connected' — the nightly and the read path both skip a provider that is not
-- active; a Super Admin switches each on in the provider admin), their place in the platform rank, and five new
-- security-master reference fields. docs/cash/CASH03_NOTES.md §2–§9 is the justification.
--
-- NO NEW TABLE (CASH03_NOTES D2 / D3): Treasury issues and money-market fund classes live in the security master
-- (portfolio.securities_global / _identifiers / security_reference_attributes, through security_master_load.Matcher);
-- auction yields, 7-day yields and WAM / WAL live in portfolio.index_levels; the ladder and the instrument plan are
-- DERIVED at read time and never stored. Every new key fits index_levels_key_chk.
--
-- THE MIGRATION WRITES NO ORG ROWS AND NO DATA ROWS: two platform provider rows, four platform rank rows, two CHECKs.

-- ── 1. Reference fields: Treasury issue facts (treasury_fiscal) and the money-market category (sec_nmfp) ──────
ALTER TABLE portfolio.security_reference_attributes DROP CONSTRAINT IF EXISTS security_reference_attributes_field_chk;
ALTER TABLE portfolio.security_reference_attributes ADD CONSTRAINT security_reference_attributes_field_chk CHECK (
    field = ANY (ARRAY['exchange', 'cik', 'sector', 'industry', 'industry_group', 'country', 'is_etf', 'fund_category',
                       'listing_status', 'first_seen', 'last_seen', 'absent_runs', 'fund_category_group',
                       'fund_structure',
                       -- CASH03
                       'maturity_date', 'issue_date', 'security_term', 'treasury_type', 'mmf_category']));

-- a date field holds an ISO date and nothing else (the ladder sorts and compares them)
ALTER TABLE portfolio.security_reference_attributes DROP CONSTRAINT IF EXISTS security_reference_attributes_date_chk;
ALTER TABLE portfolio.security_reference_attributes ADD CONSTRAINT security_reference_attributes_date_chk CHECK (
    field NOT IN ('maturity_date', 'issue_date') OR value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$');

-- the Treasury's own security types, and the N-MFP categories (both forms' spellings), as the parsers emit them
ALTER TABLE portfolio.security_reference_attributes DROP CONSTRAINT IF EXISTS security_reference_attributes_treasury_chk;
ALTER TABLE portfolio.security_reference_attributes ADD CONSTRAINT security_reference_attributes_treasury_chk CHECK (
    field <> 'treasury_type' OR value = ANY (ARRAY['Bill', 'Note', 'Bond', 'CMB', 'TIPS', 'FRN']));
ALTER TABLE portfolio.security_reference_attributes DROP CONSTRAINT IF EXISTS security_reference_attributes_mmf_chk;
ALTER TABLE portfolio.security_reference_attributes ADD CONSTRAINT security_reference_attributes_mmf_chk CHECK (
    field <> 'mmf_category' OR value = ANY (ARRAY['treasury', 'government', 'prime', 'single_state',
                                                  'other_tax_exempt']));

-- ── 2. The providers (licence facts), switched OFF ─────────────────────────────────────────────────────────────
INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                      lic_redistribution, retention_after_cancel, attribution, terms_note,
                                      confirmed_on, daily_call_budget)
SELECT 'treasury_fiscal', 'U.S. Treasury Fiscal Data — auctions (bills, notes)', 'public_api', 'not_connected',
       ARRAY['index_level', 'reference'], true, 'allowed', true, 'keep',
       'Source: U.S. Department of the Treasury, Fiscal Data (fiscaldata.treasury.gov)',
       'api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query — every auctioned '
       || 'marketable security: CUSIP, type, term, auction / issue / maturity dates, results (high yield; a bill''s '
       || 'high investment rate). No key, no registration. A work of the U.S. Government — public domain (17 U.S.C. '
       || '105): may be used, shown and redistributed. Writes the security master (fixed_income, CUSIP + ISIN, '
       || 'maturity / issue dates, term, type) through security_master_load.Matcher, and each auction''s yield into '
       || 'portfolio.index_levels as tfd:<cusip>:yield (dated the auction date). Daily cadence in the security-data '
       || 'nightly (step cash_sources). SHIPPED NOT CONNECTED (CASH03): a Super Admin switches it on.',
       DATE '2026-11-09', 50
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_providers WHERE key = 'treasury_fiscal' AND valid_to IS NULL
                  AND system_to IS NULL);

INSERT INTO portfolio.data_providers (key, name, kind, status, capabilities, lic_internal_use, lic_member_display,
                                      lic_redistribution, retention_after_cancel, attribution, terms_note,
                                      confirmed_on, daily_call_budget)
SELECT 'sec_nmfp', 'SEC EDGAR Form N-MFP (money-market funds)', 'regulator_filing', 'not_connected',
       ARRAY['index_level', 'reference'], true, 'allowed', true, 'keep',
       'Source: U.S. Securities and Exchange Commission, EDGAR (Form N-MFP)',
       'Monthly Form N-MFP2 / N-MFP3 filings (XML primary_doc.xml), found through the EDGAR index '
       || '(edgar_index.parse_index_lines, forms N-MFP2 / N-MFP3) and fetched through services/edgar_fetch (the '
       || 'declared User-Agent, at most 10 requests per second — the SEC fair-access policy). U.S. government public '
       || 'record: may be shown and redistributed. Writes each class''s 7-day net yield (nmfp:<class id>:7d), the '
       || 'series'' WAM / WAL in days (nmfp:<series id>:wam | wal) into portfolio.index_levels and the fund class + '
       || 'category into the security master (by SEC class id). Months old by design. Monthly cadence (from the '
       || '8th of the month) in the security-data nightly (step cash_sources). SHIPPED NOT CONNECTED (CASH03).',
       DATE '2026-11-09', 200
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_providers WHERE key = 'sec_nmfp' AND valid_to IS NULL
                  AND system_to IS NULL);

-- ── 3. Their place in the platform rank (after every existing provider of each type) ──────────────────────────
INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope)
SELECT 'index_level', 'treasury_fiscal', 4, 'platform'
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'index_level'
                  AND provider_key = 'treasury_fiscal' AND valid_to IS NULL AND system_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'index_level'
                  AND rank = 4 AND valid_to IS NULL AND system_to IS NULL);
INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope)
SELECT 'index_level', 'sec_nmfp', 5, 'platform'
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'index_level'
                  AND provider_key = 'sec_nmfp' AND valid_to IS NULL AND system_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'index_level'
                  AND rank = 5 AND valid_to IS NULL AND system_to IS NULL);
INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope)
SELECT 'reference', 'treasury_fiscal', 7, 'platform'
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'reference'
                  AND provider_key = 'treasury_fiscal' AND valid_to IS NULL AND system_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'reference'
                  AND rank = 7 AND valid_to IS NULL AND system_to IS NULL);
INSERT INTO portfolio.data_source_rank (data_type, provider_key, rank, owner_scope)
SELECT 'reference', 'sec_nmfp', 8, 'platform'
WHERE NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'reference'
                  AND provider_key = 'sec_nmfp' AND valid_to IS NULL AND system_to IS NULL)
  AND NOT EXISTS (SELECT 1 FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = 'reference'
                  AND rank = 8 AND valid_to IS NULL AND system_to IS NULL);
