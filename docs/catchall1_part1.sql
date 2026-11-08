-- CATCHALL1 Task 4 — a direct asset's END (sold / disposed / transferred / written off / entered in error).
--
-- An end is a DISPOSAL ROW, never a delete and never a valid_to on the positions: positions are as-of statements
-- whose valid_to means "superseded by a restatement", so stamping it would also drop the asset from every as-of
-- BEFORE the end. The net-worth core (portfolio_rollup._current_positions) counts a position only while no current
-- disposal of its asset has end_date <= the as-of. "entered in error" ends the asset from its start (never counted).
-- Valid time = end_date; a correction of an end is a restatement (valid_to on the old row, a new row).
-- RLS: one policy per operation, org GUC through NULLIF, super-admin disjunct (the portfolio pattern).

CREATE TABLE IF NOT EXISTS portfolio.asset_disposals (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL,
    asset_id     uuid NOT NULL REFERENCES portfolio.assets (id),
    end_date     date NOT NULL,
    reason       text NOT NULL,
    proceeds     numeric,
    notes        text,
    entered_by   uuid,
    created_at   timestamptz NOT NULL DEFAULT now(),
    valid_from   timestamptz NOT NULL DEFAULT now(),
    valid_to     timestamptz,
    system_from  timestamptz NOT NULL DEFAULT now(),
    system_to    timestamptz,
    CONSTRAINT asset_disposals_reason_chk
        CHECK (reason IN ('sold', 'disposed', 'transferred', 'written_off', 'entered_in_error')),
    CONSTRAINT asset_disposals_proceeds_chk CHECK (proceeds IS NULL OR proceeds >= 0),
    CONSTRAINT asset_disposals_error_no_proceeds_chk CHECK (reason <> 'entered_in_error' OR proceeds IS NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS asset_disposals_one_current
    ON portfolio.asset_disposals (asset_id) WHERE valid_to IS NULL AND system_to IS NULL;
CREATE INDEX IF NOT EXISTS asset_disposals_org_idx ON portfolio.asset_disposals (org_id, asset_id);

ALTER TABLE portfolio.asset_disposals ENABLE ROW LEVEL SECURITY;

CREATE POLICY asset_disposals_select ON portfolio.asset_disposals FOR SELECT
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
           OR current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY asset_disposals_insert ON portfolio.asset_disposals FOR INSERT
    WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                OR current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY asset_disposals_update ON portfolio.asset_disposals FOR UPDATE
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
           OR current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                OR current_setting('app.is_super_admin', true) = 'true');
CREATE POLICY asset_disposals_delete ON portfolio.asset_disposals FOR DELETE
    USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
           OR current_setting('app.is_super_admin', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.asset_disposals TO app_service;
