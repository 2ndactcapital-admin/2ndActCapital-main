"""Marketplace assistant actions (Sprint 11)."""
import json

from services.action_registry import AssistantAction, REGISTRY

ORG_ID = "00000000-0000-0000-0000-000000000001"


async def _show_new_deals(pool, user_id: str, org_id: str, **_):
    """Return recent active deals for the member."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, deal_status, asset_super_class, asset_class, created_at
            FROM deals
            WHERE org_id = $1
              AND deal_status = ANY($2)
              AND valid_to IS NULL
            ORDER BY created_at DESC
            LIMIT 5
            """,
            org_id,
            list(("active", "under_review")),
        )
    deals = [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "deal_status": r["deal_status"],
            "asset_super_class": r["asset_super_class"],
            "asset_class": r["asset_class"],
        }
        for r in rows
    ]
    count = len(deals)
    render_target = "inline" if count <= 3 else "screen"
    return {
        "data": {"deals": deals, "total": count},
        "render": {
            "component": "DealList",
            "target": render_target,
            "props": {"deals": deals},
            "screen_route": "/marketplace" if render_target == "screen" else None,
        },
        "text": f"Found {count} recent deal{'s' if count != 1 else ''} in the marketplace.",
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="marketplace.show_new_deals",
            module="marketplace",
            description="Browse recent and interest-matched deals in the marketplace.",
            access_type="read",
            required_permission=None,
            default_autonomy="auto",
            reversible=False,
            render_target="auto",
            handler=_show_new_deals,
            params_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
    )
