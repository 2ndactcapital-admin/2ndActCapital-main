"""CASH08 — apply docs/cash08_part1.sql and prove it landed (payees, bank-detail controls, SLOAs; the S30 catalog).

    python3 apps/api/scripts/_apply_cash08.py --schema      # docs/cash08_part1.sql, one transaction
    python3 apps/api/scripts/_apply_cash08.py --verify      # read it back from the catalog
    python3 apps/api/scripts/_apply_cash08.py --all         # both (what the sprint ran)

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). Idempotent.
Never prints a secret. Writes platform catalog rows only (permissions, the SoD floor item types); no org row, no payee,
and never the cash.third_party_payments setting.
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

SCHEMA_SQL = REPO / "docs" / "cash08_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TABLES = ("public.cash_payees",)
CHECKS = {
    "public.cash_payees": {
        "cash_payees_last_four_chk": ("last_four", "[0-9]{4}"),
        "cash_payees_maker_checker_chk": ("approved_by", "maker_ids"),
        "cash_payees_bank_change_checker_chk": ("bank_change_requested_by", "callback_person", "callback_number",
                                                "callback_at"),
        "cash_payees_kind_chk": ("'vendor'::text", "'charity'::text", "'person'::text", "'outside_entity'::text"),
        "cash_payees_status_chk": ("'draft'::text", "'pending_approval'::text", "'active'::text",
                                   "'suspended'::text", "'retired'::text"),
    },
}
TRIGGERS = {"public.cash_payees": ("cash_payees_no_numbers_trg", "cash_payees_guard_trg")}
ITEM_TYPES = ("payee", "payee_bank_change", "first_payment_to_new_payee", "third_party_payments_setting")


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
        for t in TABLES:
            if await conn.fetchval("SELECT to_regclass($1)", t) is None:
                bad.append(f"{t}: absent")
        for table, checks in CHECKS.items():
            defs = {r["conname"]: r["d"] for r in await conn.fetch(
                "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = $1::regclass "
                "AND conname = ANY($2::text[])", table, list(checks))}
            for name, markers in checks.items():
                d = defs.get(name)
                if d is None:
                    bad.append(f"{name}: absent")
                    continue
                miss = [m for m in markers if m not in d]
                if miss:
                    bad.append(f"{name}: lacks {miss}")
        for table, names in TRIGGERS.items():
            have = {r["tgname"] for r in await conn.fetch(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = $1::regclass AND NOT tgisinternal", table)}
            miss = [n for n in names if n not in have]
            if miss:
                bad.append(f"{table}: triggers missing {miss}")
        cmds = {r["cmd"] for r in await conn.fetch(
            "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = 'cash_payees'")}
        if cmds != {"SELECT", "INSERT", "UPDATE", "DELETE"}:
            bad.append(f"cash_payees: RLS policies {sorted(cmds)}")
        for it_key in ITEM_TYPES:
            it = await conn.fetchrow("SELECT floor_class, self_approval_capable FROM public.sod_item_types "
                                     "WHERE item_type = $1", it_key)
            if it is None or it["floor_class"] != "transfer" or it["self_approval_capable"]:
                bad.append(f"sod_item_types {it_key}: {dict(it) if it else None}")
            kinds = {r["rule_kind"] for r in await conn.fetch(
                "SELECT rule_kind FROM public.sod_rules WHERE org_id IS NULL AND item_type = $1 "
                "AND status = 'active' AND valid_to IS NULL", it_key)}
            if kinds != {"floor", "policy"}:
                bad.append(f"sod_rules {it_key}: {sorted(kinds)}")
        perms = {r["name"] for r in await conn.fetch(
            "SELECT name FROM public.permissions WHERE name IN ('manage_payees', 'approve_payees')")}
        if perms != {"manage_payees", "approve_payees"}:
            bad.append(f"permissions: {sorted(perms)}")
        n = await conn.fetchval("SELECT count(*) FROM public.cash_payees")
        print(f"[INFO] cash_payees rows: {n} (the migration writes none)")
        on = await conn.fetchval("SELECT count(*) FROM public.org_settings WHERE setting_key = "
                                 "'cash.third_party_payments' AND setting_value = 'true'::jsonb")
        if on:
            bad.append(f"cash.third_party_payments is ON for {on} org(s) — it must be off")
    finally:
        await conn.close()
    print("SCHEMA OK" if not bad else f"SCHEMA PROBLEMS: {bad}")
    return 0 if not bad else 1


async def main(argv: list[str]) -> int:
    if "--schema" in argv or "--all" in argv:
        await apply_schema()
    rc = 0
    if "--verify" in argv or "--all" in argv:
        rc = await verify_schema()
    if not any(a in argv for a in ("--schema", "--verify", "--all")):
        print(__doc__)
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
