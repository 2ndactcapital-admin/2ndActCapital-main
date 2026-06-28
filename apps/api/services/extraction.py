"""AI extraction service (Sprint 10 — Client Intelligence Layer).

Wraps the Anthropic API to pull structured fields out of conversational
Foundation answers and free-text CRM notes. All calls use the Haiku model and
return parsed JSON; the model is asked to return JSON only and we strip any
accidental markdown fences before parsing.

Inserts/updates use the shared pool (statement_cache_size=0 — PgBouncer safe).
"""

import json
import os

AI_MODEL = "claude-haiku-4-5-20251001"
ASSISTANT_MODEL = "claude-sonnet-4-6"


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        # Drop the opening fence (``` or ```json) and the trailing fence.
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        # Remove a leading "json" language tag if it survived.
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    return t.strip()


async def call_claude_json(system: str, user: str, max_tokens: int = 400) -> dict | None:
    """Call Claude and return parsed JSON, or None if unavailable/unparseable."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic as _anthropic

        client = _anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model=AI_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = message.content[0].text
        return json.loads(_strip_fences(raw))
    except Exception as exc:
        print(f"call_claude_json failed: {exc}")
        return None


async def call_claude_text(system: str, messages: list[dict], max_tokens: int = 400) -> str | None:
    """Call Claude with a message history and return the text response."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic as _anthropic

        client = _anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model=AI_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return message.content[0].text
    except Exception as exc:
        print(f"call_claude_text failed: {exc}")
        return None


async def call_claude_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    model: str = ASSISTANT_MODEL,
    max_tokens: int = 2000,
) -> dict | None:
    """Call Claude with tool-use (ASSISTANT_MODEL by default) and return the raw response dict.

    Returns a dict with keys: stop_reason, content (list of blocks).
    Returns None when the API key is absent or the call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic as _anthropic

        client = _anthropic.AsyncAnthropic(api_key=api_key)
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools
        message = await client.messages.create(**kwargs)
        return {
            "stop_reason": message.stop_reason,
            "content": [b.model_dump() for b in message.content],
        }
    except Exception as exc:
        print(f"call_claude_with_tools failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Foundation answer extraction
# ---------------------------------------------------------------------------
_ANSWER_SYSTEM = (
    "You are extracting structured investment profile data from a client's "
    "conversational answer. Return ONLY valid JSON, no other text.\n\n"
    "Extract whatever fields are relevant from the answer. Common fields to "
    "look for:\n"
    "- time_horizon_years (integer)\n"
    "- investment_objectives (array of strings)\n"
    "- risk_floor (description of catastrophic scenario to avoid)\n"
    "- liquidity_needs_description (text)\n"
    "- decision_makers (array of names/roles)\n"
    "- past_bad_advice (description)\n"
    "- non_negotiables (array)\n"
    "- complexity_preference (low/medium/high)\n"
    "- money_meaning (text)\n"
    "- advisor_contact_preference (text)\n"
    "- behavioral_risk_indicators (array)\n"
    "- key_concerns (array)\n\n"
    "Only include fields where the answer provides clear evidence. "
    "Confidence 0-1.\n\n"
    "Return format:\n"
    '{"fields": {"field_name": "value"}, "confidence": 0.85, '
    '"summary": "One sentence summary"}'
)


async def extract_from_answer(
    pool, org_id, entity_id, question_id, answer_id, question_text, answer_text
) -> dict:
    """Extract structured fields from one Foundation answer and persist them."""
    parsed = await call_claude_json(
        _ANSWER_SYSTEM,
        f"Question: {question_text}\nAnswer: {answer_text}",
        max_tokens=500,
    )
    fields = (parsed or {}).get("fields") or {}
    confidence = (parsed or {}).get("confidence")
    summary = (parsed or {}).get("summary")
    payload = {"fields": fields, "summary": summary}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO investment_profile_extractions
                (org_id, entity_id, question_id, answer_id, extracted_fields,
                 extraction_model, confidence)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            RETURNING id
            """,
            org_id, entity_id, question_id, answer_id,
            json.dumps(payload),
            AI_MODEL if parsed is not None else None,
            confidence,
        )
    return {"id": str(row["id"]), "fields": fields, "confidence": confidence,
            "summary": summary}


async def extract_all_for_entity(pool, org_id, entity_id) -> list[dict]:
    """Run extraction on every answered Foundation question for an entity."""
    async with pool.acquire() as conn:
        answers = await conn.fetch(
            """
            SELECT a.id AS answer_id, a.question_id, a.answer_value,
                   q.question_text
            FROM investment_profile_answers a
            JOIN investment_profile_questions q ON q.id = a.question_id
            WHERE a.entity_id = $1 AND a.org_id = $2
              AND a.valid_to IS NULL AND a.system_to IS NULL
              AND q.category = 'foundation'
              AND a.answer_value IS NOT NULL AND a.answer_value <> ''
            ORDER BY q.display_order
            """,
            entity_id, org_id,
        )
        # Avoid duplicate extractions for answers already processed.
        done = await conn.fetch(
            "SELECT answer_id FROM investment_profile_extractions WHERE entity_id = $1",
            entity_id,
        )
    done_ids = {str(r["answer_id"]) for r in done}

    results = []
    for a in answers:
        if str(a["answer_id"]) in done_ids:
            continue
        results.append(
            await extract_from_answer(
                pool, org_id, entity_id, a["question_id"], a["answer_id"],
                a["question_text"], a["answer_value"],
            )
        )
    return results


# ---------------------------------------------------------------------------
# CRM note extraction
# ---------------------------------------------------------------------------
_NOTE_SYSTEM = (
    "You are extracting CRM updates from an advisor's meeting note. Return ONLY "
    "JSON.\n\n"
    "Look for updates to:\n"
    "- contact info changes (email, phone, address)\n"
    "- life events (marriage, divorce, death, birth, graduation, illness)\n"
    "- financial events (liquidity event, inheritance, business sale, home "
    "purchase)\n"
    "- relationship changes (new advisor, new accountant, family member added)\n"
    "- preference updates (communication style, meeting frequency, topics of "
    "interest)\n"
    "- risk indicator changes (new concerns, changed risk appetite)\n\n"
    "Return format:\n"
    '{"entity_updates": {"field": "new_value"}, '
    '"new_attributes": {"key": "value"}, "summary": "One sentence"}'
)


async def extract_from_note(pool, org_id, note_id, entity_id, note_text) -> dict:
    """Extract CRM field updates from a meeting note and store on the note row.

    Updates are stored as suggestions in extracted_fields — never auto-applied to
    the entity record; the advisor confirms via the UI.
    """
    parsed = await call_claude_json(
        _NOTE_SYSTEM, f"Meeting note: {note_text}", max_tokens=500
    )
    if parsed is None:
        # No API key or call failed — mark skipped so the UI doesn't hang.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE entity_notes SET extraction_status = 'skipped', "
                "updated_at = now() WHERE id = $1",
                note_id,
            )
        return {}

    payload = {
        "entity_updates": parsed.get("entity_updates") or {},
        "new_attributes": parsed.get("new_attributes") or {},
        "summary": parsed.get("summary"),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE entity_notes
            SET extracted_fields = $2::jsonb,
                extraction_model = $3,
                extraction_status = 'completed',
                updated_at = now()
            WHERE id = $1
            """,
            note_id, json.dumps(payload), AI_MODEL,
        )
    return payload
