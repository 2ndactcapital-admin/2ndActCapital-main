"""udf00 Task 2 — full introspection of every genuinely UDF-related relation.

READ-ONLY. For each target: columns, PK, FKs both directions, uniques, CHECKs,
indexes (with access method, GIN flagged), triggers + trigger function source,
enum types used with values in sort order, up to 20 real rows, scope-column
distributions, org_id distributions.

Also inventories: every enum in portfolio/public, every jsonb column in
portfolio/public (broader than Task 1's name list), the full portfolio schema
table list, and every trigger in the database whose function body mentions
old/new value capture (feeds Task 3-C).

Emits JSON to /tmp/udf00_task2.json plus a human summary.
"""

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

TARGETS = [
    ("portfolio", "udf_definitions"),
    ("portfolio", "udf_values"),
    ("public", "entity_attributes"),
    ("public", "entity_document_tags"),
    ("public", "reference_data"),
]

SCOPE_HINTS = ("scope", "owner_scope", "scope_type", "level", "visibility", "tier")


async def describe(conn, schema: str, table: str) -> dict:
    d: dict = {"schema": schema, "table": table}
    oid = await conn.fetchval("SELECT to_regclass($1)::oid", f"{schema}.{table}")
    if not oid:
        d["deployed"] = False
        return d
    d["deployed"] = True
    d["oid"] = oid

    d["columns"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT a.attnum AS ord, a.attname AS name,
                   format_type(a.atttypid, a.atttypmod) AS type,
                   NOT a.attnotnull AS nullable,
                   pg_get_expr(ad.adbin, ad.adrelid) AS default_expr,
                   t.typtype::text AS typtype,
                   CASE WHEN t.typtype = 'e' THEN
                        (SELECT n2.nspname || '.' || t.typname
                         FROM pg_namespace n2 WHERE n2.oid = t.typnamespace)
                   END AS enum_type
            FROM pg_attribute a
            JOIN pg_type t ON t.oid = a.atttypid
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            oid,
        )
    ]

    d["constraints"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT conname AS name, contype::text AS kind,
                   pg_get_constraintdef(oid) AS definition
            FROM pg_constraint WHERE conrelid = $1
            ORDER BY contype, conname
            """,
            oid,
        )
    ]

    d["referenced_by"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname || '.' || c.relname AS from_table,
                   con.conname AS name, pg_get_constraintdef(con.oid) AS definition
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.confrelid = $1 AND con.contype = 'f'
            ORDER BY 1, 2
            """,
            oid,
        )
    ]

    d["indexes"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT i.relname AS name, am.amname AS method,
                   ix.indisunique AS is_unique, ix.indisprimary AS is_primary,
                   pg_get_indexdef(ix.indexrelid) AS definition
            FROM pg_index ix
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_am am ON am.oid = i.relam
            WHERE ix.indrelid = $1
            ORDER BY i.relname
            """,
            oid,
        )
    ]

    d["triggers"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT tg.tgname AS name, p.proname AS function,
                   n.nspname || '.' || p.proname AS qualified_function,
                   pg_get_triggerdef(tg.oid) AS definition,
                   pg_get_functiondef(p.oid) AS function_source
            FROM pg_trigger tg
            JOIN pg_proc p ON p.oid = tg.tgfoid
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE tg.tgrelid = $1 AND NOT tg.tgisinternal
            ORDER BY tg.tgname
            """,
            oid,
        )
    ]

    meta = await conn.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner) AS owner"
        " FROM pg_class WHERE oid = $1",
        oid,
    )
    d.update(dict(meta))
    d["policies"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT policyname, cmd, permissive, roles::text AS roles,
                   qual, with_check
            FROM pg_policies WHERE schemaname = $1 AND tablename = $2
            ORDER BY policyname
            """,
            schema,
            table,
        )
    ]

    ident = f'"{schema}"."{table}"'
    d["row_count"] = await conn.fetchval(f"SELECT count(*) FROM {ident}")
    d["sample"] = [
        {k: str(v)[:300] for k, v in dict(r).items()}
        for r in await conn.fetch(f"SELECT * FROM {ident} LIMIT 20")
    ]

    colnames = {c["name"] for c in d["columns"]}
    d["distributions"] = {}
    for c in sorted(colnames):
        if any(h in c.lower() for h in SCOPE_HINTS) or c == "org_id":
            try:
                d["distributions"][c] = [
                    dict(r)
                    for r in await conn.fetch(
                        f'SELECT "{c}"::text AS value, count(*) AS n FROM {ident}'
                        f' GROUP BY 1 ORDER BY 2 DESC, 1'
                    )
                ]
            except Exception as exc:  # noqa: BLE001
                d["distributions"][c] = f"ERROR {type(exc).__name__}: {exc}"
    return d


async def main() -> int:
    dsn, prov = await admin_dsn()
    if not dsn:
        print(f"FAIL: no working DSN ({prov})")
        return 1
    conn = await connect(dsn)
    out: dict = {"provenance": prov, "targets": {}}

    for schema, table in TARGETS:
        out["targets"][f"{schema}.{table}"] = await describe(conn, schema, table)

    # ---- every enum in portfolio/public, values in sort order ----
    out["enums"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, t.typname AS name,
                   array_agg(e.enumlabel ORDER BY e.enumsortorder)::text[] AS values
            FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE n.nspname IN ('public','portfolio')
            GROUP BY 1,2 ORDER BY 1,2
            """
        )
    ]

    # ---- full portfolio schema inventory ----
    out["portfolio_relations"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT c.relname AS name, c.relkind::text AS relkind
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'portfolio' AND c.relkind IN ('r','v','m','p')
            ORDER BY c.relkind, c.relname
            """
        )
    ]

    # ---- every jsonb/json column in public + portfolio (broader than Task 1) ----
    out["all_jsonb_columns"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, c.relname AS table, a.attname AS column
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) IN ('jsonb','json')
              AND c.relkind IN ('r','p') AND n.nspname IN ('public','portfolio')
            ORDER BY 1,2,3
            """
        )
    ]

    # ---- Task 3-C: every trigger in public/portfolio + its function source ----
    out["all_triggers"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, c.relname AS table, tg.tgname AS trigger,
                   pn.nspname || '.' || p.proname AS function,
                   pg_get_triggerdef(tg.oid) AS definition
            FROM pg_trigger tg
            JOIN pg_class c ON c.oid = tg.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_proc p ON p.oid = tg.tgfoid
            JOIN pg_namespace pn ON pn.oid = p.pronamespace
            WHERE NOT tg.tgisinternal AND n.nspname IN ('public','portfolio')
            ORDER BY 1,2,3
            """
        )
    ]

    # ---- Task 3-C: functions whose body mentions OLD/NEW value capture ----
    out["value_capture_functions"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, p.proname AS name,
                   left(p.prosrc, 4000) AS source
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname IN ('public','portfolio')
              AND p.prokind = 'f' AND p.prosrc IS NOT NULL
              AND (p.prosrc ILIKE '%old_value%'
                OR p.prosrc ILIKE '%new_value%'
                OR p.prosrc ILIKE '%OLD.%'
                OR p.prosrc ILIKE '%to_jsonb(OLD)%'
                OR p.prosrc ILIKE '%row_to_json(OLD)%')
            ORDER BY 1,2
            """
        )
    ]

    # ---- Task 3-C: candidate audit/history/journal tables ----
    out["audit_like_tables"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS relkind,
                   (SELECT array_agg(a.attname ORDER BY a.attnum)::text[]
                    FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped)
                   AS columns
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('public','portfolio') AND c.relkind IN ('r','p','v')
              AND (c.relname ILIKE '%audit%' OR c.relname ILIKE '%history%'
                OR c.relname ILIKE '%_log%' OR c.relname ILIKE 'log_%'
                OR c.relname ILIKE '%journal%' OR c.relname ILIKE '%event%'
                OR c.relname ILIKE '%version%' OR c.relname ILIKE '%activit%'
                OR c.relname ILIKE '%correction%' OR c.relname ILIKE '%change%')
            ORDER BY 1,2
            """
        )
    ]

    # ---- Task 3-A: SOC permission model tables ----
    out["permission_tables"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS relkind,
                   (SELECT array_agg(a.attname ORDER BY a.attnum)::text[]
                    FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped)
                   AS columns
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname IN ('public','portfolio') AND c.relkind IN ('r','p','v')
              AND (c.relname ILIKE '%permission%' OR c.relname ILIKE '%profile%'
                OR c.relname ILIKE '%role%' OR c.relname ILIKE '%grant%'
                OR c.relname ILIKE '%action%' OR c.relname ILIKE '%visib%'
                OR c.relname ILIKE '%restrict%' OR c.relname ILIKE '%sharing%')
            ORDER BY 1,2
            """
        )
    ]

    await conn.close()
    path = pathlib.Path("/tmp/udf00_task2.json")
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {path} ({path.stat().st_size} bytes)")

    for key, d in out["targets"].items():
        print(f"\n{'=' * 78}\n{key}  deployed={d.get('deployed')}\n{'=' * 78}")
        if not d.get("deployed"):
            continue
        print(f"  rows={d['row_count']} rls={d['relrowsecurity']} owner={d['owner']}")
        print("  COLUMNS")
        for c in d["columns"]:
            print(
                f"    {c['ord']:>2} {c['name']:<28} {c['type']:<32}"
                f" null={c['nullable']!s:<5} default={c['default_expr']}"
                + (f" ENUM={c['enum_type']}" if c["enum_type"] else "")
            )
        print("  CONSTRAINTS")
        for c in d["constraints"]:
            print(f"    [{c['kind']}] {c['name']}: {c['definition']}")
        print("  REFERENCED BY")
        for c in d["referenced_by"] or []:
            print(f"    {c['from_table']}.{c['name']}: {c['definition']}")
        if not d["referenced_by"]:
            print("    (nothing)")
        print("  INDEXES")
        for c in d["indexes"]:
            flag = "  <<< GIN" if c["method"] == "gin" else ""
            print(f"    [{c['method']}] {c['name']}{flag}: {c['definition']}")
        print("  TRIGGERS")
        for c in d["triggers"]:
            print(f"    {c['name']} -> {c['qualified_function']}")
            print(f"      {c['definition']}")
        if not d["triggers"]:
            print("    (none)")
        print("  POLICIES")
        for c in d["policies"]:
            print(f"    {c['policyname']} [{c['cmd']}] roles={c['roles']}")
            print(f"      USING {c['qual']}")
            print(f"      CHECK {c['with_check']}")
        print("  DISTRIBUTIONS")
        for col, dist in d["distributions"].items():
            print(f"    {col}: {dist}")
        print(f"  SAMPLE ({len(d['sample'])} rows)")
        for r in d["sample"]:
            print(f"    {r}")

    print(f"\n=== ENUMS in public/portfolio ({len(out['enums'])}) ===")
    for e in out["enums"]:
        print(f"  {e['schema']}.{e['name']}: {e['values']}")

    print(f"\n=== portfolio schema relations ({len(out['portfolio_relations'])}) ===")
    for r in out["portfolio_relations"]:
        print(f"  [{r['relkind']}] {r['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
