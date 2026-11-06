"""BUGFIX4 — the household's net worth and top holdings, each position counted ONCE.

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

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
CENTS = Decimal("0.01")
MAX_DEPTH = 25
TOP_N = 10
BASIS_LABELS = {"entered_value": "Entered value (as of its statement date)", "valuation": "Valuation",
                "unvalued": "No value — not counted"}

#: ownership edges, current on both axes — the two tables the rollup's look-through reads
EDGES_SQL = """
    SELECT from_entity_id::text AS parent, to_entity_id::text AS child, ownership_pct AS pct
      FROM entity_relationships
     WHERE org_id = $1::uuid AND relationship_type = 'ownership' AND ownership_pct IS NOT NULL
       AND valid_to IS NULL AND system_to IS NULL
    UNION ALL
    SELECT parent_id::text, child_id::text, ownership_pct FROM entity_ownership
     WHERE org_id = $1::uuid AND ownership_pct IS NOT NULL AND valid_to IS NULL AND system_to IS NULL
"""


def _m(v: Decimal) -> str:
    return format(v.quantize(CENTS, rounding=ROUND_HALF_UP), "f")


def shares(members: set[str], edges: list[tuple[str, str, Decimal]]) -> dict[str, Decimal]:
    """{entity: the household's share} — members 1; a non-member Σ owner share × pct / 100 over its owners, capped
    at 1. Pure. Iterates to a fixed point (bounded) so chains of any order resolve; a cycle cannot inflate past 1."""
    parents: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for p, c, pct in edges:
        if p != c:
            parents[c].append((p, Decimal(str(pct)) / Decimal(100)))
    out = {m: ONE for m in members}
    for _ in range(MAX_DEPTH):
        changed = False
        for child, ps in parents.items():
            if child in members:
                continue
            v = min(ONE, sum((out.get(p, ZERO) * w for p, w in ps), ZERO))
            if v != out.get(child, ZERO):
                out[child] = v
                changed = True
        if not changed:
            break
    return {k: v for k, v in out.items() if v > ZERO}


async def household_members(conn, org_id, household_id) -> list[str]:
    return [str(r["entity_id"]) for r in await conn.fetch(
        "SELECT m.entity_id FROM household_memberships m JOIN households h ON h.id = m.household_id "
        "WHERE m.household_id = $1::uuid AND h.org_id = $2::uuid", str(household_id), str(org_id))]


async def total(conn, org_id, member_ids: list[str], *, as_of: date | None = None) -> dict:
    """The household figures for a member set (see the module docstring)."""
    from services.portfolio_rollup import _current_positions, position_current_value

    as_of = as_of or date.today()
    members = {str(m) for m in member_ids}
    edges = [(r["parent"], r["child"], r["pct"]) for r in await conn.fetch(EDGES_SQL, str(org_id))]
    share = shares(members, edges)
    if not share:
        return _empty(as_of)
    rows = await _current_positions(conn, str(org_id), as_of, list(share))
    gross, unclassified = ZERO, ZERO
    unvalued, counted, latest = 0, 0, None
    by_basis: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_holding: dict[str, dict] = {}
    asset_ids = list({r["asset_id"] for r in rows})
    labels = {str(a["id"]): a for a in await conn.fetch(
        """SELECT a.id, COALESCE(g.name, a.name) AS name, a.global_security_id,
                  (SELECT id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = g.id
                    AND t.id_type = 'ticker' AND t.system_to IS NULL AND t.valid_to IS NULL LIMIT 1) AS ticker
             FROM portfolio.assets a LEFT JOIN portfolio.securities_global g ON g.id = a.global_security_id
              AND g.system_to IS NULL AND g.valid_to IS NULL
            WHERE a.id = ANY($1::uuid[]) AND a.system_to IS NULL""", asset_ids)} if asset_ids else {}
    for r in rows:
        w = share.get(r["owner_entity_id"], ZERO)
        if w == ZERO:
            continue
        value, _why = await position_current_value(conn, str(org_id), r, as_of)
        if value is None:
            unvalued += 1
            continue
        basis = "entered_value" if r["market_value"] is not None else "valuation"
        amt = value * w
        gross += amt
        counted += 1
        by_basis[basis] += amt
        if not r["taxonomy_key"]:
            unclassified += amt
        if latest is None or r["as_of_date"] > latest:
            latest = r["as_of_date"]
        a = labels.get(r["asset_id"])
        key = str(a["global_security_id"]) if a is not None and a["global_security_id"] else f"asset:{r['asset_id']}"
        h = by_holding.setdefault(key, {"key": key, "label": (f"{a['name']} ({a['ticker']})" if a and a["ticker"]
                                                              else (a["name"] if a else r["asset_id"])),
                                        "value": ZERO, "positions": 0})
        h["value"] += amt
        h["positions"] += 1
    liab = ZERO
    for r in await conn.fetch("SELECT owner_entity_id::text AS o, current_balance FROM portfolio.liabilities "
                              "WHERE org_id = $1::uuid AND owner_entity_id = ANY($2::uuid[]) AND valid_to IS NULL "
                              "AND system_to IS NULL AND as_of <= $3", str(org_id), list(share), as_of):
        liab += (r["current_balance"] or ZERO) * share.get(r["o"], ZERO)
    top = sorted(by_holding.values(), key=lambda h: (-h["value"], h["label"]))
    return {"as_of": as_of.isoformat(), "holdings_as_of": latest.isoformat() if latest else None,
            "gross": _m(gross), "liabilities": _m(liab), "net": _m(gross - liab), "currency": "USD",
            "positions_counted": counted, "unvalued_positions": unvalued, "unclassified_value": _m(unclassified),
            "by_basis": {k: _m(v) for k, v in by_basis.items()},
            "top_holdings": [{**h, "value": _m(h["value"])} for h in top[:TOP_N]],
            "holdings_total": len(top),
            "entities_counted": len(share), "members": len(members),
            "basis": "each live position once, at the household's share of its owner (members 100 %)"}


def _empty(as_of: date) -> dict:
    return {"as_of": as_of.isoformat(), "holdings_as_of": None, "gross": "0.00", "liabilities": "0.00",
            "net": "0.00", "currency": "USD", "positions_counted": 0, "unvalued_positions": 0,
            "unclassified_value": "0.00", "by_basis": {}, "top_holdings": [], "holdings_total": 0,
            "entities_counted": 0, "members": 0,
            "basis": "each live position once, at the household's share of its owner (members 100 %)"}


def vocabularies() -> dict:
    return {"basis_labels": [{"key": k, "label": v} for k, v in BASIS_LABELS.items()]}
