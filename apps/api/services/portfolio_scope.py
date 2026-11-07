"""BUGFIX3 (MUSE-OLIVO-CRIT) — the caller's visible owner set for every portfolio READ, from the ONE shared rule.

THE LEAK. A member (role ``member``, which holds ``view_portfolio``) opened /portfolio/positions and read "Every
holding across the firm": 23 live positions with owner names and values. ``GET /portfolio/positions`` called
``list_positions(org_id=…)`` after a ``view_portfolio`` gate — org-scoped and nothing else. The same was true of the
position detail, the asset picker, the Securities grid, the Transactions grid / detail / picker, the TA projection
and obligation ledger by commitment id, the tax-chase list, the account-link exception list, a household's
precedence override and the UDF record / value / export reads. ``GET /portfolio/commitments`` (BUGFIX1) was the one
already filtering.

THE RULE (never re-implemented per route): ``services.authz.member_entity_scope`` —
  * ``None`` for Super Admin, staff and (STAFFROLES1) any internal firm role on a READ: unscoped; RLS keeps the
    read in the caller's org;
  * otherwise the member's own family: ``document_embedding.visible_entity_ids(…, is_staff=False)`` — delegate
    grants → the resolve_entity_set subtree → the restricted-access filter. An EMPTY set means NOTHING, never "all".
Every scoped service takes ``entity_scope`` and applies it IN SQL (count, page and totals agree), and a detail
outside the scope returns the same ``None`` → 404 as a missing id.

Fail closed: if the visible set cannot be decided (the engine raises), the read raises — it is refused (500), it is
never answered unscoped.
"""

from __future__ import annotations

from fastapi import Request

from services.authz import member_entity_scope

#: The Positions / Transactions page sentence, by scope (published by the API so the page never says "across the
#: firm" to a member — the text is chosen where the scope is decided).
SCOPE_TEXT = {
    "firm": {
        "positions": "Every holding across the firm. Select a row to see its valuation, transactions and source "
                     "documents.",
        "transactions": "Every ledger entry across the firm. Select a row to see its figures, its position and its "
                        "source documents. Entries are corrected, never overwritten.",
    },
    # VISIBILITY1 — a restricted staff caller of an org that enforces staff visibility.
    "assigned": {
        "positions": "Holdings of the families you cover. Select a row to see its valuation, transactions and "
                     "source documents.",
        "transactions": "Ledger entries of the families you cover. Select a row to see its figures, its position "
                        "and its source documents. Entries are corrected, never overwritten.",
    },
    "own_family": {
        "positions": "Your family's holdings. Select a row to see its valuation, transactions and source documents.",
        "transactions": "Your family's ledger entries. Select a row to see its figures, its position and its source "
                        "documents.",
    },
}


async def entity_scope(request: Request, pool) -> list[str] | None:
    """``None`` (unscoped) or the caller's visible entity ids as a sorted list (possibly empty)."""
    scope = await member_entity_scope(request, pool)
    if scope is None:
        return None
    return sorted(str(x) for x in scope)


def scope_block(scope: list[str] | None, screen: str, request: Request | None = None) -> dict:
    """What the page says about whose data it shows. ``screen`` is ``positions`` or ``transactions``."""
    from services.authz import principal_of
    from services.family_visibility import restricted_principal

    kind = "firm" if scope is None else "own_family"
    if scope is not None and request is not None and restricted_principal(principal_of(request)):
        kind = "assigned"
    return {"kind": kind, "description": SCOPE_TEXT[kind][screen]}


__all__ = ["SCOPE_TEXT", "entity_scope", "scope_block"]
