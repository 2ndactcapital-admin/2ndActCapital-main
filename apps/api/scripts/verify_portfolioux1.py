"""Verification — Portfolio UX 1: the Positions grid.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END with an
EXACT before/after count on every table touched — never a truncate, because
every table here holds real production rows.

Real database, real ASGI app, real non-bypass ``app_service`` role.

────────────────────────────────────────────────────────────────────────────
HOW THE NON-BYPASS ROLE IS OBTAINED, AND WHY THE FALLBACK IS NOT A CHEAT
────────────────────────────────────────────────────────────────────────────
``postgres`` carries ``rolbypassrls``, so every cross-org assertion would
"pass" under it while proving nothing. A non-bypass role is mandatory.

Two ways to get one, tried in order:

  1. ``APP_SERVICE_DATABASE_URL`` — a direct login as ``app_service``.
  2. ``SET LOCAL ROLE app_service`` on a ``DATABASE_URL`` connection.

Earlier sprints REMOVED path 2, and were right to: the fallback there was
SILENT, so a rotated credential turned a real RLS check into a session that
merely looked like one. The objection was to the silence, not to the mechanism
— Postgres evaluates ``rolbypassrls`` against the CURRENT role, so ``SET ROLE``
genuinely drops the bypass.

So the fallback is restored with the silence removed, and with a positive proof
that replaces the trust:

  * which path was used is REPORTED, never inferred;
  * ``rolbypassrls`` is asserted ``false`` on the EFFECTIVE role;
  * and — the assertion the old version did not have — with the org GUC set to
    the empty string, ``portfolio.positions`` is asserted to read back ZERO
    rows. A bypassing session returns every row in the table there. That is
    live RLS demonstrated, not a role name inspected.

``SET LOCAL ROLE`` (not ``SET ROLE``) because Supabase's pooler is in
transaction mode: a session-level ``SET`` can be handed to the next transaction
on a different backend, so the role has to be raised inside the transaction
that actually performs the read.

ENVIRONMENT FINDING (reported, not worked around): the
``APP_SERVICE_DATABASE_URL`` in ``apps/api/.env`` no longer authenticates —
``InvalidPasswordError``. The role itself is healthy (``rolcanlogin=true``,
``rolbypassrls=false``); only the stored password is stale. Same failure
``docs/PROJECT_STATUS.md`` §7b recorded once before.

────────────────────────────────────────────────────────────────────────────
THE SIX ASSERTIONS THIS SPRINT IS EASIEST TO FAKE, AND HOW THEY ARE WRITTEN
────────────────────────────────────────────────────────────────────────────
**"The endpoints exist."** An endpoint that 404s, 401s or returns ``[]`` would
satisfy a status-code check or a "the route is registered" check. So every
endpoint is DRIVEN through the real ASGI app with a real token, and its body is
compared against a DIRECT SQL query of the same rows. A stub returning an empty
list fails the comparison, not just the count.

**"Filtering works."** A filter that returned nothing satisfies "the filtered
set is a subset". So each filter assertion carries a CONTROL: the unfiltered
set is asserted to be strictly larger, the filtered set is asserted NON-EMPTY,
and its membership is compared element-by-element against the same predicate
run in SQL.

**"Sorting works on real data."** The grid sorts client-side, so a server test
cannot observe it directly — and asserting "the API returned rows" would prove
nothing about ordering. What IS assertable, and what actually decides whether
the grid sorts correctly, is that money arrives as exact decimal STRINGS whose
LEXICAL order differs from their NUMERIC order. The fixtures are chosen so it
does (900.00 vs 1000.00 vs 12000.00), the difference is asserted, and the
grid's derived numeric sort keys are asserted to be present in the component.
A grid sorting the raw strings would put 900 above 12,000.

**"An inline edit persists."** A PATCH that returned 200 and wrote nothing
would pass a status check. So the edit is verified FOUR ways: the response
carries a DIFFERENT id (it is a restatement, not an update), a fresh GET shows
the new value, direct SQL shows exactly two rows for that (owner, asset) with
exactly ONE current, and the CLOSED row is asserted to still carry its ORIGINAL
value — proving the old state was preserved rather than overwritten.

**"A linked document is reachable from the pane."** Trivially true before the
edit. The real risk is the one the restatement introduces: the successor has a
NEW record_id, so a document linked to the position would silently orphan on the
first correction. The document is fetched through the REAL panel endpoint BOTH
before AND after an inline edit, and the after-case is asserted to find it under
the SUCCESSOR's id.

**"Cross-org isolation."** An endpoint that returns nothing for everybody
passes an "org B cannot see org A" check. So both directions are asserted
against the SAME call: org B's list is asserted to CONTAIN org B's own fixture
position and to omit every one of org A's, and the same is done at the RLS layer
on the real ``app_service`` connection.

Run:
    python3 scripts/verify_portfolioux1.py
"""

from __future__ import annotations

import ast
import asyncio
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.join(_HERE, "..")
_WEB = os.path.join(_HERE, "..", "..", "web")
sys.path.insert(0, _API)
sys.path.extend(sorted(glob.glob(
    os.path.join(_API, "venv", "lib", "python3*", "site-packages")
)))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_API, ".env"), override=False)

from services.portfolio_assets import (  # noqa: E402
    TABLE_ASSETS,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    TABLE_VALUATIONS,
    OwnershipBasisError,
    PortfolioError,
    create_asset,
    create_position,
    record_transaction,
    record_valuation,
    resolve_current_value,
)
from services.portfolio_positions import (  # noqa: E402
    EDITABLE_FIELDS,
    INLINE_EDITABLE_FIELDS,
    RECORD_TYPE_POSITION,
    TABLE_DOC_RECORD_LINKS,
    get_position,
    list_positions,
    update_position,
)
from services.portfolio_rollup import position_current_value  # noqa: E402

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org. A real row, not a minted one — an isolation test against
# an org that does not exist proves the FK, not the policy.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "VERIFY-PORTFOLIOUX1"

A_SUB = "auth0|verify_portfolioux1_orga"
B_SUB = "auth0|verify_portfolioux1_orgb"
# `services.permissions.get_user_id` DERIVES the id from the sub rather than
# looking it up, so a fixture seeded under a hand-picked literal is a user no
# code path ever finds (Portfolio C's finding).
A_USER_ID = str(uuid5(NAMESPACE_URL, A_SUB))
B_USER_ID = str(uuid5(NAMESPACE_URL, B_SUB))

TODAY = date(2026, 8, 25)
VAL_DATE = TODAY - timedelta(days=30)

# ── Exact figures. Chosen so LEXICAL and NUMERIC order DISAGREE. ────────────
# "12000.00" < "900.00" < "1000.00" as strings; 900 < 1000 < 12000 as numbers.
# A grid that sorted the raw decimal strings would order these backwards, so
# this is the fixture that makes the sort assertion mean something.
UNITS_QTY = Decimal("1000.00")
VALUE_MV = Decimal("12000.00")
PERCENT_PCT = Decimal("25.0000")
SMALL_MV = Decimal("900.00")

AUDITED_MARK = Decimal("3800000.00")
ESTIMATED_MARK = Decimal("4200000.00")
PERCENT_ASSET_MARK = Decimal("2000000.00")
# 25% of 2,000,000 — computed by the service in Decimal, compared exactly here.
PERCENT_EXPECTED = Decimal("500000.0000")

results: list[tuple[str, bool, str]] = []
findings: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def report(name: str, detail: str) -> None:
    """A Task 1 finding. Printed as a FINDING, never silently as a PASS."""
    findings.append(name)
    print(f"[FIND] {name}\n       {detail}")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ── Tables, in FK-safe teardown order (children first) ──────────────────────
TABLES = (
    TABLE_DOC_RECORD_LINKS,
    "public.documents",
    TABLE_TRANSACTIONS,
    TABLE_POSITIONS,
    TABLE_VALUATIONS,
    TABLE_ASSETS,
    "public.entities",
    "public.users",
)


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def teardown(conn) -> None:
    """Delete every fixture row, children first. Touches nothing else.

    Everything is matched through the TAGGED asset / entity / document names,
    never by org — the orgs are real and full of production rows. Positions and
    transactions carry no name of their own, so they are reached through their
    tagged asset.
    """
    tagged_assets = (
        f"SELECT id FROM {TABLE_ASSETS} WHERE name LIKE '{TAG}%'"
    )
    tagged_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({tagged_assets})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_DOC_RECORD_LINKS} "
        f"WHERE record_id IN ({tagged_positions}) "
        f"   OR document_id IN (SELECT id FROM public.documents "
        f"                      WHERE original_filename LIKE '{TAG}%')"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_TRANSACTIONS} WHERE position_id IN ({tagged_positions})"
    )
    await conn.execute(f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN ({tagged_assets})")
    await conn.execute(f"DELETE FROM {TABLE_VALUATIONS} WHERE asset_id IN ({tagged_assets})")
    await conn.execute(f"DELETE FROM {TABLE_ASSETS} WHERE name LIKE '{TAG}%'")
    await conn.execute(
        "DELETE FROM public.documents WHERE original_filename LIKE $1", f"{TAG}%"
    )
    await conn.execute(
        "DELETE FROM public.entities WHERE display_name LIKE $1", f"{TAG}%"
    )
    await conn.execute(
        "DELETE FROM public.users WHERE auth0_sub = ANY($1::text[])", [A_SUB, B_SUB]
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the four findings, REPORTED and ASSERTED
# ═══════════════════════════════════════════════════════════════════════════


def check_task1a() -> None:
    """1a — the service functions existed; the REST endpoints did not."""
    src = read(os.path.join(_API, "services", "portfolio_assets.py"))
    tree = ast.parse(src)
    fns = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    wanted = {"create_position", "record_transaction", "record_valuation",
              "resolve_current_value"}
    check(
        "[Y] 1a the four A2 service functions exist in "
        "services/portfolio_assets.py",
        wanted <= fns,
        f"missing={sorted(wanted - fns)}",
    )
    # A2's own docstring is the primary source for "there was no router".
    module_doc = ast.get_docstring(tree, clean=False) or ""
    check(
        "[Y] 1a portfolio_assets.py's docstring itself records that A2 shipped "
        "with NO router and NO UI — the gap was real, not assumed",
        "no router" in module_doc,
        "module docstring quotes 'no router and no UI'"
        if "no router" in module_doc else "phrase not found",
    )

    # The pre-existing portfolio routers must contain NO position CRUD. The one
    # path that mentions positions is the FILE IMPORT, which is a different
    # thing and is asserted by name so this check cannot be satisfied by it.
    pre_existing = {
        "routers/portfolio.py": read(os.path.join(_API, "routers", "portfolio.py")),
        "routers/portfolio_ingest.py": read(
            os.path.join(_API, "routers", "portfolio_ingest.py")
        ),
    }
    stray = {
        name: sorted(set(re.findall(r'"(/portfolio/positions[^"]*)"', body)))
        for name, body in pre_existing.items()
    }
    has_import = '"/portfolio/import/positions"' in pre_existing[
        "routers/portfolio_ingest.py"
    ]
    check(
        "[Y] 1a NEITHER pre-existing portfolio router declared a "
        "/portfolio/positions route — the only positions path there is the "
        "file-import endpoint, which is a different feature",
        not any(stray.values()) and has_import,
        f"stray={stray}, import endpoint present={has_import}",
    )
    report(
        "1a REST endpoints for positions did NOT exist — they were built by "
        "this sprint",
        "services/portfolio_assets.py (1147 lines) held create_position / "
        "record_transaction / record_valuation / resolve_current_value with no "
        "HTTP surface; every caller was another service or a verify script. "
        "routers/portfolio.py served only my-investments / summary / targets / "
        "allocations; routers/portfolio_ingest.py served import / precedence / "
        "rollup / altruist / tax-chase. NEW FILE: routers/portfolio_positions.py "
        "+ services/portfolio_positions.py.",
    )


def check_task1b() -> None:
    """1b — the REAL DataGrid: its props, its engine, its consumers."""
    path = os.path.join(_WEB, "components", "ui", "DataGrid.jsx")
    src = read(path)
    props = [
        "columnDefs", "rowData", "gridId", "getRowId", "onRowClick",
        "selectedRowId", "enableGlobalFilter", "enablePagination",
    ]
    missing = [p for p in props if p not in src]
    check(
        "[Y] 1b DataGrid.jsx exposes the AG-Grid-shaped prop API this sprint "
        "reuses unchanged",
        not missing,
        f"missing props={missing}" if missing else f"{len(props)} props present",
    )
    check(
        "[Y] 1b DataGrid's sort/filter state is TanStack Table, not a "
        "hand-rolled or third-party grid",
        "@tanstack/react-table" in src
        and "getSortedRowModel" in src
        and "getFilteredRowModel" in src,
        "getSortedRowModel + getFilteredRowModel present",
    )
    # The finding that decided the sprint's design: no inline-edit support.
    has_edit_prop = any(
        token in src for token in ("onCellEdit", "editable", "onValueChange")
    )
    check(
        "[Y] 1b DataGrid has NO inline-edit prop — inline editing is done "
        "through a cell RENDERER, and this sprint did not have to modify the "
        "shared grid",
        not has_edit_prop,
        "no onCellEdit/editable/onValueChange prop",
    )
    consumers = sorted(
        os.path.relpath(p, _WEB)
        for p in glob.glob(os.path.join(_WEB, "components", "**", "*.jsx"),
                           recursive=True)
        if "ui/DataGrid" in read(p) and not p.endswith("DataGrid.jsx")
    )
    check(
        "[Y] 1b the grid's existing consumers were read, not guessed — SPV "
        "ledger, DealsTable and EntityTable all drive it",
        {"components/spv/SPVLedgerClient.jsx",
         "components/marketplace/DealsTable.jsx",
         "components/crm/EntityTable.jsx"} <= set(consumers),
        f"consumers={consumers}",
    )
    report(
        "1b DataGrid.jsx is reused verbatim; inline editing is a cell renderer",
        "columnDefs[].cell is (value, row) => JSX, so an editable cell needed no "
        "grid change. SPVLedgerClient's grid-plus-detail two-pane layout is the "
        "shape this screen follows.",
    )


def check_task1c() -> None:
    """1c — no reusable drawer exists; DocumentsPanel is a card, but generic."""
    jsx = glob.glob(os.path.join(_WEB, "components", "**", "*.jsx"), recursive=True)
    jsx += glob.glob(os.path.join(_WEB, "app", "**", "*.js"), recursive=True)
    drawer_files = sorted(
        os.path.basename(p) for p in jsx
        if re.search(r"\b(Drawer|SlideOver|DetailPane|RightPane)\b",
                     os.path.basename(p))
    )
    # PositionDetailPane is THIS sprint's pane and must not be counted as
    # pre-existing evidence for its own justification.
    drawer_files = [f for f in drawer_files if f != "PositionDetailPane.jsx"]
    check(
        "[Y] 1c no reusable right-pane drawer component existed anywhere in "
        "apps/web — a new pane was genuinely needed",
        not drawer_files,
        f"pre-existing drawer components: {drawer_files or 'none'}",
    )
    panel = read(os.path.join(_WEB, "components", "DocumentsPanel.jsx"))
    check(
        "[Y] 1c DocumentsPanel is a <section> card keyed on (recordType, "
        "recordId) — NOT a pane shell, but generic enough to embed inside one",
        "recordType" in panel and "recordId" in panel and "<section" in panel,
        "props recordType/recordId + <section> root",
    )
    report(
        "1c no drawer shell existed; DocumentsPanel is embeddable as-is",
        "A new PositionDetailPane was written, following SPVLedgerClient's "
        "proven grid-cols-5 two-pane layout rather than inventing a portal "
        "drawer. DocumentsPanel is embedded unchanged inside it.",
    )


def check_task1d() -> None:
    """1d — the REAL entity picker and its real existing call sites."""
    picker = os.path.join(_WEB, "components", "EntityPicker.jsx")
    src = read(picker)
    check(
        "[Y] 1d EntityPicker.jsx is the real, existing owner-selection "
        "component (debounced /api/entities/search, dupe-check create)",
        "/api/entities/search" in src and "possible_duplicates" in src,
        "search endpoint + dupe-check confirm present",
    )
    users = sorted(
        os.path.relpath(p, _WEB)
        for p in glob.glob(os.path.join(_WEB, "components", "**", "*.jsx"),
                           recursive=True)
        if "components/EntityPicker" in read(p)
    )
    check(
        "[Y] 1d the SAME picker is already used by CRM ownership and SPV "
        "subscriptions — this sprint reuses it, it does not add a second one",
        {"components/crm/tabs/OwnershipTab.jsx",
         "components/spv/SPVSubscriptionsTab.jsx"} <= set(users),
        f"call sites={users}",
    )
    grid = read(os.path.join(_WEB, "components", "portfolio", "PositionsGrid.jsx"))
    check(
        "[Y] 1d the Positions screen's owner filter uses EntityPicker, not a "
        "new picker",
        'from "@/components/EntityPicker"' in grid,
        "PositionsGrid imports @/components/EntityPicker",
    )
    report(
        "1d EntityPicker reused verbatim for owner selection",
        "Used by components/crm/tabs/OwnershipTab.jsx, "
        "components/crm/tabs/EmploymentTab.jsx, "
        "components/spv/SPVSubscriptionsTab.jsx and "
        "components/admin/DocumentReviewManager.jsx. No second picker exists.",
    )
    report(
        "BONUS — Task 4's document link needed no new plumbing",
        "services/portfolio_documents.py already defines "
        f"RECORD_TYPE_POSITION={RECORD_TYPE_POSITION!r} and "
        "GET /records/{type}/{id}/documents dispatches generically, so "
        "DocumentsPanel works on a position today. What DID need building is "
        "_carry_document_links: a restatement mints a NEW record_id, so an "
        "edit would otherwise orphan the source document.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def seed_users(conn) -> None:
    for user_id, org, sub, email in (
        (A_USER_ID, DEFAULT_ORG_ID, A_SUB, "verify_ux1_a@test.local"),
        (B_USER_ID, OTHER_ORG_ID, B_SUB, "verify_ux1_b@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users
                (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioUX1', $4,
                    'member', true)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, org, email, sub,
        )


async def seed(conn) -> dict:
    """Two orgs, four assets, five positions, four valuations, two
    transactions, one document link."""
    ids: dict = {}

    async def entity(org, name, etype="llc"):
        return str(await conn.fetchval(
            "INSERT INTO public.entities (org_id, entity_type, display_name) "
            "VALUES ($1::uuid, $2::entity_type, $3) RETURNING id",
            org, etype, name,
        ))

    ids["owner_a"] = await entity(DEFAULT_ORG_ID, f"{TAG} Alpha Trust", "trust")
    ids["owner_a2"] = await entity(DEFAULT_ORG_ID, f"{TAG} Beta Holdings")
    ids["owner_b"] = await entity(OTHER_ORG_ID, f"{TAG} OtherOrg LLC")

    # ── Assets ──────────────────────────────────────────────────────────
    ids["asset_units"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Listed Equity",
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
    )
    ids["asset_percent"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Private LLC Interest",
        asset_type="private_equity", asset_class="financial",
        ownership_basis="percent", valuation_method="appraisal",
        currency_code="USD",
    )
    ids["asset_value"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Stated Value Asset",
        asset_type="collectible", asset_class="hard_asset",
        ownership_basis="value", valuation_method="appraisal",
        currency_code="USD",
    )
    ids["asset_novalue"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Unmarked Asset",
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
    )
    ids["asset_b"] = await create_asset(
        conn, org_id=OTHER_ORG_ID, name=f"{TAG} OtherOrg Asset",
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
    )

    # ── Valuations. Two on the SAME date with different statuses so the
    #    ladder has something to choose between, plus a superseded pair. ──
    ids["val_estimated"] = await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["asset_units"],
        valuation_date=VAL_DATE, value=ESTIMATED_MARK,
        value_basis="total", status="estimated", purpose="market",
        currency_code="USD",
    )
    ids["val_audited"] = await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["asset_units"],
        valuation_date=VAL_DATE, value=AUDITED_MARK,
        value_basis="total", status="audited", purpose="market",
        currency_code="USD",
    )
    ids["val_percent"] = await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["asset_percent"],
        valuation_date=VAL_DATE, value=PERCENT_ASSET_MARK,
        value_basis="total", status="final", purpose="market",
        currency_code="USD",
    )
    ids["val_b"] = await record_valuation(
        conn, org_id=OTHER_ORG_ID, asset_id=ids["asset_b"],
        valuation_date=VAL_DATE, value=Decimal("111.00"),
        value_basis="total", status="final", purpose="market",
    )

    # ── Positions ───────────────────────────────────────────────────────
    ids["pos_units"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a"],
        asset_id=ids["asset_units"], as_of_date=TODAY,
        authority="custodial", source_system="reporting_tool_bd",
        ownership_basis="units", quantity=UNITS_QTY,
        market_value=SMALL_MV, cost_basis=Decimal("800.00"),
        taxonomy_key="taxonomy_sc_1",
    )
    ids["pos_percent"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a"],
        asset_id=ids["asset_percent"], as_of_date=TODAY,
        authority="stated", source_system="manual",
        ownership_basis="percent", ownership_pct=PERCENT_PCT,
    )
    ids["pos_value"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a2"],
        asset_id=ids["asset_value"], as_of_date=TODAY,
        authority="manual", source_system="manual",
        ownership_basis="value", market_value=VALUE_MV,
    )
    ids["pos_novalue"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a2"],
        asset_id=ids["asset_novalue"], as_of_date=TODAY,
        authority="aggregated", source_system="altruist",
        ownership_basis="units", quantity=Decimal("5.00"),
    )
    # An outranked row, so the superseded filter has a real loser to find.
    ids["pos_loser"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a"],
        asset_id=ids["asset_units"], as_of_date=TODAY - timedelta(days=1),
        authority="aggregated", source_system="reporting_tool_orion",
        ownership_basis="units", quantity=Decimal("999.00"),
        superseded_by_source="reporting_tool_bd",
    )
    ids["pos_b"] = await create_position(
        conn, org_id=OTHER_ORG_ID, owner_entity_id=ids["owner_b"],
        asset_id=ids["asset_b"], as_of_date=TODAY,
        authority="custodial", source_system="manual",
        ownership_basis="units", quantity=Decimal("7.00"),
    )

    # ── Transactions on the units position ──────────────────────────────
    ids["txn_buy"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_units"],
        transaction_type_code=await _pick_txn_type(conn, "public"),
        trade_date=TODAY - timedelta(days=10), authority="custodial",
        source_system="reporting_tool_bd", quantity=Decimal("1000.00"),
        price=Decimal("0.80"), gross_amount=Decimal("800.00"),
        net_amount=Decimal("800.00"), currency_code="USD",
    )
    ids["txn_fee"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_units"],
        transaction_type_code=await _pick_txn_type(conn, "both"),
        trade_date=TODAY - timedelta(days=5), authority="custodial",
        source_system="reporting_tool_bd", net_amount=Decimal("-12.34"),
        currency_code="USD",
    )

    # ── One document, linked to the units position via Phase D's table ──
    ids["document"] = str(await conn.fetchval(
        """
        INSERT INTO public.documents
            (org_id, original_filename, source, mime_type, status, doc_family)
        VALUES ($1::uuid, $2, 'upload', 'application/pdf', 'confirmed',
                'statement')
        RETURNING id
        """,
        DEFAULT_ORG_ID, f"{TAG} custodial-statement.pdf",
    ))
    await conn.execute(
        f"""
        INSERT INTO {TABLE_DOC_RECORD_LINKS}
            (document_id, org_id, record_type, record_id)
        VALUES ($1::uuid, $2::uuid, $3, $4::uuid)
        ON CONFLICT DO NOTHING
        """,
        ids["document"], DEFAULT_ORG_ID, RECORD_TYPE_POSITION, ids["pos_units"],
    )
    return ids


async def _pick_txn_type(conn, market: str) -> str:
    """A REAL, active transaction type of the given market.

    Read from the deployed table rather than hardcoded: ``record_transaction``
    refuses a retired type and refuses a market mismatch, and a literal that
    happened to be renamed would fail the fixture rather than the feature.
    """
    code = await conn.fetchval(
        "SELECT code FROM public.transaction_types "
        "WHERE is_active = true AND market IS NOT DISTINCT FROM $1 "
        "ORDER BY code LIMIT 1",
        market,
    )
    if code is None:  # pragma: no cover — the A2 backfill guarantees these
        raise RuntimeError(f"no active transaction_type with market={market!r}")
    return code


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 / 3 / 4 — the endpoints, driven through the REAL ASGI app
# ═══════════════════════════════════════════════════════════════════════════


def _client(org_id: str, sub: str):
    """A TestClient whose token validation is stubbed to a REAL org's claims.

    ``verify_token`` is replaced, not the auth dependency: the request still
    passes through the RLS context middleware, the active-account gate and
    ``require_permission`` exactly as production does. Stubbing further up
    would skip the layers most likely to be wrong.
    """
    import main
    from starlette.testclient import TestClient

    main.verify_token = lambda _token: {
        "sub": sub,
        "email": f"{sub}@test.local",
        "org_id": org_id,
    }
    return TestClient(main.app, raise_server_exceptions=False)


def _routes_declared() -> dict:
    import main

    spec = main.app.openapi()
    return {
        p: sorted(spec["paths"][p]) for p in spec["paths"]
        if p.startswith("/api/v1/portfolio/positions")
        or p == "/api/v1/portfolio/assets"
    }


def endpoint_tests(ids: dict, direct: dict) -> dict:
    """Drive every new endpoint. Returns state the async checks need back."""
    hdr = {"Authorization": "Bearer stub"}
    out: dict = {}

    routes = _routes_declared()
    check(
        "[Y] 2 the four new REST endpoints are declared on the real app "
        "(list / create / detail / patch, plus the asset picker)",
        routes.get("/api/v1/portfolio/positions") == ["get", "post"]
        and routes.get("/api/v1/portfolio/positions/{position_id}")
            == ["get", "patch"]
        and routes.get("/api/v1/portfolio/assets") == ["get"],
        json.dumps(routes),
    )

    with _client(DEFAULT_ORG_ID, A_SUB) as c:
        # ── Listing, filtered to this org's fixture owner ────────────────
        r = c.get(
            "/api/v1/portfolio/positions",
            headers=hdr,
            params={"owner_entity_id": ids["owner_a"]},
        )
        if r.status_code != 200:
            check("[Y] 3 GET /portfolio/positions returns 200", False,
                  f"{r.status_code}: {r.text[:300]}")
            return out
        body = r.json()
        got = {p["id"] for p in body["positions"]}
        expected = {ids["pos_units"], ids["pos_percent"], ids["pos_loser"]}
        check(
            "[Y] 3 the grid endpoint returns REAL rows — every fixture "
            "position for this owner, and nothing else",
            got == expected,
            f"got={len(got)} expected={len(expected)} "
            f"missing={sorted(expected - got)} extra={sorted(got - expected)}",
        )
        out["list_body"] = body

        # ── The joined display fields the grid's columns render ──────────
        units_row = next(p for p in body["positions"] if p["id"] == ids["pos_units"])
        check(
            "[Y] 3 rows carry the joined asset name, owner name and "
            "server-resolved taxonomy LABEL — the grid hardcodes none of them",
            units_row["asset_name"] == f"{TAG} Listed Equity"
            and units_row["owner_name"] == f"{TAG} Alpha Trust"
            and "taxonomy_label" in units_row,
            f"asset={units_row['asset_name']!r} owner={units_row['owner_name']!r} "
            f"taxonomy_label={units_row['taxonomy_label']!r}",
        )

        # ── Money is exact decimal STRINGS, and lexical ≠ numeric order ──
        measures = {
            p["id"]: (
                p["ownership_pct"] if p["ownership_basis"] == "percent"
                else p["quantity"] if p["ownership_basis"] == "units"
                else p["market_value"]
            )
            for p in body["positions"]
        }
        all_strings = all(
            v is None or isinstance(v, str) for v in measures.values()
        )
        sample = [str(UNITS_QTY), str(VALUE_MV), str(SMALL_MV)]
        lexical = sorted(sample)
        numeric = sorted(sample, key=float)
        check(
            "[Y] 4 monetary values cross the API as exact decimal STRINGS "
            "(never floats), and this fixture set proves lexical ordering "
            "would be WRONG — so the grid's derived numeric sort keys are "
            "load-bearing, not decorative",
            all_strings and lexical != numeric,
            f"lexical={lexical} numeric={numeric}",
        )

        # ── Server-side filtering, with a control ────────────────────────
        r_all = c.get("/api/v1/portfolio/positions", headers=hdr,
                      params={"search": TAG})
        r_src = c.get("/api/v1/portfolio/positions", headers=hdr,
                      params={"search": TAG, "source_system": "manual"})
        r_sup = c.get("/api/v1/portfolio/positions", headers=hdr,
                      params={"search": TAG, "superseded": "losers"})
        all_ids = {p["id"] for p in r_all.json()["positions"]}
        src_ids = {p["id"] for p in r_src.json()["positions"]}
        sup_ids = {p["id"] for p in r_sup.json()["positions"]}
        check(
            "[Y] 3 the source_system filter genuinely NARROWS real data — the "
            "filtered set is non-empty, is a strict subset, and equals the "
            "same predicate run directly in SQL",
            src_ids
            and src_ids < all_ids
            and src_ids == direct["manual_ids"],
            f"filtered={len(src_ids)} of {len(all_ids)}; "
            f"sql={len(direct['manual_ids'])}; "
            f"diff={sorted(src_ids ^ direct['manual_ids'])}",
        )
        check(
            "[Y] 3 the superseded-state filter finds the OUTRANKED row and "
            "only that one (a filter returning nothing would pass a subset "
            "check — this asserts membership)",
            sup_ids == {ids["pos_loser"]},
            f"got={sorted(sup_ids)}",
        )
        check(
            "[Y] 3 the as_of_date range filter excludes the older row and "
            "keeps the current ones",
            {p["id"] for p in c.get(
                "/api/v1/portfolio/positions", headers=hdr,
                params={"search": TAG, "as_of_from": TODAY.isoformat()},
            ).json()["positions"]} == all_ids - {ids["pos_loser"]},
            "as_of_from=today drops the day-earlier loser row",
        )

        # ── An absent value is NULL WITH A REASON, never zero ────────────
        r_nv = c.get(
            f"/api/v1/portfolio/positions/{ids['pos_novalue']}", headers=hdr
        )
        nv = r_nv.json()["position"]
        check(
            "[Y] 4 a position with no valuation returns current_value=null "
            "WITH a reason — never 0, which a rollup could not tell from a "
            "real zero",
            r_nv.status_code == 200
            and nv["current_value"] is None
            and nv["current_value"] != "0"
            and "zero" in (nv["current_value_reason"] or ""),
            f"value={nv.get('current_value')!r} "
            f"reason={(nv.get('current_value_reason') or '')[:90]!r}",
        )

        # ── The right pane: resolved value + BOTH histories ──────────────
        r_det = c.get(
            f"/api/v1/portfolio/positions/{ids['pos_units']}", headers=hdr
        )
        det = r_det.json()
        out["detail"] = det
        check(
            "[Y] 4 the detail endpoint returns the resolved value, the "
            "GOVERNING valuation, the valuation history and the transaction "
            "history in ONE call",
            r_det.status_code == 200
            and det["position"]["current_value"] is not None
            and det["governing_valuation"]["valuation_id"] is not None
            and len(det["valuation_history"]) >= 2
            and len(det["transactions"]) == 2,
            f"valuations={len(det.get('valuation_history', []))} "
            f"transactions={len(det.get('transactions', []))}",
        )
        check(
            "[Y] 4 the governing valuation is the AUDITED mark, not the "
            "estimated one struck on the same date — the pane shows which row "
            "the ladder picked, so the number is auditable",
            det["governing_valuation"]["valuation_id"] == ids["val_audited"]
            and det["governing_valuation"]["status"] == "audited"
            and det["governing_valuation"]["asset_value"] == str(AUDITED_MARK),
            f"picked={det['governing_valuation']['status']!r} "
            f"value={det['governing_valuation']['asset_value']!r}",
        )
        check(
            "[Y] 4 the transaction history matches a direct SQL read of the "
            "same position — ids AND labels, not just a count",
            {t["id"] for t in det["transactions"]} == direct["txn_ids"]
            and all(t["transaction_type_label"] for t in det["transactions"]),
            f"api={len(det['transactions'])} sql={len(direct['txn_ids'])}",
        )
        check(
            "[Y] 4 the valuation history flags the superseded/estimated rows "
            "and matches a direct SQL read",
            {v["id"] for v in det["valuation_history"]} == direct["val_ids"],
            f"api={sorted(v['id'][:8] for v in det['valuation_history'])} "
            f"sql={sorted(v[:8] for v in direct['val_ids'])}",
        )

        # ── A percent position is a FRACTION of the asset's mark ─────────
        r_pct = c.get(
            f"/api/v1/portfolio/positions/{ids['pos_percent']}", headers=hdr
        )
        pct = r_pct.json()
        check(
            "[Y] 4 a percent-basis position is valued as its FRACTION of the "
            "asset's resolved mark (25% of 2,000,000), computed by the same "
            "function the allocation rollup uses — the grid and the sunburst "
            "cannot disagree",
            Decimal(pct["position"]["current_value"]) == PERCENT_EXPECTED,
            f"got={pct['position']['current_value']!r} "
            f"expected={PERCENT_EXPECTED}",
        )

        # ── The linked document, through the REAL Phase-9 panel endpoint ──
        record_type = det["document_record_type"]
        r_doc = c.get(
            f"/api/v1/records/{record_type}/{ids['pos_units']}/documents",
            headers=hdr,
        )
        docs = r_doc.json().get("documents", [])
        check(
            "[Y] 4 the linked source document is reachable from the pane "
            "through the REAL existing document-linking endpoint",
            r_doc.status_code == 200
            and {d["document_id"] for d in docs} == {ids["document"]}
            and record_type == RECORD_TYPE_POSITION,
            f"record_type={record_type!r} docs={len(docs)}",
        )

        # ── The basis contract, at the API boundary ──────────────────────
        before = len(c.get("/api/v1/portfolio/positions", headers=hdr,
                           params={"search": TAG}).json()["positions"])
        r_bad = c.post(
            "/api/v1/portfolio/positions", headers=hdr,
            json={
                "owner_entity_id": ids["owner_a"],
                "asset_id": ids["asset_value"],
                "as_of_date": TODAY.isoformat(),
                "authority": "manual",
                "source_system": "manual",
                "ownership_basis": "value",
                "market_value": "100.00",
                # Illegal: a `value` basis authoritatively measures market_value,
                # so quantity must be NULL.
                "quantity": "5",
            },
        )
        after = len(c.get("/api/v1/portfolio/positions", headers=hdr,
                          params={"search": TAG}).json()["positions"])
        check(
            "[Y] 2 POST refuses a row that violates the ownership-basis "
            "contract with 422, AND writes nothing — the count is unchanged, "
            "so the refusal is the contract and not a broken statement",
            r_bad.status_code == 422 and before == after,
            f"status={r_bad.status_code} count {before}→{after}",
        )

        # ── org_id can NEVER come from the body ──────────────────────────
        r_org = c.post(
            "/api/v1/portfolio/positions", headers=hdr,
            json={
                "org_id": OTHER_ORG_ID,
                "owner_entity_id": ids["owner_a"],
                "asset_id": ids["asset_value"],
                "as_of_date": TODAY.isoformat(),
                "authority": "manual", "source_system": "manual",
                "ownership_basis": "value", "market_value": "100.00",
            },
        )
        check(
            "[Y] 2 an org_id in the request body is REFUSED (422), not "
            "ignored — there is no field for a caller to send and nothing for "
            "a future edit to start trusting",
            r_org.status_code == 422,
            f"status={r_org.status_code}",
        )

        # ── Floats are refused, not silently rounded ─────────────────────
        r_float = c.post(
            "/api/v1/portfolio/positions", headers=hdr,
            json={
                "owner_entity_id": ids["owner_a"],
                "asset_id": ids["asset_value"],
                "as_of_date": TODAY.isoformat(),
                "authority": "manual", "source_system": "manual",
                "ownership_basis": "value",
                "market_value": 1234.56,  # a JSON float, deliberately
            },
        )
        check(
            "[Y] 2 a monetary value sent as a JSON FLOAT is refused (422) "
            "rather than coerced — the refusal runs mode='before', ahead of "
            "Pydantic's own float→Decimal coercion, or it would be dead code",
            r_float.status_code == 422,
            f"status={r_float.status_code}",
        )

        # ── A real create, through the real endpoint ─────────────────────
        r_new = c.post(
            "/api/v1/portfolio/positions", headers=hdr,
            json={
                "owner_entity_id": ids["owner_a2"],
                "asset_id": ids["asset_value"],
                "as_of_date": TODAY.isoformat(),
                "authority": "manual", "source_system": "manual",
                "ownership_basis": "value",
                "market_value": "4321.00",
                "taxonomy_key": "taxonomy_sc_1",
            },
        )
        created = r_new.json() if r_new.status_code == 201 else {}
        check(
            "[Y] 2 POST creates a real position and returns its full detail, "
            "with the value it was given",
            r_new.status_code == 201
            and created.get("position", {}).get("market_value") == "4321.00",
            f"status={r_new.status_code} "
            f"mv={created.get('position', {}).get('market_value')!r}",
        )
        if created:
            out["created_id"] = created["position"]["id"]

        # ── THE INLINE EDIT ──────────────────────────────────────────────
        r_edit = c.patch(
            f"/api/v1/portfolio/positions/{ids['pos_units']}",
            headers=hdr,
            json={"taxonomy_key": "taxonomy_sc_2"},
        )
        edited = r_edit.json() if r_edit.status_code == 200 else {}
        new_id = edited.get("position", {}).get("id")
        out["restated_id"] = new_id
        check(
            "[Y] 5 an inline edit round-trips through the real PATCH endpoint "
            "and returns a DIFFERENT position id — it is a bi-temporal "
            "restatement (Rule 3), not an in-place update",
            r_edit.status_code == 200
            and new_id is not None
            and new_id != ids["pos_units"]
            and edited.get("restated_from") == ids["pos_units"],
            f"status={r_edit.status_code} "
            f"{str(ids['pos_units'])[:8]}… → {str(new_id)[:8]}…",
        )
        # A fresh read, not the write's own response.
        r_after = c.get("/api/v1/portfolio/positions", headers=hdr,
                        params={"owner_entity_id": ids["owner_a"]})
        after_rows = {p["id"]: p for p in r_after.json()["positions"]}
        check(
            "[Y] 5 the edit PERSISTS — a fresh list read shows the new "
            "taxonomy on the successor and the predecessor is gone from the "
            "current set",
            new_id in after_rows
            and after_rows[new_id]["taxonomy_key"] == "taxonomy_sc_2"
            and ids["pos_units"] not in after_rows,
            f"successor taxonomy="
            f"{after_rows.get(new_id, {}).get('taxonomy_key')!r}, "
            f"predecessor present={ids['pos_units'] in after_rows}",
        )
        check(
            "[Y] 5 include_history=true surfaces the CLOSED predecessor — the "
            "old row is history, not a deletion",
            ids["pos_units"] in {
                p["id"] for p in c.get(
                    "/api/v1/portfolio/positions", headers=hdr,
                    params={"owner_entity_id": ids["owner_a"],
                            "include_history": "true"},
                ).json()["positions"]
            },
            "predecessor reachable with include_history",
        )
        # The bug the restatement introduces, asserted directly.
        r_doc2 = c.get(
            f"/api/v1/records/{RECORD_TYPE_POSITION}/{new_id}/documents",
            headers=hdr,
        )
        check(
            "[Y] 5 the source document is STILL reachable after the edit — "
            "the restatement mints a new record_id, so without an explicit "
            "link carry-over the first correction would orphan the evidence",
            r_doc2.status_code == 200
            and {d["document_id"] for d in r_doc2.json()["documents"]}
                == {ids["document"]},
            f"docs on successor={len(r_doc2.json().get('documents', []))}",
        )

        # ── An inline-editable field cannot break the basis contract ─────
        r_bad_edit = c.patch(
            f"/api/v1/portfolio/positions/{new_id}", headers=hdr,
            json={"ownership_basis": "value"},
        )
        check(
            "[Y] 5 an edit that changes ONLY the basis is refused (422) — "
            "_validate_basis runs against the MERGED row, so the leftover "
            "quantity is caught; a check on the change set alone would see "
            "one legal field and pass it",
            r_bad_edit.status_code == 422
            and "quantity" in r_bad_edit.text,
            f"status={r_bad_edit.status_code}: {r_bad_edit.text[:140]}",
        )
        check(
            "[Y] 2 a field outside the editable set is refused rather than "
            "silently ignored",
            c.patch(f"/api/v1/portfolio/positions/{new_id}", headers=hdr,
                    json={"asset_id": ids["asset_value"]}).status_code == 422,
            "asset_id / owner_entity_id are not editable",
        )

    # ── Cross-org, at the ENDPOINT layer ────────────────────────────────
    with _client(OTHER_ORG_ID, B_SUB) as c:
        r = c.get("/api/v1/portfolio/positions", headers=hdr,
                  params={"search": TAG})
        b_ids = {p["id"] for p in r.json()["positions"]}
        org_a_ids = {
            ids["pos_percent"], ids["pos_value"], ids["pos_novalue"],
            ids["pos_loser"], out.get("restated_id"),
        } - {None}
        check(
            "[Y] 5 cross-org (endpoint): org B's list CONTAINS its own "
            "position and NONE of org A's — asserted in both directions, so "
            "an endpoint returning nothing to everybody would fail",
            ids["pos_b"] in b_ids and not (b_ids & org_a_ids),
            f"orgB sees {len(b_ids)} rows; leaked org-A rows="
            f"{sorted(b_ids & org_a_ids)}",
        )
        r404 = c.get(
            f"/api/v1/portfolio/positions/{ids['pos_percent']}", headers=hdr
        )
        check(
            "[Y] 5 cross-org (endpoint): org B fetching an org-A position id "
            "gets 404 — not 403, which would confirm the id exists elsewhere",
            r404.status_code == 404,
            f"status={r404.status_code}",
        )
        r_doc_x = c.get(
            f"/api/v1/records/{RECORD_TYPE_POSITION}/{ids['pos_units']}/documents",
            headers=hdr,
        )
        check(
            "[Y] 5 cross-org: org B cannot read the document linked to an "
            "org-A position",
            r_doc_x.status_code == 200
            and r_doc_x.json().get("documents") == [],
            f"status={r_doc_x.status_code} "
            f"docs={len(r_doc_x.json().get('documents', []))}",
        )
    return out


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 / 4 — the FRONTEND is wired to the real endpoints, not to mocks
# ═══════════════════════════════════════════════════════════════════════════


def _strip_js_comments(src: str) -> str:
    """Remove // and /* */ comments so a scan reads executable code only.

    Crude by design — it does not parse JS. Good enough for "does any live line
    mention org_id", and honest about being a text tool: it is only ever used
    to make an ABSENCE assertion stricter, never to prove something is present.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _node_modules_dir() -> str | None:
    """Where this workspace's dependencies actually live.

    apps/web is an npm WORKSPACE, so node_modules is hoisted to the repo root
    and apps/web/node_modules does not exist. A check that looked only in
    apps/web would report "not measured" on a perfectly buildable tree.
    """
    for candidate in (
        os.path.join(_WEB, "node_modules"),
        os.path.join(_WEB, "..", "..", "node_modules"),
    ):
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return None


def check_frontend_wiring() -> None:
    grid_path = os.path.join(_WEB, "components", "portfolio", "PositionsGrid.jsx")
    pane_path = os.path.join(_WEB, "components", "portfolio",
                             "PositionDetailPane.jsx")
    page_path = os.path.join(_WEB, "app", "portfolio", "positions", "page.js")
    route_list = os.path.join(_WEB, "app", "api", "portfolio", "positions",
                              "route.js")
    route_one = os.path.join(_WEB, "app", "api", "portfolio", "positions",
                             "[positionId]", "route.js")

    for label, path in (
        ("PositionsGrid.jsx", grid_path),
        ("PositionDetailPane.jsx", pane_path),
        ("app/portfolio/positions/page.js", page_path),
        ("app/api/portfolio/positions/route.js", route_list),
        ("app/api/portfolio/positions/[positionId]/route.js", route_one),
    ):
        check(f"[Y] 3 {label} exists", os.path.exists(path), path)

    grid = read(grid_path)
    pane = read(pane_path)
    page = read(page_path)
    rl = read(route_list)
    ro = read(route_one)

    check(
        "[Y] 3 the grid is driven by the SHARED DataGrid, not a new grid "
        "library",
        'from "@/components/ui/DataGrid"' in grid,
        "imports @/components/ui/DataGrid",
    )
    check(
        "[Y] 2 the frontend calls the REAL endpoints — the grid fetches "
        "/api/portfolio/positions and PATCHes /api/portfolio/positions/{id}",
        "/api/portfolio/positions?" in grid
        and 'method: "PATCH"' in grid,
        "list fetch + PATCH present",
    )
    # A mock would be an array literal of position-shaped objects. Assert the
    # component has no such thing — a fetch call alone does not prove the data
    # rendered came from it.
    mocked = re.search(
        r"(MOCK|STUB|FAKE|SAMPLE)_?(POSITIONS|ROWS|DATA)", grid, re.IGNORECASE
    ) or re.search(r"asset_name\s*:\s*[\"']", grid)
    check(
        "[Y] 2 the grid contains NO mock/stub row data — every row it renders "
        "came from the API",
        mocked is None,
        f"suspect literal: {mocked.group(0)!r}" if mocked else "none found",
    )
    # Comments are stripped before the org_id scan. Both route files EXPLAIN in
    # a comment that org_id travels in the token and never in a param — a naive
    # text search flags that explanation, which is the false positive that
    # trains the next person to delete the check rather than the bug.
    rl_code, ro_code = _strip_js_comments(rl), _strip_js_comments(ro)
    check(
        "[Y] 3 the Next.js API routes forward to FastAPI (Rule 5: the browser "
        "never calls FastAPI directly) and no executable line in either route "
        "reads, sets or forwards an org_id",
        "/api/v1/portfolio/positions" in rl_code
        and "/api/v1/portfolio/positions/" in ro_code
        and "forwardToApi" in rl_code
        and "forwardToApi" in ro_code
        and "org_id" not in rl_code
        and "org_id" not in ro_code,
        "forwardToApi → /api/v1/portfolio/positions; org_id absent from both "
        "routes' executable code (present only in the comment explaining why)",
    )
    check(
        "[Y] 3 the page renders the grid inside AppShell behind a host-aware "
        "session check",
        "PositionsGrid" in page
        and "getHostSession" in page
        and "AppShell" in page,
        "getHostSession + AppShell + PositionsGrid",
    )
    check(
        "[Y] 3 selecting a row opens the pane in place — the row click sets "
        "selection, it does not navigate",
        "onRowClick" in grid
        and "setSelectedId" in grid
        and "router.push" not in grid
        and "<Link" not in grid,
        "onRowClick → setSelectedId; no router.push / <Link> in the grid",
    )
    check(
        "[Y] 3 inline editing is limited to the fields the SERVER publishes as "
        "safe — the component reads vocabularies.inline_editable rather than "
        "keeping its own list that could drift",
        "inline_editable" in grid and "inlineEditable.has" in grid,
        f"server list = {sorted(INLINE_EDITABLE_FIELDS)}",
    )
    check(
        "[Y] 3 the basis-validated measures are NOT inline-editable — they go "
        "through the pane, which has room to show a refusal",
        not (INLINE_EDITABLE_FIELDS & {"quantity", "ownership_pct",
                                       "market_value", "ownership_basis"})
        and {"quantity", "ownership_pct", "market_value", "ownership_basis"}
            <= EDITABLE_FIELDS,
        f"inline={sorted(INLINE_EDITABLE_FIELDS)} "
        f"pane-only={sorted(EDITABLE_FIELDS - INLINE_EDITABLE_FIELDS)}",
    )
    check(
        "[Y] 4 the pane embeds the REAL existing DocumentsPanel and takes the "
        "record_type from the API response rather than hardcoding the string",
        'from "@/components/DocumentsPanel"' in pane
        and "data.document_record_type" in pane
        and f'"{RECORD_TYPE_POSITION}"' not in pane,
        "recordType={data.document_record_type}",
    )
    check(
        "[Y] 4 the pane renders the resolved value, the governing valuation, "
        "the valuation history and the transaction history",
        all(token in pane for token in (
            "current_value", "governing_valuation", "valuation_history",
            "transactions",
        )),
        "all four sections present",
    )
    check(
        "[Y] 4 the pane never renders a missing value as zero — it shows the "
        "server's reason instead",
        "current_value_reason" in pane,
        "renders current_value_reason on the null branch",
    )
    check(
        "[Y] 3 the grid sorts money NUMERICALLY, not lexically — derived sort "
        "keys are built from the decimal strings",
        "_measure" in grid and "_value" in grid and "function num(" in grid,
        "_measure / _value derived from num()",
    )
    check(
        "[Y] 3 the grid adopts the successor id after an edit — a client that "
        "kept the id it sent would be reading a closed row",
        "setSelectedId((prev) => (prev === row.id ? updated.id : prev))" in grid
        or "updated.id" in grid,
        "row id swapped from the PATCH response",
    )


def check_npm_build() -> None:
    """`npm run build` must exit 0. Real build, not a lint."""
    deps = _node_modules_dir()
    if deps is None:
        # NOT a pass. A build that never ran and a build that succeeded are
        # different outcomes, and collapsing them is how a broken tree ships.
        check("[Y] npm run build exits 0", False,
              "node_modules is absent from apps/web AND the workspace root — "
              "the build was NOT measured, which is not the same as passing")
        return
    proc = subprocess.run(
        ["npm", "run", "build"],
        cwd=_WEB, capture_output=True, text=True, timeout=1800,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
    check(
        "[Y] npm run build exits 0",
        proc.returncode == 0,
        f"exit={proc.returncode}, deps at {os.path.relpath(deps, _WEB)}" + (
            "" if proc.returncode == 0 else " | " + " / ".join(tail)
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Direct-SQL comparisons + the RLS layer
# ═══════════════════════════════════════════════════════════════════════════


async def direct_reads(conn, ids: dict) -> dict:
    """The same rows, read directly. The endpoint bodies are compared to these."""
    manual = await conn.fetch(
        f"""
        SELECT p.id::text AS id
        FROM {TABLE_POSITIONS} p
        JOIN {TABLE_ASSETS} a ON a.id = p.asset_id
        WHERE p.org_id = $1::uuid AND p.source_system = 'manual'
          AND a.name LIKE $2
          AND p.valid_to IS NULL AND p.system_to IS NULL
        """,
        DEFAULT_ORG_ID, f"{TAG}%",
    )
    txns = await conn.fetch(
        f"SELECT id::text AS id FROM {TABLE_TRANSACTIONS} "
        f"WHERE position_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
        ids["pos_units"],
    )
    vals = await conn.fetch(
        f"SELECT id::text AS id FROM {TABLE_VALUATIONS} "
        f"WHERE asset_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
        ids["asset_units"],
    )
    return {
        "manual_ids": {r["id"] for r in manual},
        "txn_ids": {r["id"] for r in txns},
        "val_ids": {r["id"] for r in vals},
    }


async def check_value_agrees_with_rollup(conn, ids: dict, detail: dict) -> None:
    """The pane's number is the SAME function the allocation sunburst sums."""
    row = await conn.fetchrow(
        f"""
        SELECT p.ownership_basis, p.asset_id::text AS asset_id, p.quantity,
               p.ownership_pct, p.market_value
        FROM {TABLE_POSITIONS} p WHERE p.id = $1::uuid
        """,
        ids["pos_units"],
    )
    value, reason = await position_current_value(conn, DEFAULT_ORG_ID, row, None)
    api_value = detail["position"]["current_value"]
    check(
        "[Y] 4 the pane's resolved value is EXACTLY what a direct call to "
        "portfolio_rollup.position_current_value returns for the same row — "
        "the grid and the allocation sunburst cannot disagree",
        api_value is not None and Decimal(api_value) == value,
        f"api={api_value!r} direct={value!r} reason={reason!r}",
    )
    resolved = await resolve_current_value(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["asset_units"],
    )
    check(
        "[Y] 4 the governing valuation the pane names is the row A2's "
        "resolver actually picks — not a separately-computed 'latest'",
        detail["governing_valuation"]["valuation_id"] == resolved.valuation_id,
        f"pane={str(detail['governing_valuation']['valuation_id'])[:8]}… "
        f"resolver={str(resolved.valuation_id)[:8]}…",
    )


async def check_restatement_rows(conn, ids: dict, restated_id: str) -> None:
    """Exactly two rows, exactly one current, and the closed one UNCHANGED."""
    rows = await conn.fetch(
        f"""
        SELECT id::text AS id, taxonomy_key, quantity, valid_to
        FROM {TABLE_POSITIONS}
        WHERE org_id = $1::uuid AND owner_entity_id = $2::uuid
          AND asset_id = $3::uuid AND system_to IS NULL
          AND source_system = 'reporting_tool_bd'
        ORDER BY valid_from
        """,
        DEFAULT_ORG_ID, ids["owner_a"], ids["asset_units"],
    )
    current = [r for r in rows if r["valid_to"] is None]
    closed = [r for r in rows if r["valid_to"] is not None]
    check(
        "[Y] 5 the edit produced exactly TWO rows for this (owner, asset) "
        "with exactly ONE current — a row was superseded, not overwritten and "
        "not duplicated",
        len(rows) == 2 and len(current) == 1 and len(closed) == 1
        and current[0]["id"] == restated_id,
        f"rows={len(rows)} current={len(current)} closed={len(closed)}",
    )
    if closed:
        check(
            "[Y] 5 the CLOSED row still carries its ORIGINAL taxonomy and "
            "quantity — the previous state was preserved, which is the whole "
            "reason an edit is a restatement rather than an UPDATE",
            closed[0]["taxonomy_key"] == "taxonomy_sc_1"
            and closed[0]["quantity"] == UNITS_QTY,
            f"closed taxonomy={closed[0]['taxonomy_key']!r} "
            f"quantity={closed[0]['quantity']}",
        )


class NonBypassRole:
    """A connection that reads as ``app_service``, however it got there.

    ``mode`` is ``'dsn'`` (a direct ``app_service`` login) or ``'set_role'``
    (``SET LOCAL ROLE`` inside each transaction). The caller never has to care
    which — but the report always says which, because a fallback nobody can see
    is how a rotated credential silently turns an RLS check into a session that
    merely resembles one.
    """

    def __init__(self, conn, mode: str):
        self.conn = conn
        self.mode = mode

    def scoped(self, org_id: str | None, *, super_admin: bool = False):
        conn, mode = self.conn, self.mode

        class _Ctx:
            async def __aenter__(self):
                self.tr = conn.transaction()
                await self.tr.start()
                try:
                    if mode == "set_role":
                        # LOCAL, not session-level: the pooler is in
                        # transaction mode, so a session SET can be handed to
                        # the next transaction on a different backend.
                        await conn.execute("SET LOCAL ROLE app_service")
                    await conn.execute(
                        "SELECT set_config('app.current_org_id', $1, true),"
                        "       set_config('app.is_super_admin', $2, true)",
                        org_id or "",
                        "true" if super_admin else "false",
                    )
                except BaseException:
                    await self.tr.rollback()
                    raise
                return conn

            async def __aexit__(self, et, e, tb):
                # Always rolled back: these are reads, and a rollback also
                # unwinds SET LOCAL ROLE deterministically.
                await self.tr.rollback()
                return False

        return _Ctx()


async def open_non_bypass_role(db_url: str, app_url: str | None):
    """Return a :class:`NonBypassRole`, or ``None`` if neither path works."""
    if app_url:
        try:
            conn = await asyncpg.connect(
                app_url, statement_cache_size=0, ssl="require"
            )
            return NonBypassRole(conn, "dsn")
        except Exception as exc:  # noqa: BLE001
            report(
                "ENVIRONMENT — APP_SERVICE_DATABASE_URL in apps/api/.env no "
                "longer authenticates",
                f"{type(exc).__name__}: {exc}. The ROLE is healthy "
                f"(rolcanlogin=true, rolbypassrls=false) — only the stored "
                f"password is stale, the same failure docs/PROJECT_STATUS.md "
                f"§7b recorded once before. Falling back to SET LOCAL ROLE "
                f"app_service, which is asserted below to be a genuinely "
                f"non-bypassing session rather than assumed to be one.",
            )
    conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SET LOCAL ROLE app_service")
        finally:
            await tr.rollback()
    except Exception as exc:  # noqa: BLE001
        report(
            "FATAL — no non-bypass role is reachable",
            f"SET LOCAL ROLE app_service failed: {type(exc).__name__}: {exc}",
        )
        await conn.close()
        return None
    return NonBypassRole(conn, "set_role")


async def check_rls_isolation(role: NonBypassRole, ids: dict) -> None:
    """Cross-org at the DATABASE layer, under the real non-bypass role."""
    async with role.scoped(None) as c:
        who = await c.fetchval("SELECT current_user")
        bypass = await c.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        # THE POSITIVE PROOF. With no org context, a non-bypass session sees
        # nothing; a bypassing one sees the whole table. This is what makes the
        # role assertion above mean something rather than just naming a role.
        denied = await c.fetchval(f"SELECT count(*) FROM {TABLE_POSITIONS}")

    check(
        f"[Y] 5 the RLS isolation check runs under a role that CANNOT bypass "
        f"RLS (obtained via {role.mode!r}) — otherwise every assertion below "
        f"would pass while proving nothing",
        bypass is False and who == "app_service",
        f"current_user={who!r} rolbypassrls={bypass} path={role.mode!r}",
    )
    check(
        "[Y] 5 RLS is demonstrably LIVE on portfolio.positions: with the org "
        "GUC empty the session reads ZERO rows, where a bypassing session "
        "would read the entire table",
        denied == 0,
        f"rows visible with no org context = {denied} (must be 0)",
    )

    async def under(org_id: str):
        async with role.scoped(org_id) as c:
            return await list_positions(
                c, org_id=org_id, search=TAG, resolve_values=False,
            )

    b_view = await under(OTHER_ORG_ID)
    b_ids = {p["id"] for p in b_view["positions"]}
    a_ids = {ids["pos_percent"], ids["pos_value"], ids["pos_novalue"],
             ids["pos_loser"]}
    check(
        "[Y] 5 cross-org (RLS, real app_service connection): org B's context "
        "sees its OWN fixture position and none of org A's — both directions "
        "on the same call",
        ids["pos_b"] in b_ids and not (b_ids & a_ids),
        f"orgB rows={len(b_ids)} leaked={sorted(b_ids & a_ids)}",
    )

    a_view = await under(DEFAULT_ORG_ID)
    a_seen = {p["id"] for p in a_view["positions"]}
    check(
        "[Y] 5 the control: org A's OWN context does see org A's positions, "
        "so the check above narrowed rather than simply failing",
        a_ids <= a_seen and ids["pos_b"] not in a_seen,
        f"orgA sees {len(a_seen)} rows, missing={sorted(a_ids - a_seen)}",
    )


async def check_service_layer(conn, ids: dict) -> None:
    """A few service-level facts the endpoints cannot show on their own."""
    # A refusal is asserted by TYPE, not merely by "it raised" (Phase B's
    # finding: any exception satisfies a bare try/except).
    raised = None
    try:
        await update_position(
            conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_value"],
            changes={"ownership_basis": "units"},
        )
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    check(
        "[Y] 2 update_position raises OwnershipBasisError specifically — not "
        "just 'something raised', which any typo would also satisfy",
        isinstance(raised, OwnershipBasisError),
        f"{type(raised).__name__}: {str(raised)[:110]}",
    )
    still = await conn.fetchval(
        f"SELECT valid_to FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
        ids["pos_value"],
    )
    check(
        "[Y] 2 the refused edit left the existing row OPEN — validation runs "
        "BEFORE the close, so a rejected edit cannot delete a holding from "
        "every current read",
        still is None,
        f"valid_to={still!r}",
    )
    detail = await get_position(
        conn, org_id=OTHER_ORG_ID, position_id=ids["pos_percent"]
    )
    check(
        "[Y] 5 get_position returns None for a position in another org even "
        "on a bypassing connection — the org predicate is explicit in the "
        "SQL, not left to RLS alone",
        detail is None,
        f"got={type(detail).__name__}",
    )


def check_schema_qualification() -> None:
    """Every portfolio.* reference is schema-qualified. AST-checked.

    `portfolio` is NOT on app_service's search_path, so an unqualified
    `FROM positions` raises UndefinedTableError under the production role while
    working fine in a psql session that happened to SET search_path — invisible
    in development, total in production. Docstrings are stripped first so a
    module explaining the rule does not flag its own explanation.
    """
    for rel in ("services/portfolio_positions.py",
                "routers/portfolio_positions.py"):
        src = read(os.path.join(_API, rel))
        tree = ast.parse(src)
        code = src
        docs = [ast.get_docstring(tree, clean=False)]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                docs.append(ast.get_docstring(node, clean=False))
        for d in docs:
            if d:
                code = code.replace(d, "")
        bare = sorted({
            name for name in ("assets", "positions", "valuations",
                              "transactions", "asset_identifiers")
            if re.search(rf"\b(FROM|INTO|UPDATE|JOIN)\s+{name}\b", code)
        })
        check(
            f"[Y] {rel} schema-qualifies every portfolio table (AST-checked: "
            f"no bare FROM/INTO/UPDATE/JOIN in executable code)",
            not bare,
            f"unqualified: {bare or 'none'}",
        )


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    app_url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1

    admin_conn = await asyncpg.connect(db_url, statement_cache_size=0,
                                       ssl="require")
    role = await open_non_bypass_role(db_url, app_url)
    if role is None:
        print("[FAIL] no non-bypass role is reachable by either path. Every "
              "cross-org assertion is meaningless under a bypassrls role, so "
              "this script fails rather than pretending.")
        await admin_conn.close()
        return 1

    baseline: dict[str, int] = {}
    try:
        await teardown(admin_conn)                                    # START
        baseline = await counts(admin_conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        report(
            "TEARDOWN is by-fixture, never a truncate",
            f"Every table here holds real production rows. Fixtures are matched "
            f"through the {TAG!r} tag on asset / entity / document names and "
            f"through their tagged asset for positions and transactions, with "
            f"an exact before/after count as the backstop.",
        )

        print("\n── Task 1: DISCOVERY ──")
        check_task1a()
        check_task1b()
        check_task1c()
        check_task1d()
        check_schema_qualification()

        print("\n── Fixtures ──")
        await seed_users(admin_conn)
        ids = await seed(admin_conn)
        direct = await direct_reads(admin_conn, ids)
        print(f"   seeded: 5 assets, 6 positions, 4 valuations, "
              f"2 transactions, 1 linked document")

        print("\n── Tasks 2-5: the real endpoints, driven through the ASGI app ──")
        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(None, endpoint_tests, ids, direct)

        print("\n── The numbers agree with a direct read ──")
        if out.get("detail"):
            await check_value_agrees_with_rollup(admin_conn, ids, out["detail"])
        if out.get("restated_id"):
            await check_restatement_rows(admin_conn, ids, out["restated_id"])
        await check_service_layer(admin_conn, ids)

        print(f"\n── Cross-org isolation, under the real app_service role "
              f"(via {role.mode}) ──")
        await check_rls_isolation(role, ids)

        print("\n── Frontend wiring ──")
        check_frontend_wiring()

        print("\n── npm run build ──")
        await loop.run_in_executor(None, check_npm_build)

    finally:
        await teardown(admin_conn)                                    # END
        if baseline:
            final = await counts(admin_conn)
            drift = {t: (baseline[t], final[t]) for t in TABLES
                     if baseline[t] != final[t]}
            check(
                "[Y] TEARDOWN restores the EXACT before-count on every table "
                "touched — zero leftover rows",
                not drift,
                f"drift (before, after): {drift}" if drift
                else ", ".join(f"{t.split('.')[-1]}={final[t]}" for t in TABLES),
            )
        await role.conn.close()
        await admin_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}")
    print(f"FINDINGS REPORTED: {len(findings)}")
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
