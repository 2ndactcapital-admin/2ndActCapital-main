"""Portfolio assistant actions (Sprint 11)."""
from services.action_registry import AssistantAction, REGISTRY


async def _find_investment(pool, user_id: str, org_id: str, query: str = "", **_):
    """Find a member's investment matching the query string."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT mi.id, mi.status, mi.current_stage, mi.committed_amount,
                   mi.currency, d.name AS deal_name, d.deal_type, d.taxonomy_key
            FROM member_investments mi
            JOIN deals d ON d.id = mi.deal_id
            JOIN entities e ON e.id = mi.entity_id
            WHERE e.org_id = $1
              AND (
                LOWER(d.name) LIKE '%' || LOWER($2) || '%'
                OR LOWER(mi.status) LIKE '%' || LOWER($2) || '%'
                OR LOWER(mi.current_stage) LIKE '%' || LOWER($2) || '%'
              )
            ORDER BY mi.created_at DESC
            LIMIT 5
            """,
            org_id,
            query,
        )
    investments = [
        {
            "id": str(r["id"]),
            "deal_name": r["deal_name"],
            "deal_type": r["deal_type"],
            "status": r["status"],
            "current_stage": r["current_stage"],
            "committed_amount": float(r["committed_amount"]) if r["committed_amount"] else None,
            "currency": r["currency"],
        }
        for r in rows
    ]
    count = len(investments)
    return {
        "data": {"investments": investments},
        "render": {
            "component": "InvestmentCard",
            "target": "inline",
            "props": {"investments": investments, "query": query},
        },
        "text": (
            f"Found {count} investment{'s' if count != 1 else ''} matching '{query}'."
            if query else f"Found {count} investment{'s' if count != 1 else ''}."
        ),
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="portfolio.find_my_investment",
            module="portfolio",
            description="Find a member's investment by deal name, status, or stage.",
            access_type="read",
            required_permission=None,
            default_autonomy="auto",
            reversible=False,
            render_target="inline",
            handler=_find_investment,
            params_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term — deal name, status, or stage keyword.",
                    }
                },
                "required": ["query"],
            },
        )
    )
