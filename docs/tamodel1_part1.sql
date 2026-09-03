-- TA MODEL SPRINT 1 — Part 1: portfolio.ta_model_params + portfolio.ta_calibration_results
--
-- Task 2 schema decision: a SEPARATE table keyed by commitment_id, not new
-- columns on portfolio.commitments — bi-temporal (valid_from/valid_to,
-- system_from/system_to) per CLAUDE.md Rule 3, with a PARTIAL unique index
-- covering only the active row, the exact shape CLAUDE.md documents for
-- member_target_allocations (Sprint 8):
--
--   CREATE UNIQUE INDEX member_target_allocations_active_unique
--     ON member_target_allocations (entity_id, taxonomy_key)
--     WHERE valid_to IS NULL;
--
-- Mirrored here on (org_id, commitment_id), and additionally gated on
-- system_to IS NULL (the second bi-temporal axis every portfolio.* table
-- carries — see services/portfolio_assets.py's _current() helper).
--
-- portfolio.ta_calibration_results is a separate, APPEND-ONLY log of every
-- calibration run (not the params themselves): no valid_to/valid_from, just
-- calibrated_at. See services/ta_params.py module docstring.
--
-- RLS: single-policy tenant isolation, the same shape documented in
-- services/portfolio_assets.py for every other table in this schema —
--   org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
--   OR current_setting('app.is_super_admin', true) = 'true'
-- cmd=ALL, covering both USING and WITH CHECK.

CREATE TABLE IF NOT EXISTS portfolio.ta_model_params (
    id                    uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id                uuid NOT NULL,
    commitment_id         uuid NOT NULL REFERENCES portfolio.commitments(id),
    ta_strategy_key       text NOT NULL,
    rate_of_contribution  numeric NOT NULL,
    rate_of_distribution  numeric NOT NULL,
    growth_rate           numeric NOT NULL,
    bow_factor            numeric NOT NULL,
    fund_life_years       numeric NOT NULL,
    periods_per_year      integer NOT NULL,
    source                text NOT NULL DEFAULT 'override',
    created_by            uuid,
    valid_from            timestamptz NOT NULL DEFAULT now(),
    valid_to              timestamptz,
    system_from           timestamptz NOT NULL DEFAULT now(),
    system_to             timestamptz,
    CONSTRAINT ta_model_params_strategy_chk CHECK (
        ta_strategy_key IN (
            'buyout', 'growth_equity', 'venture_capital', 'real_estate',
            'real_assets', 'private_credit', 'fund_of_funds', 'secondaries'
        )
    ),
    CONSTRAINT ta_model_params_source_chk CHECK (source IN ('override', 'calibrated'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ta_model_params_active_unique
    ON portfolio.ta_model_params (org_id, commitment_id)
    WHERE valid_to IS NULL AND system_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_ta_model_params_commitment
    ON portfolio.ta_model_params (org_id, commitment_id);

ALTER TABLE portfolio.ta_model_params ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ta_model_params_org_isolation ON portfolio.ta_model_params;
CREATE POLICY ta_model_params_org_isolation
    ON portfolio.ta_model_params
    FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );


CREATE TABLE IF NOT EXISTS portfolio.ta_calibration_results (
    id                      uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id                  uuid NOT NULL,
    commitment_id           uuid NOT NULL REFERENCES portfolio.commitments(id),
    ta_strategy_key         text NOT NULL,
    calibrated_params       jsonb NOT NULL,
    realized_periods_used   integer NOT NULL,
    periods_per_year        integer NOT NULL,
    calibrated_at           timestamptz NOT NULL DEFAULT now(),
    created_by              uuid,
    CONSTRAINT ta_calibration_results_strategy_chk CHECK (
        ta_strategy_key IN (
            'buyout', 'growth_equity', 'venture_capital', 'real_estate',
            'real_assets', 'private_credit', 'fund_of_funds', 'secondaries'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_ta_calibration_results_commitment
    ON portfolio.ta_calibration_results (org_id, commitment_id, calibrated_at DESC);

ALTER TABLE portfolio.ta_calibration_results ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ta_calibration_results_org_isolation ON portfolio.ta_calibration_results;
CREATE POLICY ta_calibration_results_org_isolation
    ON portfolio.ta_calibration_results
    FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );
