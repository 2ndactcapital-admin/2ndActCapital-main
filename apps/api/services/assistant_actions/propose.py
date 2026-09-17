"""The universal agent write surface (Sprint actionregistryfix).

At 16 verbs, ``required_permission`` already IS the capability vocabulary —
a separate capability column would just be a second, parallel vocabulary
against a 16-row table. This is the one new verb every agent depends on
instead of a domain-specific write: an agent's output lands as a row in
``agent_proposals`` (services.agent_proposals, built by
agenticmakerchecker.structural) rather than ever touching a domain table
directly. Nothing here executes anything — the real safety gate is on the
REVIEW side (services.agent_proposals.is_eligible_reviewer /
review_proposal, which requires ``review_agent_proposals`` and refuses a
self-check both in Python and via a database CHECK constraint).

Because that downstream gate is what actually matters, this action is
deliberately NOT locked behind a permission of its own (``required_permission
= None``) — restricting who may PROPOSE would just re-litigate access
control that already exists, correctly, on the CHECK/approve path. It still
goes through the standard draft → confirm flow every other write in this
registry uses (WRITE actions are never executed from the LLM loop — see
services.action_registry's module docstring), because inserting the wrong
proposal is a low-stakes, easily-discarded mistake but should still be a
deliberate one, not a silent side effect of a conversation.

``agent_key`` is a real, required column on ``agent_proposals`` with no live
agent-run system yet supplying it (services.agent_proposals docstring: "no
agent-run table exists yet"), so it is exposed as an input here rather than
inferred — whichever caller invokes this (today: a human via the assistant
UI; eventually: a live agent run) states which of the seven agent identities
the proposal is attributed to.
"""
from services.action_registry import AssistantAction, REGISTRY
from services.agent_proposals import AGENT_KEYS, create_proposal


async def _propose_draft(pool, user_id: str, org_id: str,
                          agent_key: str = "", object_type: str = "",
                          payload: dict | None = None, rationale: str = "",
                          **_):
    """Draft handler: validate inputs and return a preview (no DB writes)."""
    errors = []
    if agent_key not in AGENT_KEYS:
        errors.append(f"agent_key must be one of {', '.join(AGENT_KEYS)}")
    if not object_type:
        errors.append("object_type is required")
    if errors:
        return {"error": "; ".join(errors)}

    return {
        "agent_key": agent_key,
        "object_type": object_type,
        "payload": payload or {},
        "rationale": rationale,
    }


async def _propose_confirm(pool, user_id: str, org_id: str,
                            choice_value: str = "confirm",
                            agent_key: str = "", object_type: str = "",
                            payload: dict | None = None, rationale: str = "",
                            **_):
    """Confirm handler: insert the agent_proposals row on choice 'confirm'."""
    if choice_value != "confirm":
        return {"result": None, "render": None, "undo_token": None}

    async with pool.acquire() as conn:
        proposal_id = await create_proposal(
            conn,
            org_id,
            agent_key=agent_key,
            object_type=object_type,
            payload=payload or {},
            proposed_by=user_id,
        )

    return {
        "result": {
            "proposal_id": proposal_id,
            "agent_key": agent_key,
            "object_type": object_type,
            "status": "pending",
        },
        "render": None,
        # No undo path: withdrawing a pending proposal is not a mechanism
        # services.agent_proposals exposes (only the reviewer's
        # approve/reject). See AssistantAction.reversible for why this stays
        # False rather than a hollow undo_token.
        "undo_token": None,
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="propose",
            module="propose",
            description=(
                "Submit a proposal for human review — the universal write "
                "surface for agent output. Nothing is executed; the "
                "proposal is recorded and awaits an eligible reviewer's "
                "approval or rejection."
            ),
            access_type="write",
            required_permission=None,
            tier=2,
            reversible=False,
            render_target="inline",
            handler=_propose_confirm,
            draft_handler=_propose_draft,
            params_schema={
                "type": "object",
                "properties": {
                    "agent_key": {
                        "type": "string",
                        "enum": list(AGENT_KEYS),
                        "description": "Which of the seven named agents this proposal is attributed to.",
                    },
                    "object_type": {
                        "type": "string",
                        "description": "The kind of object being proposed (e.g. 'crm_note', 'document_link').",
                    },
                    "payload": {
                        "type": "object",
                        "description": "The proposed content — shape depends on object_type.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this proposal is being made.",
                    },
                },
                "required": ["agent_key", "object_type", "payload"],
            },
            options=[
                {"key": "confirm", "label": "Submit for review"},
                {"key": "none", "label": "Not now"},
            ],
        )
    )
