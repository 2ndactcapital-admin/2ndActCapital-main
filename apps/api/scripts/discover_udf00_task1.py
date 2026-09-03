"""udf00 Task 1 — broad ILIKE sweep for anything UDF-shaped. READ-ONLY.

Sweeps information_schema.tables and information_schema.columns for every
pattern the sprint names, in BOTH table names and column names, then for each
distinct matched relation reports:

  - schema, name, relkind (table / view / matview / foreign / partitioned)
  - exact count(*) (never reltuples)
  - relrowsecurity + the policy names on it
  - for views: whether reloptions carries security_invoker=true

Then a second sweep: every jsonb column whose NAME suggests it holds
user-defined values, with a real count of rows where it is non-null and
non-empty.

Emits JSON to /tmp/udf00_task1.json and a human summary to stdout. No writes.
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

PATTERNS = [
    "%udf%",
    "%user_defined%",
    "%custom_field%",
    "%customfield%",
    "%attribute%",
    "%picklist%",
    "%pick_list%",
    "%value_set%",
    "%valueset%",
    "%layout%",
    "%field_def%",
    "%definition%",
    "%tag%",
    "%custom_tab%",
    "%metadata%",
]

# jsonb columns whose name suggests user-defined values
JSONB_NAME_PATTERNS = [
    "udf_values",
    "custom_values",
    "attributes",
    "extra",
    "metadata",
    "data",
]

SKIP_SCHEMAS = (
    "information_schema",
    "pg_catalog",
    "pg_toast",
)


async def main() -> int:
    dsn, prov = await admin_dsn()
    if not dsn:
        print(f"FAIL: no working DSN ({prov})")
        return 1
    print(f"DSN resolved from {prov}")
    conn = await connect(dsn)
    out: dict = {"provenance": prov, "patterns": PATTERNS}

    out["db"] = dict(
        await conn.fetchrow(
            "SELECT current_database() AS db, current_user AS usr,"
            " (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
            " AS bypassrls"
        )
    )

    # ---------------- sweep 1: table-name matches ----------------
    table_hits = await conn.fetch(
        """
        SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS relkind,
               p.pat
        FROM unnest($1::text[]) AS p(pat)
        JOIN pg_class c ON c.relname ILIKE p.pat
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r','v','m','p','f')
          AND n.nspname NOT IN ('information_schema','pg_catalog','pg_toast')
          AND n.nspname NOT LIKE 'pg_%'
        ORDER BY n.nspname, c.relname, p.pat
        """,
        PATTERNS,
    )
    out["table_name_matches"] = [dict(r) for r in table_hits]

    # ---------------- sweep 2: column-name matches ----------------
    col_hits = await conn.fetch(
        """
        SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS relkind,
               a.attname AS column, format_type(a.atttypid, a.atttypmod) AS type,
               p.pat
        FROM unnest($1::text[]) AS p(pat)
        JOIN pg_attribute a ON a.attname ILIKE p.pat AND a.attnum > 0
                           AND NOT a.attisdropped
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r','v','m','p','f')
          AND n.nspname NOT IN ('information_schema','pg_catalog','pg_toast')
          AND n.nspname NOT LIKE 'pg_%'
        ORDER BY n.nspname, c.relname, a.attname, p.pat
        """,
        PATTERNS,
    )
    out["column_name_matches"] = [dict(r) for r in col_hits]

    # ---------------- per-relation detail for every distinct hit ----------------
    rels: dict[tuple[str, str], dict] = {}
    for r in table_hits:
        key = (r["schema"], r["name"])
        d = rels.setdefault(
            key,
            {
                "schema": r["schema"],
                "name": r["name"],
                "relkind": r["relkind"],
                "matched_on_table_name": [],
                "matched_on_column_name": [],
            },
        )
        d["matched_on_table_name"].append(r["pat"])
    for r in col_hits:
        key = (r["schema"], r["name"])
        d = rels.setdefault(
            key,
            {
                "schema": r["schema"],
                "name": r["name"],
                "relkind": r["relkind"],
                "matched_on_table_name": [],
                "matched_on_column_name": [],
            },
        )
        d["matched_on_column_name"].append(f"{r['column']} ({r['type']}) ~ {r['pat']}")

    for (schema, name), d in sorted(rels.items()):
        ident = f'"{schema}"."{name}"'
        try:
            d["exact_row_count"] = await conn.fetchval(f"SELECT count(*) FROM {ident}")
        except Exception as exc:  # noqa: BLE001
            d["exact_row_count"] = None
            d["count_error"] = f"{type(exc).__name__}: {exc}"
        meta = await conn.fetchrow(
            """
            SELECT c.relrowsecurity, c.relforcerowsecurity, c.reloptions::text[] AS opts,
                   pg_get_userbyid(c.relowner) AS owner
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = $1 AND c.relname = $2
            """,
            schema,
            name,
        )
        d["rls_enabled"] = meta["relrowsecurity"]
        d["rls_forced"] = meta["relforcerowsecurity"]
        d["reloptions"] = meta["opts"]
        d["owner"] = meta["owner"]
        d["security_invoker"] = (
            any(o.lower() == "security_invoker=true" for o in (meta["opts"] or []))
            if d["relkind"] in ("v", "m")
            else None
        )
        pols = await conn.fetch(
            "SELECT policyname, cmd, roles::text AS roles FROM pg_policies"
            " WHERE schemaname = $1 AND tablename = $2 ORDER BY policyname",
            schema,
            name,
        )
        d["policies"] = [dict(p) for p in pols]
    out["relations"] = [rels[k] for k in sorted(rels)]

    # ---------------- sweep 3: suggestive jsonb columns ----------------
    jsonb_cols = await conn.fetch(
        """
        SELECT n.nspname AS schema, c.relname AS name, c.relkind::text AS relkind,
               a.attname AS column, format_type(a.atttypid, a.atttypmod) AS type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) IN ('jsonb','json')
          AND c.relkind IN ('r','p')
          AND n.nspname NOT IN ('information_schema','pg_catalog','pg_toast')
          AND n.nspname NOT LIKE 'pg_%'
          AND a.attname = ANY($1::text[])
        ORDER BY n.nspname, c.relname, a.attname
        """,
        JSONB_NAME_PATTERNS,
    )
    jrows = []
    for r in jsonb_cols:
        ident = f'"{r["schema"]}"."{r["name"]}"'
        col = f'"{r["column"]}"'
        rec = dict(r)
        try:
            rec["total_rows"] = await conn.fetchval(f"SELECT count(*) FROM {ident}")
            rec["nonnull_nonempty_rows"] = await conn.fetchval(
                f"SELECT count(*) FROM {ident} WHERE {col} IS NOT NULL"
                f" AND {col}::text NOT IN ('{{}}','[]','null','\"\"')"
            )
            rec["sample"] = [
                str(v)[:400]
                for v in await conn.fetch(
                    f"SELECT {col} AS v FROM {ident} WHERE {col} IS NOT NULL"
                    f" AND {col}::text NOT IN ('{{}}','[]','null') LIMIT 3"
                )
            ]
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"
        jrows.append(rec)
    out["jsonb_value_columns"] = jrows

    # ---------------- full schema inventory (for orientation) ----------------
    out["all_schemas"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT n.nspname AS schema, count(*) FILTER (WHERE c.relkind IN ('r','p')) AS tables,
                   count(*) FILTER (WHERE c.relkind = 'v') AS views,
                   count(*) FILTER (WHERE c.relkind = 'm') AS matviews
            FROM pg_namespace n LEFT JOIN pg_class c ON c.relnamespace = n.oid
            WHERE n.nspname NOT IN ('information_schema','pg_catalog','pg_toast')
              AND n.nspname NOT LIKE 'pg_%'
            GROUP BY n.nspname ORDER BY n.nspname
            """
        )
    ]

    await conn.close()

    path = pathlib.Path("/tmp/udf00_task1.json")
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {path} ({path.stat().st_size} bytes)")

    print("\n=== SCHEMAS ===")
    for s in out["all_schemas"]:
        print(f"  {s['schema']}: {s['tables']} tables, {s['views']} views, {s['matviews']} matviews")

    print(f"\n=== {len(out['relations'])} DISTINCT MATCHED RELATIONS ===")
    for d in out["relations"]:
        kind = {"r": "table", "v": "view", "m": "matview", "p": "part-table", "f": "foreign"}[d["relkind"]]
        print(
            f"  {d['schema']}.{d['name']} [{kind}] rows={d['exact_row_count']}"
            f" rls={d['rls_enabled']} pols={len(d['policies'])}"
            f" secinv={d['security_invoker']}"
        )
        if d["matched_on_table_name"]:
            print(f"      name~ {sorted(set(d['matched_on_table_name']))}")
        if d["matched_on_column_name"]:
            for c in sorted(set(d["matched_on_column_name"])):
                print(f"      col~  {c}")

    print(f"\n=== {len(jrows)} SUGGESTIVE JSONB/JSON COLUMNS ===")
    for r in jrows:
        print(
            f"  {r['schema']}.{r['name']}.{r['column']} ({r['type']})"
            f" total={r.get('total_rows')} populated={r.get('nonnull_nonempty_rows')}"
            f" {r.get('error','')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
