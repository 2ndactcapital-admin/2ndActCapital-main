-- coa_default.sql — default chart of accounts, fanned out per org.
-- Idempotent: skips accounts already present (system_to IS NULL, same code+org).
-- Run AFTER the Sprint 22 Part 1 SQL migration.
-- DO NOT add hardcoded UUIDs.
-- Column names from schema snapshot: is_capital_account, tax_character_code (NOT is_capital, tax_character).

INSERT INTO chart_of_accounts
    (org_id, code, name, account_type, is_capital_account, tax_character_code, normal_balance)
SELECT
    o.id,
    t.code,
    t.name,
    t.account_type,
    t.is_capital_account,
    t.tax_character_code,
    t.normal_balance
FROM organizations o
CROSS JOIN (VALUES
    -- ASSETS (normal balance D)
    ('1000', 'Cash',                         'ASSET',    false, NULL,                  'D'),
    ('1200', 'Subscriptions Receivable',     'ASSET',    false, NULL,                  'D'),
    ('1500', 'Investments at Cost',          'ASSET',    false, NULL,                  'D'),
    ('1550', 'Unrealized Appreciation',      'ASSET',    false, NULL,                  'D'),
    -- LIABILITIES (normal balance C)
    ('2000', 'Accrued Expenses',             'LIABILITY',false, NULL,                  'C'),
    ('2100', 'Due to Affiliate',             'LIABILITY',false, NULL,                  'C'),
    -- EQUITY (normal balance C)
    ('3000', 'Capital — Contributed',        'EQUITY',   true,  NULL,                  'C'),
    ('3100', 'Capital — Distributed',        'EQUITY',   true,  NULL,                  'C'),
    ('3200', 'Capital — Syndication Costs',  'EQUITY',   true,  'SYNDICATION_COST',    'C'),
    ('3300', 'Capital — Allocated Income',   'EQUITY',   true,  NULL,                  'C'),
    -- INCOME (normal balance C)
    ('4000', 'Realized Gain — Long Term',    'INCOME',   false, 'LT_CAP_GAIN',         'C'),
    ('4010', 'Realized Gain — Short Term',   'INCOME',   false, 'ST_CAP_GAIN',         'C'),
    ('4100', 'Unrealized Appreciation Income','INCOME',  false, 'LT_CAP_GAIN',         'C'),
    ('4200', 'Interest Income',              'INCOME',   false, 'PORTFOLIO_INTEREST',   'C'),
    ('4300', 'Dividend Income',              'INCOME',   false, 'QUAL_DIVIDEND',        'C'),
    -- EXPENSE (normal balance D)
    ('5000', 'Management Fee Expense',       'EXPENSE',  false, 'ORDINARY',            'D'),
    ('5100', 'Fund Admin Expense',           'EXPENSE',  false, 'ORDINARY',            'D'),
    ('5200', 'Legal & Organizational',       'EXPENSE',  false, 'ORDINARY',            'D'),
    ('5300', 'Interest Expense',             'EXPENSE',  false, 'SEC_163J_INT_EXP',    'D'),
    -- MEMO (normal balance D)
    ('9000', 'Unfunded Commitments',         'MEMO',     false, NULL,                  'D')
) AS t(code, name, account_type, is_capital_account, tax_character_code, normal_balance)
WHERE NOT EXISTS (
    SELECT 1
    FROM chart_of_accounts c
    WHERE c.org_id = o.id
      AND c.code   = t.code
      AND c.system_to IS NULL
);
