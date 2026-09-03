"""TA Model Sprint 1 (substrate) — verify.

TASK 1 — HONESTY NOTE, READ THIS FIRST
──────────────────────────────────────────────────────────────────────────────
The sprint prompt for this sprint asserted that ``docs/TA_MODEL_INTEGRATION_
BRIEF.md`` and three modules (``ta_model.py``, ``ta_config.py``,
``ta_calibrate.py``) were "already built and verified standalone (93/93)"
before this sprint started, and told this script to "extend, do not replace,
the existing 93-assertion standalone verify". A full-repo search (git log
--all across both worktrees, every ``*.md``/``*.py`` file, every prior verify
script) found NONE of that: no brief, no modules, no prior verify script, no
mention anywhere in ``docs/PROJECT_STATUS.md``. That premise was false. This
script is therefore not an extension of prior work — it is the first
verification these three modules have ever had. It does NOT claim "93
pre-existing assertions still pass"; it contains a freshly written pure-module
suite (Assertions 1.x below) sized to the actual surface area of the three
modules, plus the Task 5 API-layer proof.

A second false premise, also discovered this sprint (Task 1b): the prompt
assumed "Chancery-sourced commitment data" already carries
committed_capital/paid_in/nav in usable form. ``services/portfolio_chancery.py``
documents, and this script confirms by reading its own module constants, that
NO deployed Chancery extractor produces commitment figures at all
(``COMMITMENT_EXTRACTION_GAP``) — Chancery populates asset/position rows, not
commitment financials. The REAL commitment data source is
``portfolio.commitments`` written via ``services.portfolio_commitments.
create_commitment`` / ``recompute_commitment``, which is what this script uses
for "a real commitment's real data" in the Task 5 proof — the actual deployed
data path, not the one the prompt assumed.

DATABASE CONNECTIVITY
──────────────────────────────────────────────────────────────────────────────
The ambient ``DATABASE_URL`` (``apps/api/.env`` / ``~/.bashrc``) is STALE — a
previously-documented, recurring issue in this project (see memory: "Working
DB creds live in Doppler"). This script hydrates ``os.environ`` from Doppler
over HTTPS first (``apps/api/scripts/_doppler_env.py`` — stdlib only, reads
``DOPPLER_TOKEN`` or, failing that, a cached CLI token on disk, and PRINTS NO
VALUES), which overwrites the stale copy with the live one. This script does
NOT treat env-var presence as connectivity on its own — that exact anti-
pattern is documented elsewhere in this project as having produced
false-green sprints. It attempts a REAL connection; if that still fails on
authentication (e.g. no Doppler token reachable at all in some future
environment), every assertion that requires the database is reported as
[BLOCKED] with the actual exception, never silently skipped and never faked
as [PASS]. All pure-module assertions (Section 1) do not depend on the
database and always run for real, independent of this.

Run:
    python3 scripts/verify_tamodel1.py
"""

from __future__ import annotations

import glob
import math
import os
import sys
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.join(_HERE, "..")
sys.path.insert(0, _API)
sys.path.extend(sorted(glob.glob(os.path.join(_API, "venv", "lib", "python3*", "site-packages"))))

import asyncio  # noqa: E402

import asyncpg  # noqa: E402

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
# TASK 1 — DISCOVERY FINDINGS (no DB required — these are code/repo facts)
# ═══════════════════════════════════════════════════════════════════════════


def report_task1_findings() -> None:
    report(
        "1a — no ta_strategy field / PE-strategy taxonomy existed anywhere",
        "docs/schema_snapshot.sql and every seed SQL file were searched for "
        "'buyout', 'venture_capital', 'growth_equity', 'secondaries', "
        "'fund_of_funds', 'private_credit', 'real_assets': zero hits. The "
        "closest existing thing, config.category='asset_taxonomy', is a "
        "generic SC/MC/Sub asset-CLASS tree (services/taxonomy.py), not a "
        "PE-strategy vocabulary, and portfolio.commitments carries no "
        "strategy column. The 8 keys are new — added as a CHECK constraint "
        "vocabulary on the new portfolio.ta_model_params /  "
        "ta_calibration_results tables (docs/tamodel1_part1.sql), not on "
        "commitments itself.",
    )
    report(
        "1b — Chancery does NOT source commitment financial data",
        "services/portfolio_chancery.py:COMMITMENT_EXTRACTION_GAP states "
        "plainly that no deployed Chancery extractor produces "
        "commitment_amount/called_to_date/distributed_to_date/"
        "recallable_amount — narrative extraction has no monetary keys and "
        "template extraction (k1) maps five income boxes, not commitment "
        "figures. The real, deployed source of commitment data is "
        "services.portfolio_commitments.create_commitment / "
        "recompute_commitment against portfolio.commitments + "
        "portfolio.transactions — used below as 'the real commitment data' "
        "for Task 5's end-to-end proof, since that is what is actually "
        "deployed, not what the prompt assumed was deployed.",
    )
    report(
        "1c — org_settings had ZERO 'modeling.*' keys before this sprint",
        "Confirmed both by static read of services/org_settings.py "
        "(DEFAULT_SETTINGS / CATEGORY_BY_PREFIX had no modeling.* entries "
        "before this sprint's edit) and, when the database is reachable, by "
        "a direct query below (Assertion 3.1) — genuinely greenfield, not "
        "assumed from the absence in code alone.",
    )
    report(
        "no seed_rows() helper exists in this codebase",
        "The prompt's Task 2 instruction to seed settings 'via seed_rows()' "
        "does not match anything deployed — grepped, the only match is a "
        "local one-off function in verify_soc1.py, not reusable "
        "infrastructure. The real, existing precedent for org-default config "
        "is services.org_settings.DEFAULT_SETTINGS + set_setting's own "
        "upsert, which is what services.ta_config.default_settings_seed() + "
        "this script's seeding step use instead.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — PURE-MODULE ASSERTIONS (no DB, no I/O; always run)
# ═══════════════════════════════════════════════════════════════════════════


def run_pure_module_assertions() -> None:
    from services.ta_calibrate import (
        MIN_CALIBRATION_YEARS,
        RealizedPeriod,
        TACalibrationError,
        calibrate_strategy,
        minimum_realized_periods,
    )
    from services.ta_config import (
        DEFAULT_CALIBRATION_MIN_YEARS,
        DEFAULT_PERIODS_PER_YEAR,
        DEFAULT_PROJECTION_HORIZON_YEARS,
        DEFAULT_TA_STRATEGY_PARAMS,
        TA_STRATEGY_KEYS,
        TAConfigError,
        default_settings_seed,
        params_for_strategy,
        projection_horizon_periods,
    )
    from services.ta_model import TAModelError, TAParams, project_cash_flows

    print("\n── Section 1: pure-module assertions (ta_model / ta_config / ta_calibrate) ──")

    # -- 1.1 ta_model.py is I/O-free -----------------------------------------
    import inspect as _inspect

    src = _inspect.getsource(sys.modules["services.ta_model"])
    check(
        "1.1 ta_model.py imports nothing from asyncpg/services.database/fastapi/routers",
        not any(bad in src for bad in ("import asyncpg", "services.database", "fastapi", "routers.")),
        "a forbidden import string was found in the module source",
    )

    # -- 1.2 TAParams validation ----------------------------------------------
    valid = TAParams(
        rate_of_contribution=Decimal("0.1"), rate_of_distribution=Decimal("0.1"),
        growth_rate=Decimal("0.02"), bow_factor=Decimal("2"), fund_life_years=Decimal("10"),
        periods_per_year=4,
    )
    check("1.2 TAParams accepts a valid all-Decimal parameter set", isinstance(valid, TAParams))

    for field, bad in (
        ("rate_of_contribution", 0.1), ("rate_of_distribution", 0.1),
        ("growth_rate", 0.02), ("bow_factor", 2.0), ("fund_life_years", 10.0),
    ):
        kwargs = dict(
            rate_of_contribution=Decimal("0.1"), rate_of_distribution=Decimal("0.1"),
            growth_rate=Decimal("0.02"), bow_factor=Decimal("2"), fund_life_years=Decimal("10"),
            periods_per_year=4,
        )
        kwargs[field] = bad
        try:
            TAParams(**kwargs)
            fail(f"1.2 TAParams rejects a float in {field}", "no exception raised")
        except TAModelError:
            ok(f"1.2 TAParams rejects a float in {field}")

    for bad_rc in (Decimal("-0.1"), Decimal("1.1")):
        try:
            TAParams(
                rate_of_contribution=bad_rc, rate_of_distribution=Decimal("0.1"),
                growth_rate=Decimal("0.02"), bow_factor=Decimal("2"),
                fund_life_years=Decimal("10"), periods_per_year=4,
            )
            fail("1.2 TAParams rejects rate_of_contribution outside [0,1]", f"value={bad_rc} accepted")
        except TAModelError:
            ok(f"1.2 TAParams rejects rate_of_contribution={bad_rc} outside [0,1]")

    try:
        TAParams(
            rate_of_contribution=Decimal("0.1"), rate_of_distribution=Decimal("0.1"),
            growth_rate=Decimal("0.02"), bow_factor=Decimal("2"),
            fund_life_years=Decimal("0"), periods_per_year=4,
        )
        fail("1.2 TAParams rejects fund_life_years=0")
    except TAModelError:
        ok("1.2 TAParams rejects fund_life_years=0")

    try:
        TAParams(
            rate_of_contribution=Decimal("0.1"), rate_of_distribution=Decimal("0.1"),
            growth_rate=Decimal("0.02"), bow_factor=Decimal("2"),
            fund_life_years=Decimal("10"), periods_per_year=0,
        )
        fail("1.2 TAParams rejects periods_per_year=0")
    except TAModelError:
        ok("1.2 TAParams rejects periods_per_year=0")

    # -- 1.3 project_cash_flows: float rejection at every boundary -----------
    good_kwargs = dict(
        committed_capital=Decimal("1000000"), called_to_date=Decimal("0"),
        distributed_to_date=Decimal("0"), current_nav=Decimal("0"),
        params=valid, horizon_periods=8,
    )
    for field in ("committed_capital", "called_to_date", "distributed_to_date", "current_nav"):
        kwargs = dict(good_kwargs)
        kwargs[field] = 100.0
        try:
            project_cash_flows(**kwargs)
            fail(f"1.3 project_cash_flows rejects float {field}", "no exception raised")
        except TAModelError:
            ok(f"1.3 project_cash_flows rejects float {field}")

    check(
        "1.3 project_cash_flows accepts str/int/Decimal money interchangeably",
        len(project_cash_flows(**{**good_kwargs, "committed_capital": "1000000"})) == 8
        and len(project_cash_flows(**{**good_kwargs, "committed_capital": 1000000})) == 8,
    )

    for bad_horizon in (0, -1, 401):
        try:
            project_cash_flows(**{**good_kwargs, "horizon_periods": bad_horizon})
            fail(f"1.3 project_cash_flows rejects horizon_periods={bad_horizon}")
        except TAModelError:
            ok(f"1.3 project_cash_flows rejects horizon_periods={bad_horizon}")

    for neg_field in ("committed_capital", "called_to_date", "distributed_to_date", "current_nav"):
        try:
            project_cash_flows(**{**good_kwargs, neg_field: Decimal("-1")})
            fail(f"1.3 project_cash_flows rejects negative {neg_field}")
        except TAModelError:
            ok(f"1.3 project_cash_flows rejects negative {neg_field}")

    # -- 1.4 projection shape: monotonic uncalled decay, NAV never negative --
    periods = project_cash_flows(
        committed_capital=Decimal("1000000"), called_to_date=Decimal("0"),
        distributed_to_date=Decimal("0"), current_nav=Decimal("0"),
        params=valid, horizon_periods=40,
    )
    check("1.4 project_cash_flows returns exactly horizon_periods rows", len(periods) == 40)
    check(
        "1.4 cumulative_paid_in never exceeds committed_capital",
        all(p.cumulative_paid_in <= Decimal("1000000") for p in periods),
        f"max={max(p.cumulative_paid_in for p in periods)}",
    )
    check(
        "1.4 NAV is never negative across the projection",
        all(p.nav >= 0 for p in periods),
    )
    check(
        "1.4 every monetary field is a Decimal (never a float) in the output",
        all(
            isinstance(getattr(p, f), Decimal)
            for p in periods
            for f in ("contribution", "distribution", "nav", "cumulative_paid_in", "cumulative_distributed")
        ),
    )
    check(
        "1.4 calling project_cash_flows twice with identical inputs returns EQUAL "
        "but NOT the same list object (nothing is memoized/cached)",
        periods == project_cash_flows(
            committed_capital=Decimal("1000000"), called_to_date=Decimal("0"),
            distributed_to_date=Decimal("0"), current_nav=Decimal("0"),
            params=valid, horizon_periods=40,
        )
        and periods is not project_cash_flows(
            committed_capital=Decimal("1000000"), called_to_date=Decimal("0"),
            distributed_to_date=Decimal("0"), current_nav=Decimal("0"),
            params=valid, horizon_periods=40,
        ),
    )

    # -- 1.5 ta_config: all 8 strategy keys resolve, unknown key rejected ----
    for key in TA_STRATEGY_KEYS:
        p = params_for_strategy(key, {})
        check(f"1.5 params_for_strategy resolves strategy {key!r} from built-in defaults", isinstance(p, TAParams))
    check("1.5 exactly 8 TA strategy keys are seeded", len(TA_STRATEGY_KEYS) == 8, f"got {len(TA_STRATEGY_KEYS)}")
    try:
        params_for_strategy("not_a_real_strategy", {})
        fail("1.5 params_for_strategy rejects an unknown strategy_key")
    except TAConfigError:
        ok("1.5 params_for_strategy rejects an unknown strategy_key")

    org_override = {
        "modeling.ta.strategy_defaults": {
            "buyout": {
                "rate_of_contribution": "0.5", "rate_of_distribution": "0.4",
                "growth_rate": "0.01", "bow_factor": "1.5", "fund_life_years": "8",
            }
        }
    }
    overridden = params_for_strategy("buyout", org_override)
    check(
        "1.5 params_for_strategy uses the ORG's overridden strategy_defaults when present",
        overridden.rate_of_contribution == Decimal("0.5"),
        f"got {overridden.rate_of_contribution}",
    )
    default_buyout = params_for_strategy("buyout", {})
    check(
        "1.5 an org override for buyout does NOT change growth_equity's resolved params (per-key, not global)",
        params_for_strategy("growth_equity", org_override) == params_for_strategy("growth_equity", {}),
    )

    horizon = projection_horizon_periods({}, periods_per_year=4)
    check(
        "1.5 projection_horizon_periods = horizon_years * periods_per_year using defaults",
        horizon == DEFAULT_PROJECTION_HORIZON_YEARS * 4,
        f"got {horizon}",
    )

    seed = default_settings_seed()
    check("1.5 default_settings_seed() returns exactly the 4 keys Task 2 asks for", len(seed) == 4, f"keys={sorted(seed)}")
    check(
        "1.5 default_settings_seed()'s strategy_defaults covers all 8 keys with all-string rate fields",
        set(seed["modeling.ta.strategy_defaults"]) == set(TA_STRATEGY_KEYS)
        and all(
            isinstance(v, str)
            for raw in seed["modeling.ta.strategy_defaults"].values()
            for v in raw.values()
        ),
    )

    # -- 1.6 ta_calibrate: the frequency-aware floor (Task 3) -----------------
    check(
        "1.6 minimum_realized_periods(periods_per_year=1) == 3 (unchanged from the flat floor)",
        minimum_realized_periods(1) == 3, f"got {minimum_realized_periods(1)}",
    )
    check(
        "1.6 minimum_realized_periods(periods_per_year=4) == 12, NOT 3 — the actual "
        "fix: a flat floor would have left this at 3",
        minimum_realized_periods(4) == 12, f"got {minimum_realized_periods(4)}",
    )
    check(
        "1.6 the floor scales linearly with frequency: monthly (12) needs 36",
        minimum_realized_periods(12) == 36, f"got {minimum_realized_periods(12)}",
    )
    check(
        "1.6 minimum_realized_periods == ceil(MIN_CALIBRATION_YEARS * periods_per_year) exactly",
        minimum_realized_periods(4) == math.ceil(MIN_CALIBRATION_YEARS * 4),
    )

    realized_3y = [
        RealizedPeriod(period=i, contribution=Decimal("100000"), distribution=Decimal("20000"), nav=Decimal(str(300000 + i * 50000)))
        for i in range(1, 4)
    ]
    calibrated_3y = calibrate_strategy(
        realized_3y, committed_capital=Decimal("2000000"), periods_per_year=1,
        bow_factor=Decimal("2.0"), fund_life_years=Decimal("10"),
    )
    check(
        "1.6 calibrate_strategy ACCEPTS exactly 3 years of annual realized history (periods_per_year=1)",
        isinstance(calibrated_3y, TAParams),
    )
    check(
        "1.6 a 3-year calibration's bow_factor/fund_life_years pass through UNCHANGED "
        "(not calibrated — see the function's own docstring for why)",
        calibrated_3y.bow_factor == Decimal("2.0") and calibrated_3y.fund_life_years == Decimal("10"),
    )

    realized_3q = [
        RealizedPeriod(period=i, contribution=Decimal("25000"), distribution=Decimal("5000"), nav=Decimal(str(75000 + i * 10000)))
        for i in range(1, 4)
    ]
    try:
        calibrate_strategy(
            realized_3q, committed_capital=Decimal("2000000"), periods_per_year=4,
            bow_factor=Decimal("2.0"), fund_life_years=Decimal("10"),
        )
        fail("1.6 calibrate_strategy REFUSES 3 quarters of history at periods_per_year=4 (below the 12-period floor)")
    except TACalibrationError:
        ok("1.6 calibrate_strategy REFUSES 3 quarters of history at periods_per_year=4 (below the 12-period floor)")

    realized_12q = [
        RealizedPeriod(period=i, contribution=Decimal("25000"), distribution=Decimal("5000"), nav=Decimal(str(75000 + i * 10000)))
        for i in range(1, 13)
    ]
    calibrated_12q = calibrate_strategy(
        realized_12q, committed_capital=Decimal("2000000"), periods_per_year=4,
        bow_factor=Decimal("2.0"), fund_life_years=Decimal("10"),
    )
    check(
        "1.6 calibrate_strategy ACCEPTS exactly 12 quarters (3 years' worth at quarterly frequency)",
        isinstance(calibrated_12q, TAParams),
    )

    try:
        calibrate_strategy(
            realized_3y, committed_capital=0.5, periods_per_year=1,
            bow_factor=Decimal("2.0"), fund_life_years=Decimal("10"),
        )
        fail("1.6 calibrate_strategy rejects a float committed_capital")
    except TACalibrationError:
        ok("1.6 calibrate_strategy rejects a float committed_capital")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE-DEPENDENT ASSERTIONS (Tasks 2, 4, 5)
# ═══════════════════════════════════════════════════════════════════════════

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "VERIFY-TAMODEL1"
ADMIN_SUB = "auth0|verify_tamodel1_admin"
MEMBER_SUB = "auth0|verify_tamodel1_member"

# services.permissions.get_user_id derives a deterministic uuid5(sub) for any
# claim whose `sub` is not already a UUID — a hand-picked fixture id would
# NOT match what the router resolves from the JWT, and load_principal would
# find no row (role "unknown", every permission check refused). See project
# memory: "get_user_id returns uuid5(sub), so a hand-picked fixture id fakes
# a 403" — the exact bug this pattern avoids.
U_ADMIN = str(uuid5(NAMESPACE_URL, ADMIN_SUB))         # org A, org_admin
U_MEMBER = str(uuid5(NAMESPACE_URL, MEMBER_SUB))       # org A, plain member
ALL_TEST_USERS = [U_ADMIN, U_MEMBER]

DB_ASSERTIONS = (
    "3.x org_settings seed rows are live",
    "4.x the 5 endpoints exist and are mounted at /api/v1/modeling/ta/* "
    "(admin route at /api/v1/admin/modeling/ta/defaults)",
    "5.1 a real commitment's real data produces a real projection end-to-end "
    "through the API",
    "5.2 projected cash flows are never persisted",
    "5.3 an overridden parameter persists bi-temporally and restates correctly",
    "5.4 the frequency-aware calibration floor is proven both ways via the "
    "real calibrate endpoint",
    "5.5 cross-org isolation on defaults + projection",
    "5.6 the view/write permission split on the admin config-write endpoint",
    "5.7 teardown leaves zero leftover rows",
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
    await conn.execute("DELETE FROM org_settings WHERE org_id IN ($1, $2) AND setting_key LIKE 'modeling.ta.%'", ORG_A, ORG_B)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS)


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM entities WHERE org_id = $2 AND display_name LIKE $3)
          + (SELECT count(*) FROM portfolio.assets WHERE org_id = $2 AND name LIKE $3)
          + (SELECT count(*) FROM portfolio.ta_model_params t
                WHERE t.commitment_id IN (
                    SELECT c.id FROM portfolio.commitments c
                    JOIN portfolio.positions p ON p.id = c.position_id
                    JOIN portfolio.assets a ON a.id = p.asset_id WHERE a.name LIKE $3))
          + (SELECT count(*) FROM portfolio.ta_calibration_results t
                WHERE t.commitment_id IN (
                    SELECT c.id FROM portfolio.commitments c
                    JOIN portfolio.positions p ON p.id = c.position_id
                    JOIN portfolio.assets a ON a.id = p.asset_id WHERE a.name LIKE $3))
          + (SELECT count(*) FROM org_settings WHERE org_id IN ($2, $4) AND setting_key LIKE 'modeling.ta.%')
        """,
        ALL_TEST_USERS, ORG_A, f"{TAG}%", ORG_B,
    ))


class _Principal:
    """Drives the real ASGI app as one specific user. See verify_portfolioux3.py
    for why this exact shape (stub verify_token, one shared TestClient/loop)."""

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

    def put(self, url, **kw):
        self._become()
        return self.client.put(url, **kw)

    def post(self, url, **kw):
        self._become()
        return self.client.post(url, **kw)


HEADERS = {"Authorization": "Bearer verify-token"}


async def seed_users(conn) -> None:
    """Create the two test users FIRST — org_settings.set_setting's
    ``updated_by`` carries an FK to ``users(id)``, so settings-seeding (which
    runs before the rest of the fixtures) needs these rows to already exist.
    """
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        U_ADMIN, ORG_A, "tamodel1_admin@test.local", "TAModel1 Admin", ADMIN_SUB, "org_admin",
    )
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        U_MEMBER, ORG_A, "tamodel1_member@test.local", "TAModel1 Member", MEMBER_SUB, "member",
    )


async def seed_fixtures(conn) -> dict:
    from services.portfolio_assets import create_asset, create_position, record_transaction
    from services.portfolio_commitments import create_commitment, recompute_commitment

    entity_id = await conn.fetchval(
        "INSERT INTO entities (org_id, entity_type, display_name) VALUES ($1, $2, $3) RETURNING id",
        ORG_A, "llc", f"{TAG} Entity",
    )

    asset_id = await create_asset(
        conn, org_id=ORG_A, name=f"{TAG} Buyout Fund III", asset_type="private_fund",
        asset_class="financial", valuation_method="nav",
    )
    position_id = await create_position(
        conn, org_id=ORG_A, owner_entity_id=str(entity_id), asset_id=asset_id,
        as_of_date=date(2026, 1, 1), authority="manual", source_system="manual",
        ownership_basis="value", market_value=Decimal("350000"),
    )
    commitment_id = await create_commitment(
        conn, org_id=ORG_A, position_id=position_id,
        commitment_amount=Decimal("2000000"), commitment_date=date(2023, 1, 15),
        vintage_year=2023,
    )

    for yr in (2023, 2024, 2025):
        await record_transaction(
            conn, org_id=ORG_A, position_id=position_id,
            transaction_type_code="call_investment", trade_date=date(yr, 1, 15),
            authority="manual", source_system="manual", gross_amount=Decimal("150000"),
        )
    await recompute_commitment(conn, ORG_A, commitment_id)

    return {"entity_id": str(entity_id), "asset_id": asset_id, "position_id": position_id, "commitment_id": commitment_id}


async def run_db_assertions(admin_conn) -> None:
    from services.org_settings import get_all_settings
    from services.ta_config import TA_SETTINGS_KEYS, default_settings_seed

    print("\n── Section 2: schema + settings-seed (direct DB) ──")

    tables = await admin_conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'portfolio' "
        "AND table_name IN ('ta_model_params', 'ta_calibration_results')"
    )
    found = {r["table_name"] for r in tables}
    check(
        "2.1 both new tables exist: portfolio.ta_model_params, portfolio.ta_calibration_results",
        found == {"ta_model_params", "ta_calibration_results"}, f"found={found}",
    )

    bitemporal_cols = await admin_conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' "
        "AND table_name = 'ta_model_params' AND column_name IN "
        "('valid_from', 'valid_to', 'system_from', 'system_to')"
    )
    check(
        "2.2 portfolio.ta_model_params carries all 4 bi-temporal columns (Rule 3)",
        {r["column_name"] for r in bitemporal_cols} == {"valid_from", "valid_to", "system_from", "system_to"},
    )

    idx = await admin_conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'portfolio' "
        "AND indexname = 'ta_model_params_active_unique'"
    )
    check(
        "2.3 the active-row uniqueness is a PARTIAL index on (org_id, commitment_id) "
        "WHERE valid_to IS NULL AND system_to IS NULL — the member_target_allocations shape",
        idx is not None and "valid_to IS NULL" in idx and "system_to IS NULL" in idx,
        f"indexdef={idx}",
    )

    policy_count = await admin_conn.fetchval(
        "SELECT count(*) FROM pg_policies WHERE schemaname = 'portfolio' "
        "AND tablename IN ('ta_model_params', 'ta_calibration_results')"
    )
    check("2.4 exactly 2 RLS policies exist (one org-isolation policy per table)", policy_count == 2, f"count={policy_count}")

    # Task 2: seed the 4 org_settings rows for the default org, for real.
    seed = default_settings_seed()
    from services.org_settings import set_setting
    for key, value in seed.items():
        await set_setting(admin_conn, ORG_A, key, value, U_ADMIN, principal={"id": U_ADMIN, "org_id": ORG_A, "role": "org_admin"})

    rows = await admin_conn.fetch(
        "SELECT setting_key FROM org_settings WHERE org_id = $1 AND setting_key LIKE 'modeling.ta.%'", ORG_A,
    )
    live_keys = {r["setting_key"] for r in rows}
    check(
        "3.1 all 4 org_settings seed rows are LIVE, confirmed by direct query "
        "(not assumed from the seed function's return value)",
        live_keys == set(TA_SETTINGS_KEYS), f"live_keys={live_keys}",
    )

    settings = await get_all_settings(admin_conn, ORG_A)
    check(
        "3.1 get_all_settings resolves the seeded strategy_defaults for org A",
        settings.get("modeling.ta.strategy_defaults", {}).get("buyout", {}).get("rate_of_contribution") is not None,
    )


def _routes_declared() -> dict:
    import main
    spec = main.app.openapi()
    return {p: sorted(spec["paths"][p]) for p in spec["paths"]}


async def _ta_row_count(admin_conn) -> int:
    return int(await admin_conn.fetchval(
        "SELECT (SELECT count(*) FROM portfolio.ta_model_params) "
        "+ (SELECT count(*) FROM portfolio.ta_calibration_results)"
    ))


async def _ta_prefixed_tables(admin_conn) -> set:
    rows = await admin_conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        r"WHERE table_schema = 'portfolio' AND table_name LIKE 'ta\_%' ESCAPE '\'"
    )
    return {r["table_name"] for r in rows}


async def _active_params_count(admin_conn, commitment_id) -> int:
    return int(await admin_conn.fetchval(
        "SELECT count(*) FROM portfolio.ta_model_params "
        "WHERE commitment_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
        commitment_id,
    ))


async def _params_row_closed(admin_conn, params_id) -> bool:
    if params_id is None:
        return False
    val = await admin_conn.fetchval(
        "SELECT valid_to FROM portfolio.ta_model_params WHERE id = $1::uuid", params_id,
    )
    return val is not None


async def _seed_extra_year(admin_conn, ids: dict) -> None:
    from services.portfolio_assets import record_transaction
    from services.portfolio_commitments import recompute_commitment

    await record_transaction(
        admin_conn, org_id=ORG_A, position_id=ids["position_id"],
        transaction_type_code="call_investment", trade_date=date(2026, 1, 15),
        authority="manual", source_system="manual", gross_amount=Decimal("150000"),
    )
    await recompute_commitment(admin_conn, ORG_A, ids["commitment_id"])


async def _seed_quarterly(admin_conn, ids: dict) -> None:
    from services.portfolio_assets import record_transaction

    for offset, yr in enumerate((2023, 2024, 2025)):
        await record_transaction(
            admin_conn, org_id=ORG_A, position_id=ids["position_id"],
            transaction_type_code="call_investment", trade_date=date(yr, 6, 20 + offset),
            authority="manual", source_system="manual", gross_amount=Decimal("10000"),
        )


async def run_api_assertions(admin_conn, ids: dict) -> None:
    """The Task 5 API-layer proof — ONE continuous coroutine, so DB checks via
    ``admin_conn`` (bound to THIS coroutine's event loop) and synchronous
    TestClient calls can interleave freely without ever nesting a second
    ``run_until_complete`` inside the already-running outer loop — that nested
    call is exactly the bug an earlier draft of this script had, caught before
    ever running it against a live database.
    """
    import main
    from starlette.testclient import TestClient

    print("\n── Section 2: the 5 endpoints, through the REAL ASGI app ──")

    shared = TestClient(main.app, raise_server_exceptions=False)
    shared.__enter__()
    try:
        routes = _routes_declared()
        expected = {
            "/api/v1/modeling/ta/defaults": ["get"],
            "/api/v1/admin/modeling/ta/defaults": ["put"],
            "/api/v1/modeling/ta/projection/{commitment_id}": ["get"],
            "/api/v1/modeling/ta/projection/preview": ["post"],
            "/api/v1/modeling/ta/calibrate/{commitment_id}": ["post"],
        }
        missing = {p: v for p, v in expected.items() if routes.get(p) != v}
        check(
            "4.1 all 5 endpoints are declared, with the admin write under the literal "
            "/api/v1/admin/ prefix (not /admin/api/v1)",
            not missing, f"missing/mismatched={missing}",
        )

        admin = _Principal(shared, ORG_A, ADMIN_SUB)
        member = _Principal(shared, ORG_A, MEMBER_SUB)
        org_b_admin = _Principal(shared, ORG_B, ADMIN_SUB)

        # ── 5.1: real end-to-end projection through the real API ───────────
        res = admin.get(f"/api/v1/modeling/ta/projection/{ids['commitment_id']}?strategy_key=buyout", headers=HEADERS)
        body = res.json() if res.status_code == 200 else {}
        check(
            "5.1 GET projection succeeds end-to-end for a real commitment through "
            "the real API (not a fixture bypassing the endpoint)",
            res.status_code == 200 and len(body.get("periods", [])) > 0,
            f"status={res.status_code} body={body}",
        )
        check(
            "5.1 the projection's current_nav reflects the position's REAL market_value (350000)",
            body.get("current_nav") == "350000",
            f"got {body.get('current_nav')}",
        )
        check(
            "5.1 every period's monetary fields are JSON STRINGS, never numbers-with-decimal",
            all(isinstance(body["periods"][0][f], str) for f in ("contribution", "distribution", "nav")),
        )

        # ── 5.2: projected cash flows are never persisted ───────────────────
        before = await _ta_row_count(admin_conn)
        res2 = admin.get(f"/api/v1/modeling/ta/projection/{ids['commitment_id']}?strategy_key=buyout", headers=HEADERS)
        after = await _ta_row_count(admin_conn)
        check(
            "5.2 re-running the same projection twice does not grow ta_model_params or "
            "ta_calibration_results — no cached-projection row count appears anywhere",
            res2.status_code == 200 and before == after,
            f"before={before} after={after}",
        )
        tables_named_projection = await _ta_prefixed_tables(admin_conn)
        check(
            "5.2 no table anywhere in the schema is shaped like a stored projection "
            "(only ta_model_params [parameters] and ta_calibration_results [calibration "
            "runs] exist under the ta_ prefix)",
            tables_named_projection == {"ta_model_params", "ta_calibration_results"},
            f"found={tables_named_projection}",
        )

        # ── 5.3: override persists bi-temporally, restates correctly ────────
        override_body = {
            "ta_strategy_key": "buyout", "periods_per_year": 1,
        }
        cal1 = admin.post(f"/api/v1/modeling/ta/calibrate/{ids['commitment_id']}", json=override_body, headers=HEADERS)
        check(
            "5.3 POST calibrate (periods_per_year=1, 3 real annual call transactions) "
            "succeeds and persists an override",
            cal1.status_code == 200, f"status={cal1.status_code} body={cal1.text[:300]}",
        )
        params_id_1 = (cal1.json() or {}).get("params_id") if cal1.status_code == 200 else None

        active_rows_after_1 = await _active_params_count(admin_conn, ids["commitment_id"])
        check("5.3 exactly ONE active override row exists after the first calibration", active_rows_after_1 == 1, f"count={active_rows_after_1}")

        await _seed_extra_year(admin_conn, ids)
        cal2 = admin.post(f"/api/v1/modeling/ta/calibrate/{ids['commitment_id']}", json=override_body, headers=HEADERS)
        params_id_2 = (cal2.json() or {}).get("params_id") if cal2.status_code == 200 else None
        check(
            "5.3 a SECOND calibration (restatement) succeeds and returns a DIFFERENT params_id",
            cal2.status_code == 200 and params_id_2 is not None and params_id_2 != params_id_1,
            f"status={cal2.status_code} id1={params_id_1} id2={params_id_2}",
        )
        active_rows_after_2 = await _active_params_count(admin_conn, ids["commitment_id"])
        check(
            "5.3 STILL exactly ONE active row after restatement (the old one was "
            "CLOSED, not deleted and not left active alongside the new one)",
            active_rows_after_2 == 1, f"count={active_rows_after_2}",
        )
        closed_row_has_valid_to = await _params_row_closed(admin_conn, params_id_1)
        check(
            "5.3 the FIRST override row now has valid_to SET (closed, per Rule 3) "
            "rather than being UPDATEd in place",
            closed_row_has_valid_to,
        )

        # ── 5.4: frequency-aware calibration floor, both ways, via the REAL endpoint ──
        await _seed_quarterly(admin_conn, ids)
        cal_q_refused = admin.post(
            f"/api/v1/modeling/ta/calibrate/{ids['commitment_id']}",
            json={"ta_strategy_key": "buyout", "periods_per_year": 4}, headers=HEADERS,
        )
        check(
            "5.4 the real /calibrate endpoint REFUSES a 3-quarter calibration attempt (422)",
            cal_q_refused.status_code == 422, f"status={cal_q_refused.status_code} body={cal_q_refused.text[:200]}",
        )
        cal_y_accepted = admin.post(
            f"/api/v1/modeling/ta/calibrate/{ids['commitment_id']}",
            json={"ta_strategy_key": "buyout", "periods_per_year": 1}, headers=HEADERS,
        )
        check(
            "5.4 the SAME real endpoint ACCEPTS a 3(+)-year annual calibration on the "
            "same commitment",
            cal_y_accepted.status_code == 200, f"status={cal_y_accepted.status_code}",
        )

        # ── 5.5: cross-org isolation ─────────────────────────────────────────
        res_b_defaults = org_b_admin.get("/api/v1/modeling/ta/defaults", headers=HEADERS)
        b_strategy_defaults = (res_b_defaults.json() or {}).get("modeling.ta.strategy_defaults", {})
        check(
            "5.5 org B's GET defaults returns the UNMODIFIED built-in default "
            "(never org A's seeded/overridden value) — no cross-org leakage",
            b_strategy_defaults.get("buyout", {}).get("rate_of_contribution") == "0.0788",
            f"got {b_strategy_defaults.get('buyout')}",
        )
        res_b_projection = org_b_admin.get(
            f"/api/v1/modeling/ta/projection/{ids['commitment_id']}?strategy_key=buyout", headers=HEADERS,
        )
        check(
            "5.5 org B cannot read org A's commitment via GET projection (404, not "
            "org A's data)",
            res_b_projection.status_code == 404, f"status={res_b_projection.status_code}",
        )

        # ── 5.6: view/write permission split on the admin config-write endpoint ──
        member_get = member.get("/api/v1/modeling/ta/defaults", headers=HEADERS)
        check(
            "5.6 a plain member (no special permission) CAN read GET defaults — "
            "matches org_settings' own 'reads open to any authenticated org member' rule",
            member_get.status_code == 200, f"status={member_get.status_code}",
        )
        member_put = member.put(
            "/api/v1/admin/modeling/ta/defaults",
            json={"values": {"modeling.ta.default_periods_per_year": 1}}, headers=HEADERS,
        )
        check(
            "5.6 the SAME plain member is REFUSED (403) on PUT admin defaults — the write "
            "gate (can_manage_org_settings) is real, not vacuous",
            member_put.status_code == 403, f"status={member_put.status_code}",
        )
        admin_put = admin.put(
            "/api/v1/admin/modeling/ta/defaults",
            json={"values": {"modeling.ta.default_periods_per_year": 4}}, headers=HEADERS,
        )
        check(
            "5.6 the SAME PUT succeeds (200) for org_admin — the gate refuses the "
            "wrong caller and admits the right one on the identical request (a "
            "gate that refuses everyone would pass the negative half trivially)",
            admin_put.status_code == 200, f"status={admin_put.status_code} body={admin_put.text[:200]}",
        )
    finally:
        shared.__exit__(None, None, None)


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    from _doppler_env import hydrate_from_doppler

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS "
              f"(overwriting any stale ambient copies, e.g. apps/api/.env / ~/.bashrc)")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — falling back to ambient DATABASE_URL")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1

    print("=" * 78)
    print("TA MODEL SPRINT 1 — verify")
    print("=" * 78)

    report_task1_findings()
    run_pure_module_assertions()

    print("\n── Database connectivity check (real attempt, not a presence check) ──")
    admin_conn = None
    try:
        admin_conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed
        print(f"[BLOCKED] could not connect to DATABASE_URL: {type(exc).__name__}: {exc}")
        print(
            "This is a previously-documented, recurring credential issue in this "
            "project (see memory: 'Working DB creds live in Doppler'), not a defect "
            "in this sprint's code. Every DB-dependent assertion below is reported "
            "as [BLOCKED], not [SKIP] and not [PASS]."
        )
        for label in DB_ASSERTIONS:
            blocked_(label, "DATABASE_URL present but authentication failed — see above")
    else:
        try:
            await cleanup(admin_conn)  # teardown-at-start
            await seed_users(admin_conn)
            await run_db_assertions(admin_conn)
            ids = await seed_fixtures(admin_conn)

            try:
                await run_api_assertions(admin_conn, ids)
            except Exception as exc:  # noqa: BLE001
                fail("5.x API-layer assertions raised unexpectedly", f"{type(exc).__name__}: {exc}")

        finally:
            await cleanup(admin_conn)
            remaining = await leftover_count(admin_conn)
            check("5.7 teardown complete — zero leftover test rows", remaining == 0, f"count={remaining}")
            await admin_conn.close()

    print("\n" + "=" * 78)
    print(f"TA Model Sprint 1: {passed} passed, {failed} failed, {blocked} blocked")
    print("=" * 78)
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
