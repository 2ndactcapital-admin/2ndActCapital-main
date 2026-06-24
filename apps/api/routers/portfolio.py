"""Portfolio endpoints: member's personal investment view."""

from fastapi import APIRouter, Request

from routers.entities import get_org_id
from schemas.marketplace import InvestmentSummaryItem, PortfolioInvestmentResponse
from services.database import get_pool
from services.permissions import get_user_id

router = APIRouter(tags=["portfolio"])

_PORTFOLIO_SELECT = (
    "mi.id, mi.deal_id, d.name AS deal_name, d.deal_status, "
    "mi.user_id, mi.org_id, mi.investment_stage AS stage, mi.notes, mi.amount_committed AS invested_amount, "
    "mi.created_at, mi.updated_at"
)


def _f(value):
    return float(value) if value is not None else None


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
