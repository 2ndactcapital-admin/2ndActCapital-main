-- posting_templates.sql — ILPA-mapped posting templates, fanned out per org.
-- Idempotent: skips templates that already exist for the org + txn_code combination.
-- Run AFTER coa_default.sql.
--
-- posting_template_lines columns (from schema snapshot):
--   template_id, line_no, account_code (text), side ('D'/'C'), amount_source, dimension_source
--   No account_id — the posting engine resolves account_id at runtime via COA JOIN.
--
-- Account code → side mapping (sprint spec):
--   CAPITAL_CONTRIBUTION : dr 1000 (Cash)          cr 3000 (Capital — Contributed) dim member_series
--   INVESTMENT_PURCHASE  : dr 1500 (Investments)   cr 1000 (Cash)                  dim investment
--   MANAGEMENT_FEE       : dr 5000 (Mgmt Fee Exp)  cr 2000 (Accrued Expenses)      dim none
--   DISTRIBUTION         : dr 3100 (Capital Dist)  cr 1000 (Cash)                  dim member_series
--   REALIZED_GAIN        : dr 1000 (Cash)           cr 4000 (Realized Gain LT)      dim investment
--   VALUATION_MARK       : dr 1550 (Unrealized)    cr 4100 (Unreal. App. Income)   dim investment

WITH new_templates AS (
    INSERT INTO posting_templates
        (org_id, name, transaction_type_code, vehicle_type_scope, is_active)
    SELECT
        o.id,
        t.label,
        t.code,
        'any',
        true
    FROM organizations o
    CROSS JOIN (VALUES
        ('CAPITAL_CONTRIBUTION', 'Capital Contribution'),
        ('INVESTMENT_PURCHASE',  'Investment Purchase'),
        ('MANAGEMENT_FEE',       'Management Fee'),
        ('DISTRIBUTION',         'Distribution'),
        ('REALIZED_GAIN',        'Realized Gain'),
        ('VALUATION_MARK',       'Valuation Mark')
    ) AS t(code, label)
    WHERE NOT EXISTS (
        SELECT 1
        FROM posting_templates pt
        WHERE pt.org_id              = o.id
          AND pt.transaction_type_code = t.code
          AND pt.vehicle_type_scope  = 'any'
          AND pt.is_active           = true
    )
    RETURNING id, transaction_type_code
),

-- Debit + credit lines per transaction type.
-- account_code stored as text; side is 'D' (debit) or 'C' (credit).
line_spec(txn_code, account_code, side, dimension_source, line_no) AS (VALUES
    ('CAPITAL_CONTRIBUTION', '1000', 'D', 'none',          1),
    ('CAPITAL_CONTRIBUTION', '3000', 'C', 'member_series', 2),
    ('INVESTMENT_PURCHASE',  '1500', 'D', 'investment',    1),
    ('INVESTMENT_PURCHASE',  '1000', 'C', 'none',          2),
    ('MANAGEMENT_FEE',       '5000', 'D', 'none',          1),
    ('MANAGEMENT_FEE',       '2000', 'C', 'none',          2),
    ('DISTRIBUTION',         '3100', 'D', 'member_series', 1),
    ('DISTRIBUTION',         '1000', 'C', 'none',          2),
    ('REALIZED_GAIN',        '1000', 'D', 'none',          1),
    ('REALIZED_GAIN',        '4000', 'C', 'investment',    2),
    ('VALUATION_MARK',       '1550', 'D', 'investment',    1),
    ('VALUATION_MARK',       '4100', 'C', 'investment',    2)
)

INSERT INTO posting_template_lines
    (template_id, line_no, account_code, side, dimension_source)
SELECT
    nt.id,
    ls.line_no,
    ls.account_code,
    ls.side,
    ls.dimension_source
FROM new_templates         nt
JOIN line_spec             ls  ON ls.txn_code = nt.transaction_type_code
WHERE NOT EXISTS (
    SELECT 1
    FROM posting_template_lines ptl
    WHERE ptl.template_id = nt.id
);
