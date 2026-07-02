"""SPV assistant actions (Sprint 12)."""
import uuid
from services.action_registry import AssistantAction, REGISTRY
from services.spv_allocation import allocate_transaction


async def _list_open_spvs(pool, user_id: str, org_id: str, **_):
    """Return open/closing SPVs available for member co-investment."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id, s.name, s.spv_status AS status, s.target_raise, s.min_commitment,
                   s.close_date,
                   COALESCE(SUM(sub.commitment_amount), 0) AS total_committed
            FROM spvs s
            LEFT JOIN spv_subscriptions sub
              ON sub.spv_id = s.id AND sub.valid_to IS NULL
            WHERE s.org_id = $1
              AND s.spv_status IN ('open', 'closing')
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
            "SELECT id, name, target_raise, spv_status AS status FROM spvs WHERE id = $1 AND org_id = $2",
            spv_id,
            org_id,
        )
        if not spv:
            return {"data": {"error": "SPV not found"}, "render": None, "text": "SPV not found."}

        rows = await conn.fetch(
            """
            SELECT s.entity_id,
                   COALESCE(e.display_name, e.legal_name, s.entity_id::text) AS entity_name,
                   s.commitment_amount, s.funded_amount, s.ownership_pct,
                   s.subscription_status AS status
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
            "SELECT id, name, spv_status AS status, min_commitment FROM spvs WHERE id = $1 AND org_id = $2",
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
                (org_id, spv_id, entity_id, commitment_amount, subscription_status,
                 valid_from, created_by)
            VALUES ($1, $2, $3, $4, 'soft', now(), $5)
            RETURNING id, spv_id, entity_id, commitment_amount, subscription_status
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
        "status": row["subscription_status"],
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


async def _show_ledger_handler(pool, user_id: str, org_id: str, spv_id: str = "", **_):
    """Return ledger summary for a specific SPV (staff only)."""
    if not spv_id:
        return {"data": {"error": "spv_id required"}, "render": None, "text": "Please provide an SPV ID."}

    async with pool.acquire() as conn:
        spv = await conn.fetchrow(
            "SELECT id, name FROM spvs WHERE id = $1 AND org_id = $2",
            spv_id,
            org_id,
        )
        if not spv:
            return {"data": {"error": "SPV not found"}, "render": None, "text": "SPV not found."}

        rows = await conn.fetch(
            """
            SELECT txn_type, SUM(amount) AS total
            FROM spv_transactions
            WHERE spv_id = $1 AND status = 'posted' AND org_id = $2
            GROUP BY txn_type
            """,
            spv_id,
            org_id,
        )

    totals_by_type = {r["txn_type"]: float(r["total"]) for r in rows}
    summary = {
        "total_called": totals_by_type.get("capital_call", 0.0),
        "total_distributed": totals_by_type.get("distribution", 0.0),
        "total_fees": totals_by_type.get("fee", 0.0),
        "by_type": totals_by_type,
    }

    name = spv["name"]
    return {
        "data": {"spv_id": spv_id, "spv_name": name, "summary": summary},
        "render": {
            "component": "SPVLedger",
            "target": "screen",
            "screen_route": f"/spvs/{spv_id}?tab=transactions",
            "props": {"spv_id": spv_id, "spv_name": name, "summary": summary},
        },
        "text": f"Opening {name} ledger...",
    }


async def _record_txn_draft(
    pool, user_id: str, org_id: str,
    spv_id: str = "", txn_type: str = "", amount: float = 0.0,
    txn_date: str = "", description: str = "",
    transaction_type: str = "", currency_code: str = "USD", **_
):
    """Draft handler: validate inputs and return a preview (no DB writes)."""
    errors = []
    if not spv_id:
        errors.append("spv_id is required")
    if not txn_type and not transaction_type:
        errors.append("txn_type or transaction_type (code) is required")
    if not amount or amount <= 0:
        errors.append("amount must be a positive number")
    if not txn_date:
        errors.append("txn_date is required")
    if errors:
        return {"error": "; ".join(errors)}

    async with pool.acquire() as conn:
        spv = await conn.fetchrow(
            "SELECT id, name FROM spvs WHERE id = $1 AND org_id = $2",
            spv_id,
            org_id,
        )
        if not spv:
            return {"error": f"SPV {spv_id} not found"}

        # Resolve transaction type by code if provided
        type_label = txn_type or transaction_type
        type_row = None
        if transaction_type:
            type_row = await conn.fetchrow(
                "SELECT id, label, amount_basis FROM transaction_types "
                "WHERE code = $1 AND org_id = $2 AND is_active = true",
                transaction_type, org_id,
            )
            if type_row:
                type_label = type_row["label"]

    return {
        "spv_id": spv_id,
        "spv_name": spv["name"],
        "txn_type": txn_type or transaction_type,
        "type_label": type_label,
        "amount": amount,
        "txn_date": txn_date,
        "description": description,
        "currency_code": currency_code or "USD",
    }


async def _record_txn_confirm(
    pool, user_id: str, org_id: str,
    choice_value: str = "confirm",
    spv_id: str = "", txn_type: str = "", amount: float = 0.0,
    txn_date: str = "", description: str = "",
    transaction_type: str = "", currency_code: str = "USD", **_
):
    """Confirm handler: insert spv_transactions row and optionally allocate."""
    if choice_value == "none":
        return {"result": None, "render": None, "undo_token": None}

    async with pool.acquire() as conn:
        # Resolve transaction_type by code if provided
        resolved_type_id = None
        resolved_txn_type = txn_type
        resolved_currency = currency_code or "USD"
        if transaction_type:
            type_row = await conn.fetchrow(
                "SELECT id, code FROM transaction_types "
                "WHERE code = $1 AND org_id = $2 AND is_active = true",
                transaction_type, org_id,
            )
            if type_row:
                resolved_type_id = type_row["id"]
                resolved_txn_type = type_row["code"]

        row = await conn.fetchrow(
            """
            INSERT INTO spv_transactions
                (org_id, spv_id, txn_type, amount, txn_date, description,
                 transaction_type_id, currency_code, status, created_by)
            VALUES ($1, $2, $3, $4, $5::date, $6, $7, $8, 'draft', $9)
            RETURNING id, org_id, spv_id, txn_type, amount, txn_date,
                      description, transaction_type_id, currency_code, status
            """,
            org_id,
            spv_id,
            resolved_txn_type or txn_type,
            amount,
            txn_date,
            description,
            resolved_type_id,
            resolved_currency,
            user_id,
        )

    txn_id = row["id"]
    txn = {
        "id": str(txn_id),
        "spv_id": spv_id,
        "txn_type": txn_type,
        "amount": float(row["amount"]),
        "txn_date": row["txn_date"].isoformat() if row["txn_date"] else txn_date,
        "description": description,
        "status": row["status"],
    }

    allocations = None
    if choice_value == "confirm_and_allocate":
        allocations = await allocate_transaction(pool, str(txn_id), user_id)
        txn["status"] = "allocated"

    undo_token = {"transaction_id": str(txn_id), "action": "void"}

    return {
        "result": {**txn, "allocations": allocations},
        "render": {
            "component": "SPVLedger",
            "target": "screen",
            "screen_route": f"/spvs/{spv_id}?tab=transactions",
            "props": {"spv_id": spv_id, "transaction": txn},
        },
        "undo_token": undo_token,
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

    REGISTRY.register(
        AssistantAction(
            key="spv.show_ledger",
            module="spv",
            description="Show the transaction ledger summary for a specific SPV. Staff only.",
            access_type="read",
            required_permission="manage_deals",
            default_autonomy="auto",
            reversible=False,
            render_target="screen",
            handler=_show_ledger_handler,
            params_schema={
                "type": "object",
                "properties": {
                    "spv_id": {
                        "type": "string",
                        "description": "UUID of the SPV whose ledger to display.",
                    },
                },
                "required": ["spv_id"],
            },
        )
    )

    REGISTRY.register(
        AssistantAction(
            key="spv.record_transaction",
            module="spv",
            description=(
                "Record a transaction against an SPV (capital call, distribution, fee, etc.). "
                "Specify the type by code (transaction_type) or legacy txn_type string. "
                "Optionally allocates the transaction to subscribers immediately."
            ),
            access_type="write",
            required_permission="manage_deals",
            default_autonomy="confirm",
            reversible=True,
            render_target="screen",
            handler=_record_txn_confirm,
            draft_handler=_record_txn_draft,
            params_schema={
                "type": "object",
                "properties": {
                    "spv_id": {
                        "type": "string",
                        "description": "UUID of the SPV for this transaction.",
                    },
                    "transaction_type": {
                        "type": "string",
                        "description": "Transaction type code (e.g. call_investment, dist_standard). Preferred over txn_type.",
                    },
                    "txn_type": {
                        "type": "string",
                        "description": "Legacy transaction type string (capital_call, distribution, fee). Use transaction_type when possible.",
                    },
                    "amount": {
                        "type": "number",
                        "description": "Transaction amount.",
                    },
                    "currency_code": {
                        "type": "string",
                        "description": "ISO 4217 currency code (default: USD).",
                    },
                    "txn_date": {
                        "type": "string",
                        "description": "Transaction date (YYYY-MM-DD).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional free-text description.",
                    },
                },
                "required": ["spv_id", "amount", "txn_date"],
            },
            options=[
                {"key": "confirm_and_allocate", "label": "Create & allocate now"},
                {"key": "draft_only", "label": "Create as draft"},
                {"key": "none", "label": "Not now"},
            ],
        )
    )
