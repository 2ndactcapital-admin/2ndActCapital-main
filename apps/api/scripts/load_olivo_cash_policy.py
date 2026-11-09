"""CASH02 Task 7 — the Olivo demo family's RECOMMENDED cash policy, as a DRAFT submitted for approval.

    doppler run -- python3 apps/api/scripts/load_olivo_cash_policy.py --dry-run
    doppler run -- python3 apps/api/scripts/load_olivo_cash_policy.py --operator-email you@firm.com [--objective 3]

--dry-run prints the policy it would draft (the Recommended preset at the objective score, default 3 = balanced: N 3,
R 12, horizon 24, 8 rungs, minimum 6), its DERIVED sleeves in months / $ / %, the estimated yield of each sleeve with
its label (FRED, through services.market_data.read_service), the after-tax figure or "needs a tax profile", and the
coverage against the minimum — computed by the SAME code the Treasury tab uses (services.cash_coverage.
household_report + services.cash_policy.evaluate). When cash01's Olivo obligations loader has not run for real yet,
its planned rows are passed IN MEMORY (``extra_stored``) so the preview shows the schedule the demo will have.
The transaction is READ ONLY: nothing is inserted, nothing is written anywhere.

The real run writes as the OPERATOR (``--operator-email`` → their users row) through services.cash_policy.save_draft
and .submit on the app's own pool under the Olivo org's RLS context: a DRAFT, then submitted — a human task
(cash_policy_approval) opens for a DIFFERENT holder of manage_cash_policy. The operator then approves it IN THE APP
as a second person (Household → Treasury, or Portfolio → Approvals). Nothing here ever activates a policy.

IDEMPOTENT: the draft's reason carries ``demo-olivo: recommended cash policy``. A version awaiting approval → nothing
to do (it says so); an open draft of ours → submitted; an ACTIVE policy with the same fields → unchanged; otherwise a
new draft is saved and submitted. A demo policy is never deleted (cash_policies_demo_guard_trg).
Never prints a secret. No network beyond the database (and Doppler for the secrets).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

TAG = "demo-olivo"
REASON = f"{TAG}: recommended cash policy"


def policy_fields(score: int) -> dict:
    from services import cash_policy as CP

    return CP.normalise({"preset": "recommended", "objective_score": score, "horizon_months": 24, "rung_count": 8,
                         "buffer_months": "0", "min_coverage_months": "6"})


def print_view(label: str, fields: dict, view: dict) -> None:
    print(f"\n{label}: preset {fields['preset']}, objective {fields['objective']} ({fields['objective_score']}), "
          f"Near-term [0, {fields['near_months']}) · Reserve [{fields['near_months']}, {fields['reserve_months']}) · "
          f"Long-term [{fields['reserve_months']}, {fields['horizon_months']}) months, {fields['rung_count']} rungs, "
          f"minimum {fields['min_coverage_months']} months")
    print(f"  liquid {view['liquid'] or view['liquid_text']}; targets {view['total']}; unallocated {view['unallocated']}"
          f"; as of {view['as_of']}")
    for s in view["sleeves"]:
        y, at = s["yield"], s["after_tax"]
        ytxt = f"{y['yield_pct']}% {y['basis']} ({y['label']})" if y.get("estimated") else y.get("text")
        atxt = f"{at['yield_pct']}%" if at.get("estimated") else at.get("text")
        print(f"  {s['label']:<10} months {s['start_month']:>3}–{s['end_month']:<3} {s['amount']:>16} "
              f"{(s['pct'] + '%') if s['pct'] else s['pct_text']:>8}  {s['count']} obligations")
        print(f"             yield: {ytxt}")
        print(f"             after-tax → {atxt}")
        if s["note"]:
            print(f"             note: {s['note']}")
    b = view["blended"]
    print(f"  blended estimated yield: {b['yield_pct'] + '%' if b.get('estimated') else b.get('text')}")
    c = view["coverage"]
    print(f"  coverage {c['months'] or c['months_text']} months vs minimum {c['min_months']}"
          f"{' — BELOW the minimum' if c['below_minimum'] else ''}")


async def main(argv) -> int:
    from _db_connect import admin_dsn, connect, hydrate

    hydrate()
    import load_olivo_obligations as LO
    from services import cash_coverage as CC
    from services import cash_obligations as OB
    from services import cash_policy as CP

    dry = "--dry-run" in argv
    email = argv[argv.index("--operator-email") + 1] if "--operator-email" in argv else None
    score = int(argv[argv.index("--objective") + 1]) if "--objective" in argv else 3
    if not dry and not email:
        print("the real run names the operator: --operator-email <their sign-in email> (or use --dry-run)")
        return 2
    fields = policy_fields(score)
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"no database: {how}")
        return 1
    conn = await connect(dsn)
    today = datetime.now(timezone.utc).date()
    try:
        org, hid, names = await LO.locate(conn)
        print(f"[INFO] database via {how}; org {org}; household {hid}; {len(names)} demo members")
        async with conn.transaction():
            await LO._ctx(conn, org)
            if dry:
                await conn.execute("SET LOCAL transaction_read_only = on")
            rows = LO.read_csv()
            planned = await LO.plan(conn, org, rows, names, today)
            live_keys = {r["external_key"] for r in await conn.fetch(
                "SELECT external_key FROM cash_obligations WHERE org_id = $1::uuid AND external_key = ANY($2::text[]) "
                "AND valid_to IS NULL AND system_to IS NULL", org, [p["key"] for p in planned])}
            mem = [LO._memory_row(org, p, today) for p in planned if p["key"] not in live_keys]
            rep = await CC.household_report(conn, org, hid, today=today, extra_stored=mem or None,
                                            horizon_months=max(fields["horizon_months"], OB.DEFAULT_HORIZON_MONTHS))
            reads = await CP.read_yields(conn, today)
            profile = await CP.subject_profile(conn, org, "household", hid, today)
            policies = await CP.live_rows(conn, org, household_id=hid)
        lines = CC.scope_lines(rep, "household")
        view = CP.render(CP.evaluate(fields, lines, CC.scope_liquid(rep, "household"), reads, profile, today))
        if mem:
            print(f"[INFO] {len(mem)} of the {len(planned)} Olivo obligations are not loaded yet — included IN MEMORY "
                  "(load_olivo_obligations.py has not run for real)")
        mine = [r for r in policies if r["scope_type"] == "household"]
        open_ = next((r for r in mine if r["status"] in ("draft", "pending_approval")), None)
        active = next((r for r in mine if r["status"] == "active"), None)
        if open_ is not None and open_["status"] == "pending_approval":
            act = "awaiting approval (nothing to do — approve it in the app as a second person)"
        elif open_ is not None and (open_["reason"] or "").startswith(TAG):
            act = "submit the open demo draft"
        elif open_ is not None:
            act = "refuse — someone else's draft is open for this household; decide or edit it in the app"
        elif active is not None and all(CP._field_text(active[k]) == CP._field_text(fields[k])
                                        for k in CP.POLICY_FIELDS):
            act = "unchanged (the active policy already has these fields)"
        else:
            act = "save a draft and submit it for approval"
        print(f"[PLAN] {act}")
        print_view("Recommended policy (household)", fields, view)
        if dry:
            print("\nDRY RUN — nothing written (read-only transaction).")
            return 0
        if act.startswith(("awaiting", "refuse", "unchanged")):
            return 0 if not act.startswith("refuse") else 1
        async with conn.transaction():
            await LO._ctx(conn, org)
            op = await conn.fetchval("SELECT id FROM users WHERE lower(email) = lower($1) AND org_id = $2::uuid "
                                     "AND COALESCE(status, 'active') = 'active'", email, org)
        if op is None:
            print(f"no active users row for {email!r} in the Olivo org")
            return 1
    finally:
        await conn.close()
    from services.database import get_pool, reset_rls_context, set_rls_context

    tokens = set_rls_context(org, False)
    try:
        pool = await get_pool()
        if open_ is None:
            reach = set(rep.shares)
            draft = await CP.save_draft(pool, org_id=org, actor_id=op, household_id=hid, scope_type="household",
                                        entity_id=None, data={k: fields[k] for k in CP.POLICY_FIELDS if k != "objective"},
                                        reach=reach, reason=REASON)
            pid = draft["id"]
            print(f"[OK] draft v{draft['version_no']} saved ({pid})")
        else:
            pid = str(open_["id"])
        out = await CP.submit(pool, org_id=org, actor_id=op, policy_id=pid, derived=view,
                              scope_label="The household", household_id=hid)
        print(f"[OK] submitted for approval — task {out['task']['id']}; a DIFFERENT holder of manage_cash_policy "
              "approves it in the app (Household → Treasury, or Portfolio → Approvals)")
    finally:
        reset_rls_context(tokens)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
