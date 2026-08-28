"""Sprint fee36 verification — fee runs, approvals, reversal, reproducibility.

Pass/fail only, no prompts. Run:

    python3 scripts/verify_fee36.py

Unlike fee35 this sprint WRITES REAL ROWS, so the discipline that matters most
here is teardown. Every table this script touches is counted before the first
insert and again after the last delete, and a difference of even one row fails
the run — after the tests have already reported, so a teardown bug never
masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[1] reproduces the two trigger bugs before proving the fixes.** The
  original ``fee_run_lines_prevent_posted_mutation`` ended ``RETURN NEW``,
  which in a BEFORE DELETE trigger means RETURN NULL, which means SKIP THE
  DELETE. Every delete of a fee_run_line reported ``DELETE 0`` and changed
  nothing. The check installs the original function body, demonstrates the
  silent no-op, reinstalls the fix and demonstrates the delete. A fix shown
  only to work, never shown to address the symptom, is not proven.

* **[3] compares against fee35, not against itself.** The service's stored
  ``net_fee`` is compared to ``calculate_account_fee`` called directly on the
  SAME ``AccountCalcRequest`` the service loaded — and separately to a
  hand-derived literal, so a service and an engine that were wrong in the same
  way would still fail.

* **[6] proves BOTH directions of F4.** One account with an active BREAKPOINT
  membership resolves; one without raises ``GroupScopeMissingError`` FROM THE
  ENGINE. A resolver that returned ``None`` for everybody passes the second
  test alone; one that returned a group for everybody passes the first alone.

* **[7] attacks the database directly.** Raw SQL on a fresh connection, not
  through ``services.fee_runs``. An application that refuses to update a posted
  run proves nothing about whether the row can be updated.

* **[9] proves the hash is SENSITIVE, not merely stable.** Any hash function
  reproduces its own output. The check also back-dates a new exclusion into an
  already-posted period, re-loads the inputs and shows the hash MOVES. A
  constant would pass the first half.

* **[10] runs on app_service, whose ``rolbypassrls`` is asserted False first.**
  A cross-org check on the postgres DSN passes vacuously.
"""

from __future__ import annotations

import asyncio
import glob
import json
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

from services.fee_calc import (  # noqa: E402
    ENGINE_VERSION,
    GroupScopeMissingError,
    calculate_account_fee,
)
from services.fee_run_inputs import (  # noqa: E402
    AmbiguousBillingGroupError,
    CreditBasisUnavailableError,
    canonical_inputs,
    load_account_calc_request,
    resolve_billing_group_id,
    resolve_credit_basis,
    snapshot_hash,
)
from services import fee_runs as FR  # noqa: E402

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

#: Every fixture row this script writes carries this marker somewhere
#: greppable, so teardown is by tag and the row-count check is the backstop —
#: never an unconditional TRUNCATE, per CLAUDE.md.
TAG = "fee36verify"

#: Tables whose row counts must be identical before and after.
COUNTED = [
    "public.fee_runs", "public.fee_run_lines", "public.fee_schedules",
    "public.fee_schedule_tiers", "public.fee_assignments", "public.fee_exclusions",
    "public.fee_discounts", "public.fee_credits", "public.billing_groups",
    "public.billing_group_members", "public.accounts", "public.households",
    "public.account_balances_daily", "public.account_flows",
    "public.assistant_activities", "public.entities", "public.users",
    "public.spvs", "public.spv_subscriptions", "public.spv_transactions",
    "public.spv_transaction_allocations",
]

# Deterministic fixture ids — makes teardown exact and reruns idempotent.
U_MAKER = "99000000-0000-0000-0000-0000fee36001"
U_CHECKER = "99000000-0000-0000-0000-0000fee36002"
U_COMPLY = "99000000-0000-0000-0000-0000fee36003"
E_A = "99000000-0000-0000-0000-0000fee36011"
E_B = "99000000-0000-0000-0000-0000fee36012"
E_C = "99000000-0000-0000-0000-0000fee36013"
HH = "99000000-0000-0000-0000-0000fee36021"
ACC_A = "99000000-0000-0000-0000-0000fee36031"
ACC_B = "99000000-0000-0000-0000-0000fee36032"
ACC_C = "99000000-0000-0000-0000-0000fee36033"
BG = "99000000-0000-0000-0000-0000fee36041"
SCH_MAIN = "99000000-0000-0000-0000-0000fee36051"
SCH_GRP = "99000000-0000-0000-0000-0000fee36052"
SPV = "99000000-0000-0000-0000-0000fee36061"
SUB_C = "99000000-0000-0000-0000-0000fee36062"
TXN = "99000000-0000-0000-0000-0000fee36063"
ALLOC = "99000000-0000-0000-0000-0000fee36064"
DEAL = None  # resolved at fixture time — spvs.deal_id is NOT NULL

P_START, P_END = date(2026, 1, 1), date(2026, 3, 31)
PRIOR_START, PRIOR_END = date(2025, 10, 1), date(2025, 12, 31)


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
        return [r for r in self.rows if r[0] in ("FAIL",)]

    def summary(self):
        counts: dict[str, int] = {}
        for kind, _, _ in self.rows:
            counts[kind] = counts.get(kind, 0) + 1
        total = len(self.rows)
        passed = counts.get("PASS", 0)
        print("\n" + "=" * 78)
        print(f"fee36: {passed}/{total} PASS" + "".join(
            f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    out = {}
    for t in COUNTED:
        out[t] = await conn.fetchval(f"SELECT count(*) FROM {t}")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
#
# Three accounts, all in one household, deliberately different:
#
#   ACC_A  $1,000,000  ACCOUNT-scoped minimum schedule.  Plain case.
#   ACC_B  $  400,000  same schedule.  Exists to make the run multi-account and
#                      to give the variance report something to compare.
#   ACC_C  $  250,000  BILLING_GROUP-minimum schedule, and IS a member of a
#                      BREAKPOINT group.  Carries the SPV_MGMT_FEE_OFFSET
#                      credit.
#
# ACC_B is deliberately NOT in the breakpoint group: [6] needs an account for
# which the group-scoped minimum has nothing to resolve to.


async def teardown(conn) -> None:
    """By fixture id, in FK order. Never a TRUNCATE — these tables hold real
    data the moment this project has a client.

    The immutability triggers have to come off to remove this script's own
    POSTED runs, which is the one legitimate reason to disable them and is why
    the DELETEs are scoped to two fixture periods rather than to the table.
    They go back on in a ``finally`` — a teardown that failed halfway and left
    a billing table unprotected would be worse than the rows it was cleaning.
    """
    ids_accounts = [ACC_A, ACC_B, ACC_C]
    await conn.execute("ALTER TABLE public.fee_runs DISABLE TRIGGER fee_runs_immutable_once_posted")
    await conn.execute("ALTER TABLE public.fee_run_lines DISABLE TRIGGER fee_run_lines_immutable_once_posted")
    try:
        await conn.execute(
            """DELETE FROM public.fee_run_lines
               WHERE fee_run_id IN (SELECT id FROM public.fee_runs WHERE org_id = $1::uuid
                                    AND period_start IN ($2::date, $3::date))""",
            ORG, P_START, PRIOR_START)
        # REVERSAL runs first: fee_runs_reversal_requires_target forbids
        # NULLing reverses_run_id to break the self-FK, so the child has to go
        # before the parent rather than being detached from it.
        for reversals_first in (True, False):
            await conn.execute(
                f"""DELETE FROM public.fee_runs WHERE org_id = $1::uuid
                    AND period_start IN ($2::date, $3::date)
                    AND run_type {'=' if reversals_first else '<>'} 'REVERSAL'""",
                ORG, P_START, PRIOR_START)
    finally:
        await conn.execute("ALTER TABLE public.fee_runs ENABLE TRIGGER fee_runs_immutable_once_posted")
        await conn.execute("ALTER TABLE public.fee_run_lines ENABLE TRIGGER fee_run_lines_immutable_once_posted")

    await conn.execute(
        "DELETE FROM public.assistant_activities WHERE related_type = 'fee_run' "
        "AND org_id = ANY($1::uuid[])", [ORG, OTHER_ORG])
    await conn.execute("DELETE FROM public.spv_transaction_allocations WHERE id = $1::uuid", ALLOC)
    await conn.execute("DELETE FROM public.spv_transactions WHERE id = $1::uuid", TXN)
    await conn.execute("DELETE FROM public.spv_subscriptions WHERE id = $1::uuid", SUB_C)
    await conn.execute("DELETE FROM public.spvs WHERE id = $1::uuid", SPV)
    await conn.execute("DELETE FROM public.fee_credits WHERE org_id = ANY($1::uuid[]) AND reason LIKE $2",
                       [ORG, OTHER_ORG], f"%{TAG}%")
    await conn.execute("DELETE FROM public.fee_discounts WHERE org_id = ANY($1::uuid[]) AND reason LIKE $2",
                       [ORG, OTHER_ORG], f"%{TAG}%")
    await conn.execute("DELETE FROM public.fee_exclusions WHERE org_id = ANY($1::uuid[]) AND reason LIKE $2",
                       [ORG, OTHER_ORG], f"%{TAG}%")
    await conn.execute("DELETE FROM public.fee_assignments WHERE fee_schedule_id = ANY($1::uuid[])",
                       [SCH_MAIN, SCH_GRP])
    await conn.execute("DELETE FROM public.account_flows WHERE account_id = ANY($1::uuid[])", ids_accounts)
    await conn.execute("DELETE FROM public.account_balances_daily WHERE account_id = ANY($1::uuid[])", ids_accounts)
    await conn.execute("DELETE FROM public.billing_group_members WHERE billing_group_id = $1::uuid", BG)
    await conn.execute("DELETE FROM public.billing_groups WHERE id = $1::uuid", BG)
    await conn.execute("DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", ids_accounts)
    await conn.execute("DELETE FROM public.fee_schedule_tiers WHERE fee_schedule_id = ANY($1::uuid[])",
                       [SCH_MAIN, SCH_GRP])
    await conn.execute("DELETE FROM public.fee_schedules WHERE id = ANY($1::uuid[])", [SCH_MAIN, SCH_GRP])
    await conn.execute("DELETE FROM public.households WHERE id = $1::uuid", HH)
    await conn.execute("DELETE FROM public.entities WHERE id = ANY($1::uuid[])", [E_A, E_B, E_C])
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])",
                       [U_MAKER, U_CHECKER, U_COMPLY])


async def build_fixtures(conn) -> None:
    for uid, email in ((U_MAKER, "fee36maker"), (U_CHECKER, "fee36checker"),
                       (U_COMPLY, "fee36compliance")):
        await conn.execute(
            """INSERT INTO public.users (id, org_id, email, auth0_sub)
               VALUES ($1::uuid, $2::uuid, $3, $4)""",
            uid, ORG, f"{email}@{TAG}.local", f"auth0|{TAG}-{email}")

    for eid, nm in ((E_A, "A"), (E_B, "B"), (E_C, "C")):
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
               VALUES ($1::uuid, $2::uuid, 'individual', $3)""",
            eid, ORG, f"{TAG} entity {nm}")

    await conn.execute(
        "INSERT INTO public.households (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
        HH, ORG, f"{TAG} household")

    for aid, eid, nm in ((ACC_A, E_A, "A"), (ACC_B, E_B, "B"), (ACC_C, E_C, "C")):
        await conn.execute(
            """INSERT INTO public.accounts
                 (id, org_id, account_number_masked, account_number_hash, custodian_code,
                  registration_type, tax_status, primary_entity_id, household_id,
                  is_billable, opened_on)
               VALUES ($1::uuid,$2::uuid,$3,$4,'TEST','individual','taxable',
                       $5::uuid,$6::uuid,true,'2024-01-01')""",
            aid, ORG, f"***{nm}", f"{TAG}-{nm}", eid, HH)

    # ── schedules ───────────────────────────────────────────────────────────
    # SCH_MAIN: flat 100 bps annual, PERIOD_END, QUARTERLY, no minimum.
    #   Q1 2026 fee on $1,000,000 = 1,000,000 * 0.01 / 4 = $2,500.00
    await conn.execute(
        """INSERT INTO public.fee_schedules
             (id, org_id, code, name, product_type, rate_type, tier_method,
              billing_frequency, billing_timing, valuation_method, proration_method,
              status, day_weight_flows)
           VALUES ($1::uuid,$2::uuid,$3,$4,'ASSET_MANAGEMENT','BPS','GRADUATED',
                   'QUARTERLY','ARREARS','PERIOD_END','NONE','APPROVED', false)""",
        SCH_MAIN, ORG, f"{TAG}-MAIN", f"{TAG} main")
    await conn.execute(
        """INSERT INTO public.fee_schedule_tiers
             (org_id, fee_schedule_id, tier_seq, lower_bound, upper_bound, rate_bps)
           VALUES ($1::uuid,$2::uuid,1,0,NULL,100)""",
        ORG, SCH_MAIN)

    # SCH_GRP: same rate, but a BILLING_GROUP-scoped minimum of $5,000/yr.
    await conn.execute(
        """INSERT INTO public.fee_schedules
             (id, org_id, code, name, product_type, rate_type, tier_method,
              billing_frequency, billing_timing, valuation_method, proration_method,
              status, day_weight_flows, minimum_fee, minimum_fee_scope)
           VALUES ($1::uuid,$2::uuid,$3,$4,'ASSET_MANAGEMENT','BPS','GRADUATED',
                   'QUARTERLY','ARREARS','PERIOD_END','NONE','APPROVED', false,
                   5000, 'BILLING_GROUP')""",
        SCH_GRP, ORG, f"{TAG}-GRP", f"{TAG} group-minimum")
    await conn.execute(
        """INSERT INTO public.fee_schedule_tiers
             (org_id, fee_schedule_id, tier_seq, lower_bound, upper_bound, rate_bps)
           VALUES ($1::uuid,$2::uuid,1,0,NULL,100)""",
        ORG, SCH_GRP)

    # ── assignments ─────────────────────────────────────────────────────────
    for aid, sch in ((ACC_A, SCH_MAIN), (ACC_B, SCH_MAIN), (ACC_C, SCH_GRP)):
        await conn.execute(
            """INSERT INTO public.fee_assignments
                 (org_id, fee_schedule_id, scope_type, scope_id, precedence, effective_from)
               VALUES ($1::uuid,$2::uuid,'ACCOUNT',$3::uuid,10,'2024-01-01')""",
            ORG, sch, aid)

    # ── breakpoint group: ACC_C is in it, ACC_B deliberately is not ──────────
    await conn.execute(
        """INSERT INTO public.billing_groups (id, org_id, name, group_type, household_id)
           VALUES ($1::uuid,$2::uuid,$3,'BREAKPOINT',$4::uuid)""",
        BG, ORG, f"{TAG} breakpoint", HH)
    await conn.execute(
        """INSERT INTO public.billing_group_members (org_id, billing_group_id, account_id, valid_from)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'2024-01-01')""",
        ORG, BG, ACC_C)

    # ── balances: PERIOD_END valuation, so only the last day matters ────────
    for aid, mv in ((ACC_A, "1000000.00"), (ACC_B, "400000.00"), (ACC_C, "250000.00")):
        for d in (P_END, PRIOR_END):
            await conn.execute(
                """INSERT INTO public.account_balances_daily
                     (org_id, account_id, as_of_date, total_market_value, cash_value,
                      source_system, is_billing_source, is_final)
                   VALUES ($1::uuid,$2::uuid,$3::date,$4::numeric,0,'PRIMARY',true,true)""",
                ORG, aid, d, mv)

    # ── SPV management fee, for F1 ──────────────────────────────────────────
    # ACC_C's entity is allocated $2,000 of a $10,000 posted management-fee
    # call inside Q1 2026. The credit offsets 50% of it -> $1,000.00.
    deal = await conn.fetchval("SELECT id FROM public.deals WHERE org_id=$1::uuid LIMIT 1", ORG)
    if deal is None:
        return  # handled by the caller: F1 becomes BLOCKED, not silently skipped
    await conn.execute(
        """INSERT INTO public.spvs (id, org_id, deal_id, name, spv_status, mgmt_fee_pct)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4,'forming',2.0)""",
        SPV, ORG, deal, f"{TAG} spv")
    await conn.execute(
        """INSERT INTO public.spv_subscriptions
             (id, org_id, spv_id, entity_id, commitment_amount, funded_amount,
              ownership_pct, subscription_status)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,500000,500000,20,'confirmed')""",
        SUB_C, ORG, SPV, E_C)
    await conn.execute(
        """INSERT INTO public.spv_transactions
             (id, org_id, spv_id, txn_type, txn_date, amount, status, posted_at,
              allocation_basis, description)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'call_mgmt_fee','2026-02-15',10000,
                   'posted', now(), 'committed', $4)""",
        TXN, ORG, SPV, f"{TAG} mgmt fee call")
    await conn.execute(
        """INSERT INTO public.spv_transaction_allocations
             (id, org_id, transaction_id, spv_id, subscription_id, entity_id,
              ownership_pct, allocated_amount, status)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6::uuid,
                   20, 2000.00, 'allocated')""",
        ALLOC, ORG, TXN, SPV, SUB_C, E_C)
    await conn.execute(
        """INSERT INTO public.fee_credits
             (org_id, scope_type, scope_id, credit_source, offset_pct,
              effective_from, reason, approved_by)
           VALUES ($1::uuid,'ACCOUNT',$2::uuid,'SPV_MGMT_FEE_OFFSET',0.1,
                   '2024-01-01',$3,$4::uuid)""",
        ORG, ACC_C, f"{TAG} spv offset", U_MAKER)


# ═══════════════════════════════════════════════════════════════════════════
# Check 1 — deployment, RLS, and the two triggers (bug reproduced first)
# ═══════════════════════════════════════════════════════════════════════════

_ORIGINAL_LINES_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION public.fee_run_lines_prevent_posted_mutation()
RETURNS trigger LANGUAGE plpgsql AS $function$
DECLARE run_status text;
BEGIN
  SELECT status INTO run_status FROM fee_runs WHERE id = OLD.fee_run_id;
  IF run_status IN ('POSTED','EXPORTED','RECONCILED') THEN
    RAISE EXCEPTION 'fee_run_line % belongs to a % fee_run and is immutable', OLD.id, run_status
      USING ERRCODE = 'raise_exception';
  END IF;
  RETURN NEW;
END;
$function$;
"""


async def check_1(conn, admin, fix_sql: str):
    for t in ("fee_runs", "fee_run_lines"):
        n = await conn.fetchval(
            """SELECT count(*) FROM information_schema.tables
               WHERE table_schema='public' AND table_name=$1""", t)
        R.expect("1a", n == 1, f"public.{t} is deployed")
        rls = await conn.fetchval(
            """SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname='public' AND c.relname=$1""", t)
        R.expect("1b", rls is True, f"{t} has RLS enabled")
        pol = await conn.fetch(
            "SELECT policyname, cmd, qual FROM pg_policies WHERE schemaname='public' AND tablename=$1", t)
        shaped = [
            p for p in pol
            if "app.current_org_id" in (p["qual"] or "")
            and "NULLIF" in (p["qual"] or "")
            and "app.is_super_admin" in (p["qual"] or "")
        ]
        R.expect("1c", len(shaped) >= 1,
                 f"{t} policy is org-isolating, NULLIFs the GUC and carries the "
                 f"super-admin bypass",
                 f"policies={[dict(p) for p in pol]}")
        grants = {r["privilege_type"] for r in await conn.fetch(
            """SELECT privilege_type FROM information_schema.role_table_grants
               WHERE table_schema='public' AND table_name=$1 AND grantee='app_service'""", t)}
        R.expect("1d", {"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants,
                 f"app_service holds SELECT/INSERT/UPDATE/DELETE on {t}", str(sorted(grants)))

    trg = {r["relname"]: r["d"] for r in await conn.fetch("""
        SELECT c.relname, pg_get_triggerdef(t.oid) d FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN ('fee_runs','fee_run_lines')
          AND NOT t.tgisinternal""")}
    R.expect("1e", "fee_runs" in trg and "DELETE" in trg["fee_runs"] and "UPDATE" in trg["fee_runs"],
             "fee_runs immutability trigger fires on UPDATE and DELETE",
             trg.get("fee_runs", "MISSING"))
    R.expect("1f", "fee_run_lines" in trg and "DELETE" in trg["fee_run_lines"]
             and "UPDATE" in trg["fee_run_lines"],
             "fee_run_lines immutability trigger fires on UPDATE and DELETE",
             trg.get("fee_run_lines", "MISSING"))

    # ── reproduce F36-A on the ORIGINAL function body, then prove the fix ───
    run = await FR.create_run(admin, ORG, period_start=P_START, period_end=P_END,
                              billing_frequency="QUARTERLY", created_by=U_MAKER)
    line_sql = """
        INSERT INTO public.fee_run_lines (org_id, fee_run_id, product_type,
          fee_schedule_id, billable_value, valuation_method, gross_fee, net_fee, calc_detail)
        VALUES ($1::uuid,$2::uuid,'ASSET_MANAGEMENT',$3::uuid,1000,'PERIOD_END',10,10,'{}'::jsonb)
        RETURNING id"""

    await admin.execute(_ORIGINAL_LINES_TRIGGER_FN)
    lid = await admin.fetchval(line_sql, ORG, run, SCH_MAIN)
    gone = await admin.fetch("DELETE FROM public.fee_run_lines WHERE id=$1::uuid RETURNING id", lid)
    still = await admin.fetchval("SELECT count(*) FROM public.fee_run_lines WHERE id=$1::uuid", lid)
    R.expect("1g", len(gone) == 0 and still == 1,
             "BUG F36-A reproduced on the original trigger body: deleting a "
             "PREVIEW-run line silently did nothing",
             f"deleted={len(gone)} still_present={still}")

    await admin.execute(fix_sql)
    gone = await admin.fetch("DELETE FROM public.fee_run_lines WHERE id=$1::uuid RETURNING id", lid)
    still = await admin.fetchval("SELECT count(*) FROM public.fee_run_lines WHERE id=$1::uuid", lid)
    R.expect("1h", len(gone) == 1 and still == 0,
             "F36-A fixed: the same DELETE now removes the row",
             f"deleted={len(gone)} still_present={still}")

    await admin.execute("DELETE FROM public.fee_runs WHERE id=$1::uuid", run)


# ═══════════════════════════════════════════════════════════════════════════
# Check 2 — repeatable PREVIEW
# ═══════════════════════════════════════════════════════════════════════════


async def check_2(conn):
    run = await FR.create_run(conn, ORG, period_start=P_START, period_end=P_END,
                              billing_frequency="QUARTERLY", created_by=U_MAKER)
    r1 = await FR.preview_run(conn, ORG, run, account_ids=[ACC_A, ACC_B, ACC_C])
    n1 = await conn.fetchval("SELECT count(*) FROM public.fee_run_lines WHERE fee_run_id=$1::uuid", run)
    R.expect("2a", r1.lines_written == 3 and n1 == 3 and r1.lines_replaced == 0,
             "first PREVIEW wrote 3 lines and replaced none",
             f"written={r1.lines_written} replaced={r1.lines_replaced} in_table={n1}")

    ids1 = {r["id"] for r in await conn.fetch(
        "SELECT id::text AS id FROM public.fee_run_lines WHERE fee_run_id=$1::uuid", run)}

    r2 = await FR.preview_run(conn, ORG, run, account_ids=[ACC_A, ACC_B, ACC_C])
    r3 = await FR.preview_run(conn, ORG, run, account_ids=[ACC_A, ACC_B, ACC_C])
    n3 = await conn.fetchval("SELECT count(*) FROM public.fee_run_lines WHERE fee_run_id=$1::uuid", run)
    ids3 = {r["id"] for r in await conn.fetch(
        "SELECT id::text AS id FROM public.fee_run_lines WHERE fee_run_id=$1::uuid", run)}

    R.expect("2b", n3 == 3, "after three PREVIEWs there are still exactly 3 lines",
             f"count={n3}")
    R.expect("2c", r2.lines_replaced == 3 and r3.lines_replaced == 3,
             "each re-PREVIEW replaced the previous 3 lines",
             f"r2={r2.lines_replaced} r3={r3.lines_replaced}")
    R.expect("2d", not (ids1 & ids3),
             "no line id survives a re-PREVIEW — the rows were genuinely "
             "replaced, not updated in place",
             f"overlap={ids1 & ids3}")
    R.expect("2e", r1.calculation_snapshot_hash == r3.calculation_snapshot_hash,
             "the hash is stable across identical re-PREVIEWs")
    R.expect("2f", r1.total_net_fee == r3.total_net_fee,
             "the total is stable across identical re-PREVIEWs",
             f"{r1.total_net_fee} vs {r3.total_net_fee}")
    return run


# ═══════════════════════════════════════════════════════════════════════════
# Check 3 — the stored numbers ARE fee35's numbers
# ═══════════════════════════════════════════════════════════════════════════


async def check_3(conn, run):
    lines = {l["account_id"]: l for l in await FR.list_lines(conn, ORG, run)}

    # Hand-derived, away from the code:
    #   ACC_A  $1,000,000 * 100bps = 1% annual = $10,000/yr / 4 = $2,500.00
    #   ACC_B  $  400,000 * 1% = $4,000/yr / 4 = $1,000.00
    R.expect("3a", lines[ACC_A]["net_fee"] == D("2500.00"),
             "ACC_A net_fee is the hand-derived $2,500.00",
             str(lines[ACC_A]["net_fee"]))
    R.expect("3b", lines[ACC_B]["net_fee"] == D("1000.00"),
             "ACC_B net_fee is the hand-derived $1,000.00",
             str(lines[ACC_B]["net_fee"]))

    # ACC_C: $250,000 * 1% / 4 = $625.00 gross.
    #   CREDITS: SPV_MGMT_FEE_OFFSET, offset_pct 0.1 * basis $2,000 = -$200.00
    #            -> 625 - 200 = 425.00
    #   MINIMUM: BILLING_GROUP-scoped $5,000/period. ACC_C is the only member of
    #            the breakpoint group in this run, so its group subtotal is
    #            425.00, a shortfall of 4,575.00, allocated entirely to it.
    #            425 + 4575 = 5000.00
    R.expect("3c", lines[ACC_C]["net_fee"] == D("5000.00"),
             "ACC_C net_fee is the hand-derived $5,000.00 (gross 625, credit "
             "-200, group minimum lifts it to the 5000 floor)",
             str(lines[ACC_C]["net_fee"]))

    # ── and the same numbers straight out of fee35, same inputs ─────────────
    for aid, sch in ((ACC_A, SCH_MAIN), (ACC_B, SCH_MAIN)):
        request, _ = await load_account_calc_request(
            conn, ORG, account_id=aid, fee_schedule_id=sch,
            period_start=P_START, period_end=P_END)
        direct = calculate_account_fee(request)
        R.expect("3d", direct.amount == lines[aid]["net_fee"],
                 f"{aid[-4:]}: the stored net_fee equals calculate_account_fee "
                 f"called directly on the same AccountCalcRequest",
                 f"engine={direct.amount} stored={lines[aid]['net_fee']}")

    R.expect("3e", all(l["calc_detail"] for l in lines.values()),
             "every line carries a calc_detail trace")
    detail = lines[ACC_A]["calc_detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    R.expect("3f", detail.get("engine", {}).get("engine_version") == ENGINE_VERSION,
             f"the stored trace names the engine it came from ({ENGINE_VERSION})")
    R.expect("3g", "inputs" in detail and "provenance" in detail,
             "the line stores the hash's preimage and the resolution provenance")

    # Components are the engine's own deltas, not a re-derivation.
    c_detail = lines[ACC_C]["calc_detail"]
    if isinstance(c_detail, str):
        c_detail = json.loads(c_detail)
    R.expect("3h", lines[ACC_C]["credit_amount"] == D("-200.00"),
             "ACC_C credit_amount is the signed delta the CREDITS step applied",
             str(lines[ACC_C]["credit_amount"]))
    R.expect("3i", lines[ACC_C]["minimum_adjustment"] == D("4575.00"),
             "ACC_C minimum_adjustment is the group-minimum uplift only",
             str(lines[ACC_C]["minimum_adjustment"]))


# ═══════════════════════════════════════════════════════════════════════════
# Check 4 — approvals are assistant_activities rows
# ═══════════════════════════════════════════════════════════════════════════


async def check_4(conn, run):
    # Self-approval must be refused before anything advances.
    await FR.propose_approval(conn, ORG, run, gate="ADVISOR", proposed_by=U_MAKER,
                              rationale=f"{TAG} advisor gate")
    try:
        await FR.approve(conn, ORG, run, gate="ADVISOR", approved_by=U_MAKER)
        R.bad("4a", "self-approval was NOT refused")
    except FR.MakerCheckerError:
        R.ok("4a", "self-approval refused: the proposer cannot be the approver")

    st = (await FR.get_run(conn, ORG, run))["status"]
    R.expect("4b", st == "PREVIEW",
             "a refused approval left the run where it was", st)

    out = await FR.approve(conn, ORG, run, gate="ADVISOR", approved_by=U_CHECKER)
    st = (await FR.get_run(conn, ORG, run))["status"]
    R.expect("4c", st == "ADVISOR_APPROVED", "the run advanced to ADVISOR_APPROVED", st)

    acts = await FR.approval_activities(conn, ORG, run)
    adv = [a for a in acts if a["action_key"] == "fee_run.advisor_approve"]
    R.expect("4d", len(adv) == 1, "exactly one advisor activity exists", str(len(adv)))
    a = adv[0]
    R.expect("4e", a["related_type"] == "fee_run" and a["related_id"] == run,
             "the activity carries related_type='fee_run' and the run's id",
             f"{a['related_type']}/{a['related_id']}")
    R.expect("4f", a["status"] == FR.ACTIVITY_APPROVED,
             f"the activity's status is {FR.ACTIVITY_APPROVED!r}", a["status"])
    R.expect("4g", a["proposed_by"] == U_MAKER and a["approved_by"] == U_CHECKER,
             "maker and checker are two different, recorded users",
             f"{a['proposed_by']} / {a['approved_by']}")

    # The DB's own CHECK, independent of the service layer.
    try:
        await conn.execute(
            "UPDATE public.assistant_activities SET approved_by = proposed_by WHERE id=$1::uuid",
            a["id"])
        R.bad("4h", "assistant_activities_maker_checker_chk did NOT refuse "
                    "approved_by = proposed_by")
    except Exception as exc:
        R.expect("4h", "maker_checker" in str(exc),
                 "the DATABASE refuses approved_by = proposed_by, independently "
                 "of the service layer", str(exc).splitlines()[0])

    # Compliance gate.
    await FR.propose_approval(conn, ORG, run, gate="COMPLIANCE", proposed_by=U_CHECKER,
                              rationale=f"{TAG} compliance gate")
    await FR.approve(conn, ORG, run, gate="COMPLIANCE", approved_by=U_COMPLY)
    st = (await FR.get_run(conn, ORG, run))["status"]
    R.expect("4i", st == "COMPLIANCE_APPROVED", "the run advanced to COMPLIANCE_APPROVED", st)

    acts = await FR.approval_activities(conn, ORG, run)
    comp = [a for a in acts if a["action_key"] == "fee_run.compliance_approve"
            and a["status"] == FR.ACTIVITY_APPROVED]
    R.expect("4j", len(comp) == 1 and comp[0]["related_id"] == run,
             "the compliance transition has its own approved activity for this run")

    # Posting requires the LEDGER, not the status column.
    row = await FR.get_run(conn, ORG, run)
    R.expect("4k", row["advisor_approved_by"] == U_CHECKER
             and row["compliance_approved_by"] == U_COMPLY,
             "fee_runs' approval columns mirror the ledger exactly",
             f"{row['advisor_approved_by']} / {row['compliance_approved_by']}")

    await conn.execute(
        "UPDATE public.assistant_activities SET status='revoked' WHERE id=$1::uuid",
        comp[0]["id"])
    try:
        await FR.post_run(conn, ORG, run)
        R.bad("4l", "post_run succeeded with no approved compliance activity — "
                    "the status column, not the ledger, was the authority")
    except FR.FeeRunStateError as exc:
        R.expect("4l", "COMPLIANCE" in str(exc),
                 "post_run refuses when the LEDGER lacks an approval, even "
                 "though fee_runs.status says COMPLIANCE_APPROVED",
                 str(exc).splitlines()[0])
    await conn.execute(
        "UPDATE public.assistant_activities SET status=$2 WHERE id=$1::uuid",
        comp[0]["id"], FR.ACTIVITY_APPROVED)

    posted = await FR.post_run(conn, ORG, run)
    R.expect("4m", posted["status"] == "POSTED", "the run posted")
    R.expect("4n", posted["ledger"]["posted"] is False
             and posted["ledger"]["journal_entries_written"] == 0,
             "GL posting is a marked, non-silent stub — it reports that it "
             "wrote nothing and why")
    R.find("4o", "GL POSTING DECISION NEEDED — " + FR.GL_POSTING_DECISION_REQUIRED)
    return run


# ═══════════════════════════════════════════════════════════════════════════
# Check 5 — F1, the SPV management-fee basis
# ═══════════════════════════════════════════════════════════════════════════


async def check_5(conn, run):
    basis = await resolve_credit_basis(
        conn, ORG, credit_source="SPV_MGMT_FEE_OFFSET", account_id=ACC_C,
        owner_entity_id=E_C, period_start=P_START, period_end=P_END)
    R.expect("5a", basis.amount == D("2000.00"),
             "the SPV_MGMT_FEE_OFFSET basis is this entity's OWN allocated "
             "$2,000.00, not the vehicle-level $10,000.00",
             str(basis.amount))
    R.expect("5b", basis.source == "spv_transaction_allocations.allocated_amount",
             "the basis names the real table it came from", basis.source)

    lines = {l["account_id"]: l for l in await FR.list_lines(conn, ORG, run)}
    R.expect("5c", lines[ACC_C]["credit_amount"] == D("-200.00"),
             "0.1 offset_pct * $2,000.00 basis = a $200.00 credit on the line",
             str(lines[ACC_C]["credit_amount"]))

    detail = lines[ACC_C]["calc_detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    prov = detail["provenance"]["credit_basis"]
    R.expect("5d", any(v["basis_amount"] == "2000.00" for v in prov.values()),
             "the line stores where the basis came from, per credit",
             json.dumps(prov)[:200])

    # The negative case: a credit source with no source table must RAISE.
    for src in ("12B1", "SUB_TA", "MODEL_FEE_OFFSET", "SI_EMBEDDED_FEE_OFFSET"):
        try:
            await resolve_credit_basis(
                conn, ORG, credit_source=src, account_id=ACC_C, owner_entity_id=E_C,
                period_start=P_START, period_end=P_END)
            R.bad("5e", f"{src} returned a basis; no table stores one")
            break
        except CreditBasisUnavailableError:
            pass
    else:
        R.ok("5e", "every credit_source with no deployed source table raises "
                   "CreditBasisUnavailableError rather than crediting zero")

    # An SPV credit with no posted call in the period must also raise, not zero.
    try:
        await resolve_credit_basis(
            conn, ORG, credit_source="SPV_MGMT_FEE_OFFSET", account_id=ACC_C,
            owner_entity_id=E_C, period_start=PRIOR_START, period_end=PRIOR_END)
        R.bad("5f", "a period with no posted management-fee call returned a basis")
    except CreditBasisUnavailableError:
        R.ok("5f", "a period with no posted call raises rather than crediting $0")

    # A DRAFT call must not count — the client was never charged it.
    await conn.execute("UPDATE public.spv_transactions SET status='draft' WHERE id=$1::uuid", TXN)
    try:
        await resolve_credit_basis(
            conn, ORG, credit_source="SPV_MGMT_FEE_OFFSET", account_id=ACC_C,
            owner_entity_id=E_C, period_start=P_START, period_end=P_END)
        R.bad("5g", "a DRAFT management-fee call was treated as chargeable")
    except CreditBasisUnavailableError:
        R.ok("5g", "only a POSTED management-fee call is a basis; a draft one "
                   "would credit back money never taken")
    await conn.execute("UPDATE public.spv_transactions SET status='posted' WHERE id=$1::uuid", TXN)


# ═══════════════════════════════════════════════════════════════════════════
# Check 6 — F4, billing_group_id resolution, BOTH directions
# ═══════════════════════════════════════════════════════════════════════════


async def check_6(conn):
    got = await resolve_billing_group_id(conn, ORG, ACC_C, as_of=P_END)
    R.expect("6a", got == BG,
             "an account with an active BREAKPOINT membership resolves to it",
             str(got))

    none = await resolve_billing_group_id(conn, ORG, ACC_B, as_of=P_END)
    R.expect("6b", none is None,
             "an account with no BREAKPOINT membership resolves to None — not "
             "to some other group, and not to its household", str(none))

    # The engine must REFUSE, not fall back to an account-scoped minimum.
    await conn.execute(
        "UPDATE public.fee_assignments SET fee_schedule_id=$1::uuid "
        "WHERE scope_id=$2::uuid AND scope_type='ACCOUNT'", SCH_GRP, ACC_B)
    try:
        request, _ = await load_account_calc_request(
            conn, ORG, account_id=ACC_B, fee_schedule_id=SCH_GRP,
            period_start=P_START, period_end=P_END)
        calculate_account_fee(request)
        R.bad("6c", "a BILLING_GROUP-scoped minimum on an account with no "
                    "breakpoint membership did NOT raise")
    except GroupScopeMissingError as exc:
        R.expect("6c", "billing_group_id" in str(exc),
                 "a BILLING_GROUP-scoped minimum on an account with no "
                 "membership raises the engine's own GroupScopeMissingError, "
                 "naming the field — no silent fall back to ACCOUNT scope",
                 str(exc).splitlines()[0])
    finally:
        await conn.execute(
            "UPDATE public.fee_assignments SET fee_schedule_id=$1::uuid "
            "WHERE scope_id=$2::uuid AND scope_type='ACCOUNT'", SCH_MAIN, ACC_B)

    # Membership is AS OF the date billed, not as it stands today.
    await conn.execute(
        "UPDATE public.billing_group_members SET valid_to='2025-06-30' "
        "WHERE billing_group_id=$1::uuid AND account_id=$2::uuid", BG, ACC_C)
    ended = await resolve_billing_group_id(conn, ORG, ACC_C, as_of=P_END)
    earlier = await resolve_billing_group_id(conn, ORG, ACC_C, as_of=date(2025, 3, 31))
    R.expect("6d", ended is None and earlier == BG,
             "resolution is AS OF the date billed: a membership that ended in "
             "June 2025 resolves for March 2025 and not for March 2026",
             f"as_of {P_END}={ended}  as_of 2025-03-31={earlier}")
    await conn.execute(
        "UPDATE public.billing_group_members SET valid_to=NULL "
        "WHERE billing_group_id=$1::uuid AND account_id=$2::uuid", BG, ACC_C)

    # ── F36-D: a group minimum silently leaves the group when the account's
    # own pre-minimum amount is negative. Found by walking into it, not
    # predicted. fee35's _minimum_step short-circuits on `run.amount < ZERO`
    # ("applying a minimum here would turn a refund into a charge") BEFORE it
    # reaches the group branch, so `minimum_deferred_to_group` is never set and
    # calculate_group_fees never puts the account in a bucket. Pinned here so a
    # future change to fee35 has to decide about it deliberately.
    await conn.execute(
        "UPDATE public.fee_credits SET offset_pct = 1.0 WHERE scope_id = $1::uuid", ACC_C)
    try:
        request, _ = await load_account_calc_request(
            conn, ORG, account_id=ACC_C, fee_schedule_id=SCH_GRP,
            period_start=P_START, period_end=P_END)
        res = calculate_account_fee(request)
        step = next(s for s in res.calc_detail["steps"] if s.get("step") == "MINIMUM")
        R.expect("6f", res.amount == D("-1375.00")
                 and step["outcome"] == "skipped"
                 and res.minimum_deferred_to_group is False,
                 "a credit that exceeds the fee produces a refund and fee35 "
                 "deliberately SKIPS the minimum rather than turning a refund "
                 "into a charge — with the reason in the trace",
                 f"amount={res.amount} outcome={step.get('outcome')} "
                 f"deferred={res.minimum_deferred_to_group}")
        R.find("6g", "F36-D — because that skip happens BEFORE the group branch, "
                     "a BILLING_GROUP/HOUSEHOLD minimum on a refunding account "
                     "never reaches calculate_group_fees' bucket at all. In a "
                     "group where one account refunds and others bill, the "
                     "group subtotal is computed WITHOUT the refund, so the "
                     "shortfall charged to the rest is too large. fee35's "
                     "arithmetic, not this sprint's — reported, not changed.")
    finally:
        await conn.execute(
            "UPDATE public.fee_credits SET offset_pct = 0.1 WHERE scope_id = $1::uuid", ACC_C)

    # Two active breakpoint memberships must be an error, not a coin flip.
    bg2 = "99000000-0000-0000-0000-0000fee36042"
    await conn.execute(
        """INSERT INTO public.billing_groups (id, org_id, name, group_type)
           VALUES ($1::uuid,$2::uuid,$3,'BREAKPOINT')""", bg2, ORG, f"{TAG} bp2")
    await conn.execute(
        """INSERT INTO public.billing_group_members (org_id, billing_group_id, account_id, valid_from)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'2024-01-01')""", ORG, bg2, ACC_C)
    try:
        await resolve_billing_group_id(conn, ORG, ACC_C, as_of=P_END)
        R.bad("6e", "two active BREAKPOINT memberships silently resolved to one")
    except AmbiguousBillingGroupError:
        R.ok("6e", "two active BREAKPOINT memberships raise rather than putting "
                   "the account in an arbitrary breakpoint")
    finally:
        await conn.execute("DELETE FROM public.billing_group_members WHERE billing_group_id=$1::uuid", bg2)
        await conn.execute("DELETE FROM public.billing_groups WHERE id=$1::uuid", bg2)


# ═══════════════════════════════════════════════════════════════════════════
# Check 7 — POSTED immutability, attacked directly
# ═══════════════════════════════════════════════════════════════════════════


async def check_7(dsn, run):
    """A SEPARATE connection, raw SQL, no service layer anywhere near it."""
    raw = await connect(dsn)
    try:
        status = await raw.fetchval("SELECT status FROM public.fee_runs WHERE id=$1::uuid", run)
        if status != "POSTED":
            R.bad("7a", f"precondition: run is {status}, not POSTED")
            return
        line = await raw.fetchval(
            "SELECT id FROM public.fee_run_lines WHERE fee_run_id=$1::uuid LIMIT 1", run)

        cases = [
            ("7a", "UPDATE a POSTED run",
             "UPDATE public.fee_runs SET billing_frequency='ANNUAL' WHERE id=$1::uuid", run),
            ("7b", "DELETE a POSTED run",
             "DELETE FROM public.fee_runs WHERE id=$1::uuid", run),
            ("7c", "UPDATE a POSTED run's line",
             "UPDATE public.fee_run_lines SET net_fee=1 WHERE id=$1::uuid", line),
            ("7d", "DELETE a POSTED run's line",
             "DELETE FROM public.fee_run_lines WHERE id=$1::uuid", line),
        ]
        for ref, label, sql, arg in cases:
            sp = raw.transaction()
            await sp.start()
            try:
                await raw.execute(sql, arg)
                await sp.rollback()
                R.bad(ref, f"{label} was NOT refused by the database")
            except Exception as exc:
                await sp.rollback()
                R.expect(ref, "immutable" in str(exc),
                         f"{label} is refused by the TRIGGER, in the database",
                         str(exc).splitlines()[0])

        n = await raw.fetchval(
            "SELECT count(*) FROM public.fee_run_lines WHERE fee_run_id=$1::uuid", run)
        fee = await raw.fetchval(
            "SELECT net_fee FROM public.fee_run_lines WHERE id=$1::uuid", line)
        R.expect("7e", n == 3 and fee != D(1),
                 "nothing changed after four refused attempts",
                 f"lines={n} net_fee={fee}")
    finally:
        await raw.close()


# ═══════════════════════════════════════════════════════════════════════════
# Check 8 — REVERSAL sums to zero, per account
# ═══════════════════════════════════════════════════════════════════════════


async def check_8(conn, posted_run):
    rev = await FR.create_reversal(conn, ORG, posted_run, created_by=U_MAKER,
                                   reason=f"{TAG} reversal")
    row = await FR.get_run(conn, ORG, rev)
    R.expect("8a", row["run_type"] == "REVERSAL" and row["reverses_run_id"] == posted_run,
             "the reversal is a run_type='REVERSAL' run pointing at its target",
             f"{row['run_type']} -> {row['reverses_run_id']}")

    orig = await FR.get_run(conn, ORG, posted_run)
    R.expect("8b", orig["status"] == "POSTED",
             "the original run is untouched and still POSTED", orig["status"])
    n_orig = await conn.fetchval(
        "SELECT count(*) FROM public.fee_run_lines WHERE fee_run_id=$1::uuid", posted_run)
    R.expect("8c", n_orig == 3, "the original run still has all 3 of its lines", str(n_orig))

    balance = await FR.reversal_balance(conn, ORG, posted_run)
    R.expect("8d", len(balance) == 3, "every account is accounted for", str(len(balance)))
    bad = [b for b in balance if b["net"] != D(0)]
    R.expect("8e", not bad,
             "original + reversal sums to EXACTLY zero for every account, one "
             "account at a time",
             json.dumps([{k: str(v) for k, v in b.items()} for b in bad]))
    for b in balance:
        if b["account_id"] == ACC_C:
            R.expect("8f", b["original_net_fee"] == D("5000.00")
                     and b["reversal_net_fee"] == D("-5000.00"),
                     "ACC_C: $5,000.00 reversed by exactly -$5,000.00",
                     f"{b['original_net_fee']} / {b['reversal_net_fee']}")

    # A second reversal would credit twice.
    try:
        await FR.create_reversal(conn, ORG, posted_run, created_by=U_MAKER)
        R.bad("8g", "a second reversal of the same run was permitted")
    except FR.FeeRunStateError:
        R.ok("8g", "a second reversal of the same run is refused")

    # Reversing an unposted run is a category error.
    draft = await FR.create_run(conn, ORG, period_start=P_START, period_end=P_END,
                                billing_frequency="QUARTERLY", created_by=U_MAKER)
    try:
        await FR.create_reversal(conn, ORG, draft, created_by=U_MAKER)
        R.bad("8h", "an unposted run was reversible")
    except FR.FeeRunStateError:
        R.ok("8h", "an unposted run is corrected by re-previewing it, not reversed")
    await conn.execute("DELETE FROM public.fee_runs WHERE id=$1::uuid", draft)

    R.find("8i", "fee_runs.status carries 'REVERSED' but the original run can "
                 "never reach it: fee_runs_immutable_once_posted refuses every "
                 "UPDATE on a POSTED row, by design. The link is read backwards, "
                 "through reverses_run_id. Deliberate, recorded as F36-C.")
    return rev


# ═══════════════════════════════════════════════════════════════════════════
# Check 9 — the snapshot hash reproduces AND is sensitive
# ═══════════════════════════════════════════════════════════════════════════


async def check_9(conn, posted_run):
    v = await FR.verify_snapshot(conn, ORG, posted_run)
    R.expect("9a", v.reproduces_from_stored_inputs,
             "re-hashing the stored input documents reproduces "
             "calculation_snapshot_hash exactly",
             f"stored={v.stored_hash[:16]} recomputed="
             f"{v.recomputed_hash_from_stored_inputs[:16]}")
    R.expect("9b", not v.line_mismatches,
             "re-running fee_calc against the reconstructed inputs reproduces "
             "every net_fee to the cent",
             json.dumps(list(v.line_mismatches)))
    R.expect("9c", v.inputs_unchanged_upstream,
             "re-loading the inputs from the live tables gives the same hash — "
             "nothing upstream has moved",
             f"live={(v.recomputed_hash_from_live_inputs or '')[:16]} "
             f"err={v.live_error}")
    R.expect("9d", v.ok, "the posted run verifies")

    # ── sensitivity: a hash that never moves proves nothing ─────────────────
    before = v.recomputed_hash_from_live_inputs
    await conn.execute(
        """INSERT INTO public.fee_exclusions
             (org_id, scope_type, scope_id, basis_type, treatment, reason, effective_from)
           VALUES ($1::uuid,'ACCOUNT',$2::uuid,'CASH','EXCLUDE',$3,'2024-01-01')""",
        ORG, ACC_A, f"{TAG} drift probe")
    try:
        v2 = await FR.verify_snapshot(conn, ORG, posted_run)
        R.expect("9e", v2.recomputed_hash_from_live_inputs != before,
                 "back-dating ONE exclusion into the posted period changes the "
                 "live hash — the hash genuinely covers what it claims to",
                 f"{(before or '')[:16]} -> "
                 f"{(v2.recomputed_hash_from_live_inputs or '')[:16]}")
        R.expect("9f", not v2.inputs_unchanged_upstream,
                 "the drift is REPORTED as drift")
        R.expect("9g", v2.reproduces_from_stored_inputs and not v2.line_mismatches,
                 "the posted run still reproduces from its OWN stored inputs — "
                 "upstream drift does not retroactively invalidate what was "
                 "billed")
    finally:
        await conn.execute(
            "DELETE FROM public.fee_exclusions WHERE org_id=$1::uuid AND reason=$2",
            ORG, f"{TAG} drift probe")

    v3 = await FR.verify_snapshot(conn, ORG, posted_run)
    R.expect("9h", v3.inputs_unchanged_upstream,
             "removing the probe restores the hash — the difference was the "
             "exclusion and nothing else")


# ═══════════════════════════════════════════════════════════════════════════
# Check 10 — cross-org isolation, on app_service
# ═══════════════════════════════════════════════════════════════════════════


async def check_10(app_dsn, run):
    if app_dsn is None:
        R.blocked("10", "no working app_service DSN — RLS is unprovable on postgres")
        return
    conn = await connect(app_dsn)
    try:
        bypass = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        if not R.expect("10a", bypass is False,
                        "the test role does NOT bypass RLS — without this every "
                        "check below is vacuous", f"rolbypassrls={bypass}"):
            return

        async def as_org(org, sql, *args):
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org)
                await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
                return await conn.fetch(sql, *args)
            finally:
                await tx.rollback()

        mine = await as_org(ORG, "SELECT id FROM public.fee_runs WHERE id=$1::uuid", run)
        theirs = await as_org(OTHER_ORG, "SELECT id FROM public.fee_runs WHERE id=$1::uuid", run)
        R.expect("10b", len(mine) == 1 and len(theirs) == 0,
                 "fee_runs: the owning org sees the run and the other org sees "
                 "nothing — inclusion AND exclusion on the same row",
                 f"own={len(mine)} other={len(theirs)}")

        mine = await as_org(ORG, "SELECT id FROM public.fee_run_lines WHERE fee_run_id=$1::uuid", run)
        theirs = await as_org(OTHER_ORG, "SELECT id FROM public.fee_run_lines WHERE fee_run_id=$1::uuid", run)
        R.expect("10c", len(mine) == 3 and len(theirs) == 0,
                 "fee_run_lines: 3 rows for the owning org, 0 for the other",
                 f"own={len(mine)} other={len(theirs)}")

        empty = await as_org("", "SELECT id FROM public.fee_runs WHERE id=$1::uuid", run)
        R.expect("10d", len(empty) == 0,
                 "an EMPTY org GUC returns zero rows rather than erroring or "
                 "matching everything — the NULLIF in the policy is real",
                 f"rows={len(empty)}")

        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", OTHER_ORG)
            await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
            try:
                await conn.execute(
                    """INSERT INTO public.fee_runs
                         (org_id, period_start, period_end, billing_frequency, run_type)
                       VALUES ($1::uuid,'2026-01-01','2026-03-31','QUARTERLY','SCHEDULED')""",
                    ORG)
                R.bad("10e", "an org was able to INSERT a fee_run into another org")
            except Exception as exc:
                R.expect("10e", "policy" in str(exc).lower(),
                         "the WITH CHECK refuses writing a row into another org",
                         str(exc).splitlines()[0])
        finally:
            await tx.rollback()
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Variance (Task 2's deliverable, checked)
# ═══════════════════════════════════════════════════════════════════════════


async def check_variance(conn):
    """A prior POSTED quarter, then a current PREVIEW, then the report."""
    prior = await FR.create_run(conn, ORG, period_start=PRIOR_START, period_end=PRIOR_END,
                                billing_frequency="QUARTERLY", created_by=U_MAKER)
    await FR.preview_run(conn, ORG, prior, account_ids=[ACC_A, ACC_B])
    await FR.propose_approval(conn, ORG, prior, gate="ADVISOR", proposed_by=U_MAKER)
    await FR.approve(conn, ORG, prior, gate="ADVISOR", approved_by=U_CHECKER)
    await FR.propose_approval(conn, ORG, prior, gate="COMPLIANCE", proposed_by=U_CHECKER)
    await FR.approve(conn, ORG, prior, gate="COMPLIANCE", approved_by=U_COMPLY)
    await FR.post_run(conn, ORG, prior)

    # ACC_A's balance doubles; ACC_B's is unchanged; ACC_C is brand new.
    await conn.execute(
        """UPDATE public.account_balances_daily SET total_market_value = 2000000.00
           WHERE account_id=$1::uuid AND as_of_date=$2::date""", ACC_A, P_END)

    cur = await FR.create_run(conn, ORG, period_start=P_START, period_end=P_END,
                              billing_frequency="QUARTERLY", created_by=U_MAKER)
    await FR.preview_run(conn, ORG, cur, account_ids=[ACC_A, ACC_B, ACC_C])
    report = await FR.variance_report(conn, ORG, cur)

    R.expect("Va", len(report) == 3, "the report covers every line", str(len(report)))
    by = {r["account_id"]: r for r in report}
    R.expect("Vb", by[ACC_A]["prior_net_fee"] == D("2500.00")
             and by[ACC_A]["net_fee"] == D("5000.00")
             and by[ACC_A]["change"] == D("2500.00"),
             "ACC_A: $2,500.00 -> $5,000.00, a +$2,500.00 change against the "
             "prior POSTED period",
             f"{by[ACC_A]['prior_net_fee']} -> {by[ACC_A]['net_fee']}")
    R.expect("Vc", by[ACC_B]["change"] == D("0.00"),
             "ACC_B is unchanged and says so", str(by[ACC_B]["change"]))
    R.expect("Vd", by[ACC_C]["is_new"] is True and by[ACC_C]["prior_net_fee"] is None,
             "ACC_C has no prior POSTED line and is flagged new — not compared "
             "against zero, which would read as an infinite increase")
    R.expect("Ve", report[0]["account_id"] == ACC_A,
             "the report is sorted by absolute dollar change descending — the "
             "biggest mover is first, which is what an advisor reads",
             report[0]["account_id"])
    R.expect("Vf", report[-1]["account_id"] == ACC_C,
             "accounts with no prior sort last; they have no change to rank",
             report[-1]["account_id"])

    await conn.execute(
        """UPDATE public.account_balances_daily SET total_market_value = 1000000.00
           WHERE account_id=$1::uuid AND as_of_date=$2::date""", ACC_A, P_END)
    return cur, prior


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    repo = API_DIR.parent.parent
    fix_sql = (repo / "docs" / "fee36_part1_fix.sql").read_text()

    admin_url, admin_prov = await admin_dsn()
    app_url, app_prov = await app_service_dsn()
    print(f"admin dsn:       {admin_prov}")
    print(f"app_service dsn: {app_prov}\n")
    if admin_url is None:
        print("BLOCKED: no working admin DSN")
        return 2

    conn = await connect(admin_url)
    before = None
    try:
        await teardown(conn)          # in case a previous run died mid-way
        before = await counts(conn)
        print("pre-test row counts captured\n")

        await build_fixtures(conn)
        if await conn.fetchval("SELECT count(*) FROM public.spvs WHERE id=$1::uuid", SPV) == 0:
            R.blocked("5", "no deals row exists in this org and spvs.deal_id is "
                           "NOT NULL — the F1 fixture cannot be built")

        await check_1(conn, conn, fix_sql)
        run = await check_2(conn)
        await check_3(conn, run)
        await check_6(conn)
        await check_4(conn, run)
        await check_5(conn, run)
        await check_7(admin_url, run)
        await check_9(conn, run)
        await check_8(conn, run)
        await check_10(app_url, run)
        await check_variance(conn)

    except Exception:
        R.bad("RUN", "the script raised", traceback.format_exc())
    finally:
        try:
            await teardown(conn)
        except Exception:
            R.bad("TEARDOWN", "teardown raised", traceback.format_exc())

        R.summary()

        if before is not None:
            after = await counts(conn)
            drift = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
            if drift:
                R.bad("11", "row counts differ after teardown",
                      json.dumps({k: list(v) for k, v in drift.items()}))
                print("\n[FAIL] 11  ROW COUNT DRIFT: " +
                      json.dumps({k: list(v) for k, v in drift.items()}))
            else:
                print(f"\n[PASS] 11  every one of {len(COUNTED)} touched tables is "
                      f"back to its pre-test row count")
                R.rows.append(("PASS", "11", "no row-count drift"))
        await conn.close()

    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
