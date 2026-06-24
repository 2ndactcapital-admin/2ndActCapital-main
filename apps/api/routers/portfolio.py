"""Portfolio endpoints: member investments + entity target allocations (Sprint 8)."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from routers.entities import get_org_id
from schemas.marketplace import InvestmentSummaryItem, PortfolioInvestmentResponse
from schemas.portfolio import (
    AllocationBreakdownItem,
    TargetAllocationResponse,
    TargetAllocationWrite,
)
from services.audit import write_audit_log
from services.database import get_pool
from services.permissions import get_user_id, require_staff
from services.taxonomy import get_taxonomy_index

router = APIRouter(tags=["portfolio"])

_PORTFOLIO_SELECT = (
    "mi.id, mi.deal_id, d.name AS deal_name, d.deal_status, "
    "mi.user_id, mi.org_id, mi.investment_stage AS stage, mi.notes, mi.amount_committed AS invested_amount, "
    "mi.created_at, mi.updated_at"
)

_TARGET_SELECT = (
    "id, entity_id, taxonomy_key, target_pct, effective_date, end_date, notes, created_at"
)


def _f(value):
    return float(value) if value is not None else None


async def _fetch_targets_with_inheritance(conn, org_id: str, entity_id: UUID) -> tuple[list, list]:
    """Return (direct_rows, inherited_rows) for an entity, walking ownership up 5 levels."""
    direct = await conn.fetch(
        f"SELECT {_TARGET_SELECT} FROM member_target_allocations "
        "WHERE entity_id = $1 AND org_id = $2 AND end_date IS NULL "
        "ORDER BY taxonomy_key",
        entity_id, org_id,
    )
    covered_keys = {r["taxonomy_key"] for r in direct}
    inherited_rows: list[dict] = []

    current_id = entity_id
    for _ in range(5):
        parent = await conn.fetchrow(
            """
            SELECT eo.parent_id, e.display_name AS parent_name
            FROM entity_ownership eo
            JOIN entities e ON e.id = eo.parent_id
            WHERE eo.child_id = $1 AND eo.org_id = $2
              AND eo.valid_to IS NULL AND eo.system_to IS NULL
            ORDER BY eo.ownership_pct DESC NULLS LAST
            LIMIT 1
            """,
            current_id, org_id,
        )
        if parent is None:
            break
        parent_id = parent["parent_id"]
        parent_name = parent["parent_name"]

        ancestor_targets = await conn.fetch(
            f"SELECT {_TARGET_SELECT} FROM member_target_allocations "
            "WHERE entity_id = $1 AND org_id = $2 AND end_date IS NULL "
            "ORDER BY taxonomy_key",
            parent_id, org_id,
        )
        for row in ancestor_targets:
            if row["taxonomy_key"] not in covered_keys:
                covered_keys.add(row["taxonomy_key"])
                inherited_rows.append({
                    **dict(row),
                    "inherited_from_entity_id": parent_id,
                    "inherited_from_entity_name": parent_name,
                })
        current_id = parent_id

    return list(direct), inherited_rows


def _build_target_responses(
    direct: list,
    inherited: list,
    tax_index: dict,
) -> list[TargetAllocationResponse]:
    results = []
    for r in direct:
        entry = tax_index.get(r["taxonomy_key"], {})
        results.append(TargetAllocationResponse(
            id=r["id"],
            entity_id=r["entity_id"],
            taxonomy_key=r["taxonomy_key"],
            taxonomy_level=entry.get("type"),
            target_pct=float(r["target_pct"]),
            effective_date=r["effective_date"],
            end_date=r["end_date"],
            notes=r["notes"],
            created_at=r["created_at"],
            taxonomy_label=entry.get("label"),
            inherited=False,
        ))
    for r in inherited:
        entry = tax_index.get(r["taxonomy_key"], {})
        results.append(TargetAllocationResponse(
            id=r["id"],
            entity_id=r["entity_id"],
            taxonomy_key=r["taxonomy_key"],
            taxonomy_level=entry.get("type"),
            target_pct=float(r["target_pct"]),
            effective_date=r["effective_date"],
            end_date=r["end_date"],
            notes=r["notes"],
            created_at=r["created_at"],
            taxonomy_label=entry.get("label"),
            inherited=True,
            inherited_from_entity_id=r["inherited_from_entity_id"],
            inherited_from_entity_name=r["inherited_from_entity_name"],
        ))
    return results


# ---------------------------------------------------------------------------
# Existing portfolio endpoints
# ---------------------------------------------------------------------------

@router.get("/portfolio/my-investments", response_model=list[PortfolioInvestmentResponse])
async def get_my_investments(request: Request):
    user_id = get_user_id(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_PORTFOLIO_SELECT}
            FROM member_investments mi
            LEFT JOIN deals d
                ON d.id = mi.deal_id
                AND d.valid_to IS NULL AND d.system_to IS NULL
            WHERE mi.user_id = $1 AND mi.org_id = $2
            ORDER BY mi.updated_at DESC NULLS LAST
            """,
            user_id,
            org_id,
        )
    return [
        PortfolioInvestmentResponse(**{**dict(r), "invested_amount": _f(r["invested_amount"])})
        for r in rows
    ]


@router.get("/portfolio/summary", response_model=list[InvestmentSummaryItem])
async def get_portfolio_summary(request: Request):
    user_id = get_user_id(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT investment_stage AS stage, COUNT(*) AS count, SUM(amount_committed) AS total_amount
            FROM member_investments
            WHERE user_id = $1 AND org_id = $2
            GROUP BY investment_stage
            ORDER BY investment_stage
            """,
            user_id,
            org_id,
        )
    return [
        InvestmentSummaryItem(
            stage=r["stage"] or "unknown",
            count=r["count"],
            total_amount=_f(r["total_amount"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Sprint 8: Target allocation endpoints (entity-centric)
# ---------------------------------------------------------------------------

@router.get("/portfolio/targets", response_model=list[TargetAllocationResponse])
async def get_entity_targets(request: Request, entity_id: UUID = Query(...)):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        direct, inherited = await _fetch_targets_with_inheritance(conn, org_id, entity_id)

    all_keys = [r["taxonomy_key"] for r in direct] + [r["taxonomy_key"] for r in inherited]
    tax_index = await get_taxonomy_index(org_id) if all_keys else {}

    return _build_target_responses(direct, inherited, tax_index)


@router.put("/portfolio/targets", response_model=list[TargetAllocationResponse])
async def set_entity_targets(
    request: Request,
    entity_id: UUID = Query(...),
    body: TargetAllocationWrite = None,
):
    require_staff(request)
    org_id = get_org_id(request)
    actor_id = get_user_id(request)
    today = date.today()
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in body.items:
                await conn.execute(
                    """
                    UPDATE member_target_allocations
                       SET end_date = $1
                     WHERE entity_id = $2 AND taxonomy_key = $3
                       AND org_id = $4 AND end_date IS NULL
                    """,
                    today, entity_id, item.taxonomy_key, org_id,
                )
                await conn.execute(
                    """
                    INSERT INTO member_target_allocations
                        (org_id, entity_id, user_id, taxonomy_key, target_pct,
                         effective_date, notes, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    org_id, entity_id, actor_id, item.taxonomy_key,
                    item.target_pct, today, item.notes, actor_id,
                )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="update",
                table_name="member_target_allocations",
                record_id=entity_id,
                new={"entity_id": str(entity_id), "items": [i.model_dump() for i in body.items]},
                actor=actor_id,
            )

    return await get_entity_targets(request, entity_id=entity_id)


@router.delete("/portfolio/targets", status_code=204)
async def clear_entity_target(
    request: Request,
    entity_id: UUID = Query(...),
    taxonomy_key: str = Query(...),
):
    """Close the active target row for one taxonomy key (falls back to inherited)."""
    require_staff(request)
    org_id = get_org_id(request)
    actor_id = get_user_id(request)
    today = date.today()
    pool = await get_pool()

    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE member_target_allocations
               SET end_date = $1
             WHERE entity_id = $2 AND taxonomy_key = $3
               AND org_id = $4 AND end_date IS NULL
            """,
            today, entity_id, taxonomy_key, org_id,
        )
        if result == "UPDATE 0":
            raise HTTPException(status_code=404, detail="No active target found for this key")
        await write_audit_log(
            conn,
            org_id=org_id,
            action="delete",
            table_name="member_target_allocations",
            record_id=entity_id,
            new={"taxonomy_key": taxonomy_key, "action": "clear_override"},
            actor=actor_id,
        )


# ---------------------------------------------------------------------------
# Sprint 8: Allocation breakdown (actual vs target)
# ---------------------------------------------------------------------------

@router.get("/portfolio/allocations", response_model=list[AllocationBreakdownItem])
async def get_entity_allocations(
    request: Request,
    entity_id: UUID = Query(None),
):
    """Return actual portfolio allocation breakdown vs targets.

    Actual: derived from member_investments for the requesting user.
    Targets: from member_target_allocations for entity_id (with roll-down),
             or empty if entity_id not provided.
    """
    org_id = get_org_id(request)
    user_id_jwt = get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Targets (entity-centric with roll-down)
        targets_map: dict[str, float] = {}
        if entity_id is not None:
            direct, inherited = await _fetch_targets_with_inheritance(conn, org_id, entity_id)
            for r in direct:
                targets_map[r["taxonomy_key"]] = float(r["target_pct"])
            for r in inherited:
                if r["taxonomy_key"] not in targets_map:
                    targets_map[r["taxonomy_key"]] = float(r["target_pct"])

        # Actual investments for the requesting user
        inv_rows = await conn.fetch(
            """
            SELECT d.asset_super_class, d.asset_class, d.asset_sub_category,
                   COALESCE(mi.amount_committed, 0) AS amount
            FROM member_investments mi
            JOIN deals d ON d.id = mi.deal_id
              AND d.valid_to IS NULL AND d.system_to IS NULL
            WHERE mi.user_id = $1 AND mi.org_id = $2
            """,
            user_id_jwt, org_id,
        )

        # Tally by most specific taxonomy key per investment
        actual_map: dict[str, float] = {}
        deal_count_map: dict[str, int] = {}
        for inv in inv_rows:
            for key in [inv["asset_sub_category"], inv["asset_class"], inv["asset_super_class"]]:
                if key:
                    actual_map[key] = actual_map.get(key, 0.0) + float(inv["amount"] or 0)
                    deal_count_map[key] = deal_count_map.get(key, 0) + 1
                    break

        total_invested = sum(actual_map.values()) or 1.0  # guard div-by-zero

        all_keys = set(targets_map.keys()) | set(actual_map.keys())
        tax_index = await get_taxonomy_index(org_id) if all_keys else {}

    results = []
    for key in sorted(all_keys):
        entry = tax_index.get(key, {})
        invested = actual_map.get(key, 0.0)
        actual_pct = round(invested / total_invested * 100, 1) if invested else 0.0
        target_pct = targets_map.get(key)
        gap_pct = round(actual_pct - target_pct, 1) if target_pct is not None else None
        results.append(AllocationBreakdownItem(
            taxonomy_key=key,
            taxonomy_label=entry.get("label"),
            taxonomy_level=entry.get("type"),
            total_invested=invested,
            deal_count=deal_count_map.get(key, 0),
            actual_pct=actual_pct,
            target_pct=target_pct,
            gap_pct=gap_pct,
        ))
    return results
