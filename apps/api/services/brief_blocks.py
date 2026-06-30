"""Dashboard brief block registry (Sprint 13).

BriefBlock: one block of the daily member brief.
BriefRegistry: register, filter by permission, assemble all data.

Member blocks (order 1-4): needs_attention, new_deals, my_positions, on_horizon
Staff blocks  (order 5-6): pipeline_snapshot, spv_activity
"""
import json
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class BriefBlock:
    key: str
    title: str
    order: int
    required_permission: str | None  # None = all authenticated users
    handler: Callable               # async (pool, user_id, org_id) -> dict | None


class BriefRegistry:
    def __init__(self) -> None:
        self._blocks: dict[str, BriefBlock] = {}

    def register(self, block: BriefBlock) -> None:
        self._blocks[block.key] = block

    def blocks_for(self, permissions: set[str]) -> list[BriefBlock]:
        result = [
            b for b in self._blocks.values()
            if b.required_permission is None or b.required_permission in permissions
        ]
        return sorted(result, key=lambda b: b.order)

    async def assemble(
        self,
        pool,
        user_id: str,
        org_id: str,
        permissions: set[str],
    ) -> list[dict]:
        blocks = self.blocks_for(permissions)
        results = []
        for block in blocks:
            try:
                data = await block.handler(pool, user_id, org_id)
                if data is not None:
                    results.append({
                        "key": block.key,
                        "title": block.title,
                        "order": block.order,
                        "data": data,
                    })
            except Exception as exc:
                print(f"[brief] block {block.key} failed: {exc}")
        return results


BRIEF_REGISTRY = BriefRegistry()


# ---------------------------------------------------------------------------
# Block handlers
# ---------------------------------------------------------------------------

def _row_to_dict(row) -> dict:
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif hasattr(v, "__class__") and v.__class__.__name__ in ("UUID",):
            d[k] = str(v)
    return d


async def _needs_attention_handler(pool, user_id: str, org_id: str) -> dict | None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, body, action_href, action_label, priority, source
            FROM member_todos
            WHERE user_id = $1 AND org_id = $2
              AND kind = 'actual'
              AND dismissed_at IS NULL
              AND completed_at IS NULL
            ORDER BY priority DESC, created_at DESC
            LIMIT 10
            """,
            user_id, org_id,
        )
    if not rows:
        return None
    return {"items": [_row_to_dict(r) for r in rows]}


async def _new_deals_handler(pool, user_id: str, org_id: str) -> dict | None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, deal_status, target_raise, close_date, published_at
            FROM deals
            WHERE org_id = $1
              AND deal_status IN ('submitted', 'under_review', 'active')
              AND valid_to IS NULL
            ORDER BY published_at DESC NULLS LAST, created_at DESC
            LIMIT 5
            """,
            org_id,
        )
    if not rows:
        return None
    return {"items": [_row_to_dict(r) for r in rows]}


async def _my_positions_handler(pool, user_id: str, org_id: str) -> dict | None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT mi.id, d.name AS deal_name, mi.investment_stage,
                   mi.amount_committed, mi.amount_funded, mi.updated_at
            FROM member_investments mi
            JOIN deals d ON d.id = mi.deal_id
            WHERE mi.user_id = $1 AND mi.org_id = $2
              AND mi.valid_to IS NULL
              AND mi.investment_stage NOT IN ('exited', 'declined')
            ORDER BY mi.updated_at DESC
            LIMIT 10
            """,
            user_id, org_id,
        )
    if not rows:
        return None
    return {"items": [_row_to_dict(r) for r in rows]}


async def _on_horizon_handler(pool, user_id: str, org_id: str) -> dict | None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, body, action_href, action_label, priority, source
            FROM member_todos
            WHERE user_id = $1 AND org_id = $2
              AND kind = 'anticipated'
              AND dismissed_at IS NULL
              AND completed_at IS NULL
            ORDER BY priority DESC, created_at DESC
            LIMIT 10
            """,
            user_id, org_id,
        )
    if not rows:
        return None
    return {"items": [_row_to_dict(r) for r in rows]}


async def _pipeline_snapshot_handler(pool, user_id: str, org_id: str) -> dict | None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT deal_status, count(*) AS count
            FROM deals
            WHERE org_id = $1 AND valid_to IS NULL
            GROUP BY deal_status
            ORDER BY deal_status
            """,
            org_id,
        )
    by_status = {r["deal_status"]: int(r["count"]) for r in rows}
    if not by_status:
        return None
    return {"by_status": by_status, "total": sum(by_status.values())}


async def _spv_activity_handler(pool, user_id: str, org_id: str) -> dict | None:
    async with pool.acquire() as conn:
        recent = await conn.fetch(
            """
            SELECT s.name AS spv_name, ssh.from_status, ssh.to_status, ssh.changed_at
            FROM spv_status_history ssh
            JOIN spvs s ON s.id = ssh.spv_id
            WHERE ssh.org_id = $1
            ORDER BY ssh.changed_at DESC
            LIMIT 5
            """,
            org_id,
        )
        soft_count = await conn.fetchval(
            """
            SELECT count(*)
            FROM spv_subscriptions
            WHERE org_id = $1
              AND subscription_status = 'soft'
              AND valid_to IS NULL
            """,
            org_id,
        )
    changes = [
        {
            "spv_name": r["spv_name"],
            "from": r["from_status"],
            "to": r["to_status"],
            "changed_at": r["changed_at"].isoformat() if r["changed_at"] else None,
        }
        for r in recent
    ]
    return {"recent_changes": changes, "soft_subscriptions": int(soft_count or 0)}


# ---------------------------------------------------------------------------
# Register all blocks
# ---------------------------------------------------------------------------

def register_brief_blocks() -> None:
    BRIEF_REGISTRY.register(BriefBlock(
        key="needs_attention",
        title="Needs your attention",
        order=1,
        required_permission=None,
        handler=_needs_attention_handler,
    ))
    BRIEF_REGISTRY.register(BriefBlock(
        key="new_deals",
        title="New deals",
        order=2,
        required_permission=None,
        handler=_new_deals_handler,
    ))
    BRIEF_REGISTRY.register(BriefBlock(
        key="my_positions",
        title="My positions",
        order=3,
        required_permission=None,
        handler=_my_positions_handler,
    ))
    BRIEF_REGISTRY.register(BriefBlock(
        key="on_horizon",
        title="On the horizon",
        order=4,
        required_permission=None,
        handler=_on_horizon_handler,
    ))
    BRIEF_REGISTRY.register(BriefBlock(
        key="pipeline_snapshot",
        title="Pipeline snapshot",
        order=5,
        required_permission="manage_deals",
        handler=_pipeline_snapshot_handler,
    ))
    BRIEF_REGISTRY.register(BriefBlock(
        key="spv_activity",
        title="SPV activity",
        order=6,
        required_permission="manage_deals",
        handler=_spv_activity_handler,
    ))
