"""verify_mkt04b.py — the market indicators chart: anchor bar, client-side
rebasing, overlays, and the series endpoint's `stddev` field (mkt04b.structural).

WHAT WAS BUILT (this script proves it):
  * GET /market/series gained ONE field per series, `stddev`: the grid's own
    full-history stddev_samp, 6 dp half-even text, null when undefined
    (services/market_data/read_service.py stddev_text + read_series).
  * apps/web/lib/market/{chartContract,rebase,scales,hitTest,overlays,
    labelLayout,chartModel,chartRequest}.mjs — pure chart logic.
  * apps/web/components/market/{ChartPanel,ChartView,MarketChart,AnchorSlider,
    TrendsTable}.jsx — the Chart tab, replacing the mkt04a placeholder.
  * apps/web/tests/market/{rebase,chartGeometry,chartModel,chartRequest,
    chartRender}.test.mjs, and golden_rebase.json written by
    apps/api/scripts/gen_mkt04b_golden.py from the PRODUCTION Python transforms.

PHASE A (always): no database, no secrets — golden freshness, the backend
field's definition and diff, every node:test suite, static scans, scope, lint
and the production build (their first execution).
PHASE B (--live): the series function against the real database — stddev
equals SQL stddev_samp quantized for three real series, independent of window
and frequency; fewer than two points and zero variance give null (rollback-only
fixture, zero rows written).

Output: every line starts with [PASS], [FAIL], [FIND] or [SKIP]; the last line
is `TOTAL: <passed> passed, <failed> failed`. Exit 1 on any [FAIL].

Run:  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04b.py --live
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[3]
WEB = REPO / "apps" / "web"
API = REPO / "apps" / "api"
sys.path.insert(0, str(API))
sys.path.insert(0, str(HERE.parent))

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, why: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} for {label!r}"
    global _n_pass, _n_fail
    line = f"{'[PASS]' if passed else '[FAIL]'} {label} — why: {why}"
    if detail:
        line += f"  ({detail})"
    print(line, flush=True)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  ({detail})" if detail else ""), flush=True)


def skip(label: str, detail: str = "") -> None:
    print(f"[SKIP] {label}" + (f"  ({detail})" if detail else ""), flush=True)


def read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def rel(p: pathlib.Path) -> str:
    return str(p.relative_to(REPO))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=120).stdout


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# verify_mkt04a's scan helpers and token sources, reused so both sprints apply
# the SAME rules (it defines functions and constants only; nothing runs on import).
V4A = _load("verify_mkt04a_helpers", HERE.parent / "verify_mkt04a.py")
strip_comments = V4A.strip_comments
string_literals = V4A.string_literals

MARKET_LIB = WEB / "lib" / "market"
COMPONENTS = WEB / "components" / "market"
TESTS = WEB / "tests" / "market"
PAGE = WEB / "app" / "market" / "page.js"
DEFAULTS = MARKET_LIB / "marketDefaults.mjs"
CONTRACT = MARKET_LIB / "chartContract.mjs"
ROUTES_CORE = MARKET_LIB / "marketRoutes.mjs"
READ_SERVICE = API / "services" / "market_data" / "read_service.py"
READ_SERVICE_REL = "apps/api/services/market_data/read_service.py"
GOLDEN = TESTS / "golden_rebase.json"
GENERATOR = HERE.parent / "gen_mkt04b_golden.py"

NEW_LIB = ["chartContract", "rebase", "scales", "hitTest", "overlays", "labelLayout", "chartModel", "chartRequest"]
NEW_COMPONENTS = ["ChartPanel", "ChartView", "MarketChart", "AnchorSlider", "TrendsTable"]
PARSERS = {"rebase.mjs", "scales.mjs"}  # the only modules that turn API strings into numbers
GRID_FILES = [MARKET_LIB / "gridView.mjs", COMPONENTS / "MarketGridTable.jsx", COMPONENTS / "GridPanel.jsx"]

EXPECTED_CONTRACT = {
    "MEASURE_INDEX": '"index"',
    "MEASURE_SIGMA": '"sigma"',
    "TRANSFORM_LEVEL": '"level"',
    "WARNING_RATE_LIKE": '"rate_like_series_indexed"',
    "REASON_NON_POSITIVE_ANCHOR": '"non_positive_anchor"',
    "REASON_ZERO_VARIANCE": '"zero_variance"',
    "REASON_NO_OBSERVATIONS": '"no_observations"',
    "SCALE_LOG": '"log"',
    "SCALE_LINEAR": '"linear"',
    "FREQ_WEEKLY": '"weekly"',
    "FREQ_MONTHLY": '"monthly"',
    "FREQ_QUARTERLY": '"quarterly"',
}


# ═══════════════════════════════════════════════════════════════════════════
# The pre-sprint reference point
# ═══════════════════════════════════════════════════════════════════════════
# Located, not assumed: the commit that ADDED this sprint's golden generator is
# the sprint's commit, and its parent is the pre-sprint tree. Before that commit
# exists (the sprint is still uncommitted) the reference is HEAD and the
# working tree is the change. So the same checks mean the same thing before and
# after the commit, and a later sprint cannot contaminate them.
SPRINT_MARKER = "apps/api/scripts/gen_mkt04b_golden.py"


def sprint_range() -> tuple[str, str | None]:
    hits = git("log", "--format=%H", "--diff-filter=A", "--", SPRINT_MARKER).split()
    if hits:
        return f"{hits[-1]}^", hits[-1]
    return "HEAD", None


def changed_files(*, modified_only: bool = False) -> list[str]:
    base, sha = sprint_range()
    flt = ["--diff-filter=M"] if modified_only else []
    if sha:
        return sorted(set(git("diff", "--name-only", *flt, base, sha).split()))
    tracked = git("diff", "--name-only", *flt, base).split()
    untracked = [] if modified_only else git("ls-files", "--others", "--exclude-standard").split()
    return sorted(set(tracked) | set(untracked))


def base_text(path: str) -> str:
    base, _ = sprint_range()
    return git("show", f"{base}:{path}")


# ═══════════════════════════════════════════════════════════════════════════
# 1. Golden vectors (Task 5)
# ═══════════════════════════════════════════════════════════════════════════
def golden() -> None:
    gen = _load("gen_mkt04b_golden", GENERATOR)
    fresh = gen.render(gen.build())
    committed = read(GOLDEN) if GOLDEN.is_file() else ""
    check(fresh == committed, "golden_rebase.json equals a fresh build by the production transforms",
          "the browser is compared with what the server computes TODAY, not with a stale copy",
          f"{len(json.loads(fresh)['cases'])} cases")
    src = read(GENERATOR)
    check("from services.market_data.transforms import Series, decimal_text, transform_column" in src
          and "from services.market_data.read_service import stddev_text" in src
          and not re.search(r"asyncpg|urllib|requests|httpx|DATABASE_URL", src),
          "the generator imports the PRODUCTION transform_column and stddev_text, and no database or network",
          "a re-implemented transform would only prove the test agrees with itself")
    cases = {c["name"]: c for c in json.loads(committed or "{}").get("cases", [])}
    need = {
        "anchor_on_observation": lambda c: c["measures"]["index"]["anchor_observation_date"] == c["anchor"],
        "anchor_between_observations": lambda c: c["measures"]["index"]["anchor_observation_date"] < c["anchor"],
        "anchor_before_first_observation": lambda c: c["measures"]["index"]["floating"] is True,
        "non_positive_anchor_negative": lambda c: c["measures"]["index"]["unavailable_reason"] == "non_positive_anchor"
        and c["measures"]["sigma"]["unavailable_reason"] is None,
        "zero_variance": lambda c: c["series"]["stddev"] is None
        and c["measures"]["sigma"]["unavailable_reason"] == "zero_variance",
        "rate_like_level_series": lambda c: c["measures"]["index"]["warnings"] == ["rate_like_series_indexed"],
    }
    for name, ok in need.items():
        c = cases.get(name)
        check(c is not None and bool(ok(c)), f"golden case {name} exists and exercises its rule",
              "each canonical-definition branch must be pinned by the server's own output")


# ═══════════════════════════════════════════════════════════════════════════
# 2. The stddev field (Task 2) — definition and diff, offline
# ═══════════════════════════════════════════════════════════════════════════
def _dict_key(node: ast.AST, key: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == key


def _normalise_read_series(fn: ast.AST) -> str:
    """read_series with the mkt04b change undone: drop the 'stddev' entry and
    call series_bounds instead of series_stats."""
    fn = ast.parse(ast.unparse(fn)).body[0]
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            pairs = [(k, v) for k, v in zip(node.keys, node.values) if not _dict_key(k, "stddev")]
            node.keys = [k for k, _ in pairs]
            node.values = [v for _, v in pairs]
        if isinstance(node, ast.Attribute) and node.attr == "series_stats":
            node.attr = "series_bounds"
    return ast.dump(fn)


def _top_level(tree: ast.Module) -> dict[str, str]:
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = ast.dump(n)
        elif isinstance(n, ast.Assign):
            out["=" + ",".join(ast.unparse(t) for t in n.targets)] = ast.dump(n)
        elif isinstance(n, ast.AnnAssign):
            out["=" + ast.unparse(n.target)] = ast.dump(n)
    return out


def stddev_field() -> None:
    from services.market_data import read_service as svc

    cases = [
        (None, None, "stddev_samp is NULL under two observations"),
        (Decimal(0), None, "zero variance"),
        (Decimal("2.9274815"), "2.927482", "half-even rounds 1 up to 2 here (…815 → …82)"),
        (Decimal("1.0000005"), "1.000000", "half-even: an exact half rounds to the even digit"),
        (Decimal("1.0000015"), "1.000002", "half-even: …15 → …2"),
        (Decimal("1E+3"), "1000.000000", "never exponent notation"),
        (Decimal("1559.76322149999"), "1559.763221", "6 decimal places, like every transformed value"),
    ]
    bad = [(str(x), got, want) for x, want, _ in cases if (got := svc.stddev_text(x)) != want]
    check(not bad, "stddev_text: 6 dp ROUND_HALF_EVEN text, null for None and for 0, never exponent notation",
          "the browser divides by exactly this string; a float or a different rounding desyncs sigma", f"bad={bad}")

    cur = ast.parse(read(READ_SERVICE))
    base_src = base_text(READ_SERVICE_REL)
    if not check(bool(base_src), "the pre-sprint read_service.py is readable from git", "the diff proof needs it"):
        return
    base = ast.parse(base_src)
    fn_cur = next(n for n in cur.body if getattr(n, "name", "") == "read_series")
    fn_base = next(n for n in base.body if getattr(n, "name", "") == "read_series")
    src_fn = ast.unparse(fn_cur)
    check("'stddev': stddev_text(b.get('sd'))" in src_fn and "repo.series_stats(conn, ids)" in src_fn,
          "read_series adds `stddev` from repo.series_stats — the SAME query the grid's sigma uses",
          "one definition of sd: the grid and the chart divide by the same full-history stddev_samp")
    check(_normalise_read_series(fn_cur) == ast.dump(fn_base),
          "read_series differs from its pre-sprint version ONLY by the stddev entry and series_stats",
          "Task 2b: change nothing else in the response or the endpoint's behaviour")
    tl_cur, tl_base = _top_level(cur), _top_level(base)
    changed = sorted(k for k in set(tl_cur) | set(tl_base) if tl_cur.get(k) != tl_base.get(k))
    check(changed == ["read_series", "stddev_text"],
          "no other function, class or constant in read_service.py changed (stddev_text is the one addition)",
          "the grid, catalog and correlations must behave exactly as before", f"changed={changed}")
    repo_src = read(API / "services" / "market_data" / "read_repository.py")
    stats = repo_src.split("async def series_stats", 1)[-1].split("async def ", 1)[0]
    check("stddev_samp(value) AS sd" in stats and "valid_to IS NULL AND system_to IS NULL" in stats
          and "obs_date" not in stats.split("GROUP BY", 1)[0].split("WHERE", 1)[1].replace("obs_date) AS", ""),
          "series_stats computes stddev_samp over ALL active observations, with no date filter",
          "full history, independent of the request's window and frequency (canonical sigma definition)")
    v3_changed = "apps/api/scripts/verify_mkt03.py" in changed_files()
    check(not v3_changed, "verify_mkt03.py is unchanged",
          "Task 1c: it checks series fields one by one, never a strict key set, so an added field breaks nothing")
    find("Task 1c: verify_mkt03 asserts series fields individually (points, frequency, point_count, string-typed "
         "values) — no strict key-set assertion exists, so no edit was needed")


# ═══════════════════════════════════════════════════════════════════════════
# 3. node:test suites
# ═══════════════════════════════════════════════════════════════════════════
TEST_WHY = {
    "rebase.test.mjs": "the browser's rebase must equal the server's transforms to within 5e-7 on every golden case",
    "chartGeometry.test.mjs": "scales, ticks, the 18-px hit rule with display-scale correction, pack fences, "
                              "big moves and label spacing are known-answer, not eyeballed",
    "chartModel.test.mjs": "floating dashed, unavailable excluded with a notice, server labels, hovered last, "
                           "highlighting fades only in-pack lines",
    "chartRequest.test.mjs": "one request per burst, newest wins, the server's limit enforced before sending, "
                             "fail-closed interpretation, keyboard stepping, per-frame dragging",
    "chartRender.test.mjs": "the real components: chart for a normal envelope, error and NO chart without one, "
                            "anchor keyboard, overlay toggles",
    "render.test.mjs": "mkt04a's page proofs still hold, and its Chart tab now renders the chart",
    "gridRequest.test.mjs": "the Grid tab's request behaviour is unchanged",
    "selection.test.mjs": "the selection model is unchanged",
    "marketRoutes.test.mjs": "the route forwards the chart uses are unchanged",
}


def mkt04b_suites() -> set[str]:
    """The market suites on disk that mkt04b's own commit contains.

    Pinned by mkt04c: a later sprint's suites in the same directory are run and
    counted by that sprint's verify. Before mkt04b is committed, every suite on
    disk. A suite mkt04b committed that is now missing still fails below."""
    names = {p.name for p in TESTS.glob("*.test.mjs")}
    _, sha = sprint_range()
    if sha:
        names &= {pathlib.PurePosixPath(f).name for f in git("ls-tree", "--name-only", sha, "apps/web/tests/market/").split()}
    return names


def node_tests() -> None:
    node = shutil.which("node")
    if not check(node is not None, "node is on PATH", "the UI proofs run as node:test suites"):
        return
    present = sorted(mkt04b_suites())
    check(sorted(TEST_WHY) == present, "every market suite on disk is run here, and no listed suite is missing",
          "an unrun suite proves nothing", f"on disk={present}")
    for name, why in TEST_WHY.items():
        path = TESTS / name
        if not check(path.is_file(), f"test suite {rel(path)} exists", why):
            continue
        try:
            proc = subprocess.run([node, "--test", "--test-reporter=tap", str(path.relative_to(WEB))],
                                  cwd=WEB, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            check(False, f"{name} completes", why, "timed out after 600s")
            continue
        results = re.findall(r"^(ok|not ok) \d+ - (.+?)(?: # .*)?$", proc.stdout, flags=re.M)
        check(len(results) > 0, f"{name} ran at least one test", why,
              "" if results else (proc.stdout + proc.stderr)[-800:])
        for status, title in results:
            detail = ""
            if status != "ok":
                m = re.search(rf"not ok \d+ - {re.escape(title)}\n(.*?)\n  \.\.\.", proc.stdout, flags=re.S)
                detail = (m.group(1) if m else "")[-600:].replace("\n", " ")
            check(status == "ok", f"{name}: {title}", why, detail)
        check(proc.returncode == 0, f"{name} exits 0", why, f"exit {proc.returncode}")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Static scans — mkt04a's rules, extended to the chart
# ═══════════════════════════════════════════════════════════════════════════
def ui_files() -> list[pathlib.Path]:
    files = [PAGE, *sorted(COMPONENTS.glob("*")), *sorted(MARKET_LIB.glob("*"))]
    return [f for f in files if f.is_file() and f not in (DEFAULTS, CONTRACT, ROUTES_CORE)]


def node_eval(js: str) -> str:
    node = shutil.which("node")
    if not node:
        return ""
    return subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=120).stdout


def static_scans() -> None:
    for n in NEW_LIB:
        check((MARKET_LIB / f"{n}.mjs").is_file(), f"lib/market/{n}.mjs exists", "Task 3's pure modules")
    for n in NEW_COMPONENTS:
        check((COMPONENTS / f"{n}.jsx").is_file(), f"components/market/{n}.jsx exists", "Task 4's components")

    files = ui_files()
    new = [f for f in files if f.stem in NEW_LIB + NEW_COMPONENTS]
    check(len(files) >= 22 and len(new) == len(NEW_LIB) + len(NEW_COMPONENTS) - 1,
          "the scan covers the page, every market component and pure module, including every new chart file",
          "a scan over too few files proves nothing", f"{len(files)} files, {len(new)} new")

    categories, palette_hex, vocab = V4A.vocabulary_tokens()
    check(len(categories) >= 5 and len(vocab) >= 15 and len(palette_hex) >= 5,
          "the scan's vocabulary was read from the backend source, not typed here",
          "a hand-typed list drifts from what the server really sends")
    limits = {"40", "2000", "400", "30000", "300000"}
    hits = {"category": [], "vocab": [], "hex": [], "palette": [], "limit": []}
    for f in files:
        src = strip_comments(read(f))
        for lit in string_literals(src):
            if lit in categories:
                hits["category"].append(f"{rel(f)}: {lit!r}")
            if lit in vocab:
                hits["vocab"].append(f"{rel(f)}: {lit!r}")
        for h in re.findall(r"#[0-9A-Fa-f]{6}\b", src):
            if h.upper() not in V4A.DESIGN_TOKENS:
                hits["hex"].append(f"{rel(f)}: {h}")
            if h.upper() in palette_hex and h.upper() not in V4A.DESIGN_TOKENS:
                hits["palette"].append(f"{rel(f)}: {h}")
        for n in re.findall(r"(?<![\w\-\[.#])(\d+)(?![\w\].%])", src):
            if n in limits:
                hits["limit"].append(f"{rel(f)}: {n}")
    check(not hits["category"], "no category name is a literal in the market UI", "categories come from the catalog (Rule 1)",
          "; ".join(hits["category"]))
    check(not hits["vocab"], "no vocabulary value (mode, frequency, transform, warning, reason) is a literal outside "
          "the two pinned contract files", "codes and option lists come from the server (Rule 1)", "; ".join(hits["vocab"]))
    check(not hits["palette"], "no server palette colour appears in the UI code", "series colours come from the catalog",
          "; ".join(hits["palette"]))
    check(not hits["hex"], "every hex colour in the UI code is a design token", "brand tokens only", "; ".join(hits["hex"]))
    check(not hits["limit"], "no API limit (40 keys, 2,000 rows, 400 days, point caps) is a literal",
          "the key limit comes from vocabularies.limits", "; ".join(hits["limit"]))

    d = strip_comments(read(DEFAULTS))
    check(dict(re.findall(r"export const (\w+) = ([^;]+);", d)) == V4A.EXPECTED_DEFAULTS and "import " not in d,
          "lib/market/marketDefaults.mjs still holds only mkt04a's sprint-mandated defaults",
          "the exemption must not become a place to hide labels")
    c = strip_comments(read(CONTRACT))
    exports = dict(re.findall(r"export const (\w+) = ([^;]+);", c))
    check(exports == EXPECTED_CONTRACT and "import " not in c and not re.search(r"label|Label", c),
          "lib/market/chartContract.mjs (the scan's second exemption) holds only server contract CODES, no label",
          "the chart's codes are fixed by the API contract; every caption comes from the server", f"exports {sorted(exports)}")

    parse_re = re.compile(r"\b(Number\(|parseFloat\(|parseInt\()")
    chart_files = [*[MARKET_LIB / f"{n}.mjs" for n in NEW_LIB], *COMPONENTS.glob("*")]
    parsers = sorted(f.name for f in chart_files if parse_re.search(strip_comments(read(f))))
    check(set(parsers) == PARSERS,
          "among the chart modules and every market component, only rebase.mjs and scales.mjs convert strings to "
          "numbers (Number/parseFloat/parseInt)",
          "chart numbers are floats for plotting only; parsing lives in the two modules the golden test pins",
          f"parsing in {parsers}")
    if parse_re.search(strip_comments(read(MARKET_LIB / "gridRequest.mjs"))):
        find("mkt04a's lib/market/gridRequest.mjs calls Number() on ISO date PARTS (yearsAgo), never on a series "
             "value; it is a grid module, outside the chart's parse rule, and unchanged by this sprint")
    for f in GRID_FILES:
        bad = re.findall(r"\b(Number\(|parseFloat\(|parseInt\(|toFixed\(|toLocaleString\(|Math\.)", strip_comments(read(f)))
        check(not bad, f"{rel(f)} still never converts a grid value to a number",
              "the Grid tab shows the server's exact strings", ",".join(bad))

    api_refs = re.compile(r"NEXT_PUBLIC_API_URL|/api/v1|localhost:8000|@/lib/api\b|@/lib/apiForward|marketForward|marketRoutes")
    client_bad = [rel(f) for f in [PAGE, *sorted(COMPONENTS.glob("*")), *[MARKET_LIB / f"{n}.mjs" for n in NEW_LIB]]
                  if api_refs.search(strip_comments(read(f)))]
    check(not client_bad, "no market component or chart module references the FastAPI base URL or the server forward",
          "client components reach the API only through Next.js routes (Rule 5)", ", ".join(client_bad))
    routes = node_eval(f"import('{(MARKET_LIB / 'chartRequest.mjs').as_uri()}').then(m => "
                       "process.stdout.write(JSON.stringify([m.SERIES_ROUTE, m.KEY_DATES_ROUTE])))")
    check(routes.strip() == '["/api/market/series","/api/market/key-dates"]',
          "the chart's two routes are /api/market/series and /api/market/key-dates",
          "the host-aware Next.js forwards, never the backend", routes.strip())
    panel = strip_comments(read(COMPONENTS / "ChartPanel.jsx"))
    fetches = re.findall(r"\bfetch\(([^,)]*)", panel)
    check(fetches == ["url"] and "async function fetchJson(url)" in panel
          and re.findall(r"fetchJson\(([^)]*)\)", panel) == ["url", "KEY_DATES_ROUTE"]
          and "createSeriesLoader({ fetchJson" in panel and "buildSeriesRequest(" in panel,
          "ChartPanel fetches only the key-dates route and the URL buildSeriesRequest built from SERIES_ROUTE",
          "no other client call exists", f"fetch args={fetches}")
    identity = re.compile(r"\b(org_id|user_id|orgId|userId)\b")
    id_bad = [rel(f) for f in [*COMPONENTS.glob("Chart*.jsx"), COMPONENTS / "MarketChart.jsx",
                               *[MARKET_LIB / f"{n}.mjs" for n in NEW_LIB]] if identity.search(strip_comments(read(f)))]
    check(not id_bad, "no chart file names org_id or user_id", "identity comes only from the session", ", ".join(id_bad))

    impure = []
    for f in sorted(MARKET_LIB.glob("*.mjs")):
        # marketRoutes.mjs is the server-side forward core: its fetch is injected (deps.fetch).
        pat = r"from [\"'](?:react|react-dom|next/[\w/]+)[\"']|\bdocument\.|\bwindow\."
        if f != ROUTES_CORE:
            pat += r"|\bfetch\("
        impure += [f"{rel(f)}: {x}" for x in re.findall(pat, strip_comments(read(f)))]
    check(not impure, "every lib/market module is pure: no React, Next or DOM, and no fetch outside the injected "
          "server forward", "pure logic is testable with Node's runner alone, and components stay thin", "; ".join(impure))

    req = strip_comments(read(MARKET_LIB / "chartRequest.mjs"))
    allv = "".join(strip_comments(read(f)) for f in files)
    check("!grantsRead(body) || !Array.isArray(body.series)" in req and not re.search(r"permissions\s*(\|\||\?\?)", allv),
          "a series response draws a chart only with an envelope granting read; no truthy fallback anywhere",
          "a lost envelope must fail CLOSED")
    chart = strip_comments(read(COMPONENTS / "MarketChart.jsx"))
    check(chart.count("createFrameScheduler(") == 2 and "framesRef.current?.drag.schedule(" in chart
          and "framesRef.current?.hover.schedule(" in chart and chart.count("onAnchorChange(") == 1
          and "at.g.onAnchorChange(" in chart,
          "pointer moves are coalesced onto animation frames; the anchor changes only inside the frame callback",
          "dragging recomputes lines per frame, not per pointer event (Task 4c)")
    check("new ResizeObserver(" in chart and "ro.disconnect()" in chart and "toLogical(" in chart and "hitTest(" in chart,
          "the chart sizes itself with a ResizeObserver and hit-tests in logical pixels",
          "Task 4b/4d: the 18-px rule must hold whatever the rendered width")
    slider = strip_comments(read(COMPONENTS / "AnchorSlider.jsx"))
    check('aria-label="Anchor date"' in slider and "stepAnchorIndex(index, e.key, periods.length)" in slider
          and 'type="range"' in slider and "useState" not in slider,
          "the anchor's range input is labelled 'Anchor date' and steps through stepAnchorIndex",
          "keyboard and screen-reader users can move the anchor (Task 4c)")

    design_bad = []
    for f in files:
        src = strip_comments(read(f))
        for pat, name in ((r"\bdark:", "dark:"), (r"gradient", "gradient"), (r"shadow", "shadow"),
                          ("[\U0001F300-\U0001FAFF☀-➿]", "emoji")):
            if re.search(pat, src, flags=re.I):
                design_bad.append(f"{rel(f)}: {name}")
    check(not design_bad, "no dark mode, gradient, shadow or emoji in the market UI",
          "light theme only, quiet brand (CLAUDE.md)", "; ".join(design_bad))


# ═══════════════════════════════════════════════════════════════════════════
# 5. Scope
# ═══════════════════════════════════════════════════════════════════════════
ALLOWED_PREFIXES = ("apps/web/", "docs/", "sprint_prompts/")
ALLOWED_FILES = {READ_SERVICE_REL, "apps/api/scripts/verify_mkt04b.py", SPRINT_MARKER,
                 "apps/api/scripts/verify_mkt04a.py"}
EXISTING_WEB_EDITS = {"apps/web/components/market/MarketIndicatorsView.jsx",
                      "apps/web/tests/market/render.test.mjs", "apps/web/tests/market/fixtures.mjs"}
UNTOUCHED = ["apps/web/components/market/GridPanel.jsx", "apps/web/components/market/MarketGridTable.jsx",
             "apps/web/components/market/SelectionPanel.jsx", "apps/web/lib/market/gridView.mjs",
             "apps/web/lib/market/gridRequest.mjs", "apps/web/lib/market/selection.mjs",
             "apps/web/lib/market/catalogModel.mjs", "apps/web/lib/market/marketDefaults.mjs",
             "apps/web/lib/market/marketRoutes.mjs", "apps/web/package.json", "CLAUDE.md",
             "apps/api/scripts/verify_mkt03.py", "apps/api/routers/market_data.py",
             "apps/api/services/market_data/read_repository.py", "apps/api/services/market_data/transforms.py"]


def scope() -> list[str]:
    base, sha = sprint_range()
    find(f"pre-sprint reference: {base}" + (f", sprint commit {sha[:10]}" if sha else " (sprint not yet committed: "
         "working tree + untracked files)"))
    files = changed_files()
    check(bool(files), "the sprint's diff is readable and non-empty", "the scope check needs a real diff")
    outside = [f for f in files if f not in ALLOWED_FILES and not f.startswith(ALLOWED_PREFIXES)]
    check(not outside, "changes are confined to apps/web, the series field, the verify/generator scripts, docs and "
          "the sprint files", "no other backend, database or config change rides along", ", ".join(outside))
    check(not [f for f in files if f.endswith(".sql") or "/migrations/" in f], "no DDL or migration file",
          "this sprint's only backend change is one additive response field")
    touched = [f for f in UNTOUCHED if f in files]
    check(not touched, "the Grid tab, selection, routes, package.json, CLAUDE.md, verify_mkt03 and the read "
          "repository/transforms/router are unchanged", "Task rules: do not change the Grid tab or anything else",
          ", ".join(touched))
    modified_web = [f for f in changed_files(modified_only=True) if f.startswith("apps/web/")]
    check(set(modified_web) <= EXISTING_WEB_EDITS,
          "the only existing apps/web files edited are MarketIndicatorsView (the tab), render.test and fixtures",
          "existing pages keep their behaviour", ", ".join(sorted(set(modified_web) - EXISTING_WEB_EDITS)))
    view_diff = git("diff", "-U0", base, *([sha] if sha else []), "--", "apps/web/components/market/MarketIndicatorsView.jsx")
    removed = [l for l in view_diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    check(all(re.search(r"useState|QUIET|The chart arrives|data-market-tab=\"chart\"|</p>|^-\s+\) : \($", l) for l in removed),
          "MarketIndicatorsView only swaps the placeholder for ChartPanel (and its imports)",
          "the selection panel, tabs and Grid wiring are untouched", f"{len(removed)} removed lines")

    # The two mkt04a artefacts this sprint had to edit — recorded, not hidden.
    old_render = base_text("apps/web/tests/market/render.test.mjs")
    new_render = read(TESTS / "render.test.mjs")
    titles = lambda s: set(re.findall(r'^test\("([^"]+)"', s, flags=re.M))  # noqa: E731
    gone, added = titles(old_render) - titles(new_render), titles(new_render) - titles(old_render)
    check(gone == {"the Chart tab shows only the placeholder"} and len(added) == 1,
          "render.test.mjs: exactly the placeholder test was replaced, every other mkt04a render test kept",
          "the placeholder no longer exists; nothing else about mkt04a's proofs may weaken", f"-{gone} +{added}")
    find("render.test.mjs BEFORE: 'the Chart tab shows only the placeholder' asserted the text 'The chart arrives in "
         "the next release.'; AFTER: the Chart tab renders data-market-control=\"chart-controls\" and NOT the "
         "placeholder or the grid controls")
    if "apps/api/scripts/verify_mkt04a.py" in files:
        old4a = ast.parse(base_text("apps/api/scripts/verify_mkt04a.py") or "pass")
        new4a = ast.parse(read(HERE.parent / "verify_mkt04a.py"))
        diff = sorted(k for k in set(_top_level(old4a)) | set(_top_level(new4a))
                      if _top_level(old4a).get(k) != _top_level(new4a).get(k))
        check(set(diff) <= {"ui_files", "changed_files", "diff_scope", "mkt04a_range", "=MKT04A_MARKER", "=TEST_WHY"},
              "verify_mkt04a.py: only its scope pinning (ui_files, changed_files, diff_scope, mkt04a_range) and one "
              "TEST_WHY note changed", "every mkt04a assertion keeps what it proves", f"changed={diff}")
        find("verify_mkt04a.py was correct only while mkt04a was UNCOMMITTED: it diffed `merge-base main HEAD` "
             "against the working tree and globbed every file in lib/market and components/market. With mkt04a on "
             "main (main == HEAD at mkt04b start) its scope checks measured the NEXT sprint and the Sidebar check "
             "failed with no change at all. Now pinned to mkt04a's own commit (git log --diff-filter=A on its route "
             "core); its 225 total shifts because render.test.mjs's Chart test changed")
    return files


# ═══════════════════════════════════════════════════════════════════════════
# 6. Lint and build (first execution)
# ═══════════════════════════════════════════════════════════════════════════
def lint(files: list[str]) -> None:
    npx = shutil.which("npx")
    if not check(npx is not None, "npx is on PATH", "lint runs through the repo's own ESLint config"):
        return
    out = pathlib.Path("/tmp/verify_mkt04b_eslint.json")
    proc = subprocess.run([npx, "eslint", "-f", "json", "-o", str(out), "."], cwd=WEB,
                          capture_output=True, text=True, timeout=1200)
    try:
        report = json.loads(out.read_text())
    except (OSError, json.JSONDecodeError):
        check(False, "ESLint produced a report", "lint must actually run", (proc.stdout + proc.stderr)[-600:])
        return
    ours = {f for f in files if f.startswith("apps/web/") and f.endswith((".js", ".jsx", ".mjs")) and (REPO / f).is_file()}
    linted = {str(pathlib.Path(r["filePath"]).resolve().relative_to(REPO)) for r in report}
    check(ours <= linted, "ESLint covered every JS file this sprint added or changed", "an unlinted file proves nothing",
          ", ".join(sorted(ours - linted)))
    ours_msgs, others = [], {}
    for r in report:
        p = str(pathlib.Path(r["filePath"]).resolve().relative_to(REPO))
        for m in r["messages"]:
            if p in ours:
                ours_msgs.append(f"{p}:{m.get('line')} {m.get('ruleId')}")
            elif m["severity"] == 2:
                others[p] = others.get(p, 0) + 1
    check(not ours_msgs, "ESLint reports no error and no warning in any file this sprint touched",
          "new code meets the repo's lint rules (react-hooks/refs included)", "; ".join(ours_msgs[:15]))
    if others:
        find(f"`npm run lint` still exits non-zero on files this sprint did not touch: {sum(others.values())} errors in "
             f"{len(others)} files (the mkt04a baseline)", "not fixed here: existing pages are out of scope")
    else:
        check(proc.returncode == 0, "npm run lint (eslint) exits 0", "the whole app lints clean")


def build() -> None:
    npm = shutil.which("npm")
    if not check(npm is not None, "npm is on PATH", "the production build runs through `npm run build`"):
        return
    try:
        proc = subprocess.run([npm, "run", "build"], cwd=WEB, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        check(False, "npm run build exits 0", "only `next build` catches server-only imports in client bundles",
              "timed out after 1800s")
        return
    out = proc.stdout + proc.stderr
    check(proc.returncode == 0, "npm run build exits 0",
          "only `next build` catches a server-only import in a client bundle, or a JSX error the tests' loader allows",
          "" if proc.returncode == 0 else out[-900:].replace("\n", " "))
    if proc.returncode == 0:
        check(bool(re.search(r"[○ƒ●◐]\s+/market\b", out)) and "/api/market/series" in out
              and "/api/market/key-dates" in out,
              "the build lists /market and the two routes the chart calls", "the chart is really part of the app")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE B — the series function against the real database
# ═══════════════════════════════════════════════════════════════════════════
FIX_PREFIX = "verify.mkt04b."
SERIES_T = "market_data.indicator_series"
OBS_T = "market_data.indicator_observations"
Q6 = Decimal("0.000001")


class _Rollback(Exception):
    pass


def q6_text(x: Decimal | None) -> str | None:
    """The verify's OWN quantization (not the code under test)."""
    if x is None or x == 0:
        return None
    return format(x.quantize(Q6, rounding=ROUND_HALF_EVEN), "f")


async def _counts(conn) -> dict:
    async with conn.transaction():
        return {
            "fixture_series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key LIKE $1", FIX_PREFIX + "%"),
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T}"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T}"),
        }


async def phase_b() -> None:
    print("── PHASE B — live", flush=True)
    from _doppler_env import hydrate_from_doppler
    import asyncpg

    from services.market_data import read_repository as repo
    from services.market_data import read_service as svc

    loaded, err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS", flush=True)
    elif err:
        print(f"[INFO] Doppler hydration skipped: {err} — using the ambient environment", flush=True)
    dsn = (os.environ.get("DATABASE_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    if not check(bool(dsn), "DATABASE_URL present", "Phase B reads the real data"):
        return
    try:
        conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
        reader = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        check(False, "DB connect", "Phase B reads the real data", type(exc).__name__)
        return
    try:
        bypass = await reader.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the connection's role has rolbypassrls = false",
              "the series function must work on the application's real, RLS-enforced role", f"rolbypassrls={bypass}")

        keys = ["fred.dgs10", "fred.sp500"]
        monthly_key = await reader.fetchval(
            f"SELECT series_key FROM {SERIES_T} WHERE ingest_status = 'active' AND frequency = 'monthly' "
            "ORDER BY sort_order, series_key LIMIT 1")
        if monthly_key:
            keys.append(monthly_key)
        check(len(keys) == 3, "three real series chosen (two daily FRED series + the first active monthly one)",
              "different frequencies prove the field is not a function of the resampled points", f"keys={keys}")

        for key in keys:
            sd_sql = await reader.fetchval(
                f"SELECT stddev_samp(o.value) FROM {OBS_T} o JOIN {SERIES_T} s ON s.id = o.series_id "
                "WHERE s.series_key = $1 AND o.valid_to IS NULL AND o.system_to IS NULL", key)
            got = {}
            async with conn.transaction():
                await repo.make_read_only(conn)
                for label, q in (("monthly", svc.SeriesQuery([key], None, None, "monthly")),
                                 ("native", svc.SeriesQuery([key], None, None, "native")),
                                 ("window", svc.SeriesQuery([key], date(2015, 1, 1), date(2016, 12, 31), "quarterly"))):
                    body = await svc.read_series(conn, q)
                    got[label] = body["series"][0]["stddev"]
            exp = q6_text(sd_sql)
            check(got["monthly"] == exp and isinstance(exp, str),
                  f"{key}: stddev equals SQL stddev_samp quantized to 6 dp", "the browser's sigma divides by exactly "
                  "the grid's sd", f"api={got['monthly']} sql={sd_sql}")
            check(len(set(got.values())) == 1, f"{key}: stddev is identical at monthly, native and a 2015-16 quarterly window",
                  "full history, independent of window and frequency (a downsampled sd would differ)", f"got={got}")

        before = await _counts(reader)
        single = zero = two = "unset"
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                ids = {}
                for i, (suffix, points) in enumerate((
                    ("single", [("2020-01-31", "12.5")]),
                    ("zero", [("2020-01-31", "5"), ("2020-02-29", "5"), ("2020-03-31", "5")]),
                    ("two", [("2020-01-31", "1"), ("2020-02-29", "4")]),
                )):
                    ids[suffix] = await conn.fetchval(
                        f"""INSERT INTO {SERIES_T} (series_key, name, category, region, frequency, default_transform,
                                cost_tier, license_class, source_provider, source_code, notes, sort_order, ingest_status)
                            VALUES ($1, $2, 'Verify mkt04b', 'US', 'monthly', 'rebase_100', 'free', 'public_domain',
                                    'verifyfake', $3, 'mkt04b verify fixture (rolled back)', $4, 'active') RETURNING id""",
                        FIX_PREFIX + suffix, f"verify mkt04b {suffix}", f"VMKT04B{i}", -4000 + i)
                    await conn.execute(
                        f"INSERT INTO {OBS_T} (series_id, obs_date, value) "
                        "SELECT $1, d, v FROM unnest($2::date[], $3::numeric[]) AS t(d, v)",
                        ids[suffix], [date.fromisoformat(d) for d, _ in points], [Decimal(v) for _, v in points])
                await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
                await repo.make_read_only(conn)
                body = await svc.read_series(conn, svc.SeriesQuery(
                    [FIX_PREFIX + "single", FIX_PREFIX + "zero", FIX_PREFIX + "two"], None, None, "native"))
                by = {s["series_key"]: s for s in body["series"]}
                single = by[FIX_PREFIX + "single"]["stddev"]
                zero = by[FIX_PREFIX + "zero"]["stddev"]
                two = by[FIX_PREFIX + "two"]["stddev"]
                raise _Rollback
        except _Rollback:
            pass
        check(single is None, "a series with ONE observation returns stddev null",
              "stddev_samp is undefined under two points; the chart must show 'zero_variance', not divide by junk",
              f"got={single!r}")
        check(zero is None, "a constant series returns stddev null", "zero variance is undefined for sigma", f"got={zero!r}")
        check(two == "2.121320", "a two-point series [1, 4] returns '2.121320' (= 3/sqrt(2), 6 dp)",
              "proves the fixture path really computes, so the two nulls above are not vacuous", f"got={two!r}")
        after = await _counts(reader)
        check(after == before and after["fixture_series"] == 0,
              "zero fixture rows remain and the series / observation counts are exactly as before (rolled back)",
              "Phase B must never write real data — rollback, then an independent re-count", f"before={before} after={after}")
    finally:
        await conn.close()
        await reader.close()


# ═══════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also run Phase B against the real database")
    args = ap.parse_args()

    print("── PHASE A — golden vectors", flush=True)
    golden()
    print("── PHASE A — the stddev field", flush=True)
    stddev_field()
    print("── PHASE A — node:test suites", flush=True)
    node_tests()
    print("── PHASE A — static scans", flush=True)
    static_scans()
    print("── PHASE A — scope", flush=True)
    files = scope()
    print("── PHASE A — lint and build", flush=True)
    lint(files)
    build()
    if args.live:
        try:
            asyncio.run(phase_b())
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            check(False, "Phase B ran to completion", "an exception aborted the live proofs",
                  f"{type(exc).__name__}: {str(exc)[:300]}")
    else:
        skip("Phase B (live database) was not run — pass --live")
    print(f"TOTAL: {_n_pass} passed, {_n_fail} failed", flush=True)
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
