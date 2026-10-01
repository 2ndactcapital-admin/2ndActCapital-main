"""Portfolio assistant actions (Sprint 11 + Sprint 21; scoped Sprint hollisfix).

VISIBILITY: both actions below are user-facing data-exposure surfaces. Before
hollisfix they scoped by ``org_id`` alone and ignored the caller entirely —
``show_allocation`` and ``find_my_investment`` returned any org row to any
authenticated member. They now route through the SAME gate every other
entity-scoped surface uses (``services.assistant_actions._visibility``), never
a bespoke or org-only path. Do not add a read here that skips it.
"""
from services.action_registry import AssistantAction, REGISTRY
from services.allocation_lens import aggregate_allocation
from services.assistant_actions._visibility import (
    EntityNotVisible,
    assert_entity_visible,
)


async def _show_allocation(pool, user_id: str, org_id: str, is_staff: bool = True,
                           selector_type: str = "entity", entity_id: str = "", **_):
    """Return allocation lens data and a screen directive to the sunburst page.

    ``entity_id`` comes from the LLM tool-call input, which the member's own
    message controls, so it is gated against the caller's visible set before
    ``aggregate_allocation`` — which takes ``org_id`` only and has no caller
    identity parameter — is ever reached. A ``subtree`` selector is gated on its
    ROOT: ``resolve_entity_set`` walks the look-through from there, and the
    visibility engines resolve subtrees the same way, so a visible root implies
    a visible subtree under the platform's own definition of visible.
    """
    if not entity_id:
        return {"text": "Please specify an entity to view its allocation.", "data": {}}

    try:
        await assert_entity_visible(pool, org_id, user_id, is_staff, entity_id)
    except EntityNotVisible:
        # Same text for "not yours" and "does not exist" — see _visibility.
        return {
            "text": "That entity is not within your visible set, so I can't show its allocation.",
            "data": {},
        }

    selector: dict
    if selector_type == "subtree":
        selector = {"type": "subtree", "root_id": entity_id}
    else:
        selector = {"type": "entity", "id": entity_id}

    try:
        result = await aggregate_allocation(pool, selector, org_id)
    except (ValueError, Exception) as exc:
        return {"text": f"Could not load allocation data: {exc}", "data": {}}

    total = result.get("total_actual_dollar", 0)
    count = result.get("entity_count", 0)
    fmtd = (
        f"${total / 1e9:.2f}B" if total >= 1e9
        else f"${total / 1e6:.2f}M" if total >= 1e6
        else f"${total / 1e3:.0f}K" if total >= 1e3
        else f"${total:.0f}"
    )
    return {
        "data": result,
        "render": {
            "component": "AllocationSunburst",
            "target": "screen",
            "screen_route": "/portfolio/allocation",
            "props": {"selector_type": selector_type, "entity_id": entity_id},
        },
        "text": f"Opening allocation lens — {fmtd} across {count} {'entity' if count == 1 else 'entities'}.",
    }


async def _find_investment(pool, user_id: str, org_id: str, is_staff: bool = True,
                           query: str = "", **_):
    """Find the CALLER'S OWN investments matching the query string.

    SCOPING: ``member_investments.user_id`` is the platform's own member key for
    this table — ``routers/portfolio.py:159,185`` (``WHERE mi.user_id = $1 AND
    mi.org_id = $2``) and ``routers/marketplace.py:198`` both scope on it. The
    action is "find MY investment", so it is scoped to the caller for EVERY
    caller, staff included: a staff member's own investments are still their own.
    Staff who need to look at someone else's book use the portfolio endpoints,
    which apply the staff visibility engine; this action is not that surface.

    SCHEMA: the previous query selected ``mi.status``, ``mi.current_stage``,
    ``mi.committed_amount`` and ``mi.currency``. None of those columns exist —
    the real ones are ``investment_stage`` and ``amount_committed`` (see
    ``MEMBER_INVESTMENT_SELECT`` in routers/marketplace.py and the same names in
    queries.py and routers/portfolio.py). Every call raised
    ``UndefinedColumnError``, caught by the loop's per-tool try/except and handed
    to the model as a literal error string, since the day it shipped. The OUTPUT
    keys are kept exactly as before so AssistantPanel's InvestmentCard props do
    not change shape; only their sources are corrected. ``currency`` has no
    column on this table anywhere in the schema and is reported as None rather
    than invented.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT mi.id,
                   mi.investment_stage,
                   mi.amount_committed,
                   d.name AS deal_name,
                   d.deal_type,
                   d.taxonomy_key,
                   d.deal_status
            FROM member_investments mi
            JOIN deals d ON d.id = mi.deal_id
            WHERE mi.org_id = $1
              AND mi.user_id = $2
              AND (
                $3 = ''
                OR LOWER(d.name) LIKE '%' || LOWER($3) || '%'
                OR LOWER(COALESCE(mi.investment_stage, '')) LIKE '%' || LOWER($3) || '%'
                OR LOWER(COALESCE(d.deal_status::text, '')) LIKE '%' || LOWER($3) || '%'
              )
            ORDER BY mi.created_at DESC
            LIMIT 5
            """,
            org_id,
            user_id,
            query or "",
        )
    investments = [
        {
            "id": str(r["id"]),
            "deal_name": r["deal_name"],
            "deal_type": r["deal_type"],
            "status": r["deal_status"],
            "current_stage": r["investment_stage"],
            "committed_amount": float(r["amount_committed"]) if r["amount_committed"] else None,
            "currency": None,
        }
        for r in rows
    ]
    count = len(investments)
    return {
        "data": {"investments": investments},
        "render": {
            "component": "InvestmentCard",
            "target": "inline",
            "props": {"investments": investments, "query": query},
        },
        "text": (
            f"Found {count} investment{'s' if count != 1 else ''} matching '{query}'."
            if query else f"Found {count} investment{'s' if count != 1 else ''}."
        ),
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="portfolio.show_allocation",
            module="portfolio",
            description="Show the portfolio allocation lens — actual vs target breakdown across all taxonomy levels for a given entity or look-through scope.",
            access_type="read",
            required_permission=None,
            tier=3,
            reversible=False,
            render_target="screen",
            handler=_show_allocation,
            params_schema={
                "type": "object",
                "properties": {
                    "selector_type": {
                        "type": "string",
                        "enum": ["entity", "subtree"],
                        "description": "entity = single entity; subtree = look-through weighted.",
                    },
                    "entity_id": {
                        "type": "string",
                        "description": "UUID of the entity to query.",
                    },
                },
                "required": ["selector_type", "entity_id"],
            },
        )
    )
    REGISTRY.register(
        AssistantAction(
            key="portfolio.find_my_investment",
            module="portfolio",
            description="Find the member's own investments by deal name, stage, or deal status.",
            access_type="read",
            required_permission=None,
            tier=3,
            reversible=False,
            render_target="inline",
            handler=_find_investment,
            params_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term — deal name, investment stage, or deal status keyword.",
                    }
                },
                "required": ["query"],
            },
        )
    )
