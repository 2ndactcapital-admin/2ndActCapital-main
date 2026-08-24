"""Verification — Portfolio Phase D: SPV derivation view, cash, document
drill-through.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END.
Real database, real RLS, real ``app_service`` connection, real SPV row.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback, for the
same reason A1, A2, B and C require it: ``postgres`` has ``rolbypassrls``, so
every cross-org assertion would "pass" under it while proving nothing. A missing
or non-connecting app_service credential FAILS this script rather than degrading
it — and in this phase that matters more than in any previous one, because the
thing being isolated is a VIEW, and a view is org-isolated only if somebody
remembered ``security_invoker``.

────────────────────────────────────────────────────────────────────────────
TEARDOWN: BEFORE/AFTER COUNTS, NOT TRUNCATE
────────────────────────────────────────────────────────────────────────────
Inherited unchanged from A1/A2/B/C. Every table touched is counted before the
run and after teardown and the counts must match EXACTLY. ``spv_subscriptions``,
``spvs`` and ``deals`` in particular hold REAL production rows today (2 / 1 / n),
so an unconditional truncate would be a data-loss bug against the SPV Manager
track.

One teardown-driven design choice worth stating, because it looks like a
shortcut and is not: the cash assertions use ISO-4217 ``XTS`` (the code reserved
for testing) and ``XXX`` (the code for "no currency"), NOT ``USD``. A cash asset
is keyed on ``(org_id, currency_code)`` and is therefore ORG-GLOBAL — it carries
no fixture name to delete by. A fixture that created ``Cash (USD)`` would either
delete a real org-wide cash asset on teardown, or, if one already existed, find
it instead of creating it and prove nothing about creation. XTS/XXX cannot
collide with real data and prove the same property.

Run:
    python3 scripts/verify_portfoliod.py
"""

from __future__ import annotations

import ast
import asyncio
import glob
import os
import sys
from datetime import date
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

from services.portfolio_assets import (  # noqa: E402
    TABLE_ASSETS,
    TABLE_ASSET_IDENT,
    TABLE_EXT_REF,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    TABLE_VALUATIONS,
    PortfolioError,
    record_transaction,
    record_valuation,
    resolve_current_value,
)
from services.portfolio_cash import (  # noqa: E402
    CASH_ASSET_TYPE,
    cash_asset_name,
    ensure_cash_asset,
    get_cash_balance,
    record_cash_balance,
)
from services.portfolio_documents import (  # noqa: E402
    PORTFOLIO_RECORD_TYPES,
    RECORD_TYPE_ASSET,
    RECORD_TYPE_POSITION,
    RECORD_TYPE_TRANSACTION,
    RECORD_TYPE_VALUATION,
    link_portfolio_document,
    list_portfolio_record_documents,
    record_valuation_from_document,
)
from services.portfolio_spv import (  # noqa: E402
    ACTIVE_SUBSCRIPTION_STATUSES,
    DERIVED_POSITION_NAMESPACE,
    SPV_AUTHORITY,
    SPV_SOURCE_SYSTEM,
    TABLE_SPV_POSITIONS,
    TABLE_SPV_SUBSCRIPTIONS,
    TABLE_SPVS,
    derived_position_id,
    ensure_spv_asset,
    get_derived_position,
    list_derived_positions,
    unprojected_subscriptions,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org, for cross-org isolation. A real row, not a minted one.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

ADMIN_SUB = "auth0|verify_portfoliod_super_admin"
MEMBER_SUB = "auth0|verify_portfoliod_member"
# uuid5(NAMESPACE_URL, sub), NOT a hand-picked 99000000-… literal — Phase C's
# finding: `services.permissions.get_user_id` DERIVES the id from the sub rather
# than looking it up, so a fixture seeded under a chosen id is a user no code
# path ever finds.
ADMIN_USER_ID = str(uuid5(NAMESPACE_URL, ADMIN_SUB))
MEMBER_USER_ID = str(uuid5(NAMESPACE_URL, MEMBER_SUB))

FIXTURE_TAG = "VERIFY-PORTFOLIOD"

# ── Fixture names, declared UP FRONT and never appended to at runtime: a name
#    minted mid-run is one the NEXT run's start-teardown cannot find, so a crash
#    between minting it and the end-teardown strands it permanently. ──────────
DEAL_NAME = f"{FIXTURE_TAG} Meridian Industrial Portfolio"
SPV_NAME = f"{FIXTURE_TAG} Meridian Co-Invest SPV I"
DEAL_NAMES = [DEAL_NAME]
SPV_NAMES = [SPV_NAME]

E_SUBSCRIBER = f"{FIXTURE_TAG} Ashgrove Family LLC"
E_RETIRED_SUB = f"{FIXTURE_TAG} Belmont Holdings LLC"
E_BANK = f"{FIXTURE_TAG} Fairmount Bank Operating Account"
E_TRUST = f"{FIXTURE_TAG} Calder Family Trust"
ENTITY_NAMES = [E_SUBSCRIBER, E_RETIRED_SUB, E_BANK, E_TRUST]

DOC_NAME = f"{FIXTURE_TAG} Q2-2026 Capital Account Statement.pdf"
DOC_NAMES = [DOC_NAME]

# ── Cash. XTS / XXX, not USD — see the module docstring. ────────────────────
CASH_CCY = "XTS"
CASH_CCY_ALT = "XXX"
CASH_CCY_LOWER = "xts"     # the same currency, spelled the way a caller would
CASH_ASSET_NAMES = [cash_asset_name(CASH_CCY), cash_asset_name(CASH_CCY_ALT)]

# ── The exact figures every assertion is measured against. Exact, because
#    "a number came back" is the assertion this phase is easiest to fake. ────
PCT_CURRENT = Decimal("25")            # the current subscription's ownership
PCT_EDITED = Decimal("40")             # after an edit to the book of record
PCT_RETIRED = Decimal("10")            # the superseded subscription — must
                                       # never appear at any value
COMMITMENT = Decimal("1000000.00")
FUNDED = Decimal("1000000.00")

NAV_SUPERSEDED = Decimal("500000.00")  # audited, but superseded → demoted to 9
NAV_GOVERNING = Decimal("800000.00")   # final, supersedes the above → wins
NAV_LATER = Decimal("1000000.00")      # a later date → wins over both

V_BASELINE = Decimal("200000.00")      # 25% of 800,000
V_AFTER_PCT_EDIT = Decimal("320000.00")  # 40% of 800,000
V_AFTER_NAV_EDIT = Decimal("250000.00")  # 25% of 1,000,000
V_NAIVE_STATUS_ONLY = Decimal("125000.00")  # 25% of 500,000 — the wrong answer
                                            # a status-only ladder would give

CASH_BANK = Decimal("250000.00")
CASH_TRUST = Decimal("18500.75")

VAL_DATE = date(2026, 6, 30)
VAL_DATE_LATER = date(2026, 7, 31)
AS_OF = date(2026, 6, 30)

TABLES = (
    TABLE_ASSETS, TABLE_ASSET_IDENT, TABLE_POSITIONS,
    TABLE_VALUATIONS, TABLE_TRANSACTIONS, TABLE_EXT_REF,
    TABLE_SPV_SUBSCRIPTIONS, TABLE_SPVS, "public.deals",
    "public.documents", "public.document_record_links", "public.entities",
)

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def report(name: str, detail: str) -> None:
    """A Task 1 finding. Printed as a FINDING, never silently as a PASS."""
    print(f"[FIND] {name}\n       {detail}")


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# ── Setup / teardown ────────────────────────────────────────────────────────


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def teardown(conn) -> None:
    """Delete every fixture row, child tables first. Touches nothing else."""
    fixture_assets = (
        f"SELECT id FROM {TABLE_ASSETS} WHERE internal_spv_id IN "
        f"(SELECT id FROM {TABLE_SPVS} WHERE name = ANY($1::text[])) "
        f"   OR (asset_type = '{CASH_ASSET_TYPE}' "
        f"       AND currency_code = ANY($2::text[]))"
    )
    ca = [CASH_CCY, CASH_CCY_ALT]

    # Document links first: they point at documents AND at portfolio records,
    # both of which are about to go.
    await conn.execute(
        "DELETE FROM public.document_record_links WHERE document_id IN "
        "(SELECT id FROM public.documents WHERE original_filename = ANY($1::text[]))",
        DOC_NAMES,
    )
    await conn.execute(
        "DELETE FROM public.documents WHERE original_filename = ANY($1::text[])",
        DOC_NAMES,
    )

    fixture_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({fixture_assets})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_EXT_REF} WHERE record_id IN ({fixture_positions})",
        SPV_NAMES, ca,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_TRANSACTIONS} WHERE position_id IN ({fixture_positions})",
        SPV_NAMES, ca,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN ({fixture_assets})",
        SPV_NAMES, ca,
    )
    # The forward supersession pointer must be dropped BEFORE the rows it points
    # at, or the delete order is an FK violation on the fixture's own history.
    await conn.execute(
        f"UPDATE {TABLE_VALUATIONS} SET supersedes_valuation_id = NULL "
        f"WHERE asset_id IN ({fixture_assets})",
        SPV_NAMES, ca,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_VALUATIONS} WHERE asset_id IN ({fixture_assets})",
        SPV_NAMES, ca,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_ASSET_IDENT} WHERE asset_id IN ({fixture_assets})",
        SPV_NAMES, ca,
    )
    await conn.execute(f"DELETE FROM {TABLE_ASSETS} WHERE id IN ({fixture_assets})",
                       SPV_NAMES, ca)

    await conn.execute(
        f"DELETE FROM {TABLE_SPV_SUBSCRIPTIONS} WHERE spv_id IN "
        f"(SELECT id FROM {TABLE_SPVS} WHERE name = ANY($1::text[]))",
        SPV_NAMES,
    )
    await conn.execute(f"DELETE FROM {TABLE_SPVS} WHERE name = ANY($1::text[])",
                       SPV_NAMES)
    await conn.execute("DELETE FROM public.deals WHERE name = ANY($1::text[])",
                       DEAL_NAMES)
    await conn.execute(
        "DELETE FROM public.entities WHERE display_name = ANY($1::text[])",
        ENTITY_NAMES,
    )
    await conn.execute(
        "DELETE FROM public.users WHERE auth0_sub = ANY($1::text[])",
        [ADMIN_SUB, MEMBER_SUB],
    )


async def seed_users(conn) -> None:
    for user_id, sub, role, email in (
        (ADMIN_USER_ID, ADMIN_SUB, "super_admin", "verify_d_admin@test.local"),
        (MEMBER_USER_ID, MEMBER_SUB, "member", "verify_d_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioD', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, DEFAULT_ORG_ID, email, sub, role,
        )


async def seed_entity(conn, org_id: str, display_name: str, entity_type: str) -> str:
    return await conn.fetchval(
        "INSERT INTO public.entities (org_id, entity_type, display_name) "
        "VALUES ($1::uuid, $2::entity_type, $3) RETURNING id::text",
        org_id, entity_type, display_name,
    )


async def build_fixtures(conn) -> dict:
    ids: dict = {}
    ids["deal"] = await conn.fetchval(
        "INSERT INTO public.deals (org_id, name, created_by) "
        "VALUES ($1::uuid, $2, $3::uuid) RETURNING id::text",
        DEFAULT_ORG_ID, DEAL_NAME, ADMIN_USER_ID,
    )
    ids["spv"] = await conn.fetchval(
        f"INSERT INTO {TABLE_SPVS} "
        f"(org_id, deal_id, name, spv_status, currency, vehicle_type, created_by) "
        f"VALUES ($1::uuid, $2::uuid, $3, 'open', 'USD', 'standalone_spv', $4::uuid) "
        f"RETURNING id::text",
        DEFAULT_ORG_ID, ids["deal"], SPV_NAME, ADMIN_USER_ID,
    )
    for key, name, etype in (
        ("subscriber", E_SUBSCRIBER, "llc"),
        ("retired_sub", E_RETIRED_SUB, "llc"),
        ("bank", E_BANK, "account"),
        ("trust", E_TRUST, "trust"),
    ):
        ids[key] = await seed_entity(conn, DEFAULT_ORG_ID, name, etype)

    # The CURRENT subscription — a real, closed, funded one with a post-close
    # ownership percentage. This is what must project.
    ids["sub_current"] = await conn.fetchval(
        f"INSERT INTO {TABLE_SPV_SUBSCRIPTIONS} "
        f"(org_id, spv_id, entity_id, commitment_amount, funded_amount, "
        f" ownership_pct, subscription_status, signed_at, created_by) "
        f"VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'funded', now(), $7::uuid) "
        f"RETURNING id::text",
        DEFAULT_ORG_ID, ids["spv"], ids["subscriber"],
        COMMITMENT, FUNDED, PCT_CURRENT, ADMIN_USER_ID,
    )
    # The RETIRED subscription — identical in every respect EXCEPT valid_to.
    # Deliberately still 'funded' with a non-NULL ownership_pct so the ONLY
    # thing keeping it out of the view is the temporal predicate. A fixture that
    # was also soft-circled would pass the exclusion test for the wrong reason.
    ids["sub_retired"] = await conn.fetchval(
        f"INSERT INTO {TABLE_SPV_SUBSCRIPTIONS} "
        f"(org_id, spv_id, entity_id, commitment_amount, funded_amount, "
        f" ownership_pct, subscription_status, signed_at, valid_to, created_by) "
        f"VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, 'funded', now(), "
        f"        now(), $7::uuid) "
        f"RETURNING id::text",
        DEFAULT_ORG_ID, ids["spv"], ids["retired_sub"],
        COMMITMENT, FUNDED, PCT_RETIRED, ADMIN_USER_ID,
    )
    ids["document"] = await conn.fetchval(
        "INSERT INTO public.documents "
        "(org_id, original_filename, source, mime_type, status, doc_family, created_by) "
        "VALUES ($1::uuid, $2, 'upload', 'application/pdf', 'confirmed', "
        "        'statement', $3::uuid) "
        "RETURNING id::text",
        DEFAULT_ORG_ID, DOC_NAME, ADMIN_USER_ID,
    )
    return ids


def org_ctx(conn, org_id: str, *, super_admin: bool = False, commit: bool = True):
    """Transaction on ``conn`` with the RLS GUCs SET LOCAL.

    ``super_admin=False`` is the important default: these are TENANT tables and
    a view, and the isolation check is only meaningful without the escape hatch.
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


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the four findings, reported AND asserted
# ═══════════════════════════════════════════════════════════════════════════


async def check_task1a(conn) -> None:
    """1a — system-time tracking for spv_subscriptions: is there any?"""
    cols = {
        r["column_name"]
        for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='spv_subscriptions'"
        )
    }
    triggers = await conn.fetchval(
        "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "WHERE c.relname='spv_subscriptions' AND NOT t.tgisinternal"
    )
    history_tables = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='public' "
        "  AND (table_name LIKE 'spv_subscription%history' "
        "    OR table_name LIKE 'spv_subscription%audit' "
        "    OR table_name LIKE '%subscriptions_history')"
    )
    has_valid = {"valid_from", "valid_to"} <= cols
    has_system = bool({"system_from", "system_to"} & cols)

    report(
        "1a spv_subscriptions is SINGLE-axis temporal",
        f"columns present: valid_from/valid_to={has_valid}, "
        f"system_from/system_to={has_system}. Non-internal triggers: {triggers}. "
        f"Candidate history/audit tables: {history_tables}. audit_log is "
        f"(action, resource_type, resource_id, payload) and nothing writes "
        f"subscriptions into it. CONCLUSION: `valid_to IS NULL` alone is "
        f"'current' — there is no system-time axis anywhere for this table, "
        f"unlike every portfolio.* table, which has both.",
    )
    check(
        "1a spv_subscriptions has valid_from/valid_to and NO system-time axis, "
        "no trigger and no history table — valid_to IS NULL alone means current",
        has_valid and not has_system and triggers == 0 and history_tables == 0,
        f"valid={has_valid} system={has_system} triggers={triggers} "
        f"history_tables={history_tables}",
    )


async def check_task1b(conn) -> None:
    """1b — where an SPV interest's current market value actually lives."""
    # (i) The GL path. v_capital_accounts exists and looks right, and is not
    #     connected to anything.
    viewdef = await conn.fetchval(
        "SELECT pg_get_viewdef('public.v_capital_accounts'::regclass, true)"
    )
    groups_by_ms = "dim_member_series_id" in (viewdef or "")
    ms_fk = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid='public.journal_lines'::regclass AND contype='f' "
        "  AND 'dim_member_series_id' = ANY ("
        "      SELECT a.attname FROM pg_attribute a "
        "      WHERE a.attrelid=conrelid AND a.attnum = ANY(conkey))"
    )
    ms_relation = await conn.fetchval(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relname LIKE '%member_series%' AND n.nspname NOT LIKE 'pg_%'"
    )
    ms_populated = await conn.fetchval(
        "SELECT count(*) FROM public.journal_lines WHERE dim_member_series_id IS NOT NULL"
    )
    # (ii) spvs itself.
    spv_value_cols = [
        r[0] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='spvs' "
            "  AND (column_name ILIKE '%nav%' OR column_name ILIKE '%value%' "
            "    OR column_name ILIKE '%market%')"
        )
    ]
    # (iii) The join Phase D actually uses — it was already deployed.
    spv_asset_fk = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid='portfolio.assets'::regclass "
        "  AND conname='assets_internal_spv_id_fkey'"
    )

    report(
        "1b THE REAL QUERY PATH to an SPV interest's current value",
        "NO per-subscription current value existed before this phase. Traced:\n"
        "       · spv_subscriptions.commitment_amount / funded_amount — a "
        "commitment and a cost, not a mark.\n"
        f"       · spvs — no NAV/value/market column at all (matches: "
        f"{spv_value_cols or 'none'}); target_raise / hard_cap / min_commitment "
        f"are fundraising parameters.\n"
        "       · member_investments.amount_committed / amount_funded — same "
        "shape, same problem.\n"
        f"       · Sprint-22 GL: v_capital_accounts groups by "
        f"journal_lines.dim_member_series_id (present={groups_by_ms}) — which "
        f"has {ms_fk} foreign keys, {ms_relation} candidate referent relations "
        f"in the whole database, and is populated on {ms_populated} rows. "
        f"scripts/verify_sprint22.py itself passes str(uuid.uuid4()) for it. "
        f"'member_series' is a spvs.vehicle_type value, so even fully populated "
        f"that dimension is grained at the SERIES, not the SUBSCRIBER.\n"
        "       THE PATH PHASE D USES (the one join that already existed, "
        f"assets_internal_spv_id_fkey present={bool(spv_asset_fk)}):\n"
        "         spv_subscriptions (valid_to IS NULL, status IN "
        "('committed','funded'), ownership_pct NOT NULL)\n"
        "           -> spvs.id -> portfolio.assets.internal_spv_id (ONE per SPV)\n"
        "           -> portfolio.valuations, purpose='market', resolved by A2's "
        "ladder\n"
        "           -> value * ownership_pct / 100",
    )
    check(
        "1b the Sprint-22 GL capital-account path is NOT a usable source: "
        "dim_member_series_id has no FK, no referent relation, and is NULL on "
        "every deployed journal_lines row",
        groups_by_ms and ms_fk == 0 and ms_relation == 0 and ms_populated == 0,
        f"grouped_by_dim={groups_by_ms} fks={ms_fk} referent_relations={ms_relation} "
        f"populated_rows={ms_populated}",
    )
    check(
        "1b spvs carries NO NAV/market-value column, so the SPV's own row "
        "cannot be the source either",
        not spv_value_cols,
        f"value-ish columns on spvs: {spv_value_cols or 'none'}",
    )
    check(
        "1b the join Phase D uses (portfolio.assets.internal_spv_id -> spvs) "
        "was ALREADY deployed — the path is established, not invented",
        spv_asset_fk == 1,
        f"assets_internal_spv_id_fkey present={bool(spv_asset_fk)}",
    )


def check_task1c() -> None:
    """1c — the asset+position convention, and that Phase D follows it."""
    import services.portfolio_assets as pa
    import services.portfolio_cash as pc
    import services.portfolio_spv as ps

    report(
        "1c the established asset+position creation pattern",
        "portfolio_assets.create_asset(conn, *, org_id, name, asset_type, ...) "
        "-> asset id, THEN create_position(conn, *, org_id, owner_entity_id, "
        "asset_id, as_of_date, authority, source_system, ...) -> position id. "
        "Both wrap _OrgWrite, which raises the RLS org GUC so the policy's "
        "WITH CHECK is the real gate. create_position inherits ownership_basis "
        "from the asset when omitted and is the ONLY enforcement of the basis "
        "contract — portfolio.positions has no CHECK covering it. Phase D's "
        "cash and SPV helpers COMPOSE these two; they do not re-implement them.",
    )

    # The composition is asserted from source, not assumed from the docstring.
    cash_src = _executable_source(pc)
    spv_src = _executable_source(ps)
    composes = (
        "create_asset(" in cash_src and "create_position(" in cash_src
        and "create_asset(" in spv_src
    )
    check(
        "1c portfolio_cash and portfolio_spv COMPOSE A2's create_asset / "
        "create_position rather than re-implementing them",
        composes,
        "create_asset+create_position called from portfolio_cash; "
        "create_asset called from portfolio_spv",
    )

    # No parallel insert mechanism: a bare INSERT into either table would bypass
    # the basis contract that has no database backstop.
    bare_writes = sorted({
        f"{mod.__name__}: {frag}"
        for mod, src in ((pc, cash_src), (ps, spv_src))
        for frag in (
            f"INSERT INTO {TABLE_ASSETS}", f"INSERT INTO {TABLE_POSITIONS}",
            "INSERT INTO portfolio.assets", "INSERT INTO portfolio.positions",
        )
        if frag in src
    })
    check(
        "1c NO parallel write mechanism — neither Phase D module contains a "
        "direct INSERT into portfolio.assets or portfolio.positions",
        not bare_writes,
        f"direct inserts found: {bare_writes or 'none'}",
    )

    # And the schema-qualification rule, AST-checked exactly as A2 does it.
    portfolio_names = [
        TABLE_ASSETS, TABLE_POSITIONS, TABLE_VALUATIONS, TABLE_TRANSACTIONS,
        TABLE_ASSET_IDENT, TABLE_EXT_REF, TABLE_SPV_POSITIONS,
    ]
    bare = sorted({
        f"{mod.__name__}:{t.split('.', 1)[1]}"
        for mod, src in ((pc, cash_src), (ps, spv_src))
        for t in portfolio_names
        for kw in ("FROM ", "INTO ", "UPDATE ", "JOIN ")
        if f"{kw}{t.split('.', 1)[1]}" in src
    })
    check(
        "1c both Phase D modules schema-qualify every portfolio reference "
        "(AST-checked: no bare FROM/INTO/UPDATE/JOIN in executable code)",
        not bare,
        f"unqualified references: {bare or 'none'}",
    )
    assert pa  # imported for the report above; keeps linters honest


def _executable_source(module) -> str:
    """Module source with every docstring removed.

    These modules quote the anti-patterns they forbid in order to explain WHY
    they are forbidden — "no bare ``INSERT INTO portfolio.positions``" is a
    sentence, not a bug. A naive text scan flags its own explanation, which
    trains the next person to delete the check rather than the defect.
    """
    src = open(module.__file__).read()
    tree = ast.parse(src)
    docs = [ast.get_docstring(tree, clean=False)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            docs.append(ast.get_docstring(node, clean=False))
    for d in docs:
        if d:
            src = src.replace(d, "")
    return src


async def check_task1d(conn) -> None:
    """1d — document_record_links: is record_type genuinely open text?"""
    cols = {
        r["column_name"]: r["data_type"]
        for r in await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='document_record_links'"
        )
    }
    checks_on_type = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid='public.document_record_links'::regclass AND contype='c'"
    )
    existing_types = [
        r["record_type"] for r in await conn.fetch(
            "SELECT DISTINCT record_type FROM public.document_record_links "
            "ORDER BY record_type"
        )
    ]
    import services.document_linkage as dl
    svc_src = _executable_source(dl)
    # The service validates non-emptiness and nothing else.
    svc_has_vocabulary = any(
        tok in svc_src for tok in ("RECORD_TYPES = frozenset", "record_type not in")
    )

    report(
        "1d document_record_links is genuinely polymorphic — NO migration needed",
        f"record_type is {cols.get('record_type')!r} with {checks_on_type} CHECK "
        f"constraints. Existing values in the table: {existing_types or 'none'}. "
        f"document_linkage.link_document_to_record validates non-emptiness only "
        f"(vocabulary present: {svc_has_vocabulary}); routers/document_links.py "
        f"types it `record_type: str`; the frontend DocumentsPanel passes it "
        f"through to the URL. The four Phase-D values are added by WRITING them.",
    )
    check(
        "1d record_type is unconstrained text with ZERO CHECK constraints — "
        "the four new values need no migration",
        cols.get("record_type") == "text" and checks_on_type == 0
        and not svc_has_vocabulary,
        f"type={cols.get('record_type')} checks={checks_on_type} "
        f"service_vocabulary={svc_has_vocabulary}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — the view itself
# ═══════════════════════════════════════════════════════════════════════════


async def check_view_shape(conn) -> None:
    reloptions = await conn.fetchval(
        f"SELECT reloptions FROM pg_class WHERE oid='{TABLE_SPV_POSITIONS}'::regclass"
    )
    invoker = "security_invoker=true" in (reloptions or [])
    check(
        "the view carries security_invoker=true — WITHOUT it the view runs as "
        "its postgres owner (rolbypassrls) and returns every tenant's "
        "subscriptions to every tenant, silently",
        invoker,
        f"reloptions={reloptions}",
    )

    updatable = await conn.fetchval(
        f"SELECT pg_relation_is_updatable('{TABLE_SPV_POSITIONS}'::regclass, true)"
    )
    check(
        "the view is NOT auto-updatable (pg_relation_is_updatable = 0)",
        updatable == 0,
        f"pg_relation_is_updatable={updatable}",
    )

    privs = sorted({
        r["privilege_type"] for r in await conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee='app_service' AND table_schema='portfolio' "
            "  AND table_name='spv_derived_positions'"
        )
    })
    check(
        "app_service holds SELECT and ONLY SELECT on the view — the write "
        "grants that ALTER DEFAULT PRIVILEGES would otherwise have given it "
        "were revoked explicitly, not left to a rewrite-rule technicality",
        privs == ["SELECT"],
        f"grants={privs}",
    )

    # The projection carries the full position shape, not a convenient subset.
    view_cols = {
        r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='portfolio' AND table_name='spv_derived_positions'"
        )
    }
    position_cols = {
        r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='portfolio' AND table_name='positions'"
        )
    }
    missing = sorted(position_cols - view_cols)
    check(
        "the view projects EVERY portfolio.positions column — it is position "
        "shaped, not a convenient subset a consumer would have to special-case",
        not missing,
        f"missing columns: {missing or 'none'}",
    )


async def check_spv_asset(conn, ids: dict) -> None:
    """One asset per SPV — idempotent in Python AND enforced by an index."""
    first = await ensure_spv_asset(conn, org_id=DEFAULT_ORG_ID, spv_id=ids["spv"])
    second = await ensure_spv_asset(conn, org_id=DEFAULT_ORG_ID, spv_id=ids["spv"])
    ids["spv_asset"] = first
    n = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_ASSETS} WHERE internal_spv_id = $1::uuid "
        f"  AND valid_to IS NULL AND system_to IS NULL",
        ids["spv"],
    )
    check(
        "ensure_spv_asset is idempotent — one asset per SPV, not per call",
        first == second and n == 1,
        f"first={first} second={second} current_rows={n}",
    )

    row = await conn.fetchrow(
        f"SELECT asset_type, ownership_basis, valuation_method, currency_code, name "
        f"FROM {TABLE_ASSETS} WHERE id = $1::uuid", first,
    )
    check(
        "the SPV asset is valuation_method='nav' and ownership_basis='percent' "
        "— nav is what makes private-market transaction types legal against it",
        row["valuation_method"] == "nav" and row["ownership_basis"] == "percent",
        f"{dict(row)}",
    )

    # The index, not the Python check, is what holds under concurrency.
    raced = False
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(
            f"INSERT INTO {TABLE_ASSETS} "
            f"(org_id, name, asset_type, ownership_basis, valuation_method, "
            f" internal_spv_id) "
            f"VALUES ($1::uuid, $2, 'spv_interest', 'percent', 'nav', $3::uuid)",
            DEFAULT_ORG_ID, SPV_NAME + " DUPLICATE", ids["spv"],
        )
    except asyncpg.exceptions.UniqueViolationError:
        raced = True
    finally:
        await tr.rollback()
    check(
        "assets_internal_spv_active_uniq REFUSES a second current asset for the "
        "same SPV — idempotency survives a race, not just a sequential call",
        raced,
        "a direct duplicate INSERT raised UniqueViolationError",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 5 — prove the view against a real, seeded SPV subscription
# ═══════════════════════════════════════════════════════════════════════════


async def check_absent_value_is_null(conn, ids: dict) -> None:
    """Before any valuation exists: NULL with a reason, never zero."""
    pos = await get_derived_position(
        conn, org_id=DEFAULT_ORG_ID, subscription_id=ids["sub_current"])
    check(
        "with no valuation the projected market_value is NULL WITH A REASON, "
        "never Decimal(0) — a zero for 'we have no mark' is indistinguishable "
        "from a genuine zero position the moment it is summed",
        pos is not None and pos.market_value is None
        and pos.value_reason == "no_current_market_valuation",
        f"market_value={pos.market_value if pos else 'no row'} "
        f"reason={pos.value_reason if pos else '-'}",
    )


async def seed_valuations(conn, ids: dict) -> None:
    """Two marks on the SAME date: an audited one, superseded by a final one.

    Constructed so a status-only resolver gets the WRONG answer. audited(0)
    beats final(1) on status alone, so a ladder that forgot the supersession
    demotion returns 25% of 500,000 = 125,000 instead of 200,000. Both numbers
    are plausible; only the exact one tells them apart.
    """
    ids["val_superseded"] = await record_valuation(
        conn,
        org_id=DEFAULT_ORG_ID,
        asset_id=ids["spv_asset"],
        valuation_date=VAL_DATE,
        value=NAV_SUPERSEDED,
        value_basis="total",
        status="audited",
        currency_code="USD",
        valuation_source=f"{FIXTURE_TAG} initial audited mark",
    )
    ids["val_governing"] = await record_valuation(
        conn,
        org_id=DEFAULT_ORG_ID,
        asset_id=ids["spv_asset"],
        valuation_date=VAL_DATE,
        value=NAV_GOVERNING,
        value_basis="total",
        status="final",
        currency_code="USD",
        valuation_source=f"{FIXTURE_TAG} restated mark",
        supersedes_valuation_id=ids["val_superseded"],
    )


async def check_projection(conn, ids: dict) -> None:
    """The core Task-5 assertions."""
    rows = await list_derived_positions(
        conn, org_id=DEFAULT_ORG_ID, spv_id=ids["spv"])
    check(
        "the view projects EXACTLY ONE position for the SPV — the current "
        "subscription, and not the retired one",
        len(rows) == 1,
        f"rows={len(rows)}: {[r.subscription_id for r in rows]}",
    )
    if not rows:
        return
    pos = rows[0]
    ids["derived_id"] = pos.id

    check(
        "the projected position is authority='internal' and "
        "source_system='spv_subscriptions'",
        pos.authority == SPV_AUTHORITY and pos.source_system == SPV_SOURCE_SYSTEM,
        f"authority={pos.authority!r} source_system={pos.source_system!r}",
    )
    check(
        "owner_entity_id is the subscription's own entity_id, and the basis is "
        "'percent' with quantity NULL (A2's contract for a percent position)",
        pos.owner_entity_id == ids["subscriber"]
        and pos.ownership_basis == "percent"
        and pos.ownership_pct == PCT_CURRENT,
        f"owner={pos.owner_entity_id} basis={pos.ownership_basis} "
        f"pct={pos.ownership_pct}",
    )
    check(
        "the derived id is deterministic — the view's uuid_generate_v5 and "
        "portfolio_spv.derived_position_id agree, so a caller can name a "
        "derived position without querying the view",
        pos.id == derived_position_id(ids["sub_current"]),
        f"view={pos.id} python={derived_position_id(ids['sub_current'])} "
        f"namespace={DERIVED_POSITION_NAMESPACE}",
    )
    check(
        "the resolved value is EXACTLY 25% of the governing 800,000 mark = "
        "200,000.00, NOT 125,000.00 — the superseded 'audited' row is demoted "
        "below the 'final' row that supersedes it, which is the whole ladder",
        pos.market_value == V_BASELINE and pos.market_value != V_NAIVE_STATUS_ONLY,
        f"market_value={pos.market_value} (naive status-only answer would be "
        f"{V_NAIVE_STATUS_ONLY})",
    )
    check(
        "the governing valuation is the restated FINAL row, and the view says "
        "so — provenance, not just a number",
        pos.valuation_id == ids["val_governing"]
        and pos.valuation_status == "final"
        and pos.spv_total_value == NAV_GOVERNING
        and pos.value_reason is None,
        f"valuation_id={pos.valuation_id} status={pos.valuation_status} "
        f"nav={pos.spv_total_value} reason={pos.value_reason}",
    )


async def check_value_matches_a2_resolver(conn, ids: dict) -> None:
    """[Y] The resolved value matches Task 1b's real query path EXACTLY."""
    av = await resolve_current_value(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["spv_asset"], purpose="market"
    )
    expected = av.value * PCT_CURRENT / Decimal(100) if av.value is not None else None
    pos = await get_derived_position(
        conn, org_id=DEFAULT_ORG_ID, subscription_id=ids["sub_current"])

    check(
        "A2's resolve_current_value independently picks the same governing "
        "valuation as the view's in-SQL ladder — two implementations, one "
        "answer, so the SQL transcription did not drift",
        av.valuation_id == ids["val_governing"] and av.value == NAV_GOVERNING
        and av.is_superseded is False,
        f"resolver picked {av.valuation_id} value={av.value} "
        f"superseded={av.is_superseded} reason={av.reason}",
    )
    check(
        "the view's market_value equals resolve_current_value's value x "
        "ownership_pct / 100 EXACTLY (Decimal equality, not a tolerance)",
        pos is not None and expected is not None and pos.market_value == expected,
        f"view={pos.market_value if pos else None} "
        f"resolver_path={expected} (={av.value} x {PCT_CURRENT}/100)",
    )


async def check_retired_excluded(conn, ids: dict) -> None:
    """[Y] A subscription with valid_to SET does NOT appear via the view."""
    pos = await get_derived_position(
        conn, org_id=DEFAULT_ORG_ID, subscription_id=ids["sub_retired"])
    seen_ids = {
        r.subscription_id
        for r in await list_derived_positions(conn, org_id=DEFAULT_ORG_ID,
                                              spv_id=ids["spv"])
    }
    check(
        "a subscription with valid_to SET does NOT appear via the view — and "
        "the fixture is identical to the current one in EVERY other respect "
        "(status='funded', ownership_pct set), so the temporal predicate is "
        "the only thing that can be excluding it",
        pos is None and ids["sub_retired"] not in seen_ids,
        f"get_derived_position={pos} in_list={ids['sub_retired'] in seen_ids}",
    )

    reasons = {
        u["subscription_id"]: u["reason"]
        for u in await unprojected_subscriptions(conn, org_id=DEFAULT_ORG_ID)
    }
    check(
        "the excluded subscription is VISIBLE as unprojected with the reason "
        "'superseded' — a derived view can drop a row silently, and this is "
        "what makes that diagnosable instead of a mystery",
        reasons.get(ids["sub_retired"]) == "superseded",
        f"reason={reasons.get(ids['sub_retired'])!r} "
        f"(all unprojected: {len(reasons)})",
    )


async def check_no_write_path(conn, ids: dict) -> None:
    """[Y] NO WRITE PATH EXISTS against the view."""
    derived = ids.get("derived_id") or derived_position_id(ids["sub_current"])

    # 1 — A2's write functions refuse a view-derived position id, cleanly.
    err = None
    try:
        await record_transaction(
            conn,
            org_id=DEFAULT_ORG_ID,
            position_id=derived,
            # A private-markets type, deliberately: it is COMPATIBLE with the
            # nav-valued SPV asset, so the only possible reason for a refusal is
            # that the position does not exist. A type that would have been
            # rejected on market grounds would pass this test for free.
            transaction_type_code="call_investment",
            trade_date=VAL_DATE,
            authority="internal",
            source_system="spv_subscriptions",
            gross_amount=Decimal("1000.00"),
        )
    except PortfolioError as exc:
        err = str(exc)
    check(
        "portfolio_assets.record_transaction REFUSES a view-derived position "
        "id with a clean PortfolioError naming the id — corrections must go to "
        "spv_subscriptions, which remains the book of record",
        err is not None and "does not exist" in err and derived in err,
        f"raised: {err!r}" if err else "NO ERROR RAISED — a write got through",
    )

    # 2 — Nothing was stored. The projection is not a shadow copy.
    stored = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_POSITIONS} WHERE id = $1::uuid", derived)
    spv_sourced = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_POSITIONS} WHERE source_system = $1",
        SPV_SOURCE_SYSTEM)
    check(
        "nothing is stored twice — the derived id is absent from "
        "portfolio.positions and NO stored position carries "
        "source_system='spv_subscriptions'",
        stored == 0 and spv_sourced == 0,
        f"stored_rows_with_derived_id={stored} "
        f"stored_positions_sourced_from_subscriptions={spv_sourced}",
    )

    # 3 — SQL against the view is refused by Postgres itself.
    sql_err = None
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(
            f"UPDATE {TABLE_SPV_POSITIONS} SET market_value = 1 WHERE id = $1::uuid",
            derived,
        )
    except Exception as exc:  # noqa: BLE001
        sql_err = f"{type(exc).__name__}: {exc}"
    finally:
        await tr.rollback()
    check(
        "a direct UPDATE against the view is refused by Postgres — the view is "
        "not auto-updatable and has no INSTEAD OF trigger",
        sql_err is not None and "view" in sql_err.lower(),
        f"raised: {sql_err}" if sql_err else "NO ERROR — the view is writable",
    )

    # 4 — And no module offers one.
    import services.portfolio_spv as ps
    src = _executable_source(ps)
    write_stmts = sorted({
        s for s in (
            f"UPDATE {TABLE_SPV_POSITIONS}",
            f"INSERT INTO {TABLE_SPV_POSITIONS}",
            f"DELETE FROM {TABLE_SPV_POSITIONS}",
        ) if s in src
    })
    check(
        "services/portfolio_spv.py contains NO write statement against the "
        "view — the read-only contract is a property of the code, not only of "
        "the grants",
        not write_stmts,
        f"write statements found: {write_stmts or 'none'}",
    )


async def check_derived_not_cached(conn, ids: dict) -> None:
    """[Y] Editing spv_subscriptions changes the view on the NEXT read."""
    # An in-place UPDATE, purely to prove there is no cache. In PRODUCTION a
    # correction goes through routers/spv.py, which implements the CLAUDE.md
    # Rule 3 close-and-insert supersede for this table; that path is exercised
    # by the retired-subscription fixture above. Here the point is narrower:
    # the SAME row, edited, must change the SAME derived position.
    await conn.execute(
        f"UPDATE {TABLE_SPV_SUBSCRIPTIONS} SET ownership_pct = $1 WHERE id = $2::uuid",
        PCT_EDITED, ids["sub_current"],
    )
    after = await get_derived_position(
        conn, org_id=DEFAULT_ORG_ID, subscription_id=ids["sub_current"])
    check(
        "editing spv_subscriptions.ownership_pct 25 -> 40 changes the view's "
        "market_value 200,000.00 -> 320,000.00 on the NEXT read, under the "
        "SAME derived id — genuinely derived, not cached and not duplicated",
        after is not None and after.market_value == V_AFTER_PCT_EDIT
        and after.ownership_pct == PCT_EDITED
        and after.id == ids.get("derived_id"),
        f"market_value={after.market_value if after else None} "
        f"pct={after.ownership_pct if after else None} "
        f"same_id={after.id == ids.get('derived_id') if after else False}",
    )

    await conn.execute(
        f"UPDATE {TABLE_SPV_SUBSCRIPTIONS} SET ownership_pct = $1 WHERE id = $2::uuid",
        PCT_CURRENT, ids["sub_current"],
    )
    restored = await get_derived_position(
        conn, org_id=DEFAULT_ORG_ID, subscription_id=ids["sub_current"])
    check(
        "restoring ownership_pct restores the projected value exactly — the "
        "view holds no state of its own",
        restored is not None and restored.market_value == V_BASELINE,
        f"market_value={restored.market_value if restored else None}",
    )

    # The valuation leg is live too, not only the subscription leg.
    ids["val_later"] = await record_valuation(
        conn,
        org_id=DEFAULT_ORG_ID,
        asset_id=ids["spv_asset"],
        valuation_date=VAL_DATE_LATER,
        value=NAV_LATER,
        value_basis="total",
        status="preliminary",
        currency_code="USD",
        valuation_source=f"{FIXTURE_TAG} later preliminary mark",
    )
    revalued = await get_derived_position(
        conn, org_id=DEFAULT_ORG_ID, subscription_id=ids["sub_current"])
    check(
        "a LATER valuation moves the projection too (25% of 1,000,000 = "
        "250,000.00) — and a later 'preliminary' beats an earlier 'final', "
        "because date outranks status in the ladder",
        revalued is not None and revalued.market_value == V_AFTER_NAV_EDIT
        and revalued.valuation_id == ids["val_later"],
        f"market_value={revalued.market_value if revalued else None} "
        f"valuation={revalued.valuation_id if revalued else None}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — cash as an asset
# ═══════════════════════════════════════════════════════════════════════════


async def check_cash(conn, ids: dict) -> None:
    first = await ensure_cash_asset(conn, org_id=DEFAULT_ORG_ID, currency_code=CASH_CCY)
    second = await ensure_cash_asset(conn, org_id=DEFAULT_ORG_ID, currency_code=CASH_CCY)
    lowered = await ensure_cash_asset(
        conn, org_id=DEFAULT_ORG_ID, currency_code=CASH_CCY_LOWER)
    ids["cash_asset"] = first
    n = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_ASSETS} "
        f"WHERE org_id = $1::uuid AND asset_type = $2 AND currency_code = $3 "
        f"  AND valid_to IS NULL AND system_to IS NULL",
        DEFAULT_ORG_ID, CASH_ASSET_TYPE, CASH_CCY,
    )
    check(
        "[Y] a cash asset is created IDEMPOTENTLY per (org, currency) — three "
        "calls (including one lower-cased) yield one row and one id",
        first == second == lowered and n == 1,
        f"first={first} second={second} lowercase={lowered} current_rows={n}",
    )

    other_ccy = await ensure_cash_asset(
        conn, org_id=DEFAULT_ORG_ID, currency_code=CASH_CCY_ALT)
    check(
        "a DIFFERENT currency is a different asset — idempotency is keyed on "
        "(org, currency), not on 'cash'",
        other_ccy != first,
        f"{CASH_CCY}={first} {CASH_CCY_ALT}={other_ccy}",
    )

    raced = False
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute(
            f"INSERT INTO {TABLE_ASSETS} "
            f"(org_id, name, asset_type, ownership_basis, valuation_method, "
            f" currency_code) "
            f"VALUES ($1::uuid, $2, $3, 'value', 'amortized_cost', $4)",
            DEFAULT_ORG_ID, cash_asset_name(CASH_CCY) + " DUPLICATE",
            CASH_ASSET_TYPE, CASH_CCY,
        )
    except asyncpg.exceptions.UniqueViolationError:
        raced = True
    finally:
        await tr.rollback()
    check(
        "assets_cash_active_uniq REFUSES a second current cash asset for the "
        "same (org, currency) — idempotency survives a race",
        raced,
        "a direct duplicate INSERT raised UniqueViolationError",
    )

    # A cash position for a BANK ACCOUNT entity...
    bank = await record_cash_balance(
        conn,
        org_id=DEFAULT_ORG_ID,
        owner_entity_id=ids["bank"],
        amount=CASH_BANK,
        as_of_date=AS_OF,
        currency_code=CASH_CCY,
        authority="custodial",
        source_system="manual",
    )
    # ...and for an entity holding cash DIRECTLY, with no account in between.
    trust = await record_cash_balance(
        conn,
        org_id=DEFAULT_ORG_ID,
        owner_entity_id=ids["trust"],
        amount=CASH_TRUST,
        as_of_date=AS_OF,
        currency_code=CASH_CCY,
        authority="stated",
        source_system="manual",
    )
    ids["cash_position"] = bank.position_id

    rows = {
        r["id"]: dict(r)
        for r in await conn.fetch(
            f"SELECT id::text AS id, owner_entity_id::text AS owner_entity_id, "
            f"       asset_id::text AS asset_id, ownership_basis, quantity, "
            f"       ownership_pct, market_value "
            f"FROM {TABLE_POSITIONS} WHERE id = ANY($1::uuid[])",
            [bank.position_id, trust.position_id],
        )
    }
    b, t = rows.get(bank.position_id, {}), rows.get(trust.position_id, {})
    check(
        "[Y] a cash position round-trips through the SAME asset+position "
        "pattern as every other asset type — ownership_basis='value', "
        "quantity and ownership_pct NULL, market_value exact",
        b.get("ownership_basis") == "value" and b.get("quantity") is None
        and b.get("ownership_pct") is None
        and _dec(b.get("market_value")) == CASH_BANK
        and _dec(t.get("market_value")) == CASH_TRUST,
        f"bank={b.get('ownership_basis')}/{_dec(b.get('market_value'))} "
        f"trust={t.get('ownership_basis')}/{_dec(t.get('market_value'))}",
    )
    check(
        "a BANK ACCOUNT (entity_type='account') and an entity holding cash "
        "DIRECTLY use the IDENTICAL mechanism — same asset, same basis, only "
        "the owner differs. No special case, and no account node required",
        b.get("asset_id") == t.get("asset_id") == first
        and b.get("ownership_basis") == t.get("ownership_basis")
        and b.get("owner_entity_id") == ids["bank"]
        and t.get("owner_entity_id") == ids["trust"],
        f"shared_asset={b.get('asset_id') == t.get('asset_id')} "
        f"owners={b.get('owner_entity_id') == ids['bank']}/"
        f"{t.get('owner_entity_id') == ids['trust']}",
    )

    got = await get_cash_balance(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["bank"],
        currency_code=CASH_CCY, as_of=AS_OF,
    )
    check(
        "get_cash_balance round-trips the EXACT Decimal that was written",
        got == CASH_BANK,
        f"wrote {CASH_BANK}, read {got}",
    )

    absent = await get_cash_balance(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["subscriber"],
        currency_code=CASH_CCY,
    )
    check(
        "an entity with no cash position reads back None, NOT Decimal(0) — "
        "'never recorded' and 'is zero' are different facts and only one of "
        "them should stop a reconciliation",
        absent is None,
        f"got {absent!r}",
    )

    float_err = None
    try:
        await record_cash_balance(
            conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["trust"],
            amount=18500.75, as_of_date=AS_OF, currency_code=CASH_CCY,
        )
    except PortfolioError as exc:
        float_err = str(exc)
    check(
        "a float amount is REFUSED, not converted — a cash balance is the one "
        "number a member reads to the cent, and Decimal(float) preserves the "
        "binary error silently all the way to the screen",
        float_err is not None and "Decimal" in float_err,
        f"raised: {float_err[:90] if float_err else 'NOTHING'}",
    )

    ccy_err = None
    try:
        await ensure_cash_asset(conn, org_id=DEFAULT_ORG_ID, currency_code=None)
    except PortfolioError as exc:
        ccy_err = str(exc)
    check(
        "a NULL currency_code is refused — the partial unique index cannot "
        "constrain a NULL (NULL <> NULL), so Python closes that hole",
        ccy_err is not None and "currency_code is required" in ccy_err,
        f"raised: {ccy_err[:80] if ccy_err else 'NOTHING'}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 4 — document drill-through
# ═══════════════════════════════════════════════════════════════════════════


async def check_documents(conn, ids: dict) -> None:
    before_checks = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid='public.document_record_links'::regclass AND contype='c'"
    )

    written: dict[str, str] = {}
    targets = {
        RECORD_TYPE_VALUATION: ids["val_governing"],
        RECORD_TYPE_ASSET: ids["spv_asset"],
        RECORD_TYPE_POSITION: ids["cash_position"],
    }
    for rtype, rid in targets.items():
        link = await link_portfolio_document(
            conn,
            org_id=DEFAULT_ORG_ID,
            document_id=ids["document"],
            record_type=rtype,
            record_id=rid,
            created_by=None,      # SYSTEM link — Chancery's convention
        )
        written[rtype] = link.get("link_id")

    # A transaction against the SPV asset, to cover the fourth record type with
    # a real record rather than a minted uuid.
    ids["txn"] = await record_transaction(
        conn,
        org_id=DEFAULT_ORG_ID,
        position_id=ids["cash_position"],
        transaction_type_code="adjustment",
        trade_date=AS_OF,
        authority="manual",
        source_system="manual",
        gross_amount=Decimal("100.00"),
    )
    link = await link_portfolio_document(
        conn, org_id=DEFAULT_ORG_ID, document_id=ids["document"],
        record_type=RECORD_TYPE_TRANSACTION, record_id=ids["txn"], created_by=None,
    )
    written[RECORD_TYPE_TRANSACTION] = link.get("link_id")

    after_checks = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid='public.document_record_links'::regclass AND contype='c'"
    )
    check(
        "[Y] all FOUR new record_type values write successfully and NO "
        "migration was needed — the CHECK-constraint count on "
        "document_record_links is 0 before and 0 after",
        set(written) == PORTFOLIO_RECORD_TYPES and all(written.values())
        and before_checks == 0 and after_checks == 0,
        f"written={sorted(written)} checks {before_checks} -> {after_checks}",
    )

    # Read back through CHANCERY's real lookup path, not a new query.
    found = await list_portfolio_record_documents(
        conn, org_id=DEFAULT_ORG_ID,
        record_type=RECORD_TYPE_VALUATION, record_id=ids["val_governing"],
    )
    from services.document_linkage import list_documents_for_panel
    panel = await list_documents_for_panel(
        conn, DEFAULT_ORG_ID, RECORD_TYPE_VALUATION, ids["val_governing"])
    check(
        "[Y] the new link is queryable via Chancery's REAL existing lookup "
        "path — document_linkage.list_documents_for_panel, the exact function "
        "behind GET /records/{record_type}/{record_id}/documents and the "
        "Phase-9 DocumentsPanel — so it renders with no UI work",
        len(found) == 1 and found[0]["document_id"] == ids["document"]
        and [f["document_id"] for f in panel] == [ids["document"]]
        and found[0]["original_filename"] == DOC_NAME,
        f"wrapper returned {len(found)} row(s); the panel function returned "
        f"{len(panel)}; filename={found[0]['original_filename'] if found else '-'}",
    )
    check(
        "the SYSTEM-created link reports system_created=True — Chancery's "
        "created_by convention (human -> user id, system -> NULL) is used "
        "unchanged, not re-invented",
        bool(found) and found[0]["system_created"] is True,
        f"system_created={found[0]['system_created'] if found else '-'}",
    )

    repeat = await link_portfolio_document(
        conn, org_id=DEFAULT_ORG_ID, document_id=ids["document"],
        record_type=RECORD_TYPE_VALUATION, record_id=ids["val_governing"],
    )
    n = await conn.fetchval(
        "SELECT count(*) FROM public.document_record_links "
        "WHERE document_id = $1::uuid AND record_type = $2 AND record_id = $3::uuid",
        ids["document"], RECORD_TYPE_VALUATION, ids["val_governing"],
    )
    check(
        "linking the same document to the same record twice creates nothing — "
        "re-running an extraction is safe",
        repeat["created"] is False and n == 1,
        f"created={repeat['created']} rows={n}",
    )

    bad = None
    try:
        await link_portfolio_document(
            conn, org_id=DEFAULT_ORG_ID, document_id=ids["document"],
            record_type="portfolio_positon", record_id=ids["cash_position"],
        )
    except PortfolioError as exc:
        bad = str(exc)
    check(
        "a typo'd record_type is refused at the call — the table has no CHECK "
        "constraint, so an unrecognised value would otherwise be written "
        "happily and read back by nothing",
        bad is not None and "not a portfolio record type" in bad,
        f"raised: {bad[:80] if bad else 'NOTHING'}",
    )

    # The natural point: create the record and link it in one call.
    combo = await record_valuation_from_document(
        conn,
        org_id=DEFAULT_ORG_ID,
        document_id=ids["document"],
        asset_id=ids["spv_asset"],
        valuation_date=VAL_DATE_LATER,
        value=NAV_LATER,
        value_basis="total",
        status="estimated",
        currency_code="USD",
        valuation_source=f"{FIXTURE_TAG} extracted from statement",
    )
    ids["val_from_doc"] = combo["valuation_id"]
    linked = await list_portfolio_record_documents(
        conn, org_id=DEFAULT_ORG_ID,
        record_type=RECORD_TYPE_VALUATION, record_id=combo["valuation_id"],
    )
    check(
        "record_valuation_from_document creates the valuation AND links it to "
        "the source document in one call — the natural point, wired",
        combo["link"]["created"] is True and len(linked) == 1
        and linked[0]["document_id"] == ids["document"],
        f"valuation={combo['valuation_id']} links={len(linked)}",
    )

    import dataclasses as _dc
    import inspect as _inspect

    import services.portfolio_import as pi

    sig = _inspect.signature(pi.import_positions_file)
    result_fields = {f.name for f in _dc.fields(pi.ImportResult)}
    src = _executable_source(pi)
    check(
        "the Phase-B importer carries the hook too — import_positions_file "
        "accepts document_id/linked_by, reports documents_linked and "
        "document_link_error, and CALLS link_imported_positions",
        {"document_id", "linked_by"} <= set(sig.parameters)
        and {"documents_linked", "document_link_error"} <= result_fields
        and "link_imported_positions(" in src,
        f"params={sorted({'document_id', 'linked_by'} & set(sig.parameters))} "
        f"result_fields={sorted({'documents_linked', 'document_link_error'} & result_fields)}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Cross-org isolation, on the REAL app_service connection
# ═══════════════════════════════════════════════════════════════════════════


async def check_cross_org(app_conn, ids: dict) -> None:
    """[Y] The view and the new functions are org-isolated under app_service.

    Run as ``app_service`` with NO super-admin escape hatch. ``postgres`` has
    ``rolbypassrls``, so every assertion below would pass under it while proving
    nothing whatsoever — and for a VIEW that is the specific failure this phase
    could have shipped, since a view without ``security_invoker`` runs as its
    owner and leaks every tenant's rows.
    """
    sp = await app_conn.fetchval("SHOW search_path")
    unqualified_fails = False
    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.fetchval("SELECT count(*) FROM spv_derived_positions")
    except asyncpg.exceptions.UndefinedTableError:
        unqualified_fails = True
    finally:
        await tr.rollback()
    check(
        "'portfolio' is still NOT on app_service's search_path — the view must "
        "be schema-qualified like every table",
        unqualified_fails and "portfolio" not in sp,
        f"search_path={sp!r}; unqualified SELECT raised UndefinedTableError",
    )

    # THE CONTROL. Without this, "the other org sees 0 rows" is satisfied just
    # as well by app_service being unable to read the view at all.
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False):
        own = await app_conn.fetchval(
            f"SELECT count(*) FROM {TABLE_SPV_POSITIONS} WHERE subscription_id = $1::uuid",
            ids["sub_current"],
        )
        own_cash = await app_conn.fetchval(
            f"SELECT count(*) FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
            ids["cash_position"],
        )
    async with org_ctx(app_conn, OTHER_ORG_ID, commit=False):
        other = await app_conn.fetchval(
            f"SELECT count(*) FROM {TABLE_SPV_POSITIONS} WHERE subscription_id = $1::uuid",
            ids["sub_current"],
        )
        other_all = await app_conn.fetchval(
            f"SELECT count(*) FROM {TABLE_SPV_POSITIONS}")
        other_cash = await app_conn.fetchval(
            f"SELECT count(*) FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
            ids["cash_position"],
        )
    check(
        "[Y] cross-org isolation ON THE VIEW: app_service in the OWNING org "
        "sees the derived position (control); in the other org it sees zero — "
        "security_invoker is working, not just declared",
        own == 1 and other == 0 and other_all == 0,
        f"owning_org={own} other_org={other} other_org_total_view_rows={other_all}",
    )
    check(
        "cross-org isolation on the CASH position written by the new helper — "
        "visible in its own org, invisible in the other",
        own_cash == 1 and other_cash == 0,
        f"owning_org={own_cash} other_org={other_cash}",
    )

    # The service functions, under the other org's context.
    spv_err = None
    try:
        async with org_ctx(app_conn, OTHER_ORG_ID, commit=False):
            await ensure_spv_asset(
                app_conn, org_id=OTHER_ORG_ID, spv_id=ids["spv"])
    except PortfolioError as exc:
        spv_err = str(exc)
    except Exception as exc:  # noqa: BLE001
        spv_err = f"{type(exc).__name__}: {exc}"
    check(
        "ensure_spv_asset refuses another org's SPV, and reports it as 'does "
        "not exist in this org' rather than confirming it exists elsewhere",
        spv_err is not None and "does not exist in this org" in spv_err,
        f"raised: {spv_err[:100] if spv_err else 'NOTHING — a write got through'}",
    )

    doc_err = None
    try:
        async with org_ctx(app_conn, OTHER_ORG_ID, commit=False):
            await link_portfolio_document(
                app_conn, org_id=OTHER_ORG_ID, document_id=ids["document"],
                record_type=RECORD_TYPE_ASSET, record_id=ids["spv_asset"],
            )
    except Exception as exc:  # noqa: BLE001
        doc_err = f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"
    check(
        "link_portfolio_document refuses to link another org's document — the "
        "document is invisible under the wrong org context and reports 404",
        doc_err is not None and "not found" in doc_err.lower(),
        f"raised: {doc_err}" if doc_err else "NOTHING — a cross-org link was written",
    )

    # And app_service genuinely cannot write the view even by privilege.
    can_update = await app_conn.fetchval(
        f"SELECT has_table_privilege('app_service', '{TABLE_SPV_POSITIONS}', 'UPDATE')"
    )
    can_insert = await app_conn.fetchval(
        f"SELECT has_table_privilege('app_service', '{TABLE_SPV_POSITIONS}', 'INSERT')"
    )
    can_select = await app_conn.fetchval(
        f"SELECT has_table_privilege('app_service', '{TABLE_SPV_POSITIONS}', 'SELECT')"
    )
    check(
        "app_service holds SELECT on the view and neither INSERT nor UPDATE — "
        "checked as the production role itself, not from the catalogue",
        can_select and not can_update and not can_insert,
        f"select={can_select} insert={can_insert} update={can_update}",
    )


# ── Main ────────────────────────────────────────────────────────────────────


async def main() -> int:
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
        await teardown(admin_conn)                                   # START
        baseline = await counts(admin_conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        nonempty = {t: n for t, n in baseline.items() if n}
        if nonempty:
            report(
                "TEARDOWN — rows are already present in these tables",
                f"{nonempty}. Teardown is by-fixture + count assertion, NOT a "
                f"truncate. spv_subscriptions / spvs / deals hold REAL SPV "
                f"Manager rows and portfolio.assets is org-global for cash, "
                f"which is why the cash fixtures use ISO test currencies "
                f"({CASH_CCY}/{CASH_CCY_ALT}) rather than USD.",
            )

        print("\n── Task 1: discovery, reported AND asserted ──")
        await check_task1a(admin_conn)
        await check_task1b(admin_conn)
        check_task1c()
        await check_task1d(admin_conn)

        print("\n── Task 2: the derivation view's shape and posture ──")
        await check_view_shape(admin_conn)

        print("\n── Fixtures: one real SPV, one current + one retired sub ──")
        await seed_users(admin_conn)
        ids = await build_fixtures(admin_conn)
        await check_spv_asset(admin_conn, ids)

        print("\n── Absence is NULL, not zero ──")
        await check_absent_value_is_null(admin_conn, ids)

        print("\n── Task 5: the projection against a real subscription ──")
        await seed_valuations(admin_conn, ids)
        await check_projection(admin_conn, ids)
        await check_value_matches_a2_resolver(admin_conn, ids)
        await check_retired_excluded(admin_conn, ids)

        print("\n── No write path exists against the view ──")
        await check_no_write_path(admin_conn, ids)

        print("\n── Derived, not cached ──")
        await check_derived_not_cached(admin_conn, ids)

        print("\n── Task 3: cash as an asset, no special case ──")
        await check_cash(admin_conn, ids)

        print("\n── Task 4: document drill-through ──")
        await check_documents(admin_conn, ids)

        print("\n── Cross-org isolation (real app_service connection) ──")
        await check_cross_org(app_conn, ids)

    finally:
        await teardown(admin_conn)                                   # END
        if baseline:
            final = await counts(admin_conn)
            drift = {
                t: (baseline[t], final[t]) for t in TABLES if baseline[t] != final[t]
            }
            check(
                "[Y] TEARDOWN restores the EXACT before-count on every table "
                "touched — spv_subscriptions, portfolio.*, "
                "document_record_links included",
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
