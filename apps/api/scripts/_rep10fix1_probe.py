"""rep10 fix 1 — Task 4: prove FAIL D without a full verify run (one-off; its own is_test fixture orgs).

1. Org P1 (is_test) holds nothing for security NP → layer.get / get_bulk / history / rep10_common.longest_history
   and rebalance_proposals._unheld_prices all find nothing (found = false / no price → 'none', bought by notional).
2. FAIL D's exact source, reproduced: P1 account 1 holds security VB (10 shares, market value 1000, as of yesterday)
   — rep5's IRA shape. Asking for VB's price (as rep5's unheld lookup does for another account) → the custodian
   provider answers 100 = 1000 ÷ 10, as of yesterday. Re-stated with market value NULL → found = false.
3. Synthetic csv_upload history for NP is loaded into a SECOND fixture org P2 (P2's own RLS scope): P2 finds it,
   P1 still finds nothing through every call.
Then everything is deleted (by fixture id, operator DSN) and zero rows remain.

Run: python3 scripts/_rep10fix1_probe.py  (env via hydrate(); no network).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("AUTHZ_TEST_MODE", "1")
from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()
sys.path.insert(0, str(API_DIR))

import _verify_cleanup as VC  # noqa: E402
from services import rebalance_proposals as RP  # noqa: E402
from services import rep10_common as RC  # noqa: E402
from services import securities_global as SG  # noqa: E402
from services import security_data as SD  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import get_pool, reset_rls_context, set_rls_context  # noqa: E402
from services.security_data import writes as SDW  # noqa: E402

TAG = "rep10fix1probe"
PREFIX = "ZZR10F1"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000001f{n:04d}")


P1, P2 = _id(1), _id(2)
ENT1, ACCT1 = _id(11), _id(21)
NAMES = {"NP": "REP10FIX1 PROBE NP", "VB": "REP10FIX1 PROBE VB"}
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
_ok = {"pass": 0, "fail": 0}


def check(passed: bool, label: str, detail: str = "") -> None:
    _ok["pass" if passed else "fail"] += 1
    print(f"[{'PASS' if passed else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


async def operator():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN: {how}")
    return await connect(dsn)


async def sec_ids(op) -> list:
    rows = await op.fetch("SELECT id FROM portfolio.securities_global WHERE name = ANY($1::text[]) UNION SELECT "
                          "global_security_id FROM portfolio.securities_global_identifiers WHERE id_value LIKE $2",
                          list(NAMES.values()), f"{PREFIX}%")
    return [r["id"] for r in rows]


async def teardown() -> list[str]:
    op = await operator()
    try:
        secs = await sec_ids(op)
        plan = {"portfolio.securities_global_prices": ("global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.securities_global_identifiers": ("global_security_id = ANY($1::uuid[])", [secs]),
                "portfolio.securities_global": ("id = ANY($1::uuid[])", [secs])}
        res = await VC.cleanup(op, orgs=[P1, P2], plan=plan, label="rep10fix1_probe")
        return res["log"]
    finally:
        await op.close()


async def remaining() -> int:
    op = await operator()
    try:
        n = len(await sec_ids(op))
        n += int(await op.fetchval("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [P1, P2]))
        for r in await op.fetch(
                "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped "
                "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition AND n.nspname NOT IN ('pg_catalog', "
                "'information_schema')"):
            n += int(await op.fetchval(f"SELECT count(*) FROM {r['t']} WHERE org_id = ANY($1::uuid[])", [P1, P2]))
        return n
    finally:
        await op.close()


async def in_org(org, fn):
    pool = await get_pool()
    tok = set_rls_context(str(org), False)
    try:
        async with pool.acquire() as c:
            return await fn(c)
    finally:
        reset_rls_context(tok)


async def reads(org, key: str, t: date) -> dict:
    async def fn(c):
        g = await SD.get(c, org, "eod_price", key, as_of=t, purpose="internal")
        b = await SD.get_bulk(c, org, "eod_price", [key], as_of=t, purpose="internal")
        h = await SD.history(c, org, "eod_price", key, start=t - timedelta(days=400), end=t, purpose="internal")
        lh = await RC.longest_history(c, org, key, t - timedelta(days=400), t)
        px = await RP._unheld_prices(c, [key], t - timedelta(days=30), org_id=org, today=t)
        bb = b.get(key)
        return {"get": bool(g.found), "get_bulk": bool(bb is not None and bb.found), "history": bool(h.found and h.value),
                "longest_history": lh is not None, "unheld_price": key in px,
                "price_source": px[key][2] if key in px else "none",
                "get_detail": (g.provider_key, None if g.value is None else str(g.value), str(g.as_of)),
                "history_points": len(h.value or []) if h.found else 0}
    return await in_org(org, fn)


FLAGS = ("get", "get_bulk", "history", "longest_history", "unheld_price")


async def main() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no DATABASE_URL: {how}")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    print(f"[INFO] pre-teardown: {await teardown()}")
    t = date.today()
    yday = t - timedelta(days=1)
    conn = await connect(dsn)
    try:
        who = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                                  "current_user) AS b")
        check(who["b"] is False, "probe connection does not bypass RLS", f"{who['u']} rolbypassrls={who['b']}")
        sec, vb_pos = {}, uuid.uuid4()
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            for org, nm in ((P1, "1"), (P2, "2")):
                await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                                   org, f"REP10FIX1 Probe Org {nm}", f"{TAG}-{nm}")
            for i, key in enumerate(NAMES):
                sid = await SG.create_security(conn, name=NAMES[key], security_type="equity", is_super_admin=True)
                await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=f"{PREFIX}{i:02d}",
                                        is_super_admin=True)
                sec[key] = str(sid)
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(P1))
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                               "'individual', 'REP10FIX1 Probe Member')", ENT1, P1)
            salt = await get_or_create_salt(conn, P1)
            n = AccountNumber("ZZF1P0000001")
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, is_discretionary, is_held_away) "
                "VALUES ($1, $2, $3, $4, 'ALTRUIST', 'ira', 'tax_deferred', $5, true, false)",
                ACCT1, P1, n.masked, n.hashed(salt), ENT1)
            asset = uuid.uuid4()
            await conn.execute("INSERT INTO portfolio.assets (id, org_id, global_security_id, name, asset_type) "
                               "VALUES ($1, $2, $3, $4, 'equity')", asset, P1, UUID(sec["VB"]), NAMES["VB"])
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, quantity, "
                "market_value, authority, source_system, account_id) VALUES ($1, $2, $3, $4, $5, 10, 1000, 'manual', "
                "'manual', $6)", vb_pos, P1, ENT1, asset, yday, ACCT1)

        # 1. nothing anywhere for NP
        r = await reads(P1, sec["NP"], t)
        check(not any(r[f] for f in FLAGS) and r["price_source"] == "none",
              "org P1, security NP with no source anywhere: found = false through get, get_bulk, history, "
              "longest_history; the proposal lookup has no price (price_source 'none' → notional)", f"{r}")
        # 2. FAIL D's source reproduced: rep5's IRA shape (10 sh, mv 1000, yesterday) → custodian 100
        r = await reads(P1, sec["VB"], t)
        check(r["get"] and r["unheld_price"] and r["get_detail"][0] == "custodian"
              and Decimal(r["get_detail"][1]) == Decimal(100) and r["get_detail"][2] == yday.isoformat()
              and r["price_source"] == "security_price",
              "REPRODUCED FAIL D: a position valued in ANY account of the org (10 sh, market value 1000, yesterday) "
              "is priced by the custodian provider at 100 = 1000 ÷ 10 → price_source 'security_price'", f"{r}")
        r_b = await reads(P2, sec["VB"], t)
        check(not any(r_b[f] for f in FLAGS), "…and that valuation is invisible to another org (P2)", f"{r_b}")

        async def unvalue(c):
            async with c.transaction():
                await c.execute("UPDATE portfolio.positions SET market_value = NULL WHERE id = $1", vb_pos)
        await in_org(P1, unvalue)
        r = await reads(P1, sec["VB"], t)
        check(not any(r[f] for f in FLAGS) and r["price_source"] == "none",
              "the same holding with its valuation unknown (verify_rep5's fixed fixture) → found = false everywhere, "
              "price_source 'none'", f"{r}")

        # 3. synthetic history in a SECOND org's scope only
        days = [t - timedelta(days=k) for k in range(1, 61) if (t - timedelta(days=k)).isoweekday() < 6]
        pts = [(d_, Decimal(50) + Decimal(i)) for i, d_ in enumerate(sorted(days))]

        async def load(c):
            async with c.transaction():
                return await SDW.upsert_prices(None, sec["NP"], pts, provider_key="csv_upload", org_id=str(P2),
                                               licence={"internal_use": True}, conn=c)
        wrote = await in_org(P2, load)
        r2 = await reads(P2, sec["NP"], t)
        check(wrote == len(pts) and all(r2[f] for f in FLAGS) and r2["history_points"] == len(pts),
              f"org P2's own synthetic csv_upload history ({len(pts)} days) IS found by P2 (control)",
              f"wrote {wrote}; {r2}")
        r1 = await reads(P1, sec["NP"], t)
        check(not any(r1[f] for f in FLAGS) and r1["price_source"] == "none",
              "org P1 still finds NOTHING for NP after P2's synthetic history load (get, get_bulk, history, "
              "longest_history, proposal lookup)", f"{r1}")
    finally:
        await conn.close()
        import services.database as DB
        if DB._pool is not None:
            await DB._pool.close()
            DB._pool = None
        log = await teardown()
        print(f"[INFO] teardown removed: {log}")
        left = await remaining()
        check(left == 0, "zero probe rows remain (orgs, every org_id table, securities, identifiers, prices)",
              f"{left}")
    print(f"\nTOTAL: {_ok['pass']} PASS, {_ok['fail']} FAIL")
    return 0 if _ok["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
