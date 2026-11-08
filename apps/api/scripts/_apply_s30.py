"""S30 — apply the segregation-of-duties schema + platform catalog and prove it landed.

    python3 apps/api/scripts/_apply_s30.py --schema    # docs/s30_part1.sql, one transaction
    python3 apps/api/scripts/_apply_s30.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_s30.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner). Idempotent. Never prints a secret. Writes PLATFORM catalog rows only (org_id NULL) — no org row.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

SCHEMA_SQL = REPO / "docs" / "s30_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
NEW_TABLES = ("public.sod_duties", "public.sod_item_types", "public.sod_rules", "public.sod_decisions",
              "public.user_person_links")
GUARDS = {"public.sod_rules": "sod_rules_guard_trg", "public.sod_decisions": "sod_decisions_guard_trg"}
UNIQUES = ("sod_rules_active_uq", "sod_rules_draft_uq", "sod_rules_version_uq")
CHECKS = {
    "public.sod_item_types": ("sod_item_types_floor_chk", "sod_item_types_floor_selfapproval_chk",
                              "sod_item_types_duties_chk", "sod_item_types_pre_chk"),
    "public.sod_rules": ("sod_rules_scope_owner_chk", "sod_rules_kind_chk", "sod_rules_kind_owner_chk",
                         "sod_rules_floor_chk", "sod_rules_policy_shape_chk", "sod_rules_maker_checker_chk",
                         "sod_rules_decided_chk", "sod_rules_rejected_chk", "sod_rules_retires_chk"),
    "public.sod_decisions": ("sod_decisions_outcome_chk", "sod_decisions_selfapproval_chk"),
    "public.user_person_links": ("user_person_links_self_chk", "user_person_links_via_chk"),
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
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        for full in NEW_TABLES:
            schema, t = full.split(".")
            n = await conn.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_schema = $1 "
                                    "AND table_name = $2", schema, t)
            need(n > 0, f"{full} exists", f"{n} columns")
            need(await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full) is True,
                 f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            need(all("NULLIF(current_setting('app." in ((r["qual"] or "") + (r["with_check"] or "")) for r in pol),
                 f"{full}: every policy reads its GUC with NULLIF")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 "
                "AND table_name = $2 AND grantee = 'app_service'", schema, t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on {full}")
        for table, trg in GUARDS.items():
            n = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = $1::regclass AND tgname = $2 "
                                    "AND NOT tgisinternal", table, trg)
            need(n == 1, f"trigger {trg} on {table}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
        for name in UNIQUES:
            need("UNIQUE" in idx.get(name, ""), f"unique index {name}")
        for table, names in CHECKS.items():
            have = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass AND contype::text = 'c'", table)}
            for n in names:
                need(n in have, f"constraint {table}.{n}")
        types = await conn.fetchval("SELECT count(*) FROM public.sod_item_types")
        need(types >= 38, "sod_item_types seeded", f"{types} item types")
        missing = await conn.fetch(
            "SELECT t.item_type FROM public.sod_item_types t WHERE NOT EXISTS (SELECT 1 FROM public.sod_rules r "
            "WHERE r.org_id IS NULL AND r.item_type = t.item_type AND r.rule_kind IN ('floor','default') AND "
            "r.status = 'active' AND r.valid_to IS NULL) OR NOT EXISTS (SELECT 1 FROM public.sod_rules r WHERE "
            "r.org_id IS NULL AND r.item_type = t.item_type AND r.rule_kind = 'policy' AND r.status = 'active' "
            "AND r.valid_to IS NULL)")
        need(not missing, "every item type has an active platform rule and policy", str([m[0] for m in missing]))
        floors = await conn.fetchval("SELECT count(*) FROM public.sod_rules WHERE rule_kind = 'floor' "
                                     "AND status = 'active'")
        need(floors >= 8, "floor rules seeded", f"{floors}")
        org_rows = await conn.fetchval("SELECT count(*) FROM public.sod_rules WHERE org_id IS NOT NULL")
        print(f"[INFO] org rule rows: {org_rows}; decisions: "
              f"{await conn.fetchval('SELECT count(*) FROM public.sod_decisions')}; person links: "
              f"{await conn.fetchval('SELECT count(*) FROM public.user_person_links')}")
    finally:
        await conn.close()
    print(f"SCHEMA {'OK' if not gaps else 'GAPS: ' + ', '.join(gaps)}")
    return 1 if gaps else 0


def main() -> int:
    args = set(sys.argv[1:])
    if not args & {"--schema", "--verify", "--all"}:
        print(__doc__)
        return 2
    if args & {"--schema", "--all"}:
        asyncio.run(apply_schema())
    if args & {"--verify", "--all"}:
        return asyncio.run(verify_schema())
    return 0


if __name__ == "__main__":
    sys.exit(main())
