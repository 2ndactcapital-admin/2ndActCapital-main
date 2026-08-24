"""Verification — Portfolio Phase G: user-defined fields.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END, with an
EXACT before/after count on every table touched — never a truncate. Real
database, real RLS, real ``app_service`` connection, real ``team_members``.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback, for the
same reason A1/A2/B/C/D/E/F require it: ``postgres`` has ``rolbypassrls``, so
every cross-org assertion would "pass" under it while proving nothing.

────────────────────────────────────────────────────────────────────────────
THE FIVE ASSERTIONS THIS PHASE IS EASIEST TO FAKE, AND HOW THEY ARE WRITTEN
────────────────────────────────────────────────────────────────────────────
**"A non-member does not see the team's field."** A resolver that returned an
empty list, or that crashed and was caught, satisfies this on its own. So BOTH
directions are asserted against the SAME call: the member's list is asserted to
CONTAIN the team field, the non-member's to OMIT it, and the non-member's list
is separately asserted to be NON-EMPTY and to contain the platform and org
fields — proving resolution ran and genuinely narrowed rather than failing.

**"A duplicate is refused."** Any exception satisfies "it raised" (Phase B's
finding). So the refusal is asserted to be an ``asyncpg.UniqueViolationError``
translated into ``UdfDuplicateError`` whose ``.constraint`` is literally
``idx_udf_def_key_unique`` — the deployed partial index by name. An
application-level pre-check would raise a ``UdfError`` with no constraint and
fail here.

**"A platform write is refused for a non-super-admin."** Trivially true of code
that never writes at all. So the count of platform-scope rows is snapshotted
before the refusal and asserted UNCHANGED after — and the SAME arguments are
then accepted under a Super-Admin caller, proving the refusal was the privilege
check and not a broken statement.

**"A numeric round-trips."** ``Decimal('1234.5678') == 1234.5678`` is True in
Python for some values, so equality alone can pass on a float that was silently
converted. The assertion compares ``str()`` of the returned Decimal against the
exact literal, AND asserts the type is ``Decimal``, AND asserts a float input
is refused with ``UdfValueTypeError``.

**"The two 'asset_classification' definitions coexist."** Two rows existing
proves nothing about disambiguation. So a DIFFERENT value is recorded against
each, on the SAME target, and each is read back BY ``definition_id`` and
asserted to be its own value — a resolver matching on ``field_key`` would
return the same row twice and fail.

Run:
    python3 scripts/verify_portfoliog.py
"""

from __future__ import annotations

import asyncio
import glob
import inspect
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.extend(sorted(glob.glob(
    os.path.join(_HERE, "..", "venv", "lib", "python3*", "site-packages")
)))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_HERE, "..", ".env"), override=False)

from services import portfolio_udf as udf  # noqa: E402
from services.portfolio_udf import (  # noqa: E402
    TABLE_TEAM_MEMBERS,
    TABLE_TEAMS,
    TABLE_UDF_DEFINITIONS,
    TABLE_UDF_VALUES,
    UDF_DEF_UNIQUE_INDEX,
    UDF_VALUE_UNIQUE_INDEX,
    UdfDuplicateError,
    UdfScopeError,
    UdfTargetMismatchError,
    UdfValueTypeError,
    coerce_value,
    create_org_definition,
    create_platform_definition,
    create_team_definition,
    create_user_definition,
    get_definition,
    get_udf_value,
    is_team_member,
    list_udf_values_for_target,
    record_udf_value,
    resolve_visible_definitions,
)
from services.securities_global import (  # noqa: E402
    SecuritiesGlobalPermissionError,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org, for cross-org isolation. A real row, not a minted one.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

ADMIN_SUB = "auth0|verify_portfoliog_super_admin"
MEMBER_SUB = "auth0|verify_portfoliog_member"      # org A, ON the team
OUTSIDER_SUB = "auth0|verify_portfoliog_outsider"  # org A, NOT on the team
OTHER_SUB = "auth0|verify_portfoliog_otherorg"     # org B
# uuid5(NAMESPACE_URL, sub) — `services.permissions.get_user_id` DERIVES the id
# from the sub rather than looking it up (Phase C's finding), so a fixture
# seeded under a hand-picked literal is a user no code path ever finds.
ADMIN_USER_ID = str(uuid5(NAMESPACE_URL, ADMIN_SUB))
MEMBER_USER_ID = str(uuid5(NAMESPACE_URL, MEMBER_SUB))
OUTSIDER_USER_ID = str(uuid5(NAMESPACE_URL, OUTSIDER_SUB))
OTHER_USER_ID = str(uuid5(NAMESPACE_URL, OTHER_SUB))

FIXTURE_TAG = "VERIFY-PORTFOLIOG"

TEAM_A_NAME = f"{FIXTURE_TAG} Coverage Team"
TEAM_B_NAME = f"{FIXTURE_TAG} Other-Org Team"
TEAM_NAMES = [TEAM_A_NAME, TEAM_B_NAME]

# Every fixture definition's field_key carries the tag, so teardown is by-key.
K_PLATFORM_CLASS = f"{FIXTURE_TAG}_asset_classification"
K_ORG_CLASS = f"{FIXTURE_TAG}_asset_classification"   # SAME key. The whole point.
K_PLATFORM_LIQ = f"{FIXTURE_TAG}_platform_liquidity_tier"
K_ORG_REVIEW = f"{FIXTURE_TAG}_org_review_date"
K_TEAM_NOTE = f"{FIXTURE_TAG}_team_working_note"
K_USER_FLAG = f"{FIXTURE_TAG}_user_watchlist"
K_COMMIT_NUM = f"{FIXTURE_TAG}_commitment_side_letter_fee"
K_ORG_B = f"{FIXTURE_TAG}_orgb_private_field"
K_DUP = f"{FIXTURE_TAG}_duplicate_probe"
K_REFUSED = f"{FIXTURE_TAG}_should_never_exist"

# ── Exact figures. Exact, because "a number came back" is what a typed
#    round-trip is easiest to fake. ─────────────────────────────────────────
NUMERIC_EXACT = "1234.56789012"     # more digits than a float64 holds cleanly
NUMERIC_UPDATED = "9876.54321098"
FLOAT_REFUSED = 1234.56789012
REVIEW_DATE = date(2026, 9, 30)
PLATFORM_CLASS_VALUE = "equity"     # the standard feed says equity …
ORG_CLASS_VALUE = "debt"            # … and this client books it as debt.
CLASS_CHOICES = ["equity", "debt", "hybrid", "real_asset"]

# A synthetic target id. udf_values.target_id is polymorphic and carries NO FK
# — introspected — so it need not reference a real asset, and deliberately does
# not: pointing it at a production asset would make teardown a data risk.
TARGET_ASSET_ID = str(uuid5(NAMESPACE_URL, f"{FIXTURE_TAG}|asset|1"))
TARGET_COMMIT_ID = str(uuid5(NAMESPACE_URL, f"{FIXTURE_TAG}|commitment|1"))

TABLES = (
    TABLE_UDF_VALUES, TABLE_UDF_DEFINITIONS,
    TABLE_TEAM_MEMBERS, TABLE_TEAMS, "public.users",
)

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def report(name: str, detail: str) -> None:
    """A Task 1 finding. Printed as a FINDING, never silently as a PASS."""
    print(f"[FIND] {name}\n       {detail}")


# ── Setup / teardown ────────────────────────────────────────────────────────


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def teardown(conn) -> None:
    """Delete every fixture row, child tables first. Touches nothing else.

    FK order: ``udf_values.definition_id`` references ``udf_definitions``, and
    ``team_members`` references both ``teams`` and ``users``. Fixture
    definitions are matched by the tagged ``field_key`` — including the
    PLATFORM-scope ones, whose ``org_id`` is NULL and which therefore cannot be
    found by any org predicate.
    """
    fixture_defs = (
        f"SELECT id FROM {TABLE_UDF_DEFINITIONS} "
        f"WHERE field_key LIKE '{FIXTURE_TAG}%'"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_UDF_VALUES} WHERE definition_id IN ({fixture_defs}) "
        f"   OR target_id = ANY($1::uuid[])",
        [TARGET_ASSET_ID, TARGET_COMMIT_ID],
    )
    await conn.execute(f"DELETE FROM {TABLE_UDF_DEFINITIONS} WHERE id IN ({fixture_defs})")
    await conn.execute(
        f"DELETE FROM {TABLE_TEAM_MEMBERS} WHERE team_id IN "
        f"(SELECT id FROM {TABLE_TEAMS} WHERE name = ANY($1::text[]))",
        TEAM_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_TEAMS} WHERE name = ANY($1::text[])", TEAM_NAMES
    )
    await conn.execute(
        "DELETE FROM public.users WHERE auth0_sub = ANY($1::text[])",
        [ADMIN_SUB, MEMBER_SUB, OUTSIDER_SUB, OTHER_SUB],
    )


async def seed_users(conn) -> None:
    for user_id, org, sub, role, email in (
        (ADMIN_USER_ID, DEFAULT_ORG_ID, ADMIN_SUB, "super_admin",
         "verify_g_admin@test.local"),
        (MEMBER_USER_ID, DEFAULT_ORG_ID, MEMBER_SUB, "member",
         "verify_g_member@test.local"),
        (OUTSIDER_USER_ID, DEFAULT_ORG_ID, OUTSIDER_SUB, "member",
         "verify_g_outsider@test.local"),
        (OTHER_USER_ID, OTHER_ORG_ID, OTHER_SUB, "member",
         "verify_g_otherorg@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioG', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, org, email, sub, role,
        )


async def seed_teams(conn) -> dict[str, str]:
    """Two teams: one in org A with exactly ONE member, one in org B.

    The org-B team exists solely so the cross-org refusal in
    ``create_team_definition`` has a REAL team to point at. A refusal against a
    randomly minted uuid would also pass, and would pass for the wrong reason
    (no such team at all, rather than a team in the wrong tenant).
    """
    ids = {}
    for key, name, org in (
        ("team_a", TEAM_A_NAME, DEFAULT_ORG_ID),
        ("team_b", TEAM_B_NAME, OTHER_ORG_ID),
    ):
        ids[key] = str(await conn.fetchval(
            f"INSERT INTO {TABLE_TEAMS} (org_id, name, description) "
            f"VALUES ($1::uuid, $2, $3) RETURNING id",
            org, name, f"{FIXTURE_TAG} fixture",
        ))
    # MEMBER is on team A. OUTSIDER is deliberately NOT, and is in the same org.
    await conn.execute(
        f"INSERT INTO {TABLE_TEAM_MEMBERS} (team_id, user_id) "
        f"VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING",
        ids["team_a"], MEMBER_USER_ID,
    )
    return ids


def org_ctx(conn, org_id: str | None, *, super_admin: bool = False,
            sub: str = MEMBER_SUB, commit: bool = True):
    """Transaction on ``conn`` with the RLS GUCs SET LOCAL.

    ``super_admin=False`` is the important default: ``udf_definitions`` and
    ``udf_values`` are the tables under test and every isolation check is only
    meaningful without the escape hatch.
    """

    class _Ctx:
        async def __aenter__(self):
            self.tr = conn.transaction()
            await self.tr.start()
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', $2, true),"
                "       set_config('app.current_auth0_sub', $3, true)",
                org_id or "", "true" if super_admin else "false", sub,
            )
            return conn

        async def __aexit__(self, et, e, tb):
            if et is None and commit:
                await self.tr.commit()
            else:
                await self.tr.rollback()
            return False

    return _Ctx()


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the four findings, REPORTED and ASSERTED
# ═══════════════════════════════════════════════════════════════════════════


async def check_task1a(conn) -> None:
    """1a — both tables, their CHECKs, the partial unique indexes, the policies."""
    cols = {
        t: {r["column_name"] for r in await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'portfolio' AND table_name = $1
            """, t)}
        for t in ("udf_definitions", "udf_values")
    }
    want_defs = {
        "id", "org_id", "owner_scope", "owner_scope_id", "applies_to",
        "field_key", "label", "data_type", "options", "display_order",
        "is_active", "valid_from", "valid_to", "system_from", "system_to",
    }
    want_vals = {
        "id", "org_id", "definition_id", "target_type", "target_id",
        "value_text", "value_numeric", "value_date", "value_json",
        "valid_from", "valid_to", "system_from", "system_to",
    }
    check(
        "[Y] 1a — portfolio.udf_definitions and portfolio.udf_values are both "
        "deployed with the shapes the design describes",
        want_defs <= cols["udf_definitions"] and want_vals <= cols["udf_values"],
        f"defs missing={sorted(want_defs - cols['udf_definitions'])}, "
        f"vals missing={sorted(want_vals - cols['udf_values'])}",
    )

    checks = {
        r["conname"]: r["def"]
        for r in await conn.fetch(
            """
            SELECT c.conname, pg_get_constraintdef(c.oid) AS def
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'portfolio'
              AND t.relname IN ('udf_definitions', 'udf_values')
              AND c.contype = 'c'
            """
        )
    }
    scope_org = checks.get("udf_def_scope_org_chk", "")
    check(
        "[Y] 1a — udf_def_scope_org_chk is REAL and is a schema-level gate: "
        "platform ⇒ org_id NULL, org ⇒ org_id NOT NULL, team/user ⇒ org_id AND "
        "owner_scope_id both NOT NULL",
        bool(scope_org)
        and "'platform'" in scope_org and "'org'" in scope_org
        and "'team'" in scope_org and "'user'" in scope_org
        and "owner_scope_id IS NOT NULL" in scope_org,
        scope_org or "udf_def_scope_org_chk is ABSENT",
    )
    report(
        "1a — udf_def_scope_org_chk is STRICTER than the brief described",
        "the brief said only that org_id is NULL for platform. The deployed "
        "CHECK also requires owner_scope_id IS NULL for platform: "
        f"{scope_org}",
    )
    check(
        "[Y] 1a — udf_def_scope_chk, udf_def_applies_chk, udf_def_type_chk and "
        "udf_values_target_chk all deployed, and the Python vocabularies mirror "
        "them EXACTLY (not a superset a DB 23514 would then reject)",
        _vocab_matches(checks.get("udf_def_scope_chk", ""), udf.OWNER_SCOPES)
        and _vocab_matches(checks.get("udf_def_applies_chk", ""), udf.APPLIES_TO)
        and _vocab_matches(checks.get("udf_def_type_chk", ""), udf.DATA_TYPES)
        and _vocab_matches(checks.get("udf_values_target_chk", ""), udf.TARGET_TYPES),
        f"scopes={sorted(udf.OWNER_SCOPES)}, applies={sorted(udf.APPLIES_TO)}, "
        f"types={sorted(udf.DATA_TYPES)}",
    )

    idx = {
        r["indexname"]: r["indexdef"]
        for r in await conn.fetch(
            """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'portfolio'
              AND tablename IN ('udf_definitions', 'udf_values')
            """
        )
    }
    def_idx = idx.get(UDF_DEF_UNIQUE_INDEX, "")
    val_idx = idx.get(UDF_VALUE_UNIQUE_INDEX, "")
    check(
        f"[Y] 1a — {UDF_DEF_UNIQUE_INDEX} is a PARTIAL UNIQUE index on "
        f"(org, scope, scope_id, applies_to, field_key) restricted to ACTIVE "
        f"rows",
        "UNIQUE" in def_idx and "field_key" in def_idx
        and "owner_scope" in def_idx and "applies_to" in def_idx
        and "valid_to IS NULL" in def_idx and "system_to IS NULL" in def_idx,
        def_idx or f"{UDF_DEF_UNIQUE_INDEX} is ABSENT",
    )
    check(
        "[Y] 1a — that index COALESCEs org_id and owner_scope_id to a zero "
        "uuid, which is what makes PLATFORM duplicates collide at all (NULLs "
        "are distinct in a btree, so a bare column list would never catch them)",
        "COALESCE" in def_idx,
        def_idx or "ABSENT",
    )
    check(
        f"[Y] 1a — {UDF_VALUE_UNIQUE_INDEX} is a PARTIAL UNIQUE index on "
        f"(org_id, definition_id, target_type, target_id) — one CURRENT value "
        f"per definition per target",
        "UNIQUE" in val_idx and "definition_id" in val_idx
        and "target_type" in val_idx and "target_id" in val_idx
        and "valid_to IS NULL" in val_idx,
        val_idx or f"{UDF_VALUE_UNIQUE_INDEX} is ABSENT",
    )

    unique_constraints = [
        r["conname"] for r in await conn.fetch(
            """
            SELECT c.conname FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'portfolio' AND t.relname = 'udf_values'
              AND c.contype = 'u'
            """
        )
    ]
    check(
        "[Y] 1a — udf_values has NO unique CONSTRAINT, only the partial INDEX: "
        "ON CONFLICT must infer the target AND repeat the predicate, or PG "
        "raises 42P10",
        not unique_constraints,
        f"unexpected unique constraints: {unique_constraints}"
        if unique_constraints else "none — inference with predicate required",
    )
    report(
        "1a — the ON CONFLICT clause record_udf_value actually issues",
        f"ON CONFLICT {udf._VALUE_CONFLICT_TARGET}",
    )

    pol = {}
    for r in await conn.fetch(
        """
        SELECT tablename, policyname, cmd FROM pg_policies
        WHERE schemaname = 'portfolio'
          AND tablename IN ('udf_definitions', 'udf_values')
        """
    ):
        pol.setdefault(r["tablename"], []).append((r["policyname"], r["cmd"]))
    n_defs = len(pol.get("udf_definitions", []))
    n_vals = len(pol.get("udf_values", []))
    check(
        "[Y] 1a — the deployed RLS policy counts are EXACTLY 4 on "
        "udf_definitions and 1 on udf_values",
        n_defs == 4 and n_vals == 1,
        f"udf_definitions={n_defs} {sorted(pol.get('udf_definitions', []))}, "
        f"udf_values={n_vals} {sorted(pol.get('udf_values', []))}",
    )

    rls = {
        r["relname"]: (r["relrowsecurity"], r["relforcerowsecurity"])
        for r in await conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'portfolio'
              AND c.relname IN ('udf_definitions', 'udf_values')
            """
        )
    }
    check(
        "[Y] 1a — RLS is ENABLED on both tables",
        all(v[0] for v in rls.values()) and len(rls) == 2,
        str(rls),
    )
    report(
        "1a — RLS is enabled but NOT FORCED on either table",
        f"{rls}. `postgres` owns them and has rolbypassrls, so every isolation "
        f"assertion below runs on the app_service connection. This script "
        f"REFUSES to start without APP_SERVICE_DATABASE_URL for that reason.",
    )


def _vocab_matches(check_def: str, vocabulary) -> bool:
    """The Python frozenset equals the literal list inside the deployed CHECK."""
    if not check_def:
        return False
    return set(re.findall(r"'([a-z_]+)'::text", check_def)) == set(vocabulary)


async def check_task1b(conn) -> None:
    """1b — the REAL teams table and the REAL membership mechanism."""
    team_cols = {
        r["column_name"] for r in await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'teams'
            """
        )
    }
    check(
        "[Y] 1b — public.teams is org-scoped and carries (id, org_id, name, "
        "description)",
        {"id", "org_id", "name", "description"} <= team_cols,
        f"columns={sorted(team_cols)}",
    )

    tm_cols = {
        r["column_name"] for r in await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'team_members'
            """
        )
    }
    tm_pk = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND t.relname = 'team_members'
          AND c.contype = 'p'
        """
    )
    check(
        "[Y] 1b — the REAL team-membership mechanism is public.team_members "
        "(team_id, user_id), PRIMARY KEY (team_id, user_id) — NOT "
        "staff_assignments",
        {"team_id", "user_id"} <= tm_cols and "team_id" in (tm_pk or "")
        and "user_id" in (tm_pk or ""),
        f"columns={sorted(tm_cols)}, pk={tm_pk}",
    )
    check(
        "[Y] 1b — team_members carries NO org_id of its own, so every "
        "membership predicate in portfolio_udf JOINs public.teams to constrain "
        "the tenant (the same shape services.staff_visibility already uses)",
        "org_id" not in tm_cols
        and "JOIN {TABLE_TEAMS} t ON t.id = tm.team_id" in inspect.getsource(udf),
        f"team_members has org_id={('org_id' in tm_cols)}",
    )

    sa_check = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND t.relname = 'staff_assignments'
          AND c.conname = 'staff_assignments_exactly_one_target'
        """
    )
    report(
        "1b — staff_assignments was CONSIDERED and REJECTED as the mechanism",
        f"it maps a team-or-user to an ENTITY "
        f"({sa_check or 'exactly_one_target'}) and answers 'who covers this "
        f"client', not 'who is on this team'. portfolio_udf uses "
        f"public.team_members. Precedent: "
        f"services/staff_visibility.py::get_team_ids_for_users.",
    )
    check(
        "[Y] 1b — portfolio_udf references team_members and does NOT use "
        "staff_assignments for membership",
        "team_members" in inspect.getsource(udf)
        and not re.search(
            r"(?:FROM|JOIN)\s+(?:public\.)?staff_assignments",
            inspect.getsource(udf),
        ),
        "membership resolved through public.team_members only",
    )


def check_task1c() -> None:
    """1c — A1's real global-write pattern, COMPOSED not reinvented."""
    src = inspect.getsource(udf)
    imported = (
        "_require_super_admin" in src and "_SuperAdminWrite" in src
        and "from services.securities_global import" in src
    )
    platform_src = inspect.getsource(udf.create_platform_definition)
    check(
        "[Y] 1c — create_platform_definition COMPOSES A1's real pattern: it "
        "imports _require_super_admin and _SuperAdminWrite from "
        "services.securities_global rather than redefining either",
        imported
        and "_require_super_admin(" in platform_src
        and "_SuperAdminWrite(" in platform_src,
        "imports and calls both",
    )
    check(
        "[Y] 1c — the gate runs BEFORE the elevation, so a refusal never opens "
        "a transaction (which is what makes 'nothing was written' true rather "
        "than merely rolled back)",
        platform_src.index("_require_super_admin(")
        < platform_src.index("_SuperAdminWrite("),
        "_require_super_admin precedes _SuperAdminWrite",
    )
    check(
        "[Y] 1c — no local reimplementation of the Super-Admin gate: "
        "portfolio_udf defines neither _require_super_admin nor "
        "_SuperAdminWrite",
        "def _require_super_admin" not in src
        and "class _SuperAdminWrite" not in src,
        "composed, not copied",
    )


def check_task1d() -> None:
    """1d — A2's division of responsibility, followed here."""
    from services import portfolio_assets as pa

    basis_src = inspect.getsource(pa._validate_basis)
    check(
        "[Y] 1d — A2's precedent is real: portfolio_assets._validate_basis is "
        "the ONLY thing enforcing the ownership-basis contract, because "
        "portfolio.positions has no covering CHECK",
        "THE ONLY THING ENFORCING IT" in basis_src,
        "portfolio_assets._validate_basis",
    )
    check(
        "[Y] 1d — Phase G follows the SAME split: RLS carries the hard boundary "
        "(cross-org + platform global read) and resolve_visible_definitions "
        "carries the team/user narrowing in Python",
        "_VISIBLE_PREDICATE" in inspect.getsource(udf)
        and "owner_scope = 'team'" in udf._VISIBLE_PREDICATE
        and "tm.user_id = $2::uuid" in udf._VISIBLE_PREDICATE,
        "team narrowing lives in _VISIBLE_PREDICATE, not in a policy",
    )
    check(
        "[Y] 1d — portfolio_udf reuses A2's real org-write machinery "
        "(_OrgWrite, _require_org) rather than setting the org GUC by hand",
        "from services.portfolio_assets import" in inspect.getsource(udf)
        and "_OrgWrite" in inspect.getsource(udf)
        and "set_config('app.current_org_id'" not in inspect.getsource(udf),
        "composed from portfolio_assets",
    )


def check_schema_qualification() -> None:
    """Every portfolio.* / public.* reference is schema-qualified. `portfolio`
    is NOT on app_service's search_path — an unqualified FROM works in a psql
    session that happened to SET search_path and raises UndefinedTable in
    production."""
    src = inspect.getsource(udf)
    bare = re.findall(
        r"\b(?:FROM|INTO|UPDATE|JOIN)\s+"
        r"(udf_definitions|udf_values|teams|team_members|users)\b",
        src,
    )
    check(
        "[Y] every table reference in the new module is schema-qualified "
        "(portfolio is NOT on app_service's search_path)",
        not bare,
        f"bare references: {sorted(set(bare))}" if bare else "no bare references",
    )


def check_value_coercion() -> None:
    """The typed-value contract, exercised without touching the database.

    Kept separate from the round-trip assertions on purpose: this proves the
    REFUSALS, which a DB round-trip can never show — a value that was refused
    leaves nothing behind to look at.
    """
    accepted, refused = [], []
    for dtype, value, opts in (
        ("numeric", Decimal("1.5"), None),
        ("numeric", 42, None),
        ("numeric", "3.14159", None),
        ("date", date(2026, 1, 2), None),
        ("date", "2026-01-02", None),
        ("boolean", True, None),
        ("boolean", False, None),
        ("text", "a note", None),
        ("select", "equity", CLASS_CHOICES),
    ):
        try:
            coerce_value(dtype, value, opts)
            accepted.append((dtype, value))
        except UdfValueTypeError:
            pass
    check(
        "[Y] coerce_value accepts Decimal/int/str for numeric, a real date and "
        "an ISO string for date, a real bool for boolean, and an in-list "
        "choice for select",
        len(accepted) == 9,
        f"accepted {len(accepted)}/9: {accepted}",
    )

    for dtype, value, opts in (
        ("numeric", 1.5, None),                     # float
        ("numeric", True, None),                    # bool is not a number
        ("numeric", "not a number", None),
        ("numeric", None, None),
        ("date", datetime(2026, 1, 2, 3, 4), None),  # silent truncation
        ("date", "02/01/2026", None),
        ("boolean", "true", None),                  # truthiness would take it
        ("boolean", 1, None),
        ("text", "", None),
        ("text", 5, None),
        ("select", "commodity", CLASS_CHOICES),     # not in the option list
    ):
        try:
            coerce_value(dtype, value, opts)
        except UdfValueTypeError:
            refused.append((dtype, value))
    check(
        "[Y] coerce_value REFUSES float, bool-as-numeric, unparseable numeric, "
        "None, a datetime (which would truncate silently), a non-ISO date "
        "string, 'true'/1 as boolean, empty/non-string text, and an "
        "out-of-list select choice",
        len(refused) == 11,
        f"refused {len(refused)}/11: {refused}",
    )

    exactly_one = []
    for dtype, value, opts in (
        ("numeric", Decimal("1.5"), None),
        ("date", date(2026, 1, 2), None),
        ("boolean", True, None),
        ("text", "x", None),
        ("select", "debt", CLASS_CHOICES),
    ):
        cols = coerce_value(dtype, value, opts)
        exactly_one.append(sum(1 for v in cols.values() if v is not None) == 1
                           and len(cols) == 4)
    check(
        "[Y] coerce_value returns ALL FOUR value columns with EXACTLY ONE "
        "populated — the other three are written as NULL, so a re-record "
        "cannot strand a stale measure beside the new one",
        all(exactly_one),
        f"{exactly_one}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — DEFINE
# ═══════════════════════════════════════════════════════════════════════════


async def platform_count(conn) -> int:
    return await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_DEFINITIONS} "
        f"WHERE owner_scope = 'platform' AND field_key LIKE '{FIXTURE_TAG}%'"
    )


async def check_platform_gate(admin_conn, app_conn) -> dict[str, str]:
    ids: dict[str, str] = {}

    # ── The REFUSAL first, with a before/after count around it. ──────────────
    before = await platform_count(admin_conn)
    refused_type = None
    try:
        async with org_ctx(app_conn, DEFAULT_ORG_ID, super_admin=False) as c:
            await create_platform_definition(
                c, applies_to="asset", field_key=K_REFUSED,
                label="Should never exist", data_type="text",
                is_super_admin=False,
            )
    except SecuritiesGlobalPermissionError as exc:
        refused_type = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        refused_type = f"WRONG:{type(exc).__name__}: {exc}"
    after = await platform_count(admin_conn)
    orphan = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_DEFINITIONS} WHERE field_key = $1",
        K_REFUSED,
    )
    check(
        "[Y] a platform-scope definition is REJECTED for a non-super-admin "
        "caller, with the SPECIFIC SecuritiesGlobalPermissionError (A1's own "
        "error type) — not merely 'it raised'",
        refused_type == "SecuritiesGlobalPermissionError",
        f"raised {refused_type}",
    )
    check(
        "[Y] NOTHING was written on that refusal: the platform-scope row count "
        "is unchanged and the refused field_key does not exist anywhere",
        before == after and orphan == 0,
        f"platform rows {before} → {after}, orphans={orphan}",
    )

    # ── The SAME arguments, accepted under Super-Admin. ──────────────────────
    async with org_ctx(app_conn, None, super_admin=True, sub=ADMIN_SUB) as c:
        ids["platform_class"] = await create_platform_definition(
            c, applies_to="asset", field_key=K_PLATFORM_CLASS,
            label="Asset classification (industry standard)",
            data_type="select", options=CLASS_CHOICES, is_super_admin=True,
        )
        ids["platform_liq"] = await create_platform_definition(
            c, applies_to="asset", field_key=K_PLATFORM_LIQ,
            label="Liquidity tier", data_type="text", is_super_admin=True,
        )
    row = await admin_conn.fetchrow(
        f"SELECT org_id, owner_scope, owner_scope_id, is_active "
        f"FROM {TABLE_UDF_DEFINITIONS} WHERE id = $1::uuid",
        ids["platform_class"],
    )
    check(
        "[Y] the SAME create succeeds under a Super-Admin caller through the "
        "real app_service connection — so the refusal above was the privilege "
        "check and not a broken statement",
        row is not None and row["owner_scope"] == "platform",
        f"owner_scope={row['owner_scope'] if row else None}",
    )
    check(
        "[Y] the platform row carries org_id NULL and owner_scope_id NULL, as "
        "udf_def_scope_org_chk requires",
        row is not None and row["org_id"] is None
        and row["owner_scope_id"] is None and row["is_active"],
        f"org_id={row['org_id'] if row else '?'}, "
        f"scope_id={row['owner_scope_id'] if row else '?'}",
    )
    return ids


async def check_scoped_creates(app_conn, teams) -> dict[str, str]:
    ids: dict[str, str] = {}

    async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
        ids["org_class"] = await create_org_definition(
            c, org_id=DEFAULT_ORG_ID, applies_to="asset",
            field_key=K_ORG_CLASS,
            label="Asset classification (house view)",
            data_type="select", options=CLASS_CHOICES,
        )
        ids["org_review"] = await create_org_definition(
            c, org_id=DEFAULT_ORG_ID, applies_to="asset",
            field_key=K_ORG_REVIEW, label="Next review date", data_type="date",
        )
        ids["team_note"] = await create_team_definition(
            c, org_id=DEFAULT_ORG_ID, team_id=teams["team_a"],
            applies_to="asset", field_key=K_TEAM_NOTE,
            label="Team working note", data_type="text",
        )
        ids["user_flag"] = await create_user_definition(
            c, org_id=DEFAULT_ORG_ID, user_id=MEMBER_USER_ID,
            applies_to="asset", field_key=K_USER_FLAG,
            label="On my watchlist", data_type="boolean",
        )
        ids["commit_fee"] = await create_org_definition(
            c, org_id=DEFAULT_ORG_ID, applies_to="commitment",
            field_key=K_COMMIT_NUM, label="Side-letter fee", data_type="numeric",
        )

    # Read back INSIDE an org context. `SET LOCAL` dies with its transaction, so
    # a bare fetch on app_conn here would carry no org GUC at all and RLS would
    # correctly return nothing — which would look like a write failure and is
    # not one. (This script failed exactly that way once; the fix is the
    # context, not the assertion.)
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        rows = {
            r["id"]: (r["owner_scope"], str(r["owner_scope_id"] or ""),
                      str(r["org_id"] or ""))
            for r in await c.fetch(
                f"SELECT id::text AS id, owner_scope, owner_scope_id, org_id "
                f"FROM {TABLE_UDF_DEFINITIONS} WHERE id = ANY($1::uuid[])",
                [ids["org_class"], ids["team_note"], ids["user_flag"]],
            )
        }
    check(
        "[Y] org, team and user definitions each succeed under their correct "
        "caller context, with owner_scope_id carrying the TEAM id and the USER "
        "id respectively",
        rows.get(ids["org_class"], (None,))[0] == "org"
        and rows.get(ids["team_note"]) == ("team", teams["team_a"], DEFAULT_ORG_ID)
        and rows.get(ids["user_flag"]) == ("user", MEMBER_USER_ID, DEFAULT_ORG_ID),
        f"{rows}",
    )

    # Org B's own private field — the control for the cross-org read below.
    async with org_ctx(app_conn, OTHER_ORG_ID, sub=OTHER_SUB) as c:
        ids["orgb_field"] = await create_org_definition(
            c, org_id=OTHER_ORG_ID, applies_to="asset", field_key=K_ORG_B,
            label="Org B private field", data_type="text",
        )
    check(
        "[Y] org B can create its OWN org-scope definition through the same "
        "code path — the control that makes 'org A cannot see it' isolation "
        "rather than a broken write",
        bool(ids.get("orgb_field")),
        f"orgb definition id={ids.get('orgb_field')}",
    )
    return ids


async def check_cross_org_team_refusal(app_conn, admin_conn, teams) -> None:
    """A team_id from ANOTHER org, refused at creation."""
    before = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_DEFINITIONS} "
        f"WHERE field_key LIKE '{FIXTURE_TAG}%'"
    )
    raised = None
    try:
        async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
            await create_team_definition(
                c, org_id=DEFAULT_ORG_ID, team_id=teams["team_b"],
                applies_to="asset", field_key=K_REFUSED,
                label="Should never exist", data_type="text",
            )
    except UdfScopeError as exc:
        raised = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        raised = f"WRONG:{type(exc).__name__}: {exc}"
    after = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_DEFINITIONS} "
        f"WHERE field_key LIKE '{FIXTURE_TAG}%'"
    )
    check(
        "[Y] a team definition naming a REAL team that belongs to ANOTHER org "
        "is REFUSED at creation with UdfScopeError — owner_scope_id is "
        "polymorphic and has no FK, so nothing downstream would have caught it",
        raised == "UdfScopeError" and before == after,
        f"raised {raised}; definition count {before} → {after}",
    )

    raised_user = None
    try:
        async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
            await create_user_definition(
                c, org_id=DEFAULT_ORG_ID, user_id=OTHER_USER_ID,
                applies_to="asset", field_key=K_REFUSED,
                label="Should never exist", data_type="text",
            )
    except UdfScopeError as exc:
        raised_user = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        raised_user = f"WRONG:{type(exc).__name__}: {exc}"
    check(
        "[Y] a user definition naming a REAL user in ANOTHER org is likewise "
        "refused at creation",
        raised_user == "UdfScopeError",
        f"raised {raised_user}",
    )


async def check_duplicate_is_the_database(app_conn, admin_conn, teams) -> None:
    """The duplicate gate is the INDEX, asserted by name."""
    async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
        first = await create_org_definition(
            c, org_id=DEFAULT_ORG_ID, applies_to="asset", field_key=K_DUP,
            label="Duplicate probe", data_type="text",
        )
    check("[Y] the first definition in a namespace is created", bool(first),
          f"id={first}")

    raised, constraint = None, None
    try:
        async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
            await create_org_definition(
                c, org_id=DEFAULT_ORG_ID, applies_to="asset", field_key=K_DUP,
                label="Duplicate probe (second)", data_type="text",
            )
    except UdfDuplicateError as exc:
        raised, constraint = type(exc).__name__, exc.constraint
    except Exception as exc:  # noqa: BLE001
        raised = f"WRONG:{type(exc).__name__}: {exc}"
    n = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_DEFINITIONS} WHERE field_key = $1",
        K_DUP,
    )
    check(
        f"[Y] a DUPLICATE definition (same org + scope + applies_to + "
        f"field_key) is refused BY THE DATABASE: the exception carries "
        f"constraint={UDF_DEF_UNIQUE_INDEX!r}, which an application-level "
        f"pre-check could not produce",
        raised == "UdfDuplicateError" and constraint == UDF_DEF_UNIQUE_INDEX
        and n == 1,
        f"raised={raised}, constraint={constraint!r}, rows with that key={n}",
    )
    src = inspect.getsource(udf)
    preflight = re.findall(r"SELECT[^\"']*field_key\s*=", src)
    check(
        "[Y] portfolio_udf issues NO pre-flight SELECT looking for an existing "
        "field_key — the INSERT itself is the check, so there is no race "
        "between two concurrent creates and no way for the assertion above to "
        "pass with the index dropped",
        "asyncpg.UniqueViolationError" in inspect.getsource(udf._insert_definition)
        and not preflight,
        f"pre-flight lookups found: {preflight}" if preflight
        else "duplicate detection is exception-driven",
    )

    # The SAME field_key in a DIFFERENT namespace must NOT collide.
    async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
        sibling = await create_team_definition(
            c, org_id=DEFAULT_ORG_ID, team_id=teams["team_a"],
            applies_to="asset", field_key=K_DUP,
            label="Duplicate probe (team namespace)", data_type="text",
        )
    check(
        "[Y] the SAME field_key in a DIFFERENT namespace (team scope) is NOT a "
        "duplicate — the index keys on the whole namespace, so the refusal "
        "above was a real collision and not a blanket ban on the key",
        bool(sibling) and sibling != first,
        f"org-scope={first}, team-scope={sibling}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — RESOLVE
# ═══════════════════════════════════════════════════════════════════════════


async def check_resolution(app_conn, ids, teams) -> None:
    async with org_ctx(app_conn, DEFAULT_ORG_ID, sub=MEMBER_SUB, commit=False) as c:
        member_on = await is_team_member(
            c, org_id=DEFAULT_ORG_ID, team_id=teams["team_a"],
            user_id=MEMBER_USER_ID)
        outsider_on = await is_team_member(
            c, org_id=DEFAULT_ORG_ID, team_id=teams["team_a"],
            user_id=OUTSIDER_USER_ID)
        member = await resolve_visible_definitions(
            c, org_id=DEFAULT_ORG_ID, user_id=MEMBER_USER_ID,
            applies_to="asset")
        outsider = await resolve_visible_definitions(
            c, org_id=DEFAULT_ORG_ID, user_id=OUTSIDER_USER_ID,
            applies_to="asset")

    check(
        "[Y] the REAL membership mechanism answers both ways: MEMBER is on the "
        "team per public.team_members, OUTSIDER (same org) is not",
        member_on is True and outsider_on is False,
        f"member={member_on}, outsider={outsider_on}",
    )

    m_ids = {d["id"] for d in member}
    o_ids = {d["id"] for d in outsider}

    check(
        "[Y] RESOLUTION — the team MEMBER sees the team-scope definition",
        ids["team_note"] in m_ids,
        f"member sees {len(member)} definitions",
    )
    check(
        "[Y] RESOLUTION — a DIFFERENT user in the SAME org who is NOT on that "
        "team does NOT see it (proven directly, not inferred from the member's "
        "result)",
        ids["team_note"] not in o_ids,
        f"outsider sees {len(outsider)} definitions",
    )
    check(
        "[Y] RESOLUTION — and the outsider's list is NON-EMPTY and contains "
        "the platform and org definitions: the resolver ran and NARROWED, "
        "rather than returning nothing and passing the negative half by "
        "accident",
        o_ids and {ids["platform_class"], ids["platform_liq"],
                   ids["org_class"], ids["org_review"]} <= o_ids,
        f"outsider ids ⊇ platform+org: "
        f"{sorted({ids['platform_class'], ids['platform_liq'], ids['org_class'], ids['org_review']} - o_ids)} missing",
    )
    check(
        "[Y] RESOLUTION — the user-scope definition belongs to MEMBER and is "
        "invisible to OUTSIDER",
        ids["user_flag"] in m_ids and ids["user_flag"] not in o_ids,
        f"in member={ids['user_flag'] in m_ids}, "
        f"in outsider={ids['user_flag'] in o_ids}",
    )
    check(
        "[Y] RESOLUTION — applies_to genuinely filters: the 'commitment' "
        "definition does not appear in an 'asset' resolution",
        ids["commit_fee"] not in m_ids,
        f"commitment field leaked={ids['commit_fee'] in m_ids}",
    )

    # ── Cross-org, through the REAL app_service connection. ─────────────────
    async with org_ctx(app_conn, OTHER_ORG_ID, sub=OTHER_SUB, commit=False) as c:
        org_b = await resolve_visible_definitions(
            c, org_id=OTHER_ORG_ID, user_id=OTHER_USER_ID, applies_to="asset")
        leaked_def = await get_definition(c, definition_id=ids["org_class"])
    b_ids = {d["id"] for d in org_b}

    check(
        "[Y] RESOLUTION — PLATFORM definitions appear for EVERY org: org B, a "
        "different tenant with a different user, sees both of them",
        {ids["platform_class"], ids["platform_liq"]} <= b_ids,
        f"org B sees {len(org_b)} definitions",
    )
    check(
        "[Y] RESOLUTION — org A's org/team/user-scope definitions do NOT "
        "appear for org B",
        not ({ids["org_class"], ids["org_review"], ids["team_note"],
              ids["user_flag"]} & b_ids),
        f"leaked: {sorted({ids['org_class'], ids['org_review'], ids['team_note'], ids['user_flag']} & b_ids)}",
    )
    check(
        "[Y] RESOLUTION — org B's OWN definition DOES appear for org B: the "
        "control proving the empty intersection above is isolation, not an "
        "empty resolver",
        ids["orgb_field"] in b_ids,
        f"orgb_field present={ids['orgb_field'] in b_ids}",
    )
    check(
        "[Y] a direct get_definition on org A's definition returns None from "
        "org B's context — RLS, on the real app_service connection, with no "
        "Python org predicate helping it",
        leaked_def is None,
        f"got {leaked_def}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 4 + 5 — VALUES and PARALLEL NAMESPACES
# ═══════════════════════════════════════════════════════════════════════════


async def check_values(app_conn, admin_conn, ids) -> None:
    async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
        num_id = await record_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["commit_fee"],
            target_type="commitment", target_id=TARGET_COMMIT_ID,
            value=Decimal(NUMERIC_EXACT),
        )
        await record_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["org_review"],
            target_type="asset", target_id=TARGET_ASSET_ID, value=REVIEW_DATE,
        )
        await record_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["user_flag"],
            target_type="asset", target_id=TARGET_ASSET_ID, value=True,
        )

    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        got = await get_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["commit_fee"],
            target_type="commitment", target_id=TARGET_COMMIT_ID)
        got_date = await get_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["org_review"],
            target_type="asset", target_id=TARGET_ASSET_ID)
        got_bool = await get_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["user_flag"],
            target_type="asset", target_id=TARGET_ASSET_ID)

    check(
        "[Y] VALUES — a numeric round-trips as an EXACT Decimal: str() of the "
        "returned value equals the literal digit-for-digit, and the type is "
        "Decimal (equality alone would pass on a silently converted float)",
        got is not None and isinstance(got["value_numeric"], Decimal)
        and str(got["value_numeric"]) == NUMERIC_EXACT,
        f"got {got['value_numeric']!r} ({type(got['value_numeric']).__name__})"
        if got else "no row",
    )
    check(
        "[Y] VALUES — a date round-trips as a real date, and a boolean as a "
        "real bool through value_json",
        got_date is not None and got_date["value_date"] == REVIEW_DATE
        and got_bool is not None and got_bool["value_json"] is True,
        f"date={got_date['value_date'] if got_date else None}, "
        f"bool={got_bool['value_json'] if got_bool else None!r}",
    )

    # ── The float refusal, against the real definition. ──────────────────────
    raised = None
    try:
        async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
            await record_udf_value(
                c, org_id=DEFAULT_ORG_ID, definition_id=ids["commit_fee"],
                target_type="commitment", target_id=TARGET_COMMIT_ID,
                value=FLOAT_REFUSED,
            )
    except UdfValueTypeError as exc:
        raised = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        raised = f"WRONG:{type(exc).__name__}: {exc}"
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        still = await get_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["commit_fee"],
            target_type="commitment", target_id=TARGET_COMMIT_ID)
    check(
        "[Y] VALUES — a float is REFUSED per A2's established convention, and "
        "the stored value is untouched by the attempt",
        raised == "UdfValueTypeError" and still is not None
        and str(still["value_numeric"]) == NUMERIC_EXACT,
        f"raised={raised}, stored still "
        f"{still['value_numeric'] if still else None}",
    )

    # ── Re-record: UPDATE, not duplicate. ───────────────────────────────────
    async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
        num_id2 = await record_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["commit_fee"],
            target_type="commitment", target_id=TARGET_COMMIT_ID,
            value=Decimal(NUMERIC_UPDATED),
        )
    n_rows = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_VALUES} "
        f"WHERE definition_id = $1::uuid AND target_id = $2::uuid",
        ids["commit_fee"], TARGET_COMMIT_ID,
    )
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        after = await get_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["commit_fee"],
            target_type="commitment", target_id=TARGET_COMMIT_ID)
    check(
        "[Y] VALUES — recording a second value for the SAME target UPDATES via "
        "the real partial unique index: still exactly ONE row, the SAME row id, "
        "and the new value — a plain INSERT would have made two, and an "
        "unrelated close-and-insert would have changed the id",
        n_rows == 1 and num_id2 == num_id
        and after is not None and str(after["value_numeric"]) == NUMERIC_UPDATED,
        f"rows={n_rows}, id stable={num_id2 == num_id}, "
        f"value={after['value_numeric'] if after else None}",
    )

    # ── target_type / applies_to mismatch. ──────────────────────────────────
    raised_mm = None
    try:
        async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
            await record_udf_value(
                c, org_id=DEFAULT_ORG_ID, definition_id=ids["commit_fee"],
                target_type="asset", target_id=TARGET_ASSET_ID,
                value=Decimal("1"),
            )
    except UdfTargetMismatchError as exc:
        raised_mm = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        raised_mm = f"WRONG:{type(exc).__name__}: {exc}"
    stray = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_VALUES} "
        f"WHERE definition_id = $1::uuid AND target_type = 'asset'",
        ids["commit_fee"],
    )
    check(
        "[Y] VALUES — a numeric field defined for 'commitment' REFUSES a value "
        "keyed to an 'asset' target, with the specific UdfTargetMismatchError, "
        "and writes nothing",
        raised_mm == "UdfTargetMismatchError" and stray == 0,
        f"raised={raised_mm}, stray rows={stray}",
    )


async def check_parallel_namespaces(app_conn, admin_conn, ids) -> None:
    """Task 5 — the core design claim, with real data."""
    both = await admin_conn.fetch(
        f"SELECT id::text AS id, owner_scope, label FROM {TABLE_UDF_DEFINITIONS} "
        f"WHERE field_key = $1 AND applies_to = 'asset' "
        f"  AND owner_scope IN ('platform', 'org') "
        f"ORDER BY owner_scope",
        K_PLATFORM_CLASS,
    )
    check(
        "[Y] PARALLEL NAMESPACES — a PLATFORM 'asset_classification' and an ORG "
        "'asset_classification' for the SAME org and the SAME applies_to both "
        "exist, with the SAME field_key and different ids. No error, no "
        "collision",
        len(both) == 2
        and {r["owner_scope"] for r in both} == {"platform", "org"}
        and both[0]["id"] != both[1]["id"],
        f"{[(r['owner_scope'], r['id']) for r in both]}",
    )
    check(
        "[Y] PARALLEL NAMESPACES — the field_key is LITERALLY identical on "
        "both, which is what makes this a namespace test rather than two "
        "unrelated fields",
        K_PLATFORM_CLASS == K_ORG_CLASS,
        f"field_key={K_PLATFORM_CLASS!r} on both",
    )

    # Both resolve, independently, in ONE list.
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        visible = await resolve_visible_definitions(
            c, org_id=DEFAULT_ORG_ID, user_id=MEMBER_USER_ID,
            applies_to="asset")
    same_key = [d for d in visible if d["field_key"] == K_PLATFORM_CLASS]
    check(
        "[Y] PARALLEL NAMESPACES — resolve_visible_definitions returns BOTH, "
        "side by side, each carrying its own owner_scope. Nothing merged, "
        "nothing suppressed, no winner picked",
        len(same_key) == 2
        and {d["owner_scope"] for d in same_key} == {"platform", "org"}
        and {d["id"] for d in same_key} == {ids["platform_class"], ids["org_class"]},
        f"{[(d['owner_scope'], d['label']) for d in same_key]}",
    )

    # DIFFERENT values against each, on the SAME target.
    async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
        v_platform = await record_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["platform_class"],
            target_type="asset", target_id=TARGET_ASSET_ID,
            value=PLATFORM_CLASS_VALUE,
        )
        v_org = await record_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["org_class"],
            target_type="asset", target_id=TARGET_ASSET_ID,
            value=ORG_CLASS_VALUE,
        )
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False) as c:
        read_platform = await get_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["platform_class"],
            target_type="asset", target_id=TARGET_ASSET_ID)
        read_org = await get_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["org_class"],
            target_type="asset", target_id=TARGET_ASSET_ID)
        panel = await list_udf_values_for_target(
            c, org_id=DEFAULT_ORG_ID, user_id=MEMBER_USER_ID,
            target_type="asset", target_id=TARGET_ASSET_ID)

    check(
        "[Y] PARALLEL NAMESPACES — a DIFFERENT value is recorded against each "
        "on the SAME target ('equity' from the standard feed, 'debt' as the "
        "house view) and each reads back as ITS OWN value, keyed by "
        "definition_id",
        v_platform != v_org
        and read_platform is not None and read_org is not None
        and read_platform["value_text"] == PLATFORM_CLASS_VALUE
        and read_org["value_text"] == ORG_CLASS_VALUE,
        f"platform→{read_platform['value_text'] if read_platform else None!r}, "
        f"org→{read_org['value_text'] if read_org else None!r}",
    )
    check(
        "[Y] PARALLEL NAMESPACES — disambiguation is by definition_id and "
        "NOTHING ELSE: get_udf_value has no field_key parameter, so an "
        "inferred-match implementation could not have produced the two "
        "different answers above",
        "field_key" not in inspect.signature(get_udf_value).parameters
        and "definition_id" in inspect.signature(get_udf_value).parameters,
        f"parameters={list(inspect.signature(get_udf_value).parameters)}",
    )
    dupes = [p for p in panel if p["field_key"] == K_PLATFORM_CLASS]
    check(
        "[Y] PARALLEL NAMESPACES — the target's value panel shows BOTH values "
        "for that field_key, each labelled with its owner_scope, so a reader "
        "can reconcile the standard feed against the house view instead of "
        "seeing only a winner",
        len(dupes) == 2
        and {(d["owner_scope"], d["value_text"]) for d in dupes}
        == {("platform", PLATFORM_CLASS_VALUE), ("org", ORG_CLASS_VALUE)},
        f"{[(d['owner_scope'], d['value_text']) for d in dupes]}",
    )
    check(
        "[Y] the value panel is itself team/user-narrowed — it JOINs the SAME "
        "_VISIBLE_PREDICATE rather than filtering after the fact, so a "
        "team-scope value cannot leak by reading the value table directly",
        "_VISIBLE_PREDICATE" in inspect.getsource(list_udf_values_for_target),
        "list_udf_values_for_target reuses _VISIBLE_PREDICATE",
    )


async def check_cross_org_values(app_conn, admin_conn, ids) -> None:
    """Cross-org isolation on VALUES, against the real app_service connection."""
    # Org B writes its own value against its own definition — the control.
    async with org_ctx(app_conn, OTHER_ORG_ID, sub=OTHER_SUB) as c:
        b_value = await record_udf_value(
            c, org_id=OTHER_ORG_ID, definition_id=ids["orgb_field"],
            target_type="asset", target_id=TARGET_ASSET_ID, value="org B only",
        )
    check(
        "[Y] CROSS-ORG — org B CAN record a value against its own definition on "
        "the same target id: the control for every 'cannot see' assertion below",
        bool(b_value),
        f"org B value id={b_value}",
    )

    async with org_ctx(app_conn, OTHER_ORG_ID, sub=OTHER_SUB, commit=False) as c:
        leaked = await get_udf_value(
            c, org_id=DEFAULT_ORG_ID, definition_id=ids["org_class"],
            target_type="asset", target_id=TARGET_ASSET_ID)
        b_panel = await list_udf_values_for_target(
            c, org_id=OTHER_ORG_ID, user_id=OTHER_USER_ID,
            target_type="asset", target_id=TARGET_ASSET_ID)
    b_defs = {p["definition_id"] for p in b_panel}
    check(
        "[Y] CROSS-ORG — org A's value on the SAME target id is invisible from "
        "org B's context, even reading with org A's own org_id and "
        "definition_id in hand",
        leaked is None,
        f"got {leaked}",
    )
    check(
        "[Y] CROSS-ORG — org B's panel for that target contains its OWN value "
        "and NONE of org A's, though both orgs wrote against the identical "
        "target_id",
        ids["orgb_field"] in b_defs
        and not ({ids["org_class"], ids["platform_class"], ids["org_review"],
                  ids["user_flag"]} & b_defs),
        f"org B panel definition_ids={sorted(b_defs)}",
    )

    # ── The WRITE boundary, stated as it actually is. ───────────────────────
    #
    # A2's _OrgWrite sets app.current_org_id FROM ITS org_id ARGUMENT. So the
    # question "can org B's connection write into org A" has two different
    # answers depending on which door is used, and both are asserted here
    # rather than only the flattering one.
    raised = None
    try:
        async with org_ctx(app_conn, OTHER_ORG_ID, sub=OTHER_SUB, commit=False) as c:
            await c.execute(
                f"INSERT INTO {TABLE_UDF_VALUES} "
                f"(org_id, definition_id, target_type, target_id, value_text) "
                f"VALUES ($1::uuid, $2::uuid, 'asset', $3::uuid, 'leaked')",
                DEFAULT_ORG_ID, ids["org_class"], TARGET_ASSET_ID,
            )
    except asyncpg.InsufficientPrivilegeError as exc:
        raised = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        raised = f"WRONG:{type(exc).__name__}: {exc}"
    stray = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_VALUES} "
        f"WHERE definition_id = $1::uuid AND value_text = 'leaked'",
        ids["org_class"],
    )
    check(
        "[Y] CROSS-ORG — a RAW write naming org A's org_id, issued on a "
        "connection whose org context is org B, is refused by the deployed "
        "udf_values_org_isolation WITH CHECK — the real policy, on the real "
        "app_service role, leaving nothing behind",
        raised == "InsufficientPrivilegeError" and stray == 0,
        f"raised={raised}, stray rows={stray}",
    )

    # The other door, demonstrated in a transaction that is ROLLED BACK.
    through_service = None
    try:
        async with org_ctx(app_conn, OTHER_ORG_ID, sub=OTHER_SUB, commit=False) as c:
            await record_udf_value(
                c, org_id=DEFAULT_ORG_ID, definition_id=ids["org_class"],
                target_type="asset", target_id=TARGET_ASSET_ID, value="equity",
            )
            through_service = "succeeded"
            raise _Rollback()
    except _Rollback:
        pass
    except Exception as exc:  # noqa: BLE001
        through_service = f"{type(exc).__name__}: {exc}"
    residue = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_VALUES} "
        f"WHERE definition_id = $1::uuid AND value_text = 'equity' "
        f"  AND target_type = 'asset'",
        ids["org_class"],
    )
    check(
        "[Y] CROSS-ORG — and the demonstration left NO row: the transaction "
        "above was rolled back deliberately",
        residue == 0,
        f"residue={residue}",
    )
    report(
        "CROSS-ORG — where the write boundary actually is, stated plainly",
        f"record_udf_value(org_id=<org A>) called on a connection whose "
        f"context is org B {through_service} — because A2's _OrgWrite SETS "
        f"app.current_org_id FROM ITS org_id ARGUMENT, which is the whole "
        f"point of that class. RLS is therefore NOT a defence against a caller "
        f"that passes the wrong org_id; it is a defence against a connection "
        f"that never set one. That is exactly why CLAUDE.md's standing rule is "
        f"'org_id never from a request body' — the router's JWT claim is the "
        f"boundary, and this module never defaults org_id or reads it back off "
        f"the connection. READS are a different matter and are genuinely "
        f"RLS-gated: get_definition and get_udf_value both returned None above "
        f"with org A's ids in hand.",
    )


class _Rollback(Exception):
    """Sentinel: unwind an org_ctx deliberately without leaving a row."""


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    app_url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1
    if not app_url:
        print("[FAIL] APP_SERVICE_DATABASE_URL is not set. There is NO SET ROLE "
              "fallback: every cross-org and team-narrowing assertion is "
              "meaningless under a bypassrls role, so this script fails rather "
              "than pretending.")
        return 1

    admin_conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
    try:
        app_conn = await asyncpg.connect(
            app_url, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] APP_SERVICE_DATABASE_URL did not connect: "
              f"{type(exc).__name__}: {exc}")
        await admin_conn.close()
        return 1

    baseline: dict[str, int] = {}
    try:
        await teardown(admin_conn)                                   # START
        baseline = await counts(admin_conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        nonempty = {t: n for t, n in baseline.items() if n}
        if nonempty:
            report(
                "TEARDOWN — rows are already present in these tables",
                f"{nonempty}. Teardown is by-fixture (every fixture definition's "
                f"field_key carries the {FIXTURE_TAG!r} tag, every fixture team "
                f"carries it in its name) plus an exact count assertion, NOT a "
                f"truncate. public.users and public.teams hold real production "
                f"rows.",
            )

        print("\n── Task 1: DISCOVERY ──")
        await check_task1a(admin_conn)
        await check_task1b(admin_conn)
        check_task1c()
        check_task1d()
        check_schema_qualification()
        check_value_coercion()

        print("\n── Fixtures: four users, two teams, one membership ──")
        await seed_users(admin_conn)
        teams = await seed_teams(admin_conn)

        print("\n── Task 2: DEFINE — platform scope, Super-Admin-gated ──")
        ids = await check_platform_gate(admin_conn, app_conn)

        print("\n── Task 2: DEFINE — org, team and user scopes ──")
        ids.update(await check_scoped_creates(app_conn, teams))
        await check_cross_org_team_refusal(app_conn, admin_conn, teams)
        await check_duplicate_is_the_database(app_conn, admin_conn, teams)

        print("\n── Task 3: RESOLVE — team/user narrowing in the service layer ──")
        await check_resolution(app_conn, ids, teams)

        print("\n── Task 4: VALUES — typed round-trip and upsert ──")
        await check_values(app_conn, admin_conn, ids)

        print("\n── Task 5: PARALLEL NAMESPACES ──")
        await check_parallel_namespaces(app_conn, admin_conn, ids)

        print("\n── Cross-org isolation on VALUES (real app_service connection) ──")
        await check_cross_org_values(app_conn, admin_conn, ids)

    finally:
        await teardown(admin_conn)                                   # END
        if baseline:
            final = await counts(admin_conn)
            drift = {
                t: (baseline[t], final[t]) for t in TABLES if baseline[t] != final[t]
            }
            check(
                "[Y] TEARDOWN restores the EXACT before-count on every table "
                "touched — including portfolio.udf_definitions and "
                "portfolio.udf_values",
                not drift,
                f"drift (before, after): {drift}" if drift
                else ", ".join(f"{t.split('.')[-1]}={final[t]}" for t in TABLES),
            )
        await app_conn.close()
        await admin_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}")
    print(f"RESULT: {passed}/{total} passed")
    failures = [(n, d) for n, ok, d in results if not ok]
    if failures:
        print("\nFAILURES:")
        for name, detail in failures:
            print(f"  · {name} — {detail}")
    print("=" * 72)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
