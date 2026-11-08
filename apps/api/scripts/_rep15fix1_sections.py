"""rep15 fix 1 — one-off driver: runs verify_rep15's sections against the live DB (fixtures only), surfacing every
app exception's traceback (the verify's own transport swallows them as a bare 500).

    python3 scripts/_rep15fix1_sections.py [--upto SECTION] [--keep]

Mirrors verify_rep15.main_async's order and gating; tears down at start and end (unless --keep). Never runs the
front-end build or the nested regression.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
for sp in sorted((HERE.parent / "venv" / "lib").glob("python*/site-packages")):
    sys.path.insert(0, str(sp))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import verify_rep15 as V  # noqa: E402
import httpx  # noqa: E402

APP_ERRORS: list[str] = []


async def api_raw(uid, method, path, body=None, files=None, form=None, timeout=300.0):
    kw = {"headers": {"Authorization": f"Bearer {V.token_for(uid)}", "User-Agent": "verify_rep15"}}
    if body is not None:
        kw["json"] = body
    if files is not None:
        kw["files"] = files
        kw["data"] = form or {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=V.main.app, raise_app_exceptions=True),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b""
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc()
            APP_ERRORS.append(f"{method} {path}\n{tb}")
            print(f"[APP-500] {method} {path}\n{tb}", flush=True)
            return 500, json.dumps({"raw": "Internal Server Error"}).encode()
    return res.status_code, res.content


V.api_raw = api_raw

ORDER = ["provider model", "gate off", "gate open", "publish", "subscribe", "use", "new version", "suspend",
         "isolation", "fee", "gate close"]


async def main_async() -> int:
    upto = sys.argv[sys.argv.index("--upto") + 1] if "--upto" in sys.argv else None
    keep = "--keep" in sys.argv
    dsn, how = await V.admin_dsn()
    import os
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await V.connect(dsn)
    V.install_stubs()
    pre = None
    S = V.S
    run = V.run

    def stop(name):
        return upto is not None and ORDER.index(name) > ORDER.index(upto)

    try:
        await run("pre-teardown", V.teardown_retrying)
        V.check(await V.leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        if not await V.guard_prefix(conn):
            S["no_teardown"] = True
            return 1
        pre = await V.counts(conn)
        S["real"] = await V.real_fingerprints(conn)
        await run("schema", V.y_schema, conn)
        await run("seed literals", V.y_seed_literals, conn)
        if S.get("literals_ok"):
            await run("seed", V.seed, conn)
        if S.get("seeded") and "--tenant-only" in sys.argv:
            # Task 1: an ORDINARY fixture tenant org (A), its org admin (a1) — never the real 2nd Act org
            V.section("[Y] fix1 — a fixture TENANT org's org admin uploads and confirms (rep3's plain path)")
            code, content = await V.api_raw(V.U_A1, "POST", "/api/v1/models/uploads",
                                            files={"file": ("rep15_tenant.csv", V.csv_bytes(V.V1, "tenant"),
                                                            "text/csv")})
            up = json.loads(content.decode() or "{}").get("upload") or {}
            c2, o2 = await V.api(V.U_A1, "POST", f"/api/v1/models/uploads/{up.get('id')}/confirm",
                                 {"model_name": "REP15 Verify Tenant Model"})
            V.check(code == 201 and c2 == 201 and bool((o2.get("model") or {}).get("id")),
                    "tenant A's org admin: upload 201 → confirm 201, a draft model created", f"{code} {c2} "
                    f"{V.dumps(o2)[:300]}")
            n = await V.scoped(conn, V.ORG_A, "SELECT count(*) FROM portfolio.model_version_positions p JOIN "
                               "portfolio.model_versions v ON v.id = p.version_id WHERE v.id = $1::uuid",
                               (o2.get("version") or {}).get("id"), fetch="fetchval")
            V.check(n == 3, "its three positions persisted (re-read on the verify's own app_service connection)",
                    f"{n}")
        elif S.get("seeded"):
            await run("provider model", V.y_provider_model, conn)
            if S.get("provider_model_ok") and not stop("gate off"):
                await run("gate off", V.y_gate_off, conn)
                await run("gate open", V.y_gate_open, conn)
                if S.get("gate_open") and not stop("publish"):
                    await run("publish", V.y_publish, conn)
                    if S.get("published") and not stop("subscribe"):
                        await run("subscribe", V.y_subscribe, conn)
                        if S.get("sub_a_ok") and S.get("mirror_b") and not stop("use"):
                            await run("use", V.y_use, conn)
                            if S.get("joined") and not stop("new version"):
                                await run("new version", V.y_new_version, conn)
                                await run("suspend", V.y_suspend, conn)
                                await run("isolation", V.y_isolation, conn)
                                if S.get("v2_ok") and not stop("fee"):
                                    await run("fee", V.y_fee, conn)
                            elif not S.get("joined"):
                                V.check(False, "the provided model joined the fixture accounts", "later skipped")
                        else:
                            V.check(False, "both tenants' subscriptions became active (or stopped)", "later skipped")
                    else:
                        V.check(False, "the catalog version was published (or stopped)", "later skipped")
                    if not stop("gate close"):
                        await run("gate close", V.y_gate_close, conn)
                else:
                    V.check(False, "the fixture gate opened (or stopped)", "later skipped")
            else:
                V.check(False, "the provider model was approved (or stopped)", "later skipped")
        await run("real rows", V.y_real_rows, conn)
        V.check(V.NETWORK_CALLS == [] and V.MODEL_CALLS == [] and V.S3_BUILT == [],
                "no network call, no model call and no real R2 client", f"{V.NETWORK_CALLS[:3]} {V.MODEL_CALLS[:3]}")
    finally:
        V.section("[Y] Teardown")
        await V.close_app_pool()
        if keep:
            print("[INFO] --keep: fixtures left in place")
        else:
            try:
                log = await V.teardown_retrying()
                print(f"[INFO] teardown removed: {log}")
                left = await V.leftovers(conn)
                V.check(left == 0, "zero fixture rows remain", f"{left}")
                if pre is not None:
                    post = await V.counts(conn)
                    for t in V.COUNTED:
                        V.check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
                if S.get("real") is not None:
                    after = await V.real_fingerprints(conn)
                    for k in S["real"]:
                        V.check(after.get(k) == S["real"][k], f"real {k} unchanged", f"{S['real'][k]} → {after.get(k)}")
                real = await V.platform(conn, "SELECT portfolio.model_publishing_open($1::uuid)", V.PLATFORM_ORG_ID,
                                        fetch="fetchval")
                V.check(real is False, "the REAL platform org's gate is OFF after teardown", f"{real}")
            except Exception as exc:  # noqa: BLE001
                V.check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
                traceback.print_exc()
        await V.close_conn(conn)
    print(f"APP_ERRORS: {len(APP_ERRORS)}")
    print(V.total_line())
    return 0 if V._n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
