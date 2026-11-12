"""cpsim05 fix 3 — ONE-OFF probe (not a verify): how many DB round-trips does the proposal approval request make, by
caller, on the simulated route vs the manual route, at 2 and 6 lines? Reuses verify_cpsim05's seed / teardown
(fixtures only) by running it as `--only=timing` with the timing section swapped for this probe.

    timeout 600 python3 scripts/_probe_cpsim05_fix3.py
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time

sys.argv = [sys.argv[0], "--only=timing"]
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify_cpsim05 as V  # noqa: E402
import asyncpg  # noqa: E402

API = os.path.dirname(HERE)
STATE = {"on": False, "rows": []}
_ORIG = {}


def _caller():
    f = sys._getframe(2)
    chain = []
    while f is not None:
        fn = f.f_code.co_filename
        if fn.startswith(API) and "/scripts/" not in fn and "/venv/" not in fn:
            chain.append(f"{os.path.relpath(fn, API)}:{f.f_code.co_name}")
        f = f.f_back
    return chain


def _wrap(name):
    orig = getattr(asyncpg.connection.Connection, name)
    _ORIG[name] = orig

    async def w(self, *a, **k):
        if not STATE["on"]:
            return await orig(self, *a, **k)
        t0 = time.monotonic()
        try:
            return await orig(self, *a, **k)
        finally:
            sql = str(a[0]) if a else ""
            STATE["rows"].append({"t": time.monotonic() - t0, "sql": " ".join(sql.split())[:110],
                                  "chain": _caller()})
    setattr(asyncpg.connection.Connection, name, w)


for _n in ("execute", "executemany", "fetch", "fetchrow", "fetchval"):
    _wrap(_n)


def bucket(chain: list[str]) -> str:
    s = " ".join(chain)
    if "fix_sim_auto.py:_run_batch" in s or "fix_sim_auto.py:gated" in s:
        return "fix_sim automatic step (AFTER response)"
    if "main.py:" in s and "rebalance_proposals" not in s:
        return "middleware (auth / principal / RLS context)"
    if "held_away" in s or "_approval_precheck" in s:
        return "approval precheck (rep7 held-away)"
    if "sod.py" in s:
        return "SoD gate"
    if "fix_sim_auto.py" in s:
        return "fix_sim scheduling"
    if "trading.py" in s or "execution_routers" in s or "custodian_execution" in s or "services/execution.py" in s:
        return "choke-point routing + instruction creation"
    if "rebalance_proposals.py:execute" in s:
        return "execute (proposal restatement / load)"
    if "human_tasks" in s:
        return "human task complete"
    if "rebalance_proposals.py" in s:
        return "approval (load / lock / update / audit / publish)"
    return "other: " + (chain[0] if chain else "?")


async def one(conn, label, account, n_lines):
    lines = [{"security": V.SEC_FUND, "side": "buy", "quantity": "1", "price": V.PRICE_FUND} for _ in range(n_lines)]
    pid = await V.make_proposal(conn, account, lines)
    c, o = await V.api(V.U_MK, "POST", f"{V._PR}/{pid}/submit")
    if c != 200:
        print(f"[PROBE] {label}: submit {c} {V.dumps(o)[:300]}")
        return None
    STATE["rows"] = []
    STATE["on"] = True
    t0 = time.monotonic()
    c, o = await V.api(V.U_CK, "POST", f"{V._PR}/{pid}/approve", {"note": "cpsim05 fix 3 probe"})
    secs = time.monotonic() - t0
    STATE["on"] = False
    rows = list(STATE["rows"])
    await V.FA.drain()
    ids = await V.ids_of(conn, pid)
    routed = [(await V.instruction(conn, i)).get("routed_via") for i in ids]
    by = collections.Counter(bucket(r["chain"]) for r in rows)
    in_req = [r for r in rows if not bucket(r["chain"]).startswith("fix_sim automatic")]
    sq = collections.Counter(r["sql"] for r in in_req)
    print(f"[PROBE] {label}: HTTP {c} lines={n_lines} routed={routed} secs={secs:.2f} "
          f"queries_in_request={len(in_req)} db_secs={sum(r['t'] for r in in_req):.2f} "
          f"(all incl. after-response={len(rows)})")
    for k, v in by.most_common():
        print(f"[PROBE]    {v:4d}  {k}")
    print(f"[PROBE]    top repeated statements:")
    for s, v in sq.most_common(12):
        print(f"[PROBE]      {v:3d}x {s}")
    V.S.setdefault("probe", {})[label] = {"code": c, "secs": round(secs, 2), "n": len(in_req),
                                          "by": dict(by), "routed": routed}
    await V.free_account(conn, pid)
    return pid


async def probe(conn):
    V.section("[PROBE] cpsim05 fix 3 — approval request round-trips")
    await V.behaviour(default_outcome="accept")
    for label, acc, n in (("manual_2", V.ACC_OTHER, 2), ("sim_2", V.ACC_SIM, 2),
                          ("manual_6", V.ACC_OTHER, 6), ("sim_6", V.ACC_SIM, 6)):
        try:
            await one(conn, label, acc, n)
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"[PROBE] {label} raised {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print("[PROBE-JSON] " + json.dumps(V.S.get("probe")))


V.FIXTURE_SECTIONS = (("timing", probe),)

if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(V.main_async()))
