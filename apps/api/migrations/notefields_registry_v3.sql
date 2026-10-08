-- notefields.structural — part 2 of 2: the registry becomes the schema.
--
-- docs/NOTE_FIELDS.md (v3, approved by Joe 2026-10-08) is loaded into
-- portfolio.note_terms_field_registry. After this migration the registry is the
-- ONLY source of the B1 readers' field list (services/note_extraction/schema.py
-- reads nothing else).
--
-- NOTHING IS DELETED. A key the document drops or renames keeps its row with
-- retired_at set and replaced_by / replacement_rule naming where its meaning
-- went. That matters twice:
--   * every existing reading / staged field / gold value still points at a
--     registry key (live, or retired-with-mapping);
--   * the legacy Claude extractor (services/note_terms_extraction.py) writes the
--     FIXED columns of portfolio.securities_global_note_terms, one per original
--     registry key. It now reads only those original keys (LEGACY_FIELD_KEYS),
--     so renaming a key here can never change what it writes. That table is
--     out of scope (B2) and is not touched.
--
-- No new table, so no new RLS policy: the registry, readings, staged fields and
-- gold values already carry the four global-table policies.
--
-- RENAMES migrate data. A row on an old key is moved to the new key with the
-- old key kept in metadata.renamed_from (plus the value transform applied, if
-- any). Readings are append-only and gold values are human-guarded, so the two
-- BEFORE UPDATE triggers are disabled for exactly the migration UPDATE and
-- re-enabled in the same transaction. Counted live on 2026-10-08: ZERO rows sit
-- on a renamed key (the 58 migrated legacy readings use protection_type,
-- autocall_frequency, basket_type — still live — and is_decrement_index,
-- return_basis, terms_status — retired with a mapping, values left as stated).

-- ═══ Registry columns ═════════════════════════════════════════════════════
ALTER TABLE portfolio.note_terms_field_registry
    ADD COLUMN IF NOT EXISTS description       text,
    ADD COLUMN IF NOT EXISTS section           text,
    ADD COLUMN IF NOT EXISTS sort_order        integer,
    ADD COLUMN IF NOT EXISTS is_critical       boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS value_shape       text NOT NULL DEFAULT 'scalar',
    ADD COLUMN IF NOT EXISTS unit              text,
    ADD COLUMN IF NOT EXISTS enum_values       text[],
    ADD COLUMN IF NOT EXISTS synonyms          text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS trap_rule         text,
    ADD COLUMN IF NOT EXISTS extraction_method text NOT NULL DEFAULT 'model',
    ADD COLUMN IF NOT EXISTS derived_from      text[],
    ADD COLUMN IF NOT EXISTS former_keys       text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS retired_at        timestamptz,
    ADD COLUMN IF NOT EXISTS replaced_by       text[],
    ADD COLUMN IF NOT EXISTS replacement_rule  text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'note_terms_field_registry_value_shape_chk') THEN
        ALTER TABLE portfolio.note_terms_field_registry ADD CONSTRAINT note_terms_field_registry_value_shape_chk
            CHECK (value_shape IN ('scalar', 'range', 'list'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'note_terms_field_registry_method_chk') THEN
        ALTER TABLE portfolio.note_terms_field_registry ADD CONSTRAINT note_terms_field_registry_method_chk
            CHECK (extraction_method IN ('model', 'rules', 'derived'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'note_terms_field_registry_list_json_chk') THEN
        -- a list is json; json is only ever a list
        ALTER TABLE portfolio.note_terms_field_registry ADD CONSTRAINT note_terms_field_registry_list_json_chk
            CHECK ((value_shape = 'list') = (data_type = 'json'));
    END IF;
END $$;

-- staged fields and gold values gain metadata (renamed_from, review reasons)
ALTER TABLE portfolio.note_extraction_staged_fields
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE portfolio.note_gold_values
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ═══ The v3 field list ════════════════════════════════════════════════════
-- Units: pct = percent (70.0, never 0.7) of principal or of the initial level as
-- the description says; usd; usd_per_1000 = currency per $1,000 of principal;
-- months; x = a multiple.
CREATE TEMP TABLE _nf_fields (
    field_key text, display_label text, data_type text, section text, sort_order int,
    is_critical boolean, value_shape text, unit text, enum_values text[], synonyms text[],
    trap_rule text, extraction_method text, derived_from text[], former_keys text[],
    description text
);

INSERT INTO _nf_fields VALUES
-- 1 · Identity and program
('cusip', 'CUSIP', 'text', 'identity', 101, true, 'scalar', NULL, NULL,
 ARRAY['CUSIP No.', 'CUSIP Number', 'CUSIP/ISIN'], NULL, 'model', NULL, '{}',
 'The 9-character CUSIP of these notes. Rules extract it beside its label and validate the check digit; a model reading is a cross-check only.'),
('isin', 'ISIN', 'text', 'identity', 102, false, 'scalar', NULL, NULL,
 ARRAY['ISIN No.'], NULL, 'rules', NULL, '{isin}',
 'The 12-character ISIN of these notes, as labeled.'),
('issuer_entity', 'Issuer', 'text', 'identity', 103, true, 'scalar', NULL, NULL,
 ARRAY['Issuer', 'Issuer name'], NULL, 'model', NULL, '{issuer,issuer_name}',
 'The legal entity that issues the notes, exactly as named (normalised afterwards to the structured-note issuer table).'),
('guarantor_entity', 'Guarantor', 'text', 'identity', 104, true, 'scalar', NULL, NULL,
 ARRAY['Guarantor'], 'The guarantor is the credit actually standing behind the note; a mismatch with the issuer table''s mapping goes to review.',
 'model', NULL, '{guarantor_name,guarantor}',
 'The entity that guarantees payment on the notes, exactly as named; null if the notes are not guaranteed.'),
('product_supplement_ref', 'Product Supplement', 'text', 'identity', 105, false, 'scalar', NULL, NULL,
 ARRAY['Product Supplement No.', 'Product supplement referenced'], NULL, 'model', NULL, '{product_supplement_reference}',
 'The name and date of the program document whose generic terms these notes use (e.g. "EQUITY-1, Dec 8 2025").'),
('issue_size', 'Issue Size', 'numeric', 'identity', 106, false, 'scalar', 'usd', NULL,
 ARRAY['Aggregate Principal Amount', 'Aggregate principal amount', 'Total Offering Amount', 'Total Price to Public', 'Aggregate stated principal amount'],
 NULL, 'model', NULL, '{issue_price_total,total_offering_amount,aggregate_principal_amount}',
 'The aggregate principal amount of this issue in currency units (e.g. 2892000).'),
('denomination', 'Denomination', 'numeric', 'identity', 107, false, 'scalar', 'usd', NULL,
 ARRAY['Minimum Denomination', 'Denominations', 'Minimum investment', 'Stated principal amount'],
 NULL, 'model', NULL, '{denomination_amount}',
 'The minimum / per-note denomination in currency units (usually 1000 or 10).'),
('currency', 'Currency', 'text', 'identity', 108, false, 'scalar', NULL, NULL,
 ARRAY['Specified Currency', 'Denominated currency', 'Notional currency'], NULL, 'model', NULL, '{notional_currency}',
 'ISO currency code of the principal, e.g. USD.'),
-- 2 · Dates
('pricing_date', 'Pricing Date', 'date', 'dates', 201, true, 'scalar', NULL, NULL,
 ARRAY['Pricing Date', 'Trade Date', 'Strike Date', 'Initial Valuation Date'],
 'Decision A: the initial valuation date IS the pricing date. A filing stating a different initial valuation date is flagged for review, never merged.',
 'model', NULL, '{initial_valuation_date}',
 'The pricing / trade / strike date on which the initial level is set (YYYY-MM-DD).'),
('issue_date', 'Issue Date', 'date', 'dates', 202, false, 'scalar', NULL, NULL,
 ARRAY['Original Issue Date', 'Settlement Date', 'Issue Date'], NULL, 'model', NULL, '{original_issue_date}',
 'The settlement / original issue date (YYYY-MM-DD).'),
('final_valuation_date', 'Final Valuation Date', 'date', 'dates', 203, true, 'scalar', NULL, NULL,
 ARRAY['Final Valuation Date', 'Final Determination Date', 'Final Observation Date', 'Final Calculation Day', 'Valuation Date'],
 NULL, 'model', NULL, '{}',
 'The final valuation / observation / determination date on which the final level is set (YYYY-MM-DD).'),
('maturity_date', 'Maturity Date', 'date', 'dates', 204, true, 'scalar', NULL, NULL,
 ARRAY['Maturity Date', 'Stated Maturity Date'], NULL, 'model', NULL, '{}',
 'The stated maturity date (YYYY-MM-DD).'),
('tenor_months', 'Tenor (Months)', 'numeric', 'dates', 205, false, 'scalar', 'months', NULL,
 '{}', NULL, 'derived', ARRAY['pricing_date', 'maturity_date'], '{tenor_years}',
 'Months from the pricing date to the maturity date. Computed, never extracted.'),
-- 3 · Underlyings
('underlyings', 'Underlyings', 'json', 'underlyings', 301, true, 'list', NULL, NULL,
 ARRAY['Underlying', 'Underlier', 'Reference Asset', 'Underlying Asset'],
 'Return basis: price return without dividends is worth less than total return.',
 'model', NULL, '{reference_assets,initial_values_by_underlying}',
 'Every underlying (index, ETF, stock, rate), one entry each: its name as stated, Bloomberg ticker if given, kind, weight (null for single and worst-of), initial level, return basis and FX treatment.'),
('basket_type', 'Basket Type', 'text', 'underlyings', 302, true, 'scalar', NULL,
 ARRAY['single', 'worst_of', 'weighted', 'average'],
 ARRAY['Worst Performing Underlying', 'Least Performing Underlying', 'Basket'],
 'Worst-of is much riskier than average.', 'model', NULL, '{}',
 '''single'' = one underlying. ''worst_of'' = the single WORST performer among several drives the payoff. ''weighted'' = a weighted basket of several underlyings. ''average'' = the simple average of several underlyings.'),
-- 4 · Payoff and protection
('product_archetype', 'Product Archetype', 'text', 'payoff', 401, true, 'scalar', NULL,
 ARRAY['autocallable', 'buffered', 'barrier', 'digital', 'leveraged', 'principal_protected', 'reverse_convertible', 'other'],
 '{}', NULL, 'model', NULL, '{}',
 'The product family the payoff belongs to.'),
('protection_type', 'Protection Type', 'text', 'payoff', 402, true, 'scalar', NULL,
 ARRAY['none', 'buffer', 'barrier', 'floor', 'full'], '{}',
 'Never a yes/no. A buffer and a barrier with the same number are opposite payoffs.', 'model', NULL, '{}',
 'How principal is exposed at maturity. ''full'': repayment of principal at maturity does NOT depend on the underlying. ''floor'': a minimum repayment below 100% of principal. ''buffer'': losses begin only after the underlying falls more than X% and only the decline beyond X% is lost (X goes in buffer_pct). ''barrier'': once the underlying ends below (or breaches) a threshold, the FULL decline from the initial level applies (threshold goes in barrier_pct). ''none'': every percent of decline is lost.'),
('buffer_pct', 'Buffer %', 'numeric', 'payoff', 403, true, 'scalar', 'pct', NULL,
 ARRAY['Buffer Amount', 'Buffer Percentage', 'Buffer Level'],
 'Losses start only after the underlying falls more than the buffer; never a barrier level.', 'model', NULL, '{}',
 'ONLY for protection_type ''buffer'': the buffer size X in "losses begin only after the underlying falls more than X%" (e.g. 15.0). Never a barrier level.'),
('barrier_pct', 'Barrier %', 'numeric', 'payoff', 404, true, 'scalar', 'pct', NULL,
 ARRAY['Barrier Value', 'Downside Threshold', 'Trigger Value', 'Knock-In Level', 'Threshold Value', 'trigger', 'knock-in'],
 'Below the barrier the full decline applies from the first dollar.', 'model', NULL, '{}',
 'ONLY for protection_type ''barrier'': the downside threshold as a percent of the initial level (e.g. 70.0); once breached the FULL decline applies. Never a buffer size and never the coupon barrier.'),
('barrier_observation', 'Barrier Observation', 'text', 'payoff', 405, true, 'scalar', NULL,
 ARRAY['at_maturity', 'daily', 'continuous'], ARRAY['Barrier Observation', 'Knock-In Event'],
 'Daily or continuous observation is far riskier than at maturity only.', 'model', NULL, '{}',
 'When the downside barrier is observed: ''at_maturity'' (final valuation date only), ''daily'' (every trading day''s close), ''continuous'' (at any time). Null if there is no barrier.'),
('downside_leverage', 'Downside Leverage', 'numeric', 'payoff', 406, false, 'scalar', 'x', NULL,
 ARRAY['Downside Leverage Factor', 'Downside Participation'], NULL, 'model', NULL, '{}',
 'The multiple applied to losses below the barrier or beyond the buffer (1.0 for 1:1; e.g. 1.25 for geared loss).'),
('principal_at_risk', 'Principal at Risk', 'boolean', 'payoff', 407, true, 'scalar', NULL, NULL,
 ARRAY['Principal at risk', 'You may lose'], NULL, 'model', NULL, '{principal_conditional}',
 'True unless repayment of principal at maturity does not depend on the underlying.'),
('participation_rate', 'Participation Rate', 'numeric', 'payoff', 408, false, 'scalar', 'pct', NULL,
 ARRAY['Upside Gearing', 'Upside Leverage Factor', 'Participation Rate'], NULL, 'model', NULL, '{}',
 'Upside participation as a percent (150% leverage = 150.0).'),
('cap_pct', 'Cap %', 'numeric', 'payoff', 409, false, 'scalar', 'pct', NULL,
 ARRAY['Maximum Return', 'Cap Level', 'Maximum Payment'], NULL, 'model', NULL, '{}',
 'Maximum return as a percent of principal (e.g. 25.0), or null if uncapped.'),
('fixed_payout_amount', 'Fixed Payout Amount', 'numeric', 'payoff', 410, false, 'scalar', 'usd_per_1000', NULL,
 ARRAY['Digital Payment', 'Fixed Payment', 'Redemption Amount'],
 'A "barrier" on a digital or redemption-barrier payout protects nothing.', 'model', NULL, '{}',
 'For digital / redemption-barrier payouts: the fixed amount paid per $1,000 of principal when the threshold is met (e.g. 1330.0).'),
('fixed_payout_threshold_pct', 'Fixed Payout Threshold %', 'numeric', 'payoff', 411, false, 'scalar', 'pct', NULL,
 ARRAY['Digital Barrier', 'Redemption Barrier', 'Payment Threshold'],
 'A "barrier" on a digital or redemption-barrier payout protects nothing.', 'model', NULL, '{}',
 'For digital / redemption-barrier payouts: the level, as a percent of the initial level, at or above which the fixed payout is paid.'),
('final_value_method', 'Final Value Method', 'text', 'payoff', 412, true, 'scalar', NULL,
 ARRAY['single_close', 'average', 'lowest'], ARRAY['Final Value', 'Final Level', 'Final Underlying Level', 'Ending Value'],
 'Averaging or lowest-of changes the note''s value materially.', 'model', NULL, '{final_value_definition}',
 'How the final level is set: ''single_close'' = one closing level on the final valuation date; ''average'' = the average of closing levels on several dates; ''lowest'' = the lowest closing level of several dates.'),
('payoff_formula_quotes', 'Payoff Formula Quotes', 'text', 'payoff', 413, false, 'scalar', NULL, NULL,
 ARRAY['Payment at Maturity'], NULL, 'model', NULL,
 '{payment_at_maturity,payment_at_maturity_if_below_threshold,payment_at_maturity_if_at_or_above_threshold}',
 'Evidence, not a term: the filing''s own payment-at-maturity formulas (above and below the threshold), copied verbatim, for the payoff self-check.'),
('max_principal_loss_pct', 'Maximum Principal Loss %', 'numeric', 'payoff', 414, false, 'scalar', 'pct', NULL,
 '{}', NULL, 'derived',
 ARRAY['protection_type', 'buffer_pct', 'barrier_pct', 'downside_leverage', 'principal_at_risk'], '{}',
 'The largest percent of principal an investor can lose at maturity, computed from the protection type and level. Never extracted.'),
-- 5 · Income
('coupon_type', 'Coupon Type', 'text', 'income', 501, true, 'scalar', NULL,
 ARRAY['none', 'fixed', 'contingent'], ARRAY['Contingent Coupon', 'Contingent Interest', 'Coupon'], NULL, 'model', NULL, '{}',
 '''contingent'' = paid only if a condition (e.g. a coupon barrier) is met; ''fixed'' = paid regardless; ''none'' = no coupon.'),
('coupon_rate_pa', 'Coupon Rate (p.a.)', 'numeric', 'income', 502, true, 'scalar', 'pct', NULL,
 ARRAY['Contingent Coupon Rate', 'Contingent Interest Rate', 'Coupon Rate'],
 'A per-period rate is not the annual rate.', 'model', NULL, '{coupon_rate}',
 'Coupon rate PER ANNUM as a percent (e.g. 9.25). Record the payment frequency in coupon_frequency; never put a per-period rate here.'),
('coupon_frequency', 'Coupon Frequency', 'text', 'income', 503, false, 'scalar', NULL,
 ARRAY['monthly', 'quarterly', 'semi_annual', 'annual'], ARRAY['Coupon Payment Dates', 'Interest Payment Dates'], NULL, 'model', NULL, '{}',
 'How often the coupon is paid.'),
('coupon_barrier_pct', 'Coupon Barrier %', 'numeric', 'income', 504, true, 'scalar', 'pct', NULL,
 ARRAY['Coupon Barrier', 'Coupon Threshold', 'Coupon Trigger', 'Interest Barrier'],
 'The coupon barrier is not the downside barrier.', 'model', NULL, '{contingent_coupon_terms,coupon_and_barrier_terms}',
 'Coupon barrier (coupon threshold) as a percent of the initial level, e.g. 70.0.'),
('coupon_memory', 'Coupon Memory', 'boolean', 'income', 505, true, 'scalar', NULL, NULL,
 ARRAY['Memory Feature', 'previously unpaid'], NULL, 'model', NULL, '{}',
 'True if missed contingent coupons are paid later when the condition is next met (memory / snowball feature).'),
-- 6 · Calls
('call_type', 'Call Type', 'text', 'calls', 601, true, 'scalar', NULL,
 ARRAY['none', 'automatic', 'issuer'], ARRAY['Automatic Call', 'Optional Redemption', 'Early Redemption'],
 'Issuer calls favour the bank.', 'model', NULL, '{}',
 '''automatic'' = called automatically when a level is met; ''issuer'' = the issuer MAY redeem at its option; ''none'' = not callable.'),
('autocall_level_pct', 'Autocall Level %', 'numeric', 'calls', 602, true, 'scalar', 'pct', NULL,
 ARRAY['Call Value', 'Call Threshold', 'Call Barrier Level', 'Autocall Barrier', 'Trigger Level'],
 NULL, 'model', NULL, '{autocall_barrier_pct,call_value}',
 'Level at or above which the note is automatically called, as a percent of the initial level, e.g. 100.0.'),
('autocall_frequency', 'Autocall Frequency', 'text', 'calls', 603, false, 'scalar', NULL,
 ARRAY['monthly', 'quarterly', 'semi_annual', 'annual'], ARRAY['Call Observation Dates', 'Review Dates'], NULL, 'model', NULL, '{}',
 'How often the note can be called (automatic or issuer call). Null if it cannot be called.'),
('no_call_months', 'No-Call Period (Months)', 'numeric', 'calls', 604, false, 'scalar', 'months', NULL,
 ARRAY['No-Call Period', 'Non-Call Period'], NULL, 'model', NULL, '{has_no_call_period}',
 'Length of the initial period in months during which the note cannot be called; null if there is none.'),
('observation_schedule', 'Observation Schedule', 'json', 'calls', 605, false, 'list', NULL, NULL,
 ARRAY['Observation Dates', 'Review Dates', 'Coupon Determination Dates', 'Call Observation Dates'],
 NULL, 'model', NULL, '{contingent_coupon_payment_dates,interest_payment_dates}',
 'One entry per observation date, for coupons and calls together: observation date, payment date, coupon barrier, coupon amount, call level and call amount on that date.'),
-- 7 · Economics
('price_to_public_pct', 'Price to Public %', 'numeric', 'economics', 701, true, 'scalar', 'pct', NULL,
 ARRAY['Price to Public', 'Issue Price', 'Original Issue Price', 'Public Offering Price'], NULL, 'model', NULL, '{issue_price_per_security}',
 'The price to the public as a percent of principal (usually 100.0).'),
('estimated_value_per_1000', 'Estimated Value per $1,000', 'numeric', 'economics', 702, true, 'range', 'usd_per_1000', NULL,
 ARRAY['Estimated value', 'Estimated initial value', 'Initial estimated value', 'Estimated Value per Note'],
 'Decision B: stored both per $1,000 and as % of principal; the stated one is extracted, the other derived and marked derived. "Not less than $X" is a range.',
 'model', ARRAY['estimated_value_pct'], '{}',
 'The issuer''s estimated value per $1,000 of principal (e.g. 965.50). As a MIN and MAX: "not less than $X" sets only the minimum; a single amount sets both.'),
('estimated_value_pct', 'Estimated Value %', 'numeric', 'economics', 703, false, 'range', 'pct', NULL,
 ARRAY['Estimated value', 'Estimated initial value', 'Initial estimated value'],
 'Decision B: the companion of estimated_value_per_1000; derived when the filing states only the dollar amount.',
 'model', ARRAY['estimated_value_per_1000'], '{}',
 'The issuer''s estimated value as a percent of principal (e.g. 96.55). As a MIN and MAX: "not less than X%" sets only the minimum; a single amount sets both.'),
('agent_commission_pct', 'Agent Commission %', 'numeric', 'economics', 704, true, 'range', 'pct', NULL,
 ARRAY['Underwriting Discount', 'Agent''s Commission', 'Selling Commission', 'Underwriting commissions'],
 '"Up to" sets only the maximum.', 'model', NULL, '{}',
 'The agent''s commission / underwriting discount as a percent of principal, as a MIN and MAX: "up to 2.50%" sets only the maximum; a single amount sets both.'),
('structuring_fee_pct', 'Structuring Fee %', 'numeric', 'economics', 705, false, 'scalar', 'pct', NULL,
 ARRAY['Structuring Fee', 'Structuring and development costs'], NULL, 'model', NULL, '{}',
 'Structuring fee as a percent of principal.'),
('other_fees_pct', 'Other Fees %', 'numeric', 'economics', 706, false, 'scalar', 'pct', NULL,
 ARRAY['Platform Fee', 'Marketing Fee', 'Referral Fee'], NULL, 'model', NULL, '{}',
 'Platform, marketing and other fees not counted as commission or structuring fee, as a percent of principal.'),
('total_fees_pct', 'Total Fees %', 'numeric', 'economics', 707, true, 'scalar', 'pct', NULL,
 ARRAY['Underwriting Discounts and Commissions', 'Total Commissions', 'Fees and Commissions'], NULL, 'model', NULL, '{total_commissions_fees_pct}',
 'Total selling commissions plus structuring and other fees, as a percent of principal ($22.50 per $1,000 = 2.25).'),
('fee_based_account_price', 'Fee-Based Account Price', 'numeric', 'economics', 708, true, 'range', 'usd_per_1000', NULL,
 ARRAY['fee-based advisory accounts', 'fiduciary accounts', 'advisory accounts'],
 'This is the price an RIA''s advisory client pays, usually a range; never the price to public.', 'model', NULL, '{fee_based_account_price_pct}',
 'The price per $1,000 of principal paid by investors in fee-based / advisory accounts, as a MIN and MAX: "may pay as little as $977.50" sets only the minimum; a single amount sets both.'),
('proceeds_to_issuer_pct', 'Proceeds to Issuer %', 'numeric', 'economics', 709, false, 'scalar', 'pct', NULL,
 ARRAY['Proceeds to Issuer', 'Proceeds to Us'], NULL, 'model', NULL, '{proceeds_to_issuer_per_security}',
 'Proceeds to the issuer as a percent of principal (self-check: price minus fees).'),
-- 8 · Distribution
('distribution', 'Distribution Participants', 'json', 'distribution', 801, true, 'list', NULL, NULL,
 ARRAY['Plan of Distribution', 'Selling Agents', 'Placement Agents', 'Dealers'],
 NULL, 'model', NULL, '{distribution_agent,agent_name}',
 'EVERY selling agent, distributor, dealer or placement agent named in the (supplemental) plan of distribution, one entry each: the name exactly as stated, its role, and the fee it receives as a MIN and MAX percent of principal.'),
('calculation_agent', 'Calculation Agent', 'text', 'distribution', 802, false, 'scalar', NULL, NULL,
 ARRAY['Calculation Agent'], 'An issuer-affiliated calculation agent is a conflict and is flagged.', 'model', NULL, '{calculation_agent}',
 'The calculation agent, exactly as named.'),
-- 9 · Later, rules only
('registration_number', 'Registration Statement No.', 'text', 'rules_only', 901, false, 'scalar', NULL, NULL,
 ARRAY['Registration Statement No.', 'Registration No.'], NULL, 'rules', NULL, '{registration_number}',
 'The SEC registration statement number (e.g. 333-275898).'),
('listing_status', 'Listing', 'text', 'rules_only', 902, false, 'scalar', NULL, NULL,
 ARRAY['Listing'], NULL, 'rules', NULL, '{listing_status}',
 'The listing statement as written (e.g. "The notes will not be listed on any securities exchange").'),
('prospectus_date', 'Prospectus Date', 'date', 'rules_only', 903, false, 'scalar', NULL, NULL,
 ARRAY['Prospectus dated'], NULL, 'rules', NULL, '{prospectus_date}',
 'The date of the base prospectus (YYYY-MM-DD).'),
('prospectus_supplement_date', 'Prospectus Supplement Date', 'date', 'rules_only', 904, false, 'scalar', NULL, NULL,
 ARRAY['Prospectus Supplement dated'], NULL, 'rules', NULL, '{prospectus_supplement_date}',
 'The date of the prospectus supplement (YYYY-MM-DD).'),
('form_of_notes', 'Form of Notes', 'text', 'rules_only', 905, false, 'scalar', NULL, NULL,
 ARRAY['Form of Notes', 'Form of Note', 'Form'], NULL, 'rules', NULL, '{form_of_notes}',
 'The form of the notes as written (e.g. book-entry).'),
('filed_pursuant_to_rule', 'Filed Pursuant to Rule', 'text', 'rules_only', 906, false, 'scalar', NULL, NULL,
 ARRAY['Filed pursuant to Rule'], NULL, 'rules', NULL, '{filed_pursuant_to_rule}',
 'The rule the filing is made under (e.g. 424(b)(2)).'),
('credit_risk_statement', 'Credit Risk Statement', 'text', 'rules_only', 907, false, 'scalar', NULL, NULL,
 ARRAY['subject to the credit risk'], NULL, 'rules', NULL, '{credit_risk_statement}',
 'The issuer credit-risk boilerplate sentence, as written.'),
('business_day_convention', 'Business Day Convention', 'text', 'rules_only', 908, false, 'scalar', NULL, NULL,
 ARRAY['Business Day', 'Following Business Day'], NULL, 'rules', NULL, '{}',
 'The business-day convention for dates that fall on a non-business day, as written.'),
('settlement_lag', 'Settlement Lag', 'text', 'rules_only', 909, false, 'scalar', NULL, NULL,
 ARRAY['T+', 'settle in'], NULL, 'rules', NULL, '{}',
 'The settlement cycle as written (e.g. "T+5").');

INSERT INTO portfolio.note_terms_field_registry AS r
    (field_key, display_label, data_type, hazard_field, description, section, sort_order, is_critical,
     value_shape, unit, enum_values, synonyms, trap_rule, extraction_method, derived_from, former_keys,
     retired_at, replaced_by, replacement_rule)
SELECT field_key, display_label, data_type, false, description, section, sort_order, is_critical,
       value_shape, unit, enum_values, synonyms, trap_rule, extraction_method, derived_from, former_keys,
       NULL, NULL, NULL
  FROM _nf_fields
ON CONFLICT (field_key) DO UPDATE SET
    -- display_label, data_type and hazard_field of a pre-existing row are the
    -- legacy extractor's; only the new properties are written over them
    description = EXCLUDED.description, section = EXCLUDED.section, sort_order = EXCLUDED.sort_order,
    is_critical = EXCLUDED.is_critical, value_shape = EXCLUDED.value_shape, unit = EXCLUDED.unit,
    enum_values = EXCLUDED.enum_values, synonyms = EXCLUDED.synonyms, trap_rule = EXCLUDED.trap_rule,
    extraction_method = EXCLUDED.extraction_method, derived_from = EXCLUDED.derived_from,
    former_keys = EXCLUDED.former_keys, retired_at = NULL, replaced_by = NULL, replacement_rule = NULL;

-- ═══ Retired keys (never deleted) ═════════════════════════════════════════
-- The original registry keys the document drops or renames, plus B1's former
-- hard-coded extension keys that were renamed (so a reading on one of them can
-- never be an orphan).
CREATE TEMP TABLE _nf_retired (
    field_key text, display_label text, data_type text, section text,
    replaced_by text[], replacement_rule text
);

INSERT INTO _nf_retired VALUES
('coupon_rate', 'Coupon Rate', 'numeric', 'income', ARRAY['coupon_rate_pa'],
 'renamed: same value, now always per annum'),
('autocall_barrier_pct', 'Autocall Barrier %', 'numeric', 'calls', ARRAY['autocall_level_pct'],
 'renamed: same value'),
('tenor_years', 'Tenor (Years)', 'numeric', 'dates', ARRAY['tenor_months'],
 'renamed: value x 12; tenor is now derived from pricing_date and maturity_date'),
('notional_currency', 'Notional Currency', 'text', 'identity', ARRAY['currency'],
 'renamed: same value'),
('initial_valuation_date', 'Initial Valuation Date', 'date', 'dates', ARRAY['pricing_date'],
 'decision A: the initial valuation date is the pricing date; a different stated date is flagged for review'),
('protection_pct', 'Protection %', 'numeric', 'payoff', ARRAY['buffer_pct', 'barrier_pct'],
 'split by protection_type: buffer -> buffer_pct, barrier -> barrier_pct; full/floor/none have no v3 level field and stay on this key'),
('has_no_call_period', 'Has No-Call Period', 'boolean', 'calls', ARRAY['no_call_months'],
 'dropped: true exactly when no_call_months > 0; readings stay on this key'),
('is_decrement_index', 'Decrement Index', 'boolean', 'underlyings', ARRAY['underlyings'],
 'moved into the underlyings list: true -> underlyings[].return_basis = ''decrement''; readings stay on this key'),
('return_basis', 'Return Basis', 'text', 'underlyings', ARRAY['underlyings'],
 'moved into the underlyings list as underlyings[].return_basis (per underlying); readings stay on this key'),
('terms_status', 'Terms Status', 'text', 'identity', ARRAY[]::text[],
 'dropped from the v3 extraction schema (decision D); the legacy extractor still derives it from the form type'),
('principal_conditional', 'Principal Conditional', 'boolean', 'payoff', ARRAY['principal_at_risk'],
 'renamed: same value (true = repayment depends on the underlying)'),
('total_commissions_fees_pct', 'Total Commissions & Fees %', 'numeric', 'economics', ARRAY['total_fees_pct'],
 'renamed: same value'),
('fee_based_account_price_pct', 'Fee-Based Account Price %', 'numeric', 'economics', ARRAY['fee_based_account_price'],
 'renamed: percent of principal x 10 = per $1,000, stored as a range with min = max'),
('denomination_amount', 'Minimum Denomination', 'numeric', 'identity', ARRAY['denomination'],
 'renamed: same value');

INSERT INTO portfolio.note_terms_field_registry AS r
    (field_key, display_label, data_type, hazard_field, section, is_critical, retired_at,
     replaced_by, replacement_rule, extraction_method)
SELECT field_key, display_label, data_type, false, section, false, now(), replaced_by, replacement_rule, 'model'
  FROM _nf_retired
ON CONFLICT (field_key) DO UPDATE SET
    section = EXCLUDED.section, is_critical = false,
    retired_at = COALESCE(r.retired_at, now()),
    replaced_by = EXCLUDED.replaced_by, replacement_rule = EXCLUDED.replacement_rule;

-- ═══ Constraints that need every row loaded first ════════════════════════
-- (the 19 pre-existing rows had no description until the load above)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'note_terms_field_registry_derived_chk') THEN
        ALTER TABLE portfolio.note_terms_field_registry ADD CONSTRAINT note_terms_field_registry_derived_chk
            CHECK (extraction_method <> 'derived' OR COALESCE(cardinality(derived_from), 0) > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'note_terms_field_registry_live_desc_chk') THEN
        -- a live field must carry what the readers are told about it
        ALTER TABLE portfolio.note_terms_field_registry ADD CONSTRAINT note_terms_field_registry_live_desc_chk
            CHECK (retired_at IS NOT NULL OR (description IS NOT NULL AND section IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'note_terms_field_registry_retired_chk') THEN
        -- a retired field is never critical and always says where it went
        ALTER TABLE portfolio.note_terms_field_registry ADD CONSTRAINT note_terms_field_registry_retired_chk
            CHECK (retired_at IS NULL OR (NOT is_critical AND replacement_rule IS NOT NULL));
    END IF;
END $$;

-- ═══ Data migration: rows on a renamed key move to the new key ════════════
-- old key kept in metadata.renamed_from. Value transforms: 'same', 'x12'
-- (years -> months), 'pct_to_per1000_range' (x10, wrapped as {min,max}),
-- 'split_by_protection_type' (protection_pct only).
CREATE TEMP TABLE _nf_renames (old_key text, new_key text, transform text);
INSERT INTO _nf_renames VALUES
    ('coupon_rate', 'coupon_rate_pa', 'same'),
    ('autocall_barrier_pct', 'autocall_level_pct', 'same'),
    ('tenor_years', 'tenor_months', 'x12'),
    ('notional_currency', 'currency', 'same'),
    ('initial_valuation_date', 'pricing_date', 'same'),
    ('principal_conditional', 'principal_at_risk', 'same'),
    ('total_commissions_fees_pct', 'total_fees_pct', 'same'),
    ('fee_based_account_price_pct', 'fee_based_account_price', 'pct_to_per1000_range'),
    ('denomination_amount', 'denomination', 'same');

CREATE OR REPLACE FUNCTION pg_temp.nf_transform(v jsonb, t text) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $f$
    SELECT CASE
        WHEN v IS NULL OR jsonb_typeof(v) <> 'number' OR t = 'same' THEN v
        WHEN t = 'x12' THEN to_jsonb((v::text)::numeric * 12)
        WHEN t = 'pct_to_per1000_range' THEN jsonb_build_object(
            'min', (v::text)::numeric * 10, 'max', (v::text)::numeric * 10, 'bound', 'exact')
        ELSE v END
$f$;

-- protection_pct is split by the SAME filing's protection_type reading from
-- the same source (latest wins); without one it stays on protection_pct.
CREATE TEMP TABLE _nf_protection_split AS
SELECT r.id, CASE pt.value #>> '{}' WHEN 'buffer' THEN 'buffer_pct' WHEN 'barrier' THEN 'barrier_pct' END AS new_key
  FROM portfolio.note_term_readings r
  LEFT JOIN LATERAL (
        SELECT p.value FROM portfolio.note_term_readings p
         WHERE p.reference_filing_id = r.reference_filing_id AND p.field_key = 'protection_type'
           AND p.source = r.source ORDER BY p.created_at DESC LIMIT 1) pt ON true
 WHERE r.field_key = 'protection_pct';

ALTER TABLE portfolio.note_term_readings DISABLE TRIGGER note_term_readings_no_update;
ALTER TABLE portfolio.note_gold_values DISABLE TRIGGER note_gold_values_human_guard_ins;

UPDATE portfolio.note_term_readings r
   SET field_key = m.new_key,
       value = pg_temp.nf_transform(r.value, m.transform),
       metadata = r.metadata || jsonb_build_object('renamed_from', m.old_key, 'renamed_by', 'notefields',
                                                   'value_transform', m.transform)
  FROM _nf_renames m WHERE r.field_key = m.old_key;

UPDATE portfolio.note_term_readings r
   SET field_key = s.new_key,
       metadata = r.metadata || jsonb_build_object('renamed_from', 'protection_pct', 'renamed_by', 'notefields',
                                                   'value_transform', 'split_by_protection_type')
  FROM _nf_protection_split s WHERE r.id = s.id AND s.new_key IS NOT NULL;

UPDATE portfolio.note_extraction_staged_fields f
   SET field_key = m.new_key,
       resolved_value = pg_temp.nf_transform(f.resolved_value, m.transform),
       metadata = f.metadata || jsonb_build_object('renamed_from', m.old_key, 'renamed_by', 'notefields',
                                                   'value_transform', m.transform)
  FROM _nf_renames m
 WHERE f.field_key = m.old_key
   AND NOT EXISTS (SELECT 1 FROM portfolio.note_extraction_staged_fields g
                    WHERE g.staging_id = f.staging_id AND g.field_key = m.new_key);

UPDATE portfolio.note_gold_values g
   SET field_key = m.new_key,
       value = pg_temp.nf_transform(g.value, m.transform),
       metadata = g.metadata || jsonb_build_object('renamed_from', m.old_key, 'renamed_by', 'notefields',
                                                   'value_transform', m.transform)
  FROM _nf_renames m WHERE g.field_key = m.old_key;

ALTER TABLE portfolio.note_gold_values ENABLE TRIGGER note_gold_values_human_guard_ins;
ALTER TABLE portfolio.note_term_readings ENABLE TRIGGER note_term_readings_no_update;

-- ═══ Model guardrails (platform_model_catalog) ═══════════════════════════
-- public_data_only: usable ONLY by global / public-data task keys (the note
-- extraction ensemble, the EDGAR inventory). Set for the OpenAI models — the
-- OpenAI project shares data with OpenAI in exchange for free usage.
-- manual_*_cost_per_mtok: a price entered by hand for a model whose price the
-- proxy does not know; bulk runs refuse a model with neither.
ALTER TABLE public.platform_model_catalog
    ADD COLUMN IF NOT EXISTS public_data_only boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS manual_input_cost_per_mtok numeric,
    ADD COLUMN IF NOT EXISTS manual_output_cost_per_mtok numeric;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'platform_model_catalog_manual_price_chk') THEN
        ALTER TABLE public.platform_model_catalog ADD CONSTRAINT platform_model_catalog_manual_price_chk
            CHECK ((manual_input_cost_per_mtok IS NULL) = (manual_output_cost_per_mtok IS NULL)
                   AND COALESCE(manual_input_cost_per_mtok, 0) >= 0
                   AND COALESCE(manual_output_cost_per_mtok, 0) >= 0);
    END IF;
END $$;

UPDATE public.platform_model_catalog SET public_data_only = true WHERE provider = 'openai';
