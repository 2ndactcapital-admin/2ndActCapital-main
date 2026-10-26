-- REP6 FIX 4 — one model_change proposal per (account, model version).
--
-- Two callers generating for one account at once (rep3's direct after_approval
-- call and a scheduler's 'model.version_approved' sweep) each read "no
-- model_change proposal yet"; the second then superseded the first's fresh
-- draft and inserted its own — two model_change proposals for one event, the
-- first's submit failing on "superseded, not a draft". rebalance_proposals_open_uq
-- (one OPEN proposal per account) could not catch it: the first was already
-- superseded, i.e. no longer open, when the second was inserted.
--
-- The application now serializes every proposal write per account
-- (services.rebalance_proposals.account_lock, pg_advisory_xact_lock). This
-- index is the backstop: at most ONE live model_change proposal per account and
-- model version, whatever its status — the same rule on_model_version_approved
-- applies ("already proposed" once any live row exists; a superseded, rejected
-- or expired one is never re-proposed for the same version). Archived copies
-- (system_to set) are outside it.
--
-- Additive only: no DROP, no rewrite. Idempotent.

CREATE UNIQUE INDEX IF NOT EXISTS rebalance_proposals_model_change_uq
  ON public.rebalance_proposals (account_id, model_version_id)
  WHERE trigger = 'model_change' AND system_to IS NULL;
