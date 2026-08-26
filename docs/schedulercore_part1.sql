-- schedulercore.structural — Part 1
-- ============================================================================
-- Makes `workflow_triggers.schedule_cron` actionable. Before this migration the
-- column was DEAD: schedulerdiscovery.lowrisk confirmed (§3.4) that nothing in
-- the repo reads it to act on, and that POST /admin/workflow-triggers had no
-- path to create a schedule-type trigger at all.
--
-- Six columns are added. Each exists because the firing loop cannot be correct
-- without it:
--
--   timezone          Render's cron scheduling is UTC-ONLY and cannot be made
--                     timezone-aware (Render docs, confirmed 2026-08-26). A
--                     per-org "every weekday at 9am" therefore has to be
--                     resolved in OUR recurrence computation, against a real
--                     IANA zone, on every tick. NOT NULL DEFAULT 'UTC' so every
--                     pre-existing row keeps a well-defined, non-surprising
--                     meaning rather than a NULL the loop has to guess at.
--
--   start_date        Lower bound. An occurrence computed before this instant
--   end_date          is not due. Upper bound; past it the trigger is spent.
--   max_occurrences   Total-fire cap; NULL = unbounded.
--   occurrence_count  The running counter compared against max_occurrences.
--                     NOT NULL DEFAULT 0 — a NULL counter would make
--                     `occurrence_count < max_occurrences` NULL, i.e. never
--                     true, silently disabling every capped trigger.
--
--   last_fired_at     THE IDEMPOTENCY KEY. The tick computes the most recent
--                     occurrence at or before now (in the trigger's own zone)
--                     and fires only when last_fired_at does not already cover
--                     it. The claim is a single conditional UPDATE
--                     (`WHERE last_fired_at IS NULL OR last_fired_at < $occ`),
--                     so two concurrent ticks cannot both win: the second one's
--                     UPDATE matches zero rows.
--
-- Deliberately NOT added:
--   * a CHECK on `timezone` — validating an IANA name needs pg_timezone_names,
--     which is not IMMUTABLE and so cannot appear in a CHECK. It is validated
--     at the API boundary (routers/workflows.ScheduleTriggerCreate) and again
--     in services/workflow_schedule.py, which raises on an unknown zone rather
--     than silently falling back to UTC.
--   * a CHECK on `trigger_type` — the deployed vocabulary is 'manual' /
--     'event' / 'scheduled' by convention only, and constraining it now would
--     be a behaviour change this sprint did not scope.
--
-- NOTE ON THE VALUE 'scheduled'. The sprint prompt says trigger_type
-- 'schedule'. The DEPLOYED data and the frontend both say **'scheduled'**
-- (workflow_triggers row 99000000-…-04b1, and the equality test at
-- apps/web/components/admin/WorkflowTriggerScheduler.jsx:114). 'scheduled' is
-- what this sprint uses everywhere; adopting the prompt's spelling would have
-- orphaned the existing row and broken the trigger list UI.
--
-- Idempotent: safe to run more than once.
-- ============================================================================

ALTER TABLE workflow_triggers
    ADD COLUMN IF NOT EXISTS timezone         text    NOT NULL DEFAULT 'UTC',
    ADD COLUMN IF NOT EXISTS start_date       timestamptz,
    ADD COLUMN IF NOT EXISTS end_date         timestamptz,
    ADD COLUMN IF NOT EXISTS max_occurrences  integer,
    ADD COLUMN IF NOT EXISTS occurrence_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_fired_at    timestamptz;

COMMENT ON COLUMN workflow_triggers.timezone IS
    'IANA zone name (e.g. America/New_York) the schedule_cron expression is '
    'interpreted in. Render cron is UTC-only, so per-org local time is resolved '
    'by services/workflow_schedule.py on every tick, not by the cron schedule.';
COMMENT ON COLUMN workflow_triggers.last_fired_at IS
    'The UTC instant of the OCCURRENCE most recently fired (not the wall-clock '
    'time of the tick). Idempotency key: the conditional-UPDATE claim in '
    'services/workflow_scheduler.py fires only when this does not already cover '
    'the computed occurrence.';
COMMENT ON COLUMN workflow_triggers.occurrence_count IS
    'Running count of fires, incremented in the SAME conditional UPDATE that '
    'claims an occurrence. Compared against max_occurrences.';

-- A cap of zero or a negative cap is a configuration error, not "unlimited".
ALTER TABLE workflow_triggers
    DROP CONSTRAINT IF EXISTS workflow_triggers_max_occurrences_positive;
ALTER TABLE workflow_triggers
    ADD CONSTRAINT workflow_triggers_max_occurrences_positive
    CHECK (max_occurrences IS NULL OR max_occurrences > 0);

-- An end before the start can never fire; reject it at write time rather than
-- leaving a permanently-inert row that looks active in the trigger list.
ALTER TABLE workflow_triggers
    DROP CONSTRAINT IF EXISTS workflow_triggers_date_window_ordered;
ALTER TABLE workflow_triggers
    ADD CONSTRAINT workflow_triggers_date_window_ordered
    CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date);

-- The scheduler tick's ONLY scan: active schedule-type triggers, all orgs. The
-- partial index keeps that scan off the event/manual rows, which are the
-- majority and are never candidates.
CREATE INDEX IF NOT EXISTS idx_workflow_triggers_due_scan
    ON workflow_triggers (trigger_type, is_active)
    WHERE trigger_type = 'scheduled' AND is_active;
