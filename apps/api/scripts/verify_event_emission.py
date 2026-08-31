"""Sprint event-emission verification — domain_events publish side.

Pass/fail only, no prompts. Run:

    python3 scripts/verify_event_emission.py

Every table this script writes to is counted before the first insert and again
after the last delete; a difference of even one row fails the run, reported
AFTER the tests so a teardown bug never masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **It runs the end-to-end through the REAL ``_RLSPool``, not a raw pool.**
  ``workflow_engine._independent_acquire`` exists because ``_RLSPool.acquire()``
  opens an OUTER transaction, which silently demotes every inner "commit" to a
  savepoint — and its own docstring records that *every existing verify script
  built a RAW pool, which is exactly why this never showed up*. A raw pool here
  would prove the publish path works in a shape that is not deployed. So this
  script calls ``services.database.get_pool()`` — the actual object the API
  uses — and drives the actual ``post_transaction``.

* **Nothing is published by calling ``publish_event`` directly in the happy
  path.** Every firing in [2]–[9] goes through ``spv_allocation.post_transaction``,
  the single writer of ``status='posted'`` in the codebase. A mocked call would
  prove the publisher works and prove nothing about whether it is wired in.

* **[2] proves both directions on fixtures that differ in ONE field.** The
  ``dist_gain`` and ``dist_roc`` transactions share an SPV, a date, an amount,
  a subscription set and an allocation basis; only ``transaction_type_id``
  differs. A filter that fires on every distribution passes the positive half
  and fails here, and a filter that fires on nothing fails the positive half.

* **[3] proves the POSTED gate, not merely that nobody called the emitter.**
  The draft transaction is handed to ``emit_spv_realization`` *directly* while
  it is still unposted; the guard must refuse it. Asserting "no event exists
  for a transaction nobody posted" would pass for an emitter with no status
  check at all.

* **[5] flips the flag and re-runs.** "The inactive trigger did not fire" is
  also true of a trigger that is simply broken. So the same trigger, on an
  otherwise-identical posted ``dist_gain``, MUST fire once ``is_active`` is
  set true — and must not while it is false. Both halves, same trigger row.

* **[8] puts the broken trigger FIRST.** Its ``created_at`` is backdated so the
  fan-out's ``ORDER BY created_at, id`` reaches it before the healthy one. A
  ``FAILED`` delivery that aborted the publish would then take the valid
  trigger down with it, and the check would catch it. Ordering the healthy one
  first would make "the others still fired" unfalsifiable.

* **[1] proves the dedupe index BEHAVIOURALLY and in both directions.** A
  second identical insert must be REFUSED, and an insert differing only in
  ``source_id`` must be ACCEPTED — otherwise "it refuses duplicates" would pass
  for an index that refuses everything. The ``status`` CHECK is likewise
  exercised with a real rejected INSERT rather than read out of ``pg_constraint``.

* **[10] compares the payload against SQL, not against itself.** The
  per-investor amounts in the event are matched to the ``spv_transaction_allocations``
  rows read independently, and their sum must equal the vehicle amount EXACTLY
  as ``Decimal`` — the fixture amount is 100,000.01 against a 60/40 split
  precisely so a float round-trip would show up.

* **[11] runs on app_service, whose ``rolbypassrls`` is asserted False FIRST.**
  Without that assertion every isolation check below it proves nothing.

* Teardown is by fixture id, in FK order, never a TRUNCATE. The
  ``workflow_runs`` rows this sprint's code creates have ids this script never
  chooses, so they are reaped by ``workflow_version_id``, which by construction
  only matches this script's own fixture versions.
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

TAG = "evtemitverify"

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

# ── fixture ids ─────────────────────────────────────────────────────────────
U_ACTOR = "99000000-0000-0000-0000-0000e7e70001"
USERS = [U_ACTOR]

DEAL_MAIN = "99000000-0000-0000-0000-0000e7e70011"
DEAL_OTHER = "99000000-0000-0000-0000-0000e7e70012"
DEALS = [DEAL_MAIN, DEAL_OTHER]

E_ONE = "99000000-0000-0000-0000-0000e7e70021"
E_TWO = "99000000-0000-0000-0000-0000e7e70022"
E_OTHER = "99000000-0000-0000-0000-0000e7e70023"
ENTITIES = [E_ONE, E_TWO, E_OTHER]

SPV_MAIN = "99000000-0000-0000-0000-0000e7e70031"
SPV_OTHER = "99000000-0000-0000-0000-0000e7e70032"
SPVS = [SPV_MAIN, SPV_OTHER]

SUB_ONE = "99000000-0000-0000-0000-0000e7e70041"
SUB_TWO = "99000000-0000-0000-0000-0000e7e70042"

TXN_GAIN = "99000000-0000-0000-0000-0000e7e70051"     # [2+][4][6][9][10]
TXN_ROC = "99000000-0000-0000-0000-0000e7e70052"      # [2-]
TXN_GAIN_B = "99000000-0000-0000-0000-0000e7e70053"   # [5+]
TXN_DRAFT = "99000000-0000-0000-0000-0000e7e70054"    # [3]
TXN_FANOUT = "99000000-0000-0000-0000-0000e7e70055"   # [7]
TXN_FAIL = "99000000-0000-0000-0000-0000e7e70056"     # [8]
TXNS = [TXN_GAIN, TXN_ROC, TXN_GAIN_B, TXN_DRAFT, TXN_FANOUT, TXN_FAIL]

DEF_OK = "99000000-0000-0000-0000-0000e7e70061"
DEF_OK2 = "99000000-0000-0000-0000-0000e7e70062"
DEF_NOVER = "99000000-0000-0000-0000-0000e7e70063"
DEF_OTHER = "99000000-0000-0000-0000-0000e7e70064"
DEFS = [DEF_OK, DEF_OK2, DEF_NOVER, DEF_OTHER]

VER_OK = "99000000-0000-0000-0000-0000e7e70071"
VER_OK2 = "99000000-0000-0000-0000-0000e7e70072"
VER_NOVER = "99000000-0000-0000-0000-0000e7e70073"    # is_current = FALSE
VER_OTHER = "99000000-0000-0000-0000-0000e7e70074"
VERSIONS = [VER_OK, VER_OK2, VER_NOVER, VER_OTHER]

TRG_MAIN = "99000000-0000-0000-0000-0000e7e70081"
TRG_SECOND = "99000000-0000-0000-0000-0000e7e70082"
TRG_INACTIVE = "99000000-0000-0000-0000-0000e7e70083"
TRG_OTHEREVT = "99000000-0000-0000-0000-0000e7e70084"
TRG_NOVERSION = "99000000-0000-0000-0000-0000e7e70085"
TRG_OTHERORG = "99000000-0000-0000-0000-0000e7e70086"
TRIGGERS = [TRG_MAIN, TRG_SECOND, TRG_INACTIVE, TRG_OTHEREVT,
            TRG_NOVERSION, TRG_OTHERORG]

# [1]/[11] write domain rows directly; ids fixed so teardown is exact.
EVT_DEDUPE_A = "99000000-0000-0000-0000-0000e7e70091"
EVT_DEDUPE_B = "99000000-0000-0000-0000-0000e7e70092"
EVT_XORG = "99000000-0000-0000-0000-0000e7e70093"
DIRECT_EVENTS = [EVT_DEDUPE_A, EVT_DEDUPE_B, EVT_XORG]
DLV_XORG = "99000000-0000-0000-0000-0000e7e70094"

OTHER_EVENT_TYPE = f"{TAG}_unrelated_event"

# 60/40 of 100,000.01 does not divide cleanly — see the [10] note above.
TXN_AMOUNT = D("100000.01")

COUNTED = (
    "public.domain_event_deliveries",
    "public.domain_events",
    "public.member_todos",
    "public.workflow_run_steps",
    "public.workflow_runs",
    "public.workflow_triggers",
    "public.workflow_steps",
    "public.workflow_versions",
    "public.workflow_definitions",
    "public.spv_transaction_allocations",
    "public.spv_transactions",
    "public.spv_subscriptions",
    "public.spvs",
    "public.entities",
    "public.deals",
    "public.audit_log",
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
        print(f"event-emission: {counts.get('PASS', 0)}/{total} PASS" + "".join(
            f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS"))
        print("=" * 78)


R = Results()


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


async def scoped(conn, org_id: str):
    """Raise the org GUC on ``conn`` for the rest of its transaction."""
    await conn.execute("SELECT set_config('app.current_org_id', $1, true)", org_id)
    await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")


def trivial_bpmn(proc_id: str) -> str:
    """Start -> End. Runs to 'completed' with no side effects and no steps."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" id="D_{proc_id}" '
        'targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="{proc_id}" isExecutable="true">'
        '<bpmn:startEvent id="p_start"><bpmn:outgoing>p1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:endEvent id="p_end"><bpmn:incoming>p1</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="p1" sourceRef="p_start" targetRef="p_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
async def teardown(conn) -> None:
    """By fixture id, in FK order. Never a TRUNCATE."""
    # Deliveries first: they FK both domain_events and workflow_runs.
    await conn.execute(
        """DELETE FROM public.domain_event_deliveries
           WHERE workflow_trigger_id = ANY($1::uuid[])
              OR domain_event_id = ANY($2::uuid[])""",
        TRIGGERS, DIRECT_EVENTS)
    await conn.execute(
        """DELETE FROM public.domain_events
           WHERE id = ANY($1::uuid[])
              OR source_id = ANY($2::uuid[])
              OR event_type = $3""",
        DIRECT_EVENTS, TXNS, OTHER_EVENT_TYPE)
    # A held run would have alerted the fixture user; none is expected, but a
    # leftover todo would drift the count silently.
    await conn.execute(
        "DELETE FROM public.member_todos WHERE user_id = ANY($1::uuid[])", USERS)
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
    await conn.execute(
        "DELETE FROM public.spv_subscriptions WHERE spv_id = ANY($1::uuid[])", SPVS)
    await conn.execute(
        "DELETE FROM public.spv_status_history WHERE spv_id = ANY($1::uuid[])", SPVS)
    await conn.execute("DELETE FROM public.spvs WHERE id = ANY($1::uuid[])", SPVS)
    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])", ENTITIES)
    await conn.execute("DELETE FROM public.deals WHERE id = ANY($1::uuid[])", DEALS)
    # post_transaction writes audit rows whose ids this script never sees.
    await conn.execute(
        """DELETE FROM public.audit_log
           WHERE resource_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])""",
        TXNS, USERS)
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def type_id(conn, code: str):
    return await conn.fetchval(
        "SELECT id FROM public.transaction_types WHERE code = $1", code)


async def build_fixtures(conn) -> None:
    await conn.execute(
        """INSERT INTO public.users (id, org_id, email, full_name, auth0_sub, is_active)
           VALUES ($1::uuid, $2::uuid, $3, $4, $5, true)""",
        U_ACTOR, ORG, f"actor@{TAG}.local", f"{TAG} actor", f"auth0|{TAG}-actor")

    for did, org, nm in ((DEAL_MAIN, ORG, "main"), (DEAL_OTHER, OTHER_ORG, "other")):
        await conn.execute(
            "INSERT INTO public.deals (id, org_id, name) VALUES ($1::uuid,$2::uuid,$3)",
            did, org, f"{TAG} deal {nm}")

    for eid, org, nm in ((E_ONE, ORG, "investor one"), (E_TWO, ORG, "investor two"),
                         (E_OTHER, OTHER_ORG, "otherorg investor")):
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
               VALUES ($1::uuid,$2::uuid,'individual',$3)""",
            eid, org, f"{TAG} {nm}")

    for sid, org, did, cls, nm in (
        (SPV_MAIN, ORG, DEAL_MAIN, "A", "main"),
        (SPV_OTHER, OTHER_ORG, DEAL_OTHER, "A", "otherorg"),
    ):
        await conn.execute(
            """INSERT INTO public.spvs
                 (id, org_id, deal_id, name, spv_status, class_label, carry_pct)
               VALUES ($1::uuid,$2::uuid,$3::uuid,$4,'closed',$5,20)""",
            sid, org, did, f"{TAG} spv {nm}", cls)

    # TWO investors, deliberately: [10] must show per-investor amounts, which a
    # single-subscriber fixture could not distinguish from the vehicle total.
    for sub, eid, pct in ((SUB_ONE, E_ONE, 60), (SUB_TWO, E_TWO, 40)):
        await conn.execute(
            """INSERT INTO public.spv_subscriptions
                 (id, org_id, spv_id, entity_id, commitment_amount, funded_amount,
                  ownership_pct, subscription_status)
               VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,1000000,1000000,
                       $5::numeric,'funded')""",
            sub, ORG, SPV_MAIN, eid, pct)

    gain_id = await type_id(conn, "dist_gain")
    roc_id = await type_id(conn, "dist_roc")

    # Identical in every field but transaction_type_id — see the [2] note.
    for tid, ttype in (
        (TXN_GAIN, gain_id), (TXN_GAIN_B, gain_id), (TXN_DRAFT, gain_id),
        (TXN_FANOUT, gain_id), (TXN_FAIL, gain_id), (TXN_ROC, roc_id),
    ):
        await conn.execute(
            """INSERT INTO public.spv_transactions
                 (id, org_id, spv_id, txn_type, txn_date, amount, status,
                  allocation_basis, transaction_type_id, currency_code, description)
               VALUES ($1::uuid,$2::uuid,$3::uuid,'distribution','2026-06-30',
                       $4::numeric,'draft','ownership_pct',$5::uuid,'USD',$6)""",
            tid, ORG, SPV_MAIN, TXN_AMOUNT, ttype, f"{TAG} txn")

    for defid, org, verid, is_current, nm in (
        (DEF_OK, ORG, VER_OK, True, "primary"),
        (DEF_OK2, ORG, VER_OK2, True, "second subscriber"),
        (DEF_NOVER, ORG, VER_NOVER, False, "no current version"),
        (DEF_OTHER, OTHER_ORG, VER_OTHER, True, "otherorg"),
    ):
        await conn.execute(
            """INSERT INTO public.workflow_definitions
                 (id, org_id, name, description, created_by)
               VALUES ($1::uuid,$2::uuid,$3,$4,NULL)""",
            defid, org, f"{TAG} {nm}", f"{TAG} fixture")
        await conn.execute(
            """INSERT INTO public.workflow_versions
                 (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                  change_summary, is_current)
               VALUES ($1::uuid,$2::uuid,$3::uuid,1,$4,'v1',$5)""",
            verid, defid, org, trivial_bpmn(f"p_{nm.replace(' ', '_')}"), is_current)

    for trg, org, defid, evt, active, backdate in (
        (TRG_MAIN, ORG, DEF_OK, "spv_realization", True, False),
        (TRG_SECOND, ORG, DEF_OK2, "spv_realization", False, False),
        (TRG_INACTIVE, ORG, DEF_OK, "spv_realization", False, False),
        (TRG_OTHEREVT, ORG, DEF_OK, OTHER_EVENT_TYPE, True, False),
        # Backdated so the fan-out's ORDER BY reaches the BROKEN trigger first
        # — see the [8] note.
        (TRG_NOVERSION, ORG, DEF_NOVER, "spv_realization", False, True),
        (TRG_OTHERORG, OTHER_ORG, DEF_OTHER, "spv_realization", True, False),
    ):
        await conn.execute(
            """INSERT INTO public.workflow_triggers
                 (id, workflow_definition_id, org_id, trigger_type, event_type,
                  is_active, created_by, created_at)
               VALUES ($1::uuid,$2::uuid,$3::uuid,'event',$4,$5,NULL,
                       CASE WHEN $6 THEN now() - interval '1 hour' ELSE now() END)""",
            trg, defid, org, evt, active, backdate)


async def set_trigger_active(conn, trigger_id, active: bool) -> None:
    await conn.execute(
        "UPDATE public.workflow_triggers SET is_active = $2 WHERE id = $1::uuid",
        trigger_id, active)


# ═══════════════════════════════════════════════════════════════════════════
# Readers
# ═══════════════════════════════════════════════════════════════════════════
async def events_for(conn, source_id, event_type: str = "spv_realization") -> list:
    """This org's events of ONE type for one source.

    Scoped by event_type AND org_id deliberately: [1] writes unrelated-type rows
    against the same source ids to exercise the dedupe index, and [11] writes an
    OTHER_ORG row against one of them. Without both filters those fixtures would
    leak into the counts here and read as duplicate publishes.
    """
    return await conn.fetch(
        """SELECT id, org_id, event_type, source_type, source_id, payload,
                  occurred_at, created_by
           FROM public.domain_events
           WHERE source_id = $1::uuid AND event_type = $2 AND org_id = $3::uuid
           ORDER BY occurred_at""",
        source_id, event_type, ORG)


async def deliveries_for(conn, event_id) -> list:
    return await conn.fetch(
        """SELECT id, org_id, workflow_trigger_id, workflow_run_id, status,
                  error_detail
           FROM public.domain_event_deliveries WHERE domain_event_id = $1::uuid
           ORDER BY created_at, id""",
        event_id)


async def run_row(conn, run_id):
    return await conn.fetchrow(
        """SELECT id, workflow_version_id, org_id, status, context, started_by
           FROM public.workflow_runs WHERE id = $1::uuid""",
        run_id)


def as_json(value) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return json.loads(value)


async def post_through_real_path(pool, txn_id) -> None:
    """Allocate then post via the REAL service functions — never a hand-written
    UPDATE. The emitter hangs off ``post_transaction``; a fixture that set
    ``status='posted'`` in SQL would bypass exactly what is under test."""
    from services.spv_allocation import allocate_transaction, post_transaction

    await allocate_transaction(pool, str(txn_id), U_ACTOR)
    await post_transaction(pool, str(txn_id), U_ACTOR)


# ═══════════════════════════════════════════════════════════════════════════
# [1] Deployment, RLS shape, and a dedupe index that is genuinely enforced
# ═══════════════════════════════════════════════════════════════════════════
async def check_1(admin) -> None:
    for t in ("domain_events", "domain_event_deliveries"):
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

    idx = await admin.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
        "AND indexname='domain_events_source_dedupe_uq'")
    R.expect("1d", idx is not None and "UNIQUE" in idx
             and all(c in idx for c in
                     ("org_id", "event_type", "source_type", "source_id")),
             "the dedupe index is UNIQUE on (org_id, event_type, source_type, source_id)",
             detail=str(idx))

    # ── Behavioural: the index must actually refuse a repeat, and must NOT
    #    refuse a genuinely different source. Both halves, same shape.
    await admin.execute(
        """INSERT INTO public.domain_events
             (id, org_id, event_type, source_type, source_id, payload)
           VALUES ($1::uuid,$2::uuid,$3,'spv_transaction',$4::uuid,'{}'::jsonb)""",
        EVT_DEDUPE_A, ORG, OTHER_EVENT_TYPE, TXN_GAIN)
    refused = False
    try:
        await admin.execute(
            """INSERT INTO public.domain_events
                 (org_id, event_type, source_type, source_id, payload)
               VALUES ($1::uuid,$2,'spv_transaction',$3::uuid,'{}'::jsonb)""",
            ORG, OTHER_EVENT_TYPE, TXN_GAIN)
    except asyncpg.UniqueViolationError:
        refused = True
    R.expect("1e", refused,
             "a second identical (org, event_type, source_type, source_id) row is "
             "refused by the database, not merely by application code")
    accepted = True
    try:
        await admin.execute(
            """INSERT INTO public.domain_events
                 (id, org_id, event_type, source_type, source_id, payload)
               VALUES ($1::uuid,$2::uuid,$3,'spv_transaction',$4::uuid,'{}'::jsonb)""",
            EVT_DEDUPE_B, ORG, OTHER_EVENT_TYPE, TXN_ROC)
    except Exception as exc:  # noqa: BLE001
        accepted = False
        detail = str(exc)
    R.expect("1f", accepted,
             "the same index ACCEPTS a row differing only in source_id — it "
             "refuses duplicates, not everything",
             detail="" if accepted else detail)

    # ── The delivery status CHECK, exercised rather than read.
    bad_status = False
    try:
        await admin.execute(
            """INSERT INTO public.domain_event_deliveries
                 (org_id, domain_event_id, workflow_trigger_id, status)
               VALUES ($1::uuid,$2::uuid,$3::uuid,'PROBABLY')""",
            ORG, EVT_DEDUPE_A, TRG_MAIN)
    except asyncpg.CheckViolationError:
        bad_status = True
    R.expect("1g", bad_status,
             "domain_event_deliveries.status refuses a value outside "
             "{DELIVERED, FAILED}")

    # ── The realization predicate this sprint implements, checked against the
    #    live catalogue rather than against the prompt.
    from services.spv_events import (
        REALIZATION_CATEGORY,
        REALIZATION_PERFORMANCE_IMPACT,
    )
    codes = [r["code"] for r in await admin.fetch(
        """SELECT code FROM public.transaction_types
           WHERE category = $1 AND performance_impact = $2 AND is_active
           ORDER BY code""",
        REALIZATION_CATEGORY, REALIZATION_PERFORMANCE_IMPACT)]
    R.expect("1h", codes == ["dist_gain"],
             "the flag-driven realization predicate (category='distribution' AND "
             "performance_impact='gain') resolves to exactly ['dist_gain'] on the "
             "live catalogue",
             detail=str(codes))
    R.find("1h-note",
           "the emitter matches on transaction_types' own accounting flags, not on "
           "code='dist_gain'. Both halves are load-bearing: 'sell' also carries "
           "performance_impact='gain' and is excluded only by category='transfer'. "
           "dist_roc/dist_recallable/dist_stock carry performance_impact="
           "'distribution' and dist_income carries 'income' — a return of capital "
           "genuinely has no gain to carry against, confirming the sprint premise "
           "from the data rather than restating it.")


# ═══════════════════════════════════════════════════════════════════════════
# [2] dist_gain fires; dist_roc does not — one field apart
# [4] a real run with the right context + a DELIVERED row naming it
# [5-] the inactive trigger stays silent
# [6] a different event_type is not matched
# [10] per-investor amounts
# ═══════════════════════════════════════════════════════════════════════════
async def check_2_4_6_10(admin, pool) -> None:
    await post_through_real_path(pool, TXN_GAIN)

    gain_events = await events_for(admin, TXN_GAIN)
    if not R.expect("2a", len(gain_events) == 1,
                    "posting a dist_gain SPV transaction wrote exactly one "
                    "domain_events row", detail=f"{len(gain_events)} rows"):
        return
    evt = gain_events[0]
    R.expect("2b", evt["event_type"] == "spv_realization"
             and evt["source_type"] == "spv_transaction"
             and str(evt["source_id"]) == TXN_GAIN
             and str(evt["org_id"]) == ORG,
             "the event identifies itself as spv_realization on this "
             "spv_transaction, in this org",
             detail=str(dict(evt)))

    # ── [2−] the negative, on a fixture differing only in transaction_type_id.
    await post_through_real_path(pool, TXN_ROC)
    roc_status = await admin.fetchval(
        "SELECT status FROM public.spv_transactions WHERE id=$1::uuid", TXN_ROC)
    roc_events = [e for e in await events_for(admin, TXN_ROC)
                  if e["event_type"] == "spv_realization"]
    R.expect("2c", roc_status == "posted",
             "the dist_roc control really was posted — the negative below is "
             "about the type, not about a transaction that never posted",
             detail=str(roc_status))
    R.expect("2d", len(roc_events) == 0,
             "posting an otherwise-identical dist_roc transaction fired NO "
             "spv_realization event",
             detail=str([dict(e) for e in roc_events]))

    # ── [10] the payload carries the real per-investor split.
    payload = as_json(evt["payload"])
    alloc_rows = await admin.fetch(
        """SELECT entity_id, allocated_amount FROM public.spv_transaction_allocations
           WHERE transaction_id = $1::uuid AND status='active' ORDER BY entity_id""",
        TXN_GAIN)
    from_sql = {str(a["entity_id"]): D(str(a["allocated_amount"])) for a in alloc_rows}
    from_evt = {a["entity_id"]: D(a["allocated_amount"])
                for a in payload.get("allocations", [])}
    R.expect("10a", len(from_sql) == 2,
             "the fixture really has more than one investor allocated",
             detail=str(from_sql))
    R.expect("10b", from_evt == from_sql and len(from_evt) == 2,
             "the event payload's per-investor amounts match the real "
             "spv_transaction_allocations rows exactly, investor by investor",
             detail=f"event={from_evt} sql={from_sql}")
    R.expect("10c", sum(from_evt.values(), D("0")) == TXN_AMOUNT
             and D(payload["amount"]) == TXN_AMOUNT,
             f"the per-investor amounts sum to the vehicle total {TXN_AMOUNT} "
             f"EXACTLY as Decimal (a float round-trip of a 60/40 split of this "
             f"amount would not)",
             detail=f"sum={sum(from_evt.values(), D('0'))} amount={payload.get('amount')}")
    R.expect("10d", payload.get("spv_id") == SPV_MAIN
             and payload.get("spv_transaction_id") == TXN_GAIN
             and payload.get("class_label") == "A"
             and payload.get("transaction_type_code") == "dist_gain",
             "the payload carries spv_id, spv_transaction_id, class_label and the "
             "resolved transaction type code",
             detail=str({k: payload.get(k) for k in
                         ("spv_id", "spv_transaction_id", "class_label",
                          "transaction_type_code")}))

    # ── [4] the delivery and the run it names.
    dels = await deliveries_for(admin, evt["id"])
    R.expect("4a", len(dels) == 1
             and str(dels[0]["workflow_trigger_id"]) == TRG_MAIN
             and dels[0]["status"] == "DELIVERED"
             and dels[0]["workflow_run_id"] is not None,
             "the one active matching trigger produced exactly one DELIVERED "
             "delivery naming a real workflow_run",
             detail=str([dict(d) for d in dels]))
    if len(dels) != 1 or dels[0]["workflow_run_id"] is None:
        return
    run = await run_row(admin, dels[0]["workflow_run_id"])
    R.expect("4b", run is not None
             and str(run["workflow_version_id"]) == VER_OK
             and str(run["org_id"]) == ORG,
             "the workflow_runs row genuinely exists, on the definition's CURRENT "
             "version and in this org",
             detail=str(dict(run) if run else None))
    ctx = as_json(run["context"]) if run else {}
    R.expect("4c", ctx.get("event_type") == "spv_realization"
             and ctx.get("source_type") == "spv_transaction"
             and ctx.get("source_id") == TXN_GAIN
             and ctx.get("occurred_at") == evt["occurred_at"].isoformat()
             and ctx.get("domain_event_id") == str(evt["id"])
             and ctx.get("trigger_id") == TRG_MAIN,
             "the run context carries event_type, source_type, source_id, "
             "occurred_at (matching the event row) and the originating trigger",
             detail=str({k: ctx.get(k) for k in
                         ("event_type", "source_type", "source_id", "occurred_at",
                          "domain_event_id", "trigger_id")}))
    ctx_allocs = {a["entity_id"]: D(a["allocated_amount"])
                  for a in ctx.get("payload", {}).get("allocations", [])}
    R.expect("4d", ctx_allocs == from_sql,
             "the run context carries the FULL payload, per-investor amounts "
             "included — the subscriber does not have to go and re-read it",
             detail=str(ctx_allocs))

    # ── [5−] and [6], on this same real publish.
    inactive_dels = [d for d in dels if str(d["workflow_trigger_id"]) == TRG_INACTIVE]
    R.expect("5a", not inactive_dels,
             "the INACTIVE trigger matching the same event_type produced no "
             "delivery and no run",
             detail=str([dict(d) for d in inactive_dels]))
    other_dels = [d for d in dels if str(d["workflow_trigger_id"]) == TRG_OTHEREVT]
    other_events = await admin.fetchval(
        "SELECT count(*) FROM public.domain_events WHERE event_type = $1 "
        "AND source_id = ANY($2::uuid[])", OTHER_EVENT_TYPE, [TXN_GAIN_B])
    R.expect("6a", not other_dels and other_events == 0,
             "the ACTIVE trigger registered for a DIFFERENT event_type was not "
             "matched by this event",
             detail=str([dict(d) for d in other_dels]))
    xorg_dels = [d for d in dels if str(d["workflow_trigger_id"]) == TRG_OTHERORG]
    R.expect("6b", not xorg_dels,
             "the matching trigger in ANOTHER ORG was not matched either — "
             "fan-out is org-scoped")


# ═══════════════════════════════════════════════════════════════════════════
# [3] a draft fires nothing; posting it afterward does
# ═══════════════════════════════════════════════════════════════════════════
async def check_3(admin, pool) -> None:
    from services.spv_allocation import allocate_transaction, post_transaction
    from services.spv_events import emit_spv_realization

    await allocate_transaction(pool, TXN_DRAFT, U_ACTOR)
    status = await admin.fetchval(
        "SELECT status FROM public.spv_transactions WHERE id=$1::uuid", TXN_DRAFT)
    R.expect("3a", status == "allocated",
             "the unposted dist_gain fixture is genuinely allocated-but-unposted",
             detail=str(status))

    # Handed to the emitter DIRECTLY while unposted — this is the guard under
    # test, not "nobody called it".
    result = await emit_spv_realization(pool, TXN_DRAFT, actor_user_id=U_ACTOR)
    pre_events = await events_for(admin, TXN_DRAFT)
    R.expect("3b", result is None and len(pre_events) == 0,
             "calling the emitter directly on an unposted dist_gain publishes "
             "NOTHING — the posted-only gate is real, not incidental",
             detail=f"result={result} events={len(pre_events)}")

    await post_transaction(pool, TXN_DRAFT, U_ACTOR)
    post_events = await events_for(admin, TXN_DRAFT)
    R.expect("3c", len(post_events) == 1
             and post_events[0]["event_type"] == "spv_realization",
             "posting the SAME transaction afterward fires the event — the gate "
             "delays it, it does not lose it",
             detail=f"{len(post_events)} events")


# ═══════════════════════════════════════════════════════════════════════════
# [5+] flip is_active and the same trigger fires
# ═══════════════════════════════════════════════════════════════════════════
async def check_5(admin, pool) -> None:
    await set_trigger_active(admin, TRG_INACTIVE, True)
    try:
        await post_through_real_path(pool, TXN_GAIN_B)
        events = await events_for(admin, TXN_GAIN_B)
        if not R.expect("5b", len(events) == 1, "the second dist_gain published",
                        detail=f"{len(events)} events"):
            return
        dels = await deliveries_for(admin, events[0]["id"])
        fired = {str(d["workflow_trigger_id"]) for d in dels
                 if d["status"] == "DELIVERED"}
        R.expect("5c", TRG_INACTIVE in fired,
                 "the SAME trigger row fires once is_active is set true — proving "
                 "[5a] was the flag being read, not a trigger that never worked",
                 detail=str(sorted(fired)))
        R.expect("5d", fired == {TRG_MAIN, TRG_INACTIVE},
                 "both now-active matching triggers fired, and nothing else did",
                 detail=str(sorted(fired)))
    finally:
        await set_trigger_active(admin, TRG_INACTIVE, False)


# ═══════════════════════════════════════════════════════════════════════════
# [7] one event fans out to two subscribers
# ═══════════════════════════════════════════════════════════════════════════
async def check_7(admin, pool) -> None:
    await set_trigger_active(admin, TRG_SECOND, True)
    try:
        await post_through_real_path(pool, TXN_FANOUT)
        events = await events_for(admin, TXN_FANOUT)
        if not R.expect("7a", len(events) == 1,
                        "the fan-out event was recorded ONCE, not once per "
                        "subscriber", detail=f"{len(events)} events"):
            return
        dels = await deliveries_for(admin, events[0]["id"])
        by_trigger = {str(d["workflow_trigger_id"]): d for d in dels}
        R.expect("7b", set(by_trigger) == {TRG_MAIN, TRG_SECOND},
                 "both matching triggers got their own domain_event_deliveries row",
                 detail=str(sorted(by_trigger)))
        runs = [d["workflow_run_id"] for d in dels if d["workflow_run_id"]]
        R.expect("7c", len(runs) == 2 and len(set(runs)) == 2,
                 "each subscriber got its OWN workflow_runs row — two distinct runs "
                 "from one event", detail=str(runs))
        versions = []
        for r in runs:
            versions.append(str((await run_row(admin, r))["workflow_version_id"]))
        versions.sort()
        R.expect("7d", versions == sorted([VER_OK, VER_OK2]),
                 "each run is on its own trigger's definition's current version, "
                 "not both on the same one", detail=str(versions))
    finally:
        await set_trigger_active(admin, TRG_SECOND, False)


# ═══════════════════════════════════════════════════════════════════════════
# [8] an unresolvable definition FAILS loudly and does not take the others down
# ═══════════════════════════════════════════════════════════════════════════
async def check_8(admin, pool) -> None:
    await set_trigger_active(admin, TRG_NOVERSION, True)
    try:
        order = [str(r["id"]) for r in await admin.fetch(
            """SELECT id FROM public.workflow_triggers
               WHERE org_id=$1::uuid AND trigger_type='event'
                 AND event_type='spv_realization' AND is_active
               ORDER BY created_at, id""", ORG)]
        R.expect("8a", order and order[0] == TRG_NOVERSION,
                 "the BROKEN trigger is processed FIRST, so 'the valid one still "
                 "fired' is a real claim about ordering, not luck",
                 detail=str(order))

        await post_through_real_path(pool, TXN_FAIL)
        events = await events_for(admin, TXN_FAIL)
        if not R.expect("8b", len(events) == 1,
                        "the event was still recorded despite a broken subscriber",
                        detail=f"{len(events)} events"):
            return
        dels = {str(d["workflow_trigger_id"]): d
                for d in await deliveries_for(admin, events[0]["id"])}
        broken = dels.get(TRG_NOVERSION)
        R.expect("8c", broken is not None and broken["status"] == "FAILED"
                 and broken["workflow_run_id"] is None
                 and broken["error_detail"]
                 and DEF_NOVER in broken["error_detail"],
                 "the trigger whose definition has no current version produced a "
                 "FAILED delivery naming that definition — not a silent skip",
                 detail=str(dict(broken) if broken else None))
        healthy = dels.get(TRG_MAIN)
        R.expect("8d", healthy is not None and healthy["status"] == "DELIVERED"
                 and healthy["workflow_run_id"] is not None,
                 "the VALID trigger, processed after the broken one, still got a "
                 "DELIVERED delivery and a real run — the failure did not abort "
                 "the publish",
                 detail=str(dict(healthy) if healthy else None))
        R.expect("8e", await run_row(admin, healthy["workflow_run_id"]) is not None
                 if healthy and healthy["workflow_run_id"] else False,
                 "that run row is genuinely readable back on an independent "
                 "connection — it committed, it did not vanish with a savepoint")
    finally:
        await set_trigger_active(admin, TRG_NOVERSION, False)


# ═══════════════════════════════════════════════════════════════════════════
# [9] re-publishing the identical event is a clean no-op
# ═══════════════════════════════════════════════════════════════════════════
async def check_9(admin, pool) -> None:
    from services.spv_events import emit_spv_realization

    before_events = len(await events_for(admin, TXN_GAIN))
    evt_id = (await events_for(admin, TXN_GAIN))[0]["id"]
    before_dels = len(await deliveries_for(admin, evt_id))
    before_runs = await admin.fetchval(
        "SELECT count(*) FROM public.workflow_runs WHERE workflow_version_id = ANY($1::uuid[])",
        VERSIONS)

    result = await emit_spv_realization(pool, TXN_GAIN, actor_user_id=U_ACTOR)

    after_events = len(await events_for(admin, TXN_GAIN))
    after_dels = len(await deliveries_for(admin, evt_id))
    after_runs = await admin.fetchval(
        "SELECT count(*) FROM public.workflow_runs WHERE workflow_version_id = ANY($1::uuid[])",
        VERSIONS)

    R.expect("9a", result is not None and result.get("deduped") is True
             and result.get("event_id") == str(evt_id),
             "re-publishing reports deduped=True and adopts the EXISTING event id "
             "— it does not quietly create a parallel event",
             detail=str({k: result.get(k) for k in ("deduped", "event_id")}
                        if result else None))
    R.expect("9b", after_events == before_events == 1,
             "no duplicate domain_events row was created",
             detail=f"{before_events} -> {after_events}")
    R.expect("9c", after_dels == before_dels,
             "no duplicate domain_event_deliveries row was created for a trigger "
             "that already fired", detail=f"{before_dels} -> {after_dels}")
    R.expect("9d", after_runs == before_runs,
             "no second workflow_runs row was started — a subscriber that already "
             "ran does not run again", detail=f"{before_runs} -> {after_runs}")
    R.expect("9e", result.get("already_delivered") == [TRG_MAIN]
             if result else False,
             "the skip is reported explicitly, naming the trigger, rather than "
             "looking like 'no subscribers'",
             detail=str(result.get("already_delivered") if result else None))


# ═══════════════════════════════════════════════════════════════════════════
# [11] cross-org isolation, on app_service
# ═══════════════════════════════════════════════════════════════════════════
async def check_11(admin, app) -> None:
    bypass = await app.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    who = await app.fetchval("SELECT current_user")
    if not R.expect("11a", bypass is False,
                    f"the isolation connection is '{who}' with rolbypassrls=False "
                    f"— without this every check below proves nothing",
                    detail=f"rolbypassrls={bypass}"):
        return

    # A real OTHER_ORG event + delivery, written by admin.
    await admin.execute(
        """INSERT INTO public.domain_events
             (id, org_id, event_type, source_type, source_id, payload)
           VALUES ($1::uuid,$2::uuid,'spv_realization','spv_transaction',
                   $3::uuid,'{}'::jsonb)""",
        EVT_XORG, OTHER_ORG, TXN_GAIN)
    await admin.execute(
        """INSERT INTO public.domain_event_deliveries
             (id, org_id, domain_event_id, workflow_trigger_id, status)
           VALUES ($1::uuid,$2::uuid,$3::uuid,$4::uuid,'DELIVERED')""",
        DLV_XORG, OTHER_ORG, EVT_XORG, TRG_OTHERORG)

    async with app.transaction():
        await scoped(app, ORG)
        seen_evt = await app.fetchval(
            "SELECT count(*) FROM public.domain_events WHERE id=$1::uuid", EVT_XORG)
        seen_dlv = await app.fetchval(
            "SELECT count(*) FROM public.domain_event_deliveries WHERE id=$1::uuid",
            DLV_XORG)
        own_evt = await app.fetchval(
            "SELECT count(*) FROM public.domain_events WHERE source_id=$1::uuid "
            "AND org_id=$2::uuid", TXN_GAIN, ORG)
    R.expect("11b", seen_evt == 0 and seen_dlv == 0,
             "under app_service scoped to this org, the OTHER org's domain_events "
             "and domain_event_deliveries rows are invisible",
             detail=f"events={seen_evt} deliveries={seen_dlv}")
    R.expect("11c", own_evt >= 1,
             "the same connection DOES see this org's own rows — the isolation is "
             "a filter, not a blanket denial", detail=f"own={own_evt}")

    async with app.transaction():
        await scoped(app, OTHER_ORG)
        other_evt = await app.fetchval(
            "SELECT count(*) FROM public.domain_events WHERE id=$1::uuid", EVT_XORG)
        other_dlv = await app.fetchval(
            "SELECT count(*) FROM public.domain_event_deliveries WHERE id=$1::uuid",
            DLV_XORG)
        leaked = await app.fetchval(
            "SELECT count(*) FROM public.domain_events WHERE source_id=$1::uuid "
            "AND org_id=$2::uuid", TXN_GAIN, ORG)
    R.expect("11d", other_evt == 1 and other_dlv == 1,
             "scoped to the OTHER org, that org's own rows become visible — "
             "proving [11b] was org scoping and not an unreadable table",
             detail=f"events={other_evt} deliveries={other_dlv}")
    R.expect("11e", leaked == 0,
             "and this org's rows are correspondingly invisible from there — "
             "isolation holds in both directions", detail=f"leaked={leaked}")

    # Writing into another org must also be refused, not merely reading.
    refused = False
    try:
        async with app.transaction():
            await scoped(app, ORG)
            await app.execute(
                """INSERT INTO public.domain_events
                     (org_id, event_type, source_type, source_id, payload)
                   VALUES ($1::uuid,'spv_realization','spv_transaction',
                           $2::uuid,'{}'::jsonb)""",
                OTHER_ORG, TXN_ROC)
    except asyncpg.InsufficientPrivilegeError:
        refused = True
    R.expect("11f", refused,
             "app_service scoped to this org cannot INSERT a domain_events row "
             "into another org — the policy's WITH CHECK is real")


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

    # The application's own pool reads DATABASE_URL. Pin it to the DSN that was
    # actually probed working, so get_pool() cannot pick up a stale copy.
    os.environ["DATABASE_URL"] = admin_url

    admin = await connect(admin_url)
    app = await connect(app_url)

    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context

    pre: dict[str, int] = {}
    pool = None
    tokens = None
    try:
        await teardown(admin)
        pre = await counts(admin)
        await build_fixtures(admin)

        # THE REAL POOL — see the header note on _independent_acquire.
        pool = await get_pool()
        R.expect("0", type(pool).__name__ == "_RLSPool",
                 "the end-to-end runs on the deployed _RLSPool, not a raw pool "
                 "(a raw pool hides the savepoint-not-commit failure mode)",
                 detail=type(pool).__name__)
        tokens = set_rls_context(ORG, False)

        await check_1(admin)
        await check_2_4_6_10(admin, pool)
        await check_3(admin, pool)
        await check_5(admin, pool)
        await check_7(admin, pool)
        await check_8(admin, pool)
        await check_9(admin, pool)
        await check_11(admin, app)
    except Exception:  # noqa: BLE001
        R.bad("driver", "the run aborted", traceback.format_exc())
    finally:
        if tokens is not None:
            reset_rls_context(tokens)
        try:
            await teardown(admin)
        except Exception:  # noqa: BLE001
            R.bad("teardown", "teardown failed", traceback.format_exc())
        post = await counts(admin)
        drift = {t: (pre.get(t), post.get(t)) for t in COUNTED
                 if pre.get(t) != post.get(t)}
        R.expect("12", not drift,
                 f"every one of the {len(COUNTED)} tables this script writes to is "
                 f"back at its pre-test row count", detail=str(drift))
        if pool is not None:
            await close_pool()
        await admin.close()
        await app.close()

    R.summary()
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
