"""TA Model Sprint 4 (calibration UX + obligation ledger integration) — verify.

TASK 1 — DISCOVERY FINDINGS, READ THIS FIRST
──────────────────────────────────────────────────────────────────────────────
1a. NO real "obligation ledger" existed anywhere in this codebase before this
    sprint — and neither did the two primitives the prompt claimed already
    existed on it. A full grep of ``services/ta_model.py`` (and the whole
    repo) for ``contributions_between``/``contributions_in_years`` found ZERO
    hits before this sprint's own edits: the docstrings the prompt quoted
    ("This is the read-time primitive the obligation ledger consumes", "a
    36-month visibility horizon") did not exist either. This is the exact
    same shape of false premise Sprint 1's own brief documented for its own
    prior "93/93 verified standalone" claim — reported honestly, not silently
    treated as blocking. This sprint built both primitives (``ta_model.py``)
    AND the first real consumer: ``GET /modeling/ta/obligations/{commitment_id}``
    (``routers/modeling_ta.py``), computed at read time, never persisted.

1b. The REAL, current permission gate on ``POST /calibrate/{commitment_id}``,
    read directly from ``routers/modeling_ta.py`` (unchanged by this sprint —
    only confirmed): ``require_permission(pool, user_id, org_id,
    WRITE_PERMISSION)`` where ``WRITE_PERMISSION = "manage_portfolio"``
    (``services.portfolio_assets``) — NOT ``view_portfolio``, the read gate
    Sprint 3's projection screen uses. A genuinely stricter, separate gate,
    proven independently below (Section 2, permission-both-ways).

1c. Sprint 3's ``CommitmentProjectionScreen.jsx`` displayed NO confidence-tier
    information at all before this sprint (confirmed by reading the full
    file: only the 6 raw ``params`` fields and the 4 "current state" fields
    were rendered — no ``source``, no tier, no confidence signal of any
    kind). A real, user-facing gap this sprint closes with
    ``ConfidenceTierCard``.

1d. ``POST /calibrate/{commitment_id}``'s REAL request body
    (``CalibrateBody`` in ``routers/modeling_ta.py``) is, and always was,
    ``{ta_strategy_key: str, periods_per_year: int}`` — there is NO list of
    period+amount pairs anywhere in its input shape, contrary to the
    prompt's claim. The handler derives realized periods itself, server-side,
    via ``services.ta_params.realized_periods_from_transactions`` — a real
    query joining ``portfolio.transactions`` through the commitment's
    position, bucketed by calendar period. There is nothing for a caller to
    assemble or map: real distribution/contribution history is ALREADY
    queryable directly from ``portfolio.transactions``, and the endpoint
    already does so. This sprint's Calibrate UX (Task 3) therefore has no
    manual-entry form — it is a strategy/frequency picker that triggers the
    real endpoint, with a genuine preview-then-confirm flow added via a new,
    additive ``dry_run`` field (default False, so every existing caller,
    including ``verify_tamodel1.py``'s own proof, is unaffected).

DATABASE CONNECTIVITY
──────────────────────────────────────────────────────────────────────────────
Same discipline as verify_tamodel1.py / verify_tamodel3.py: hydrates from
Doppler over HTTPS first, attempts a REAL connection, and reports every
DB-dependent assertion as [BLOCKED] (never silently skipped, never faked
[PASS]) if that connection fails.

Run:
    python3 scripts/verify_tamodel4.py
"""

from __future__ import annotations

import glob
import os
import pathlib
import subprocess
import sys
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.join(_HERE, "..")
sys.path.insert(0, _HERE)
sys.path.insert(0, _API)
sys.path.extend(sorted(glob.glob(os.path.join(_API, "venv", "lib", "python3*", "site-packages"))))

import asyncio  # noqa: E402

import asyncpg  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
WEB = REPO / "apps" / "web"

passed = 0
failed = 0
blocked = 0


def ok(label: str) -> None:
    global passed
    passed += 1
    print(f"[PASS] {label}")


def fail(label: str, detail: str = "") -> None:
    global failed
    failed += 1
    print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        ok(label)
    else:
        fail(label, detail)


def blocked_(label: str, reason: str) -> None:
    global blocked
    blocked += 1
    print(f"[BLOCKED] {label} — {reason}")


def report(label: str, detail: str) -> None:
    print(f"[FIND] {label}\n       {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — DISCOVERY FINDINGS (repo facts, no DB required)
# ═══════════════════════════════════════════════════════════════════════════


def report_task1_findings() -> None:
    print("\n── Task 1: discovery findings ──")
    report(
        "1a. no obligation-ledger consumer (or the primitives it would call) "
        "existed anywhere before this sprint",
        "grep for contributions_between/contributions_in_years across the "
        "whole repo, pre-sprint, returns zero hits — this sprint adds both "
        "pure functions to services/ta_model.py and the first real consumer, "
        "GET /modeling/ta/obligations/{commitment_id}, computed at read time.",
    )
    report(
        "1b. POST /calibrate's real gate is manage_portfolio, stricter than "
        "the view_portfolio read gate",
        "routers/modeling_ta.py: require_permission(pool, user_id, org_id, "
        "WRITE_PERMISSION) where WRITE_PERMISSION = "
        "services.portfolio_assets.WRITE_PERMISSION = 'manage_portfolio'.",
    )
    report(
        "1c. the Sprint 3 projection screen displayed zero confidence signal",
        "CommitmentProjectionScreen.jsx rendered only params + current-state "
        "fields — no source, no tier. Closed by ConfidenceTierCard.",
    )
    report(
        "1d. /calibrate takes no period+amount list — realized periods are "
        "derived server-side from portfolio.transactions already",
        "CalibrateBody = {ta_strategy_key, periods_per_year} only. "
        "services.ta_params.realized_periods_from_transactions does the real "
        "derivation. The Calibrate UX is a strategy/frequency picker, not a "
        "data-entry form.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — PURE-MODULE ASSERTIONS (no DB, no I/O; always run)
# ═══════════════════════════════════════════════════════════════════════════


def run_pure_module_assertions() -> None:
    from services.ta_confidence import (
        ASSUMED,
        IMPLEMENTED_TIERS,
        OBSERVED,
        PEER_CALIBRATED,
        STRATEGY_DEFAULT,
        TIER_DESCRIPTIONS,
        confidence_tier_for,
    )
    from services.ta_model import TAModelError, TAParams, contributions_between, contributions_in_years, project_cash_flows

    print("\n── Section 1: pure-module assertions (ta_model / ta_confidence) ──")

    params = TAParams(
        rate_of_contribution=Decimal("0.25"), rate_of_distribution=Decimal("0.15"),
        growth_rate=Decimal("0.02"), bow_factor=Decimal("1.5"), fund_life_years=Decimal("10"),
        periods_per_year=4,
    )
    periods = project_cash_flows(
        committed_capital=Decimal("1000000"), called_to_date=Decimal(0),
        distributed_to_date=Decimal(0), current_nav=Decimal(0),
        params=params, horizon_periods=40,
    )

    manual_between = sum((p.contribution for p in periods if 5 <= p.period <= 8), Decimal(0))
    check(
        "1.1 contributions_between sums exactly the requested period range",
        contributions_between(periods, 5, 8) == manual_between,
        f"got {contributions_between(periods, 5, 8)} want {manual_between}",
    )
    check(
        "1.2 contributions_between over the full horizon equals the sum of every period",
        contributions_between(periods, 1, 40) == sum((p.contribution for p in periods), Decimal(0)),
    )
    manual_3y = sum((p.contribution for p in periods if p.period <= 12), Decimal(0))
    check(
        "1.3 contributions_in_years(0, 3, periods_per_year=4) — the 36-month "
        "visibility horizon — equals periods 1..12 summed",
        contributions_in_years(periods, 0, 3, 4) == manual_3y,
        f"got {contributions_in_years(periods, 0, 3, 4)} want {manual_3y}",
    )
    manual_yr2 = sum((p.contribution for p in periods if 5 <= p.period <= 8), Decimal(0))
    check(
        "1.4 contributions_in_years(1, 2, ...) (year 2 only) matches the "
        "equivalent period-index range",
        contributions_in_years(periods, 1, 2, 4) == manual_yr2,
    )
    try:
        contributions_between(periods, 0, 5)
        fail("1.5 contributions_between rejects period_start < 1")
    except TAModelError:
        ok("1.5 contributions_between rejects period_start < 1")
    try:
        contributions_between(periods, 8, 5)
        fail("1.6 contributions_between rejects period_end < period_start")
    except TAModelError:
        ok("1.6 contributions_between rejects period_end < period_start")
    try:
        contributions_in_years(periods, 3, 3, 4)
        fail("1.7 contributions_in_years rejects end_year <= start_year")
    except TAModelError:
        ok("1.7 contributions_in_years rejects end_year <= start_year")

    check("1.8 confidence_tier_for(None) == STRATEGY_DEFAULT (no override row)", confidence_tier_for(None) == STRATEGY_DEFAULT)
    check("1.9 confidence_tier_for('override') == ASSUMED (admin-typed, not fit to data)", confidence_tier_for("override") == ASSUMED)
    check("1.10 confidence_tier_for('calibrated') == OBSERVED (fit to real realized history)", confidence_tier_for("calibrated") == OBSERVED)
    try:
        confidence_tier_for("bogus")
        fail("1.11 confidence_tier_for rejects an unrecognized source")
    except ValueError:
        ok("1.11 confidence_tier_for rejects an unrecognized source")
    check(
        "1.12 PEER_CALIBRATED is named but NEVER returned — no real peer-fund "
        "aggregation exists anywhere in this codebase to back it",
        PEER_CALIBRATED not in IMPLEMENTED_TIERS
        and all(confidence_tier_for(s) != PEER_CALIBRATED for s in (None, "override", "calibrated")),
    )
    check(
        "1.13 every implemented tier has a real, non-empty plain-language description",
        all(TIER_DESCRIPTIONS.get(t) for t in (STRATEGY_DEFAULT, ASSUMED, OBSERVED)),
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — STATIC FRONTEND ASSERTIONS (no DB; source-level checks)
# ═══════════════════════════════════════════════════════════════════════════


def run_static_frontend_assertions() -> None:
    print("\n── Section 2: static frontend assertions ──")

    screen_path = WEB / "components" / "portfolio" / "CommitmentProjectionScreen.jsx"
    src = screen_path.read_text() if screen_path.exists() else ""
    check("2.1 CommitmentProjectionScreen.jsx exists and is non-empty", bool(src))
    check("2.2 ConfidenceTierCard is defined and rendered", "function ConfidenceTierCard" in src and "<ConfidenceTierCard" in src)
    check("2.3 CalibratePanel is defined and rendered", "function CalibratePanel" in src and "<CalibratePanel" in src)
    check("2.4 ObligationLedgerPanel is defined and rendered", "function ObligationLedgerPanel" in src and "<ObligationLedgerPanel" in src)
    check(
        "2.5 CalibratePanel is gated fail-closed on the server's own "
        "can_calibrate envelope (strict === true, no truthy fallback)",
        "projection.permissions?.can_calibrate === true" in src,
    )
    check(
        "2.6 the floor refusal is displayed verbatim from the server "
        "(formatApiError over the real response), never re-derived — no "
        "client-side minimum-periods constant anywhere in this file",
        "formatApiError" in src and "MIN_CALIBRATION" not in src and "minimum_realized_periods" not in src,
    )
    check(
        "2.7 a real calibration confirm triggers a fresh, independent GET "
        "(never a locally-patched object) so the tier shown updates live",
        "async function refreshProjection" in src and "onCalibrated={refreshProjection}" in src,
    )

    calibrate_route = WEB / "app" / "api" / "modeling" / "ta" / "calibrate" / "[commitmentId]" / "route.js"
    obligations_route = WEB / "app" / "api" / "modeling" / "ta" / "obligations" / "[commitmentId]" / "route.js"
    check("2.8 the calibrate Next.js proxy route exists (POST, forwards to FastAPI)", calibrate_route.exists() and "forwardToApi" in calibrate_route.read_text())
    check("2.9 the obligations Next.js proxy route exists (GET, forwards to FastAPI)", obligations_route.exists() and "forwardToApi" in obligations_route.read_text())


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — DATABASE-DEPENDENT ASSERTIONS (Tasks 2, 3, 4, 5)
# ═══════════════════════════════════════════════════════════════════════════

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "VERIFY-TAMODEL4"
VIEWER_SUB = "auth0|verify_tamodel4_viewer"       # org A — view_portfolio ONLY
CALIBRATOR_SUB = "auth0|verify_tamodel4_calibrator"  # org A — view_portfolio + manage_portfolio
NOPERM_SUB = "auth0|verify_tamodel4_noperm"       # org A — a REAL role grant with ZERO permissions
ORGB_SUB = "auth0|verify_tamodel4_orgb"           # org B

U_VIEWER = str(uuid5(NAMESPACE_URL, VIEWER_SUB))
U_CALIBRATOR = str(uuid5(NAMESPACE_URL, CALIBRATOR_SUB))
U_NOPERM = str(uuid5(NAMESPACE_URL, NOPERM_SUB))
U_ORGB = str(uuid5(NAMESPACE_URL, ORGB_SUB))
ALL_TEST_USERS = [U_VIEWER, U_CALIBRATOR, U_NOPERM, U_ORGB]

ROLE_VIEWER = f"{TAG}_viewer_only"
ROLE_CALIBRATOR = f"{TAG}_calibrator"
ROLE_NOPERM = f"{TAG}_noperm"
ALL_TEST_ROLES = [ROLE_VIEWER, ROLE_CALIBRATOR, ROLE_NOPERM]

HEADERS = {"Authorization": "Bearer verify-token"}

DB_ASSERTIONS = (
    "3.x confidence tier is STRATEGY_DEFAULT before any override, and the "
    "can_calibrate envelope matches the real permission per caller",
    "3.x the calibration permission gate is proven both ways (403 without "
    "manage_portfolio, 200 with it) — independent of the view_portfolio read gate",
    "3.x dry_run preview fits and validates without persisting anything",
    "3.x the frequency-aware floor's real refusal surfaces via the API, "
    "reused verbatim by the UI's own error path",
    "3.x a real (non-dry-run) calibration persists and a FRESH, independent "
    "GET shows the upgraded OBSERVED tier",
    "3.x confidence tier genuinely differs across two real commitments "
    "(STRATEGY_DEFAULT vs OBSERVED)",
    "3.x the obligation ledger produces genuinely different real output for "
    "two commitments with different committed-capital/call schedules",
    "3.x the obligation ledger is read-time only (row count unchanged)",
    "3.x cross-org isolation on both calibrate and the obligation ledger",
    "3.x teardown leaves zero leftover rows",
)


async def cleanup(conn) -> None:
    await conn.execute(
        "DELETE FROM portfolio.ta_calibration_results WHERE org_id = $1 "
        "AND commitment_id IN (SELECT c.id FROM portfolio.commitments c "
        "JOIN portfolio.positions p ON p.id = c.position_id "
        "JOIN portfolio.assets a ON a.id = p.asset_id WHERE a.name LIKE $2)",
        ORG_A, f"{TAG}%",
    )
    await conn.execute(
        "DELETE FROM portfolio.ta_model_params WHERE org_id = $1 "
        "AND commitment_id IN (SELECT c.id FROM portfolio.commitments c "
        "JOIN portfolio.positions p ON p.id = c.position_id "
        "JOIN portfolio.assets a ON a.id = p.asset_id WHERE a.name LIKE $2)",
        ORG_A, f"{TAG}%",
    )
    await conn.execute(
        "DELETE FROM portfolio.transactions WHERE org_id = $1 "
        "AND position_id IN (SELECT p.id FROM portfolio.positions p "
        "JOIN portfolio.assets a ON a.id = p.asset_id WHERE a.name LIKE $2)",
        ORG_A, f"{TAG}%",
    )
    await conn.execute(
        "DELETE FROM portfolio.commitments WHERE org_id = $1 "
        "AND position_id IN (SELECT p.id FROM portfolio.positions p "
        "JOIN portfolio.assets a ON a.id = p.asset_id WHERE a.name LIKE $2)",
        ORG_A, f"{TAG}%",
    )
    await conn.execute(
        "DELETE FROM portfolio.positions WHERE org_id = $1 "
        "AND asset_id IN (SELECT id FROM portfolio.assets WHERE name LIKE $2)",
        ORG_A, f"{TAG}%",
    )
    await conn.execute("DELETE FROM portfolio.assets WHERE org_id = $1 AND name LIKE $2", ORG_A, f"{TAG}%")
    await conn.execute("DELETE FROM entities WHERE org_id = $1 AND display_name LIKE $2", ORG_A, f"{TAG}%")
    await conn.execute("DELETE FROM public.user_roles WHERE user_id = ANY($1::uuid[])", ALL_TEST_USERS)
    await conn.execute(
        "DELETE FROM public.role_permissions WHERE role_id IN "
        "(SELECT id FROM public.roles WHERE org_id = $1 AND name = ANY($2::text[]))",
        ORG_A, ALL_TEST_ROLES,
    )
    await conn.execute("DELETE FROM public.roles WHERE org_id = $1 AND name = ANY($2::text[])", ORG_A, ALL_TEST_ROLES)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS)


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM public.user_roles WHERE user_id = ANY($1::uuid[]))
          + (SELECT count(*) FROM public.roles WHERE org_id = $2 AND name = ANY($3::text[]))
          + (SELECT count(*) FROM entities WHERE org_id = $2 AND display_name LIKE $4)
          + (SELECT count(*) FROM portfolio.assets WHERE org_id = $2 AND name LIKE $4)
          + (SELECT count(*) FROM portfolio.ta_model_params t
                WHERE t.commitment_id IN (
                    SELECT c.id FROM portfolio.commitments c
                    JOIN portfolio.positions p ON p.id = c.position_id
                    JOIN portfolio.assets a ON a.id = p.asset_id WHERE a.name LIKE $4))
          + (SELECT count(*) FROM portfolio.ta_calibration_results t
                WHERE t.commitment_id IN (
                    SELECT c.id FROM portfolio.commitments c
                    JOIN portfolio.positions p ON p.id = c.position_id
                    JOIN portfolio.assets a ON a.id = p.asset_id WHERE a.name LIKE $4))
        """,
        ALL_TEST_USERS, ORG_A, ALL_TEST_ROLES, f"{TAG}%",
    ))


class _Principal:
    """Drives the real ASGI app as one specific user (see verify_tamodel1.py /
    verify_tamodel3.py for why this exact shape: stub verify_token, one
    shared TestClient/loop)."""

    __slots__ = ("client", "org_id", "sub")

    def __init__(self, client, org_id: str, sub: str):
        self.client = client
        self.org_id = org_id
        self.sub = sub

    def _become(self) -> None:
        import main
        main.verify_token = lambda _token: {
            "sub": self.sub, "email": f"{self.sub}@test.local", "org_id": self.org_id,
        }

    def get(self, url, **kw):
        self._become()
        return self.client.get(url, **kw)

    def post(self, url, **kw):
        self._become()
        return self.client.post(url, **kw)


async def seed_users(conn) -> None:
    for user_id, org, sub, role in (
        (U_VIEWER, ORG_A, VIEWER_SUB, "member"),
        (U_CALIBRATOR, ORG_A, CALIBRATOR_SUB, "member"),
        (U_NOPERM, ORG_A, NOPERM_SUB, "member"),
        (U_ORGB, ORG_B, ORGB_SUB, "org_admin"),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (auth0_sub) DO UPDATE SET role = EXCLUDED.role, org_id = EXCLUDED.org_id
            """,
            user_id, org, f"{sub.split('|')[-1]}@test.local", f"{TAG} {sub}", sub, role,
        )

    # Three REAL role grants — U_VIEWER holds ONLY view_portfolio, U_CALIBRATOR
    # holds view_portfolio + manage_portfolio, U_NOPERM holds NOTHING. None of
    # these rely on the zero-roles default-allow (see memory: "a role-less
    # user default-allows and would silently hold write" — the exact bug this
    # pattern rules out for the permission-both-ways proof below).
    role_ids = {}
    for name in ALL_TEST_ROLES:
        role_ids[name] = await conn.fetchval(
            """
            INSERT INTO public.roles (org_id, name, description)
            VALUES ($1, $2, 'verify_tamodel4 fixture')
            ON CONFLICT (org_id, name) DO UPDATE SET description = EXCLUDED.description
            RETURNING id
            """,
            ORG_A, name,
        )
    await conn.execute(
        "INSERT INTO public.role_permissions (role_id, permission_id) "
        "SELECT $1::uuid, id FROM public.permissions WHERE name = 'view_portfolio' "
        "ON CONFLICT DO NOTHING",
        role_ids[ROLE_VIEWER],
    )
    await conn.execute(
        "INSERT INTO public.role_permissions (role_id, permission_id) "
        "SELECT $1::uuid, id FROM public.permissions WHERE name IN ('view_portfolio', 'manage_portfolio') "
        "ON CONFLICT DO NOTHING",
        role_ids[ROLE_CALIBRATOR],
    )
    # ROLE_NOPERM gets zero role_permissions rows, deliberately.
    for user_id, role_name in ((U_VIEWER, ROLE_VIEWER), (U_CALIBRATOR, ROLE_CALIBRATOR), (U_NOPERM, ROLE_NOPERM)):
        await conn.execute(
            "INSERT INTO public.user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            user_id, role_ids[role_name],
        )


async def seed_fixtures(conn) -> dict:
    from services.portfolio_assets import create_asset, create_position, record_transaction
    from services.portfolio_commitments import create_commitment, recompute_commitment

    entity_id = await conn.fetchval(
        "INSERT INTO entities (org_id, entity_type, display_name) VALUES ($1, $2, $3) RETURNING id",
        ORG_A, "llc", f"{TAG} Entity",
    )

    # Commitment 1 — LARGE, with 3 real annual call transactions (used for
    # the calibration + frequency-floor proof: bucketed quarterly these 3
    # same-month transactions land in exactly 3 distinct quarters, below the
    # 12-quarter floor — the identical mechanic verify_tamodel1.py proved).
    asset1_id = await create_asset(
        conn, org_id=ORG_A, name=f"{TAG} Commitment1 Buyout Fund", asset_type="private_fund",
        asset_class="financial", valuation_method="nav",
    )
    position1_id = await create_position(
        conn, org_id=ORG_A, owner_entity_id=str(entity_id), asset_id=asset1_id,
        as_of_date=date(2026, 1, 1), authority="manual", source_system="manual",
        ownership_basis="value", market_value=Decimal("1600000"),
    )
    commitment1_id = await create_commitment(
        conn, org_id=ORG_A, position_id=position1_id,
        commitment_amount=Decimal("2000000"), commitment_date=date(2023, 1, 15),
        vintage_year=2023,
    )
    for yr in (2023, 2024, 2025):
        await record_transaction(
            conn, org_id=ORG_A, position_id=position1_id,
            transaction_type_code="call_investment", trade_date=date(yr, 1, 15),
            authority="manual", source_system="manual", gross_amount=Decimal("150000"),
        )
    await recompute_commitment(conn, ORG_A, commitment1_id)

    # Commitment 2 — SMALL, no calls at all — untouched STRATEGY_DEFAULT tier
    # throughout, genuinely different committed_capital/called_to_date from
    # commitment 1, for the tier-difference and ledger-difference proofs.
    asset2_id = await create_asset(
        conn, org_id=ORG_A, name=f"{TAG} Commitment2 Small Fund", asset_type="private_fund",
        asset_class="financial", valuation_method="nav",
    )
    position2_id = await create_position(
        conn, org_id=ORG_A, owner_entity_id=str(entity_id), asset_id=asset2_id,
        as_of_date=date(2026, 1, 1), authority="manual", source_system="manual",
        ownership_basis="value", market_value=Decimal("0"),
    )
    commitment2_id = await create_commitment(
        conn, org_id=ORG_A, position_id=position2_id,
        commitment_amount=Decimal("500000"), commitment_date=date(2024, 6, 1),
        vintage_year=2024,
    )
    await recompute_commitment(conn, ORG_A, commitment2_id)

    return {
        "entity_id": str(entity_id),
        "commitment1_id": commitment1_id, "commitment2_id": commitment2_id,
    }


async def _ta_row_count(admin_conn) -> int:
    return int(await admin_conn.fetchval(
        "SELECT (SELECT count(*) FROM portfolio.ta_model_params) "
        "+ (SELECT count(*) FROM portfolio.ta_calibration_results)"
    ))


async def _active_params_count(admin_conn, commitment_id) -> int:
    return int(await admin_conn.fetchval(
        "SELECT count(*) FROM portfolio.ta_model_params "
        "WHERE commitment_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
        commitment_id,
    ))


async def run_api_assertions(admin_conn, ids: dict) -> None:
    import main
    from starlette.testclient import TestClient

    print("\n── Section 3: the real ASGI app — calibration UX + obligation ledger ──")

    c1, c2 = ids["commitment1_id"], ids["commitment2_id"]

    shared = TestClient(main.app, raise_server_exceptions=False)
    shared.__enter__()
    try:
        viewer = _Principal(shared, ORG_A, VIEWER_SUB)
        calibrator = _Principal(shared, ORG_A, CALIBRATOR_SUB)
        org_b = _Principal(shared, ORG_B, ORGB_SUB)

        # ── 3.1: confidence tier + can_calibrate envelope BEFORE any override ──
        res1 = viewer.get(f"/api/v1/modeling/ta/projection/{c1}?strategy_key=buyout", headers=HEADERS)
        body1 = res1.json() if res1.status_code == 200 else {}
        check(
            "3.1 GET projection (commitment 1, no override yet) succeeds and "
            "reports confidence_tier=STRATEGY_DEFAULT",
            res1.status_code == 200 and body1.get("confidence_tier") == "STRATEGY_DEFAULT" and body1.get("source") is None,
            f"status={res1.status_code} body={body1}",
        )
        check(
            "3.1 the same response carries a real, non-empty plain-language "
            "confidence_description (not just a color chip)",
            bool(body1.get("confidence_description")),
        )
        check(
            "3.1 permissions.can_calibrate is FALSE for the view-only caller "
            "(no client-side default — this is the server's own computed value)",
            body1.get("permissions", {}).get("can_calibrate") is False,
            f"permissions={body1.get('permissions')}",
        )
        res1_cal = calibrator.get(f"/api/v1/modeling/ta/projection/{c1}?strategy_key=buyout", headers=HEADERS)
        check(
            "3.1 the SAME envelope reports can_calibrate TRUE for the caller "
            "who actually holds manage_portfolio",
            (res1_cal.json() or {}).get("permissions", {}).get("can_calibrate") is True,
        )

        # ── 3.2: calibration permission gate, proven BOTH ways, independent
        # of the view_portfolio read gate already proven in Sprint 3 ──────
        refused = viewer.post(
            f"/api/v1/modeling/ta/calibrate/{c1}",
            json={"ta_strategy_key": "buyout", "periods_per_year": 1, "dry_run": True}, headers=HEADERS,
        )
        check(
            "3.2 a caller with view_portfolio but WITHOUT manage_portfolio is "
            "REFUSED (403) on calibrate — even though the SAME caller can "
            "read the projection (proven in 3.1) — a genuinely separate gate",
            refused.status_code == 403, f"status={refused.status_code} body={refused.text[:200]}",
        )
        before_dry = await _ta_row_count(admin_conn)
        preview = calibrator.post(
            f"/api/v1/modeling/ta/calibrate/{c1}",
            json={"ta_strategy_key": "buyout", "periods_per_year": 1, "dry_run": True}, headers=HEADERS,
        )
        after_dry = await _ta_row_count(admin_conn)
        preview_body = preview.json() if preview.status_code == 200 else {}
        check(
            "3.2 the SAME caller, holding manage_portfolio, is ADMITTED (200) "
            "on calibrate — the gate refuses the wrong caller and admits the "
            "right one on the identical request",
            preview.status_code == 200, f"status={preview.status_code} body={preview.text[:300]}",
        )

        # ── 3.3: dry_run preview fits for real but persists nothing ─────────
        check(
            "3.3 dry_run=true returns dry_run:true, a real fitted params set, "
            "and a null calibration_id/params_id (nothing to reference — "
            "nothing was written)",
            preview_body.get("dry_run") is True
            and preview_body.get("calibration_id") is None
            and preview_body.get("params_id") is None
            and preview_body.get("params", {}).get("rate_of_contribution") is not None,
            f"body={preview_body}",
        )
        check(
            "3.3 the preview reports confidence_tier=OBSERVED (what confirming "
            "would upgrade to) WITHOUT actually upgrading anything yet",
            preview_body.get("confidence_tier") == "OBSERVED",
        )
        check(
            "3.3 the dry run wrote ZERO rows to ta_model_params/ta_calibration_results",
            before_dry == after_dry, f"before={before_dry} after={after_dry}",
        )

        # ── 3.4: the frequency-aware floor's real refusal, same endpoint,
        # same shape the UI's CalibratePanel calls (Task 1d/1b) ─────────────
        floor_refused = calibrator.post(
            f"/api/v1/modeling/ta/calibrate/{c1}",
            json={"ta_strategy_key": "buyout", "periods_per_year": 4, "dry_run": True}, headers=HEADERS,
        )
        check(
            "3.4 a quarterly calibration attempt on 3 same-month annual calls "
            "(3 realized quarters, below the 12-quarter floor) is REFUSED (422), "
            "even under dry_run — a preview can never claim success where a "
            "real submission would be refused",
            floor_refused.status_code == 422, f"status={floor_refused.status_code} body={floor_refused.text[:200]}",
        )
        floor_detail = (floor_refused.json() or {}).get("detail", "")
        check(
            "3.4 the refusal names the real floor (12 periods) and the "
            "'calibration floor' phrase — this exact string is what "
            "CalibratePanel surfaces verbatim",
            "12" in floor_detail and "calibration floor" in floor_detail,
            f"detail={floor_detail!r}",
        )

        # ── 3.5: a REAL (non-dry-run) calibration persists; a FRESH, "
        # independent GET reflects the upgrade ───────────────────────────
        real_cal = calibrator.post(
            f"/api/v1/modeling/ta/calibrate/{c1}",
            json={"ta_strategy_key": "buyout", "periods_per_year": 1, "dry_run": False}, headers=HEADERS,
        )
        real_body = real_cal.json() if real_cal.status_code == 200 else {}
        check(
            "3.5 the real calibration (dry_run omitted implicitly False by "
            "confirming) succeeds and persists",
            real_cal.status_code == 200 and real_body.get("dry_run") is False
            and real_body.get("calibration_id") and real_body.get("params_id"),
            f"status={real_cal.status_code} body={real_body}",
        )
        active_count = await _active_params_count(admin_conn, c1)
        check("3.5 exactly ONE active override row exists after calibration", active_count == 1, f"count={active_count}")

        fresh = calibrator.get(f"/api/v1/modeling/ta/projection/{c1}", headers=HEADERS)
        fresh_body = fresh.json() if fresh.status_code == 200 else {}
        check(
            "3.5 a FRESH, independent GET (new request, not the calibrate "
            "response) confirms confidence_tier=OBSERVED and source=calibrated",
            fresh.status_code == 200 and fresh_body.get("confidence_tier") == "OBSERVED"
            and fresh_body.get("source") == "calibrated",
            f"status={fresh.status_code} body={fresh_body}",
        )
        check(
            "3.5 the fresh GET's bow_factor for real (the fitted "
            "rate_of_contribution/rate_of_distribution differ from the "
            "buyout strategy default, proving a REAL fit ran, not a no-op)",
            fresh_body.get("params", {}).get("rate_of_contribution") != body1.get("params", {}).get("rate_of_contribution"),
            f"fresh={fresh_body.get('params', {}).get('rate_of_contribution')} "
            f"default={body1.get('params', {}).get('rate_of_contribution')}",
        )

        # ── 3.6: confidence tier genuinely differs across two REAL commitments ──
        res2 = viewer.get(f"/api/v1/modeling/ta/projection/{c2}?strategy_key=buyout", headers=HEADERS)
        body2 = res2.json() if res2.status_code == 200 else {}
        check(
            "3.6 commitment 2 (never calibrated) is still STRATEGY_DEFAULT, "
            "while commitment 1 (just calibrated) is OBSERVED — genuinely "
            "different real output, not a static label",
            res2.status_code == 200 and body2.get("confidence_tier") == "STRATEGY_DEFAULT"
            and fresh_body.get("confidence_tier") == "OBSERVED",
            f"c2_tier={body2.get('confidence_tier')} c1_tier={fresh_body.get('confidence_tier')}",
        )
        check(
            "3.6 the two tiers carry DIFFERENT plain-language descriptions",
            body2.get("confidence_description") != fresh_body.get("confidence_description"),
        )

        # ── 3.7: the obligation ledger differs for two differently-shaped
        # commitments — driven by real, live data ──────────────────────────
        led1 = calibrator.get(f"/api/v1/modeling/ta/obligations/{c1}?strategy_key=buyout", headers=HEADERS)
        led2 = calibrator.get(f"/api/v1/modeling/ta/obligations/{c2}?strategy_key=buyout", headers=HEADERS)
        led1_body = led1.json() if led1.status_code == 200 else {}
        led2_body = led2.json() if led2.status_code == 200 else {}
        check(
            "3.7 GET obligations succeeds for both real commitments",
            led1.status_code == 200 and led2.status_code == 200,
            f"c1={led1.status_code} c2={led2.status_code}",
        )
        check(
            "3.7 the two commitments' 36-month projected-call totals are "
            "GENUINELY DIFFERENT (committed_capital 2,000,000 vs 500,000) — "
            "not fixture-shaped coincidence",
            led1_body.get("total_projected_contribution") is not None
            and led1_body.get("total_projected_contribution") != led2_body.get("total_projected_contribution"),
            f"c1_total={led1_body.get('total_projected_contribution')} "
            f"c2_total={led2_body.get('total_projected_contribution')}",
        )
        check(
            "3.7 the ledger publishes a per-year breakdown covering the full "
            "3-year (36-month) window, both commitments",
            len(led1_body.get("by_year", [])) == 3 and len(led2_body.get("by_year", [])) == 3,
        )

        # ── 3.8: the obligation ledger is READ-TIME ONLY ─────────────────────
        before_ledger = await _ta_row_count(admin_conn)
        calibrator.get(f"/api/v1/modeling/ta/obligations/{c1}?strategy_key=buyout", headers=HEADERS)
        calibrator.get(f"/api/v1/modeling/ta/obligations/{c2}?strategy_key=buyout", headers=HEADERS)
        calibrator.get(f"/api/v1/modeling/ta/obligations/{c1}?strategy_key=buyout", headers=HEADERS)
        after_ledger = await _ta_row_count(admin_conn)
        check(
            "3.8 three real calls to GET obligations write ZERO new rows to "
            "either ta_ table — confirmed by an exact row-count check, not "
            "an assumption",
            before_ledger == after_ledger, f"before={before_ledger} after={after_ledger}",
        )

        # ── 3.9: cross-org isolation on BOTH new surfaces ────────────────────
        cross_ledger = org_b.get(f"/api/v1/modeling/ta/obligations/{c1}?strategy_key=buyout", headers=HEADERS)
        check(
            "3.9 org B cannot read org A's obligation ledger (404, not org "
            "A's data)",
            cross_ledger.status_code == 404, f"status={cross_ledger.status_code}",
        )
        cross_calibrate = org_b.post(
            f"/api/v1/modeling/ta/calibrate/{c1}",
            json={"ta_strategy_key": "buyout", "periods_per_year": 1, "dry_run": True}, headers=HEADERS,
        )
        check(
            "3.9 org B cannot calibrate org A's commitment (404, not a "
            "cross-org write)",
            cross_calibrate.status_code == 404, f"status={cross_calibrate.status_code}",
        )

        # ── zero-permission caller refused cleanly on both new read gates ───
        noperm = _Principal(shared, ORG_A, NOPERM_SUB)
        noperm_projection = noperm.get(f"/api/v1/modeling/ta/projection/{c1}", headers=HEADERS)
        noperm_ledger = noperm.get(f"/api/v1/modeling/ta/obligations/{c1}?strategy_key=buyout", headers=HEADERS)
        check(
            "3.9 a REAL zero-permission role (not a zero-roles fixture, which "
            "would default-allow) is refused (403) on both projection and "
            "obligation-ledger reads",
            noperm_projection.status_code == 403 and noperm_ledger.status_code == 403,
            f"projection={noperm_projection.status_code} ledger={noperm_ledger.status_code}",
        )
    finally:
        shared.__exit__(None, None, None)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — npm run build
# ═══════════════════════════════════════════════════════════════════════════


def _node_modules_dir() -> str | None:
    for candidate in (WEB / "node_modules", WEB / ".." / ".." / "node_modules"):
        if candidate.is_dir():
            return str(candidate.resolve())
    return None


def check_npm_build() -> None:
    print("\n── Section 4: npm run build ──")
    deps = _node_modules_dir()
    if deps is None:
        check("4.1 npm run build exits 0", False,
              "node_modules absent from apps/web and the workspace root — "
              "the build was NOT measured, which is not the same as passing")
        return
    proc = subprocess.run(
        ["npm", "run", "build"], cwd=str(WEB), capture_output=True, text=True, timeout=1800,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
    check(
        "4.1 npm run build exits 0",
        proc.returncode == 0,
        f"exit={proc.returncode}" + ("" if proc.returncode == 0 else " | " + " / ".join(tail)),
    )


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    from _doppler_env import hydrate_from_doppler

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — falling back to ambient DATABASE_URL")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1

    print("=" * 78)
    print("TA MODEL SPRINT 4 — verify")
    print("=" * 78)

    report_task1_findings()
    run_pure_module_assertions()
    run_static_frontend_assertions()

    print("\n── Database connectivity check (real attempt, not a presence check) ──")
    admin_conn = None
    try:
        admin_conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        print(f"[BLOCKED] could not connect to DATABASE_URL: {type(exc).__name__}: {exc}")
        for label in DB_ASSERTIONS:
            blocked_(label, "DATABASE_URL present but authentication failed — see above")
    else:
        try:
            await cleanup(admin_conn)  # teardown-at-start
            await seed_users(admin_conn)
            ids = await seed_fixtures(admin_conn)
            try:
                await run_api_assertions(admin_conn, ids)
            except Exception as exc:  # noqa: BLE001
                fail("3.x API-layer assertions raised unexpectedly", f"{type(exc).__name__}: {exc}")
        finally:
            await cleanup(admin_conn)
            remaining = await leftover_count(admin_conn)
            check("3.10 teardown complete — zero leftover test rows", remaining == 0, f"count={remaining}")
            await admin_conn.close()

    check_npm_build()

    print("\n" + "=" * 78)
    print(f"TA Model Sprint 4: {passed} passed, {failed} failed, {blocked} blocked")
    print("=" * 78)
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
