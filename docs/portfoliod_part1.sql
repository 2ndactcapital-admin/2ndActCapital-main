-- ===========================================================================
-- Portfolio Phase D — Part 1
--   * portfolio.spv_derived_positions   — the SPV derivation VIEW
--   * two partial unique indexes         — "one asset per SPV", "one cash asset
--                                          per (org, currency)"
--
-- Written AFTER Task 1's discovery, not before it. The brief deliberately
-- shipped no pre-specified schema for this phase because the correct
-- SPV-valuation join was a real question; what follows is the join that the
-- deployed database actually supports, not the one that would have been
-- guessed.
--
-- Idempotent: safe to re-run.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- 1 · One asset per SPV — enforced, not merely intended.
--
-- The brief says "one asset per spv, not per subscription". Without an index
-- that is a comment: two concurrent calls to ensure_spv_asset() would both miss
-- the SELECT and both INSERT, and the derivation view would then project every
-- subscription TWICE, silently doubling the SPV in every rollup downstream.
--
-- PARTIAL on the current-row predicate, exactly as
-- member_target_allocations_active_unique is (CLAUDE.md "Schema Notes"): the
-- portfolio tables are bi-temporal, so unlimited superseded history for the
-- same SPV must stay legal and only the CURRENT row is constrained.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS assets_internal_spv_active_uniq
    ON portfolio.assets (org_id, internal_spv_id)
    WHERE internal_spv_id IS NOT NULL
      AND valid_to  IS NULL
      AND system_to IS NULL;


-- ---------------------------------------------------------------------------
-- 2 · One cash asset per (org, currency_code) — the Task 3 idempotency key.
--
-- Same reasoning. `ensure_cash_asset` is find-or-create in Python; this index
-- is what makes "idempotently" true under concurrency rather than true only
-- when nothing races. USD cash and EUR cash are two different assets; the same
-- org asking for USD cash twice must get the same row back.
--
-- NOTE the deliberate asymmetry with index 1: currency_code is NULLABLE on
-- portfolio.assets, and a NULL is not equal to itself in a unique index, so
-- this constrains nothing for a cash asset with no currency. ensure_cash_asset
-- therefore REQUIRES currency_code and refuses NULL — the Python side closes
-- the hole the index cannot.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS assets_cash_active_uniq
    ON portfolio.assets (org_id, currency_code)
    WHERE asset_type = 'cash'
      AND valid_to  IS NULL
      AND system_to IS NULL;


-- ---------------------------------------------------------------------------
-- 3 · portfolio.spv_derived_positions — the derivation view.
--
-- ===========================================================================
-- WHY security_invoker = true, AND WHAT HAPPENS WITHOUT IT
-- ===========================================================================
-- Every table this view reads (spv_subscriptions, spvs, portfolio.assets,
-- portfolio.valuations) has RLS enabled with ONE org-isolation policy. All four
-- are owned by `postgres`, and so is this view.
--
-- A Postgres view WITHOUT security_invoker executes its query as the view's
-- OWNER. `postgres` has rolbypassrls. So the default-built view would return
-- EVERY TENANT'S SUBSCRIPTIONS TO EVERY TENANT, and nothing would raise —
-- app_service would be handed rows RLS exists specifically to hide, through a
-- relation that looks exactly like the org-isolated table it was derived from.
-- That is the single worst thing this phase could ship, and it is the DEFAULT.
--
-- security_invoker = true (PG15+; deployed server is 17.6, introspected) makes
-- the underlying policies evaluate against the QUERYING role, so the view is
-- exactly as org-isolated as portfolio.positions is. verify_portfoliod.py
-- asserts both the reloption and the behaviour, on the real app_service
-- connection, because the reloption alone is a claim.
--
-- ===========================================================================
-- WHY THERE IS NO WRITE PATH, AND WHY THAT IS ENFORCED TWICE
-- ===========================================================================
-- spv_subscriptions remains the book of record; a correction goes there. The
-- view must therefore never be writable:
--
--   (a) It is not auto-updatable. Postgres auto-updates only single-relation
--       views with no CTE, DISTINCT, join or aggregate. This has all four, so
--       INSERT/UPDATE/DELETE raise "cannot insert into view" of their own
--       accord.
--   (b) The grants are revoked anyway. `ALTER DEFAULT PRIVILEGES` in the
--       portfolio schema grants app_service arwd on every new relation
--       (introspected: defaclacl = {app_service=arwd/postgres}) and a VIEW is a
--       relation, so app_service would otherwise HOLD INSERT/UPDATE/DELETE on
--       this view. They would fail at (a) — but a privilege that is only
--       harmless because of a rewrite-rule technicality is not a decision, and
--       the day someone simplifies this view into a single-table projection it
--       silently becomes an editable shadow copy of spv_subscriptions.
--
-- ===========================================================================
-- WHICH SUBSCRIPTIONS PROJECT, AND WHY THOSE
-- ===========================================================================
-- The predicate is lifted VERBATIM from services/spv_allocation.py, which is
-- the deployed definition of an active subscription — not a new rule invented
-- here:
--
--     valid_to IS NULL                                   (Task 1a: the only
--                                                         temporal axis)
--     subscription_status IN ('committed','funded')      (spv_allocation.py:72)
--     ownership_pct IS NOT NULL                          (spv_allocation.py:92,
--                                                         "skip subs without a
--                                                          post-close pct")
--
-- The last one is also a hard requirement of the position shape: A2's
-- _validate_basis refuses ownership_basis='percent' with a NULL ownership_pct,
-- so a row without it could not be a legitimate percent position at all.
--
-- CONSEQUENCE, stated plainly: both subscriptions currently deployed are
-- status='soft' with ownership_pct NULL, so THE VIEW RETURNS ZERO ROWS TODAY.
-- That is correct — a soft-circled commitment with no post-close percentage is
-- not a holding — but it means an empty result is the expected state until an
-- SPV closes, and must not be read as the view being broken.
--
-- ===========================================================================
-- THE VALUE, AND WHY IT IS NULL RATHER THAN ZERO
-- ===========================================================================
-- The ladder below is A2's resolve_current_value transcribed into SQL: latest
-- valuation_date; within a date audited(0) > final(1) > preliminary(2) >
-- estimated(3) > restated(4); any row a CURRENT valuation supersedes demoted to
-- 9 regardless of its own status; ties broken on system_from.
--
-- market_value is NULL — never 0 — when no mark qualifies, and value_reason
-- says which of the three cases applies. A zero for "we have no mark" is
-- indistinguishable from a genuine zero position the moment it is summed, and
-- by then the fact that it was never measured is gone. Same rule as
-- AssetValue, same reason.
--
-- per_unit is a real case, not a technicality: an SPV NAV per unit is a valid
-- valuation and is not a market value on its own. A percent-basis position has
-- no quantity to multiply it by, so it resolves to NULL with that reason.
--
-- The arithmetic is `value * ownership_pct / 100`, multiplying BEFORE dividing.
-- (value * (pct/100)) would round the quotient first and lose cents on any
-- percentage that is not a terminating decimal — 33.333333% of a real NAV is
-- the normal case, not the exotic one.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS portfolio.spv_derived_positions;

CREATE VIEW portfolio.spv_derived_positions
    WITH (security_invoker = true)
AS
WITH spv_asset AS (
    -- The tenant asset standing for the SPV. One per SPV — index 1 enforces it;
    -- DISTINCT ON is belt-and-braces so a pre-index duplicate degrades to
    -- "picks the newest" rather than to double-counting.
    SELECT DISTINCT ON (a.org_id, a.internal_spv_id)
           a.id                    AS asset_id,
           a.org_id,
           a.internal_spv_id,
           a.default_taxonomy_key,
           a.currency_code         AS asset_currency_code,
           a.valuation_method
    FROM portfolio.assets a
    WHERE a.internal_spv_id IS NOT NULL
      AND a.is_active
      AND a.valid_to  IS NULL
      AND a.system_to IS NULL
    ORDER BY a.org_id, a.internal_spv_id, a.valid_from DESC, a.id
),
val_flagged AS (
    -- Every current market valuation, with the supersession flag computed once.
    SELECT v.org_id,
           v.asset_id,
           v.id            AS valuation_id,
           v.valuation_date,
           v.value,
           v.value_basis,
           v.status,
           v.currency_code AS valuation_currency_code,
           v.system_from,
           EXISTS (
               SELECT 1
               FROM portfolio.valuations sup
               WHERE sup.supersedes_valuation_id = v.id
                 AND sup.org_id    = v.org_id
                 AND sup.valid_to  IS NULL
                 AND sup.system_to IS NULL
           ) AS is_superseded
    FROM portfolio.valuations v
    WHERE v.purpose   = 'market'
      AND v.valid_to  IS NULL
      AND v.system_to IS NULL
),
val_ranked AS (
    SELECT vf.*,
           CASE
               WHEN vf.is_superseded THEN 9      -- _SUPERSEDED_PRIORITY
               WHEN vf.status = 'audited'     THEN 0
               WHEN vf.status = 'final'       THEN 1
               WHEN vf.status = 'preliminary' THEN 2
               WHEN vf.status = 'estimated'   THEN 3
               WHEN vf.status = 'restated'    THEN 4
               ELSE 9   -- an unknown status ranks with superseded, matching
                        -- _STATUS_PRIORITY.get(status, _SUPERSEDED_PRIORITY)
           END AS rank_priority
    FROM val_flagged vf
),
governing_val AS (
    SELECT DISTINCT ON (vr.org_id, vr.asset_id) vr.*
    FROM val_ranked vr
    ORDER BY vr.org_id,
             vr.asset_id,
             vr.valuation_date DESC,
             vr.rank_priority  ASC,
             vr.system_from    DESC
)
SELECT
    -- Deterministic and derived, so the SAME subscription yields the SAME id on
    -- every read. NOT a portfolio.positions.id and never will be: it is a v5
    -- UUID under a namespace minted for this view, so an id that reaches a
    -- write function cannot collide with a stored position and is refused by
    -- that function's existence check rather than silently updating something.
    uuid_generate_v5('0c0df483-d70c-5244-a0fb-3175651a48a9'::uuid, s.id::text)
                                              AS id,
    s.org_id,
    s.entity_id                               AS owner_entity_id,
    sa.asset_id,
    -- The position is "as of" the date its value was struck. With no mark there
    -- is nothing to date it to but the subscription itself, and as_of_date is
    -- NOT NULL in the position shape this projects into.
    COALESCE(gv.valuation_date, s.valid_from::date)
                                              AS as_of_date,
    'percent'::text                           AS ownership_basis,
    NULL::numeric                             AS quantity,        -- percent forbids it
    s.ownership_pct,
    s.funded_amount                           AS cost_basis,      -- what was actually paid in
    CASE
        WHEN gv.valuation_id IS NULL          THEN NULL
        WHEN gv.value_basis  = 'per_unit'     THEN NULL
        ELSE gv.value * s.ownership_pct / 100
    END::numeric                              AS market_value,
    NULL::numeric                             AS market_value_native,
    NULL::uuid                                AS fx_rate_id,
    NULL::numeric                             AS accrued_income,
    'internal'::text                          AS authority,
    'spv_subscriptions'::text                 AS source_system,
    sa.default_taxonomy_key                   AS taxonomy_key,
    false                                     AS is_reconciled,
    NULL::timestamptz                         AS reconciled_at,
    NULL::text                                AS superseded_by_source,
    s.valid_from,
    NULL::timestamptz                         AS valid_to,        -- current rows only
    s.created_at                              AS system_from,
    NULL::timestamptz                         AS system_to,

    -- ── Provenance. Not part of the position shape; present so a drill-through
    --    can get from a derived position back to the book of record in one hop,
    --    which is the whole point of not duplicating the row.
    s.id                                      AS subscription_id,
    s.spv_id,
    s.subscription_status,
    s.commitment_amount,
    s.funded_amount,
    gv.valuation_id,
    gv.valuation_date,
    gv.status                                 AS valuation_status,
    gv.value                                  AS spv_total_value,
    gv.value_basis,
    COALESCE(gv.valuation_currency_code, sa.asset_currency_code)
                                              AS currency_code,
    COALESCE(gv.is_superseded, false)         AS is_superseded,
    CASE
        WHEN gv.valuation_id IS NULL      THEN 'no_current_market_valuation'
        WHEN gv.value_basis = 'per_unit'  THEN 'per_unit_valuation_requires_quantity'
        ELSE NULL
    END::text                                 AS value_reason
FROM public.spv_subscriptions s
-- INNER join: asset_id is NOT NULL in the shape this projects into, so a
-- subscription whose SPV has no asset cannot be a position. It is not lost
-- silently — portfolio_spv.unprojected_subscriptions() reports exactly this
-- case, with its reason, and ensure_spv_asset() fixes it.
JOIN spv_asset sa
      ON sa.org_id = s.org_id
     AND sa.internal_spv_id = s.spv_id
LEFT JOIN governing_val gv
      ON gv.org_id = sa.org_id
     AND gv.asset_id = sa.asset_id
WHERE s.valid_to IS NULL
  AND s.subscription_status IN ('committed', 'funded')
  AND s.ownership_pct IS NOT NULL;


COMMENT ON VIEW portfolio.spv_derived_positions IS
    'Phase D. CURRENT spv_subscriptions projected into position shape '
    '(authority=internal, source_system=spv_subscriptions, basis=percent). '
    'Derived, never stored. security_invoker=true so RLS applies to the '
    'querying role. NOT writable by design — corrections go to '
    'spv_subscriptions, which remains the book of record.';


-- Read-only, explicitly. See point (b) in the header block.
GRANT SELECT ON portfolio.spv_derived_positions TO app_service;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON portfolio.spv_derived_positions FROM app_service;
