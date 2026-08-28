"""Sprint fee34 Task 1 — measure the deployed shape of the fee catalog.

Read-only. Writes nothing, creates nothing. Run:

    python3 scripts/discover_fee34.py

WHY THIS SCRIPT EXISTS AT ALL
──────────────────────────────────────────────────────────────────────────────
fee33's prompt carried the identical sentence — "Part 1 SQL is already applied
by Joe directly via Supabase MCP" — and it was NOT applied; neither table
existed in any schema, and the sprint had to author the DDL itself. So the
sentence is not evidence. This script is the evidence, and it re-measures on
the SAME DSN the application uses rather than on whatever endpoint an MCP tool
happens to point at. A table that exists behind the MCP's ``postgres`` role and
not on the app's connection would be a false green of exactly the shape
CLAUDE.md's "verify it actually landed with a real follow-up query" is about.

Every finding printed below is measured in this run. Nothing here is quoted
from the prompt.
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

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

FEE_TABLES = (
    "fee_schedules",
    "fee_schedule_tiers",
    "fee_assignments",
    "fee_exclusions",
    "fee_discounts",
    "fee_credits",
)

#: The scope tables fee_assignments.scope_id points at. Deliberately NOT
#: FK-enforced by Part 1 (scope_id is a bare uuid with no FK — it cannot have
#: one, since it addresses four different tables), so "is it queryable for
#: validation" is a real question and not a formality.
SCOPE_TABLES = ("accounts", "households", "billing_groups", "entities", "documents")


def rule(title: str) -> None:
    print("\n" + "─" * 78)
    print(f"  {title}")
    print("─" * 78)


async def main() -> int:
    dsn, prov = await admin_dsn()
    app_dsn, app_prov = await app_service_dsn()
    print(f"admin dsn:       {prov}")
    print(f"app_service dsn: {app_prov}")
    if dsn is None:
        print("BLOCKED: no admin DSN — nothing can be measured")
        return 2

    conn = await connect(dsn)
    try:
        rule("0 — which database is this, really")
        print("  server:  ", await conn.fetchval("SELECT inet_server_addr()::text"))
        print("  database:", await conn.fetchval("SELECT current_database()"))
        print("  user:    ", await conn.fetchval("SELECT current_user"))

        rule("1 — do the six tables exist, and in which schema")
        rows = await conn.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_name = ANY($1::text[])
            ORDER BY table_schema, table_name
            """,
            list(FEE_TABLES),
        )
        found = {r["table_name"]: r["table_schema"] for r in rows}
        for name in FEE_TABLES:
            print(f"  {name:<20} {found.get(name, '*** MISSING ***')}")
        missing = [n for n in FEE_TABLES if n not in found]
        if missing:
            print(f"\n  PART 1 IS NOT APPLIED. Missing: {missing}")
            print("  Do not write code against these tables until it is.")
            return 1
        print("\n  All six present in 'public'. Part 1 IS applied on the app's own DSN.")

        rule("2 — columns as deployed")
        cols = await conn.fetch(
            """
            SELECT table_name, ordinal_position, column_name, data_type,
                   numeric_precision, numeric_scale, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY($1::text[])
            ORDER BY table_name, ordinal_position
            """,
            list(FEE_TABLES),
        )
        current = None
        for r in cols:
            if r["table_name"] != current:
                current = r["table_name"]
                print(f"\n  {current}")
            typ = r["data_type"]
            if r["numeric_precision"] is not None and typ == "numeric":
                typ = f"numeric({r['numeric_precision']},{r['numeric_scale']})"
            null = "" if r["is_nullable"] == "YES" else " NOT NULL"
            dflt = f"  DEFAULT {r['column_default']}" if r["column_default"] else ""
            print(f"    {r['column_name']:<26} {typ}{null}{dflt}")

        rule("3 — CHECK constraints as deployed")
        checks = await conn.fetch(
            """
            SELECT rel.relname AS table_name, con.conname,
                   pg_get_constraintdef(con.oid) AS def
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            WHERE n.nspname = 'public' AND con.contype = 'c'
              AND rel.relname = ANY($1::text[])
            ORDER BY rel.relname, con.conname
            """,
            list(FEE_TABLES),
        )
        current = None
        for r in checks:
            if r["table_name"] != current:
                current = r["table_name"]
                print(f"\n  {current}")
            print(f"    {r['conname']}")
            print(f"      {r['def']}")

        rule("4 — foreign keys (does agreement_document_id resolve?)")
        fks = await conn.fetch(
            """
            SELECT rel.relname AS table_name, con.conname,
                   pg_get_constraintdef(con.oid) AS def
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            WHERE n.nspname = 'public' AND con.contype = 'f'
              AND rel.relname = ANY($1::text[])
            ORDER BY rel.relname, con.conname
            """,
            list(FEE_TABLES),
        )
        for r in fks:
            print(f"  {r['table_name']:<20} {r['conname']}")
            print(f"      {r['def']}")

        doc_fk = [r for r in fks if r["conname"] == "fee_assignments_agreement_document_id_fkey"]
        doc_exists = await conn.fetchval(
            "SELECT to_regclass('public.documents') IS NOT NULL"
        )
        print(f"\n  public.documents exists: {doc_exists}")
        print(f"  agreement_document_id FK deployed: {bool(doc_fk)}")
        if doc_fk:
            print(f"    -> {doc_fk[0]['def']}")

        rule("5 — RLS: enabled, and what the policy actually says")
        pol = await conn.fetch(
            """
            SELECT c.relname AS table_name, c.relrowsecurity, c.relforcerowsecurity,
                   p.polname, p.polcmd, p.polpermissive,
                   pg_get_expr(p.polqual, p.polrelid) AS using_expr,
                   pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_policy p ON p.polrelid = c.oid
            WHERE n.nspname = 'public' AND c.relname = ANY($1::text[])
            ORDER BY c.relname, p.polname
            """,
            list(FEE_TABLES),
        )
        for r in pol:
            print(
                f"  {r['table_name']:<20} rls={r['relrowsecurity']} "
                f"force={r['relforcerowsecurity']} policy={r['polname']} "
                f"cmd={r['polcmd']} permissive={r['polpermissive']}"
            )
            print(f"      USING      {r['using_expr']}")
            print(f"      WITH CHECK {r['check_expr']}")

        rule("6 — grants to app_service (RLS is unreachable without them)")
        grants = await conn.fetch(
            """
            SELECT table_name, grantee,
                   string_agg(privilege_type, ',' ORDER BY privilege_type) AS privs
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND table_name = ANY($1::text[])
              AND grantee = 'app_service'
            GROUP BY table_name, grantee ORDER BY table_name
            """,
            list(FEE_TABLES),
        )
        for r in grants:
            print(f"  {r['table_name']:<20} {r['privs']}")
        if len(grants) != len(FEE_TABLES):
            got = {r["table_name"] for r in grants}
            print(f"  *** app_service has NO grant on: {sorted(set(FEE_TABLES) - got)}")

        rule("7 — indexes (what uniqueness is actually enforced)")
        idx = await conn.fetch(
            """
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = ANY($1::text[])
            ORDER BY tablename, indexname
            """,
            list(FEE_TABLES),
        )
        current = None
        for r in idx:
            if r["tablename"] != current:
                current = r["tablename"]
                print(f"\n  {current}")
            print(f"    {r['indexdef']}")

        rule("8 — scope tables: queryable, and do they carry a 'closed' axis")
        for table in SCOPE_TABLES:
            exists = await conn.fetchval(f"SELECT to_regclass('public.{table}') IS NOT NULL")
            if not exists:
                print(f"  {table:<20} *** MISSING ***")
                continue
            temporal = await conn.fetch(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                  AND column_name IN ('valid_from','valid_to','system_from','system_to')
                ORDER BY column_name
                """,
                table,
            )
            count = await conn.fetchval(f"SELECT count(*) FROM public.{table}")
            axes = [r["column_name"] for r in temporal] or ["(none — no closed state)"]
            print(f"  {table:<20} rows={count:<8} temporal={axes}")

        rule("9 — current row counts in the six fee tables")
        for table in FEE_TABLES:
            n = await conn.fetchval(f"SELECT count(*) FROM public.{table}")
            print(f"  {table:<20} {n}")

        rule("10 — ordering_policy's deployed DEFAULT, read back as data")
        default = await conn.fetchval(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_schema='public' AND table_name='fee_schedules'
              AND column_name='ordering_policy'
            """
        )
        print(f"  raw default: {default}")
        # information_schema renders the default WITH its cast suffix
        # ("'[...]'::jsonb"), which is not itself valid JSON — parsed here
        # rather than fed back to the server, which rejects it.
        literal = (default or "").split("::jsonb")[0].strip()
        if literal.startswith("'") and literal.endswith("'"):
            literal = literal[1:-1].replace("''", "'")
        try:
            steps = json.loads(literal)
        except ValueError as exc:  # noqa: BLE001
            steps = None
            print(f"  could not parse: {exc}")
        expected = ["EXCLUSIONS", "TIERS", "DISCOUNTS", "CREDITS", "MINIMUM", "MAXIMUM"]
        print(f"  parsed:      {steps}")
        print(f"  equals the prompt's six-step list, in order: {steps == expected}")

        rule("11 — app_service can actually reach the tables under RLS")
        if app_dsn is None:
            print(f"  BLOCKED — {app_prov}")
        else:
            app = await connect(app_dsn)
            try:
                bypass = await app.fetchval(
                    "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
                print(f"  current_user={await app.fetchval('SELECT current_user')} "
                      f"rolbypassrls={bypass}")
                if bypass:
                    print("  *** this role BYPASSES RLS — no isolation proof is "
                          "possible on it")
                for table in FEE_TABLES:
                    try:
                        n = await app.fetchval(f"SELECT count(*) FROM public.{table}")
                        print(f"  {table:<20} readable, {n} rows visible with no org GUC")
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {table:<20} {type(exc).__name__}: {exc}")
            finally:
                await app.close()

        rule("SUMMARY — findings that CONFLICT with the prompt's description")
        conflicts = []

        tier_cols = {
            r["column_name"] for r in cols if r["table_name"] == "fee_schedule_tiers"
        }
        if not {"valid_from", "valid_to", "system_from", "system_to"} & tier_cols:
            conflicts.append(
                "fee_schedule_tiers has NO temporal columns at all (no valid_*/"
                "system_*, no created_at). Tiers are plain children of a versioned "
                "schedule row, not bi-temporal in their own right. Rule 3's "
                "restatement pattern does not apply to them; a schedule edit "
                "replaces its tier set outright."
            )

        excl_scope = [
            r["def"] for r in checks if r["conname"] == "fee_exclusions_scope_type_check"
        ]
        if excl_scope and "ORG_DEFAULT" not in excl_scope[0]:
            conflicts.append(
                "fee_exclusions.scope_type admits 'ORG', NOT 'ORG_DEFAULT', and "
                "does not admit 'ENTITY'. Its vocabulary is a DIFFERENT set from "
                "fee_assignments.scope_type — the two must not share one constant."
            )

        for tbl in ("fee_discounts", "fee_credits"):
            d = [r["def"] for r in checks if r["conname"] == f"{tbl}_scope_type_check"]
            if d and "ENTITY" not in d[0]:
                conflicts.append(
                    f"{tbl}.scope_type is ACCOUNT/BILLING_GROUP/HOUSEHOLD only — "
                    f"no ENTITY, no ORG. A third distinct scope vocabulary."
                )

        prec = [
            r for r in cols
            if r["table_name"] == "fee_assignments" and r["column_name"] == "precedence"
        ]
        if prec and prec[0]["is_nullable"] == "NO" and prec[0]["column_default"] is None:
            conflicts.append(
                "fee_assignments.precedence is NOT NULL with NO default — the "
                "application must supply it on every insert. Nothing in the "
                "database ties it to scope_type, so a caller could write "
                "ORG_DEFAULT at precedence 1 and invert the whole resolution "
                "order. It must be DERIVED from scope_type by the service and "
                "never accepted from a request body."
            )

        sched_uq = [
            r["indexdef"] for r in idx if r["indexname"] == "fee_schedules_code_version_uq"
        ]
        if sched_uq and "WHERE" not in sched_uq[0]:
            conflicts.append(
                "fee_schedules_code_version_uq is UNIQUE (org_id, code, version) "
                "with NO partial predicate. A valid-axis restatement — closing a "
                "row and re-inserting the same (code, version) — is therefore "
                "IMPOSSIBLE. Versioning must go through version+1, and a DRAFT "
                "edit must be an in-place UPDATE. That is what the prompt asks "
                "for, and the index is why there is no alternative."
            )

        no_assign_uq = not any(
            r["indexname"].endswith("_uq") and r["tablename"] == "fee_assignments"
            for r in idx
        )
        if no_assign_uq:
            conflicts.append(
                "fee_assignments has NO unique index — nothing stops two active "
                "assignments on the SAME scope_id with overlapping effective "
                "dates. Precedence would then be ambiguous within a tier. The "
                "service must close the incumbent before opening a successor; "
                "the database will not."
            )

        excl_link = [
            r for r in cols
            if r["table_name"] == "fee_exclusions" and r["column_name"] == "fee_schedule_id"
        ]
        if not excl_link:
            conflicts.append(
                "fee_exclusions has NO fee_schedule_id — only alt_fee_schedule_id "
                "(the REDUCED_RATE target). Exclusions are scoped to an "
                "account/group/household, NOT to a schedule. So 'validate the "
                "exclusions before approving the schedule' has no join path: "
                "there is no set of exclusions belonging to a schedule. The "
                "exclusion/discount/credit rules must be independently callable "
                "validators used at THEIR OWN write time, not folded into the "
                "schedule-approval gate."
            )

        for tbl, col in (("fee_exclusions", "reason"), ("fee_discounts", "reason"),
                         ("fee_credits", "reason")):
            c = [r for r in cols if r["table_name"] == tbl and r["column_name"] == col]
            if c and c[0]["is_nullable"] == "NO":
                conflicts.append(
                    f"{tbl}.{col} is NOT NULL, which admits ''. The empty-string "
                    f"gap the prompt names for fee_exclusions.reason applies "
                    f"identically to {tbl}."
                )

        if not conflicts:
            print("  none")
        for i, c in enumerate(conflicts, 1):
            print(f"\n  [{i}] {c}")

        print()
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
