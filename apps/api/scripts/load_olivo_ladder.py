"""CASH03 Task 7 — the Olivo demo family's LADDER and INSTRUMENT PLAN, from its active (or open draft) cash policy.

    doppler run -- python3 apps/api/scripts/load_olivo_ladder.py --dry-run

DRY RUN ONLY: the ladder and the plan are DERIVED (docs/cash/CASH03_NOTES.md D3) — there is nothing to write. The
policy is the household's ACTIVE one; else its open draft / version awaiting approval; else (none yet — cash02's
load_olivo_cash_policy.py has not run for real) the Recommended policy that loader would draft, in memory. When
cash01's Olivo obligations loader has not run for real either, its planned rows are passed IN MEMORY
(``extra_stored``). Computed by the SAME code the Treasury tab uses: services.cash_coverage.household_report →
services.cash_policy.evaluate (the sleeves) → services.cash_instruments.build (services.cash_ladder + the
after-tax selection) on the Olivo org's RLS context, in a READ ONLY transaction. Prints the rungs (maturity, type,
par, what each covers, indicative marked) and per sleeve × account the chosen instrument, why, net / after-tax yield
and the runner-up. Never prints a secret. No network beyond the database (and Doppler for the secrets).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))


def _y(y: dict) -> str:
    if not y.get("estimated"):
        return y.get("text") or "not estimated"
    return f"{y['yield_pct']}% — {y.get('basis') or y.get('label') or ''}".rstrip(" —")


def print_plan(label: str, fields: dict, ladder: dict, ins: dict, sources: dict) -> None:
    w = ladder["window"]
    print(f"\n{label}: Long-term months {w['start_month']}–{w['end_month']} ({w['from']} to before {w['until']}), "
          f"{fields['rung_count']} rungs; {ladder['threshold']}")
    print(f"  Long-term target {ladder['target']}; par {ladder['par_total']}; leftover {ladder['leftover']}; "
          f"ladder yield {_y(ladder['yield'])}")
    for r in ladder["rungs"]:
        what = ", ".join(n or k for n, k in zip(r["target_names"], r["targets"])) if r["kind"] == "targeted" else "filler"
        print(f"  rung {r['index']:>2} {r['kind']:<8} matures {r['maturity']} {r['instrument_type']:<4} par {r['par']:>16}"
              f"  covers {what}{' + ' + str(len(r['funds'])) + ' smaller' if r['funds'] else ''}")
        print(f"           {r['label'] or (r['issue_name'] or '')}{' · ' + r['adjustment'] if r['adjustment'] else ''}")
        print(f"           yield: {_y(r['yield'])}")
    for u in ladder["unfunded"]:
        print(f"  unfunded: {u['date']} {u['amount']} — {u['text']}")
    for n in ladder["notes"]:
        print(f"  note: {n}")
    print(f"  sources: issues — {sources.get('issues') or str(sources.get('issues_count')) + ' issue(s)'}; "
          f"{sources.get('funds_count')} money-market class(es); curve missing: "
          f"{'; '.join(sources.get('curve_missing') or []) or 'none'}")
    print(f"\n  Instrument plan (allocation: {ins['weights_text'] or 'by each account cash balance'})")
    for s in ins["sleeves"]:
        print(f"  {s['label']} ({s['amount']}): instruments {_y(s['blended'])} net, {_y(s['blended_after_tax'])} after "
              f"tax; in effect {_y(s['effective'])} [{s['effective'].get('from')}]")
        for a in s["accounts"][:60]:
            ch = a["chosen"]
            print(f"    {a['account_label']:<38} {a['treatment']['label']:<14} → "
                  f"{(ch['kind_label'] + (' — ' + ch['name'] if ch.get('name') else '')) if ch else 'nothing chosen'}")
            print(f"      why: {a['why']}")
            if ch:
                print(f"      net {_y(ch['net'])}; after-tax {_y(ch['after_tax'])}; runner-up "
                      f"{a['runner_up']['kind_label'] if a['runner_up'] else 'none'} ({a['gap_bp'] or '—'} bp)")
    print(f"  household blended: {_y(ins['blended'])} net, {_y(ins['blended_after_tax'])} after tax")


async def main(argv) -> int:
    if "--dry-run" not in argv:
        print("dry run only: the ladder and the plan are derived — there is nothing to write. Approve the household's "
              "cash policy (load_olivo_cash_policy.py, then a second person in the app) and the plan shows on Treasury.")
        return 2
    from _db_connect import admin_dsn, connect, hydrate

    hydrate()
    import load_olivo_cash_policy as LP
    import load_olivo_obligations as LO
    from services import cash_coverage as CC
    from services import cash_instruments as CI
    from services import cash_ladder as CL
    from services import cash_obligations as OB
    from services import cash_policy as CP

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
            mine = [r for r in rows if r["scope_type"] == "household"]
            pol = (next((r for r in mine if r["status"] == "active"), None)
                   or next((r for r in mine if r["status"] in ("draft", "pending_approval")), None))
            if pol is not None:
                fields, source = CP.fields_of(pol), f"the {pol['status']} policy v{pol['version_no']}"
            else:
                fields, source = LP.policy_fields(3), "no policy yet — the Recommended policy cash02's loader drafts"
            planned = await LO.plan(conn, org, LO.read_csv(), names, today)
            live = {r["external_key"] for r in await conn.fetch(
                "SELECT external_key FROM cash_obligations WHERE org_id = $1::uuid AND external_key = ANY($2::text[]) "
                "AND valid_to IS NULL AND system_to IS NULL", org, [p["key"] for p in planned])}
            mem = [LO._memory_row(org, p, today) for p in planned if p["key"] not in live]
            rep = await CC.household_report(conn, org, hid, today=today, extra_stored=mem or None,
                                            horizon_months=max(fields["horizon_months"], OB.DEFAULT_HORIZON_MONTHS))
            lines = CC.scope_lines(rep, "household")
            view = CP.evaluate(fields, lines, CC.scope_liquid(rep, "household"), await CP.read_yields(conn, today),
                               await CP.subject_profile(conn, org, "household", hid, today), today)
            accounts = await CI.scope_accounts(conn, org, household_id=hid, entity_ids=list(rep.shares), today=today)
            built = await CI.build(conn, org, fields=fields, lines=lines, sleeves=view["sleeves"], accounts=accounts,
                                   today=today, purpose="internal")
        if mem:
            print(f"[INFO] {len(mem)} of the {len(planned)} Olivo obligations are not loaded yet — included IN MEMORY")
        print(f"[INFO] policy: {source}; {len(accounts)} open accounts "
              f"({sum(1 for a in accounts if a['cash_value'] is not None)} with a cash balance)")
        nm = {ln["key"]: ln.get("name") for ln in lines}
        print_plan("Ladder (household)", fields, CL.render(built["ladder"], nm), CI.render(built["instruments"]),
                   built["sources"])
        print("\nDRY RUN — nothing written (read-only transaction).")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
