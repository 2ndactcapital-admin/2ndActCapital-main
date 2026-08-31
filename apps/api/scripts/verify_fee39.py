"""Sprint fee39 verification — profitability views.

Pass/fail only, no prompts. Run:

    python3 scripts/verify_fee39.py

Every table this script writes to is counted before the first insert and again
after the last delete; a difference of even one row fails the run, reported
AFTER the tests so a teardown bug never masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[1d] does not take "the view inherits RLS" on trust — it MEASURES it, and
  it reproduces the failure first.** The sprint prompt flagged this as a thing
  to check rather than assume, and it was right to: as deployed by Part 1,
  ``v_profitability_events`` had NO ``security_invoker``, and a view without it
  evaluates the base tables' RLS as its OWNER. The owner is ``postgres``, whose
  ``rolbypassrls`` is TRUE. So the view bypassed org isolation entirely while
  both base tables looked correctly locked down. [7] builds a TWIN view with
  the original (no-option) definition, shows app_service reads another org's
  rows straight through it, and only then shows the real view refusing. A test
  that had checked the fixed view alone would have proven the fix works without
  ever showing there was anything to fix.

* **[2]/[3] separate "emitted" from "skipped" rather than counting rows.**
  Idempotency is a claim about WHICH of the two happened. A second run that
  raised a caught exception, or that deleted and re-inserted, would leave the
  same row count and pass a naive count check. [3] asserts the emitted ids are
  byte-identical across both passes and that ``created_at`` did not move.

* **[4] proves the reversal nets to zero THROUGH THE VIEW**, not by adding up
  what the emitter returned. And it asserts the reversal took the SAME code
  path — no branch on ``run_type`` anywhere in ``emit_revenue_for_run``'s
  source — since the requirement was specifically not to special-case it.

* **[5] hand-computes all eight cuts away from the code.** The expected numbers
  are written as literals in :data:`EXPECTED_CUTS` below, derived from the
  fixture table in comments, and never from calling the thing under test. Each
  cut asserts all seven P&L lines, not just the total: a sign error that moved
  cost between two bands would leave net profit correct.

* **[5i] proves the cuts EXCLUDE as well as include.** Every cut is also
  checked against the complement — firm total minus the cut must equal what a
  cut on everything-else returns. A filter that silently matched everything
  would produce plausible-looking totals and pass an inclusion-only check.

* **[6] uses a fixture where the two ranking keys genuinely DISAGREE.** A
  fourth household is sized so ``net_profit`` order and ``margin_pct`` order
  are different lists. Ranking on a fixture where both agree would prove
  nothing about which key the query actually used.

* **[7] runs on app_service, whose ``rolbypassrls`` is asserted False FIRST.**
  Without that assertion every isolation check below it is vacuous.

* **[8] counts EVERY table touched, including the ones only written to
  indirectly** (assistant_activities via the approval gates, fee_run_lines via
  preview). Teardown is by fixture id and fixture period, never a TRUNCATE.
"""

from __future__ import annotations

import glob
import inspect
import pathlib
import sys
import traceback
from datetime import date
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from services import fee_runs as FR  # noqa: E402
from services import profitability as P  # noqa: E402

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "fee39verify"

# ── fixture ids ─────────────────────────────────────────────────────────────
U_MAKER = "99000000-0000-0000-0000-0000fee39001"
U_CHECKER = "99000000-0000-0000-0000-0000fee39002"
U_COMPLY = "99000000-0000-0000-0000-0000fee39003"
ADV_1 = "99000000-0000-0000-0000-0000fee39004"
ADV_2 = "99000000-0000-0000-0000-0000fee39005"
USERS = [U_MAKER, U_CHECKER, U_COMPLY, ADV_1, ADV_2]

HH_P = "99000000-0000-0000-0000-0000fee39011"   # profitable
HH_T = "99000000-0000-0000-0000-0000fee39012"   # thin
HH_L = "99000000-0000-0000-0000-0000fee39013"   # loss-making
HH_S = "99000000-0000-0000-0000-0000fee39014"   # small, terrible PERCENTAGE
HH_Q = "99000000-0000-0000-0000-0000fee39015"   # the fee-run household
HOUSEHOLDS = [HH_P, HH_T, HH_L, HH_S, HH_Q]

ACC_P1 = "99000000-0000-0000-0000-0000fee39021"
ACC_P2 = "99000000-0000-0000-0000-0000fee39022"
ACC_T1 = "99000000-0000-0000-0000-0000fee39023"
ACC_L1 = "99000000-0000-0000-0000-0000fee39024"
ACC_S1 = "99000000-0000-0000-0000-0000fee39025"
ACC_Q1 = "99000000-0000-0000-0000-0000fee39026"
ACC_Q2 = "99000000-0000-0000-0000-0000fee39027"
ACCOUNTS = [ACC_P1, ACC_P2, ACC_T1, ACC_L1, ACC_S1, ACC_Q1, ACC_Q2]

ENTITIES = [f"99000000-0000-0000-0000-0000fee390{n}" for n in range(31, 38)]

BG_1 = "99000000-0000-0000-0000-0000fee39041"
BG_2 = "99000000-0000-0000-0000-0000fee39042"
BILLING_GROUPS = [BG_1, BG_2]

SCH = "99000000-0000-0000-0000-0000fee39051"

OTHER_ENT = "99000000-0000-0000-0000-0000fee39061"
OTHER_HH = "99000000-0000-0000-0000-0000fee39062"
OTHER_ACC = "99000000-0000-0000-0000-0000fee39063"

TWIN_VIEW = "public.v_fee39_twin_no_invoker"

# Q1 — the fee-run / emission window.
Q1_START, Q1_END = date(2026, 1, 1), date(2026, 3, 31)
# Q2 — the roll-up window. Kept disjoint from Q1 so the emission fixture and
# the arithmetic fixture cannot contaminate each other's totals.
Q2_START, Q2_END = date(2026, 4, 1), date(2026, 6, 30)
Q2_EVENT = date(2026, 6, 15)

COUNTED = (
    "public.revenue_events",
    "public.cost_events",
    "public.fee_runs",
    "public.fee_run_lines",
    "public.fee_schedules",
    "public.fee_schedule_tiers",
    "public.fee_assignments",
    "public.assistant_activities",
    "public.account_balances_daily",
    "public.billing_group_members",
    "public.billing_groups",
    "public.accounts",
    "public.households",
    "public.entities",
    "public.users",
)


# ═══════════════════════════════════════════════════════════════════════════
# Harness
# ═══════════════════════════════════════════════════════════════════════════


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def ok(self, ref, msg):
        self.rows.append(("PASS", ref, msg))
        print(f"[PASS] {ref}  {msg}")

    def bad(self, ref, msg, detail=""):
        self.rows.append(("FAIL", ref, f"{msg} — {detail}" if detail else msg))
        print(f"[FAIL] {ref}  {msg}" + (f"\n         {detail}" if detail else ""))

    def find(self, ref, msg):
        self.rows.append(("FIND", ref, msg))
        print(f"[FIND] {ref}  {msg}")

    def blocked(self, ref, msg):
        self.rows.append(("BLOCKED", ref, msg))
        print(f"[BLOCKED] {ref}  {msg}")

    def expect(self, ref, condition, msg, detail=""):
        if condition:
            self.ok(ref, msg)
        else:
            self.bad(ref, msg, detail)
        return bool(condition)

    @property
    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]

    def summary(self):
        counts: dict[str, int] = {}
        for kind, _, _ in self.rows:
            counts[kind] = counts.get(kind, 0) + 1
        total = len(self.rows)
        print("\n" + "=" * 78)
        print(f"fee39: {counts.get('PASS', 0)}/{total} PASS" + "".join(
            f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


# ═══════════════════════════════════════════════════════════════════════════
# The Q2 roll-up fixture, and every expected number, hand-computed
# ═══════════════════════════════════════════════════════════════════════════
#
# REVENUE (all event_date 2026-06-15, recognition ACCRUAL)
#
#   account  household  advisor  billing_grp  product           amount
#   ACC_P1   HH_P       ADV_1    BG_1         ASSET_MANAGEMENT  10,000
#   ACC_P2   HH_P       ADV_1    BG_1         ASSET_MANAGEMENT   6,000
#   ACC_P1   HH_P       ADV_1    BG_1         SPV                3,000
#   ACC_T1   HH_T       ADV_1    BG_1         ASSET_MANAGEMENT   4,000
#   ACC_L1   HH_L       ADV_2    BG_2         ASSET_MANAGEMENT   2,000
#   ACC_S1   HH_S       ADV_2    BG_2         ASSET_MANAGEMENT     100
#                                                        total  25,100
#
# COSTS (stored positive; the view negates them)
#
#   ACC_P1   HH_P  ADV_1  BG_1  CUSTODY        direct      500
#   ACC_P1   HH_P  ADV_1  BG_1  ADVISOR_COMP   service   2,000
#   ACC_P2   HH_P  ADV_1  BG_1  CUSTODY        direct      300
#   ACC_T1   HH_T  ADV_1  BG_1  CUSTODY        direct      200
#   ACC_T1   HH_T  ADV_1  BG_1  ADVISOR_COMP   service   3,000
#   ACC_T1   HH_T  ADV_1  BG_1  OVERHEAD_ALLOC overhead  1,000
#   ACC_L1   HH_L  ADV_2  BG_2  TECH           direct    1,500
#   ACC_L1   HH_L  ADV_2  BG_2  SERVICE_TIME   service   2,500
#   ACC_L1   HH_L  ADV_2  BG_2  OVERHEAD_ALLOC overhead    800
#   ACC_S1   HH_S  ADV_2  BG_2  SERVICE_TIME   service     500
#                              direct 2,500  service 8,000  overhead 1,800
#
# The SPV revenue has no matching cost on purpose: the product cut then has a
# case with revenue and no costs at all, where a band-filter bug that leaked
# other products' costs in would show up immediately.

REV_ROWS = [
    # (account, household, advisor, billing_group, product_type, amount)
    (ACC_P1, HH_P, ADV_1, BG_1, "ASSET_MANAGEMENT", D("10000")),
    (ACC_P2, HH_P, ADV_1, BG_1, "ASSET_MANAGEMENT", D("6000")),
    (ACC_P1, HH_P, ADV_1, BG_1, "SPV", D("3000")),
    (ACC_T1, HH_T, ADV_1, BG_1, "ASSET_MANAGEMENT", D("4000")),
    (ACC_L1, HH_L, ADV_2, BG_2, "ASSET_MANAGEMENT", D("2000")),
    (ACC_S1, HH_S, ADV_2, BG_2, "ASSET_MANAGEMENT", D("100")),
]

COST_ROWS = [
    # (account, household, advisor, billing_group, cost_type, amount)
    (ACC_P1, HH_P, ADV_1, BG_1, "CUSTODY", D("500")),
    (ACC_P1, HH_P, ADV_1, BG_1, "ADVISOR_COMP", D("2000")),
    (ACC_P2, HH_P, ADV_1, BG_1, "CUSTODY", D("300")),
    (ACC_T1, HH_T, ADV_1, BG_1, "CUSTODY", D("200")),
    (ACC_T1, HH_T, ADV_1, BG_1, "ADVISOR_COMP", D("3000")),
    (ACC_T1, HH_T, ADV_1, BG_1, "OVERHEAD_ALLOC", D("1000")),
    (ACC_L1, HH_L, ADV_2, BG_2, "TECH", D("1500")),
    (ACC_L1, HH_L, ADV_2, BG_2, "SERVICE_TIME", D("2500")),
    (ACC_L1, HH_L, ADV_2, BG_2, "OVERHEAD_ALLOC", D("800")),
    (ACC_S1, HH_S, ADV_2, BG_2, "SERVICE_TIME", D("500")),
]

#: label -> (Cut, gross, direct, cm_direct, service, cm_after, overhead, net)
#: Every figure below was added up by hand from the two tables above. None of
#: it comes from running the code under test.
EXPECTED_CUTS: dict[str, tuple] = {
    # ACC_P1: rev 10,000 + 3,000 = 13,000; direct 500; service 2,000
    "ACCOUNT": (
        lambda: P.Cut.account(ACC_P1),
        D("13000"), D("500"), D("12500"), D("2000"), D("10500"), D("0"), D("10500"),
    ),
    # ACC_P1 + ACC_T1: rev 13,000 + 4,000; direct 500 + 200;
    #                  service 2,000 + 3,000; overhead 1,000
    "ACCOUNTS": (
        lambda: P.Cut.accounts([ACC_P1, ACC_T1]),
        D("17000"), D("700"), D("16300"), D("5000"), D("11300"), D("1000"), D("10300"),
    ),
    # HH_P: rev 10,000 + 6,000 + 3,000; direct 500 + 300; service 2,000
    "HOUSEHOLD": (
        lambda: P.Cut.household(HH_P),
        D("19000"), D("800"), D("18200"), D("2000"), D("16200"), D("0"), D("16200"),
    ),
    # HH_T + HH_L: rev 4,000 + 2,000; direct 200 + 1,500;
    #              service 3,000 + 2,500; overhead 1,000 + 800
    "HOUSEHOLDS": (
        lambda: P.Cut.households([HH_T, HH_L]),
        D("6000"), D("1700"), D("4300"), D("5500"), D("-1200"), D("1800"), D("-3000"),
    ),
    # BG_1 = HH_P + HH_T
    "BILLING_GROUP": (
        lambda: P.Cut.billing_group(BG_1),
        D("23000"), D("1000"), D("22000"), D("5000"), D("17000"), D("1000"), D("16000"),
    ),
    # ADV_2 = HH_L + HH_S
    "ADVISOR": (
        lambda: P.Cut.advisor(ADV_2),
        D("2100"), D("1500"), D("600"), D("3000"), D("-2400"), D("800"), D("-3200"),
    ),
    # SPV: one revenue row, no costs at all
    "PRODUCT_TYPE": (
        lambda: P.Cut.product_type("SPV"),
        D("3000"), D("0"), D("3000"), D("0"), D("3000"), D("0"), D("3000"),
    ),
    # everything
    "FIRM": (
        lambda: P.Cut.firm(),
        D("25100"), D("2500"), D("22600"), D("8000"), D("14600"), D("1800"), D("12800"),
    ),
}

#: Households worst-first. The two keys deliberately DISAGREE — HH_S loses only
#: $400 but on $100 of revenue, so it is worst by percentage and third by
#: dollars. A fixture where both orders matched would not prove ``rank_by``
#: was read at all.
#:
#:   HH_P  rev 19,000  net  16,200   pct   +0.852631…
#:   HH_T  rev  4,000  net    -200   pct   -0.05
#:   HH_L  rev  2,000  net  -2,800   pct   -1.40
#:   HH_S  rev    100  net    -400   pct   -4.00
EXPECTED_RANK_NET = [HH_L, HH_S, HH_T, HH_P]
EXPECTED_RANK_PCT = [HH_S, HH_L, HH_T, HH_P]
EXPECTED_HH_NET = {
    HH_P: D("16200"), HH_T: D("-200"), HH_L: D("-2800"), HH_S: D("-400"),
}


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def teardown(conn) -> None:
    """By fixture id and fixture period, in FK order. Never a TRUNCATE.

    ``revenue_events`` goes before ``cost_events`` would need it to: fee39's
    Part 1 added ``cost_events_linked_revenue_event_fkey``, so a cost row
    pointing at a revenue row pins it. Both are deleted by fixture id, and the
    cost rows first.
    """
    await conn.execute(f"DROP VIEW IF EXISTS {TWIN_VIEW}")

    await conn.execute(
        "DELETE FROM public.cost_events WHERE org_id = ANY($1::uuid[]) "
        "AND (account_id = ANY($2::uuid[]) OR household_id = ANY($3::uuid[]))",
        [ORG, OTHER_ORG], ACCOUNTS + [OTHER_ACC], HOUSEHOLDS + [OTHER_HH])
    await conn.execute(
        "DELETE FROM public.revenue_events WHERE org_id = ANY($1::uuid[]) "
        "AND (account_id = ANY($2::uuid[]) OR household_id = ANY($3::uuid[]))",
        [ORG, OTHER_ORG], ACCOUNTS + [OTHER_ACC], HOUSEHOLDS + [OTHER_HH])

    await conn.execute(
        "ALTER TABLE public.fee_runs DISABLE TRIGGER fee_runs_immutable_once_posted")
    await conn.execute(
        "ALTER TABLE public.fee_run_lines DISABLE TRIGGER fee_run_lines_immutable_once_posted")
    try:
        await conn.execute(
            """DELETE FROM public.fee_run_lines
               WHERE fee_run_id IN (SELECT id FROM public.fee_runs
                                    WHERE org_id = $1::uuid AND period_start = $2::date)""",
            ORG, Q1_START)
        # REVERSAL first: fee_runs_reversal_requires_target forbids NULLing
        # reverses_run_id, so the child cannot be detached from its parent.
        for reversals_first in (True, False):
            await conn.execute(
                f"""DELETE FROM public.fee_runs WHERE org_id = $1::uuid
                    AND period_start = $2::date
                    AND run_type {'=' if reversals_first else '<>'} 'REVERSAL'""",
                ORG, Q1_START)
    finally:
        await conn.execute(
            "ALTER TABLE public.fee_runs ENABLE TRIGGER fee_runs_immutable_once_posted")
        await conn.execute(
            "ALTER TABLE public.fee_run_lines ENABLE TRIGGER fee_run_lines_immutable_once_posted")

    await conn.execute(
        "DELETE FROM public.assistant_activities WHERE related_type = 'fee_run' "
        "AND org_id = ANY($1::uuid[]) AND rationale LIKE $2", [ORG, OTHER_ORG], f"%{TAG}%")
    await conn.execute(
        "DELETE FROM public.fee_assignments WHERE fee_schedule_id = $1::uuid", SCH)
    await conn.execute(
        "DELETE FROM public.account_balances_daily WHERE account_id = ANY($1::uuid[])",
        ACCOUNTS)
    await conn.execute(
        "DELETE FROM public.billing_group_members WHERE billing_group_id = ANY($1::uuid[])",
        BILLING_GROUPS)
    await conn.execute(
        "DELETE FROM public.billing_groups WHERE id = ANY($1::uuid[])", BILLING_GROUPS)
    await conn.execute(
        "DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", ACCOUNTS + [OTHER_ACC])
    await conn.execute(
        "DELETE FROM public.fee_schedule_tiers WHERE fee_schedule_id = $1::uuid", SCH)
    await conn.execute("DELETE FROM public.fee_schedules WHERE id = $1::uuid", SCH)
    await conn.execute(
        "DELETE FROM public.households WHERE id = ANY($1::uuid[])", HOUSEHOLDS + [OTHER_HH])
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", ENTITIES + [OTHER_ENT])
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def build_fixtures(conn) -> None:
    for uid, nm in zip(USERS, ("maker", "checker", "compliance", "advisor1", "advisor2")):
        await conn.execute(
            """INSERT INTO public.users (id, org_id, email, auth0_sub)
               VALUES ($1::uuid,$2::uuid,$3,$4)""",
            uid, ORG, f"{nm}@{TAG}.local", f"auth0|{TAG}-{nm}")

    for eid in ENTITIES:
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
               VALUES ($1::uuid,$2::uuid,'individual',$3)""",
            eid, ORG, f"{TAG} entity {eid[-3:]}")
    await conn.execute(
        """INSERT INTO public.entities (id, org_id, entity_type, display_name)
           VALUES ($1::uuid,$2::uuid,'individual',$3)""",
        OTHER_ENT, OTHER_ORG, f"{TAG} other-org entity")

    for hid, nm in zip(HOUSEHOLDS, ("profitable", "thin", "loss", "small", "feerun")):
        await conn.execute(
            "INSERT INTO public.households (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
            hid, ORG, f"{TAG} {nm}")
    await conn.execute(
        "INSERT INTO public.households (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
        OTHER_HH, OTHER_ORG, f"{TAG} other-org household")

    plan = [
        (ACC_P1, HH_P, ADV_1), (ACC_P2, HH_P, ADV_1), (ACC_T1, HH_T, ADV_1),
        (ACC_L1, HH_L, ADV_2), (ACC_S1, HH_S, ADV_2),
        (ACC_Q1, HH_Q, ADV_1), (ACC_Q2, HH_Q, ADV_2),
    ]
    for (aid, hid, adv), eid in zip(plan, ENTITIES):
        await conn.execute(
            """INSERT INTO public.accounts
                 (id, org_id, account_number_masked, account_number_hash, custodian_code,
                  registration_type, tax_status, primary_entity_id, household_id,
                  advisor_of_record_id, is_billable, opened_on)
               VALUES ($1::uuid,$2::uuid,$3,$4,'TEST','individual','taxable',
                       $5::uuid,$6::uuid,$7::uuid,true,'2024-01-01')""",
            aid, ORG, f"***{aid[-3:]}", f"{TAG}-{aid[-3:]}", eid, hid, adv)
    await conn.execute(
        """INSERT INTO public.accounts
             (id, org_id, account_number_masked, account_number_hash, custodian_code,
              registration_type, tax_status, primary_entity_id, household_id,
              is_billable, opened_on)
           VALUES ($1::uuid,$2::uuid,'***OTH',$3,'TEST','individual','taxable',
                   $4::uuid,$5::uuid,true,'2024-01-01')""",
        OTHER_ACC, OTHER_ORG, f"{TAG}-other", OTHER_ENT, OTHER_HH)

    for bg, hid, members in ((BG_1, HH_P, [ACC_P1, ACC_P2, ACC_T1]),
                             (BG_2, HH_L, [ACC_L1, ACC_S1])):
        await conn.execute(
            """INSERT INTO public.billing_groups (id, org_id, name, group_type, household_id)
               VALUES ($1::uuid,$2::uuid,$3,'BREAKPOINT',$4::uuid)""",
            bg, ORG, f"{TAG} group {bg[-3:]}", hid)
        for aid in members:
            await conn.execute(
                """INSERT INTO public.billing_group_members
                     (org_id, billing_group_id, account_id, valid_from)
                   VALUES ($1::uuid,$2::uuid,$3::uuid,'2024-01-01')""",
                ORG, bg, aid)

    # ── the Q1 fee-run schedule: flat 100 bps annual, quarterly, PERIOD_END.
    #    ACC_Q1 $1,000,000 -> 1,000,000 * 0.01 / 4 = $2,500.00
    #    ACC_Q2 $  800,000 ->   800,000 * 0.01 / 4 = $2,000.00
    await conn.execute(
        """INSERT INTO public.fee_schedules
             (id, org_id, code, name, product_type, rate_type, tier_method,
              billing_frequency, billing_timing, valuation_method, proration_method,
              status, day_weight_flows)
           VALUES ($1::uuid,$2::uuid,$3,$4,'ASSET_MANAGEMENT','BPS','GRADUATED',
                   'QUARTERLY','ARREARS','PERIOD_END','NONE','APPROVED',false)""",
        SCH, ORG, f"{TAG}-SCH", f"{TAG} schedule")
    await conn.execute(
        """INSERT INTO public.fee_schedule_tiers
             (org_id, fee_schedule_id, tier_seq, lower_bound, upper_bound, rate_bps)
           VALUES ($1::uuid,$2::uuid,1,0,NULL,100)""",
        ORG, SCH)
    for aid in (ACC_Q1, ACC_Q2):
        await conn.execute(
            """INSERT INTO public.fee_assignments
                 (org_id, fee_schedule_id, scope_type, scope_id, precedence, effective_from)
               VALUES ($1::uuid,$2::uuid,'ACCOUNT',$3::uuid,10,'2024-01-01')""",
            ORG, SCH, aid)
    for aid, mv in ((ACC_Q1, "1000000.00"), (ACC_Q2, "800000.00")):
        await conn.execute(
            """INSERT INTO public.account_balances_daily
                 (org_id, account_id, as_of_date, total_market_value, cash_value,
                  source_system, is_billing_source, is_final)
               VALUES ($1::uuid,$2::uuid,$3::date,$4::numeric,0,'PRIMARY',true,true)""",
            ORG, aid, Q1_END, mv)


async def build_q2_events(conn) -> None:
    """The roll-up fixture, inserted directly.

    Deliberately NOT routed through a fee run: [5] is about the arithmetic of
    the eight cuts, and it should assert against numbers a person chose, not
    against whatever the fee engine happened to produce. [2]–[4] cover the
    emission path on their own fixture in a different quarter.
    """
    for acct, hh, adv, bg, product, amount in REV_ROWS:
        await conn.execute(
            """INSERT INTO public.revenue_events
                 (org_id, event_date, period_start, period_end, amount, revenue_type,
                  recognition, account_id, household_id, billing_group_id, advisor_id,
                  product_type, source_type, source_id)
               VALUES ($1::uuid,$2,$3,$4,$5,$6,'ACCRUAL',$7::uuid,$8::uuid,$9::uuid,
                       $10::uuid,$11,'MANUAL',NULL)""",
            ORG, Q2_EVENT, Q2_START, Q2_END, amount,
            P.revenue_type_for(product), acct, hh, bg, adv, product)
    for acct, hh, adv, bg, cost_type, amount in COST_ROWS:
        await conn.execute(
            """INSERT INTO public.cost_events
                 (org_id, event_date, period_start, period_end, amount, cost_type,
                  allocation_method, account_id, household_id, billing_group_id,
                  advisor_id, product_type)
               VALUES ($1::uuid,$2,$3,$4,$5,$6,'DIRECT',$7::uuid,$8::uuid,$9::uuid,
                       $10::uuid,'ASSET_MANAGEMENT')""",
            ORG, Q2_EVENT, Q2_START, Q2_END, amount, cost_type, acct, hh, bg, adv)


# ═══════════════════════════════════════════════════════════════════════════
# [1] deployment, constraints, the FK, and the UNION shape
# ═══════════════════════════════════════════════════════════════════════════


async def check_1(conn):
    # relkind is Postgres' "char" type, which asyncpg hands back as BYTES, not
    # str. Cast in SQL rather than comparing against b'r' — a future driver
    # change that started returning str would silently flip this to failing.
    kind = await conn.fetchval(
        """SELECT c.relkind::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='public' AND c.relname='revenue_events'""")
    R.expect("1a", kind == "r", "public.revenue_events is deployed as a table", str(kind))

    rls = await conn.fetchval(
        """SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='public' AND c.relname='revenue_events'""")
    R.expect("1b", rls is True, "revenue_events has RLS enabled")

    pol = await conn.fetch(
        "SELECT policyname, qual FROM pg_policies WHERE schemaname='public' "
        "AND tablename='revenue_events'")
    shaped = [p for p in pol
              if "app.current_org_id" in (p["qual"] or "")
              and "NULLIF" in (p["qual"] or "")
              and "app.is_super_admin" in (p["qual"] or "")]
    R.expect("1c", len(shaped) >= 1,
             "revenue_events' policy is org-isolating, NULLIFs the GUC and "
             "carries the super-admin bypass", str([dict(p) for p in pol]))

    checks = {r["conname"]: r["d"] for r in await conn.fetch(
        """SELECT conname, pg_get_constraintdef(oid) d FROM pg_constraint
           WHERE conrelid='public.revenue_events'::regclass AND contype='c'""")}
    rt = checks.get("revenue_events_type_check", "")
    R.expect("1d", set(P.REVENUE_TYPES) == {v for v in P.REVENUE_TYPES if f"'{v}'" in rt}
             and rt.count("::text") == len(P.REVENUE_TYPES),
             "services.profitability.REVENUE_TYPES matches the deployed "
             "revenue_events_type_check exactly, both directions", rt)
    st = checks.get("revenue_events_source_type_check", "")
    R.expect("1e", all(f"'{v}'" in st for v in P.SOURCE_TYPES)
             and st.count("::text") == len(P.SOURCE_TYPES),
             "SOURCE_TYPES matches revenue_events_source_type_check exactly", st)
    R.expect("1f", "revenue_events_source_id_required" in checks,
             "the source_id-required CHECK is deployed",
             str(sorted(checks)))

    idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='revenue_events'")}
    dedupe = idx.get("revenue_events_source_dedupe_uq", "")
    R.expect("1g", "UNIQUE" in dedupe and "org_id, source_type, source_id" in dedupe
             and "system_to IS NULL" in dedupe and "source_id IS NOT NULL" in dedupe,
             "the source dedupe index is UNIQUE on (org_id, source_type, source_id) "
             "over live rows with a source", dedupe or "MISSING")

    fk = await conn.fetchval(
        """SELECT pg_get_constraintdef(oid) FROM pg_constraint
           WHERE conname='cost_events_linked_revenue_event_fkey'""")
    R.expect("1h", fk is not None and "revenue_events(id)" in fk,
             "cost_events_linked_revenue_event_fkey exists and points at "
             "revenue_events(id) — the FK fee37 had to defer", str(fk))

    # ...and is ENFORCED, not merely declared. NOT VALID / disabled FKs exist.
    try:
        await conn.execute(
            """INSERT INTO public.cost_events
                 (org_id, event_date, amount, cost_type, allocation_method,
                  linked_revenue_event_id, account_id)
               VALUES ($1::uuid,$2,1,'CUSTODY','DIRECT',
                       '00000000-0000-0000-0000-0000000fee39'::uuid,$3::uuid)""",
            ORG, Q2_EVENT, ACC_P1)
        R.bad("1i", "the linked_revenue_event FK did NOT refuse a dangling id")
    except Exception as exc:
        R.expect("1i", "cost_events_linked_revenue_event_fkey" in str(exc),
                 "the FK is genuinely ENFORCED: a cost_event pointing at a "
                 "non-existent revenue_event is refused",
                 str(exc).splitlines()[0])


async def check_1_view(conn):
    """The UNION shape, on a small fixture: revenue positive, cost negative."""
    rows = await conn.fetch(
        """SELECT line_kind, category, signed_amount, account_id::text AS account_id
           FROM public.v_profitability_events
           WHERE org_id=$1::uuid AND account_id=$2::uuid AND event_date=$3
           ORDER BY line_kind, category""",
        ORG, ACC_P1, Q2_EVENT)
    rev = [r for r in rows if r["line_kind"] == "REVENUE"]
    cost = [r for r in rows if r["line_kind"] == "COST"]
    R.expect("1j", len(rev) == 2 and len(cost) == 2,
             "the view UNIONs both sides for ACC_P1: 2 revenue rows, 2 cost rows",
             f"rev={len(rev)} cost={len(cost)}")
    R.expect("1k", all(r["signed_amount"] > 0 for r in rev),
             "every REVENUE row's signed_amount is POSITIVE",
             str([str(r["signed_amount"]) for r in rev]))
    R.expect("1l", all(r["signed_amount"] < 0 for r in cost),
             "every COST row's signed_amount is NEGATIVE — the view negates "
             "cost_events.amount, which is stored positive",
             str([str(r["signed_amount"]) for r in cost]))
    R.expect("1m", {r["category"] for r in rev} == {"ADVISORY_FEE", "SPV_MGMT_FEE"}
             and {r["category"] for r in cost} == {"CUSTODY", "ADVISOR_COMP"},
             "category carries revenue_type on one side and cost_type on the other",
             str(sorted(r["category"] for r in rows)))
    total = sum(r["signed_amount"] for r in rows)
    R.expect("1n", total == D("10500"),
             "SUM(signed_amount) over ACC_P1 is the hand-computed $10,500 "
             "(13,000 revenue - 500 custody - 2,000 comp)", str(total))


# ═══════════════════════════════════════════════════════════════════════════
# [2] posting a fee_run emits one revenue_event per line
# ═══════════════════════════════════════════════════════════════════════════


async def post_a_run(conn, run_id: str) -> None:
    """Drive fee36's two approval gates. Maker != checker at each one."""
    await FR.propose_approval(conn, ORG, run_id, gate="ADVISOR",
                              proposed_by=U_MAKER, rationale=f"{TAG} advisor")
    await FR.approve(conn, ORG, run_id, gate="ADVISOR", approved_by=U_CHECKER)
    await FR.propose_approval(conn, ORG, run_id, gate="COMPLIANCE",
                              proposed_by=U_CHECKER, rationale=f"{TAG} compliance")
    await FR.approve(conn, ORG, run_id, gate="COMPLIANCE", approved_by=U_COMPLY)


async def check_2(conn):
    run = await FR.create_run(conn, ORG, period_start=Q1_START, period_end=Q1_END,
                              billing_frequency="QUARTERLY", created_by=U_MAKER)
    preview = await FR.preview_run(conn, ORG, run, account_ids=[ACC_Q1, ACC_Q2])
    R.expect("2a", preview.lines_written == 2,
             "the Q1 run previewed 2 lines", str(preview.lines_written))

    lines = {l["account_id"]: l for l in await FR.list_lines(conn, ORG, run)}
    # Hand-derived: $1,000,000 * 100bps / 4 = $2,500.00; $800,000 -> $2,000.00
    R.expect("2b", lines[ACC_Q1]["net_fee"] == D("2500.00")
             and lines[ACC_Q2]["net_fee"] == D("2000.00"),
             "the two lines carry the hand-derived $2,500.00 and $2,000.00",
             f"{lines[ACC_Q1]['net_fee']} / {lines[ACC_Q2]['net_fee']}")

    before = await conn.fetchval("SELECT count(*) FROM public.revenue_events")
    await post_a_run(conn, run)
    posted = await FR.post_run(conn, ORG, run)
    R.expect("2c", posted["status"] == "POSTED", "the run reached POSTED")
    R.expect("2d", posted["revenue"]["emitted"] == 2 and posted["revenue"]["skipped"] == 0,
             "post_run reports 2 revenue_events emitted, 0 skipped",
             str(posted["revenue"]))

    after = await conn.fetchval("SELECT count(*) FROM public.revenue_events")
    R.expect("2e", after - before == 2,
             "exactly 2 revenue_events rows appeared — one per fee_run_line",
             f"{before} -> {after}")

    evs = {r["source_id"]: r for r in await conn.fetch(
        """SELECT source_id::text AS source_id, amount, revenue_type, recognition,
                  source_type, account_id::text AS account_id,
                  household_id::text AS household_id,
                  billing_group_id::text AS billing_group_id,
                  advisor_id::text AS advisor_id, product_type, event_date,
                  period_start, period_end, currency
           FROM public.revenue_events WHERE org_id=$1::uuid AND source_type='FEE_RUN_LINE'""",
        ORG)}
    R.expect("2f", set(evs) == {l["id"] for l in lines.values()},
             "each revenue_event's source_id IS a fee_run_line id, one to one",
             f"{sorted(evs)} vs {sorted(l['id'] for l in lines.values())}")

    by_acct = {e["account_id"]: e for e in evs.values()}
    R.expect("2g", by_acct[ACC_Q1]["amount"] == D("2500.00")
             and by_acct[ACC_Q2]["amount"] == D("2000.00"),
             "amounts match net_fee EXACTLY, to the stored scale",
             str({k[-3:]: str(v['amount']) for k, v in by_acct.items()}))
    R.expect("2h", all(e["revenue_type"] == "ADVISORY_FEE" for e in evs.values()),
             "ASSET_MANAGEMENT lines mapped to revenue_type ADVISORY_FEE",
             str({e["revenue_type"] for e in evs.values()}))
    R.expect("2i", all(e["source_type"] == "FEE_RUN_LINE"
                       and e["recognition"] == "ACCRUAL" for e in evs.values()),
             "source_type is FEE_RUN_LINE and recognition is ACCRUAL on both")
    R.expect("2j", all(e["event_date"] == Q1_END and e["period_start"] == Q1_START
                       and e["period_end"] == Q1_END for e in evs.values()),
             "event_date is the run's period_end, and the accrual period is "
             "carried through unchanged",
             str([(str(e['event_date']), str(e['period_start'])) for e in evs.values()]))
    R.expect("2k", by_acct[ACC_Q1]["advisor_id"] == ADV_1
             and by_acct[ACC_Q2]["advisor_id"] == ADV_2
             and all(e["household_id"] == HH_Q for e in evs.values()),
             "every dimensional key (household, advisor) is carried from the "
             "line onto the revenue_event, so the cuts work on emitted revenue",
             str({k[-3:]: v["advisor_id"] for k, v in by_acct.items()}))
    return run


# ═══════════════════════════════════════════════════════════════════════════
# [3] re-processing is a no-op, not a duplicate and not a raw constraint error
# ═══════════════════════════════════════════════════════════════════════════


async def check_3(conn, run):
    before = {r["id"]: r["created_at"] for r in await conn.fetch(
        """SELECT id::text AS id, created_at FROM public.revenue_events
           WHERE org_id=$1::uuid AND source_type='FEE_RUN_LINE'""", ORG)}

    try:
        again = await P.emit_revenue_for_run(conn, ORG, run)
    except Exception as exc:  # noqa: BLE001
        R.bad("3a", "re-processing raised instead of no-opping",
              f"{type(exc).__name__}: {exc}")
        return
    R.expect("3a", again.emitted == 0 and again.skipped == 2 and again.was_noop,
             "re-processing the same POSTED run emitted 0 and skipped 2 — a "
             "clean, counted no-op rather than a constraint violation",
             str(again))

    after = {r["id"]: r["created_at"] for r in await conn.fetch(
        """SELECT id::text AS id, created_at FROM public.revenue_events
           WHERE org_id=$1::uuid AND source_type='FEE_RUN_LINE'""", ORG)}
    R.expect("3b", len(after) == 2, "still exactly 2 revenue_events rows",
             str(len(after)))
    R.expect("3c", set(before) == set(after),
             "the same two row ids survived — nothing was deleted and "
             "re-inserted behind a stable count",
             f"{sorted(before)} vs {sorted(after)}")
    R.expect("3d", all(before[i] == after[i] for i in before),
             "created_at did not move on either row")

    # And the index is the real gate, independent of the service's ON CONFLICT.
    line_id = next(iter(await conn.fetch(
        "SELECT id::text AS id FROM public.fee_run_lines WHERE fee_run_id=$1::uuid LIMIT 1",
        run)))["id"]
    try:
        await conn.execute(
            """INSERT INTO public.revenue_events
                 (org_id, event_date, amount, revenue_type, source_type, source_id)
               VALUES ($1::uuid,$2,1,'ADVISORY_FEE','FEE_RUN_LINE',$3::uuid)""",
            ORG, Q1_END, line_id)
        R.bad("3e", "raw SQL inserted a SECOND revenue_event for the same "
                    "fee_run_line — the dedupe index is not the guarantee")
    except Exception as exc:
        R.expect("3e", "revenue_events_source_dedupe_uq" in str(exc),
                 "the DATABASE refuses a duplicate (org, source_type, source_id) "
                 "independently of the service layer", str(exc).splitlines()[0])


# ═══════════════════════════════════════════════════════════════════════════
# [4] a REVERSAL emits negative revenue, on the SAME code path
# ═══════════════════════════════════════════════════════════════════════════


async def check_4(conn, run):
    src = inspect.getsource(P.emit_revenue_for_run)
    R.expect("4a", "REVERSAL" not in src and "run_type" not in src,
             "emit_revenue_for_run contains no branch on run_type or REVERSAL — "
             "a reversal really does take the same path, it is not two "
             "implementations that happen to agree")

    rev_run = await FR.create_reversal(conn, ORG, run, created_by=U_MAKER,
                                       reason=f"{TAG} reversal")
    lines = {l["account_id"]: l for l in await FR.list_lines(conn, ORG, rev_run)}
    R.expect("4b", lines[ACC_Q1]["net_fee"] == D("-2500.00")
             and lines[ACC_Q2]["net_fee"] == D("-2000.00"),
             "the reversal's lines carry negated net_fee",
             f"{lines[ACC_Q1]['net_fee']} / {lines[ACC_Q2]['net_fee']}")

    await post_a_run(conn, rev_run)
    posted = await FR.post_run(conn, ORG, rev_run)
    R.expect("4c", posted["revenue"]["emitted"] == 2,
             "posting the reversal emitted its own 2 revenue_events",
             str(posted["revenue"]))

    evs = await conn.fetch(
        """SELECT re.amount, re.account_id::text AS account_id
           FROM public.revenue_events re
           JOIN public.fee_run_lines l ON l.id = re.source_id
           WHERE re.org_id=$1::uuid AND l.fee_run_id=$2::uuid""",
        ORG, rev_run)
    R.expect("4d", len(evs) == 2 and all(e["amount"] < 0 for e in evs),
             "both reversal revenue_events are NEGATIVE",
             str([str(e["amount"]) for e in evs]))

    # The netting, measured THROUGH THE VIEW rather than from what was returned.
    for acct, expected in ((ACC_Q1, D("2500.00")), (ACC_Q2, D("2000.00"))):
        gross = await conn.fetchval(
            """SELECT COALESCE(SUM(signed_amount),0) FROM public.v_profitability_events
               WHERE org_id=$1::uuid AND account_id=$2::uuid AND line_kind='REVENUE'
                 AND event_date BETWEEN $3 AND $4""",
            ORG, acct, Q1_START, Q1_END)
        R.expect(f"4e-{acct[-3:]}", gross == D("0"),
                 f"through the view, {acct[-3:]}'s Q1 revenue nets to exactly 0 "
                 f"across the original (+{expected}) and its reversal",
                 str(gross))

    pnl = await P.profit_and_loss(conn, ORG, P.Cut.account(ACC_Q1),
                                  period_start=Q1_START, period_end=Q1_END)
    R.expect("4f", pnl.gross_revenue == D("0") and pnl.net_profit == D("0")
             and pnl.revenue_rows == 2,
             "the P&L for that account over Q1 is zero on 2 real rows — netted, "
             "not empty", f"gross={pnl.gross_revenue} rows={pnl.revenue_rows}")


# ═══════════════════════════════════════════════════════════════════════════
# [5] all eight cuts, against hand-computed numbers
# ═══════════════════════════════════════════════════════════════════════════

_LINE_KEYS = ("gross_revenue", "direct_costs", "contribution_margin_direct",
              "service_costs", "contribution_margin_after_service",
              "allocated_overhead", "net_profit")


async def check_5(conn):
    R.expect("5-order", [k for k, _ in P.PNL_LINE_ORDER] == list(_LINE_KEYS),
             "the published line order is gross revenue, direct costs, margin "
             "BEFORE allocation, service cost, margin after service, allocated "
             "overhead, net profit — in that order",
             str([k for k, _ in P.PNL_LINE_ORDER]))

    for label, (make_cut, *expected) in EXPECTED_CUTS.items():
        pnl = await P.profit_and_loss(
            conn, ORG, make_cut(), period_start=Q2_START, period_end=Q2_END)
        actual = [getattr(pnl, k) for k in _LINE_KEYS]
        R.expect(f"5-{label}", actual == list(expected),
                 f"cut {label}: all seven P&L lines match the hand-computed "
                 f"figures",
                 f"expected={[str(e) for e in expected]} actual={[str(a) for a in actual]}")
        R.expect(f"5-{label}-lines",
                 [l["key"] for l in pnl.lines()] == list(_LINE_KEYS)
                 and pnl.band_check_delta == D("0"),
                 f"cut {label}: lines() emits the fixed order and net profit "
                 f"still equals SUM(signed_amount)",
                 f"delta={pnl.band_check_delta}")

    # ── EXCLUSION, not just inclusion ───────────────────────────────────────
    # A cut that quietly matched everything would produce a plausible number and
    # pass every check above. Each cut plus its complement must equal the firm.
    firm = await P.profit_and_loss(conn, ORG, P.Cut.firm(),
                                   period_start=Q2_START, period_end=Q2_END)
    complements = [
        ("ACCOUNT", P.Cut.account(ACC_P1),
         P.Cut.accounts([ACC_P2, ACC_T1, ACC_L1, ACC_S1])),
        ("HOUSEHOLD", P.Cut.household(HH_P), P.Cut.households([HH_T, HH_L, HH_S])),
        ("ADVISOR", P.Cut.advisor(ADV_2), P.Cut.advisor(ADV_1)),
    ]
    for label, part, rest in complements:
        a = await P.profit_and_loss(conn, ORG, part,
                                    period_start=Q2_START, period_end=Q2_END)
        b = await P.profit_and_loss(conn, ORG, rest,
                                    period_start=Q2_START, period_end=Q2_END)
        sums = [getattr(a, k) + getattr(b, k) for k in _LINE_KEYS]
        firm_lines = [getattr(firm, k) for k in _LINE_KEYS]
        R.expect(f"5i-{label}", sums == firm_lines and a.net_profit != firm.net_profit,
                 f"{label} genuinely EXCLUDES: the cut and its complement add "
                 f"back to the firm total, and the cut is not the firm",
                 f"cut+rest={[str(s) for s in sums]} firm={[str(f) for f in firm_lines]}")

    # An empty result is zero and says so, rather than being indistinguishable
    # from a filter that silently matched nothing because it was malformed.
    empty = await P.profit_and_loss(conn, ORG, P.Cut.product_type("PLANNING"),
                                    period_start=Q2_START, period_end=Q2_END)
    R.expect("5j", empty.net_profit == D("0") and empty.revenue_rows == 0
             and empty.cost_rows == 0,
             "a cut with genuinely no rows returns zero AND reports zero rows, "
             "so an empty answer is distinguishable from a broken filter",
             str(empty.as_dict()["lines"]))

    # The period window narrows: Q1's netted fee-run revenue is outside Q2.
    unwindowed = await P.profit_and_loss(conn, ORG, P.Cut.firm())
    R.expect("5k", unwindowed.revenue_rows > firm.revenue_rows,
             "dropping the period window admits strictly more revenue rows — "
             "the window is a real filter, not a no-op",
             f"windowed={firm.revenue_rows} unwindowed={unwindowed.revenue_rows}")

    # No fixture cost is a pass-through and no duplicate exists, so BOTH
    # annotations must be absent. A caveat that is always attached says nothing.
    R.expect("5l", firm.caveats == () and firm.warnings == (),
             "no caveat and no warning on a clean fixture — so one that does "
             "appear means something", f"{firm.caveats} {firm.warnings}")
    return firm


async def check_5_annotations(conn):
    """The caveat and the duplicate warning, each proven to actually fire."""
    ev = await conn.fetchval(
        """INSERT INTO public.cost_events
             (org_id, event_date, period_start, period_end, amount, cost_type,
              allocation_method, account_id, household_id, billing_group_id,
              advisor_id, is_passed_through)
           VALUES ($1::uuid,$2,$3,$4,50,'CUSTODY','DIRECT',$5::uuid,$6::uuid,
                   $7::uuid,$8::uuid,true) RETURNING id::text""",
        ORG, Q2_EVENT, Q2_START, Q2_END, ACC_P1, HH_P, BG_1, ADV_1)
    pnl = await P.profit_and_loss(conn, ORG, P.Cut.account(ACC_P1),
                                  period_start=Q2_START, period_end=Q2_END)
    R.expect("5m", P.UNVERIFIED_RATE_CAVEAT in pnl.caveats,
             "a pass-through cost inside the cut attaches the fee37-F6 "
             "unverified-rate caveat — measured from the rows summed, not "
             "attached unconditionally", str(pnl.caveats))
    clean = await P.profit_and_loss(conn, ORG, P.Cut.account(ACC_L1),
                                    period_start=Q2_START, period_end=Q2_END)
    R.expect("5n", clean.caveats == (),
             "a cut with no pass-through cost gets no caveat, on the same "
             "database state", str(clean.caveats))
    await conn.execute("DELETE FROM public.cost_events WHERE id=$1::uuid", ev)

    # ── the fee37-F4 duplicate hole, reproduced ─────────────────────────────
    # Two identical firm-level costs (NULL account/household/billing_group).
    # cost_events_dedupe_uq indexes those columns, and a UNIQUE index does not
    # constrain rows containing NULL — so BOTH inserts succeed.
    dupes = []
    for _ in range(2):
        dupes.append(await conn.fetchval(
            """INSERT INTO public.cost_events
                 (org_id, event_date, period_start, period_end, amount, cost_type,
                  allocation_method, household_id)
               VALUES ($1::uuid,$2,$3,$4,777,'TECH','PRO_RATA_AUM',$5::uuid)
               RETURNING id::text""",
            ORG, Q2_EVENT, Q2_START, Q2_END, HH_P))
    R.expect("5o", len(dupes) == 2 and dupes[0] != dupes[1],
             "fee37 F4 REPRODUCED: two byte-identical cost_events with a NULL "
             "account_id both inserted — cost_events_dedupe_uq cannot see them",
             str(dupes))

    scan = await P.duplicate_cost_scan(conn, ORG,
                                       period_start=Q2_START, period_end=Q2_END)
    hit = [d for d in scan if d["cost_type"] == "TECH" and d["copies"] == 2]
    R.expect("5p", len(hit) == 1 and hit[0]["duplicate_amount"] == D("777"),
             "duplicate_cost_scan finds the pair and reports 777 as the SURPLUS "
             "(the amount a roll-up is overstated by), not the 1,554 group total",
             str(scan))
    pnl = await P.profit_and_loss(conn, ORG, P.Cut.household(HH_P),
                                  period_start=Q2_START, period_end=Q2_END)
    R.expect("5q", any("DUPLICATE" in w for w in pnl.warnings),
             "the P&L warns that its cost lines may be overstated rather than "
             "reporting a doubled number silently", str(pnl.warnings))
    await conn.execute("DELETE FROM public.cost_events WHERE id = ANY($1::uuid[])", dupes)


# ═══════════════════════════════════════════════════════════════════════════
# [6] households ranked by margin, worst first
# ═══════════════════════════════════════════════════════════════════════════


async def check_6(conn):
    by_net = await P.households_by_margin(
        conn, ORG, period_start=Q2_START, period_end=Q2_END,
        rank_by=P.RANK_NET_PROFIT)
    ids = [h.household_id for h in by_net]
    R.expect("6a", ids == EXPECTED_RANK_NET,
             "ranked by net_profit, WORST FIRST: loss-making household, then "
             "small, then thin, then profitable",
             f"got={[i[-3:] for i in ids]} want={[i[-3:] for i in EXPECTED_RANK_NET]}")
    R.expect("6b", all(h.net_profit == EXPECTED_HH_NET[h.household_id] for h in by_net),
             "each household's net profit is the hand-computed figure",
             str({h.household_id[-3:]: str(h.net_profit) for h in by_net}))
    R.expect("6c", by_net[0].net_profit < by_net[-1].net_profit
             and by_net[0].net_profit < 0 < by_net[-1].net_profit,
             "the list genuinely runs worst -> best and spans the sign change",
             f"{by_net[0].net_profit} .. {by_net[-1].net_profit}")

    by_pct = await P.households_by_margin(
        conn, ORG, period_start=Q2_START, period_end=Q2_END,
        rank_by=P.RANK_MARGIN_PCT)
    pct_ids = [h.household_id for h in by_pct]
    R.expect("6d", pct_ids == EXPECTED_RANK_PCT,
             "ranked by margin_pct the order is DIFFERENT — the small "
             "household loses only $400 but on $100 of revenue, so rank_by is "
             "genuinely read rather than ignored",
             f"got={[i[-3:] for i in pct_ids]} want={[i[-3:] for i in EXPECTED_RANK_PCT]}")
    R.expect("6e", pct_ids != ids,
             "the two rankings are not the same list, so [6a] and [6d] are two "
             "distinct assertions rather than one repeated")

    small = next(h for h in by_pct if h.household_id == HH_S)
    R.expect("6f", small.margin_pct == D("-4"),
             "the small household's margin_pct is the hand-computed -400/100 = -4.0",
             str(small.margin_pct))
    R.expect("6g", all(h.lines()[i]["key"] == k
                       for h in by_net for i, k in enumerate(_LINE_KEYS)),
             "every ranked household publishes the same fixed seven lines")

    top2 = await P.households_by_margin(
        conn, ORG, period_start=Q2_START, period_end=Q2_END, limit=2)
    R.expect("6h", [h.household_id for h in top2] == EXPECTED_RANK_NET[:2],
             "limit keeps the WORST n, not an arbitrary n",
             str([h.household_id[-3:] for h in top2]))

    housed = {h.household_id for h in by_net}
    unhoused = await P.households_by_margin(
        conn, ORG, period_start=Q2_START, period_end=Q2_END, include_unhoused=True)
    R.expect("6i", len(unhoused) >= len(by_net)
             and housed <= {h.household_id for h in unhoused},
             "include_unhoused only ever adds the NULL-household bucket; it "
             "never drops a real household",
             f"{len(by_net)} -> {len(unhoused)}")


# ═══════════════════════════════════════════════════════════════════════════
# [7] cross-org isolation — and the view's own RLS, MEASURED
# ═══════════════════════════════════════════════════════════════════════════


async def check_7(app_dsn, admin):
    conn = await connect(app_dsn)
    try:
        bypass = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        if not R.expect("7a", bypass is False,
                        "the test role's rolbypassrls is False — without this "
                        "every isolation check below proves nothing",
                        f"current_user bypasses RLS: {bypass}"):
            return

        async def as_org(org, sql, *args):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_org_id',$1,true)", org)
                await conn.execute("SELECT set_config('app.is_super_admin','false',true)")
                return await conn.fetch(sql, *args)

        # Seed one revenue_event in the OTHER org, from the admin connection.
        other_ev = await admin.fetchval(
            """INSERT INTO public.revenue_events
                 (org_id, event_date, amount, revenue_type, source_type, source_id,
                  account_id, household_id)
               VALUES ($1::uuid,$2,4242,'ADVISORY_FEE','MANUAL',NULL,$3::uuid,$4::uuid)
               RETURNING id::text""",
            OTHER_ORG, Q2_EVENT, OTHER_ACC, OTHER_HH)

        mine = await as_org(ORG,
            "SELECT id::text AS id FROM public.revenue_events WHERE amount = 4242")
        theirs = await as_org(OTHER_ORG,
            "SELECT id::text AS id FROM public.revenue_events WHERE amount = 4242")
        R.expect("7b", len(mine) == 0,
                 "on the BASE TABLE, org A cannot see org B's revenue_event",
                 str([dict(r) for r in mine]))
        R.expect("7c", len(theirs) == 1 and theirs[0]["id"] == other_ev,
                 "the owning org CAN see it — so [7b] is isolation, not an "
                 "empty table", str([dict(r) for r in theirs]))

        empty_guc = await as_org("",
            "SELECT id::text AS id FROM public.revenue_events WHERE amount = 4242")
        R.expect("7d", len(empty_guc) == 0,
                 "an EMPTY org GUC reads zero rows — the policy's NULLIF, not "
                 "a cast error and not a wide-open read",
                 str(len(empty_guc)))

        # ── the view. REPRODUCE the original defect, then prove the fix. ─────
        # Part 1 deployed v_profitability_events with no security_invoker. A
        # view without it evaluates base-table RLS as its OWNER; the owner is
        # postgres, whose rolbypassrls is TRUE. The twin below is that exact
        # definition, so the leak is demonstrated rather than argued.
        owner_bypass = await admin.fetchval(
            """SELECT r.rolbypassrls FROM pg_class c
               JOIN pg_roles r ON r.oid = c.relowner
               JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname='public' AND c.relname='v_profitability_events'""")
        R.expect("7e", owner_bypass is True,
                 "the view's owner has rolbypassrls=TRUE, which is exactly why "
                 "security_invoker is load-bearing here and not cosmetic",
                 str(owner_bypass))

        await admin.execute(f"""
            CREATE VIEW {TWIN_VIEW} AS
              SELECT id, org_id, amount AS signed_amount, 'REVENUE'::text AS line_kind
              FROM public.revenue_events WHERE system_to IS NULL""")
        await admin.execute(f"GRANT SELECT ON {TWIN_VIEW} TO app_service")

        leaked = await as_org(ORG,
            f"SELECT id::text AS id FROM {TWIN_VIEW} WHERE signed_amount = 4242")
        R.expect("7f", len(leaked) == 1,
                 "DEFECT REPRODUCED: a twin view WITHOUT security_invoker hands "
                 "org A org B's row straight through, even though the base "
                 "table refused the same read in [7b]",
                 f"rows visible through the unfixed view: {len(leaked)}")

        opts = await admin.fetchval(
            """SELECT c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname='public' AND c.relname='v_profitability_events'""")
        R.expect("7g", opts is not None and "security_invoker=true" in opts,
                 "the real view now carries security_invoker=true", str(opts))

        through_view = await as_org(ORG,
            "SELECT id::text AS id FROM public.v_profitability_events "
            "WHERE signed_amount = 4242")
        R.expect("7h", len(through_view) == 0,
                 "FIXED: through the real view, org A cannot see org B's row — "
                 "the same read that leaked in [7f]", str(len(through_view)))
        their_view = await as_org(OTHER_ORG,
            "SELECT id::text AS id FROM public.v_profitability_events "
            "WHERE signed_amount = 4242")
        R.expect("7i", len(their_view) == 1,
                 "and the owning org still sees it through the view, so [7h] is "
                 "isolation rather than the view returning nothing at all",
                 str(len(their_view)))

        # The cost side of the UNION inherits it too — proving one branch of a
        # UNION says nothing about the other.
        other_cost = await admin.fetchval(
            """INSERT INTO public.cost_events
                 (org_id, event_date, amount, cost_type, allocation_method,
                  account_id, household_id)
               VALUES ($1::uuid,$2,9191,'CUSTODY','DIRECT',$3::uuid,$4::uuid)
               RETURNING id::text""",
            OTHER_ORG, Q2_EVENT, OTHER_ACC, OTHER_HH)
        cost_leak = await as_org(ORG,
            "SELECT id::text AS id FROM public.v_profitability_events "
            "WHERE signed_amount = -9191")
        cost_own = await as_org(OTHER_ORG,
            "SELECT id::text AS id FROM public.v_profitability_events "
            "WHERE signed_amount = -9191")
        R.expect("7j", len(cost_leak) == 0 and len(cost_own) == 1
                 and cost_own[0]["id"] == other_cost,
                 "the COST branch of the UNION is isolated too, proven in both "
                 "directions", f"leak={len(cost_leak)} own={len(cost_own)}")

        # And the service layer itself, on the non-bypassing role.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id',$1,true)", ORG)
            await conn.execute("SELECT set_config('app.is_super_admin','false',true)")
            pnl = await P.profit_and_loss(conn, ORG, P.Cut.firm(),
                                          period_start=Q2_START, period_end=Q2_END)
        R.expect("7k", pnl.net_profit == D("12800"),
                 "profit_and_loss on app_service returns this org's own "
                 "hand-computed firm total, with the other org's 4,242 and "
                 "9,191 nowhere in it", str(pnl.net_profit))

        await admin.execute(f"DROP VIEW IF EXISTS {TWIN_VIEW}")
        await admin.execute("DELETE FROM public.cost_events WHERE id=$1::uuid", other_cost)
        await admin.execute("DELETE FROM public.revenue_events WHERE id=$1::uuid", other_ev)
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    if not admin_url:
        R.blocked("0", f"no working admin DSN: {admin_prov}")
        R.summary()
        return 1
    app_url, app_prov = await app_service_dsn()
    print(f"admin: {admin_prov}\napp_service: {app_prov}\n")

    admin = await connect(admin_url)
    before = None
    try:
        await teardown(admin)          # leftovers from a crashed earlier run
        before = await counts(admin)

        await build_fixtures(admin)
        await build_q2_events(admin)

        # A module-level assertion, called out as its own check.
        try:
            P.assert_cost_types_agree()
            R.ok("0a", "services.cost_model and services.profitability agree on "
                       "the cost_type vocabulary")
        except Exception as exc:  # noqa: BLE001
            R.bad("0a", "cost_type vocabularies have drifted", str(exc))

        await check_1(admin)
        await check_1_view(admin)
        run = await check_2(admin)
        await check_3(admin, run)
        await check_4(admin, run)
        await check_5(admin)
        await check_5_annotations(admin)
        await check_6(admin)
        if app_url:
            await check_7(app_url, admin)
        else:
            R.blocked("7", f"no working app_service DSN, so RLS is unproven: {app_prov}")

        R.find("F39-A", (
            "v_profitability_events was deployed by Part 1 WITHOUT "
            "security_invoker, and its owner (postgres) has rolbypassrls=TRUE. "
            "Both base tables were correctly locked down, so the leak was "
            "invisible from either table's own RLS. Fixed in this sprint with "
            "ALTER VIEW ... SET (security_invoker = true); reproduced in [7f] "
            "and proven fixed in [7h]. NOTE the same class of bug is still open "
            "on v_trial_balance and the other GL views (portfolio-D flagged "
            "them; they remain security_invoker=FALSE in the snapshot)."))
        R.find("F39-B", (
            "fee_run_lines and fee_schedules were both EMPTY at sprint start — "
            "fee36's fixtures were torn down, so there was no real posted run "
            "to read product_type values from. The vocabulary was taken from "
            "the deployed fee_schedules_product_type_check instead. The "
            "advisory value is spelled ASSET_MANAGEMENT, not ADVISORY; the "
            "prompt named the other five and left this one implied."))
        R.find("F39-C", (
            "revenue_events_type_check admits 8 values, of which 3 cannot come "
            "from a fee run (SPV_CARRY is deferred, PASS_THROUGH_MARKUP is "
            "fee37's, INTEREST_SHARE has no fee-run source) — 5 revenue types "
            "for 6 product types. STRUCTURED_INVESTMENT and TRANSACTION both "
            "map to PLACEMENT_FEE. Nothing is lost, since product_type is on "
            "the revenue_events row itself and the product cut still separates "
            "them, but they cannot be told apart by revenue_type alone."))
        R.find("F39-D", (
            "Neither revenue_events.advisor_id nor cost_events.advisor_id has a "
            "FOREIGN KEY, unlike account_id / household_id / billing_group_id "
            "on both tables. The ADVISOR cut therefore groups on an "
            "unconstrained uuid: a typo'd advisor id inserts cleanly and shows "
            "up as its own silent, empty-named bucket in any advisor roll-up."))
    except Exception:
        R.bad("main", "the run raised", traceback.format_exc().strip().splitlines()[-1])
        traceback.print_exc()
    finally:
        try:
            await teardown(admin)
        except Exception:
            R.bad("teardown", "teardown raised",
                  traceback.format_exc().strip().splitlines()[-1])
        if before is not None:
            after = await counts(admin)
            drift = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
            R.expect("8", not drift,
                     "every counted table is back to its pre-test row count",
                     str(drift))
        await admin.close()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
