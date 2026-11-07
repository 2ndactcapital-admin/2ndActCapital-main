-- S28 — AI drift monitor: thresholds (versioned, platform-scoped), alerts (one open per org / task / rule) and the
-- alert event history. Additive only (no destructive statement), nothing in ai_decision_log changes.
--
--   public.ai_drift_thresholds   platform defaults (task_type NULL) and optional per-task overrides. owner_scope
--                                'platform', org_id NULL (CLAUDE.md Rule 6). Valid-time versioned (Rule 3): an edit
--                                closes the live row (valid_to) and inserts version + 1. Super Admin writes only.
--   public.ai_drift_alerts       the governed alert: ONE open row per (org, task, rule) (partial unique index),
--                                refreshed with the latest numbers, closed (never deleted) after recovery.
--   public.ai_drift_alert_events append-only history of every alert: opened / updated / recovering / closed, each
--                                with the numbers it saw.
-- RLS: a policy for EVERY operation on every table (CLAUDE.md "row not found is the symptom of TWO bugs").
-- Notifications reuse member_todos (services.workflow_todos) — no new channel.

CREATE TABLE IF NOT EXISTS public.ai_drift_thresholds (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_scope   text NOT NULL DEFAULT 'platform',
    org_id        uuid NULL,
    task_type     text NULL,
    rule_key      text NOT NULL,
    threshold     numeric NULL,
    min_recent    integer NOT NULL,
    min_baseline  integer NOT NULL,
    enabled       boolean NOT NULL DEFAULT true,
    version       integer NOT NULL DEFAULT 1,
    valid_from    timestamptz NOT NULL DEFAULT clock_timestamp(),
    valid_to      timestamptz NULL,
    changed_by    uuid NULL,
    change_note   text NULL,
    CONSTRAINT ai_drift_thresholds_scope_chk CHECK (owner_scope = 'platform' AND org_id IS NULL),
    CONSTRAINT ai_drift_thresholds_rule_chk CHECK (rule_key IN (
        'cost_per_call', 'latency_p95', 'fallback_rate', 'failure_rate', 'bypass_rate',
        'effort_downgrade_rate', 'model_change', 'model_mismatch', 'silence')),
    CONSTRAINT ai_drift_thresholds_value_chk CHECK (
        (rule_key IN ('model_change', 'model_mismatch') AND threshold IS NULL)
        OR (rule_key NOT IN ('model_change', 'model_mismatch') AND threshold IS NOT NULL AND threshold >= 0)),
    CONSTRAINT ai_drift_thresholds_sample_chk CHECK (min_recent >= 1 AND min_baseline >= 0),
    CONSTRAINT ai_drift_thresholds_task_chk CHECK (task_type IS NULL OR length(btrim(task_type)) > 0),
    CONSTRAINT ai_drift_thresholds_version_chk CHECK (version >= 1),
    CONSTRAINT ai_drift_thresholds_valid_chk CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS ai_drift_thresholds_live_uq
    ON public.ai_drift_thresholds (rule_key, COALESCE(task_type, '')) WHERE valid_to IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ai_drift_thresholds_version_uq
    ON public.ai_drift_thresholds (rule_key, COALESCE(task_type, ''), version);

CREATE TABLE IF NOT EXISTS public.ai_drift_alerts (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             uuid NOT NULL REFERENCES public.organizations(id),
    task_type          text NOT NULL,
    rule_key           text NOT NULL,
    status             text NOT NULL DEFAULT 'open',
    opened_at          timestamptz NOT NULL,
    last_evaluated_at  timestamptz NOT NULL,
    last_drift_at      timestamptz NOT NULL,
    evaluations        integer NOT NULL DEFAULT 1,
    ok_since           timestamptz NULL,
    closed_at          timestamptz NULL,
    close_reason       text NULL,
    detail             text NOT NULL,
    numbers            jsonb NOT NULL,
    threshold_id       uuid NULL REFERENCES public.ai_drift_thresholds(id),
    link               text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ai_drift_alerts_status_chk CHECK (status IN ('open', 'closed')),
    CONSTRAINT ai_drift_alerts_closed_chk CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
    CONSTRAINT ai_drift_alerts_rule_chk CHECK (rule_key IN (
        'cost_per_call', 'latency_p95', 'fallback_rate', 'failure_rate', 'bypass_rate',
        'effort_downgrade_rate', 'model_change', 'model_mismatch', 'silence')),
    CONSTRAINT ai_drift_alerts_evaluations_chk CHECK (evaluations >= 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS ai_drift_alerts_open_uq
    ON public.ai_drift_alerts (org_id, task_type, rule_key) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS ai_drift_alerts_task_idx ON public.ai_drift_alerts (task_type, status);

CREATE TABLE IF NOT EXISTS public.ai_drift_alert_events (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id   uuid NOT NULL REFERENCES public.ai_drift_alerts(id),
    org_id     uuid NOT NULL REFERENCES public.organizations(id),
    event      text NOT NULL,
    at         timestamptz NOT NULL,
    numbers    jsonb NOT NULL,
    note       text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ai_drift_alert_events_event_chk CHECK (event IN ('opened', 'updated', 'recovering', 'closed'))
);
CREATE INDEX IF NOT EXISTS ai_drift_alert_events_alert_idx ON public.ai_drift_alert_events (alert_id, at);

ALTER TABLE public.ai_drift_thresholds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_drift_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_drift_alert_events ENABLE ROW LEVEL SECURITY;

-- thresholds: any caller with an org context reads (platform configuration, shown read-only to org admins);
-- only a Super Admin context writes.
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_thresholds' AND policyname = 'ai_drift_thresholds_select') THEN EXECUTE 'CREATE POLICY ai_drift_thresholds_select ON public.ai_drift_thresholds FOR SELECT USING (
    NULLIF(current_setting(''app.current_org_id'', true), '''') IS NOT NULL
    OR current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_thresholds' AND policyname = 'ai_drift_thresholds_insert') THEN EXECUTE 'CREATE POLICY ai_drift_thresholds_insert ON public.ai_drift_thresholds FOR INSERT
    WITH CHECK (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_thresholds' AND policyname = 'ai_drift_thresholds_update') THEN EXECUTE 'CREATE POLICY ai_drift_thresholds_update ON public.ai_drift_thresholds FOR UPDATE
    USING (current_setting(''app.is_super_admin'', true) = ''true'')
    WITH CHECK (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_thresholds' AND policyname = 'ai_drift_thresholds_delete') THEN EXECUTE 'CREATE POLICY ai_drift_thresholds_delete ON public.ai_drift_thresholds FOR DELETE
    USING (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;

-- alerts + events: an org reads its own; the monitor (platform scope) writes.
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_alerts' AND policyname = 'ai_drift_alerts_select') THEN EXECUTE 'CREATE POLICY ai_drift_alerts_select ON public.ai_drift_alerts FOR SELECT USING (
    org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid
    OR current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_alerts' AND policyname = 'ai_drift_alerts_insert') THEN EXECUTE 'CREATE POLICY ai_drift_alerts_insert ON public.ai_drift_alerts FOR INSERT
    WITH CHECK (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_alerts' AND policyname = 'ai_drift_alerts_update') THEN EXECUTE 'CREATE POLICY ai_drift_alerts_update ON public.ai_drift_alerts FOR UPDATE
    USING (current_setting(''app.is_super_admin'', true) = ''true'')
    WITH CHECK (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_alerts' AND policyname = 'ai_drift_alerts_delete') THEN EXECUTE 'CREATE POLICY ai_drift_alerts_delete ON public.ai_drift_alerts FOR DELETE
    USING (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_alert_events' AND policyname = 'ai_drift_alert_events_select') THEN EXECUTE 'CREATE POLICY ai_drift_alert_events_select ON public.ai_drift_alert_events FOR SELECT USING (
    org_id = (NULLIF(current_setting(''app.current_org_id'', true), ''''))::uuid
    OR current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_alert_events' AND policyname = 'ai_drift_alert_events_insert') THEN EXECUTE 'CREATE POLICY ai_drift_alert_events_insert ON public.ai_drift_alert_events FOR INSERT
    WITH CHECK (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_alert_events' AND policyname = 'ai_drift_alert_events_update') THEN EXECUTE 'CREATE POLICY ai_drift_alert_events_update ON public.ai_drift_alert_events FOR UPDATE
    USING (current_setting(''app.is_super_admin'', true) = ''true'')
    WITH CHECK (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'ai_drift_alert_events' AND policyname = 'ai_drift_alert_events_delete') THEN EXECUTE 'CREATE POLICY ai_drift_alert_events_delete ON public.ai_drift_alert_events FOR DELETE
    USING (current_setting(''app.is_super_admin'', true) = ''true'')'; END IF; END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.ai_drift_thresholds, public.ai_drift_alerts,
    public.ai_drift_alert_events TO app_service;

-- Platform defaults, version 1 (services/ai_drift.py DEFAULT_THRESHOLDS — the same values; the sample rule is in
-- [live DB: first applied 2026-11-07 with min_baseline 30; version 2 = 20 issued the same day through
-- services.ai_drift.set_threshold after the replay in docs/S28_DISCOVERY.md 1c — a fresh database seeds 20 as v1]
-- docs/S28_DISCOVERY.md 1c: a single event in the recent window must never cross a threshold on its own).
INSERT INTO public.ai_drift_thresholds (task_type, rule_key, threshold, min_recent, min_baseline, enabled, version,
                                        change_note)
SELECT NULL, v.rule_key, v.threshold, v.min_recent, v.min_baseline, v.enabled, 1, 'S28 platform default'
FROM (VALUES
    ('cost_per_call',         50::numeric,  5, 20, true),
    ('latency_p95',           50::numeric, 20, 20, true),
    ('fallback_rate',         10::numeric, 10, 20, true),
    ('failure_rate',           5::numeric, 20, 20, true),
    ('bypass_rate',           10::numeric, 10, 20, true),
    ('effort_downgrade_rate', 10::numeric, 10, 20, true),
    ('model_change',          NULL::numeric, 1, 20, true),
    ('model_mismatch',        NULL::numeric, 1,  0, true),
    ('silence',                5::numeric,  1, 20, false)
) AS v(rule_key, threshold, min_recent, min_baseline, enabled)
WHERE NOT EXISTS (SELECT 1 FROM public.ai_drift_thresholds t WHERE t.task_type IS NULL AND t.rule_key = v.rule_key);
