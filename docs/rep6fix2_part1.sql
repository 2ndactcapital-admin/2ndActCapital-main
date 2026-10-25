-- REP6 FIX 2 — organizations.is_test: schedulers never act on a test org.
--
-- A crashed verify_rep6 teardown left its fixture orgs in the database, and the
-- live scheduler (workflow_scheduler_tick → rebalance_proposals.proposals_tick)
-- ran the drift scan on their accounts every tick: 854 drift proposals, and
-- concurrent writes that deadlocked the next verify's teardown.
--
-- organizations.is_test  verify fixtures. Mirrors users.is_test
--                        (docs/usermgmt1_part1.sql): a trigger sets it for
--                        every id in the reserved verify prefix '99000000-',
--                        so every verify script that seeds a fixture org with
--                        that prefix is flagged with no per-script change;
--                        a verify script may also set it explicitly at insert.
--                        Read through services.orgs (is_test_org /
--                        NOT_TEST_ORG_SQL) by every scheduled sweep.
--
-- Additive only: no DROP, no rewrite (a constant default is catalog-only).
-- The one UPDATE touches fixture-prefix rows only. Idempotent.

ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS is_test boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN public.organizations.is_test IS
  'Verify fixture org. Scheduled sweeps skip it (services.orgs). Set by trigger for the reserved id prefix 99000000-.';

CREATE OR REPLACE FUNCTION public.organizations_mark_test_fixture()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF NEW.id::text LIKE '99000000-%' THEN
    NEW.is_test := true;
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER organizations_mark_test_fixture
  BEFORE INSERT OR UPDATE OF id, is_test ON public.organizations
  FOR EACH ROW EXECUTE FUNCTION public.organizations_mark_test_fixture();

UPDATE public.organizations SET is_test = true WHERE id::text LIKE '99000000-%' AND NOT is_test;
