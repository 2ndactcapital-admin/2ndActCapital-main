"""udf00 Task 3 — the four blocked design questions, from the live schema.

READ-ONLY.

A  Field-level security: full structure + real contents of permissions,
   permission_sets, permission_set_permissions, profiles, profile_permissions,
   roles, role_permissions, assistant_action_catalog. Looks for ANY column that
   could carry a field name.
B  (repo-side; this script only reports DB-side layout/ordering columns)
C  Value history: row counts + population evidence for every candidate
   old/new-value journal, and confirmation of which have triggers.
D  Tags: entity_document_tags contents + vocabulary, entities.tags,
   deals.tags, and reference_data list_key inventory.

Plus: the two field-definition systems Task 1's patterns did NOT catch —
public.investment_profile_questions / investment_profile_answers and
portfolio.note_terms_field_registry — described in full, because they are
prior art for exactly the thing being designed.

Emits JSON to /tmp/udf00_task3.json plus a human summary.
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

PERM_TABLES = [
    "permissions",
    "permission_sets",
    "permission_set_permissions",
    "profiles",
    "profile_permissions",
    "roles",
    "role_permissions",
    "user_permission_sets",
    "user_roles",
    "assistant_action_catalog",
]

JOURNAL_CANDIDATES = [
    ("public", "audit_log"),
    ("public", "ownership_change_log"),
    ("public", "document_field_corrections"),
    ("public", "investment_stage_history"),
    ("public", "spv_status_history"),
    ("public", "restricted_access_audit"),
    ("public", "domain_events"),
    ("public", "assistant_activities"),
    ("public", "workflow_versions"),
    ("public", "notification_delivery_log"),
    ("public", "ai_decision_log"),
]

PRIOR_ART = [
    ("public", "investment_profile_questions"),
    ("public", "investment_profile_answers"),
    ("portfolio", "note_terms_field_registry"),
    ("public", "org_settings"),
]


async def cols(conn, schema, table):
    return [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
                   NOT a.attnotnull AS nullable,
                   pg_get_expr(ad.adbin, ad.adrelid) AS default_expr
            FROM pg_attribute a
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE a.attrelid = to_regclass($1)::oid AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            f"{schema}.{table}",
        )
    ]


async def constraints(conn, schema, table):
    return [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT conname AS name, contype::text AS kind,
                   pg_get_constraintdef(oid) AS definition
            FROM pg_constraint WHERE conrelid = to_regclass($1)::oid
            ORDER BY contype, conname
            """,
            f"{schema}.{table}",
        )
    ]


async def sample(conn, schema, table, n=25):
    return [
        {k: str(v)[:200] for k, v in dict(r).items()}
        for r in await conn.fetch(f'SELECT * FROM "{schema}"."{table}" LIMIT {n}')
    ]


async def main() -> int:
    dsn, prov = await admin_dsn()
    if not dsn:
        print(f"FAIL: no working DSN ({prov})")
        return 1
    conn = await connect(dsn)
    out: dict = {"provenance": prov}

    # ---------------- A — permission model ----------------
    a: dict = {}
    for t in PERM_TABLES:
        rec = {
            "columns": await cols(conn, "public", t),
            "constraints": await constraints(conn, "public", t),
            "row_count": await conn.fetchval(f'SELECT count(*) FROM public."{t}"'),
        }
        rec["sample"] = await sample(conn, "public", t, 30)
        a[t] = rec
    a["distinct_permission_keys"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT permission_key, count(*) AS n FROM (
              SELECT permission_key FROM public.permission_set_permissions
              UNION ALL
              SELECT permission_key FROM public.profile_permissions
            ) s GROUP BY 1 ORDER BY 1
            """
        )
    ]
    a["permissions_resource_action"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT resource, action, count(*) AS n FROM public.permissions"
            " GROUP BY 1,2 ORDER BY 1,2"
        )
    ]
    # any column anywhere that could carry a field name alongside a permission
    a["field_shaped_columns_in_perm_tables"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT c.relname AS table, a.attname AS column
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY($1::text[])
              AND a.attnum > 0 AND NOT a.attisdropped
              AND (a.attname ILIKE '%field%' OR a.attname ILIKE '%column%'
                OR a.attname ILIKE '%attribute%' OR a.attname ILIKE '%udf%')
            ORDER BY 1,2
            """,
            PERM_TABLES,
        )
    ]
    out["A_permissions"] = a

    # ---------------- C — journals ----------------
    c: dict = {}
    for schema, t in JOURNAL_CANDIDATES:
        c[f"{schema}.{t}"] = {
            "columns": [x["name"] for x in await cols(conn, schema, t)],
            "row_count": await conn.fetchval(f'SELECT count(*) FROM "{schema}"."{t}"'),
            "triggers": [
                r["tgname"]
                for r in await conn.fetch(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass($1)::oid"
                    " AND NOT tgisinternal",
                    f"{schema}.{t}",
                )
            ],
            "sample": await sample(conn, schema, t, 5),
        }
    # bi-temporal tables: the OTHER history mechanism in this DB
    c["bitemporal_tables"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, cl.relname AS table
            FROM pg_class cl JOIN pg_namespace n ON n.oid = cl.relnamespace
            WHERE cl.relkind IN ('r','p') AND n.nspname IN ('public','portfolio')
              AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = cl.oid
                          AND a.attname = 'valid_to' AND NOT a.attisdropped)
              AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = cl.oid
                          AND a.attname = 'system_to' AND NOT a.attisdropped)
            ORDER BY 1,2
            """
        )
    ]
    out["C_journals"] = c

    # ---------------- D — tags + reference_data ----------------
    d: dict = {}
    d["entity_document_tags_vocabulary"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT tag, is_fixed, count(*) AS n FROM public.entity_document_tags"
            " GROUP BY 1,2 ORDER BY 3 DESC, 1"
        )
    ]
    d["entities_tags"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT t AS tag, count(*) AS n FROM public.entities e,"
            " LATERAL unnest(coalesce(e.tags,'{}'::text[])) t GROUP BY 1 ORDER BY 2 DESC, 1"
        )
    ]
    d["deals_tags"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT t AS tag, count(*) AS n FROM public.deals dl,"
            " LATERAL unnest(coalesce(dl.tags,'{}'::text[])) t GROUP BY 1 ORDER BY 2 DESC, 1"
        )
    ]
    d["other_text_array_columns"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, cl.relname AS table, a.attname AS column
            FROM pg_attribute a
            JOIN pg_class cl ON cl.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = cl.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = 'text[]'
              AND cl.relkind IN ('r','p') AND n.nspname IN ('public','portfolio')
            ORDER BY 1,2,3
            """
        )
    ]
    d["reference_data_lists"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT list_key, count(*) AS n,
                   count(*) FILTER (WHERE org_id IS NOT NULL) AS org_scoped,
                   count(*) FILTER (WHERE extra IS NOT NULL) AS with_extra,
                   count(*) FILTER (WHERE parent_code IS NOT NULL) AS with_parent,
                   count(*) FILTER (WHERE NOT is_active) AS inactive
            FROM public.reference_data GROUP BY 1 ORDER BY 1
            """
        )
    ]
    d["config_table"] = {
        "exists": await conn.fetchval("SELECT to_regclass('public.config') IS NOT NULL"),
    }
    if d["config_table"]["exists"]:
        d["config_table"]["columns"] = [x["name"] for x in await cols(conn, "public", "config")]
        d["config_table"]["categories"] = [
            dict(r)
            for r in await conn.fetch(
                "SELECT category, count(*) AS n FROM public.config GROUP BY 1 ORDER BY 1"
            )
        ]
    out["D_tags"] = d

    # ---------------- prior art ----------------
    pa: dict = {}
    for schema, t in PRIOR_ART:
        pa[f"{schema}.{t}"] = {
            "columns": await cols(conn, schema, t),
            "constraints": await constraints(conn, schema, t),
            "row_count": await conn.fetchval(f'SELECT count(*) FROM "{schema}"."{t}"'),
            "sample": await sample(conn, schema, t, 12),
            "policies": [
                dict(r)
                for r in await conn.fetch(
                    "SELECT policyname, cmd, qual FROM pg_policies"
                    " WHERE schemaname=$1 AND tablename=$2 ORDER BY policyname",
                    schema,
                    t,
                )
            ],
        }
    out["PRIOR_ART"] = pa

    await conn.close()
    path = pathlib.Path("/tmp/udf00_task3.json")
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {path} ({path.stat().st_size} bytes)")

    print("\n===== A — PERMISSION MODEL =====")
    for t in PERM_TABLES:
        r = a[t]
        print(f"\n  public.{t}  rows={r['row_count']}")
        print(f"    cols: {[x['name'] for x in r['columns']]}")
        for cc in r["constraints"]:
            print(f"    [{cc['kind']}] {cc['name']}: {cc['definition']}")
    print(f"\n  field-shaped columns in perm tables: {a['field_shaped_columns_in_perm_tables']}")
    print(f"\n  permissions resource/action ({len(a['permissions_resource_action'])}):")
    for r in a["permissions_resource_action"]:
        print(f"    {r['resource']} / {r['action']} x{r['n']}")
    print(f"\n  distinct permission_key granted ({len(a['distinct_permission_keys'])}):")
    for r in a["distinct_permission_keys"]:
        print(f"    {r['permission_key']} x{r['n']}")

    print("\n===== C — JOURNAL CANDIDATES =====")
    for k, v in c.items():
        if k == "bitemporal_tables":
            continue
        print(f"  {k}: rows={v['row_count']} triggers={v['triggers']}")
        print(f"    cols: {v['columns']}")
    print(f"\n  bi-temporal (valid_to + system_to) tables: {len(c['bitemporal_tables'])}")
    print(f"    {[x['schema'] + '.' + x['table'] for x in c['bitemporal_tables']]}")

    print("\n===== D — TAGS / REFERENCE DATA =====")
    print(f"  entity_document_tags vocabulary: {d['entity_document_tags_vocabulary']}")
    print(f"  entities.tags: {d['entities_tags']}")
    print(f"  deals.tags: {d['deals_tags']}")
    print(f"  other text[] columns: {[x['schema']+'.'+x['table']+'.'+x['column'] for x in d['other_text_array_columns']]}")
    print("  reference_data lists:")
    for r in d["reference_data_lists"]:
        print(
            f"    {r['list_key']:<28} n={r['n']:<4} org_scoped={r['org_scoped']}"
            f" with_extra={r['with_extra']} with_parent={r['with_parent']} inactive={r['inactive']}"
        )
    print(f"  config table: {d['config_table']}")

    print("\n===== PRIOR ART =====")
    for k, v in pa.items():
        print(f"\n  {k}  rows={v['row_count']}")
        for cc in v["columns"]:
            print(f"    {cc['name']:<26} {cc['type']:<28} null={cc['nullable']} def={cc['default_expr']}")
        for cc in v["constraints"]:
            print(f"    [{cc['kind']}] {cc['name']}: {cc['definition']}")
        for s in v["sample"][:6]:
            print(f"    ROW {s}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
