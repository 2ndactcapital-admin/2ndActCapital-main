"""Aggregate / attribute-filter assistant actions (Sprint assistantquery).

Closes the two confirmed gaps Joe hit — "how many entities reside in CT" and
"how many investments are there" — with the assistant's FIRST count/filter READ
actions. Every other assistant action to date is a single-lookup or a write;
there was no way to answer an aggregate-count-across-a-collection question.

VISIBILITY (the whole point of this module): these are user-facing data-exposure
surfaces, so both actions route through the SAME visibility engines every other
part of the platform uses — NEVER a bespoke or org-only path. The composition
below is identical to ``services.ownership_tree`` and
``services.document_embedding._visible_entity_ids``:

    is_staff → services.staff_visibility.get_staff_visible_entity_ids
               (assignment + team + hierarchy; super_admin ⇒ every org entity)
    member   → services.delegate_grants.get_delegate_visible_entity_ids
               (resolve_entity_set over the member's OWN active grants)
    both then wrapped by services.restricted_access.filter_restricted

The caller's ``is_staff`` flag is resolved by ``services.permissions.is_staff``
in the router (same source ``routers.semantic_search`` uses) and threaded into
the handler — never trusted from a request body. ``org_id`` likewise comes from
the JWT, never the tool input.
"""
from services.action_registry import AssistantAction, REGISTRY

# A small cap on the sample list returned alongside the count — the ANSWER is
# the count; the sample is just so the UI can show a few representative rows.
_SAMPLE_LIMIT = 25


async def _visible_entity_ids(pool, org_id: str, user_id: str, is_staff: bool) -> set:
    """Entity ids the caller may see — the SAME engines the rest of the app uses.

    Imported locally (like ``document_embedding._visible_entity_ids``) so this
    module stays importable without eagerly pulling the whole visibility stack.
    Returns a set of ``str`` ids. May be EMPTY — a member with no active grants,
    or a staff user with no assignments, legitimately sees nothing, and the
    counts below must then be zero (never a silent fall-through to org-wide).
    """
    from services.delegate_grants import get_delegate_visible_entity_ids
    from services.restricted_access import filter_restricted
    from services.staff_visibility import get_staff_visible_entity_ids

    if is_staff:
        allowed = await get_staff_visible_entity_ids(pool, user_id, org_id)
    else:
        allowed = await get_delegate_visible_entity_ids(pool, org_id, user_id)
    allowed = await filter_restricted(pool, allowed, user_id, org_id)
    return {str(x) for x in allowed}


# ---------------------------------------------------------------------------
# entities.count — "how many entities reside in CT"
# ---------------------------------------------------------------------------
async def _count_entities(
    pool, user_id: str, org_id: str, is_staff: bool = True,
    state: str = "", entity_type: str = "", **_,
):
    """Count (and sample) the caller's VISIBLE entities, optionally filtered by
    US state / CA province and/or entity type.

    The state filter matches ``entity_addresses.state`` OR ``region_code``
    case-insensitively (an entity counts if it has ANY active address in that
    state). Active entities only, matching the ``GET /entities`` list default.
    """
    allowed = await _visible_entity_ids(pool, org_id, user_id, is_staff)
    state = (state or "").strip()
    entity_type = (entity_type or "").strip()

    if not allowed:
        return _entities_result(0, [], state, entity_type)

    conditions = [
        "e.org_id = $1",
        "e.valid_to IS NULL",
        "e.system_to IS NULL",
        "e.is_active = true",
        "e.id = ANY($2::uuid[])",
    ]
    params: list = [org_id, list(allowed)]

    if entity_type:
        params.append(entity_type)
        conditions.append(f"e.entity_type::text = ${len(params)}")
    if state:
        params.append(state)
        pos = len(params)
        conditions.append(
            f"""EXISTS (
                SELECT 1 FROM entity_addresses a
                WHERE a.entity_id = e.id
                  AND a.org_id = e.org_id
                  AND a.valid_to IS NULL AND a.system_to IS NULL
                  AND (
                        UPPER(TRIM(a.state)) = UPPER(${pos})
                     OR UPPER(TRIM(a.region_code)) = UPPER(${pos})
                  )
            )"""
        )

    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            f"SELECT COUNT(*) FROM entities e WHERE {where}", *params
        )
        rows = await conn.fetch(
            f"SELECT e.id, e.display_name, e.entity_type FROM entities e "
            f"WHERE {where} ORDER BY e.display_name ASC LIMIT {_SAMPLE_LIMIT}",
            *params,
        )

    sample = [
        {"id": str(r["id"]), "display_name": r["display_name"],
         "entity_type": r["entity_type"]}
        for r in rows
    ]
    return _entities_result(int(count or 0), sample, state, entity_type)


def _entities_result(count: int, sample: list, state: str, entity_type: str):
    filters = {}
    if state:
        filters["state"] = state
    if entity_type:
        filters["entity_type"] = entity_type
    where_txt = ""
    if state:
        where_txt += f" in {state}"
    if entity_type:
        where_txt += f" of type {entity_type}"
    return {
        "data": {"count": count, "entities": sample, "filters": filters},
        "render": {
            "component": "EntityCount",
            "target": "inline",
            "props": {"count": count, "entities": sample, "filters": filters},
        },
        "text": (
            f"{count} entit{'y' if count == 1 else 'ies'}{where_txt} "
            "within your visible set."
        ),
    }


# ---------------------------------------------------------------------------
# investments.count — "how many investments are there"
# ---------------------------------------------------------------------------
async def _count_investments(
    pool, user_id: str, org_id: str, is_staff: bool = True,
    stage: str = "", deal_status: str = "", **_,
):
    """Count (and sample) the caller's VISIBLE member investments, optionally
    filtered by investment stage and/or the underlying deal's status.

    Visibility mirrors semantic search exactly: an investment is counted only if
    its ``entity_id`` is in the caller's visible set. An investment with NO
    entity is org-general — counted for staff, never for a member.
    """
    allowed = await _visible_entity_ids(pool, org_id, user_id, is_staff)
    stage = (stage or "").strip()
    deal_status = (deal_status or "").strip()

    conditions = [
        "mi.org_id = $1",
        "mi.valid_to IS NULL",
        "mi.system_to IS NULL",
    ]
    params: list = [org_id, list(allowed)]
    if is_staff:
        conditions.append("(mi.entity_id = ANY($2::uuid[]) OR mi.entity_id IS NULL)")
    else:
        conditions.append("mi.entity_id = ANY($2::uuid[])")

    if stage:
        params.append(stage)
        conditions.append(f"mi.investment_stage = ${len(params)}")
    if deal_status:
        params.append(deal_status)
        conditions.append(f"d.deal_status::text = ${len(params)}")

    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            f"SELECT COUNT(*) FROM member_investments mi "
            f"LEFT JOIN deals d ON d.id = mi.deal_id WHERE {where}",
            *params,
        )
        rows = await conn.fetch(
            f"SELECT mi.id, mi.investment_stage, mi.amount_committed, "
            f"       d.name AS deal_name, d.deal_status "
            f"FROM member_investments mi "
            f"LEFT JOIN deals d ON d.id = mi.deal_id "
            f"WHERE {where} ORDER BY mi.created_at DESC LIMIT {_SAMPLE_LIMIT}",
            *params,
        )

    sample = [
        {
            "id": str(r["id"]),
            "deal_name": r["deal_name"],
            "investment_stage": r["investment_stage"],
            "deal_status": r["deal_status"],
            "amount_committed": float(r["amount_committed"]) if r["amount_committed"] else None,
        }
        for r in rows
    ]

    filters = {}
    if stage:
        filters["stage"] = stage
    if deal_status:
        filters["deal_status"] = deal_status
    where_txt = ""
    if stage:
        where_txt += f" at stage {stage}"
    if deal_status:
        where_txt += f" on {deal_status} deals"
    n = int(count or 0)
    return {
        "data": {"count": n, "investments": sample, "filters": filters},
        "render": {
            "component": "InvestmentCount",
            "target": "inline",
            "props": {"count": n, "investments": sample, "filters": filters},
        },
        "text": (
            f"{n} investment{'' if n == 1 else 's'}{where_txt} "
            "within your visible set."
        ),
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="entities.count",
            module="queries",
            description=(
                "Count how many entities match a filter, and return a short "
                "sample. Use this for aggregate questions like 'how many "
                "entities are there', 'how many entities are in Connecticut', "
                "or 'how many trusts do we have'. Optionally filter by US state / "
                "province code (e.g. CT) and/or entity type. Always scoped to the "
                "entities the caller is permitted to see."
            ),
            access_type="read",
            required_permission=None,
            default_autonomy="auto",
            reversible=False,
            render_target="inline",
            handler=_count_entities,
            params_schema={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": (
                            "Optional US state or CA province code / name to "
                            "filter by (e.g. 'CT'). Matches the address state or "
                            "region code. Omit to count across all locations."
                        ),
                    },
                    "entity_type": {
                        "type": "string",
                        "description": (
                            "Optional entity type to filter by (e.g. "
                            "'individual', 'llc', 'trust'). Omit for all types."
                        ),
                    },
                },
                "required": [],
            },
        )
    )

    REGISTRY.register(
        AssistantAction(
            key="investments.count",
            module="queries",
            description=(
                "Count how many member investments match a filter, and return a "
                "short sample. Use this for aggregate questions like 'how many "
                "investments are there' or 'how many investments are funded'. "
                "Optionally filter by investment stage and/or the underlying "
                "deal's status. Always scoped to the investments the caller is "
                "permitted to see."
            ),
            access_type="read",
            required_permission=None,
            default_autonomy="auto",
            reversible=False,
            render_target="inline",
            handler=_count_investments,
            params_schema={
                "type": "object",
                "properties": {
                    "stage": {
                        "type": "string",
                        "description": (
                            "Optional investment stage to filter by (e.g. "
                            "'interest_indicated', 'funded'). Omit for all stages."
                        ),
                    },
                    "deal_status": {
                        "type": "string",
                        "description": (
                            "Optional underlying deal status to filter by (e.g. "
                            "'active', 'under_review'). Omit for all statuses."
                        ),
                    },
                },
                "required": [],
            },
        )
    )
