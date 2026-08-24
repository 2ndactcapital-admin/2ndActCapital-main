"""Verification — Portfolio Phase C, rollup into ``entity_holdings``.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END.
Real database, real RLS, real ``app_service`` connection, real ownership graph.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback, for the
same reason A1, A2 and Phase B require it: the cross-org isolation check is
meaningless under a ``rolbypassrls`` role. Running it as ``postgres`` would
"pass" while proving nothing, so a missing or non-connecting app_service
credential FAILS this script rather than degrading it.

────────────────────────────────────────────────────────────────────────────
TEARDOWN: BEFORE/AFTER COUNTS, NOT TRUNCATE
────────────────────────────────────────────────────────────────────────────
Inherited unchanged from A1/A2/B, and Phase C is the first sprint that writes
``public.entity_holdings`` — a PUBLIC, tenant-visible table that S21, the RLS
Batch-A verification and ``services.households`` all read. An unconditional
truncate here would delete another track's rows. Every table is counted before
the run and after teardown and the counts must match EXACTLY.

────────────────────────────────────────────────────────────────────────────
WHY THE ASSERTIONS USE EXACT FIGURES
────────────────────────────────────────────────────────────────────────────
"A row got created" is the assertion this sprint is easiest to fake. Every
look-through check below asserts an exact Decimal — $30,000.00 and not
$50,000.00 or $60,000.00 — because 50% and 60% and their product are three
different plausible bugs and only the exact number tells them apart.

Run:
    python3 scripts/verify_portfolioc.py
"""

from __future__ import annotations

import asyncio
import glob
import inspect
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
    create_asset,
    create_position,
    record_valuation,
)
from services.portfolio_precedence import DEFAULT_SOURCE_ORDER, resolve_holding  # noqa: E402
from services.portfolio_rollup import (  # noqa: E402
    BASE_CURRENCY,
    ROLLUP_PERMISSION,
    ROLLUP_SOURCE,
    TABLE_HOLDINGS,
    rollup_entity_holdings,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org, for cross-org isolation. A real row, not a minted one.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

ADMIN_SUB = "auth0|verify_portfolioc_super_admin"
MEMBER_SUB = "auth0|verify_portfolioc_member"

# NOT the usual 99000000-… literals. `services.permissions.get_user_id` does
# NOT look the user up by auth0_sub — with no namespaced `user_id` claim and a
# non-UUID `sub`, it returns `uuid5(NAMESPACE_URL, sub)`, and that derived id is
# what `rbac.load_principal` and `_has_any_role` are then handed. Seeding a
# fixture user under a hand-picked id therefore produces a user the endpoint
# never finds: `load_principal` returns None (no super-admin), `_has_any_role`
# finds nothing, and `has_permission` DEFAULT-ALLOWS. The 403 assertion would
# then fail for a reason with nothing to do with the endpoint, and — worse —
# a "member is denied" test could pass by accident on a different codebase.
ADMIN_USER_ID = str(uuid5(NAMESPACE_URL, ADMIN_SUB))
MEMBER_USER_ID = str(uuid5(NAMESPACE_URL, MEMBER_SUB))

FIXTURE_TAG = "VERIFY-PORTFOLIOC"
AS_OF = date(2026, 6, 30)

# ── Entity fixtures ─────────────────────────────────────────────────────────
# Declared UP FRONT and never appended to at runtime: a name minted mid-run is
# one the NEXT run's start-teardown cannot find, so a crash between minting it
# and the end-teardown strands it permanently.
E_DIRECT = f"{FIXTURE_TAG} Direct Custodial Account"
# Chain A — whole ownership, two levels. Proves ATTRIBUTION REACHES THE TOP.
E_LT_INDIV = f"{FIXTURE_TAG} Lookthrough Individual"
E_LT_TRUST = f"{FIXTURE_TAG} Lookthrough Trust"
E_LT_ACCT = f"{FIXTURE_TAG} Lookthrough Account"
# Chain B — 50% of a trust that owns 60% of an LLC. Proves COMPOUNDING.
E_FR_INDIV = f"{FIXTURE_TAG} Fractional Individual"
E_FR_TRUST = f"{FIXTURE_TAG} Fractional Trust"
E_FR_LLC = f"{FIXTURE_TAG} Fractional LLC"
E_OTHERORG = f"{FIXTURE_TAG} Other-Org Account"

ENTITY_NAMES = [
    E_DIRECT, E_LT_INDIV, E_LT_TRUST, E_LT_ACCT,
    E_FR_INDIV, E_FR_TRUST, E_FR_LLC, E_OTHERORG,
]

# ── Asset fixtures ──────────────────────────────────────────────────────────
A_DIRECT = f"{FIXTURE_TAG} Ridgecrest Core Equity Fund"
A_LOOKTHROUGH = f"{FIXTURE_TAG} Fairmount Aggregate Bond Fund"
A_FRACTIONAL = f"{FIXTURE_TAG} Wexford Holdings LLC Interest"
A_SUPERSEDED = f"{FIXTURE_TAG} Kestrel Contested Holding"
A_PERCENT = f"{FIXTURE_TAG} Alderpoint Operating Company"
A_OTHERORG = f"{FIXTURE_TAG} Other-Org Holding"

ASSET_NAMES = [
    A_DIRECT, A_LOOKTHROUGH, A_FRACTIONAL, A_SUPERSEDED, A_PERCENT, A_OTHERORG,
]

# ── Taxonomy keys. Real-shaped, one per assertion, so no two checks can be
#    satisfied by the same bucket. ─────────────────────────────────────────
TAX_DIRECT = "taxonomy_sc_1"
TAX_LT = "taxonomy_sc_2"
TAX_FRAC = "taxonomy_sc_3"
TAX_SUPER = "taxonomy_sc_4"
TAX_PCT = "taxonomy_sc_5"

# ── The exact figures every assertion is measured against ───────────────────
V_DIRECT = Decimal("500000.00")
V_LOOKTHROUGH = Decimal("250000.00")
V_FRACTIONAL = Decimal("100000.00")
# 50% of a trust that owns 60% of the LLC → 0.5 * 0.6 = 0.30 of $100,000.
V_FRAC_INDIV = Decimal("30000.00")
V_FRAC_TRUST = Decimal("60000.00")
V_SUPER_WINNER = Decimal("77777.00")   # source reporting_tool_import
V_SUPER_LOSER = Decimal("10000.00")    # source manual — must never appear
# Percent basis: 25% of an asset valued at $400,000, then revalued to $800,000.
PCT_OWNED = Decimal("25")
V_PCT_ASSET_1 = Decimal("400000.00")
V_PCT_ASSET_2 = Decimal("800000.00")
V_PCT_RUN1 = Decimal("100000.00")
V_PCT_RUN2 = Decimal("200000.00")
V_OTHERORG = Decimal("999999.00")

TABLES = (
    TABLE_ASSETS, TABLE_ASSET_IDENT, TABLE_POSITIONS,
    TABLE_VALUATIONS, TABLE_TRANSACTIONS, TABLE_EXT_REF,
    TABLE_HOLDINGS, "public.entity_relationships",
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

    ``entity_holdings`` is deleted by fixture ENTITY, not by ``source`` and not
    by ``as_of_date``: a delete keyed to ``source = 'portfolio'`` would take out
    a real rollup another track had run for the same date, which is exactly the
    production row this discipline exists to protect.
    """
    entity_ids = "SELECT id FROM entities WHERE display_name = ANY($1::text[])"
    asset_ids = f"SELECT id FROM {TABLE_ASSETS} WHERE name = ANY($2::text[])"

    await conn.execute(
        f"DELETE FROM {TABLE_HOLDINGS} WHERE entity_id IN ({entity_ids})",
        ENTITY_NAMES,
    )
    fixture_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN "
        f"(SELECT id FROM {TABLE_ASSETS} WHERE name = ANY($1::text[]))"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_EXT_REF} WHERE record_id IN ({fixture_positions})",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_TRANSACTIONS} WHERE position_id IN ({fixture_positions})",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN "
        f"(SELECT id FROM {TABLE_ASSETS} WHERE name = ANY($1::text[]))",
        ASSET_NAMES,
    )
    # The forward supersession pointer must be dropped before the rows it points
    # at, or the delete order is an FK violation on the fixture's own history.
    await conn.execute(
        f"UPDATE {TABLE_VALUATIONS} SET supersedes_valuation_id = NULL "
        f"WHERE asset_id IN (SELECT id FROM {TABLE_ASSETS} WHERE name = ANY($1::text[]))",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_VALUATIONS} WHERE asset_id IN "
        f"(SELECT id FROM {TABLE_ASSETS} WHERE name = ANY($1::text[]))",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_ASSET_IDENT} WHERE asset_id IN "
        f"(SELECT id FROM {TABLE_ASSETS} WHERE name = ANY($1::text[]))",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_ASSETS} WHERE name = ANY($1::text[])", ASSET_NAMES
    )
    await conn.execute(
        f"DELETE FROM public.entity_relationships "
        f"WHERE from_entity_id IN ({entity_ids}) OR to_entity_id IN ({entity_ids})",
        ENTITY_NAMES,
    )
    await conn.execute(
        "DELETE FROM entities WHERE display_name = ANY($1::text[])", ENTITY_NAMES
    )
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[])", [ADMIN_SUB, MEMBER_SUB]
    )


async def seed_users(conn) -> None:
    for user_id, sub, role, email in (
        (ADMIN_USER_ID, ADMIN_SUB, "super_admin", "verify_c_admin@test.local"),
        (MEMBER_USER_ID, MEMBER_SUB, "member", "verify_c_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioC', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, DEFAULT_ORG_ID, email, sub, role,
        )


async def seed_entity(conn, org_id: str, display_name: str, entity_type: str) -> str:
    return await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1::uuid, $2::entity_type, $3)
        RETURNING id::text
        """,
        org_id, entity_type, display_name,
    )


async def seed_ownership(conn, org_id: str, owner: str, owned: str, pct: str) -> None:
    """One current ownership edge, owner → owned, at ``pct`` (0–100).

    ``from_entity_id`` is the OWNER: that is the direction
    ``entity_graph.get_lookthrough`` walks (``WHERE from_entity_id = current``),
    and getting it backwards would build a graph that silently attributes
    nothing.
    """
    await conn.execute(
        """
        INSERT INTO public.entity_relationships
            (org_id, from_entity_id, to_entity_id, relationship_type, ownership_pct)
        VALUES ($1::uuid, $2::uuid, $3::uuid, 'ownership', $4::numeric)
        """,
        org_id, owner, owned, pct,
    )


def org_ctx(conn, org_id: str, *, super_admin: bool = False, commit: bool = True):
    """Transaction on ``conn`` with the RLS GUCs SET LOCAL.

    ``super_admin=False`` is the important default: these are TENANT tables and
    the isolation check is only meaningful without the escape hatch.
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


# ── Bucket reader ───────────────────────────────────────────────────────────


async def bucket(conn, entity_id: str, taxonomy_key: str,
                 as_of: date = AS_OF) -> Decimal | None:
    """The rollup's own bucket for one entity + key, or None."""
    v = await conn.fetchval(
        f"SELECT market_value FROM {TABLE_HOLDINGS} "
        f"WHERE entity_id = $1::uuid AND taxonomy_key = $2 "
        f"  AND as_of_date = $3::date AND source = $4",
        entity_id, taxonomy_key, as_of, ROLLUP_SOURCE,
    )
    return None if v is None else Decimal(str(v))


async def fixture_holding_count(conn) -> int:
    return await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_HOLDINGS} WHERE entity_id IN "
        f"(SELECT id FROM entities WHERE display_name = ANY($1::text[]))",
        ENTITY_NAMES,
    )


# ── Task 1: the four findings, asserted ─────────────────────────────────────


async def check_task1(conn) -> None:
    """Task 1's four discovery findings — reported AND asserted against reality.

    Reported is not enough on its own: a finding printed from a docstring and
    never checked is a claim, and this sprint's whole premise is that the rollup
    output must match what the sunburst actually reads.
    """

    # ── 1a: what allocation_lens really reads ────────────────────────────────
    import services.allocation_lens as lens

    src = inspect.getsource(lens.aggregate_allocation)
    reads_holdings = "FROM entity_holdings" in src
    grain = "DISTINCT ON (entity_id, taxonomy_key)" in src
    cols = all(c in src for c in ("entity_id, taxonomy_key, market_value",))
    date_ceiling = "as_of_date <= $3" in src
    report(
        "1a — services/allocation_lens.py, the REAL query (line ~138)",
        "SELECT DISTINCT ON (entity_id, taxonomy_key) entity_id, taxonomy_key, "
        "market_value FROM entity_holdings WHERE org_id = $1 AND entity_id = "
        "ANY($2::uuid[]) AND as_of_date <= $3 ORDER BY entity_id, taxonomy_key, "
        "as_of_date DESC.\n"
        "       GRAIN: one row per (entity_id, taxonomy_key), latest as_of_date "
        "on or before the query date. It reads THREE columns and no others — "
        "not `source`, not `currency_code`. The rollup therefore writes exactly "
        "one row per (entity, taxonomy_key) per as_of_date, and market_value in "
        "the org's base currency.\n"
        "       CONSEQUENCE, reported not papered over: because the lens does "
        "NOT filter on `source`, a manual entity_holdings row and a rollup row "
        "for the same (entity, key, date) are tie-broken arbitrarily by "
        "DISTINCT ON. Phase C owns source='portfolio' and never writes or "
        "deletes another source's rows; deduplicating across sources belongs "
        "in the lens.",
    )
    check("Task 1a: allocation_lens reads entity_holdings at the "
          "(entity_id, taxonomy_key) grain the rollup writes",
          reads_holdings and grain and cols and date_ceiling,
          f"reads_holdings={reads_holdings}, distinct_on_grain={grain}, "
          f"three_columns={cols}, as_of_ceiling={date_ceiling}")

    # The interaction that is real and is NOT fixed here (out of scope).
    report(
        "1a (cont.) — the subtree-selector double count, reported not hidden",
        "aggregate_allocation weights `{'type':'subtree','root_id':R}` as R at "
        "1.0 PLUS every descendant at its effective_pct. Against look-through "
        "buckets that double counts, because R's own bucket already contains "
        "the descendants' compounded value. `{'type':'entity','id':E}` (weight "
        "1.0, E alone) is exactly correct. services/allocation_lens.py is "
        "explicitly out of scope for Phase C, so this is recorded here and in "
        "docs/PROJECT_STATUS.md rather than silently absorbed.",
    )

    # ── 1b: the real look-through mechanism ──────────────────────────────────
    import services.entity_graph as eg

    sig_res = inspect.signature(eg.resolve_entity_set)
    sig_lt = inspect.signature(eg.get_lookthrough)
    lt_src = inspect.getsource(eg.get_lookthrough)
    compounds = "cumulative_pct * edge_pct" in lt_src
    divides = 'Decimal(str(row["ownership_pct"])) / Decimal("100")' in lt_src
    walks_down = "er.from_entity_id = $2" in lt_src
    report(
        "1b — the REAL ownership mechanism (services/entity_graph.py)",
        f"async def resolve_entity_set(pool, org_id: str, selector: dict) -> "
        f"list[dict]  {sig_res}\n"
        f"       async def get_lookthrough(pool, org_id: str, root_entity_id: "
        f"str) -> list[dict]  {sig_lt}\n"
        "       resolve_entity_set('subtree') DELEGATES to get_lookthrough, "
        "which is the actual percentage engine: a BFS over "
        "entity_relationships WHERE relationship_type='ownership' AND "
        "ownership_pct IS NOT NULL AND valid_to IS NULL AND system_to IS NULL, "
        "compounding `cumulative_pct * (ownership_pct/100)` down each path and "
        "SUMMING across paths, returning effective_pct as a 0–1 fraction to 6dp.\n"
        "       DIRECTION: it walks DOWN (from_entity_id = current → "
        "to_entity_id). The rollup needs the UP direction — for a position "
        "owner, which ancestors hold it and at what fraction — so "
        "services/portfolio_rollup.py calls this SAME function once per entity "
        "that owns anything and inverts the result. It does NOT reimplement the "
        "percentage walk. Both take a POOL; the rollup takes a conn (it runs "
        "inside the caller's transaction), so it passes a one-connection "
        "pool shim rather than acquiring a second connection that would not see "
        "the caller's uncommitted rows or its org GUC.",
    )
    check("Task 1b: get_lookthrough is the real compounding engine "
          "(pct/100, multiplied down the chain, walked via from_entity_id)",
          compounds and divides and walks_down,
          f"compounds={compounds}, pct_is_0_100={divides}, "
          f"walks_from_entity_id={walks_down}")

    # ── 1c: source_system values and the precedence winner ───────────────────
    live_sources = await conn.fetch(
        f"SELECT source_system, count(*) AS n, "
        f"       count(*) FILTER (WHERE superseded_by_source IS NOT NULL) AS lost "
        f"FROM {TABLE_POSITIONS} GROUP BY source_system ORDER BY source_system"
    )
    import services.portfolio_rollup as pr

    pos_src = inspect.getsource(pr._current_positions)
    excludes_losers = "superseded_by_source IS NULL" in pos_src
    both_axes = "valid_to IS NULL" in pos_src and "system_to IS NULL" in pos_src
    report(
        "1c — source_system values, live, and which ROW the rollup reads",
        f"DEFAULT precedence order (org_settings "
        f"'portfolio.precedence.source_order', highest first): "
        f"{list(DEFAULT_SOURCE_ORDER)}\n"
        f"       LIVE rows in portfolio.positions right now: "
        f"{ {r['source_system']: {'rows': r['n'], 'superseded': r['lost']} for r in live_sources } or 'none'}\n"
        "       altruist remains unconfirmed live (Phase B BLOCKED on absent "
        "partner credentials) — it is a legal source_system with no live rows.\n"
        "       The rollup selects ONLY precedence winners: "
        "`superseded_by_source IS NULL`, on top of `valid_to IS NULL AND "
        "system_to IS NULL`. Phase B leaves losing rows in place, CURRENT and "
        "queryable, annotated with the source that beat them — the temporal "
        "predicate alone does not exclude them, so a rollup filtering only on "
        "valid_to/system_to would count both sides of every contested holding.",
    )
    check("Task 1c: the rollup reads precedence WINNERS only "
          "(superseded_by_source IS NULL) and both temporal axes",
          excludes_losers and both_axes,
          f"excludes_superseded={excludes_losers}, both_temporal_axes={both_axes}")

    # ── 1d: entity_holdings' real deployed shape ─────────────────────────────
    cols_live = await conn.fetch(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'entity_holdings'
        ORDER BY ordinal_position
        """
    )
    uniq = await conn.fetch(
        """
        SELECT c.conname, pg_get_constraintdef(c.oid) AS def
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND t.relname = 'entity_holdings'
          AND c.contype = 'u'
        """
    )
    col_names = [r["column_name"] for r in cols_live]
    expected_cols = [
        "id", "org_id", "entity_id", "taxonomy_key", "market_value",
        "currency_code", "as_of_date", "source", "created_at", "updated_at",
    ]
    wanted = "(org_id, entity_id, taxonomy_key, as_of_date, source)"
    matching = [r for r in uniq if r["def"].replace("UNIQUE ", "") == wanted]
    report(
        "1d — public.entity_holdings as DEPLOYED",
        "columns: "
        + ", ".join(f"{r['column_name']} {r['data_type']}"
                    f"{'' if r['is_nullable'] == 'YES' else ' NOT NULL'}"
                    for r in cols_live)
        + "\n       unique constraints: "
        + ("; ".join(f"{r['conname']} {r['def']}" for r in uniq) or "NONE")
        + "\n       The rollup UPSERTs with ON CONFLICT inferring exactly "
        "(org_id, entity_id, taxonomy_key, as_of_date, source). Before Phase "
        "C's Part 1 SQL there was NO unique constraint at all, so a second run "
        "would have inserted a duplicate bucket instead of updating one, and "
        "the lens's DISTINCT ON would have picked between them arbitrarily.",
    )
    check("Task 1d: entity_holdings has the 10 expected columns",
          col_names == expected_cols,
          f"got {col_names}")
    check("Task 1d: the UNIQUE constraint "
          "(org_id, entity_id, taxonomy_key, as_of_date, source) is deployed",
          bool(matching),
          f"{matching[0]['conname']}" if matching
          else f"not found; unique constraints present: "
               f"{[r['conname'] for r in uniq]}")


# ── Fixture construction ────────────────────────────────────────────────────


async def build_fixtures(conn) -> dict:
    """Every entity, edge, asset, valuation and position this run needs."""
    ids: dict[str, str] = {}

    async with org_ctx(conn, DEFAULT_ORG_ID, super_admin=True) as c:
        ids["direct"] = await seed_entity(c, DEFAULT_ORG_ID, E_DIRECT, "account")
        ids["lt_indiv"] = await seed_entity(c, DEFAULT_ORG_ID, E_LT_INDIV, "individual")
        ids["lt_trust"] = await seed_entity(c, DEFAULT_ORG_ID, E_LT_TRUST, "trust")
        ids["lt_acct"] = await seed_entity(c, DEFAULT_ORG_ID, E_LT_ACCT, "account")
        ids["fr_indiv"] = await seed_entity(c, DEFAULT_ORG_ID, E_FR_INDIV, "individual")
        ids["fr_trust"] = await seed_entity(c, DEFAULT_ORG_ID, E_FR_TRUST, "trust")
        ids["fr_llc"] = await seed_entity(c, DEFAULT_ORG_ID, E_FR_LLC, "llc")

        # Chain A: individual → trust → account, whole ownership.
        await seed_ownership(c, DEFAULT_ORG_ID, ids["lt_indiv"], ids["lt_trust"], "100")
        await seed_ownership(c, DEFAULT_ORG_ID, ids["lt_trust"], ids["lt_acct"], "100")
        # Chain B: individual 50% of trust, trust 60% of LLC.
        await seed_ownership(c, DEFAULT_ORG_ID, ids["fr_indiv"], ids["fr_trust"], "50")
        await seed_ownership(c, DEFAULT_ORG_ID, ids["fr_trust"], ids["fr_llc"], "60")

    async with org_ctx(conn, OTHER_ORG_ID, super_admin=True) as c:
        ids["otherorg"] = await seed_entity(c, OTHER_ORG_ID, E_OTHERORG, "account")

    # Assets + positions. create_asset/create_position raise their own org
    # context (_OrgWrite), so they take the bare connection.
    ids["a_direct"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=A_DIRECT, asset_type="mutual_fund",
        ownership_basis="value", default_taxonomy_key=TAX_DIRECT,
    )
    ids["a_lt"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=A_LOOKTHROUGH, asset_type="mutual_fund",
        ownership_basis="value", default_taxonomy_key=TAX_LT,
    )
    ids["a_frac"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=A_FRACTIONAL, asset_type="private_equity",
        ownership_basis="value", default_taxonomy_key=TAX_FRAC,
    )
    ids["a_super"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=A_SUPERSEDED, asset_type="mutual_fund",
        ownership_basis="value", default_taxonomy_key=TAX_SUPER,
    )
    ids["a_pct"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=A_PERCENT, asset_type="operating_company",
        ownership_basis="percent", valuation_method="appraisal",
        default_taxonomy_key=TAX_PCT,
    )
    ids["a_otherorg"] = await create_asset(
        conn, org_id=OTHER_ORG_ID, name=A_OTHERORG, asset_type="mutual_fund",
        ownership_basis="value", default_taxonomy_key=TAX_DIRECT,
    )

    # The percent-basis asset's first appraisal. Total basis, so 25% of it is
    # the position's value with no quantity involved.
    ids["val_1"] = await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["a_pct"],
        valuation_date=date(2026, 3, 31), value=V_PCT_ASSET_1,
        value_basis="total", status="final", valuation_method="appraisal",
    )

    # ── Positions ────────────────────────────────────────────────────────────
    ids["p_direct"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["direct"],
        asset_id=ids["a_direct"], as_of_date=AS_OF, authority="custodial",
        source_system="reporting_tool_import", ownership_basis="value",
        market_value=V_DIRECT,
    )
    ids["p_lt"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["lt_acct"],
        asset_id=ids["a_lt"], as_of_date=AS_OF, authority="custodial",
        source_system="reporting_tool_import", ownership_basis="value",
        market_value=V_LOOKTHROUGH,
    )
    ids["p_frac"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["fr_llc"],
        asset_id=ids["a_frac"], as_of_date=AS_OF, authority="stated",
        source_system="manual", ownership_basis="value",
        market_value=V_FRACTIONAL,
    )
    # The contested holding: two sources, same (owner, asset, as_of_date).
    ids["p_super_win"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["direct"],
        asset_id=ids["a_super"], as_of_date=AS_OF, authority="custodial",
        source_system="reporting_tool_import", ownership_basis="value",
        market_value=V_SUPER_WINNER,
    )
    ids["p_super_lose"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["direct"],
        asset_id=ids["a_super"], as_of_date=AS_OF, authority="stated",
        source_system="manual", ownership_basis="value",
        market_value=V_SUPER_LOSER,
    )
    # Percent basis — ownership_pct is authoritative, market_value MUST be NULL
    # is not required (it is permitted), but it is omitted so the rollup has no
    # stored figure to fall back on and must go through the valuation.
    ids["p_pct"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["direct"],
        asset_id=ids["a_pct"], as_of_date=AS_OF, authority="internal",
        source_system="manual", ownership_basis="percent",
        ownership_pct=PCT_OWNED,
    )
    ids["p_otherorg"] = await create_position(
        conn, org_id=OTHER_ORG_ID, owner_entity_id=ids["otherorg"],
        asset_id=ids["a_otherorg"], as_of_date=AS_OF, authority="custodial",
        source_system="reporting_tool_import", ownership_basis="value",
        market_value=V_OTHERORG,
    )

    # Run the REAL precedence resolver over the contested holding. Setting
    # superseded_by_source by hand would test the rollup against a state the
    # system does not actually produce.
    outcome = await resolve_holding(
        conn, DEFAULT_ORG_ID,
        owner_entity_id=ids["direct"], asset_id=ids["a_super"], as_of_date=AS_OF,
    )
    ids["_precedence_winner"] = outcome.winner_position_id
    ids["_precedence_winner_source"] = outcome.winner_source_system
    return ids


# ── Assertions ──────────────────────────────────────────────────────────────


async def check_rollup(conn, ids: dict) -> None:
    result = await rollup_entity_holdings(
        conn, org_id=DEFAULT_ORG_ID, as_of_date=AS_OF
    )
    print(f"       rollup run 1: {result.as_dict()['positions_considered']} positions "
          f"considered, {result.positions_valued} valued, "
          f"{result.buckets_written} buckets, {result.positions_skipped} skipped")
    for s in result.skipped:
        print(f"         · skipped {s.position_id}: {s.reason}")

    # [Y] Direct ownership.
    got = await bucket(conn, ids["direct"], TAX_DIRECT)
    check("A real position for a DIRECTLY-owned entity rolls up with the "
          "correct taxonomy_key and market_value",
          got == V_DIRECT,
          f"entity_holdings[{TAX_DIRECT}] = {got}, expected {V_DIRECT}")

    # [Y] LOOK-THROUGH, two levels up.
    acct = await bucket(conn, ids["lt_acct"], TAX_LT)
    trust = await bucket(conn, ids["lt_trust"], TAX_LT)
    indiv = await bucket(conn, ids["lt_indiv"], TAX_LT)
    check("LOOK-THROUGH: a position on an account owned by a trust owned by an "
          "individual reaches the INDIVIDUAL's own bucket, two levels up",
          indiv == V_LOOKTHROUGH,
          f"account={acct}, trust={trust}, INDIVIDUAL={indiv}, "
          f"expected individual={V_LOOKTHROUGH}")
    check("LOOK-THROUGH: the intermediate trust and the direct owner are ALSO "
          "attributed — the chain is populated at every level, not just the top",
          acct == V_LOOKTHROUGH and trust == V_LOOKTHROUGH,
          f"account={acct}, trust={trust}, expected both {V_LOOKTHROUGH}")

    # [Y] FRACTIONAL: the exact compounded figure.
    llc = await bucket(conn, ids["fr_llc"], TAX_FRAC)
    fr_trust = await bucket(conn, ids["fr_trust"], TAX_FRAC)
    fr_indiv = await bucket(conn, ids["fr_indiv"], TAX_FRAC)
    check("FRACTIONAL: an individual owning 50% of a trust that owns 60% of an "
          "LLC holding $100,000 sees EXACTLY $30,000.00 — not 50%, not 60%",
          fr_indiv == V_FRAC_INDIV,
          f"individual={fr_indiv}, expected {V_FRAC_INDIV} "
          f"(50%-only would be {V_FRACTIONAL / 2}, "
          f"60%-only would be {V_FRACTIONAL * Decimal('0.6')})")
    check("FRACTIONAL: the intervening trust sees its own 60%, and the LLC the "
          "whole position — compounding happens per level, not once",
          fr_trust == V_FRAC_TRUST and llc == V_FRACTIONAL,
          f"llc={llc} (expected {V_FRACTIONAL}), "
          f"trust={fr_trust} (expected {V_FRAC_TRUST})")

    # [Y] SUPERSEDED positions excluded.
    sup = await bucket(conn, ids["direct"], TAX_SUPER)
    check("A SUPERSEDED (precedence-losing) position is EXCLUDED: only the "
          "winner is counted, never both, never the loser",
          sup == V_SUPER_WINNER,
          f"bucket={sup}, winner={V_SUPER_WINNER} "
          f"(source {ids['_precedence_winner_source']}), "
          f"loser={V_SUPER_LOSER}, both-summed would be "
          f"{V_SUPER_WINNER + V_SUPER_LOSER}")

    # [Y] percent basis resolves through the asset's valuation.
    pct = await bucket(conn, ids["direct"], TAX_PCT)
    check("PERCENT BASIS: a 25% position with no stored market_value is valued "
          "at 25% of the asset's resolved appraisal, not skipped and not zeroed",
          pct == V_PCT_RUN1,
          f"bucket={pct}, expected {V_PCT_RUN1} "
          f"(= 25% of {V_PCT_ASSET_1})")

    return result


async def check_idempotence(conn, ids: dict) -> None:
    """[Y] Re-running UPDATES the existing rows rather than duplicating them."""
    before_rows = await fixture_holding_count(conn)
    before_direct = await bucket(conn, ids["direct"], TAX_DIRECT)

    await rollup_entity_holdings(conn, org_id=DEFAULT_ORG_ID, as_of_date=AS_OF)

    after_rows = await fixture_holding_count(conn)
    after_direct = await bucket(conn, ids["direct"], TAX_DIRECT)
    check("Re-running the rollup for the same org and as_of_date UPDATES the "
          "existing entity_holdings rows — row count identical, no duplicates",
          before_rows == after_rows and before_rows > 0
          and before_direct == after_direct,
          f"fixture holding rows before={before_rows}, after={after_rows}; "
          f"direct bucket {before_direct} → {after_direct}")

    # And prove the constraint is what is doing it, not luck.
    dupes = await conn.fetchval(
        f"""
        SELECT count(*) FROM (
            SELECT org_id, entity_id, taxonomy_key, as_of_date, source
            FROM {TABLE_HOLDINGS}
            WHERE entity_id IN (SELECT id FROM entities
                                WHERE display_name = ANY($1::text[]))
            GROUP BY 1, 2, 3, 4, 5 HAVING count(*) > 1
        ) d
        """,
        ENTITY_NAMES,
    )
    check("No duplicate (org, entity, taxonomy_key, as_of_date, source) bucket "
          "exists after two runs",
          dupes == 0, f"duplicate groups = {dupes}")


async def check_value_change(conn, ids: dict) -> None:
    """[Y] A superseding valuation is reflected on the SECOND run."""
    before = await bucket(conn, ids["direct"], TAX_PCT)

    # A real supersession: a NEW valuation row carrying a forward pointer at the
    # old one. record_valuation never updates the prior row (A2's Rule-3 shape),
    # so this is the exact state production reaches.
    await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["a_pct"],
        valuation_date=date(2026, 6, 30), value=V_PCT_ASSET_2,
        value_basis="total", status="final", valuation_method="appraisal",
        supersedes_valuation_id=ids["val_1"],
    )

    await rollup_entity_holdings(conn, org_id=DEFAULT_ORG_ID, as_of_date=AS_OF)
    after = await bucket(conn, ids["direct"], TAX_PCT)

    check("A position's value changing between two runs (a new valuation "
          "superseding an old one) IS reflected on the second run — the rollup "
          "reads current state, not a cached figure",
          before == V_PCT_RUN1 and after == V_PCT_RUN2,
          f"run 1 = {before} (25% of {V_PCT_ASSET_1}), "
          f"run 2 = {after}, expected {V_PCT_RUN2} (25% of {V_PCT_ASSET_2})")


async def check_stale_removal(conn, ids: dict) -> None:
    """A bucket the current positions no longer produce does not survive.

    Not in the brief's assertion list, and it is the failure the brief's own
    "re-run UPDATES rather than duplicates" assertion cannot see: an upsert
    touches only the keys it computes, so a holding that goes away leaves its
    last figure standing under the current date forever. Asserted here because
    the rollup deletes for exactly this reason.
    """
    before = await bucket(conn, ids["direct"], TAX_DIRECT)

    # Retire the position the honest way — close it on the valid-time axis.
    async with org_ctx(conn, DEFAULT_ORG_ID, super_admin=True) as c:
        await c.execute(
            f"UPDATE {TABLE_POSITIONS} SET valid_to = now() WHERE id = $1::uuid",
            ids["p_direct"],
        )

    await rollup_entity_holdings(conn, org_id=DEFAULT_ORG_ID, as_of_date=AS_OF)
    after = await bucket(conn, ids["direct"], TAX_DIRECT)
    check("A bucket whose position was retired between runs is REMOVED, not "
          "left standing at its stale figure",
          before == V_DIRECT and after is None,
          f"before={before}, after={after} (expected None)")

    # Restore it so the cross-org check and the counts run against the full set.
    async with org_ctx(conn, DEFAULT_ORG_ID, super_admin=True) as c:
        await c.execute(
            f"UPDATE {TABLE_POSITIONS} SET valid_to = NULL WHERE id = $1::uuid",
            ids["p_direct"],
        )


async def check_cross_org(app_conn, admin_conn, ids: dict) -> None:
    """[Y] Cross-org isolation, on the REAL app_service connection.

    Run under ``app_service`` with no super-admin escape hatch, which is the
    only configuration in which this proves anything: under a bypassrls role
    every query sees every org and the assertion passes vacuously.
    """
    other_total_before = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_HOLDINGS} h "
        f"JOIN entities e ON e.id = h.entity_id "
        f"WHERE e.display_name = $1",
        E_OTHERORG,
    )

    result = await rollup_entity_holdings(
        app_conn, org_id=DEFAULT_ORG_ID, as_of_date=AS_OF
    )

    leaked_row = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_HOLDINGS} h "
        f"JOIN entities e ON e.id = h.entity_id "
        f"WHERE e.display_name = $1",
        E_OTHERORG,
    )
    check("Cross-org isolation: the default org's rollup writes NO bucket for "
          "the other org's entity (real app_service connection, no super-admin)",
          leaked_row == other_total_before == 0,
          f"other-org buckets before={other_total_before}, after={leaked_row}")

    leaked_value = await admin_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_HOLDINGS} "
        f"WHERE org_id = $1::uuid AND market_value = $2 AND source = $3",
        DEFAULT_ORG_ID, V_OTHERORG, ROLLUP_SOURCE,
    )
    check("Cross-org isolation: the other org's position value never appears in "
          "any default-org bucket",
          leaked_value == 0,
          f"default-org buckets carrying the other org's ${V_OTHERORG}: "
          f"{leaked_value}")

    # And the other direction: the other org's own rollup sees only its own.
    other = await rollup_entity_holdings(
        app_conn, org_id=OTHER_ORG_ID, as_of_date=AS_OF
    )
    other_bucket = await admin_conn.fetchval(
        f"SELECT market_value FROM {TABLE_HOLDINGS} "
        f"WHERE entity_id = $1::uuid AND taxonomy_key = $2 AND source = $3",
        ids["otherorg"], TAX_DIRECT, ROLLUP_SOURCE,
    )
    check("Cross-org isolation: the OTHER org's own rollup produces its own "
          "bucket and does not pick up the default org's positions",
          other_bucket is not None
          and Decimal(str(other_bucket)) == V_OTHERORG
          and result.total_value != other.total_value,
          f"other-org bucket={other_bucket} (expected {V_OTHERORG}); "
          f"default-org total={result.total_value}, "
          f"other-org total={other.total_value}")


async def check_endpoint_gating() -> None:
    """[Y] The rollup endpoint is rejected for a non-admin caller.

    ``rbac.has_permission`` DEFAULT-ALLOWS a user with zero rows in
    ``user_roles`` (the single-admin stage). A "non-admin" fixture with no role
    at all would therefore be allowed, and the 403 assertion would fail for a
    reason that has nothing to do with this endpoint. The member below is given
    a real role that grants a real, different permission — the only shape in
    which the strict check actually runs.
    """
    try:
        import main
        from starlette.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        check("The rollup endpoint is REJECTED for a non-admin caller",
              False, f"could not import app/TestClient: {type(exc).__name__}: {exc}")
        return

    def _drive(sub: str, org: str):
        main.verify_token = lambda _t: {
            "sub": sub, "email": "verify_c@test.local", "org_id": org,
        }
        with TestClient(main.app, raise_server_exceptions=False) as client:
            return client.post(
                "/api/v1/portfolio/rollup",
                headers={"Authorization": "Bearer stub"},
                data={"as_of_date": AS_OF.isoformat()},
            )

    denied = await asyncio.to_thread(_drive, MEMBER_SUB, DEFAULT_ORG_ID)
    body = {}
    try:
        body = denied.json()
    except Exception:  # noqa: BLE001
        pass
    check("The rollup endpoint is REJECTED for a non-admin caller (403, naming "
          f"the {ROLLUP_PERMISSION!r} permission)",
          denied.status_code == 403
          and ROLLUP_PERMISSION in str(body.get("detail", "")),
          f"HTTP {denied.status_code}, body={body}")

    allowed = await asyncio.to_thread(_drive, ADMIN_SUB, DEFAULT_ORG_ID)
    allowed_body = {}
    try:
        allowed_body = allowed.json()
    except Exception:  # noqa: BLE001
        pass
    check("The rollup endpoint ACCEPTS an authorised caller and returns a real "
          "result (proves the 403 above is the permission gate, not a broken "
          "route)",
          allowed.status_code == 200
          and allowed_body.get("source") == ROLLUP_SOURCE
          and allowed_body.get("currency_code") == BASE_CURRENCY,
          f"HTTP {allowed.status_code}, "
          f"buckets_written={allowed_body.get('buckets_written')}, "
          f"positions_considered={allowed_body.get('positions_considered')}")


async def seed_member_role(conn) -> None:
    """A role for the member that grants something OTHER than manage_portfolio.

    Without it the member has zero user_roles rows and rbac default-allows them.
    """
    role_id = "99000000-0000-0000-0000-0000000000c1"
    perm_id = "99000000-0000-0000-0000-0000000000c2"
    await conn.execute(
        "INSERT INTO roles (id, org_id, name) VALUES ($1::uuid, $2::uuid, $3) "
        "ON CONFLICT (id) DO NOTHING",
        role_id, DEFAULT_ORG_ID, f"{FIXTURE_TAG} Read Only",
    )
    await conn.execute(
        "INSERT INTO permissions (id, name, resource, action) "
        "VALUES ($1::uuid, $2, $3, $4) ON CONFLICT (id) DO NOTHING",
        perm_id, f"{FIXTURE_TAG.lower()}_noop", "portfolioc_verify", "noop",
    )
    await conn.execute(
        "INSERT INTO role_permissions (role_id, permission_id) "
        "VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING",
        role_id, perm_id,
    )
    await conn.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES ($1::uuid, $2::uuid) "
        "ON CONFLICT DO NOTHING",
        MEMBER_USER_ID, role_id,
    )


async def teardown_member_role(conn) -> None:
    role_id = "99000000-0000-0000-0000-0000000000c1"
    perm_id = "99000000-0000-0000-0000-0000000000c2"
    await conn.execute("DELETE FROM user_roles WHERE role_id = $1::uuid", role_id)
    await conn.execute(
        "DELETE FROM role_permissions WHERE role_id = $1::uuid", role_id)
    await conn.execute("DELETE FROM roles WHERE id = $1::uuid", role_id)
    await conn.execute("DELETE FROM permissions WHERE id = $1::uuid", perm_id)


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
        await teardown_member_role(admin_conn)
        baseline = await counts(admin_conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        nonempty = {t: n for t, n in baseline.items() if n}
        if nonempty:
            report("TEARDOWN — rows are already present in these tables",
                   f"{nonempty}. Teardown is by-fixture + count assertion, NOT "
                   f"a truncate. public.entity_holdings in particular is read by "
                   f"S21, services/households.py and the RLS Batch-A "
                   f"verification — an unconditional truncate here would be a "
                   f"data-loss bug against another track.")

        await seed_users(admin_conn)
        await seed_member_role(admin_conn)

        print("\n── Task 1: discovery, reported AND asserted ──")
        await check_task1(admin_conn)

        print("\n── Fixtures: two ownership chains, one contested holding ──")
        ids = await build_fixtures(admin_conn)
        print(f"       precedence winner = {ids['_precedence_winner_source']} "
              f"({ids['_precedence_winner']})")

        print("\n── Task 2 + 3: the rollup, look-through and compounding ──")
        await check_rollup(admin_conn, ids)

        print("\n── Idempotence: the UPSERT on the real constraint ──")
        await check_idempotence(admin_conn, ids)

        print("\n── Current state, not a cached figure ──")
        await check_value_change(admin_conn, ids)

        print("\n── Stale buckets do not survive ──")
        await check_stale_removal(admin_conn, ids)

        print("\n── Cross-org isolation (real app_service connection) ──")
        await check_cross_org(app_conn, admin_conn, ids)

        print("\n── Task 4: the endpoint's permission gate ──")
        await check_endpoint_gating()

    finally:
        await teardown(admin_conn)                                   # END
        await teardown_member_role(admin_conn)
        if baseline:
            final = await counts(admin_conn)
            drift = {
                t: (baseline[t], final[t]) for t in TABLES if baseline[t] != final[t]
            }
            check(
                "TEARDOWN restores the EXACT before-count on every table it "
                "touched, including public.entity_holdings",
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
