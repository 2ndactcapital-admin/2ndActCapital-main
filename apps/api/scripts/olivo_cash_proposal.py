"""CASH04 Task 7 — the Olivo demo family's CASH PROPOSAL from its ACTIVE cash policy, and the instruction sheet each line
WOULD become.

    doppler run -- python3 apps/api/scripts/olivo_cash_proposal.py --dry-run

DRY RUN ONLY. Requires the household's cash policy to be APPROVED (cash02's step: load_olivo_cash_policy.py drafts and
submits it, a DIFFERENT holder of manage_cash_policy approves it in the app). With no ACTIVE household policy it says
so and exits 0. Otherwise it computes, in a READ ONLY transaction on the Olivo org's RLS context, exactly what
``POST /cash/households/{id}/proposals`` would write — services.cash_proposals.build_household (cash03's plan →
targets per account → the trade list: sells in withdrawal order with the early-sale warning, buys of the chosen
instruments and ladder rungs within each account's cash, the shortfall) — and prints each line with its reason, then
the manual instruction sheet (services.execution_letter.letter_rows, the ONE renderer) for it. It creates no proposal,
no instruction and no task, and NEVER routes anything to a custodian. Never prints a secret.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

NO_POLICY = "no ACTIVE cash policy for the Olivo household — approve it first (cash02's step); nothing to propose."


def print_account(rec: dict, *, executor: str, execute_by, notes_for) -> None:
    from services import cash_proposals as CPR

    a = rec["account"]
    print(f"\n{a.get('label')}  [{rec['scope_plan']['label']} policy v{rec['scope_plan']['active']['version_no']}]")
    if rec["why"] and not rec["built"]:
        print(f"  {rec['why']}")
        return
    b = rec["built"]
    print("  targets   " + "; ".join(f"{CPR.SLEEVE_LABELS[s]} {CPR.money(b['targets'][s])}" for s in CPR.SLEEVES))
    print("  holdings  " + "; ".join(f"{CPR.SLEEVE_LABELS[s]} {CPR.money(b['current_before'][s])}" for s in CPR.SLEEVES)
          + f"; cash {CPR.money(b['cash'])}")
    for u in b["untouched"]:
        print(f"  untouched {u['label']} — {u['why']}")
    for ln in b["lines"]:
        amt = f"{ln['quantity']} {ln['units']}" if ln["quantity"] is not None else f"{ln['notional']} notional"
        lim = f" limit {ln['limit_price']}" if ln["limit_price"] is not None else " at market"
        print(f"  {ln['line_no']:>2}. {ln['side'].upper():<4} {amt}{lim} — {ln['label']} [{CPR.SLEEVE_LABELS[ln['sleeve']]}]")
        print(f"      why: {ln['reason']}")
        if ln["warning"]:
            print(f"      WARNING: {ln['warning']}")
        sec = {"name": ln["label"], "security_type": "fixed_income" if ln["units"] == "par" else "fund"}
        for k, v in CPR.instruction_sheet(ln, account=a | {"account_number_masked": a.get("account_number_masked")},
                                          executor_type=executor, execute_by=execute_by, notes=notes_for(rec),
                                          security=sec):
            print(f"        {k:<15} {v}")
    print(f"  shortfall {CPR.money(b['shortfall'])} (shown, never borrowed); cash after {CPR.money(b['cash_after'])}")
    for n in b["notes"]:
        print(f"  note: {n}")


async def main(argv) -> int:
    if "--dry-run" not in argv:
        print("dry run only: proposals are made in the app (Household → Treasury → Proposed trades) by a holder of "
              "manage_proposals and approved by a DIFFERENT person. This script never writes.")
        return 2
    from _db_connect import admin_dsn, connect, hydrate

    hydrate()
    import load_olivo_obligations as LO
    from services import cash_policy as CP
    from services import cash_proposals as CPR

    dsn, how = await admin_dsn()
    if not dsn:
        print(f"no database: {how}")
        return 1
    conn = await connect(dsn)
    today = datetime.now(timezone.utc).date()
    try:
        org, hid, names = await LO.locate(conn)
        print(f"[INFO] database via {how}; org {org}; household {hid}; {len(names)} demo members; today {today}")
        async with conn.transaction():
            await LO._ctx(conn, org)
            await conn.execute("SET LOCAL transaction_read_only = on")
            rows = await CP.live_rows(conn, org, household_id=hid)
            active = [r for r in rows if r["status"] == "active" and r["scope_type"] == "household"]
            if not active:
                print(NO_POLICY)
                print("\nDRY RUN — nothing written (read-only transaction).")
                return 0
            recs = await CPR.build_household(conn, org, hid, today)
        execute_by = today + timedelta(days=5)

        def notes_for(rec):
            return (f"From cash proposal (dry run — not created) (cash plan, policy version "
                    f"{rec['scope_plan']['active']['version_no']}); fixed-income lines: {CPR.FI_UNITS}")

        print(f"[INFO] {len(recs)} account(s) in the household's active cash policies")
        for rec in recs:
            print_account(rec, executor="custodian_desk", execute_by=execute_by, notes_for=notes_for)
        print("\nNothing routed: an approved proposal becomes instructions only through the app (a different person "
              "approves; the manual route — never a custodian API).")
        print("\nDRY RUN — nothing written (read-only transaction).")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
