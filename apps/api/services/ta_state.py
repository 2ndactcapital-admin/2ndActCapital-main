"""TA model — one commitment's known-to-date state + its active parameters (CASH01: moved out of routers/modeling_ta).

``resolve_commitment_state`` used to live inside the router as ``_resolve_commitment_state`` and raised
``HTTPException``. CASH01's obligations read (services/cash_obligations) projects capital calls from the SAME state,
so the function moved here unchanged in behaviour and raises :class:`TAStateError` (status + detail); the router maps
it back to the identical HTTPException, so GET /projection, GET /obligations and the cash ledger can never disagree on
what "this commitment's own state" means. Nothing here writes — projections are never persisted.
"""

from __future__ import annotations

from decimal import Decimal

from services.org_settings import get_all_settings
from services.portfolio_commitments import CommitmentError, get_commitment
from services.ta_config import TA_STRATEGY_KEYS, TAConfigError, params_for_strategy
from services.ta_model import TAModelError
from services.ta_params import get_active_params


class TAStateError(Exception):
    """The commitment's state cannot be resolved; ``status`` is the HTTP status the routes return."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def resolve_commitment_state(conn, *, org_id: str, commitment_id, strategy_key: str | None,
                                   periods_per_year: int | None, entity_scope: list[str] | None = None,
                                   settings: dict | None = None) -> dict:
    try:
        commitment = await get_commitment(conn, org_id=org_id, commitment_id=str(commitment_id))
    except CommitmentError as exc:
        raise TAStateError(400, str(exc)) from exc
    if commitment is None:
        raise TAStateError(404, "commitment not found")
    if entity_scope is not None:
        # BUGFIX3 — a member projects only their own family's commitment; another family's = the SAME 404
        owner = await conn.fetchval(
            "SELECT owner_entity_id::text FROM portfolio.positions WHERE id = $1::uuid AND org_id = $2::uuid",
            str(commitment["position_id"]), str(org_id))
        if owner is None or owner not in set(entity_scope):
            raise TAStateError(404, "commitment not found")

    if settings is None:
        settings = await get_all_settings(conn, org_id)  # fetched ONCE

    override = await get_active_params(conn, org_id=org_id, commitment_id=str(commitment_id))
    if override is not None:
        params = override["params"]
        ta_strategy_key = override["ta_strategy_key"]
        source = override["source"]
    else:
        if not strategy_key:
            raise TAStateError(
                422,
                "this commitment has no TA parameter override set — pass "
                f"?strategy_key=<one of {TA_STRATEGY_KEYS}> to project against a strategy default")
        try:
            params = params_for_strategy(strategy_key, settings, periods_per_year=periods_per_year)
        except (TAConfigError, TAModelError) as exc:
            raise TAStateError(422, str(exc)) from exc
        ta_strategy_key = strategy_key
        source = None

    position = await conn.fetchrow(
        """
        SELECT p.market_value, p.ownership_basis, p.ownership_pct, p.quantity, p.asset_id::text AS asset_id
        FROM portfolio.positions p
        JOIN portfolio.commitments c ON c.position_id = p.id AND c.org_id = p.org_id
        WHERE c.id = $1::uuid AND c.org_id = $2::uuid
          AND p.valid_to IS NULL AND p.system_to IS NULL
          AND c.valid_to IS NULL AND c.system_to IS NULL
        """,
        str(commitment_id), org_id,
    )

    committed = commitment["commitment_amount"] or Decimal(0)
    called = commitment["called_to_date"] or Decimal(0)
    distributed = commitment["distributed_to_date"] or Decimal(0)
    # PORTENTRY1 (GAP-PORT-04) — a private investment entered on the CRM record carries its NAV as a VALUATION on
    # a percent position (market_value NULL): read it through the same position_current_value the rollup and the
    # grid use. A stored market_value is returned first by that function, so existing commitments are unchanged.
    resolved = None
    if position is not None:
        from services.portfolio_rollup import position_current_value

        resolved, _ = await position_current_value(conn, org_id, dict(position), None)
    if resolved is not None:
        current_nav = Decimal(resolved)
    else:
        current_nav = called - distributed
        if current_nav < 0:
            current_nav = Decimal(0)

    return {
        "commitment_id": str(commitment_id),
        "ta_strategy_key": ta_strategy_key,
        "source": source,
        "params": params,
        "settings": settings,
        "current_nav": current_nav,
        "committed_capital": committed,
        "called_to_date": called,
        "distributed_to_date": distributed,
    }
