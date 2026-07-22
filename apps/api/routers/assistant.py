"""Assistant orchestration router (Sprint 11).

Five endpoints:
  GET  /assistant/conversation   — get or create the active conversation
  POST /assistant/message        — run the LLM loop, return response
  POST /assistant/confirm        — execute a proposed WRITE action
  POST /assistant/activity/{id}/undo — undo a reversible activity
  GET  /assistant/activities     — list activities with optional status filter
"""
import json
import traceback
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.action_registry import REGISTRY, AssistantAction
from services.audit import write_audit_log
from services.database import get_pool
from services.extraction import call_claude_with_tools
from services.org_settings import get_brand_name
from services.rbac import get_user_permissions
from services.users import ensure_user

router = APIRouter(tags=["assistant"])

ORG_ID = "00000000-0000-0000-0000-000000000001"

def system_prompt(brand_name: str) -> str:
    """Sprint 24: the firm's name comes from org_settings, not a literal."""
    return (
    f"You are the member's private AI assistant on the {brand_name} platform — "
    "a trust-gated private wealth community. You are calm, precise, and discreet — "
    "a capable presence on the member's side of the table. Never salesy, never an "
    "exclamation point, never emoji. You have tools. Use READ tools freely to gather "
    "what you need to answer well. For any tool that changes state (write), DO NOT "
    "assume — instead propose it as a clear choice and let the member decide. When "
    "you propose an action, give a one-line rationale and offer concrete options, "
    "always including a way to decline. When showing data, prefer rendering a "
    "component over describing it in prose. Give a short prose take, then let the "
    "visual carry the detail. Keep replies brief. You are talking to a sophisticated "
    "person who values their time."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_api_messages(stored: list[dict]) -> list[dict]:
    """Strip display-only fields before sending to Claude."""
    return [
        {"role": m["role"], "content": m["content"]}
        for m in stored
        if isinstance(m, dict)
    ]


def _extract_text(content: list[dict]) -> str:
    return " ".join(
        b.get("text", "") for b in content if b.get("type") == "text"
    ).strip()


async def _run_loop(
    api_messages: list[dict],
    tool_specs: list[dict],
    action_map: dict[str, AssistantAction],
    pool,
    user_id: str,
    org_id: str,
) -> dict:
    """Run the assistant LLM loop.

    READ tools execute automatically; WRITE tools stop the loop and are returned
    as proposed_action — they are NEVER executed from here.
    """
    disclosures: list[str] = []
    render: dict | None = None
    proposed_action: dict | None = None
    final_text = ""
    new_messages: list[dict] = []

    current = list(api_messages)

    # Resolved once, not per iteration — the brand cannot change mid-loop.
    system = system_prompt(await get_brand_name(pool, org_id))

    for _iteration in range(10):
        response = await call_claude_with_tools(
            system=system,
            messages=current,
            tools=tool_specs,
            max_tokens=2000,
        )
        if response is None:
            final_text = final_text or "I'm unable to process your request right now."
            break

        stop_reason = response.get("stop_reason")
        content: list[dict] = response.get("content", [])

        # Extract any text from this turn
        turn_text = _extract_text(content)
        if turn_text:
            final_text = turn_text

        if stop_reason == "end_turn":
            # Record the assistant reply for storage
            new_messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "_display": {
                        "text": final_text,
                        "disclosures": list(disclosures),
                        "render": render,
                    },
                }
            )
            break

        if stop_reason == "tool_use":
            tool_calls = [b for b in content if b.get("type") == "tool_use"]

            # Store the assistant message that contains the tool_use blocks
            current.append({"role": "assistant", "content": content})
            new_messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "_display": {
                        "text": final_text,
                        "disclosures": [],  # filled after tool results
                        "render": None,
                    },
                }
            )

            tool_results: list[dict] = []
            write_detected = False

            for tc in tool_calls:
                tool_name: str = tc.get("name", "")
                tool_input: dict = tc.get("input", {}) or {}
                tool_use_id: str = tc.get("id", "")

                action = action_map.get(tool_name)
                if action is None:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": "Action not found.",
                        }
                    )
                    continue

                if action.access_type == "write":
                    write_detected = True
                    # Generate draft preview if a draft_handler is defined
                    preview: dict = {}
                    if action.draft_handler:
                        try:
                            preview = await action.draft_handler(
                                pool=pool,
                                user_id=user_id,
                                org_id=org_id,
                                **tool_input,
                            )
                        except Exception as exc:
                            print(f"draft_handler error ({action.key}): {exc}")
                            print(traceback.format_exc())
                    proposed_action = {
                        "action_key": action.key,
                        "params": {**tool_input, **preview},
                        "options": action.options,
                        "rationale": final_text or "I would like to propose this action.",
                    }
                    break  # stop the loop on first WRITE

                # READ: execute the handler
                try:
                    result = await action.handler(
                        pool=pool,
                        user_id=user_id,
                        org_id=org_id,
                        **tool_input,
                    )
                    disclosures.append(action.description)
                    if result.get("render"):
                        render = result["render"]
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": json.dumps(result.get("data", {})),
                        }
                    )
                    if result.get("text"):
                        final_text = result["text"]
                except Exception as exc:
                    print(f"handler error ({action.key}): {exc}")
                    print(traceback.format_exc())
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": f"Error: {exc}",
                        }
                    )

            if write_detected:
                break

            # Feed tool results back to Claude
            tool_result_msg = {"role": "user", "content": tool_results}
            current.append(tool_result_msg)
            new_messages.append(
                {
                    "role": "user",
                    "content": tool_results,
                    "_display": None,
                }
            )

            # Update disclosures on the last assistant new_message
            for m in reversed(new_messages):
                if m["role"] == "assistant" and m.get("_display") is not None:
                    m["_display"]["disclosures"] = list(disclosures)
                    m["_display"]["render"] = render
                    break

        else:
            # Unexpected stop reason — treat as end_turn
            new_messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "_display": {"text": final_text, "disclosures": list(disclosures), "render": render},
                }
            )
            break

    return {
        "message": final_text,
        "new_messages": new_messages,
        "disclosures": disclosures,
        "render": render,
        "proposed_action": proposed_action,
    }


async def _load_conversation(conn, org_id: str, user_id: str,
                              context_type: str | None, context_id: str | None) -> dict:
    # context_ref is a single jsonb column: {"type": ..., "id": ...}
    query = (
        "SELECT id, messages, context_ref, created_at, updated_at "
        "FROM assistant_conversations "
        "WHERE user_id = $1 AND org_id = $2 AND status = 'active' "
    )
    params: list = [user_id, org_id]
    if context_type:
        params.append(context_type)
        query += f" AND context_ref->>'type' = ${len(params)}"
    if context_id:
        params.append(context_id)
        query += f" AND context_ref->>'id' = ${len(params)}"
    query += " ORDER BY updated_at DESC LIMIT 1"

    row = await conn.fetchrow(query, *params)
    return dict(row) if row else {}


async def _create_conversation(conn, org_id: str, user_id: str,
                                context_type: str | None, context_id: str | None) -> dict:
    ctx_ref: str | None = None
    if context_type or context_id:
        ctx_ref = json.dumps({"type": context_type, "id": context_id})
    row = await conn.fetchrow(
        """
        INSERT INTO assistant_conversations
            (org_id, user_id, context_ref, messages, status)
        VALUES ($1, $2, $3::jsonb, '[]'::jsonb, 'active')
        RETURNING id, messages, context_ref, created_at, updated_at
        """,
        org_id,
        user_id,
        ctx_ref,
    )
    return dict(row)


def _parse_jsonb(value, default):
    """Return value parsed from JSON string if needed, else value, else default."""
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value) if value else default
    return value


def _format_conversation(row: dict) -> dict:
    raw = _parse_jsonb(row.get("messages"), [])
    display_messages = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        if m.get("_display") is None:
            content = m.get("content", "")
            if isinstance(content, str):
                display_messages.append({"role": "user", "text": content})
        else:
            d = m["_display"]
            display_messages.append(
                {
                    "role": "assistant",
                    "text": d.get("text", ""),
                    "disclosures": d.get("disclosures") or [],
                    "render": d.get("render"),
                }
            )
    ctx_ref = _parse_jsonb(row.get("context_ref"), {})
    return {
        "id": str(row["id"]),
        "context_type": ctx_ref.get("type"),
        "context_id": ctx_ref.get("id"),
        "messages": display_messages,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/assistant/conversation")
async def get_or_create_conversation(
    request: Request,
    context_type: str | None = Query(None),
    context_id: str | None = Query(None),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        org_id = get_org_id(request)
        row = await _load_conversation(conn, org_id, user_id, context_type, context_id)
        if not row:
            row = await _create_conversation(conn, org_id, user_id, context_type, context_id)
    return _format_conversation(row)


class MessageBody(BaseModel):
    message: str
    context_ref: dict | None = None  # {type: str, id: str}


@router.post("/assistant/message")
async def post_message(request: Request, body: MessageBody):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        org_id = get_org_id(request)

        # Resolve or create conversation
        ctx_type = (body.context_ref or {}).get("type")
        ctx_id = (body.context_ref or {}).get("id")
        row = await _load_conversation(conn, org_id, user_id, ctx_type, ctx_id)
        if not row:
            row = await _create_conversation(conn, org_id, user_id, ctx_type, ctx_id)
        conv_id = str(row["id"])
        stored_messages: list[dict] = list(_parse_jsonb(row.get("messages"), []))

    # RBAC
    permissions = await get_user_permissions(pool, user_id, org_id)
    allowed = REGISTRY.list_for_user(user_id, permissions)
    tool_specs = REGISTRY.to_tool_specs(allowed)
    action_map = {a.key.replace(".", "_"): a for a in allowed}

    # Append new user message
    user_msg = {"role": "user", "content": body.message, "_display": None}
    stored_messages.append(user_msg)

    # Build Anthropic-format messages from storage
    api_messages = _to_api_messages(stored_messages)

    # Run the loop
    result = await _run_loop(api_messages, tool_specs, action_map, pool, user_id, org_id)

    # Persist updated messages
    all_stored = stored_messages + result["new_messages"]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE assistant_conversations
            SET messages = $1::jsonb, updated_at = now()
            WHERE id = $2
            """,
            json.dumps(all_stored),
            conv_id,
        )

    return {
        "conversation_id": conv_id,
        "message": result["message"],
        "disclosures": result["disclosures"],
        "render": result["render"],
        "proposed_action": result["proposed_action"],
    }


class ConfirmBody(BaseModel):
    proposed_action: dict
    choice_value: str  # e.g. 'save', 'edit', 'none'
    conversation_id: str | None = None


@router.post("/assistant/confirm")
async def confirm_action(request: Request, body: ConfirmBody):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        org_id = get_org_id(request)

    pa = body.proposed_action
    action_key: str = pa.get("action_key", "")
    params: dict = pa.get("params", {})
    rationale: str = pa.get("rationale", "")

    action = REGISTRY.get(action_key)
    if action is None:
        raise HTTPException(status_code=404, detail=f"Action {action_key!r} not found")
    if action.access_type != "write":
        raise HTTPException(status_code=400, detail="Only WRITE actions can be confirmed")

    # Re-check permission
    permissions = await get_user_permissions(pool, user_id, org_id)
    if action.required_permission and action.required_permission not in permissions:
        raise HTTPException(status_code=403, detail="Permission denied")

    handler_result: dict = {"result": None, "render": None, "undo_token": None}
    if body.choice_value != "none":
        try:
            handler_result = await action.handler(
                pool=pool,
                user_id=user_id,
                org_id=org_id,
                choice_value=body.choice_value,
                **params,
            )
        except Exception as exc:
            print(f"confirm handler error ({action_key}): {exc}")
            print(traceback.format_exc())
            raise HTTPException(status_code=500, detail=str(exc))

    # Write assistant_activities row
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO assistant_activities
                (org_id, user_id, action_key, title, status,
                 rationale, reversible, undo_token, result)
            VALUES ($1, $2, $3, $4, 'done', $5, $6, $7::jsonb, $8::jsonb)
            RETURNING id, status, created_at
            """,
            org_id,
            user_id,
            action_key,
            action.description,
            rationale,
            action.reversible,
            json.dumps(handler_result.get("undo_token")),
            json.dumps(handler_result.get("result")),
        )
        activity_id_val = str(row["id"])

        await write_audit_log(
            conn,
            org_id=org_id,
            actor=user_id,
            action=f"assistant.confirm.{action_key}",
            table_name="assistant_activity",
            record_id=activity_id_val,
            new={"choice": body.choice_value, "rationale": rationale},
        )

    return {
        "result": handler_result.get("result"),
        "activity_id": activity_id_val,
        "render": handler_result.get("render"),
        "reversible": action.reversible,
    }


@router.post("/assistant/activity/{activity_id}/undo")
async def undo_activity(request: Request, activity_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        org_id = get_org_id(request)

        row = await conn.fetchrow(
            """
            SELECT id, action_key, reversible, undo_token, status
            FROM assistant_activities
            WHERE id = $1 AND user_id = $2 AND org_id = $3
            """,
            activity_id,
            user_id,
            org_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Activity not found")
        if not row["reversible"]:
            raise HTTPException(status_code=400, detail="Activity is not reversible")
        if row["status"] == "undone":
            raise HTTPException(status_code=400, detail="Activity already undone")

        undo_token = _parse_jsonb(row["undo_token"], None)
        action = REGISTRY.get(row["action_key"])

        # Execute reverse using undo_token if handler supports it
        if action and undo_token:
            try:
                await action.handler(
                    pool=pool,
                    user_id=user_id,
                    org_id=org_id,
                    choice_value="undo",
                    undo_token=undo_token,
                )
            except Exception as exc:
                print(f"undo handler error ({row['action_key']}): {exc}")
                print(traceback.format_exc())

        await conn.execute(
            "UPDATE assistant_activities SET status = 'undone', updated_at = now() WHERE id = $1",
            activity_id,
        )
        await write_audit_log(
            conn,
            org_id=org_id,
            actor=user_id,
            action=f"assistant.undo.{row['action_key']}",
            table_name="assistant_activity",
            record_id=activity_id,
        )

    return {"activity_id": activity_id, "status": "undone"}


@router.get("/assistant/activities")
async def list_activities(
    request: Request,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        org_id = get_org_id(request)

        conditions = ["user_id = $1", "org_id = $2"]
        params: list[Any] = [user_id, org_id]
        if status:
            params.append(status)
            conditions.append(f"status = ${len(params)}")

        query = (
            f"SELECT id, action_key, title, status, rationale, "
            f"reversible, created_at, undone_at "
            f"FROM assistant_activities "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY created_at DESC LIMIT {limit}"
        )
        rows = await conn.fetch(query, *params)

    return [
        {
            "id": str(r["id"]),
            "action_key": r["action_key"],
            "title": r["title"],
            "status": r["status"],
            "rationale": r["rationale"],
            "reversible": r["reversible"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "undone_at": r["undone_at"].isoformat() if r["undone_at"] else None,
        }
        for r in rows
    ]
