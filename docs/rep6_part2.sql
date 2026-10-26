-- REP6 part 2 — harvesting is OPT-IN per account (Joe, 2026-10-24).
--
-- account_model_links gains `harvest_enabled` (boolean, NOT NULL, default
-- false): a versioned, approved link setting like harvest_loss_pct. Every
-- existing link is false. The daily harvest scan (services.harvest) considers
-- only links with harvest_enabled true AND harvesting_engine 'hollisworks'.
--
-- Additive only: no DROP, no rewrite (a constant default is catalog-only), no
-- row is updated. Idempotent.

ALTER TABLE public.account_model_links ADD COLUMN IF NOT EXISTS harvest_enabled boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN public.account_model_links.harvest_enabled IS
  'rep6: the daily tax-loss harvest scan runs for this account only when true (opt-in; default false)';
