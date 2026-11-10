"""CASH06 — apply docs/cash06_part1.sql and prove it landed (cash rules, stage A).

    python3 apps/api/scripts/_apply_cash06.py --schema      # docs/cash06_part1.sql, one transaction
    python3 apps/api/scripts/_apply_cash06.py --verify      # read it back from the catalog
    python3 apps/api/scripts/_apply_cash06.py --all         # both (what the sprint ran)

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). Idempotent.
Never prints a secret. Writes platform catalog rows only (the SoD floor item type cash_rule and its two platform rules);
no org row, no rule, no payment.
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

SCHEMA_SQL = REPO / "docs" / "cash06_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TABLES = ("public.cash_rules", "public.cash_rule_firings")
CHECKS = {
    "public.cash_rules": {
        "cash_rules_maker_checker_chk": ("approved_by", "maker_ids"),
        "cash_rules_owner_maker_chk": ("owner_id", "maker_ids"),
        "cash_rules_stage_chk": ("'A'::text",),
        "cash_rules_type_chk": ("'distribute'::text", "'maintain_target'::text", "'recurring'::text",
                                "'redirect_incoming'::text"),
        "cash_rules_frequency_chk": ("'business_days'::text", "'on_incoming'::text"),
        "cash_rules_shape_chk": ("supporting_account_id",),
        "cash_rules_pending_action_chk": ("'resume'::text", "'activate'::text"),
        "cash_rules_resume_checker_chk": ("resume_requested_by",),
    },
    "public.cash_rule_firings": {
        "cash_rule_firings_outcome_chk": ("'firing'::text", "'held'::text", "'partly_held'::text"),
        "cash_rule_firings_period_chk": ("txn:",),
    },
    "public.payment_instructions": {
        "payment_instructions_purpose_ref_chk": ("'cash_rule'::text", "'cash_obligation'::text"),
    },
}
TRIGGERS = {
    "public.cash_rules": ("cash_rules_guard_trg", "cash_rules_same_owner_trg"),
    "public.cash_rule_firings": ("cash_rule_firings_guard_trg",),
}
UNIQUES = ("cash_rules_version_live_uq", "cash_rules_running_uq", "cash_rules_open_uq", "cash_rules_task_live_uq",
           "cash_rule_firings_period_uq")


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
        idx = {r["indexname"] for r in await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE indexname = ANY($1::text[]) AND indexdef LIKE 'CREATE UNIQUE%'",
            list(UNIQUES))}
        if idx != set(UNIQUES):
            bad.append(f"unique indexes missing {sorted(set(UNIQUES) - idx)}")
        for t in ("cash_rules", "cash_rule_firings"):
            cmds = {r["cmd"] for r in await conn.fetch(
                "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = $1", t)}
            if cmds != {"SELECT", "INSERT", "UPDATE", "DELETE"}:
                bad.append(f"{t}: RLS policies {sorted(cmds)}")
        it = await conn.fetchrow("SELECT floor_class, self_approval_capable, maker_duty, checker_duty FROM "
                                 "public.sod_item_types WHERE item_type = 'cash_rule'")
        if (it is None or it["floor_class"] != "transfer" or it["self_approval_capable"]
                or (it["maker_duty"], it["checker_duty"]) != ("initiate_payment", "approve_payment")):
            bad.append(f"sod_item_types cash_rule: {dict(it) if it else None}")
        kinds = {r["rule_kind"] for r in await conn.fetch(
            "SELECT rule_kind FROM public.sod_rules WHERE org_id IS NULL AND item_type = 'cash_rule' "
            "AND status = 'active' AND valid_to IS NULL")}
        if kinds != {"floor", "policy"}:
            bad.append(f"sod_rules cash_rule: {sorted(kinds)}")
        n = await conn.fetchval("SELECT count(*) FROM public.cash_rules")
        print(f"[INFO] cash_rules rows: {n} (the migration writes none)")
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
