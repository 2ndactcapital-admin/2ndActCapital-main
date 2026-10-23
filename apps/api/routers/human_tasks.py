"""REP2 — the signed-in user's governed human tasks (/human-tasks).

  active account   GET /human-tasks/mine   open tasks this user may decide, and
                                            open tasks they opened (waiting on others)

Self-scoped: a task is listed as actionable only when the caller holds its
eligible permission (Super Admin first) and is not its maker. Deciding happens
on the subject's own screen and route (e.g. /execution/instructions/{id}/approve),
which re-check everything.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from routers.entities import get_org_id
from services import human_tasks as HT
from services.authz import require_active
from services.database import get_pool

router = APIRouter(tags=["human-tasks"])


@router.get("/human-tasks/mine")
async def my_tasks(request: Request):
    principal = require_active(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        mine = await HT.tasks_for_user(conn, org_id, user_id=principal.user_id, permissions=principal.permissions,
                                       is_super_admin=principal.is_super_admin)
    return {
        **mine,
        "vocabularies": {
            "statuses": [{"key": k, "label": v} for k, v in HT.STATUS_LABELS.items()],
            "outcomes": [{"key": k, "label": v} for k, v in HT.OUTCOME_LABELS.items()],
            "kinds": [{"key": k, "label": v.label} for k, v in HT.kinds().items()],
        },
    }
