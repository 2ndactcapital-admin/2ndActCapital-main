"""Verification — Portfolio Phase B, ingestion + source precedence.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END.
Real database, real files, real RLS, real ``app_service`` connection.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback, for the
same reason A1 and A2 require it: the cross-org isolation checks are meaningless
under a ``rolbypassrls`` role. Running them as ``postgres`` would "pass" every
one of them while proving nothing, so a missing or non-connecting app_service
credential FAILS this script rather than degrading it.

────────────────────────────────────────────────────────────────────────────
TEARDOWN: BEFORE/AFTER COUNTS, NOT TRUNCATE
────────────────────────────────────────────────────────────────────────────
Inherited unchanged from A1 and A2, and Phase B is the first sprint for which
it is not merely hygiene. A1 found its four tables holding the live EDGAR
corpus. A2's six measured empty. Phase B is the sprint that WRITES real
positions, so by the next run these tables may hold production rows from a
different track — every table is counted before the run and after teardown and
the counts must match exactly. A leaked fixture row fails as hard as a deleted
production row.

``public.org_settings`` is counted too, and is the reason this file is careful
rather than merely tidy: the precedence order is a real settings row on a real
org, so this script WRITES to live tenant configuration. It captures whatever
the org had before it started and restores it byte-for-byte at the end. The one
case it will not restore is a stored value identical to this script's own
fixture order — that is a previous crashed run's leftover, not the org's
setting, and restoring it would make every future run inherit the wreckage.

────────────────────────────────────────────────────────────────────────────
[BLOCKED] ASSERTIONS
────────────────────────────────────────────────────────────────────────────
Task 3 (Altruist) is gated on partner credentials that do not exist. Its
assertions report [BLOCKED] with the exact reason, exactly as the Textract,
Voyage and SES gates did. A BLOCKED assertion is NOT a pass and is NOT counted
as one — it is reported separately so that "all green" can never quietly mean
"all green except the thing that was never measured".

Run:
    python3 scripts/verify_portfoliob.py
"""

from __future__ import annotations

import asyncio
import csv
import glob
import inspect
import io
import json
import os
import sys
from datetime import date
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.extend(sorted(glob.glob(
    os.path.join(_HERE, "..", "venv", "lib", "python3*", "site-packages")
)))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_HERE, "..", ".env"), override=False)

from services.org_settings import (  # noqa: E402
    DEFAULT_SETTINGS,
    SettingsValidationError,
    _validate_setting,
    category_for,
    get_setting_with_origin,
)
from services.portfolio_altruist import (  # noqa: E402
    ALTRUIST_ENV_VARS,
    AltruistBlocked,
    credential_state,
    ingest_positions,
    probe,
)
from services.portfolio_assets import (  # noqa: E402
    SOURCE_SYSTEMS,
    TABLE_ASSET_IDENT,
    TABLE_ASSETS,
    TABLE_EXT_REF,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    TABLE_VALUATIONS,
    create_asset,
    create_position,
    find_external_reference,
    upsert_external_reference,
)
from services.portfolio_import import (  # noqa: E402
    IMPORT_SOURCE_SYSTEM,
    ImportError_,
    import_positions_file,
    map_headers,
    parse_tabular,
)
from services.portfolio_precedence import (  # noqa: E402
    DEFAULT_SOURCE_ORDER,
    PRECEDENCE_SETTING_KEY,
    PrecedenceConfigError,
    PrecedenceError,
    get_source_order,
    resolve_holding,
    resolve_precedence,
    validate_source_order,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org, for the cross-org isolation checks. A real row, not a
# minted one — `organizations` has FKs pointing at it from a dozen places.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

ADMIN_USER_ID = "99000000-0000-0000-0000-000000000051"
ADMIN_SUB = "auth0|verify_portfoliob_super_admin"
MEMBER_USER_ID = "99000000-0000-0000-0000-000000000052"
MEMBER_SUB = "auth0|verify_portfoliob_member"

FIXTURE_TAG = "VERIFY-PORTFOLIOB"

# ── Entity fixtures ─────────────────────────────────────────────────────────
FIX_ACCOUNT = f"{FIXTURE_TAG} Custodial Account 4402"
FIX_OTHERORG_ACCOUNT = f"{FIXTURE_TAG} Other-Org Account"
ENTITY_NAMES = [FIX_ACCOUNT, FIX_OTHERORG_ACCOUNT]

# ── Asset fixtures ──────────────────────────────────────────────────────────
# Every name declared UP FRONT and never appended to at runtime: a name minted
# mid-run is one the NEXT run's start-teardown cannot find, so a crash between
# minting it and the end-teardown strands it permanently and the count
# assertion then fails forever against a silently-absorbed baseline.
FIX_ASSET_A = f"{FIXTURE_TAG} Ridgeline Global Equity Fund"
FIX_ASSET_B = f"{FIXTURE_TAG} Harborview Municipal Bond Fund"
FIX_ASSET_C = f"{FIXTURE_TAG} Northgate Private Credit LP"
FIX_ASSET_XLSX = f"{FIXTURE_TAG} Sequoia Ridge Balanced Fund"
FIX_ASSET_OTHERORG = f"{FIXTURE_TAG} Other-Org Holding"
ASSET_NAMES = [
    FIX_ASSET_A, FIX_ASSET_B, FIX_ASSET_C, FIX_ASSET_XLSX, FIX_ASSET_OTHERORG,
]

# The external_ids this script can create. Row hashes are content-derived, so
# they cannot be listed literally — teardown deletes ext-ref rows by their
# record_id pointing at a fixture position instead, which covers both the
# explicit ids below and every hash.
FIX_EXT_EXPLICIT = f"{FIXTURE_TAG}-ROW-1"
FIX_OTHERORG_EXT_ID = f"{FIXTURE_TAG}-OTHERORG-EXTREF"

AS_OF = date(2026, 6, 30)

# The fixture precedence order. Deliberately inverts the default's top and
# bottom so the configured case cannot accidentally agree with the default —
# a "configured order wins" test whose configured order happens to produce the
# same winner as the default proves nothing at all.
FIX_CUSTOM_ORDER = [
    "manual",
    "chancery",
    "spv_subscriptions",
    "altruist",
    "reporting_tool_import",
    "reporting_tool_apx",
    "reporting_tool_orion",
    "reporting_tool_addepar",
    "reporting_tool_bd",
]

TABLES = (
    TABLE_ASSETS, TABLE_ASSET_IDENT, TABLE_POSITIONS,
    TABLE_VALUATIONS, TABLE_TRANSACTIONS, TABLE_EXT_REF,
    "public.org_settings",
)

results: list[tuple[str, bool, str]] = []
blocked: list[tuple[str, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def report(name: str, detail: str) -> None:
    """A Task 1 finding. Printed as a FINDING, never silently as a PASS."""
    print(f"[FIND] {name} — {detail}")


def block(name: str, reason: str) -> None:
    """An assertion that could not be measured. NOT a pass."""
    blocked.append((name, reason))
    print(f"[BLOCKED] {name} — {reason}")


# ── Fixture files ───────────────────────────────────────────────────────────


def build_csv(*, include_malformed: bool) -> bytes:
    """A reporting-tool style holdings export.

    Column headers are deliberately NOT our internal field names — they are the
    kind of headers Black Diamond / Addepar / Orion actually emit, because a
    header mapper tested only against its own vocabulary tests nothing.

    ``include_malformed`` adds one row whose quantity is prose. Everything
    around it is valid, so "the rest of the file still imports" is a real
    assertion and not a statement about an empty file.
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([
        "Position ID", "Security Name", "Ticker", "CUSIP", "Units",
        "Ending Market Value", "Total Cost", "As Of Date", "Currency",
        "Security Type",
    ])
    w.writerow([
        FIX_EXT_EXPLICIT, FIX_ASSET_A, "RGEFX", "74933W401", "1,250.75",
        "$48,912.40", "41,000.00", "2026-06-30", "USD", "mutual_fund",
    ])
    # No explicit id — exercises the SHA-256 row hash.
    w.writerow([
        "", FIX_ASSET_B, "HVMBX", "41013V206", "3,004.125",
        "$31,441.09", "30,900.00", "06/30/2026", "USD", "mutual_fund",
    ])
    if include_malformed:
        w.writerow([
            "", FIX_ASSET_C, "", "", "not a number",
            "$100,000.00", "", "2026-06-30", "USD", "private_credit",
        ])
    # Accounting-negative cost, and a value-only holding (no units at all).
    w.writerow([
        "", FIX_ASSET_C, "", "", "",
        "$212,500.00", "(1,250.00)", "2026-06-30", "USD", "private_credit",
    ])
    return buf.getvalue().encode("utf-8")


def build_xlsx() -> bytes:
    """The same shape as an XLSX, to prove the openpyxl path is really used.

    Numbers are written as real numeric cells and the date as a real date cell,
    which is what makes this a different test rather than a CSV in a zip: the
    XLSX path gets Python ``float`` and ``datetime`` out of openpyxl where the
    CSV path gets strings.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Holdings"
    ws.append(["Security Name", "Ticker", "Quantity", "Market Value", "As Of"])
    ws.append([FIX_ASSET_XLSX, "SQRBX", 500.5, 27310.25, date(2026, 6, 30)])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ── Setup / teardown ────────────────────────────────────────────────────────


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def read_stored_precedence(conn) -> str | None:
    """The RAW jsonb text of the org's precedence setting, or None if unset."""
    return await conn.fetchval(
        "SELECT setting_value::text FROM org_settings "
        "WHERE org_id = $1::uuid AND setting_key = $2",
        DEFAULT_ORG_ID, PRECEDENCE_SETTING_KEY,
    )


async def teardown(conn, *, restore_precedence: str | None = None) -> None:
    """Delete every fixture row, child tables first. Touches nothing else."""
    asset_ids = f"SELECT id FROM {TABLE_ASSETS} WHERE name = ANY($1::text[])"
    fixture_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({asset_ids})"
    )

    # External references first: they carry no FK to positions (record_id is a
    # bare uuid), so nothing forces this order — but deleting positions first
    # would leave the ext-ref rows unfindable, since record_id is the ONLY link
    # back to a fixture. Row hashes cannot be listed literally, so this is the
    # one handle there is.
    await conn.execute(
        f"DELETE FROM {TABLE_EXT_REF} WHERE record_id IN ({fixture_positions})",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_EXT_REF} WHERE external_id = ANY($1::text[])",
        [FIX_EXT_EXPLICIT, FIX_OTHERORG_EXT_ID],
    )
    await conn.execute(
        f"DELETE FROM {TABLE_TRANSACTIONS} WHERE position_id IN ({fixture_positions})",
        ASSET_NAMES,
    )
    await conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN ({asset_ids})",
        ASSET_NAMES,
    )
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
        f"DELETE FROM {TABLE_ASSETS} WHERE name = ANY($1::text[])", ASSET_NAMES
    )
    await conn.execute(
        "DELETE FROM entities WHERE display_name = ANY($1::text[])", ENTITY_NAMES
    )
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[])", [ADMIN_SUB, MEMBER_SUB]
    )

    # The org's precedence setting. Restore what was there, or remove ours.
    await conn.execute(
        "DELETE FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
        DEFAULT_ORG_ID, PRECEDENCE_SETTING_KEY,
    )
    if restore_precedence is not None:
        await conn.execute(
            "INSERT INTO org_settings (org_id, setting_key, setting_value, category) "
            "VALUES ($1::uuid, $2, $3::jsonb, $4)",
            DEFAULT_ORG_ID, PRECEDENCE_SETTING_KEY, restore_precedence,
            category_for(PRECEDENCE_SETTING_KEY),
        )


async def seed_users(conn) -> None:
    for user_id, sub, role, email in (
        (ADMIN_USER_ID, ADMIN_SUB, "super_admin", "verify_b_admin@test.local"),
        (MEMBER_USER_ID, MEMBER_SUB, "member", "verify_b_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioB', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, DEFAULT_ORG_ID, email, sub, role,
        )


async def seed_entity(conn, org_id: str, display_name: str) -> str:
    """One account entity, created under the org's own RLS context."""
    return await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1::uuid, 'account', $2)
        RETURNING id::text
        """,
        org_id, display_name,
    )


def org_ctx(conn, org_id: str, *, super_admin: bool = False, commit: bool = True):
    """Transaction on ``conn`` with the org GUC SET LOCAL.

    ``super_admin=False`` is the important default: these are TENANT tables and
    the isolation checks are only meaningful without the escape hatch.
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


# ── Task 1: the four findings, asserted ─────────────────────────────────────


async def check_task1(conn) -> None:
    """The four Task 1 discovery findings, each asserted against reality."""

    # 1a — the real A2 signatures this sprint calls rather than duplicates.
    expected = {
        "create_asset": ("org_id", "name", "asset_type"),
        "create_position": (
            "org_id", "owner_entity_id", "asset_id", "as_of_date", "authority",
            "source_system",
        ),
        "record_transaction": (
            "org_id", "position_id", "transaction_type_code", "trade_date",
            "authority", "source_system",
        ),
        "record_valuation": ("org_id", "asset_id", "valuation_date", "value"),
    }
    import services.portfolio_assets as pa

    missing: list[str] = []
    positional: list[str] = []
    for fn_name, required in expected.items():
        fn = getattr(pa, fn_name, None)
        if fn is None:
            missing.append(fn_name)
            continue
        sig = inspect.signature(fn)
        for param in required:
            p = sig.parameters.get(param)
            if p is None:
                missing.append(f"{fn_name}.{param}")
            elif p.kind is not inspect.Parameter.KEYWORD_ONLY:
                positional.append(f"{fn_name}.{param}")
    check(
        "1a A2's four write functions exist with the exact keyword-only "
        "signature Phase B calls (create_asset / create_position / "
        "record_transaction / record_valuation)",
        not missing and not positional,
        f"missing: {missing or 'none'}; not keyword-only: {positional or 'none'}",
    )
    report(
        "1a A2 signatures",
        "all four take `conn` positionally and everything else keyword-only, "
        "with org_id required and never defaulted. Phase B CALLS them; it does "
        "not reimplement any of the four.",
    )

    # The A2 defect this sprint had to fix. The old three-column ON CONFLICT
    # matches no constraint since the Part-1 widening, so every ext-ref write
    # would have raised — asserted against the LIVE constraint, not the source.
    ext_unique = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'portfolio' AND t.relname = 'external_references'
          AND c.contype = 'u'
        """
    )
    check(
        "1a external_references UNIQUE is org-scoped (the Part-1 fix), and "
        "upsert_external_reference's ON CONFLICT was re-pointed at it — the "
        "stale 3-column target matched NO constraint and raised on every call",
        ext_unique is not None
        and "org_id" in ext_unique
        and "source_system" in ext_unique
        and "external_id" in ext_unique
        and "record_type" in ext_unique,
        f"live constraint: {ext_unique}",
    )

    fx_unique = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND t.relname = 'fx_rates' AND c.contype = 'u'
        """
    )
    check(
        "1a fx_rates UNIQUE includes rate_type (the other Part-1 fix) — a "
        "spot/period_end pair for one date is now possible",
        fx_unique is not None and "rate_type" in fx_unique,
        f"live constraint: {fx_unique}",
    )

    # The migration Phase B discovered on its own.
    src_chk = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'portfolio' AND t.relname = 'positions'
          AND c.conname = 'positions_source_chk'
        """
    )
    check(
        "1a positions_source_chk admits 'reporting_tool_import' "
        "(docs/portfoliob_part1.sql) — Task 4's mandated source_system was NOT "
        "in the deployed CHECK and every import would have raised 23514",
        bool(src_chk) and "reporting_tool_import" in src_chk,
        f"live constraint: {src_chk}",
    )
    check(
        "1a the Python SOURCE_SYSTEMS vocabulary matches the widened CHECK",
        "reporting_tool_import" in SOURCE_SYSTEMS,
        f"{len(SOURCE_SYSTEMS)} values",
    )

    # 1b — the parsing convention is Chancery's, reused, not duplicated.
    import services.portfolio_import as pi

    reused = (
        pi.detect_file_type.__module__ == "services.chancery_intake"
        and pi.extract_xlsx.__module__ == "services.chancery_intake"
        and pi.extract_text.__module__ == "services.chancery_intake"
    )
    check(
        "1b the importer REUSES chancery_intake's parsers (detect_file_type / "
        "extract_xlsx / extract_text) rather than adding a second stack",
        reused,
        f"detect_file_type from {pi.detect_file_type.__module__}, "
        f"extract_xlsx from {pi.extract_xlsx.__module__}, "
        f"extract_text from {pi.extract_text.__module__}",
    )
    report(
        "1b parsing convention",
        "chancery_intake.detect_file_type classifies by MAGIC BYTES (extension "
        "is only a weak tie-breaker); extract_xlsx is the existing openpyxl "
        "path. Chancery has NO CSV-specific path, so CSV goes through "
        "extract_text plus the stdlib csv reader — the text path with a "
        "delimiter, not a new parsing approach.",
    )

    # 1c — the settings convention, asserted against the live table.
    check(
        "1c the precedence key follows the real org_settings convention: "
        "dotted namespace, present in DEFAULT_SETTINGS, category from prefix",
        PRECEDENCE_SETTING_KEY in DEFAULT_SETTINGS
        and PRECEDENCE_SETTING_KEY.count(".") >= 2
        and category_for(PRECEDENCE_SETTING_KEY) == "portfolio",
        f"key={PRECEDENCE_SETTING_KEY}, "
        f"category={category_for(PRECEDENCE_SETTING_KEY)}",
    )
    live_keys = [
        r["setting_key"] for r in await conn.fetch(
            "SELECT DISTINCT setting_key FROM org_settings ORDER BY setting_key"
        )
    ]
    report(
        "1c org_settings convention",
        f"{len(live_keys)} distinct keys live, all dotted "
        f"(e.g. {', '.join(live_keys[:3])}); setting_value is jsonb NOT NULL so "
        f"an ordered list is a native fit — ai.model.fallback_chain is the "
        f"existing array-valued precedent.",
    )

    # 1d — Altruist is greenfield, asserted rather than asserted-about.
    present, missing_vars = credential_state()
    check(
        "1d Altruist is greenfield: a gate module exists, and it is honest "
        "about having no credentials and no observed response shape",
        not present and len(missing_vars) == len(ALTRUIST_ENV_VARS),
        f"required: {list(ALTRUIST_ENV_VARS)}; missing: {list(missing_vars)}",
    )
    report(
        "1d Altruist pre-existing code",
        "NONE. The only references in the repo are: a comment in "
        "schemas/entities.py naming Altruist as an example custodian; the "
        "string 'altruist' as a positions_source_chk vocabulary slot; a "
        "fixture constant in verify_portfolioa2.py; services/trading_authority "
        "explicitly recording that the assumed custodian subsystem does not "
        "exist; and design-doc prose. No client, no stub, no env var.",
    )


# ── Task 3: the Altruist gate ───────────────────────────────────────────────


async def check_task3() -> bool:
    """Run the REAL gate. Returns True only if genuinely unblocked."""
    present, missing_vars = credential_state()
    gate = await probe()

    report(
        "3 Altruist gate — credentials",
        f"present={present}; missing={list(missing_vars) or 'none'}",
    )
    report(
        "3 Altruist gate — real call",
        f"attempted={gate.attempted}; ok={gate.ok}; "
        f"status_code={gate.status_code}; reason={gate.reason}",
    )

    # This assertion is measurable either way: the gate must REPORT its state
    # truthfully. It is not the same as "Altruist works".
    check(
        "3 the Altruist gate reports its real state — credentials checked, a "
        "call attempted if and only if they were present, exact reason carried",
        gate.attempted == present and bool(gate.reason),
        f"attempted={gate.attempted}, credentials_present={present}",
    )

    if gate.ok:
        return True

    block(
        "3b Altruist ingestion creates real positions + external_references",
        gate.reason,
    )
    block(
        "3b re-running Altruist ingestion against the same external_id is "
        "idempotent",
        gate.reason,
    )

    # What IS provable while blocked: that the blocked path refuses loudly
    # rather than returning empty or fabricating rows.
    try:
        await ingest_positions(None, DEFAULT_ORG_ID)
        raised = False
        detail = "ingest_positions RETURNED instead of raising"
    except AltruistBlocked as exc:
        raised, detail = True, str(exc)[:160]
    except Exception as exc:  # noqa: BLE001
        raised, detail = False, f"raised {type(exc).__name__}, not AltruistBlocked"
    check(
        "3 the blocked Altruist path raises AltruistBlocked with the reason — "
        "it does not return empty, and it does not fabricate positions",
        raised,
        detail,
    )
    return False


# ── Task 2 + 4 + 5 ──────────────────────────────────────────────────────────


async def check_precedence_config(conn) -> None:
    """Task 2 — the ordering is DATA, and bad data is refused at write time."""
    order = await get_source_order(conn, DEFAULT_ORG_ID)
    check(
        "2 an org with NO configured precedence falls back to the design's "
        "stated DEFAULT order — tested explicitly, not inferred from a "
        "configured case",
        order.is_default and order.order == DEFAULT_SOURCE_ORDER,
        f"is_default={order.is_default}, order={list(order.order)}",
    )
    check(
        "2 the default order is design V6 §1.1's: reporting_tool_* > altruist "
        "> spv_subscriptions > chancery > manual",
        (order.order.index("reporting_tool_import")
         < order.order.index("altruist")
         < order.order.index("spv_subscriptions")
         < order.order.index("chancery")
         < order.order.index("manual")),
        " > ".join(order.order),
    )
    check(
        "2 every entry of the default order is a source_system the deployed "
        "CHECK admits — an order naming a value no row can carry ranks nothing",
        set(DEFAULT_SOURCE_ORDER) <= SOURCE_SYSTEMS,
        f"unknown: {sorted(set(DEFAULT_SOURCE_ORDER) - SOURCE_SYSTEMS) or 'none'}",
    )

    # Write-time rejection, through org_settings' own validator — the same code
    # path a router hits, not a private copy.
    bad_cases = [
        ("a bare string", "manual"),
        ("an empty array", []),
        ("an unknown source_system", ["manual", "schwab_direct"]),
        ("a duplicated source", ["manual", "manual"]),
    ]
    rejected = []
    for label, value in bad_cases:
        try:
            _validate_setting(PRECEDENCE_SETTING_KEY, value)
            rejected.append(f"{label}: ACCEPTED")
        except SettingsValidationError:
            pass
        except Exception as exc:  # noqa: BLE001
            rejected.append(f"{label}: {type(exc).__name__}")
    check(
        "2 org_settings REJECTS a malformed precedence order at write time "
        "(string / empty / unknown source / duplicate) — caught at save, not "
        "silently mis-ranking every later ingestion run",
        not rejected,
        f"not rejected: {rejected or 'none — all four refused'}",
    )
    check(
        "2 a valid custom order is ACCEPTED by the same validator",
        validate_source_order(FIX_CUSTOM_ORDER) == tuple(FIX_CUSTOM_ORDER),
        f"{len(FIX_CUSTOM_ORDER)} entries",
    )

    # A partial order is legal: unnamed sources rank after everything named.
    partial = validate_source_order(["manual"])
    check(
        "2 a PARTIAL order is legal, and an unnamed source ranks after every "
        "named one — 'not configured' does not mean 'promoted'",
        partial == ("manual",),
        "['manual'] accepted; unnamed sources fall to the tail",
    )


async def check_import(conn, owner_id: str) -> dict:
    """Task 4 — the file import, its idempotency, and its bad-row handling."""
    out: dict = {}

    # Header mapping against real reporting-tool headers.
    mapping = map_headers([
        "Position ID", "Security Name", "Ticker", "CUSIP", "Units",
        "Ending Market Value", "Total Cost", "As Of Date", "Currency",
        "Security Type",
    ])
    check(
        "4 the header mapper binds real reporting-tool headers, longest-alias "
        "first — 'Ending Market Value' is not swallowed by the 'value' alias",
        mapping.get("market_value") == 5 and mapping.get("quantity") == 4
        and mapping.get("external_id") == 0 and mapping.get("cost_basis") == 6,
        f"mapping={mapping}",
    )

    csv_bytes = build_csv(include_malformed=True)
    check(
        "4 parse_tabular reads the CSV through chancery_intake's text path",
        len(parse_tabular(csv_bytes, "holdings.csv")) == 5,
        f"{len(parse_tabular(csv_bytes, 'holdings.csv'))} rows incl. header",
    )

    first = await import_positions_file(
        conn, org_id=DEFAULT_ORG_ID, file_bytes=csv_bytes,
        filename="holdings.csv", owner_entity_id=owner_id,
    )
    out["first"] = first
    check(
        "4 a real CSV import creates real assets and positions",
        first.imported == 3 and first.assets_created == 3,
        f"imported={first.imported}, assets_created={first.assets_created}, "
        f"assets_matched={first.assets_matched}, errors={len(first.errors)}",
    )

    # The malformed row: skipped, reported, and the REST of the file imported.
    malformed_ok = (
        len(first.errors) == 1
        and first.errors[0].line == 4
        and "not a number" in first.errors[0].reason
    )
    check(
        "4 a malformed row is SKIPPED and REPORTED with its file line number, "
        "and the rest of the file still imports successfully",
        malformed_ok and first.imported == 3,
        f"errors={[(e.line, e.reason[:60]) for e in first.errors]}, "
        f"imported={first.imported}",
    )

    # The rows really landed, with the right source_system.
    rows = await conn.fetch(
        f"""
        SELECT p.id::text AS id, p.source_system, p.ownership_basis,
               p.quantity, p.market_value, p.cost_basis, a.name
        FROM {TABLE_POSITIONS} p JOIN {TABLE_ASSETS} a ON a.id = p.asset_id
        WHERE a.name = ANY($1::text[]) AND p.org_id = $2::uuid
        ORDER BY a.name
        """,
        [FIX_ASSET_A, FIX_ASSET_B, FIX_ASSET_C], DEFAULT_ORG_ID,
    )
    check(
        "4 every imported position carries source_system="
        f"'{IMPORT_SOURCE_SYSTEM}'",
        len(rows) == 3 and all(r["source_system"] == IMPORT_SOURCE_SYSTEM for r in rows),
        f"{len(rows)} positions: "
        + ", ".join(sorted({r["source_system"] for r in rows})),
    )

    by_name = {r["name"]: r for r in rows}
    equity = by_name.get(FIX_ASSET_A)
    check(
        "4 monetary and quantity cells parse to exact Decimals — currency "
        "symbols, thousands separators and accounting-negative parentheses",
        equity is not None
        and equity["quantity"] == Decimal("1250.75")
        and equity["market_value"] == Decimal("48912.40")
        and by_name[FIX_ASSET_C]["cost_basis"] == Decimal("-1250.00"),
        f"units={equity['quantity'] if equity else None}, "
        f"mv={equity['market_value'] if equity else None}, "
        f"negative cost={by_name.get(FIX_ASSET_C, {}).get('cost_basis')}",
    )
    check(
        "4 a value-only row (no units at all) lands on ownership_basis='value' "
        "with quantity NULL — the basis follows what the file measures",
        by_name.get(FIX_ASSET_C) is not None
        and by_name[FIX_ASSET_C]["ownership_basis"] == "value"
        and by_name[FIX_ASSET_C]["quantity"] is None,
        f"basis={by_name.get(FIX_ASSET_C, {}).get('ownership_basis')}, "
        f"quantity={by_name.get(FIX_ASSET_C, {}).get('quantity')}",
    )

    # Real external_references, one per position, both explicit-id and hashed.
    ext = await conn.fetch(
        f"""
        SELECT e.external_id, e.record_type, e.source_system
        FROM {TABLE_EXT_REF} e
        WHERE e.record_id IN (
            SELECT p.id FROM {TABLE_POSITIONS} p
            JOIN {TABLE_ASSETS} a ON a.id = p.asset_id
            WHERE a.name = ANY($1::text[])
        ) AND e.org_id = $2::uuid
        """,
        [FIX_ASSET_A, FIX_ASSET_B, FIX_ASSET_C], DEFAULT_ORG_ID,
    )
    ext_ids = {r["external_id"] for r in ext}
    check(
        "4 a real external_references row exists per imported position — the "
        "file's own row id where it has one, a SHA-256 row hash where it does "
        "not",
        len(ext) == 3
        and FIX_EXT_EXPLICIT in {i.removeprefix("row:") for i in ext_ids}
        and sum(1 for i in ext_ids if i.startswith("sha256:")) == 2
        and all(r["record_type"] == "position" for r in ext),
        f"{len(ext)} refs: "
        + ", ".join(sorted(i[:24] + "…" if len(i) > 24 else i for i in ext_ids)),
    )

    # ── THE IDEMPOTENCY PROOF ───────────────────────────────────────────────
    before = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_POSITIONS} p "
        f"JOIN {TABLE_ASSETS} a ON a.id = p.asset_id "
        f"WHERE a.name = ANY($1::text[])",
        ASSET_NAMES,
    )
    second = await import_positions_file(
        conn, org_id=DEFAULT_ORG_ID, file_bytes=csv_bytes,
        filename="holdings.csv", owner_entity_id=owner_id,
    )
    after = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_POSITIONS} p "
        f"JOIN {TABLE_ASSETS} a ON a.id = p.asset_id "
        f"WHERE a.name = ANY($1::text[])",
        ASSET_NAMES,
    )
    check(
        "4 re-uploading the IDENTICAL file creates ZERO new positions — the "
        "row count is unchanged and every row is reported as a skipped "
        "duplicate, not merely 'it did not error'",
        before == after and second.imported == 0 and second.skipped_duplicate == 3
        and second.assets_created == 0,
        f"positions before={before}, after={after}; "
        f"imported={second.imported}, skipped_duplicate={second.skipped_duplicate}, "
        f"assets_created={second.assets_created}",
    )

    # A file renamed but otherwise identical is still the same holdings.
    third = await import_positions_file(
        conn, org_id=DEFAULT_ORG_ID, file_bytes=csv_bytes,
        filename="holdings-FINAL-v2.csv", owner_entity_id=owner_id,
    )
    check(
        "4 the row hash covers the row's MEANING, not the filename — the same "
        "holdings re-sent under a different filename is still one position",
        third.imported == 0 and third.skipped_duplicate == 3,
        f"imported={third.imported}, skipped={third.skipped_duplicate}",
    )

    # The XLSX path — real numeric and date cells out of openpyxl.
    xlsx = await import_positions_file(
        conn, org_id=DEFAULT_ORG_ID, file_bytes=build_xlsx(),
        filename="holdings.xlsx", owner_entity_id=owner_id,
    )
    xlsx_row = await conn.fetchrow(
        f"SELECT p.quantity, p.market_value, p.as_of_date FROM {TABLE_POSITIONS} p "
        f"JOIN {TABLE_ASSETS} a ON a.id = p.asset_id "
        f"WHERE a.name = $1 AND p.org_id = $2::uuid",
        FIX_ASSET_XLSX, DEFAULT_ORG_ID,
    )
    check(
        "4 the XLSX path imports through openpyxl, and a float market value "
        "arrives as an exact Decimal (Decimal(str(f)), never Decimal(float))",
        xlsx.imported == 1 and xlsx_row is not None
        and xlsx_row["market_value"] == Decimal("27310.25")
        and xlsx_row["quantity"] == Decimal("500.5")
        and xlsx_row["as_of_date"] == AS_OF,
        f"imported={xlsx.imported}, mv={xlsx_row['market_value'] if xlsx_row else None}, "
        f"qty={xlsx_row['quantity'] if xlsx_row else None}, "
        f"as_of={xlsx_row['as_of_date'] if xlsx_row else None}",
    )

    # A file that is unusable AS A FILE is a different failure from a bad row.
    for label, payload, name in (
        ("a PDF", b"%PDF-1.4\n%fake", "holdings.pdf"),
        ("a header with no data rows", b"Security Name,Units\n", "empty.csv"),
        ("a table naming no security", b"Foo,Bar\n1,2\n", "nosecurity.csv"),
    ):
        try:
            await import_positions_file(
                conn, org_id=DEFAULT_ORG_ID, file_bytes=payload,
                filename=name, owner_entity_id=owner_id,
            )
            ok, detail = False, "imported without raising"
        except ImportError_ as exc:
            ok, detail = True, str(exc)[:70]
        check(
            f"4 {label} is refused as an unusable FILE (distinct from a bad row)",
            ok, detail,
        )

    out["asset_a_id"] = await conn.fetchval(
        f"SELECT id::text FROM {TABLE_ASSETS} WHERE name = $1 AND org_id = $2::uuid",
        FIX_ASSET_A, DEFAULT_ORG_ID,
    )
    return out


async def check_precedence_resolution(conn, owner_id: str, asset_id: str) -> None:
    """Task 5 — two REAL sources for one holding key, resolved both ways."""

    # The second source: a manual entry for the SAME owner + asset + as-of.
    manual_id = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=owner_id, asset_id=asset_id,
        as_of_date=AS_OF, authority="manual", source_system="manual",
        ownership_basis="units", quantity=Decimal("1200"),
        market_value=Decimal("46000.00"),
    )
    import_id = await conn.fetchval(
        f"SELECT id::text FROM {TABLE_POSITIONS} "
        f"WHERE asset_id = $1::uuid AND org_id = $2::uuid "
        f"AND source_system = $3 AND as_of_date = $4",
        asset_id, DEFAULT_ORG_ID, IMPORT_SOURCE_SYSTEM, AS_OF,
    )
    check(
        "5 two REAL position candidates now exist for one "
        "(owner, asset, as_of_date) from two different source_system values",
        bool(manual_id) and bool(import_id) and manual_id != import_id,
        f"reporting_tool_import={import_id}, manual={manual_id}",
    )

    # Snapshot every column of the row that is about to lose, so "only the
    # annotation changed" is a fact and not a hope.
    before_loser = dict(await conn.fetchrow(
        f"SELECT * FROM {TABLE_POSITIONS} WHERE id = $1::uuid", manual_id
    ))

    # ── Case 1: the DEFAULT order (org has configured nothing) ──────────────
    outcome = await resolve_holding(
        conn, DEFAULT_ORG_ID, owner_entity_id=owner_id, asset_id=asset_id,
        as_of_date=AS_OF,
    )
    check(
        "5 under the DEFAULT order the winner is EXACTLY "
        "'reporting_tool_import'",
        outcome is not None
        and outcome.winner_source_system == IMPORT_SOURCE_SYSTEM
        and outcome.winner_position_id == import_id
        and outcome.order_is_default,
        f"winner={outcome.winner_source_system if outcome else None}, "
        f"is_default={outcome.order_is_default if outcome else None}",
    )

    loser = await conn.fetchrow(
        f"SELECT * FROM {TABLE_POSITIONS} WHERE id = $1::uuid", manual_id
    )
    check(
        "5 the LOSING row still EXISTS and is queryable — not deleted",
        loser is not None and loser["valid_to"] is None and loser["system_to"] is None,
        f"row present={loser is not None}, still current="
        f"{loser is not None and loser['valid_to'] is None}",
    )
    check(
        "5 the losing row's superseded_by_source is set to the WINNING source "
        f"('{IMPORT_SOURCE_SYSTEM}')",
        loser is not None and loser["superseded_by_source"] == IMPORT_SOURCE_SYSTEM,
        f"superseded_by_source={loser['superseded_by_source'] if loser else None}",
    )
    changed = sorted(
        k for k, v in dict(loser).items()
        if k != "superseded_by_source" and before_loser[k] != v
    )
    check(
        "5 superseded_by_source is the ONLY column the resolution touched — "
        "the losing row's measures are byte-identical, so it is still usable "
        "for reconciliation",
        not changed,
        f"other columns changed: {changed or 'none'}",
    )
    winner_row = await conn.fetchrow(
        f"SELECT superseded_by_source FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
        import_id,
    )
    check(
        "5 the WINNING row's superseded_by_source is NULL",
        winner_row is not None and winner_row["superseded_by_source"] is None,
        f"winner superseded_by_source="
        f"{winner_row['superseded_by_source'] if winner_row else 'ROW MISSING'}",
    )

    # Idempotent: resolving again changes nothing.
    again = await resolve_holding(
        conn, DEFAULT_ORG_ID, owner_entity_id=owner_id, asset_id=asset_id,
        as_of_date=AS_OF,
    )
    check(
        "5 re-resolving an already-resolved holding writes NOTHING",
        again is not None and again.rows_marked == 0 and again.rows_cleared == 0
        and again.winner_position_id == import_id,
        f"rows_marked={again.rows_marked}, rows_cleared={again.rows_cleared}",
    )

    # ── Case 2: the org CONFIGURES its own order, inverting the default ─────
    await conn.execute(
        "INSERT INTO org_settings (org_id, setting_key, setting_value, category) "
        "VALUES ($1::uuid, $2, $3::jsonb, $4) "
        "ON CONFLICT (org_id, setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value",
        DEFAULT_ORG_ID, PRECEDENCE_SETTING_KEY, json.dumps(FIX_CUSTOM_ORDER),
        category_for(PRECEDENCE_SETTING_KEY),
    )
    configured = await get_source_order(conn, DEFAULT_ORG_ID)
    check(
        "2 the configured order is read back from org_settings as the org's "
        "OWN (is_default False), not the platform default",
        not configured.is_default and configured.order == tuple(FIX_CUSTOM_ORDER)
        and configured.invalid_reason is None,
        f"is_default={configured.is_default}, first={configured.order[0]}",
    )

    outcome2 = await resolve_holding(
        conn, DEFAULT_ORG_ID, owner_entity_id=owner_id, asset_id=asset_id,
        as_of_date=AS_OF,
    )
    check(
        "5 with the org's CONFIGURED order the winner flips to EXACTLY "
        "'manual' — precedence is genuinely driven by the setting, not by code",
        outcome2 is not None and outcome2.winner_source_system == "manual"
        and outcome2.winner_position_id == manual_id
        and not outcome2.order_is_default,
        f"winner={outcome2.winner_source_system if outcome2 else None}, "
        f"is_default={outcome2.order_is_default if outcome2 else None}",
    )
    flipped_loser = await conn.fetchrow(
        f"SELECT superseded_by_source, valid_to FROM {TABLE_POSITIONS} "
        f"WHERE id = $1::uuid", import_id,
    )
    flipped_winner = await conn.fetchrow(
        f"SELECT superseded_by_source FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
        manual_id,
    )
    check(
        "5 the previous winner is now the loser and carries "
        "superseded_by_source='manual', still present and still current",
        flipped_loser is not None
        and flipped_loser["superseded_by_source"] == "manual"
        and flipped_loser["valid_to"] is None,
        f"superseded_by_source="
        f"{flipped_loser['superseded_by_source'] if flipped_loser else None}",
    )
    check(
        "5 the new winner's STALE superseded_by_source was CLEARED to NULL — "
        "a row still flagged by a source that no longer outranks it would be "
        "skipped by every downstream reader",
        flipped_winner is not None
        and flipped_winner["superseded_by_source"] is None
        and outcome2.rows_cleared == 1,
        f"superseded_by_source="
        f"{flipped_winner['superseded_by_source'] if flipped_winner else None}, "
        f"rows_cleared={outcome2.rows_cleared}",
    )

    # Restore the unconfigured state for the remaining checks.
    await conn.execute(
        "DELETE FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
        DEFAULT_ORG_ID, PRECEDENCE_SETTING_KEY,
    )
    reverted = await get_source_order(conn, DEFAULT_ORG_ID)
    check(
        "2 clearing the setting reverts the org to the platform default",
        reverted.is_default and reverted.order == DEFAULT_SOURCE_ORDER,
        f"is_default={reverted.is_default}",
    )

    # Two positions on DIFFERENT holding keys have no shared answer, and
    # resolving them together would mark one superseded by a source describing
    # a different asset. The XLSX fixture is a different asset on the same
    # owner and date, so this is a real mixed set, not a contrived one.
    xlsx_position_id = await conn.fetchval(
        f"SELECT p.id::text FROM {TABLE_POSITIONS} p "
        f"JOIN {TABLE_ASSETS} a ON a.id = p.asset_id "
        f"WHERE a.name = $1 AND p.org_id = $2::uuid",
        FIX_ASSET_XLSX, DEFAULT_ORG_ID,
    )
    try:
        await resolve_precedence(conn, DEFAULT_ORG_ID, [import_id, xlsx_position_id])
        refused, detail = False, "resolved across two different assets"
    except PrecedenceError as exc:
        refused, detail = True, str(exc)[:90]
    # The reason matters, not just the raise. On the first run of this script
    # the XLSX import was broken, `xlsx_position_id` was None, and this
    # assertion passed on "position_candidates contains None" — a real vacuous
    # pass, caught only because the detail string was printed. Asserting the id
    # exists AND that the refusal names the holding-key spanning is what makes
    # it a test of the rule rather than of an accident.
    check(
        "2 resolving candidates that span DIFFERENT holding keys is refused — "
        "it would mark a position superseded by a source describing another "
        "asset",
        refused and bool(xlsx_position_id) and "distinct" in detail,
        f"xlsx_position_id={xlsx_position_id}; refusal: {detail}",
    )
    check(
        "2 resolving two positions that DO share a holding key is accepted",
        (await resolve_precedence(
            conn, DEFAULT_ORG_ID, [import_id, manual_id], apply=False
        )).winner_source_system == IMPORT_SOURCE_SYSTEM,
        "same (owner, asset, as_of_date) — accepted, and apply=False writes "
        "nothing",
    )


async def check_cross_org(app_conn, admin_conn, other_owner_id: str) -> None:
    """Cross-org isolation, against the REAL app_service connection.

    Run under app_service specifically: `postgres` has rolbypassrls, so every
    one of these would "pass" while proving nothing.
    """
    # A real position and a real ext-ref in the OTHER org.
    other_asset_id = await create_asset(
        admin_conn, org_id=OTHER_ORG_ID, name=FIX_ASSET_OTHERORG,
        asset_type="unclassified", ownership_basis="value",
    )
    other_position_id = await create_position(
        admin_conn, org_id=OTHER_ORG_ID, owner_entity_id=other_owner_id,
        asset_id=other_asset_id, as_of_date=AS_OF, authority="stated",
        source_system="manual", ownership_basis="value",
        market_value=Decimal("999999.99"),
    )
    await upsert_external_reference(
        admin_conn, org_id=OTHER_ORG_ID, source_system="manual",
        external_id=FIX_OTHERORG_EXT_ID, record_type="position",
        record_id=other_position_id,
    )

    async with org_ctx(app_conn, DEFAULT_ORG_ID) as c:
        seen_position = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
            other_position_id,
        )
        seen_ext = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_EXT_REF} WHERE external_id = $1",
            FIX_OTHERORG_EXT_ID,
        )
        seen_asset = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_ASSETS} WHERE id = $1::uuid",
            other_asset_id,
        )
    check(
        "X cross-org: org A cannot see org B's positions, external_references "
        "or assets under the real app_service connection",
        seen_position == 0 and seen_ext == 0 and seen_asset == 0,
        f"positions={seen_position}, external_references={seen_ext}, "
        f"assets={seen_asset} (all must be 0)",
    )

    # And the other direction, so the check is not passing by the fixtures
    # simply not existing.
    async with org_ctx(app_conn, OTHER_ORG_ID) as c:
        own_position = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
            other_position_id,
        )
        own_ext = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_EXT_REF} WHERE external_id = $1",
            FIX_OTHERORG_EXT_ID,
        )
    check(
        "X cross-org: org B CAN see its own rows — the isolation check above "
        "is not passing merely because the rows do not exist",
        own_position == 1 and own_ext == 1,
        f"own positions={own_position}, own external_references={own_ext}",
    )

    # The idempotency key is now org-scoped: the SAME external_id in two orgs
    # is two independent rows, which the pre-Part-1 constraint made impossible.
    same_id_here = await find_external_reference(
        admin_conn, org_id=DEFAULT_ORG_ID, source_system="manual",
        external_id=FIX_OTHERORG_EXT_ID, record_type="position",
    )
    check(
        "X the ext-ref idempotency key is org-scoped: org B's external_id is "
        "invisible to org A's lookup, so two tenants ingesting the same "
        "upstream id no longer collide",
        same_id_here is None,
        f"org A lookup of org B's external_id returned {same_id_here!r}",
    )

    # Precedence itself refuses to reach across the tenant boundary.
    try:
        await resolve_precedence(admin_conn, DEFAULT_ORG_ID, [other_position_id])
        refused, detail = False, "resolved another org's position"
    except PrecedenceError as exc:
        refused, detail = True, str(exc)[:80]
    check(
        "X precedence refuses to resolve a position that is not visible in the "
        "caller's org — it raises rather than silently resolving nothing",
        refused, detail,
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
    saved_precedence: str | None = None
    try:
        # Read the org's REAL precedence setting BEFORE teardown deletes it.
        # A stored value equal to this script's own fixture order is a previous
        # crashed run's leftover, not the org's configuration — restoring that
        # would make every future run inherit the wreckage.
        stored = await read_stored_precedence(admin_conn)
        if stored is not None and json.loads(stored) != FIX_CUSTOM_ORDER:
            saved_precedence = stored
            report("TEARDOWN — org has a real precedence setting",
                   f"captured for restore at end: {stored[:80]}")
        elif stored is not None:
            report("TEARDOWN — leftover fixture precedence order found",
                   "equal to this script's own fixture order, so it is a "
                   "previous crashed run's residue and will NOT be restored")

        await teardown(admin_conn, restore_precedence=saved_precedence)   # START
        baseline = await counts(admin_conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        nonempty = {t: n for t, n in baseline.items() if n}
        if nonempty:
            report("TEARDOWN — rows are already present in these tables",
                   f"{nonempty}. Teardown is by-fixture + count assertion, "
                   f"NOT a truncate — Phase B is the sprint that starts writing "
                   f"real positions, so an unconditional truncate here would be "
                   f"a data-loss bug by the next quarter-end.")

        await seed_users(admin_conn)

        print("\n── Task 1: discovery, asserted ──")
        await check_task1(admin_conn)

        print("\n── Task 3: the Altruist gate (real) ──")
        await check_task3()

        print("\n── Task 2: precedence configuration ──")
        await check_precedence_config(admin_conn)

        print("\n── Task 4: file-based reporting-tool import ──")
        owner_id = await seed_entity(admin_conn, DEFAULT_ORG_ID, FIX_ACCOUNT)
        imported = await check_import(admin_conn, owner_id)

        print("\n── Task 5: precedence proved with two real sources ──")
        await check_precedence_resolution(
            admin_conn, owner_id, imported["asset_a_id"]
        )

        print("\n── Cross-org isolation (real app_service connection) ──")
        other_owner_id = await seed_entity(
            admin_conn, OTHER_ORG_ID, FIX_OTHERORG_ACCOUNT
        )
        await check_cross_org(app_conn, admin_conn, other_owner_id)

    finally:
        await teardown(admin_conn, restore_precedence=saved_precedence)   # END
        if baseline:
            final = await counts(admin_conn)
            drift = {
                t: (baseline[t], final[t]) for t in TABLES if baseline[t] != final[t]
            }
            check(
                "TEARDOWN restores the EXACT before-count on every table it "
                "touched, including public.org_settings",
                not drift,
                f"drift (before, after): {drift}" if drift
                else ", ".join(f"{t.split('.')[-1]}={final[t]}" for t in TABLES),
            )
            restored = await read_stored_precedence(admin_conn)
            check(
                "TEARDOWN restores the org's own precedence setting byte-for-"
                "byte (or leaves it unset if it was unset) — this script writes "
                "to LIVE tenant configuration",
                (restored is None and saved_precedence is None)
                or (restored is not None and saved_precedence is not None
                    and json.loads(restored) == json.loads(saved_precedence)),
                f"before={saved_precedence!r}, after={restored!r}",
            )
        await app_conn.close()
        await admin_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}")
    print(f"RESULT: {passed}/{total} passed"
          + (f", {len(blocked)} BLOCKED (not counted as passes)" if blocked else ""))
    if blocked:
        print("\nBLOCKED — measured as unmeasurable, never as green:")
        for name, reason in blocked:
            print(f"  · {name}\n      {reason}")
    failures = [(n, d) for n, ok, d in results if not ok]
    if failures:
        print("\nFAILURES:")
        for name, detail in failures:
            print(f"  · {name} — {detail}")
    print("=" * 72)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
