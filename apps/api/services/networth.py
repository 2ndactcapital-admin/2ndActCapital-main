"""ROLLUP2 — THE net-worth core. Every net-worth figure in the app is computed here, from live positions.

THE BUG (docs/ROLLUP2_DISCOVERY.md, 1a). Two formulas existed:

* the entity Net worth card, its Recalculate and every member figure read ``public.entity_holdings`` — a stored
  snapshot that ``portfolio_rollup`` wrote, and the rollup SKIPPED a valued position with no taxonomy key (there was
  no bucket to put it in, and ``entity_holdings.taxonomy_key`` is NOT NULL). Olivo: $111,970,400 of listed funds
  missing from six cards; the CRUT (two unkeyed ETFs, $11,100,000) had no snapshot row at all → "$0, Holdings not
  yet rolled up";
* the household total (bugfix4 ``household_total``) computed live and counted those lines.

THE RULE, ONE PLACE. For a set of ROOT entities, each entity's share is 1 for a root and, for a non-root, Σ over
its owners of (owner's share × ownership %) capped at 1 — over BOTH ownership tables (``entity_relationships``
ownership + ``entity_ownership``), current on both axes. Each live position (``portfolio_rollup._current_positions``:
one line per owner / asset / account, latest as-of, precedence winners only) is valued by
``portfolio_rollup.position_current_value`` and counted at its owner's share:

* a position WITH a value and NO effective key (``COALESCE(position key, asset default key)``) is IN the gross,
  under the bucket ``UNCLASSIFIED_KEY``, and listed (``unclassified``) — never dropped;
* a position with NO value is NOT counted and is listed (``skipped``) with its reason — never a silent zero.

Liabilities: each live balance at its borrower's share. Net = gross − liabilities.

An entity card is ``figures(ctx, {entity})``; a household is ``figures(ctx, members)``; the stored snapshot the
rollup writes for the sunburst is built from the same lines and the same shares (``portfolio_rollup``), so a card,
its Recalculate, the household's Owners list and the household total agree to the cent for the same as-of.

Runs on the caller's connection, which carries the request's org RLS context (every table read here is org
isolated). Decimal end to end; strings only at the JSON boundary (:func:`render`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
CENTS = Decimal("0.01")
MAX_DEPTH = 25
TOP_N = 10

#: the bucket a valued position with no taxonomy key is counted under (the stored snapshot's key too). Not a
#: taxonomy key of any org: its label is published in the envelope (``vocabularies``), never hardcoded client-side.
UNCLASSIFIED_KEY = "unclassified"
UNCLASSIFIED_LABEL = "Unclassified"
BASIS_LABELS = {"entered_value": "Entered value (as of its statement date)", "valuation": "Valuation",
                "unvalued": "No value — not counted",
                # PRICES1 — quantity × the latest end-of-day close the layer allows for this view (an estimate)
                "price": "End-of-day price (estimate: source and as-of on each line)"}
#: PRICES1 — the purpose every net-worth figure reads prices with. A member sees these figures (cards, the household
#: total, the stored snapshot), so the default is the MEMBER licence: a source whose licence does not allow member
#: display (the free Yahoo feed) never moves a member's number. An adviser-only caller may pass "internal".
DEFAULT_PRICE_PURPOSE = "member_display"
BASIS = ("each live position once, at the share of its owner (the entity itself 100 %); a valued position with no "
         "allocation bucket counts under Unclassified; a position with no value is listed, not counted")

#: ROLLUP2 Task 5 (1f) — how an entity is marked charitable: its entity type, or (a trust) its trust type. The trust
#: type is stored as its LABEL in ``entities.sub_type`` (crm_vocab: trust_type sets_sub_type) — matched through
#: crm_vocab.TRUST_TYPES, never a hardcoded label. Charitable-REMAINDER trusts (CRUT / CRAT) are charitable; a CLAT's
#: remainder returns to the family, so it is not.
CHARITABLE_ENTITY_TYPES = frozenset({"foundation"})
CHARITABLE_TRUST_TYPES = frozenset({"crut", "crat"})

#: ownership edges, current on both axes — BOTH tables (services.ownership_read reads the same two)
EDGES_SQL = """
    SELECT from_entity_id::text AS parent, to_entity_id::text AS child, ownership_pct AS pct
      FROM entity_relationships
     WHERE org_id = $1::uuid AND relationship_type = 'ownership' AND ownership_pct IS NOT NULL
       AND valid_to IS NULL AND system_to IS NULL
    UNION ALL
    SELECT parent_id::text, child_id::text, ownership_pct FROM entity_ownership
     WHERE org_id = $1::uuid AND ownership_pct IS NOT NULL AND valid_to IS NULL AND system_to IS NULL
"""


def q(v: Decimal) -> Decimal:
    return v.quantize(CENTS, rounding=ROUND_HALF_UP)


def money(v: Decimal) -> str:
    return format(q(v), "f")


def shares(roots: set[str], edges: list[tuple[str, str, Decimal]]) -> dict[str, Decimal]:
    """{entity: its share} — roots 1; a non-root Σ owner share × pct / 100 over its owners, capped at 1. Pure.
    Iterates to a fixed point (bounded) so chains of any order resolve; a cycle cannot inflate past 1."""
    parents: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for p, c, pct in edges:
        if p != c:
            parents[c].append((p, Decimal(str(pct)) / Decimal(100)))
    out = {m: ONE for m in roots}
    for _ in range(MAX_DEPTH):
        changed = False
        for child, ps in parents.items():
            if child in roots:
                continue
            v = min(ONE, sum((out.get(p, ZERO) * w for p, w in ps), ZERO))
            if v != out.get(child, ZERO):
                out[child] = v
                changed = True
        if not changed:
            break
    return {k: v for k, v in out.items() if v > ZERO}


@dataclass
class Ctx:
    """Everything the figures need, read ONCE (positions valued once) for a set of roots and what they reach."""

    org_id: str
    as_of: date
    edges: list[tuple[str, str, Decimal]] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)       # valued positions
    skipped: list[dict] = field(default_factory=list)     # positions with no value (reason kept)
    liabilities: list[tuple[str, Decimal]] = field(default_factory=list)
    assets: dict[str, dict] = field(default_factory=dict)
    names: dict[str, dict] = field(default_factory=dict)


async def load_edges(conn, org_id) -> list[tuple[str, str, Decimal]]:
    return [(r["parent"], r["child"], r["pct"]) for r in await conn.fetch(EDGES_SQL, str(org_id))]


async def listed_prices(conn, org_id: str, rows: list, as_of: date, purpose: str) -> dict[str, dict]:
    """PRICES1 — {asset_id: {"price", "provider_key", "provider_name", "as_of", "licence", "label"}} for the live
    units-basis positions of listed securities: the layer's end-of-day close for ``purpose`` (ONE bulk read). A
    custodian winner is not a price (it IS the entered value), and a close OLDER than the position's own as-of never
    replaces it. Nothing → the line keeps bugfix4's entered value."""
    from services import security_data as SD

    units = [r for r in rows if r["ownership_basis"] == "units" and r["quantity"] is not None]
    if not units:
        return {}
    gs = {str(a["id"]): str(a["global_security_id"]) for a in await conn.fetch(
        "SELECT id, global_security_id FROM portfolio.assets WHERE id = ANY($1::uuid[]) AND global_security_id IS NOT "
        "NULL AND valid_to IS NULL AND system_to IS NULL", list({r["asset_id"] for r in units}))}
    if not gs:
        return {}
    got = await SD.get_bulk(conn, org_id, "eod_price", sorted(set(gs.values())), as_of=as_of, purpose=purpose)
    out = {}
    for r in units:
        res = got.get(gs.get(r["asset_id"], ""))
        if res is None or not res.found or res.provider_key == "custodian" or res.as_of < r["as_of_date"]:
            continue
        out[r["asset_id"]] = {"price": res.value, "provider_key": res.provider_key, "provider_name": res.provider_name,
                              "as_of": res.as_of, "licence": res.licence, "label": res.label}
    return out


async def value_lines(conn, org_id: str, as_of: date, owner_ids: list[str] | None, *,
                      price_purpose: str = DEFAULT_PRICE_PURPOSE) -> tuple[list[dict], list[dict]]:
    """(valued, skipped) for the live positions of ``owner_ids`` (None = every owner in the org). PRICES1: a listed
    units position with an allowed, fresher end-of-day close is valued quantity × close (basis "price", with the
    source and as-of); otherwise bugfix4's entered value / the valuation ladder, unchanged."""
    from services.portfolio_rollup import _current_positions, position_current_value

    valued, skipped = [], []
    rows = await _current_positions(conn, str(org_id), as_of, owner_ids)
    px = await listed_prices(conn, str(org_id), rows, as_of, price_purpose)
    for r in rows:
        base = {"position_id": r["position_id"], "owner_entity_id": r["owner_entity_id"], "asset_id": r["asset_id"],
                "as_of_date": r["as_of_date"], "taxonomy_key": r["taxonomy_key"] or None}
        p = px.get(r["asset_id"])
        if p is not None:
            valued.append({**base, "value": Decimal(str(r["quantity"])) * p["price"], "basis": "price",
                           "price": {"value": format(p["price"], "f"), "provider_key": p["provider_key"],
                                     "provider_name": p["provider_name"], "as_of": p["as_of"].isoformat(),
                                     "label": p["label"], "entered_value": None if r["market_value"] is None
                                     else format(r["market_value"], "f")}})
            continue
        value, why = await position_current_value(conn, str(org_id), r, as_of)
        if value is None:
            skipped.append({**base, "reason": why or "no value"})
            continue
        valued.append({**base, "value": value,
                       "basis": "entered_value" if r["market_value"] is not None else "valuation"})
    return valued, skipped


async def load(conn, org_id, roots, *, as_of: date | None = None, price_purpose: str = DEFAULT_PRICE_PURPOSE) -> Ctx:
    """Read the edges, value every live position of every entity the roots reach, and the liabilities."""
    org = str(org_id)
    ctx = Ctx(org_id=org, as_of=as_of or date.today())
    ctx.edges = await load_edges(conn, org)
    reach = list(shares({str(r) for r in roots}, ctx.edges))
    if not reach:
        return ctx
    ctx.lines, ctx.skipped = await value_lines(conn, org, ctx.as_of, reach, price_purpose=price_purpose)
    ctx.liabilities = [(r["o"], r["current_balance"] or ZERO) for r in await conn.fetch(
        "SELECT owner_entity_id::text AS o, current_balance FROM portfolio.liabilities WHERE org_id = $1::uuid "
        "AND owner_entity_id = ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS NULL AND as_of <= $3",
        org, reach, ctx.as_of)]
    asset_ids = list({ln["asset_id"] for ln in ctx.lines + ctx.skipped})
    if asset_ids:
        ctx.assets = {str(a["id"]): dict(a) for a in await conn.fetch(
            """SELECT a.id, COALESCE(g.name, a.name) AS name, a.global_security_id, a.asset_type,
                      (SELECT id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = g.id
                        AND t.id_type = 'ticker' AND t.system_to IS NULL AND t.valid_to IS NULL LIMIT 1) AS ticker
                 FROM portfolio.assets a LEFT JOIN portfolio.securities_global g ON g.id = a.global_security_id
                  AND g.system_to IS NULL AND g.valid_to IS NULL
                WHERE a.id = ANY($1::uuid[]) AND a.system_to IS NULL AND a.valid_to IS NULL""", asset_ids)}
    ctx.names = {str(r["id"]): {"display_name": r["display_name"], "entity_type": r["entity_type"],
                                "sub_type": r["sub_type"]}
                 for r in await conn.fetch(
                     "SELECT id, display_name, entity_type::text AS entity_type, sub_type FROM entities "
                     "WHERE org_id = $1::uuid AND id = ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS NULL",
                     org, reach)}
    return ctx


def _label(ctx: Ctx, asset_id: str) -> str:
    a = ctx.assets.get(asset_id)
    if a is None:
        return asset_id
    return f"{a['name']} ({a['ticker']})" if a.get("ticker") else a["name"]


def figures(ctx: Ctx, roots=None, *, weights: dict[str, Decimal] | None = None) -> dict:
    """The figures (Decimal, unrounded) for ``roots`` — or for explicit per-entity ``weights``. Pure."""
    share = weights if weights is not None else shares({str(r) for r in roots or ()}, ctx.edges)
    gross = unclassified = liab = ZERO
    counted, latest = 0, None
    by_basis: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_bucket: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_holding: dict[str, dict] = {}
    uncl_rows, skipped_rows = [], []
    for ln in ctx.lines:
        w = share.get(ln["owner_entity_id"], ZERO)
        if w == ZERO:
            continue
        amt = ln["value"] * w
        gross += amt
        counted += 1
        by_basis[ln["basis"]] += amt
        by_bucket[ln["taxonomy_key"] or UNCLASSIFIED_KEY] += amt
        if latest is None or ln["as_of_date"] > latest:
            latest = ln["as_of_date"]
        if not ln["taxonomy_key"]:
            unclassified += amt
            uncl_rows.append({"position_id": ln["position_id"], "owner_entity_id": ln["owner_entity_id"],
                              "owner_name": (ctx.names.get(ln["owner_entity_id"]) or {}).get("display_name"),
                              "asset_id": ln["asset_id"], "label": _label(ctx, ln["asset_id"]),
                              "asset_type": (ctx.assets.get(ln["asset_id"]) or {}).get("asset_type"),
                              "value": amt, "share": w, "as_of_date": ln["as_of_date"]})
        a = ctx.assets.get(ln["asset_id"])
        key = str(a["global_security_id"]) if a is not None and a["global_security_id"] else f"asset:{ln['asset_id']}"
        h = by_holding.setdefault(key, {"key": key, "label": _label(ctx, ln["asset_id"]), "value": ZERO,
                                        "positions": 0})
        h["value"] += amt
        h["positions"] += 1
    for s in ctx.skipped:
        w = share.get(s["owner_entity_id"], ZERO)
        if w == ZERO:
            continue
        skipped_rows.append({"position_id": s["position_id"], "owner_entity_id": s["owner_entity_id"],
                             "owner_name": (ctx.names.get(s["owner_entity_id"]) or {}).get("display_name"),
                             "asset_id": s["asset_id"], "label": _label(ctx, s["asset_id"]), "reason": s["reason"]})
    for owner, bal in ctx.liabilities:
        liab += bal * share.get(owner, ZERO)
    return {"gross": gross, "liabilities": liab, "unclassified": unclassified, "counted": counted, "latest": latest,
            "by_basis": dict(by_basis), "by_bucket": dict(by_bucket), "holdings": by_holding,
            "unclassified_rows": uncl_rows, "skipped_rows": skipped_rows, "entities": len(share)}


def render(fig: dict, as_of: date) -> dict:
    """JSON-ready strings. The keys bugfix4's household total published are kept; ROLLUP2 adds the lists."""
    top = sorted(fig["holdings"].values(), key=lambda h: (-h["value"], h["label"]))
    g, li = q(fig["gross"]), q(fig["liabilities"])
    return {"as_of": as_of.isoformat(), "holdings_as_of": fig["latest"].isoformat() if fig["latest"] else None,
            "gross": format(g, "f"), "liabilities": format(li, "f"), "net": format(g - li, "f"), "currency": "USD",
            "positions_counted": fig["counted"], "unvalued_positions": len(fig["skipped_rows"]),
            "unclassified_value": money(fig["unclassified"]),
            "unclassified": [{**r, "value": money(r["value"]), "share": format(r["share"], "f"),
                              "as_of_date": r["as_of_date"].isoformat() if r["as_of_date"] else None}
                             for r in sorted(fig["unclassified_rows"], key=lambda r: (-r["value"], r["label"]))],
            "skipped": fig["skipped_rows"],
            "by_basis": {k: money(v) for k, v in fig["by_basis"].items()},
            "by_bucket": {k: money(v) for k, v in sorted(fig["by_bucket"].items())},
            "top_holdings": [{**h, "value": money(h["value"])} for h in top[:TOP_N]],
            "holdings_total": len(top), "entities_counted": fig["entities"], "basis": BASIS}


def is_charitable(entity: dict | None) -> bool:
    """1f — a foundation (private foundation / DAF / public charity), or a charitable-remainder trust."""
    from services.crm_vocab import TRUST_TYPES

    if not entity:
        return False
    if (entity.get("entity_type") or "") in CHARITABLE_ENTITY_TYPES:
        return True
    if (entity.get("entity_type") or "") == "trust":
        label = (entity.get("sub_type") or "").strip().lower()
        keys = {k for k, lbl in TRUST_TYPES if lbl.strip().lower() == label}
        return bool(keys & CHARITABLE_TRUST_TYPES)
    return False


async def entity(conn, org_id, entity_id, *, as_of: date | None = None) -> dict:
    """One entity's card: its own positions plus its share of everything it owns (look-through)."""
    ctx = await load(conn, org_id, [str(entity_id)], as_of=as_of)
    return {"entity_id": str(entity_id), **render(figures(ctx, {str(entity_id)}), ctx.as_of)}


def vocabularies() -> dict:
    return {"basis_labels": [{"key": k, "label": v} for k, v in BASIS_LABELS.items()],
            "unclassified": {"key": UNCLASSIFIED_KEY, "label": UNCLASSIFIED_LABEL},
            "split_lines": [{"key": "family", "label": "Family net worth"},
                            {"key": "charitable", "label": "Charitable"},
                            {"key": "household", "label": "Household total"}]}
