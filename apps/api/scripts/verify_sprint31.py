#!/usr/bin/env python3
"""verify_sprint31.py — SSVI volatility surface engine + admin viewer.

Twelve checks, from the sprint's Part 3 table. Effects, not exit codes: every
check asserts the thing the sprint actually promised (route really registered on
the app object, guard really raises, handler really maps every typed status),
not that some command ran.

Three outcomes, not two:

    PASS     the effect was observed
    FAIL     the effect was observed to be wrong
    BLOCKED  the effect could not be measured here

BLOCKED is deliberate. Checks 2 and 3 execute the calibration engine, which
needs numpy and scipy. Those are pinned in requirements.txt for Render but are
not installed in this sandbox and cannot be (pip is permission-gated in the
unattended runner). Reporting them as PASS because nothing threw would be a
gate that an outage passes vacuously — the exact failure mode that let an
unfunded API key "clear" the DeepEval gate in the corrections sprint. BLOCKED
checks do not fail the run, but they are counted and printed loudly, and the
sprint stays HELD until they are run on a machine with the deps.

No interactive prompts, no note-entry, no save step.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]      # apps/api
REPO_ROOT = API_ROOT.parents[1]                     # repo root
WEB_ROOT = REPO_ROOT / "apps" / "web"

# The unattended sprint runner invokes a bare, allowlisted `python3` rather than
# the venv interpreter, so put the venv's site-packages on the path ourselves.
# Harmless when already running inside the venv.
VENV_SITE_PACKAGES = sorted(
    str(p) for p in API_ROOT.glob("venv/lib/python3*/site-packages") if p.is_dir()
)

sys.path.insert(0, str(API_ROOT))
for _sp in VENV_SITE_PACKAGES:
    if _sp not in sys.path:
        sys.path.append(_sp)

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"
results: list[tuple[int, str, str, str]] = []


def record(n: int, outcome: str, title: str, detail: str = "") -> None:
    results.append((n, outcome, title, detail))
    print(f"[{n:>2}] {outcome:<7} {title}")
    if detail:
        for line in str(detail).strip().splitlines():
            print(f"          {line}")


def check(n: int, title: str, fn) -> None:
    """Run a check. It returns (outcome, detail) or raises."""
    try:
        outcome, detail = fn()
    except Exception as exc:  # a check that explodes is a FAIL, not a crash
        record(n, FAIL, title, f"{type(exc).__name__}: {exc}")
        return
    record(n, outcome, title, detail)


# Paths under test
SSVI_PATH = API_ROOT / "services" / "pricing" / "ssvi_surface.py"
GUARD_PATH = API_ROOT / "services" / "pricing" / "memory_guard.py"
ROUTER_PATH = API_ROOT / "routers" / "pricing_surface.py"
PAGE_PATH = WEB_ROOT / "app" / "admin" / "pricing" / "surface" / "page.js"
ROUTE_PATH = WEB_ROOT / "app" / "api" / "admin" / "pricing" / "surface" / "route.js"
CALIBRATOR_PATH = WEB_ROOT / "components" / "admin" / "SurfaceCalibrator.jsx"
CHART_PATH = WEB_ROOT / "components" / "admin" / "SmileChart.jsx"
SIDEBAR_PATH = WEB_ROOT / "components" / "Sidebar.jsx"

EXPECTED_ROUTE = "/api/v1/admin/pricing/surface"

# The typed error contract from the sprint's table, plus the three the handler
# adds (invalid ticker, engine import failure, and a typed last-resort).
EXPECTED_STATUSES = {
    "insufficient_data",
    "quality_gate_failed",
    "arbitrage_violation",
    "insufficient_memory",
    "out_of_memory",
    "data_provider_error",
    "timeout",
    "invalid_ticker",
    "module_unavailable",
    "unexpected_error",
}


def _engine_env() -> dict:
    """Environment for running the engine as a subprocess."""
    env = dict(os.environ)
    parts = [str(API_ROOT), *VENV_SITE_PACKAGES]
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _run_engine(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "services.pricing.ssvi_surface", *args],
        cwd=str(API_ROOT),
        env=_engine_env(),
        capture_output=True,
        text=True,
        timeout=600,
    )


def _missing_engine_dep(output: str) -> str | None:
    """Return the missing package name if the engine could not import."""
    m = re.search(r"No module named '([A-Za-z0-9_.]+)'", output or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# 1 — module present and parses
# ---------------------------------------------------------------------------
def check_01():
    if not SSVI_PATH.exists():
        return FAIL, f"missing: {SSVI_PATH}"
    src = SSVI_PATH.read_text()
    ast.parse(src)  # raises -> FAIL

    # The math must be the reference math. Compare the pure-math + calibration
    # region against backend_reference; only the documented containment edits
    # (gc import, max_expiries config field, the yahoo builder) may differ.
    ref = REPO_ROOT / "backend_reference" / "ssvi_surface.py"
    detail = f"{SSVI_PATH.relative_to(REPO_ROOT)} parses ({len(src.splitlines())} lines)"
    if ref.exists():
        def math_region(text: str) -> str:
            start = text.index("def black_from_forward")
            end = text.index("def build_slices_from_yahoo")
            return text[start:end]

        if math_region(src) != math_region(ref.read_text()):
            return FAIL, "math region differs from backend_reference/ssvi_surface.py"
        detail += "; math region byte-identical to backend_reference"
    return PASS, detail


# ---------------------------------------------------------------------------
# 2 — self-test exits 0 with 8/8
# ---------------------------------------------------------------------------
def check_02():
    proc = _run_engine(["--self-test"])
    output = (proc.stdout or "") + (proc.stderr or "")
    missing = _missing_engine_dep(output)
    if missing:
        return BLOCKED, (
            f"engine dependency '{missing}' not installed in this sandbox; "
            f"pinned in requirements.txt. Re-run where numpy/scipy are present."
        )
    if proc.returncode != 0:
        return FAIL, f"exit {proc.returncode}\n{output.strip()[-1500:]}"
    if "8/8 passed" not in output:
        return FAIL, f"expected '8/8 passed'\n{output.strip()[-1500:]}"
    return PASS, "exit 0, 8/8 passed"


# ---------------------------------------------------------------------------
# 3 — synthetic fit under 1.5 vol points pooled
# ---------------------------------------------------------------------------
def check_03():
    out_json = API_ROOT / ".verify_sprint31_synthetic.json"
    try:
        proc = _run_engine(["--synthetic", "--json", str(out_json)])
        output = (proc.stdout or "") + (proc.stderr or "")
        missing = _missing_engine_dep(output)
        if missing:
            return BLOCKED, f"engine dependency '{missing}' not installed in this sandbox"
        if proc.returncode != 0:
            return FAIL, f"exit {proc.returncode}\n{output.strip()[-1500:]}"
        if not out_json.exists():
            return FAIL, "no JSON written"
        import json

        fit = json.loads(out_json.read_text())
        pooled_vp = float(fit["rmse_iv_pooled"]) * 100.0
        if not pooled_vp < 1.5:
            return FAIL, f"pooled RMSE {pooled_vp:.3f} vol pts is not < 1.5"
        return PASS, f"pooled RMSE {pooled_vp:.3f} vol pts < 1.5"
    finally:
        if out_json.exists():
            out_json.unlink()


# ---------------------------------------------------------------------------
# 4 — assert_headroom raises on an impossible requirement
# ---------------------------------------------------------------------------
def check_04():
    from services.pricing import memory_guard as mg

    snapshot = mg.memory_snapshot()
    if snapshot is None or snapshot.available_bytes is None:
        return BLOCKED, (
            "no memory tier could be read on this host, so the guard cannot be "
            "exercised (it fails open by design when memory is unmeasurable)"
        )
    try:
        mg.assert_headroom(999999)
    except mg.InsufficientMemoryError as exc:
        return PASS, f"raised InsufficientMemoryError: {exc}"
    return FAIL, "assert_headroom(999999) did not raise"


# ---------------------------------------------------------------------------
# 5 — cgroup readers return None (not raise) when the paths are absent
# ---------------------------------------------------------------------------
def check_05():
    from services.pricing import memory_guard as mg

    absent = ("/nonexistent/cgroup/memory.max", "/nonexistent/cgroup/limit_in_bytes")
    limit = mg.read_cgroup_limit(paths=absent)
    usage = mg.read_cgroup_usage(paths=absent)
    if limit is not None or usage is not None:
        return FAIL, f"expected None/None, got {limit!r}/{usage!r}"

    # And the whole guard must stay usable rather than crashing.
    mg.memory_snapshot()
    mg.apply_address_space_limit()
    return PASS, "read_cgroup_limit/usage returned None; snapshot + rlimit did not raise"


# ---------------------------------------------------------------------------
# 6 — route registered under /api/v1/admin/, not bare /api/v1/
# ---------------------------------------------------------------------------
def _iter_routes(routes, prefix: str = ""):
    """Yield (path, methods) pairs, descending into lazily-included routers.

    This FastAPI version does not flatten ``include_router`` at import time: it
    parks a ``_IncludedRouter`` wrapper on ``app.routes`` and resolves the real
    routes on first request. Reading ``r.path`` off the top level therefore sees
    only the handful of routes declared directly on the app, which would make
    this check silently pass-by-absence. Recurse into ``original_router``.
    """
    for r in routes:
        original = getattr(r, "original_router", None)
        if original is not None:
            ctx = getattr(r, "include_context", None)
            child_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            yield from _iter_routes(original.routes, child_prefix)
            continue
        path = getattr(r, "path", None)
        if path:
            yield prefix + path, set(getattr(r, "methods", set()) or set())


def check_06():
    import main  # noqa: F401  (imports register every router)

    routes = dict(_iter_routes(main.app.routes))
    if len(routes) < 50:
        return FAIL, f"only {len(routes)} routes resolved — the walker missed the tree"

    if EXPECTED_ROUTE not in routes:
        near = sorted(p for p in routes if "surface" in p or "pricing" in p)
        return FAIL, f"{EXPECTED_ROUTE} not registered; nearby: {near}"

    bare = [p for p in routes if p.startswith("/api/v1/pricing/")]
    if bare:
        return FAIL, f"also registered outside the admin prefix: {bare}"

    if "POST" not in routes[EXPECTED_ROUTE]:
        return FAIL, (
            f"{EXPECTED_ROUTE} registered but methods={sorted(routes[EXPECTED_ROUTE])}"
        )
    return PASS, (
        f"POST {EXPECTED_ROUTE} registered ({len(routes)} routes resolved); "
        "nothing under a bare /api/v1/pricing/ prefix"
    )


# ---------------------------------------------------------------------------
# 7 — non-super_admin gets 403
# ---------------------------------------------------------------------------
def check_07():
    """Exercise the real guard with a member principal.

    Driving this through TestClient would need a live Auth0 token, and an
    unauthenticated request returns 401 before the role is ever consulted — so
    it would prove nothing about the 403. Instead the router's own
    ``_require_super_admin`` is called with its two collaborators stubbed, which
    is the code path the endpoint actually takes.
    """
    import asyncio
    import contextlib

    from fastapi import HTTPException
    import routers.pricing_surface as ps

    class _FakeConn:
        pass

    class _FakePool:
        @contextlib.asynccontextmanager
        async def acquire(self):
            yield _FakeConn()

    async def fake_get_pool():
        return _FakePool()

    async def fake_ensure_user(conn, request):
        return "99000000-0000-0000-0000-000000000001"

    originals = (ps.get_pool, ps.ensure_user, ps.load_principal, ps.get_org_id)
    try:
        ps.get_pool = fake_get_pool
        ps.ensure_user = fake_ensure_user
        ps.get_org_id = lambda request: "00000000-0000-0000-0000-000000000001"

        async def member_principal(conn, actor_id):
            return {"id": actor_id, "role": "member", "permissions": []}

        async def super_principal(conn, actor_id):
            return {"id": actor_id, "role": "super_admin", "permissions": []}

        ps.load_principal = member_principal
        try:
            asyncio.run(ps._require_super_admin(object()))
        except HTTPException as exc:
            if exc.status_code != 403:
                return FAIL, f"member got {exc.status_code}, expected 403"
        else:
            return FAIL, "member principal was NOT rejected"

        # Guard against a check that passes because everything is rejected.
        ps.load_principal = super_principal
        asyncio.run(ps._require_super_admin(object()))
        return PASS, "member -> 403; super_admin -> allowed"
    finally:
        ps.get_pool, ps.ensure_user, ps.load_principal, ps.get_org_id = originals


# ---------------------------------------------------------------------------
# 8 — org_id never read from the request body
# ---------------------------------------------------------------------------
def check_08():
    src = ROUTER_PATH.read_text()
    tree = ast.parse(src)

    # The request model must not expose an org_id field at all.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SurfaceRequest":
            fields = [
                t.target.id
                for t in node.body
                if isinstance(t, ast.AnnAssign) and isinstance(t.target, ast.Name)
            ]
            if "org_id" in fields:
                return FAIL, "SurfaceRequest declares an org_id field"

    offenders = [
        line.strip()
        for line in src.splitlines()
        if re.search(r"(body|payload|request\.json\(\))[^\n]*org_id", line)
        or re.search(r"org_id[^\n]*\b(body|payload)\b", line)
    ]
    if offenders:
        return FAIL, "org_id read from the body:\n" + "\n".join(offenders)
    if "get_org_id(request)" not in src:
        return FAIL, "org_id is not resolved server-side via get_org_id(request)"

    # And the Next.js proxy must not forward one either.
    web_src = ROUTE_PATH.read_text()
    if "org_id" in web_src and "never be accepted" not in web_src:
        return FAIL, "the Next.js proxy references org_id"
    return PASS, "org_id resolved via get_org_id(request); body carries ticker only"


# ---------------------------------------------------------------------------
# 9 — matplotlib never imported at module scope server-side
# ---------------------------------------------------------------------------
def check_09():
    offenders = []
    scanned = 0
    skip = {"venv", "__pycache__", "node_modules", ".next"}
    for path in API_ROOT.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        scanned += 1
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        # Only TOP-LEVEL nodes: an import inside a function is exactly the
        # containment the sprint asks for.
        for node in tree.body:
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n.split(".")[0] == "matplotlib" for n in names):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    if offenders:
        return FAIL, "module-scope matplotlib import:\n" + "\n".join(offenders)

    # Positive control: the engine does still import it lazily under --plot, so
    # this check is looking at a file that genuinely mentions matplotlib.
    engine = SSVI_PATH.read_text()
    if "matplotlib" not in engine:
        return FAIL, "engine no longer mentions matplotlib — check is vacuous"
    return PASS, f"{scanned} server-side .py files scanned, zero module-scope imports"


# ---------------------------------------------------------------------------
# 10 — frontend route exists and the nav entry is super_admin-gated
# ---------------------------------------------------------------------------
def check_10():
    missing = [
        str(p.relative_to(REPO_ROOT))
        for p in (PAGE_PATH, ROUTE_PATH, CALIBRATOR_PATH, CHART_PATH)
        if not p.exists()
    ]
    if missing:
        return FAIL, "missing frontend files: " + ", ".join(missing)

    sidebar = SIDEBAR_PATH.read_text()
    if "/admin/pricing/surface" not in sidebar:
        return FAIL, "no /admin/pricing/surface nav entry in Sidebar.jsx"

    # The nav item must sit inside the `role === "super_admin"` block, not
    # merely somewhere in the file.
    guard = sidebar.index('role === "super_admin"')
    tail = sidebar[guard:]
    org_admin_after = tail.find('role === "org_admin"')
    item_at = tail.find("VOL_SURFACE_ITEM\n")
    if item_at == -1:
        item_at = tail.find("item={VOL_SURFACE_ITEM}")
    if item_at == -1:
        return FAIL, "VOL_SURFACE_ITEM is not rendered after the super_admin guard"
    if org_admin_after != -1 and org_admin_after < item_at:
        return FAIL, "VOL_SURFACE_ITEM falls outside the super_admin block"
    return PASS, "page + proxy + components present; nav entry inside the super_admin block"


# ---------------------------------------------------------------------------
# 11 — no hardcoded hex in the new frontend files
# ---------------------------------------------------------------------------
def check_11():
    hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    offenders = []
    for path in (PAGE_PATH, ROUTE_PATH, CALIBRATOR_PATH, CHART_PATH):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            for match in hex_re.finditer(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {match.group(0)}")
    if offenders:
        return FAIL, "hardcoded hex colours:\n" + "\n".join(offenders)

    # Positive control: the chart must actually be theming off the tokens.
    chart = CHART_PATH.read_text()
    if "var(--2a-navy)" not in chart or "var(--2a-gold)" not in chart:
        return FAIL, "SmileChart does not use the --2a-* palette tokens"
    return PASS, "zero hex literals; chart colours come from --2a-* tokens"


# ---------------------------------------------------------------------------
# 12 — every typed error status is reachable from the handler
# ---------------------------------------------------------------------------
def check_12():
    tree = ast.parse(ROUTER_PATH.read_text())

    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_typed_error"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            found.add(node.args[1].value)

    missing = EXPECTED_STATUSES - found
    if missing:
        return FAIL, f"no _typed_error call emits: {sorted(missing)}"

    # The HTTP codes must match the contract, not just the strings.
    expected_codes = {
        "insufficient_data": 422,
        "quality_gate_failed": 422,
        "arbitrage_violation": 422,
        "invalid_ticker": 422,
        "insufficient_memory": 503,
        "out_of_memory": 503,
        "module_unavailable": 503,
        "data_provider_error": 502,
        "timeout": 504,
    }
    mismatches = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_typed_error"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[1], ast.Constant)
        ):
            code, status = node.args[0].value, node.args[1].value
            want = expected_codes.get(status)
            if want is not None and code != want:
                mismatches.append(f"{status}: got {code}, contract says {want}")
    if mismatches:
        return FAIL, "status/code mismatch:\n" + "\n".join(mismatches)

    # And the three engine exceptions must actually be caught by name.
    src = ROUTER_PATH.read_text()
    for exc in ("InsufficientDataError", "SurfaceQualityError", "SurfaceArbitrageError"):
        if f"except ssvi.{exc}" not in src:
            return FAIL, f"handler does not catch ssvi.{exc}"
    if "except MemoryError" not in src:
        return FAIL, "handler does not catch MemoryError"
    return PASS, f"{len(found)} typed statuses, codes match the contract, engine exceptions caught"


# ---------------------------------------------------------------------------
# 13 — the chart's market-point -> slice join actually holds
# ---------------------------------------------------------------------------
def check_13():
    """Every market point must land on a slice the chart can select.

    SmileChart filters `market_points` by exact equality against `per_slice.T`.
    The engine rounds per_slice.T to 4dp inside `iv_diagnostics`, so the payload
    has to round identically — otherwise every join misses and the chart renders
    empty for every maturity while the rest of the page looks perfectly healthy.
    Exercises the real `build_payload`, not a copy of it.
    """
    try:
        from services.pricing import ssvi_surface as ssvi
    except Exception as exc:
        missing = _missing_engine_dep(f"{exc}")
        if missing:
            return BLOCKED, f"engine dependency '{missing}' not installed in this sandbox"
        raise

    from routers.pricing_surface import build_payload

    # Realistic day-count maturities (days/365), NOT the module's default
    # (0.1, 0.25, 0.5, 1.0, 2.0). Those defaults are exact at any rounding, so
    # they cannot detect a rounding mismatch — the check would pass vacuously.
    # Real expiries carry many decimals and are what the bug bites on.
    maturities = tuple(d / 365.0 for d in (30, 67, 158, 331, 704))
    slices = ssvi.build_synthetic_slices(seed=5, maturities=maturities)
    fit = ssvi.calibrate(slices, ticker="TEST")
    payload = build_payload(fit, slices, stats=None)

    slice_ts = {s["T"] for s in payload["fit"]["per_slice"]}
    point_ts = {p["T"] for p in payload["market_points"]}
    if not payload["market_points"]:
        return FAIL, "no market points emitted"

    orphans = point_ts - slice_ts
    if orphans:
        return FAIL, (
            f"{len(orphans)} market-point T values match no slice: "
            f"{sorted(orphans)[:5]} vs slices {sorted(slice_ts)[:5]}"
        )
    empty = [t for t in slice_ts if not any(p["T"] == t for p in payload["market_points"])]
    if empty:
        return FAIL, f"slices with zero joinable market points: {sorted(empty)}"

    # And the boundary marker the chart draws must be reachable.
    if max(slice_ts) > fit.max_listed_maturity + 1e-3:
        return FAIL, "per_slice T exceeds max_listed_maturity"
    return PASS, (
        f"{len(payload['market_points'])} points join cleanly onto "
        f"{len(slice_ts)} slices"
    )


def main() -> int:
    print("=" * 72)
    print("verify_sprint31 — SSVI volatility surface engine + admin viewer")
    print("=" * 72)

    check(1, "ssvi_surface.py present and parses", check_01)
    check(2, "engine --self-test exits 0 with 8/8", check_02)
    check(3, "engine --synthetic pooled RMSE < 1.5 vol points", check_03)
    check(4, "assert_headroom(999999) raises InsufficientMemoryError", check_04)
    check(5, "memory guard returns None when cgroup paths are absent", check_05)
    check(6, f"route registered at {EXPECTED_ROUTE}", check_06)
    check(7, "non-super_admin session is rejected with 403", check_07)
    check(8, "org_id is never read from the request body", check_08)
    check(9, "matplotlib never imported at module scope server-side", check_09)
    check(10, "frontend route exists, nav entry gated on super_admin", check_10)
    check(11, "no hardcoded hex colours in the new frontend files", check_11)
    check(12, "every typed error status is reachable from the handler", check_12)
    check(13, "chart market-point -> slice join holds (T rounding contract)", check_13)

    passed = sum(1 for _, o, _, _ in results if o == PASS)
    failed = [r for r in results if r[1] == FAIL]
    blocked = [r for r in results if r[1] == BLOCKED]

    print()
    print("-" * 72)
    print(f"{passed}/{len(results)} passed, {len(failed)} failed, {len(blocked)} blocked")
    if blocked:
        print()
        print("BLOCKED — measured by nothing, so proved by nothing:")
        for n, _, title, detail in blocked:
            print(f"  [{n}] {title}")
            print(f"       {detail}")
        print()
        print("  These are NOT passes. The sprint stays HELD until they run on a")
        print("  host with numpy/scipy installed (pinned in requirements.txt).")
    if failed:
        print()
        print("FAILED:")
        for n, _, title, _ in failed:
            print(f"  [{n}] {title}")
    print("-" * 72)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
