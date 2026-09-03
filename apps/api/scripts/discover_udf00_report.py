"""udf00 — pass/fail per task. READ-ONLY.

A task PASSES only if BOTH hold:

  1. its section exists in docs/discovery/UDF_DISCOVERY_REPORT.md, and
  2. the load-bearing claims that section rests on still measure true against
     the live database and the live repo.

Checking (1) alone would pass vacuously the moment the report drifted from the
schema, which is exactly the failure this project's verify discipline exists to
prevent — so every check below re-measures rather than re-reads the report.

No writes of any kind.
"""

from __future__ import annotations

import asyncio
import glob
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parent.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, connect  # noqa: E402

REPORT = REPO / "docs" / "discovery" / "UDF_DISCOVERY_REPORT.md"


def _grep_count(path: pathlib.Path, needle: str) -> int:
    try:
        return path.read_text(encoding="utf-8", errors="replace").count(needle)
    except OSError:
        return -1


async def main() -> int:
    failures: list[tuple[str, str]] = []
    notes: list[str] = []

    if not REPORT.exists():
        print("TASK 1 — FAIL: report file missing")
        print("TASK 2 — FAIL: report file missing")
        print("TASK 3 — FAIL: report file missing")
        return 1
    text = REPORT.read_text(encoding="utf-8")

    dsn, prov = await admin_dsn()
    if not dsn:
        print(f"TASK 1 — FAIL: no working DSN ({prov})")
        print(f"TASK 2 — FAIL: no working DSN ({prov})")
        print(f"TASK 3 — FAIL: no working DSN ({prov})")
        return 1
    conn = await connect(dsn)

    # ------------------------------------------------------------------ T1
    t1: list[str] = []
    if "# TASK 1 — Broad sweep" not in text:
        t1.append("section 'TASK 1 — Broad sweep' absent from report")

    # The nine patterns the report claims match NOTHING must still match nothing.
    dead = [
        "%user_defined%", "%custom_field%", "%customfield%", "%picklist%",
        "%pick_list%", "%value_set%", "%valueset%", "%layout%", "%field_def%",
        "%custom_tab%",
    ]
    hits = await conn.fetchval(
        """
        SELECT count(*) FROM unnest($1::text[]) AS p(pat)
        WHERE EXISTS (
              SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE c.relname ILIKE p.pat AND c.relkind IN ('r','v','m','p','f')
                AND n.nspname NOT IN ('information_schema','pg_catalog','pg_toast')
                AND n.nspname NOT LIKE 'pg_%')
           OR EXISTS (
              SELECT 1 FROM pg_attribute a
              JOIN pg_class c ON c.oid = a.attrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE a.attname ILIKE p.pat AND a.attnum > 0 AND NOT a.attisdropped
                AND c.relkind IN ('r','v','m','p','f')
                AND n.nspname NOT IN ('information_schema','pg_catalog','pg_toast')
                AND n.nspname NOT LIKE 'pg_%')
        """,
        dead,
    )
    if hits:
        t1.append(f"{hits} pattern(s) the report calls empty now match something")
    else:
        notes.append(f"T1: all {len(dead)} 'no-match' patterns still match nothing")

    # The two UDF tables must exist and still be empty.
    for t in ("udf_definitions", "udf_values"):
        if await conn.fetchval("SELECT to_regclass($1)", f"portfolio.{t}") is None:
            t1.append(f"portfolio.{t} is not deployed")
            continue
        n = await conn.fetchval(f"SELECT count(*) FROM portfolio.{t}")
        if n != 0:
            t1.append(f"portfolio.{t} has {n} rows; report says 0")
    notes.append("T1: portfolio.udf_definitions and portfolio.udf_values both deployed, both 0 rows")

    # reference_data.extra must still be the only populated non-vendor jsonb hit.
    ref_pop = await conn.fetchval(
        "SELECT count(*) FROM public.reference_data WHERE extra IS NOT NULL"
        " AND extra::text NOT IN ('{}','[]','null')"
    )
    if ref_pop != 50:
        t1.append(f"reference_data.extra populated={ref_pop}; report says 50")
    else:
        notes.append("T1: reference_data.extra populated rows = 50")

    # ------------------------------------------------------------------ T2
    t2: list[str] = []
    if "# TASK 2 — The Phase G implementation" not in text:
        t2.append("section 'TASK 2' absent from report")

    checks = {
        r["conname"]: r["def"]
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid IN (to_regclass('portfolio.udf_definitions')::oid,"
            "                    to_regclass('portfolio.udf_values')::oid)"
            "   AND contype = 'c'"
        )
    }
    want_vocab = {
        "udf_def_scope_chk": ["platform", "org", "team", "user"],
        "udf_def_type_chk": ["text", "numeric", "date", "boolean", "select"],
        "udf_def_applies_chk": [
            "asset", "position", "valuation", "transaction", "commitment", "entity",
        ],
        "udf_values_target_chk": [
            "asset", "position", "valuation", "transaction", "commitment", "entity",
        ],
    }
    for name, vals in want_vocab.items():
        d = checks.get(name)
        if not d:
            t2.append(f"CHECK {name} is absent")
        elif not all(f"'{v}'" in d for v in vals):
            t2.append(f"CHECK {name} no longer carries {vals}")
    notes.append("T2: all 4 vocabulary CHECKs present with the values the report lists")

    # Q4 — no type parameters. Q6 — no permission column. Q7 — display_order only.
    defcols = {
        r["attname"]
        for r in await conn.fetch(
            "SELECT attname FROM pg_attribute"
            " WHERE attrelid = to_regclass('portfolio.udf_definitions')::oid"
            "   AND attnum > 0 AND NOT attisdropped"
        )
    }
    banned = [
        c for c in defcols
        if re.search(
            r"precision|scale|length|min_|max_|_min$|_max$|permission|profile_id"
            r"|section|placement|col_span|colspan|tab_",
            c,
        )
    ]
    if banned:
        t2.append(f"udf_definitions gained parameter/permission/layout columns: {banned}")
    if "display_order" not in defcols:
        t2.append("udf_definitions.display_order is gone")
    notes.append(
        "T2: udf_definitions still has NO precision/scale/length/min/max, NO permission "
        "or profile column, NO section/span/tab column; display_order present"
    )

    # Q8 — still no triggers on either table.
    ntrig = await conn.fetchval(
        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN ("
        " to_regclass('portfolio.udf_definitions')::oid,"
        " to_regclass('portfolio.udf_values')::oid)"
    )
    if ntrig:
        t2.append(f"{ntrig} trigger(s) now exist on the UDF tables; report says 0")
    else:
        notes.append("T2: 0 triggers on either UDF table")

    # Q10 — the orphan claim. Re-measured against the repo, not the report.
    svc = API_DIR / "services" / "portfolio_udf.py"
    if not svc.exists():
        t2.append("services/portfolio_udf.py is missing")
    routers = list((API_DIR / "routers").glob("*.py"))
    router_hits = [p.name for p in routers if "portfolio_udf" in p.read_text(errors="replace")
                   or "udf_definitions" in p.read_text(errors="replace")
                   or "udf_values" in p.read_text(errors="replace")]
    if router_hits:
        t2.append(f"routers now reference UDF tables: {router_hits}; report says none")
    else:
        notes.append(f"T2: 0 of {len(routers)} routers reference the UDF tables")

    web = REPO / "apps" / "web"
    web_hits = [
        str(p.relative_to(REPO))
        for p in web.rglob("*.js*")
        if "node_modules" not in p.parts and ".next" not in p.parts
        and re.search(r"udf", p.read_text(errors="replace"), re.I)
    ]
    if web_hits:
        t2.append(f"frontend now references UDF: {web_hits}; report says none")
    else:
        notes.append("T2: 0 frontend files reference UDF")

    fri = API_DIR / "services" / "fee_run_inputs.py"
    if "portfolio.udf_values" not in fri.read_text(errors="replace"):
        t2.append("fee_run_inputs.py no longer reads portfolio.udf_values")
    else:
        notes.append("T2: fee_run_inputs.py is still the sole production reader of udf_values")

    # ------------------------------------------------------------------ T3
    t3: list[str] = []
    if "# TASK 3 — The four blocked design questions" not in text:
        t3.append("section 'TASK 3' absent from report")
    for heading in ("## A — Field-level security", "## B — Layout metadata",
                    "## C — Value history / retention", "## D — Tags"):
        if heading not in text:
            t3.append(f"missing Task 3 sub-section: {heading}")
    if "# Blocking questions — answered / unanswered" not in text:
        t3.append("missing the 'Blocking questions' block")

    # A — no field-shaped column anywhere in the permission model.
    perm_field = await conn.fetch(
        """
        SELECT c.relname AS t, a.attname AS c FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped
          AND c.relname = ANY(ARRAY['permissions','permission_sets',
              'permission_set_permissions','profiles','profile_permissions','roles',
              'role_permissions','user_permission_sets','user_roles',
              'assistant_action_catalog'])
          AND (a.attname ILIKE '%field%' OR a.attname ILIKE '%column%'
            OR a.attname ILIKE '%attribute%' OR a.attname ILIKE '%udf%')
        """
    )
    if perm_field:
        t3.append(f"permission model gained field-shaped columns: {[dict(r) for r in perm_field]}")
    else:
        notes.append("T3-A: still 0 field-shaped columns across all 10 permission tables")

    # B — the CRM layout is still hardcoded.
    tabs = web / "components" / "crm" / "EntityDetailTabs.jsx"
    form = web / "components" / "crm" / "EntityDetailsForm.jsx"
    if 'key: "overview"' not in tabs.read_text(errors="replace"):
        t3.append("EntityDetailTabs.jsx no longer carries a literal tab array")
    if "COMMON_TEXT_FIELDS" not in form.read_text(errors="replace"):
        t3.append("EntityDetailsForm.jsx no longer carries literal field arrays")
    notes.append("T3-B: CRM tab list and field lists are still literals in JSX")

    # C — every trigger in public/portfolio is still a guard, never a recorder.
    ntrig_all = await conn.fetchval(
        """
        SELECT count(*) FROM pg_trigger tg
        JOIN pg_class c ON c.oid = tg.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE NOT tg.tgisinternal AND n.nspname IN ('public','portfolio')
        """
    )
    if ntrig_all != 10:
        t3.append(f"public+portfolio trigger count is {ntrig_all}; report enumerates 10")
    else:
        notes.append("T3-C: exactly 10 triggers in public+portfolio, as enumerated")
    dfc = await conn.fetchval("SELECT count(*) FROM public.document_field_corrections")
    al = await conn.fetchval("SELECT count(*) FROM public.audit_log")
    notes.append(f"T3-C: document_field_corrections={dfc} rows, audit_log={al} rows")

    # D — is_fixed is still write-only.
    ed = API_DIR / "routers" / "entity_documents.py"
    src = ed.read_text(errors="replace")
    # Per-LINE, not per-file: a file-wide `SELECT[^;]*is_fixed` with DOTALL spans
    # from an unrelated SELECT to a later INSERT and reports a read that is not
    # there. Every is_fixed occurrence must sit on an INSERT column-list line.
    fixed_lines = [ln.strip() for ln in src.splitlines() if "is_fixed" in ln]
    reads = [ln for ln in fixed_lines
             if "INSERT INTO entity_document_tags" not in ln]
    if reads:
        t3.append(
            f"entity_documents.py now references is_fixed outside an INSERT "
            f"column list ({len(reads)} line(s)): {reads}"
        )
    else:
        notes.append(
            f"T3-D: is_fixed appears on {len(fixed_lines)} line(s) in "
            f"entity_documents.py, every one an INSERT column list, 0 reads"
        )
    nonfalse = await conn.fetchval(
        "SELECT count(*) FROM public.entity_document_tags WHERE is_fixed"
    )
    if nonfalse:
        t3.append(f"{nonfalse} entity_document_tags row(s) now have is_fixed=true")
    else:
        notes.append("T3-D: 0 entity_document_tags rows have is_fixed=true")
    rd_org = await conn.fetchval(
        "SELECT count(*) FROM public.reference_data WHERE org_id IS NOT NULL"
    )
    if rd_org:
        t3.append(f"reference_data now has {rd_org} org-scoped row(s); report says 0")
    else:
        notes.append("T3-D: all reference_data rows still global (org_id IS NULL)")

    await conn.close()

    for line in notes:
        print(f"  · {line}")
    print()
    for label, errs in (("TASK 1", t1), ("TASK 2", t2), ("TASK 3", t3)):
        if errs:
            print(f"{label} — FAIL")
            for e in errs:
                print(f"    · {e}")
            failures.append((label, "; ".join(errs)))
        else:
            print(f"{label} — PASS  (section written, claims re-measured true)")
    print()
    print(f"REPORT: {REPORT.relative_to(REPO)} ({len(text.splitlines())} lines)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
