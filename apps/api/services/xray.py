"""The portfolio X-ray (rep7) — per household: every account (managed and
held-away, each line with its source and as-of), every fund looked through to
its holdings, exposure by security and by sector, concentration, overlap, and
the funds that can be replicated — with the estimated fee difference, embedded
gain, transition tax and break-even, and a one-click join request for a
MANAGED account. Nothing here trades; a run is a record, nothing more.

METHOD
──────────────────────────────────────────────────────────────────────────────
Holdings   every live position of every account of the household
           (accounts.household_id), quantity ≠ 0. Held-away lines carry their
           held-away source (held_away_records) and as-of; managed lines their
           position's source_system and as_of_date.
Look-through
           a holding whose security is a Replication Studio registry entry
           (portfolio.replica_securities.global_security_id, not retired) with
           a live holdings snapshot is decomposed: exposure = constituent
           weight × the holding's market value (cents, ROUND_HALF_EVEN),
           labelled with the snapshot's as-of. A registry fund with no live
           snapshot, and any other fund, stays ONE line "no look-through". A
           single stock (any non-fund security) passes through at weight 1.
Sector     the holdings file's own 'Sector' column (raw) when the provider
           publishes one; else "unclassified". The security master has no
           sector field — nothing is inferred.
Concentration
           a name whose exposure ÷ the household's valued total is at or above
           replication.xray.single_name_pct (default 0.10) is flagged.
Overlap    between two looked-through funds: Σ min(weight_a, weight_b) over
           their shared constituents. With a followed replica: Σ
           min(household weight, the model's target weight).
Replicable funds (each a labelled ESTIMATE with its inputs and as-of)
           viability     the latest rep4 report's overall verdict, else
                         "no viability report"; model — the approved "Holdings
                         of …" model, else "no model yet". Replicable = an
                         approved model AND a verdict of yes.
           fee difference = market value × expense_ratio_net_pct ÷ 100 (the
                         approved terms; a percent) − the model's strategy fee
                         on the same value (rep4's strategy_fee: the model's
                         default schedule through fee_calc.apply_tiers); no
                         schedule → "no fee schedule".
           embedded gain = Σ over the fund's open lots (lot quantity × the
                         position's price − the lot's cost); a lot with no
                         cost → "not estimated — cost unknown".
           transition tax = the embedded gain netted per character (short /
                         long, a net loss on one side offsets the other) × the
                         tax subject's approved rep6 profile's combined rates;
                         no profile → "not estimated — no tax profile"; an IRA
                         / Roth → no tax.
           break-even years = transition tax ÷ annual fee difference (none when
                         the replica does not cost less).
           harvest benefit of switching, tracking error, after-tax performance
                         → "not estimated — needs prices"
                         (securities_global_prices has no history).
Join       a MANAGED account holding a replicable fund with an approved model:
           "Open join request" → rep5's account_models.propose_change(join)
           (the person clicking is the maker; rep5's approval follows). A
           held-away line has no button: "held away — moving the assets comes
           first".
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from services import held_away as HA

log = logging.getLogger(__name__)

T_RUN = "public.xray_runs"
T_LINE = "public.xray_lines"
CENT = Decimal("0.01")
W = Decimal("0.0000000001")
ZERO = Decimal(0)
CONCENTRATION_SETTING = "replication.xray.single_name_pct"
DEFAULT_CONCENTRATION = Decimal("0.10")
FUND_TYPES = ("fund", "private_fund", "index")
SECTOR_KEYS = ("Sector", "sector", "GICS Sector", "gics_sector", "Sector Name")
UNCLASSIFIED = "unclassified"
NEEDS_PRICES = "not estimated — needs prices"
NO_PROFILE = "not estimated — no tax profile"
NO_FEE_SCHEDULE = "no fee schedule"
NO_MODEL = "no model yet"
NO_REPORT = "no viability report"
COST_UNKNOWN = "not estimated — cost unknown"
NO_ER = "not estimated — no net expense ratio in the approved terms"
NO_VALUE = "not estimated — no market value"
HELD_AWAY_NOTE = "held away — moving the assets comes first"
STATUS_LINES = {
    "tracking_error": f"Tracking error: {NEEDS_PRICES}",
    "harvest_benefit": f"Harvest value of switching: {NEEDS_PRICES}",
    "after_tax_performance": f"After-tax performance: {NEEDS_PRICES}",
    "no_look_through": "no look-through — not in the registry, or no holdings file",
    "no_tax_profile": NO_PROFILE,
    "no_fee_schedule": NO_FEE_SCHEDULE,
    "no_model": NO_MODEL,
    "held_away": HELD_AWAY_NOTE,
    "unclassified": "sector unclassified — the holdings file publishes no sector",
}
LINE_KIND_LABELS = {"holding": "Holding", "constituent": "Look-through exposure", "fund": "Fund analysis"}
TRIGGER_LABELS = {"on_demand": "Run on demand", "scheduler": "Daily sweep"}


class XrayError(Exception):
    pass


class XrayNotFound(XrayError, LookupError):
    pass


class XrayConflict(XrayError):
    pass


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _s(v):
    return None if v is None else str(v)


def _iso(v):
    """date | datetime | an already-ISO str | None → str | None."""
    if v is None or v == "":
        return None
    return v if isinstance(v, str) else v.isoformat()


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else o.isoformat()
                      if isinstance(o, (date, datetime)) else str(o))


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise XrayNotFound(f"{what} not found") from None


def _constituent_key(h: dict) -> str:
    if h["constituent_global_security_id"]:
        return str(h["constituent_global_security_id"])
    return f"raw:{(h['raw_ticker'] or h['raw_cusip'] or h['raw_name'] or h['row_num'])}"


def sector_of(raw: dict | None) -> str:
    raw = raw or {}
    for k in SECTOR_KEYS:
        v = raw.get(k)
        if v is not None and str(v).strip() and str(v).strip() not in ("-", "--"):
            return str(v).strip()
    return UNCLASSIFIED


def net_by_character(st: Decimal, lt: Decimal) -> tuple[Decimal, Decimal]:
    """A net loss on one side offsets a net gain on the other (IRS order)."""
    if st < 0 < lt:
        return ZERO, lt + st
    if lt < 0 < st:
        return st + lt, ZERO
    return st, lt


def transition_tax(st: Decimal, lt: Decimal, rates: dict) -> Decimal:
    s, l_ = net_by_character(st, lt)
    return money(max(s, ZERO) * rates["st"] + max(l_, ZERO) * rates["lt"])


def break_even(tax: Decimal | None, diff: Decimal | None) -> dict:
    if tax is None or diff is None:
        return {"years": None, "note": "not estimated — needs the transition tax and the fee difference"}
    if diff <= 0:
        return {"years": None, "note": "no break-even — the replica does not cost less"}
    return {"years": str((tax / diff).quantize(CENT, rounding=ROUND_HALF_EVEN)),
            "note": "estimate: transition tax ÷ annual fee difference"}


# ── Loading ─────────────────────────────────────────────────────────────────
async def household(conn, org_id, household_id) -> dict:
    row = await conn.fetchrow("SELECT id, name FROM public.households WHERE id = $1::uuid AND org_id = $2::uuid",
                              _uuid(household_id, "household"), str(org_id))
    if row is None:
        raise XrayNotFound("household not found")
    return dict(row)


async def household_accounts(conn, org_id, household_id) -> list[dict]:
    from services import authority as AU      # rep7b — each account's effective authority / custody

    return await AU.attach_many(conn, org_id, [dict(r) for r in await conn.fetch(
        "SELECT id, org_id, account_number_masked, custodian_code, registration_type, tax_status, primary_entity_id, "
        "household_id, is_discretionary, is_held_away, closed_on FROM public.accounts WHERE org_id = $1::uuid "
        "AND household_id = $2::uuid AND system_to IS NULL AND valid_to IS NULL ORDER BY account_number_masked",
        str(org_id), str(household_id))])


_HOLD_SQL = f"""
SELECT p.id, p.account_id, p.asset_id, p.quantity, p.market_value, p.as_of_date, p.source_system,
       a.global_security_id, a.asset_type, COALESCE(g.name, a.name) AS name, g.security_type,
       (SELECT id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = g.id
         AND t.id_type = 'ticker' AND t.system_to IS NULL AND t.valid_to IS NULL LIMIT 1) AS ticker,
       s.kind AS held_away_source, h.as_of AS held_away_as_of
FROM portfolio.positions p
JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
LEFT JOIN portfolio.securities_global g ON g.id = a.global_security_id AND g.system_to IS NULL AND g.valid_to IS NULL
LEFT JOIN {HA.T_REC} h ON h.record_type = 'position' AND h.record_id = p.id AND h.system_to IS NULL
LEFT JOIN {HA.T_SRC} s ON s.id = h.source_id
WHERE p.org_id = $1::uuid AND p.account_id = ANY($2::uuid[]) AND p.valid_to IS NULL AND p.system_to IS NULL
  AND p.quantity IS NOT NULL AND p.quantity <> 0
ORDER BY p.account_id, name
"""


async def registry_entry(conn, org_id, security_id: str) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM portfolio.replica_securities WHERE org_id = $1::uuid "
                              "AND global_security_id = $2::uuid AND status <> 'retired' AND valid_to IS NULL "
                              "AND system_to IS NULL ORDER BY status = 'active' DESC, created_at LIMIT 1",
                              str(org_id), security_id)
    return dict(row) if row else None


async def approved_model(conn, org_id, entry_id, custodian_code: str | None) -> dict | None:
    """The approved "Holdings of …" model for a registry entry — the one built
    for the account's custodian first."""
    from services import models as M
    from services.custody.registry import trade_custodian_key

    key = trade_custodian_key(custodian_code) or "manual"
    rows = await conn.fetch(
        """SELECT m.id, m.name, m.default_fee_schedule_id, k.custodian_code FROM portfolio.models m
           LEFT JOIN portfolio.replica_registry_keys k ON k.model_id = m.id AND k.valid_to IS NULL AND k.system_to IS NULL
           WHERE m.org_id = $1::uuid AND m.replica_security_id = $2::uuid AND m.source = 'fund_replica'
             AND m.status = 'active' AND m.valid_to IS NULL AND m.system_to IS NULL
           ORDER BY (k.custodian_code = $3) DESC NULLS LAST, m.name""", str(org_id), str(entry_id), key)
    for r in rows:
        live = await M.live_approved(conn, r["id"])
        if live:
            return {"id": str(r["id"]), "name": r["name"], "live_version_id": str(live["id"]),
                    "live_version_no": live["version_no"],
                    "default_fee_schedule_id": str(r["default_fee_schedule_id"]) if r["default_fee_schedule_id"]
                    else None}
    return None


async def _concentration(conn, org_id) -> Decimal:
    from services import rebalance_proposals as RP

    return await RP._setting(conn, org_id, CONCENTRATION_SETTING, DEFAULT_CONCENTRATION, lo=Decimal("0.0001"),
                             hi=Decimal(1))


async def followed_replicas(conn, org_id, accounts: list[dict]) -> list[dict]:
    """The models the household's accounts follow (rep5 active links), with
    their live target weights."""
    from services import account_models as AM
    from services import models as M

    out = {}
    for a in accounts:
        link = await AM.active_link(conn, a["id"])
        if not link or str(link["model_id"]) in out:
            continue
        live = await M.live_approved(conn, link["model_id"])
        name = await conn.fetchval("SELECT name FROM portfolio.models WHERE id = $1 AND system_to IS NULL "
                                   "ORDER BY valid_to NULLS FIRST LIMIT 1", link["model_id"])
        weights = {}
        if live:
            for r in await conn.fetch("SELECT global_security_id, target_weight FROM portfolio.model_version_positions "
                                      "WHERE version_id = $1 AND global_security_id IS NOT NULL AND valid_to IS NULL "
                                      "AND system_to IS NULL", live["id"]):
                weights[str(r["global_security_id"])] = weights.get(str(r["global_security_id"]), ZERO) + Decimal(
                    str(r["target_weight"] or 0))
        out[str(link["model_id"])] = {"model_id": str(link["model_id"]), "name": name, "weights": weights,
                                      "version_no": live["version_no"] if live else None}
    return list(out.values())


# ── Compute (no writes) ─────────────────────────────────────────────────────
async def compute(conn, org_id, household_id, *, today: date | None = None) -> dict:
    from services import models as M
    from services import replica_holdings as H
    from services import replica_models as RM
    from services import replica_terms as T
    from services import replica_viability as RV
    from services import tax_profiles as TP
    from services import wash_sale as WS

    today = today or date.today()
    hh = await household(conn, org_id, household_id)
    accounts = await household_accounts(conn, org_id, hh["id"])
    by_acct = {str(a["id"]): a for a in accounts}
    rows = [dict(r) for r in await conn.fetch(_HOLD_SQL, str(org_id), list(by_acct))] if by_acct else []
    threshold = await _concentration(conn, org_id)
    holdings, constituents, funds = [], [], []
    exposure: dict[str, dict] = {}
    fund_weights: dict[str, dict[str, Decimal]] = {}
    total = ZERO
    unvalued = 0
    snap_cache: dict[str, tuple] = {}
    profile_cache: dict[str, dict | None] = {}

    def add_exposure(key, label, sector, amount, *, via):
        e = exposure.setdefault(key, {"key": key, "label": label, "sector": sector, "exposure": ZERO, "via": []})
        e["exposure"] += amount
        if via not in e["via"]:
            e["via"].append(via)

    for r in rows:
        a = by_acct[str(r["account_id"])]
        held = bool(a["is_held_away"])
        mv = None if r["market_value"] is None else Decimal(str(r["market_value"]))
        label = f"{r['name']} ({r['ticker']})" if r["ticker"] else r["name"]
        sec = str(r["global_security_id"]) if r["global_security_id"] else None
        line = {"kind": "holding", "account_id": str(a["id"]), "account": a["account_number_masked"],
                "held_away": held, "security_id": sec, "label": label, "quantity": _s(r["quantity"]),
                "market_value": _s(None if mv is None else money(mv)),
                "as_of": _iso(r["held_away_as_of"] or r["as_of_date"]),
                "source": (r["held_away_source"] or "held_away") if held else r["source_system"], "status_lines": []}
        holdings.append(line)
        if mv is None:
            unvalued += 1
            line["status_lines"].append(NO_VALUE)
            continue
        total += mv
        entry = await registry_entry(conn, org_id, sec) if sec else None
        if (r["asset_type"] or "").lower() == "cash" or r["security_type"] == "cash":
            add_exposure("cash", "Cash", "Cash", mv, via=label)
            constituents.append({"kind": "constituent", "account_id": str(a["id"]), "fund_security_id": sec,
                                 "security_id": None, "label": "Cash", "weight": "1", "weight_as_of": line["as_of"],
                                 "exposure": str(money(mv)), "sector": "Cash", "look_through": False})
            continue
        if entry is None:
            is_fund = r["security_type"] in FUND_TYPES
            sector = "no look-through" if is_fund else UNCLASSIFIED
            add_exposure(sec or f"asset:{r['asset_id']}", label, sector, mv, via=label)
            constituents.append({"kind": "constituent", "account_id": str(a["id"]), "fund_security_id": sec,
                                 "security_id": sec, "label": label, "weight": "1", "weight_as_of": line["as_of"],
                                 "exposure": str(money(mv)), "sector": sector, "look_through": False,
                                 "status": STATUS_LINES["no_look_through"] if is_fund else "single holding — passes "
                                                                                         "through"})
            if is_fund:
                line["status_lines"].append(STATUS_LINES["no_look_through"])
            continue
        # a registry fund: look through its live holdings file
        if sec not in snap_cache:
            snap = await H.latest_snapshot(conn, sec, entry["holdings_source_key"], live_only=True)
            snap_cache[sec] = (snap, await H.snapshot_rows(conn, snap["id"]) if snap else [])
            if snap is not None:
                # the fund's OWN file weights, once per fund — never re-added per account holding it
                fw = fund_weights[sec] = {}
                for h in snap_cache[sec][1]:
                    if h["weight"] is not None:
                        k = _constituent_key(h)
                        fw[k] = fw.get(k, ZERO) + Decimal(str(h["weight"]))
        snap, srows = snap_cache[sec]
        if snap is None:
            add_exposure(sec, label, "no look-through", mv, via=label)
            constituents.append({"kind": "constituent", "account_id": str(a["id"]), "fund_security_id": sec,
                                 "security_id": sec, "label": label, "weight": "1", "weight_as_of": None,
                                 "exposure": str(money(mv)), "sector": "no look-through", "look_through": False,
                                 "status": "no look-through — the registry fund has no live holdings file"})
            line["status_lines"].append("no look-through — no live holdings file")
        else:
            for h in srows:
                if h["weight"] is None:
                    continue
                w = Decimal(str(h["weight"]))
                cs = str(h["constituent_global_security_id"]) if h["constituent_global_security_id"] else None
                key = _constituent_key(h)
                clabel = h["raw_name"] or h["raw_ticker"] or key
                if h["raw_ticker"] and h["raw_name"]:
                    clabel = f"{h['raw_name']} ({h['raw_ticker']})"
                sector = "Cash" if h["asset_type"] == "cash" else sector_of(_loads(h["raw"]))
                amt = money(w * mv)
                add_exposure(key, clabel, sector, amt, via=label)
                constituents.append({"kind": "constituent", "account_id": str(a["id"]), "fund_security_id": sec,
                                     "security_id": cs, "label": clabel, "weight": str(w),
                                     "weight_as_of": _iso(snap["as_of"]), "exposure": str(amt), "sector": sector,
                                     "look_through": True})
            line["look_through_as_of"] = _iso(snap["as_of"])
        # the per-fund analysis
        funds.append(await _fund_analysis(conn, org_id, a, r, line, entry, mv, today, M=M, RM=RM, T=T, RV=RV, TP=TP,
                                          WS=WS, profile_cache=profile_cache, snap=snap))
    # aggregates
    by_security = sorted(exposure.values(), key=lambda e: -e["exposure"])
    for e in by_security:
        e["weight"] = str((e["exposure"] / total).quantize(W)) if total else None
        e["flag"] = bool(total) and e["key"] != "cash" and (e["exposure"] / total) >= threshold
        e["exposure"] = str(money(e["exposure"]))
    sectors: dict[str, Decimal] = {}
    for e in exposure.values():
        sectors[e["sector"]] = sectors.get(e["sector"], ZERO) + Decimal(e["exposure"])
    by_sector = [{"sector": k, "exposure": str(money(v)), "weight": str((v / total).quantize(W)) if total else None}
                 for k, v in sorted(sectors.items(), key=lambda kv: -kv[1])]
    overlap = []
    keys = sorted(fund_weights)
    labels = {f["fund_security_id"]: f["label"] for f in funds}
    for i, fa in enumerate(keys):
        for fb in keys[i + 1:]:
            shared = set(fund_weights[fa]) & set(fund_weights[fb])
            overlap.append({"fund_a": fa, "fund_a_label": labels.get(fa, fa), "fund_b": fb,
                            "fund_b_label": labels.get(fb, fb), "shared": len(shared),
                            "overlap_weight": str(sum((min(fund_weights[fa][k], fund_weights[fb][k]) for k in shared),
                                                      ZERO).quantize(W))})
    replica_overlap = []
    hh_w = {e["key"]: Decimal(e["weight"]) for e in by_security if e["weight"] is not None}
    for m in await followed_replicas(conn, org_id, accounts):
        shared = set(hh_w) & set(m["weights"])
        replica_overlap.append({"model_id": m["model_id"], "name": m["name"], "version_no": m["version_no"],
                                "shared": len(shared),
                                "overlap_weight": str(sum((min(hh_w[k], m["weights"][k]) for k in shared),
                                                          ZERO).quantize(W))})
    flags = [{"key": e["key"], "label": e["label"], "weight": e["weight"], "threshold": str(threshold),
              "message": f"{e['label']} is {Decimal(e['weight']) * 100:.2f}% of the household — at or above the "
                         f"{threshold * 100:.2f}% single-name threshold"} for e in by_security if e["flag"]]
    status_lines = [STATUS_LINES["tracking_error"], STATUS_LINES["harvest_benefit"],
                    STATUS_LINES["after_tax_performance"]]
    return {"household": {"id": str(hh["id"]), "name": hh["name"]}, "as_of": today.isoformat(),
            "accounts": [{"id": str(a["id"]), "account_number_masked": a["account_number_masked"],
                          "held_away": a["is_held_away"], "registration_type": a["registration_type"],
                          "tax_status": a["tax_status"], "authority": a.get("authority"),
                          "custody": a.get("custody")} for a in accounts],
            "total_market_value": str(money(total)), "unvalued_holdings": unvalued,
            "holdings": holdings, "constituents": constituents, "by_security": by_security, "by_sector": by_sector,
            "concentration": {"threshold": str(threshold), "setting": CONCENTRATION_SETTING, "flags": flags},
            "overlap": overlap, "replica_overlap": replica_overlap, "funds": funds, "status_lines": status_lines,
            "label": "estimate"}


async def _fund_analysis(conn, org_id, a, r, line, entry, mv, today, *, M, RM, T, RV, TP, WS, profile_cache,
                         snap) -> dict:
    from services import execution as X

    sec = str(r["global_security_id"])
    held = bool(a["is_held_away"])
    out = {"kind": "fund", "account_id": str(a["id"]), "account": a["account_number_masked"], "held_away": held,
           "fund_security_id": sec, "label": line["label"], "market_value": str(money(mv)), "as_of": line["as_of"],
           "entry_id": str(entry["id"]), "look_through_as_of": _iso(snap["as_of"]) if snap else None,
           "status_lines": [], "label_estimate": "estimate"}
    reports = await RV.list_reports(conn, org_id, entry["id"], limit=1)
    out["viability"] = ({"overall": reports[0]["overall"], "report_id": reports[0]["id"],
                         "computed_at": reports[0]["computed_at"], "custodian_code": reports[0]["custodian_code"]}
                        if reports else {"overall": None, "note": NO_REPORT})
    model = await approved_model(conn, org_id, entry["id"], a["custodian_code"])
    out["model"] = model
    if model is None:
        out["status_lines"].append(NO_MODEL)
    out["replicable"] = bool(model) and bool(reports) and reports[0]["overall"] == "yes"
    # fee difference
    terms = await T.live_approved(conn, entry["id"])
    er = RM.term_value(terms, "expense_ratio_net_pct")
    fee = {"label": "estimate", "inputs": {"market_value": str(money(mv)), "as_of": line["as_of"],
                                           "expense_ratio_net_pct": _s(er),
                                           "terms_version_id": str(terms["id"]) if terms else None}}
    if er is None:
        fee.update(annual_difference=None, note=NO_ER)
    elif model is None:
        fee.update(annual_difference=None, note=NO_MODEL)
    else:
        fund_fee = money(mv * Decimal(str(er)) / 100)
        rep_fee, inputs, missing = await RV.strategy_fee(conn, org_id, model["id"], mv)
        fee["inputs"].update(fund_fee=str(fund_fee), replica_fee_inputs=inputs)
        if rep_fee is None:
            fee.update(annual_difference=None, note=NO_FEE_SCHEDULE, missing=missing)
            out["status_lines"].append(NO_FEE_SCHEDULE)
        else:
            rep_fee = money(rep_fee)
            fee["inputs"]["replica_fee"] = str(rep_fee)
            fee.update(annual_difference=str(fund_fee - rep_fee),
                       note=f"estimate: {money(mv)} × {er}% − the model's strategy fee {rep_fee}")
    out["fee"] = fee
    # embedded gain from the fund's open lots in this account
    lots = await X.account_lots(conn, org_id, a["id"], sec)
    qty = Decimal(str(r["quantity"]))
    price = mv / qty if qty else None
    st = lt = ZERO
    known = bool(lots) and price is not None
    for lot in lots:
        q = Decimal(str(lot["quantity"] or 0))
        cost = lot["adjusted_cost"] if lot["adjusted_cost"] is not None else lot["original_cost"]
        if cost is None or q <= 0:
            known = False
            break
        g = q * price - Decimal(str(cost))
        term = lot["term"] or HA.term_of(date.fromisoformat(lot["acquired_date"]) if lot["acquired_date"] else None,
                                         today)
        if term is None:
            known = False
            break
        if term == "long":
            lt += g
        else:
            st += g
    gain = {"label": "estimate", "inputs": {"lots": len(lots), "price": _s(None if price is None else
                                                                            price.quantize(Decimal("0.0001"))),
                                            "price_source": "the position's own valuation", "as_of": line["as_of"]}}
    if known:
        gain.update(amount=str(money(st + lt)), short_term=str(money(st)), long_term=str(money(lt)))
    else:
        gain.update(amount=None, note=COST_UNKNOWN)
    out["embedded_gain"] = gain
    # transition tax
    tax = {"label": "estimate"}
    if WS.tax_class(a) == "tax_advantaged":
        tax.update(amount="0.00", note="no tax — a sale inside an IRA / Roth realizes no taxable gain")
    elif not known:
        tax.update(amount=None, note=COST_UNKNOWN)
    else:
        sk = str(a["household_id"] or a["primary_entity_id"])
        if sk not in profile_cache:
            profile_cache[sk] = TP.combined_rates(await TP.profile_for_account(conn, org_id, a, today.year))
        rates = profile_cache[sk]
        if rates is None:
            tax.update(amount=None, note=NO_PROFILE)
            out["status_lines"].append(NO_PROFILE)
        else:
            tax.update(amount=str(transition_tax(st, lt, rates)),
                       inputs={"short_term": str(money(st)), "long_term": str(money(lt)), "st_rate": str(rates["st"]),
                               "lt_rate": str(rates["lt"]), "profile_id": str(rates["profile_id"]),
                               "tax_year": rates["tax_year"], "rates_as_of": rates["as_of"]},  # combined_rates: ISO str
                       note="estimate: embedded gain netted by character × the profile's combined rates")
    out["transition_tax"] = tax
    diff = Decimal(fee["annual_difference"]) if fee.get("annual_difference") is not None else None
    out["break_even"] = break_even(Decimal(tax["amount"]) if tax.get("amount") is not None else None, diff)
    out["harvest_benefit"] = NEEDS_PRICES
    out["tracking_error"] = NEEDS_PRICES
    if held and (a.get("authority") or "read_only") == "read_only":
        out["status_lines"].append(HELD_AWAY_NOTE)
    out["join"] = join_eligibility(a, model, out["replicable"])
    out["one_off"] = one_off_eligibility(a)
    return out


def one_off_eligibility(account: dict) -> dict:
    """rep7b — on an ADVISE account the line offers "Propose a one-off trade"
    (a draft manual proposal selling the fund) instead of "Open join request"."""
    from services import authority as AU

    if not AU.path_allowed(account, "one_off"):
        return {"available": False}
    return {"available": True, "side": "sell",
            "reason": "opens a draft one-off proposal selling the fund; the member or another adviser executes it "
                      "once a second person approves"}


def join_eligibility(account: dict, model: dict | None, replicable: bool) -> dict:
    from services import authority as AU

    if not AU.path_allowed(account, "join"):
        e = AU.effective(account)
        if account.get("is_held_away") and e["authority"] == "read_only":
            return {"available": False, "reason": HELD_AWAY_NOTE}
        return {"available": False, "reason": AU.refusal(account, "a join", path="join")}
    if model is None:
        return {"available": False, "reason": NO_MODEL}
    if not replicable:
        return {"available": False, "reason": "not replicable — the viability verdict is not yes"}
    return {"available": True, "model_id": model["id"], "model_name": model["name"],
            "reason": "opens rep5's join request; a second person approves it"}


async def open_one_off(pool, *, org_id, actor_id, household_id, account_id, fund_security_id, executor_type: str,
                       executor_name: str | None = None, executor_contact: str | None = None) -> dict:
    """rep7b — the advise one click: re-check, then a DRAFT one-off proposal
    selling the whole fund (rep5's manual trigger; the clicker is the maker;
    submit and a second person's approval follow)."""
    from services import rebalance_proposals as RP

    async with pool.acquire() as conn:
        hh = await household(conn, org_id, household_id)
        accts = {str(a["id"]): a for a in await household_accounts(conn, org_id, hh["id"])}
        a = accts.get(_uuid(account_id, "account"))
        if a is None:
            raise XrayNotFound("account not found in this household")
        sec = _uuid(fund_security_id, "fund")
        held = await conn.fetchval(
            "SELECT 1 FROM portfolio.positions p JOIN portfolio.assets s ON s.id = p.asset_id WHERE p.org_id = $1::uuid "
            "AND p.account_id = $2::uuid AND s.global_security_id = $3::uuid AND p.valid_to IS NULL "
            "AND p.system_to IS NULL AND p.quantity <> 0", str(org_id), str(a["id"]), sec)
        if not held:
            raise XrayConflict("the account does not hold this fund")
    return await RP.generate_one_off(pool, org_id=org_id, account_id=a["id"], maker_id=actor_id,
                                     lines=[{"global_security_id": sec, "side": "sell", "quantity": None}],
                                     executor_type=executor_type, executor_name=executor_name,
                                     executor_contact=executor_contact,
                                     note="from the X-ray: propose a one-off trade selling the fund")


# ── Runs ────────────────────────────────────────────────────────────────────
async def run(pool, *, org_id, household_id, trigger: str, actor_id=None, today: date | None = None) -> dict | None:
    """Compute and RECORD a run (run + lines). The scheduler's run is one per
    household and day (a second is skipped). Nothing else is written."""
    from services import human_tasks as HT

    if trigger not in TRIGGER_LABELS:
        raise XrayError(f"trigger is one of {list(TRIGGER_LABELS)}")
    today = today or date.today()
    async with pool.acquire() as conn:
        if trigger == "on_demand":
            await HT.assert_human(conn, actor_id, org_id)
        x = await compute(conn, org_id, household_id, today=today)
        async with conn.transaction():
            rid = await conn.fetchval(
                f"""INSERT INTO {T_RUN} (org_id, household_id, run_date, trigger, created_by, settings, summary,
                        status_lines)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb, $7::jsonb, $8::jsonb)
                    ON CONFLICT DO NOTHING RETURNING id""",
                str(org_id), x["household"]["id"], today, trigger, str(actor_id) if actor_id else None,
                _dumps({"single_name_pct": x["concentration"]["threshold"]}),
                _dumps({k: x[k] for k in ("total_market_value", "unvalued_holdings", "by_security", "by_sector",
                                          "concentration", "overlap", "replica_overlap", "accounts")}),
                _dumps(x["status_lines"]))
            if rid is None:
                return None
            for h in x["holdings"]:
                await _line(conn, org_id, rid, "holding", h["account_id"], h["security_id"], None, h["label"],
                            h["market_value"], h["as_of"], h["source"], None, None, None, None, None,
                            {"held_away": h["held_away"], "quantity": h["quantity"],
                             "look_through_as_of": h.get("look_through_as_of")}, h["status_lines"])
            for c in x["constituents"]:
                await _line(conn, org_id, rid, "constituent", c["account_id"], c["security_id"],
                            c["fund_security_id"], c["label"], None, None, None, c["weight"], c["weight_as_of"],
                            c["exposure"], c["sector"], None, {"look_through": c["look_through"]},
                            [c["status"]] if c.get("status") else [])
            for f in x["funds"]:
                await _line(conn, org_id, rid, "fund", f["account_id"], f["fund_security_id"], f["fund_security_id"],
                            f["label"], f["market_value"], f["as_of"], None, None, f["look_through_as_of"], None, None,
                            f["replicable"], {k: f[k] for k in ("viability", "model", "fee", "embedded_gain",
                                                                 "transition_tax", "break_even", "join", "one_off",
                                                                 "harvest_benefit", "tracking_error")},
                            f["status_lines"])
    return {"run_id": str(rid), **x}


async def _line(conn, org_id, rid, kind, account_id, sec, fund, label, mv, as_of, source, weight, weight_as_of,
                exposure, sector, replicable, detail, status_lines) -> None:
    await conn.execute(
        f"""INSERT INTO {T_LINE} (org_id, run_id, line_kind, account_id, global_security_id, fund_security_id, label,
                market_value, as_of, source, weight, weight_as_of, exposure, sector, replicable, detail, status_lines)
            VALUES ($1::uuid, $2, $3, $4::uuid, $5::uuid, $6::uuid, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                    $16::jsonb, $17::jsonb)""",
        str(org_id), rid, kind, account_id, sec, fund, label[:500], None if mv is None else Decimal(mv),
        date.fromisoformat(as_of) if as_of else None, source, None if weight is None else Decimal(weight),
        date.fromisoformat(weight_as_of) if weight_as_of else None, None if exposure is None else Decimal(exposure),
        sector, replicable, _dumps(detail), _dumps(status_lines))


async def runs_for(conn, org_id, household_id, limit: int = 20) -> list[dict]:
    return [{"id": str(r["id"]), "run_date": _iso(r["run_date"]), "trigger": r["trigger"],
             "created_by": str(r["created_by"]) if r["created_by"] else None, "created_at": _iso(r["created_at"])}
            for r in await conn.fetch(f"SELECT * FROM {T_RUN} WHERE org_id = $1::uuid AND household_id = $2::uuid "
                                      "AND valid_to IS NULL AND system_to IS NULL ORDER BY created_at DESC LIMIT $3",
                                      str(org_id), _uuid(household_id, "household"), limit)]


async def open_join(pool, *, org_id, actor_id, household_id, account_id, fund_security_id) -> dict:
    """The one click: re-check, then rep5's propose_change(join) — the person
    clicking is the maker; rep5's maker-checker approval follows."""
    from services import account_models as AM

    async with pool.acquire() as conn:
        hh = await household(conn, org_id, household_id)
        accts = {str(a["id"]): a for a in await household_accounts(conn, org_id, hh["id"])}
        a = accts.get(_uuid(account_id, "account"))
        if a is None:
            raise XrayNotFound("account not found in this household")
        from services import authority as AU

        why = AU.refusal(a, "a join", path="join")     # rep7b — the authority decides (read-only held-away: never)
        if why:
            raise XrayConflict(f"{a['account_number_masked']}: "
                               + (f"{HELD_AWAY_NOTE} — " if a["is_held_away"] else "") + why)
        sec = _uuid(fund_security_id, "fund")
        held = await conn.fetchval(
            "SELECT 1 FROM portfolio.positions p JOIN portfolio.assets s ON s.id = p.asset_id WHERE p.org_id = $1::uuid "
            "AND p.account_id = $2::uuid AND s.global_security_id = $3::uuid AND p.valid_to IS NULL "
            "AND p.system_to IS NULL AND p.quantity <> 0", str(org_id), str(a["id"]), sec)
        if not held:
            raise XrayConflict("the account does not hold this fund")
        entry = await registry_entry(conn, org_id, sec)
        if entry is None:
            raise XrayConflict("the fund is not in the Replication Studio registry")
        from services import replica_viability as RV

        model = await approved_model(conn, org_id, entry["id"], a["custodian_code"])
        reports = await RV.list_reports(conn, org_id, entry["id"], limit=1)
        elig = join_eligibility(a, model, bool(model) and bool(reports) and reports[0]["overall"] == "yes")
        if not elig["available"]:
            raise XrayConflict(elig["reason"])
    return await AM.propose_change(pool, org_id=org_id, actor_id=actor_id, account_id=a["id"], change_kind="join",
                                   model_id=model["id"],
                                   reason=f"Portfolio X-ray: replicate the fund held in {a['account_number_masked']}")


async def sweep(pool, *, org_id, today: date | None = None) -> dict:
    """The daily X-ray sweep for one org (the caller set its RLS context): one
    scheduler run per household with accounts. Records runs only."""
    today = today or date.today()
    async with pool.acquire() as conn:
        hhs = [r["household_id"] for r in await conn.fetch(
            "SELECT DISTINCT household_id FROM public.accounts WHERE org_id = $1::uuid AND household_id IS NOT NULL "
            "AND system_to IS NULL AND valid_to IS NULL ORDER BY household_id", str(org_id))]
        done = {r["household_id"] for r in await conn.fetch(
            f"SELECT household_id FROM {T_RUN} WHERE org_id = $1::uuid AND run_date = $2 AND trigger = 'scheduler' "
            "AND valid_to IS NULL AND system_to IS NULL", str(org_id), today)}
    out = {"runs": [], "skipped": 0, "errors": []}
    for h in hhs:
        if h in done:
            out["skipped"] += 1
            continue
        try:
            r = await run(pool, org_id=org_id, household_id=h, trigger="scheduler", today=today)
            if r is None:
                out["skipped"] += 1
            else:
                out["runs"].append(r["run_id"])
        except Exception as exc:  # noqa: BLE001 — one household never stops the others
            out["errors"].append(f"household {h}: {exc}")
    return out


def vocabularies() -> dict:
    return {"xray_status_lines": [{"key": k, "label": v} for k, v in STATUS_LINES.items()],
            "xray_line_kinds": [{"key": k, "label": v} for k, v in LINE_KIND_LABELS.items()],
            "xray_triggers": [{"key": k, "label": v} for k, v in TRIGGER_LABELS.items()],
            "viability_verdicts": [{"key": "yes", "label": "Replicable"}, {"key": "no", "label": "Not replicable"},
                                   {"key": "unknown", "label": "Unknown — inputs missing"}],
            "estimate_label": "estimate", "needs_prices_label": NEEDS_PRICES, "no_profile_label": NO_PROFILE,
            "held_away_note": HELD_AWAY_NOTE}
