"""CASH03 — AFTER-TAX INSTRUMENT SELECTION per sleeve × account. A TARGET plan, never a trade (cash04). Derived at
read time; nothing is stored. docs/cash/CASH_LADDER.md is the reference; every rule is a pure function below.

CANDIDATES per sleeve (CASH03_NOTES D7), each with its NET yield, the yield's BASIS and AS-OF:
    near_term   Treasury / Government / muni money-market funds  — SEC N-MFP 7-day net yield
    reserve     a T-bill (target = the Reserve window's midpoint; a real issue, else indicative) + the same MMFs;
                an ultra-short bond fund ("30-day SEC yield: needs a source" — never chosen)
    long_term   the Treasury ladder (services.cash_ladder: par-weighted rung yields); a muni ladder ("muni yield:
                needs a source" — never chosen)
  Per money-market category the class with the highest fresh yield is the candidate (ties: the class id).

THE EXEMPTION TABLE (instrument × tax; CASH03_NOTES §6):
    treasury_mmf, bill, treasury_ladder   federal taxed · state + local EXEMPT
    government_mmf                         federal taxed · state + local taxed (the exemption depends on the fund's
                                           US-obligation share — not applied, and said so)
    muni_mmf                               federal EXEMPT · state + local taxed (the owner's state of domicile and a
                                           single-state fund's state are not recorded — conservative, and said so)
  federal = federal_ordinary_rate + niit_rate when niit_applies; state = state_rate; city = city_rate (interest is
  ordinary income; the long-term rates never apply).

AFTER-TAX = net × (1 − the rates that apply), by the account's TREATMENT (accounts.tax_status):
    taxable + an approved profile (tax_profiles.profile_for_account)  → the table above; chosen on AFTER-TAX yield
    taxable, no profile   → net only, "after-tax: needs a tax profile"; chosen on NET yield and the why says so
    tax_deferred / tax_exempt  → tax does not apply now: after-tax = net; the highest NET yield wins, whatever the
                                 exemption
  Ties: the lower applicable rate (the more tax-favoured), then the kind order, then the security. The RUNNER-UP is
  the next estimated candidate; the difference is in basis points on the score the choice used.

THE ROADMAP FACT (a test case): a fully taxable CD must yield y × (1 − fed) ÷ (1 − fed − state − city) to match a
T-bill yielding y after tax; the hurdle is that minus y, in bp (≈ 133 bp for a NY / NYC member at the top rates, y 4 %).

ALLOCATION (D8): a sleeve's $ is split across the scope's open accounts by each account's latest cash balance
(account_balances_daily.cash_value). One account → weight 1. Several and no balance → "not allocated — needs each
account's cash balance" (the per-account choices still show) — unless every account's chosen yield is the same, when the
split cannot change the blend. BLENDED per sleeve and household = Σ value × yield ÷ Σ value (net and after-tax
separately); a sleeve without an instrument blend keeps cash02's Treasury-curve proxy, with cash02's label.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from services import cash_ladder as CL
from services import cash_obligations as OB

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
MMF_MAX_AGE_DAYS = 45
NEEDS_PROFILE = "after-tax: needs a tax profile"
NOT_ALLOCATED = "not allocated — needs each account's cash balance (account_balances_daily)"
TAX_KEY = "cash.tax_estimate"
TAX_ESTIMATE = "After-tax figures use your tax profile and are estimates; have counsel review."
KINDS = {
    "treasury_mmf": {"label": "Treasury money-market fund", "federal": True, "state": False, "liquidity": "same day"},
    "government_mmf": {"label": "Government money-market fund", "federal": True, "state": True,
                       "liquidity": "same day",
                       "note": "state tax applied: the exemption depends on the fund's US-obligation share"},
    "muni_mmf": {"label": "Municipal (tax-exempt) money-market fund", "federal": False, "state": True,
                 "liquidity": "same day",
                 "note": "state tax applied: the owner's state of domicile and the fund's state are not recorded"},
    "bill": {"label": "Treasury bill", "federal": True, "state": False, "liquidity": "at maturity"},
    "treasury_ladder": {"label": "Treasury ladder (bills / notes)", "federal": True, "state": False,
                        "liquidity": "at each rung's maturity"},
    "muni_ladder": {"label": "Municipal bond ladder", "federal": False, "state": False, "liquidity": "at maturity",
                    "unavailable": "muni yield: needs a source"},
    "ultra_short_fund": {"label": "Ultra-short bond fund", "federal": True, "state": True, "liquidity": "next day",
                         "unavailable": "30-day SEC yield: needs a source"},
}
KIND_ORDER = ("treasury_mmf", "bill", "treasury_ladder", "government_mmf", "muni_mmf", "muni_ladder",
              "ultra_short_fund")
SLEEVE_KINDS = {"near_term": ("treasury_mmf", "government_mmf", "muni_mmf"),
                "reserve": ("bill", "treasury_mmf", "government_mmf", "muni_mmf", "ultra_short_fund"),
                "long_term": ("treasury_ladder", "muni_ladder")}
MMF_CATEGORY_KIND = {"treasury": "treasury_mmf", "government": "government_mmf", "single_state": "muni_mmf",
                     "other_tax_exempt": "muni_mmf"}
TREATMENTS = {"taxable": ("Taxable", True), "tax_deferred": ("Tax-deferred", False),
              "tax_exempt": ("Tax-exempt", False)}


# ═══ Pure: treatment, rates, after-tax ══════════════════════════════════════════════════════════════════════════
def treatment(account: dict) -> dict:
    key = (account.get("tax_status") or "").strip()
    if key in TREATMENTS:
        label, applies = TREATMENTS[key]
        return {"key": key, "label": label, "tax_applies": applies, "registration_type": account.get("registration_type")}
    return {"key": "unknown", "label": "Tax status unknown — treated as taxable", "tax_applies": True,
            "registration_type": account.get("registration_type")}


def rates(profile: dict | None) -> dict | None:
    if not profile or profile.get("federal_ordinary_rate") is None:
        return None

    def d(k):
        v = profile.get(k)
        return ZERO if v is None else Decimal(str(v))

    fed = d("federal_ordinary_rate") + (d("niit_rate") if profile.get("niit_applies") else ZERO)
    return {"federal": fed, "state": d("state_rate"), "city": d("city_rate"), "state_code": profile.get("state_code"),
            "city_name": profile.get("city"), "tax_year": profile.get("tax_year"), "profile_id": str(profile.get("id")),
            "as_of": profile.get("as_of")}


def applicable_rate(kind: str, r: dict) -> Decimal:
    k = KINDS[kind]
    return (r["federal"] if k["federal"] else ZERO) + ((r["state"] + r["city"]) if k["state"] else ZERO)


def after_tax(kind: str, net: dict, treat: dict, r: dict | None) -> dict:
    if not net.get("estimated"):
        return {"estimated": False, "text": net.get("text")}
    if not treat["tax_applies"]:
        return {"estimated": True, "yield_pct": net["yield_pct"], "rate": ZERO,
                "label": f"{treat['label'].lower()} account — tax does not apply now"}
    if r is None:
        return {"estimated": False, "text": NEEDS_PROFILE}
    rate = applicable_rate(kind, r)
    k = KINDS[kind]
    parts = []
    parts.append(f"federal {format(r['federal'], 'f')}" if k["federal"] else "federal exempt")
    parts.append(f"state {format(r['state'], 'f')} + city {format(r['city'], 'f')}" if k["state"]
                 else "state and local exempt")
    return {"estimated": True, "yield_pct": net["yield_pct"] * (ONE - rate), "rate": rate,
            "label": f"after-tax estimate — {'; '.join(parts)} (tax profile {r['tax_year']}, as of {r['as_of']})"}


def cd_hurdle(treasury_pct: Decimal, r: dict) -> dict:
    """The fully-taxable yield (a CD) that matches a Treasury yielding ``treasury_pct`` after tax, and the hurdle."""
    fed, sl = r["federal"], r["state"] + r["city"]
    denom = ONE - fed - sl
    if denom <= 0:
        return {"estimated": False, "text": "not estimated — the rates leave nothing after tax"}
    eq = treasury_pct * (ONE - fed) / denom
    bp = ((eq - treasury_pct) * HUNDRED).quantize(ONE, rounding=ROUND_HALF_UP)
    return {"estimated": True, "equivalent_pct": eq, "hurdle_bp": bp,
            "text": f"a fully taxable CD must yield {CL.pct_text(eq)} % (+{bp} bp) to match this Treasury after tax"}


def _score(c: dict, treat: dict, r: dict | None) -> tuple[str, Decimal | None]:
    if treat["tax_applies"] and r is not None:
        a = c["after_tax"]
        return "after_tax", (a["yield_pct"] if a.get("estimated") else None)
    return "net", (c["net"]["yield_pct"] if c["net"].get("estimated") else None)


def choose(cands: list[dict], treat: dict, r: dict | None) -> dict:
    """The chosen instrument for one account in one sleeve, WHY (one sentence), the runner-up and the bp gap."""
    scored = []
    for c in cands:
        basis, s = _score(c, treat, r)
        if s is None:
            continue
        rate = applicable_rate(c["kind"], r) if (r is not None and treat["tax_applies"]) else ZERO
        scored.append((s, rate, c))
    scored.sort(key=lambda x: (-x[0], x[1], KIND_ORDER.index(x[2]["kind"]), str(x[2].get("security_id") or "")))
    if not scored:
        return {"chosen": None, "runner_up": None, "gap_bp": None,
                "why": "nothing chosen — no candidate has a yield ("
                       + "; ".join(f"{KINDS[c['kind']]['label']}: {c['net'].get('text')}" for c in cands) + ")"}
    best, nxt = scored[0], (scored[1] if len(scored) > 1 else None)
    basis = "after_tax" if (treat["tax_applies"] and r is not None) else "net"
    gap = ((best[0] - nxt[0]) * HUNDRED).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if nxt else None
    lab = KINDS[best[2]["kind"]]["label"]
    if basis == "after_tax":
        ex = "state- and local-tax exempt" if not KINDS[best[2]["kind"]]["state"] else (
            "federally tax-exempt" if not KINDS[best[2]["kind"]]["federal"] else "fully taxable")
        why = (f"{lab}: the highest after-tax yield for a taxable account at the owner's rates — its interest is {ex}"
               + (f", {gap} bp ahead of the runner-up" if gap is not None else ""))
    elif not treat["tax_applies"]:
        why = (f"{lab}: the highest net yield — a {treat['label'].lower()} account pays no tax now, so the exemption "
               "does not matter" + (f" ({gap} bp ahead)" if gap is not None else ""))
    else:
        why = (f"{lab}: the highest NET yield — no approved tax profile, so the after-tax choice could not be made"
               + (f" ({gap} bp ahead)" if gap is not None else ""))
    return {"chosen": best[2], "runner_up": nxt[2] if nxt else None, "gap_bp": gap, "basis": basis, "why": why}


# ═══ Pure: candidates ═══════════════════════════════════════════════════════════════════════════════════════════
def mmf_candidates(funds: list[dict]) -> dict[str, dict]:
    """{kind: candidate} — per category the class with the highest yield (ties: the class id). ``funds``:
    [{security_id, name, class_id, category, yield: {estimated, yield_pct, as_of, basis}}]."""
    best: dict[str, dict] = {}
    for f in funds:
        kind = MMF_CATEGORY_KIND.get(f.get("category"))
        if kind is None:
            continue
        if not (f.get("yield") or {}).get("estimated"):
            # no fresh yield: keep the class only to carry WHY (e.g. the provider is not connected) — never chosen
            if kind not in best:
                best[kind] = {"kind": kind, "security_id": f["security_id"], "name": f["name"],
                              "class_id": f["class_id"], "net": f.get("yield") or {"estimated": False}}
            continue
        cur = best.get(kind)
        if cur is not None and not cur["net"].get("estimated"):
            cur = None
        key = (f["yield"]["yield_pct"], "".join(chr(255 - ord(ch)) for ch in f["class_id"]))
        if cur is None or key > (cur["net"]["yield_pct"], "".join(chr(255 - ord(ch)) for ch in cur["class_id"])):
            best[kind] = {"kind": kind, "security_id": f["security_id"], "name": f["name"], "class_id": f["class_id"],
                          "net": f["yield"]}
    return best


def reserve_bill(fields: dict, today: date, issues: list[dict], auctions: dict, curve: list[dict], *,
                 curve_why: str | None = None) -> dict:
    """The Reserve sleeve's T-bill: the issue maturing latest on or before the Reserve window's midpoint (a real
    issue), else an indicative bill at that date's prior business day with the curve's yield."""
    from services import cash_policy as CP
    from services import us_business_days as BD

    w = next(x for x in CP.windows(fields["near_months"], fields["reserve_months"], fields["horizon_months"], today)
             if x["key"] == "reserve")
    if w["months"] <= 0:
        return {"kind": "bill", "net": {"estimated": False, "text": "no Reserve window"}, "name": None}
    mid = OB.add_months(today, (w["start_month"] + w["end_month"]) // 2) if w["months"] > 1 else w["from"]
    issue = CL.match_issue([i for i in issues if i["treasury_type"] == "Bill"], mid, today)
    if issue is not None:
        return {"kind": "bill", "security_id": issue.get("security_id"), "name": issue.get("name"),
                "cusip": issue["cusip"], "maturity": issue["maturity"], "indicative": False,
                "net": CL.issue_yield(issue, auctions, today, curve, curve_why=curve_why)}
    mat = BD.prior_business_day(mid)
    return {"kind": "bill", "security_id": None, "name": f"Treasury bill maturing about {mat.isoformat()}",
            "cusip": None, "maturity": mat, "indicative": True, "label": CL.INDICATIVE,
            "net": CL.curve_yield(curve, CL.term_months(today, mat), why=curve_why)}


def candidates(sleeve: str, *, mmfs: dict, bill: dict | None, ladder: dict | None) -> list[dict]:
    out = []
    for kind in SLEEVE_KINDS[sleeve]:
        if kind in MMF_CATEGORY_KIND.values():
            c = mmfs.get(kind) or {"kind": kind, "security_id": None, "name": None,
                                   "net": {"estimated": False, "text": f"no {KINDS[kind]['label'].lower()} with a "
                                                                       "fresh N-MFP 7-day yield"}}
        elif kind == "bill":
            c = bill or {"kind": "bill", "net": {"estimated": False, "text": "no T-bill"}}
        elif kind == "treasury_ladder":
            c = {"kind": kind, "security_id": None, "name": "Treasury ladder",
                 "net": (ladder or {}).get("yield") or {"estimated": False, "text": "no ladder"}}
        else:
            c = {"kind": kind, "security_id": None, "name": None,
                 "net": {"estimated": False, "text": KINDS[kind]["unavailable"]}}
        out.append(dict(c))
    return out


def weights(accounts: list[dict]) -> tuple[dict | None, str | None]:
    """{account id: weight} by cash balance, or (None, why)."""
    if not accounts:
        return None, "no open account in this scope"
    if len(accounts) == 1:
        return {accounts[0]["id"]: ONE}, None
    known = {a["id"]: a["cash_value"] for a in accounts if a.get("cash_value") is not None and a["cash_value"] > 0}
    tot = sum(known.values(), ZERO)
    if tot <= 0:
        return None, NOT_ALLOCATED
    return {a["id"]: (known.get(a["id"], ZERO) / tot) for a in accounts}, None


def _uniform(ys: list[dict | None], why: str | None) -> dict:
    """Without allocation weights a blend is still exact when EVERY account's chosen yield is the same estimated
    figure (the split cannot change it); otherwise not estimated, saying why."""
    if ys and all(y is not None and y.get("estimated") for y in ys) and len({y["yield_pct"] for y in ys}) == 1:
        return {"estimated": True, "yield_pct": ys[0]["yield_pct"], "as_of": ys[0].get("as_of"),
                "basis": "the same yield in every account (the allocation cannot change it)", "source": "instruments"}
    return {"estimated": False, "text": why or "not allocated"}


def _blend(items: list[tuple[Decimal, dict]]) -> dict:
    """Σ value × yield ÷ Σ value over items with value > 0; any such item unestimated → not estimated."""
    held = [(v, y) for v, y in items if v > 0]
    if not held:
        return {"estimated": False, "text": "nothing allocated"}
    miss = [y for _v, y in held if not y.get("estimated")]
    if miss:
        return {"estimated": False, "text": f"not estimated — {miss[0].get('text')}"}
    tot = sum((v for v, _y in held), ZERO)
    return {"estimated": True, "yield_pct": sum((v * y["yield_pct"] for v, y in held), ZERO) / tot,
            "as_of": min((y["as_of"] for _v, y in held if y.get("as_of")), default=None),
            "basis": "value-weighted chosen instruments", "source": "instruments"}


def plan(sleeves: list[dict], accounts: list[dict], *, cands_by_sleeve: dict, profiles: dict) -> dict:
    """The instrument plan. ``sleeves``: cash02's derived sleeves [{key, label, amount (Decimal), yield (cash02)}];
    ``accounts``: [{id, label, tax_status, registration_type, cash_value}]; ``profiles``: {account id: profile|None}.
    Pure."""
    w, w_why = weights(accounts)
    out_sleeves, hh_net, hh_at = [], [], []
    for s in sleeves:
        cands = cands_by_sleeve.get(s["key"], [])
        rows, net_items, at_items = [], [], []
        for a in accounts:
            treat = treatment(a)
            r = rates(profiles.get(a["id"])) if treat["tax_applies"] else None
            cs = [dict(c, after_tax=after_tax(c["kind"], c["net"], treat, r)) for c in cands]
            ch = choose(cs, treat, r)
            value = (s["amount"] * w[a["id"]]) if w is not None else None
            row = {"account_id": a["id"], "account_label": a.get("label"), "treatment": treat,
                   "tax_profile": r is not None, "candidates": cs, **ch, "value": value}
            if ch["chosen"] is not None and treat["tax_applies"] and r is not None and ch["chosen"]["kind"] in (
                    "bill", "treasury_ladder", "treasury_mmf"):
                row["cd_hurdle"] = cd_hurdle(ch["chosen"]["net"]["yield_pct"], r)
            rows.append(row)
            if value is not None:
                c = ch["chosen"]
                net_items.append((value, c["net"] if c else {"estimated": False, "text": ch["why"]}))
                at_items.append((value, c["after_tax"] if c else {"estimated": False, "text": ch["why"]}))
        if w is None:
            bn = _uniform([r["chosen"]["net"] if r["chosen"] else None for r in rows], w_why)
            ba = _uniform([r["chosen"]["after_tax"] if r["chosen"] else None for r in rows], w_why)
        else:
            bn, ba = _blend(net_items), _blend(at_items)
        eff = bn if bn.get("estimated") else (s.get("yield") or {"estimated": False, "text": "not estimated"})
        out_sleeves.append({"key": s["key"], "label": s["label"], "amount": s["amount"], "accounts": rows,
                            "candidate_kinds": list(SLEEVE_KINDS[s["key"]]),
                            "blended": bn, "blended_after_tax": ba,
                            "effective": {**eff, "from": "instruments" if bn.get("estimated") else "cash02 proxy"}})
        hh_net.append((s["amount"], bn))
        hh_at.append((s["amount"], ba))
    return {"sleeves": out_sleeves, "weights": w, "weights_text": w_why,
            "blended": _blend(hh_net), "blended_after_tax": _blend(hh_at)}


# ═══ JSON ═════════════════════════════════════════════════════════════════════════════════════════════════════
def _cand_json(c: dict | None, *, member: bool) -> dict | None:
    if c is None:
        return None
    out = {"kind": c["kind"], "kind_label": KINDS[c["kind"]]["label"], "name": c.get("name"),
           "security_id": c.get("security_id"), "liquidity": KINDS[c["kind"]]["liquidity"],
           "net": CL.yield_json(c["net"]), "after_tax": _at_json(c.get("after_tax")),
           "indicative": bool(c.get("indicative")), "label": c.get("label"), "note": KINDS[c["kind"]].get("note")}
    if c.get("maturity"):
        out["maturity"] = c["maturity"].isoformat()
    if c.get("class_id"):
        out["class_id"] = c["class_id"]
    if c.get("cusip") and not member:
        out["cusip"] = c["cusip"]
    return out


def _at_json(a: dict | None) -> dict:
    a = a or {}
    if not a.get("estimated"):
        return {"estimated": False, "text": a.get("text") or "not estimated"}
    return {"estimated": True, "yield_pct": CL.pct_text(a["yield_pct"]), "label": a.get("label"),
            "rate": format(a["rate"], "f") if a.get("rate") is not None else None}


def render(p: dict, *, member: bool = False) -> dict:
    def blend(b):
        if not b.get("estimated"):
            return {"estimated": False, "text": b.get("text")}
        return {"estimated": True, "yield_pct": CL.pct_text(b["yield_pct"]), "basis": b.get("basis"),
                "as_of": b["as_of"].isoformat() if b.get("as_of") else None}

    def eff(e):
        if not e.get("estimated"):
            return {"estimated": False, "text": e.get("text"), "from": e.get("from")}
        if e.get("from") == "cash02 proxy":
            return {"estimated": True, "yield_pct": format(Decimal(e["yield_pct"]).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP), "f"), "from": e["from"],
                "label": f"estimate — Treasury curve proxy, as of {e['as_of'].isoformat()}"
                if hasattr(e.get("as_of"), "isoformat") else "estimate — Treasury curve proxy"}
        return {**blend(e), "from": e.get("from")}

    return {
        "weights_text": p["weights_text"],
        "blended": blend(p["blended"]), "blended_after_tax": blend(p["blended_after_tax"]),
        "sleeves": [{"key": s["key"], "label": s["label"], "amount": OB.money_text(s["amount"]),
                     "candidate_kinds": s["candidate_kinds"], "blended": blend(s["blended"]),
                     "blended_after_tax": blend(s["blended_after_tax"]), "effective": eff(s["effective"]),
                     "accounts": [{"account_id": r["account_id"], "account_label": r["account_label"],
                                   "treatment": r["treatment"], "tax_profile": r["tax_profile"],
                                   "chosen": _cand_json(r["chosen"], member=member),
                                   "runner_up": _cand_json(r["runner_up"], member=member),
                                   "gap_bp": format(r["gap_bp"], "f") if r["gap_bp"] is not None else None,
                                   "basis": r.get("basis"), "why": r["why"],
                                   "value": OB.money_text(r["value"]) if r["value"] is not None else None,
                                   "value_text": None if r["value"] is not None else p["weights_text"],
                                   "cd_hurdle": ({"estimated": True, "equivalent_pct": CL.pct_text(
                                       r["cd_hurdle"]["equivalent_pct"]), "hurdle_bp": format(
                                       r["cd_hurdle"]["hurdle_bp"], "f"), "text": r["cd_hurdle"]["text"]}
                                       if (r.get("cd_hurdle") or {}).get("estimated") else None),
                                   "candidates": [_cand_json(c, member=member) for c in r["candidates"]]}
                                  for r in s["accounts"]]} for s in p["sleeves"]],
    }


def vocabularies() -> dict:
    return {"instrument_kinds": [{"key": k, "label": v["label"], "liquidity": v["liquidity"]} for k, v in KINDS.items()],
            "treatments": [{"key": k, "label": v[0]} for k, v in TREATMENTS.items()],
            "rung_kinds": [{"key": k, "label": v} for k, v in CL.RUNG_KINDS],
            "instrument_types": [{"key": k, "label": v} for k, v in CL.INSTRUMENT_TYPES]}


# ═══ Reads (the request connection; RLS context already set) ════════════════════════════════════════════════════
async def scope_accounts(conn, org_id, *, household_id=None, entity_ids=(), today: date) -> list[dict]:
    """The scope's OPEN accounts (the household's, or those whose primary entity is in ``entity_ids``) with their
    latest cash balance — ONE row per account (the latest date; on that date the first source_system
    alphabetically: account_balances_daily's key includes source_system, so a SUM would double-count)."""
    rows = await conn.fetch(
        "SELECT a.id::text AS id, a.account_number_masked, a.registration_type, a.tax_status, a.household_id, "
        "a.primary_entity_id::text AS primary_entity_id, a.is_held_away, b.cash_value, b.as_of_date "
        "FROM public.accounts a LEFT JOIN LATERAL (SELECT cash_value, as_of_date FROM public.account_balances_daily x "
        " WHERE x.account_id = a.id AND x.org_id = a.org_id AND x.as_of_date <= $4::date "
        " ORDER BY x.as_of_date DESC, x.source_system LIMIT 1) b ON true "
        "WHERE a.org_id = $1::uuid AND a.valid_to IS NULL AND a.system_to IS NULL AND a.closed_on IS NULL "
        "AND ((a.household_id = $2::uuid AND a.primary_entity_id = ANY($3::uuid[])) OR "
        "     ($2::uuid IS NULL AND a.primary_entity_id = ANY($3::uuid[])) OR "
        "     (a.household_id IS NULL AND a.primary_entity_id = ANY($3::uuid[]))) "
        "ORDER BY a.account_number_masked, a.id",
        str(org_id), str(household_id) if household_id else None, [uuid.UUID(str(e)) for e in entity_ids], today)
    return [{**dict(r), "cash_value": Decimal(r["cash_value"]) if r["cash_value"] is not None else None,
             "label": f"{r['account_number_masked']} · {r['registration_type']}"
                      + (" · held away" if r["is_held_away"] else "")} for r in rows]


async def account_profiles(conn, org_id, accounts: list[dict], tax_year: int) -> dict:
    from services import tax_profiles as TP

    cache: dict = {}
    out = {}
    for a in accounts:
        subj = TP.subject_of(a)
        if subj not in cache:
            cache[subj] = await TP.approved_profile(conn, org_id, subj[0], subj[1], tax_year)
        out[a["id"]] = cache[subj]
    return out


async def read_mmfs(conn, org_id, today: date, *, purpose: str = "internal", class_like: str | None = None,
                    providers: dict | None = None) -> list[dict]:
    """Money-market fund classes in the security master with an N-MFP category, and their 7-day yield (rank walk,
    licence, ≤ 45 days old). ``class_like`` narrows the read (tests: their fixture prefix)."""
    from services import edgar_nmfp as NM
    from services.security_data import layer as L

    rows = await conn.fetch(
        "SELECT g.id::text AS security_id, g.name, i.id_value AS class_id, c.value AS category "
        "FROM portfolio.security_reference_attributes c "
        "JOIN portfolio.securities_global g ON g.id = c.global_security_id AND g.valid_to IS NULL "
        " AND g.system_to IS NULL AND g.merged_into_id IS NULL "
        "JOIN portfolio.securities_global_identifiers i ON i.global_security_id = g.id AND i.id_type = 'sec_class_id' "
        " AND i.valid_to IS NULL AND i.system_to IS NULL "
        "WHERE c.field = 'mmf_category' AND c.valid_to IS NULL AND c.system_to IS NULL "
        "AND ($1::text IS NULL OR i.id_value LIKE $1::text) ORDER BY i.id_value", class_like)
    if not rows:
        return []
    got = await L.get_bulk(conn, org_id, "index_level", [NM.yield_key(r["class_id"]) for r in rows], as_of=today,
                           purpose=purpose, max_age_days=MMF_MAX_AGE_DAYS, providers=providers)
    out = []
    for r in rows:
        res = got[NM.yield_key(r["class_id"])]
        y = ({"estimated": True, "yield_pct": Decimal(res.value), "as_of": res.as_of, "source": "nmfp",
              "basis": NM.BASIS} if res.found else {"estimated": False, "text": f"not estimated — {res.missing}"})
        out.append({**dict(r), "yield": y})
    return out


async def build(conn, org_id, *, fields: dict, lines: list[dict], sleeves: list[dict], accounts: list[dict],
                today: date, purpose: str = "internal", fixture: dict | None = None) -> dict:
    """The whole plan for one scope: ladder + instruments (pure functions over the reads). ``fixture`` narrows the
    issue / fund reads to a verify's own prefixes ({cusip_like, class_like}) and may re-point the curve."""
    fx = fixture or {}
    lt = CL.long_term_window(fields, today)
    until = max(lt["until"], OB.add_months(today, fields["reserve_months"])) + timedelta(days=1)
    curve, curve_missing = await CL.read_curve(conn, org_id, today, purpose=purpose, points=fx.get("curve_points"))
    issues, issues_why = await CL.available_issues(conn, org_id, today, until, purpose=purpose,
                                                   cusip_like=fx.get("cusip_like"))
    auctions = await CL.read_auctions(conn, org_id, [i["cusip"] for i in issues], today, purpose=purpose)
    lt_amount = next((s["amount"] for s in sleeves if s["key"] == "long_term"), None)
    curve_why = CL.no_curve_text(curve_missing) if not curve else None
    ladder = CL.build_ladder(fields, lines, today, issues=issues, auctions=auctions, curve=curve,
                             sleeve_amount=lt_amount, curve_why=curve_why)
    funds = await read_mmfs(conn, org_id, today, purpose=purpose, class_like=fx.get("class_like"))
    mmfs = mmf_candidates(funds)
    bill = reserve_bill(fields, today, issues, auctions, curve, curve_why=curve_why)
    cands = {s["key"]: candidates(s["key"], mmfs=mmfs, bill=bill, ladder=ladder) for s in sleeves}
    profiles = await account_profiles(conn, org_id, accounts, today.year)
    p = plan(sleeves, accounts, cands_by_sleeve=cands, profiles=profiles)
    return {"ladder": ladder, "instruments": p, "sources": {"curve_missing": curve_missing, "issues": issues_why,
                                                            "issues_count": len(issues), "funds_count": len(funds)}}


async def scope_plans(conn, org_id, household_id, rep, today: date, *, purpose: str = "internal",
                      fixture: dict | None = None) -> list[dict]:
    """CASH03 (moved here from routers.cash._plan_block by CASH04, no logic change) — per scope with an ACTIVE policy
    (the household, then each entity override the household reaches): the policy row, its fields, the scope's lines,
    cash02's view, the scope's accounts and the built plan (:func:`build`). The ONE derivation both the Treasury
    envelope (rendered) and cash04's proposals (raw) read."""
    from services import cash_coverage as CC
    from services import cash_policy as CP

    rows = await CP.live_rows(conn, org_id, household_id=household_id, entity_ids=list(rep.shares))
    keys = [("household", str(household_id))] + sorted(
        {("entity", str(r["entity_id"])) for r in rows if r["scope_type"] == "entity"})
    reads = await CP.read_yields(conn, today)
    out = []
    for st, sid in keys:
        active = next((r for r in rows if r["scope_type"] == st and r["status"] == "active" and str(
            r["household_id"] if st == "household" else r["entity_id"]) == sid), None)
        if active is None:
            continue
        if st == "entity" and sid not in rep.shares:
            continue
        fields = CP.fields_of(active)
        lines = CC.scope_lines(rep, st, None if st == "household" else sid)
        liquid = CC.scope_liquid(rep, st, None if st == "household" else sid)
        profile = await CP.subject_profile(conn, org_id, st, sid, today)
        view = CP.evaluate(fields, lines, liquid, reads, profile, today)
        accounts = await scope_accounts(conn, org_id, household_id=household_id if st == "household" else None,
                                        entity_ids=list(rep.shares) if st == "household" else [sid], today=today)
        built = await build(conn, org_id, fields=fields, lines=lines, sleeves=view["sleeves"], accounts=accounts,
                            today=today, purpose=purpose, fixture=fixture)
        out.append({"scope_type": st, "scope_id": sid, "active": active, "fields": fields, "lines": lines,
                    "view": view, "accounts": accounts, "built": built,
                    "label": "The household" if st == "household" else (rep.names.get(sid) or "An entity")})
    return out
