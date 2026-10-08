"""verify_mkt04a.py — the market indicators page: Next.js routes, page shell,
selection panel and grid (mkt04a.structural).

WHAT WAS BUILT (this script proves it):
  * apps/web/lib/market/marketRoutes.mjs — the pure forward core and the table
    of all eleven market routes; apps/web/lib/marketForward.js binds it to the
    host-aware Auth0 client (lib/authServer getRequestAuthClient); nine route
    files under apps/web/app/api/market/** export the eleven handlers.
  * apps/web/app/market/page.js + components/market/* — the page, fail-closed
    on the catalog's permissions envelope, the selection panel and the grid.
  * apps/web/lib/market/{catalogModel,selection,gridRequest,gridView,
    marketDefaults}.mjs — pure logic (no React, no DOM).
  * apps/web/tests/market/*.test.mjs — node:test suites (run by `npm test`),
    which import the REAL modules and render the REAL components with
    react-dom/server (JSX compiled by tests/market/jsxLoader.mjs).
  * One navigation entry (/market) in components/Sidebar.jsx NAV_ITEMS and
    lib/menuVisibility.mjs MENU_ITEMS, ungated (the API's gate is session-only).

No database, no secrets: every check is static, a node:test run, ESLint, or
the production build. The operator's run is the first time the build executes.

Output: every line starts with [PASS], [FAIL], [FIND] or [SKIP]; the last line
is `TOTAL: <passed> passed, <failed> failed`. Exit 1 on any [FAIL].

Run:  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04a.py
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[3]
WEB = REPO / "apps" / "web"
API = REPO / "apps" / "api"

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


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?<![:\"'])//.*$", "", src, flags=re.M)


def string_literals(src: str) -> list[str]:
    return [m.group(2) for m in re.finditer(r"(['\"`])((?:\\.|(?!\1).)*)\1", src)]


def rel(p: pathlib.Path) -> str:
    return str(p.relative_to(REPO))


# ═══════════════════════════════════════════════════════════════════════════
# Files under test
# ═══════════════════════════════════════════════════════════════════════════
MARKET_LIB = WEB / "lib" / "market"
COMPONENTS = WEB / "components" / "market"
PAGE = WEB / "app" / "market" / "page.js"
FORWARD = WEB / "lib" / "marketForward.js"
ROUTES_CORE = MARKET_LIB / "marketRoutes.mjs"
DEFAULTS = MARKET_LIB / "marketDefaults.mjs"
TESTS = WEB / "tests" / "market"

ROUTE_FILES = {
    "market/catalog": {"GET": "catalog"},
    "market/series": {"GET": "series"},
    "market/grid": {"POST": "grid"},
    "market/correlations": {"POST": "correlations"},
    "market/key-dates": {"GET": "keyDates"},
    "market/key-dates/custom": {"POST": "customDateCreate"},
    "market/key-dates/custom/[id]": {"DELETE": "customDateDelete"},
    "market/views": {"GET": "views", "POST": "viewCreate"},
    "market/views/[id]": {"PUT": "viewUpdate", "DELETE": "viewDelete"},
}
# The verify's OWN copy of the backend contract (CONFIRMED REAL FACTS).
BACKEND = {
    "catalog": ("GET", "/market/catalog"),
    "series": ("GET", "/market/series"),
    "grid": ("POST", "/market/grid"),
    "correlations": ("POST", "/market/correlations"),
    "keyDates": ("GET", "/market/key-dates"),
    "customDateCreate": ("POST", "/market/key-dates/custom"),
    "customDateDelete": ("DELETE", "/market/key-dates/custom/{id}"),
    "views": ("GET", "/market/views"),
    "viewCreate": ("POST", "/market/views"),
    "viewUpdate": ("PUT", "/market/views/{id}"),
    "viewDelete": ("DELETE", "/market/views/{id}"),
}

DESIGN_TOKENS = {
    "#1B2B4B", "#C5A880", "#E8D5A3", "#FAF9F6", "#F5F1EB", "#FFFFFF",
    "#0F172A", "#334155", "#64748B", "#E2E8F0", "#9B2335", "#2D6A4F", "#ECE8DD",
}
EXPECTED_DEFAULTS = {
    "DEFAULT_MODE": '"default"',
    "DEFAULT_FREQUENCY": '"monthly"',
    "DEFAULT_ANCHOR_YEARS_BACK": "5",
    "UNRESTRICTED_LICENSE_CLASS": '"public_domain"',
    "KIND_INDICATOR": '"indicator"',
    "KIND_SECURITY": '"security"',
}


def ui_files() -> list[pathlib.Path]:
    """The new UI code the no-hardcoding scan covers.

    Once mkt04a is committed, exactly the files mkt04a itself added (mkt04b
    pinned this: a later sprint's files in the same directories carry their
    own contract codes and are scanned by that sprint's verify, against its
    own exemptions)."""
    files = [PAGE, *sorted(COMPONENTS.glob("*")), *sorted(MARKET_LIB.glob("*"))]
    rng = mkt04a_range()
    if rng:
        added = set(git("diff", "--name-only", "--diff-filter=A", *rng).split())
        files = [f for f in files if rel(f) in added]
    return [f for f in files if f.is_file() and f not in (DEFAULTS, ROUTES_CORE)]


# ═══════════════════════════════════════════════════════════════════════════
# 1. Environment facts (Task 1)
# ═══════════════════════════════════════════════════════════════════════════
def environment() -> dict:
    pkg = json.loads(read(WEB / "package.json"))
    scripts = pkg.get("scripts", {})
    check(scripts.get("test") == "node --test tests/",
          "apps/web has a `test` script running Node's built-in runner",
          "the repo had no test runner; node:test needs no new dependency")
    if "typecheck" in scripts:
        find("apps/web has a typecheck script", scripts["typecheck"])
    else:
        skip("typecheck", "apps/web is JavaScript (jsconfig.json, no tsconfig) and has no typecheck script")
    return pkg


# ═══════════════════════════════════════════════════════════════════════════
# 2. node:test suites — real modules, real components
# ═══════════════════════════════════════════════════════════════════════════
TEST_WHY = {
    "marketRoutes.test.mjs": "route handlers must authenticate, forward to the right backend path and method, "
                             "pass status and error text through, never cache, never add identity",
    "selection.test.mjs": "the selection must hold exactly [{kind, key}] (mkt04c persists it), honour the "
                          "server's limit, and never admit an unselectable security or note",
    "gridRequest.test.mjs": "the grid body must carry only fields the API accepts; one request per burst; "
                            "an older response must never overwrite a newer one",
    "render.test.mjs": "a lost envelope must fail CLOSED (no controls); the grid must show the server's "
                       "strings unparsed; the nav entry must be visible to any signed-in user "
                       "(mkt04b: the Chart-tab test now asserts the chart, not the placeholder)",
}


def node_tests() -> None:
    node = shutil.which("node")
    if not check(node is not None, "node is on PATH", "the UI proofs run as node:test suites"):
        return
    for name, why in TEST_WHY.items():
        path = TESTS / name
        if not check(path.is_file(), f"test suite {rel(path)} exists", why):
            continue
        try:
            proc = subprocess.run(
                [node, "--test", "--test-reporter=tap", str(path.relative_to(WEB))],
                cwd=WEB, capture_output=True, text=True, timeout=600,
            )
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
# 3. Route handlers — static
# ═══════════════════════════════════════════════════════════════════════════
def route_table() -> list[dict] | None:
    node = shutil.which("node")
    if not node:
        return None
    js = (f"import('{ROUTES_CORE.as_uri()}').then(m => process.stdout.write(JSON.stringify(m.MARKET_ROUTES)))")
    proc = subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=120)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def routes_static() -> None:
    table = route_table()
    if not check(isinstance(table, list) and len(table) == 11,
                 "MARKET_ROUTES lists exactly eleven routes",
                 "one handler per backend route in the API contract, no more, no fewer",
                 f"got {None if table is None else len(table)}"):
        return
    by_id = {r["id"]: r for r in table}
    for rid, (method, backend) in BACKEND.items():
        r = by_id.get(rid, {})
        check(r.get("method") == method and r.get("backend") == backend,
              f"route {rid} forwards to {method} /api/v1{backend}",
              "a handler aimed at the wrong backend path or method silently breaks one feature",
              f"table has {r.get('method')} {r.get('backend')}")
    check(all(r.get("query") is True for r in table if r["id"] == "series")
          and not any(r.get("query") for r in table if r["id"] != "series"),
          "only GET series passes the browser's query string through",
          "series is the only endpoint that takes query parameters; the catalog refuses any")
    check({r["id"] for r in table if r.get("body")} == {"grid", "correlations", "customDateCreate", "viewCreate", "viewUpdate"},
          "exactly the POST and PUT routes forward a body", "a GET or DELETE must never carry a body")

    core = strip_comments(read(ROUTES_CORE))
    check('"Cache-Control": NO_STORE' in core and 'const NO_STORE = "no-store"' in core and "cache: NO_STORE" in core,
          "the forward sets Cache-Control: no-store on every response and fetches with no-store",
          "views and custom dates are per-user data; a cached response could leak across users")
    check("status: res.status" in core and "await res.text()" in core,
          "the backend's status and body text are returned unchanged",
          "a backend 401/403/422 must reach the page as itself, never as a 200 or a rewritten message")
    check(re.search(r"JSON\.parse\(bodyText\)", core) is not None and "body: route.body ? bodyText" in core,
          "a body is validated as JSON then forwarded as the caller's own text",
          "re-serialising could add or reorder fields; forwarding the text proves nothing was added")
    check("console." not in core and "console." not in strip_comments(read(FORWARD)),
          "the forward logs nothing", "bodies and tokens must never reach a log")

    fwd = strip_comments(read(FORWARD))
    check('import { getRequestAuthClient } from "@/lib/authServer"' in fwd and "getAuthClient: getRequestAuthClient" in fwd,
          "lib/marketForward.js authenticates with the host-aware getRequestAuthClient",
          "a fixed 2nd Act client misreads a Hollisworks session (the production redirect-loop bug)")
    check("NEXT_PUBLIC_API_URL" in fwd, "the forward uses the same API base variable as lib/apiForward.js",
          "one base-URL variable for every server-side forward")

    identity = re.compile(r"\b(org_id|user_id|orgId|userId)\b")
    for f in [ROUTES_CORE, FORWARD]:
        check(identity.search(strip_comments(read(f))) is None, f"{rel(f)} never names org_id or user_id",
              "identity comes only from the session's token; a field could let a caller choose an org")

    for sub, methods in ROUTE_FILES.items():
        f = WEB / "app" / "api" / sub / "route.js"
        if not check(f.is_file(), f"route file app/api/{sub}/route.js exists", "the browser reaches the API only here"):
            continue
        src = strip_comments(read(f))
        exported = dict(re.findall(r"export const (GET|POST|PUT|DELETE|PATCH) = marketHandler\(\"(\w+)\"\);", src))
        check(exported == methods, f"app/api/{sub} exports {methods}",
              "every method must be the shared host-aware handler for its own backend route", f"found {exported}")
        check('import { marketHandler } from "@/lib/marketForward";' in src and identity.search(src) is None
              and "fetch(" not in src,
              f"app/api/{sub} is a thin forward with no identity field and no fetch of its own",
              "a handler that forwards on its own could skip the session check")
        for rid in methods.values():
            web = by_id.get(rid, {}).get("web")
            check(web == f"/api/{sub}", f"table entry {rid} matches its file location",
                  "the table the tests exercise must be the routes the app actually serves", f"table web={web}")


# ═══════════════════════════════════════════════════════════════════════════
# 4. No hardcoding — static scan of the new UI code
# ═══════════════════════════════════════════════════════════════════════════
def py_tuple(path: pathlib.Path, name: str) -> list[str]:
    m = re.search(rf"^{name}\s*=\s*\(([^)]*)\)", read(path), flags=re.M)
    return re.findall(r"\"([^\"]+)\"", m.group(1)) if m else []


def vocabulary_tokens() -> tuple[set[str], set[str], set[str]]:
    svc = API / "services" / "market_data"
    palette = read(svc / "palette.py")
    base = re.search(r"CATEGORY_BASE = \{(.*?)\}", palette, flags=re.S).group(1)
    categories = set(re.findall(r"\"([^\"]+)\":", base))
    palette_hex = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}", palette)}
    vocab = set(py_tuple(svc / "transforms.py", "MODES"))
    vocab |= set(py_tuple(svc / "transforms.py", "DEFAULT_TRANSFORMS"))
    vocab |= set(py_tuple(svc / "resample.py", "REQUEST_FREQUENCIES"))
    vocab |= set(py_tuple(svc / "adapters.py", "REAL_PROVIDERS"))
    doc = read(REPO / "docs" / "MARKET_DATA_DESIGN_V1.md")
    sec4 = doc.split("### 4. `license_class`", 1)[-1].split("\n### ", 1)[0]
    vocab |= set(re.findall(r"^\| `([a-z_]+)` \|", sec4, flags=re.M))
    vocab |= {  # grid warnings / unavailable reasons / unselectable reason / price source (API contract)
        "rate_like_series_indexed", "native_frequency_coarser_than_grid", "non_positive_anchor",
        "zero_variance", "no_observations", "no_price_history", "indicator_series",
        "index", "structured_note",
    }
    return categories, palette_hex, vocab


def no_hardcoding() -> None:
    files = ui_files()
    check(len(files) >= 10, "the scan covers the page, every market component and the pure modules",
          "a scan over too few files proves nothing", f"{len(files)} files")
    categories, palette_hex, vocab = vocabulary_tokens()
    check(len(categories) >= 5 and len(vocab) >= 15 and len(palette_hex) >= 5,
          "the scan's vocabulary was read from the backend source, not typed here",
          "a hand-typed list drifts from what the server really sends",
          f"{len(categories)} categories, {len(vocab)} vocabulary values, {len(palette_hex)} palette hexes")
    find("the server publishes no label vocabulary for license classes, grid warnings, unavailable reasons or "
         "unselectable reasons; the page shows those codes as text (underscores as spaces)",
         "lib/market/catalogModel.mjs vocabText uses a server [{key,label}] list if one ever appears")
    limits = {"40", "2000", "400", "30000", "300000"}

    hits = {"category": [], "vocab": [], "hex": [], "palette": [], "limit": []}
    for f in files:
        src = strip_comments(read(f))
        lits = string_literals(src)
        for lit in lits:
            if lit in categories:
                hits["category"].append(f"{rel(f)}: {lit!r}")
            if lit in vocab:
                hits["vocab"].append(f"{rel(f)}: {lit!r}")
        for h in re.findall(r"#[0-9A-Fa-f]{6}\b", src):
            if h.upper() not in DESIGN_TOKENS:
                hits["hex"].append(f"{rel(f)}: {h}")
            if h.upper() in palette_hex and h.upper() not in DESIGN_TOKENS:
                hits["palette"].append(f"{rel(f)}: {h}")
        for n in re.findall(r"(?<![\w\-\[.#])(\d+)(?![\w\].%])", src):
            if n in limits:
                hits["limit"].append(f"{rel(f)}: {n}")
    check(not hits["category"], "no category name from the server's palette appears in the UI code",
          "categories come from the catalog (Rule 1)", "; ".join(hits["category"]))
    check(not hits["vocab"], "no vocabulary value (mode, frequency, transform, provider, license, reason) is a literal",
          "option lists and codes come from the server's vocabularies (Rule 1)", "; ".join(hits["vocab"]))
    check(not hits["palette"], "no series/category colour from the server palette appears in the UI code",
          "colours come from the catalog (Rule 1)", "; ".join(hits["palette"]))
    check(not hits["hex"], "every hex colour in the UI code is a design token",
          "the page's own chrome uses only the brand tokens", "; ".join(hits["hex"]))
    check(not hits["limit"], "no API limit (40 keys, 2,000 rows, 400 days, point caps) is a literal",
          "limits come from vocabularies.limits so a server change cannot desync the page", "; ".join(hits["limit"]))

    d = strip_comments(read(DEFAULTS))
    exports = dict(re.findall(r"export const (\w+) = ([^;]+);", d))
    check(exports == EXPECTED_DEFAULTS and "import " not in d,
          "lib/market/marketDefaults.mjs (the scan's one exemption) holds only the sprint-mandated defaults",
          "the exemption must not become a place to hide hardcoded labels", f"exports {sorted(exports)}")

    api_refs = re.compile(r"NEXT_PUBLIC_API_URL|/api/v1|localhost:8000|@/lib/api\b|@/lib/apiForward|marketForward|marketRoutes")
    client_bad = [rel(f) for f in [PAGE, *sorted(COMPONENTS.glob("*"))] if api_refs.search(strip_comments(read(f)))]
    check(not client_bad, "no client component imports or calls the FastAPI base URL or the server forward",
          "client components reach the API only through Next.js routes (Rule 5)", ", ".join(client_bad))
    fetches = []
    for f in sorted(COMPONENTS.glob("*.jsx")):
        fetches += re.findall(r"fetch\(\s*([\"'`][^\"'`]*[\"'`])", strip_comments(read(f)))
    check(bool(fetches) and all(u.strip("\"'`").startswith("/api/market/") for u in fetches),
          "every fetch in the market components targets /api/market/*",
          "a client fetch anywhere else would bypass the host-aware forward", ", ".join(fetches))
    for f in [MARKET_LIB / "gridView.mjs", COMPONENTS / "MarketGridTable.jsx", COMPONENTS / "GridPanel.jsx"]:
        src = strip_comments(read(f))
        bad = re.findall(r"\b(Number\(|parseFloat\(|parseInt\(|toFixed\(|toLocaleString\(|Math\.)", src)
        check(not bad, f"{rel(f)} never converts a grid value to a number",
              "the grid shows the server's exact decimal strings; a float round-trip can change digits", ",".join(bad))
    for f in sorted(MARKET_LIB.glob("*.mjs")):
        impure = re.findall(r"from [\"'](?:react|react-dom|next/[\w/]+)[\"']|\bdocument\.|\bwindow\.", strip_comments(read(f)))
        check(not impure, f"{rel(f)} has no React or Next import and no DOM access",
              "pure logic must be testable with Node's runner alone", ", ".join(impure))
    design_bad = []
    for f in files:
        src = strip_comments(read(f))
        if re.search(r"\bdark:", src):
            design_bad.append(f"{rel(f)}: dark:")
        if re.search(r"gradient", src):
            design_bad.append(f"{rel(f)}: gradient")
        if re.search(r"shadow-(md|lg|xl|2xl)", src):
            design_bad.append(f"{rel(f)}: heavy shadow")
        if re.search("[\U0001F300-\U0001FAFF☀-➿]", src):
            design_bad.append(f"{rel(f)}: emoji")
    check(not design_bad, "no dark mode, gradient, heavy shadow or emoji in the new UI",
          "light theme only, quiet brand (CLAUDE.md Design Tokens / Brand System)", "; ".join(design_bad))


# ═══════════════════════════════════════════════════════════════════════════
# 5. Page shell, fail-closed structure, navigation — static
# ═══════════════════════════════════════════════════════════════════════════
def page_and_nav() -> None:
    src = strip_comments(read(PAGE))
    check('import { getHostSession } from "@/lib/authServer"' in src and "@/lib/auth0" not in src,
          "the page checks its session with the host-aware getHostSession",
          "a page checking the wrong tenant's client loops on redirect (production incident)")
    check('redirect("/auth/login?returnTo=/market")' in src,
          "no session redirects to login and back to /market", "signing out must send the user to login")
    check("<AppShell" in src and "Market indicators" in src and "<MarketIndicators />" in src,
          "the page sits in the standard AppShell titled 'Market indicators'", "the app's standard layout")

    view = strip_comments(read(COMPONENTS / "MarketIndicatorsView.jsx"))
    ready_at = view.find("function ReadyView")
    outside = view[:ready_at]
    check(ready_at > 0 and "<SelectionPanel" not in outside and "<GridPanel" not in outside,
          "the selection panel and the grid exist only inside the ready branch",
          "there must be no render path to the controls without a verified envelope")
    model = strip_comments(read(MARKET_LIB / "catalogModel.mjs"))
    check("body.permissions.can_read === true" in model and not re.search(r"permissions\s*\|\|", model + view),
          "read is granted only by an explicit can_read === true, with no truthy fallback",
          "a lost envelope must fail CLOSED, never default to showing controls")
    grid = strip_comments(read(COMPONENTS / "GridPanel.jsx"))
    check("grantsRead(data)" in grid,
          "a grid response without its envelope is an error, not a table",
          "every permission-gated response is checked, not only the catalog")

    sidebar = read(WEB / "components" / "Sidebar.jsx")
    nav_block = sidebar.split("const NAV_ITEMS = [", 1)[-1].split("];", 1)[0]
    check('href: "/market"' in nav_block, "Sidebar NAV_ITEMS carries the /market entry",
          "the page must be reachable from navigation")
    check(re.search(r"\{NAV_ITEMS\.map\(", sidebar) is not None and "NAV_ITEMS.filter" not in sidebar,
          "NAV_ITEMS renders unfiltered for every signed-in user",
          "matches the API's session-only gate: any valid login may read market data")
    menu = read(WEB / "lib" / "menuVisibility.mjs")
    check('{ href: "/market", label: "Market Indicators", gate: null }' in menu,
          "lib/menuVisibility.mjs MENU_ITEMS lists /market with gate null",
          "the single source of menu rules agrees with the sidebar")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Nothing else changed
# ═══════════════════════════════════════════════════════════════════════════
ALLOWED = ("apps/web/", "docs/", "sprint_prompts/")
ALLOWED_FILES = {"apps/api/scripts/verify_mkt04a.py"}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=120).stdout


# mkt04b: this section used to diff `merge-base main HEAD` against the working
# tree. That is right only while mkt04a is uncommitted on its own branch. Once
# mkt04a is on main, the same diff measures the NEXT sprint's changes (and the
# Sidebar check fails with no change at all). The range is now pinned to
# mkt04a's own commit, located by the commit that ADDED its route core, and
# falls back to the old working-tree diff only before that commit exists.
MKT04A_MARKER = "apps/web/lib/market/marketRoutes.mjs"


def mkt04a_range() -> tuple[str, str] | None:
    hits = git("log", "--format=%H", "--diff-filter=A", "--", MKT04A_MARKER).split()
    return (f"{hits[-1]}^", hits[-1]) if hits else None


def changed_files() -> list[str]:
    rng = mkt04a_range()
    if rng:
        return sorted(set(git("diff", "--name-only", *rng).split()))
    base = git("merge-base", "main", "HEAD").strip()
    if not base:
        return []
    tracked = git("diff", "--name-only", base).split()
    untracked = git("ls-files", "--others", "--exclude-standard").split()
    return sorted(set(tracked) | set(untracked))


def diff_scope(pkg: dict) -> list[str]:
    files = changed_files()
    rng = mkt04a_range()
    if rng:
        find(f"scope is mkt04a's own commit {rng[1][:10]} (pinned by the commit that added {MKT04A_MARKER})")
        pkg = json.loads(git("show", f"{rng[1]}:apps/web/package.json") or "{}")
    check(bool(files), "the diff against main is readable and non-empty", "the scope check needs a real diff")
    outside = [f for f in files if f not in ALLOWED_FILES and not f.startswith(ALLOWED)]
    check(not outside, "changes are confined to apps/web, verify_mkt04a.py, docs and the sprint files",
          "no backend, database or other change rides along with a UI sprint", ", ".join(outside))
    base = rng[0] if rng else git("merge-base", "main", "HEAD").strip()
    after = [rng[1]] if rng else []
    try:
        old = json.loads(git("show", f"{base}:apps/web/package.json"))
    except json.JSONDecodeError:
        old = {}
    same = all(old.get(k) == pkg.get(k) for k in ("dependencies", "devDependencies"))
    check(same, "apps/web dependencies are unchanged", "no new dependency was needed (Task 1)")
    old_scripts = {k: v for k, v in (old.get("scripts") or {}).items()}
    new_scripts = dict(pkg.get("scripts") or {})
    new_scripts.pop("test", None)
    check(old_scripts == new_scripts, "the only package.json script change is the new `test` script",
          "build, dev and lint must behave exactly as before")
    existing_touched = [f for f in git("diff", "--name-only", *(["--diff-filter=M"] if rng else []), base, *after).split()
                        if f.startswith("apps/web/") and f not in ("apps/web/package.json",
                                                                   "apps/web/components/Sidebar.jsx",
                                                                   "apps/web/lib/menuVisibility.mjs")]
    check(not existing_touched, "no existing apps/web file changed beyond package.json and the one nav entry",
          "existing pages keep their behaviour", ", ".join(existing_touched))
    for f in ("apps/web/components/Sidebar.jsx", "apps/web/lib/menuVisibility.mjs"):
        added = [l for l in git("diff", "-U0", base, *after, "--", f).splitlines()
                 if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
        removed = [l for l in added if l.startswith("-")]
        check(not removed and len(added) <= 2 and any("/market" in l for l in added),
              f"{f} only gains the /market entry", "the nav change adds one entry and alters nothing else",
              f"{len(added)} lines changed")
    return files


# ═══════════════════════════════════════════════════════════════════════════
# 7. Lint and build
# ═══════════════════════════════════════════════════════════════════════════
def lint(files: list[str]) -> None:
    npx = shutil.which("npx")
    if not check(npx is not None, "npx is on PATH", "lint and build run through the repo's own npm scripts"):
        return
    out = pathlib.Path("/tmp/verify_mkt04a_eslint.json")
    proc = subprocess.run([npx, "eslint", "-f", "json", "-o", str(out), "."], cwd=WEB,
                          capture_output=True, text=True, timeout=1200)
    try:
        report = json.loads(out.read_text())
    except (OSError, json.JSONDecodeError):
        check(False, "ESLint produced a report", "lint must actually run", (proc.stdout + proc.stderr)[-600:])
        return
    ours = {f for f in files if f.startswith("apps/web/") and f.endswith((".js", ".jsx", ".mjs"))}
    linted = {str(pathlib.Path(r["filePath"]).resolve().relative_to(REPO)) for r in report}
    check(ours <= linted, "ESLint covered every JS file this sprint added or changed",
          "an unlinted file proves nothing", ", ".join(sorted(ours - linted)))
    ours_msgs, others = [], {}
    for r in report:
        p = str(pathlib.Path(r["filePath"]).resolve().relative_to(REPO))
        for m in r["messages"]:
            if p in ours and (m["severity"] == 2 or p.startswith(("apps/web/lib/market", "apps/web/components/market",
                                                                   "apps/web/app/market", "apps/web/app/api/market",
                                                                   "apps/web/tests/market"))):
                ours_msgs.append(f"{p}:{m.get('line')} {m.get('ruleId')}")
            elif m["severity"] == 2:
                others[p] = others.get(p, 0) + 1
    check(not ours_msgs, "ESLint reports no error in any file this sprint touched (and no warning in new files)",
          "new code meets the repo's lint rules", "; ".join(ours_msgs[:15]))
    if others:
        find(f"`npm run lint` exits non-zero on the base branch: {sum(others.values())} pre-existing errors in "
             f"{len(others)} files this sprint did not touch (mostly react-hooks/set-state-in-effect)",
             "not fixed here: the sprint forbids changing existing pages")
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
          "only `next build` catches a server-only import dragged into a client bundle (dashboardhostfix)",
          "" if proc.returncode == 0 else out[-900:].replace("\n", " "))
    if proc.returncode == 0:
        check(bool(re.search(r"[○ƒ●◐]\s+/market\b", out)) and "/api/market/catalog" in out,
              "the build lists /market and the /api/market routes", "the new routes are really part of the app")


def main() -> int:
    pkg = environment()
    node_tests()
    routes_static()
    no_hardcoding()
    page_and_nav()
    files = diff_scope(pkg)
    lint(files)
    build()
    print(f"TOTAL: {_n_pass} passed, {_n_fail} failed", flush=True)
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
