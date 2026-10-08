-- goldset.structural — part 2 of 2 (additive only): the review screen's skip
-- reason on a gold candidate.
--
--   skip_reason  why the reviewer skipped the note (required when status = 'skipped')
--   skipped_by   the signed-in Super Admin who skipped it
--
-- No new table, so no new RLS policies: note_gold_candidates already has the
-- four global-table policies (SELECT true; INSERT / UPDATE / DELETE only with
-- app.is_super_admin = 'true'). The table holds 0 rows today, so the CHECK is
-- added VALID. Idempotent.

ALTER TABLE portfolio.note_gold_candidates ADD COLUMN IF NOT EXISTS skip_reason text;
ALTER TABLE portfolio.note_gold_candidates ADD COLUMN IF NOT EXISTS skipped_by uuid REFERENCES public.users(id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'note_gold_candidates_skip_reason_chk'
                      AND conrelid = 'portfolio.note_gold_candidates'::regclass) THEN
        ALTER TABLE portfolio.note_gold_candidates
            ADD CONSTRAINT note_gold_candidates_skip_reason_chk
            CHECK (status <> 'skipped' OR (skip_reason IS NOT NULL AND btrim(skip_reason) <> ''));
    END IF;
END $$;
