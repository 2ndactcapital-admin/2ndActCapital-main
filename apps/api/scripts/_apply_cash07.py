"""CASH07 — apply docs/cash07_part1.sql and prove it landed (payment instructions, stage A).

    python3 apps/api/scripts/_apply_cash07.py --schema      # docs/cash07_part1.sql, one transaction
    python3 apps/api/scripts/_apply_cash07.py --verify      # read it back from the catalog
    python3 apps/api/scripts/_apply_cash07.py --all         # both (what the sprint ran)

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). Idempotent.
Never prints a secret. Writes platform catalog rows only (permissions, the SoD floor item type, custodian capability
versions); no org row, no payment.
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

SCHEMA_SQL = REPO / "docs" / "cash07_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TABLES = ("public.payment_instructions", "public.payment_events")
CHECKS = {
    "public.payment_instructions": {
        "payment_instructions_maker_checker_chk": ("approved_by", "maker_ids"),
        "payment_instructions_rail_chk": ("'ach'::text", "'wire'::text", "'journal'::text", "'check'::text"),
        "payment_instructions_currency_chk": ("'USD'::text",),
        "payment_instructions_reconciler_chk": ("reconciled_by", "sent_by"),
        "payment_instructions_returned_chk": ("return_code",),
        "payment_instructions_provisional_chk": ("provisional_until",),
    },
    "public.payment_events": {
        "payment_events_human_chk": ("'expire'::text", "'unmatched_alert'::text"),
    },
    "public.custodian_capabilities": {
        "custodian_capabilities_key_chk": ("'ach'::text", "'payment_status_feed'::text", "'payment_sheet'::text"),
    },
}
TRIGGERS = {
    "public.payment_instructions": ("payment_instructions_guard_trg", "payment_instructions_same_owner_trg",
                                    "payment_instructions_event_trg"),
    "public.payment_events": ("payment_events_immutable_trg",),
}


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
        for t in ("payment_instructions", "payment_events"):
            cmds = {r["cmd"] for r in await conn.fetch(
                "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = $1", t)}
            if cmds != {"SELECT", "INSERT", "UPDATE", "DELETE"}:
                bad.append(f"{t}: RLS policies {sorted(cmds)}")
        it = await conn.fetchrow("SELECT floor_class, self_approval_capable FROM public.sod_item_types "
                                 "WHERE item_type = 'payment_instruction'")
        if it is None or it["floor_class"] != "transfer" or it["self_approval_capable"]:
            bad.append(f"sod_item_types payment_instruction: {dict(it) if it else None}")
        kinds = {r["rule_kind"] for r in await conn.fetch(
            "SELECT rule_kind FROM public.sod_rules WHERE org_id IS NULL AND item_type = 'payment_instruction' "
            "AND status = 'active' AND valid_to IS NULL")}
        if kinds != {"floor", "policy"}:
            bad.append(f"sod_rules payment_instruction: {sorted(kinds)}")
        perms = {r["name"] for r in await conn.fetch(
            "SELECT name FROM public.permissions WHERE name IN ('initiate_payment', 'approve_payment')")}
        if perms != {"initiate_payment", "approve_payment"}:
            bad.append(f"permissions: {sorted(perms)}")
        caps = await conn.fetchval(
            "SELECT count(*) FROM public.custodian_capabilities WHERE capability_key IN ('ach', 'wire', 'journal', "
            "'check', 'payment_status_feed', 'payment_sheet') AND valid_to IS NULL AND system_to IS NULL")
        if int(caps) < 11:
            bad.append(f"custodian capabilities for payments: {caps}")
        n = await conn.fetchval("SELECT count(*) FROM public.payment_instructions")
        print(f"[INFO] payment_instructions rows: {n} (the migration writes none)")
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
