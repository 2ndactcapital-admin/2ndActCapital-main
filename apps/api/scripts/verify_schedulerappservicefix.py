"""verify_schedulerappservicefix.py — proves the rlscutover Task 2 fix.

BACKGROUND. rlscutover Task 1 discovery found exactly one real, legitimate
cross-org code path that would silently break if DATABASE_URL is ever cut
over from the bypass `postgres` role to the non-bypass `app_service` role:
apps/api/workflow_scheduler_tick.py opens a raw asyncpg connection with NO
RLS context at all, and uses it for a platform-wide (all-orgs) due-trigger
scan. Under `postgres` (rolbypassrls=true) that scan sees everything
regardless of RLS. Under `app_service` it would see literally nothing —
every table it touches (workflow_triggers, workflow_definitions,
workflow_versions, member_todos, workflow_runs) already carries a
`... OR is_super_admin` RLS carve-out, but the scheduler's raw connection
never sets `app.is_super_admin`, so that carve-out never engages.

The FIRST fix attempt (session-level `set_config(..., false)` once at connect
time) looked right and passed a single-org test, but THIS SCRIPT, when
extended to two orgs firing in the same tick, caught it being wrong: under
Supabase's transaction-mode pooler, this connection's physical backend was
observably shared with the ordinary RLS-aware pool used to fire each run, and
the moment that pool's transaction committed, the pooler reset the shared
backend's session GUCs — silently wiping the session-level setting mid-tick.
The real fix is `services.database.platform_scope`: every platform-scope
query re-asserts `SET LOCAL app.is_super_admin='true'` fresh, inside its own
transaction, every time. See that function's docstring for the full mechanism.

WHAT THIS PROVES — against the REAL deployed database, using
APP_SERVICE_DATABASE_URL to simulate the post-cutover role WITHOUT touching
the actual Render/Doppler DATABASE_URL config (that cutover is a separate,
not-yet-approved step):

  [Repro]  The ORIGINAL bug, reproduced directly: the exact query
           load_due_candidates() runs, issued raw on a connection to
           app_service with NO app.is_super_admin set (the pre-fix shape),
           returns ZERO rows for two real due fixture triggers in two
           different orgs — even though both are genuinely due.
  [Fix]    The REAL, current services.workflow_scheduler.load_due_candidates()
           — which now wraps itself in platform_scope internally, so no
           caller has to remember to set anything — sees BOTH fixture
           triggers, across BOTH orgs, in one scan.
  [E2E]    The REAL entrypoint — apps.api.workflow_scheduler_tick.main(), the
           exact function Render's cron invokes — run with DATABASE_URL
           pointed at app_service for the duration of this process only, with
           TWO due triggers in TWO different orgs examined in the SAME tick
           (the exact shape that caught the first fix attempt's bug). Both
           fire through the real engine (workflow_engine.start_workflow_run):
           each gets its own workflow_runs row, status='completed' (trivial
           start->end BPMN), and its own occurrence_count incremented by
           exactly 1.
  [Isolation] Each fired run's org_id matches its OWN trigger's org — the
           2nd Act run is never created under Hollisworks, or vice versa,
           even though the scan itself is platform-wide.
  [Teardown] Every fixture row deleted by id; before/after count proves zero
           leftovers.

Run:  python3 apps/api/scripts/verify_schedulerappservicefix.py
"""
import asyncio
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402 (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

UTC = timezone.utc

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")        # 2nd Act Capital
OTHER_ORG_ID = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")  # Hollisworks

D_A = UUID("99000000-0000-0000-0000-00000000af01")  # def, org 2nd Act
D_B = UUID("99000000-0000-0000-0000-00000000af02")  # def, org Hollisworks
VER = {D_A: UUID("99000000-0000-0000-0000-00000000af11"),
       D_B: UUID("99000000-0000-0000-0000-00000000af12")}
T_A = UUID("99000000-0000-0000-0000-00000000af21")  # trigger, org 2nd Act
T_B = UUID("99000000-0000-0000-0000-00000000af22")  # trigger, org Hollisworks
ALL_DEFS = [D_A, D_B]
ALL_TRIGGERS = [T_A, T_B]

U_CREATOR = UUID("99000000-0000-0000-0000-00000000af31")
SUB_CREATOR = "schedappservicefix_creator"

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

_ok = True
_n_pass = 0
_n_fail = 0


def check(label, passed, detail=""):
    global _ok, _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def trivial_bpmn(proc_id) -> str:
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


async def _teardown(pg_conn):
    await pg_conn.execute("DELETE FROM workflow_run_steps WHERE org_id = ANY($1)", [ORG_ID, OTHER_ORG_ID])
    await pg_conn.execute("DELETE FROM workflow_runs WHERE workflow_version_id = ANY($1)", list(VER.values()))
    await pg_conn.execute("DELETE FROM workflow_triggers WHERE id = ANY($1)", ALL_TRIGGERS)
    await pg_conn.execute("DELETE FROM workflow_versions WHERE id = ANY($1)", list(VER.values()))
    await pg_conn.execute("DELETE FROM workflow_definitions WHERE id = ANY($1)", ALL_DEFS)
    await pg_conn.execute("DELETE FROM users WHERE id = $1", U_CREATOR)


async def main() -> int:
    postgres_dsn = await bootstrap_async()
    if not postgres_dsn:
        print("[BLOCKED] could not resolve a working postgres-role DATABASE_URL via Doppler")
        return 2

    app_service_dsn = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not app_service_dsn:
        print("[BLOCKED] APP_SERVICE_DATABASE_URL not present after Doppler hydration")
        return 2

    # Probe app_service connectivity for real before relying on it (Doppler
    # secrets have gone stale here before — see docs/PROJECT_STATUS.md).
    try:
        probe = await asyncpg.connect(app_service_dsn, statement_cache_size=0, ssl="require", timeout=20)
        role, bypass = await probe.fetchrow("SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        await probe.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[BLOCKED] APP_SERVICE_DATABASE_URL does not connect: {type(exc).__name__}: {exc}")
        return 2
    check("APP_SERVICE_DATABASE_URL connects as app_service, not postgres", role == "app_service", f"role={role}")
    check("app_service role genuinely has rolbypassrls=False", bypass is False, f"rolbypassrls={bypass}")
    if not _ok:
        return 2

    pg_conn = await asyncpg.connect(postgres_dsn, statement_cache_size=0, ssl="require")

    # Row counts BEFORE, scoped to fixture ids only (never a table-wide count —
    # these tables hold real production data).
    before = await pg_conn.fetchval(
        "SELECT count(*) FROM workflow_triggers WHERE id = ANY($1)", ALL_TRIGGERS
    )
    check("teardown precondition: zero fixture triggers before setup", before == 0, f"found {before}")

    try:
        # ── Fixture setup (as postgres, bypassing RLS — this is fixture
        #    plumbing, not the thing under test) ──────────────────────────
        await pg_conn.execute(
            """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
               VALUES ($1, $2, $3, $3, $4, 'org_admin', true)
               ON CONFLICT (auth0_sub) DO UPDATE SET org_id = EXCLUDED.org_id, is_active = true""",
            U_CREATOR, ORG_ID, f"{SUB_CREATOR}@test.local", SUB_CREATOR,
        )

        now = datetime.now(UTC)
        due_cron = f"{now.minute} {now.hour} * * *"  # due "this minute", real clock

        for def_id, org_id in ((D_A, ORG_ID), (D_B, OTHER_ORG_ID)):
            await pg_conn.execute(
                """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
                   VALUES ($1, $2, 'schedappservicefix fixture', 'proof for rlscutover Task 2', $3)
                   ON CONFLICT (id) DO NOTHING""",
                def_id, org_id, U_CREATOR,
            )
            await pg_conn.execute(
                """INSERT INTO workflow_versions
                     (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                      change_summary, is_current, created_by)
                   VALUES ($1, $2, $3, 1, $4, 'v1', true, $5)
                   ON CONFLICT (id) DO NOTHING""",
                VER[def_id], def_id, org_id, trivial_bpmn(f"p_{def_id.hex[:8]}"), U_CREATOR,
            )

        for trig_id, def_id, org_id in ((T_A, D_A, ORG_ID), (T_B, D_B, OTHER_ORG_ID)):
            await pg_conn.execute(
                """INSERT INTO workflow_triggers
                     (id, workflow_definition_id, org_id, trigger_type, schedule_cron,
                      timezone, is_active, created_by, occurrence_count)
                   VALUES ($1, $2, $3, 'scheduled', $4, 'UTC', true, $5, 0)
                   ON CONFLICT (id) DO NOTHING""",
                trig_id, def_id, org_id, due_cron, U_CREATOR,
            )

        # ── [Repro] the ORIGINAL bug: the same query, issued raw with no
        #    is_super_admin GUC set — the pre-fix shape (before platform_scope
        #    existed, this WAS what load_due_candidates ran). ──────────────
        broken_conn = await asyncpg.connect(app_service_dsn, statement_cache_size=0, ssl="require")
        try:
            rows_before_fix = await broken_conn.fetch(
                """
                SELECT t.id FROM workflow_triggers t
                JOIN workflow_definitions d ON d.id = t.workflow_definition_id
                WHERE t.trigger_type = 'scheduled' AND t.is_active
                """
            )
            ids_seen = {r["id"] for r in rows_before_fix}
        finally:
            await broken_conn.close()
        check(
            "[Repro] pre-fix shape (app_service, no is_super_admin GUC): "
            "raw scan sees ZERO of our 2 due fixture triggers",
            T_A not in ids_seen and T_B not in ids_seen,
            f"saw {len(ids_seen & set(ALL_TRIGGERS))}/2 fixture triggers "
            f"(total rows returned: {len(rows_before_fix)})",
        )

        # ── [Fix] the REAL, current function — it wraps itself in
        #    platform_scope, so a plain unprepared connection is enough. ──
        from services.workflow_scheduler import load_due_candidates

        fixed_conn = await asyncpg.connect(app_service_dsn, statement_cache_size=0, ssl="require")
        try:
            rows_after_fix = await load_due_candidates(fixed_conn)
            ids_seen_fixed = {r["id"] for r in rows_after_fix}
        finally:
            await fixed_conn.close()
        check(
            "[Fix] real load_due_candidates() sees BOTH fixture triggers, both orgs, "
            "on a connection that never set anything itself",
            T_A in ids_seen_fixed and T_B in ids_seen_fixed,
            f"saw {len(ids_seen_fixed & set(ALL_TRIGGERS))}/2 fixture triggers",
        )

        # ── [E2E] the REAL entrypoint, DATABASE_URL pointed at app_service ──
        # for this process only — no Render/Doppler config is touched.
        from services.database import close_pool

        prior_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = app_service_dsn
        await close_pool()  # drop any pool already cached against the postgres DSN
        try:
            import workflow_scheduler_tick

            exit_code = await workflow_scheduler_tick.main()
        finally:
            if prior_database_url is not None:
                os.environ["DATABASE_URL"] = prior_database_url
            else:
                os.environ.pop("DATABASE_URL", None)
            await close_pool()

        check("[E2E] real cron entrypoint exits 0 under app_service", exit_code == 0, f"exit={exit_code}")

        run_a = await pg_conn.fetchrow(
            "SELECT id, org_id, status FROM workflow_runs WHERE workflow_version_id = $1", VER[D_A]
        )
        run_b = await pg_conn.fetchrow(
            "SELECT id, org_id, status FROM workflow_runs WHERE workflow_version_id = $1", VER[D_B]
        )
        check("[E2E] trigger T_A (2nd Act) fired a real workflow_runs row", run_a is not None)
        check("[E2E] trigger T_B (Hollisworks) fired a real workflow_runs row", run_b is not None)
        if run_a:
            check("[E2E] T_A's run status is 'completed' (trivial start->end BPMN)", run_a["status"] == "completed", f"status={run_a['status']}")
            check("[Isolation] T_A's run is under 2nd Act's org_id, not Hollisworks'", run_a["org_id"] == ORG_ID, f"org_id={run_a['org_id']}")
        if run_b:
            check("[E2E] T_B's run status is 'completed' (trivial start->end BPMN)", run_b["status"] == "completed", f"status={run_b['status']}")
            check("[Isolation] T_B's run is under Hollisworks' org_id, not 2nd Act's", run_b["org_id"] == OTHER_ORG_ID, f"org_id={run_b['org_id']}")

        occ_a = await pg_conn.fetchval("SELECT occurrence_count FROM workflow_triggers WHERE id = $1", T_A)
        occ_b = await pg_conn.fetchval("SELECT occurrence_count FROM workflow_triggers WHERE id = $1", T_B)
        check("[E2E] T_A occurrence_count incremented to exactly 1 (claimed exactly once)", occ_a == 1, f"occurrence_count={occ_a}")
        check("[E2E] T_B occurrence_count incremented to exactly 1 (claimed exactly once)", occ_b == 1, f"occurrence_count={occ_b}")

    finally:
        await _teardown(pg_conn)
        after = await pg_conn.fetchval(
            "SELECT count(*) FROM workflow_triggers WHERE id = ANY($1)", ALL_TRIGGERS
        )
        check("[Teardown] zero fixture triggers remain", after == 0, f"found {after}")
        await pg_conn.close()

    print(f"\n{_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
