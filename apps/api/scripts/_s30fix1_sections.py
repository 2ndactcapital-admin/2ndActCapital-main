"""s30 fix 1 — one-off driver for verify_s30's failing sections, against the live DB (fixtures only).

    python3 scripts/_s30fix1_sections.py repro      # seed, draft a role-grant policy, approve it with the app's
                                                    # exceptions RAISED (prints the traceback of the 500)
    python3 scripts/_s30fix1_sections.py task1      # Group A: the 1a inventory section (no DB writes)
    python3 scripts/_s30fix1_sections.py fixtures   # seed → every fixture-scoped section (floor .. isolation),
                                                    # in the verify's own order, with its own check expressions

Each DB mode tears down at start and end, checks zero fixture rows remain, and checks the real fingerprints (real
role grants included: count + md5 of user_roles outside the fixture orgs) are unchanged. Never runs the npm build,
node tests, route --check scripts or the nested regression. Exit code non-zero on any FAIL.
"""
from __future__ import annotations

import os

os.environ["AUTHZ_TEST_MODE"] = "1"

import asyncio  # noqa: E402
import pathlib  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
for sp in sorted((HERE.parent / "venv" / "lib").glob("python*/site-packages")):
    sys.path.insert(0, str(sp))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))


async def _start(V):
    dsn, how = await V.admin_dsn()
    if not dsn:
        print("no DSN", how)
        return None
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await V.connect(dsn)
    V.install_stubs()
    await V.run("pre-teardown", V.teardown_retrying)
    V.check(await V.leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
    if not await V.guard_prefix(conn):
        V.S["no_teardown"] = True
        return conn
    V.S["real"] = await V.real_fingerprints(conn)
    V.S["pre"] = await V.counts(conn)
    await V.run("catalog", V.y_catalog, conn)
    if V.S.get("literals_ok"):
        await V.run("seed", V.seed, conn)
    return conn


async def _finish(V, conn) -> int:
    S = V.S
    V.section("[Y] Teardown")
    V.remove_fixture_verbs()
    await V.close_app_pool()
    if not S.get("no_teardown"):
        log = await V.teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
        left = await V.leftovers(conn)
        V.check(left == 0, "zero fixture rows remain", f"{left}")
        post = await V.counts(conn)
        for t in V.COUNTED:
            V.check(post[t] == S["pre"][t], f"{t} at its pre-test count (fixture rows)", f"{S['pre'][t]} → {post[t]}")
        after = await V.real_fingerprints(conn)
        for k in S["real"]:
            V.check(after.get(k) == S["real"][k], f"real {k} unchanged after teardown (count + checksum)",
                    f"{S['real'][k]} → {after.get(k)}")
    await V.close_conn(conn)
    print(V.total_line())
    return 0 if V._n_fail == 0 else 1


async def repro() -> int:
    import httpx
    import verify_s30 as V

    conn = await _start(V)
    try:
        if V.S.get("seeded"):
            code, r1, _ = await V.api(V.U_A1, "POST", "/api/v1/admin/sod/rules", {
                "rule_kind": "policy", "item_type": "role_grant", "self_approval": "never", "enforce_floor": True,
                "description": "S30 fix1 repro"})
            rid = (r1.get("rule") or {}).get("id")
            print(f"[INFO] draft {code} {rid}")
            kw = {"headers": {"Authorization": f"Bearer {V.token_for(V.U_A2)}"}, "json": {"outcome": "approve"}}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=V.main.app, raise_app_exceptions=True),
                                         base_url="http://verify", timeout=120) as client:
                try:
                    res = await client.post(f"/api/v1/admin/sod/rules/{rid}/decide", **kw)
                    print(f"[INFO] decide {res.status_code} {res.text[:400]}")
                    V.check(res.status_code == 200, "approve by a different admin answers 200", res.text[:200])
                except Exception as exc:  # noqa: BLE001
                    traceback.print_exc()
                    V.check(False, "approve by a different admin answers 200", f"{type(exc).__name__}: {exc}")
    finally:
        rc = await _finish(V, conn)
    return rc


async def task1() -> int:
    import verify_s30 as V

    dsn, how = await V.admin_dsn()
    conn = await V.connect(dsn)
    await V.run("task 1", V.y_task1, conn)
    await V.close_conn(conn)
    print(V.total_line())
    return 0 if V._n_fail == 0 else 1


async def fixtures() -> int:
    import verify_s30 as V

    conn = await _start(V)
    try:
        if V.S.get("seeded"):
            for name, fn in (("replay", V.y_replay), ("floor", V.y_floor), ("human task", V.y_human_task),
                             ("org rules", V.y_org_rules), ("roles", V.y_roles),
                             ("self-approval", V.y_self_approval), ("workflows", V.y_workflows),
                             ("admin page", V.y_admin_page), ("isolation", V.y_isolation),
                             ("real rows", V.y_real_rows)):
                await V.run(name, fn, conn)
            V.check(V.NETWORK_CALLS == [] and V.MODEL_CALLS == [], "no network call and no model call",
                    f"{V.NETWORK_CALLS[:3]} {V.MODEL_CALLS[:3]}")
        else:
            V.check(False, "seed completed")
    finally:
        rc = await _finish(V, conn)
    return rc


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"repro": repro, "task1": task1, "fixtures": fixtures}.get(mode)
    if fn is None:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(fn()))
