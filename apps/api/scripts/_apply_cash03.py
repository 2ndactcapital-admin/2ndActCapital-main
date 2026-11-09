"""CASH03 — apply docs/cash03_part1.sql and prove it landed.

    python3 apps/api/scripts/_apply_cash03.py --schema    # docs/cash03_part1.sql, one transaction
    python3 apps/api/scripts/_apply_cash03.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_cash03.py --all       # both

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). Idempotent.
Never prints a secret. Catalog rows only (two platform providers, NOT connected; four platform rank rows; the widened
reference-field CHECKs) — writes no org row and no data row.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

SCHEMA_SQL = REPO / "docs" / "cash03_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
PROVIDERS = {"treasury_fiscal": "public_api", "sec_nmfp": "regulator_filing"}
RANKS = {("index_level", "treasury_fiscal"): 4, ("index_level", "sec_nmfp"): 5, ("reference", "treasury_fiscal"): 7,
         ("reference", "sec_nmfp"): 8}
NEW_FIELDS = ("maturity_date", "issue_date", "security_term", "treasury_type", "mmf_category")
CHECKS = ("security_reference_attributes_field_chk", "security_reference_attributes_date_chk",
          "security_reference_attributes_treasury_chk", "security_reference_attributes_mmf_chk")


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def apply_schema() -> None:
    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
    finally:
        await conn.close()


async def verify_schema() -> int:
    conn = await _conn()
    bad = []
    try:
        for key, kind in PROVIDERS.items():
            r = await conn.fetchrow("SELECT kind, status, capabilities, lic_internal_use, lic_member_display, "
                                    "lic_redistribution, retention_after_cancel, attribution FROM portfolio.data_providers "
                                    "WHERE key = $1 AND valid_to IS NULL AND system_to IS NULL", key)
            if r is None:
                bad.append(f"provider {key}: absent")
                continue
            if r["kind"] != kind or set(r["capabilities"]) != {"index_level", "reference"}:
                bad.append(f"provider {key}: kind {r['kind']} capabilities {r['capabilities']}")
            if not (r["lic_internal_use"] and r["lic_member_display"] == "allowed" and r["lic_redistribution"]
                    and r["retention_after_cancel"] == "keep" and r["attribution"]):
                bad.append(f"provider {key}: licence facts {dict(r)}")
            print(f"[INFO] provider {key}: status {r['status']} (shipped not_connected; a Super Admin switches it on)")
        for (dtype, key), rank in RANKS.items():
            got = await conn.fetchval("SELECT rank FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND "
                                      "data_type = $1 AND provider_key = $2 AND valid_to IS NULL AND system_to IS NULL",
                                      dtype, key)
            if got is None:
                bad.append(f"rank {dtype}/{key}: absent")
            elif got != rank:
                print(f"[INFO] rank {dtype}/{key} is {got} (the migration's {rank} was taken — an operator re-ranked)")
        defs = {r["conname"]: r["d"] for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = "
            "'portfolio.security_reference_attributes'::regclass AND conname = ANY($1::text[])", list(CHECKS))}
        if set(defs) != set(CHECKS):
            bad.append(f"checks missing {sorted(set(CHECKS) - set(defs))}")
        fld = defs.get("security_reference_attributes_field_chk", "")
        miss = [f for f in NEW_FIELDS if f"'{f}'" not in fld]
        if miss:
            bad.append(f"field check lacks {miss}")
    finally:
        await conn.close()
    print("SCHEMA OK" if not bad else f"SCHEMA PROBLEMS: {bad}")
    return 0 if not bad else 1


async def main(argv: list[str]) -> int:
    if "--schema" in argv or "--all" in argv:
        await apply_schema()
    if "--verify" in argv or "--all" in argv:
        return await verify_schema()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
