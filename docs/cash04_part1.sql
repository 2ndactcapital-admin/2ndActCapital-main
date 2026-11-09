-- CASH04 part 1 — a cash proposal is a KIND of rebalance proposal (docs/cash/CASH04_NOTES.md §3). Idempotent.
-- Applied by apps/api/scripts/_apply_cash04.py --schema (operator DSN: DDL needs the table owner).
--
--   * rebalance_proposals_kind_chk   + a strict 'cash' branch; the 'model' branch refuses the cash_waterfall trigger
--   * rebalance_proposals_trigger_chk + 'cash_waterfall' (the maturity-waterfall job's system drafts)
--   * rebalance_proposal_trades.cash_leg (jsonb) + rebalance_proposal_trades_cash_leg_chk — the rep8 option_leg /
--     rep13 short_leg pattern: the sleeve, the price kind (a bill / note by par with a limit), the reason
--   * rebalance_proposals_cash_waterfall_uq — one live waterfall proposal per (account, period): the idempotency backstop
--
-- execution_instructions is NOT touched: a cash line is buy / sell, exactly one of quantity / notional, market / limit.
-- Writes no row. One transaction: the apply script wraps it (no BEGIN / COMMIT here).

ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_trigger_chk;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_trigger_chk CHECK (
  trigger = ANY (ARRAY['join'::text, 'model_change'::text, 'drift'::text, 'cash_flow'::text, 'manual'::text,
                       'harvest'::text, 'cash_waterfall'::text]));

ALTER TABLE public.rebalance_proposals DROP CONSTRAINT IF EXISTS rebalance_proposals_kind_chk;
ALTER TABLE public.rebalance_proposals ADD CONSTRAINT rebalance_proposals_kind_chk CHECK (
  ((proposal_kind = 'model'::text) AND (link_id IS NOT NULL) AND (model_id IS NOT NULL)
    AND (model_version_id IS NOT NULL) AND (trigger <> 'cash_waterfall'::text))
  OR ((proposal_kind = 'one_off'::text) AND (trigger = 'manual'::text) AND (maker_kind = 'user'::text)
    AND (link_id IS NULL) AND (model_id IS NULL) AND (model_version_id IS NULL))
  OR ((proposal_kind = 'options'::text) AND (trigger = 'manual'::text) AND (maker_kind = 'user'::text)
    AND (model_id IS NOT NULL) AND (trigger_ref ? 'option_package_id'::text))
  OR ((proposal_kind = 'cash'::text) AND (link_id IS NULL) AND (model_id IS NULL) AND (model_version_id IS NULL)
    AND (trigger_ref ? 'cash_policy_id'::text)
    AND (((trigger = 'manual'::text) AND (maker_kind = 'user'::text))
      OR ((trigger = 'cash_waterfall'::text) AND (maker_kind = 'system'::text) AND (trigger_ref ? 'period'::text)))));

ALTER TABLE public.rebalance_proposal_trades ADD COLUMN IF NOT EXISTS cash_leg jsonb;
ALTER TABLE public.rebalance_proposal_trades DROP CONSTRAINT IF EXISTS rebalance_proposal_trades_cash_leg_chk;
ALTER TABLE public.rebalance_proposal_trades ADD CONSTRAINT rebalance_proposal_trades_cash_leg_chk CHECK (
  (cash_leg IS NULL)
  OR ((jsonb_typeof(cash_leg) = 'object'::text)
    AND ((cash_leg ->> 'sleeve'::text) = ANY (ARRAY['near_term'::text, 'reserve'::text, 'long_term'::text]))
    AND ((cash_leg ->> 'price_kind'::text) = ANY (ARRAY['market'::text, 'limit'::text]))
    AND (side = ANY (ARRAY['buy'::text, 'sell'::text]))
    AND (((cash_leg ->> 'price_kind'::text) = 'market'::text AND (cash_leg ->> 'limit_price'::text) IS NULL)
      OR ((cash_leg ->> 'price_kind'::text) = 'limit'::text AND (cash_leg ->> 'limit_price'::text) IS NOT NULL
        AND ((cash_leg ->> 'limit_price'::text))::numeric > (0)::numeric AND (quantity IS NOT NULL)))
    AND (option_leg IS NULL) AND (short_leg IS NULL)));

CREATE UNIQUE INDEX IF NOT EXISTS rebalance_proposals_cash_waterfall_uq ON public.rebalance_proposals
  USING btree (account_id, ((trigger_ref ->> 'period'::text)))
  WHERE ((trigger = 'cash_waterfall'::text) AND (system_to IS NULL) AND (archived_from_id IS NULL));

