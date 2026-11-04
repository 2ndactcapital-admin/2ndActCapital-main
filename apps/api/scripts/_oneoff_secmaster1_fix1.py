"""One-off (secmaster1 fix 1): prove the three fixes on is_test fixtures only, reusing verify_secmaster1's own
fixtures, seed and sections (imported as a module — its __main__ is not run, no regression, no build).

  (A) reproduce: the pre-fix BatchResult.merge (source at 8a61922) drops by_record; then the real 13(f) section:
      a fixture CUSIP answered positionally by the OpenFIGI stub → a new security with ticker + FIGI + CUSIP.
  (B)+(C) the real [Y] Jobs section: forced step failure → held run with the error, Super Admin alert, org admin
      403 on close, Super Admin closes it, schedule fires again; Super Admin in the client org → held "runs only
      in the platform org"; member without the permission → permission refusal.
      reproduce C's root first: under the CLIENT org's RLS context the Super Admin's users row is invisible.
Teardown (start and end) by the verify's own fixture-keyed teardown; zero fixture rows, every counted table at its
pre-test count, real rows unchanged (count + checksum).
"""
from __future__ import annotations

import os
os.environ["AUTHZ_TEST_MODE"] = "1"
os.environ["VERIFY_NESTED"] = "1"

import asyncio  # noqa: E402
import pathlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_secmaster1 as V  # noqa: E402


def reproduce_merge() -> None:
    V.section("[A] reproduce: the pre-fix merge")
    src = subprocess.run(["git", "show", "8a61922:apps/api/services/security_master_load.py"], cwd=V.REPO,
                         capture_output=True, text=True, check=True).stdout
    start = src.index("@dataclass\nclass BatchResult:")
    end = src.index("\n    @property", start)
    ns: dict = {}
    exec("from dataclasses import dataclass, field\n" + src[start:end], ns)  # noqa: S102 — our own committed source
    old_total = ns["BatchResult"]()
    old_total.merge(ns["BatchResult"](by_record={"9ZZSM00N1": ("new", "x", "created")}))
    V.check(old_total.by_record == {}, "[repro] at 8a61922, BatchResult.merge drops by_record (→ 'not mapped — no match')",
            f"{old_total.by_record}")
    new_total = V.M.BatchResult()
    new_total.merge(V.M.BatchResult(by_record={"9ZZSM00N1": ("new", "x", "created")}))
    V.check(new_total.by_record == {"9ZZSM00N1": ("new", "x", "created")}, "[fixed] merge carries by_record",
            f"{new_total.by_record}")


async def reproduce_principal(conn) -> None:
    V.section("[C] reproduce: the Super Admin's own row under the client org's RLS context")
    from services.database import get_pool, platform_scope, reset_rls_context, set_rls_context
    from services.rbac import load_principal

    pool = await V.get_pool()
    tokens = set_rls_context(str(V.ORG_A), False)
    try:
        async with pool.acquire() as c:
            async with c.transaction():
                hidden = await load_principal(c, V.U_SUPER)
        async with pool.acquire() as c:
            async with platform_scope(c):
                seen = await load_principal(c, V.U_SUPER)
    finally:
        reset_rls_context(tokens)
    V.check(hidden is None, "[repro] under ORG_A's context load_principal(Super Admin of ORG_P) is None — the "
            "bypass could never fire, so the permission check refused first", f"{hidden}")
    V.check(bool(seen and seen["role"] == "super_admin"), "[fixed] under platform_scope it resolves (super_admin)",
            f"{seen}")
    _ = get_pool


async def main() -> int:
    dsn, how = await V.admin_dsn()
    if not dsn:
        print(f"[FAIL] no DB: {how}")
        return 1
    os.environ["DATABASE_URL"] = dsn
    conn = await V.connect(dsn)
    V.install_stubs()
    pre = None
    try:
        reproduce_merge()
        await V.run("pre-teardown", V.teardown_retrying)
        V.check(await V.leftovers(conn) == 0, "pre-run: zero fixture rows")
        if not await V.guard_prefix(conn):
            return 1
        pre = await V.counts(conn)
        V.S["real"] = await V.real_fingerprints(conn)
        await V.run("seed", V.seed, conn)
        await reproduce_principal(conn)
        await V.run("matcher", V.y_matcher, conn)
        await V.run("13f", V.y_13f, conn)
        await V.run("jobs", V.y_jobs, conn)
    finally:
        V.section("[Y] Teardown")
        await V.close_app_pool()
        log = await V.teardown_retrying()
        print(f"[INFO] teardown removed: {log}")
        left = await V.leftovers(conn)
        V.check(left == 0, "zero fixture rows remain", f"{left}")
        if pre is not None:
            post = await V.counts(conn)
            bad = {t: (pre[t], post[t]) for t in V.COUNTED if pre[t] != post[t]}
            V.check(not bad, "every counted table at its pre-test count", f"{bad}")
            after = await V.real_fingerprints(conn)
            V.check(after == V.S["real"], "real rows unchanged (count + checksum)",
                    f"{[k for k in after if after[k] != V.S['real'][k]]}")
        await V.close_conn(conn)
    V.check(V.NETWORK_CALLS == [], "no network call attempted", f"{V.NETWORK_CALLS[:3]}")
    print(f"\nTOTAL: {V._n_pass} PASS, {V._n_fail} FAIL")
    return 0 if V._n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
