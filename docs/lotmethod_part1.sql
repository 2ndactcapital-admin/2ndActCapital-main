-- LOTMETHOD — the lot-relief method an adviser chooses (household default,
-- account override, per-proposal override), a truer tax-optimized ranking, the
-- custodian's own cost-basis method on record, and BUGFIX2's B6 (the deal id
-- on a deal's history row).
--
-- Applied by apps/api/scripts/_apply_lotmethod.py --schema on the operator DSN
-- (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) in ONE transaction.
-- Idempotent: IF NOT EXISTS / DROP ... IF EXISTS + CREATE.
--
-- THE MIGRATION WRITES NO SETTINGS ROWS. An account with no approved method
-- row keeps TODAY'S RULE exactly (services/rebalance_engine — losses first,
-- then highest cost; long-term before short-term at equal cost; then oldest;
-- unknown cost never sold against a gain budget), so no existing proposal,
-- plan or X-ray figure changes until an adviser sets a method.
--
-- MAKER-CHECKER (Task 1b). A method is an investment-policy setting of the
-- same class as rep5's gain budget and short-term guard, which are already
-- versioned, approved link settings (account_model_links + a maker-checker
-- task). So a method change is a request a second holder of approve_proposals
-- approves — the rep7b household_authority / account_authority_settings
-- pattern, copied, and the same generic guard function.
--
-- BI-TEMPORAL (Rule 3)
--   household_lot_method, account_lot_method_settings — SYSTEM axis for the
--     decision (draft → approved / rejected on the same id; the state before
--     is archived as a copy), VALID axis for supersession (a newer approved
--     change closes the previous approved row's valid_to — never deleted).
--   rebalance_proposals / rebalance_proposal_trades — new columns only; those
--     tables keep their own rep5 axes and guards.
--   deals.version_of_deal_id — a new column on the SYSTEM-axis history copies;
--     backfilled once where the match is unambiguous (B6).

-- ── 1. Household default method ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.household_lot_method (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                uuid NOT NULL REFERENCES public.organizations(id),
  household_id          uuid NOT NULL REFERENCES public.households(id),
  lot_method            text NOT NULL DEFAULT 'tax_optimized',
  gain_preference       text NOT NULL DEFAULT 'prefer_long_term',
  realize_gains_target  numeric(28, 8),
  reason                text NOT NULL,
  status                text NOT NULL DEFAULT 'draft',
  maker_id              uuid NOT NULL REFERENCES public.users(id),
  approved_by           uuid REFERENCES public.users(id),
  approval_task_id      uuid REFERENCES public.workflow_run_steps(id),
  decided_at            timestamptz,
  decision_note         text,
  superseded_reason     text,
  archived_from_id      uuid REFERENCES public.household_lot_method(id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  valid_from            timestamptz NOT NULL DEFAULT now(),
  valid_to              timestamptz,
  system_from           timestamptz NOT NULL DEFAULT now(),
  system_to             timestamptz,
  CONSTRAINT household_lot_method_method_chk CHECK (lot_method IN ('tax_optimized', 'hifo', 'fifo', 'lifo',
                                                                   'lowest_cost', 'highest_cost', 'specific')),
  CONSTRAINT household_lot_method_pref_chk CHECK (gain_preference IN ('prefer_long_term', 'st_losses_first',
                                                                      'realize_gains')),
  -- the adviser enters the target; it exists only for realize_gains and is above zero
  CONSTRAINT household_lot_method_target_chk CHECK ((gain_preference = 'realize_gains' AND realize_gains_target > 0)
                                                    OR (gain_preference <> 'realize_gains'
                                                        AND realize_gains_target IS NULL)),
  CONSTRAINT household_lot_method_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
  CONSTRAINT household_lot_method_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT household_lot_method_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> maker_id),
  CONSTRAINT household_lot_method_pending_chk CHECK (status <> 'draft' OR approval_task_id IS NOT NULL
                                                     OR system_to IS NOT NULL),
  CONSTRAINT household_lot_method_decided_chk CHECK (status = 'draft'
                                                     OR (approved_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT household_lot_method_rejected_chk CHECK (status <> 'rejected'
                                                      OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT household_lot_method_closed_chk CHECK (valid_to IS NULL OR status = 'approved'),
  CONSTRAINT household_lot_method_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS household_lot_method_live_uq ON public.household_lot_method (household_id)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS household_lot_method_pending_uq ON public.household_lot_method (household_id)
  WHERE status = 'draft' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS household_lot_method_task_uq ON public.household_lot_method (approval_task_id)
  WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

-- ── 2. Account override + the custodian's own cost-basis method ───────────
-- A separate versioned table, not columns on public.accounts: the custody
-- importer restates accounts rows from the custodian file (rep7b's reason).
CREATE TABLE IF NOT EXISTS public.account_lot_method_settings (
  id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                        uuid NOT NULL REFERENCES public.organizations(id),
  account_id                    uuid NOT NULL REFERENCES public.accounts(id),
  lot_method_override           text,          -- NULL = the household's method
  gain_preference_override      text,          -- NULL = the household's preference
  realize_gains_target          numeric(28, 8),
  custodian_cost_basis_method   text NOT NULL DEFAULT 'unknown',
  custodian_method_source       text,          -- 'custodian' (as reported) | 'adviser' (entered); NULL when unknown
  -- securities this account's custodian carries at AVERAGE COST (mutual-fund / DRIP election, as reported):
  -- no lot choice for them; the security master marks no such thing (Task 1c)
  average_cost_security_ids     uuid[] NOT NULL DEFAULT '{}',
  reason                        text NOT NULL,
  status                        text NOT NULL DEFAULT 'draft',
  maker_id                      uuid NOT NULL REFERENCES public.users(id),
  approved_by                   uuid REFERENCES public.users(id),
  approval_task_id              uuid REFERENCES public.workflow_run_steps(id),
  decided_at                    timestamptz,
  decision_note                 text,
  superseded_reason             text,
  archived_from_id              uuid REFERENCES public.account_lot_method_settings(id),
  created_at                    timestamptz NOT NULL DEFAULT now(),
  valid_from                    timestamptz NOT NULL DEFAULT now(),
  valid_to                      timestamptz,
  system_from                   timestamptz NOT NULL DEFAULT now(),
  system_to                     timestamptz,
  CONSTRAINT account_lot_method_settings_method_chk CHECK (lot_method_override IS NULL OR lot_method_override IN (
    'tax_optimized', 'hifo', 'fifo', 'lifo', 'lowest_cost', 'highest_cost', 'specific')),
  CONSTRAINT account_lot_method_settings_pref_chk CHECK (gain_preference_override IS NULL
    OR gain_preference_override IN ('prefer_long_term', 'st_losses_first', 'realize_gains')),
  CONSTRAINT account_lot_method_settings_target_chk CHECK (
    (gain_preference_override = 'realize_gains' AND realize_gains_target > 0)
    OR (gain_preference_override IS DISTINCT FROM 'realize_gains' AND realize_gains_target IS NULL)),
  CONSTRAINT account_lot_method_settings_custodian_chk CHECK (custodian_cost_basis_method IN (
    'unknown', 'fifo', 'lifo', 'hifo', 'lowest_cost', 'highest_cost', 'average_cost', 'specific', 'tax_optimized')),
  CONSTRAINT account_lot_method_settings_source_chk CHECK (
    (custodian_cost_basis_method = 'unknown' AND custodian_method_source IS NULL)
    OR (custodian_cost_basis_method <> 'unknown' AND custodian_method_source IN ('custodian', 'adviser'))),
  CONSTRAINT account_lot_method_settings_status_chk CHECK (status IN ('draft', 'approved', 'rejected')),
  CONSTRAINT account_lot_method_settings_reason_chk CHECK (length(btrim(reason)) > 0),
  CONSTRAINT account_lot_method_settings_maker_checker_chk CHECK (approved_by IS NULL OR approved_by <> maker_id),
  CONSTRAINT account_lot_method_settings_pending_chk CHECK (status <> 'draft' OR approval_task_id IS NOT NULL
                                                            OR system_to IS NOT NULL),
  CONSTRAINT account_lot_method_settings_decided_chk CHECK (status = 'draft'
                                                            OR (approved_by IS NOT NULL AND decided_at IS NOT NULL)),
  CONSTRAINT account_lot_method_settings_rejected_chk CHECK (status <> 'rejected'
                                                             OR length(btrim(COALESCE(decision_note, ''))) > 0),
  CONSTRAINT account_lot_method_settings_closed_chk CHECK (valid_to IS NULL OR status = 'approved'),
  CONSTRAINT account_lot_method_settings_archive_chk CHECK (archived_from_id IS NULL OR system_to IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS account_lot_method_settings_live_uq
  ON public.account_lot_method_settings (account_id)
  WHERE status = 'approved' AND valid_to IS NULL AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS account_lot_method_settings_pending_uq
  ON public.account_lot_method_settings (account_id) WHERE status = 'draft' AND system_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS account_lot_method_settings_task_uq
  ON public.account_lot_method_settings (approval_task_id) WHERE approval_task_id IS NOT NULL AND system_to IS NULL;

-- the rep7b guard is generic over TG_TABLE_NAME: a draft is decided, never edited; an approved row is only closed
DROP TRIGGER IF EXISTS household_lot_method_guard_trg ON public.household_lot_method;
CREATE TRIGGER household_lot_method_guard_trg BEFORE UPDATE OR DELETE ON public.household_lot_method
  FOR EACH ROW EXECUTE FUNCTION public.authority_change_guard();
DROP TRIGGER IF EXISTS account_lot_method_settings_guard_trg ON public.account_lot_method_settings;
CREATE TRIGGER account_lot_method_settings_guard_trg BEFORE UPDATE OR DELETE ON public.account_lot_method_settings
  FOR EACH ROW EXECUTE FUNCTION public.authority_change_guard();

-- ── 3. What each proposal used (method, source, picks) ────────────────────
-- NULL on every proposal made before this sprint (they used today's rule).
ALTER TABLE public.rebalance_proposals ADD COLUMN IF NOT EXISTS lot_method text;
ALTER TABLE public.rebalance_proposals ADD COLUMN IF NOT EXISTS lot_method_source text;
ALTER TABLE public.rebalance_proposals ADD COLUMN IF NOT EXISTS gain_preference text;
ALTER TABLE public.rebalance_proposals ADD COLUMN IF NOT EXISTS realize_gains_target numeric(28, 8);
ALTER TABLE public.rebalance_proposals ADD COLUMN IF NOT EXISTS lot_picks jsonb;
ALTER TABLE public.rebalance_proposals ADD COLUMN IF NOT EXISTS custodian_cost_basis_method text;
ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_lot_method_chk;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_lot_method_chk CHECK (
  (lot_method IS NULL AND lot_method_source IS NULL AND gain_preference IS NULL AND realize_gains_target IS NULL
   AND lot_picks IS NULL)
  OR (lot_method IN ('tax_optimized', 'hifo', 'fifo', 'lifo', 'lowest_cost', 'highest_cost', 'specific')
      AND lot_method_source IN ('default', 'household', 'account', 'proposal')
      AND gain_preference IN ('prefer_long_term', 'st_losses_first', 'realize_gains')
      AND ((gain_preference = 'realize_gains' AND realize_gains_target > 0)
           OR (gain_preference <> 'realize_gains' AND realize_gains_target IS NULL))
      AND (lot_picks IS NULL OR jsonb_typeof(lot_picks) = 'array')
      AND (lot_method = 'specific' OR lot_picks IS NULL)));
ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_custodian_method_chk;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_custodian_method_chk CHECK (
  custodian_cost_basis_method IS NULL OR custodian_cost_basis_method IN (
    'unknown', 'fifo', 'lifo', 'hifo', 'lowest_cost', 'highest_cost', 'average_cost', 'specific', 'tax_optimized'));

-- each sale line: the method that chose its lots (each lot's own "why" rides in lots[].why)
ALTER TABLE public.rebalance_proposal_trades ADD COLUMN IF NOT EXISTS lot_method text;
ALTER TABLE public.rebalance_proposal_trades DROP CONSTRAINT IF EXISTS rebalance_proposal_trades_lot_method_chk;
ALTER TABLE public.rebalance_proposal_trades ADD CONSTRAINT rebalance_proposal_trades_lot_method_chk CHECK (
  lot_method IS NULL OR (side = 'sell' AND lot_method IN ('tax_optimized', 'hifo', 'fifo', 'lifo', 'lowest_cost',
                                                         'highest_cost', 'specific', 'average_cost',
                                                         'harvest_loss')));

-- ── 4. B6: the deal id on a deal's history row ────────────────────────────
-- A history copy (system_to IS NOT NULL) gets its own id; before this it was tied back to its deal only by
-- (org_id, created_at, created_by) — two deals created in one transaction by one person share both, so
-- Un-archive could restore one deal to the OTHER's prior stage. No FK: a teardown that deletes a live deal by
-- id must not be blocked by its own history copies (they are deleted by org in the same statement).
ALTER TABLE public.deals ADD COLUMN IF NOT EXISTS version_of_deal_id uuid;
CREATE INDEX IF NOT EXISTS deals_version_of_idx ON public.deals (version_of_deal_id)
  WHERE version_of_deal_id IS NOT NULL;
-- backfill ONLY where exactly one live deal matches the old key; an ambiguous copy stays NULL (the read falls
-- back to the old match for it, logged) — _apply_lotmethod.py --verify lists both counts
WITH m AS (
  SELECT h.id AS hid, min(l.id::text)::uuid AS lid, count(*) AS n
  FROM public.deals h
  JOIN public.deals l ON l.org_id = h.org_id AND l.created_at = h.created_at
                     AND l.created_by IS NOT DISTINCT FROM h.created_by
                     AND l.system_to IS NULL AND l.id <> h.id
  WHERE h.system_to IS NOT NULL AND h.version_of_deal_id IS NULL
  GROUP BY h.id
)
UPDATE public.deals d SET version_of_deal_id = m.lid FROM m WHERE d.id = m.hid AND m.n = 1;

-- ── 5. RLS — a policy for every operation each new table takes ────────────
DO $$
DECLARE t text; pred text;
BEGIN
  pred := '(org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid '
          'OR NULLIF(current_setting(''app.is_super_admin'', true), '''') = ''true'')';
  FOREACH t IN ARRAY ARRAY['household_lot_method', 'account_lot_method_settings'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_select', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT USING %s', t || '_org_select', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_insert', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT WITH CHECK %s', t || '_org_insert', t, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_update', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR UPDATE USING %s WITH CHECK %s', t || '_org_update', t,
                   pred, pred);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', t || '_org_delete', t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR DELETE USING %s', t || '_org_delete', t, pred);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO app_service', t);
  END LOOP;
END $$;
