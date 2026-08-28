-- fee36 — Part 1 CORRECTION to the already-applied fee_runs/fee_run_lines DDL.
--
-- The tables, RLS policies and both immutability triggers were applied before
-- this sprint ran and are NOT recreated here. Two defects in the trigger
-- FUNCTIONS were found by running them against the live database (both
-- reproduced in scripts/verify_fee36.py before this file was applied).
--
-- ─────────────────────────────────────────────────────────────────────────────
-- [F36-A] fee_run_lines_prevent_posted_mutation SILENTLY CANCELLED EVERY DELETE
-- ─────────────────────────────────────────────────────────────────────────────
-- The function ended `RETURN NEW`. In a BEFORE DELETE row trigger NEW is NULL,
-- and returning NULL from a BEFORE row trigger tells Postgres to SKIP the
-- operation for that row. So deleting a fee_run_line belonging to a DRAFT or
-- PREVIEW run reported `DELETE 0` and left the row in place — no error, no
-- warning, nothing in the log. Measured live:
--
--     DELETE FROM fee_run_lines WHERE id = <a PREVIEW run's line>
--       -> 'DELETE 0'; rows still present: 1
--
-- This is not cosmetic: re-running a PREVIEW replaces its lines, so every
-- re-preview would have ACCUMULATED a second full set of lines instead of
-- replacing the first, and the run's total would have doubled each time.
-- The immutability the trigger exists to enforce was also unprovable, because
-- a POSTED run's line "could not be deleted" for the same reason an unposted
-- one could not.
--
-- Fix: RETURN COALESCE(NEW, OLD) — OLD on DELETE, NEW on UPDATE.
--
-- Tightened at the same time: an UPDATE now checks the status of BOTH the run
-- the line is leaving and the run it is moving to. The original checked only
-- OLD.fee_run_id, so re-pointing a DRAFT run's line AT a POSTED run inserted a
-- line into a posted run through an UPDATE.

CREATE OR REPLACE FUNCTION public.fee_run_lines_prevent_posted_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
  old_status text;
  new_status text;
BEGIN
  SELECT status INTO old_status FROM fee_runs WHERE id = OLD.fee_run_id;
  IF old_status IN ('POSTED','EXPORTED','RECONCILED') THEN
    RAISE EXCEPTION 'fee_run_line % belongs to a % fee_run and is immutable', OLD.id, old_status
      USING ERRCODE = 'raise_exception';
  END IF;

  IF TG_OP = 'UPDATE' AND NEW.fee_run_id IS DISTINCT FROM OLD.fee_run_id THEN
    SELECT status INTO new_status FROM fee_runs WHERE id = NEW.fee_run_id;
    IF new_status IN ('POSTED','EXPORTED','RECONCILED') THEN
      RAISE EXCEPTION
        'fee_run_line % cannot be moved onto fee_run %, which is %', OLD.id, NEW.fee_run_id, new_status
        USING ERRCODE = 'raise_exception';
    END IF;
  END IF;

  -- OLD on DELETE (NEW is NULL there, and returning NULL would silently
  -- cancel the delete); NEW on UPDATE.
  RETURN COALESCE(NEW, OLD);
END;
$function$;


-- ─────────────────────────────────────────────────────────────────────────────
-- [F36-B] fee_runs_immutable_once_posted DID NOT FIRE ON DELETE
-- ─────────────────────────────────────────────────────────────────────────────
-- The trigger was BEFORE UPDATE only. A POSTED fee_run that happened to carry
-- lines was protected incidentally by fee_run_lines_fee_run_id_fkey, but a
-- POSTED run with NO lines — a period in which no account was in scope, which
-- is a perfectly ordinary posted run — deleted cleanly. Measured live:
--
--     DELETE FROM fee_runs WHERE id = <a POSTED run with no lines>
--       -> 'DELETE 1'; rows still present: 0
--
-- Relying on a foreign key for immutability means the guarantee holds only for
-- the runs that happen to have children.

CREATE OR REPLACE FUNCTION public.fee_runs_prevent_posted_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
  IF OLD.status IN ('POSTED','EXPORTED','RECONCILED') THEN
    RAISE EXCEPTION
      'fee_run % is % and immutable; corrections must be a REVERSAL run, not an update or a delete',
      OLD.id, OLD.status
      USING ERRCODE = 'raise_exception';
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$function$;

DROP TRIGGER IF EXISTS fee_runs_immutable_once_posted ON public.fee_runs;
CREATE TRIGGER fee_runs_immutable_once_posted
  BEFORE UPDATE OR DELETE ON public.fee_runs
  FOR EACH ROW EXECUTE FUNCTION public.fee_runs_prevent_posted_mutation();
