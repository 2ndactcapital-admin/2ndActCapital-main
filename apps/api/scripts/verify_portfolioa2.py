"""Verification — Portfolio A2, tenant assets / positions / transactions.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END.
Real database, real rows, real RLS, real ``app_service`` connection.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback, for the
same reason A1 requires it: the cross-org isolation checks are meaningless under
a ``rolbypassrls`` role. Running them as ``postgres`` would "pass" every one of
them while proving nothing at all, so a missing or non-connecting app_service
credential FAILS this script rather than degrading it.

────────────────────────────────────────────────────────────────────────────
TEARDOWN: BEFORE/AFTER COUNTS, NOT TRUNCATE
────────────────────────────────────────────────────────────────────────────
A1 found its four tables holding the live EDGAR corpus and had to replace
"teardown leaves zero rows" with "counts match exactly", or the script would
have deleted verified production data.

A2's six tables measured EMPTY at the start of this sprint — but the discipline
is identical anyway, and deliberately so. "It was empty when I looked" is a fact
about one afternoon; the count assertion is a fact about every run. The moment
Phase B ingestion writes the first real position, an unconditional TRUNCATE in
here becomes a data-loss bug that nobody notices until the next quarter-end.
So: all six tables are counted before the run and after teardown, and the
counts must match exactly. A leaked fixture row fails as hard as a deleted
production row.

Fixtures are tagged FIXTURE_TAG and deleted by exact match. Every fixture name
is declared up front and never appended to at runtime — a name minted mid-run
is one the NEXT run's start-teardown cannot find, so a crash between creating
it and the end-teardown would strand it permanently and the count assertion
would then fail forever against a baseline that had silently absorbed it.

Run:
    python3 scripts/verify_portfolioa2.py
"""

from __future__ import annotations

import ast
import asyncio
import glob
import os
import sys
import uuid
from datetime import date
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
# The venv's Python minor version has moved during this project's life, so glob
# rather than hard-code it — a stale hard-coded path fails as "asyncpg not
# installed", which reads like an environment problem and is not.
sys.path.extend(sorted(glob.glob(os.path.join(_HERE, "..", "venv", "lib", "python3*", "site-packages"))))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_HERE, "..", ".env"), override=False)

from schemas.entities import (  # noqa: E402
    OPERATIONAL_ENTITY_TYPES,
    EntityType,
)
from services.portfolio_assets import (  # noqa: E402
    TABLE_ASSET_IDENT,
    TABLE_ASSETS,
    TABLE_EXT_REF,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    TABLE_VALUATIONS,
    OwnershipBasisError,
    PortfolioError,
    TransactionMarketError,
    add_identifier,
    create_asset,
    create_position,
    record_transaction,
    record_valuation,
    resolve_current_value,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org, used for the cross-org isolation checks. A real row, not
# a minted one: `organizations` has FKs pointing at it from a dozen places and
# inventing a throwaway org would need its own teardown ordering.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

ADMIN_USER_ID = "99000000-0000-0000-0000-000000000041"
ADMIN_SUB = "auth0|verify_portfolioa2_super_admin"
MEMBER_USER_ID = "99000000-0000-0000-0000-000000000042"
MEMBER_SUB = "auth0|verify_portfolioa2_member"

FIXTURE_TAG = "VERIFY-PORTFOLIOA2"

# ── Entity fixtures ─────────────────────────────────────────────────────────
FIX_ACCOUNT = f"{FIXTURE_TAG} Custodial Account 7781"
FIX_TRUST = f"{FIXTURE_TAG} Family Trust"
FIX_OTHERORG_TRUST = f"{FIXTURE_TAG} Other-Org Trust"
# Deliberately shares its display_name with the account fixture, so the dupe
# check has something it WOULD have matched. Without this, "dupes returned
# nothing" proves the name was absent, not that the exclusion fired.
FIX_DUPE_BAIT = f"{FIXTURE_TAG} Ambiguous Name"

ENTITY_NAMES = [FIX_ACCOUNT, FIX_TRUST, FIX_OTHERORG_TRUST, FIX_DUPE_BAIT]

# ── Asset fixtures ──────────────────────────────────────────────────────────
FIX_ASSET_UNITS = f"{FIXTURE_TAG} listed equity (units basis)"
FIX_ASSET_PCT = f"{FIXTURE_TAG} operating LLC interest (percent basis)"
FIX_ASSET_VALUE = f"{FIXTURE_TAG} appraised artwork (value basis)"
FIX_ASSET_VALUATIONS = f"{FIXTURE_TAG} private fund (valuation ladder)"
FIX_ASSET_NOVALUE = f"{FIXTURE_TAG} asset with no valuation at all"
FIX_ASSET_OTHERORG = f"{FIXTURE_TAG} other-org asset"

ASSET_NAMES = [
    FIX_ASSET_UNITS, FIX_ASSET_PCT, FIX_ASSET_VALUE,
    FIX_ASSET_VALUATIONS, FIX_ASSET_NOVALUE, FIX_ASSET_OTHERORG,
]

FIX_EXT_REF_SOURCE = "altruist"
FIX_EXT_REF_ID = f"{FIXTURE_TAG}-EXTREF-1"

AS_OF = date(2026, 6, 30)

TABLES = (
    TABLE_ASSETS, TABLE_ASSET_IDENT, TABLE_POSITIONS,
    TABLE_VALUATIONS, TABLE_TRANSACTIONS, TABLE_EXT_REF,
)

# The Task 2 classification, asserted against the live table rather than
# re-derived. If somebody re-runs the backfill with different judgement, this
# fails and says which code moved.
EXPECTED_MARKET = {
    "call_investment": "private",
    "call_mgmt_fee": "private",
    "call_org_cost": "private",
    "call_partnership_expense": "private",
    "dist_roc": "private",
    "dist_gain": "private",
    "dist_income": "private",
    "dist_recallable": "private",
    "dist_stock": "private",
    "valuation": "private",
    "buy": "public",
    "sell": "public",
    "dividend": "public",
    "adjustment": "both",
    "fee_expense": "both",
    "interest": "both",
}

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
    asset_ids = f"SELECT id FROM {TABLE_ASSETS} WHERE name = ANY($1::text[])"

    await conn.execute(
        f"DELETE FROM {TABLE_TRANSACTIONS} WHERE position_id IN ("
        f"  SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({asset_ids}))",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN ({asset_ids})",
        ASSET_NAMES,
    )
    # Valuations FK themselves via supersedes_valuation_id. Break the edge
    # before deleting, or the superseded row cannot go.
    await conn.execute(
        f"UPDATE {TABLE_VALUATIONS} SET supersedes_valuation_id = NULL "
        f"WHERE asset_id IN ({asset_ids})",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_VALUATIONS} WHERE asset_id IN ({asset_ids})",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_ASSET_IDENT} WHERE asset_id IN ({asset_ids})",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_EXT_REF} WHERE external_id = $1", FIX_EXT_REF_ID
    )
    await conn.execute(
        f"DELETE FROM {TABLE_ASSETS} WHERE name = ANY($1::text[])", ASSET_NAMES
    )
    await conn.execute(
        "DELETE FROM entities WHERE display_name = ANY($1::text[])", ENTITY_NAMES
    )
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[])", [ADMIN_SUB, MEMBER_SUB]
    )


async def seed_users(conn) -> None:
    for user_id, sub, role, email in (
        (ADMIN_USER_ID, ADMIN_SUB, "super_admin", "verify_a2_admin@test.local"),
        (MEMBER_USER_ID, MEMBER_SUB, "member", "verify_a2_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioA2', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, DEFAULT_ORG_ID, email, sub, role,
        )


def org_ctx(conn, org_id: str, *, super_admin: bool = False, commit: bool = True):
    """Transaction on ``conn`` with the org GUC SET LOCAL.

    ``super_admin=False`` is the important default: these are TENANT tables and
    the whole point of the isolation checks is that they run without the escape
    hatch. A2 has no Super-Admin-only path, so nothing here needs elevation
    except the fixture setup that spans two orgs.
    """

    class _Ctx:
        async def __aenter__(self):
            self.tr = conn.transaction()
            await self.tr.start()
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', $2, true),"
                "       set_config('app.current_auth0_sub', $3, true)",
                org_id, "true" if super_admin else "false",
                ADMIN_SUB if super_admin else MEMBER_SUB,
            )
            return conn

        async def __aexit__(self, et, e, tb):
            if et is None and commit:
                await self.tr.commit()
            else:
                await self.tr.rollback()
            return False

    return _Ctx()


# ── Task 1 findings, asserted ───────────────────────────────────────────────


async def check_task1a_schema(conn) -> None:
    """1a — six tables, RLS enabled, EXACTLY ONE policy each."""
    for table in TABLES:
        schema, name = table.split(".", 1)
        cols = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2",
            schema, name,
        )
        check(f"1a {table} exists", bool(cols), f"{cols} columns")

        rls = await conn.fetchrow(
            "SELECT c.relrowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relname = $2",
            schema, name,
        )
        check(f"1a {table} has RLS enabled", bool(rls and rls["relrowsecurity"]))

    # THE assertion this whole sprint hinges on. A1's four-policy global-read
    # shape copy-pasted onto a tenant table would be a silent cross-org read:
    # `USING (true)` raises nothing, logs nothing and returns every other
    # tenant's positions. Asserted as an exact count AND an exact shape, because
    # "one policy" that happens to be `USING (true)` would pass a count check.
    policies = await conn.fetch(
        "SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies "
        "WHERE schemaname = 'portfolio' AND tablename = ANY($1::text[]) "
        "ORDER BY tablename, policyname",
        [t.split(".", 1)[1] for t in TABLES],
    )
    by_table: dict[str, list] = {}
    for p in policies:
        by_table.setdefault(p["tablename"], []).append(p)

    wrong_count = {t: len(ps) for t, ps in by_table.items() if len(ps) != 1}
    check(
        "1a EXACTLY ONE RLS policy per table (NOT A1's four-policy global-read "
        "shape — that copy-paste would be a silent cross-org read)",
        not wrong_count and len(by_table) == len(TABLES),
        f"policy counts: "
        + ", ".join(f"{t}={len(ps)}" for t, ps in sorted(by_table.items()))
        + (f" | WRONG: {wrong_count}" if wrong_count else ""),
    )

    # And that the single policy is the direct-scoped shape, not `USING (true)`.
    bad_shape = sorted(
        t for t, ps in by_table.items()
        if not ps or "current_org_id" not in (ps[0]["qual"] or "")
        or "org_id" not in (ps[0]["qual"] or "")
        or (ps[0]["qual"] or "").strip().lower() == "true"
    )
    check(
        "1a the single policy is org-scoped (org_id = current_org_id OR "
        "is_super_admin), never USING (true)",
        not bad_shape,
        f"tables with a non-org-scoped policy: {bad_shape or 'none'}",
    )

    # Drift found during Task 1, asserted so it cannot quietly change.
    pos_checks = await conn.fetch(
        "SELECT con.conname FROM pg_constraint con "
        "JOIN pg_class rel ON rel.oid = con.conrelid "
        "JOIN pg_namespace n ON n.oid = rel.relnamespace "
        "WHERE n.nspname = 'portfolio' AND rel.relname = 'positions' "
        "AND con.contype = 'c'",
    )
    names = sorted(r["conname"] for r in pos_checks)
    report(
        "1a DRIFT — portfolio.positions has NO CHECK tying ownership_basis to "
        "which measure is populated",
        f"only {names}. There is NO database backstop: "
        f"services/portfolio_assets.py::_validate_basis is the ONLY enforcement.",
    )
    report(
        "1a DRIFT — portfolio.assets.asset_type is NOT NULL with no CHECK",
        "open text; validated for non-emptiness only, deliberately",
    )
    ext_unique = await conn.fetchval(
        "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
        "JOIN pg_class rel ON rel.oid = con.conrelid "
        "JOIN pg_namespace n ON n.oid = rel.relnamespace "
        "WHERE n.nspname = 'portfolio' AND rel.relname = 'external_references' "
        "AND con.contype = 'u'",
    )
    report(
        "1a DRIFT — portfolio.external_references UNIQUE is NOT org-scoped",
        f"{ext_unique} — two tenants ingesting the same source+external_id "
        f"hard-conflict, and the loser gets a unique violation on a row RLS "
        f"will not let it see. Widen to include org_id in Phase B.",
    )
    trig = await conn.fetchval(
        "SELECT count(*) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'portfolio' AND NOT t.tgisinternal "
        "AND c.relname = ANY($1::text[])",
        [t.split(".", 1)[1] for t in TABLES],
    )
    check(
        "1a no triggers on the six new tables — so no BEFORE trigger can mask a "
        "CHECK constraint here (A1's hazard has no instance in A2)",
        trig == 0,
        f"{trig} triggers",
    )


async def check_task1b_public(conn) -> None:
    """1b — the three public changes, and the real market classification."""
    labels = [
        r["enumlabel"] for r in await conn.fetch(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'entity_type' ORDER BY e.enumsortorder"
        )
    ]
    check("1b entity_type enum gained 'account'", "account" in labels,
          f"{len(labels)} values, last four: {labels[-4:]}")
    report(
        "1b NOT IN THE BRIEF — entity_type also carries 'spv'",
        "it landed alongside 'account' and has the identical CRM-visibility "
        "problem, so OPERATIONAL_ENTITY_TYPES covers both",
    )

    mkt_chk = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'transaction_types_market_chk'"
    )
    check("1b transaction_types.market exists with its CHECK", bool(mkt_chk),
          str(mkt_chk))

    fx_cols = {
        r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'fx_rates'"
        )
    }
    need = {"rate_type", "valid_from", "valid_to", "system_from", "system_to"}
    check("1b fx_rates gained rate_type + bitemporal columns",
          need <= fx_cols, f"missing: {sorted(need - fx_cols) or 'none'}")
    fx_unique = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'fx_rates_base_ccy_quote_ccy_as_of_date_key'"
    )
    report(
        "1b DRIFT — fx_rates UNIQUE was not widened for rate_type",
        f"{fx_unique} — so a spot AND a period_end rate cannot coexist for the "
        f"same pair/date, and a Rule 3 supersede on fx_rates is impossible "
        f"(close + reinsert violates it). Blocks Phase B FX.",
    )

    # THE Task 2 report: every one of the 16 rows, its live value, named.
    rows = await conn.fetch(
        "SELECT code, category, applies_to_security_types, market "
        "FROM transaction_types ORDER BY market, code"
    )
    print("\n  ── Task 2 · transaction_types.market, all 16 rows (live) ──")
    for r in rows:
        print(f"     {r['code']:26} -> {str(r['market']):8} "
              f"(category={r['category']}, applies_to={r['applies_to_security_types']})")
    actual = {r["code"]: r["market"] for r in rows}
    mismatch = {c: (m, actual.get(c)) for c, m in EXPECTED_MARKET.items()
                if actual.get(c) != m}
    check(
        "2 all 16 transaction_types rows classified (was: 16 x NULL)",
        not mismatch and len(rows) == 16,
        f"{len(rows)} rows; "
        + ", ".join(f"{m}={sum(1 for v in actual.values() if v == m)}"
                    for m in ("private", "public", "both"))
        + (f" | MISMATCH {mismatch}" if mismatch else ""),
    )
    report(
        "2 DELIBERATE DEVIATION FROM THE BRIEF — 'interest' is 'both', not 'public'",
        "the deployed row already records applies_to_security_types = "
        "{unitized, alt}. Classifying it 'public' would make "
        "record_transaction reject private-credit interest, which is real and "
        "common. The data won over the brief.",
    )


async def check_task1c_qualification(app_conn) -> None:
    """1c — `portfolio` is not on the search_path; the module never emits bare."""
    sp = await app_conn.fetchval("SHOW search_path")
    qualified_required = False
    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.fetchval("SELECT count(*) FROM assets")
    except asyncpg.exceptions.UndefinedTableError:
        qualified_required = True
    finally:
        await tr.rollback()

    report("1c search_path (as app_service)", sp)
    check(
        "1c 'portfolio' is NOT on the search_path — every query must "
        "schema-qualify",
        qualified_required and "portfolio" not in sp,
        f"unqualified SELECT FROM assets raised UndefinedTableError; "
        f"search_path={sp!r}",
    )
    n = await app_conn.fetchval(f"SELECT count(*) FROM {TABLE_ASSETS}")
    check(
        "1c schema-qualified form works from the same connection",
        isinstance(n, int),
        f"SELECT count(*) FROM {TABLE_ASSETS} = {n}",
    )

    # AST check, replicated from A1. Docstrings are stripped before scanning:
    # this module's docstring quotes the exact anti-pattern ("an unqualified
    # FROM assets raises ...") to explain why the rule exists, and a naive text
    # scan flags its own explanation — a false positive that trains the next
    # person to delete the check rather than the bug.
    path = os.path.join(_HERE, "..", "services", "portfolio_assets.py")
    src = open(path).read()
    code = src
    tree = ast.parse(src)
    docs = [ast.get_docstring(tree, clean=False)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            docs.append(ast.get_docstring(node, clean=False))
    for d in docs:
        if d:
            code = code.replace(d, "")

    portfolio_tables = [t for t in TABLES]
    qualified_consts = all(f'= "{t}"' in src for t in portfolio_tables)
    bare = sorted({
        t.split(".", 1)[1] for t in portfolio_tables
        if f"FROM {t.split('.', 1)[1]}" in code
        or f"INTO {t.split('.', 1)[1]}" in code
        or f"UPDATE {t.split('.', 1)[1]}" in code
        or f"JOIN {t.split('.', 1)[1]}" in code
    })
    check(
        "1c services/portfolio_assets.py schema-qualifies every table reference "
        "(AST-checked: no bare FROM/INTO/UPDATE/JOIN in executable code)",
        qualified_consts and not bare,
        f"unqualified references: {bare or 'none'}",
    )


def check_task1d_callsites() -> None:
    """1d — the CRM-facing call sites, and the schema gap that blocked Task 4."""
    import inspect

    from routers import entities as entities_router

    check(
        "1d EntityType schema now admits 'account' (it did NOT before A2 — "
        "POST /entities returned 422 and the account node could not be created "
        "through the real path at all)",
        "account" in {e.value for e in EntityType},
        f"{len(list(EntityType))} values",
    )
    check(
        "1d OPERATIONAL_ENTITY_TYPES covers account and spv",
        OPERATIONAL_ENTITY_TYPES == frozenset({"account", "spv"}),
        str(sorted(OPERATIONAL_ENTITY_TYPES)),
    )
    # INVESTOR_ENTITY_TYPES is an allow-list and was already safe — asserted so
    # a future edit that turns it into a deny-list is caught.
    check(
        "1d INVESTOR_ENTITY_TYPES is an allow-list that already excluded "
        "accounts by construction (no change needed)",
        "account" not in entities_router.INVESTOR_ENTITY_TYPES
        and "spv" not in entities_router.INVESTOR_ENTITY_TYPES,
        str(entities_router.INVESTOR_ENTITY_TYPES),
    )
    for fn_name in ("list_entities", "search_entities", "find_entity_dupes"):
        fn = getattr(entities_router, fn_name)
        params = inspect.signature(fn).parameters
        has_flag = "include_operational" in params
        default_excludes = has_flag and params["include_operational"].default is False
        check(
            f"1d {fn_name} excludes operational types by default, with an "
            f"explicit include_operational opt-out",
            default_excludes,
            f"include_operational present={has_flag}, "
            f"default={params['include_operational'].default if has_flag else 'n/a'}",
        )
    report(
        "1d call sites found and fixed",
        "routers/entities.py list_entities / search_entities / "
        "find_entity_dupes (the last is reused verbatim by Chancery Phase 5 "
        "document-party linkage and Phase 11a narrative parties, so an account "
        "named 'Fidelity' could have been matched as a document party)",
    )


# ── Task 4 · the account node ───────────────────────────────────────────────


async def seed_entities(conn) -> dict[str, str]:
    """Create the entity fixtures. The account goes in as entity_type='account'."""
    ids: dict[str, str] = {}
    async with org_ctx(conn, DEFAULT_ORG_ID, super_admin=True) as c:
        for name, etype in (
            (FIX_ACCOUNT, "account"),
            (FIX_TRUST, "trust"),
            (FIX_DUPE_BAIT, "account"),
        ):
            ids[name] = await c.fetchval(
                "INSERT INTO entities (org_id, entity_type, display_name, status) "
                "VALUES ($1::uuid, $2::entity_type, $3, 'client') RETURNING id::text",
                DEFAULT_ORG_ID, etype, name,
            )
    async with org_ctx(conn, OTHER_ORG_ID, super_admin=True) as c:
        ids[FIX_OTHERORG_TRUST] = await c.fetchval(
            "INSERT INTO entities (org_id, entity_type, display_name, status) "
            "VALUES ($1::uuid, 'trust', $2, 'client') RETURNING id::text",
            OTHER_ORG_ID, FIX_OTHERORG_TRUST,
        )
    return ids


async def check_account_node(app_conn, entity_ids: dict[str, str]) -> None:
    """A real entity with entity_type='account' exists and is a real row."""
    async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
        row = await c.fetchrow(
            "SELECT id::text, entity_type::text AS entity_type, display_name "
            "FROM entities WHERE id = $1::uuid",
            entity_ids[FIX_ACCOUNT],
        )
    check(
        "4 a real entity with entity_type='account' exists and is readable "
        "under the app_service role",
        row is not None and row["entity_type"] == "account",
        f"{row['display_name']!r} type={row['entity_type']!r}" if row else "not found",
    )


async def check_account_absent_from_crm(app_conn, entity_ids: dict[str, str]) -> None:
    """PROVEN ABSENT from a real CRM-facing call — not inferred.

    Uses the REAL `find_entity_dupes` helper, not a hand-written query. That is
    the point: a reimplementation here would prove that this script can write an
    exclusion, not that the shipped code does.

    FIX_DUPE_BAIT is an ACCOUNT whose display_name the check is then run
    against. Without it, "dupes returned nothing" would be indistinguishable
    from "that name does not exist" — the check would pass with the exclusion
    deleted.
    """
    from routers.entities import find_entity_dupes

    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        excluded = await find_entity_dupes(c, DEFAULT_ORG_ID, FIX_DUPE_BAIT)
        included = await find_entity_dupes(
            c, DEFAULT_ORG_ID, FIX_DUPE_BAIT, include_operational=True
        )
        # And the picker search, the other CRM surface.
        picker_hits = await c.fetch(
            "SELECT id::text FROM entities WHERE org_id = $1::uuid "
            "AND LOWER(display_name) LIKE $2 "
            "AND entity_type::text <> ALL($3::text[]) "
            "AND valid_to IS NULL AND system_to IS NULL",
            DEFAULT_ORG_ID, f"%{FIXTURE_TAG.lower()}%",
            sorted(OPERATIONAL_ENTITY_TYPES),
        )

    check(
        "4 the account is CORRECTLY ABSENT from find_entity_dupes (the real "
        "CRM/document-linkage duplicate check) — and the same call WITH "
        "include_operational=true DOES return it, proving the row exists and "
        "the exclusion is what hid it",
        len(excluded) == 0 and len(included) == 1,
        f"default={len(excluded)} rows, include_operational=True={len(included)} rows",
    )
    picker_ids = {r["id"] for r in picker_hits}
    check(
        "4 the account is CORRECTLY ABSENT from the entity-picker search filter",
        entity_ids[FIX_ACCOUNT] not in picker_ids
        and entity_ids[FIX_TRUST] in picker_ids,
        f"picker returned {len(picker_ids)} fixture entities; trust present, "
        f"account absent",
    )


class _StubRequest:
    """The smallest thing `get_org_id(request)` accepts.

    The two endpoints below are invoked as plain coroutines rather than over
    HTTP because the global JWT middleware would 401 an unauthenticated call and
    minting a real token is not what is being tested.
    """

    class _State:
        user = {"org_id": DEFAULT_ORG_ID}

    state = _State()


async def check_crm_endpoints_execute(entity_ids: dict[str, str]) -> None:
    """RUN the two rewritten endpoints for real. Signatures are not enough.

    `_operational_filter` derives its `$n` placeholder from the live length of
    the params list, because these queries build their WHERE clauses
    incrementally. That is exactly the kind of thing an `inspect.signature`
    check cannot see: a wrong placeholder number binds the wrong value, or
    raises at execution, and the endpoint is broken for every caller while the
    signature check still passes.
    """
    from routers.entities import list_entities, search_entities

    req = _StubRequest()
    # EVERY `Query(...)`-defaulted argument is passed explicitly. Calling an
    # endpoint as a plain coroutine bypasses FastAPI's dependency resolution, so
    # an omitted one arrives as the `Query` object itself and asyncpg rejects it
    # as a bind parameter — a failure of the harness, not of the endpoint.
    LIST_KW = dict(offset=0, limit=100)
    SEARCH_KW = dict(entity_type=[], exclude_ids=[], page=1, page_size=100)

    listed = await list_entities(req, search=FIXTURE_TAG, **LIST_KW)
    listed_ids = {str(e.id) for e in listed}
    check(
        "4 GET /entities EXECUTES and excludes the account (real call, not a "
        "signature check)",
        entity_ids[FIX_ACCOUNT] not in listed_ids
        and entity_ids[FIX_TRUST] in listed_ids,
        f"{len(listed_ids)} fixture entities returned; trust present, "
        f"account absent",
    )

    with_accounts = await list_entities(
        req, search=FIXTURE_TAG, include_operational=True, **LIST_KW
    )
    with_ids = {str(e.id) for e in with_accounts}
    check(
        "4 GET /entities?include_operational=true DOES return the account — "
        "proving the row was there and the exclusion is what hid it",
        entity_ids[FIX_ACCOUNT] in with_ids,
        f"{len(with_ids)} entities with the opt-out vs {len(listed_ids)} without",
    )

    searched = await search_entities(req, q=FIXTURE_TAG, **SEARCH_KW)
    search_ids = {str(i.id) for i in searched.items}
    check(
        "4 GET /entities/search EXECUTES and excludes the account (the picker "
        "surface)",
        entity_ids[FIX_ACCOUNT] not in search_ids
        and entity_ids[FIX_TRUST] in search_ids,
        f"{searched.total} total; trust present, account absent",
    )

    explicit = await search_entities(
        req, q=FIXTURE_TAG, **{**SEARCH_KW, "entity_type": ["account"]}
    )
    explicit_ids = {str(i.id) for i in explicit.items}
    check(
        "4 an EXPLICIT ?entity_type=account is still honoured — the exclusion "
        "is a default for unfiltered CRM lists, not a prohibition",
        entity_ids[FIX_ACCOUNT] in explicit_ids
        and entity_ids[FIX_TRUST] not in explicit_ids,
        f"{explicit.total} accounts returned",
    )


# ── Task 3 · the three ownership bases ──────────────────────────────────────


async def check_ownership_bases(conn, entity_ids: dict[str, str]) -> dict[str, str]:
    """Three bases round-trip; each rejects the wrong field. Both directions."""
    asset_ids: dict[str, str] = {}
    for name, basis, method, atype in (
        (FIX_ASSET_UNITS, "units", "market_price", "equity"),
        (FIX_ASSET_PCT, "percent", "nav", "operating_company"),
        (FIX_ASSET_VALUE, "value", "appraisal", "collectible"),
    ):
        asset_ids[name] = await create_asset(
            conn, org_id=DEFAULT_ORG_ID, name=name, asset_type=atype,
            asset_class="hard_asset" if basis == "value" else "financial",
            ownership_basis=basis, valuation_method=method,
        )

    # ── The three happy paths, each round-tripped out of the database ───────
    pos_ids: dict[str, str] = {}
    pos_ids["units"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=entity_ids[FIX_ACCOUNT],
        asset_id=asset_ids[FIX_ASSET_UNITS], as_of_date=AS_OF,
        authority="custodial", source_system="altruist",
        quantity=Decimal("1250.5"), market_value=Decimal("187575.00"),
    )
    pos_ids["percent"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=entity_ids[FIX_TRUST],
        asset_id=asset_ids[FIX_ASSET_PCT], as_of_date=AS_OF,
        authority="stated", source_system="manual",
        ownership_pct=Decimal("33.3333"), market_value=Decimal("2400000.00"),
    )
    pos_ids["value"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=entity_ids[FIX_TRUST],
        asset_id=asset_ids[FIX_ASSET_VALUE], as_of_date=AS_OF,
        authority="stated", source_system="manual",
        market_value=Decimal("450000.00"),
    )

    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        rows = {
            k: await c.fetchrow(
                f"SELECT ownership_basis, quantity, ownership_pct, market_value "
                f"FROM {TABLE_POSITIONS} WHERE id = $1::uuid", v,
            )
            for k, v in pos_ids.items()
        }

    u, p, v = rows["units"], rows["percent"], rows["value"]
    check(
        "3 units-basis position round-trips: quantity stored as Decimal, "
        "ownership_pct NULL",
        u["ownership_basis"] == "units"
        and u["quantity"] == Decimal("1250.5")
        and isinstance(u["quantity"], Decimal)
        and u["ownership_pct"] is None,
        f"basis={u['ownership_basis']}, quantity={u['quantity']!r}, "
        f"ownership_pct={u['ownership_pct']!r}",
    )
    check(
        "3 percent-basis position round-trips: ownership_pct stored, "
        "quantity NULL",
        p["ownership_basis"] == "percent"
        and p["ownership_pct"] == Decimal("33.3333")
        and p["quantity"] is None,
        f"basis={p['ownership_basis']}, ownership_pct={p['ownership_pct']!r}, "
        f"quantity={p['quantity']!r}",
    )
    check(
        "3 value-basis position round-trips: market_value only, BOTH quantity "
        "and ownership_pct NULL",
        v["ownership_basis"] == "value"
        and v["market_value"] == Decimal("450000.00")
        and v["quantity"] is None and v["ownership_pct"] is None,
        f"basis={v['ownership_basis']}, market_value={v['market_value']!r}, "
        f"quantity={v['quantity']!r}, ownership_pct={v['ownership_pct']!r}",
    )

    # ── Each basis REJECTS the wrong field populated instead ────────────────
    # Two distinct failures per basis where they exist: the required measure
    # missing, and a forbidden one supplied. "It raised" is not enough — the
    # error class is asserted, so a bad FK or a vocabulary typo cannot pass as
    # a basis rejection.
    async def rejects(label, **kwargs):
        try:
            await create_position(
                conn, org_id=DEFAULT_ORG_ID,
                owner_entity_id=entity_ids[FIX_TRUST], as_of_date=AS_OF,
                authority="manual", source_system="manual", **kwargs,
            )
        except OwnershipBasisError as exc:
            check(f"3 REJECTS {label}", True, str(exc)[:130])
            return
        except Exception as exc:  # noqa: BLE001
            check(f"3 REJECTS {label}", False,
                  f"raised {type(exc).__name__}, not OwnershipBasisError: {exc}")
            return
        check(f"3 REJECTS {label}", False, "the write was ACCEPTED")

    await rejects(
        "units-basis position given ownership_pct instead of quantity",
        asset_id=asset_ids[FIX_ASSET_UNITS], ownership_basis="units",
        ownership_pct=Decimal("50"),
    )
    await rejects(
        "percent-basis position given quantity instead of ownership_pct",
        asset_id=asset_ids[FIX_ASSET_PCT], ownership_basis="percent",
        quantity=Decimal("100"),
    )
    await rejects(
        "value-basis position given quantity instead of market_value",
        asset_id=asset_ids[FIX_ASSET_VALUE], ownership_basis="value",
        quantity=Decimal("100"),
    )
    await rejects(
        "value-basis position given market_value AND ownership_pct",
        asset_id=asset_ids[FIX_ASSET_VALUE], ownership_basis="value",
        market_value=Decimal("1"), ownership_pct=Decimal("10"),
    )

    # ── Accounts are OPTIONAL, not required ─────────────────────────────────
    # The percent position above already owns via a TRUST with no account node
    # in between. Asserted explicitly rather than left implied, because the
    # thing being proven is a negative and negatives do not assert themselves.
    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        owner = await c.fetchrow(
            f"SELECT e.entity_type::text AS entity_type, e.display_name "
            f"FROM {TABLE_POSITIONS} p "
            f"JOIN public.entities e ON e.id = p.owner_entity_id "
            f"WHERE p.id = $1::uuid",
            pos_ids["percent"],
        )
    check(
        "3 a position can name a NON-account owner (a trust) directly, with no "
        "account node in between — accounts are genuinely optional",
        owner["entity_type"] == "trust",
        f"owner_entity_id -> {owner['display_name']!r} "
        f"(entity_type={owner['entity_type']!r})",
    )
    check(
        "4 the same account that is hidden from the CRM is CORRECTLY PRESENT "
        "as a valid owner_entity_id on a position (proven independently of the "
        "absence check above)",
        u is not None and pos_ids["units"] is not None,
        f"position {pos_ids['units']} owned by the account entity",
    )
    return asset_ids


# ── Task 3 · transactions and the market check ──────────────────────────────


async def check_transaction_market(conn, entity_ids, asset_ids) -> None:
    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        public_pos = await c.fetchval(
            f"SELECT id::text FROM {TABLE_POSITIONS} WHERE asset_id = $1::uuid",
            asset_ids[FIX_ASSET_UNITS],
        )
        private_pos = await c.fetchval(
            f"SELECT id::text FROM {TABLE_POSITIONS} WHERE asset_id = $1::uuid",
            asset_ids[FIX_ASSET_PCT],
        )

    txn_id = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=public_pos,
        transaction_type_code="buy", trade_date=AS_OF,
        authority="custodial", source_system="altruist",
        quantity=Decimal("100"), price=Decimal("150.00"),
        gross_amount=Decimal("15000.00"), net_amount=Decimal("15009.95"),
        fees=Decimal("9.95"),
    )
    check("3 'buy' (market=public) accepted against a market_price asset",
          bool(txn_id), f"transaction {txn_id}")

    txn2 = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=private_pos,
        transaction_type_code="call_investment", trade_date=AS_OF,
        authority="stated", source_system="manual",
        gross_amount=Decimal("250000.00"),
    )
    check("3 'call_investment' (market=private) accepted against a nav asset",
          bool(txn2), f"transaction {txn2}")

    for code, pos, why in (
        ("call_investment", public_pos,
         "a capital call against a listed, market_price asset"),
        ("buy", private_pos,
         "a buy against a nav-valued private fund interest"),
    ):
        try:
            await record_transaction(
                conn, org_id=DEFAULT_ORG_ID, position_id=pos,
                transaction_type_code=code, trade_date=AS_OF,
                authority="manual", source_system="manual",
                gross_amount=Decimal("1"),
            )
        except TransactionMarketError as exc:
            check(f"3 REJECTS {why}", True, str(exc)[:150])
        except Exception as exc:  # noqa: BLE001
            check(f"3 REJECTS {why}", False,
                  f"raised {type(exc).__name__}, not TransactionMarketError: {exc}")
        else:
            check(f"3 REJECTS {why}", False, "the write was ACCEPTED")

    # 'both' opts out by classification, not by a special case in the checker.
    both_ok = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=private_pos,
        transaction_type_code="interest", trade_date=AS_OF,
        authority="stated", source_system="manual",
        gross_amount=Decimal("1200.00"),
    )
    check(
        "3 'interest' (market=both) is accepted against a PRIVATE asset — the "
        "Task 2 deviation from the brief, working as intended",
        bool(both_ok), f"transaction {both_ok}",
    )

    try:
        await record_transaction(
            conn, org_id=DEFAULT_ORG_ID, position_id=public_pos,
            transaction_type_code="not_a_real_code", trade_date=AS_OF,
            authority="manual", source_system="manual",
        )
    except PortfolioError as exc:
        check("3 an unknown transaction_type_code is refused by name, not by a "
              "bare FK violation", "not_a_real_code" in str(exc), str(exc)[:120])
    else:
        check("3 an unknown transaction_type_code is refused", False, "accepted")


# ── Task 3 · valuation history and the resolver ─────────────────────────────


async def check_valuation_history(conn, asset_ids) -> None:
    asset = asset_ids[FIX_ASSET_VALUATIONS]

    first_id = await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=asset, valuation_date=AS_OF,
        value=Decimal("4200000.00"), status="estimated", value_basis="total",
        currency_code="USD", valuation_source="GP estimate",
    )
    # Snapshot EVERY column of the prior row before the restatement. Comparing
    # only `value` would miss an implementation that closed the row with
    # `valid_to` — which is exactly the mistake this check exists to catch.
    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        before = dict(await c.fetchrow(
            f"SELECT * FROM {TABLE_VALUATIONS} WHERE id = $1::uuid", first_id
        ))

    second_id = await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=asset, valuation_date=AS_OF,
        value=Decimal("3800000.00"), status="audited", value_basis="total",
        currency_code="USD", valuation_source="audited financials",
        supersedes_valuation_id=first_id,
    )

    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        after = dict(await c.fetchrow(
            f"SELECT * FROM {TABLE_VALUATIONS} WHERE id = $1::uuid", first_id
        ))
        second = dict(await c.fetchrow(
            f"SELECT * FROM {TABLE_VALUATIONS} WHERE id = $1::uuid", second_id
        ))

    drift = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    check(
        "3 VALUATION HISTORY — the superseded row is BYTE-IDENTICAL after the "
        "restatement (every column re-read and compared, not just `value`)",
        not drift,
        f"changed columns: {drift or 'none'}",
    )
    check(
        "3 both valuations remain independently queryable, and the supersession "
        "edge is a FORWARD pointer on the new row",
        after["value"] == Decimal("4200000.00")
        and second["value"] == Decimal("3800000.00")
        and str(second["supersedes_valuation_id"]) == first_id
        and after["supersedes_valuation_id"] is None,
        f"prior={after['value']} (status {after['status']!r}), "
        f"new={second['value']} (status {second['status']!r}, "
        f"supersedes={str(second['supersedes_valuation_id'])[:8]}…)",
    )


async def check_value_resolver(conn, asset_ids) -> None:
    """audited beats estimated; a missing mark is NULL-with-a-reason, not zero."""
    asset = asset_ids[FIX_ASSET_VALUATIONS]

    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        resolved = await resolve_current_value(
            c, org_id=DEFAULT_ORG_ID, asset_id=asset
        )
    check(
        "3 the resolver picks 'audited' over 'estimated' when both exist for "
        "the same asset and date",
        resolved.found
        and resolved.value == Decimal("3800000.00")
        and resolved.status == "audited"
        and resolved.is_superseded is False,
        f"value={resolved.value} status={resolved.status!r} "
        f"date={resolved.valuation_date} superseded={resolved.is_superseded}",
    )

    # An asset with no valuation at all. NULL with a reason, never Decimal(0).
    empty_asset = asset_ids[FIX_ASSET_NOVALUE]
    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        missing = await resolve_current_value(
            c, org_id=DEFAULT_ORG_ID, asset_id=empty_asset
        )
    check(
        "3 the resolver returns NULL WITH A CLEAR REASON when no valuation "
        "exists — never zero",
        missing.value is None
        and missing.value != Decimal(0)
        and bool(missing.reason)
        and "zero" in missing.reason,
        f"value={missing.value!r} reason={missing.reason!r}",
    )

    # Same for a date window that predates every mark.
    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        too_early = await resolve_current_value(
            c, org_id=DEFAULT_ORG_ID, asset_id=asset, as_of=date(2020, 1, 1)
        )
    check(
        "3 the resolver returns NULL with a reason for an as_of window that "
        "predates every mark",
        too_early.value is None and "2020-01-01" in (too_early.reason or ""),
        f"reason={too_early.reason!r}",
    )


# ── Cross-org isolation ─────────────────────────────────────────────────────


async def check_cross_org(app_conn, admin_conn, entity_ids, asset_ids) -> None:
    """Isolation on TWO of the six tables, under the real app_service role."""
    who = await app_conn.fetchval("SELECT current_user")
    bypass = await app_conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    check(
        "isolation runs under a role that CANNOT bypass RLS (otherwise every "
        "check below would pass while proving nothing)",
        bypass is False,
        f"current_user={who!r}, rolbypassrls={bypass}",
    )

    # Seed one asset in the OTHER org, using the admin connection so the setup
    # itself is not what is being tested.
    other_asset = await create_asset(
        admin_conn, org_id=OTHER_ORG_ID, name=FIX_ASSET_OTHERORG,
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price",
    )
    other_pos = await create_position(
        admin_conn, org_id=OTHER_ORG_ID,
        owner_entity_id=entity_ids[FIX_OTHERORG_TRUST], asset_id=other_asset,
        as_of_date=AS_OF, authority="stated", source_system="manual",
        quantity=Decimal("10"), market_value=Decimal("1000.00"),
    )

    # TABLE 1 — portfolio.assets
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        seen_asset = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_ASSETS} WHERE id = $1::uuid", other_asset
        )
        own_assets = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_ASSETS} WHERE name = ANY($1::text[])",
            ASSET_NAMES,
        )
    check(
        "ISOLATION portfolio.assets — org A cannot see org B's asset, but does "
        "see its own",
        seen_asset == 0 and own_assets >= 3,
        f"other-org asset visible={seen_asset}, own fixture assets={own_assets}",
    )

    # TABLE 2 — portfolio.positions
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        seen_pos = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_POSITIONS} WHERE id = $1::uuid", other_pos
        )
    check(
        "ISOLATION portfolio.positions — org A cannot see org B's position",
        seen_pos == 0, f"other-org position visible={seen_pos}",
    )

    # And the write side: the WITH CHECK half of the policy, not just USING.
    # A policy with a correct USING and a missing/true WITH CHECK reads
    # correctly and writes across the boundary, and only this catches it.
    wrote_across = None
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        try:
            await c.execute(
                f"INSERT INTO {TABLE_ASSETS} (org_id, name, asset_type) "
                f"VALUES ($1::uuid, $2, 'equity')",
                OTHER_ORG_ID, f"{FIXTURE_TAG} illegal cross-org write",
            )
            wrote_across = True
        except asyncpg.exceptions.InsufficientPrivilegeError as exc:
            wrote_across = False
            detail = type(exc).__name__
    check(
        "ISOLATION portfolio.assets WITH CHECK — org A cannot INSERT a row "
        "stamped with org B's org_id (proves the write half of the policy, not "
        "just the read half)",
        wrote_across is False,
        detail if wrote_across is False else "the cross-org INSERT SUCCEEDED",
    )

    # Teardown for the other-org rows, admin side.
    await admin_conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE id = $1::uuid", other_pos
    )
    await admin_conn.execute(
        f"DELETE FROM {TABLE_ASSETS} WHERE id = $1::uuid", other_asset
    )


# ── Assets / identifiers smoke ──────────────────────────────────────────────


async def check_asset_identifiers(conn, asset_ids) -> None:
    ident_id = await add_identifier(
        conn, org_id=DEFAULT_ORG_ID, asset_id=asset_ids[FIX_ASSET_UNITS],
        id_type="ticker", id_value="  aapl ", is_primary=True,
    )
    async with org_ctx(conn, DEFAULT_ORG_ID, commit=False) as c:
        row = await c.fetchrow(
            f"SELECT id_type, id_value, org_id::text FROM {TABLE_ASSET_IDENT} "
            f"WHERE id = $1::uuid", ident_id,
        )
    check(
        "3 add_identifier normalises and stores (ticker upper-cased, trimmed), "
        "and stamps its OWN org_id",
        row["id_value"] == "AAPL" and row["org_id"] == DEFAULT_ORG_ID,
        f"id_value={row['id_value']!r} org_id={row['org_id'][:8]}…",
    )

    try:
        await add_identifier(
            conn, org_id=DEFAULT_ORG_ID, asset_id=str(uuid.uuid4()),
            id_type="cusip", id_value="037833100",
        )
    except PortfolioError as exc:
        check("3 add_identifier refuses an asset that is not in this org",
              "does not exist in this org" in str(exc), str(exc)[:110])
    else:
        check("3 add_identifier refuses an asset that is not in this org",
              False, "accepted")

    # float is refused outright, not converted — same rule as A1's _money.
    try:
        await record_valuation(
            conn, org_id=DEFAULT_ORG_ID, asset_id=asset_ids[FIX_ASSET_NOVALUE],
            valuation_date=AS_OF, value=4200000.00,
        )
    except PortfolioError as exc:
        check("3 a float monetary value is REFUSED, not silently converted",
              "float" in str(exc), str(exc)[:120])
    else:
        check("3 a float monetary value is REFUSED", False, "accepted")


# ── Main ────────────────────────────────────────────────────────────────────


async def main_async() -> int:
    db_url = os.environ.get("DATABASE_URL")
    app_url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1
    if not app_url:
        print("[FAIL] APP_SERVICE_DATABASE_URL is not set. There is NO SET ROLE "
              "fallback: the cross-org isolation checks are meaningless under a "
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
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[1]}={n}" for t, n in baseline.items()))
        nonempty = {t: n for t, n in baseline.items() if n}
        if nonempty:
            report("TEARDOWN — production-rooted rows are already present",
                   f"{nonempty}. Teardown is by-fixture-name + count assertion, "
                   f"NOT a truncate.")
        else:
            report("TEARDOWN — all six tables start empty",
                   "count-match discipline applied anyway: the moment Phase B "
                   "writes the first real position, a truncate in here becomes "
                   "a data-loss bug nobody notices until quarter-end")
        print()
        await seed_users(admin_conn)

        await check_task1a_schema(admin_conn)
        await check_task1b_public(admin_conn)
        await check_task1c_qualification(app_conn)
        check_task1d_callsites()

        entity_ids = await seed_entities(admin_conn)
        await check_account_node(app_conn, entity_ids)
        await check_account_absent_from_crm(app_conn, entity_ids)
        await check_crm_endpoints_execute(entity_ids)

        asset_ids = await check_ownership_bases(admin_conn, entity_ids)
        asset_ids[FIX_ASSET_VALUATIONS] = await create_asset(
            admin_conn, org_id=DEFAULT_ORG_ID, name=FIX_ASSET_VALUATIONS,
            asset_type="private_fund", ownership_basis="percent",
            valuation_method="nav",
        )
        asset_ids[FIX_ASSET_NOVALUE] = await create_asset(
            admin_conn, org_id=DEFAULT_ORG_ID, name=FIX_ASSET_NOVALUE,
            asset_type="private_fund", ownership_basis="value",
            valuation_method="mark_to_model",
        )

        await check_asset_identifiers(admin_conn, asset_ids)
        await check_transaction_market(admin_conn, entity_ids, asset_ids)
        await check_valuation_history(admin_conn, asset_ids)
        await check_value_resolver(admin_conn, asset_ids)
        await check_cross_org(app_conn, admin_conn, entity_ids, asset_ids)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("SCRIPT COMPLETED WITHOUT AN UNHANDLED EXCEPTION", False,
              f"{type(exc).__name__}: {exc}")
    finally:
        try:
            await teardown(admin_conn)                   # END
            final = await counts(admin_conn)
            if baseline:
                drift = {t: (baseline[t], final[t]) for t in TABLES
                         if baseline[t] != final[t]}
                check(
                    "TEARDOWN — all six portfolio.* tables returned to their "
                    "exact pre-run counts (zero fixture residue, zero "
                    "collateral damage)",
                    not drift,
                    "; ".join(f"{t.split('.')[1]} {b}->{f}"
                              for t, (b, f) in drift.items())
                    or ", ".join(f"{t.split('.')[1]}={n}" for t, n in final.items()),
                )
        finally:
            await admin_conn.close()
            await app_conn.close()
            # The endpoint checks open the app's shared pool. Left open, the
            # script hangs on exit instead of returning its exit code.
            from services.database import close_pool
            await close_pool()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\nRESULT: {'PASS' if failed == 0 else 'FAIL'} "
          f"({len(results)} checks, {passed} passed, {failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
