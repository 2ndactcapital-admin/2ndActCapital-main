"""verify_mkt04c.py — key dates, My dates, saved views and the "What moves with
it" correlations panel on the market indicators page (mkt04c.structural).

WHAT WAS BUILT (this script proves it):
  * apps/web/lib/market/{keyDatesModel,customDates,viewsModel,correlationModel,
    viewContract}.mjs — pure logic (viewContract: the pinned contract codes).
  * apps/web/components/market/{KeyDatesBar,KeyDatesPanel,CorrelationsView,
    CorrelationsPanel,SavedViewsView,SavedViewsPanel}.jsx + marketClient.mjs.
  * Edits, only where the features need them: MarketIndicatorsView (lifted
    state, the Saved views card), ChartPanel (key-dates reload), ChartView (the
    two panels), MarketChart (band, personal lines, flag width, slots),
    chartModel (flag, band, lines, legend), chartRequest.interpretKeyDates
    (custom dates, envelope, limits).
  * apps/web/tests/market/{keyDatesModel,customDates,viewsModel,
    correlationModel,mkt04cRender}.test.mjs + mkt04cFixtures.mjs.
  * verify_mkt04b.py: its suite-presence check re-pinned to mkt04b's commit.

No database and no secrets: there is no backend change to prove live. Runs
the node:test suites, static scans, scope, lint, and the production build.

Output: every line starts with [PASS], [FAIL], [FIND] or [SKIP]; the last line
is `TOTAL: <passed> passed, <failed> failed`. Exit 1 on any [FAIL].

Run:  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04c.py
"""
from __future__ import annotations

import ast
import importlib.util
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


def rel(p: pathlib.Path) -> str:
    return str(p.relative_to(REPO))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=120).stdout


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# verify_mkt04a's scan helpers and token sources, reused so all three sprints
# apply the SAME rules (it defines functions and constants only on import).
V4A = _load("verify_mkt04a_helpers", HERE.parent / "verify_mkt04a.py")
strip_comments = V4A.strip_comments
string_literals = V4A.string_literals

MARKET_LIB = WEB / "lib" / "market"
COMPONENTS = WEB / "components" / "market"
TESTS = WEB / "tests" / "market"
PAGE = WEB / "app" / "market" / "page.js"
DEFAULTS = MARKET_LIB / "marketDefaults.mjs"
CONTRACT = MARKET_LIB / "chartContract.mjs"
VIEW_CONTRACT = MARKET_LIB / "viewContract.mjs"
ROUTES_CORE = MARKET_LIB / "marketRoutes.mjs"
SVC = API / "services" / "market_data"

NEW_LIB = ["keyDatesModel", "customDates", "viewsModel", "correlationModel", "viewContract"]
NEW_COMPONENTS = ["KeyDatesBar", "KeyDatesPanel", "CorrelationsView", "CorrelationsPanel", "SavedViewsView", "SavedViewsPanel"]
NEW_CLIENT = COMPONENTS / "marketClient.mjs"
EDITED_WEB = {
    "apps/web/components/market/MarketIndicatorsView.jsx",
    "apps/web/components/market/ChartPanel.jsx",
    "apps/web/components/market/ChartView.jsx",
    "apps/web/components/market/MarketChart.jsx",
    "apps/web/lib/market/chartModel.mjs",
    "apps/web/lib/market/chartRequest.mjs",
}
EXPECTED_VIEW_CONTRACT = {
    "CONFIG_VERSION": "1",
    "ANCHOR_DATE": '"date"',
    "ANCHOR_RELATIVE": '"relative"',
    "PRECISION_MONTH": '"month"',
}
SELECTED_BAND = "rgba(43,95,158,0.16)"


def new_files() -> list[pathlib.Path]:
    return ([MARKET_LIB / f"{n}.mjs" for n in NEW_LIB] + [COMPONENTS / f"{n}.jsx" for n in NEW_COMPONENTS]
            + [NEW_CLIENT])


def node_eval(js: str) -> str:
    node = shutil.which("node")
    if not node:
        return ""
    return subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=120).stdout


# ═══════════════════════════════════════════════════════════════════════════
# 1. node:test suites
# ═══════════════════════════════════════════════════════════════════════════
TEST_WHY = {
    "keyDatesModel.test.mjs": "dropdown labels (day, month precision, ranges) in the server's order, the period a date "
                              "lands on (clamped), Start/End, the flag text and the band are known answers",
    "customDates.test.mjs": "Save needs only a name and a date; the server's 422/409 message is shown once with the "
                            "inputs kept; nothing optimistic; delete is two steps; can_write must be exactly true",
    "viewsModel.test.mjs": "the config is exactly the canonical schema; build-then-apply round-trips; relative years on "
                           "a leap day; unavailable keys skipped without touching the view; non-chart measures noted",
    "correlationModel.test.mjs": "the request excludes the focus and respects the lag and key limits; one request per "
                                 "burst; stale responses discarded; fail closed; r never parsed",
    "mkt04cRender.test.mjs": "the real components: both dropdowns, Start/End, the band with overlays off, two-step "
                             "delete, the add form on 422/409, no write control without can_write, presets "
                             "read-only, the correlations card's states",
}
# Earlier sprints' suites, run here as a regression (their own verifies count them).
EARLIER = ["rebase.test.mjs", "chartGeometry.test.mjs", "chartModel.test.mjs", "chartRequest.test.mjs",
           "chartRender.test.mjs", "render.test.mjs", "gridRequest.test.mjs", "selection.test.mjs",
           "marketRoutes.test.mjs"]


def node_tests() -> None:
    node = shutil.which("node")
    if not check(node is not None, "node is on PATH", "the UI proofs run as node:test suites"):
        return
    present = sorted(p.name for p in TESTS.glob("*.test.mjs"))
    check(sorted([*TEST_WHY, *EARLIER]) == present, "every market suite on disk is run here (mkt04c's per test, "
          "earlier sprints' as a regression)", "an unrun suite proves nothing", f"on disk={present}")
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
    for name in EARLIER:
        path = TESTS / name
        proc = subprocess.run([node, "--test", "--test-reporter=tap", str(path.relative_to(WEB))],
                              cwd=WEB, capture_output=True, text=True, timeout=600) if path.is_file() else None
        fails = len(re.findall(r"^not ok \d+ - ", proc.stdout, flags=re.M)) if proc else -1
        oks = len(re.findall(r"^ok \d+ - ", proc.stdout, flags=re.M)) if proc else 0
        check(proc is not None and proc.returncode == 0 and fails == 0 and oks > 0,
              f"regression: the earlier suite {name} still passes in full",
              "mkt04c's edits to the chart, the page and interpretKeyDates must not weaken an existing proof",
              f"{oks} ok, {fails} not ok")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Static scans
# ═══════════════════════════════════════════════════════════════════════════
def ui_files() -> list[pathlib.Path]:
    files = [PAGE, *sorted(COMPONENTS.glob("*")), *sorted(MARKET_LIB.glob("*"))]
    return [f for f in files if f.is_file() and f not in (DEFAULTS, CONTRACT, VIEW_CONTRACT, ROUTES_CORE)]


def _py_dict_values(path: pathlib.Path, name: str) -> set[str]:
    for node in ast.parse(read(path)).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, ast.Dict):
                return {v.value for v in node.value.values if isinstance(v, ast.Constant) and isinstance(v.value, str)}
    return set()


def _py_str_consts(path: pathlib.Path, prefix: str) -> set[str]:
    out = set()
    for node in ast.parse(read(path)).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            if any(isinstance(t, ast.Name) and t.id.startswith(prefix) for t in node.targets):
                out.add(node.value.value)
    return out


def server_texts() -> tuple[set[str], set[str]]:
    """Labels and messages the SERVER sends for these features, read from the backend source."""
    labels = set()
    for path, names in ((SVC / "key_dates.py", ["KIND_LABELS", "REGIME_TYPE_LABELS"]),
                        (SVC / "saved_views.py", ["SCALE_LABELS", "ANCHOR_TYPE_LABELS", "SELECTION_KIND_LABELS"]),
                        (SVC / "read_service.py", ["MODE_LABELS", "FREQUENCY_LABELS", "TRANSFORM_LABELS"])):
        for n in names:
            labels |= _py_dict_values(path, n)
    messages = set()
    for path in (SVC / "key_dates.py", SVC / "saved_views.py"):
        messages |= _py_str_consts(path, "MSG_")
    messages |= _py_str_consts(SVC / "personal.py", "MSG_") | _py_str_consts(SVC / "personal.py", "NOT_FOUND")
    messages.add("That date is outside the available data")  # key_dates.outside_message's fixed prefix
    return labels, messages


def static_scans() -> None:
    for f in new_files():
        check(f.is_file(), f"{rel(f)} exists", "Task 2-5's modules and components")

    files = ui_files()
    newf = [f for f in new_files() if f.is_file()]
    check(len(files) >= 34 and all(f in files or f == VIEW_CONTRACT for f in newf),
          "the scan covers the page, every market component and pure module, including every new mkt04c file",
          "a scan over too few files proves nothing", f"{len(files)} files")

    # Rule 1 — mkt04a's vocabulary/category/palette/hex/limit rules over EVERY market UI file.
    categories, palette_hex, vocab = V4A.vocabulary_tokens()
    check(len(categories) >= 5 and len(vocab) >= 15 and len(palette_hex) >= 5,
          "the scan's vocabulary was read from the backend source, not typed here",
          "a hand-typed list drifts from what the server really sends")
    api_limits = {"40", "2000", "400", "30000", "300000"}
    personal_limits = {"50", "60", "80", "20000", "24", "120"}  # views/dates max, name max, config bytes, lag, min_periods
    hits = {"category": [], "vocab": [], "hex": [], "limit": [], "label": [], "message": []}
    labels, messages = server_texts()
    check(len(labels) >= 15 and len(messages) >= 12, "the server's labels and messages for these features were read "
          "from key_dates.py, saved_views.py, personal.py and read_service.py",
          "the no-hardcoding scan must compare against what the server really sends",
          f"{len(labels)} labels, {len(messages)} messages")
    for f in files:
        src = strip_comments(read(f))
        lits = string_literals(src)
        is_new = f in newf
        for lit in lits:
            if lit in categories:
                hits["category"].append(f"{rel(f)}: {lit!r}")
            if lit in vocab:
                hits["vocab"].append(f"{rel(f)}: {lit!r}")
            if is_new and lit in labels:
                hits["label"].append(f"{rel(f)}: {lit!r}")
            if is_new and any(m in lit for m in messages):
                hits["message"].append(f"{rel(f)}: {lit[:60]!r}")
        for h in re.findall(r"#[0-9A-Fa-f]{6}\b", src):
            if h.upper() not in V4A.DESIGN_TOKENS:
                hits["hex"].append(f"{rel(f)}: {h}")
        for n in re.findall(r"(?<![\w\-\[.#])(\d+)(?![\w\].%])", src):
            if n in api_limits or (is_new and n in personal_limits):
                hits["limit"].append(f"{rel(f)}: {n}")
    check(not hits["category"], "no category name is a literal in the market UI", "categories come from the catalog",
          "; ".join(hits["category"]))
    check(not hits["vocab"], "no vocabulary value (mode, frequency, transform, reason) is a literal outside the three "
          "pinned contract files", "codes and option lists come from the server (Rule 1)", "; ".join(hits["vocab"]))
    check(not hits["label"], "no server label (key-date kinds, regime types, modes, scales, anchor types, frequencies) "
          "is typed in a new mkt04c file", "labels come from the server's vocabularies (Rule 1)", "; ".join(hits["label"]))
    check(not hits["message"], "no server message (empty name, duplicate, limit, out of range, not found…) is typed in "
          "a new mkt04c file", "the UI shows the server's message verbatim and never re-implements its rules",
          "; ".join(hits["message"]))
    check(not hits["hex"], "every hex colour in the market UI is a design token", "brand tokens only",
          "; ".join(hits["hex"]))
    check(not hits["limit"], "no API or personal-endpoint limit (40 keys, 50 views/dates, 60/80 name length, "
          "lag 24, min_periods 120…) is a literal", "limits come from the responses' limits/vocabularies",
          "; ".join(hits["limit"]))
    rgba = sorted({m for f in files for m in re.findall(r"rgba\([^)]*\)", strip_comments(read(f)))})
    check(rgba == [SELECTED_BAND], "the only rgba() colour in the market UI is the decided selected-period blue",
          "the light blue is a sprint decision, not a new palette", f"rgba={rgba}")

    vc = strip_comments(read(VIEW_CONTRACT))
    exports = dict(re.findall(r"export const (\w+) = ([^;]+);", vc))
    check(exports == EXPECTED_VIEW_CONTRACT and "import " not in vc and not re.search(r"label|Label", vc),
          "lib/market/viewContract.mjs (the scan's third exemption) holds only the config version, the two anchor "
          "shapes and the month precision", "the exemption must not become a place to hide labels",
          f"exports {sorted(exports)}")
    check(dict(re.findall(r"export const (\w+) = ([^;]+);", strip_comments(read(DEFAULTS)))) == V4A.EXPECTED_DEFAULTS,
          "lib/market/marketDefaults.mjs still holds only mkt04a's defaults", "the earlier exemption did not grow")

    # Rule 5 — client calls only /api/market/*.
    routes = node_eval(
        "Promise.all(['customDates','viewsModel','correlationModel','chartRequest'].map(n => import("
        f"'{MARKET_LIB.as_uri()}/' + n + '.mjs'))).then(([c, v, k, r]) => process.stdout.write(JSON.stringify("
        "[c.CUSTOM_DATES_ROUTE, v.VIEWS_ROUTE, k.CORRELATIONS_ROUTE, r.KEY_DATES_ROUTE, c.customDateUrl('x'), "
        "v.viewUrl('y')])))")
    try:
        got = json.loads(routes or "[]")
    except json.JSONDecodeError:
        got = []
    check(got == ["/api/market/key-dates/custom", "/api/market/views", "/api/market/correlations",
                  "/api/market/key-dates", "/api/market/key-dates/custom/x", "/api/market/views/y"],
          "every mkt04c route and URL builder targets the Next.js /api/market/* forwards",
          "client components never call FastAPI directly (Rule 5)", f"routes={got}")
    fetchers = sorted(f.name for f in COMPONENTS.glob("*") if re.search(r"\bfetch\(", strip_comments(read(f))))
    check(fetchers == ["ChartPanel.jsx", "GridPanel.jsx", "MarketIndicators.jsx", "marketClient.mjs"],
          "only the three earlier callers and the one new request helper call fetch",
          "every new request goes through requestJson with a URL built by a lib module", f"fetch in {fetchers}")
    client = strip_comments(read(NEW_CLIENT))
    check(re.findall(r"\bfetch\(([^,)]*)", client) == ["url"] and "export async function requestJson(url" in client,
          "marketClient.requestJson fetches only the URL it is given", "the URL always comes from a lib route builder")
    allowed_args = {"step.request.url", "req.url", "VIEWS_ROUTE", "url"}
    req_args = sorted({a.strip() for f in COMPONENTS.glob("*.jsx")
                       for a in re.findall(r"requestJson\(([^,)]*)", strip_comments(read(f)))})
    check(bool(req_args) and set(req_args) <= allowed_args,
          "every requestJson call passes a URL from a lib/market route constant or request builder",
          "no component assembles its own URL", f"args={req_args}")
    api_refs = re.compile(r"NEXT_PUBLIC_API_URL|/api/v1|localhost:8000|@/lib/api\b|@/lib/apiForward|marketForward|marketRoutes")
    check(not [rel(f) for f in newf if api_refs.search(strip_comments(read(f)))],
          "no new file references the FastAPI base URL or the server forward", "Rule 5")
    check(not [rel(f) for f in newf if re.search(r"\b(org_id|user_id|orgId|userId)\b", strip_comments(read(f)))],
          "no new file names org_id or user_id", "identity comes only from the session (Rule 6)")

    # Fail closed / can_write === true, no truthy fallback.
    cd = strip_comments(read(MARKET_LIB / "customDates.mjs"))
    vm = strip_comments(read(MARKET_LIB / "viewsModel.mjs"))
    cm = strip_comments(read(MARKET_LIB / "correlationModel.mjs"))
    allsrc = "".join(strip_comments(read(f)) for f in files)
    check("kd.permissions?.can_write === true" in cd and "state.permissions?.can_write === true" in vm
          and not re.search(r"can_write\s*(\|\||\?\?|!=|==(?!=))", allsrc) and not re.search(r"permissions\s*(\|\||\?\?)", allsrc),
          "write controls are gated on can_write === true exactly, with no truthy fallback anywhere",
          "a lost envelope must fail CLOSED (CLAUDE.md, Permission Envelope Pattern)")
    check("!grantsRead(body) || !Array.isArray(body.presets) || !Array.isArray(body.views)" in vm
          and "!grantsRead(body) || !Array.isArray(body.results)" in cm,
          "views and correlations responses are shown only with an envelope granting read",
          "every permission-gated response is checked, not only the catalog")
    for name in ("KeyDatesBar.jsx", "SavedViewsView.jsx"):
        src = strip_comments(read(COMPONENTS / name))
        guards = len(re.findall(r"\{writable && ", src))
        check(guards >= 3 and "const writable = " in src,
              f"{name}: every write control sits inside a `writable &&` branch", "the render tests prove it with real "
              "envelopes; this pins the shape", f"{guards} guarded blocks")

    # No parsing: r strings never; series strings only in rebase/scales.
    corr_src = cm + "".join(strip_comments(read(COMPONENTS / f"{n}.jsx")) for n in ("CorrelationsView", "CorrelationsPanel"))
    bad = re.findall(r"\b(Number\(|parseFloat\(|parseInt\(|Number\.parse\w+\(|toFixed\(|toPrecision\()|\+\s*row\.r\b|\br\s*\*\s*1\b", corr_src)
    check(not bad, "correlationModel and the correlations components never convert an r string to a number",
          "r is displayed exactly as returned; the bar width is read from the text (rBarWidth)", ",".join(bad))
    check("export function rBarWidth(r)" in cm and cm.count("rBarWidth(") >= 2,
          "the |r| bar width comes from one display helper, rBarWidth", "a single place turns the r text into a width")
    parse_re = re.compile(r"\b(Number\(|parseFloat\(|parseInt\()")
    parsers = sorted(f.name for f in [*MARKET_LIB.glob("*.mjs"), *COMPONENTS.glob("*")] if parse_re.search(strip_comments(read(f))))
    check(set(parsers) <= {"rebase.mjs", "scales.mjs", "gridRequest.mjs"} and {"rebase.mjs", "scales.mjs"} <= set(parsers),
          "string-to-number parsing in the market UI exists only in rebase.mjs and scales.mjs (plus mkt04a's date "
          "parts in gridRequest.mjs)", "the mkt04b rule: series strings are parsed only in the two pinned modules",
          f"parsing in {parsers}")
    if "gridRequest.mjs" in parsers:
        find("lib/market/gridRequest.mjs still calls Number() on ISO date PARTS (yearsAgo), never on a series value; "
             "mkt04a's, unchanged here, and recorded the same way by verify_mkt04b")

    impure = []
    for f in [MARKET_LIB / f"{n}.mjs" for n in NEW_LIB]:
        impure += [f"{rel(f)}: {x}" for x in re.findall(
            r"from [\"'](?:react|react-dom|next/[\w/]+)[\"']|\bdocument\.|\bwindow\.|\bfetch\(|\bsetTimeout\(", strip_comments(read(f)))]
    check(not impure, "the new lib modules are pure: no React, Next, DOM, fetch or real timers",
          "pure logic is testable with Node's runner alone, and components stay thin", "; ".join(impure))
    hookless = [n for n in ("KeyDatesBar", "SavedViewsView", "CorrelationsView") if re.search(r"\buse[A-Z]\w*\(", read(COMPONENTS / f"{n}.jsx"))]
    check(not hookless, "the three view components use no hooks", "tests call them directly and fire their handlers",
          ", ".join(hookless))

    # The mkt04b pins this sprint's edits must not break.
    chart = strip_comments(read(COMPONENTS / "MarketChart.jsx"))
    panel = strip_comments(read(COMPONENTS / "ChartPanel.jsx"))
    check(chart.count("onAnchorChange(") == 1 and re.findall(r"fetchJson\(([^)]*)\)", panel) == ["url", "KEY_DATES_ROUTE"],
          "MarketChart still moves the anchor only in its frame callback, and ChartPanel still reads key-dates "
          "through its one fetchJson(KEY_DATES_ROUTE)", "verify_mkt04b pins both; a reload reuses the same call")
    check(f'fill={{SELECTED_BAND_FILL}}' in chart and 'data-chart="selected-band"' in chart
          and "{model.selectedBand && (" in chart and "overlays" not in chart.split("{model.selectedBand && (")[1].split("data-chart=")[0],
          "the selected-period band draws on model.selectedBand alone, not behind an overlay toggle",
          "a selected range is shaded even with 'Regimes & events' off (sprint decision)")

    design_bad = []
    for f in newf:
        src = strip_comments(read(f))
        for pat, name in ((r"\bdark:", "dark:"), (r"gradient", "gradient"), (r"shadow", "shadow"),
                          ("[\U0001F300-\U0001FAFF☀-➿]", "emoji")):
            if re.search(pat, src, flags=re.I):
                design_bad.append(f"{rel(f)}: {name}")
    check(not design_bad, "no dark mode, gradient, shadow or emoji in the new files", "light theme only, quiet brand",
          "; ".join(design_bad))


# ═══════════════════════════════════════════════════════════════════════════
# 3. Scope
# ═══════════════════════════════════════════════════════════════════════════
SPRINT_MARKER = "apps/api/scripts/verify_mkt04c.py"
ALLOWED_PREFIXES = ("apps/web/", "docs/", "sprint_prompts/")
ALLOWED_FILES = {SPRINT_MARKER, "apps/api/scripts/verify_mkt04b.py"}
UNTOUCHED = [
    "apps/web/components/market/GridPanel.jsx", "apps/web/components/market/MarketGridTable.jsx",
    "apps/web/components/market/SelectionPanel.jsx", "apps/web/components/market/AnchorSlider.jsx",
    "apps/web/components/market/TrendsTable.jsx", "apps/web/components/market/MarketIndicators.jsx",
    "apps/web/lib/market/gridView.mjs", "apps/web/lib/market/gridRequest.mjs", "apps/web/lib/market/selection.mjs",
    "apps/web/lib/market/catalogModel.mjs", "apps/web/lib/market/marketDefaults.mjs",
    "apps/web/lib/market/marketRoutes.mjs", "apps/web/lib/market/chartContract.mjs",
    "apps/web/lib/market/rebase.mjs", "apps/web/lib/market/scales.mjs", "apps/web/lib/marketForward.js",
    "apps/web/app/market/page.js", "apps/web/package.json", "apps/web/package-lock.json", "CLAUDE.md",
    "apps/web/tests/market/fixtures.mjs", "apps/web/tests/market/jsxLoader.mjs",
    "apps/web/tests/market/golden_rebase.json", *[f"apps/web/tests/market/{n}" for n in EARLIER],
    "apps/api/scripts/verify_mkt04a.py", "apps/api/scripts/verify_mkt03.py",
]


def sprint_range() -> tuple[str, str | None]:
    """Located, not assumed: the commit that ADDED this script is the sprint's
    commit and its parent is the pre-sprint tree. Before that commit exists,
    HEAD plus the working tree and untracked files are the change."""
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


def _top_level(src: str) -> dict[str, str]:
    out = {}
    for n in ast.parse(src or "pass").body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = ast.dump(n)
        elif isinstance(n, ast.Assign):
            out["=" + ",".join(ast.unparse(t) for t in n.targets)] = ast.dump(n)
        elif isinstance(n, ast.AnnAssign):
            out["=" + ast.unparse(n.target)] = ast.dump(n)
    return out


def scope() -> list[str]:
    base, sha = sprint_range()
    find(f"pre-sprint reference: {base}" + (f", sprint commit {sha[:10]}" if sha else " (sprint not yet committed: "
         "working tree + untracked files)"))
    files = changed_files()
    check(bool(files), "the sprint's diff is readable and non-empty", "the scope check needs a real diff")
    outside = [f for f in files if f not in ALLOWED_FILES and not f.startswith(ALLOWED_PREFIXES)]
    check(not outside, "changes are confined to apps/web, verify_mkt04c.py (plus verify_mkt04b's re-pin), docs and "
          "the sprint files", "no backend, database or config change rides along", ", ".join(outside))
    backend = [f for f in files if f.startswith("apps/api/") and f not in ALLOWED_FILES]
    check(not backend, "no backend file changed (routers, services, migrations, other scripts)",
          "this sprint is front end only", ", ".join(backend))
    check(not [f for f in files if f.endswith(".sql") or "/migrations/" in f], "no DDL or migration file",
          "no database change")
    check("CLAUDE.md" not in files, "CLAUDE.md is unchanged", "task rule")
    touched = [f for f in UNTOUCHED if f in files]
    check(not touched, "the Grid tab, selection, routes and forward, contract and parsing modules, the page, "
          "package files, every earlier test suite and fixture, and verify_mkt04a/03 are unchanged",
          "edits to earlier files are allowed only where the features need them", ", ".join(touched))
    modified_web = [f for f in changed_files(modified_only=True) if f.startswith("apps/web/")]
    check(set(modified_web) <= EDITED_WEB, "the only existing apps/web files edited are the six the features need "
          "(page state, chart panel/view/drawing, chart model, interpretKeyDates)", "existing pages keep their behaviour",
          ", ".join(sorted(set(modified_web) - EDITED_WEB)))

    grid_diff = git("diff", base, *([sha] if sha else []), "--", "apps/web/components/market/MarketIndicatorsView.jsx")
    removed = [l for l in grid_diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    check(all(re.search(r"initialChartSettings|useState\(\(\) => initialChartSettings|patchChartSettings = useCallback|"
                        r"<SelectionPanel |^-\s*$|setChartSettings\(\(prev\) => \(\{ \.\.\.prev, \.\.\.patch \}\)\)", l)
              for l in removed),
          "MarketIndicatorsView: only the chart-settings initialiser, its patcher and the SelectionPanel line were "
          "replaced; the Grid wiring and tabs are untouched", "Do not change the Grid tab's behaviour",
          f"{len(removed)} removed lines")
    cr_old = _top_level_js(base_text("apps/web/lib/market/chartRequest.mjs"))
    cr_new = _top_level_js(read(MARKET_LIB / "chartRequest.mjs"))
    changed = sorted(k for k in set(cr_old) | set(cr_new) if cr_old.get(k) != cr_new.get(k))
    check(changed == ["interpretKeyDates"], "chartRequest.mjs: only interpretKeyDates changed (additive fields)",
          "the series request, loader and keyboard stepping keep mkt04b's behaviour", f"changed={changed}")

    if "apps/api/scripts/verify_mkt04b.py" in files:
        old4b = _top_level(base_text("apps/api/scripts/verify_mkt04b.py"))
        new4b = _top_level(read(HERE.parent / "verify_mkt04b.py"))
        diff = sorted(k for k in set(old4b) | set(new4b) if old4b.get(k) != new4b.get(k))
        check(diff == ["mkt04b_suites", "node_tests"],
              "verify_mkt04b.py: only its suite-presence check changed (node_tests + the new mkt04b_suites helper)",
              "every other mkt04b assertion keeps what it proves", f"changed={diff}")
        find("verify_mkt04b.py required every tests/market/*.test.mjs on disk to be in its own TEST_WHY, so ANY later "
             "sprint's suite failed it. Re-pinned minimally: the suites on disk that mkt04b's own commit contains "
             "(git ls-tree). A missing mkt04b suite still fails; its total stays 277")
    return files


def _top_level_js(src: str) -> dict[str, str]:
    """Top-level `export function NAME` / `export const NAME` blocks of a JS module, by name."""
    out: dict[str, str] = {}
    parts = re.split(r"(?m)^(?=export (?:async )?(?:function|const) )", src)
    for p in parts:
        m = re.match(r"export (?:async )?(?:function|const) (\w+)", p)
        if m:
            body = re.sub(r"(?m)^\s*(//.*|\*.*|/\*\*.*)$", "", p).strip()
            out[m.group(1)] = body
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 4. Lint and build
# ═══════════════════════════════════════════════════════════════════════════
def lint(files: list[str]) -> None:
    npx = shutil.which("npx")
    if not check(npx is not None, "npx is on PATH", "lint runs through the repo's own ESLint config"):
        return
    ours = sorted(f for f in files if f.startswith("apps/web/") and f.endswith((".js", ".jsx", ".mjs")) and (REPO / f).is_file())
    if not check(bool(ours), "the sprint touched JS files to lint", "lint must have something to check"):
        return
    out = pathlib.Path("/tmp/verify_mkt04c_eslint.json")
    proc = subprocess.run([npx, "eslint", "-f", "json", "-o", str(out), *[str(REPO / f) for f in ours]], cwd=WEB,
                          capture_output=True, text=True, timeout=1200)
    try:
        report = json.loads(out.read_text())
    except (OSError, json.JSONDecodeError):
        check(False, "ESLint produced a report", "lint must actually run", (proc.stdout + proc.stderr)[-600:])
        return
    linted = {str(pathlib.Path(r["filePath"]).resolve().relative_to(REPO)) for r in report}
    check(set(ours) <= linted, "ESLint covered every JS file this sprint added or changed", "an unlinted file proves nothing",
          ", ".join(sorted(set(ours) - linted)))
    msgs = [f"{pathlib.Path(r['filePath']).name}:{m.get('line')} {m.get('ruleId')}" for r in report for m in r["messages"]]
    check(not msgs and proc.returncode == 0, "ESLint reports no error and no warning in any file this sprint touched",
          "npm run lint clean on the sprint's files (react-hooks included)", "; ".join(msgs[:15]))


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
        need = ["/api/market/key-dates", "/api/market/key-dates/custom", "/api/market/views", "/api/market/correlations"]
        check(bool(re.search(r"[○ƒ●◐]\s+/market\b", out)) and all(r in out for r in need),
              "the build lists /market and every route the new panels call", "the panels are really part of the app",
              ", ".join(r for r in need if r not in out))


# ═══════════════════════════════════════════════════════════════════════════
def main() -> int:
    node_tests()
    static_scans()
    files = scope()
    lint(files)
    build()
    skip("live database proofs: none apply — mkt04c changes no backend code and writes no data")
    print(f"TOTAL: {_n_pass} passed, {_n_fail} failed", flush=True)
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
