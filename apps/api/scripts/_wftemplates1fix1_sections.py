"""wftemplates1 fix 1 — one-off driver for the two sections that failed, against the live DB (fixtures only).

    python3 scripts/_wftemplates1fix1_sections.py browse      # verify_wftemplates1: seed → validator → platform → browse
    python3 scripts/_wftemplates1fix1_sections.py tiergating  # verify_tiergating: seed, fixture-role check, the
                                                              # downgrade run + the run-console checks (b, c)

Each mode tears down at start and end and checks zero fixture rows remain. It never runs the npm build, the
node tests or the nested regression. It uses the verify's OWN check expressions, which are copied verbatim for
tiergating. Its exit code is non-zero on any FAIL.
"""
from __future__ import annotations

import os

os.environ["AUTHZ_TEST_MODE"] = "1"

import asyncio  # noqa: E402
import pathlib  # noqa: E402
import sys  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
for sp in sorted((HERE.parent / "venv" / "lib").glob("python*/site-packages")):
    sys.path.insert(0, str(sp))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))


async def browse() -> int:
    import verify_wftemplates1 as V

    dsn, how = await V.admin_dsn()
    if not dsn:
        print("no DSN", how)
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await V.connect(dsn)
    V.install_stubs()
    S, run = V.S, V.run
    try:
        await run("pre-teardown", V.teardown_retrying)
        V.check(await V.leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        if not await V.guard_prefix(conn):
            S["no_teardown"] = True
            return 1
        await run("seed literals", V.y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", V.seed, conn)
        if S.get("seeded"):
            await run("validator", V.y_validator, conn)
            await run("platform", V.y_platform, conn)
            if S.get("platform_ok"):
                await run("browse", V.y_browse, conn)
                V.check(S.get("browse_ok") is True, "y_browse ran to its last line (browse_ok set)")
        else:
            V.check(False, "seed completed")
    finally:
        V.section("[Y] Teardown")
        await V.close_app_pool()
        if not S.get("no_teardown"):
            log = await V.teardown_retrying()
            print(f"[INFO] teardown removed: {log}")
            left = await V.leftovers(conn)
            V.check(left == 0, "zero fixture rows remain", f"{left}")
        await V.close_conn(conn)
    print(V.total_line())
    return 0 if V._n_fail == 0 else 1


async def tiergating() -> int:
    import verify_tiergating as T
    import asyncpg
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context

    check = T.check
    dsn = await T.bootstrap_async()
    if not dsn:
        check(False, "a working DATABASE_URL", "none resolved")
        return 1
    T.register_all()
    T.register_fake_actions()
    T.CALL_COUNTS.clear()
    original = T.REGISTRY.sync_catalog

    async def _hermetic(pool, org_id):
        return None

    T.REGISTRY.sync_catalog = _hermetic
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        await T.teardown(conn)
        print("\n── Fixtures ──")
        fixtures = await T.seed(conn)
        await T.check_fixture_roles(conn)
        check(fixtures["viewer_profile"] is not None, "the ORG_A viewer profile (view_workflow_runs) was created",
              f"viewer_profile={fixtures['viewer_profile']}")
        await close_pool()
        pool = await get_pool()
        print("\n── [Y] Tier-1 verb marked Tier 3 in the diagram STILL suspends ──")
        tokens = set_rls_context(T.ORG_A, False)
        try:
            run = await T.start_workflow_run(pool, T.VER_DOWNGRADE, T.ORG_A, {"note": "downgrade"}, T.U_MAKER)
        finally:
            reset_rls_context(tokens)
        check(run["status"] == "awaiting_approval" and run["suspended_at"] == "Svc_1",
              "the run suspends at the Service Task instead of completing", f"run={run}")
        downgrade_step = await T._read_rls_row(
            conn, """SELECT rs.id, rs.status FROM workflow_run_steps rs WHERE rs.workflow_run_id = $1""",
            run["run_id"])
        print("\n── [Y] GET /admin/workflow-runs/{id} shows the EFFECTIVE tier ──")
        await close_pool()
        loop = asyncio.get_running_loop()
        status, body = await loop.run_in_executor(None, T._api_get_run, T.U_MAKER, T.ORG_A, run["run_id"])
        await close_pool()
        step_out = None
        if status == 200:
            step_out = next((s for s in body.get("steps", []) if s["id"] == str(downgrade_step["id"])), None)
        check(
            status == 200 and step_out is not None
            and step_out.get("effective_tier") == 1 and step_out.get("autonomy_tier") == 3,
            "the API reports effective_tier=1 (what the engine actually enforced) alongside autonomy_tier=3",
            f"status={status}, step_out={step_out}",
        )
        check(
            status == 200 and body.get("permissions", {}).get("can_review") is True,
            "permissions.can_review is true for the maker, who holds review_agent_proposals via the ROLE system",
            f"permissions={body.get('permissions') if status == 200 else None}",
        )
        # The negative case, on the SAME data (teardown follows, so the removed grant needs no restore): without the internal role, the profile key is capped (the client
        # rule) — prove the fix is the role, not a side effect, and that the rule itself still holds.
        from services import authz as AZ
        async with conn.transaction():
            await T._super(conn)
            await conn.execute("DELETE FROM user_roles WHERE user_id = $1 AND role_id IN (SELECT id FROM roles WHERE "
                               "org_id = $2 AND name = $3)", T.U_MAKER, T.ORG_A, T.ROLE_INTERNAL_A)
            pr = await AZ.load_principal(conn, T.U_MAKER)
            check("view_workflow_runs" not in pr.permissions,
                  "negative: with the internal role removed, the client rule still caps the maker's Profile key "
                  "(view_workflow_runs absent) — the BUGFIX2 rule is intact", f"{sorted(pr.permissions)}")
    finally:
        await T.teardown(conn)
        async with conn.transaction():
            await T._super(conn)
            left = await conn.fetchval(
                """SELECT (SELECT count(*) FROM user_roles WHERE user_id = ANY($1::uuid[]))
                        + (SELECT count(*) FROM roles WHERE org_id = ANY($2::uuid[]))
                        + (SELECT count(*) FROM profiles WHERE org_id = ANY($2::uuid[]) AND is_seed = false)
                        + (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_definitions WHERE org_id = ANY($2::uuid[]))
                        + (SELECT count(*) FROM agent_proposals WHERE org_id = ANY($2::uuid[]))
                        + (SELECT count(*) FROM organizations WHERE id = ANY($2::uuid[]))""",
                T.ALL_FIXTURE_USERS, T.ALL_FIXTURE_ORGS)
        check(left == 0, "TEARDOWN: zero fixture rows (users, user_roles, roles incl. 'advisor', profiles, "
                         "definitions, proposals, orgs)", f"{left}")
        T.REGISTRY.sync_catalog = original
        await close_pool()
        await conn.close()
    print(f"\nTOTAL: {T._n_pass} PASS, {T._n_fail} FAIL")
    return 0 if T._n_fail == 0 else 1


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"browse": browse, "tiergating": tiergating}.get(mode)
    if fn is None:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(fn()))
