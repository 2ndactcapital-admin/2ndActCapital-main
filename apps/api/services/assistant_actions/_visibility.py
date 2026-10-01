"""Shared member-visibility gate for assistant actions (Sprint hollisfix).

WHY THIS MODULE EXISTS
----------------------
``hollisdiscovery.lowrisk`` found the same defect in three independently
written action modules: scope the query by ``org_id`` and never consult the
caller's own visible-entity set. That is not three bugs, it is one missing
shared primitive — every new read action copy-pasted from ``portfolio.py`` or
``entity_graph.py`` inherited it.

``queries.py`` already had the correct composition, but it lived in a private
``_visible_entity_ids`` helper inside that one module, so nothing else could
reach it. This module is that helper, lifted out, with an entity-level gate
added. Every assistant action that reads or writes entity-scoped data now
routes through here, and nothing re-implements it.

THE ENGINES (unchanged — this module composes, it does not invent)
------------------------------------------------------------------
    is_staff → services.staff_visibility.get_staff_visible_entity_ids
               (assignment + team + hierarchy; super_admin => every org entity)
    member   → services.delegate_grants.get_delegate_visible_entity_ids
               (resolve_entity_set over the member's OWN active grants)
    both then wrapped by services.restricted_access.filter_restricted

Identical to ``services.ownership_tree``, ``services.document_embedding
._visible_entity_ids`` and this package's own ``queries.py``. The refusal
behaviour of :func:`assert_entity_visible` mirrors
``services.ownership_tree.member_tree``, which raises rather than silently
widening when the focal entity is outside the visible set.

KNOWN LIVE CONSEQUENCE, RECORDED DELIBERATELY
---------------------------------------------
``delegate_grants`` had ZERO rows for org 1 at the time of the discovery
sprint. Under the correct engine, every real member's visible set is therefore
EMPTY today, so gated actions will refuse for members until grants exist. That
is the engine reporting the truth, not a regression: ``entities.count`` /
``investments.count`` have behaved this way since they shipped. The previous
behaviour of these actions — returning all 34 org entities to any authenticated
member — was the bug, not the baseline.

CAVEAT ON ``is_staff``
----------------------
``services.permissions.is_staff`` returns True when the token carries NO roles
claim (a single-admin dev-stage default). A tokenless-roles caller therefore
takes the STAFF branch here. That is a platform-wide auth default, out of scope
for this module, but it means this gate is only as strong as the roles claim.
Do not read a pass through this function as proof the caller is really staff.
"""

__all__ = ["visible_entity_ids", "assert_entity_visible", "EntityNotVisible"]


class EntityNotVisible(PermissionError):
    """Raised when a caller asks for an entity outside their visible set.

    Subclasses PermissionError so existing ``except PermissionError`` handlers
    (e.g. the ownership-tree routers) keep working unchanged.
    """


async def visible_entity_ids(pool, org_id: str, user_id: str, is_staff: bool) -> set[str]:
    """Entity ids this caller may see — the SAME engines the rest of the app uses.

    Imported locally (like ``document_embedding._visible_entity_ids``) so this
    module stays importable without eagerly pulling the whole visibility stack.

    Returns a set of ``str`` ids. MAY BE EMPTY — a member with no active grants,
    or a staff user with no assignments, legitimately sees nothing, and every
    caller must then return nothing. Never fall through to org-wide on empty.
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


async def assert_entity_visible(
    pool, org_id: str, user_id: str, is_staff: bool, entity_id: str
) -> None:
    """Refuse unless ``entity_id`` is inside the caller's visible set.

    ``entity_id`` on an assistant action arrives from the LLM tool-call input,
    which the member's own free-text message controls — so it is caller-supplied
    data and must be checked against the caller, never merely against ``org_id``.

    Raises :class:`EntityNotVisible` on a missing or out-of-scope id. The message
    is deliberately identical in both cases: distinguishing "no such entity" from
    "exists but not yours" would leak org membership by probing.
    """
    if not entity_id:
        raise EntityNotVisible("Entity is not within your visible set")
    allowed = await visible_entity_ids(pool, org_id, user_id, is_staff)
    if str(entity_id) not in allowed:
        raise EntityNotVisible("Entity is not within your visible set")
