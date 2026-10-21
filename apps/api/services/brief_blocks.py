"""Dashboard brief block registry (Sprint 13).

BriefBlock: one block of the daily member brief.
BriefRegistry: register, filter by permission, assemble all data.

Member blocks (order 1-4): needs_attention, new_deals, my_positions, on_horizon
Staff blocks  (order 5-6): pipeline_snapshot, spv_activity
"""
import json
from dataclasses import dataclass, field
from typing import Callable

from services import deal_lifecycle as dlc
from services.spv_rollup import spv_commitment_summaries
from services.todo_links import todo_href


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
            SELECT id, title, detail, action_key, action_params, priority, source,
                   related_type, related_id
            FROM member_todos
            WHERE user_id = $1 AND org_id = $2
              AND kind = 'actual'
              AND status = 'open'
            ORDER BY priority DESC, created_at DESC
            LIMIT 10
            """,
            user_id, org_id,
        )
    if not rows:
        return None
    return {"items": [_todo_item(r) for r in rows]}


def _todo_item(row) -> dict:
    """BUGFIX1 (DASH-02) — every to-do carries the href where it is resolved."""
    d = _row_to_dict(row)
    d["href"] = todo_href(row)
    return d


async def _new_deals_handler(pool, user_id: str, org_id: str) -> dict | None:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, deal_status, target_raise, close_date, published_at
            FROM deals
            WHERE org_id = $1
              AND deal_status IN ('submitted', 'under_review', 'active')
              AND valid_to IS NULL AND system_to IS NULL
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
            SELECT id, title, detail, action_key, action_params, priority, source,
                   related_type, related_id
            FROM member_todos
            WHERE user_id = $1 AND org_id = $2
              AND kind = 'anticipated'
              AND status = 'open'
            ORDER BY priority DESC, created_at DESC
            LIMIT 10
            """,
            user_id, org_id,
        )
    if not rows:
        return None
    return {"items": [_todo_item(r) for r in rows]}


async def _pipeline_snapshot_handler(pool, user_id: str, org_id: str) -> dict | None:
    """BUGFIX1 (DASH-01) — the SAME services.deal_lifecycle.summary the
    Marketplace summary bar uses (staff-only block: no member narrowing), so
    the two can no longer count in two vocabularies. The old query also
    lacked `system_to IS NULL` and would have counted closed versions."""
    async with pool.acquire() as conn:
        stages = await dlc.summary(conn, org_id)
    total = sum(s["count"] for s in stages)
    if not total:
        return None
    return {
        "stages": stages,
        "by_status": {s["status"]: s["count"] for s in stages if s["count"]},
        "total": total,
    }


async def _spv_activity_handler(pool, user_id: str, org_id: str) -> dict | None:
    """BUGFIX1 (DASH-03) — ONE line per SPV with its CURRENT state (it listed
    the last five spv_status_history EVENTS, so one SPV created then opened
    appeared twice), with committed / % of target from the same
    spv_commitment_summaries the cap table and list card use."""
    async with pool.acquire() as conn:
        spvs = await conn.fetch(
            """
            SELECT s.id, s.name, s.spv_status,
                   (SELECT max(ssh.changed_at) FROM spv_status_history ssh
                     WHERE ssh.spv_id = s.id AND ssh.org_id = s.org_id) AS changed_at
            FROM spvs s
            WHERE s.org_id = $1 AND s.spv_status NOT IN ('closed', 'cancelled')
            ORDER BY changed_at DESC NULLS LAST, s.created_at DESC
            LIMIT 5
            """,
            org_id,
        )
        summaries = await spv_commitment_summaries(conn, org_id, [r["id"] for r in spvs])
    if not spvs:
        return None
    items = []
    for r in spvs:
        c = summaries.get(r["id"]) or {}
        items.append({
            "spv_id": str(r["id"]),
            "spv_name": r["name"],
            "status": r["spv_status"],
            "changed_at": r["changed_at"].isoformat() if r["changed_at"] else None,
            "commitment": _jsonable(c),
        })
    soft = sum(int((summaries.get(r["id"]) or {}).get("soft_count", 0)) for r in spvs)
    return {"spvs": items, "soft_subscriptions": soft}


def _jsonable(d: dict) -> dict:
    out = {}
    for k, v in (d or {}).items():
        if v is not None and v.__class__.__name__ == "Decimal":
            v = float(v)
        elif v is not None and v.__class__.__name__ == "UUID":
            v = str(v)
        out[k] = v
    return out


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
