"""Investment Profile endpoints: questions and per-entity answers.

All routes require a valid JWT (enforced by the global middleware) and scope to
the caller's org_id. Answers are upserted: because the table has a
UNIQUE(entity_id, question_id) constraint, a single current row is kept per
(entity, question) and the full change history is recorded in audit_log.
"""

import json
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from routers.entities import get_org_id
from schemas.investment_profile import (
    AnswerIn,
    AnswerOut,
    BriefOut,
    ConversationMessageIn,
    ConversationMessageOut,
    ConversationOut,
    ExtractionOut,
    ExtractionReviewIn,
    QuestionOut,
)
from services.audit import write_audit_log
from services.database import get_pool
from services.extraction import (
    AI_MODEL,
    call_claude_json,
    call_claude_text,
    extract_all_for_entity,
)
from services.permissions import get_user_id, require_staff
from services.users import ensure_user

router = APIRouter(tags=["investment-profile"])

# Opening line injected when a Foundation conversation starts.
_OPENING_TEMPLATE = (
    "Let's start with a big one:\n\n{question}\n\n"
    "Take your time — there's no wrong answer."
)

# Phrases the model uses to signal it is moving to the next question.
_TRANSITION_SIGNALS = (
    "shift to", "move on", "something related", "i want to shift",
    "turn to", "next", "another area", "change direction",
)
_EXCHANGES_BEFORE_ADVANCE = 3

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


# ===========================================================================
# Sprint 10 — Foundation guided conversation
# ===========================================================================
async def _foundation_questions(conn, org_id) -> list[dict]:
    """The ordered Foundation questions (category='foundation')."""
    rows = await conn.fetch(
        """
        SELECT id, question_text, options, display_order
        FROM investment_profile_questions
        WHERE org_id = $1 AND category = 'foundation'
          AND valid_to IS NULL AND system_to IS NULL
        ORDER BY display_order
        """,
        org_id,
    )
    out = []
    for r in rows:
        opts = _parse_json(r["options"]) or {}
        out.append({
            "id": r["id"],
            "question_text": r["question_text"],
            "reveals": opts.get("reveals") if isinstance(opts, dict) else None,
        })
    return out


def _conversation_out(row, total: int) -> ConversationOut:
    return ConversationOut(
        id=row["id"],
        entity_id=row["entity_id"],
        status=row["status"],
        current_question_index=row["current_question_index"],
        messages=_parse_json(row["messages"]) or [],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        total_questions=total,
    )


async def _latest_conversation(conn, org_id, entity_id):
    return await conn.fetchrow(
        """
        SELECT id, entity_id, status, current_question_index, messages,
               started_at, completed_at
        FROM profile_conversations
        WHERE entity_id = $1 AND org_id = $2
        ORDER BY started_at DESC
        LIMIT 1
        """,
        entity_id, org_id,
    )


async def _save_foundation_answer(conn, org_id, entity_id, question_id, text):
    await conn.execute(
        """
        INSERT INTO investment_profile_answers
            (org_id, entity_id, question_id, answer_value)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (entity_id, question_id) DO UPDATE SET
            answer_value = EXCLUDED.answer_value,
            valid_from = now(), valid_to = NULL,
            system_from = now(), system_to = NULL, updated_at = now()
        """,
        org_id, entity_id, question_id, text,
    )


async def _run_entity_extraction(org_id, entity_id):
    """Background task: extract structured fields from all answers."""
    try:
        pool = await get_pool()
        await extract_all_for_entity(pool, org_id, entity_id)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback

        print(f"ERROR in entity extraction: {exc}")
        print(traceback.format_exc())


@router.get(
    "/investment-profile/{entity_id}/conversation",
    response_model=ConversationOut | None,
)
async def get_conversation(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        questions = await _foundation_questions(conn, org_id)
        row = await _latest_conversation(conn, org_id, entity_id)
    if row is None:
        return None
    return _conversation_out(row, len(questions))


@router.post(
    "/investment-profile/{entity_id}/conversation/start",
    response_model=ConversationOut,
)
async def start_conversation(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_entity(conn, org_id, entity_id)
        questions = await _foundation_questions(conn, org_id)
        if not questions:
            raise HTTPException(
                status_code=400, detail="No Foundation questions configured"
            )

        existing = await conn.fetchrow(
            """
            SELECT id, entity_id, status, current_question_index, messages,
                   started_at, completed_at
            FROM profile_conversations
            WHERE entity_id = $1 AND org_id = $2 AND status = 'in_progress'
            ORDER BY started_at DESC LIMIT 1
            """,
            entity_id, org_id,
        )
        if existing:
            return _conversation_out(existing, len(questions))

        opening = _OPENING_TEMPLATE.format(question=questions[0]["question_text"])
        messages = [{"role": "assistant", "content": opening, "question_index": 0}]
        creator = await ensure_user(conn, request)
        row = await conn.fetchrow(
            """
            INSERT INTO profile_conversations
                (org_id, entity_id, current_question_index, status, messages,
                 created_by)
            VALUES ($1, $2, 0, 'in_progress', $3::jsonb, $4)
            RETURNING id, entity_id, status, current_question_index, messages,
                      started_at, completed_at
            """,
            org_id, entity_id, json.dumps(messages), creator,
        )
    return _conversation_out(row, len(questions))


@router.post(
    "/investment-profile/{entity_id}/conversation/message",
    response_model=ConversationMessageOut,
)
async def conversation_message(
    request: Request,
    entity_id: UUID,
    body: ConversationMessageIn,
    background_tasks: BackgroundTasks,
):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        questions = await _foundation_questions(conn, org_id)
        total = len(questions)
        convo = await conn.fetchrow(
            """
            SELECT id, current_question_index, messages, status
            FROM profile_conversations
            WHERE entity_id = $1 AND org_id = $2 AND status = 'in_progress'
            ORDER BY started_at DESC LIMIT 1
            """,
            entity_id, org_id,
        )
        if convo is None:
            raise HTTPException(status_code=404, detail="No active conversation")

        idx = convo["current_question_index"]
        messages = _parse_json(convo["messages"]) or []
        messages.append({"role": "user", "content": body.message, "question_index": idx})

        current_q = questions[idx] if idx < total else None

        # Build the AI response.
        ai_text = None
        if current_q:
            system = (
                "You are a thoughtful advisor conducting a client discovery "
                "conversation for a private wealth platform. Your job is to "
                "understand the client deeply through natural conversation.\n\n"
                f"Current question you are exploring:\n{current_q['question_text']}\n\n"
                f"What it reveals: {current_q.get('reveals') or 'their priorities'}\n\n"
                "Guidelines:\n"
                "- Ask one follow-up question at a time\n"
                "- Acknowledge what they said before asking more\n"
                "- When you have enough depth on the current question (2-3 "
                "exchanges), transition naturally to the next\n"
                "- Never sound like a form or intake sheet\n"
                "- Use the client's own words back to them\n"
                "- If they give a one-word answer, gently invite more: 'Tell me "
                "more about that'\n"
                "- Signal question transitions naturally: 'That's really "
                "helpful. I want to shift to something related...'\n"
                "- After all 10 questions, close warmly: 'This has been really "
                "valuable. I have a much better sense of what matters most to "
                "you.'"
            )
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in messages
                if m.get("role") in ("user", "assistant")
            ]
            ai_text = await call_claude_text(system, history, max_tokens=300)

        if ai_text is None:
            # No API key / call failed — deterministic fallback so the flow
            # still progresses (and verify works without a key).
            ai_text = "Thank you for sharing that — it's really helpful."

        # Decide whether to advance to the next question.
        exchanges = sum(
            1 for m in messages
            if m.get("role") == "user" and m.get("question_index") == idx
        )
        signaled = any(s in ai_text.lower() for s in _TRANSITION_SIGNALS)
        advance = current_q is not None and (
            signaled or exchanges >= _EXCHANGES_BEFORE_ADVANCE
        )

        is_complete = False
        new_idx = idx
        if advance:
            # Persist the answer for the current question (all user turns on it).
            answer_text = "\n\n".join(
                m["content"] for m in messages
                if m.get("role") == "user" and m.get("question_index") == idx
            )
            await _save_foundation_answer(
                conn, org_id, entity_id, current_q["id"], answer_text
            )
            new_idx = idx + 1
            if new_idx >= total:
                is_complete = True

        messages.append(
            {"role": "assistant", "content": ai_text, "question_index": new_idx}
        )

        status = "completed" if is_complete else "in_progress"
        await conn.execute(
            """
            UPDATE profile_conversations
            SET messages = $2::jsonb,
                current_question_index = $3,
                status = $4,
                completed_at = CASE WHEN $4 = 'completed' THEN now()
                                    ELSE completed_at END
            WHERE id = $1
            """,
            convo["id"], json.dumps(messages), new_idx, status,
        )

    if is_complete:
        background_tasks.add_task(_run_entity_extraction, org_id, entity_id)

    progress = round(min(new_idx, total) / total * 100, 1) if total else 100.0
    return ConversationMessageOut(
        message=ai_text,
        question_index=new_idx,
        total_questions=total,
        is_complete=is_complete,
        progress_pct=progress,
    )


@router.post(
    "/investment-profile/{entity_id}/conversation/complete",
    response_model=ConversationOut,
)
async def complete_conversation(
    request: Request, entity_id: UUID, background_tasks: BackgroundTasks
):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        questions = await _foundation_questions(conn, org_id)
        convo = await conn.fetchrow(
            """
            SELECT id, current_question_index, messages, status
            FROM profile_conversations
            WHERE entity_id = $1 AND org_id = $2 AND status = 'in_progress'
            ORDER BY started_at DESC LIMIT 1
            """,
            entity_id, org_id,
        )
        if convo is None:
            raise HTTPException(status_code=404, detail="No active conversation")

        idx = convo["current_question_index"]
        messages = _parse_json(convo["messages"]) or []
        # Save any pending answer for the current question.
        if idx < len(questions):
            answer_text = "\n\n".join(
                m["content"] for m in messages
                if m.get("role") == "user" and m.get("question_index") == idx
            )
            if answer_text:
                await _save_foundation_answer(
                    conn, org_id, entity_id, questions[idx]["id"], answer_text
                )

        row = await conn.fetchrow(
            """
            UPDATE profile_conversations
            SET status = 'completed', completed_at = now()
            WHERE id = $1
            RETURNING id, entity_id, status, current_question_index, messages,
                      started_at, completed_at
            """,
            convo["id"],
        )
    background_tasks.add_task(_run_entity_extraction, org_id, entity_id)
    return _conversation_out(row, len(questions))


# ===========================================================================
# Sprint 10 — AI extraction review
# ===========================================================================
@router.post(
    "/investment-profile/{entity_id}/extract", response_model=list[ExtractionOut]
)
async def run_extraction(request: Request, entity_id: UUID):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    await extract_all_for_entity(pool, org_id, entity_id)
    return await get_extractions(request, entity_id)


@router.get(
    "/investment-profile/{entity_id}/extractions",
    response_model=list[ExtractionOut],
)
async def get_extractions(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT e.id, e.entity_id, e.question_id, e.answer_id, e.extracted_fields,
               e.confidence, e.advisor_reviewed, e.advisor_accepted, e.created_at,
               q.question_text, a.answer_value AS answer_text
        FROM investment_profile_extractions e
        JOIN investment_profile_questions q ON q.id = e.question_id
        LEFT JOIN investment_profile_answers a ON a.id = e.answer_id
        WHERE e.entity_id = $1 AND e.org_id = $2
        ORDER BY e.created_at DESC
        """,
        entity_id, org_id,
    )
    return [
        ExtractionOut(
            **{
                **dict(r),
                "extracted_fields": _parse_json(r["extracted_fields"]),
                "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            }
        )
        for r in rows
    ]


@router.put(
    "/investment-profile/{entity_id}/extractions/{extraction_id}/review",
    response_model=ExtractionOut,
)
async def review_extraction(
    request: Request, entity_id: UUID, extraction_id: UUID, body: ExtractionReviewIn
):
    require_staff(request)
    org_id = get_org_id(request)
    actor = None
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor = await ensure_user(conn, request)
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE investment_profile_extractions
                SET advisor_reviewed = true, advisor_accepted = $3,
                    reviewed_by = $4, reviewed_at = now()
                WHERE id = $1 AND entity_id = $2 AND org_id = $5
                RETURNING id, entity_id, question_id, answer_id, extracted_fields,
                          confidence, advisor_reviewed, advisor_accepted, created_at
                """,
                extraction_id, entity_id, body.accepted, actor, org_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Extraction not found")

            if body.accepted:
                parsed = _parse_json(row["extracted_fields"]) or {}
                fields = body.edits if body.edits else (parsed.get("fields") or {})
                for key, value in fields.items():
                    await conn.execute(
                        """
                        INSERT INTO entity_attributes
                            (org_id, entity_id, attribute_key, attribute_value,
                             value_type)
                        VALUES ($1, $2, $3, $4, 'string')
                        """,
                        org_id, entity_id, key,
                        value if isinstance(value, str) else json.dumps(value),
                    )
    return ExtractionOut(
        **{
            **dict(row),
            "extracted_fields": _parse_json(row["extracted_fields"]),
            "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
        }
    )


# ===========================================================================
# Sprint 10 — Client brief
# ===========================================================================
_BRIEF_SYSTEM = (
    "You are a senior advisor writing a concise client brief for an internal "
    "audience. Write in third person. Be specific — use the client's actual "
    "words and examples where possible. No boilerplate. No generic statements. "
    "Format: two sections, plain prose, no bullet points.\n\n"
    "Section 1: Who They Are (150 words max)\n"
    "The person, their background, what drives them, who matters to them.\n\n"
    "Section 2: What They Need From Us (150 words max)\n"
    "Their real objectives, their floor, their decision style, what success "
    "looks like for this relationship."
)

_BRIEF_THEMES_SYSTEM = (
    "Extract a compact summary of this client brief as JSON only:\n"
    '{"key_themes": ["theme", ...up to 5], "risk_profile": "short phrase", '
    '"decision_style": "short phrase"}'
)


@router.post("/investment-profile/{entity_id}/brief", response_model=BriefOut)
async def generate_brief(request: Request, entity_id: UUID):
    require_staff(request)
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            """
            SELECT display_name, entity_type, primary_email, notes
            FROM entities
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            entity_id, org_id,
        )
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")

        answers = await conn.fetch(
            """
            SELECT q.question_text, a.answer_value
            FROM investment_profile_answers a
            JOIN investment_profile_questions q ON q.id = a.question_id
            WHERE a.entity_id = $1 AND a.org_id = $2
              AND a.valid_to IS NULL AND a.system_to IS NULL
              AND a.answer_value IS NOT NULL AND a.answer_value <> ''
            ORDER BY q.display_order
            """,
            entity_id, org_id,
        )
        notes = await conn.fetch(
            """
            SELECT note_text, note_type, meeting_date FROM entity_notes
            WHERE entity_id = $1 AND org_id = $2
            ORDER BY created_at DESC LIMIT 25
            """,
            entity_id, org_id,
        )
        actor = await ensure_user(conn, request)

    parts = [
        f"Client: {entity['display_name']} ({entity['entity_type']})",
    ]
    if entity["notes"]:
        parts.append(f"CRM notes field: {entity['notes']}")
    if answers:
        parts.append("\nFoundation answers:")
        for a in answers:
            parts.append(f"Q: {a['question_text']}\nA: {a['answer_value']}")
    if notes:
        parts.append("\nMeeting notes:")
        for n in notes:
            when = f" ({n['meeting_date']})" if n["meeting_date"] else ""
            parts.append(f"- [{n['note_type']}{when}] {n['note_text']}")
    context = "\n".join(parts)

    brief_text = await call_claude_text(
        _BRIEF_SYSTEM, [{"role": "user", "content": context}], max_tokens=600
    )
    if brief_text is None:
        raise HTTPException(
            status_code=503,
            detail="AI is not configured (ANTHROPIC_API_KEY missing)",
        )

    # Best-effort secondary pass for structured summary fields.
    themes = await call_claude_json(_BRIEF_THEMES_SYSTEM, brief_text, max_tokens=200)
    key_themes = (themes or {}).get("key_themes") or None
    risk_profile = (themes or {}).get("risk_profile")
    decision_style = (themes or {}).get("decision_style")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE entity_briefs SET is_current = false "
                "WHERE entity_id = $1 AND org_id = $2 AND is_current = true",
                entity_id, org_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO entity_briefs
                    (org_id, entity_id, brief_text, key_themes, risk_profile,
                     decision_style, input_sources, model_used, is_current,
                     generated_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, true, $9)
                RETURNING id, entity_id, brief_text, key_themes, risk_profile,
                          decision_style, is_current, generated_at, model_used
                """,
                org_id, entity_id, brief_text, key_themes, risk_profile,
                decision_style,
                json.dumps({"answers": len(answers), "notes": len(notes)}),
                AI_MODEL, actor,
            )
    return BriefOut(**dict(row))


@router.get(
    "/investment-profile/{entity_id}/brief", response_model=BriefOut | None
)
async def get_brief(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, entity_id, brief_text, key_themes, risk_profile,
               decision_style, is_current, generated_at, model_used
        FROM entity_briefs
        WHERE entity_id = $1 AND org_id = $2 AND is_current = true
        ORDER BY generated_at DESC LIMIT 1
        """,
        entity_id, org_id,
    )
    if row is None:
        return None
    return BriefOut(**dict(row))
