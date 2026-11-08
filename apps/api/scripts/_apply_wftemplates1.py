"""WFTEMPLATES1 — apply the workflow template library schema and prove it landed.

    python3 apps/api/scripts/_apply_wftemplates1.py --schema    # docs/wftemplates1_part1.sql, one transaction
    python3 apps/api/scripts/_apply_wftemplates1.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_wftemplates1.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner). Idempotent. Never prints a secret. THE MIGRATION WRITES NO ROWS: --verify prints each new table's row count
and the real Hollisworks platform org's template count.
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

SCHEMA_SQL = REPO / "docs" / "wftemplates1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
PLATFORM_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

NEW_TABLES = ("public.workflow_templates", "public.workflow_template_versions", "public.workflow_template_copies")
GUARDS = {"public.workflow_templates": "workflow_templates_guard_trg",
          "public.workflow_template_versions": "workflow_template_versions_guard_trg",
          "public.workflow_template_copies": "workflow_template_copies_guard_trg"}
UNIQUES = ("workflow_templates_key_live_uq", "workflow_template_versions_no_uq", "workflow_template_versions_draft_uq",
           "workflow_template_copies_definition_uq")
CHECKS = {
    "public.workflow_templates": ("workflow_templates_scope_chk", "workflow_templates_key_chk"),
    "public.workflow_template_versions": ("wtv_scope_chk", "wtv_status_chk", "wtv_category_chk", "wtv_maker_checker_chk",
                                          "wtv_published_chk", "wtv_org_no_draft_chk", "wtv_rejected_chk",
                                          "wtv_retired_chk", "wtv_archive_chk", "wtv_sha_chk", "wtv_steps_chk"),
    "public.workflow_template_copies": ("wtc_values_chk", "wtc_version_no_chk"),
}
DEFINER_FUNCTIONS = ("workflow_template_platform_org_allowed", "workflow_template_visible",
                     "workflow_template_published", "workflow_template_usage")


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
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2",
                schema, t)]
            need(bool(cols), f"{full} exists", f"{len(cols)} columns")
            need(await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full) is True,
                 f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            need(all("NULLIF(current_setting('app.current_org_id'" in ((r["qual"] or "") + (r["with_check"] or ""))
                     for r in pol), f"{full}: every policy reads the org GUC with NULLIF")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 "
                "AND table_name = $2 AND grantee = 'app_service'", schema, t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on {full}")
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND "
            "table_name = 'workflow_template_versions'")}
        need({"valid_from", "valid_to", "system_from", "system_to", "archived_from_id"} <= cols,
             "workflow_template_versions carries both temporal axes and archived_from_id")
        for table, trg in GUARDS.items():
            n = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = $1::regclass AND tgname = $2 "
                                    "AND NOT tgisinternal", table, trg)
            need(n == 1, f"trigger {trg} on {table}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
        for name in UNIQUES:
            need("UNIQUE" in idx.get(name, ""), f"unique index {name}", idx.get(name, "")[:120])
        for table, names in CHECKS.items():
            have = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass AND contype::text = 'c'", table)}
            for n in names:
                need(n in have, f"constraint {table}.{n}")
        fns = {r["proname"]: r["prosecdef"] for r in await conn.fetch(
            "SELECT p.proname, p.prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = ANY($1::text[])", list(DEFINER_FUNCTIONS))}
        for f in DEFINER_FUNCTIONS:
            need(fns.get(f) is True, f"SECURITY DEFINER public.{f}")
        fk = await conn.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE contype::text = 'f' AND conrelid = ANY($1::regclass[]) AND "
            "confrelid IN ('public.workflow_definitions'::regclass, 'public.workflow_versions'::regclass)",
            list(NEW_TABLES))
        need(fk == 0, "no FK from the template tables to workflow_definitions / workflow_versions (provenance only; "
                      "the never-run delete path keeps working)")
        n_real = await conn.fetchval("SELECT count(*) FROM public.workflow_templates WHERE owner_org_id = $1::uuid",
                                     PLATFORM_ORG_ID)
        print(f"[INFO] the real platform org owns {n_real} template row(s) (the migration writes none; the operator "
              "loads the catalog)")
        for full in NEW_TABLES:
            print(f"[INFO] {full}: {await conn.fetchval(f'SELECT count(*) FROM {full}')} row(s)")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
        return asyncio.run(verify_schema())
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--verify" in args:
        return asyncio.run(verify_schema())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
