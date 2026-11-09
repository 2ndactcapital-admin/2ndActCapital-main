"""CASH01 — cash coverage: how far the liquid cash covers the obligations, per ENTITY and per HOUSEHOLD, with flags.

FORMULAS (docs/cash/CASH_OBLIGATIONS.md; pure functions below, Decimal, deterministic):

* LIQUID BALANCE = the Cash & Equivalents buckets of the NET-WORTH CORE (services.networth — never a second formula):
  every bucket keyed ``taxonomy_mc_8_1`` or ``taxonomy_sub_8_1_<n>`` (cash, money-market, T-bill funds).
    - an ENTITY's liquid balance is its OWN cash (``networth.figures(ctx, weights={entity: 1})``): the payer pays from
      its own accounts, so a trust is not covered by cash an LLC it owns holds;
    - a HOUSEHOLD's is the household figure (``networth.figures(ctx, members)``: look-through shares, each position
      once) — the same number as the household total's Cash & Equivalents.
  An approved securities-backed line's UNDRAWN amount is shown separately (``lines_of_credit``), never added.
* MONTHLY GROSS OUTFLOW = Σ counted obligations dated in the next 12 months ÷ 12.
* MONTHS OF COVERAGE = liquid ÷ monthly outflow (one decimal; None when there is no outflow — "no obligations").
* COVERED THROUGH = walk the dated schedule forward (date, then key), drawing down the liquid balance; the first
  obligation the balance cannot pay in full is the NEXT GAP; covered through = the day before it (no gap → the end of
  the horizon, "covered beyond"). A household walks its total over every reached entity's obligations at the
  household's share of that entity.
* FLAGS: ``below_minimum`` (months < the entity's / household's minimum — the ``min_coverage_months`` of its
  ACTIVE cash policy (CASH02, services.cash_policy), default 6), ``uncovered_90d`` (the next gap
  falls within 90 days), ``no_payer_cash`` (obligations but no cash position at all).

Read-only on money: nothing here moves, instructs or reserves anything (cash02+).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from services import cash_obligations as OB

ZERO = Decimal(0)
ONE = Decimal(1)
TENTH = Decimal("0.1")
DEFAULT_MIN_COVERAGE_MONTHS = Decimal(6)
OUTFLOW_WINDOW_MONTHS = 12
UNCOVERED_WINDOW_DAYS = 90
CASH_MAJOR_KEY = "taxonomy_mc_8_1"
CASH_SUB_PREFIX = "taxonomy_sub_8_1_"
#: the disclosure-library key every member-facing coverage figure carries (services.disclosures)
ESTIMATE_KEY = "cash.coverage_estimate"
#: the built-in text (the library's fallback until a firm publishes its own)
COVERAGE_ESTIMATE = ("Estimate — coverage is projected from recorded and estimated obligations and today's cash; "
                     "projected capital calls, interest and payouts will differ. Have counsel review before relying "
                     "on it.")
FLAG_LABELS = (("below_minimum", "Below its minimum coverage"),
               ("uncovered_90d", "An obligation is uncovered within 90 days"),
               ("no_payer_cash", "Obligations with no cash to pay them"))

TODO_SOURCE = {"below_minimum": "cash_below_minimum", "uncovered_90d": "cash_uncovered_90d",
               "unmatched_notice": "cash_unmatched_notice"}
#: the permission that authors obligations — reused from the portfolio set (services.portfolio_assets)
WRITE_PERMISSION = "manage_portfolio"
READ_PERMISSION = "view_portfolio"


def is_cash_key(key: str | None) -> bool:
    """Rule 4 key patterns: the Cash & Equivalents major class or one of its sub-categories — exactly."""
    return bool(key) and (key == CASH_MAJOR_KEY or (key.startswith(CASH_SUB_PREFIX)
                                                    and key[len(CASH_SUB_PREFIX):].isdigit()))


def liquid_from_buckets(by_bucket: dict) -> Decimal:
    return sum((v for k, v in by_bucket.items() if is_cash_key(k)), ZERO)


def monthly_outflow(lines, today: date) -> Decimal:
    until = OB.add_months(today, OUTFLOW_WINDOW_MONTHS)
    total = sum((ln["amount"] for ln in lines if today <= ln["date"] < until), ZERO)
    return total / Decimal(OUTFLOW_WINDOW_MONTHS)


def months_of_coverage(liquid: Decimal, monthly: Decimal) -> Decimal | None:
    if monthly <= 0:
        return None
    return (liquid / monthly).quantize(TENTH, rounding=ROUND_HALF_UP)


def walk(liquid: Decimal, lines, until: date) -> dict:
    """The forward walk. ``lines``: [{date, amount, key, …}] (counted only). Pure."""
    bal = liquid
    for ln in sorted(lines, key=lambda x: (x["date"], x["key"])):
        if ln["amount"] > bal:
            return {"covered_through": ln["date"] - timedelta(days=1), "covered_beyond_horizon": False,
                    "next_gap": ln, "shortfall": ln["amount"] - max(bal, ZERO), "balance_before_gap": bal}
        bal -= ln["amount"]
    return {"covered_through": until, "covered_beyond_horizon": True, "next_gap": None, "shortfall": ZERO,
            "balance_before_gap": bal}


def coverage(liquid: Decimal | None, lines, *, today: date, until: date, min_months: Decimal) -> dict:
    """One entity's / household's figures + flags. ``liquid`` None = no cash position at all. Pure."""
    counted = [ln for ln in lines if ln.get("counted", True)]
    base = liquid if liquid is not None else ZERO
    monthly = monthly_outflow(counted, today)
    months = months_of_coverage(base, monthly)
    w = walk(base, counted, until)
    flags = []
    if months is not None and months < min_months:
        flags.append("below_minimum")
    if w["next_gap"] is not None and w["next_gap"]["date"] <= today + timedelta(days=UNCOVERED_WINDOW_DAYS):
        flags.append("uncovered_90d")
    if counted and (liquid is None or liquid <= 0):
        flags.append("no_payer_cash")
    return {"liquid": liquid, "monthly_outflow": monthly, "months": months, "min_months": min_months,
            "outflow_12m": sum((ln["amount"] for ln in counted if today <= ln["date"] < OB.add_months(
                today, OUTFLOW_WINDOW_MONTHS)), ZERO),
            "obligations": len(counted), **w, "flags": flags}


def _m(v) -> str | None:
    return OB.money_text(v) if v is not None else None


def render(c: dict, names: dict) -> dict:
    gap = c["next_gap"]
    return {"liquid": _m(c["liquid"]), "has_cash": c["liquid"] is not None,
            "monthly_outflow": _m(c["monthly_outflow"]), "outflow_12m": _m(c["outflow_12m"]),
            "months": None if c["months"] is None else format(c["months"], "f"),
            "months_text": "no obligations in the next 12 months" if c["months"] is None else None,
            "min_months": format(c["min_months"].normalize(), "f"),
            "covered_through": c["covered_through"].isoformat(), "covered_beyond_horizon": c["covered_beyond_horizon"],
            "next_gap": None if gap is None else {
                "name": gap["name"], "amount": _m(gap["amount"]), "date": gap["date"].isoformat(),
                "payer_entity_id": gap["payer_entity_id"], "payer_name": names.get(gap["payer_entity_id"]),
                "source_type": gap["source_type"], "certainty": gap["certainty"], "key": gap["key"],
                "shortfall": _m(c["shortfall"])},
            "obligations": c["obligations"], "flags": list(c["flags"])}


# ═══ Minimums — ONE home: the scope's ACTIVE cash policy (CASH02; cash01's cash_coverage_minimums is retired) ═════
async def minimums(conn, org_id, *, entity_ids=(), household_id=None) -> dict:
    """{entity_id | "household:<id>": min_coverage_months} from the ACTIVE ``cash_policies`` of the household and of
    every named entity (an entity override). No active policy → absent (the caller uses the default 6). A draft or a
    policy awaiting approval never moves a minimum — only an approved (active) one does."""
    rows = await conn.fetch(
        "SELECT scope_type, entity_id::text AS e, household_id::text AS h, min_coverage_months FROM cash_policies "
        "WHERE org_id = $1::uuid AND status = 'active' AND system_to IS NULL AND (entity_id = ANY($2::uuid[]) OR "
        "household_id = $3::uuid)", str(org_id), [UUID(str(e)) for e in entity_ids],
        str(household_id) if household_id else None)
    out = {}
    for r in rows:
        out[r["e"] if r["scope_type"] == "entity" else f"household:{r['h']}"] = r["min_coverage_months"]
    return out


# ═══ The read ══════════════════════════════════════════════════════════════════════════════════════════════════
@dataclass
class Report:
    household: dict
    entities: list
    lines: list
    not_estimated: list
    lines_of_credit: list
    unmatched_notices: list
    today: date
    until: date
    names: dict
    #: CASH02 — exposed so the cash policy sizes its sleeves on exactly these figures (never a second formula):
    #: each reached entity's household share, the household's liquid (None = no cash position) and each entity's own
    shares: dict = field(default_factory=dict)
    household_liquid: Decimal | None = None
    entity_liquid: dict = field(default_factory=dict)


def _line_dict(ln: OB.Line, w: Decimal = ONE) -> dict:
    return {"key": ln.key, "date": ln.date, "amount": ln.amount * w, "name": ln.name,
            "payer_entity_id": ln.payer_entity_id, "source_type": ln.source_type, "certainty": ln.certainty,
            "counted": ln.counted}


async def household_report(conn, org_id, household_id, *, today: date, member_scope: set | None = None,
                           horizon_months: int = OB.DEFAULT_HORIZON_MONTHS,
                           extra_stored: list[dict] | None = None) -> Report:
    """Coverage for one household (and each entity it reaches). ``member_scope`` (a member / restricted caller's
    visible entity set) narrows the members — never another family's record."""
    from services import household_total as HT
    from services import networth as NW

    members = set(await HT.household_members(conn, org_id, household_id))
    if member_scope is not None:
        members &= {str(x) for x in member_scope}
    ctx = await NW.load(conn, org_id, sorted(members), as_of=today)
    share = NW.shares(members, ctx.edges)
    if member_scope is not None:
        share = {e: w for e, w in share.items() if e in {str(x) for x in member_scope}}
    ents = sorted(share)
    own = {e: NW.figures(ctx, weights={e: ONE}) for e in ents}
    values = {e: f["gross"] for e, f in own.items()}
    sched = await OB.schedule(conn, org_id, ents, today=today, horizon_months=horizon_months, names=ctx.names,
                              values=values, extra_stored=extra_stored) if ents else {
        "lines": [], "not_estimated": [], "lines_of_credit": [], "unmatched_notices": [], "until": OB.add_months(
            today, horizon_months)}
    until = sched["until"]
    mins = await minimums(conn, org_id, entity_ids=ents, household_id=household_id)
    names = {e: (ctx.names.get(e) or {}).get("display_name") for e in ents}
    # an entity "has cash" when the core counted at least one Cash & Equivalents line for it
    has_cash = {e: any(is_cash_key(k) for k in own[e]["by_bucket"]) for e in ents}
    rows = []
    for e in ents:
        lines = [_line_dict(ln) for ln in sched["lines"] if ln.payer_entity_id == e]
        liquid = liquid_from_buckets(own[e]["by_bucket"]) if has_cash[e] else None
        if not lines and liquid is None:
            continue   # an entity with neither cash nor obligations is not a payer here (the household still counts it)
        # CASH02: the entity's own active policy (an override), else the household's active policy, else the default
        basis = "entity policy" if e in mins else ("household policy" if f"household:{household_id}" in mins
                                                    else "default")
        c = coverage(liquid, lines, today=today, until=until,
                     min_months=mins.get(e, mins.get(f"household:{household_id}", DEFAULT_MIN_COVERAGE_MONTHS)))
        info = ctx.names.get(e) or {}
        rows.append({"entity_id": e, "display_name": info.get("display_name"), "entity_type": info.get("entity_type"),
                     "member": e in members, "household_share": format(share[e], "f"), "as_of": today.isoformat(),
                     "min_basis": basis,
                     "liquid_basis": "the entity's own Cash & Equivalents (net-worth core)", **render(c, names)})
    hfig = NW.figures(ctx, members)
    h_has_cash = any(is_cash_key(k) for k in hfig["by_bucket"])
    h_lines = household_lines(sched["lines"], share)
    h_liquid = liquid_from_buckets(hfig["by_bucket"]) if h_has_cash else None
    hc = coverage(h_liquid, h_lines, today=today, until=until,
                  min_months=mins.get(f"household:{household_id}", DEFAULT_MIN_COVERAGE_MONTHS))
    household = {"household_id": str(household_id), "as_of": today.isoformat(),
                 "min_basis": "household policy" if f"household:{household_id}" in mins else "default",
                 "liquid_basis": "the household's Cash & Equivalents (net-worth core: look-through, each position "
                                 "once)", "horizon_months": horizon_months, "until": until.isoformat(),
                 **render(hc, names)}
    rows.sort(key=lambda r: (not r["flags"], r["display_name"] or "", r["entity_id"]))
    return Report(household=household, entities=rows, lines=sched["lines"], not_estimated=sched["not_estimated"],
                  lines_of_credit=sched["lines_of_credit"], unmatched_notices=sched["unmatched_notices"], today=today,
                  until=until, names=names, shares=dict(share), household_liquid=h_liquid,
                  entity_liquid={e: (liquid_from_buckets(own[e]["by_bucket"]) if has_cash[e] else None)
                                 for e in ents})


def household_lines(lines, share: dict) -> list[dict]:
    """The household's lines: every reached entity's lines at the household's share of that entity (zero dropped)."""
    out = [_line_dict(ln, share.get(ln.payer_entity_id, ZERO)) for ln in lines]
    return [x for x in out if x["amount"] > 0]


def scope_lines(report: "Report", scope_type: str, entity_id=None) -> list[dict]:
    """CASH02 — the lines a policy of this scope sizes its sleeves on: the household's share-weighted lines, or one
    entity's own lines (the same lines its coverage walks)."""
    if scope_type == "household":
        return household_lines(report.lines, report.shares)
    return [_line_dict(ln) for ln in report.lines if ln.payer_entity_id == str(entity_id)]


def scope_liquid(report: "Report", scope_type: str, entity_id=None) -> Decimal | None:
    if scope_type == "household":
        return report.household_liquid
    return report.entity_liquid.get(str(entity_id))


def calendar_months(lines, names: dict, today: date, months: int) -> list[dict]:
    """The obligations calendar: month → counted total and its lines (dates ascending)."""
    out = []
    for i in range(months):
        start = OB.add_months(date(today.year, today.month, 1), i, 1)
        end = OB.add_months(start, 1, 1)
        ls = [ln for ln in lines if start <= ln.date < end]
        out.append({"month": start.strftime("%Y-%m"), "label": start.strftime("%b %Y"),
                    "total": OB.money_text(sum((ln.amount for ln in ls if ln.counted), ZERO)),
                    "count": sum(1 for ln in ls if ln.counted), "keys": [ln.key for ln in ls]})
    return out


# ═══ Task 6 — tasks and alerts (member_todos, de-duplicated while open, closed when resolved) ══════════════════
def wanted_alerts(report: Report) -> dict[tuple[str, str, str], dict]:
    """{(source, related_type, related_id): {title, detail}} for everything flagged now. Pure."""
    out: dict[tuple[str, str, str], dict] = {}
    hh = report.household
    hid = hh["household_id"]
    subjects = [("household", hid, "The household", hh)] + [("entity", r["entity_id"], r["display_name"] or "An entity",
                                                             r) for r in report.entities]
    for rtype, rid, label, r in subjects:
        if "below_minimum" in r["flags"]:
            out[(TODO_SOURCE["below_minimum"], rtype, rid)] = {
                "title": f"{label}: cash coverage {r['months']} months, below its minimum of {r['min_months']}",
                "detail": f"Liquid {r['liquid'] or '0.00'}; average monthly outflow {r['monthly_outflow']}."}
        if "uncovered_90d" in r["flags"] and r["next_gap"]:
            g = r["next_gap"]
            out[(TODO_SOURCE["uncovered_90d"], rtype, rid)] = {
                "title": f"{label}: {g['name']} (~{g['amount']}, {g['date']}) is not covered by its cash",
                "detail": f"Covered through {r['covered_through']}; shortfall {g['shortfall']}."}
    for n in report.unmatched_notices:
        out[(TODO_SOURCE["unmatched_notice"], "cash_obligation", n["lineage_id"])] = {
            "title": f"Capital-call notice '{n['name']}' matches no commitment",
            "detail": f"{n['amount']} due {n['date']}: match it to a commitment so the projection is superseded."}
    return out


async def sync_alerts(conn, *, org_id, household_id, report: Report, recipients: list[str]) -> dict:
    """Open / refresh one to-do per flagged subject per recipient (``workflow_todos._upsert_todo`` de-duplicates on
    (user, source, subject)); close (status 'done', kept) every open cash to-do of this household whose subject is no
    longer flagged. Returns {"opened": [...ids], "closed": n}."""
    from services.workflow_todos import _upsert_todo

    link = f"/portfolio/households/{household_id}?tab=treasury"
    want = wanted_alerts(report)
    ids = []
    for (source, rtype, rid), body in sorted(want.items()):
        for uid in sorted(set(recipients)):
            ids.append(await _upsert_todo(conn, org_id=UUID(str(org_id)), user_id=UUID(uid), source=source,
                                          related_type=rtype, related_id=UUID(rid), title=body["title"][:300],
                                          detail=body["detail"][:2000], priority=5, action_key=link))
    subjects = {str(household_id)} | {r["entity_id"] for r in report.entities} | {
        str(r["lineage_id"]) for r in await conn.fetch(
            "SELECT DISTINCT lineage_id FROM cash_obligations WHERE org_id = $1::uuid AND entity_id = ANY($2::uuid[])",
            str(org_id), [UUID(r["entity_id"]) for r in report.entities])}
    open_rows = await conn.fetch(
        "SELECT id, source, related_type, related_id::text AS rid FROM member_todos WHERE org_id = $1::uuid AND "
        "source = ANY($2::text[]) AND status = 'open' AND related_id = ANY($3::uuid[])", str(org_id),
        list(TODO_SOURCE.values()), [UUID(s) for s in subjects])
    stale = [r["id"] for r in open_rows if (r["source"], r["related_type"], r["rid"]) not in want]
    if stale:
        await conn.execute("UPDATE member_todos SET status = 'done', detail = left(coalesce(detail, '') || "
                           "' [Closed: resolved]', 2000), updated_at = now() WHERE id = ANY($1::uuid[])", stale)
    return {"opened": [str(i) for i in ids], "closed": len(stale), "flagged": len(want)}


def vocabularies() -> dict:
    return {"flags": [{"key": k, "label": v} for k, v in FLAG_LABELS],
            "default_min_months": format(DEFAULT_MIN_COVERAGE_MONTHS, "f"),
            "cash_keys": {"major": CASH_MAJOR_KEY, "sub_prefix": CASH_SUB_PREFIX}}
