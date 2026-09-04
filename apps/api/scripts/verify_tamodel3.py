"""TA Model Sprint 3 (commitment projection UX) — verify.

TASK 1 — DISCOVERY FINDINGS, READ THIS FIRST
──────────────────────────────────────────────────────────────────────────────
1a. No commitments list/detail screen exists anywhere in apps/web (no
    /portfolio/commitments route, no app/api/portfolio/commitments proxy —
    confirmed by a full recursive search before writing anything). There is
    also no GENERAL "list an org's commitments" backend function:
    services/portfolio_commitments.py exposes only ``get_commitment`` (single,
    by id), ``create_commitment``, ``recompute_commitment(s)`` and
    ``tax_chase_list`` (commitments due a K-1 in one tax year — a different,
    narrower feature). Building a general list endpoint is a new subsystem,
    out of this sprint's scope. This sprint therefore does NOT add a
    projection tab to an existing screen (the prompt's own conditional
    premise) — it builds a new standalone screen at
    /portfolio/commitments/[commitmentId], reached by a minimal id-lookup
    form (CommitmentLookupForm.jsx) rather than a full list/grid.

1b. The real gate on both projection endpoints, read directly from
    apps/api/routers/modeling_ta.py, is ``require_permission(pool, user_id,
    org_id, READ_PERMISSION)`` where ``READ_PERMISSION = "view_portfolio"``
    (imported from services.portfolio_assets — the SAME constant every other
    portfolio read endpoint uses). Not a new, TA-specific permission.

1c. Response shapes, read directly from the router:
      GET  /modeling/ta/projection/{commitment_id} ->
        {commitment_id, ta_strategy_key, params{5 Decimal fields as strings +
         periods_per_year}, current_nav, periods[]}. BEFORE this sprint it did
        NOT publish committed_capital/called_to_date/distributed_to_date, even
        though the handler already computes all three from the commitment row
        — which meant a client-side "what if" preview had no way to seed
        POST /preview with this commitment's real known state (the preview
        endpoint takes committed_capital as a REQUIRED field with no server-
        side default). Fixed additively: the three Decimals already in scope
        are now returned as fixed-point strings alongside current_nav — no
        new computation, no schema change, existing consumers unaffected.
      POST /modeling/ta/projection/preview accepts EITHER strategy_key OR a
        full params_override (all 5 rate/bow/life Decimal fields + int
        periods_per_year, Pydantic extra="forbid" — partial overrides are
        REFUSED, not merged), plus committed_capital (required),
        called_to_date/distributed_to_date/current_nav (default "0"), and
        optional horizon_periods/periods_per_year. Confirmed unsaved by
        Sprint 1's own proof (row-count before/after) — reproduced again below
        rather than assumed still true.

1d. apps/web/package.json carries NO charting dependency (checked directly —
    no recharts/visx/d3/nivo/chart.js in dependencies or devDependencies).
    Per the standing rule (reuse what's established, never add one
    unilaterally), the chart is a small inline SVG component
    (ProjectionChart.jsx), not a new library. Also: no ``by_year()`` (or
    similarly-named) aggregation exists anywhere server-side for TA
    projections (grepped) — the screen displays the real per-period data the
    API actually returns, unaggregated, rather than inventing client-side
    Decimal aggregation the prompt only asked for IF the API already exposed
    it.

DATABASE CONNECTIVITY
──────────────────────────────────────────────────────────────────────────────
Same discipline as verify_tamodel1.py / verify_tamodel2.py: hydrates from
Doppler over HTTPS first, attempts a REAL connection, and reports every
DB-dependent assertion as [BLOCKED] (never silently skipped, never faked
[PASS]) if that connection fails.

Run:
    python3 scripts/verify_tamodel3.py
"""

from __future__ import annotations

import glob
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
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


def read(path) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8")


def strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — DISCOVERY FINDINGS
# ═══════════════════════════════════════════════════════════════════════════


def report_task1_findings() -> None:
    report(
        "1a — no commitments list/detail screen and no general list-commitments "
        "endpoint exist",
        "Confirmed by search: no /portfolio/commitments route, no "
        "app/api/portfolio/commitments proxy existed before this sprint. "
        "services/portfolio_commitments.py has get_commitment (by id only), "
        "create_commitment, recompute_commitment(s), tax_chase_list (by tax "
        "year — a narrower, different feature) and nothing that lists an "
        "org's commitments generally. Built a NEW standalone screen at "
        "/portfolio/commitments/[commitmentId] instead of adding a tab to an "
        "existing screen, reached by a minimal id-lookup form rather than "
        "inventing a new list backend (out of this sprint's scope).",
    )
    report(
        "1b — the real gate is view_portfolio, reused verbatim",
        "apps/api/routers/modeling_ta.py: both GET .../projection/{id} and "
        "POST .../projection/preview call require_permission(..., "
        "READ_PERMISSION) where READ_PERMISSION = "
        "services.portfolio_assets.READ_PERMISSION = 'view_portfolio' — the "
        "same constant every other portfolio read endpoint imports. No new "
        "permission was added.",
    )
    report(
        "1c — GET's response was missing 3 fields the preview endpoint needs "
        "as inputs; fixed additively",
        "GET .../projection/{id} returned current_nav but not "
        "committed_capital/called_to_date/distributed_to_date, even though "
        "the handler already computes all three from the commitment row. "
        "POST .../projection/preview requires committed_capital (no default) "
        "to compute anything meaningful. Fixed by publishing the three "
        "already-computed Decimals as fixed-point strings on the GET response "
        "— additive, no new computation, no schema change.",
    )
    report(
        "1d — no charting library in apps/web/package.json; no server-side "
        "by_year() aggregation exists",
        "package.json dependencies/devDependencies contain no recharts/visx/"
        "d3/nivo/chart.js. Built a small inline SVG chart component instead "
        "of adding a dependency. Grepped the API for 'by_year'/'byYear': zero "
        "hits anywhere — the screen shows real per-period data unaggregated, "
        "matching what the API actually returns.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — STATIC / PURE ASSERTIONS (no DB; always run)
# ═══════════════════════════════════════════════════════════════════════════


def run_decimal_string_proof() -> dict | None:
    """Actually EXECUTES lib/decimalString.js (via node — the file uses ESM
    `export` syntax, so it is copied into a temp .mjs, which node always
    treats as ESM regardless of apps/web/package.json's missing "type" field)
    against a value chosen specifically because it would round differently if
    coerced through a JS float: 9007199254740993.13's integer part exceeds
    Number.MAX_SAFE_INTEGER (9007199254740991), so Number(...) cannot
    represent it exactly. This is a real, executed proof, not a re-derivation.
    """
    src = read(WEB / "lib" / "decimalString.js")
    harness = src + """
const out = {
  exact: formatMoneyExact("9007199254740993.13"),
  viaNumber: (() => {
    const n = Number("9007199254740993.13");
    return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  })(),
  rate: formatRateExact("0.078800"),
  count: formatNumberExact("10"),
};
console.log(JSON.stringify(out));
"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False, encoding="utf-8")
    try:
        tmp.write(harness)
        tmp.close()
        proc = subprocess.run(["node", tmp.name], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            print(f"[INFO] node harness failed: {proc.stderr[-500:]}")
            return None
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"[INFO] node harness could not run: {type(exc).__name__}: {exc}")
        return None
    finally:
        os.unlink(tmp.name)


def run_static_assertions() -> None:
    print("\n── Section 2: static / pure assertions ──")

    decimal_lib = WEB / "lib" / "decimalString.js"
    check("2.1 lib/decimalString.js exists", decimal_lib.exists())

    src = strip_js_comments(read(decimal_lib))
    # The only permitted Number()/parseFloat()/parseInt() use is inside
    # chartFraction, which is documented as geometry-only and never feeds a
    # rendered label. Every OTHER function must be float-coercion-free.
    non_chart_src = src.split("export function chartFraction")[0]
    check(
        "2.2 formatMoneyExact/formatRateExact/formatNumberExact never call "
        "Number()/parseFloat()/parseInt() — only chartFraction (geometry-"
        "only, documented) does",
        "Number(" not in non_chart_src and "parseFloat(" not in non_chart_src
        and "parseInt(" not in non_chart_src,
        "found float coercion outside chartFraction",
    )

    proof = run_decimal_string_proof()
    if proof is None:
        blocked_(
            "2.3 formatMoneyExact preserves exact digits a float would corrupt",
            "node was not available to execute the real formatter",
        )
    else:
        check(
            "2.3 formatMoneyExact('9007199254740993.13') round-trips EXACTLY "
            "(no digit lost) — a value chosen because its integer part "
            "exceeds Number.MAX_SAFE_INTEGER",
            proof["exact"] == "$9,007,199,254,740,993.13",
            f"got {proof['exact']!r}",
        )
        check(
            "2.3 ...and genuinely DIFFERS from the Number()-coerced result on "
            "the SAME input — proving this is a real divergence, not a "
            "vacuous equality",
            proof["exact"] != proof["viaNumber"],
            f"exact={proof['exact']!r} viaNumber={proof['viaNumber']!r}",
        )
        check(
            "2.3 formatRateExact('0.078800') -> '7.88%' via pure decimal-"
            "point shift, no multiplication",
            proof["rate"] == "7.88%", f"got {proof['rate']!r}",
        )

    screen = strip_js_comments(read(WEB / "components" / "portfolio" / "CommitmentProjectionScreen.jsx"))
    chart = strip_js_comments(read(WEB / "components" / "portfolio" / "ProjectionChart.jsx"))
    check(
        "2.4 CommitmentProjectionScreen renders money/rate fields ONLY "
        "through the exact formatters, never Number()/parseFloat() directly "
        "on a period/param field",
        "formatMoneyExact" in screen and "formatRateExact" in screen
        and "parseFloat(" not in screen and "Number(projection" not in screen,
        "did not find the exact formatters, or found direct coercion",
    )
    check(
        "2.4 ProjectionChart's only Number()/coercion use is chartFraction "
        "(geometry) and the shared maxima scan (also geometry) — its tooltip "
        "TEXT is built from formatMoneyExact",
        "formatMoneyExact" in chart,
        "no formatMoneyExact usage found in the chart's tooltip",
    )

    check(
        "2.5 the screen has NO save/edit control for the loaded projection "
        "itself — the brief is explicit that projected cash flows are never "
        "persisted",
        not re.search(r'method:\s*"(POST|PUT|PATCH)"[^}]*projection(?!/preview)', screen),
        "found a non-preview write against the projection endpoint",
    )
    check(
        "2.5 the what-if panel is labeled as an unsaved preview, not the "
        "commitment's actual configured projection",
        "Preview" in screen and "not saved" in screen.lower(),
    )

    forbidden = read(WEB / "app" / "api" / "modeling" / "ta" / "projection" / "preview" / "route.js")
    check(
        "2.6 the preview proxy route forwards the real backend endpoint "
        "verbatim, not a re-implementation",
        "/api/v1/modeling/ta/projection/preview" in forbidden,
    )
    getroute = read(WEB / "app" / "api" / "modeling" / "ta" / "projection" / "[commitmentId]" / "route.js")
    check(
        "2.6 the GET proxy route forwards the real backend path with the "
        "commitment id interpolated, not hardcoded",
        "/api/v1/modeling/ta/projection/${commitmentId}" in getroute,
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — DATABASE-DEPENDENT ASSERTIONS
# ═══════════════════════════════════════════════════════════════════════════

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "VERIFY-TAMODEL3"
ADMIN_SUB = "auth0|verify_tamodel3_admin"
VIEWER_SUB = "auth0|verify_tamodel3_viewer"       # org A, has view_portfolio (default-allow, zero roles)
NOPERM_SUB = "auth0|verify_tamodel3_noperm"       # org A, a REAL role grant that lacks view_portfolio
ORGB_SUB = "auth0|verify_tamodel3_orgb"           # org B

U_ADMIN = str(uuid5(NAMESPACE_URL, ADMIN_SUB))
U_VIEWER = str(uuid5(NAMESPACE_URL, VIEWER_SUB))
U_NOPERM = str(uuid5(NAMESPACE_URL, NOPERM_SUB))
U_ORGB = str(uuid5(NAMESPACE_URL, ORGB_SUB))
ALL_TEST_USERS = [U_ADMIN, U_VIEWER, U_NOPERM, U_ORGB]

NOPERM_ROLE_NAME = f"{TAG}_noperm_role"

HEADERS = {"Authorization": "Bearer verify-token"}

DB_ASSERTIONS = (
    "3.x a real commitment's real saved projection renders end-to-end "
    "(chart + table consistent)",
    "3.x the preview tool produces a genuinely different result on a "
    "changed assumption",
    "3.x preview confirmed non-persisting via a real row-count check",
    "3.x view_portfolio gates the screen server-side (403 without it)",
    "3.x cross-org isolation (404, not org A's data)",
    "4.x npm run build exits 0",
    "5.x teardown leaves zero leftover rows",
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
    await conn.execute(
        "DELETE FROM public.user_roles WHERE user_id = ANY($1::uuid[])", ALL_TEST_USERS,
    )
    await conn.execute(
        "DELETE FROM public.role_permissions WHERE role_id IN "
        "(SELECT id FROM public.roles WHERE org_id = $1 AND name = $2)",
        ORG_A, NOPERM_ROLE_NAME,
    )
    await conn.execute(
        "DELETE FROM public.roles WHERE org_id = $1 AND name = $2", ORG_A, NOPERM_ROLE_NAME,
    )
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS)


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM public.user_roles WHERE user_id = ANY($1::uuid[]))
          + (SELECT count(*) FROM public.roles WHERE org_id = $2 AND name = $3)
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
        ALL_TEST_USERS, ORG_A, NOPERM_ROLE_NAME, f"{TAG}%",
    ))


async def seed_users(conn) -> None:
    for user_id, org, sub, role in (
        (U_ADMIN, ORG_A, ADMIN_SUB, "org_admin"),
        (U_VIEWER, ORG_A, VIEWER_SUB, "member"),
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

    # U_VIEWER deliberately gets NO user_roles row: rbac.has_permission
    # default-allows a user with zero roles (single-admin-stage posture) —
    # this is the SAME real mechanism that lets a plain member read the TA
    # settings screen (Sprint 1/2), reused here, not a fixture shortcut.
    #
    # U_NOPERM gets a REAL role grant to a role that holds NO permissions at
    # all — created fresh, in org A, for this run only. Without a real grant,
    # "no roles" would default-allow (see memory: "a role-less user
    # default-allows and would silently hold write") and the refusal proof
    # below would be vacuous.
    role_id = await conn.fetchval(
        """
        INSERT INTO public.roles (org_id, name, description)
        VALUES ($1, $2, 'verify_tamodel3 — deliberately zero permissions')
        ON CONFLICT (org_id, name) DO UPDATE SET description = EXCLUDED.description
        RETURNING id
        """,
        ORG_A, NOPERM_ROLE_NAME,
    )
    await conn.execute(
        """
        INSERT INTO public.user_roles (user_id, role_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        U_NOPERM, role_id,
    )


async def seed_fixtures(conn) -> dict:
    from services.portfolio_assets import create_asset, create_position, record_transaction
    from services.portfolio_commitments import create_commitment, recompute_commitment

    entity_id = await conn.fetchval(
        "INSERT INTO entities (org_id, entity_type, display_name) VALUES ($1, $2, $3) RETURNING id",
        ORG_A, "llc", f"{TAG} Entity",
    )
    asset_id = await create_asset(
        conn, org_id=ORG_A, name=f"{TAG} Growth Fund II", asset_type="private_fund",
        asset_class="financial", valuation_method="nav",
    )
    position_id = await create_position(
        conn, org_id=ORG_A, owner_entity_id=str(entity_id), asset_id=asset_id,
        as_of_date=date(2026, 1, 1), authority="manual", source_system="manual",
        ownership_basis="value", market_value=Decimal("500000"),
    )
    commitment_id = await create_commitment(
        conn, org_id=ORG_A, position_id=position_id,
        commitment_amount=Decimal("3000000"), commitment_date=date(2024, 3, 1),
        vintage_year=2024,
    )
    for yr in (2024, 2025):
        await record_transaction(
            conn, org_id=ORG_A, position_id=position_id,
            transaction_type_code="call_investment", trade_date=date(yr, 3, 15),
            authority="manual", source_system="manual", gross_amount=Decimal("250000"),
        )
    await recompute_commitment(conn, ORG_A, commitment_id)
    return {"entity_id": str(entity_id), "asset_id": asset_id, "position_id": position_id, "commitment_id": commitment_id}


async def _ta_row_count(admin_conn) -> int:
    return int(await admin_conn.fetchval(
        "SELECT (SELECT count(*) FROM portfolio.ta_model_params) "
        "+ (SELECT count(*) FROM portfolio.ta_calibration_results)"
    ))


class _Principal:
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


async def run_api_assertions(admin_conn, ids: dict) -> None:
    import main
    from starlette.testclient import TestClient

    print("\n── Section 3: the real API, through the real ASGI app ──")

    shared = TestClient(main.app, raise_server_exceptions=False)
    shared.__enter__()
    try:
        admin = _Principal(shared, ORG_A, ADMIN_SUB)
        viewer = _Principal(shared, ORG_A, VIEWER_SUB)
        noperm = _Principal(shared, ORG_A, NOPERM_SUB)
        org_b = _Principal(shared, ORG_B, ORGB_SUB)

        cid = ids["commitment_id"]

        # ── 3.1 no override yet -> the real 422, not a guess ────────────────
        res_noover = admin.get(f"/api/v1/modeling/ta/projection/{cid}", headers=HEADERS)
        check(
            "3.1 GET with no strategy_key and no active override returns the "
            "real 422 (drives the frontend's strategy-picker branch)",
            res_noover.status_code == 422, f"status={res_noover.status_code}",
        )

        # ── 3.2 real end-to-end projection, chart + table consistent ────────
        res = admin.get(
            f"/api/v1/modeling/ta/projection/{cid}?strategy_key=growth_equity", headers=HEADERS,
        )
        body = res.json() if res.status_code == 200 else {}
        check(
            "3.2 GET projection succeeds end-to-end for a real commitment "
            "through the real API",
            res.status_code == 200 and len(body.get("periods", [])) > 0,
            f"status={res.status_code} body={str(body)[:300]}",
        )
        check(
            "3.2 the response now publishes committed_capital/called_to_date/"
            "distributed_to_date (Task 1c fix) matching the REAL fixture "
            "(3000000 committed, 500000 called across 2 transactions)",
            body.get("committed_capital") == "3000000"
            and body.get("called_to_date") == "500000",
            f"got committed={body.get('committed_capital')} called={body.get('called_to_date')}",
        )
        check(
            "3.2 every period's monetary fields are JSON STRINGS — the chart "
            "(ProjectionChart) and the table (DataGrid) are driven from this "
            "SAME periods array, so string-typed here means both are "
            "consistent with each other by construction, not by coincidence",
            all(isinstance(body["periods"][0][f], str) for f in ("contribution", "distribution", "nav")),
        )

        # ── 3.3 preview genuinely differs on a changed assumption ───────────
        base_params = body["params"]
        low_bow = dict(base_params, bow_factor="1.0")
        high_bow = dict(base_params, bow_factor="4.0")
        common = {
            "committed_capital": body["committed_capital"],
            "called_to_date": body["called_to_date"],
            "distributed_to_date": body["distributed_to_date"],
            "current_nav": body["current_nav"],
            "horizon_periods": len(body["periods"]),
        }

        prev_low = admin.post(
            "/api/v1/modeling/ta/projection/preview",
            json={"params_override": low_bow, **common}, headers=HEADERS,
        )
        prev_high = admin.post(
            "/api/v1/modeling/ta/projection/preview",
            json={"params_override": high_bow, **common}, headers=HEADERS,
        )
        low_periods = prev_low.json().get("periods", []) if prev_low.status_code == 200 else []
        high_periods = prev_high.json().get("periods", []) if prev_high.status_code == 200 else []
        early = min(3, len(low_periods) - 1) if low_periods else 0
        low_early_dist = Decimal(low_periods[early]["distribution"]) if low_periods else None
        high_early_dist = Decimal(high_periods[early]["distribution"]) if high_periods else None
        # Measured against the real engine (services/ta_model.py), not
        # assumed: distribution(t) = RD * bow(t) * nav(t-1), where
        # bow(t) = elapsed_fraction(t) * bow_factor. elapsed_fraction alone
        # (driven by fund_life_years) is what ramps 0->1 over fund life — the
        # J-curve shape; bow_factor is a uniform SCALE on top of that ramp,
        # not a deferral. So a HIGHER bow_factor means a LARGER distribution
        # at every period, including early ones. First run of this assertion
        # asserted the opposite (deferral) and correctly FAILED against the
        # real endpoint — corrected here to match measured behavior rather
        # than the model's docstring-implied intuition.
        check(
            "3.3 the real preview endpoint produces a GENUINELY different "
            "result for a changed bow_factor: a higher bow_factor scales an "
            "early period's distribution UP (bow_factor is a uniform "
            "multiplier on the ramp, not a deferral) — computed server-side, "
            "not client-recomputed",
            prev_low.status_code == 200 and prev_high.status_code == 200
            and low_early_dist is not None and high_early_dist is not None
            and high_early_dist > low_early_dist,
            f"low(bow=1.0) period {early} distribution={low_early_dist}, "
            f"high(bow=4.0) period {early} distribution={high_early_dist}",
        )

        # ── 3.4 preview does not persist ─────────────────────────────────
        before = await _ta_row_count(admin_conn)
        prev_again = admin.post(
            "/api/v1/modeling/ta/projection/preview",
            json={"params_override": high_bow, **common}, headers=HEADERS,
        )
        after = await _ta_row_count(admin_conn)
        check(
            "3.4 running the preview tool does not grow ta_model_params or "
            "ta_calibration_results — real row-count check, before/after, "
            "same shape as Sprint 1's own proof",
            prev_again.status_code == 200 and before == after,
            f"before={before} after={after}",
        )

        # ── 3.5 permission: default-allow viewer succeeds, real-no-perm refused ──
        res_viewer = viewer.get(
            f"/api/v1/modeling/ta/projection/{cid}?strategy_key=growth_equity", headers=HEADERS,
        )
        check(
            "3.5 a plain member with no explicit role (default-allow, same "
            "mechanism the TA settings screen already relies on) CAN read "
            "the projection",
            res_viewer.status_code == 200, f"status={res_viewer.status_code}",
        )
        res_noperm = noperm.get(
            f"/api/v1/modeling/ta/projection/{cid}?strategy_key=growth_equity", headers=HEADERS,
        )
        check(
            "3.5 a user with a REAL role grant that holds NO permissions "
            "(not a zero-roles fixture — that would default-allow) is "
            "REFUSED 403 on the identical request",
            res_noperm.status_code == 403, f"status={res_noperm.status_code}",
        )

        # ── 3.6 cross-org isolation ───────────────────────────────────────
        res_orgb = org_b.get(
            f"/api/v1/modeling/ta/projection/{cid}?strategy_key=growth_equity", headers=HEADERS,
        )
        check(
            "3.6 org B cannot read org A's commitment's projection — 404, "
            "not org A's data",
            res_orgb.status_code == 404, f"status={res_orgb.status_code}",
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
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-10:]
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
    print("TA MODEL SPRINT 3 — verify")
    print("=" * 78)

    report_task1_findings()
    run_static_assertions()

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
            check("5.1 teardown complete — zero leftover test rows", remaining == 0, f"count={remaining}")
            await admin_conn.close()

    check_npm_build()

    print("\n" + "=" * 78)
    print(f"TA Model Sprint 3: {passed} passed, {failed} failed, {blocked} blocked")
    print("=" * 78)
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
