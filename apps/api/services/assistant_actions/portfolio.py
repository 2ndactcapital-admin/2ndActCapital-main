"""Portfolio assistant actions (Sprint 11 + Sprint 21)."""
from services.action_registry import AssistantAction, REGISTRY
from services.allocation_lens import aggregate_allocation


async def _show_allocation(pool, user_id: str, org_id: str,
                           selector_type: str = "entity", entity_id: str = "", **_):
    """Return allocation lens data and a screen directive to the sunburst page."""
    selector: dict
    if selector_type == "entity" or selector_type == "subtree":
        if not entity_id:
            return {"text": "Please specify an entity to view its allocation.", "data": {}}
        selector = {"type": selector_type, "id": entity_id} if selector_type == "entity" \
            else {"type": "subtree", "root_id": entity_id}
    else:
        selector = {"type": "entity", "id": entity_id} if entity_id else {"type": "all"}

    try:
        result = await aggregate_allocation(pool, selector, org_id)
    except (ValueError, Exception) as exc:
        return {"text": f"Could not load allocation data: {exc}", "data": {}}

    total = result.get("total_actual_dollar", 0)
    count = result.get("entity_count", 0)
    fmtd = (
        f"${total / 1e9:.2f}B" if total >= 1e9
        else f"${total / 1e6:.2f}M" if total >= 1e6
        else f"${total / 1e3:.0f}K" if total >= 1e3
        else f"${total:.0f}"
    )
    return {
        "data": result,
        "render": {
            "component": "AllocationSunburst",
            "target": "screen",
            "screen_route": "/portfolio/allocation",
            "props": {"selector_type": selector_type, "entity_id": entity_id},
        },
        "text": f"Opening allocation lens — {fmtd} across {count} {'entity' if count == 1 else 'entities'}.",
    }


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
            key="portfolio.show_allocation",
            module="portfolio",
            description="Show the portfolio allocation lens — actual vs target breakdown across all taxonomy levels for a given entity or look-through scope.",
            access_type="read",
            required_permission=None,
            default_autonomy="auto",
            reversible=False,
            render_target="screen",
            handler=_show_allocation,
            params_schema={
                "type": "object",
                "properties": {
                    "selector_type": {
                        "type": "string",
                        "enum": ["entity", "subtree"],
                        "description": "entity = single entity; subtree = look-through weighted.",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "UUID of the entity to query.",
                    },
                },
                "required": ["selector_type", "entity_id"],
            },
        )
    )
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
