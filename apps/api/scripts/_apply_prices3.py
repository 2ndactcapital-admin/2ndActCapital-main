"""PRICES3 — apply the lean price-of-record objects (EMPTY) and read the catalog back.

    python3 apps/api/scripts/_apply_prices3.py --schema     # services.security_data.price_store.schema_sql('portfolio')
    python3 apps/api/scripts/_apply_prices3.py --verify     # read the catalog back
    python3 apps/api/scripts/_apply_prices3.py --all        # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``: DDL needs the owner). Idempotent.
NEVER touches portfolio.securities_global_prices (no copy, rename or drop — that is scripts/prices3_migrate.py, run
by the operator). Refuses to re-apply once the migration has swapped (the view then carries the old name).
Never prints a secret."""

from __future__ import annotations

import asyncio
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()
sys.path.insert(0, str(API_DIR))

from services.security_data import price_store as PS  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
EXPECTED_ROWS = {"price_series": 0, "price_provenance": 0, "price_quality_flags": 1, "security_prices": 0,
                 "security_price_history": 0, "price_bars_pending": 0, "price_archive_state": 0,
                 "prices3_migration": 1, "prices3_dirty": 0}


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def apply_schema(conn, schema: str = "portfolio", *, fks: bool = True) -> None:
    if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"{schema}.prices3_migration"):
        phase = await conn.fetchval(f"SELECT phase FROM {schema}.prices3_migration WHERE id = 1")
        if phase in ("swapped", "dropped"):
            raise SystemExit(f"[REFUSED] the migration is '{phase}': the compatibility view carries the old name now; "
                             "re-applying would create a second view")
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout = '10s'")
        await conn.execute(PS.schema_sql(schema, fks=fks))
    print(f"[OK] PRICES3 schema applied to {schema}")


async def read_back(conn, schema: str = "portfolio") -> tuple[int, int, list[str]]:
    """(passes, fails, lines) — the catalog as it stands."""
    ok = bad = 0
    lines: list[str] = []

    def chk(c, label, detail=""):
        nonlocal ok, bad
        ok, bad = (ok + 1, bad) if c else (ok, bad + 1)
        lines.append(f"[{'PASS' if c else 'FAIL'}] {label}" + ("" if c or not detail else f" — {detail}"))

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for tname, n in EXPECTED_ROWS.items():
            reg = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"{schema}.{tname}")
            got = await conn.fetchval(f"SELECT count(*) FROM {schema}.{tname}") if reg else None
            chk(reg and got == n, f"{schema}.{tname} exists with {n} row(s)", f"exists={reg} rows={got}")
        rls = {r["relname"]: r["relrowsecurity"] for r in await conn.fetch(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relname = ANY($2::text[])", schema, list(EXPECTED_ROWS))}
        chk(all(rls.get(x) for x in EXPECTED_ROWS), "RLS enabled on every new table", f"{rls}")
        pol = {r["tablename"]: r["n"] for r in await conn.fetch(
            "SELECT tablename, count(*) AS n FROM pg_policies WHERE schemaname = $1 AND tablename = ANY($2::text[]) "
            "GROUP BY 1", schema, list(EXPECTED_ROWS))}
        want = {**{x: 4 for x in ("price_series", "price_provenance", "security_prices", "security_price_history",
                                  "price_bars_pending")},
                **{x: 2 for x in ("price_quality_flags", "price_archive_state", "prices3_migration", "prices3_dirty")}}
        chk(pol == want, "policies: 4 per org / series-scoped table, 2 per platform table", f"{pol}")
        vopt = await conn.fetchval("SELECT c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                   "WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind = 'v'", schema, PS.VIEW_V3)
        chk(vopt is not None and "security_invoker=true" in vopt, f"{schema}.{PS.VIEW_V3} is a security_invoker view",
            f"{vopt}")
        cols = await conn.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = $1 "
                                "AND table_name = $2 ORDER BY ordinal_position", schema, PS.VIEW_V3)
        chk(tuple(c["column_name"] for c in cols) == PS.OLD_COLUMNS, "the view publishes the old 19 columns in order",
            f"{[c['column_name'] for c in cols]}")
        ref_schema = schema if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                                   f"{schema}.{PS.OLD}") else "portfolio"
        old = await conn.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = $1 "
                               "AND table_name = $2 ORDER BY ordinal_position", ref_schema, PS.OLD)
        chk([tuple(c) for c in cols] == [tuple(c) for c in old],
            f"…with the same types as {ref_schema}.{PS.OLD}",
            f"{[(a['column_name'], a['data_type'], b['data_type']) for a, b in zip(cols, old) if tuple(a) != tuple(b)]}")
        trg = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = $1::regclass AND tgname = "
                                  "'prices3_view_write'", f"{schema}.{PS.VIEW_V3}")
        chk(trg == 1, "the INSTEAD OF write trigger is on the view", f"{trg}")
        mode = await conn.fetchrow(f"SELECT store_mode, phase FROM {schema}.prices3_migration WHERE id = 1")
        chk(mode and mode["store_mode"] == "legacy", "the switch reads 'legacy' (writers unchanged until the swap)",
            f"{dict(mode) if mode else None}")
        if schema == "portfolio":
            real = await conn.fetchval("SELECT c.relkind::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                       "WHERE n.nspname = 'portfolio' AND c.relname = $1", PS.OLD)
            chk(real == "r" or (mode and mode["phase"] in ("swapped", "dropped")),
                "portfolio.securities_global_prices is still the TABLE (the sprint never swaps)", f"relkind={real}")
        grants = {(r["grantee"], r["privilege_type"]) for r in await conn.fetch(
            "SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 AND "
            "table_name = $2", schema, PS.VIEW_V3)}
        chk({("app_service", p) for p in ("SELECT", "INSERT", "UPDATE", "DELETE")} | {("cpsim_app", "SELECT")}
            <= grants, "the view carries the old table's grants (app_service RW, cpsim_app R)", f"{sorted(grants)}")
        idx = {r["indexname"] for r in await conn.fetch("SELECT indexname FROM pg_indexes WHERE schemaname = $1 AND "
                                                        "tablename = 'security_prices'", schema)}
        chk(idx == {"security_prices_pkey", "security_prices_date_ix", "security_prices_recorded_brin"},
            "security_prices indexes: PK (series, date), date btree, recorded_at BRIN — nothing else", f"{idx}")
    return ok, bad, lines


async def main() -> int:
    args = set(sys.argv[1:])
    if not args & {"--schema", "--verify", "--all"}:
        print(__doc__)
        return 2
    conn = await _conn()
    try:
        if args & {"--schema", "--all"}:
            await apply_schema(conn)
        if args & {"--verify", "--all"}:
            ok, bad, lines = await read_back(conn)
            print("\n".join(lines))
            print(f"SCHEMA {'OK' if not bad else 'NOT OK'} — {ok} PASS, {bad} FAIL")
            return 0 if not bad else 1
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
