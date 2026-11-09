"""CASH05 — Treasury → HOLDINGS: what the family's liquid balance is made of, per sleeve, with every ladder rung.
Read-only on money (nothing here writes a row).

WHAT IS LIQUID (one rule, shared by the overview, holdings, activity, earnings and documents — docs/cash/
CASH_TREASURY_SCREENS.md): a live position counts when it is cash01's Cash & Equivalents (its effective taxonomy key is
``taxonomy_mc_8_1`` / ``taxonomy_sub_8_1_<n>`` — ``cash_coverage.is_cash_key``) OR its asset is a sleeve instrument the
security master classes (a money-market fund with an N-MFP ``mmf_category``, a Treasury Bill / Note / Bond with a
readable ``maturity_date`` — cash04's classes) OR its asset is a ``kind = cash`` asset (portfolio_cash). Each position
counts ONCE. Values come from THE net-worth core (``networth.load`` / ``figures``, narrowed by ``keep`` to these rows):
the household at its look-through shares, an entity (the entity filter) at 1 — cash01's two figures.

SLEEVES (a held instrument's sleeve, never re-derived): a money-market fund is Near-term; a Treasury by its maturity in
cash02's windows (``cash_policy.windows`` of the scope's ACTIVE policy, else the default boundaries 3 / 12 / 24 months,
said so) through cash04's ``cash_proposals.classify``; a matured Treasury is Near-term ("matured — awaiting proceeds");
cash is its own group; any other Cash & Equivalents line is "Other cash & equivalents".

YIELDS (each holding on its own basis — cash03): a fund's 7-day net yield (SEC Form N-MFP, through
``cash_instruments.read_mmfs`` — the licence rule per caller); a bill's yield to maturity from the account's own
valuation (simple bond-equivalent: (100 − P) ÷ P × 365 ÷ days); a note's auction high yield (Treasury Fiscal Data,
``cash_ladder.read_auctions`` — a note's coupon is not recorded, so its YTM is not derived); cash: "not estimated —
needs the custodian's cash rate". Anything without data reads "not estimated — needs <x>".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from services import cash_coverage as CC
from services import cash_obligations as OB

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
CENT = Decimal("0.01")
BP4 = Decimal("0.0001")
TENTH = Decimal("0.1")
DAYS_PER_YEAR = Decimal(365)
GROUPS = (("cash", "Cash"), ("near_term", "Near-term"), ("reserve", "Reserve"), ("long_term", "Long-term"),
          ("other", "Other cash & equivalents"))
SLEEVE_KEYS = ("near_term", "reserve", "long_term")
DEFAULT_BOUNDARIES = (3, 12, 24)
TREASURY_KIND = {"Bill": "bill", "Note": "note", "Bond": "note"}
KIND_LABELS = (("cash", "Cash"), ("mmf", "Money-market fund"), ("bill", "Treasury bill"), ("note", "Treasury note"),
               ("other", "Cash equivalent"))
MMF_CATEGORY_LABELS = {"treasury": "Treasury", "government": "government", "prime": "prime",
                       "single_state": "single-state municipal", "other_tax_exempt": "tax-exempt municipal"}
CUTOFF_SETTING = "cash.mmf_cutoff_et"
CUTOFF_RX = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
NO_CASH_RATE = "not estimated — needs the custodian's cash rate (the custodian feed does not return it)"
NO_YIELD_SOURCE = "not estimated — no yield source is registered for this holding"
NO_PROSPECTUS = "prospectus: not recorded in the security master"
#: CASH05 — reserved for cash07 (the sweep rate); registered in the disclosure library, NOT shown by any cash05 screen
SWEEP_RATE_KEY = "cash.sweep_rate"
SWEEP_RATE_ESTIMATE = ("Estimate — the sweep rate is the custodian's published rate for the program your cash is "
                       "swept into; it can change at any time and may differ from money-market fund yields.")


class ScopeNotFound(Exception):
    """The household / entity is not one the caller can open (the route answers 404, as for a missing one)."""


def q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def money(v: Decimal | None) -> str | None:
    return None if v is None else format(q(v), "f")


def pct(v: Decimal | None) -> str | None:
    return None if v is None else format(v.quantize(BP4, rounding=ROUND_HALF_UP), "f")


# ═══ Scope (household, or one entity of it) ═════════════════════════════════════════════════════════════════════
@dataclass
class Scope:
    org_id: str
    household_id: str
    members: set
    edges: list
    shares: dict                    # the household's look-through shares (narrowed to a member's own entities)
    entity_id: str | None = None
    weights: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    member_scope: set | None = None

    @property
    def owners(self) -> list[str]:
        return sorted(e for e, w in self.weights.items() if w > ZERO)

    @property
    def label(self) -> str:
        return "The household" if self.entity_id is None else (self.names.get(self.entity_id) or "An entity")


async def resolve_scope(conn, org_id, household_id, *, member_scope: set | None = None, entity_id=None) -> Scope:
    """The household (its members' look-through shares — cash01's household figure) or ONE entity it reaches (that
    entity's own positions at 1 — cash01's entity figure). A member scope narrows exactly as cash01 does."""
    from services import household_total as HT
    from services import networth as NW

    members = set(await HT.household_members(conn, org_id, household_id))
    if member_scope is not None:
        members &= {str(x) for x in member_scope}
    if not members:
        raise ScopeNotFound("household not found")
    edges = await NW.load_edges(conn, str(org_id))
    share = NW.shares(members, edges)
    if member_scope is not None:
        share = {e: w for e, w in share.items() if e in {str(x) for x in member_scope}}
    names = {str(r["id"]): r["display_name"] for r in await conn.fetch(
        "SELECT id, display_name FROM entities WHERE org_id = $1::uuid AND id = ANY($2::uuid[]) AND valid_to IS NULL "
        "AND system_to IS NULL", str(org_id), sorted(share))} if share else {}
    sc = Scope(org_id=str(org_id), household_id=str(household_id), members=members, edges=edges, shares=share,
               names=names, member_scope=None if member_scope is None else {str(x) for x in member_scope})
    if entity_id is not None:
        if str(entity_id) not in share:
            raise ScopeNotFound("entity not found in this household")
        sc.entity_id = str(entity_id)
        sc.weights = {str(entity_id): ONE}
    else:
        sc.weights = dict(share)
    return sc


# ═══ What is liquid ═════════════════════════════════════════════════════════════════════════════════════════════
LIQUID_ASSETS_SQL = """
SELECT DISTINCT a.id::text AS asset_id, a.global_security_id::text AS sid, COALESCE(g.name, a.name) AS name,
       a.asset_type, a.default_taxonomy_key,
       (SELECT t.id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = g.id
         AND t.id_type = 'ticker' AND t.valid_to IS NULL AND t.system_to IS NULL ORDER BY t.id_value LIMIT 1) AS ticker,
       (SELECT t.id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = g.id
         AND t.id_type = 'cusip' AND t.valid_to IS NULL AND t.system_to IS NULL ORDER BY t.id_value LIMIT 1) AS cusip
  FROM portfolio.positions p
  JOIN portfolio.assets a ON a.id = p.asset_id AND a.org_id = p.org_id AND a.valid_to IS NULL AND a.system_to IS NULL
  LEFT JOIN portfolio.securities_global g ON g.id = a.global_security_id AND g.valid_to IS NULL AND g.system_to IS NULL
 WHERE p.org_id = $1::uuid AND p.owner_entity_id = ANY($2::uuid[])
"""
ATTRS_SQL = """
SELECT a.global_security_id::text AS sid, a.field, a.value
  FROM portfolio.security_reference_attributes a
 WHERE a.global_security_id = ANY($1::uuid[])
   AND a.field = ANY(ARRAY['mmf_category', 'treasury_type', 'maturity_date'])
   AND a.valid_to IS NULL AND a.system_to IS NULL
 ORDER BY a.global_security_id, a.field, a.as_of DESC
"""


def asset_kind(asset_type: str | None, attrs: dict) -> tuple[str | None, date | None]:
    """(kind, maturity): 'cash' (a kind = cash asset), 'mmf', 'bill' / 'note' (a Treasury with a readable maturity) or
    None (not a sleeve instrument — it is liquid only through its Cash & Equivalents key). Pure."""
    if (asset_type or "").lower() == "cash":
        return "cash", None
    if attrs.get("mmf_category"):
        return "mmf", None
    tk = TREASURY_KIND.get(attrs.get("treasury_type") or "")
    if tk:
        try:
            return tk, date.fromisoformat(attrs.get("maturity_date") or "")
        except ValueError:
            return None, None      # a Treasury without a readable maturity is not classed (cash04's rule)
    return None, None


async def liquid_assets(conn, org_id, owner_ids) -> dict[str, dict]:
    """{asset_id: info} for every asset the owners have ever held (any position version), with its sleeve class."""
    if not owner_ids:
        return {}
    rows = [dict(r) for r in await conn.fetch(LIQUID_ASSETS_SQL, str(org_id), [str(o) for o in owner_ids])]
    sids = sorted({r["sid"] for r in rows if r["sid"]})
    attrs: dict[str, dict] = {}
    for r in await conn.fetch(ATTRS_SQL, sids) if sids else []:
        attrs.setdefault(r["sid"], {}).setdefault(r["field"], r["value"])
    out = {}
    for r in rows:
        a = attrs.get(r["sid"] or "", {})
        kind, mat = asset_kind(r["asset_type"], a)
        out[r["asset_id"]] = {**r, "kind": kind, "maturity": mat, "mmf_category": a.get("mmf_category"),
                              "treasury_type": a.get("treasury_type")}
    return out


def is_liquid(row, assets: dict) -> bool:
    """THE liquid rule over one ``_current_positions`` row (or any row with ``taxonomy_key`` / ``asset_id``)."""
    if CC.is_cash_key(row["taxonomy_key"]):
        return True
    a = assets.get(str(row["asset_id"]))
    return bool(a and a.get("kind"))


def keep_for(assets: dict):
    return lambda row: is_liquid(row, assets)


# ═══ Current holdings (the core, today) ═════════════════════════════════════════════════════════════════════════
async def core_lines(conn, scope: Scope, assets: dict, as_of: date):
    """The net-worth core's valued liquid lines for the scope on ``as_of`` (+ skipped ones) — ONE core read."""
    from services import networth as NW

    roots = sorted(scope.members) if scope.entity_id is None else [scope.entity_id]
    ctx = await NW.load(conn, scope.org_id, roots, as_of=as_of, keep=keep_for(assets), edges=scope.edges)
    return ctx


def liquid_total(ctx, scope: Scope) -> Decimal:
    """Σ value × weight over the core's (already liquid-only) lines — figures(...)['gross'] of the narrowed ctx."""
    from services import networth as NW

    return NW.figures(ctx, weights=scope.weights)["gross"]


async def position_details(conn, org_id, position_ids: list[str]) -> dict[str, dict]:
    if not position_ids:
        return {}
    return {str(r["id"]): dict(r) for r in await conn.fetch(
        "SELECT p.id, p.quantity, p.market_value, p.cost_basis, p.account_id::text AS account_id, p.as_of_date, "
        "a.account_number_masked FROM portfolio.positions p LEFT JOIN public.accounts a ON a.id = p.account_id "
        "AND a.org_id = p.org_id AND a.valid_to IS NULL AND a.system_to IS NULL "
        "WHERE p.id = ANY($1::uuid[]) AND p.org_id = $2::uuid", position_ids, str(org_id))}


def holdings_from(ctx, scope: Scope, assets: dict, details: dict) -> list[dict]:
    """One holding per security (or per asset when it has none): value Σ line value × weight; quantity Σ quantity ×
    weight; the positions behind it. Pure over its inputs."""
    by: dict[str, dict] = {}
    for ln in ctx.lines:
        w = scope.weights.get(ln["owner_entity_id"], ZERO)
        if w == ZERO:
            continue
        a = assets.get(ln["asset_id"]) or {}
        key = a.get("sid") or f"asset:{ln['asset_id']}"
        d = details.get(ln["position_id"]) or {}
        h = by.setdefault(key, {"key": key, "security_id": a.get("sid"), "asset_id": ln["asset_id"],
                                "name": a.get("name") or "A holding", "ticker": a.get("ticker"),
                                "cusip": a.get("cusip"), "kind": a.get("kind") or "other",
                                "mmf_category": a.get("mmf_category"), "treasury_type": a.get("treasury_type"),
                                "maturity": a.get("maturity"), "value": ZERO, "quantity": ZERO,
                                "raw_value": ZERO, "raw_quantity": ZERO, "positions": [], "as_of": None,
                                "taxonomy_key": ln["taxonomy_key"]})
        v = ln["value"] * w
        h["value"] += v
        qty = d.get("quantity")
        if qty is not None:
            h["quantity"] += Decimal(str(qty)) * w
            h["raw_quantity"] += Decimal(str(qty))
            h["raw_value"] += ln["value"]
        if h["as_of"] is None or ln["as_of_date"] > h["as_of"]:
            h["as_of"] = ln["as_of_date"]
        h["positions"].append({"position_id": ln["position_id"], "owner_entity_id": ln["owner_entity_id"],
                               "owner_name": scope.names.get(ln["owner_entity_id"]), "share": w, "value": v,
                               "account_id": d.get("account_id"), "account": d.get("account_number_masked"),
                               "as_of": ln["as_of_date"], "basis": ln["basis"], "quantity": d.get("quantity"),
                               "cost_basis": d.get("cost_basis")})
    return sorted(by.values(), key=lambda h: (h["maturity"] or date.max, h["name"], h["key"]))


# ═══ Sleeves, yields, wording (pure) ════════════════════════════════════════════════════════════════════════════
def sleeve_windows(fields: dict | None, today: date) -> tuple[dict, str]:
    from services import cash_policy as CP

    if fields:
        n, r, h = fields["near_months"], fields["reserve_months"], fields["horizon_months"]
        basis = f"the active cash policy's boundaries ({n} / {r} months, horizon {h})"
    else:
        n, r, h = DEFAULT_BOUNDARIES
        basis = f"default boundaries ({n} / {r} months, horizon {h}) — no active cash policy"
    return {x["key"]: (x["from"], x["until"]) for x in CP.windows(n, r, h, today)}, basis


def group_of(h: dict, windows: dict, today: date) -> tuple[str, str | None]:
    """(group key, note). Through cash04's classify — one formula."""
    from services import cash_proposals as CPR

    if h["kind"] == "cash":
        return "cash", None
    if h["kind"] == "other":
        return "other", None
    c = CPR.classify({"cash_kind": h["kind"], "maturity": h["maturity"]}, windows, today)
    if c == "mmf":
        return "near_term", None
    if c == "matured":
        return "near_term", "matured — awaiting proceeds"
    return (c, None) if c in SLEEVE_KEYS else ("other", None)


def bill_ytm(price_per_1: Decimal, maturity: date, today: date) -> Decimal | None:
    """Simple bond-equivalent YTM in percent from a price per $1 of par: (1 − p) ÷ p × 365 ÷ days × 100."""
    days = (maturity - today).days
    if days <= 0 or price_per_1 <= ZERO:
        return None
    return ((ONE - price_per_1) / price_per_1 * DAYS_PER_YEAR / Decimal(days) * HUNDRED).quantize(
        BP4, rounding=ROUND_HALF_UP)


def holding_yield(h: dict, today: date, *, mmfs: dict, auctions: dict) -> dict:
    """{estimated, yield_pct, basis, as_of, source} or {estimated: False, text}. Pure."""
    k = h["kind"]
    if k == "mmf":
        f = mmfs.get(h["security_id"] or "")
        if f is None:
            return {"estimated": False, "text": "not estimated — needs the fund's 7-day yield (SEC Form N-MFP)"}
        y = f["yield"]
        if not y.get("estimated"):
            return {"estimated": False, "text": y.get("text") or "not estimated — needs the fund's 7-day yield"}
        return {"estimated": True, "yield_pct": Decimal(str(y["yield_pct"])), "basis": y.get("basis") or
                "7-day net yield (SEC Form N-MFP)", "as_of": y.get("as_of"), "source": "nmfp"}
    if k == "bill":
        if h["maturity"] is not None and h["maturity"] <= today:
            return {"estimated": False, "text": "matured — awaiting proceeds"}
        if not h["raw_quantity"] or h["raw_quantity"] <= ZERO:
            return {"estimated": False, "text": "not estimated — needs the position's par (quantity)"}
        p = h["raw_value"] / h["raw_quantity"]
        y = bill_ytm(p, h["maturity"], today)
        if y is None:
            return {"estimated": False, "text": "not estimated — needs a positive price"}
        return {"estimated": True, "yield_pct": y, "as_of": h["as_of"], "source": "account_valuation",
                "basis": f"yield to maturity from the account's own valuation ({format((p * HUNDRED).quantize(BP4), 'f')}"
                         f" per 100 of par as of {h['as_of']}): (100 − P) ÷ P × 365 ÷ days"}
    if k == "note":
        a = auctions.get(h["cusip"] or "") if h.get("cusip") else None
        if a and a.get("found"):
            return {"estimated": True, "yield_pct": Decimal(str(a["yield_pct"])), "as_of": a.get("as_of"),
                    "source": "treasury_fiscal",
                    "basis": "auction high yield (Treasury Fiscal Data) — the note's coupon is not recorded, so its "
                             "YTM at today's price is not derived"}
        return {"estimated": False, "text": "not estimated — needs the note's auction yield or coupon (Treasury "
                                            "Fiscal Data)"}
    if k == "cash":
        return {"estimated": False, "text": NO_CASH_RATE}
    return {"estimated": False, "text": NO_YIELD_SOURCE}


def liquidity_text(kind: str, *, cutoff: str | None, maturity: date | None) -> str:
    if kind == "mmf":
        if cutoff:
            return f"Same day if instructed by {cutoff} ET"
        return ("Same day if instructed by the fund's cut-off — the cut-off time is not recorded (needs the org "
                f"setting {CUTOFF_SETTING})")
    if kind in ("bill", "note"):
        return (f"At maturity ({maturity.isoformat() if maturity else 'date not recorded'}); an early sale settles the "
                "next business day and may lose principal")
    if kind == "cash":
        return "Available now"
    return "Liquidity not recorded"


def description(h: dict) -> str:
    k = h["kind"]
    if k == "mmf":
        cat = MMF_CATEGORY_LABELS.get(h.get("mmf_category") or "", "")
        return (f"A {cat + ' ' if cat else ''}money-market fund: it aims to keep a stable $1 share price, and shares can "
                "usually be sold the same day.")
    if k == "bill":
        return (f"A U.S. Treasury bill maturing {h['maturity']}: bought below face value and repaid at face value at "
                "maturity.")
    if k == "note":
        return (f"A U.S. Treasury note maturing {h['maturity']}: pays interest twice a year and repays face value at "
                "maturity.")
    if k == "cash":
        return "Cash held at the custodian."
    return "A cash-equivalent holding (classified under Cash & Equivalents)."


def months_covered(value: Decimal, monthly: Decimal | None) -> Decimal | None:
    if monthly is None or monthly <= ZERO:
        return None
    return (value / monthly).quantize(TENTH, rounding=ROUND_HALF_UP)


def ytext(y: dict) -> dict:
    if not y.get("estimated"):
        return {"estimated": False, "text": y.get("text")}
    return {"estimated": True, "yield_pct": pct(y["yield_pct"]), "basis": y.get("basis"),
            "as_of": y["as_of"].isoformat() if hasattr(y.get("as_of"), "isoformat") else y.get("as_of"),
            "source": y.get("source")}


# ═══ Reads ══════════════════════════════════════════════════════════════════════════════════════════════════════
async def cutoff_setting(conn, org_id) -> str | None:
    raw = await conn.fetchval("SELECT setting_value FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
                              str(org_id), CUTOFF_SETTING)
    if raw is None:
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return v if isinstance(v, str) and CUTOFF_RX.match(v) else None


async def scope_fields(conn, org_id, scope: Scope) -> tuple[dict | None, dict | None]:
    """(fields, the active policy row) — the entity's own ACTIVE policy for an entity filter, else the household's."""
    from services import cash_policy as CP

    rows = await CP.live_rows(conn, org_id, household_id=scope.household_id,
                              entity_ids=[scope.entity_id] if scope.entity_id else [])
    act = None
    if scope.entity_id:
        act = next((r for r in rows if r["scope_type"] == "entity" and str(r["entity_id"]) == scope.entity_id
                    and r["status"] == "active"), None)
    if act is None:
        act = next((r for r in rows if r["scope_type"] == "household" and r["status"] == "active"), None)
    return (CP.fields_of(act), act) if act else (None, None)


async def yield_reads(conn, org_id, holdings: list[dict], today: date, *, purpose: str) -> tuple[dict, dict]:
    """(mmfs by security id, auctions by cusip) — cash03's reads, ONE each."""
    from services import cash_instruments as CI
    from services import cash_ladder as CL

    mmfs = {}
    if any(h["kind"] == "mmf" for h in holdings):
        mmfs = {f["security_id"]: f for f in await CI.read_mmfs(conn, org_id, today, purpose=purpose)}
    cusips = sorted({h["cusip"] for h in holdings if h["kind"] == "note" and h.get("cusip")})
    auctions = await CL.read_auctions(conn, org_id, cusips, today, purpose=purpose) if cusips else {}
    return mmfs, auctions


async def current(conn, scope: Scope, today: date, *, purpose: str) -> dict:
    """Everything the overview's yields and the holdings screen share: the classified, valued holdings of today."""
    assets = await liquid_assets(conn, scope.org_id, scope.owners)
    ctx = await core_lines(conn, scope, assets, today)
    details = await position_details(conn, scope.org_id, [ln["position_id"] for ln in ctx.lines])
    # a closed holding (a matured bill restated at zero) is not shown; its value is zero either way
    hs = [h for h in holdings_from(ctx, scope, assets, details) if h["value"] != ZERO or h["quantity"] != ZERO]
    fields, active = await scope_fields(conn, scope.org_id, scope)
    windows, basis = sleeve_windows(fields, today)
    mmfs, auctions = await yield_reads(conn, scope.org_id, hs, today, purpose=purpose)
    for h in hs:
        h["group"], h["note"] = group_of(h, windows, today)
        h["yield"] = holding_yield(h, today, mmfs=mmfs, auctions=auctions)
    total = liquid_total(ctx, scope)
    return {"assets": assets, "ctx": ctx, "holdings": hs, "total": total, "windows": windows,
            "windows_basis": basis, "fields": fields, "active_policy": active,
            "skipped": [{"position_id": s["position_id"], "reason": s["reason"],
                         "label": (assets.get(s["asset_id"]) or {}).get("name")} for s in ctx.skipped
                        if scope.weights.get(s["owner_entity_id"], ZERO) > ZERO]}


async def plan_rungs(conn, scope: Scope, report, today: date, *, purpose: str) -> tuple[list[dict], str | None]:
    """The scope's planned ladder rungs (cash03's derived plan — never stored) with the obligation names each serves.
    A failure never takes the holdings screen down."""
    from services import cash_coverage as CCv
    from services import cash_instruments as CI
    from services import cash_policy as CP

    try:
        rows = await CP.live_rows(conn, scope.org_id, household_id=scope.household_id, entity_ids=list(report.shares))
        want = CP.max_horizon(rows, OB.DEFAULT_HORIZON_MONTHS)
        rep = report if want <= OB.DEFAULT_HORIZON_MONTHS else await CCv.household_report(
            conn, scope.org_id, scope.household_id, today=today, horizon_months=want, member_scope=scope.member_scope)
        plans = await CI.scope_plans(conn, scope.org_id, scope.household_id, rep, today, purpose=purpose)
    except Exception as exc:  # noqa: BLE001
        return [], f"the planned ladder could not be read ({type(exc).__name__})"
    want_scope = ("entity", scope.entity_id) if scope.entity_id else ("household", scope.household_id)
    sp = next((p for p in plans if (p["scope_type"], p["scope_id"]) == want_scope), None)
    if sp is None and scope.entity_id:
        sp = next((p for p in plans if p["scope_type"] == "household"), None)
    if sp is None:
        return [], "no active cash policy — no planned ladder"
    names = {ln["key"]: ln.get("name") for ln in sp["lines"]}
    out = []
    for i, r in enumerate(sp["built"]["ladder"]["rungs"], start=1):
        out.append({"index": i, "maturity": r["maturity"], "kind": r["kind"], "par": r["par"],
                    "security_id": r.get("security_id"), "cusip": r.get("cusip"),
                    "instrument_type": r.get("instrument_type"), "indicative": bool(r.get("indicative")),
                    "obligations": [names.get(k) or k for k in r.get("targets") or []]})
    return out, None


def rungs_view(holdings: list[dict], planned: list[dict], *, member: bool) -> list[dict]:
    """Every rung of the Long-term ladder: each held Treasury (par vs market, YTM with its basis, the obligation it
    serves when a planned rung matches it by security, else by maturity) + each planned rung not held. Pure."""
    used, rows = set(), []
    for h in holdings:
        if h["group"] != "long_term":
            continue
        match = next((p for i, p in enumerate(planned) if i not in used and p["security_id"]
                      and p["security_id"] == h["security_id"]), None)
        if match is None:
            match = next((p for i, p in enumerate(planned) if i not in used and p["maturity"] == h["maturity"]), None)
        if match is not None:
            used.add(planned.index(match))
        rows.append({"held": True, "security_id": h["security_id"], "name": h["name"],
                     "cusip": None if member else h.get("cusip"), "maturity": h["maturity"].isoformat()
                     if h["maturity"] else None, "type": h["kind"], "par": money(h["quantity"]),
                     "market_value": money(h["value"]),
                     "par_vs_market": money(h["value"] - h["quantity"]) if h["quantity"] else None,
                     "ytm": ytext(h["yield"]), "obligations": match["obligations"] if match else [],
                     "serves": ", ".join(match["obligations"]) if match and match["obligations"] else
                     ("a filler rung (monthly)" if match else "no planned rung matches this holding")})
    for i, p in enumerate(planned):
        if i in used:
            continue
        rows.append({"held": False, "security_id": p["security_id"], "name": None,
                     "cusip": None if member else p["cusip"], "maturity": p["maturity"].isoformat(),
                     "type": p["instrument_type"], "par": money(p["par"]), "market_value": None,
                     "par_vs_market": None, "ytm": {"estimated": False, "text": "planned — not held"},
                     "obligations": p["obligations"], "indicative": p["indicative"],
                     "serves": ", ".join(p["obligations"]) if p["obligations"] else "a filler rung (monthly)"})
    rows.sort(key=lambda r: (r["maturity"] or "9999", not r["held"]))
    return rows


def holding_out(h: dict, *, monthly: Decimal | None, cutoff: str | None, member: bool) -> dict:
    return {"key": h["key"], "security_id": h["security_id"], "asset_id": h["asset_id"], "name": h["name"],
            "ticker": h.get("ticker"), "cusip": None if member else h.get("cusip"), "kind": h["kind"],
            "description": description(h), "group": h["group"], "note": h.get("note"),
            "maturity": h["maturity"].isoformat() if h["maturity"] else None,
            "shares": format(h["quantity"].normalize(), "f") if h["quantity"] else None,
            "units": "par (USD face)" if h["kind"] in ("bill", "note") else ("USD" if h["kind"] == "cash" else
                                                                             "shares"),
            "value": money(h["value"]), "as_of": h["as_of"].isoformat() if h["as_of"] else None,
            "yield": ytext(h["yield"]), "liquidity": liquidity_text(h["kind"], cutoff=cutoff, maturity=h["maturity"]),
            "months_covered": None if months_covered(h["value"], monthly) is None
            else format(months_covered(h["value"], monthly), "f"),
            "prospectus": {"href": None, "text": NO_PROSPECTUS},
            "positions": [{"position_id": p["position_id"], "owner_name": p["owner_name"], "account": p["account"],
                           "value": money(p["value"]), "share": format(p["share"], "f"),
                           "as_of": p["as_of"].isoformat()} for p in h["positions"]]}


def build(cur: dict, *, monthly: Decimal | None, cutoff: str | None, planned: list[dict], plan_note: str | None,
          member: bool) -> dict:
    """The holdings screen (pure over the reads)."""
    groups = []
    for key, label in GROUPS:
        hs = [h for h in cur["holdings"] if h["group"] == key]
        if not hs and key not in SLEEVE_KEYS:
            continue
        val = sum((h["value"] for h in hs), ZERO)
        g = {"key": key, "label": label, "value": money(val),
             "months_covered": None if months_covered(val, monthly) is None
             else format(months_covered(val, monthly), "f"),
             "holdings": [holding_out(h, monthly=monthly, cutoff=cutoff, member=member) for h in hs],
             "empty_text": None if hs else f"Nothing is held in the {label} sleeve."}
        if key == "long_term":
            g["rungs"] = rungs_view(cur["holdings"], planned, member=member)
            g["rungs_note"] = plan_note
        groups.append(g)
    return {"groups": groups, "total": money(cur["total"]), "sleeve_basis": cur["windows_basis"],
            "monthly_outflow": money(monthly) if monthly is not None else None,
            "months_basis": "value ÷ the scope's average monthly gross outflow (next 12 months, cash01)",
            "skipped": cur["skipped"]}


def vocabularies() -> dict:
    return {"groups": [{"key": k, "label": v} for k, v in GROUPS],
            "kinds": [{"key": k, "label": v} for k, v in KIND_LABELS], "editable": [], "inline_editable": []}
