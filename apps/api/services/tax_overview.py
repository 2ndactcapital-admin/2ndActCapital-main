"""The Tax screens' reads (rep6): the harvest dashboard across households and
the account page's Tax panel. Read-only; every tax number carries its
estimate label, rates and as-of (or says why it is not estimated)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from services import account_models as AM
from services import harvest as HV
from services import loss_bank as LB
from services import tax_profiles as TP
from services import wash_sale as WS


async def _latest_runs(conn, org_id, account_ids: list[str]) -> dict[str, dict]:
    if not account_ids:
        return {}
    rows = await conn.fetch(
        f"""SELECT DISTINCT ON (account_id) * FROM {HV.T_RUN} WHERE org_id = $1::uuid AND account_id = ANY($2::uuid[])
            AND valid_to IS NULL AND system_to IS NULL ORDER BY account_id, run_date DESC, created_at DESC""",
        str(org_id), account_ids)
    return {str(r["account_id"]): HV.run_out(dict(r)) for r in rows}


async def dashboard(conn, org_id, *, today: date | None = None, limit: int = 200) -> dict:
    """Harvestable losses today (with their estimated value), candidates skipped
    and why, the gain budget used, open wash-sale windows with their end dates,
    and the loss bank by member."""
    from services import rebalance_proposals as RP

    today = today or date.today()
    accts = [dict(r) for r in await conn.fetch(
        f"""SELECT a.id, a.account_number_masked, a.custodian_code, a.registration_type, a.tax_status, a.household_id,
                   a.primary_entity_id, a.org_id, a.is_held_away, l.harvesting_engine, l.gain_budget, l.id AS link_id
            FROM public.accounts a JOIN {AM.T_LINK} l ON l.account_id = a.id AND l.status = 'active'
                 AND l.valid_to IS NULL AND l.system_to IS NULL
            WHERE a.org_id = $1::uuid AND a.system_to IS NULL AND a.valid_to IS NULL AND a.closed_on IS NULL
            ORDER BY a.account_number_masked LIMIT $2""", str(org_id), limit)]
    runs = await _latest_runs(conn, org_id, [str(a["id"]) for a in accts])
    harvestable, skipped, budgets, windows = [], [], [], []
    seen_households: set = set()
    subjects: dict[tuple, dict] = {}
    for a in accts:
        aid = str(a["id"])
        run = runs.get(aid)
        cands = await HV.candidates_of(conn, org_id, run["id"]) if run else []
        for c in cands:
            row = {**c, "account": a["account_number_masked"], "run_date": run["run_date"]}
            if c["outcome"] == "proposed" or c["skip_reason"] in ("open_proposal", "no_clear_replacement",
                                                                   "recent_household_buy", "proposal_failed"):
                harvestable.append(row)
            if c["outcome"] == "skipped":
                skipped.append(row)
        ytd = await RP.realized_ytd(conn, org_id, aid, today)
        budgets.append({"account_id": aid, "account": a["account_number_masked"], "engine": a["harvesting_engine"],
                        "gain_budget": TP._num(a["gain_budget"]), "realized_ytd": str(ytd),
                        "room": None if a["gain_budget"] is None else str(Decimal(str(a["gain_budget"])) - ytd),
                        "last_run": run})
        hh = tuple(sorted(str(x["id"]) for x in await RP.household_accounts(conn, org_id, a)))
        if hh not in seen_households:
            seen_households.add(hh)
            for w in await WS.open_windows(conn, org_id, a, today):
                windows.append({**w, "household_of": a["account_number_masked"]})
        st, sid = TP.subject_of(a)
        subjects.setdefault((st, sid), {"subject_type": st, "subject_id": sid, "accounts": []})["accounts"].append(
            a["account_number_masked"])
    names = {(s["subject_type"], s["subject_id"]): s["name"] for s in await TP.subjects(conn, org_id)}
    bank = []
    for (st, sid), s in subjects.items():
        summ = await LB.summary(conn, org_id, st, sid, today.year)
        summ.pop("entries", None)
        bank.append({**s, "name": names.get((st, sid)), "summary": summ})

    def total(rows, k):
        return str(sum((Decimal(r[k]) for r in rows if r.get(k) is not None), Decimal(0)))

    est_rows = [r for r in harvestable if r.get("est_tax_value") is not None]
    return {"as_of": today.isoformat(),
            "harvestable": {"rows": harvestable, "loss": str(-Decimal(total(harvestable, "unrealized"))),
                            "est_value": total(est_rows, "est_tax_value"), "estimated": len(est_rows),
                            "not_estimated": len(harvestable) - len(est_rows), "label": TP.ESTIMATE},
            "skipped": skipped, "skip_counts": {k: sum(1 for r in skipped if r["skip_reason"] == k)
                                                for k in HV.SKIP_LABELS},
            "gain_budgets": budgets, "wash_windows": windows, "loss_bank": bank,
            "not_estimated": {"after_tax_performance": TP.NEEDS_PRICES, "tracking_error_cost": TP.NEEDS_PRICES}}


async def account_panel(conn, org_id, account_id, *, today: date | None = None) -> dict:
    """The account page's Tax panel: the profile summary, the loss bank, the
    household's open windows and the last harvest run."""
    today = today or date.today()
    account = await AM.load_account(conn, org_id, account_id)
    st, sid = TP.subject_of(account)
    profile = await TP.approved_profile(conn, org_id, st, sid, today.year)
    link = await AM.active_link(conn, account["id"])
    runs = await HV.runs_for(conn, org_id, account_id=account["id"], limit=1)
    last = runs[0] if runs else None
    summ = await LB.summary(conn, org_id, st, sid, today.year)
    return {"account": {"id": str(account["id"]), "account_number_masked": account["account_number_masked"],
                        "tax_class": WS.tax_class(account), "registration_type": account["registration_type"],
                        "tax_status": account["tax_status"]},
            "subject": {"subject_type": st, "subject_id": sid},
            "profile": TP.profile_out(profile) if profile else None,
            "rates": TP.rates_out(TP.combined_rates(profile)),
            "harvesting_engine": link["harvesting_engine"] if link else None,
            "loss_bank": summ, "wash_windows": await WS.open_windows(conn, org_id, account, today),
            "last_harvest_run": last,
            "last_harvest_candidates": await HV.candidates_of(conn, org_id, last["id"]) if last else []}
