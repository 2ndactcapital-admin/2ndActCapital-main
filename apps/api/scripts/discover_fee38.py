"""fee38 Task 1 discovery — read-only. Reports what is ACTUALLY deployed."""

from __future__ import annotations

import asyncio
import glob
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, connect  # noqa: E402

NEW_TABLES = ("provider_benefit_schedules", "altruist_one_evaluations")


async def main() -> int:
    dsn, prov = await admin_dsn()
    if dsn is None:
        print(f"NO DSN: {prov}")
        return 1
    print(f"# connected via {prov}\n")
    conn = await connect(dsn)
    out: dict = {}

    # --- 1. the two new tables, exactly as deployed -------------------------
    for t in NEW_TABLES:
        cols = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default,
                   numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=$1
            ORDER BY ordinal_position
            """,
            t,
        )
        out[f"{t}.columns"] = [dict(r) for r in cols]
        cons = await conn.fetch(
            """
            SELECT con.conname, pg_get_constraintdef(con.oid) AS def, con.contype
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' AND c.relname=$1
            ORDER BY con.contype, con.conname
            """,
            t,
        )
        out[f"{t}.constraints"] = [dict(r) for r in cons]
        idx = await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename=$1 ORDER BY indexname",
            t,
        )
        out[f"{t}.indexes"] = [dict(r) for r in idx]
        pol = await conn.fetch(
            """
            SELECT polname, polcmd,
                   pg_get_expr(polqual, polrelid) AS using_expr,
                   pg_get_expr(polwithcheck, polrelid) AS check_expr
            FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=$1
            """,
            t,
        )
        out[f"{t}.policies"] = [dict(r) for r in pol]
        rls = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=$1",
            t,
        )
        out[f"{t}.rls"] = dict(rls) if rls else None
        out[f"{t}.rowcount"] = await conn.fetchval(f"SELECT count(*) FROM public.{t}")

    # --- 2. fee37 cost_providers / cost_schedules ---------------------------
    out["cost_providers"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT * FROM public.cost_providers ORDER BY provider_code"
        )
    ]
    out["cost_schedules"] = [
        dict(r) for r in await conn.fetch("SELECT * FROM public.cost_schedules")
    ]
    for t in ("cost_schedules", "cost_providers"):
        out[f"{t}.columns"] = [
            dict(r)
            for r in await conn.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position",
                t,
            )
        ]

    # --- 3. what account-level data actually exists ------------------------
    want = [
        "account_balances_daily",
        "account_positions_daily",
        "account_flows",
        "accounts",
        "households",
        "entities",
        "entity_holdings",
        "cost_events",
    ]
    found = await conn.fetch(
        """
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_name = ANY($1::text[])
        ORDER BY table_schema, table_name
        """,
        want,
    )
    out["candidate_tables_found"] = [dict(r) for r in found]

    # anything name-like we might have missed
    like = await conn.fetch(
        """
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema IN ('public','portfolio')
          AND (table_name ILIKE '%balance%' OR table_name ILIKE '%cash%'
               OR table_name ILIKE '%margin%' OR table_name ILIKE '%flow%'
               OR table_name ILIKE '%trade%' OR table_name ILIKE '%sweep%'
               OR table_name ILIKE '%account%' OR table_name ILIKE '%household%')
        ORDER BY table_schema, table_name
        """
    )
    out["name_like_tables"] = [dict(r) for r in like]

    for t in ("positions", "transactions", "securities", "cash_balances"):
        cols = await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='portfolio' AND table_name=$1 ORDER BY ordinal_position",
            t,
        )
        if cols:
            out[f"portfolio.{t}.columns"] = [dict(r) for r in cols]
            out[f"portfolio.{t}.rowcount"] = await conn.fetchval(
                f"SELECT count(*) FROM portfolio.{t}"
            )

    # entities: is there a household entity_type + how do accounts hang off it
    out["entity_type_enum"] = [
        r["v"]
        for r in await conn.fetch(
            "SELECT unnest(enum_range(NULL::entity_type))::text AS v"
        )
    ]
    out["entities.columns"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='entities' ORDER BY ordinal_position"
        )
    ]

    # cost_events dedupe index (fee37 F4)
    out["cost_events.indexes"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='cost_events' ORDER BY indexname"
        )
    ]

    await conn.close()
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
