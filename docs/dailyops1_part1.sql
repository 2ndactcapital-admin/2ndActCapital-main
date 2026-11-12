-- dailyops1 — "no dollar cap" is recorded as NULL, never as a sentinel number.
--
-- portfolio.note_extraction_runs.spend_cap_usd is numeric(12,6) NOT NULL. The
-- nightly AI run has no dollar cap (its per-model daily budgets are the limit)
-- and wrote the sentinel 1e9, which overflows numeric(12,6): every nightly run
-- since 2026-11-05 stopped before reading a document. The column now allows
-- NULL (= no cap). The existing CHECK (spend_cap_usd >= 0) is unchanged and
-- passes NULL. No row is rewritten; no default changes. Idempotent.
--
-- Relaxes a constraint only (no data loss). Applied by
-- apps/api/scripts/_apply_dailyops1.py on the operator DSN (DDL needs the owner).

ALTER TABLE portfolio.note_extraction_runs ALTER COLUMN spend_cap_usd DROP NOT NULL;

COMMENT ON COLUMN portfolio.note_extraction_runs.spend_cap_usd IS
  'Per-run dollar cap (numeric(12,6), at most 999999.999999). NULL = no dollar cap: the daily model budgets '
  '(public.ai_ensemble_configs budgets, enforced by services.ai_budget) are the limit. dailyops1.';
