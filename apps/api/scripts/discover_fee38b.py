"""fee38 Task 1 discovery, part 2 — account-level data reality check."""

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

TABLES = [
    ("public", "accounts"),
    ("public", "households"),
    ("public", "household_memberships"),
    ("public", "account_balances_daily"),
    ("public", "account_flows"),
    ("public", "account_owners"),
    ("portfolio", "positions"),
    ("portfolio", "transactions"),
]


async def main() -> int:
    dsn, prov = await admin_dsn()
    if dsn is None:
        print(f"NO DSN: {prov}")
        return 1
    conn = await connect(dsn)
    out: dict = {}
    for schema, t in TABLES:
        key = f"{schema}.{t}"
        out[key] = {
            "columns": [
                dict(r)
                for r in await conn.fetch(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema=$1 AND table_name=$2 ORDER BY ordinal_position",
                    schema,
                    t,
                )
            ],
            "rowcount": await conn.fetchval(f"SELECT count(*) FROM {schema}.{t}"),
            "fks": [
                dict(r)
                for r in await conn.fetch(
                    """
                    SELECT con.conname, pg_get_constraintdef(con.oid) AS def
                    FROM pg_constraint con
                    JOIN pg_class c ON c.oid=con.conrelid
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=$1 AND c.relname=$2 AND con.contype IN ('f','c')
                    """,
                    schema,
                    t,
                )
            ],
        }

    # what CHECK vocabularies constrain the account/balance rows we care about
    for col in ("account_type", "balance_type", "flow_type", "status"):
        out[f"distinct.{col}"] = {}
    for schema, t in TABLES:
        cols = {c["column_name"] for c in out[f"{schema}.{t}"]["columns"]}
        for col in ("account_type", "balance_type", "cash_type", "flow_type", "status",
                    "sub_type", "category", "quantity_type"):
            if col in cols:
                vals = await conn.fetch(
                    f"SELECT {col}::text AS v, count(*) AS n FROM {schema}.{t} "
                    f"GROUP BY 1 ORDER BY 2 DESC LIMIT 25"
                )
                out.setdefault("distinct_values", {})[f"{schema}.{t}.{col}"] = [
                    dict(r) for r in vals
                ]

    await conn.close()
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
