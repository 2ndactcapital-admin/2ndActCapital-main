"""REP10 — the backtest: the replica and its harvesting replayed through a past
market against the fund. HYPOTHETICAL PERFORMANCE, always (backtests.hypothetical
is a CHECK = true), shown only under the firm's hypothetical-performance policy
(services.hypothetical_policy). Nothing runs a backtest automatically.

DATA — eod_price history through the security-data layer ONLY (purpose
internal), from the ranked source with the most points in the window (never
combined — rep10_common.longest_history). No vendor is added: missing history
says so.

COVERAGE FIRST — :func:`coverage`
──────────────────────────────────────────────────────────────────────────────
Trading days = the dates the FUND has a price in the window. A security is
covered when it has a price on the first trading day AND on at least
replication.backtest.min_day_coverage (95%) of the trading days. The run is
made only when the covered securities hold at least
replication.backtest.min_weight_coverage (95%) of the security weight of EVERY
model version used; otherwise it is stored not_estimated with the coverage
found and the missing securities — no partial figures. When it is made, an
uncovered security's weight is held as cash (stated) and a missing day carries
the last price forward (stated).

THE ENGINE — :func:`simulate` (pure, exact Decimal, prec 50)
──────────────────────────────────────────────────────────────────────────────
Start value = the notional (default 1,000,000 — a scale, not an account).
Day 0: buy the model weights in effect (fractional shares, no trading costs,
price return: distributions are not included on either side — stated).
Model weights at each rebalance: the version approved latest on or before that
date (before the first approval, the first version). Each later day:
  1. HARVEST (when on): every lot whose loss ≥ the loss % of its cost AND ≥
     the loss minimum (rep6's thresholds: the account settings given, else the
     org settings, else rep6's defaults) is sold whole; the security may not be
     bought for 30 days (the wash-sale window); its proceeds are bought back on
     the 31st day (rep6's swap-back, in the same security — substitutes are not
     replayed);
  2. REBALANCE on the first trading day of each new period (monthly |
     quarterly | annually | none): sells to target, highest-cost lots first,
     then buys from cash — never a security inside its wash-sale window.
Statistics: cumulative return series for both; realised tracking error =
sample stdev of daily return differences × √252; maximum drawdown of each;
harvested losses (by holding period) and, with a tax profile, their estimated
tax value (short-term × ST combined rate + long-term × LT combined rate — the
rates and as-of stated); fees: the fund's net expense ratio vs the firm's
strategy fee on the notional (annual, shown — not deducted from the series).
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from services import rebalance_engine as E
from services import rep10_common as C

T_BT = "public.backtests"
VIEW = "view_replication"
MANAGE = "manage_replication"
CADENCES = ("monthly", "quarterly", "annually", "none")
CADENCE_LABELS = {"monthly": "Monthly", "quarterly": "Quarterly", "annually": "Annually",
                  "none": "Never (buy and hold)"}
PRESETS = {"2008": (date(2008, 1, 1), date(2008, 12, 31)), "2020": (date(2020, 1, 1), date(2020, 12, 31)),
           "2022": (date(2022, 1, 1), date(2022, 12, 31))}
PRESET_LABELS = {"2008": "2008 — the financial crisis", "2020": "2020 — the pandemic drawdown",
                 "2022": "2022 — rates and inflation", "custom": "Custom window"}
WEIGHT_COVERAGE_SETTING = "replication.backtest.min_weight_coverage"
DAY_COVERAGE_SETTING = "replication.backtest.min_day_coverage"
DEFAULT_COVERAGE = Decimal("0.95")
DEFAULT_NOTIONAL = Decimal("1000000")
WASH_DAYS = 30
SWAP_BACK_DAYS = WASH_DAYS + 1
ANNUALISE = Decimal(252)
METHOD_NOTES = (
    "Hypothetical performance: a replay of the model through past prices, not the record of any account.",
    "Price return only — distributions are not included for the replica or the fund.",
    "Fractional shares, no trading costs, no taxes paid out of the portfolio.",
    "A missing day carries the last price forward; an uncovered security's weight is held as cash.",
    "Harvesting sells a qualifying lot whole and buys the same security back after 31 days (no substitutes).",
)


# ═══ Pure ═══════════════════════════════════════════════════════════════════
def _period(d: date, cadence: str):
    if cadence == "monthly":
        return (d.year, d.month)
    if cadence == "quarterly":
        return (d.year, (d.month - 1) // 3)
    if cadence == "annually":
        return d.year
    return None


def _px(series: dict[date, Decimal], d: date, cache: dict) -> Decimal | None:
    """The price on ``d``, else the last one before it (carried forward)."""
    if d in series:
        cache[id(series)] = series[d]
        return series[d]
    return cache.get(id(series))


def simulate(days: list[date], prices: dict[str, dict[date, Decimal]], fund: dict[date, Decimal], weights_at,
             *, cadence: str, harvest: dict | None, notional: Decimal) -> dict:
    """``weights_at(d)`` → {key | 'cash': weight} (summing to 1). Returns the series and statistics, exact."""
    with localcontext() as ctx:
        ctx.prec = 50
        cash = Decimal(notional)
        lots: dict[str, list[list]] = {}          # key → [[qty, unit_cost, acquired]]
        blocked: dict[str, date] = {}             # key → first day it may be bought again
        pending: list[dict] = []                  # swap-back buys
        events: list[dict] = []
        realized_rebalance = Decimal(0)
        cache: dict = {}
        values, fvals, out_series = [], [], []

        def price_of(k, d):
            return _px(prices[k], d, cache.setdefault(k, {}))

        def value(d):
            return cash + sum((sum((l[0] for l in ls), Decimal(0)) * price_of(k, d) for k, ls in lots.items() if ls),
                              Decimal(0))

        def rebalance(d):
            nonlocal cash, realized_rebalance
            w = weights_at(d)
            v = value(d)
            held = {k: sum((l[0] for l in ls), Decimal(0)) for k, ls in lots.items()}
            for k in sorted(set(held) | {x for x in w if x != E.CASH}):
                p = price_of(k, d) if k in prices else None
                if p is None:
                    continue
                cur = held.get(k, Decimal(0)) * p
                tgt = w.get(k, Decimal(0)) * v
                if cur > tgt:                         # sell, highest-cost lots first
                    need = (cur - tgt) / p
                    for lot in sorted(lots.get(k, []), key=lambda l: (-l[1], l[2])):
                        if need <= 0:
                            break
                        q = min(lot[0], need)
                        realized_rebalance += (p - lot[1]) * q
                        lot[0] -= q
                        need -= q
                        cash += q * p
                    lots[k] = [l for l in lots.get(k, []) if l[0] > 0]
            v = value(d)
            for k in sorted(x for x in w if x != E.CASH):
                if k not in prices or (k in blocked and d < blocked[k]):
                    continue
                p = price_of(k, d)
                if p is None:
                    continue
                cur = sum((l[0] for l in lots.get(k, [])), Decimal(0)) * p
                tgt = w[k] * v
                if tgt > cur:
                    spend = min(tgt - cur, cash)
                    if spend > 0:
                        lots.setdefault(k, []).append([spend / p, p, d])
                        cash -= spend

        prev_period = None
        for i, d in enumerate(days):
            if i == 0:
                rebalance(d)
            else:
                if harvest:
                    for k in sorted(lots):
                        p = price_of(k, d)
                        keep = []
                        for lot in sorted(lots[k], key=lambda l: (l[2], -l[1])):
                            basis = lot[0] * lot[1]
                            loss = (lot[1] - p) * lot[0]
                            if loss > 0 and basis > 0 and loss / basis >= harvest["loss_pct"] \
                                    and loss >= harvest["loss_min"]:
                                cash += lot[0] * p
                                term = "long" if d >= E.long_term_from(lot[2]) else "short"
                                events.append({"date": d, "key": k, "quantity": lot[0], "loss": loss, "term": term})
                                blocked[k] = d + timedelta(days=SWAP_BACK_DAYS)
                                pending.append({"key": k, "amount": lot[0] * p, "on": d + timedelta(days=SWAP_BACK_DAYS)})
                            else:
                                keep.append(lot)
                        lots[k] = keep
                    for pb in [x for x in pending if d >= x["on"]]:
                        pending.remove(pb)
                        p = price_of(pb["key"], d)
                        spend = min(pb["amount"], cash)
                        if p and spend > 0:
                            lots.setdefault(pb["key"], []).append([spend / p, p, d])
                            cash -= spend
                per = _period(d, cadence)
                if per is not None and per != prev_period:
                    rebalance(d)
            prev_period = _period(d, cadence)
            v = value(d)
            f = fund[d]
            values.append(v)
            fvals.append(f)
            out_series.append({"date": d.isoformat(), "replica": str((v / values[0] - 1).quantize(C.Q)),
                               "fund": str((f / fvals[0] - 1).quantize(C.Q))})
        rp = [values[t] / values[t - 1] - 1 for t in range(1, len(values))]
        rf = [fvals[t] / fvals[t - 1] - 1 for t in range(1, len(fvals))]
        diff = [a - b for a, b in zip(rp, rf)]
        n = len(diff)
        if n >= 2:
            m = sum(diff, Decimal(0)) / n
            var = sum(((x - m) ** 2 for x in diff), Decimal(0)) / (n - 1)
            te = (var * ANNUALISE).sqrt().quantize(C.Q, rounding=ROUND_HALF_EVEN)
        else:
            te = None

        def mdd(vs):
            peak, worst = vs[0], Decimal(0)
            for x in vs:
                peak = max(peak, x)
                worst = min(worst, x / peak - 1)
            return format(worst.quantize(C.Q, rounding=ROUND_HALF_EVEN), "f")

        st = sum((e["loss"] for e in events if e["term"] == "short"), Decimal(0))
        lt = sum((e["loss"] for e in events if e["term"] == "long"), Decimal(0))
        return {"series": out_series,
                "statistics": {"replica_return": str((values[-1] / values[0] - 1).quantize(C.Q)),
                               "fund_return": str((fvals[-1] / fvals[0] - 1).quantize(C.Q)),
                               "tracking_error": str(te) if te is not None else None,
                               "max_drawdown_replica": mdd(values), "max_drawdown_fund": mdd(fvals),
                               "observations": n, "end_value": str(C.money(values[-1]))},
                "harvest": {"events": [{"date": e["date"].isoformat(), "key": e["key"],
                                        "quantity": str(e["quantity"].quantize(C.QTY)),
                                        "loss": str(C.money(e["loss"])), "term": e["term"]} for e in events],
                            "count": len(events), "losses_total": str(C.money(st + lt)),
                            "short_term": str(C.money(st)), "long_term": str(C.money(lt))},
                "realized_rebalance_gain": str(C.money(realized_rebalance))}


def coverage(days: list[date], prices: dict[str, dict[date, Decimal]], versions: list[dict],
             *, min_day: Decimal, min_weight: Decimal) -> dict:
    """``versions`` [{version_no, weights}] — the weight test must hold for EVERY version used."""
    covered, missing = [], []
    per_sec = {}
    keys = sorted({k for v in versions for k in v["weights"] if k != E.CASH})
    for k in keys:
        s = prices.get(k) or {}
        hit = sum(1 for d in days if d in s)
        share = (Decimal(hit) / Decimal(len(days))).quantize(C.Q) if days else Decimal(0)
        ok = bool(days) and days[0] in s and share >= min_day
        per_sec[k] = {"days_covered": str(share), "has_first_day": bool(days) and days[0] in s}
        (covered if ok else missing).append(k)
    by_version = []
    for v in versions:
        sec = sum((w for k, w in v["weights"].items() if k != E.CASH), Decimal(0))
        cw = sum((w for k, w in v["weights"].items() if k in covered), Decimal(0))
        by_version.append({"version_no": v["version_no"],
                           "weight_covered": str((cw / sec).quantize(C.Q)) if sec > 0 else "1.0000000000"})
    worst = min((Decimal(x["weight_covered"]) for x in by_version), default=Decimal(0))
    return {"trading_days": len(days), "weight_covered": str(worst), "min_weight_coverage": str(min_weight),
            "min_day_coverage": str(min_day), "by_version": by_version, "securities": len(keys),
            "covered": covered, "missing": missing, "per_security": per_sec,
            "ok": bool(days) and len(days) >= 2 and worst >= min_weight}


# ═══ Inputs ═════════════════════════════════════════════════════════════════
async def model_versions(conn, org_id, model_id) -> list[dict]:
    """Every approved (or since superseded) version with its look-through weights and its approval date."""
    from services import models as M

    rows = await conn.fetch("SELECT id, version_no, approved_at FROM portfolio.model_versions WHERE model_id = $1::uuid "
                            "AND status IN ('approved', 'superseded') AND system_to IS NULL ORDER BY approved_at NULLS "
                            "FIRST, version_no", str(model_id))
    out = []
    for r in rows:
        lt = await M.look_through(conn, r["id"])
        w: dict[str, Decimal] = {}
        for v in lt.values():
            k = v["global_security_id"] if v["resolution_state"] != "cash" and v["global_security_id"] else E.CASH
            w[k] = w.get(k, C.ZERO) + Decimal(str(v["weight"]))
        out.append({"id": str(r["id"]), "version_no": r["version_no"],
                    "approved_on": C.local_day(r["approved_at"]), "weights": w})
    return out


def version_at(versions: list[dict], d: date) -> dict:
    live = [v for v in versions if v["approved_on"] is not None and v["approved_on"] <= d]
    return live[-1] if live else versions[0]


async def histories(conn, org_id, keys: list[str], start: date, end: date) -> tuple[dict, dict]:
    out, prov = {}, {}
    for k in keys:
        r = await C.longest_history(conn, org_id, k, start, end)
        if r:
            out[k] = dict(r["points"])
            prov[k] = {"provider": r["provider"], "points": len(out[k])}
    return out, prov


def window_of(preset: str, start: date | None, end: date | None) -> tuple[date, date]:
    if preset in PRESETS:
        return PRESETS[preset]
    if preset != "custom" or start is None or end is None or end <= start:
        raise C.Rep10Error("a custom window needs a start and a later end")
    return start, end


async def _harvest_settings(conn, org_id, s: dict) -> dict | None:
    from services import harvest as HV

    if not s.get("harvest", True):
        return None
    pct = s.get("loss_pct")
    if pct is None:
        pct = await C.setting(conn, org_id, HV.PCT_SETTING, HV.DEFAULT_PCT, lo=Decimal("0.0000000001"),
                              hi=Decimal("0.9999999999"))
    mn = s.get("loss_min")
    if mn is None:
        mn = await C.setting(conn, org_id, HV.MIN_SETTING, HV.DEFAULT_MIN, lo=Decimal(0))
    return {"loss_pct": Decimal(str(pct)), "loss_min": Decimal(str(mn))}


async def run(pool, *, org_id, actor_id, model_id, fund_security_id, window_preset: str, start: date | None = None,
              end: date | None = None, cadence: str = "quarterly", harvest: bool = True,
              loss_pct: Decimal | None = None, loss_min: Decimal | None = None, notional: Decimal | None = None,
              household_id=None) -> dict:
    from services import account_models as AM
    from services import hypothetical_policy as HP
    from services import replica_models as RM
    from services import replica_terms as T
    from services import replica_viability as RV
    from services import tax_profiles as TP
    from services import xray as XR

    if cadence not in CADENCES:
        raise C.Rep10Error(f"cadence is one of {list(CADENCES)}")
    if window_preset not in PRESET_LABELS:
        raise C.Rep10Error(f"window is one of {list(PRESET_LABELS)}")
    s, e = window_of(window_preset, start, end)
    notional = notional if notional is not None else DEFAULT_NOTIONAL
    if not isinstance(notional, Decimal) or notional <= 0:
        raise C.Rep10Error("the notional is a decimal above zero")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        model = await AM.model_brief(conn, org_id, C.uid(model_id, "model"))
        fund = C.uid(fund_security_id, "fund")
        if not await conn.fetchval("SELECT 1 FROM portfolio.securities_global WHERE id = $1::uuid", fund):
            raise C.Rep10NotFound("fund not found")
        hh = None
        if household_id:
            hh = (await XR.household(conn, org_id, C.uid(household_id, "household")))["id"]
        versions = await model_versions(conn, org_id, model["id"])
        if not versions:
            raise C.Rep10Conflict(f"{model['name']} has no approved version to replay")
        settings = {"cadence": cadence, "harvest": harvest, "notional": str(notional),
                    "loss_pct": str(loss_pct) if loss_pct is not None else None,
                    "loss_min": str(loss_min) if loss_min is not None else None}
        hv = await _harvest_settings(conn, org_id, {"harvest": harvest, "loss_pct": loss_pct, "loss_min": loss_min})
        if hv:
            settings["harvest_rules"] = {k: str(v) for k, v in hv.items()}
        min_w = await C.setting(conn, org_id, WEIGHT_COVERAGE_SETTING, DEFAULT_COVERAGE, lo=Decimal("0.0000000001"),
                                hi=Decimal(1))
        min_d = await C.setting(conn, org_id, DAY_COVERAGE_SETTING, DEFAULT_COVERAGE, lo=Decimal("0.0000000001"),
                                hi=Decimal(1))
        fh, fprov = await histories(conn, org_id, [fund], s, e)
        fund_px = fh.get(fund, {})
        days = sorted(fund_px)
        used = sorted({version_at(versions, d)["version_no"] for d in days} or {versions[0]["version_no"]})
        vused = [v for v in versions if v["version_no"] in used]
        keys = sorted({k for v in vused for k in v["weights"] if k != E.CASH})
        px, prov = await histories(conn, org_id, keys, s, e)
        cov = coverage(days, px, vused, min_day=min_d, min_weight=min_w)
        cov["fund_points"] = len(days)
        cov["provenance"] = {**prov, **{f"fund:{k}": v for k, v in fprov.items()}}
        policy = await HP.current(conn, org_id)
        result, reason, status = None, None, "computed"
        if not days or len(days) < 2:
            status, reason = "not_estimated", f"{C.NEEDS_PRICES} — the fund has {len(days)} price(s) in the window"
        elif not cov["ok"]:
            status = "not_estimated"
            reason = (f"{C.NEEDS_PRICES} — {(Decimal(cov['weight_covered']) * 100).quantize(Decimal('0.01'))}% of the "
                      f"replica's weight covered ({(min_w * 100).quantize(Decimal('0.01'))}% needed); missing "
                      f"{len(cov['missing'])} securities")
        else:
            covered = set(cov["covered"])

            def weights_at(d):
                w = version_at(versions, d)["weights"]
                out = {k: v for k, v in w.items() if k == E.CASH or k in covered}
                out[E.CASH] = out.get(E.CASH, C.ZERO) + sum((v for k, v in w.items() if k != E.CASH
                                                             and k not in covered), C.ZERO)
                return out

            result = simulate(days, {k: px[k] for k in covered}, fund_px, weights_at, cadence=cadence, harvest=hv,
                              notional=notional)
            result["labels"] = {k: (await conn.fetchval("SELECT name FROM portfolio.securities_global WHERE id = "
                                                        "$1::uuid", k)) or k
                                for k in sorted({ev["key"] for ev in result["harvest"]["events"]})}
            rates = None
            if hh:
                prof = await TP.approved_profile(conn, org_id, "household", hh, e.year)
                rates = TP.combined_rates(prof) if prof else None
            h = result["harvest"]
            if rates is None:
                h["tax_value"] = None
                h["tax_value_note"] = "not estimated — no tax profile" + ("" if hh else " (no household chosen)")
            else:
                h["tax_value"] = str(C.money(Decimal(h["short_term"]) * rates["st"] + Decimal(h["long_term"])
                                             * rates["lt"]))
                h["tax_value_note"] = (f"estimate: short-term losses × {rates['st']} + long-term × {rates['lt']} "
                                       f"({rates['tax_year']} profile as of {rates['as_of']})")
            entry = await XR.registry_entry(conn, org_id, fund)
            terms = await T.live_approved(conn, entry["id"]) if entry else None
            er = RM.term_value(terms, "expense_ratio_net_pct")
            fee = {"label": C.ESTIMATE, "notional": str(notional), "deducted_from_series": False}
            if er is None:
                fee.update(fund_fee=None, fund_note="not estimated — needs the fund's net expense ratio")
            else:
                fee.update(fund_fee=str(C.money(notional * Decimal(str(er)) / 100)), expense_ratio_net_pct=str(er))
            rep, _inp, missing = await RV.strategy_fee(conn, org_id, model["id"], notional)
            if rep is None:
                fee.update(replica_fee=None, replica_note="not estimated — needs " + "; ".join(missing))
            else:
                fee["replica_fee"] = str(C.money(rep))
            if fee.get("fund_fee") and fee.get("replica_fee"):
                fee["annual_difference"] = str(C.money(Decimal(fee["fund_fee"]) - Decimal(fee["replica_fee"])))
            result["fees"] = fee
            result["method_notes"] = list(METHOD_NOTES)
            if len(cov["missing"]):
                result["uncovered_as_cash"] = cov["missing"]
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""INSERT INTO {T_BT} (org_id, model_id, fund_security_id, household_id, window_preset, start_date,
                        end_date, settings, coverage, status, result, not_estimated_reason, hypothetical, policy_id,
                        created_by)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11::jsonb,
                            $12, true, $13::uuid, $14::uuid) RETURNING *""",
                str(org_id), str(model["id"]), fund, hh, window_preset, s, e, C.dumps(settings), C.dumps(cov), status,
                C.dumps(result) if result is not None else None, reason, str(policy["id"]) if policy else None,
                str(actor["id"]))
            await C.audit(conn, org_id, actor["id"], "backtest_run", "backtest", row["id"],
                          {"status": status, "model_id": str(model["id"]), "fund_security_id": fund})
            gate = await HP.gate_for(conn, org_id, policy, household_id=hh, loaded=True)
        return backtest_out(dict(row), gate)


def backtest_out(r: dict, gate: dict) -> dict:
    return {"id": str(r["id"]), "model_id": str(r["model_id"]), "fund_security_id": str(r["fund_security_id"]),
            "household_id": str(r["household_id"]) if r["household_id"] else None,
            "window_preset": r["window_preset"], "start_date": C.iso(r["start_date"]), "end_date": C.iso(r["end_date"]),
            "settings": C.loads(r["settings"]), "coverage": C.loads(r["coverage"]), "status": r["status"],
            "result": C.loads(r["result"]), "not_estimated_reason": r["not_estimated_reason"],
            "hypothetical": r["hypothetical"] is True, "label": C.HYPOTHETICAL, "disclosure": gate["disclosure"],
            "policy": gate, "created_at": C.iso(r["created_at"])}


async def load(conn, org_id, backtest_id) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_BT} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                              C.uid(backtest_id, "backtest"), str(org_id))
    if row is None:
        raise C.Rep10NotFound("backtest not found")
    return dict(row)


async def list_backtests(conn, org_id, *, model_id=None, limit: int = 100) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_BT} WHERE org_id = $1::uuid AND system_to IS NULL AND ($2::uuid IS NULL "
                            "OR model_id = $2::uuid) ORDER BY created_at DESC LIMIT $3", str(org_id),
                            str(model_id) if model_id else None, limit)
    return [dict(r) for r in rows]


async def member_view(pool, conn, org_id, user_id) -> dict:
    """What a MEMBER may see: nothing unless the approved policy's audience is staff_and_members; then only computed
    backtests run for a household the member can see, each with the disclosure."""
    from services import hypothetical_policy as HP
    from services.document_embedding import visible_entity_ids

    policy = await HP.current(conn, org_id)
    gate = await HP.gate_for(conn, org_id, policy, loaded=True)
    if not gate["members_allowed"]:
        return {"rows": [], "policy": {"members_allowed": False}, "label": C.HYPOTHETICAL,
                "note": "No backtests are shown to members under the firm's hypothetical-performance policy."}
    ents = [str(x) for x in await visible_entity_ids(pool, org_id, user_id, False)]
    hhs = {str(r["household_id"]) for r in await conn.fetch(
        "SELECT household_id FROM public.household_memberships WHERE entity_id = ANY($1::uuid[]) UNION "
        "SELECT household_id FROM public.accounts WHERE org_id = $2::uuid AND household_id IS NOT NULL "
        "AND primary_entity_id = ANY($1::uuid[]) AND system_to IS NULL AND valid_to IS NULL", ents, str(org_id))}
    rows = await conn.fetch(f"SELECT * FROM {T_BT} WHERE org_id = $1::uuid AND status = 'computed' AND system_to IS NULL "
                            "AND household_id = ANY($2::uuid[]) ORDER BY created_at DESC LIMIT 50", str(org_id),
                            sorted(hhs))
    return {"rows": [backtest_out(dict(r), gate) for r in rows], "policy": gate, "label": C.HYPOTHETICAL,
            "disclosure": gate["disclosure"]}


def build_pdf(b: dict, *, org_name: str, model_name: str, fund_name: str) -> bytes:
    """The backtest as a PDF — HYPOTHETICAL in its title and the policy's disclosure on it. On demand, never stored."""
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    from services.deal_room_pdf import MARGIN, MUTED, NAVY, PAGE_H, PAGE_W, _safe

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(_safe(f"Hypothetical backtest {b['id']}"))
    width = PAGE_W - 2 * MARGIN
    y = PAGE_H - MARGIN

    def line(text, font="Helvetica", size=10, color=(0, 0, 0), gap=13):
        nonlocal y
        c.setFillColorRGB(*color)
        c.setFont(font, size)
        for ln in simpleSplit(_safe(text), font, size, width) or [""]:
            if y < MARGIN + 30:
                c.showPage()
                y = PAGE_H - MARGIN
                c.setFillColorRGB(*color)
                c.setFont(font, size)
            c.drawString(MARGIN, y, ln)
            y -= gap

    line(org_name, "Times-Bold", 13, NAVY, gap=18)
    line("HYPOTHETICAL PERFORMANCE — backtest", "Times-Roman", 16, NAVY, gap=22)
    line(f"Disclosure: {b['disclosure']}", "Helvetica-Bold", 9, NAVY, gap=12)
    y -= 6
    line(f"Replica: {model_name}    Fund: {fund_name}")
    line(f"Window: {b['start_date']} to {b['end_date']} ({PRESET_LABELS.get(b['window_preset'], b['window_preset'])})")
    st = b["settings"] or {}
    line(f"Rebalance: {CADENCE_LABELS.get(st.get('cadence'), st.get('cadence'))}; harvesting "
         f"{'on' if st.get('harvest') else 'off'}; notional {st.get('notional')}")
    y -= 6
    if b["status"] != "computed":
        line(f"Not estimated: {b['not_estimated_reason']}", "Helvetica-Bold", 10)
        line(f"Missing: {', '.join((b['coverage'] or {}).get('missing') or [])}", size=9, color=MUTED)
    else:
        s = b["result"]["statistics"]
        h = b["result"]["harvest"]
        for label, v in (("Replica return", s["replica_return"]), ("Fund return", s["fund_return"]),
                         ("Tracking error (realised)", s["tracking_error"]),
                         ("Maximum drawdown — replica", s["max_drawdown_replica"]),
                         ("Maximum drawdown — fund", s["max_drawdown_fund"]),
                         ("Harvested losses", f"{h['losses_total']} ({h['count']} sale(s))"),
                         ("Estimated tax value of harvesting", h.get("tax_value") or h.get("tax_value_note"))):
            line(f"{label}: {v}")
        for n in b["result"].get("method_notes") or []:
            line(n, size=8, color=MUTED, gap=11)
    y -= 10
    line(f"Disclosure: {b['disclosure']}", size=8, color=MUTED, gap=11)
    c.showPage()
    c.save()
    return buf.getvalue()


def vocabularies() -> dict:
    return {"windows": [{"key": k, "label": v} for k, v in PRESET_LABELS.items()],
            "cadences": [{"key": k, "label": v} for k, v in CADENCE_LABELS.items()],
            "statuses": [{"key": "computed", "label": "Computed (hypothetical)"},
                         {"key": "not_estimated", "label": "Not estimated — needs prices"}],
            "method_notes": list(METHOD_NOTES), "hypothetical_label": C.HYPOTHETICAL}
