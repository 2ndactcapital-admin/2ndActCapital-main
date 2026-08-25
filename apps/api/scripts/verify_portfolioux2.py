"""Verification — Portfolio UX 2: the Transactions grid.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END with an
EXACT before/after count on every table touched — never a truncate, because
these tables hold real rows.

Real database, real ASGI app, real non-bypass ``app_service`` role. The harness
is UX 1's, deliberately: a second, differently-shaped harness for the same kind
of sprint would be a second thing to keep honest.

────────────────────────────────────────────────────────────────────────────
THE ASSERTIONS THIS SPRINT IS EASIEST TO FAKE, AND HOW THEY ARE WRITTEN
────────────────────────────────────────────────────────────────────────────
**"The endpoints exist."** An endpoint that 404s, 401s or returns ``[]`` would
satisfy a status-code check. So every endpoint is DRIVEN through the real ASGI
app with a real token, and its body is compared against a DIRECT SQL query of
the same rows.

**"The is_corporate_action_adjustment filter works."** This is the one the
sprint brief singles out, and it is the easiest to fake in BOTH directions: a
filter that returned everything would pass ``adjustments ⊆ all``, and a filter
that returned nothing would pass ``adjustments ∩ trades = ∅``. So it is asserted
as a PARTITION: ``true`` and ``false`` are each non-empty, each is a strict
subset, they are disjoint, and their union is exactly the unfiltered set. A
broken filter cannot satisfy all four.

**"The transaction_type_code filter works."** Same shape — non-empty, strict
subset, and compared element-by-element against the same predicate in SQL.

**"A correction works."** A POST that returned 201 and wrote nothing would pass
a status check. So it is verified five ways: the response carries a DIFFERENT
id, a fresh GET shows the new value, direct SQL shows exactly two rows with
exactly ONE current, the CLOSED row still carries its ORIGINAL value, and the
successor's ``related_transaction_id`` points back at it.

**"The original row remains reachable."** Asserted through the API
(``include_history=true`` and a direct GET on the old id both find it), not just
in SQL — a row that exists but that no endpoint will return is not reachable.

**"Cross-org isolation."** An endpoint that returns nothing for everybody passes
an "org B cannot see org A" check. Both directions are asserted against the SAME
call, at the endpoint layer AND at the RLS layer on a real non-bypassing
``app_service`` connection.

Run:
    python3 scripts/verify_portfolioux2.py
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
    PortfolioError,
    TransactionMarketError,
    create_asset,
    create_position,
    record_transaction,
)
from services.portfolio_transactions import (  # noqa: E402
    CORRECTABLE_FIELDS,
    INLINE_CORRECTABLE_FIELDS,
    RECORD_TYPE_TRANSACTION,
    TABLE_DOC_RECORD_LINKS,
    correct_transaction,
    correction_history,
    get_transaction,
    list_transactions,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org. A real row, not a minted one — an isolation test against
# an org that does not exist proves the FK, not the policy.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "VERIFY-PORTFOLIOUX2"

A_SUB = "auth0|verify_portfolioux2_orga"
B_SUB = "auth0|verify_portfolioux2_orgb"
# `services.permissions.get_user_id` DERIVES the id from the sub rather than
# looking it up, so a fixture seeded under a hand-picked literal is a user no
# code path ever finds (Portfolio C's finding).
A_USER_ID = str(uuid5(NAMESPACE_URL, A_SUB))
B_USER_ID = str(uuid5(NAMESPACE_URL, B_SUB))

TODAY = date(2026, 8, 25)

# ── Exact figures. Chosen so LEXICAL and NUMERIC order DISAGREE. ────────────
# "12000.00" < "900.00" < "1000.00" as strings; 900 < 1000 < 12000 as numbers.
BUY_QTY = Decimal("1000.00")
BUY_PRICE = Decimal("12.00")
BUY_GROSS = Decimal("12000.00")
BUY_NET = Decimal("12000.00")
SELL_NET = Decimal("900.00")
FEE_NET = Decimal("-12.34")
ADJ_QTY = Decimal("1000.00")

CORRECTED_NET = Decimal("11950.00")

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
    never by org — the orgs are real and full of production rows. Transactions
    carry no name of their own, so they are reached through their tagged asset's
    positions.

    ``portfolio.transactions`` has a SELF-referencing FK
    (``related_transaction_id``), which a correction populates. One DELETE
    statement removes a chain safely: referential-integrity triggers for a
    NO ACTION constraint fire at end-of-statement, so parent and child going
    together is fine — two statements in the wrong order would not be.
    """
    tagged_assets = f"SELECT id FROM {TABLE_ASSETS} WHERE name LIKE '{TAG}%'"
    tagged_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({tagged_assets})"
    )
    tagged_txns = (
        f"SELECT id FROM {TABLE_TRANSACTIONS} "
        f"WHERE position_id IN ({tagged_positions})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_DOC_RECORD_LINKS} "
        f"WHERE record_id IN ({tagged_positions}) "
        f"   OR record_id IN ({tagged_txns}) "
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


def _repo_python_sources() -> dict[str, str]:
    """Every .py file under apps/api that is not a verify script.

    Verify scripts are excluded because they legitimately write to the tables
    directly, and counting them would let a script's own teardown satisfy — or
    break — an assertion about the APPLICATION's behaviour.
    """
    out = {}
    for path in glob.glob(os.path.join(_API, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, _API)
        if rel.startswith(("scripts" + os.sep, "venv" + os.sep)):
            continue
        out[rel] = read(path)
    return out


def check_task1a() -> None:
    """1a — record_transaction existed; no REST endpoint did."""
    src = read(os.path.join(_API, "services", "portfolio_assets.py"))
    tree = ast.parse(src)
    fns = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    check(
        "[Y] 1a record_transaction exists in services/portfolio_assets.py — the "
        "write contract shipped in A2 and has been service-only ever since",
        "record_transaction" in fns,
        f"portfolio_assets defines {len(fns)} functions",
    )

    # Which modules call it. If any pre-existing ROUTER did, the gap was not
    # real and this whole sprint would be re-building something.
    callers = sorted(
        rel for rel, body in _repo_python_sources().items()
        if "record_transaction(" in body
        and rel not in ("services/portfolio_assets.py",)
    )
    new_files = {
        "services/portfolio_transactions.py",
        "routers/portfolio_transactions.py",
    }
    pre_existing_callers = [c for c in callers if c not in new_files]
    check(
        "[Y] 1a before this sprint every caller of record_transaction was "
        "another SERVICE — no router, so nothing reached it over HTTP",
        pre_existing_callers == ["services/portfolio_corporate_actions.py",
                                 "services/portfolio_documents.py"]
        and all(c.startswith("services" + os.sep) for c in pre_existing_callers),
        f"pre-existing callers = {pre_existing_callers}",
    )
    # The second caller is itself unwired, which is worth knowing rather than
    # glossing: a helper with no callers is not an HTTP surface, but it IS a
    # second place transactions can be written from, and a claim of "one caller"
    # would have been wrong.
    docs_helper_callers = sorted(
        rel for rel, body in _repo_python_sources().items()
        if "record_transaction_from_document" in body
        and rel != "services/portfolio_documents.py"
    )
    check(
        "[Y] 1a portfolio_documents.record_transaction_from_document is an "
        "UNWIRED helper — it has no callers at all, so it is not a second HTTP "
        "path either",
        docs_helper_callers == [],
        f"callers = {docs_helper_callers or 'none'}",
    )

    # No pre-existing router declared a /portfolio/transactions path.
    pre_existing_routers = {
        rel: body for rel, body in _repo_python_sources().items()
        if rel.startswith("routers" + os.sep) and rel not in new_files
    }
    stray = {
        rel: sorted(set(re.findall(r'"(/portfolio/transactions[^"]*)"', body)))
        for rel, body in pre_existing_routers.items()
    }
    check(
        "[Y] 1a NO pre-existing router declared a /portfolio/transactions route",
        not any(stray.values()),
        f"stray declarations = "
        f"{ {k: v for k, v in stray.items() if v} or 'none'}",
    )
    report(
        "1a REST endpoints for transactions did NOT exist — they were built by "
        "this sprint",
        "services/portfolio_assets.record_transaction shipped in Portfolio A2 "
        "with no HTTP surface. Its pre-existing callers were TWO services and "
        "the verify scripts: services/portfolio_corporate_actions.py, and "
        "services/portfolio_documents.record_transaction_from_document — which "
        "is itself UNWIRED (zero callers anywhere), so it was never an HTTP "
        "path either. The single pre-existing HTTP exposure of a transaction "
        "anywhere was the READ-ONLY nested list in the Positions detail pane "
        "(services/portfolio_positions.transaction_history). NEW FILES: "
        "routers/portfolio_transactions.py + services/portfolio_transactions.py.",
    )


def check_task1b() -> None:
    """1b — UX 1's real shipped code was read and is mirrored, not re-derived."""
    pairs = (
        ("services/portfolio_positions.py", "services/portfolio_transactions.py"),
        ("routers/portfolio_positions.py", "routers/portfolio_transactions.py"),
    )
    for old, new in pairs:
        check(
            f"[Y] 1b {old} exists and was the model for {new}",
            os.path.exists(os.path.join(_API, old))
            and os.path.exists(os.path.join(_API, new)),
            f"{old} → {new}",
        )

    svc = read(os.path.join(_API, "services", "portfolio_transactions.py"))
    rtr = read(os.path.join(_API, "routers", "portfolio_transactions.py"))
    # The four structural habits UX 1 established, each asserted by the artefact
    # that would be missing if it had been re-derived instead of copied.
    check(
        "[Y] 1b the same permission constants, paging shape and decimal-string "
        "serialisation as UX 1 — not a second set of conventions",
        'READ_PERMISSION = "view_portfolio"' in svc
        and 'WRITE_PERMISSION = "manage_portfolio"' in svc
        and "DEFAULT_LIMIT = 200" in svc
        and "MAX_LIMIT = 1000" in svc,
        "view_portfolio / manage_portfolio / 200 / 1000",
    )
    check(
        "[Y] 1b the float refusal is the same mode='before' validator UX 1 "
        "proved is load-bearing — written any later it is dead code, because "
        "Decimal is in the field union and lax mode accepts a float into it",
        'field_validator(*MONEY_FIELDS, mode="before")' in rtr,
        "mode='before' on every money field",
    )
    check(
        "[Y] 1b _carry_document_links is carried over — a correction mints a "
        "NEW record_id, so without it the first settle-date fix would orphan "
        "the statement the entry was read out of",
        "_carry_document_links" in svc,
        "present in services/portfolio_transactions.py",
    )
    grid = read(os.path.join(_WEB, "components", "portfolio", "TransactionsGrid.jsx"))
    check(
        "[Y] 1b the frontend reuses the SAME DataGrid and the SAME EntityPicker "
        "UX 1 used — no second grid, no second picker",
        'from "@/components/ui/DataGrid"' in grid
        and 'from "@/components/EntityPicker"' in grid,
        "DataGrid + EntityPicker imported",
    )
    report(
        "1b UX 1's shipped code was read and mirrored exactly",
        "routers/portfolio_positions.py (459 ln), "
        "services/portfolio_positions.py (941 ln), PositionsGrid.jsx (669 ln) "
        "and PositionDetailPane.jsx (733 ln). Everything reused: the "
        "vocabularies envelope with server-published editable-field lists, "
        "total-before-limit paging, exact-decimal-string money, the "
        "mode='before' float refusal, DataGrid cell renderers for inline edit, "
        "EntityPicker for the owner filter, the embedded DocumentsPanel keyed "
        "on a server-supplied record_type, and _carry_document_links.",
    )


def check_task1c(txn_types: list[dict]) -> None:
    """1c — the REAL market rule and the REAL meaning of the adjustment flag."""
    src = read(os.path.join(_API, "services", "portfolio_assets.py"))
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "record_transaction"
    )
    doc = ast.get_docstring(fn, clean=False) or ""
    check(
        "[Y] 1c record_transaction is the ONLY place checking a type's market "
        "against the asset's, and it says so — the grid respects that rule "
        "rather than re-deriving it (every correction runs back through it)",
        "market" in doc and "TransactionMarketError" in src,
        "record_transaction raises TransactionMarketError on a mismatch",
    )
    # The rule, as deployed, read off the REAL vocabulary rather than assumed.
    both = sorted(t["code"] for t in txn_types if t["market"] == "both")
    public = sorted(t["code"] for t in txn_types if t["market"] == "public")
    private = sorted(t["code"] for t in txn_types if t["market"] == "private")
    check(
        "[Y] 1c the deployed transaction_types vocabulary really does carry a "
        "market on every row, with a small 'both' escape hatch — so the check "
        "is a classification, not a rules engine",
        len(both) >= 1 and len(public) >= 1 and len(private) >= 1
        and not [t for t in txn_types if t["market"] is None],
        f"both={both} public={public} private={private}",
    )

    ca_src = read(os.path.join(_API, "services", "portfolio_corporate_actions.py"))
    # Whitespace-normalised before the search. The phrase is wrapped across a
    # line in the source, and a raw substring test would fail on the newline —
    # reporting a missing design decision that is in fact right there.
    ca_flat = " ".join(ca_src.split())
    a2_flat = " ".join(read(
        os.path.join(_API, "services", "portfolio_assets.py")).split())
    check(
        "[Y] 1c is_corporate_action_adjustment is set EXPLICITLY and is NOT "
        "derived from corporate_action_id — BOTH the writer (A2's "
        "record_transaction) and Phase F's module say so in as many words, and "
        "the two legitimately differ for a cash-in-lieu sale, which cites an "
        "action and IS a realized gain",
        "is set explicitly and is NOT derived from" in ca_flat
        and "rather than something derived from" in a2_flat,
        "both modules record the non-derivation",
    )
    check(
        "[Y] 1c the flag is half of Phase F's IDEMPOTENCY key — "
        "already_applied_transactions requires corporate_action_id AND the "
        "flag, so the grid must not let either be hand-edited",
        "already_applied_transactions" in ca_src
        and "is_corporate_action_adjustment" in ca_src,
        "already_applied_transactions predicates on both",
    )
    svc = read(os.path.join(_API, "services", "portfolio_transactions.py"))
    check(
        "[Y] 1c neither corporate-action field is correctable, and neither is "
        "settable on create — the grid respects Phase F rather than exposing "
        "the key that stops a corporate action being applied twice",
        "corporate_action_id" not in CORRECTABLE_FIELDS
        and "is_corporate_action_adjustment" not in CORRECTABLE_FIELDS
        and "idempotency key" in svc,
        f"correctable = {sorted(CORRECTABLE_FIELDS)}",
    )
    report(
        "1c both Phase E and Phase F rules confirmed against deployed data, "
        "and respected rather than re-derived",
        f"MARKET (Phase E, record_transaction:771-785): type market 'both' or "
        f"NULL → always allowed; asset market 'both' (amortized_cost) → always "
        f"allowed; otherwise must be equal. The asset's market is derived from "
        f"valuation_method, NOT asset_type, which has no CHECK constraint. "
        f"Deployed vocabulary: {len(both)} of {len(txn_types)} types are "
        f"market='both' ({', '.join(both)}). "
        f"FLAG (Phase F): is_corporate_action_adjustment is stored explicitly, "
        f"never derived from corporate_action_id IS NOT NULL — a cash-in-lieu "
        f"sale cites an action and is still a realized gain. Together the two "
        f"columns are the idempotency key for apply_corporate_action, so "
        f"neither is correctable or settable through this API.",
    )


def check_task1d() -> None:
    """1d — THE finding. Transactions are append-only; there is no edit path."""
    sources = _repo_python_sources()
    new_files = {
        "services/portfolio_transactions.py",
        "routers/portfolio_transactions.py",
    }
    # Any UPDATE against the transactions table, anywhere in the application,
    # excluding the two files this sprint added.
    pattern = re.compile(
        r"UPDATE\s+(?:\{TABLE_TRANSACTIONS\}|portfolio\.transactions)",
        re.IGNORECASE,
    )
    updaters = sorted(
        rel for rel, body in sources.items()
        if rel not in new_files and pattern.search(body)
    )
    check(
        "[Y] 1d there was NO edit path — not one UPDATE against "
        "portfolio.transactions existed anywhere in the application before "
        "this sprint. The ledger is append-only, so this sprint builds a "
        "CORRECTION, not an in-place edit",
        updaters == [],
        f"pre-existing UPDATE sites = {updaters or 'none'}",
    )

    svc = read(os.path.join(_API, "services", "portfolio_transactions.py"))
    # The ONE update this sprint adds is the bi-temporal close, and it must
    # touch valid_to and nothing else. Asserted on the statement, not on intent.
    closes = re.findall(
        r"UPDATE\s+\{TABLE_TRANSACTIONS\}[^;]*?SET\s+([a-z_]+)\s*=", svc
    )
    check(
        "[Y] 1d the only UPDATE this sprint adds is the bi-temporal close — it "
        "sets valid_to and nothing else, and never rewrites a figure",
        closes == ["valid_to"],
        f"SET targets = {closes}",
    )
    tree = ast.parse(svc)
    fns = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    check(
        "[Y] 1d the correction goes back through A2's record_transaction — so "
        "the Phase-E market check and the retired-type refusal run on the "
        "CORRECTION, not only on the original insert",
        "correct_transaction" in fns and "record_transaction" in svc,
        "correct_transaction → record_transaction",
    )
    rtr = read(os.path.join(_API, "routers", "portfolio_transactions.py"))
    check(
        "[Y] 1d the REST surface has NO PATCH on a transaction — the "
        "correction is a POST to a /corrections sub-resource, because it MINTS "
        "a row rather than mutating one",
        "corrections" in rtr and ".patch(" not in rtr,
        "POST /portfolio/transactions/{id}/corrections; no PATCH declared",
    )
    report(
        "1d TRANSACTIONS ARE APPEND-ONLY — this sprint built a CORRECTION path, "
        "not an in-place edit",
        "Zero UPDATE statements against portfolio.transactions existed anywhere "
        "in apps/api. The mechanism chosen is bi-temporal supersession (close "
        "valid_to, insert a successor through record_transaction) rather than "
        "an offsetting reversal, on a fact about the EXISTING readers: "
        "portfolio_positions.transaction_history, portfolio_commitments' "
        "SUM(amount * tt.affects_paid_in) roll-up and "
        "portfolio_corporate_actions.already_applied_transactions ALL already "
        "filter valid_to IS NULL AND system_to IS NULL, so closing a row "
        "removes it from all three correctly with no change to any of them. An "
        "offsetting reversal would leave both rows current, and the deployed "
        "transaction_types vocabulary has no negative counterpart for any of "
        "its sixteen codes (sell is not the reversal of buy — it carries "
        "performance_impact='gain'); it would also double count(*), which "
        "portfolio_commitments reads as `n`, and make "
        "already_applied_transactions see two markers for one applied action. "
        "The chain is recorded in related_transaction_id, which had ZERO "
        "writers and ZERO rows before this sprint (record_transaction accepted "
        "it; nothing ever passed one) and which carries a self-FK. CONSTRAINT "
        "REPORTED, not designed around: because the column now means exactly "
        "'the row this row corrects', the REST surface deliberately does not "
        "expose it on create — a future ingest wanting it for fee-to-trade "
        "pairing needs its own column.",
    )
    report(
        "GAP — a correction has nowhere to record a REASON",
        "portfolio.transactions has no note, no reason and no corrected_by "
        "column. document_field_corrections was made polymorphic by the "
        "corrections sprint and is the obvious home, but it is field-grained "
        "and guarded by document_field_corrections_document_pairing_chk, whose "
        "target_type vocabulary this sprint has no mandate to extend. The API "
        "therefore does not accept a reason at all — accepting one and dropping "
        "it would be worse than not accepting one. Who and when ARE captured, "
        "bi-temporally, on the successor row.",
    )
    report(
        "BONUS — Task 4's document link needed no new plumbing, again",
        f"services/portfolio_documents.py already defines "
        f"RECORD_TYPE_TRANSACTION={RECORD_TYPE_TRANSACTION!r} and "
        f"GET /records/{{type}}/{{id}}/documents dispatches on it generically — "
        f"the same generic-dispatch finding UX 1 recorded for positions, now "
        f"confirmed to hold for a second record type. What DID need building is "
        f"_carry_document_links for transactions: a correction mints a new "
        f"record_id, so without it the first fix would orphan the evidence.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def seed_users(conn) -> None:
    for user_id, org, sub, email in (
        (A_USER_ID, DEFAULT_ORG_ID, A_SUB, "verify_ux2_a@test.local"),
        (B_USER_ID, OTHER_ORG_ID, B_SUB, "verify_ux2_b@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users
                (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioUX2', $4,
                    'member', true)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, org, email, sub,
        )


async def _pick_txn_type(conn, market: str, *, prefer: str | None = None) -> str:
    """A REAL, active transaction type of the given market.

    Read from the deployed table rather than hardcoded: ``record_transaction``
    refuses a retired type and refuses a market mismatch, and a literal that
    happened to be renamed would fail the fixture rather than the feature.
    """
    if prefer:
        code = await conn.fetchval(
            "SELECT code FROM public.transaction_types "
            "WHERE code = $1 AND is_active = true AND market = $2",
            prefer, market,
        )
        if code:
            return code
    code = await conn.fetchval(
        "SELECT code FROM public.transaction_types "
        "WHERE is_active = true AND market = $1 ORDER BY display_order, code "
        "LIMIT 1",
        market,
    )
    if code is None:  # pragma: no cover — the A2 backfill guarantees these
        raise RuntimeError(f"no active transaction_type with market={market!r}")
    return code


async def seed(conn) -> dict:
    """Two orgs, three assets, three positions, five transactions, one linked
    document."""
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

    # market_price → a PUBLIC-markets asset. appraisal → PRIVATE.
    ids["asset_public"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Listed Equity",
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
    )
    ids["asset_private"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Private Fund Interest",
        asset_type="private_equity", asset_class="financial",
        ownership_basis="percent", valuation_method="appraisal",
        currency_code="USD",
    )
    ids["asset_b"] = await create_asset(
        conn, org_id=OTHER_ORG_ID, name=f"{TAG} OtherOrg Asset",
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
    )

    ids["pos_public"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a"],
        asset_id=ids["asset_public"], as_of_date=TODAY,
        authority="custodial", source_system="reporting_tool_bd",
        ownership_basis="units", quantity=BUY_QTY,
        cost_basis=Decimal("12000.00"), taxonomy_key="taxonomy_sc_1",
    )
    ids["pos_private"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a2"],
        asset_id=ids["asset_private"], as_of_date=TODAY,
        authority="stated", source_system="manual",
        ownership_basis="percent", ownership_pct=Decimal("25.0000"),
    )
    ids["pos_b"] = await create_position(
        conn, org_id=OTHER_ORG_ID, owner_entity_id=ids["owner_b"],
        asset_id=ids["asset_b"], as_of_date=TODAY,
        authority="custodial", source_system="manual",
        ownership_basis="units", quantity=Decimal("7.00"),
    )

    ids["type_buy"] = await _pick_txn_type(conn, "public", prefer="buy")
    ids["type_sell"] = await _pick_txn_type(conn, "public", prefer="sell")
    ids["type_fee"] = await _pick_txn_type(conn, "both", prefer="fee_expense")
    ids["type_adj"] = await _pick_txn_type(conn, "both", prefer="adjustment")
    ids["type_call"] = await _pick_txn_type(conn, "private", prefer="call_investment")

    # ── Three ordinary trades on the public position ────────────────────
    ids["txn_buy"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_public"],
        transaction_type_code=ids["type_buy"],
        trade_date=TODAY - timedelta(days=20), authority="custodial",
        source_system="reporting_tool_bd", quantity=BUY_QTY, price=BUY_PRICE,
        gross_amount=BUY_GROSS, net_amount=BUY_NET, currency_code="USD",
        external_ref=f"{TAG}-BUY-001",
    )
    ids["txn_sell"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_public"],
        transaction_type_code=ids["type_sell"],
        trade_date=TODAY - timedelta(days=10), authority="custodial",
        source_system="reporting_tool_bd", quantity=Decimal("75.00"),
        price=Decimal("12.00"), gross_amount=SELL_NET, net_amount=SELL_NET,
        currency_code="USD", external_ref=f"{TAG}-SELL-001",
    )
    ids["txn_fee"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_public"],
        transaction_type_code=ids["type_fee"],
        trade_date=TODAY - timedelta(days=5), authority="custodial",
        source_system="reporting_tool_bd", net_amount=FEE_NET,
        currency_code="USD",
    )

    # ── A CORPORATE-ACTION ADJUSTMENT ───────────────────────────────────
    # corporate_action_id is left NULL deliberately, and that is a legitimate
    # state rather than a shortcut: Phase F stores the flag EXPLICITLY and never
    # derives it from the id. Setting the flag alone is exactly the case a
    # derived implementation would get wrong, so it is the honest fixture for
    # testing a filter that must read the stored flag.
    ids["txn_adj"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_public"],
        transaction_type_code=ids["type_adj"],
        trade_date=TODAY - timedelta(days=3), authority="internal",
        source_system="manual", quantity=ADJ_QTY, currency_code="USD",
        is_corporate_action_adjustment=True,
    )
    # A second one, on the private position, so "adjustments only" is not
    # trivially "everything on one position".
    ids["txn_adj2"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_private"],
        transaction_type_code=ids["type_adj"],
        trade_date=TODAY - timedelta(days=2), authority="internal",
        source_system="manual", net_amount=Decimal("1.00"),
        currency_code="USD", is_corporate_action_adjustment=True,
    )
    # A private-markets call on the private position — gives the type filter a
    # second code to narrow to and proves the market rule cuts both ways.
    ids["txn_call"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_private"],
        transaction_type_code=ids["type_call"],
        trade_date=TODAY - timedelta(days=15), authority="stated",
        source_system="manual", gross_amount=Decimal("50000.00"),
        net_amount=Decimal("50000.00"), currency_code="USD",
    )

    ids["txn_b"] = await record_transaction(
        conn, org_id=OTHER_ORG_ID, position_id=ids["pos_b"],
        transaction_type_code=ids["type_buy"],
        trade_date=TODAY - timedelta(days=8), authority="custodial",
        source_system="manual", quantity=Decimal("7.00"),
        price=Decimal("3.00"), net_amount=Decimal("21.00"), currency_code="USD",
    )

    # ── One document, linked to the BUY transaction via Phase D's table ──
    ids["document"] = str(await conn.fetchval(
        """
        INSERT INTO public.documents
            (org_id, original_filename, source, mime_type, status, doc_family)
        VALUES ($1::uuid, $2, 'upload', 'application/pdf', 'confirmed',
                'statement')
        RETURNING id
        """,
        DEFAULT_ORG_ID, f"{TAG} trade-confirm.pdf",
    ))
    await conn.execute(
        f"""
        INSERT INTO {TABLE_DOC_RECORD_LINKS}
            (document_id, org_id, record_type, record_id)
        VALUES ($1::uuid, $2::uuid, $3, $4::uuid)
        ON CONFLICT DO NOTHING
        """,
        ids["document"], DEFAULT_ORG_ID, RECORD_TYPE_TRANSACTION, ids["txn_buy"],
    )

    ids["org_a_txns"] = {
        ids["txn_buy"], ids["txn_sell"], ids["txn_fee"], ids["txn_adj"],
        ids["txn_adj2"], ids["txn_call"],
    }
    ids["org_a_adjustments"] = {ids["txn_adj"], ids["txn_adj2"]}
    ids["org_a_trades"] = ids["org_a_txns"] - ids["org_a_adjustments"]
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# TASKS 2 / 3 / 4 / 5 — the endpoints, driven through the REAL ASGI app
# ═══════════════════════════════════════════════════════════════════════════


def _client(org_id: str, sub: str):
    """A TestClient whose token validation is stubbed to a REAL org's claims.

    ``verify_token`` is replaced, not the auth dependency: the request still
    passes through the RLS context middleware, the active-account gate and
    ``require_permission`` exactly as production does. Stubbing further up would
    skip the layers most likely to be wrong.
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
        if p.startswith("/api/v1/portfolio/transaction")
    }


def endpoint_tests(ids: dict, direct: dict) -> dict:
    """Drive every new endpoint. Returns state the async checks need back."""
    hdr = {"Authorization": "Bearer stub"}
    out: dict = {}

    routes = _routes_declared()
    check(
        "[Y] 2 the new REST endpoints are declared on the real app: list / "
        "create / detail / correct, plus the position picker",
        routes.get("/api/v1/portfolio/transactions") == ["get", "post"]
        and routes.get("/api/v1/portfolio/transactions/{transaction_id}") == ["get"]
        and routes.get(
            "/api/v1/portfolio/transactions/{transaction_id}/corrections"
        ) == ["post"]
        and routes.get("/api/v1/portfolio/transaction-positions") == ["get"],
        json.dumps(routes),
    )
    check(
        "[Y] 2 there is NO PATCH on a transaction — the ledger is append-only "
        "and the URL says so",
        "patch" not in routes.get(
            "/api/v1/portfolio/transactions/{transaction_id}", []
        ),
        "detail route declares GET only",
    )

    with _client(DEFAULT_ORG_ID, A_SUB) as c:
        # ── Listing: REAL rows, org-scoped ──────────────────────────────
        r_all = c.get("/api/v1/portfolio/transactions", headers=hdr,
                      params={"search": TAG})
        if r_all.status_code != 200:
            check("[Y] 3 GET /portfolio/transactions returns 200", False,
                  f"{r_all.status_code}: {r_all.text[:300]}")
            return out
        body = r_all.json()
        all_ids = {t["id"] for t in body["transactions"]}
        check(
            "[Y] 3 the grid endpoint returns REAL rows — every fixture "
            "transaction in this org, and nothing else",
            all_ids == ids["org_a_txns"],
            f"got={len(all_ids)} expected={len(ids['org_a_txns'])} "
            f"missing={sorted(ids['org_a_txns'] - all_ids)} "
            f"extra={sorted(all_ids - ids['org_a_txns'])}",
        )
        check(
            "[Y] 3 the endpoint body matches a DIRECT SQL read of the same "
            "predicate — a stub returning a plausible-looking list would fail "
            "this, not just a count check",
            all_ids == direct["all_ids"],
            f"api={len(all_ids)} sql={len(direct['all_ids'])} "
            f"diff={sorted(all_ids ^ direct['all_ids'])}",
        )
        out["list_body"] = body

        # ── Joined display fields + server-resolved LABEL (Rule 1) ───────
        buy_row = next(t for t in body["transactions"] if t["id"] == ids["txn_buy"])
        check(
            "[Y] 3 rows carry the joined asset name, owner name and the "
            "server-resolved transaction-type LABEL — the grid hardcodes none "
            "of them, and shows the label, never the raw code",
            buy_row["asset_name"] == f"{TAG} Listed Equity"
            and buy_row["owner_name"] == f"{TAG} Alpha Trust"
            and buy_row["transaction_type_label"]
            and buy_row["transaction_type_label"] != buy_row["transaction_type_code"]
            and buy_row["amount_basis"] in ("units", "currency"),
            f"asset={buy_row['asset_name']!r} owner={buy_row['owner_name']!r} "
            f"label={buy_row['transaction_type_label']!r} "
            f"basis={buy_row['amount_basis']!r}",
        )
        check(
            "[Y] 3 the type vocabulary ships WITH the page (Rule 1) so the "
            "type filter and the type column need no second round-trip and no "
            "hardcoded list",
            len(body.get("transaction_types", [])) >= 10
            and all("label" in t and "market" in t and "amount_basis" in t
                    for t in body["transaction_types"]),
            f"{len(body.get('transaction_types', []))} types shipped",
        )

        # ── Money is exact decimal STRINGS, lexical ≠ numeric order ──────
        nets = [t["net_amount"] for t in body["transactions"]
                if t["net_amount"] is not None]
        all_strings = all(isinstance(v, str) for v in nets)
        sample = [str(BUY_NET), str(SELL_NET), str(FEE_NET)]
        check(
            "[Y] 3 figures cross the API as exact decimal STRINGS (never "
            "floats), and this fixture set proves LEXICAL ordering would be "
            "WRONG — so the grid's derived numeric sort keys are load-bearing",
            all_strings and sorted(sample) != sorted(sample, key=float),
            f"lexical={sorted(sample)} numeric={sorted(sample, key=float)}",
        )

        # ══ THE ADJUSTMENT FILTER — asserted as a PARTITION ══════════════
        r_adj = c.get("/api/v1/portfolio/transactions", headers=hdr,
                      params={"search": TAG,
                              "is_corporate_action_adjustment": "true"})
        r_trade = c.get("/api/v1/portfolio/transactions", headers=hdr,
                        params={"search": TAG,
                                "is_corporate_action_adjustment": "false"})
        adj_ids = {t["id"] for t in r_adj.json()["transactions"]}
        trade_ids = {t["id"] for t in r_trade.json()["transactions"]}
        check(
            "[Y] 5 is_corporate_action_adjustment=true NARROWS to exactly the "
            "adjustment rows — non-empty AND a strict subset, so a filter "
            "returning everything and a filter returning nothing both fail",
            adj_ids and adj_ids < all_ids and adj_ids == ids["org_a_adjustments"],
            f"adjustments={len(adj_ids)} of {len(all_ids)}; "
            f"expected={sorted(ids['org_a_adjustments'])}",
        )
        check(
            "[Y] 5 is_corporate_action_adjustment=false NARROWS to exactly the "
            "ordinary trades — this is the realized-gain population and a "
            "DIFFERENT question from 'no filter', which is why it has to be "
            "expressible at all",
            trade_ids and trade_ids < all_ids and trade_ids == ids["org_a_trades"],
            f"trades={len(trade_ids)} of {len(all_ids)}; "
            f"expected={sorted(ids['org_a_trades'])}",
        )
        check(
            "[Y] 5 the two states PARTITION the unfiltered set: disjoint, and "
            "their union is exactly everything. A broken filter cannot satisfy "
            "both directions plus the partition",
            not (adj_ids & trade_ids) and (adj_ids | trade_ids) == all_ids,
            f"overlap={sorted(adj_ids & trade_ids)} "
            f"union={len(adj_ids | trade_ids)} vs all={len(all_ids)}",
        )
        check(
            "[Y] 5 both filtered sets match the same predicate run directly in "
            "SQL, element by element",
            adj_ids == direct["adjustment_ids"]
            and trade_ids == direct["trade_ids"],
            f"adj diff={sorted(adj_ids ^ direct['adjustment_ids'])} "
            f"trade diff={sorted(trade_ids ^ direct['trade_ids'])}",
        )

        # ══ THE TYPE FILTER ══════════════════════════════════════════════
        r_type = c.get("/api/v1/portfolio/transactions", headers=hdr,
                       params={"search": TAG,
                               "transaction_type_code": ids["type_buy"]})
        type_ids = {t["id"] for t in r_type.json()["transactions"]}
        check(
            "[Y] 5 the transaction_type_code filter NARROWS correctly — "
            "non-empty, a strict subset, equal to the same predicate in SQL, "
            "and every returned row really carries that code",
            type_ids
            and type_ids < all_ids
            and type_ids == direct["buy_ids"]
            and all(t["transaction_type_code"] == ids["type_buy"]
                    for t in r_type.json()["transactions"]),
            f"code={ids['type_buy']!r} filtered={len(type_ids)} of "
            f"{len(all_ids)}; sql={len(direct['buy_ids'])}",
        )
        r_type2 = c.get("/api/v1/portfolio/transactions", headers=hdr,
                        params={"search": TAG,
                                "transaction_type_code": ids["type_call"]})
        type2_ids = {t["id"] for t in r_type2.json()["transactions"]}
        check(
            "[Y] 5 a SECOND type narrows to a DIFFERENT, disjoint set — one "
            "filter value that happened to work could be a coincidence",
            type2_ids == {ids["txn_call"]} and not (type2_ids & type_ids),
            f"{ids['type_call']!r} → {sorted(type2_ids)}",
        )

        # ── The other filters, each with a control ──────────────────────
        r_pos = c.get("/api/v1/portfolio/transactions", headers=hdr,
                      params={"position_id": ids["pos_public"]})
        pos_ids = {t["id"] for t in r_pos.json()["transactions"]}
        check(
            "[Y] 3 the position_id filter narrows to that position's ledger "
            "and nothing else",
            pos_ids == {ids["txn_buy"], ids["txn_sell"], ids["txn_fee"],
                        ids["txn_adj"]},
            f"got={len(pos_ids)}",
        )
        r_owner = c.get("/api/v1/portfolio/transactions", headers=hdr,
                        params={"search": TAG,
                                "owner_entity_id": ids["owner_a2"]})
        owner_ids = {t["id"] for t in r_owner.json()["transactions"]}
        check(
            "[Y] 3 the owner filter (EntityPicker's value) narrows across "
            "positions — the join reaches through position → entity",
            owner_ids == {ids["txn_call"], ids["txn_adj2"]},
            f"got={sorted(owner_ids)}",
        )
        r_date = c.get("/api/v1/portfolio/transactions", headers=hdr,
                       params={"search": TAG,
                               "trade_from": (TODAY - timedelta(days=6)).isoformat()})
        date_ids = {t["id"] for t in r_date.json()["transactions"]}
        check(
            "[Y] 3 the trade-date range filter narrows and excludes the older "
            "entries",
            date_ids == {ids["txn_fee"], ids["txn_adj"], ids["txn_adj2"]},
            f"got={len(date_ids)} of {len(all_ids)}",
        )
        r_src = c.get("/api/v1/portfolio/transactions", headers=hdr,
                      params={"search": TAG, "source_system": "manual"})
        src_ids = {t["id"] for t in r_src.json()["transactions"]}
        check(
            "[Y] 3 the source_system filter narrows and matches SQL",
            src_ids and src_ids < all_ids and src_ids == direct["manual_ids"],
            f"filtered={len(src_ids)} sql={len(direct['manual_ids'])}",
        )

        # ── The right pane ──────────────────────────────────────────────
        r_det = c.get(f"/api/v1/portfolio/transactions/{ids['txn_buy']}",
                      headers=hdr)
        det = r_det.json()
        out["detail"] = det
        check(
            "[Y] 4 the detail endpoint returns the entry, its position and its "
            "correction chain in ONE call",
            r_det.status_code == 200
            and det["transaction"]["id"] == ids["txn_buy"]
            and det["position"] is not None
            and isinstance(det.get("correction_history"), list),
            f"status={r_det.status_code} "
            f"keys={sorted(det.keys()) if r_det.status_code == 200 else ''}",
        )
        check(
            "[Y] 4 the pane LINKS BACK to the owning position, and carries the "
            "CURRENT position id alongside the one the entry is attached to — "
            "the Positions grid hides closed rows, so linking a restated "
            "position's old id would land on nothing",
            det["position"]["id"] == ids["pos_public"]
            and "current_position_id" in det["position"]
            and det["position"]["current_position_id"] == ids["pos_public"]
            and det["position"]["asset_name"] == f"{TAG} Listed Equity",
            f"position={det['position']['id'][:8]}… "
            f"current={str(det['position']['current_position_id'])[:8]}…",
        )
        check(
            "[Y] 4 figures reach the pane as exact decimal strings, unrounded",
            det["transaction"]["quantity"] == str(BUY_QTY)
            and det["transaction"]["price"] == str(BUY_PRICE)
            and det["transaction"]["net_amount"] == str(BUY_NET),
            f"qty={det['transaction']['quantity']!r} "
            f"net={det['transaction']['net_amount']!r}",
        )

        # ── The linked document, through the REAL Phase-9 panel endpoint ──
        record_type = det["document_record_type"]
        r_doc = c.get(
            f"/api/v1/records/{record_type}/{ids['txn_buy']}/documents",
            headers=hdr,
        )
        docs = r_doc.json().get("documents", [])
        check(
            "[Y] 4 the linked source document is reachable from the pane "
            "through the REAL existing document-linking endpoint, with the "
            "record_type supplied BY THE API rather than hardcoded",
            r_doc.status_code == 200
            and {d["document_id"] for d in docs} == {ids["document"]}
            and record_type == RECORD_TYPE_TRANSACTION,
            f"record_type={record_type!r} docs={len(docs)}",
        )

        # ── The adjustment carries its Phase-F marker out to the client ──
        r_adj_det = c.get(f"/api/v1/portfolio/transactions/{ids['txn_adj']}",
                          headers=hdr)
        adj_det = r_adj_det.json()
        check(
            "[Y] 3 an adjustment row is flagged in the API payload, so the "
            "grid can mark it — Phase F requires it never look like a trade",
            adj_det["transaction"]["is_corporate_action_adjustment"] is True
            and next(t for t in body["transactions"]
                     if t["id"] == ids["txn_adj"]
                     )["is_corporate_action_adjustment"] is True,
            "flag present on both the list row and the detail",
        )

        # ── Writes: the create endpoint and its refusals ────────────────
        before = len(c.get("/api/v1/portfolio/transactions", headers=hdr,
                           params={"search": TAG}).json()["transactions"])

        # Phase E, at the API boundary: a private-markets type against a
        # PUBLIC-markets asset.
        r_market = c.post(
            "/api/v1/portfolio/transactions", headers=hdr,
            json={
                "position_id": ids["pos_public"],
                "transaction_type_code": ids["type_call"],
                "trade_date": TODAY.isoformat(),
                "authority": "manual", "source_system": "manual",
                "net_amount": "100.00",
            },
        )
        after = len(c.get("/api/v1/portfolio/transactions", headers=hdr,
                          params={"search": TAG}).json()["transactions"])
        check(
            "[Y] 2 POST refuses a market-incompatible type with 422 AND writes "
            "nothing — the count is unchanged, so the refusal is the Phase-E "
            "rule and not a broken statement",
            r_market.status_code == 422 and before == after
            and "markets" in r_market.text,
            f"status={r_market.status_code} count {before}→{after}: "
            f"{r_market.text[:120]}",
        )
        check(
            "[Y] 2 an org_id in the request body is REFUSED (422), not ignored "
            "— there is no field for a caller to send",
            c.post("/api/v1/portfolio/transactions", headers=hdr, json={
                "org_id": OTHER_ORG_ID,
                "position_id": ids["pos_public"],
                "transaction_type_code": ids["type_buy"],
                "trade_date": TODAY.isoformat(),
                "authority": "manual", "source_system": "manual",
            }).status_code == 422,
            "extra='forbid' on TransactionCreate",
        )
        check(
            "[Y] 2 a figure sent as a JSON FLOAT is refused (422) rather than "
            "coerced — the refusal runs mode='before', ahead of Pydantic's own "
            "float→Decimal coercion, or it would be dead code",
            c.post("/api/v1/portfolio/transactions", headers=hdr, json={
                "position_id": ids["pos_public"],
                "transaction_type_code": ids["type_buy"],
                "trade_date": TODAY.isoformat(),
                "authority": "manual", "source_system": "manual",
                "net_amount": 1234.56,
            }).status_code == 422,
            "float body rejected",
        )
        check(
            "[Y] 2 corporate-action fields are NOT settable through the API — "
            "a hand-written row claiming an action would break the idempotency "
            "key that stops apply_corporate_action running twice",
            c.post("/api/v1/portfolio/transactions", headers=hdr, json={
                "position_id": ids["pos_public"],
                "transaction_type_code": ids["type_buy"],
                "trade_date": TODAY.isoformat(),
                "authority": "manual", "source_system": "manual",
                "is_corporate_action_adjustment": True,
            }).status_code == 422,
            "is_corporate_action_adjustment rejected by extra='forbid'",
        )

        r_new = c.post(
            "/api/v1/portfolio/transactions", headers=hdr,
            json={
                "position_id": ids["pos_public"],
                "transaction_type_code": ids["type_buy"],
                "trade_date": TODAY.isoformat(),
                "authority": "manual", "source_system": "manual",
                "quantity": "10.00", "price": "13.50",
                "gross_amount": "135.00", "net_amount": "135.00",
                "currency_code": "USD",
                "external_ref": f"{TAG}-NEW-001",
            },
        )
        created = r_new.json() if r_new.status_code == 201 else {}
        check(
            "[Y] 2 POST records a real transaction and returns its full "
            "detail, with the exact figures it was given",
            r_new.status_code == 201
            and created.get("transaction", {}).get("net_amount") == "135.00"
            and created.get("transaction", {}).get("price") == "13.50"
            and created.get("transaction", {})
                       .get("is_corporate_action_adjustment") is False,
            f"status={r_new.status_code} "
            f"net={created.get('transaction', {}).get('net_amount')!r}",
        )
        if created:
            out["created_id"] = created["transaction"]["id"]

        # ══ THE CORRECTION — Task 1d's real mechanism ════════════════════
        r_corr = c.post(
            f"/api/v1/portfolio/transactions/{ids['txn_buy']}/corrections",
            headers=hdr,
            json={"net_amount": str(CORRECTED_NET),
                  "settle_date": TODAY.isoformat()},
        )
        corrected = r_corr.json() if r_corr.status_code == 201 else {}
        new_id = corrected.get("transaction", {}).get("id")
        out["corrected_id"] = new_id
        check(
            "[Y] 5 a correction round-trips through the real endpoint and "
            "returns a DIFFERENT transaction id with 201 — it MINTS a row, it "
            "does not update one",
            r_corr.status_code == 201
            and new_id is not None
            and new_id != ids["txn_buy"]
            and corrected.get("corrected_from") == ids["txn_buy"],
            f"status={r_corr.status_code} "
            f"{str(ids['txn_buy'])[:8]}… → {str(new_id)[:8]}…",
        )
        check(
            "[Y] 5 the successor points BACK at the row it corrects — the "
            "chain is explicit and referentially enforced by the self-FK, not "
            "reconstructed from timestamps",
            corrected.get("transaction", {}).get("corrects_transaction_id")
            == ids["txn_buy"],
            f"corrects={str(corrected.get('transaction', {}).get('corrects_transaction_id'))[:8]}…",
        )
        # A fresh read, not the write's own response.
        r_after = c.get("/api/v1/portfolio/transactions", headers=hdr,
                        params={"search": TAG})
        after_rows = {t["id"]: t for t in r_after.json()["transactions"]}
        check(
            "[Y] 5 the correction PERSISTS — a fresh list read shows the new "
            "figures on the successor and the original is gone from the "
            "current set",
            new_id in after_rows
            and after_rows[new_id]["net_amount"] == str(CORRECTED_NET)
            and after_rows[new_id]["settle_date"] == TODAY.isoformat()
            and ids["txn_buy"] not in after_rows,
            f"successor net={after_rows.get(new_id, {}).get('net_amount')!r}, "
            f"original present={ids['txn_buy'] in after_rows}",
        )
        check(
            "[Y] 5 the ORIGINAL row is still REACHABLE through the API — a "
            "direct GET on the old id returns it, flagged as superseded. A row "
            "that exists but that no endpoint returns is not reachable",
            (lambda r: r.status_code == 200
             and r.json()["transaction"]["id"] == ids["txn_buy"]
             and r.json()["transaction"]["is_current"] is False
             and r.json()["transaction"]["net_amount"] == str(BUY_NET))(
                c.get(f"/api/v1/portfolio/transactions/{ids['txn_buy']}",
                      headers=hdr)
            ),
            "GET on the original id returns it with its ORIGINAL net amount",
        )
        check(
            "[Y] 5 include_history=true surfaces the superseded original in "
            "the LIST too — it is history, not a deletion",
            ids["txn_buy"] in {
                t["id"] for t in c.get(
                    "/api/v1/portfolio/transactions", headers=hdr,
                    params={"search": TAG, "include_history": "true"},
                ).json()["transactions"]
            },
            "original reachable with include_history",
        )
        r_chain = c.get(f"/api/v1/portfolio/transactions/{new_id}", headers=hdr)
        chain = r_chain.json()["correction_history"]
        check(
            "[Y] 5 the pane's correction chain shows BOTH versions, oldest "
            "first, with exactly one current — this is what makes the "
            "correction legible after the fact",
            len(chain) == 2
            and chain[0]["id"] == ids["txn_buy"]
            and chain[1]["id"] == new_id
            and [v["is_current"] for v in chain] == [False, True]
            and chain[0]["net_amount"] == str(BUY_NET),
            f"chain={[str(v['id'])[:8] for v in chain]}",
        )
        # THE BUG THE CORRECTION INTRODUCES, asserted directly.
        r_doc2 = c.get(
            f"/api/v1/records/{RECORD_TYPE_TRANSACTION}/{new_id}/documents",
            headers=hdr,
        )
        check(
            "[Y] 5 the source document is STILL reachable after the correction "
            "— a correction mints a new record_id, so without an explicit link "
            "carry-over the first settle-date fix would orphan the evidence",
            r_doc2.status_code == 200
            and {d["document_id"] for d in r_doc2.json()["documents"]}
                == {ids["document"]},
            f"docs on successor={len(r_doc2.json().get('documents', []))}",
        )
        check(
            "[Y] 5 the ORIGINAL keeps its links too — the copy is a COPY, so "
            "the historical entry stays exactly as auditable as before",
            {d["document_id"] for d in c.get(
                f"/api/v1/records/{RECORD_TYPE_TRANSACTION}/{ids['txn_buy']}"
                f"/documents", headers=hdr).json()["documents"]}
            == {ids["document"]},
            "document reachable on BOTH the closed row and the successor",
        )

        # ── Corrections that must be refused ────────────────────────────
        check(
            "[Y] 2 a correction that re-types an entry into an incompatible "
            "market is REFUSED (422) — the Phase-E check runs on the "
            "CORRECTION, not only on the original insert",
            (lambda r: r.status_code == 422 and "markets" in r.text)(
                c.post(f"/api/v1/portfolio/transactions/{new_id}/corrections",
                       headers=hdr,
                       json={"transaction_type_code": ids["type_call"]})
            ),
            "private-markets type on a public-markets asset",
        )
        check(
            "[Y] 2 a refused correction leaves the entry OPEN — a rejected "
            "correction must not delete a ledger entry from every current read",
            c.get(f"/api/v1/portfolio/transactions/{new_id}", headers=hdr)
             .json()["transaction"]["is_current"] is True,
            "successor still current after the refusal",
        )
        check(
            "[Y] 2 a field outside the correctable set is refused (422) rather "
            "than silently ignored — including position_id and the two "
            "corporate-action columns",
            all(
                c.post(f"/api/v1/portfolio/transactions/{new_id}/corrections",
                       headers=hdr, json=payload).status_code == 422
                for payload in (
                    {"position_id": ids["pos_private"]},
                    {"is_corporate_action_adjustment": True},
                    {"corporate_action_id": None},
                    {"related_transaction_id": ids["txn_sell"]},
                )
            ),
            "position_id / both corporate-action fields / "
            "related_transaction_id all refused",
        )
        check(
            "[Y] 2 an EMPTY correction is refused (400) — a no-op that minted "
            "a row would put a version in the chain that changed nothing",
            c.post(f"/api/v1/portfolio/transactions/{new_id}/corrections",
                   headers=hdr, json={}).status_code == 400,
            "empty body refused",
        )
        check(
            "[Y] 5 correcting an ALREADY-SUPERSEDED entry is refused — "
            "branching the ledger off a closed row would leave two current "
            "successors for one entry",
            c.post(
                f"/api/v1/portfolio/transactions/{ids['txn_buy']}/corrections",
                headers=hdr, json={"net_amount": "1.00"},
            ).status_code == 400,
            "correction on the closed original refused",
        )

        # ── The picker feeding the create form ──────────────────────────
        r_pick = c.get("/api/v1/portfolio/transaction-positions", headers=hdr,
                       params={"search": TAG})
        pick_ids = {p["id"] for p in r_pick.json()["positions"]}
        check(
            "[Y] 2 the position picker returns this org's CURRENT positions "
            "with the valuation_method the market rule keys on",
            r_pick.status_code == 200
            and {ids["pos_public"], ids["pos_private"]} <= pick_ids
            and ids["pos_b"] not in pick_ids
            and all("valuation_method" in p for p in r_pick.json()["positions"]),
            f"picker returned {len(pick_ids)} positions",
        )

    # ── Cross-org, at the ENDPOINT layer ────────────────────────────────
    with _client(OTHER_ORG_ID, B_SUB) as c:
        r = c.get("/api/v1/portfolio/transactions", headers=hdr,
                  params={"search": TAG})
        b_ids = {t["id"] for t in r.json()["transactions"]}
        a_ids = (ids["org_a_txns"] | {out.get("corrected_id"),
                                      out.get("created_id")}) - {None}
        check(
            "[Y] 5 cross-org (endpoint): org B's list CONTAINS its own "
            "transaction and NONE of org A's — asserted in both directions, so "
            "an endpoint returning nothing to everybody would FAIL",
            ids["txn_b"] in b_ids and not (b_ids & a_ids),
            f"orgB sees {len(b_ids)} rows; leaked org-A rows="
            f"{sorted(b_ids & a_ids)}",
        )
        check(
            "[Y] 5 cross-org (endpoint): org B fetching an org-A transaction "
            "id gets 404 — not 403, which would confirm the id exists elsewhere",
            c.get(f"/api/v1/portfolio/transactions/{ids['txn_sell']}",
                  headers=hdr).status_code == 404,
            "detail 404 across orgs",
        )
        check(
            "[Y] 5 cross-org (endpoint): org B cannot CORRECT an org-A "
            "transaction — the write path is scoped too, not just the reads",
            c.post(
                f"/api/v1/portfolio/transactions/{ids['txn_sell']}/corrections",
                headers=hdr, json={"net_amount": "1.00"},
            ).status_code == 400,
            "correction across orgs refused",
        )
        check(
            "[Y] 5 cross-org: org B cannot read the document linked to an "
            "org-A transaction",
            (lambda r: r.status_code == 200 and r.json().get("documents") == [])(
                c.get(f"/api/v1/records/{RECORD_TYPE_TRANSACTION}/"
                      f"{ids['txn_buy']}/documents", headers=hdr)
            ),
            "no documents leak across orgs",
        )
    return out


# ═══════════════════════════════════════════════════════════════════════════
# TASKS 3 / 4 — the FRONTEND is wired to the real endpoints, not to mocks
# ═══════════════════════════════════════════════════════════════════════════


def _strip_js_comments(src: str) -> str:
    """Remove // and /* */ comments so a scan reads executable code only.

    Crude by design — it does not parse JS. Only ever used to make an ABSENCE
    assertion stricter, never to prove something is present.
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
    grid_path = os.path.join(_WEB, "components", "portfolio", "TransactionsGrid.jsx")
    pane_path = os.path.join(_WEB, "components", "portfolio",
                             "TransactionDetailPane.jsx")
    page_path = os.path.join(_WEB, "app", "portfolio", "transactions", "page.js")
    route_list = os.path.join(_WEB, "app", "api", "portfolio", "transactions",
                              "route.js")
    route_one = os.path.join(_WEB, "app", "api", "portfolio", "transactions",
                             "[transactionId]", "route.js")
    route_corr = os.path.join(_WEB, "app", "api", "portfolio", "transactions",
                              "[transactionId]", "corrections", "route.js")

    for label, path in (
        ("TransactionsGrid.jsx", grid_path),
        ("TransactionDetailPane.jsx", pane_path),
        ("app/portfolio/transactions/page.js", page_path),
        ("app/api/portfolio/transactions/route.js", route_list),
        ("app/api/portfolio/transactions/[transactionId]/route.js", route_one),
        ("app/api/portfolio/transactions/[transactionId]/corrections/route.js",
         route_corr),
    ):
        check(f"[Y] 3 {label} exists", os.path.exists(path), path)

    grid = read(grid_path)
    pane = read(pane_path)
    page = read(page_path)
    rl, ro, rc = read(route_list), read(route_one), read(route_corr)

    check(
        "[Y] 3 the grid is driven by the SHARED DataGrid, not a new grid "
        "library",
        'from "@/components/ui/DataGrid"' in grid,
        "imports @/components/ui/DataGrid",
    )
    check(
        "[Y] 2 the frontend calls the REAL endpoints — the grid fetches "
        "/api/portfolio/transactions and POSTs to .../corrections",
        "/api/portfolio/transactions?" in grid
        and "/corrections" in grid
        and 'method: "POST"' in grid,
        "list fetch + correction POST present",
    )
    check(
        "[Y] 2 the pane loads and corrects through the same real endpoints",
        "/api/portfolio/transactions/${transactionId}" in pane
        and "/corrections" in pane,
        "detail GET + correction POST present",
    )
    # A mock would be an array literal of transaction-shaped objects.
    mocked = re.search(
        r"(MOCK|STUB|FAKE|SAMPLE)_?(TRANSACTIONS|ROWS|DATA)", grid, re.IGNORECASE
    ) or re.search(r"transaction_type_label\s*:\s*[\"']", grid)
    check(
        "[Y] 2 the grid contains NO mock/stub row data — every row it renders "
        "came from the API",
        mocked is None,
        f"suspect literal: {mocked.group(0)!r}" if mocked else "none found",
    )
    # Comments are stripped before the org_id scan: all three route files
    # EXPLAIN in a comment that org_id travels in the token, and a naive text
    # search flags the explanation — the false positive that trains the next
    # person to delete the check rather than the bug.
    codes = [_strip_js_comments(s) for s in (rl, ro, rc)]
    check(
        "[Y] 3 all three Next.js API routes forward to FastAPI (Rule 5: the "
        "browser never calls FastAPI directly) and no executable line in any "
        "of them reads, sets or forwards an org_id",
        all("forwardToApi" in c for c in codes)
        and all("org_id" not in c for c in codes)
        and "/api/v1/portfolio/transactions" in codes[0]
        and "/corrections" in codes[2],
        "forwardToApi in all three; org_id absent from every executable line",
    )
    check(
        "[Y] 2 the detail route declares NO PATCH — an append-only ledger has "
        "no in-place edit, and the route file says so by omission",
        "PATCH" not in _strip_js_comments(ro)
        and "export async function GET" in ro,
        "GET only on [transactionId]/route.js",
    )
    check(
        "[Y] 3 the page renders the grid inside AppShell behind a host-aware "
        "session check",
        "TransactionsGrid" in page and "getHostSession" in page
        and "AppShell" in page,
        "getHostSession + AppShell + TransactionsGrid",
    )
    check(
        "[Y] 3 selecting a row opens the pane in place — the row click sets "
        "selection, it does not navigate",
        "onRowClick" in grid and "setSelectedId" in grid
        and "router.push" not in grid,
        "onRowClick → setSelectedId; no router.push in the grid",
    )
    check(
        "[Y] 3 inline editing is limited to what the SERVER publishes as safe "
        "— the component reads vocabularies.inline_correctable rather than "
        "keeping its own list that could drift",
        "inline_correctable" in grid and "inlineCorrectable.has" in grid,
        f"server list = {sorted(INLINE_CORRECTABLE_FIELDS)}",
    )
    check(
        "[Y] 3 the validated fields are NOT inline-editable — everything a "
        "correction can be REFUSED for goes through the pane, which has room "
        "to show why",
        not (INLINE_CORRECTABLE_FIELDS & {
            "transaction_type_code", "trade_date", "quantity", "price",
            "gross_amount", "net_amount", "fees", "taxes", "authority",
            "source_system",
        })
        and INLINE_CORRECTABLE_FIELDS < CORRECTABLE_FIELDS,
        f"inline={sorted(INLINE_CORRECTABLE_FIELDS)} "
        f"pane-only={sorted(CORRECTABLE_FIELDS - INLINE_CORRECTABLE_FIELDS)}",
    )
    # Phase F's visual requirement, asserted on the artefacts that implement it.
    check(
        "[Y] 3 a corporate-action adjustment is marked THREE ways so it cannot "
        "read as an ordinary trade (Phase F): a row-level gold wash, a "
        "dedicated Kind cell, and a pill beside the type label",
        "getRowStyle" in grid
        and "ADJUSTMENT_WASH" in grid
        and "function KindCell" in grid
        and "corp. action" in grid
        and "is_corporate_action_adjustment" in grid,
        "row wash + KindCell + type pill",
    )
    check(
        "[Y] 3 the adjustment filter is a first-class TRI-STATE control, and "
        "the 'false' state survives the query builder — a truthiness test "
        "would drop 'trades only' and silently widen the query to everything",
        'value="false"' in grid
        and 'value="true"' in grid
        and 'if (value !== "") params.set(key, value)' in grid,
        "three options; the filter builder tests against \"\", not truthiness",
    )
    rl_code = _strip_js_comments(rl)
    check(
        "[Y] 3 the Next.js route forwards the adjustment filter verbatim and "
        "does NOT coerce it — the same 'false' trap, one layer down",
        '"is_corporate_action_adjustment"' in rl_code
        and 'value !== null && value !== ""' in rl_code,
        "allow-listed and passed through by string comparison",
    )
    check(
        "[Y] 4 the pane embeds the REAL existing DocumentsPanel and takes the "
        "record_type from the API response rather than hardcoding the string",
        'from "@/components/DocumentsPanel"' in pane
        and "data.document_record_type" in pane
        and f'"{RECORD_TYPE_TRANSACTION}"' not in pane,
        "recordType={data.document_record_type}",
    )
    check(
        "[Y] 4 the pane LINKS THROUGH to the Positions screen, using the "
        "CURRENT position id rather than the possibly-closed row the entry is "
        "attached to",
        "/portfolio/positions?position=" in pane
        and "current_position_id" in pane,
        "positionHref built from current_position_id",
    )
    check(
        "[Y] 4 the pane renders the correction chain and disables the form on "
        "a superseded entry — correcting history in place is the one thing an "
        "append-only ledger must never allow",
        "correction_history" in pane and "!txn.is_current" in pane,
        "Versions section + is_current gate on the Correct button",
    )
    check(
        "[Y] 3 the grid sorts money NUMERICALLY, not lexically — derived sort "
        "keys are built from the exact decimal strings",
        "_net" in grid and "_amount" in grid and "function num(" in grid,
        "_net / _amount derived from num()",
    )
    check(
        "[Y] 5 the grid adopts the SUCCESSOR id after a correction — a client "
        "that kept the id it sent would be reading history",
        "updated.id" in grid and "detail.corrected_from" in grid,
        "row id swapped from the correction response",
    )
    # The shared grid change, asserted as additive.
    dg = read(os.path.join(_WEB, "components", "ui", "DataGrid.jsx"))
    check(
        "[Y] 3 the DataGrid change is purely ADDITIVE — getRowStyle defaults "
        "to undefined, so the three existing consumers are untouched, and it "
        "can only affect styling",
        "getRowStyle," in dg
        and "getRowStyle ? getRowStyle(row.original) : undefined" in dg,
        "optional prop, undefined default, style-only",
    )
    consumers = sorted(
        os.path.relpath(p, _WEB)
        for p in glob.glob(os.path.join(_WEB, "components", "**", "*.jsx"),
                           recursive=True)
        if "ui/DataGrid" in read(p) and not p.endswith("DataGrid.jsx")
    )
    check(
        "[Y] 3 the pre-existing DataGrid consumers were checked, not assumed — "
        "none of them passes getRowStyle, so none changes behaviour",
        not any("getRowStyle" in read(os.path.join(_WEB, rel))
                for rel in consumers
                if not rel.endswith("TransactionsGrid.jsx")),
        f"consumers={consumers}",
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
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-8:]
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
    base = f"""
        FROM {TABLE_TRANSACTIONS} t
        JOIN {TABLE_POSITIONS} p ON p.id = t.position_id
        JOIN {TABLE_ASSETS} a ON a.id = p.asset_id
        WHERE t.org_id = $1::uuid AND a.name LIKE $2
          AND t.valid_to IS NULL AND t.system_to IS NULL
    """

    async def q(extra: str, *args):
        rows = await conn.fetch(
            f"SELECT t.id::text AS id {base} {extra}",
            DEFAULT_ORG_ID, f"{TAG}%", *args,
        )
        return {r["id"] for r in rows}

    return {
        "all_ids": await q(""),
        "adjustment_ids": await q("AND t.is_corporate_action_adjustment"),
        "trade_ids": await q("AND NOT t.is_corporate_action_adjustment"),
        "buy_ids": await q("AND t.transaction_type_code = $3", ids["type_buy"]),
        "manual_ids": await q("AND t.source_system = 'manual'"),
    }


async def check_correction_rows(conn, ids: dict, corrected_id: str) -> None:
    """Exactly two rows, exactly one current, the closed one UNCHANGED."""
    rows = await conn.fetch(
        f"""
        SELECT id::text AS id, net_amount, settle_date, quantity,
               related_transaction_id::text AS related_transaction_id, valid_to
        FROM {TABLE_TRANSACTIONS}
        WHERE org_id = $1::uuid
          AND id = ANY($2::uuid[])
          AND system_to IS NULL
        ORDER BY valid_from
        """,
        DEFAULT_ORG_ID, [ids["txn_buy"], corrected_id],
    )
    current = [r for r in rows if r["valid_to"] is None]
    closed = [r for r in rows if r["valid_to"] is not None]
    check(
        "[Y] 5 the correction produced exactly TWO rows with exactly ONE "
        "current — an entry was superseded, not overwritten and not duplicated",
        len(rows) == 2 and len(current) == 1 and len(closed) == 1
        and current[0]["id"] == corrected_id
        and closed[0]["id"] == ids["txn_buy"],
        f"rows={len(rows)} current={len(current)} closed={len(closed)}",
    )
    if closed and current:
        check(
            "[Y] 5 the CLOSED row still carries its ORIGINAL net amount and "
            "quantity — the previous state was PRESERVED, which is the whole "
            "reason a correction is an append rather than an UPDATE",
            closed[0]["net_amount"] == BUY_NET
            and closed[0]["quantity"] == BUY_QTY
            and closed[0]["settle_date"] is None,
            f"closed net={closed[0]['net_amount']} qty={closed[0]['quantity']} "
            f"settle={closed[0]['settle_date']}",
        )
        check(
            "[Y] 5 the successor carries the corrected figures AND the "
            "backpointer, which the self-FK makes impossible to dangle",
            current[0]["net_amount"] == CORRECTED_NET
            and current[0]["settle_date"] == TODAY
            and current[0]["related_transaction_id"] == ids["txn_buy"],
            f"successor net={current[0]['net_amount']} "
            f"related={str(current[0]['related_transaction_id'])[:8]}…",
        )

    # Everything else on the row must have been CARRIED, not defaulted. This is
    # the failure mode A2's own INSERT had before Phase F fixed it: a column the
    # writer forgot silently stores its default and nothing raises.
    carried = await conn.fetchrow(
        f"""
        SELECT o.transaction_type_code = n.transaction_type_code AS type_same,
               o.trade_date  = n.trade_date                      AS date_same,
               o.quantity    = n.quantity                        AS qty_same,
               o.price       = n.price                           AS price_same,
               o.authority   = n.authority                       AS auth_same,
               o.source_system = n.source_system                 AS src_same,
               o.external_ref  IS NOT DISTINCT FROM n.external_ref AS ref_same,
               o.is_corporate_action_adjustment
                 = n.is_corporate_action_adjustment              AS flag_same,
               o.corporate_action_id
                 IS NOT DISTINCT FROM n.corporate_action_id      AS ca_same
        FROM {TABLE_TRANSACTIONS} o, {TABLE_TRANSACTIONS} n
        WHERE o.id = $1::uuid AND n.id = $2::uuid
        """,
        ids["txn_buy"], corrected_id,
    )
    check(
        "[Y] 5 every field the correction did NOT name was carried forward "
        "verbatim — including both corporate-action columns, which are Phase "
        "F's idempotency key and must survive a correction untouched",
        all(carried.values()),
        f"not carried: {[k for k, v in dict(carried).items() if not v]}",
    )


async def check_service_layer(conn, ids: dict) -> None:
    """Service-level facts the endpoints cannot show on their own."""
    # A refusal asserted by TYPE, not merely by "it raised" (Phase B's finding:
    # any exception satisfies a bare try/except).
    raised = None
    try:
        await correct_transaction(
            conn, org_id=DEFAULT_ORG_ID, transaction_id=ids["txn_sell"],
            changes={"transaction_type_code": ids["type_call"]},
        )
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    check(
        "[Y] 2 correct_transaction raises TransactionMarketError specifically "
        "— not just 'something raised', which any typo would also satisfy",
        isinstance(raised, TransactionMarketError),
        f"{type(raised).__name__}: {str(raised)[:110]}",
    )
    still = await conn.fetchval(
        f"SELECT valid_to FROM {TABLE_TRANSACTIONS} WHERE id = $1::uuid",
        ids["txn_sell"],
    )
    check(
        "[Y] 2 the refused correction left the entry OPEN — the close and the "
        "successor are ONE transaction, so a refusal cannot leave a hole in "
        "the ledger where an entry used to be",
        still is None,
        f"valid_to={still!r}",
    )

    raised2 = None
    try:
        await correct_transaction(
            conn, org_id=DEFAULT_ORG_ID, transaction_id=ids["txn_sell"],
            changes={"is_corporate_action_adjustment": False},
        )
    except BaseException as exc:  # noqa: BLE001
        raised2 = exc
    check(
        "[Y] 2 the service itself refuses a corporate-action field, not just "
        "the Pydantic model — a service-level caller cannot route around the "
        "API boundary to break the idempotency key",
        isinstance(raised2, PortfolioError)
        and "not correctable" in str(raised2),
        f"{type(raised2).__name__}: {str(raised2)[:110]}",
    )

    detail = await get_transaction(
        conn, org_id=OTHER_ORG_ID, transaction_id=ids["txn_sell"]
    )
    check(
        "[Y] 5 get_transaction returns None for a transaction in another org "
        "even on a BYPASSING connection — the org predicate is explicit in the "
        "SQL, not left to RLS alone",
        detail is None,
        f"got={type(detail).__name__}",
    )
    control = await get_transaction(
        conn, org_id=DEFAULT_ORG_ID, transaction_id=ids["txn_sell"]
    )
    check(
        "[Y] 5 the control: the SAME call in the right org DOES return the "
        "row, so the check above narrowed rather than simply failing",
        control is not None and control["transaction"]["id"] == ids["txn_sell"],
        "same id, correct org → found",
    )

    chain = await correction_history(
        conn, org_id=DEFAULT_ORG_ID, transaction_id=ids["txn_sell"]
    )
    check(
        "[Y] 5 an UNCORRECTED entry's chain is a single-element list, not an "
        "empty one — 'one version' and 'no history' are different answers",
        len(chain) == 1 and chain[0]["id"] == ids["txn_sell"],
        f"chain length={len(chain)}",
    )


class NonBypassRole:
    """A connection that reads as ``app_service``, however it got there.

    ``mode`` is ``'dsn'`` (a direct ``app_service`` login) or ``'set_role'``
    (``SET LOCAL ROLE`` inside each transaction). Which path was used is always
    REPORTED — a fallback nobody can see is how a rotated credential silently
    turns an RLS check into a session that merely resembles one.
    """

    def __init__(self, conn, mode: str):
        self.conn = conn
        self.mode = mode

    def scoped(self, org_id: str | None):
        conn, mode = self.conn, self.mode

        class _Ctx:
            async def __aenter__(self):
                self.tr = conn.transaction()
                await self.tr.start()
                try:
                    if mode == "set_role":
                        # LOCAL, not session-level: the pooler is in transaction
                        # mode, so a session SET can be handed to the next
                        # transaction on a different backend.
                        await conn.execute("SET LOCAL ROLE app_service")
                    await conn.execute(
                        "SELECT set_config('app.current_org_id', $1, true)",
                        org_id or "",
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
                "ENVIRONMENT — APP_SERVICE_DATABASE_URL in apps/api/.env does "
                "not authenticate",
                f"{type(exc).__name__}: {exc}. Falling back to SET LOCAL ROLE "
                f"app_service, which is ASSERTED below to be a genuinely "
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
        # THE POSITIVE PROOF. With no org context a non-bypass session sees
        # nothing; a bypassing one sees the whole table. This is what makes the
        # role assertion mean something rather than just naming a role.
        denied = await c.fetchval(f"SELECT count(*) FROM {TABLE_TRANSACTIONS}")

    check(
        f"[Y] 5 the RLS isolation check runs under a role that CANNOT bypass "
        f"RLS (obtained via {role.mode!r}) — otherwise every assertion below "
        f"would pass while proving nothing",
        bypass is False and who == "app_service",
        f"current_user={who!r} rolbypassrls={bypass} path={role.mode!r}",
    )
    check(
        "[Y] 5 RLS is demonstrably LIVE on portfolio.transactions: with the "
        "org GUC empty the session reads ZERO rows, where a bypassing session "
        "would read the entire table",
        denied == 0,
        f"rows visible with no org context = {denied} (must be 0)",
    )

    async def under(org_id: str, **kw):
        async with role.scoped(org_id) as c:
            return await list_transactions(c, org_id=org_id, search=TAG, **kw)

    b_view = await under(OTHER_ORG_ID)
    b_ids = {t["id"] for t in b_view["transactions"]}
    check(
        "[Y] 5 cross-org (RLS, real non-bypassing app_service connection): org "
        "B's context sees its OWN transaction and none of org A's — both "
        "directions on the same call",
        ids["txn_b"] in b_ids and not (b_ids & ids["org_a_txns"]),
        f"orgB rows={len(b_ids)} leaked={sorted(b_ids & ids['org_a_txns'])}",
    )

    a_view = await under(DEFAULT_ORG_ID)
    a_seen = {t["id"] for t in a_view["transactions"]}
    check(
        "[Y] 5 the control: org A's OWN context DOES see org A's transactions, "
        "so the check above narrowed rather than simply failing",
        (ids["org_a_txns"] - {ids["txn_buy"]}) <= a_seen
        and ids["txn_b"] not in a_seen,
        f"orgA sees {len(a_seen)} rows, "
        f"missing={sorted((ids['org_a_txns'] - {ids['txn_buy']}) - a_seen)}",
    )

    # The filters, under the real role — a policy that only allows the
    # unfiltered read would pass everything above.
    adj = await under(DEFAULT_ORG_ID, is_corporate_action_adjustment=True)
    trades = await under(DEFAULT_ORG_ID, is_corporate_action_adjustment=False)
    adj_ids = {t["id"] for t in adj["transactions"]}
    trade_ids = {t["id"] for t in trades["transactions"]}
    check(
        "[Y] 5 both adjustment-filter states still PARTITION correctly under "
        "the non-bypassing role — the filter and the policy compose",
        adj_ids == ids["org_a_adjustments"]
        and not (adj_ids & trade_ids)
        and (adj_ids | trade_ids) == a_seen,
        f"adj={len(adj_ids)} trades={len(trade_ids)} all={len(a_seen)}",
    )


def check_schema_qualification() -> None:
    """Every portfolio.* reference is schema-qualified. AST-checked.

    `portfolio` is NOT on app_service's search_path, so an unqualified
    `FROM transactions` raises UndefinedTableError under the production role
    while working fine in a psql session that happened to SET search_path —
    invisible in development, total in production. Docstrings are stripped first
    so a module explaining the rule does not flag its own explanation.
    """
    for rel in ("services/portfolio_transactions.py",
                "routers/portfolio_transactions.py"):
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


def check_no_org_id_from_body() -> None:
    """org_id is never read from a request body or a path segment."""
    rtr = read(os.path.join(_API, "routers", "portfolio_transactions.py"))
    tree = ast.parse(rtr)
    code = rtr
    docs = [ast.get_docstring(tree, clean=False)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            docs.append(ast.get_docstring(node, clean=False))
    for d in docs:
        if d:
            code = code.replace(d, "")
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    body_reads = re.findall(r"body\.org_id|\.get\(['\"]org_id", code)
    check(
        "[Y] 2 org_id is taken ONLY from get_org_id(request) — no executable "
        "line reads it from a body or a path segment, and neither request "
        "model declares such a field",
        not body_reads
        and code.count("get_org_id(request)") >= 5
        and "org_id:" not in code,
        f"get_org_id call sites={code.count('get_org_id(request)')}, "
        f"body reads={body_reads or 'none'}",
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
            f"These tables hold real rows. Fixtures are matched through the "
            f"{TAG!r} tag on asset / entity / document names and through their "
            f"tagged asset for positions and transactions, with an exact "
            f"before/after count as the backstop.",
        )

        txn_types = [
            dict(r) for r in await admin_conn.fetch(
                "SELECT code, label, market, is_active FROM "
                "public.transaction_types ORDER BY display_order, code"
            )
        ]

        print("\n── Task 1: DISCOVERY ──")
        check_task1a()
        check_task1b()
        check_task1c(txn_types)
        check_task1d()
        check_schema_qualification()
        check_no_org_id_from_body()

        print("\n── Fixtures ──")
        await seed_users(admin_conn)
        ids = await seed(admin_conn)
        direct = await direct_reads(admin_conn, ids)
        print(f"   seeded: 3 assets, 3 positions, 7 transactions "
              f"(2 corporate-action adjustments), 1 linked document")

        print("\n── Tasks 2-5: the real endpoints, driven through the ASGI app ──")
        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(None, endpoint_tests, ids, direct)

        print("\n── The rows agree with a direct read ──")
        if out.get("corrected_id"):
            await check_correction_rows(admin_conn, ids, out["corrected_id"])
        else:
            check("[Y] 5 the correction produced a successor row", False,
                  "no corrected_id came back from the endpoint pass")
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
