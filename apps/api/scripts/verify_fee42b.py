"""Sprint fee42b verification — SPV carry distribution engine.

Pass/fail only, no prompts. Run:

    python3 apps/api/scripts/verify_fee42b.py

Every table this script writes to is counted before the first insert and again
after the last delete; a difference of even one row fails the run, reported
AFTER the tests so a teardown bug never masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[1] proves the balance CHECK BEHAVIOURALLY, in both directions.** Reading
  ``spv_carry_run_lines_balance_check`` out of ``pg_constraint`` proves the text
  of the constraint, not its effect. So a line whose ``net_to_lp + carry_to_gp``
  is one cent off the ``gross_gain_allocated`` is actually INSERTed and must be
  refused — and, because "refuses everything" would also pass that, a
  reconciling line must be ACCEPTED on the same table in the same breath.

* **[1g] is the positive control the immutability triggers need.** Both
  triggers ``RETURN COALESCE(NEW, OLD)``. That detail is load-bearing: a
  ``RETURN NEW`` in a ``BEFORE DELETE`` trigger returns NULL, which SILENTLY
  SKIPS the delete — fee36 found exactly that bug in this codebase. So a
  NON-posted run and its line are really UPDATEd and really DELETEd here, and
  the row counts are re-read afterwards. Without this, [7]'s "POSTED cannot be
  changed" would also pass for a trigger that blocks everything, and for one
  that silently swallows every delete.

* **[2] compares against numbers computed by hand, not against the engine.**
  Each golden case's expected ``return_of_capital``/``preferred_return``/
  ``gp_catchup``/``carry_to_gp``/``net_to_lp`` is written out as a literal with
  the arithmetic that produced it beside it. A test that asserts the engine
  agrees with itself proves the engine is deterministic and nothing else. Each
  case also re-derives the GP's share as a PERCENTAGE of profit, because
  "20% carry paid the GP 20% of the profit" is the one check that catches a
  tier boundary in the wrong place while every individual number still adds up.

* **[3] changes exactly one field.** Case 3 and case 4 share their gain, their
  paid-in, their prior distributions, their carry_pct, their hurdle_pct and
  their catchup_pct; only ``hurdle_type`` differs, and the script asserts that
  by diffing the two input dicts before comparing the outputs. It then asserts
  the DIRECTION — a HARD hurdle must pay the GP strictly LESS than a SOFT one,
  never merely "differently", because a sign error would satisfy "they are not
  equal" while handing the GP the LP's preferred return.

* **[4] drives the REAL path end to end and touches nothing by hand.** The
  distribution is posted through ``spv_allocation.post_transaction`` — the
  single writer of ``status='posted'`` — on the deployed ``_RLSPool``, not a
  raw pool (``workflow_engine._independent_acquire`` exists because a raw pool
  hides the savepoint-not-commit failure mode, and every earlier verify script
  used one). No function in this script calls ``propose_carry_run``: the DRAFT
  either arrives because the event fired and the subscriber ran, or it does not
  arrive.

* **[4c] proves the automatic path is not a permission bypass.** The same post,
  by an actor who does NOT hold ``manage_billing``, must produce NO carry run
  and a FAILED delivery. An event trigger that reached further than the person
  it runs as would be a privilege escalation with an audit trail saying a
  workflow did it.

* **[5] proves the DRAFT is a floor, not a starting gun.** Status is asserted
  to be exactly ``'DRAFT'``, with ``posted_at``, both ``*_approved_by`` and both
  ``*_approved_at`` NULL, and ZERO ``assistant_activities`` rows — and the
  workflow run itself is asserted to be PAUSED at its User Task rather than
  completed, because the pause is what "propose, never dispose" is made of.

* **[6] proves BOTH layers of maker-checker refuse self-approval.** The service
  refuses it (``MakerCheckerError``) AND a direct UPDATE that bypasses the
  service entirely is refused by ``assistant_activities_maker_checker_chk``.
  Neither substitutes for the other. The run status is re-read after the
  refusal to prove nothing moved. [6h] then walks the ledger back to
  ``'proposed'`` while leaving ``spv_carry_runs.status`` at
  COMPLIANCE_APPROVED, and ``post_run`` must still refuse — proving the posting
  decision genuinely rests on the activities ledger and not on the status
  mirror it maintains.

* **[7] attacks the POSTED run from the DATABASE, not through the service.**
  Four separate statements — UPDATE run, DELETE run, UPDATE line, DELETE line —
  issued as raw SQL on the admin connection. A service layer that simply
  declines to offer an edit proves nothing about what a script, a migration or
  a psql session can do.

* **[8] re-reads the lines from the database.** The reconciliation is checked
  against what the ``numeric`` columns actually hold after a round trip, not
  against the ``CarryResult`` objects still in memory, and it checks BOTH
  identities: the balance constraint, and that the four tiers tile the gain.

* **[9] runs on app_service, whose ``rolbypassrls`` is asserted False FIRST.**
  Without that assertion every isolation check below it proves nothing. Both
  directions are proved on both tables: the other org's rows are invisible, the
  caller's OWN rows are visible (otherwise "sees nothing" passes), and an
  INSERT into another org is refused by the policy's WITH CHECK.

* **Teardown disables the two immutability triggers for exactly the length of
  the DELETE and re-enables them, then asserts they are enabled again.** A
  POSTED fixture row cannot be removed any other way — that is the entire point
  of the trigger — and leaving them disabled would silently unprotect the table
  for every future run. Teardown is otherwise by fixture id in FK order, never
  a TRUNCATE.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import pathlib
import sys
import traceback
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncpg  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "fee42bverify"
BPMN_FIXTURE = API_DIR / "fixtures" / "spv_realization_carry_proposal.bpmn"

# ── fixture ids ─────────────────────────────────────────────────────────────
U_ACTOR = "99000000-0000-0000-0000-00004b420001"      # holds manage_billing
U_NOPERM = "99000000-0000-0000-0000-00004b420002"     # holds nothing — [4c]
U_APPROVER = "99000000-0000-0000-0000-00004b420003"   # advisor checker
U_COMPLIANCE = "99000000-0000-0000-0000-00004b420004"  # compliance checker
USERS = [U_ACTOR, U_NOPERM, U_APPROVER, U_COMPLIANCE]

PROFILE_BILLING = f"{TAG}_billing"
PROFILE_NONE = f"{TAG}_none"
PROFILES = [PROFILE_BILLING, PROFILE_NONE]

DEAL_MAIN = "99000000-0000-0000-0000-00004b420011"
DEAL_SERIES = "99000000-0000-0000-0000-00004b420012"
DEAL_OTHER = "99000000-0000-0000-0000-00004b420013"
DEALS = [DEAL_MAIN, DEAL_SERIES, DEAL_OTHER]

E_ONE = "99000000-0000-0000-0000-00004b420021"        # 60% — SOFT (base terms)
E_TWO = "99000000-0000-0000-0000-00004b420022"        # 40% — HARD (side letter)
E_OTHER = "99000000-0000-0000-0000-00004b420023"
ENTITIES = [E_ONE, E_TWO, E_OTHER]

SPV_MAIN = "99000000-0000-0000-0000-00004b420031"
SPV_SERIES = "99000000-0000-0000-0000-00004b420032"   # member_series — [F4]
SPV_OTHER = "99000000-0000-0000-0000-00004b420033"
SPVS = [SPV_MAIN, SPV_SERIES, SPV_OTHER]

SUB_ONE = "99000000-0000-0000-0000-00004b420041"
SUB_TWO = "99000000-0000-0000-0000-00004b420042"
SUBS = [SUB_ONE, SUB_TWO]

TXN_CALL = "99000000-0000-0000-0000-00004b420051"     # capital call, posted
TXN_GAIN = "99000000-0000-0000-0000-00004b420052"     # the realization — [4]
TXN_GAIN_NOPERM = "99000000-0000-0000-0000-00004b420053"  # [4c]
TXNS = [TXN_CALL, TXN_GAIN, TXN_GAIN_NOPERM]

DEF_CARRY = "99000000-0000-0000-0000-00004b420061"
DEFS = [DEF_CARRY]
VER_CARRY = "99000000-0000-0000-0000-00004b420071"
VERSIONS = [VER_CARRY]
TRG_CARRY = "99000000-0000-0000-0000-00004b420081"
TRIGGERS = [TRG_CARRY]

# Rows [1] and [9] write directly; ids fixed so teardown is exact.
RUN_SHAPE = "99000000-0000-0000-0000-00004b420091"    # [1e]/[1g] scratch run
RUN_XORG = "99000000-0000-0000-0000-00004b420092"     # [9] other-org run
LINE_XORG = "99000000-0000-0000-0000-00004b420093"
RUN_OWN_WRITE = "99000000-0000-0000-0000-00004b420094"  # [9g] own-org control
DIRECT_RUNS = [RUN_SHAPE, RUN_XORG, RUN_OWN_WRITE]


class _Rollback(Exception):
    """Unwinds a savepoint after a write whose EFFECT was the point, not its
    persistence. Used so the own-org positive control in [9g] proves the INSERT
    is genuinely allowed without leaving a row behind for [12] to trip on."""

# ── the economics, chosen so every number divides to the cent ───────────────
CALL_AMOUNT = D("1000000.00")     # 60/40 -> 600,000.00 / 400,000.00
GAIN_AMOUNT = D("1500000.00")     # 60/40 -> 900,000.00 / 600,000.00
CARRY_PCT = D("0.20")
HURDLE_PCT = D("0.08")
CATCHUP_PCT = D("1.00")

STEP_SERVICE = "Propose_Carry_Run"
STEP_USER = "Review_Carry_Proposal"

COUNTED = (
    "public.spv_carry_run_lines",
    "public.spv_carry_runs",
    "public.assistant_activities",
    "public.domain_event_deliveries",
    "public.domain_events",
    "public.member_todos",
    "public.workflow_run_steps",
    "public.workflow_runs",
    "public.workflow_triggers",
    "public.workflow_steps",
    "public.workflow_versions",
    "public.workflow_definitions",
    "public.spv_fee_side_letters",
    "public.spv_fee_terms",
    "public.spv_transaction_allocations",
    "public.spv_transactions",
    "public.spv_subscriptions",
    "public.spvs",
    "public.entities",
    "public.deals",
    "public.audit_log",
    "public.profile_permissions",
    "public.profiles",
    "public.users",
)

TRIGGER_NAMES = (
    ("public.spv_carry_runs", "spv_carry_runs_immutable_once_posted"),
    ("public.spv_carry_run_lines", "spv_carry_run_lines_immutable_once_posted"),
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
        print(f"fee42b: {counts.get('PASS', 0)}/{total} PASS" + "".join(
            f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


def as_json(value) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return json.loads(value)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
async def _set_triggers(conn, enabled: bool) -> None:
    """Disable/enable the two immutability triggers.

    ONLY used by teardown. A POSTED fixture row cannot be deleted while they
    are on — that IS the trigger — and [12] asserts they are back on before the
    script exits, so a crash between the two cannot leave the table unguarded
    without the run failing.
    """
    verb = "ENABLE" if enabled else "DISABLE"
    for table, trig in TRIGGER_NAMES:
        await conn.execute(f"ALTER TABLE {table} {verb} TRIGGER {trig}")


async def teardown(conn) -> None:
    """By fixture id, in FK order. Never a TRUNCATE."""
    await _set_triggers(conn, False)
    try:
        await conn.execute(
            """DELETE FROM public.spv_carry_run_lines
               WHERE spv_carry_run_id IN (
                   SELECT id FROM public.spv_carry_runs
                   WHERE spv_id = ANY($1::uuid[]) OR id = ANY($2::uuid[]))
                  OR id = ANY($3::uuid[])""",
            SPVS, DIRECT_RUNS, [LINE_XORG])
        await conn.execute(
            """DELETE FROM public.spv_carry_runs
               WHERE spv_id = ANY($1::uuid[]) OR id = ANY($2::uuid[])""",
            SPVS, DIRECT_RUNS)
    finally:
        await _set_triggers(conn, True)

    await conn.execute(
        """DELETE FROM public.assistant_activities
           WHERE related_type = 'spv_carry_run'
              OR user_id = ANY($1::uuid[])
              OR proposed_by = ANY($1::uuid[])""",
        USERS)
    # Todos raised by a HELD run ([4c]) land on the run's starter AND on every
    # org_admin of the org — real people this script did not create. They are
    # reaped by the run they point at, not by recipient.
    await conn.execute(
        """DELETE FROM public.member_todos
           WHERE (related_type = 'workflow_run' AND related_id IN
                    (SELECT id FROM public.workflow_runs
                     WHERE workflow_version_id = ANY($1::uuid[])))
              OR related_id IN
                    (SELECT rs.id FROM public.workflow_run_steps rs
                     JOIN public.workflow_runs r ON r.id = rs.workflow_run_id
                     WHERE r.workflow_version_id = ANY($1::uuid[]))
              OR user_id = ANY($2::uuid[])""",
        VERSIONS, USERS)
    await conn.execute(
        """DELETE FROM public.domain_event_deliveries
           WHERE workflow_trigger_id = ANY($1::uuid[])
              OR domain_event_id IN (SELECT id FROM public.domain_events
                                     WHERE source_id = ANY($2::uuid[]))""",
        TRIGGERS, TXNS)
    await conn.execute(
        "DELETE FROM public.domain_events WHERE source_id = ANY($1::uuid[])", TXNS)
    await conn.execute(
        """DELETE FROM public.workflow_run_steps
           WHERE workflow_run_id IN (SELECT id FROM public.workflow_runs
                                     WHERE workflow_version_id = ANY($1::uuid[]))""",
        VERSIONS)
    await conn.execute(
        "DELETE FROM public.workflow_runs WHERE workflow_version_id = ANY($1::uuid[])",
        VERSIONS)
    await conn.execute(
        "DELETE FROM public.workflow_triggers WHERE id = ANY($1::uuid[])", TRIGGERS)
    await conn.execute(
        "DELETE FROM public.workflow_steps WHERE workflow_version_id = ANY($1::uuid[])",
        VERSIONS)
    await conn.execute(
        "DELETE FROM public.workflow_versions WHERE id = ANY($1::uuid[])", VERSIONS)
    await conn.execute(
        "DELETE FROM public.workflow_definitions WHERE id = ANY($1::uuid[])", DEFS)
    await conn.execute(
        """DELETE FROM public.spv_transaction_allocations
           WHERE transaction_id = ANY($1::uuid[])""", TXNS)
    await conn.execute(
        "DELETE FROM public.spv_transactions WHERE id = ANY($1::uuid[])", TXNS)
    # create_terms / create_side_letter give their rows ids this script never
    # sees, so they are reaped by spv_id.
    await conn.execute(
        "DELETE FROM public.spv_fee_side_letters WHERE spv_id = ANY($1::uuid[])", SPVS)
    await conn.execute(
        "DELETE FROM public.spv_fee_terms WHERE spv_id = ANY($1::uuid[])", SPVS)
    await conn.execute(
        "DELETE FROM public.spv_subscriptions WHERE spv_id = ANY($1::uuid[])", SPVS)
    await conn.execute(
        "DELETE FROM public.spv_status_history WHERE spv_id = ANY($1::uuid[])", SPVS)
    await conn.execute("DELETE FROM public.spvs WHERE id = ANY($1::uuid[])", SPVS)
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", ENTITIES)
    await conn.execute("DELETE FROM public.deals WHERE id = ANY($1::uuid[])", DEALS)
    await conn.execute(
        """DELETE FROM public.audit_log
           WHERE resource_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])""",
        TXNS, USERS)
    await conn.execute(
        "UPDATE public.users SET profile_id = NULL WHERE id = ANY($1::uuid[])", USERS)
    await conn.execute(
        """DELETE FROM public.profile_permissions WHERE profile_id IN
           (SELECT id FROM public.profiles WHERE org_id = $1::uuid
              AND name = ANY($2::text[]))""",
        ORG, PROFILES)
    await conn.execute(
        """DELETE FROM public.profiles WHERE org_id = $1::uuid
             AND name = ANY($2::text[]) AND is_seed = false""",
        ORG, PROFILES)
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def type_id(conn, code: str):
    return await conn.fetchval(
        "SELECT id FROM public.transaction_types WHERE code = $1", code)


async def build_fixtures(conn) -> None:
    from services.spv_fee_terms import create_terms

    # ── profiles: one WITH manage_billing, one with nothing. [4c] needs the
    #    second to be a real member who simply lacks the key, not a stranger.
    p_billing = await conn.fetchval(
        """INSERT INTO public.profiles (org_id, name, description, is_seed)
           VALUES ($1::uuid,$2,'fee42b verify',false) RETURNING id""",
        ORG, PROFILE_BILLING)
    p_none = await conn.fetchval(
        """INSERT INTO public.profiles (org_id, name, description, is_seed)
           VALUES ($1::uuid,$2,'fee42b verify',false) RETURNING id""",
        ORG, PROFILE_NONE)
    await conn.execute(
        """INSERT INTO public.profile_permissions (org_id, profile_id, permission_key)
           VALUES ($1::uuid,$2,'manage_billing')""",
        ORG, p_billing)

    for uid, sub, pid in (
        (U_ACTOR, "actor", p_billing),
        (U_NOPERM, "noperm", p_none),
        (U_APPROVER, "approver", p_billing),
        (U_COMPLIANCE, "compliance", p_billing),
    ):
        await conn.execute(
            """INSERT INTO public.users
                 (id, org_id, email, full_name, auth0_sub, role, profile_id, is_active)
               VALUES ($1::uuid,$2::uuid,$3,$4,$5,'member',$6,true)""",
            uid, ORG, f"{sub}@{TAG}.local", f"{TAG} {sub}", f"auth0|{TAG}-{sub}", pid)

    for did, org, nm in ((DEAL_MAIN, ORG, "main"), (DEAL_SERIES, ORG, "series"),
                         (DEAL_OTHER, OTHER_ORG, "other")):
        await conn.execute(
            "INSERT INTO public.deals (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
            did, org, f"{TAG} deal {nm}")

    for eid, org, nm in ((E_ONE, ORG, "investor one"), (E_TWO, ORG, "investor two"),
                         (E_OTHER, OTHER_ORG, "otherorg investor")):
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
               VALUES ($1::uuid,$2::uuid,'individual',$3)""",
            eid, org, f"{TAG} {nm}")

    await conn.execute(
        """INSERT INTO public.spvs
             (id, org_id, deal_id, name, spv_status, vehicle_type, master_entity_id)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4,'closed','standalone_spv',NULL)""",
        SPV_MAIN, ORG, DEAL_MAIN, f"{TAG} spv main")
    # A member_series vehicle whose whole fund is bigger than itself — [F4].
    await conn.execute(
        """INSERT INTO public.spvs
             (id, org_id, deal_id, name, spv_status, vehicle_type, master_entity_id)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4,'closed','member_series',$5::uuid)""",
        SPV_SERIES, ORG, DEAL_SERIES, f"{TAG} spv series", E_ONE)
    await conn.execute(
        """INSERT INTO public.spvs
             (id, org_id, deal_id, name, spv_status, vehicle_type)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4,'closed','standalone_spv')""",
        SPV_OTHER, OTHER_ORG, DEAL_OTHER, f"{TAG} spv otherorg")

    for sub, eid, pct in ((SUB_ONE, E_ONE, 60), (SUB_TWO, E_TWO, 40)):
        await conn.execute(
            """INSERT INTO public.spv_subscriptions
                 (id, org_id, spv_id, entity_id, commitment_amount, funded_amount,
                  ownership_pct, subscription_status)
               VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,1000000,0,
                       $5::numeric,'funded')""",
            sub, ORG, SPV_MAIN, eid, pct)

    # ── Base terms: SOFT hurdle, through fee42's OWN writer, not raw SQL.
    await create_terms(
        conn, ORG, SPV_MAIN,
        effective_from="2026-01-01",
        mgmt_fee_basis="COMMITTED", mgmt_fee_frequency="ANNUAL",
        carry_pct=CARRY_PCT, hurdle_pct=HURDLE_PCT, hurdle_type="SOFT",
        catchup_pct=CATCHUP_PCT, carry_basis="DEAL_BY_DEAL",
        clawback_applies=True, created_by=U_ACTOR,
    )
    # ── A side letter moving investor TWO to a HARD hurdle. fee42 shipped a
    #    LOADER and no writer for this table (measured — services.spv_fee_terms
    #    has load_side_letter and no create_side_letter), so the row is written
    #    directly, exactly as verify_fee42.py's own fixtures do. Resolution is
    #    still fee42's job: this proves the consumer really goes through
    #    resolve_terms_for_entity, and it makes [4] pay two investors under two
    #    genuinely different waterfalls.
    await conn.execute(
        """INSERT INTO public.spv_fee_side_letters
             (org_id, spv_id, entity_id, overrides, effective_from,
              approved_by, reason)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::jsonb,'2026-01-01',
                   $5::uuid,$6)""",
        ORG, SPV_MAIN, E_TWO, json.dumps({"hurdle_type": "HARD"}),
        U_ACTOR, f"{TAG}: hard hurdle side letter")

    call_id = await type_id(conn, "call_investment")
    gain_id = await type_id(conn, "dist_gain")
    await conn.execute(
        """INSERT INTO public.spv_transactions
             (id, org_id, spv_id, txn_type, txn_date, amount, status,
              allocation_basis, transaction_type_id, currency_code, description)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'capital_call','2026-02-01',
                   $4::numeric,'draft','ownership_pct',$5::uuid,'USD',$6)""",
        TXN_CALL, ORG, SPV_MAIN, CALL_AMOUNT, call_id, f"{TAG} call")
    for tid in (TXN_GAIN, TXN_GAIN_NOPERM):
        await conn.execute(
            """INSERT INTO public.spv_transactions
                 (id, org_id, spv_id, txn_type, txn_date, amount, status,
                  allocation_basis, transaction_type_id, currency_code, description)
               VALUES ($1::uuid,$2::uuid,$3::uuid,'distribution','2026-06-30',
                       $4::numeric,'draft','ownership_pct',$5::uuid,'USD',$6)""",
            tid, ORG, SPV_MAIN, GAIN_AMOUNT, gain_id, f"{TAG} realization")

    # ── The subscription: definition + version + REAL derived steps + trigger.
    from services.workflow_steps_deriver import derive_and_store_steps

    bpmn_xml = BPMN_FIXTURE.read_text()
    await conn.execute(
        """INSERT INTO public.workflow_definitions
             (id, org_id, name, description, created_by)
           VALUES ($1::uuid,$2::uuid,$3,$4,$5::uuid)""",
        DEF_CARRY, ORG, f"{TAG} SPV realization carry proposal",
        f"{TAG} fixture", U_ACTOR)
    await conn.execute(
        """INSERT INTO public.workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current)
           VALUES ($1::uuid,$2::uuid,$3::uuid,1,$4,'v1',true)""",
        VER_CARRY, DEF_CARRY, ORG, bpmn_xml)
    await derive_and_store_steps(conn, VER_CARRY, ORG, bpmn_xml)
    await conn.execute(
        """INSERT INTO public.workflow_triggers
             (id, workflow_definition_id, org_id, trigger_type, event_type,
              is_active, created_by)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'event','spv_realization',true,$4::uuid)""",
        TRG_CARRY, DEF_CARRY, ORG, U_ACTOR)


async def post_through_real_path(pool, txn_id, actor) -> None:
    """Allocate then post via the REAL service functions — never a hand-written
    UPDATE. The emitter hangs off ``post_transaction``; a fixture that set
    ``status='posted'`` in SQL would bypass exactly what is under test."""
    from services.spv_allocation import allocate_transaction, post_transaction

    await allocate_transaction(pool, str(txn_id), actor)
    await post_transaction(pool, str(txn_id), actor)


# ═══════════════════════════════════════════════════════════════════════════
# [1] Deployment, shape, the balance CHECK, and the immutability triggers
# ═══════════════════════════════════════════════════════════════════════════
async def check_1(admin) -> None:
    from services.spv_carry import CARRY_BASES
    from services.spv_carry_runs import RUN_STATUSES

    for t in ("spv_carry_runs", "spv_carry_run_lines"):
        R.expect(f"1a:{t}",
                 await admin.fetchval("SELECT to_regclass($1)", f"public.{t}") is not None,
                 f"{t} is deployed")
        rls = await admin.fetchval(
            """SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n
               ON n.oid = c.relnamespace WHERE n.nspname='public' AND relname=$1""", t)
        R.expect(f"1b:{t}", rls is True, f"{t} has RLS enabled")
        pol = await admin.fetchrow(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE schemaname='public' AND tablename=$1", t)
        R.expect(f"1c:{t}",
                 pol is not None
                 and "NULLIF" in (pol["qual"] or "")
                 and "app.current_org_id" in (pol["qual"] or "")
                 and "NULLIF" in (pol["with_check"] or ""),
                 f"{t} carries an org-isolation policy with the NULLIF guard on "
                 f"both USING and WITH CHECK",
                 detail=str(dict(pol) if pol else None))

    status_def = await admin.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='spv_carry_runs_status_check'")
    R.expect("1d", status_def is not None
             and all(s in status_def for s in RUN_STATUSES),
             "spv_carry_runs_status_check admits exactly the lifecycle this "
             "module implements, and the module's RUN_STATUSES was read from it",
             detail=str(status_def))
    basis_def = await admin.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='spv_carry_runs_carry_basis_check'")
    R.expect("1d2", basis_def is not None
             and all(b in basis_def for b in CARRY_BASES),
             "spv_carry_runs_carry_basis_check admits DEAL_BY_DEAL and WHOLE_FUND",
             detail=str(basis_def))

    bal_def = await admin.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname='spv_carry_run_lines_balance_check'")
    R.expect("1e", bal_def is not None and "net_to_lp" in bal_def
             and "carry_to_gp" in bal_def and "gross_gain_allocated" in bal_def,
             "spv_carry_run_lines_balance_check is deployed on "
             "(net_to_lp + carry_to_gp = gross_gain_allocated)",
             detail=str(bal_def))

    # ── BEHAVIOURAL, both directions. A scratch DRAFT run to hang lines off.
    await admin.execute(
        """INSERT INTO public.spv_carry_runs
             (id, org_id, spv_id, status, carry_basis, calculation_snapshot_hash,
              engine_version)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'DRAFT','DEAL_BY_DEAL','shape','shape')""",
        RUN_SHAPE, ORG, SPV_MAIN)

    refused = False
    try:
        await admin.execute(
            """INSERT INTO public.spv_carry_run_lines
                 (org_id, spv_carry_run_id, entity_id, gross_gain_allocated,
                  return_of_capital, preferred_return, gp_catchup, carry_to_gp,
                  net_to_lp, calc_detail)
               VALUES ($1::uuid,$2::uuid,$3::uuid,1000.00,0,0,0,200.00,799.99,
                       '{}'::jsonb)""",
            ORG, RUN_SHAPE, E_ONE)
    except asyncpg.CheckViolationError:
        refused = True
    R.expect("1e2", refused,
             "a line one cent out of balance (200.00 + 799.99 != 1000.00) is "
             "REFUSED by the database, not merely by the engine")

    accepted_id = None
    try:
        accepted_id = await admin.fetchval(
            """INSERT INTO public.spv_carry_run_lines
                 (org_id, spv_carry_run_id, entity_id, gross_gain_allocated,
                  return_of_capital, preferred_return, gp_catchup, carry_to_gp,
                  net_to_lp, calc_detail)
               VALUES ($1::uuid,$2::uuid,$3::uuid,1000.00,0,0,0,200.00,800.00,
                       '{}'::jsonb) RETURNING id""",
            ORG, RUN_SHAPE, E_ONE)
    except asyncpg.PostgresError:
        accepted_id = None
    R.expect("1e3", accepted_id is not None,
             "a line that DOES reconcile is accepted — the constraint refuses "
             "unbalanced rows, not every row")

    for table, trig in TRIGGER_NAMES:
        tdef = await admin.fetchval(
            "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid WHERE t.tgname = $1", trig)
        R.expect(f"1f:{trig}",
                 tdef is not None and "BEFORE DELETE OR UPDATE" in tdef,
                 f"{trig} fires BEFORE DELETE OR UPDATE on {table}",
                 detail=str(tdef))

    # ── [1g] THE POSITIVE CONTROL. A BEFORE DELETE trigger that RETURNs NEW
    #    returns NULL and silently swallows the delete (fee36 found exactly
    #    that). Both triggers RETURN COALESCE(NEW, OLD); prove it.
    await admin.execute(
        "UPDATE public.spv_carry_run_lines SET gp_catchup = 1.00 WHERE id = $1",
        accepted_id)
    moved = await admin.fetchval(
        "SELECT gp_catchup FROM public.spv_carry_run_lines WHERE id = $1", accepted_id)
    R.expect("1g", moved == D("1.00"),
             "a line on a NON-posted run can still be UPDATEd — the trigger "
             "blocks POSTED rows, not all rows", detail=str(moved))

    await admin.execute(
        "DELETE FROM public.spv_carry_run_lines WHERE id = $1", accepted_id)
    left = await admin.fetchval(
        "SELECT count(*) FROM public.spv_carry_run_lines WHERE id = $1", accepted_id)
    R.expect("1g2", left == 0,
             "a line on a NON-posted run is really DELETEd — the trigger does "
             "not silently swallow the delete by returning NULL",
             detail=f"rows remaining: {left}")

    await admin.execute(
        "UPDATE public.spv_carry_runs SET status = 'PREVIEW' WHERE id = $1::uuid",
        RUN_SHAPE)
    st = await admin.fetchval(
        "SELECT status FROM public.spv_carry_runs WHERE id = $1::uuid", RUN_SHAPE)
    await admin.execute(
        "DELETE FROM public.spv_carry_runs WHERE id = $1::uuid", RUN_SHAPE)
    gone = await admin.fetchval(
        "SELECT count(*) FROM public.spv_carry_runs WHERE id = $1::uuid", RUN_SHAPE)
    R.expect("1g3", st == "PREVIEW" and gone == 0,
             "a NON-posted run can be UPDATEd and really DELETEd",
             detail=f"status={st} remaining={gone}")


# ═══════════════════════════════════════════════════════════════════════════
# [2] Five golden cases, hand-computed. [3] HARD vs SOFT genuinely differ.
# ═══════════════════════════════════════════════════════════════════════════
#
# Every case: paid_in 1,000,000.00, hurdle 8% -> preferred return owed
# 80,000.00, carry 20%, catch-up 100%.
#
#  1  G = 400,000      absorbed entirely by return of capital.
#                      roc 400,000 | pref 0 | catchup 0 | carry 0 | lp 400,000
#  2  G = 1,050,000    capital back, 50,000 into an 80,000 pref. Not cleared.
#                      roc 1,000,000 | pref 50,000 | carry 0 | lp 1,050,000
#  3  G = 1,500,000    HARD. profit 500,000; pref 80,000 is the LP's for good;
#                      420,000 splits 20/80 -> GP 84,000.
#                      roc 1,000,000 | pref 80,000 | catchup 0 | carry 84,000
#                      | lp 1,416,000
#  4  G = 1,500,000    SOFT, otherwise identical. Catch-up tier
#                      C = 0.20*80,000/(1.00-0.20) = 20,000, all to the GP;
#                      residual 400,000 splits 20/80 -> 80,000.
#                      carry 20,000 + 80,000 = 100,000 = 20% of the whole
#                      500,000 profit — which is what a soft hurdle means.
#                      roc 1,000,000 | pref 80,000 | catchup 20,000
#                      | carry 100,000 | lp 1,400,000
#  5  G = 2,000,000 on top of 1,050,000 ALREADY distributed (case 2's state),
#                      SOFT. Cumulative 3,050,000; cumulative profit 2,050,000;
#                      GP cumulative 20% = 410,000, none of it taken before.
#                      roc 0 (already returned) | pref 30,000 (the rest of the
#                      80,000) | catchup 20,000 | carry 410,000 | lp 1,590,000
#
GOLDEN = [
    ("2a", "fully absorbed by return of capital", "HARD",
     D("400000.00"), D("1000000.00"), D("0.00"),
     dict(roc=D("400000.00"), pref=D("0.00"), catchup=D("0.00"),
          carry=D("0.00"), lp=D("400000.00"))),
    ("2b", "into the preferred return but hurdle NOT cleared", "HARD",
     D("1050000.00"), D("1000000.00"), D("0.00"),
     dict(roc=D("1000000.00"), pref=D("50000.00"), catchup=D("0.00"),
          carry=D("0.00"), lp=D("1050000.00"))),
    ("2c", "hurdle cleared, HARD — no catch-up, carry only above the hurdle",
     "HARD",
     D("1500000.00"), D("1000000.00"), D("0.00"),
     dict(roc=D("1000000.00"), pref=D("80000.00"), catchup=D("0.00"),
          carry=D("84000.00"), lp=D("1416000.00"))),
    ("2d", "same fixture, SOFT — the GP catches up on the whole pref", "SOFT",
     D("1500000.00"), D("1000000.00"), D("0.00"),
     dict(roc=D("1000000.00"), pref=D("80000.00"), catchup=D("20000.00"),
          carry=D("100000.00"), lp=D("1400000.00"))),
    ("2e", "catch-up complete and the residual split at carry_pct", "SOFT",
     D("2000000.00"), D("1000000.00"), D("1050000.00"),
     dict(roc=D("0.00"), pref=D("30000.00"), catchup=D("20000.00"),
          carry=D("410000.00"), lp=D("1590000.00"))),
]


def _terms(hurdle_type):
    from services.spv_carry import terms_from_resolved
    return terms_from_resolved({
        "carry_pct": CARRY_PCT, "hurdle_pct": HURDLE_PCT,
        "hurdle_type": hurdle_type, "catchup_pct": CATCHUP_PCT,
        "carry_basis": "DEAL_BY_DEAL", "clawback_applies": True,
    })


def check_2_and_3() -> None:
    from services.spv_carry import InvestorState, compute_carry

    results = {}
    for ref, label, ht, gain, paid_in, prior, want in GOLDEN:
        res = compute_carry(
            gross_gain_allocated=gain,
            state=InvestorState(cumulative_paid_in=paid_in,
                                cumulative_distributed=prior),
            terms=_terms(ht),
        )
        results[ref] = res
        got = dict(roc=res.return_of_capital, pref=res.preferred_return,
                   catchup=res.gp_catchup, carry=res.carry_to_gp,
                   lp=res.net_to_lp)
        R.expect(ref, got == want,
                 f"golden case — {label} — matches the hand-computed result "
                 f"exactly",
                 detail=f"want={want} got={got}")
        R.expect(f"{ref}-bal", res.net_to_lp + res.carry_to_gp == gain,
                 f"golden case {ref} satisfies the balance constraint exactly",
                 detail=f"{res.net_to_lp} + {res.carry_to_gp} != {gain}")

        # calc_detail must carry the tier BOUNDARIES, not just the totals.
        tiers = res.calc_detail["cumulative_after"]
        names = [t["name"] for t in tiers]
        R.expect(f"{ref}-detail", names == [
            "RETURN_OF_CAPITAL", "PREFERRED_RETURN", "GP_CATCHUP",
            "RESIDUAL_SPLIT"],
            f"calc_detail for {ref} traces all four tiers in order",
            detail=str(names))
        chained = all(
            tiers[i]["balance_out"] == tiers[i + 1]["balance_in"]
            for i in range(len(tiers) - 1)
        )
        R.expect(f"{ref}-chain", chained
                 and tiers[0]["balance_in"] == str(prior + gain)
                 and tiers[-1]["balance_out"] == "0.00",
                 f"calc_detail for {ref} shows the running balance handed from "
                 f"each tier to the next, opening at the cumulative "
                 f"distribution and closing at zero",
                 detail=str([(t["balance_in"], t["balance_out"]) for t in tiers]))
        R.expect(f"{ref}-tile",
                 res.calc_detail["reconciliation"]["tiers_tile_ok"] is True,
                 f"the four tiers tile {ref}'s gain exactly — no gap, no "
                 f"double-count",
                 detail=res.calc_detail["reconciliation"]["tiers_tile"])

    # ── [3] HARD vs SOFT: one field apart, and the RIGHT way round.
    hard = next(g for g in GOLDEN if g[0] == "2c")
    soft = next(g for g in GOLDEN if g[0] == "2d")
    same_inputs = hard[3:6] == soft[3:6] and hard[2] != soft[2]
    R.expect("3a", same_inputs,
             "cases 3 and 4 share gain, paid-in and prior distributions and "
             "differ ONLY in hurdle_type",
             detail=f"hard={hard[2:6]} soft={soft[2:6]}")
    c_hard = results["2c"].carry_to_gp
    c_soft = results["2d"].carry_to_gp
    R.expect("3b", c_hard != c_soft,
             "HARD and SOFT produce genuinely different carry_to_gp on "
             "otherwise-identical fixtures — the distinction is real, not "
             "decorative", detail=f"HARD={c_hard} SOFT={c_soft}")
    R.expect("3c", c_hard < c_soft,
             "and the DIRECTION is right: a HARD hurdle pays the GP strictly "
             "LESS, because the preferred return stays the LP's",
             detail=f"HARD={c_hard} SOFT={c_soft}")
    R.expect("3d", results["2c"].gp_catchup == D("0.00")
             and results["2d"].gp_catchup > 0,
             "a HARD hurdle produces NO catch-up tier at all, a SOFT one does "
             "— that absence IS the hard hurdle",
             detail=f"HARD catchup={results['2c'].gp_catchup} "
                    f"SOFT catchup={results['2d'].gp_catchup}")
    profit = D("500000.00")
    R.expect("3e", c_soft == (profit * CARRY_PCT).quantize(D("0.01")),
             "the SOFT case leaves the GP holding exactly carry_pct of the "
             "WHOLE profit (100,000 of 500,000) — the identity that defines a "
             "completed catch-up", detail=f"{c_soft} vs {profit * CARRY_PCT}")
    R.expect("3f", results["2e"].carry_to_gp
             == (D("2050000.00") * CARRY_PCT).quantize(D("0.01")),
             "case 5's cumulative carry is exactly 20% of cumulative profit "
             "across TWO realizations — differencing cumulative states does "
             "not drift", detail=str(results["2e"].carry_to_gp))

    # ── Refusals: the terms that cannot be priced honestly.
    from services.spv_carry import (
        CarryTermsIncompleteError, CatchupUnreachableError, PercentScaleError,
        terms_from_resolved,
    )
    base = {"carry_pct": "0.20", "hurdle_pct": "0.08", "hurdle_type": "SOFT",
            "catchup_pct": "1.00", "carry_basis": "DEAL_BY_DEAL"}
    for ref, mutation, exc, why in (
        ("3g", {"carry_pct": "20"}, PercentScaleError,
         "a carry_pct of 20 (percent, not fraction) is REFUSED — the deployed "
         "table has no range CHECK, so this is the only thing standing between "
         "0.20 and paying the GP 20x the distribution"),
        ("3h", {"catchup_pct": None}, CarryTermsIncompleteError,
         "a SOFT hurdle with no catchup_pct is REFUSED rather than silently "
         "treated as HARD — the two pay the GP different money"),
        ("3i", {"catchup_pct": "0.20"}, CatchupUnreachableError,
         "catchup_pct <= carry_pct is REFUSED — that tier can never complete "
         "and would consume every remaining dollar"),
        ("3j", {"hurdle_type": None}, CarryTermsIncompleteError,
         "carry_pct with no hurdle_type is REFUSED, mirroring the deployed "
         "spv_fee_terms_carry_requires_hurdle_type CHECK"),
        ("3k", {"carry_basis": None}, CarryTermsIncompleteError,
         "a NULL carry_basis is REFUSED — there is no safe default between "
         "netting against one deal and against a whole fund"),
    ):
        payload = dict(base, **mutation)
        caught = None
        try:
            terms_from_resolved(payload)
        except Exception as e:  # noqa: BLE001
            caught = e
        R.expect(ref, isinstance(caught, exc), why,
                 detail=f"got {type(caught).__name__ if caught else 'no error'}")

    # A float is refused at the boundary rather than converted.
    from services.spv_carry import CarryInputError
    caught = None
    try:
        compute_carry(gross_gain_allocated=1500000.0,
                      state=InvestorState(cumulative_paid_in=D("1000000.00")),
                      terms=_terms("SOFT"))
    except Exception as e:  # noqa: BLE001
        caught = e
    R.expect("3l", isinstance(caught, CarryInputError),
             "a float amount is REFUSED at the boundary, not silently "
             "converted — Decimal(0.08) is not 0.08 and would move a hurdle",
             detail=f"got {type(caught).__name__ if caught else 'no error'}")


# ═══════════════════════════════════════════════════════════════════════════
# [4] End to end: post a real dist_gain, get a DRAFT carry run. No human.
# [5] and it does NOT go one step further.
# ═══════════════════════════════════════════════════════════════════════════
#
# Investor ONE — 60% of a 1,000,000 call and of a 1,500,000 realization.
#   paid_in 600,000 | G 900,000 | pref owed 48,000 | SOFT (base terms)
#   roc 600,000; after 300,000; pref 48,000; after 252,000
#   catch-up C = 0.20*48,000/0.80 = 12,000 (all to GP)
#   residual 240,000 -> GP 48,000 / LP 192,000
#   carry 60,000 | lp 840,000    (60,000 = 20% of the 300,000 profit)
#
# Investor TWO — 40%, moved to a HARD hurdle by a side letter.
#   paid_in 400,000 | G 600,000 | pref owed 32,000 | HARD
#   roc 400,000; after 200,000; pref 32,000; after 168,000
#   NO catch-up; residual 168,000 -> GP 33,600 / LP 134,400
#   carry 33,600 | lp 566,400
#
EXPECTED_LINES = {
    E_ONE: dict(gross=D("900000.00"), roc=D("600000.00"), pref=D("48000.00"),
                catchup=D("12000.00"), carry=D("60000.00"), lp=D("840000.00")),
    E_TWO: dict(gross=D("600000.00"), roc=D("400000.00"), pref=D("32000.00"),
                catchup=D("0.00"), carry=D("33600.00"), lp=D("566400.00")),
}


async def check_4_and_5(admin, pool) -> str | None:
    # The capital call first, posted through the real path — it is what makes
    # cumulative_paid_in real rather than a number this script asserted.
    await post_through_real_path(pool, TXN_CALL, U_ACTOR)
    paid = await admin.fetch(
        """SELECT entity_id, allocated_amount FROM public.spv_transaction_allocations
           WHERE transaction_id = $1::uuid ORDER BY entity_id""", TXN_CALL)
    paid_by = {str(r["entity_id"]): r["allocated_amount"] for r in paid}
    R.expect("4a", paid_by.get(E_ONE) == D("600000.00")
             and paid_by.get(E_TWO) == D("400000.00"),
             "the capital call posted and allocated 600,000 / 400,000 — the "
             "cumulative paid-in the waterfall reads is real posted money, not "
             "a fixture constant", detail=str(paid_by))

    runs_before = await admin.fetchval(
        "SELECT count(*) FROM public.spv_carry_runs WHERE spv_id = $1::uuid",
        SPV_MAIN)

    # THE REALIZATION. Nothing else in this function touches carry.
    await post_through_real_path(pool, TXN_GAIN, U_ACTOR)

    event = await admin.fetchrow(
        """SELECT id, event_type, payload FROM public.domain_events
           WHERE source_id = $1::uuid AND event_type = 'spv_realization'
             AND org_id = $2::uuid""", TXN_GAIN, ORG)
    R.expect("4b", event is not None,
             "posting a real dist_gain fired the spv_realization domain event "
             "through the event-emission sprint's own mechanism")
    if event is None:
        return None

    delivery = await admin.fetchrow(
        """SELECT status, workflow_run_id, error_detail
           FROM public.domain_event_deliveries
           WHERE domain_event_id = $1 AND workflow_trigger_id = $2::uuid""",
        event["id"], TRG_CARRY)
    R.expect("4c", delivery is not None and delivery["status"] == "DELIVERED",
             "the carry subscriber's trigger was resolved and its workflow run "
             "started — a real DELIVERED row, not a skip",
             detail=str(dict(delivery) if delivery else None))

    runs = await admin.fetch(
        """SELECT id, status, carry_basis, domain_event_id,
                  triggering_transaction_id, engine_version,
                  calculation_snapshot_hash, created_by, posted_at,
                  advisor_approved_by, advisor_approved_at,
                  compliance_approved_by, compliance_approved_at
           FROM public.spv_carry_runs WHERE spv_id = $1::uuid""", SPV_MAIN)
    R.expect("4d", len(runs) == runs_before + 1,
             "exactly ONE spv_carry_run appeared, with no human action of any "
             "kind between the post and the proposal",
             detail=f"before={runs_before} after={len(runs)}")
    if len(runs) != runs_before + 1:
        return None
    run = runs[0]
    run_id = str(run["id"])

    R.expect("4e", str(run["domain_event_id"]) == str(event["id"])
             and str(run["triggering_transaction_id"]) == TXN_GAIN,
             "the run names the domain event AND the transaction that produced "
             "it — the provenance survives",
             detail=f"event={run['domain_event_id']} txn={run['triggering_transaction_id']}")

    lines = await admin.fetch(
        """SELECT entity_id, spv_subscription_id, gross_gain_allocated,
                  return_of_capital, preferred_return, gp_catchup, carry_to_gp,
                  net_to_lp, calc_detail
           FROM public.spv_carry_run_lines WHERE spv_carry_run_id = $1
           ORDER BY entity_id""", run["id"])
    R.expect("4f", len(lines) == 2,
             "one line per allocated investor", detail=f"{len(lines)} lines")

    by_entity = {str(l["entity_id"]): l for l in lines}
    for eid, want in EXPECTED_LINES.items():
        row = by_entity.get(eid)
        got = None if row is None else dict(
            gross=row["gross_gain_allocated"], roc=row["return_of_capital"],
            pref=row["preferred_return"], catchup=row["gp_catchup"],
            carry=row["carry_to_gp"], lp=row["net_to_lp"])
        R.expect(f"4g:{eid[-4:]}", got == want,
                 f"investor {eid[-4:]}'s line matches the hand-computed "
                 f"waterfall to the cent", detail=f"want={want} got={got}")

    # The side letter really moved a number: two investors, two hurdle types.
    hts = {
        str(l["entity_id"]):
            as_json(l["calc_detail"])["terms"]["hurdle_type"]
        for l in lines
    }
    R.expect("4h", hts.get(E_ONE) == "SOFT" and hts.get(E_TWO) == "HARD",
             "terms were resolved PER INVESTOR through fee42's own resolver — "
             "the side letter moved investor two to a HARD hurdle and the "
             "engine paid a different number for it", detail=str(hts))
    sl = as_json(by_entity[E_TWO]["calc_detail"])["terms"]["side_letter_id"]
    R.expect("4h2", sl is not None,
             "and the line records WHICH side letter did it", detail=str(sl))

    # ── [5] It stopped. Every one of these is a separate way of not stopping.
    R.expect("5a", run["status"] == "DRAFT",
             "the triggered run is DRAFT — a workflow trigger creates a "
             "proposal, never a posted fact", detail=str(run["status"]))
    R.expect("5b", run["posted_at"] is None
             and run["advisor_approved_by"] is None
             and run["advisor_approved_at"] is None
             and run["compliance_approved_by"] is None
             and run["compliance_approved_at"] is None,
             "no approval column was stamped on the way past",
             detail=str(dict(run)))
    acts = await admin.fetchval(
        """SELECT count(*) FROM public.assistant_activities
           WHERE related_type = 'spv_carry_run' AND related_id = $1""", run["id"])
    R.expect("5c", acts == 0,
             "and no approval activity was opened or closed by the automatic "
             "path — the maker-checker ledger is untouched", detail=str(acts))

    wf = await admin.fetchrow(
        """SELECT r.id, r.status, r.context FROM public.workflow_runs r
           WHERE r.id = $1""", delivery["workflow_run_id"])
    R.expect("5d", wf is not None and wf["status"] == "running",
             "the workflow run itself is PAUSED, not completed — the BPMN's "
             "User Task is the gate, and the process genuinely stops at it",
             detail=str(wf["status"] if wf else None))
    active = await admin.fetchrow(
        """SELECT ws.step_key, rs.status, rs.proposed_by
           FROM public.workflow_run_steps rs
           JOIN public.workflow_steps ws ON ws.id = rs.workflow_step_id
           WHERE rs.workflow_run_id = $1 AND rs.status = 'active'""", wf["id"])
    R.expect("5e", active is not None and active["step_key"] == STEP_USER,
             f"the run is waiting at {STEP_USER}, its human review step",
             detail=str(dict(active) if active else None))
    svc = await admin.fetchrow(
        """SELECT ws.step_key, rs.status, rs.result
           FROM public.workflow_run_steps rs
           JOIN public.workflow_steps ws ON ws.id = rs.workflow_step_id
           WHERE rs.workflow_run_id = $1 AND ws.step_key = $2""",
        wf["id"], STEP_SERVICE)
    svc_result = as_json(svc["result"]) if svc else {}
    R.expect("5f", svc is not None and svc["status"] == "completed"
             and svc_result.get("invoked") is True
             and svc_result.get("handler_data", {}).get("status") == "DRAFT",
             "the Service Task really INVOKED the registered action (not merely "
             "resolved its key) and recorded that it produced a DRAFT",
             detail=str(svc_result))
    ctx = as_json(wf["context"])
    R.expect("5g", ctx.get("domain_event_id") == str(event["id"]),
             "the handler priced the event named in the run's own context — it "
             "does not scan for 'the most recent realization', which would race "
             "the moment two distributions post together",
             detail=str(ctx.get("domain_event_id")))

    return run_id


async def check_4c_permission(admin, pool) -> None:
    """The automatic path must not reach further than the member it runs as."""
    runs_before = await admin.fetchval(
        "SELECT count(*) FROM public.spv_carry_runs WHERE spv_id = $1::uuid",
        SPV_MAIN)
    try:
        await post_through_real_path(pool, TXN_GAIN_NOPERM, U_NOPERM)
    except Exception:  # noqa: BLE001 — the post itself must still succeed
        pass
    posted = await admin.fetchval(
        "SELECT status FROM public.spv_transactions WHERE id = $1::uuid",
        TXN_GAIN_NOPERM)
    R.expect("4i", posted == "posted",
             "a subscriber that cannot run does NOT undo the post that "
             "happened — publishing is an observation, not a precondition",
             detail=str(posted))
    event_id = await admin.fetchval(
        """SELECT id FROM public.domain_events WHERE source_id = $1::uuid
             AND event_type = 'spv_realization' AND org_id = $2::uuid""",
        TXN_GAIN_NOPERM, ORG)
    delivery = await admin.fetchrow(
        """SELECT status, error_detail FROM public.domain_event_deliveries
           WHERE domain_event_id = $1 AND workflow_trigger_id = $2::uuid""",
        event_id, TRG_CARRY)
    R.expect("4j", delivery is not None and delivery["status"] == "FAILED"
             and "manage_billing" in (delivery["error_detail"] or ""),
             "an actor without manage_billing produces a FAILED delivery "
             "naming the missing permission — loudly, not silently",
             detail=str(dict(delivery) if delivery else None))
    runs_after = await admin.fetchval(
        "SELECT count(*) FROM public.spv_carry_runs WHERE spv_id = $1::uuid",
        SPV_MAIN)
    R.expect("4k", runs_after == runs_before,
             "and NO carry run was written — an event trigger is not a route "
             "around a permission gate",
             detail=f"before={runs_before} after={runs_after}")


# ═══════════════════════════════════════════════════════════════════════════
# [6] The approval chain, including both layers of self-approval refusal
# ═══════════════════════════════════════════════════════════════════════════
async def check_6(admin, run_id: str) -> bool:
    from services import spv_carry_runs as SCR

    async def status():
        return await admin.fetchval(
            "SELECT status FROM public.spv_carry_runs WHERE id = $1::uuid", run_id)

    prev = await SCR.preview_run(admin, ORG, run_id)
    R.expect("6a", prev["status"] == "PREVIEW" and await status() == "PREVIEW",
             "DRAFT -> PREVIEW, by a human calling the service",
             detail=str(prev["status"]))

    # Approving with no proposal at all is a single-party approval in disguise.
    caught = None
    try:
        await SCR.approve(admin, ORG, run_id, gate="ADVISOR", approved_by=U_APPROVER)
    except Exception as e:  # noqa: BLE001
        caught = e
    R.expect("6b", isinstance(caught, SCR.CarryRunStateError)
             and await status() == "PREVIEW",
             "approving a gate nobody proposed is REFUSED, and the run did not "
             "move", detail=f"{type(caught).__name__ if caught else None}")

    act_id = await SCR.propose_approval(
        admin, ORG, run_id, gate="ADVISOR", proposed_by=U_ACTOR,
        rationale=f"{TAG} advisor")
    R.expect("6c", await status() == "PREVIEW",
             "proposing the ADVISOR gate does NOT advance the run — a proposal "
             "is a request for a second person")

    caught = None
    try:
        await SCR.approve(admin, ORG, run_id, gate="ADVISOR", approved_by=U_ACTOR)
    except Exception as e:  # noqa: BLE001
        caught = e
    R.expect("6d", isinstance(caught, SCR.MakerCheckerError)
             and await status() == "PREVIEW",
             "the SERVICE refuses self-approval and leaves the run untouched",
             detail=f"{type(caught).__name__ if caught else None}")

    # The database refuses it too, for a caller that skips the service.
    refused = False
    try:
        await admin.execute(
            """UPDATE public.assistant_activities
               SET status = 'approved', approved_by = proposed_by
               WHERE id = $1::uuid""", act_id)
    except asyncpg.CheckViolationError:
        refused = True
    R.expect("6e", refused,
             "and assistant_activities_maker_checker_chk refuses the same move "
             "issued as raw SQL — bypassing the service does not bypass the rule")

    res = await SCR.approve(admin, ORG, run_id, gate="ADVISOR",
                            approved_by=U_APPROVER)
    R.expect("6f", res["status"] == "ADVISOR_APPROVED"
             and await status() == "ADVISOR_APPROVED",
             "a DIFFERENT member closes the ADVISOR gate and the run advances",
             detail=str(res["status"]))

    caught = None
    try:
        await SCR.post_run(admin, ORG, run_id)
    except Exception as e:  # noqa: BLE001
        caught = e
    R.expect("6g", isinstance(caught, SCR.CarryRunStateError)
             and await status() == "ADVISOR_APPROVED",
             "posting with only ONE approval is REFUSED — compliance is not "
             "optional", detail=f"{type(caught).__name__ if caught else None}")

    comp_act = await SCR.propose_approval(
        admin, ORG, run_id, gate="COMPLIANCE", proposed_by=U_APPROVER,
        rationale=f"{TAG} compliance")
    res = await SCR.approve(admin, ORG, run_id, gate="COMPLIANCE",
                            approved_by=U_COMPLIANCE)
    R.expect("6h", res["status"] == "COMPLIANCE_APPROVED"
             and await status() == "COMPLIANCE_APPROVED",
             "a THIRD member closes the COMPLIANCE gate",
             detail=str(res["status"]))

    # ── The ledger is the authority, not the status mirror. Walk the activity
    #    back while leaving the run's status alone; posting must still refuse.
    await admin.execute(
        "UPDATE public.assistant_activities SET status = 'proposed' "
        "WHERE id = $1::uuid", comp_act)
    caught = None
    try:
        await SCR.post_run(admin, ORG, run_id)
    except Exception as e:  # noqa: BLE001
        caught = e
    R.expect("6i", isinstance(caught, SCR.CarryRunStateError)
             and await status() == "COMPLIANCE_APPROVED",
             "with the run still SAYING COMPLIANCE_APPROVED but the ledger "
             "walked back, posting is REFUSED — the decision rests on the "
             "activities ledger, not on the status column that mirrors it",
             detail=f"{type(caught).__name__ if caught else None}")
    await admin.execute(
        "UPDATE public.assistant_activities SET status = 'approved' "
        "WHERE id = $1::uuid", comp_act)

    posted = await SCR.post_run(admin, ORG, run_id)
    R.expect("6j", posted["status"] == "POSTED" and await status() == "POSTED",
             "and with both gates genuinely closed the run POSTS",
             detail=str(posted["status"]))
    R.expect("6k", posted["total_carry_to_gp"] == D("93600.00")
             and posted["total_net_to_lp"] == D("1406400.00"),
             "the posted totals are the sum of the two hand-computed lines "
             "(60,000 + 33,600 to the GP)",
             detail=str((posted["total_carry_to_gp"], posted["total_net_to_lp"])))
    R.expect("6l", "NOT POSTED" in posted["general_ledger"],
             "and posting the carry run does NOT post to the general ledger — "
             "that is fee43's open question #3, and it says so rather than "
             "quietly doing nothing")
    return posted["status"] == "POSTED"


# ═══════════════════════════════════════════════════════════════════════════
# [7] A POSTED run is immutable — proved from the database, not the service
# [8] and every line reconciles, re-read from the numeric columns
# ═══════════════════════════════════════════════════════════════════════════
async def check_7_and_8(admin, run_id: str) -> None:
    line_id = await admin.fetchval(
        "SELECT id FROM public.spv_carry_run_lines WHERE spv_carry_run_id = $1::uuid "
        "ORDER BY entity_id LIMIT 1", run_id)

    for ref, sql, args, what in (
        ("7a", "UPDATE public.spv_carry_runs SET carry_basis = 'WHOLE_FUND' "
               "WHERE id = $1::uuid", (run_id,), "UPDATE the run"),
        ("7b", "DELETE FROM public.spv_carry_runs WHERE id = $1::uuid",
         (run_id,), "DELETE the run"),
        ("7c", "UPDATE public.spv_carry_run_lines SET carry_to_gp = 0, "
               "net_to_lp = gross_gain_allocated WHERE id = $1", (line_id,),
         "UPDATE a line"),
        ("7d", "DELETE FROM public.spv_carry_run_lines WHERE id = $1",
         (line_id,), "DELETE a line"),
    ):
        refused = False
        detail = ""
        try:
            await admin.execute(sql, *args)
        except asyncpg.RaiseError as exc:
            refused = True
            detail = str(exc)[:120]
        R.expect(ref, refused,
                 f"a direct SQL attempt to {what} of a POSTED run is REFUSED by "
                 f"the database itself, not by the service layer",
                 detail=detail or "the statement succeeded")

    # ── The gap the triggers do NOT close, asked rather than assumed. Both
    #    triggers fire BEFORE DELETE OR UPDATE; neither fires on INSERT, so a
    #    NEW line appearing on a POSTED run is a different question from
    #    changing an existing one. Measured, not inferred from the trigger text.
    added = None
    try:
        added = await admin.fetchval(
            """INSERT INTO public.spv_carry_run_lines
                 (org_id, spv_carry_run_id, entity_id, gross_gain_allocated,
                  carry_to_gp, net_to_lp, calc_detail)
               VALUES ($1::uuid,$2::uuid,$3::uuid,1.00,0.20,0.80,'{}'::jsonb)
               RETURNING id""",
            ORG, run_id, E_ONE)
    except asyncpg.PostgresError:
        added = None
    if added is not None:
        # Remove it before it can affect [8] or the row counts. The line's own
        # trigger refuses a DELETE on a POSTED run, so this needs the same
        # disable/enable dance teardown uses.
        await _set_triggers(admin, False)
        try:
            await admin.execute(
                "DELETE FROM public.spv_carry_run_lines WHERE id = $1", added)
        finally:
            await _set_triggers(admin, True)
    R.find("F9",
           "The immutability triggers fire BEFORE DELETE OR UPDATE only. "
           f"INSERTing a BRAND NEW line onto a POSTED run is "
           f"{'ACCEPTED' if added is not None else 'refused'} — no trigger and "
           f"no constraint covers that path. So a POSTED run's existing lines "
           f"cannot be altered or removed (checks 7a-7e), but a line can still "
           f"be ADDED to one. Reported rather than patched: closing it is a "
           f"Part-1 schema change (a BEFORE INSERT trigger on "
           f"spv_carry_run_lines checking the parent run's status), which is "
           f"outside this sprint's applied SQL.")

    survived = await admin.fetchrow(
        "SELECT status, carry_basis FROM public.spv_carry_runs WHERE id = $1::uuid",
        run_id)
    n_lines = await admin.fetchval(
        "SELECT count(*) FROM public.spv_carry_run_lines "
        "WHERE spv_carry_run_id = $1::uuid", run_id)
    R.expect("7e", survived is not None and survived["status"] == "POSTED"
             and survived["carry_basis"] == "DEAL_BY_DEAL" and n_lines == 2,
             "and the run and both its lines are still there, unchanged — the "
             "refusals refused, they did not silently no-op",
             detail=f"{dict(survived) if survived else None} lines={n_lines}")

    # ── [8] read back from the numeric columns, not from memory.
    lines = await admin.fetch(
        """SELECT entity_id, gross_gain_allocated, return_of_capital,
                  preferred_return, gp_catchup, carry_to_gp, net_to_lp,
                  calc_detail
           FROM public.spv_carry_run_lines WHERE spv_carry_run_id = $1::uuid
           ORDER BY entity_id""", run_id)
    for line in lines:
        eid = str(line["entity_id"])
        g = line["gross_gain_allocated"]
        R.expect(f"8a:{eid[-4:]}",
                 line["net_to_lp"] + line["carry_to_gp"] == g,
                 f"line {eid[-4:]} reconciles to the cent: net_to_lp + "
                 f"carry_to_gp = gross_gain_allocated",
                 detail=f"{line['net_to_lp']} + {line['carry_to_gp']} != {g}")
        detail = as_json(line["calc_detail"])["this_realization"]
        tiles = (D(detail["return_of_capital"]) + D(detail["preferred_return"])
                 + D(detail["catchup_tier"]) + D(detail["residual_tier"]))
        R.expect(f"8b:{eid[-4:]}", tiles == g,
                 f"line {eid[-4:]}'s four tiers tile the gain exactly — "
                 f"return of capital + preferred return + catch-up tier + "
                 f"residual = {g}", detail=f"tiles to {tiles}")
        gp = D(detail["catchup_to_gp"]) + D(detail["residual_to_gp"])
        lp = (D(detail["return_of_capital"]) + D(detail["preferred_return"])
              + D(detail["catchup_to_lp"]) + D(detail["residual_to_lp"]))
        R.expect(f"8c:{eid[-4:]}",
                 gp == line["carry_to_gp"] and lp == line["net_to_lp"],
                 f"and every tier's GP/LP split adds back to the two stored "
                 f"totals — the audit trail is not decorative",
                 detail=f"gp={gp} vs {line['carry_to_gp']}, "
                        f"lp={lp} vs {line['net_to_lp']}")


# ═══════════════════════════════════════════════════════════════════════════
# [9] Cross-org isolation, on app_service
# ═══════════════════════════════════════════════════════════════════════════
async def check_9(admin, app, run_id: str) -> None:
    bypass = await admin.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_service'")
    if not R.expect("9a", bypass is False,
                    "app_service has rolbypassrls = FALSE — without this every "
                    "check below would pass vacuously", detail=str(bypass)):
        return

    await admin.execute(
        """INSERT INTO public.spv_carry_runs
             (id, org_id, spv_id, status, carry_basis, calculation_snapshot_hash,
              engine_version)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'DRAFT','DEAL_BY_DEAL','x','x')""",
        RUN_XORG, OTHER_ORG, SPV_OTHER)
    await admin.execute(
        """INSERT INTO public.spv_carry_run_lines
             (id, org_id, spv_carry_run_id, entity_id, gross_gain_allocated,
              carry_to_gp, net_to_lp, calc_detail)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,100.00,20.00,80.00,
                   '{}'::jsonb)""",
        LINE_XORG, OTHER_ORG, RUN_XORG, E_OTHER)
    # A DRAFT run in THIS org for the own-org write control in [9g].
    await admin.execute(
        """INSERT INTO public.spv_carry_runs
             (id, org_id, spv_id, status, carry_basis, calculation_snapshot_hash,
              engine_version)
           VALUES ($1::uuid,$2::uuid,$3::uuid,'DRAFT','DEAL_BY_DEAL','x','x')""",
        RUN_OWN_WRITE, ORG, SPV_MAIN)

    async with app.transaction():
        await app.execute(
            "SELECT set_config('app.current_org_id', $1, true)", ORG)
        await app.execute("SELECT set_config('app.is_super_admin', 'false', true)")

        seen_other = await app.fetchval(
            "SELECT count(*) FROM public.spv_carry_runs WHERE id = $1::uuid",
            RUN_XORG)
        R.expect("9b", seen_other == 0,
                 "under app_service scoped to this org, the OTHER org's "
                 "spv_carry_runs row is invisible", detail=str(seen_other))
        seen_own = await app.fetchval(
            "SELECT count(*) FROM public.spv_carry_runs WHERE id = $1::uuid",
            run_id)
        R.expect("9c", seen_own == 1,
                 "and its OWN org's run IS visible — the policy filters, it "
                 "does not simply deny", detail=str(seen_own))

        seen_other_line = await app.fetchval(
            "SELECT count(*) FROM public.spv_carry_run_lines WHERE id = $1::uuid",
            LINE_XORG)
        own_lines = await app.fetchval(
            "SELECT count(*) FROM public.spv_carry_run_lines "
            "WHERE spv_carry_run_id = $1::uuid", run_id)
        R.expect("9d", seen_other_line == 0 and own_lines == 2,
                 "the same holds on spv_carry_run_lines, in both directions",
                 detail=f"other={seen_other_line} own={own_lines}")

        # Each attempted write gets its own SAVEPOINT. A refused INSERT aborts
        # the enclosing transaction under asyncpg, and every later statement in
        # it would then fail with InFailedSQLTransactionError — which would
        # make the SECOND refusal unfalsifiable (it "fails" either way).
        refused = False
        try:
            async with app.transaction():
                await app.execute(
                    """INSERT INTO public.spv_carry_runs
                         (org_id, spv_id, status, carry_basis)
                       VALUES ($1::uuid,$2::uuid,'DRAFT','DEAL_BY_DEAL')""",
                    OTHER_ORG, SPV_OTHER)
        except asyncpg.InsufficientPrivilegeError:
            refused = True
        R.expect("9e", refused,
                 "app_service scoped to this org cannot INSERT a spv_carry_runs "
                 "row into another org — the policy's WITH CHECK is real")

        refused = False
        try:
            async with app.transaction():
                await app.execute(
                    """INSERT INTO public.spv_carry_run_lines
                         (org_id, spv_carry_run_id, entity_id, gross_gain_allocated,
                          carry_to_gp, net_to_lp, calc_detail)
                       VALUES ($1::uuid,$2::uuid,$3::uuid,10.00,2.00,8.00,
                               '{}'::jsonb)""",
                    OTHER_ORG, RUN_XORG, E_OTHER)
        except asyncpg.InsufficientPrivilegeError:
            refused = True
        R.expect("9f", refused,
                 "and cannot INSERT a line into another org either")

        # The positive control on the WRITE path: the same INSERT into its OWN
        # org succeeds, so "refused" above is the policy filtering by org and
        # not app_service simply lacking INSERT on the table.
        wrote = None
        try:
            async with app.transaction():
                wrote = await app.fetchval(
                    """INSERT INTO public.spv_carry_run_lines
                         (org_id, spv_carry_run_id, entity_id, gross_gain_allocated,
                          carry_to_gp, net_to_lp, calc_detail)
                       VALUES ($1::uuid,$2::uuid,$3::uuid,10.00,2.00,8.00,
                               '{}'::jsonb) RETURNING id""",
                    ORG, RUN_OWN_WRITE, E_ONE)
                raise _Rollback
        except _Rollback:
            pass
        except asyncpg.PostgresError:
            wrote = None
        R.expect("9g", wrote is not None,
                 "while the SAME insert into its OWN org is accepted — "
                 "app_service genuinely holds INSERT, so [9e]/[9f] measure the "
                 "org policy and not a missing grant")


# ═══════════════════════════════════════════════════════════════════════════
# Findings — the honest scope report Task 1 owes
# ═══════════════════════════════════════════════════════════════════════════
async def check_findings(admin) -> None:
    from services import spv_carry_runs as SCR

    probe = await SCR.capital_account_probe(admin, ORG, SPV_MAIN)
    view_rows = await admin.fetchval("SELECT count(*) FROM public.v_capital_accounts")
    dim_table = await admin.fetchval(
        "SELECT to_regclass('public.dim_member_series')")
    dim_nonnull = await admin.fetchval(
        "SELECT count(*) FROM public.journal_lines "
        "WHERE dim_member_series_id IS NOT NULL")
    R.expect("F2-probe", probe.usable is False and view_rows == 0
             and dim_table is None and dim_nonnull == 0,
             "the v_capital_accounts gap is re-measured live, not asserted",
             detail=f"view_rows={view_rows} dim_member_series={dim_table} "
                    f"non-null dim ids={dim_nonnull}")
    R.find("F2",
           f"v_capital_accounts CANNOT supply cumulative paid-in or "
           f"distributions-to-date. It groups by journal_lines."
           f"dim_member_series_id — a column with NO dim_member_series table "
           f"(none exists, nor any dim_* table), NO foreign key, and NULL in "
           f"{'all' if dim_nonnull == 0 else dim_nonnull} deployed rows — while "
           f"its own WHERE requires that column NOT NULL, so it returns "
           f"{view_rows} rows and structurally will until a GL posting path "
           f"populates the dimension (fee43, open question #3). Even populated, "
           f"there is no join path from that id to an SPV investor entity. It "
           f"also keys on journal_entries.vehicle_id and every deployed SPV has "
           f"vehicle_entity_id NULL. Cumulative figures are read instead from "
           f"the POSTED spv_transaction_allocations themselves — not a second "
           f"balance table, the actual transactions.")

    # ── WHOLE_FUND: refused where it would be wrong, allowed where it is not.
    caught = None
    try:
        await SCR.assert_scope_supported(admin, ORG, SPV_SERIES, "WHOLE_FUND")
    except Exception as e:  # noqa: BLE001
        caught = e
    R.expect("F4a", isinstance(caught, SCR.WholeFundScopeError),
             "WHOLE_FUND on a member_series vehicle with a master is REFUSED "
             "rather than computed per-vehicle and labelled whole-fund",
             detail=f"{type(caught).__name__ if caught else 'no error'}")
    scope = await SCR.assert_scope_supported(admin, ORG, SPV_MAIN, "WHOLE_FUND")
    R.expect("F4b", scope["scope"] == "spv" and scope["vehicle_type"] == "standalone_spv",
             "and WHOLE_FUND on a standalone SPV proceeds, recording that the "
             "two bases coincide at this grain", detail=str(scope["scope"]))
    inv_cols = await admin.fetchval(
        """SELECT count(*) FROM information_schema.columns
           WHERE table_schema='public' AND table_name='spv_transactions'
             AND (column_name ILIKE '%invest%' OR column_name ILIKE '%position%'
                  OR column_name ILIKE '%asset%' OR column_name ILIKE '%security%')""")
    R.expect("F4c", inv_cols == 0,
             "spv_transactions carries no investment/position reference — "
             "measured, not assumed", detail=str(inv_cols))
    R.find("F4",
           "WHOLE_FUND vs DEAL_BY_DEAL: an SPV has NO grain below itself "
           "(spv_transactions has zero investment/position/asset columns) and "
           "spvs.deal_id is NOT NULL, so on a standalone vehicle the two bases "
           "are the same arithmetic on the same rows and both are computed. "
           "They diverge only for investment_series/member_series vehicles "
           "under a master_entity_id, which would need a master-level rollup "
           "that has no deployed data — every deployed SPV is standalone_spv "
           "with master_entity_id NULL. That case is REFUSED "
           "(WholeFundScopeError), not approximated. This is the honest gap.")

    R.find("F3",
           "HARD vs SOFT hurdle is defined NOWHERE in this repository — the "
           "only prior mention is prose in spv_fee_terms' docstring ('an 8% "
           "soft hurdle and a 100% catch-up') which uses the terms without "
           "defining them, and the deployed CHECK admits HARD/SOFT/NONE with no "
           "semantics. Standard PE convention adopted and stated once in "
           "services/spv_carry.py: SOFT = the GP catches up on the WHOLE "
           "preferred return once the hurdle clears (a timing preference); "
           "HARD = no catch-up at all, the GP carries only above the hurdle (an "
           "economic preference). Checks 3b-3e prove the two are different, in "
           "the right direction, and that SOFT leaves the GP holding exactly "
           "carry_pct of total profit.")

    R.find("F5",
           "The preferred RETURN is an amount; hurdle_pct is a rate, and no "
           "deployed column, CHECK or document supplies the convention that "
           "turns one into the other. ONE is implemented and named in every "
           "calc_detail: preferred_return_owed = hurdle_pct x "
           "cumulative_paid_in, cumulative and NON-COMPOUNDING, not "
           "time-weighted. A time-weighted IRR-style accrual needs dated flows "
           "AND a compounding convention nobody has specified. compute_carry "
           "takes an explicit preferred_return_owed override so a later sprint "
           "replaces one argument, not the waterfall.")

    trg_check = await admin.fetchval(
        """SELECT count(*) FROM pg_constraint
           WHERE conrelid='public.workflow_triggers'::regclass AND contype='c'
             AND pg_get_constraintdef(oid) ILIKE '%trigger_type%'""")
    R.expect("F6-probe", trg_check == 0,
             "workflow_triggers.trigger_type carries no CHECK — measured",
             detail=str(trg_check))
    R.find("F6",
           "workflow_triggers.trigger_type is FREE TEXT — there is no CHECK "
           "constraint on it. 'event' is a convention held only in code "
           "(domain_events.EVENT_TRIGGER_TYPE). A typo in a trigger row "
           "subscribes to nothing and fails silently; nothing in the database "
           "would catch it.")

    R.find("F7",
           "workflow_engine._execute_service_task passed a handler only "
           "(pool, user_id, org_id) — NOT the run's context. An event-triggered "
           "Service Task therefore could not tell WHICH event started it, and "
           "every event looked identical to it. This sprint adds run_context "
           "and workflow_run_id to that call (both the start and the "
           "User-Task-resume paths). Additive: every existing handler already "
           "absorbs extra keyword arguments. Check 5g proves the carry handler "
           "prices the event named in its own run context rather than scanning "
           "for a recent one.")

    R.find("F8",
           "public.permissions is a CLOSED vocabulary of 28 names and contains "
           "no carry-specific key. Rather than reference a permission that does "
           "not exist — which would make the gate inert — the action reuses "
           "manage_billing, the key fee33/fee34 already settled on as the fee "
           "module's write authority (fee_schedules.WRITE_PERMISSION). Check 4j "
           "proves the gate is live by naming it in a real refusal.")


# ═══════════════════════════════════════════════════════════════════════════
async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    app_url, app_prov = await app_service_dsn()
    if admin_url is None:
        print(f"FATAL: cannot reach the database as postgres — {admin_prov}")
        return 2
    if app_url is None:
        print(f"FATAL: cannot reach the database as app_service — {app_prov}. "
              f"Every RLS check would pass vacuously on the postgres DSN")
        return 2
    print(f"admin       : {admin_prov}")
    print(f"app_service : {app_prov}\n")

    os.environ["DATABASE_URL"] = admin_url

    admin = await connect(admin_url)
    app = await connect(app_url)

    from services.assistant_actions import register_all
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context

    register_all()

    pre: dict[str, int] = {}
    pool = None
    tokens = None
    try:
        await teardown(admin)
        pre = await counts(admin)
        await build_fixtures(admin)

        pool = await get_pool()
        R.expect("0", type(pool).__name__ == "_RLSPool",
                 "the end-to-end runs on the deployed _RLSPool, not a raw pool "
                 "(a raw pool hides the savepoint-not-commit failure mode)",
                 detail=type(pool).__name__)
        tokens = set_rls_context(ORG, False)

        await check_1(admin)
        check_2_and_3()
        run_id = await check_4_and_5(admin, pool)
        await check_4c_permission(admin, pool)
        await check_findings(admin)
        if run_id is None:
            R.bad("6-9", "the end-to-end produced no carry run; the lifecycle, "
                          "immutability and isolation checks could not run")
        else:
            if await check_6(admin, run_id):
                await check_7_and_8(admin, run_id)
            else:
                R.bad("7-8", "the run never reached POSTED; immutability could "
                             "not be tested")
            await check_9(admin, app, run_id)
    except Exception:  # noqa: BLE001
        R.bad("driver", "the run aborted", traceback.format_exc())
    finally:
        if tokens is not None:
            reset_rls_context(tokens)
        try:
            await teardown(admin)
        except Exception:  # noqa: BLE001
            R.bad("teardown", "teardown failed", traceback.format_exc())
        # Teardown disables the immutability triggers to remove a POSTED
        # fixture row. Leaving them off would silently unprotect the table.
        enabled = await admin.fetch(
            "SELECT tgname, tgenabled FROM pg_trigger WHERE tgname = ANY($1::text[])",
            [t for _, t in TRIGGER_NAMES])
        # pg_trigger.tgenabled is "char" — asyncpg hands it back as b'O'.
        R.expect("11", len(enabled) == 2
                 and all(r["tgenabled"] in ("O", b"O") for r in enabled),
                 "both immutability triggers are ENABLED again after teardown",
                 detail=str([dict(r) for r in enabled]))
        post = await counts(admin)
        drift = {t: (pre.get(t), post.get(t)) for t in COUNTED
                 if pre.get(t) != post.get(t)}
        R.expect("12", not drift,
                 f"every one of the {len(COUNTED)} tables this script writes to "
                 f"is back at its pre-test row count", detail=str(drift))
        if pool is not None:
            await close_pool()
        await admin.close()
        await app.close()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
