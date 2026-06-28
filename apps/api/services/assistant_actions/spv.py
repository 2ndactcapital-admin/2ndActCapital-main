"""SPV assistant actions (Sprint 12)."""
from services.action_registry import AssistantAction, REGISTRY


async def _list_open_spvs(pool, user_id: str, org_id: str, **_):
    """Return open/closing SPVs available for member co-investment."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id, s.name, s.status, s.target_raise, s.min_commitment,
                   s.close_date,
                   COALESCE(SUM(sub.commitment_amount), 0) AS total_committed
            FROM spvs s
            LEFT JOIN spv_subscriptions sub
              ON sub.spv_id = s.id AND sub.valid_to IS NULL
            WHERE s.org_id = $1
              AND s.status IN ('open', 'closing')
            GROUP BY s.id
            ORDER BY s.close_date ASC NULLS LAST, s.created_at DESC
            LIMIT 10
            """,
            org_id,
        )
    spvs = [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "status": r["status"],
            "target_raise": float(r["target_raise"]) if r["target_raise"] else None,
            "min_commitment": float(r["min_commitment"]) if r["min_commitment"] else None,
            "total_committed": float(r["total_committed"]) if r["total_committed"] else 0,
            "close_date": r["close_date"].isoformat() if r["close_date"] else None,
        }
        for r in rows
    ]
    count = len(spvs)
    return {
        "data": {"spvs": spvs, "total": count},
        "render": {
            "component": "SPVList",
            "target": "inline",
            "props": {"spvs": spvs},
        },
        "text": f"Found {count} open SPV{'s' if count != 1 else ''} available for co-investment.",
    }


async def _show_captable(pool, user_id: str, org_id: str, spv_id: str = "", **_):
    """Return the cap table for a specific SPV (staff only)."""
    if not spv_id:
        return {"data": {"error": "spv_id required"}, "render": None, "text": "Please provide an SPV ID."}

    async with pool.acquire() as conn:
        spv = await conn.fetchrow(
            "SELECT id, name, target_raise, status FROM spvs WHERE id = $1 AND org_id = $2",
            spv_id,
            org_id,
        )
        if not spv:
            return {"data": {"error": "SPV not found"}, "render": None, "text": "SPV not found."}

        rows = await conn.fetch(
            """
            SELECT s.entity_id,
                   COALESCE(e.display_name, e.legal_name, s.entity_id::text) AS entity_name,
                   s.commitment_amount, s.funded_amount, s.ownership_pct, s.status
            FROM spv_subscriptions s
            LEFT JOIN entities e ON e.id = s.entity_id AND e.valid_to IS NULL
            WHERE s.spv_id = $1 AND s.org_id = $2 AND s.valid_to IS NULL
            ORDER BY s.commitment_amount DESC NULLS LAST
            """,
            spv_id,
            org_id,
        )

    entries = [
        {
            "entity_name": r["entity_name"],
            "commitment_amount": float(r["commitment_amount"]) if r["commitment_amount"] else 0,
            "funded_amount": float(r["funded_amount"]) if r["funded_amount"] else None,
            "ownership_pct": float(r["ownership_pct"]) if r["ownership_pct"] else None,
            "status": r["status"],
        }
        for r in rows
    ]
    total_committed = sum(e["commitment_amount"] for e in entries)

    return {
        "data": {
            "spv_name": spv["name"],
            "total_committed": total_committed,
            "target_raise": float(spv["target_raise"]) if spv["target_raise"] else None,
            "subscriptions": entries,
        },
        "render": {
            "component": "CapTable",
            "target": "inline",
            "props": {
                "spv_name": spv["name"],
                "total_committed": total_committed,
                "target_raise": float(spv["target_raise"]) if spv["target_raise"] else None,
                "subscriptions": entries,
            },
        },
        "text": f"Cap table for {spv['name']}: {len(entries)} subscriber(s), ${total_committed:,.0f} committed.",
    }


async def _preview_subscribe(pool, user_id: str, org_id: str,
                              spv_id: str = "", entity_id: str = "",
                              commitment_amount: float = 0.0, **_):
    """Draft handler: validate SPV + entity, return subscription preview."""
    if not spv_id or not entity_id or not commitment_amount:
        return {"error": "spv_id, entity_id, and commitment_amount are required"}

    async with pool.acquire() as conn:
        spv = await conn.fetchrow(
            "SELECT id, name, status, min_commitment FROM spvs WHERE id = $1 AND org_id = $2",
            spv_id,
            org_id,
        )
        if not spv:
            return {"error": f"SPV {spv_id} not found"}
        if spv["status"] not in ("open", "closing"):
            return {"error": f"SPV is {spv['status']} — not accepting subscriptions"}
        if spv["min_commitment"] and commitment_amount < float(spv["min_commitment"]):
            return {"error": f"Minimum commitment is ${float(spv['min_commitment']):,.0f}"}

        entity = await conn.fetchrow(
            "SELECT id, display_name FROM entities WHERE id = $1 AND org_id = $2 AND valid_to IS NULL",
            entity_id,
            org_id,
        )
        if not entity:
            return {"error": f"Entity {entity_id} not found"}

    return {
        "spv_id": spv_id,
        "spv_name": spv["name"],
        "entity_id": entity_id,
        "entity_name": entity["display_name"],
        "commitment_amount": commitment_amount,
    }


async def _execute_subscribe(pool, user_id: str, org_id: str,
                              choice_value: str = "confirm",
                              spv_id: str = "", entity_id: str = "",
                              commitment_amount: float = 0.0, **_):
    """Confirm handler: insert or amend spv_subscriptions on choice 'confirm'."""
    if choice_value != "confirm":
        return {"result": None, "render": None, "undo_token": {"spv_id": spv_id, "entity_id": entity_id, "action": "unsubscribe"}}

    async with pool.acquire() as conn:
        # Close any existing active subscription for this entity+SPV.
        existing = await conn.fetchrow(
            "SELECT id FROM spv_subscriptions "
            "WHERE spv_id = $1 AND entity_id = $2 AND valid_to IS NULL",
            spv_id,
            entity_id,
        )
        if existing:
            await conn.execute(
                "UPDATE spv_subscriptions SET valid_to = now() WHERE id = $1",
                existing["id"],
            )

        row = await conn.fetchrow(
            """
            INSERT INTO spv_subscriptions
                (org_id, spv_id, entity_id, commitment_amount, status,
                 valid_from, created_by)
            VALUES ($1, $2, $3, $4, 'pending', now(), $5)
            RETURNING id, spv_id, entity_id, commitment_amount, status
            """,
            org_id,
            spv_id,
            entity_id,
            commitment_amount,
            user_id,
        )

    sub = {
        "id": str(row["id"]),
        "spv_id": spv_id,
        "entity_id": entity_id,
        "commitment_amount": commitment_amount,
        "status": row["status"],
    }
    return {
        "result": sub,
        "render": {
            "component": "SPVList",
            "target": "inline",
            "props": {"spvs": [sub]},
        },
        "undo_token": {
            "subscription_id": str(row["id"]),
            "spv_id": spv_id,
            "entity_id": entity_id,
            "action": "unsubscribe",
        },
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="spv.list_open",
            module="spv",
            description="Browse open SPVs available for member co-investment.",
            access_type="read",
            required_permission=None,
            default_autonomy="auto",
            reversible=False,
            render_target="inline",
            handler=_list_open_spvs,
            params_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
    )

    REGISTRY.register(
        AssistantAction(
            key="spv.show_captable",
            module="spv",
            description="Show the cap table for a specific SPV. Staff only.",
            access_type="read",
            required_permission="manage_deals",
            default_autonomy="auto",
            reversible=False,
            render_target="inline",
            handler=_show_captable,
            params_schema={
                "type": "object",
                "properties": {
                    "spv_id": {
                        "type": "string",
                        "description": "UUID of the SPV to show the cap table for.",
                    },
                },
                "required": ["spv_id"],
            },
        )
    )

    REGISTRY.register(
        AssistantAction(
            key="spv.subscribe",
            module="spv",
            description=(
                "Submit a co-investment subscription to an SPV. "
                "The member reviews the commitment before it is recorded."
            ),
            access_type="write",
            required_permission=None,
            default_autonomy="confirm",
            reversible=True,
            render_target="inline",
            handler=_execute_subscribe,
            draft_handler=_preview_subscribe,
            params_schema={
                "type": "object",
                "properties": {
                    "spv_id": {
                        "type": "string",
                        "description": "UUID of the SPV to subscribe to.",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "UUID of the investing entity.",
                    },
                    "commitment_amount": {
                        "type": "number",
                        "description": "Commitment amount in USD.",
                    },
                },
                "required": ["spv_id", "entity_id", "commitment_amount"],
            },
            options=[
                {"key": "confirm", "label": "Confirm subscription"},
                {"key": "none", "label": "Not now — I'll think about it"},
            ],
        )
    )
