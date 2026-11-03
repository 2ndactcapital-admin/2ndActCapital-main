"""verify_vhk2 — verify housekeeping round 2 (VHK2): no regression-set check is
frozen against LIVE state a later sprint may extend.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -p hollisworks -c prd -- apps/api/venv/bin/python -u apps/api/scripts/verify_vhk2.py \
        2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^\\[SKIP\\]|^TOTAL"

Pass/fail only, no prompts, no network, no npm. Prints 'TOTAL: N PASS, M FAIL'
and exits non-zero on any failure.

WHAT IT PROVES
  (1) STATIC, over every script in scripts/regression_set.txt: no check
      compares against a frozen count or list of live rows, pages, fields,
      scripts or callers (the VHK2 Task 1f patterns, FROZEN_PATTERNS below).
      Every remaining hit is a listed invariant in docs/VHK2_FINDINGS.md
      (section "Invariants kept") with why it is one.
  (2) the ten scripts that failed on 2026-11-02, FULL, one at a time, as
      synchronous subprocesses with VERIFY_NESTED=1 and a 1800 s timeout →
      each exits 0 with 0 FAIL. Before verify_bulkfetchui: the laptop bulk
      worker check run_regressions.sh does (a live worker → that script is
      not run and this is a FAIL naming why).
  (3) every Task 2 code change proven both ways (see y_task2).
  (4) verify_dealroom1 --cleanup-only → exit 0, LEFTOVERS: 0.

With VERIFY_NESTED=1, (2)–(4)'s subprocesses are skipped with one
'[SKIP] nested run' line. --self-checks-only runs the static scan, the docs
checks and the scope check (stdlib + git only). --cleanup-only: VHK2 seeds no
fixtures of its own; it runs verify_dealroom1 --cleanup-only and reports its
LEFTOVERS.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

import _verify_cleanup as VC  # noqa: E402,F401  (every regression-set script imports it; VHK2 has no DB fixtures)
import _verify_self as VS  # noqa: E402

SPRINT_KEY = "vhk2"
BEFORE_REF = "265f15c"   # vhk2-work base: main @ c6f7077 "merge rep10" + "chore: refresh schema snapshot"
SET_FILE = REPO / "scripts" / "regression_set.txt"
DOC = REPO / "docs" / "VHK2_FINDINGS.md"
RULES = REPO / "docs" / "VERIFY_RULES.md"
SUB_TIMEOUT = 1800
#: the scripts with failures on 2026-11-02 (bfdfe16), run here in full, one at a time
PREVIOUSLY_FAILING = ["verify_authz1.py", "verify_navregroup.py", "verify_dealroom1.py", "verify_bulkfetchui.py",
                      "verify_bulkfetchui3.py", "verify_edgarpipelinea.py", "verify_rep1.py", "verify_rep5.py",
                      "verify_lotmethod.py", "verify_marketdata1.py"]

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    global _n_pass, _n_fail
    ok = bool(passed)
    if ok:
        _n_pass += 1
        print(f"[PASS] {label}", flush=True)
    else:
        _n_fail += 1
        print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f" — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def run_cmd(args, cwd, timeout, env=None):
    """Foreground subprocess; never backgrounded, never interactive."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, out, f"timeout after {timeout}s"


def regression_set() -> list[str]:
    names = []
    for raw in SET_FILE.read_text(encoding="utf-8").splitlines():
        name = raw.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return names


def total_of(out: str):
    m = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    return (int(m[-1][0]), int(m[-1][1])) if m else None


def tail(text: str, n: int = 600) -> str:
    return text[-n:].replace("\n", " | ")


# ── (1) static scan ───────────────────────────────────────────────────────────

#: names that are HTTP / exit codes, never a count of live state
CODEISH = re.compile(r"^(?:s|code\w*|c\w{0,3}|status\w*|rc|http\w*|\w*_code|returncode|exit\w*|n?code\d*|resp\w*)$")
LIVE_WORDS = re.compile(r"real|existing|live|listed|regression|registry|callers|pages|hrefs|groups|perms|permissions|"
                        r"identity|fields|crit", re.I)
#: the VHK2 Task 1f patterns of a frozen count / list of live rows, pages, fields, scripts or callers
FROZEN_PATTERNS = {
    "len-eq": re.compile(r"\blen\((?!R\.new|new\b|mine\b|rep1\b|tax\b|models\b)[^()]*\)\s*==\s*[1-9]\d+"),
    "set-literal-eq": re.compile(r"==\s*\{\s*\"[a-z_]+\"\s*,\s*\"[a-z_]+\""),
    "list-literal-eq": re.compile(r"==\s*\[\s*\"(?:verify_|/|[a-z_]+\.jsx)"),
    "startswith-all": re.compile(r"all\(\w+\.startswith\(\"[A-Z]"),
    "live-list-eq": re.compile(r"\b(?:listed|names|callers|caller_names|pages|hrefs|groups)\s*==\s*[A-Za-z_]\w*\b"),
    "live-len-floor": re.compile(r"\blen\((?:groups|listed|names|callers|caller_names|pages|hrefs)\)\s*(?:==|>=)\s*\d"),
    "no-other": re.compile(r"no other role|== \{\"org_admin\"\}"),
}
#: (file, a substring of the line) for every remaining hit that is a TRUE invariant — each is listed, with why, in
#: docs/VHK2_FINDINGS.md "Invariants kept" (checked in y_doc)
INVARIANTS = [
    ("verify_navregroup.py", 'set(perm_ids) == {"view_deals", "manage_deals"}'),
    ("verify_dealroom2.py", "pages[3][0] == 404"),
    ("verify_bulkfetch.py", "len(stamps) == 36"),
    ("verify_edgarpipelinea.py", 'all(n.startswith("Edgar") for n in at_end)'),
    ("verify_edgarpipelinea.py", 'len(res["rows"]) == 50'),
    ("verify_rep2.py", 'set(acct) == {"id", "name", "raw"}'),
    ("verify_rep5.py", 'codes == {"buy", "sell"}'),
    ("verify_rep6.py", 'codes == {"buy", "sell"}'),
    ("verify_rep7.py", 'codes == {"buy", "sell"}'),
    ("verify_rep7b.py", 'codes == {"buy", "sell"}'),
    ("verify_rep9.py", 'codes == {"buy", "sell"}'),
    ("verify_rep7b.py", "len(results) == 4"),
    ("verify_rep8.py", 'set(roles) == {"underlying"'),
    ("verify_marketdata1.py", 'set(rej) == {"unresolved"'),
    ("verify_vhk1.py", "len(names) >= 25"),
    ("_apply_rep1.py", 'perms == {"view_replication"'),
    ("_apply_rep1.py", 'crit == {"expense_ratio_gross_pct"'),
    ("_apply_rep1.py", "granted to org_admin and no other role"),
    ("_apply_rep2.py", 'perms == {"view_execution"'),
    ("_apply_rep2.py", "granted to org_admin and no other role"),
    ("_apply_rep3.py", "no other role"),
]
#: the frozen forms the 2026-11-02 run failed on, as they stood at BEFORE_REF: the scan must flag each there (it is
#: not vacuous) and none may remain today
OLD_FROZEN = [
    ("verify_bulkfetchui3.py", "live-list-eq"),
    ("verify_edgarpipelinea.py", "startswith-all"),
    ("verify_edgarpipelinea.py", "live-len-floor"),
    ("verify_rep2.py", "len-eq"),
]
#: each re-based check: (file, frozen form that must be GONE, rule markers that must be PRESENT, label)
REBASED = [
    ("verify_authz1.py", ['"org_admin": {"manage_org_settings", "review_agent_proposals"}',
                          '== r["effective_after"]', 'p["additions_exact"]'],
     ["load_principal", "ORG_ADMIN_DEFAULT_PERMISSIONS", "app_importers_without_flag", 'p["additions_ok"]',
      "the recompute from current role grants"],
     "authz1: persona permissions from the DB, effective perms == recompute, flag rule over app loaders"),
    ("verify_navregroup.py", [], ["NON_NAV_REASONS", "harness_at(", "additions_ok", "y_live_roles",
                                  "self_pages_have_1a_rows"],
     "navregroup: page rule, additions at its own merge + additions_ok today, live roles"),
    ("menuvisibility_harness.mjs", [], ["additions_ok", "serverAdmits", "isTraced"],
     "menu-visibility harness: traced gates (untraced fails closed), additions_ok"),
    ("verify_dealroom1.py", ["signed-URL downloads allowed exactly there"], ["dealroom.activity_teardown", "X-Watermark-Id"],
     "dealroom1: downloads served as the room's settings say; teardown passes dealroom3's activity GUC"),
    ("verify_bulkfetchui.py", [], ["PREVIEW_LIMIT_SECONDS = 2.0", "load_per_cpu", "getloadavg"],
     "bulkfetchui: 2 s limit unchanged; retry under high load, load reported"),
    ("verify_bulkfetchui3.py", ['check(listed == seed_list'], ["sprint_end_ref", "missing_seed"],
     "bulkfetchui3: seeding at its own end point; today every seed listed, every name a real script"),
    ("verify_edgarpipelinea.py", ["identity_rows == 200"], ["at_end", "SAMPLE_GROUPS", "when_empty"],
     "edgarpipelinea: callers at its own commit, sample groups on what exists, pre-existing documents as a rule"),
    ("verify_rep2.py", ["len(alt) == 13"], ["CAPABILITY_KEYS"], "rep2: Altruist cell for every capability key"),
    ("_apply_rep1.py", ['FROM portfolio.replica_terms_field_registry WHERE is_critical")'], ["instrument_classes"], "_apply_rep1 --verify: rep1's all-class critical fields"),
    ("_apply_marketdata1.py", [], ["md1_types", "not_connected"],
     "_apply_marketdata1 --verify: no vendor-slot rank on its own types; never first and not connected elsewhere"),
]


def frozen_int(ln: str) -> bool:
    for m in re.finditer(r"([\w\.\[\]\"']+)\s*==\s*(\d+)\b", ln):
        left, n = m.group(1), int(m.group(2))
        if n < 10 or CODEISH.match(left.split(".")[-1].split("[")[0]):
            continue
        if 100 <= n <= 599 and re.search(r"code|status", left):
            continue
        if LIVE_WORDS.search(ln):
            return True
    return False


def scan_text(name: str, text: str) -> list[tuple[str, int, str, str]]:
    """[(file, line, pattern, text)] — every frozen-state pattern hit outside comment lines."""
    hits = []
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.lstrip().startswith("#"):
            continue
        if frozen_int(ln):
            hits.append((name, i, "int-eq-live", ln.strip()))
        for k, pat in FROZEN_PATTERNS.items():
            if pat.search(ln):
                hits.append((name, i, k, ln.strip()))
    return hits


def scanned_files(names: list[str]) -> list[pathlib.Path]:
    """Every regression-set script (except this one, whose own pattern table would match itself) + the _apply_*.py
    whose --verify the verifies call."""
    return [HERE / n for n in names if n != pathlib.Path(__file__).name] + sorted(HERE.glob("_apply_*.py"))


def is_invariant(hit) -> bool:
    return any(hit[0] == f and sub in hit[3] for f, sub in INVARIANTS)


def y_static(names: list[str], check, find) -> None:
    files = scanned_files(names)
    missing = [f.name for f in files if not f.exists()]
    check(not missing, "every regression-set script exists", f"{missing}")
    hits = [h for f in files if f.exists() for h in scan_text(f.name, f.read_text(encoding="utf-8"))]
    bad = [h for h in hits if not is_invariant(h)]
    check(not bad, f"no frozen count / list of live state remains in the {len(files)} scanned scripts, except the "
          f"{len(INVARIANTS)} listed invariants ({len(hits)} invariant hit(s))",
          "; ".join(f"{f}:{i} [{k}] {t[:120]}" for f, i, k, t in bad[:8]))
    unused = [(f, sub) for f, sub in INVARIANTS if not any(h[0] == f and sub in h[3] for h in hits)]
    if unused:
        find("listed invariants no longer present (a later edit removed the line — prune the list)", f"{unused}")
    # non-vacuous: the same scan flags each 2026-11-02 frozen form at BEFORE_REF, and none of them today
    for fname, kind in OLD_FROZEN:
        rel = (HERE / fname).relative_to(REPO).as_posix()
        old = VS.git_show(BEFORE_REF, rel) or ""
        then = [h for h in scan_text(fname, old) if h[2] == kind and not is_invariant(h)]
        now = [h for h in hits if h[0] == fname and h[2] == kind and not is_invariant(h)]
        check(bool(then) and not now, f"the scan flags {fname}'s frozen [{kind}] at {BEFORE_REF} and not today",
              f"{fname}: then {[(h[1], h[3][:80]) for h in then][:2]}; now {[(h[1], h[3][:80]) for h in now][:2]}")
    for fname, gone, present, label in REBASED:
        text = (HERE / fname).read_text(encoding="utf-8") if (HERE / fname).exists() else ""
        still = [g for g in gone if g in text]
        absent = [p for p in present if p not in text]
        check(bool(text) and not still and not absent, f"re-based: {label}",
              f"{fname}: frozen form still present {still}; rule markers missing {absent}")


def y_doc(names: list[str], check, find) -> None:
    text = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    heads = [h for h in ("1a", "1b", "1c", "1d", "1e", "1f") if re.search(rf"^#+ .*\b{h}\b", text, re.M)]
    check(len(heads) == 6, "docs/VHK2_FINDINGS.md carries Task 1 sections 1a–1f", f"{heads}")
    for name in PREVIOUSLY_FAILING + ["verify_vhk1.py"]:
        row = [ln for ln in text.splitlines() if name in ln and re.search(r"\b(STALE|REAL)\b", ln)]
        check(bool(row), f"VHK2_FINDINGS classes {name}'s 2026-11-02 failures stale vs real", f"{name}")
    inv = text.split("## Invariants kept", 1)[1] if "## Invariants kept" in text else ""
    missing = sorted({f for f, _ in INVARIANTS if f not in inv})
    check(bool(inv) and not missing, "every listed invariant's script is named, with why, under 'Invariants kept'",
          f"{missing}")
    rules = RULES.read_text(encoding="utf-8") if RULES.exists() else ""
    check("never freeze a count or list of live rows, pages, fields, scripts or callers" in rules.lower(),
          "docs/VERIFY_RULES.md states the VHK2 rule", "")
    check("verify_vhk2.py" in names, "scripts/regression_set.txt lists verify_vhk2.py", "")
    end, _ = VS.sprint_end_ref(SPRINT_KEY)
    status = VS.git_show(end, "docs/PROJECT_STATUS.md") or ""
    check("VHK2" in status, "docs/PROJECT_STATUS.md records VHK2", end)


# ── scope: Task 2 found no real defect, so VHK2 changed no application code ───

#: VHK2's own diff may touch only verify scripts and their helpers / harnesses, the nav unit test, docs, sprint
#: prompts, the regression list, and the operator-removed "Claude outputs/" prompt copies (deletions)
ALLOWED = (re.compile(r"^apps/api/scripts/(verify_\w+|_verify_(cleanup|self)|_apply_(rep1|marketdata1))\.py$"),
           re.compile(r"^apps/api/scripts/(menuvisibility_harness|navregroup_traced_gates|navregroup_today)\.mjs$"),
           re.compile(r"^apps/web/tests/nav/[\w.-]+\.mjs$"), re.compile(r"^docs/"), re.compile(r"^sprint_prompts/"),
           re.compile(r"^scripts/regression_set\.txt$"), re.compile(r"^Claude outputs/"))


def self_checks(check, find):
    """Stdlib + git only: the static scan, the docs, and VHK2's own scope (BEFORE_REF → its own end)."""
    names = regression_set()
    y_static(names, check, find)
    y_doc(names, check, find)
    code, out, err = VS.own_diff(BEFORE_REF, SPRINT_KEY, args=("--name-status",))
    rows = [ln.split("\t") for ln in out.splitlines() if ln.strip()]
    bad = [r[-1] for r in rows if not any(p.match(r[-1]) for p in ALLOWED)]
    outputs_added = [r[-1] for r in rows if r[-1].startswith("Claude outputs/") and not r[0].startswith("D")]
    end, how = VS.sprint_end_ref(SPRINT_KEY)
    check(code == 0 and bool(rows) and not bad and not outputs_added,
          "Task 2: no real defect was proven, and VHK2 changed no application code, route record or schema (only "
          "verify scripts / harnesses / the nav unit test / docs / the regression list)",
          f"{BEFORE_REF} → {how}; {len(rows)} files; outside scope: {bad[:8]}; added outputs {outputs_added}")
    for doc in ("docs/AUTHZ1_ROUTES.json", "apps/web/lib/menuVisibility.mjs"):
        c, d, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, doc)
        check(c == 0 and not d.strip(), f"{doc} is byte-identical across VHK2 (no gate or nav visibility changed)",
              d[:300])
    y_range(rows, check)


#: aibudget1 merged into main BEFORE vhk2 (bd63420, vhk2's merge's first parent); these are its files, never VHK2's
AIBUDGET1_BEFORE_REF = "c6f7077"   # verify_aibudget1.py's BEFORE_REF
AIBUDGET1_FILES = ("apps/api/routers/ai_ensembles.py", "docs/AUTHZ1_ROUTES.json", "apps/web/lib/menuVisibility.mjs")


def y_range(rows, check) -> None:
    """VHK2 FIX 1: a sprint's own change set is its merge's SECOND parent against the merge base. Reproduce the
    2026-11-03 failure (the merge commit as the end point lists aibudget1's files as VHK2's), then show the rule."""
    own = {r[-1] for r in rows}
    merge = VS.sprint_merge(SPRINT_KEY)
    end, how = VS.sprint_end_ref(SPRINT_KEY)
    ok, why = VS.own_base_ok(BEFORE_REF, SPRINT_KEY)
    check(ok and end != "HEAD" and merge and end != merge, f"VHK2's end point is its branch tip, not its merge: {how}",
          f"{why}; end {end}; merge {merge}")
    if not merge:
        return
    c, old_out, _ = VS.git("diff", "--name-only", BEFORE_REF, merge)
    old_files = set(old_out.splitlines())
    check(c == 0 and set(AIBUDGET1_FILES) <= old_files,
          f"reproduced: {BEFORE_REF} → the merge commit {merge} lists aibudget1's {len(AIBUDGET1_FILES)} files as VHK2's",
          f"{sorted(set(AIBUDGET1_FILES) - old_files)} missing from the old range")
    check(not (set(AIBUDGET1_FILES) & own), "fixed: VHK2's own range (to its merge's second parent) lists none of "
          "aibudget1's files", f"{sorted(set(AIBUDGET1_FILES) & own)}")
    c3, three, _ = VS.git("diff", "--name-only", f"{merge}^1...{merge}^2")
    check(c3 == 0 and own <= set(three.splitlines()) and not (set(AIBUDGET1_FILES) & set(three.splitlines())),
          f"VHK2's range is inside `git diff {merge}^1...{merge}^2` (three-dot: merge base → second parent)",
          f"outside: {sorted(own - set(three.splitlines()))[:5]}")
    ab_own = {ln for ln in VS.own_diff(AIBUDGET1_BEFORE_REF, "aibudget1", args=("--name-only",))[1].splitlines()}
    check(set(AIBUDGET1_FILES) <= ab_own, "aibudget1's own range (c6f7077 → its branch tip) does list its three files",
          f"{sorted(set(AIBUDGET1_FILES) - ab_own)}")


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))


#: the same query run_regressions.sh's DEFAULT_WORKER_CHECK runs, in a subprocess of this interpreter
#: VHK2 FIX 1: the DSN is hydrated from Doppler over HTTPS and probed (``_db_connect.admin_dsn``), the way every other
#: verify gets it — never ``os.environ["DATABASE_URL"]`` as inherited, which run_sprint's Step 3 sources from the stale
#: apps/api/.env (2026-11-03: InvalidPasswordError, so verify_bulkfetchui never ran).
WORKER_CHECK = """
import asyncio, sys
sys.path.insert(0, sys.argv[1])
from _db_connect import admin_dsn, connect
async def main():
    dsn, how = await admin_dsn()
    if not dsn:
        raise SystemExit("no working DATABASE_URL from Doppler: " + how)
    c = await connect(dsn)
    try:
        async with c.transaction():
            await c.execute("SET LOCAL app.is_super_admin = 'true'")
            n = await c.fetchval("SELECT count(*) FROM portfolio.edgar_fetch_workers WHERE target = 'laptop' "
                                 "AND stopped_at IS NULL AND heartbeat_at >= now() - interval '120 seconds'")
    finally:
        await c.close()
    print(n)
asyncio.run(main())
"""


def live_laptop_workers() -> tuple[int | None, str]:
    code, out, err = run_cmd([sys.executable, "-c", WORKER_CHECK, str(HERE)], REPO, 120)
    last = (out.strip().splitlines() or [""])[-1].strip()
    return (int(last) if code == 0 and last.isdigit() else None), tail(out + err, 300)


def run_full(name: str) -> None:
    t0 = time.monotonic()
    code, out, err = run_cmd([sys.executable, "-u", str(HERE / name)], REPO, SUB_TIMEOUT,
                             env=os.environ | {"VERIFY_NESTED": "1"})
    tot = total_of(out)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    print(f"[INFO] {name}: exit {code}, {tot}, {time.monotonic() - t0:.0f} s")
    check(code == 0 and tot is not None and tot[1] == 0 and tot[0] > 0,
          f"{name} in full → 0 FAIL ({tot[0] if tot else '?'} PASS)",
          f"{name}: exit {code}; failing lines: {fails[:6]}; tail: {tail(out + err)}")


def y_full_runs() -> None:
    section("[Y] The ten previously failing scripts → 0 FAIL in full (one at a time, 1800 s each)")
    for name in PREVIOUSLY_FAILING:
        if name == "verify_bulkfetchui.py":
            n, detail = live_laptop_workers()
            if not check(n == 0, "before verify_bulkfetchui: no laptop bulk worker heartbeating in the last 120 s "
                         "(the run_regressions.sh check)", f"live={n}; {detail}"):
                continue
        try:
            run_full(name)
        except Exception as exc:  # noqa: BLE001
            check(False, f"{name} full run raised", f"{type(exc).__name__}: {exc}")


def dealroom1_cleanup() -> None:
    t0 = time.monotonic()
    code, out, err = run_cmd([sys.executable, "-u", str(HERE / "verify_dealroom1.py"), VS.CLEANUP_FLAG], REPO, 900,
                             env=os.environ | {"VERIFY_NESTED": "1"})
    left = re.findall(r"^LEFTOVERS: (\d+)", out, re.M)
    tot = total_of(out)
    print(f"[INFO] verify_dealroom1 --cleanup-only: exit {code}, {time.monotonic() - t0:.0f} s")
    check(code == 0 and bool(left) and int(left[-1]) == 0 and tot is not None and tot[1] == 0,
          "verify_dealroom1 --cleanup-only: exits 0, zero fixture rows remain (deal_room_activity included)",
          f"verify_dealroom1.py: exit {code}; LEFTOVERS {left[-1] if left else '?'}; {tail(out + err)}")


# ── (3) both ways: the nav / server-gate rule on identical hrefs ─────────────

NAV_PROBE = """
const S = process.argv[1];
const T = await import("file://" + S + "/apps/api/scripts/navregroup_traced_gates.mjs");
const M = await import("file://" + S + "/apps/web/lib/menuVisibility.mjs");
const vis = (me, h) => [...M.visibleMenuItems(me).map(i => i.href),
  ...M.visibleAdminGroups(me).flatMap(g => g.tabs.map(t => t.href))].includes(h);
const P = {
  org_admin: { account_role: "org_admin", status: "active", permissions: ["manage_org_settings"] },
  replication_only: { account_role: "member", status: "active", permissions: ["view_replication"] },
  none: { account_role: "member", status: "active", permissions: [] },
  super_admin: { account_role: "super_admin", status: "active", permissions: [] },
};
const out = {};
for (const h of JSON.parse(process.argv[2])) {
  out[h] = { traced: T.isTraced(h) };
  for (const [n, me] of Object.entries(P)) out[h][n] = { nav: vis(me, h), server: T.serverAdmits(me, h) };
}
console.log(JSON.stringify(out));
"""
#: href -> (persona shown AND admitted, persona hidden AND refused) — the identical href both ways
BOTH_WAYS = {
    "/admin/custodian-capabilities": ("org_admin", "replication_only"),
    "/admin/security-data/sources": ("org_admin", "none"),
    "/admin/security-data/quality": ("org_admin", "none"),
    "/portfolio/blocks": ("super_admin", "org_admin"),
    "/admin/security-data": ("super_admin", "org_admin"),
}
FAKE_HREF = "/admin/vhk2-does-not-exist"


def y_task2() -> None:
    section("[Y] Real defects fixed, both ways — none proven; the nav/server rule shown both ways on identical hrefs")
    hrefs = [*BOTH_WAYS, FAKE_HREF]
    code, out, err = run_cmd(["node", "--input-type=module", "-e", NAV_PROBE, str(REPO), json.dumps(hrefs)], REPO, 120)
    try:
        d = json.loads((out.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        d = {}
    if not check(code == 0 and bool(d), "the nav probe (menuVisibility.mjs + traced server gates) runs",
                 f"exit {code}; {tail(out + err)}"):
        return
    for h, (yes, no) in BOTH_WAYS.items():
        r = d.get(h, {})
        check(r.get("traced") is True and r.get(yes) == {"nav": True, "server": True}
              and r.get(no) == {"nav": False, "server": False},
              f"{h}: {yes} shown AND admitted by its traced server gate; {no} hidden AND refused",
              f"{r}")
    shown_refused = [(h, n) for h, r in d.items() if h != FAKE_HREF for n, v in r.items()
                     if isinstance(v, dict) and v["nav"] and not v["server"]]
    check(not shown_refused, "no persona is shown a probed href its server refuses", f"{shown_refused}")
    f = d.get(FAKE_HREF, {})
    check(f.get("traced") is False and (f.get("super_admin") or {}).get("server") is False,
          "an untraced href fails CLOSED (no gate found → refused, even for Super Admin)", f"{f}")


# ── main / --cleanup-only ─────────────────────────────────────────────────────

def cleanup_only() -> int:
    """VHK2 seeds no fixtures of its own: the one teardown it owns is verify_dealroom1's (Task 4)."""
    t0 = time.monotonic()
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
        left = 0
    else:
        code, out, err = run_cmd([sys.executable, "-u", str(HERE / "verify_dealroom1.py"), VS.CLEANUP_FLAG], REPO,
                                 900, env=os.environ | {"VERIFY_NESTED": "1"})
        m = re.findall(r"^LEFTOVERS: (\d+)", out, re.M)
        left = int(m[-1]) if m and code == 0 else -1
        check(left == 0, "verify_dealroom1 --cleanup-only: zero fixture rows remain", f"exit {code}; {tail(out + err)}")
    print(f"CLEANUP_SECONDS: {time.monotonic() - t0:.1f}")
    print(f"LEFTOVERS: {max(left, 0) if left >= 0 else 'unknown'}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


def main() -> int:
    if VS.CLEANUP_FLAG in sys.argv:
        return cleanup_only()
    section("[Y] Task 1 findings 1a–1f; no frozen count / list of live state remains (or is a listed invariant); "
            "no application code changed")
    try:
        self_checks(check, find)
    except Exception as exc:  # noqa: BLE001
        check(False, "static / docs / scope checks raised", f"{type(exc).__name__}: {exc}")
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        for fn in (y_task2, y_full_runs, lambda: (section("[Y] dealroom1 teardown — --cleanup-only → zero rows"),
                                                  dealroom1_cleanup())):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                check(False, f"{getattr(fn, '__name__', 'step')} raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
