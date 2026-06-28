"""CRM assistant actions (Sprint 11)."""
from services.action_registry import AssistantAction, REGISTRY
from services.extraction import call_claude_text

_DRAFT_SYSTEM = (
    "You are a discreet private wealth assistant drafting a CRM note for an advisor. "
    "Write in first-person advisor voice — concise, professional, no hype. "
    "Return only the note text, no labels, no JSON."
)


async def _draft_note_preview(pool, user_id: str, org_id: str,
                               entity_id: str = "", content_hint: str = "", **_):
    """Generate a draft note via Sonnet; returns preview data for proposed_action."""
    from services.extraction import ASSISTANT_MODEL
    draft_text = await call_claude_text(
        system=_DRAFT_SYSTEM,
        messages=[{"role": "user", "content": content_hint or "Draft a general update note."}],
        max_tokens=400,
    )
    return {
        "draft_text": draft_text or content_hint,
        "entity_id": entity_id,
    }


async def _save_note(pool, user_id: str, org_id: str,
                     choice_value: str = "save",
                     entity_id: str = "", draft_text: str = "", **_):
    """Confirm handler: insert entity_notes row on choice 'save'."""
    if choice_value != "save":
        return {"result": None, "render": None, "undo_token": None}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO entity_notes
                (org_id, entity_id, note_text, note_type, extraction_status, created_by)
            VALUES ($1, $2, $3, 'meeting', 'pending', $4)
            RETURNING id, note_text, note_type, extraction_status, created_at
            """,
            org_id, entity_id, draft_text, user_id,
        )
    note = {
        "id": str(row["id"]),
        "note_text": row["note_text"],
        "note_type": row["note_type"],
        "extraction_status": row["extraction_status"],
    }
    return {
        "result": note,
        "render": {
            "component": "NoteDraft",
            "target": "inline",
            "props": {"note": note},
        },
        "undo_token": None,
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="crm.draft_note",
            module="crm",
            description=(
                "Draft a CRM note for a contact or entity. "
                "The member reviews the draft before it is saved."
            ),
            access_type="write",
            required_permission=None,
            default_autonomy="confirm",
            reversible=False,
            render_target="inline",
            handler=_save_note,
            draft_handler=_draft_note_preview,
            params_schema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "UUID of the entity to attach the note to.",
                    },
                    "content_hint": {
                        "type": "string",
                        "description": "Key points or context to include in the note.",
                    },
                },
                "required": ["entity_id", "content_hint"],
            },
            options=[
                {"key": "save", "label": "Save note"},
                {"key": "edit", "label": "Edit before saving"},
                {"key": "none", "label": "Not now — let me think"},
            ],
        )
    )
