-- fee43 — invoices, reconciliation, GL posting.
--
-- Part 1 (ledger_books, journal_entries.vehicle_kind, fee_invoices,
-- fee_receipts) was applied separately and is NOT repeated here. This file is
-- the sprint's own schema work:
--
--   1. REPAIR  fn_reverse_journal_entry, which Part 1's NOT NULL vehicle_kind
--              column broke (finding F43-A).
--   2. SEED    the two ledger_books, the revenue/receivable/expense accounts
--              the RIA and club books need, and the posting templates that
--              expand into them.
--
-- Idempotent: safe to re-run. Every insert is guarded, and the guard matches
-- the deployed partial unique index (all three are partial on the live axis,
-- so a plain ON CONFLICT (a, b) would not match the index).

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- 1. REPAIR — fn_reverse_journal_entry
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Part 1 added journal_entries.vehicle_kind as NOT NULL with no DEFAULT. This
-- function's INSERT..SELECT names its columns explicitly and did not name that
-- one, so EVERY reversal of EVERY entry has been failing with a
-- not-null violation since Part 1 landed. Reproduced against the live database
-- before this fix was written.
--
-- The reversal carries the ORIGINAL entry's vehicle_kind, for the same reason
-- it already carries its vehicle_id: a reversal books in the same vehicle's
-- books as the entry it reverses, or it is not a reversal.
CREATE OR REPLACE FUNCTION public.fn_reverse_journal_entry(
  p_entry_id uuid, p_reason text, p_user uuid, p_entry_date date DEFAULT NULL::date
) RETURNS uuid LANGUAGE plpgsql AS $function$
DECLARE v_new uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM journal_entries WHERE id=p_entry_id AND posted_at IS NOT NULL)
    THEN RAISE EXCEPTION 'Cannot reverse unposted entry %', p_entry_id; END IF;

  INSERT INTO journal_entries (org_id, vehicle_id, vehicle_kind, entry_date,
                               ledger_basis, transaction_type_code, memo,
                               reverses_entry_id, reversal_reason, created_by)
  SELECT org_id, vehicle_id, vehicle_kind, COALESCE(p_entry_date, entry_date),
         ledger_basis, transaction_type_code, 'Reversal of ' || id::text, id,
         p_reason, p_user
    FROM journal_entries WHERE id = p_entry_id
  RETURNING id INTO v_new;

  INSERT INTO journal_lines (entry_id, line_no, account_id, debit, credit, currency_code,
                             dim_member_series_id, dim_investment_id, dim_tax_lot_id, memo)
  SELECT v_new, line_no, account_id, credit, debit, currency_code,
         dim_member_series_id, dim_investment_id, dim_tax_lot_id, memo
    FROM journal_lines WHERE entry_id = p_entry_id;

  PERFORM fn_post_journal_entry(v_new, p_user);
  RETURN v_new;
END $function$;


-- ═══════════════════════════════════════════════════════════════════════════
-- 2. SEED — ledger_books
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Two books, not one. The RIA and the 501(c)(6) club are legally distinct
-- businesses that happen to share an org_id; a single book would report each
-- entity's revenue as the other's. Books are bi-temporal on the system axis
-- (ledger_books_code_uq is partial on system_to IS NULL) — retiring a book is a
-- system-axis close, never a delete.
INSERT INTO ledger_books (org_id, book_code, name, description)
SELECT '00000000-0000-0000-0000-000000000001'::uuid, v.book_code, v.name, v.description
FROM (VALUES
  ('RIA_OPERATING', 'RIA Operating',
   'The registered investment adviser''s own books. Advisory, planning and '
   'transaction fee revenue earned BY the firm — as distinct from a fee an SPV '
   'accrues to its manager, which books inside that SPV.'),
  ('CLUB_DUES', 'Club Operating',
   'The 501(c)(6) membership club''s own books. Membership dues revenue. Kept '
   'separate from RIA_OPERATING because the club is a legally distinct entity '
   'and merging the two would misstate both entities'' financials.')
) AS v(book_code, name, description)
WHERE NOT EXISTS (
  SELECT 1 FROM ledger_books b
  WHERE b.org_id = '00000000-0000-0000-0000-000000000001'::uuid
    AND b.book_code = v.book_code AND b.system_to IS NULL
);


-- ═══════════════════════════════════════════════════════════════════════════
-- 3. SEED — chart_of_accounts
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Matching the DEPLOYED convention, which is flat: all 20 pre-existing rows
-- have parent_code NULL, so there is no hierarchy to slot into (finding F43-B).
-- What the chart actually encodes is a 4-digit code banded by account_type —
-- 1xxx ASSET/D, 2xxx LIABILITY/C, 3xxx EQUITY/C, 4xxx INCOME/C, 5xxx EXPENSE/D,
-- 9xxx MEMO/D — with in-band gaps of 10 or 100. Each new code takes the next
-- free slot in its own band and none collides with an existing code.
--
-- tax_character_code carries no CHECK and the values in use are a partnership
-- K-1 vocabulary (LT_CAP_GAIN, ORDINARY, SYNDICATION_COST, ...). Advisory,
-- planning, placement and incentive fees are ordinary business items, so
-- ORDINARY is right for them. Club dues are a 501(c)(6)'s exempt-function
-- income, for which that vocabulary has no member — left NULL rather than
-- mislabelled ORDINARY, matching the eight existing rows that also carry no
-- tax character.
--
-- FOUR revenue accounts, not the two the sprint prompt sketched. The extra two
-- are not decoration: fee39 already resolves every fee_run_line to one of five
-- reachable revenue_types, and ASSET_MANAGEMENT / PLANNING / TRANSACTION are
-- three DIFFERENT ones (ADVISORY_FEE, PLANNING_FEE, PLACEMENT_FEE) that all
-- book to the RIA_OPERATING book. Collapsing them into a single "advisory"
-- account would put planning and placement revenue on the advisory line of the
-- firm's income statement, and would make revenue_events and the GL impossible
-- to reconcile line-for-line. One account per revenue_type makes that
-- reconciliation a straight comparison.
--
-- Receivables are NOT split the same way. A receivable is a claim on the same
-- client in the same book; which revenue type created it is on the revenue
-- side, and splitting the asset would add a distinction nobody collects on.
INSERT INTO chart_of_accounts
  (org_id, code, name, account_type, normal_balance, tax_character_code,
   parent_code, is_capital_account, is_active)
SELECT '00000000-0000-0000-0000-000000000001'::uuid, v.code, v.name,
       v.account_type, v.normal_balance, v.tax_character_code, NULL, false, true
FROM (VALUES
  -- receivables: the debit side of the firm's own revenue, one per book
  ('1210', 'Fees Receivable',                     'ASSET',     'D', NULL),
  ('1220', 'Club Dues Receivable',                'ASSET',     'D', NULL),
  -- the firm's own revenue, one account per fee39 revenue_type
  ('4400', 'Advisory Fee Revenue',                'INCOME',    'C', 'ORDINARY'),
  ('4500', 'Planning Fee Revenue',                'INCOME',    'C', 'ORDINARY'),
  ('4600', 'Club Dues Revenue',                   'INCOME',    'C', NULL),
  ('4700', 'Placement Fee Revenue',               'INCOME',    'C', 'ORDINARY'),
  -- vehicle-side expense: what an SPV accrues to its manager
  ('5400', 'Placement Fee Expense',               'EXPENSE',   'D', 'ORDINARY'),
  ('5500', 'Carried Interest — Incentive Allocation', 'EXPENSE', 'D', 'ORDINARY')
) AS v(code, name, account_type, normal_balance, tax_character_code)
WHERE NOT EXISTS (
  SELECT 1 FROM chart_of_accounts c
  WHERE c.org_id = '00000000-0000-0000-0000-000000000001'::uuid
    AND c.code = v.code AND c.system_to IS NULL
);


-- ═══════════════════════════════════════════════════════════════════════════
-- 4. SEED — posting_templates
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Same mechanism the SPV ledger already uses: resolved by
-- (org_id, transaction_type_code, vehicle_type_scope='any', is_active), then
-- expanded line-by-line by services.ledger.posting. No parallel mechanism.
--
-- posting_templates carries no unique constraint on
-- (org_id, transaction_type_code), so these are guarded by NOT EXISTS rather
-- than ON CONFLICT.
--
-- MANAGEMENT_FEE is NOT redefined here. product_type 'SPV' books through the
-- template that already exists (D 5000 Management Fee Expense / C 2000 Accrued
-- Expenses), because that is exactly what it means: an SPV accruing a
-- management fee to its manager. Reused, not re-created.
INSERT INTO posting_templates (org_id, transaction_type_code, name, vehicle_type_scope, is_active)
SELECT '00000000-0000-0000-0000-000000000001'::uuid, v.code, v.name, 'any', true
FROM (VALUES
  ('ADVISORY_FEE_REVENUE',  'Advisory Fee Revenue'),
  ('PLANNING_FEE_REVENUE',  'Planning Fee Revenue'),
  ('CLUB_DUES_REVENUE',     'Club Dues Revenue'),
  ('PLACEMENT_FEE_REVENUE', 'Placement Fee Revenue'),
  ('SPV_PLACEMENT_FEE',     'SPV Placement Fee'),
  ('CARRY_ALLOCATION',      'Carried Interest Allocation')
) AS v(code, name)
WHERE NOT EXISTS (
  SELECT 1 FROM posting_templates t
  WHERE t.org_id = '00000000-0000-0000-0000-000000000001'::uuid
    AND t.transaction_type_code = v.code
);

-- Template lines. dimension_source is 'none' on every one of them: the member
-- series dimension has no dim_member_series table to point at, so populating it
-- would be inventing a key. This is why fee43 does NOT incidentally repair
-- v_capital_accounts (finding F43-E).
INSERT INTO posting_template_lines (template_id, line_no, account_code, side, amount_source, dimension_source)
SELECT t.id, v.line_no, v.account_code, v.side, 'event_amount', 'none'
FROM (VALUES
  -- The firm earns fee revenue: receivable up, the matching revenue line up.
  ('ADVISORY_FEE_REVENUE',  1, '1210', 'D'),
  ('ADVISORY_FEE_REVENUE',  2, '4400', 'C'),
  ('PLANNING_FEE_REVENUE',  1, '1210', 'D'),
  ('PLANNING_FEE_REVENUE',  2, '4500', 'C'),
  ('PLACEMENT_FEE_REVENUE', 1, '1210', 'D'),
  ('PLACEMENT_FEE_REVENUE', 2, '4700', 'C'),
  -- The club earns dues, into its own book's own receivable.
  ('CLUB_DUES_REVENUE',     1, '1220', 'D'),
  ('CLUB_DUES_REVENUE',     2, '4600', 'C'),
  -- Inside an SPV's books: a placement fee accrued to the manager. The mirror
  -- of the firm's revenue, booked in the vehicle that owes it.
  ('SPV_PLACEMENT_FEE',     1, '5400', 'D'),
  ('SPV_PLACEMENT_FEE',     2, '2000', 'C'),
  -- Inside an SPV's books: carried interest owed to the manager. Credited to
  -- the EXISTING 2100 Due to Affiliate, because no GP legal entity exists in
  -- this schema to hold a capital account (finding F43-D). This books carry as
  -- a payable to the manager, not as an equity allocation to a GP.
  ('CARRY_ALLOCATION',      1, '5500', 'D'),
  ('CARRY_ALLOCATION',      2, '2100', 'C')
) AS v(code, line_no, account_code, side)
JOIN posting_templates t
  ON t.org_id = '00000000-0000-0000-0000-000000000001'::uuid
 AND t.transaction_type_code = v.code
ON CONFLICT (template_id, line_no) DO NOTHING;

COMMIT;
