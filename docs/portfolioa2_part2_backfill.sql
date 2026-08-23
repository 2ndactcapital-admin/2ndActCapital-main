-- Portfolio A2 · Task 2 — backfill public.transaction_types.market
--
-- The `market` column shipped in the A2 Part 1 SQL as nullable with
--   CHECK (market IN ('public','private','both'))
-- and ALL 16 existing rows carried market = NULL (verified live before this ran).
--
-- Classification rationale, per row, using the row's OWN deployed evidence
-- (`category`, `applies_to_security_types`, `amount_basis`) rather than the code
-- string alone:
--
--   PRIVATE (10)
--     call_investment, call_mgmt_fee, call_org_cost, call_partnership_expense
--         ILPA capital-call constructs. applies_to_security_types = {alt,fund}.
--         A listed security is never "called".
--     dist_roc, dist_gain, dist_income, dist_recallable, dist_stock
--         ILPA distribution waterfall. applies_to_security_types = {alt,fund}.
--         `dist_income` is distinct from `dividend`: a fund distribution of
--         income is a waterfall event, a dividend is a corporate action.
--     valuation
--         A valuation mark AS A TRANSACTION (affects_nav = +1) is how an
--         illiquid holding's NAV moves. A listed position is marked by its
--         price series, not by a transaction row. Public books do not write
--         these; private books cannot report without them.
--
--   PUBLIC (3)
--     buy, sell            amount_basis='units', applies_to={unitized}.
--     dividend             a corporate action on a listed security.
--
--   BOTH (3)
--     adjustment           category='other', applies_to = NULL. Generic
--                          correction; both books need it.
--     fee_expense          applies_to = NULL. Advisory fees and commissions on
--                          the public side; management fees paid outside a
--                          capital call on the private side.
--     interest             DELIBERATE DEVIATION FROM THE SPRINT BRIEF, which
--                          suggested 'public'. The deployed row says
--                          applies_to_security_types = {unitized, alt} — it is
--                          already recorded as spanning both, and it genuinely
--                          does: a bond coupon is public, private-credit
--                          interest is not. Classifying it 'public' would make
--                          the compatibility check in
--                          services/portfolio_assets.py::record_transaction
--                          reject private-credit interest, which is a real and
--                          common transaction. The data won over the brief.
--
-- Idempotent: re-running sets the same values. Deliberately NOT gated on
-- `market IS NULL` — this statement is the source of truth for these 16 codes,
-- so a re-run also repairs a hand-edited row.

UPDATE public.transaction_types
   SET market = v.market
  FROM (VALUES
        ('call_investment',           'private'),
        ('call_mgmt_fee',             'private'),
        ('call_org_cost',             'private'),
        ('call_partnership_expense',  'private'),
        ('dist_roc',                  'private'),
        ('dist_gain',                 'private'),
        ('dist_income',               'private'),
        ('dist_recallable',           'private'),
        ('dist_stock',                'private'),
        ('valuation',                 'private'),
        ('buy',                       'public'),
        ('sell',                      'public'),
        ('dividend',                  'public'),
        ('adjustment',                'both'),
        ('fee_expense',               'both'),
        ('interest',                  'both')
       ) AS v(code, market)
 WHERE public.transaction_types.code = v.code;

-- Post-condition: zero NULLs remain among the 16 seeded codes.
--   SELECT market, count(*) FROM public.transaction_types GROUP BY 1;
--   ->  both 3 | private 10 | public 3
