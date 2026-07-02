"""Dashboard router (Sprint 13).

GET  /dashboard/brief            — deterministic blocks, no AI blocking
GET  /dashboard/brief/narration  — AI narration, cached to dashboard_briefs
POST /dashboard/todos/regenerate — idempotent todo generation (debounced 2 min)
GET  /dashboard/todos            — list member todos (status = pending)
PATCH /dashboard/todos/{id}      — dismiss or complete a todo
"""
import json
from datetime import date

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

_NARRATION_SYSTEM = (
    "You are the trusted advisor voice of 2nd Act Capital — a private membership "
    "platform for post-liquidity founders. Write a warm, spare daily brief for the "
    "member in 2–3 sentences. Tone: calm, precise, no hype. No exclamation points, "
    "no emoji. Mention the most important item needing attention if there is one, "
    "and note any promising opportunity or upcoming event. If nothing is urgent, "
    "give a quiet affirming observation about their position."
)


def _greeting(full_name: str | None) -> str:
    from datetime import datetime
    h = datetime.now().hour
    time = "Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening"
    name = (full_name or "").strip().split()[0] if full_name else "Member"
    return f"{time}, {name}."


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
            "SELECT narration FROM dashboard_briefs WHERE user_id = $1 AND generated_at::date = $2",
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

    # Cache the result (safe upsert: try UPDATE, INSERT if nothing updated).
    if narration:
        async with pool.acquire() as conn:
            # Fetch full_name for greeting
            profile = await conn.fetchrow(
                "SELECT full_name FROM users WHERE id = $1", user_id,
            )
            greeting_text = _greeting(profile["full_name"] if profile else None)
            blocks_json = json.dumps(blocks)

            result = await conn.execute(
                """
                UPDATE dashboard_briefs
                SET greeting = $1, narration = $2, blocks = $3::jsonb,
                    generated_at = now()
                WHERE user_id = $4 AND generated_at::date = $5
                """,
                greeting_text, narration, blocks_json, user_id, today,
            )
            if result == "UPDATE 0":
                await conn.execute(
                    """
                    INSERT INTO dashboard_briefs
                        (org_id, user_id, greeting, narration, blocks)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    ON CONFLICT DO NOTHING
                    """,
                    org_id, user_id, greeting_text, narration, blocks_json,
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

    # Debounce: skip if any todo was created in the last 2 minutes.
    async with pool.acquire() as conn:
        recent = await conn.fetchval(
            """
            SELECT count(*) FROM member_todos
            WHERE user_id = $1 AND org_id = $2
              AND created_at > now() - interval '2 minutes'
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
            SELECT id, kind, category, source, related_type, related_id,
                   title, detail, action_key, action_params,
                   priority, status, due_date, expected_window, created_at
            FROM member_todos
            WHERE user_id = $1 AND org_id = $2
              AND status = 'open'
            ORDER BY kind, priority DESC, created_at DESC
            """,
            user_id, org_id,
        )

    def _fmt(row):
        d = dict(row)
        for k in ("id", "related_id"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        for k in ("created_at",):
            if d.get(k) is not None:
                d[k] = d[k].isoformat()
        if d.get("due_date") is not None:
            d["due_date"] = d["due_date"].isoformat()
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

        if body.dismissed is True:
            new_status = "dismissed"
        elif body.completed is True:
            new_status = "done"
        elif body.dismissed is False or body.completed is False:
            new_status = "open"
        else:
            raise HTTPException(status_code=400, detail="No update fields provided")

        updated = await conn.fetchrow(
            """
            UPDATE member_todos
            SET status = $2
            WHERE id = $1
            RETURNING id, kind, title, status
            """,
            todo_id, new_status,
        )

    d = dict(updated)
    d["id"] = str(d["id"])
    return d
