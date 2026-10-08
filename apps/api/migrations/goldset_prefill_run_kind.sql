-- goldset.structural — part 1 of 2: widen note_extraction_runs.run_kind with
-- 'gold_prefill' (one gpt-5-mini pre-fill run per gold batch).
--
-- Split from goldset_candidate_review.sql because it contains DROP CONSTRAINT
-- (the MCP migration tool has cancelled DROP-containing migrations before).
-- Nothing is removed: every value the old constraint accepted is still accepted.
-- Idempotent.

ALTER TABLE portfolio.note_extraction_runs
    DROP CONSTRAINT IF EXISTS note_extraction_runs_run_kind_check;
ALTER TABLE portfolio.note_extraction_runs
    ADD CONSTRAINT note_extraction_runs_run_kind_check
    CHECK (run_kind = ANY (ARRAY['evaluation', 'pilot', 'cascade', 'participant_seed', 'verify', 'gold_prefill']));
