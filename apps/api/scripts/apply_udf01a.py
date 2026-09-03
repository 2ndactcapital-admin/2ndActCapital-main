"""Apply migrations/udf01a_definitions_layer.sql and prove each object landed.

DDL success is not proof. Every object created by the migration is re-read from
pg_catalog / information_schema on a FRESH connection afterwards, and the
backfills are re-counted, before this script reports success.
"""

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect  # noqa: E402

MIGRATION = HERE.parent / "migrations" / "udf01a_definitions_layer.sql"

ZERO = "00000000-0000-0000-0000-000000000000"


async def already_applied(conn) -> bool:
    return await conn.fetchval("SELECT to_regclass('public.reference_data_lists') IS NOT NULL")


async def apply(conn) -> None:
    sql = MIGRATION.read_text()
    # The file carries its own BEGIN/COMMIT.
    await conn.execute(sql)


async def verify(conn) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []

    def check(ok, label, detail=""):
        out.append((bool(ok), f"{label}" + (f" — {detail}" if detail else "")))

    # 1a.1 columns
    cols = {
        r["column_name"]: r
        for r in await conn.fetch(
            "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema='portfolio' AND table_name='udf_definitions'"
        )
    }
    want = [
        "type_params", "api_name", "help_text", "description", "is_required",
        "default_value", "is_unique", "unique_case_sensitive", "is_external_id",
        "is_platform_managed", "value_set_id", "deleted_at", "deleted_by",
        "updated_at", "updated_by", "record_type_id", "controlling_definition_id",
    ]
    missing = [c for c in want if c not in cols]
    check(not missing, f"1a.1 all {len(want)} udf_definitions columns exist", f"missing={missing}")
    check(
        cols.get("type_params", {}).get("is_nullable") == "NO",
        "1a.1 type_params is NOT NULL with a default",
        str(cols.get("type_params", {}).get("column_default")),
    )

    # api_name unique index, with its real definition
    idx = await conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='portfolio' "
        "AND indexname='udf_def_api_name_uq'"
    )
    check(idx is not None, "1a.1 udf_def_api_name_uq exists")
    if idx:
        for token in ("owner_scope", "owner_scope_id", "applies_to", "api_name",
                      "deleted_at IS NULL", "system_to IS NULL", "valid_to IS NULL"):
            check(token in idx, f"1a.1 udf_def_api_name_uq keys/predicates on {token}")
        check("target_type" not in idx and "team_id" not in idx,
              "1a.1 udf_def_api_name_uq does NOT reference the prompt's phantom columns")

    # 1a.2 widened CHECK
    chk = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='udf_def_type_chk'"
    )
    new_types = ["long_text", "rich_text", "integer", "currency", "percent",
                 "datetime", "multiselect", "tags", "email", "url", "phone"]
    check(chk is not None, "1a.2 udf_def_type_chk exists")
    if chk:
        absent = [t for t in new_types if f"'{t}'" not in chk]
        check(not absent, "1a.2 CHECK carries all 16 data types", f"absent={absent}")

    # 1a.3 reference_data_lists
    check(await conn.fetchval("SELECT to_regclass('public.reference_data_lists') IS NOT NULL"),
          "1a.3 public.reference_data_lists exists")
    n_lists = await conn.fetchval("SELECT count(*) FROM public.reference_data_lists")
    check(n_lists == 11, "1a.3 backfilled exactly 11 platform list headers", f"got {n_lists}")
    n_plat = await conn.fetchval(
        "SELECT count(*) FROM public.reference_data_lists "
        "WHERE owner_scope='platform' AND org_id IS NULL")
    check(n_plat == 11, "1a.3 all 11 headers are platform-scope with NULL org_id", f"got {n_plat}")

    rls = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname='reference_data_lists'")
    check(rls, "1a.3 reference_data_lists has RLS ENABLED")
    n_pol = await conn.fetchval(
        "SELECT count(*) FROM pg_policies WHERE schemaname='public' "
        "AND tablename='reference_data_lists'")
    check(n_pol == 1, "1a.3 reference_data_lists carries its isolation policy", f"got {n_pol}")

    # list_id backfill — every one of the 155 rows linked
    unlinked = await conn.fetchval(
        "SELECT count(*) FROM public.reference_data WHERE list_id IS NULL")
    total = await conn.fetchval("SELECT count(*) FROM public.reference_data")
    check(total == 155 and unlinked == 0,
          "1a.3 all 155 reference_data rows backfilled to a list_id",
          f"total={total} unlinked={unlinked}")
    mismatched = await conn.fetchval(
        "SELECT count(*) FROM public.reference_data rd "
        "JOIN public.reference_data_lists l ON l.id = rd.list_id "
        "WHERE l.list_key <> rd.list_key")
    check(mismatched == 0, "1a.3 every list_id points at the row's OWN list_key",
          f"mismatched={mismatched}")

    # blocker-3 uniqueness: both old rules gone, new one present
    old_c = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname='reference_data_list_key_code_parent_code_key'")
    old_i = await conn.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
        "AND indexname='reference_data_org_list_code_uniq'")
    new_i = await conn.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
        "AND indexname='reference_data_scoped_uq'")
    check(old_c == 0, "1a.3 global (list_key,code,parent_code) constraint dropped")
    check(old_i == 0, "1a.3 reference_data_org_list_code_uniq ALSO dropped (blocker 3)")
    check(new_i == 1, "1a.3 reference_data_scoped_uq is the single operative rule")

    fk = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='udf_def_value_set_fk'")
    check(fk is not None and "reference_data_lists" in (fk or ""),
          "1a.3 udf_def_value_set_fk targets reference_data_lists", str(fk))

    # 1a.4 tags
    check(await conn.fetchval("SELECT to_regclass('portfolio.udf_tag_assignments') IS NOT NULL"),
          "1a.4 portfolio.udf_tag_assignments exists")
    trls = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='portfolio' AND c.relname='udf_tag_assignments'")
    check(trls, "1a.4 udf_tag_assignments has RLS ENABLED")
    for iname in ("udf_tag_assign_uq", "udf_tag_assign_lookup"):
        check(await conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='portfolio' AND indexname=$1",
            iname) == 1, f"1a.4 {iname} exists")

    # 1a.5 audit
    check(await conn.fetchval("SELECT to_regclass('portfolio.udf_definition_audit') IS NOT NULL"),
          "1a.5 portfolio.udf_definition_audit exists")
    arls = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='portfolio' AND c.relname='udf_definition_audit'")
    check(arls, "1a.5 udf_definition_audit has RLS ENABLED")

    # untouched by this migration
    check(await conn.fetchval("SELECT count(*) FROM portfolio.udf_definitions") == 0,
          "post: udf_definitions still empty")
    check(await conn.fetchval("SELECT count(*) FROM portfolio.udf_values") == 0,
          "post: udf_values still empty")
    return out


async def main() -> None:
    dsn, prov = await admin_dsn()
    if not dsn:
        print(f"FATAL: no admin DSN — {prov}")
        sys.exit(1)
    print(f"admin dsn <- {prov}")

    conn = await connect(dsn)
    try:
        if await already_applied(conn):
            print("migration already applied; verifying only")
        else:
            await apply(conn)
            print("migration applied")
    finally:
        await conn.close()

    # Fresh connection for verification.
    conn = await connect(dsn)
    try:
        results = await verify(conn)
    finally:
        await conn.close()

    for ok, label in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    n_fail = sum(1 for ok, _ in results if not ok)
    print(f"\n{len(results) - n_fail}/{len(results)} PASS, {n_fail} FAIL")
    sys.exit(1 if n_fail else 0)


asyncio.run(main())
