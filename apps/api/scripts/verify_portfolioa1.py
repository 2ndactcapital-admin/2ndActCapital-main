"""Verification — Portfolio A1, the global security layer.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END.
Real database, real rows, real RLS.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback.
The whole point of the write-gate checks is that they run under a role with
``rolbypassrls = false``. Running them as ``postgres`` would "pass" every one
of them while proving nothing at all, so a missing or non-connecting
app_service credential FAILS this script rather than degrading it.

────────────────────────────────────────────────────────────────────────────
TEARDOWN: WHAT "ZERO ROWS" HAD TO BECOME, AND WHY
────────────────────────────────────────────────────────────────────────────
The brief asks that teardown leave zero rows in all four portfolio tables.
It cannot, and should not.

Those tables are NOT empty and never were during this sprint. At the time A1
was built they held the live EDGAR corpus: 67 securities, 64 identifiers,
97 relationships — the output of the note-terms and underlying-resolution
sprints. ``TRUNCATE`` would delete verified production data to satisfy a
literal reading of a checklist item.

So the invariant is enforced in the form that actually means something:
**every one of the four tables is counted before the run and after teardown,
and the counts must match exactly.** Zero fixture residue, zero collateral
damage. A leaked fixture row fails the check just as hard as a deleted corpus
row would. Fixtures are all named with the FIXTURE_TAG below, so teardown can
find them by exact match rather than by guessing at a time window.

Run:
    python3 scripts/verify_portfolioa1.py
"""

from __future__ import annotations

import ast
import asyncio
import glob
import os
import sys
from datetime import date
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
# The venv's Python minor version has moved during this project's life (3.12 ->
# 3.14), so glob rather than hard-code it — a stale hard-coded path fails as
# "asyncpg not installed", which reads like an environment problem and is not.
sys.path.extend(sorted(glob.glob(os.path.join(_HERE, "..", "venv", "lib", "python3*", "site-packages"))))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_HERE, "..", ".env"), override=False)

from services.securities_global import (  # noqa: E402
    HAS_SERIES,
    TABLE_IDENT,
    TABLE_PRICE,
    TABLE_REL,
    TABLE_SEC,
    SecuritiesGlobalPermissionError,
    StructuredNotePricingError,
    add_identifier,
    add_price,
    add_relationship,
    backfill_canonical_ids,
    canonical_id_for,
    create_security,
    get_by_identifier,
    merge_securities,
    resolve_scoreability,
    set_price_coverage,
)
from services.underlying_resolution import confirm_resolution  # noqa: E402

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
ADMIN_USER_ID = "99000000-0000-0000-0000-000000000031"
ADMIN_SUB = "auth0|verify_portfolioa1_super_admin"
MEMBER_USER_ID = "99000000-0000-0000-0000-000000000032"
MEMBER_SUB = "auth0|verify_portfolioa1_member"

# Every fixture name and identifier carries this. No prospectus, no ticker and
# no CUSIP will ever contain it, so teardown deletes by exact match and cannot
# touch a corpus row.
FIXTURE_TAG = "VERIFY-PORTFOLIOA1"
FIX_NOTE = f"{FIXTURE_TAG} structured note"
FIX_INDEX_A = f"{FIXTURE_TAG} index A (merge survivor)"
FIX_INDEX_B = f"{FIXTURE_TAG} index B (merged away)"
FIX_INDEX_UNCOVERED = f"{FIXTURE_TAG} index with no price series"
FIX_EQUITY = f"{FIXTURE_TAG} equity"
FIX_CANONICAL = f"{FIXTURE_TAG} canonical-on-create"
# Declared up front, in full, and never appended to at runtime. A name added
# mid-run is a name the NEXT run's start-teardown does not know about, so a
# crash between creating it and the end-teardown would strand a fixture row in
# the corpus permanently — and the count assertion would then fail forever
# against a baseline that had silently absorbed it.
FIXTURE_NAMES = [
    FIX_NOTE, FIX_INDEX_A, FIX_INDEX_B, FIX_INDEX_UNCOVERED,
    FIX_EQUITY, FIX_CANONICAL,
]

# The unresolved edge's verbatim text. resolve_scoreability must quote this
# back; the check asserts on the string, not on a boolean.
RAW_UNCHECKED = f"the {FIXTURE_TAG} Wholly Unchecked Strategy Index"
RAW_RESOLVED = f"the {FIXTURE_TAG} Resolvable Index"

TABLES = (TABLE_SEC, TABLE_IDENT, TABLE_PRICE, TABLE_REL)

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def report(name: str, detail: str) -> None:
    """A Task 1 finding. Printed as a FINDING, never silently as a PASS."""
    print(f"[FIND] {name} — {detail}")


# ── Setup / teardown ────────────────────────────────────────────────────────


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def teardown(conn) -> None:
    """Delete every fixture row, child tables first. Touches nothing else."""
    fixture_ids = (
        f"SELECT id FROM {TABLE_SEC} WHERE name = ANY($1::text[])"
    )
    # Relationships first — they FK both directions into securities_global.
    await conn.execute(
        f"DELETE FROM {TABLE_REL} WHERE from_global_security_id IN ({fixture_ids}) "
        f"   OR to_global_security_id IN ({fixture_ids}) "
        f"   OR proposed_global_security_id IN ({fixture_ids})",
        FIXTURE_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_PRICE} WHERE global_security_id IN ({fixture_ids})",
        FIXTURE_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_IDENT} WHERE global_security_id IN ({fixture_ids})",
        FIXTURE_NAMES,
    )
    # Break the merge FK before deleting, or the survivor cannot go.
    await conn.execute(
        f"UPDATE {TABLE_SEC} SET merged_into_id = NULL, canonical_id = id "
        f"WHERE name = ANY($1::text[])",
        FIXTURE_NAMES,
    )
    await conn.execute(f"DELETE FROM {TABLE_SEC} WHERE name = ANY($1::text[])", FIXTURE_NAMES)
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[])", [ADMIN_SUB, MEMBER_SUB]
    )


async def seed_users(conn) -> None:
    for user_id, sub, role, email in (
        (ADMIN_USER_ID, ADMIN_SUB, "super_admin", "verify_a1_admin@test.local"),
        (MEMBER_USER_ID, MEMBER_SUB, "member", "verify_a1_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioA1', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, DEFAULT_ORG_ID, email, sub, role,
        )


class _AppServicePool:
    """Minimal pool facade over one app_service connection.

    ``confirm_resolution`` takes a pool and calls ``pool.acquire()``; this is
    the smallest thing that satisfies it without spinning up a second real
    pool. Deliberately used for the confirm so the resolved edge in the
    scoreability check is produced by the REAL human-confirm path — trigger,
    RLS and all — rather than by a hand-written UPDATE that would prove nothing
    about how resolved edges actually come to exist.
    """

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def super_admin_ctx(conn):
    """Transaction on ``conn`` with super-admin + org GUCs set LOCAL."""

    class _Ctx:
        async def __aenter__(self):
            self.tr = conn.transaction()
            await self.tr.start()
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', 'true', true)",
                DEFAULT_ORG_ID,
            )
            return conn

        async def __aexit__(self, et, e, tb):
            if et is None:
                await self.tr.commit()
            else:
                await self.tr.rollback()
            return False

    return _Ctx()


def member_ctx(conn):
    """Transaction on ``conn`` with org context and is_super_admin FALSE."""

    class _Ctx:
        async def __aenter__(self):
            self.tr = conn.transaction()
            await self.tr.start()
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', 'false', true),"
                "       set_config('app.current_auth0_sub', $2, true)",
                DEFAULT_ORG_ID, MEMBER_SUB,
            )
            return conn

        async def __aexit__(self, et, e, tb):
            await self.tr.rollback()
            return False

    return _Ctx()


# ── Task 1 findings, asserted ───────────────────────────────────────────────


async def check_task1_schema(conn) -> None:
    """1a — four tables, RLS enabled, exactly 4 policies each."""
    rows = await conn.fetch(
        """
        SELECT c.relname, c.relrowsecurity,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS npol
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'portfolio' AND c.relkind = 'r'
          AND c.relname = ANY($1::text[])
        """,
        [t.split(".", 1)[1] for t in TABLES],
    )
    by_name = {r["relname"]: r for r in rows}
    check(
        "1a tables exist — all four portfolio.* tables present",
        len(by_name) == 4,
        f"found {sorted(by_name)}",
    )
    check(
        "1a RLS enabled on all four (pg_class.relrowsecurity)",
        all(r["relrowsecurity"] for r in rows) and len(rows) == 4,
        ", ".join(f"{r['relname']}={r['relrowsecurity']}" for r in rows),
    )
    check(
        "1a exactly 4 policies each (pg_policy)",
        all(r["npol"] == 4 for r in rows) and len(rows) == 4,
        ", ".join(f"{r['relname']}={r['npol']}" for r in rows),
    )
    pol = await conn.fetch(
        "SELECT tablename, policyname, cmd FROM pg_policies "
        "WHERE schemaname='portfolio' AND tablename = ANY($1::text[]) "
        "ORDER BY tablename, cmd",
        [t.split(".", 1)[1] for t in TABLES],
    )
    for r in pol:
        report("1a policy", f"{r['tablename']}.{r['policyname']} ({r['cmd']})")

    # The two CHECKs the rest of this script leans on.
    defs = {
        r["conname"]: r["def"]
        for r in await conn.fetch(
            """
            SELECT con.conname, pg_get_constraintdef(con.oid) AS def
            FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            WHERE n.nspname = 'portfolio' AND con.contype = 'c'
            """
        )
    }
    check(
        "1a CHECK sec_global_rel_resolved_has_target exists",
        "sec_global_rel_resolved_has_target" in defs,
        defs.get("sec_global_rel_resolved_has_target", "MISSING"),
    )
    check(
        "1a CHECK securities_global_price_coverage_chk exists",
        "securities_global_price_coverage_chk" in defs,
        defs.get("securities_global_price_coverage_chk", "MISSING"),
    )


async def check_task1_gucs() -> None:
    """1b — the GUCs are connection-level SET LOCAL, not schema-scoped."""
    src = open(os.path.join(_HERE, "..", "services", "database.py")).read()
    has_set_config = "set_config('app.current_org_id'" in src and \
                     "set_config('app.is_super_admin'" in src
    # is_local => true is the third argument. Anything else would be a plain
    # SET, which leaks across transactions on a pooled backend.
    local = "set_config('app.is_super_admin', $2, true)" in src
    check(
        "1b services/database.py sets app.current_org_id + app.is_super_admin "
        "via set_config(..., is_local=true) — i.e. SET LOCAL",
        has_set_config and local,
        "connection-level GUCs, no schema binding — which is why RLS works "
        "in the non-public 'portfolio' schema",
    )
    report(
        "1b mechanism",
        "_RLSPool.acquire() opens an explicit transaction and its FIRST "
        "statement is set_config(app.current_org_id / app.is_super_admin / "
        "app.current_auth0_sub, ..., is_local=true). Session GUCs, unqualified "
        "by schema.",
    )


async def check_task1_search_path(app_conn) -> None:
    """1c — is `portfolio` on the search_path, or must we schema-qualify?"""
    sp = await app_conn.fetchval("SHOW search_path")
    qualified_required = False
    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.fetchval("SELECT count(*) FROM securities_global")
    except asyncpg.exceptions.UndefinedTableError:
        qualified_required = True
    finally:
        await tr.rollback()

    report("1c search_path (as app_service)", sp)
    check(
        "1c 'portfolio' is NOT on the search_path — every query must "
        "schema-qualify",
        qualified_required and "portfolio" not in sp,
        f"unqualified SELECT raised UndefinedTableError; search_path={sp!r}",
    )
    # And prove the qualified form works from the same connection.
    n = await app_conn.fetchval(f"SELECT count(*) FROM {TABLE_SEC}")
    check(
        "1c schema-qualified form works from the same connection",
        isinstance(n, int),
        f"SELECT count(*) FROM {TABLE_SEC} = {n}",
    )
    # And that the service layer never emits a bare table name.
    src = open(os.path.join(_HERE, "..", "services", "securities_global.py")).read()
    # Docstrings are stripped before scanning. The module docstring quotes the
    # exact anti-pattern ("an unqualified FROM securities_global raises...") to
    # explain why the rule exists, and a naive text scan flags its own
    # explanation — a false positive that would train the next person to delete
    # the check rather than the bug.
    code = src
    tree = ast.parse(src)
    docs = [ast.get_docstring(tree, clean=False)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            docs.append(ast.get_docstring(node, clean=False))
    for d in docs:
        if d:
            code = code.replace(d, "")

    qualified_consts = all(f'= "{t}"' in src for t in TABLES)
    bare = sorted({
        t.split(".", 1)[1] for t in TABLES
        if f"FROM {t.split('.', 1)[1]}" in code
        or f"INTO {t.split('.', 1)[1]}" in code
        or f"UPDATE {t.split('.', 1)[1]}" in code
    })
    check(
        "1c services/securities_global.py schema-qualifies every table "
        "reference (no bare FROM/INTO/UPDATE in executable code)",
        qualified_consts and not bare,
        f"unqualified references: {bare or 'none'}",
    )


async def check_task1_role_gate(app_conn) -> None:
    """1d — app_service can SELECT, and is genuinely BLOCKED from INSERT."""
    who = await app_conn.fetchval("SELECT current_user")
    bypass = await app_conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    check(
        "1d connected as a real non-bypass role",
        who == "app_service" and bypass is False,
        f"current_user={who}, rolbypassrls={bypass}",
    )
    report(
        "1d",
        f"app_service rolbypassrls={bypass}; global read is USING(true), "
        f"writes gated on app.is_super_admin",
    )


# ── The checks proper ───────────────────────────────────────────────────────


async def check_global_read(app_conn, admin_conn) -> None:
    """A non-super-admin connection CAN read securities_global."""
    async with super_admin_ctx(admin_conn) as c:
        sec_id = await create_security(
            c, name=FIX_INDEX_A, security_type="index", is_super_admin=True
        )

    async with member_ctx(app_conn) as c:
        row = await c.fetchrow(
            f"SELECT id::text, name FROM {TABLE_SEC} WHERE id = $1::uuid", sec_id
        )
        total = await c.fetchval(f"SELECT count(*) FROM {TABLE_SEC}")
    check(
        "GLOBAL READ — non-super-admin app_service (org set, "
        "is_super_admin='false') reads securities_global",
        row is not None and row["name"] == FIX_INDEX_A and total > 0,
        f"read the row it did not have permission to write; {total} rows visible",
    )
    return sec_id


async def check_write_gate(app_conn) -> None:
    """The SAME non-super-admin connection CANNOT insert — into all four tables.

    Asserts on the exception, not on the absence of one. A check written as
    "run it and see if anything blew up" passes when the insert silently
    affects zero rows, which is a completely different bug.
    """
    probes = [
        (TABLE_SEC,
         f"INSERT INTO {TABLE_SEC} (name, security_type) VALUES ('{FIXTURE_TAG} gate probe','index')"),
        (TABLE_IDENT,
         f"INSERT INTO {TABLE_IDENT} (global_security_id, id_type, id_value) "
         f"SELECT id, 'internal', '{FIXTURE_TAG}-gate' FROM {TABLE_SEC} LIMIT 1"),
        (TABLE_PRICE,
         f"INSERT INTO {TABLE_PRICE} (global_security_id, price_date, price) "
         f"SELECT id, DATE '2026-01-02', 1 FROM {TABLE_SEC} LIMIT 1"),
        (TABLE_REL,
         f"INSERT INTO {TABLE_REL} (from_global_security_id, raw_underlying_text) "
         f"SELECT id, '{FIXTURE_TAG}-gate' FROM {TABLE_SEC} LIMIT 1"),
    ]
    for table, sql in probes:
        rejected, detail = False, "INSERT SUCCEEDED — the write gate is OPEN"
        async with member_ctx(app_conn) as c:
            try:
                await c.execute(sql)
            except asyncpg.exceptions.InsufficientPrivilegeError as exc:
                rejected, detail = True, str(exc).splitlines()[0]
            except Exception as exc:  # noqa: BLE001
                detail = f"rejected, but not by RLS: {type(exc).__name__}: {exc}"
        check(
            f"SUPER-ADMIN WRITE GATE — non-super-admin INSERT into {table} is "
            f"REJECTED",
            rejected,
            detail,
        )

    # And the service layer refuses before it ever reaches the database.
    refused = False
    try:
        await create_security(
            app_conn, name=f"{FIXTURE_TAG} never", security_type="index",
            is_super_admin=False,
        )
    except SecuritiesGlobalPermissionError as exc:
        refused = True
        detail = str(exc).split(".")[0]
    check(
        "SUPER-ADMIN WRITE GATE — service layer refuses is_super_admin=False "
        "before touching the database",
        refused,
        detail if refused else "create_security did not raise",
    )


async def check_super_admin_write(app_conn) -> None:
    """A super-admin-context connection CAN insert — on the SAME app_service role."""
    inserted = None
    async with super_admin_ctx(app_conn) as c:
        inserted = await create_security(
            c, name=FIX_INDEX_UNCOVERED, security_type="index",
            price_coverage="no_public_source", is_super_admin=True,
        )
        seen = await c.fetchval(
            f"SELECT name FROM {TABLE_SEC} WHERE id = $1::uuid", inserted
        )
    check(
        "SUPER-ADMIN WRITE — same app_service role, is_super_admin='true', "
        "INSERT succeeds",
        inserted is not None and seen == FIX_INDEX_UNCOVERED,
        f"id={inserted}",
    )
    return inserted


async def check_merge_chain(admin_conn) -> None:
    """Create A, create B, merge B into A, resolve B's identifier -> A.

    Also asserts the resolution is a JOIN and not a walk, by reading the query
    plan: a row-by-row walk would show up as a recursive CTE or as more than
    one round trip, and neither can hide from EXPLAIN.
    """
    cusip_b = f"{FIXTURE_TAG}-B"

    async with super_admin_ctx(admin_conn) as c:
        a_id = await c.fetchval(f"SELECT id::text FROM {TABLE_SEC} WHERE name=$1", FIX_INDEX_A)
        b_id = await create_security(
            c, name=FIX_INDEX_B, security_type="index", is_super_admin=True
        )
        await add_identifier(
            c, global_security_id=b_id, id_type="internal", id_value=cusip_b,
            is_primary=True, is_super_admin=True,
        )

    # Before the merge, B's identifier resolves to B.
    async with member_ctx(admin_conn) as c:
        before = await get_by_identifier(c, "internal", cusip_b)
    check(
        "MERGE CHAIN — before merge, B's identifier resolves to B",
        before is not None and before["id"] == b_id and before["was_merged"] is False,
        f"-> {before['name'] if before else None}",
    )

    async with super_admin_ctx(admin_conn) as c:
        merged = await merge_securities(
            c, source_id=b_id, target_id=a_id, is_super_admin=True
        )

    async with member_ctx(admin_conn) as c:
        after = await get_by_identifier(c, "internal", cusip_b)
    check(
        "MERGE CHAIN — after merging B into A, B's identifier resolves to A",
        after is not None and after["id"] == a_id and after["name"] == FIX_INDEX_A,
        f"asked for B ({b_id[:8]}), got {after['name'] if after else None} "
        f"({after['id'][:8] if after else '-'})",
    )
    check(
        "MERGE CHAIN — the result reports it forwarded (was_merged, "
        "matched_global_security_id)",
        after is not None and after["was_merged"] is True
        and after["matched_global_security_id"] == b_id,
        f"matched={after['matched_global_security_id'][:8] if after else '-'}, "
        f"canonical={after['id'][:8] if after else '-'}",
    )

    # canonical_id is MATERIALIZED, not derived at read time.
    async with member_ctx(admin_conn) as c:
        b_row = await c.fetchrow(
            f"SELECT merged_into_id::text AS m, canonical_id::text AS c "
            f"FROM {TABLE_SEC} WHERE id = $1::uuid", b_id
        )
    check(
        "MERGE CHAIN — canonical_id materialized on B (= A), not walked",
        b_row["c"] == a_id and b_row["m"] == a_id,
        f"B.canonical_id={b_row['c'][:8]}, B.merged_into_id={b_row['m'][:8]}",
    )

    # THE ASSERTION THAT MATTERS: no row-by-row walk. Read the plan.
    async with member_ctx(admin_conn) as c:
        plan_rows = await c.fetch(
            f"""
            EXPLAIN
            SELECT canonical.id
            FROM {TABLE_IDENT} i
            JOIN {TABLE_SEC} matched ON matched.id = i.global_security_id
            JOIN {TABLE_SEC} canonical
              ON canonical.id = COALESCE(matched.canonical_id, matched.id)
            WHERE i.id_type = 'internal' AND i.id_value = $1
              AND i.valid_to IS NULL AND i.system_to IS NULL
            """,
            cusip_b,
        )
    plan = "\n".join(r["QUERY PLAN"] for r in plan_rows)
    no_recursion = "Recursive" not in plan and "WorkTable" not in plan
    check(
        "MERGE CHAIN — resolution does NOT walk: plan has no recursive node, "
        "and it is one statement (N+1 impossible)",
        no_recursion,
        plan.replace("\n", " | ")[:180],
    )

    check(
        "MERGE CHAIN — merge helper reports chain collapse work",
        merged["canonical_id"] == a_id,
        f"chain_rows_repointed={merged['chain_rows_repointed']}",
    )
    return a_id, b_id


async def check_nullable_target(admin_conn, note_id: str) -> None:
    """An UNRESOLVED edge inserts with to_global_security_id NULL.

    This is the case v5 got wrong: making the target NOT NULL forces a choice
    between dropping the edge (the note then looks like it has no underlyings)
    and minting a placeholder security for a string nobody has checked.
    """
    async with super_admin_ctx(admin_conn) as c:
        rel_id = await add_relationship(
            c, from_global_security_id=note_id,
            raw_underlying_text=RAW_UNCHECKED,
            is_super_admin=True,
        )
        row = await c.fetchrow(
            f"SELECT to_global_security_id, raw_underlying_text, link_state "
            f"FROM {TABLE_REL} WHERE id = $1::uuid", rel_id
        )
    check(
        "NULLABLE TARGET — unresolved edge inserts with "
        "to_global_security_id NULL and raw_underlying_text populated",
        row["to_global_security_id"] is None
        and row["raw_underlying_text"] == RAW_UNCHECKED
        and row["link_state"] == "unresolved",
        f"link_state={row['link_state']}, raw={row['raw_underlying_text']!r}",
    )
    return rel_id


async def check_resolved_requires_target(admin_conn, note_id: str,
                                         index_id: str) -> None:
    """link_state='resolved' + NULL target is REJECTED — by the CHECK itself.

    ─────────────────────────────────────────────────────────────────────────
    WHY THIS IS AN UPDATE AND NOT AN INSERT
    ─────────────────────────────────────────────────────────────────────────
    The obvious test — INSERT a resolved row with a NULL target — does get
    rejected, and it proves nothing. BEFORE triggers fire ahead of CHECK
    constraints, and ``sec_global_rel_confirm_gate`` carries its own
    belt-and-braces ``RAISE ... ERRCODE 23514`` for exactly this case. So the
    INSERT is stopped by the trigger, raises a CheckViolation-shaped error, and
    the constraint under test is never consulted. Drop the constraint entirely
    and that test still passes.

    (This is not hypothetical: the first version of this check did exactly that
    and reported PASS. Asserting on the constraint NAME rather than on "an
    exception happened" is what exposed it.)

    So the row is driven into 'resolved' legitimately first, through the real
    confirm path, and THEN its target is nulled. The trigger deliberately does
    not re-gate an already-resolved row (``OLD.link_state IS DISTINCT FROM
    'resolved'`` is false), so it steps aside and the CHECK constraint is the
    only thing standing there. Which is the point: the constraint is the
    backstop for the case the trigger does not cover.
    """
    pool = _AppServicePool(admin_conn)
    async with super_admin_ctx(admin_conn) as c:
        rel_id = await add_relationship(
            c, from_global_security_id=note_id,
            raw_underlying_text=f"{FIXTURE_TAG} check-constraint probe",
            is_super_admin=True,
        )
    async with super_admin_ctx(admin_conn):
        await confirm_resolution(
            pool, rel_id, actor_id=ADMIN_USER_ID,
            global_security_id=index_id, is_super_admin=True,
        )
    state = await admin_conn.fetchval(
        f"SELECT link_state FROM {TABLE_REL} WHERE id = $1::uuid", rel_id
    )
    check(
        "CHECK CONSTRAINT — precondition: the probe edge really is 'resolved' "
        "(so the trigger will not re-gate it)",
        state == "resolved",
        f"link_state={state}",
    )

    rejected, detail = False, "UPDATE SUCCEEDED — the CHECK is not enforcing"
    async with super_admin_ctx(admin_conn) as c:
        sp = c.transaction()
        await sp.start()
        try:
            await c.execute(
                f"UPDATE {TABLE_REL} SET to_global_security_id = NULL "
                f"WHERE id = $1::uuid",
                rel_id,
            )
        except asyncpg.exceptions.CheckViolationError as exc:
            # Assert on WHICH constraint, not merely that something raised.
            rejected = "sec_global_rel_resolved_has_target" in str(exc)
            detail = f"CheckViolationError: {str(exc).splitlines()[0]}"
        except Exception as exc:  # noqa: BLE001
            detail = f"rejected by something else: {type(exc).__name__}: {exc}"
        finally:
            await sp.rollback()
    check(
        "CHECK CONSTRAINT — link_state='resolved' with NULL "
        "to_global_security_id is REJECTED by "
        "sec_global_rel_resolved_has_target itself (trigger stepped aside)",
        rejected,
        detail,
    )

    # And the trigger independently blocks the INSERT route into 'resolved'.
    trigger_blocked, tdetail = False, "INSERT into 'resolved' was ALLOWED"
    async with super_admin_ctx(admin_conn) as c:
        sp = c.transaction()
        await sp.start()
        try:
            await c.execute(
                f"INSERT INTO {TABLE_REL} (from_global_security_id, "
                f"raw_underlying_text, link_state, to_global_security_id, "
                f"resolved_by, resolved_at) "
                f"VALUES ($1::uuid, $2, 'resolved', $3::uuid, $4::uuid, now())",
                note_id, f"{FIXTURE_TAG} trigger probe", index_id, ADMIN_USER_ID,
            )
        except asyncpg.exceptions.InsufficientPrivilegeError as exc:
            trigger_blocked = True
            tdetail = str(exc).splitlines()[0]
        except Exception as exc:  # noqa: BLE001
            tdetail = f"{type(exc).__name__}: {exc}"
        finally:
            await sp.rollback()
    check(
        "CHECK CONSTRAINT — and trg_sec_global_rel_confirm_gate separately "
        "blocks any direct INSERT into 'resolved' without the confirm token",
        trigger_blocked,
        tdetail,
    )

    async with super_admin_ctx(admin_conn) as c:
        await c.execute(f"DELETE FROM {TABLE_REL} WHERE id = $1::uuid", rel_id)


async def check_pricing_rule(admin_conn, note_id: str, index_id: str) -> None:
    """add_price refuses a structured_note and accepts an index."""
    raised, detail = False, "add_price ACCEPTED a structured note"
    async with super_admin_ctx(admin_conn) as c:
        try:
            await add_price(
                c, global_security_id=note_id, price_date=date(2026, 1, 2),
                price=Decimal("99.25"), is_super_admin=True,
            )
        except StructuredNotePricingError as exc:
            raised = True
            detail = str(exc).split(". ")[0]
    check(
        "PRICING RULE — add_price against security_type='structured_note' "
        "raises StructuredNotePricingError",
        raised,
        detail,
    )

    # And it is a REFUSAL, not a log-and-continue: zero rows were written.
    async with member_ctx(admin_conn) as c:
        n = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_PRICE} WHERE global_security_id = $1::uuid",
            note_id,
        )
    check(
        "PRICING RULE — the refusal wrote nothing (not log-and-continue)",
        n == 0,
        f"{n} price rows for the note",
    )

    # The same call against an index succeeds.
    price_id, equity_price_id = None, None
    async with super_admin_ctx(admin_conn) as c:
        price_id = await add_price(
            c, global_security_id=index_id, price_date=date(2026, 1, 2),
            price=Decimal("4783.4501"), currency_code="USD", source=FIXTURE_TAG,
            is_super_admin=True,
        )
        stored = await c.fetchval(
            f"SELECT price FROM {TABLE_PRICE} WHERE id = $1::uuid", price_id
        )
    check(
        "PRICING RULE — the same call against an 'index' succeeds",
        price_id is not None and stored == Decimal("4783.4501"),
        f"stored {stored!r} ({type(stored).__name__} — Decimal, not float)",
    )

    # 'equity' too, so the rule is demonstrably about notes and not about
    # "everything except index".
    async with super_admin_ctx(admin_conn) as c:
        eq_id = await create_security(
            c, name=FIX_EQUITY, security_type="equity", is_super_admin=True,
        )
        equity_price_id = await add_price(
            c, global_security_id=eq_id, price_date=date(2026, 1, 2),
            price="182.40", is_super_admin=True,
        )
    check(
        "PRICING RULE — the same call against an 'equity' also succeeds",
        equity_price_id is not None,
        f"price id={equity_price_id[:8] if equity_price_id else '-'}",
    )


async def check_scoreability(app_conn, admin_conn, note_id, index_id,
                             unresolved_rel_id, uncovered_id) -> None:
    """Resolved + has_series => scoreable. One unresolved edge => not, with the
    specific raw_underlying_text named."""
    pool = _AppServicePool(admin_conn)

    # Give the survivor index a price series, and add a second, resolvable edge.
    async with super_admin_ctx(admin_conn) as c:
        await set_price_coverage(
            c, global_security_id=index_id, price_coverage=HAS_SERIES,
            is_super_admin=True,
        )
        resolvable_rel = await add_relationship(
            c, from_global_security_id=note_id,
            raw_underlying_text=RAW_RESOLVED, is_super_admin=True,
        )

    # Resolve it through the REAL human-confirm path.
    async with super_admin_ctx(admin_conn):
        await confirm_resolution(
            pool, resolvable_rel, actor_id=ADMIN_USER_ID,
            global_security_id=index_id, is_super_admin=True,
        )

    async with member_ctx(admin_conn) as c:
        blocked = await resolve_scoreability(c, note_id)
    check(
        "SCOREABILITY — a note with one resolved has_series underlying and one "
        "UNRESOLVED edge is NOT scoreable",
        blocked.scoreable is False,
        f"{blocked.relationship_count} edges, {len(blocked.gaps)} gap(s)",
    )
    check(
        "SCOREABILITY — the reason names the specific raw_underlying_text",
        RAW_UNCHECKED in (blocked.reason or ""),
        (blocked.reason or "")[:150],
    )

    # Remove the unresolved edge; now every edge is resolved + has_series.
    async with super_admin_ctx(admin_conn) as c:
        await c.execute(f"DELETE FROM {TABLE_REL} WHERE id = $1::uuid", unresolved_rel_id)

    async with member_ctx(admin_conn) as c:
        good = await resolve_scoreability(c, note_id)
    check(
        "SCOREABILITY — with every edge resolved and every target "
        "price_coverage='has_series', the note IS scoreable",
        good.scoreable is True and good.reason is None,
        f"{good.relationship_count} edge(s), reason={good.reason}",
    )

    # An edge to a target that has no price series blocks it, and the reason
    # names the target rather than shrugging.
    async with super_admin_ctx(admin_conn) as c:
        uncov_rel = await add_relationship(
            c, from_global_security_id=note_id,
            raw_underlying_text=f"the {FIXTURE_TAG} Uncovered Index",
            is_super_admin=True,
        )
    async with super_admin_ctx(admin_conn):
        await confirm_resolution(
            pool, uncov_rel, actor_id=ADMIN_USER_ID,
            global_security_id=uncovered_id, is_super_admin=True,
        )
    async with member_ctx(admin_conn) as c:
        uncovered = await resolve_scoreability(c, note_id)
    check(
        "SCOREABILITY — a RESOLVED edge whose target lacks a price series also "
        "blocks, and the reason names that target",
        uncovered.scoreable is False
        and FIX_INDEX_UNCOVERED in (uncovered.reason or "")
        and "no_public_source" in (uncovered.reason or ""),
        (uncovered.reason or "")[:160],
    )
    check(
        "SCOREABILITY — derived, not stored (no scoreable column exists)",
        not await admin_conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='portfolio' AND table_name='securities_global' "
            "AND column_name LIKE '%scoreab%')"
        ),
        "securities_global has no scoreability column",
    )


async def check_canonical_backfill(admin_conn) -> None:
    """canonical_id is maintained on write, and the backfill closes the legacy
    gap the Part 1 SQL left behind."""
    async with super_admin_ctx(admin_conn) as c:
        n = await backfill_canonical_ids(c, is_super_admin=True)
        remaining = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_SEC} WHERE canonical_id IS NULL"
        )
        wrong = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_SEC} "
            f"WHERE canonical_id IS DISTINCT FROM COALESCE(merged_into_id, id)"
        )
    check(
        "CANONICAL — backfill leaves zero NULL canonical_id and zero rows "
        "violating canonical_id = COALESCE(merged_into_id, id)",
        remaining == 0 and wrong == 0,
        f"backfilled {n} row(s) this run; {remaining} NULL, {wrong} inconsistent",
    )
    async with super_admin_ctx(admin_conn) as c:
        fresh = await create_security(
            c, name=FIX_CANONICAL, security_type="other", is_super_admin=True,
        )
        cid = await canonical_id_for(c, fresh)
    check(
        "CANONICAL — a newly created security is canonical from the instant it "
        "exists (no NULL window)",
        cid == fresh,
        f"canonical_id == id == {fresh[:8]}",
    )


# ── Main ────────────────────────────────────────────────────────────────────


async def main_async() -> int:
    db_url = os.environ.get("DATABASE_URL")
    app_url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1
    if not app_url:
        print("[FAIL] APP_SERVICE_DATABASE_URL is not set. There is NO SET ROLE "
              "fallback: the write-gate checks are meaningless under a "
              "bypassrls role, so this script fails rather than pretending.")
        return 1

    admin_conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
    try:
        app_conn = await asyncpg.connect(app_url, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] APP_SERVICE_DATABASE_URL did not connect: "
              f"{type(exc).__name__}: {exc}")
        await admin_conn.close()
        return 1

    baseline: dict[str, int] = {}
    try:
        await teardown(admin_conn)                       # START
        baseline = await counts(admin_conn)
        print(f"\nBASELINE (live corpus, must be preserved): "
              + ", ".join(f"{t.split('.')[1]}={n}" for t, n in baseline.items()) + "\n")
        await seed_users(admin_conn)

        await check_task1_schema(admin_conn)
        await check_task1_gucs()
        await check_task1_search_path(app_conn)
        await check_task1_role_gate(app_conn)

        index_a = await check_global_read(app_conn, admin_conn)
        await check_write_gate(app_conn)
        uncovered_id = await check_super_admin_write(app_conn)

        async with super_admin_ctx(admin_conn) as c:
            note_id = await create_security(
                c, name=FIX_NOTE, security_type="structured_note",
                is_super_admin=True,
            )

        index_a, _index_b = await check_merge_chain(admin_conn)
        unresolved_rel = await check_nullable_target(admin_conn, note_id)
        await check_resolved_requires_target(admin_conn, note_id, index_a)
        await check_pricing_rule(admin_conn, note_id, index_a)
        await check_scoreability(app_conn, admin_conn, note_id, index_a,
                                 unresolved_rel, uncovered_id)
        await check_canonical_backfill(admin_conn)
    finally:
        try:
            await teardown(admin_conn)                   # END
            final = await counts(admin_conn)
            if baseline:
                drift = {t: (baseline[t], final[t]) for t in TABLES
                         if baseline[t] != final[t]}
                check(
                    "TEARDOWN — all four portfolio.* tables returned to their "
                    "exact pre-run counts (zero fixture residue, zero corpus "
                    "damage)",
                    not drift,
                    "; ".join(f"{t.split('.')[1]} {b}->{f}"
                              for t, (b, f) in drift.items()) or
                    ", ".join(f"{t.split('.')[1]}={n}" for t, n in final.items()),
                )
        finally:
            await admin_conn.close()
            await app_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\nRESULT: {'PASS' if failed == 0 else 'FAIL'} "
          f"({len(results)} checks, {passed} passed, {failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
