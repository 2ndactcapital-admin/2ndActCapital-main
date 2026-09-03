"""ta_params.py — bi-temporal persistence for TA model parameter overrides,
calibration-result logging, and realized-period derivation from real
transactions.

TA MODEL SPRINT 1 — TASK 2 (schema decision).

``portfolio.ta_model_params`` holds ADMIN/CALIBRATION-OVERRIDDEN parameters
per commitment, ONE ACTIVE ROW AT A TIME — a partial unique index on
``(org_id, commitment_id) WHERE valid_to IS NULL AND system_to IS NULL``,
the exact shape CLAUDE.md documents for ``member_target_allocations``
(Sprint 8). Restating a commitment's parameters follows Rule 3: close the
current row, then insert the new one, in ONE transaction — never UPDATE a
parameter value in place. "What did we assume in Q2 and why did the
projection change" is a real, anticipated question (the brief's own
rationale for choosing a separate table over new columns on
``portfolio.commitments``), and an in-place update would destroy the answer.

``portfolio.ta_calibration_results`` is a separate, APPEND-ONLY log of every
calibration RUN (not the params themselves) — realized_periods_used and
periods_per_year travel with each row so a later reader can see exactly what
evidentiary basis a given calibration had, including ones that predate a
change to the frequency-aware floor.

PROJECTED CASH FLOWS ARE NEVER WRITTEN BY ANYTHING IN THIS FILE. Only
parameters (``ta_model_params``) and calibration results
(``ta_calibration_results``) persist — see ``ta_model.py``'s module
docstring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from services.portfolio_assets import PortfolioError, _OrgWrite, _require_org
from services.ta_calibrate import RealizedPeriod
from services.ta_model import TAParams

TABLE_PARAMS = "portfolio.ta_model_params"
TABLE_CALIBRATIONS = "portfolio.ta_calibration_results"
TABLE_COMMITMENTS = "portfolio.commitments"
TABLE_TRANSACTIONS = "portfolio.transactions"
TABLE_VALUATIONS = "portfolio.valuations"
TABLE_TXN_TYPES = "public.transaction_types"

#: Supported realized-period frequencies for derivation from transactions.
#: Matches ta_config.DEFAULT_PERIODS_PER_YEAR's own vocabulary (annual /
#: quarterly) — the two frequencies Task 5's proof exercises.
_TRUNC_BY_PERIODS_PER_YEAR = {1: "year", 4: "quarter"}

_AMOUNT = "COALESCE(t.gross_amount, t.net_amount)"
_DISTRIBUTION_CATEGORY = "distribution"


class TAParamsError(PortfolioError):
    """A TA-params read/write was refused for a reason the caller can fix."""


def _current(alias: str) -> str:
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


def _row_to_params(row) -> TAParams:
    return TAParams(
        rate_of_contribution=row["rate_of_contribution"],
        rate_of_distribution=row["rate_of_distribution"],
        growth_rate=row["growth_rate"],
        bow_factor=row["bow_factor"],
        fund_life_years=row["fund_life_years"],
        periods_per_year=row["periods_per_year"],
    )


# ── Reads ─────────────────────────────────────────────────────────────────


async def get_active_params(conn, *, org_id: str, commitment_id: str) -> dict | None:
    """The current override row for a commitment, or ``None`` if it has never
    been overridden (in which case the caller falls back to
    ``ta_config.params_for_strategy`` with a strategy_key it supplies).
    """
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"""
        SELECT id::text AS id, ta_strategy_key, rate_of_contribution,
               rate_of_distribution, growth_rate, bow_factor, fund_life_years,
               periods_per_year, source, valid_from
        FROM {TABLE_PARAMS} t
        WHERE t.org_id = $1::uuid AND t.commitment_id = $2::uuid AND {_current('t')}
        """,
        org_id, str(commitment_id),
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "ta_strategy_key": row["ta_strategy_key"],
        "source": row["source"],
        "valid_from": row["valid_from"],
        "params": _row_to_params(row),
    }


# ── Writes (Rule 3: close old row, insert new) ──────────────────────────────


async def set_override_params(
    conn,
    *,
    org_id: str,
    commitment_id: str,
    ta_strategy_key: str,
    params: TAParams,
    created_by: str | None,
    source: str = "override",
) -> str:
    """Close any current override row, insert the new one. Returns the new
    row's id. Both steps run in ONE transaction via ``_OrgWrite`` — a crash
    between them must never leave a commitment with zero active rows and no
    way to tell whether that is "never overridden" or "mid-write".
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        commitment = await c.fetchrow(
            f"SELECT id FROM {TABLE_COMMITMENTS} c "
            f"WHERE c.id = $1::uuid AND c.org_id = $2::uuid AND {_current('c')}",
            str(commitment_id), org_id,
        )
        if commitment is None:
            raise TAParamsError(
                f"commitment {commitment_id} is not a current commitment in org {org_id}"
            )

        await c.execute(
            f"""
            UPDATE {TABLE_PARAMS} t
            SET valid_to = now()
            WHERE t.org_id = $1::uuid AND t.commitment_id = $2::uuid AND {_current('t')}
            """,
            org_id, str(commitment_id),
        )

        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_PARAMS}
                (org_id, commitment_id, ta_strategy_key, rate_of_contribution,
                 rate_of_distribution, growth_rate, bow_factor, fund_life_years,
                 periods_per_year, source, created_by)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11::uuid)
            RETURNING id::text
            """,
            org_id, str(commitment_id), ta_strategy_key,
            params.rate_of_contribution, params.rate_of_distribution,
            params.growth_rate, params.bow_factor, params.fund_life_years,
            params.periods_per_year, source, created_by,
        )
    return row["id"]


async def record_calibration_result(
    conn,
    *,
    org_id: str,
    commitment_id: str,
    ta_strategy_key: str,
    calibrated_params: TAParams,
    realized_periods_used: int,
    created_by: str | None,
) -> str:
    """Append one immutable calibration-run record. Never updated, never
    superseded — a run either happened with a given evidentiary basis or it
    did not, and that fact does not change later.
    """
    org_id = _require_org(org_id)
    payload = calibrated_params.to_json()
    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_CALIBRATIONS}
                (org_id, commitment_id, ta_strategy_key, calibrated_params,
                 realized_periods_used, periods_per_year, created_by)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6, $7::uuid)
            RETURNING id::text
            """,
            org_id, str(commitment_id), ta_strategy_key, json.dumps(payload),
            int(realized_periods_used), calibrated_params.periods_per_year, created_by,
        )
    return row["id"]


# ── Realized-period derivation from real transactions ───────────────────────


async def realized_periods_from_transactions(
    conn, *, org_id: str, commitment_id: str, periods_per_year: int,
) -> list[RealizedPeriod]:
    """Bucket a commitment's REAL transaction history into calendar periods
    for calibration input.

    ``committed = called + distributed`` figures come from the same
    ``portfolio.transactions`` JOIN ``public.transaction_types`` shape
    ``services.portfolio_commitments.derive_commitment_totals`` uses (gross
    amount falling back to net, ``affects_paid_in`` for calls,
    ``category = 'distribution'`` for distributions) — reused here rather
    than re-derived so the two modules can never silently disagree about what
    counts as a call.

    NAV per period is approximated from the position's real
    ``portfolio.valuations`` where one exists at or before the period end;
    where no valuation exists yet for that window this Sprint-1 substrate
    falls back to a running ``cumulative_paid_in - cumulative_distributed``
    estimate (floored at 0) rather than leaving the period unusable. This is
    a documented simplification, not a claim of mark-to-market precision —
    see ``docs/TA_MODEL_INTEGRATION_BRIEF.md`` §8 residual items.

    Supports ``periods_per_year`` of 1 (annual) or 4 (quarterly) only, the two
    frequencies the sprint's calibration-floor proof exercises.
    """
    org_id = _require_org(org_id)
    trunc = _TRUNC_BY_PERIODS_PER_YEAR.get(periods_per_year)
    if trunc is None:
        raise TAParamsError(
            f"realized-period derivation supports periods_per_year in "
            f"{sorted(_TRUNC_BY_PERIODS_PER_YEAR)}, got {periods_per_year}"
        )

    commitment = await conn.fetchrow(
        f"""
        SELECT c.position_id::text AS position_id, p.asset_id::text AS asset_id
        FROM {TABLE_COMMITMENTS} c
        JOIN portfolio.positions p ON p.id = c.position_id AND p.org_id = c.org_id
         AND {_current('p')}
        WHERE c.id = $1::uuid AND c.org_id = $2::uuid AND {_current('c')}
        """,
        str(commitment_id), org_id,
    )
    if commitment is None:
        raise TAParamsError(f"commitment {commitment_id} is not current in org {org_id}")
    position_id = commitment["position_id"]
    asset_id = commitment["asset_id"]

    flows = await conn.fetch(
        f"""
        SELECT date_trunc('{trunc}', t.trade_date) AS period_start,
               COALESCE(SUM({_AMOUNT} * tt.affects_paid_in) FILTER (
                   WHERE tt.affects_paid_in > 0), 0)         AS contribution,
               COALESCE(SUM({_AMOUNT}) FILTER (
                   WHERE tt.category = $3), 0)                AS distribution
        FROM {TABLE_TRANSACTIONS} t
        JOIN {TABLE_TXN_TYPES} tt ON tt.code = t.transaction_type_code
        WHERE t.position_id = $1::uuid AND t.org_id = $2::uuid AND {_current('t')}
        GROUP BY period_start
        ORDER BY period_start
        """,
        position_id, org_id, _DISTRIBUTION_CATEGORY,
    )
    if not flows:
        return []

    valuations = await conn.fetch(
        f"""
        SELECT date_trunc('{trunc}', v.valuation_date) AS period_start,
               v.value
        FROM {TABLE_VALUATIONS} v
        WHERE v.asset_id = $1::uuid AND v.org_id = $2::uuid
          AND v.valid_to IS NULL AND v.system_to IS NULL
        ORDER BY v.valuation_date
        """,
        asset_id, org_id,
    )
    nav_by_period = {row["period_start"]: row["value"] for row in valuations}

    periods: list[RealizedPeriod] = []
    cumulative_paid_in = Decimal(0)
    cumulative_distributed = Decimal(0)
    for idx, row in enumerate(flows, start=1):
        contribution = Decimal(row["contribution"] or 0)
        distribution = Decimal(row["distribution"] or 0)
        cumulative_paid_in += contribution
        cumulative_distributed += distribution
        nav = nav_by_period.get(row["period_start"])
        if nav is None:
            nav = cumulative_paid_in - cumulative_distributed
            if nav < 0:
                nav = Decimal(0)
        periods.append(
            RealizedPeriod(
                period=idx,
                contribution=contribution,
                distribution=distribution,
                nav=Decimal(nav),
            )
        )
    return periods
