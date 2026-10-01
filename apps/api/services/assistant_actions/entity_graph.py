"""Entity hierarchy assistant actions (Sprint 15)."""
from services.action_registry import AssistantAction, REGISTRY
from services.entity_graph import get_subtree, detect_cycle
from services.assistant_actions._visibility import (
    EntityNotVisible,
    assert_entity_visible,
)


async def _show_hierarchy_handler(pool, user_id: str, org_id: str,
                                   is_staff: bool = True,
                                   entity_id: str = "", **_):
    """READ handler: fetch subtree + lookthrough for an entity.

    VISIBILITY (Sprint hollisfix): ``get_subtree`` / ``get_lookthrough`` take
    ``org_id`` + ``entity_id`` and never a caller identity, so before this gate
    every authenticated member could read any entity's full ownership tree by
    supplying its id — and ``entity_id`` is LLM tool-call input, driven by the
    member's own free text. The focal entity is now checked against the caller's
    visible set first, the same refuse-don't-widen contract
    ``services.ownership_tree.member_tree`` enforces for the ownership-tree API.
    """
    if not entity_id:
        return {"text": "Error: entity_id is required.", "data": None, "render": None}

    try:
        await assert_entity_visible(pool, org_id, user_id, is_staff, entity_id)
    except EntityNotVisible:
        # Identical text for "not yours" and "no such entity" — see _visibility.
        return {
            "text": "That entity is not within your visible set, so I can't show its hierarchy.",
            "data": None,
            "render": None,
        }

    from services.entity_graph import get_lookthrough

    subtree = await get_subtree(pool, org_id, entity_id)
    lookthrough = await get_lookthrough(pool, org_id, entity_id)

    entity_display_name = subtree.get("display_name", entity_id)

    return {
        "data": {"tree": subtree, "lookthrough": lookthrough},
        "render": {
            "component": "EntityTree",
            "target": "screen",
            "screen_route": f"/crm/{entity_id}/hierarchy",
            "props": {
                "entity_id": entity_id,
                "tree": subtree,
                "lookthrough": lookthrough,
            },
        },
        "text": f"Showing ownership hierarchy for {entity_display_name}.",
    }


async def _link_ownership_draft(pool, user_id: str, org_id: str,
                                 is_staff: bool = True,
                                 from_entity_id: str = "",
                                 to_entity_id: str = "",
                                 ownership_pct: float = 0.0,
                                 **_):
    """Draft handler: validate cycle and return proposed_action data.

    Gated (Sprint hollisfix) because the preview reads back each endpoint's
    ``display_name`` scoped by ``org_id`` alone — an org-membership oracle for
    any caller who can guess or enumerate entity ids, even before the confirm
    step refuses them.
    """
    try:
        await assert_entity_visible(pool, org_id, user_id, is_staff, from_entity_id)
        await assert_entity_visible(pool, org_id, user_id, is_staff, to_entity_id)
    except EntityNotVisible:
        return {
            "proposed_action": {
                "error": "One or both entities are not within your visible set.",
                "from_entity_id": from_entity_id,
                "to_entity_id": to_entity_id,
                "ownership_pct": float(ownership_pct),
            }
        }

    async with pool.acquire() as conn:
        from_row = await conn.fetchrow(
            """
            SELECT display_name FROM entities
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            from_entity_id,
            org_id,
        )
        to_row = await conn.fetchrow(
            """
            SELECT display_name FROM entities
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            to_entity_id,
            org_id,
        )

    from_name = from_row["display_name"] if from_row else from_entity_id
    to_name = to_row["display_name"] if to_row else to_entity_id

    has_cycle = await detect_cycle(pool, org_id, from_entity_id, to_entity_id)
    if has_cycle:
        return {
            "proposed_action": {
                "error": (
                    f"Cannot link: adding {from_name} -> {to_name} would create "
                    "a cycle in the ownership graph."
                ),
                "from_name": from_name,
                "to_name": to_name,
                "from_entity_id": from_entity_id,
                "to_entity_id": to_entity_id,
                "ownership_pct": float(ownership_pct),
            }
        }

    return {
        "proposed_action": {
            "from_name": from_name,
            "to_name": to_name,
            "from_entity_id": from_entity_id,
            "to_entity_id": to_entity_id,
            "ownership_pct": float(ownership_pct),
        }
    }


async def _link_ownership_handler(pool, user_id: str, org_id: str,
                                   is_staff: bool = True,
                                   choice_value: str = "confirm",
                                   from_entity_id: str = "",
                                   to_entity_id: str = "",
                                   ownership_pct: float = 0.0,
                                   **_):
    """Confirm handler: insert entity_relationships row on choice 'confirm'.

    VISIBILITY (Sprint hollisfix): ``manage_deals`` says the caller may mutate
    ownership SOMEWHERE; it does not say WHICH entities. Both endpoints of the
    edge arrive in the client-supplied ``proposed_action.params``, so both are
    gated against the caller's own visible set before the insert. A staff user
    with no assignments has an empty visible set and is refused here — that is
    the staff visibility engine working, not a regression.
    """
    if choice_value == "cancel":
        return {"result": None, "render": None, "undo_token": None}

    if choice_value in ("confirm", None):
        await assert_entity_visible(pool, org_id, user_id, is_staff, from_entity_id)
        await assert_entity_visible(pool, org_id, user_id, is_staff, to_entity_id)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id,
                     relationship_type, ownership_pct, created_by)
                VALUES ($1, $2, $3, 'ownership', $4, $5)
                RETURNING id
                """,
                org_id,
                from_entity_id,
                to_entity_id,
                float(ownership_pct),
                user_id,
            )
        new_id = str(row["id"])
        undo_token = {"relationship_id": new_id, "action": "soft_delete"}
        return {
            "result": {"relationship_id": new_id},
            "render": None,
            "undo_token": undo_token,
        }

    return {"result": None, "render": None, "undo_token": None}


async def _link_ownership_undo(pool, user_id: str, org_id: str,
                                undo_token: dict, **_):
    """Undo handler: soft-delete the relationship row."""
    relationship_id = undo_token["relationship_id"]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE entity_relationships
            SET valid_to = now(), system_to = now()
            WHERE id = $1
            """,
            relationship_id,
        )


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="entity_graph.show_hierarchy",
            # module collapses entities/entity/entity_graph into one domain —
            # module is write-only metadata (upserted into the catalog and
            # read by nothing else, confirmed by grep); the action_key is
            # untouched, so no workflow_steps or fixture reference breaks.
            module="entity",
            description=(
                "Show the ownership hierarchy for an entity, including its "
                "full subtree and lookthrough effective percentages."
            ),
            access_type="read",
            required_permission=None,
            tier=3,
            reversible=False,
            render_target="screen",
            handler=_show_hierarchy_handler,
            params_schema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "UUID of the entity to show hierarchy for",
                    },
                },
                "required": ["entity_id"],
            },
        )
    )

    REGISTRY.register(
        AssistantAction(
            key="entity.link_ownership",
            module="entity",
            description=(
                "Link two entities with an ownership relationship, "
                "specifying the ownership percentage."
            ),
            access_type="write",
            # "staff" was a ROLE (services.permissions.STAFF_ROLES), not a row
            # in the `permissions` table rbac.get_user_permissions actually
            # returns — so this gate silently denied EVERY caller, forever.
            # manage_deals is this registry's existing de facto staff gate
            # (already used for spv.show_captable/show_ledger/
            # record_transaction) and, of the real seeded permissions, its
            # role grant set (admin, investment_staff, super_admin) is the
            # closest match to STAFF_ROLES for org 1 — reused rather than
            # inventing a new permission for one action.
            required_permission="manage_deals",
            tier=1,
            reversible=True,
            render_target="inline",
            handler=_link_ownership_handler,
            draft_handler=_link_ownership_draft,
            params_schema={
                "type": "object",
                "properties": {
                    "from_entity_id": {
                        "type": "string",
                        "description": "UUID of the owning entity",
                    },
                    "to_entity_id": {
                        "type": "string",
                        "description": "UUID of the owned entity",
                    },
                    "ownership_pct": {
                        "type": "number",
                        "description": "Ownership percentage (0-100)",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
                "required": ["from_entity_id", "to_entity_id", "ownership_pct"],
            },
            options=[
                {"value": "confirm", "label": "Link: {from} owns {pct}% of {to}"},
                {"value": "edit", "label": "Change the percentage"},
                {"value": "cancel", "label": "Not now"},
            ],
        )
    )
