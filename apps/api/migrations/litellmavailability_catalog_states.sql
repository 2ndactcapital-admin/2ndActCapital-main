-- LiteLLM D2 §3 follow-up — three-state model availability
-- (docs/LITELLM_D2_E_SPEC.md §3). ALREADY APPLIED AND VERIFIED LIVE before
-- this sprint started (per the sprint prompt) — this file exists so the
-- change is tracked in the repo like every other migration, not to be
-- re-run.
--
-- 'available'  — in the org picker, usable (today's only real state).
-- 'deprecated' — hidden from the picker for orgs that have not already
--                selected it; an org's EXISTING selection keeps resolving
--                and working; affected orgs are alerted.
-- 'disabled'   — never resolves; a task that would have used it falls back
--                to the org safe model; affected orgs are alerted.
--
-- All three existing rows (claude-sonnet, claude-haiku, voyage-3.5) default
-- to 'available' — nothing changes behaviourally until a state is set.
ALTER TABLE platform_model_catalog
  ADD COLUMN availability text NOT NULL DEFAULT 'available',
  ADD CONSTRAINT platform_model_catalog_availability_chk
    CHECK (availability IN ('available', 'deprecated', 'disabled'));
