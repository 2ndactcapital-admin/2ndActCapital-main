"""Investment Profile endpoints: questions and per-entity answers.

All routes require a valid JWT (enforced by the global middleware) and scope to
the caller's org_id. Answers are upserted: because the table has a
UNIQUE(entity_id, question_id) constraint, a single current row is kept per
(entity, question) and the full change history is recorded in audit_log.
"""

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from routers.entities import get_org_id
from schemas.investment_profile import AnswerIn, AnswerOut, QuestionOut
from services.audit import write_audit_log
from services.database import get_pool

router = APIRouter(tags=["investment-profile"])

QUESTION_COLS = (
    "id, question_key, question_text, question_type, options, category, "
    "is_required, display_order"
)
ANSWER_COLS = (
    "id, entity_id, question_id, answer_value, answer_json, created_at, updated_at"
)


def _parse_json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------
@router.get("/investment-profile/questions", response_model=list[QuestionOut])
async def list_questions(request: Request, category: str | None = None):
    org_id = get_org_id(request)
    conditions = ["org_id = $1", "valid_to IS NULL", "system_to IS NULL"]
    params: list = [org_id]
    if category:
        params.append(category)
        conditions.append(f"category = ${len(params)}")

    query = (
        f"SELECT {QUESTION_COLS} FROM investment_profile_questions "
        f"WHERE {' AND '.join(conditions)} ORDER BY display_order"
    )
    pool = await get_pool()
    rows = await pool.fetch(query, *params)
    return [
        QuestionOut(**{**dict(r), "options": _parse_json(r["options"])}) for r in rows
    ]


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------
async def _ensure_entity(conn, org_id, entity_id: UUID):
    found = await conn.fetchval(
        """
        SELECT 1 FROM entities
        WHERE id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        """,
        entity_id,
        org_id,
    )
    if not found:
        raise HTTPException(status_code=404, detail="Entity not found")


async def _upsert_one(conn, org_id, entity_id: UUID, ans: AnswerIn):
    question = await conn.fetchval(
        """
        SELECT 1 FROM investment_profile_questions
        WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL
        """,
        ans.question_id,
    )
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    old = await conn.fetchrow(
        """
        SELECT answer_value, answer_json FROM investment_profile_answers
        WHERE entity_id = $1 AND question_id = $2
        """,
        entity_id,
        ans.question_id,
    )

    answer_json = json.dumps(ans.answer_json) if ans.answer_json is not None else None
    row = await conn.fetchrow(
        f"""
        INSERT INTO investment_profile_answers (
            org_id, entity_id, question_id, answer_value, answer_json
        ) VALUES ($1, $2, $3, $4, $5::jsonb)
        ON CONFLICT (entity_id, question_id) DO UPDATE SET
            answer_value = EXCLUDED.answer_value,
            answer_json = EXCLUDED.answer_json,
            valid_from = now(),
            valid_to = NULL,
            system_from = now(),
            system_to = NULL,
            updated_at = now()
        RETURNING {ANSWER_COLS}
        """,
        org_id,
        entity_id,
        ans.question_id,
        ans.answer_value,
        answer_json,
    )

    await write_audit_log(
        conn,
        org_id=org_id,
        action="upsert",
        table_name="investment_profile_answers",
        record_id=row["id"],
        old=dict(old) if old else None,
        new=dict(row),
    )
    return row


@router.get(
    "/investment-profile/{entity_id}/answers", response_model=list[AnswerOut]
)
async def get_answers(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT a.id, a.entity_id, a.question_id, a.answer_value, a.answer_json,
               a.created_at, a.updated_at,
               q.question_key, q.question_text, q.question_type, q.options,
               q.category, q.is_required, q.display_order
        FROM investment_profile_answers a
        JOIN investment_profile_questions q ON q.id = a.question_id
        WHERE a.entity_id = $1 AND a.org_id = $2
          AND a.valid_to IS NULL AND a.system_to IS NULL
        ORDER BY q.display_order
        """,
        entity_id,
        org_id,
    )
    return [
        AnswerOut(
            **{
                **dict(r),
                "options": _parse_json(r["options"]),
                "answer_json": _parse_json(r["answer_json"]),
            }
        )
        for r in rows
    ]


@router.post(
    "/investment-profile/{entity_id}/answers",
    response_model=AnswerOut,
    status_code=201,
)
async def upsert_answer(request: Request, entity_id: UUID, body: AnswerIn):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_entity(conn, org_id, entity_id)
            row = await _upsert_one(conn, org_id, entity_id, body)
    return AnswerOut(**{**dict(row), "answer_json": _parse_json(row["answer_json"])})


@router.post(
    "/investment-profile/{entity_id}/answers/bulk",
    response_model=list[AnswerOut],
    status_code=201,
)
async def bulk_upsert_answers(
    request: Request, entity_id: UUID, body: list[AnswerIn]
):
    org_id = get_org_id(request)
    pool = await get_pool()
    results = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_entity(conn, org_id, entity_id)
            for ans in body:
                results.append(await _upsert_one(conn, org_id, entity_id, ans))
    return [
        AnswerOut(**{**dict(r), "answer_json": _parse_json(r["answer_json"])})
        for r in results
    ]
