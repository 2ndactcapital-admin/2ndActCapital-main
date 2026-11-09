"""CASH04 — the MONTHLY MATURITY WATERFALL (v1 §2.4): a scheduled job on the workflow engine that produces DRAFT cash
proposals, never a trade.

    (1) proceeds of rungs that matured since the last run TOP UP any sleeve below target, Near-term first (then
        Reserve);
    (2) the remainder is REINVESTED as a new rung — on the next UNCOVERED Long-term obligation date when there is one,
        else at the LADDER'S END (the rung of the plan maturing latest on or before that date);
    (3) EARNINGS (dividend / interest transactions since the last run) are reinvested PRO RATA to the target allocation
        (the Long-term share joins the new rung).

THE JOB. ``cash_maturity_waterfall`` in services.security_master_jobs.JOBS (a platform job in the Hollisworks org,
like secmaster1's and S28's), seeded INACTIVE by ``scripts/_apply_cash04.py --seed-jobs`` (an operator step) and
switched on by a Super Admin with BUGFIX4's switch (Admin → Security master). Its Service Task verb
``cash.maturity_waterfall`` is classed in services.workflow_autonomy as ``eligible`` + ``platform_job`` +
``draft_only``; its ONLY write is :func:`services.cash_proposals.write_draft` (a SYSTEM draft, no task — asserted). The
verbs that would submit or route (``cash.proposal_submit`` / ``cash.proposal_route``) are ``never`` and are not
registered as actions at all.

PER HOUSEHOLD (an ACTIVE household cash policy): per account with a plan (cash_proposals.build_household's inputs) —
the PERIOD is the month (``2026-11``) when the account has no waterfall row for this month yet (the monthly run), else
``maturity:<date>`` when a rung matured since its last run, else nothing ("nothing since the last run"). Idempotent per
(account, period): ``write_draft`` returns the existing row (backstop: rebalance_proposals_cash_waterfall_uq). One
review task per household (``cash_waterfall_review``), de-duplicated while open (human_tasks: one open per kind ×
subject). Every household is audited ``cash.waterfall.household``; the run is the workflow engine's own row (history,
holds and the held-run alert come with it). Any failure RAISES → the engine HOLDS the run and alerts the Super Admin.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from services import cash_proposals as CPR

log = logging.getLogger(__name__)

ACTION_KEY = "cash.maturity_waterfall"
JOB_KEY = "cash_maturity_waterfall"
ZERO = Decimal(0)
CENT = Decimal("0.01")
EARNING_TYPES = ("dividend", "interest")       # transaction_types: category distribution, performance_impact income
FIRST_LOOKBACK_DAYS = 31
#: verify only: run_job kwargs (org_ids, today) — production leaves it empty
JOB_KWARGS: dict = {}


class WaterfallError(RuntimeError):
    pass


# ═══ Pure ══════════════════════════════════════════════════════════════════════════════════════════════════════
def period_for(today: date, *, month_done: bool, matured: list[dict]) -> str | None:
    """The run's period for one account: the month when its monthly run is not done yet; else the latest maturity
    since the last run; else None (nothing to do)."""
    if not month_done:
        return f"{today.year:04d}-{today.month:02d}"
    if matured:
        return f"maturity:{max(h['maturity'] for h in matured).isoformat()}"
    return None


def split_pro_rata(amount: Decimal, weights: dict) -> dict:
    """``amount`` across the sleeves by their target weights, to the cent; the odd cents to the LAST sleeve with a
    weight (so the parts add up exactly)."""
    tot = sum((w for w in weights.values() if w > 0), ZERO)
    out = {k: ZERO for k in weights}
    if amount <= 0 or tot <= 0:
        return out
    keys = [k for k in CPR.SLEEVES if weights.get(k, ZERO) > 0]
    for k in keys:
        out[k] = (amount * weights[k] / tot).quantize(CENT, rounding=ROUND_DOWN)
    out[keys[-1]] += amount - sum(out.values(), ZERO)
    return out


def new_rung_target(rungs: list[dict], uncovered: list[dict], today: date) -> tuple[dict | None, str]:
    """The rung a maturity's remainder buys: the plan rung maturing latest on or before the next UNCOVERED Long-term
    obligation date; with none uncovered, the ladder's END (the latest rung). (rung, why)."""
    rs = sorted(rungs, key=lambda r: (r["maturity"], r["index"]))
    if not rs:
        return None, "no ladder — the remainder stays cash"
    nxt = sorted((u for u in uncovered if u["date"] > today), key=lambda u: u["date"])
    if nxt:
        u = nxt[0]
        home = [r for r in rs if r["maturity"] <= u["date"]]
        if home:
            return home[-1], (f"the next uncovered obligation — {u.get('name') or 'an obligation'} on "
                              f"{u['date'].isoformat()}")
    return rs[-1], f"the ladder's end ({rs[-1]['maturity'].isoformat()})"


def uncovered_obligations(scope_plan: dict, holdings: list[dict]) -> list[dict]:
    """Long-term obligations no HELD rung covers: the ladder's own 'unfunded' list, plus each obligation whose plan rung
    the account does not hold."""
    lad = scope_plan["built"]["ladder"]
    held = {str(h["security_id"]) for h in holdings if (h.get("quantity") or ZERO) > 0}
    out = [{"date": u["date"], "name": u.get("name"), "amount": u.get("amount")} for u in lad.get("unfunded") or []]
    by_index = {r["index"]: r for r in lad.get("rungs") or []}
    for ob in lad.get("obligations") or []:
        r = by_index.get(ob.get("rung"))
        if r is not None and (not r.get("security_id") or str(r["security_id"]) not in held):
            out.append({"date": ob["date"], "name": ob.get("name"), "amount": ob.get("amount")})
    return out


def build_waterfall(*, account: dict, holdings: list[dict], cash: Decimal, targets: dict, choices: dict,
                    rungs: list[dict], windows: dict, today: date, since: date, earnings: Decimal,
                    uncovered: list[dict], min_trade: Decimal = Decimal(100)) -> dict:
    """The waterfall's draft for one account. Pure, Decimal. ``since``: the last run's date (exclusive)."""
    earnings = CPR._d(earnings) or ZERO
    cash = CPR._d(cash) or ZERO
    targets = {s: (CPR._d(targets.get(s)) or ZERO) for s in CPR.SLEEVES}
    matured = [h for h in holdings if h.get("cash_kind") in ("bill", "note") and h.get("maturity") is not None
               and since < h["maturity"] <= today and (h.get("quantity") or ZERO) > 0]
    proceeds = sum((CPR._d(h["quantity"]) for h in matured), ZERO)
    live = [h for h in holdings if h not in matured]
    mmfs = [h for h in live if CPR.classify(h, windows, today) == "mmf"]
    mmf_total = sum(((CPR._d(h.get("market_value")) or ZERO) for h in mmfs), ZERO)
    par_in = {s: sum(((CPR._d(h["quantity"]) or ZERO) for h in live if CPR.classify(h, windows, today) == s), ZERO)
              for s in CPR.SLEEVES}
    mmf_near = min(mmf_total, max(targets["near_term"] - par_in["near_term"], ZERO))
    current = {"near_term": par_in["near_term"] + mmf_near, "reserve": par_in["reserve"] + (mmf_total - mmf_near),
               "long_term": par_in["long_term"]}
    before = dict(current)
    lines, notes, warnings = [], [], []
    avail = cash + proceeds          # the matured par is cash pending redemption; the earnings are already in cash
    m_left = proceeds
    for h in matured:
        notes.append(f"{h['label']} matured on {h['maturity'].isoformat()} — {CPR.money(CPR._d(h['quantity']))} at par")
    if earnings > 0:
        notes.append(f"earnings since {since.isoformat()}: {CPR.money(earnings)} (dividends and interest)")

    def buy(sleeve, amount, step, reason):
        nonlocal avail
        ch = choices.get(sleeve)
        if amount < min_trade or ch is None:
            if amount > 0 and ch is None:
                notes.append(f"{CPR.SLEEVE_LABELS[sleeve]}: {CPR.money(amount)} not bought — no instrument chosen")
            return ZERO
        kind = ch.get("kind")
        if kind in CPR.MMF_KINDS and ch.get("security_id"):
            can = min(amount, avail).quantize(CENT, rounding=ROUND_DOWN)
            if can < min_trade:
                return ZERO
            lines.append(CPR._line(side="buy", security_id=ch["security_id"], label=ch.get("name"), cash_kind="mmf",
                                   sleeve=sleeve, notional=can, amount=can, step=step, units="notional",
                                   est_net_yield=ch.get("net"), est_after_tax_yield=ch.get("after_tax"),
                                   reason=reason))
            avail -= can
            return can
        if kind == "bill" and ch.get("security_id") and not ch.get("indicative"):
            r = {"index": None, "maturity": ch["maturity"], "security_id": ch["security_id"], "name": ch.get("name"),
                 "instrument_type": "bill", "yield": ch.get("net")}
            got = CPR._buy_fi(lines, sleeve, r, amount, avail, today, step=step, choice=ch, reason=reason)
            if got is None:
                notes.append(f"{CPR.SLEEVE_LABELS[sleeve]} T-bill: no limit — no yield; not bought")
                return ZERO
            avail -= got[0]
            return got[0]
        notes.append(f"{CPR.SLEEVE_LABELS[sleeve]} ({CPR.money(amount)}): {CPR.NEEDS_ISSUE}")
        return ZERO

    def buy_rung(amount, step, reason):
        """Spend ``amount`` (cash) on the new rung: par = the amount at the rung's limit price, whole $100."""
        nonlocal avail
        rung, why = new_rung_target(rungs, uncovered, today)
        if rung is None:
            notes.append(why)
            return ZERO, why
        if not rung.get("security_id"):
            notes.append(f"new rung ({rung['maturity'].isoformat()}): {CPR.NEEDS_ISSUE}")
            warnings.append({"code": "cash_needs_issue", "message": f"new rung: {CPR.NEEDS_ISSUE}"})
            return ZERO, why
        limit, _how = CPR.buy_limit(rung.get("instrument_type") or "bill", rung.get("yield"), rung["maturity"], today)
        if limit is None:
            notes.append(f"new rung ({rung['maturity'].isoformat()}): no limit — no yield; not bought")
            return ZERO, why
        face = CPR.floor_par(amount / (limit / CPR.HUNDRED))
        if face <= 0:
            return ZERO, why
        got = CPR._buy_fi(lines, "long_term", rung, face, avail, today, step=step,
                          choice=choices.get("long_term") or {}, reason=f"{reason} — a new rung at {why}")
        if got is None:
            notes.append(f"new rung ({rung['maturity'].isoformat()}): no limit — no yield; not bought")
            return ZERO, why
        avail -= got[0]
        return got[0], why

    # (1) top up any sleeve below target, Near-term first, from the maturity proceeds
    for s in ("near_term", "reserve"):
        deficit = targets[s] - current[s]
        if deficit <= 0 or m_left <= 0:
            continue
        amt = min(deficit, m_left)
        spent = buy(s, amt, "top_up", f"{CPR.SLEEVE_LABELS[s]} below target by {CPR.money(deficit)} — topped up "
                                      "from the matured rung (Near-term first)")
        m_left -= spent
        current[s] += spent
    # (2) the remainder → a new rung (next uncovered obligation date, else the ladder's end)
    rung_why = None
    if m_left >= min_trade:
        spent, rung_why = buy_rung(m_left, "reinvest", "the rest of the maturity proceeds")
        m_left -= spent
    # (3) earnings, pro rata to the target allocation (Long-term → the new rung)
    parts = split_pro_rata(earnings, targets)
    for s in ("near_term", "reserve"):
        if parts[s] > 0:
            buy(s, parts[s], "earnings", f"earnings reinvested by the target allocation ({CPR.SLEEVE_LABELS[s]} share)")
    if parts["long_term"] >= min_trade:
        buy_rung(parts["long_term"], "earnings", "earnings reinvested by the target allocation (Long-term share)")
    merged = merge_lines(lines)
    for i, ln in enumerate(merged, start=1):
        ln["line_no"] = i
    if any(ln["units"] == "par" for ln in merged):
        warnings.append({"code": "cash_fixed_income", "message": CPR.FI_UNITS})
    return {"account_id": str(account["id"]), "account_label": account.get("label"), "lines": merged,
            "targets": targets, "current_before": before, "current_after": current, "cash": cash,
            "available_cash": cash + proceeds, "cash_after": avail, "sells_total": ZERO,
            "buys_total": sum((ln["amount"] for ln in merged), ZERO), "shortfall": ZERO,
            "shortfall_by_sleeve": {s: ZERO for s in CPR.SLEEVES}, "untouched": [], "notes": notes,
            "warnings": warnings, "after_tax_effect": {"estimated": False, "text": "waterfall draft"},
            "matured": [{"label": h["label"], "maturity": h["maturity"].isoformat(),
                         "par": CPR.money(CPR._d(h["quantity"]))} for h in matured],
            "proceeds": proceeds, "earnings": earnings, "rung_why": rung_why}


def merge_lines(lines: list[dict]) -> list[dict]:
    """Same security, side and limit → one line (one instruction), amounts added, reasons joined."""
    out: dict[tuple, dict] = {}
    for ln in lines:
        k = (str(ln["security_id"]), ln["side"], ln["price_kind"], str(ln["limit_price"]))
        if k not in out:
            out[k] = dict(ln)
            continue
        m = out[k]
        for f in ("quantity", "notional", "amount"):
            if ln[f] is not None:
                m[f] = (m[f] or ZERO) + ln[f]
        m["reason"] = f"{m['reason']}; {ln['reason']}"
        m["step"] = m["step"] if m["step"] == ln["step"] else "reinvest"
    return list(out.values())


# ═══ Reads ═════════════════════════════════════════════════════════════════════════════════════════════════════
async def last_run(conn, org_id, account_id) -> tuple[date | None, set[str]]:
    """(the last waterfall run's date for the account, the periods it already has)."""
    rows = await conn.fetch(
        "SELECT trigger_ref->>'period' AS period, trigger_ref->>'as_of' AS as_of FROM public.rebalance_proposals "
        "WHERE org_id = $1::uuid AND account_id = $2::uuid AND trigger = 'cash_waterfall' AND system_to IS NULL",
        str(org_id), str(account_id))
    dates = [date.fromisoformat(r["as_of"]) for r in rows if r["as_of"]]
    return (max(dates) if dates else None), {r["period"] for r in rows if r["period"]}


async def earnings_since(conn, org_id, account_id, since: date, today: date) -> Decimal:
    """Σ dividend + interest transactions of the account's positions in (since, today] (live rows only)."""
    v = await conn.fetchval(
        "SELECT COALESCE(sum(abs(COALESCE(t.net_amount, t.gross_amount, 0))), 0) FROM portfolio.transactions t "
        "JOIN portfolio.positions p ON p.id = t.position_id AND p.org_id = t.org_id "
        "WHERE t.org_id = $1::uuid AND p.account_id = $2::uuid AND t.transaction_type_code = ANY($3::text[]) "
        "AND t.valid_to IS NULL AND t.system_to IS NULL AND t.trade_date > $4::date AND t.trade_date <= $5::date",
        str(org_id), str(account_id), list(EARNING_TYPES), since, today)
    return Decimal(v or 0)


# ═══ One household ═════════════════════════════════════════════════════════════════════════════════════════════
async def run_household(pool, *, org_id, household_id, today: date, workflow_run_id=None) -> dict:
    """Detect, build and write the drafts for one household; raise ONE review task (de-duplicated while open)."""
    from services import human_tasks as HT

    made, existing, skipped = [], [], []
    async with pool.acquire() as conn:
        plans = await CPR.household_plans(conn, org_id, household_id, today)
        for sp in plans:
            sp.setdefault("household_id", str(household_id))
        by_account = CPR.assign_scopes(plans)
        mt = await CPR.min_trade(conn, org_id)
        work = []
        for aid, sp in sorted(by_account.items()):
            acct = next(a for a in sp["accounts"] if str(a["id"]) == aid)
            inputs = CPR.account_inputs(sp, aid, today)
            if inputs.get("why"):
                skipped.append({"account_id": aid, "why": inputs["why"]})
                continue
            last, periods = await last_run(conn, org_id, aid)
            since = last or (today - timedelta(days=FIRST_LOOKBACK_DAYS))
            h = await CPR.account_holdings(conn, org_id, aid)
            matured = [x for x in h["holdings"] if x.get("cash_kind") in ("bill", "note") and x.get("maturity")
                       and since < x["maturity"] <= today]
            month = f"{today.year:04d}-{today.month:02d}"
            period = period_for(today, month_done=month in periods, matured=matured)
            if period is None or period in periods:
                skipped.append({"account_id": aid, "why": "nothing since the last run"})
                continue
            earned = await earnings_since(conn, org_id, aid, since, today)
            built = build_waterfall(account=acct, holdings=h["holdings"], cash=h["cash"], targets=inputs["targets"],
                                    choices=inputs["choices"], rungs=inputs["rungs"], windows=inputs["windows"],
                                    today=today, since=since, earnings=earned,
                                    uncovered=uncovered_obligations(sp, h["holdings"]), min_trade=mt)
            work.append((aid, acct, sp, inputs, built, period, since))
    for aid, acct, sp, inputs, built, period, since in work:
        if not built["lines"]:
            skipped.append({"account_id": aid, "why": "nothing to reinvest", "period": period, "notes": built["notes"]})
            continue
        ref = CPR.trigger_ref_for(sp, inputs, built, extra={
            "made_by": "waterfall", "as_of": today.isoformat(), "since": since.isoformat(),
            "maturities": built["matured"], "earnings": CPR.money(built["earnings"]),
            "proceeds": CPR.money(built["proceeds"]), "rung_why": built["rung_why"],
            "workflow_run_id": str(workflow_run_id) if workflow_run_id else None})
        try:
            row = await CPR.write_draft(pool, org_id=org_id, account_id=aid, built=built, trigger_ref=ref,
                                        period=period, today=today)
        except (CPR.CashProposalConflict, CPR.RP.ProposalConflict) as exc:
            skipped.append({"account_id": aid, "why": str(exc), "period": period})
            continue
        (existing if row.get("_existing") else made).append({"proposal_id": str(row["id"]), "account_id": aid,
                                                             "period": period})
    task = None
    if made:
        try:
            task = await HT.open_task(
                pool, org_id=org_id, kind=CPR.TASK_KIND_REVIEW, subject_type="household", subject_id=household_id,
                maker_id=None, eligible_permission=CPR.PERMISSION, actor_kind="system",
                title=f"Review the cash waterfall's draft proposals ({len(made)})",
                payload={"proposals": [m["proposal_id"] for m in made], "today": today.isoformat()},
                action_path=f"/portfolio/households/{household_id}?tab=treasury")
            task = {"id": task["id"], "opened": True}
        except HT.HumanTaskConflict:
            task = {"opened": False, "why": "a review task is already open for this household"}
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
            "($1::uuid, NULL, 'cash.waterfall.household', 'household', $2::uuid, $3::jsonb)", str(org_id),
            str(household_id), CPR._dumps({"today": today.isoformat(), "made": made, "existing": existing,
                                           "skipped": skipped, "task": task,
                                           "workflow_run_id": str(workflow_run_id) if workflow_run_id else None}))
    return {"household_id": str(household_id), "made": made, "existing": existing, "skipped": skipped, "task": task}


# ═══ The platform job ══════════════════════════════════════════════════════════════════════════════════════════
async def households_with_policy(conn, org_id) -> list[str]:
    return [str(r["household_id"]) for r in await conn.fetch(
        "SELECT DISTINCT household_id FROM public.cash_policies WHERE org_id = $1::uuid AND scope_type = 'household' "
        "AND status = 'active' AND valid_to IS NULL AND system_to IS NULL AND household_id IS NOT NULL ORDER BY 1",
        str(org_id))]


async def run_job(pool, *, org_id, user_id, run_context=None, workflow_run_id=None) -> dict:
    """The Service Task. Runs only in a platform-job org (security_master_jobs.PLATFORM_JOB_ORG_IDS). A FIXTURE
    platform org's run touches only the test orgs its verify named (never a real org), as S28's does."""
    from services import security_master_jobs as J
    from services.database import platform_scope, reset_rls_context, set_rls_context
    from services.orgs import is_test_org

    J._assert_platform(org_id)
    kw = dict(JOB_KWARGS)
    today = kw.get("today") or datetime.now(timezone.utc).date()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            if await is_test_org(conn, org_id):
                org_ids = [str(o) for o in (kw.get("org_ids") or [])]
                real = await conn.fetchval("SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[]) "
                                           "AND NOT is_test", org_ids)
                if real:
                    raise WaterfallError("a test platform org's run may act on test orgs only")
            else:
                org_ids = [str(r["org_id"]) for r in await conn.fetch(
                    "SELECT DISTINCT p.org_id FROM public.cash_policies p JOIN public.organizations o ON o.id = p.org_id "
                    "WHERE p.status = 'active' AND p.valid_to IS NULL AND p.system_to IS NULL AND NOT o.is_test")]
    results = []
    for org in org_ids:
        tokens = set_rls_context(org, False)
        try:
            async with pool.acquire() as conn:
                hhs = await households_with_policy(conn, org)
            for hh in hhs:
                results.append({"org_id": org, **await run_household(pool, org_id=org, household_id=hh, today=today,
                                                                     workflow_run_id=workflow_run_id)})
        finally:
            reset_rls_context(tokens)
    made = sum(len(r["made"]) for r in results)
    summary = {"job": JOB_KEY, "today": today.isoformat(), "orgs": len(org_ids), "households": len(results),
               "drafts_made": made, "drafts_existing": sum(len(r["existing"]) for r in results),
               "tasks_opened": sum(1 for r in results if (r.get("task") or {}).get("opened")),
               "submitted": 0, "routed": 0, "workflow_run_id": str(workflow_run_id) if workflow_run_id else None}
    return {"data": summary, "text": f"cash waterfall: {made} draft proposal(s) in {len(results)} household(s); "
                                     "nothing submitted or routed"}


async def _handler(*, pool=None, user_id=None, org_id=None, run_context=None, workflow_run_id=None, **_):
    return await run_job(pool, org_id=org_id, user_id=user_id, run_context=run_context,
                         workflow_run_id=workflow_run_id)


def register_actions() -> None:
    from services.action_registry import REGISTRY, AssistantAction
    from services.security_master_jobs import REQUIRED_PERMISSION

    REGISTRY.register(AssistantAction(
        key=ACTION_KEY, module="cash",
        description="Platform job: the monthly / on-maturity cash waterfall — DRAFT cash proposals (top up Near-term "
                    "first, reinvest the remainder as a new rung, earnings by the target allocation) and one review "
                    "task per household. Never submits, approves or routes.",
        access_type="write", required_permission=REQUIRED_PERMISSION, tier=2, reversible=False,
        render_target="inline", handler=_handler, params_schema={"type": "object", "properties": {}, "required": []},
        workflow_invocable=True))
