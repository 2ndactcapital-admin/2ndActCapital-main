"""Dashboard router (Sprint 13).

GET  /dashboard/brief            — deterministic blocks, no AI blocking
GET  /dashboard/brief/narration  — AI narration, cached to dashboard_briefs
POST /dashboard/todos/regenerate — idempotent todo generation (debounced 2 min)
GET  /dashboard/todos            — list member todos
PATCH /dashboard/todos/{id}      — dismiss or complete a todo
"""
import json
from datetime import date, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.brief_blocks import BRIEF_REGISTRY
from services.database import get_pool
from services.extraction import call_claude_text, ASSISTANT_MODEL
from services.rbac import get_user_permissions
from services.todo_generators import regenerate_todos
from services.users import ensure_user

router = APIRouter(tags=["dashboard"])

ORG_ID = "00000000-0000-0000-0000-000000000001"

_NARRATION_SYSTEM = (
    "You are the trusted advisor voice of 2nd Act Capital — a private membership "
    "platform for post-liquidity founders. Write a warm, spare daily brief for the "
    "member in 2–3 sentences. Tone: calm, precise, no hype. No exclamation points, "
    "no emoji. Mention the most important item needing attention if there is one, "
    "and note any promising opportunity or upcoming event. If nothing is urgent, "
    "give a quiet affirming observation about their position."
)


# ---------------------------------------------------------------------------
# GET /dashboard/brief
# ---------------------------------------------------------------------------

@router.get("/dashboard/brief")
async def get_brief(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)

    permissions = set(await get_user_permissions(pool, user_id, org_id))
    blocks = await BRIEF_REGISTRY.assemble(pool, user_id, org_id, permissions)

    return {
        "blocks": blocks,
        "narration_pending": True,
    }


# ---------------------------------------------------------------------------
# GET /dashboard/brief/narration
# ---------------------------------------------------------------------------

@router.get("/dashboard/brief/narration")
async def get_brief_narration(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)

    today = date.today()

    # Return cached narration if generated today.
    async with pool.acquire() as conn:
        cached = await conn.fetchrow(
            "SELECT narration FROM dashboard_briefs WHERE user_id = $1 AND brief_date = $2",
            user_id, today,
        )
    if cached and cached["narration"]:
        return {"narration": cached["narration"]}

    # Assemble blocks to form the prompt context.
    permissions = set(await get_user_permissions(pool, user_id, org_id))
    blocks = await BRIEF_REGISTRY.assemble(pool, user_id, org_id, permissions)

    if not blocks:
        return {"narration": None}

    context_parts = []
    for block in blocks:
        items = block["data"].get("items") or []
        by_status = block["data"].get("by_status")
        if items:
            titles = [i.get("title") or i.get("deal_name") or "" for i in items[:3]]
            context_parts.append(f"{block['title']}: {', '.join(t for t in titles if t)}")
        elif by_status:
            summary = "; ".join(f"{k}: {v}" for k, v in by_status.items())
            context_parts.append(f"{block['title']}: {summary}")

    context = "\n".join(context_parts) or "No data today."

    narration = await call_claude_text(
        system=_NARRATION_SYSTEM,
        messages=[{"role": "user", "content": f"Member brief context:\n{context}"}],
        max_tokens=180,
        model=ASSISTANT_MODEL,
    )

    # Cache the result (upsert by user + date).
    if narration:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dashboard_briefs (org_id, user_id, brief_date, narration, model_used)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, brief_date)
                DO UPDATE SET narration = EXCLUDED.narration,
                              model_used = EXCLUDED.model_used,
                              generated_at = now()
                """,
                org_id, user_id, today, narration, ASSISTANT_MODEL,
            )

    return {"narration": narration}


# ---------------------------------------------------------------------------
# POST /dashboard/todos/regenerate
# ---------------------------------------------------------------------------

@router.post("/dashboard/todos/regenerate")
async def regenerate(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)

    # Debounce: skip if any todo was touched in the last 2 minutes.
    async with pool.acquire() as conn:
        recent = await conn.fetchval(
            """
            SELECT count(*) FROM member_todos
            WHERE user_id = $1 AND org_id = $2
              AND updated_at > now() - interval '2 minutes'
            """,
            user_id, org_id,
        )
    if recent and recent > 0:
        return {"skipped": True, "reason": "debounced"}

    counts = await regenerate_todos(pool, user_id, org_id)
    return {"generated": counts}


# ---------------------------------------------------------------------------
# GET /dashboard/todos
# ---------------------------------------------------------------------------

@router.get("/dashboard/todos")
async def list_todos(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        rows = await conn.fetch(
            """
            SELECT id, kind, source, related_id, title, body,
                   action_href, action_label, priority,
                   dismissed_at, completed_at, created_at
            FROM member_todos
            WHERE user_id = $1 AND org_id = $2
            ORDER BY kind, priority DESC, created_at DESC
            """,
            user_id, org_id,
        )

    def _fmt(row):
        d = dict(row)
        for k in ("id", "related_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("dismissed_at", "completed_at", "created_at"):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        return d

    items = [_fmt(r) for r in rows]
    actual = [i for i in items if i["kind"] == "actual"]
    anticipated = [i for i in items if i["kind"] == "anticipated"]
    return {"actual": actual, "anticipated": anticipated}


# ---------------------------------------------------------------------------
# PATCH /dashboard/todos/{id}
# ---------------------------------------------------------------------------

class TodoPatch(BaseModel):
    dismissed: bool | None = None
    completed: bool | None = None


@router.patch("/dashboard/todos/{todo_id}")
async def patch_todo(todo_id: str, body: TodoPatch, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)

        row = await conn.fetchrow(
            "SELECT id FROM member_todos WHERE id = $1 AND user_id = $2 AND org_id = $3",
            todo_id, user_id, org_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Todo not found")

        updates = {}
        if body.dismissed is True:
            updates["dismissed_at"] = "now()"
        elif body.dismissed is False:
            updates["dismissed_at"] = None
        if body.completed is True:
            updates["completed_at"] = "now()"
        elif body.completed is False:
            updates["completed_at"] = None

        if not updates:
            raise HTTPException(status_code=400, detail="No update fields provided")

        # Build SET clause — now() fields handled specially.
        set_parts = []
        params: list = [todo_id]
        for col, val in updates.items():
            if val == "now()":
                set_parts.append(f"{col} = now()")
            else:
                params.append(val)
                set_parts.append(f"{col} = ${len(params)}")
        set_parts.append("updated_at = now()")
        set_clause = ", ".join(set_parts)

        updated = await conn.fetchrow(
            f"""
            UPDATE member_todos
            SET {set_clause}
            WHERE id = $1
            RETURNING id, kind, title, dismissed_at, completed_at
            """,
            *params,
        )

    d = dict(updated)
    d["id"] = str(d["id"])
    for k in ("dismissed_at", "completed_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d
