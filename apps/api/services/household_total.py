"""BUGFIX4 (ROLLUP2: now a thin layer over services.networth) — the household's net worth and top holdings, each position counted ONCE.

THE BUG (Task 1b / 1f). The household card and Recalculate read ``households._sum_holdings(member_ids)`` = Σ each
member's ``entity_holdings``. But a member's buckets already include its look-through share of every entity it owns
(``portfolio_rollup`` writes ancestor buckets), and a household's members are typically BOTH the owners and what they
own (Olivo: Olivo Holdings LP is 30 % / 34.5 % / 10 % × 3 / 5 % / 0.5 % owned by six other members). Summing them
counts the same position once per owning member. And a position with a value but no taxonomy key has no bucket, so
it was never in ``entity_holdings`` at all (the $7,545,600 of listed lines the X-ray did count).

THE RULE. The household's SHARE of an entity: 1 for a member; for a non-member, Σ over its owners of (owner's share ×
ownership %), capped at 1 — so an entity reached through two members is counted once, at the household's real
interest. Gross = Σ over the household's entities of share × each live position's value; liabilities likewise. The
value is ``portfolio_rollup.position_current_value`` — THE valuation function the rollup and the grids use:
the entered market value when there is one ("entered value"), otherwise quantity × the resolved valuation
("valuation"). There is no free end-of-day price source yet (Task 4), so a listed line is valued at its entered
market value as of its as-of — the same basis the X-ray uses. A position with no value at all is COUNTED as unvalued,
never as zero; a valued position with no taxonomy key is IN the total and reported as "not classified".

Runs on the caller's connection under the request's org RLS context. Pure arithmetic in Decimal; strings at the
JSON boundary.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

# ROLLUP2 — the per-position logic now lives in services.networth (THE core: the entity card, its Recalculate, the
# stored snapshot and this household total all read it). The names below stay importable for existing callers.
from services.networth import (  # noqa: F401  (re-exported)
    BASIS_LABELS,
    CENTS,
    EDGES_SQL,
    MAX_DEPTH,
    ONE,
    TOP_N,
    ZERO,
    figures,
    is_charitable,
    load,
    q,
    render,
)
from services import networth as NW


def shares(members: set[str], edges: list[tuple[str, str, Decimal]]) -> dict[str, Decimal]:
    """{entity: the household's share} — members 1; a non-member Σ owner share × pct / 100 over its owners, capped
    at 1 (services.networth.shares — ONE implementation; kept here for the bugfix4 callers)."""
    return NW.shares(members, edges)


def _m(v: Decimal) -> str:
    return NW.money(v)


async def household_members(conn, org_id, household_id) -> list[str]:
    return [str(r["entity_id"]) for r in await conn.fetch(
        "SELECT m.entity_id FROM household_memberships m JOIN households h ON h.id = m.household_id "
        "WHERE m.household_id = $1::uuid AND h.org_id = $2::uuid", str(household_id), str(org_id))]


def _lines(g: Decimal, li: Decimal, uncl: Decimal) -> dict:
    return {"gross": format(g, "f"), "liabilities": format(li, "f"), "net": format(g - li, "f"),
            "unclassified_value": format(uncl, "f")}


def split(ctx, members: set[str]) -> dict:
    """ROLLUP2 Task 5 — Family / Charitable / Household total. Charitable = the household's interest held THROUGH its
    charitable members (services.networth.is_charitable): each entity at min(its household share, its share from the
    charitable members alone). Family = household − charitable, figure by figure on the cent-rounded values, so the
    two always sum EXACTLY to the household total."""
    total_share = shares(members, ctx.edges)
    charitable = sorted(m for m in members if is_charitable(ctx.names.get(m)))
    c_share = shares(set(charitable), ctx.edges) if charitable else {}
    w_c = {e: min(total_share.get(e, ZERO), v) for e, v in c_share.items() if total_share.get(e, ZERO) > ZERO}
    h = figures(ctx, weights=total_share)
    c = figures(ctx, weights=w_c)
    hg, hl, hu = q(h["gross"]), q(h["liabilities"]), q(h["unclassified"])
    cg, cl, cu = q(c["gross"]), q(c["liabilities"]), q(c["unclassified"])
    return {"household": _lines(hg, hl, hu), "charitable": {**_lines(cg, cl, cu), "entities": [
                {"entity_id": e, "display_name": (ctx.names.get(e) or {}).get("display_name")} for e in charitable]},
            "family": _lines(hg - cg, hl - cl, hu - cu),
            "rule": "charitable = foundations and charitable-remainder trusts (CRUT / CRAT), with what they own"}


def owners(ctx, members: set[str]) -> list[dict]:
    """ROLLUP2 (1c) — the household's Owners list: each member's OWN card figure, from the same lines and the same
    rule as its entity card (services.networth.entity), so the list and the card cannot disagree. Every member is
    listed — an entity whose only holdings are unclassified (the CRUT) included."""
    out = []
    for m in sorted(members, key=lambda e: ((ctx.names.get(e) or {}).get("display_name") or "", e)):
        f = figures(ctx, {m})
        g, li = q(f["gross"]), q(f["liabilities"])
        info = ctx.names.get(m) or {}
        out.append({"entity_id": m, "display_name": info.get("display_name"), "entity_type": info.get("entity_type"),
                    "charitable": is_charitable(info), **_lines(g, li, q(f["unclassified"])),
                    "positions_counted": f["counted"], "unvalued_positions": len(f["skipped_rows"])})
    return out


async def total(conn, org_id, member_ids: list[str], *, as_of: date | None = None) -> dict:
    """The household figures for a member set (services.networth), with the family / charitable split and the
    per-owner list."""
    members = {str(m) for m in member_ids}
    ctx = await load(conn, org_id, list(members), as_of=as_of)
    out = render(figures(ctx, members), ctx.as_of)
    out["members"] = len(members)
    out["split"] = split(ctx, members)
    out["owners"] = owners(ctx, members)
    return out


def _empty(as_of: date) -> dict:
    ctx = NW.Ctx(org_id="", as_of=as_of)
    out = render(figures(ctx, set()), as_of)
    return {**out, "members": 0, "split": split(ctx, set()), "owners": []}


def vocabularies() -> dict:
    return NW.vocabularies()
