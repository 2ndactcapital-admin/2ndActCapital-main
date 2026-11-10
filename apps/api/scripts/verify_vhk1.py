"""verify_vhk1 — verify housekeeping (VHK1): every regression-set verify is immune
to later sprints.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_vhk1.py 2>&1 | grep -E "^\\[FAIL\\]|^\\[FIND\\]|^TOTAL"

Pass/fail only, no prompts, no network, no npm. Prints 'TOTAL: N PASS, M FAIL'
and exits non-zero on any failure.

WHAT IT PROVES
  (1) STATIC, over every script in scripts/regression_set.txt (AST + text):
      no since-base comparison against the WORKING TREE remains (a one-ref
      `git diff`, a `git show main:` read, a base read of AUTHZ1_ROUTES.json
      beside a working-tree read, a git_show(...) == read_text() byte check,
      VC.b1_diff_bad(..., read_text())); no module-level TODAY / NOW / VAL /
      YEAR / YEAR_START; a --self-checks-only dispatch placed BEFORE
      hydrate() / `import main`; a --cleanup-only path; `import
      _verify_cleanup`. The helper's own rules (sprint_end_ref, when_empty)
      are unit-checked in-process.
  (2) every script's --self-checks-only, a synchronous subprocess (300 s each)
      → exit 0, 0 FAIL.
  (3) the later-sprint SIMULATION in a TEMPORARY git worktree (never the
      operator's working tree): a throwaway commit there adds a fake route to
      docs/AUTHZ1_ROUTES.json and a line to verify_rep5.py; every script's
      --self-checks-only from that worktree → still 0 FAIL; the worktree is
      removed (`git worktree remove --force`) and the real tree's
      `git status --porcelain` is unchanged.
  (4) every script's --cleanup-only (900 s each) → exit 0, LEFTOVERS: 0.
  (5) scope: VHK1's own diff (its base → its own end: its merge, or HEAD on
      its branch) touches only verify scripts, the two shared helpers, docs,
      sprint_prompts and scripts/regression_set.txt — no application code, no
      schema.

With VERIFY_NESTED=1, (2)–(4)'s subprocesses are skipped with one
'[SKIP] nested run' line. --self-checks-only runs (5) only. --cleanup-only
removes any stale VHK1 simulation worktree (this script's only fixture) and
prints LEFTOVERS.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

import _verify_cleanup as VC  # noqa: E402,F401  (every regression-set script imports it; VHK1 has no DB fixtures)
import _verify_self as VS  # noqa: E402

SPRINT_KEY = "vhk1"
BEFORE_REF = "afcae0b"   # main @ "merge rep9" — the vhk1-work base
SET_FILE = REPO / "scripts" / "regression_set.txt"
DOC = REPO / "docs" / "VHK1_SELF_CHECKS.md"
SIM_PREFIX = "vhk1-sim-"
FAKE_ROUTE = {"after": "public", "before": "public", "before_effect": "public", "expect": {"rule": "public"},
              "find": False, "methods": ["GET"], "module": "routers.vhk1_sim", "name": "vhk1_sim_fake",
              "note": "VHK1 simulation — a later sprint's route", "path": "/api/v1/vhk1-sim/fake", "public": True,
              "write": False}
ALLOWED = (re.compile(r"^apps/api/scripts/verify_[\w]+\.py$"), re.compile(r"^apps/api/scripts/_verify_(cleanup|self)\.py$"),
           re.compile(r"^docs/"), re.compile(r"^sprint_prompts/"), re.compile(r"^scripts/regression_set\.txt$"))
DATE_NAMES = {"TODAY", "NOW", "VAL", "YEAR", "YEAR_START"}
#: the Task 1c checks VHK1 rewrote onto VS.when_empty (script -> minimum number of when_empty calls)
WHEN_EMPTY = {"verify_usermgmt1.py": 1, "verify_dealroom1.py": 2, "verify_bulkfetchui.py": 1,
              "verify_bulkfetchui3.py": 1, "verify_edgarpipelinea.py": 2, "verify_rep7b.py": 2, "verify_lotmethod.py": 1}

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

def _str_parts(node) -> list[str]:
    """Constant strings inside a node (incl. f-string pieces)."""
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _is_read_text(node) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "read_text"


def _calls(node, name: str) -> list[ast.Call]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Name) and f.id == name) or (isinstance(f, ast.Attribute) and f.attr == name):
                out.append(n)
    return out


def working_tree_comparisons(src: str) -> list[str]:
    """Every since-base comparison against the WORKING TREE still in a script (line: why)."""
    tree = ast.parse(src)
    bad = []
    for n in ast.walk(tree):
        # a `git diff` list with one ref before "--" (BASE vs the working tree)
        if isinstance(n, ast.List) and len(n.elts) >= 2 and all(isinstance(e, ast.Constant) for e in n.elts[:2]) \
                and [e.value for e in n.elts[:2]] == ["git", "diff"]:
            refs, seen_dashdash = [], False
            for e in n.elts[2:]:
                if isinstance(e, ast.Constant) and e.value == "--":
                    seen_dashdash = True
                    break
                if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value.startswith("-"):
                    continue
                refs.append(e)
            if seen_dashdash and len(refs) == 1:
                bad.append(f"{n.lineno}: git diff <one ref> -- … (base vs the working tree)")
        # a read of the moving `main` ref
        if isinstance(n, (ast.Constant, ast.JoinedStr)):
            for s in _str_parts(n) if isinstance(n, ast.JoinedStr) else [n.value]:
                if isinstance(s, str) and re.match(r"^main:[\w./-]+$", s):
                    bad.append(f"{n.lineno}: git show {s} (main moves with every later merge)")
        # git_show(...) == <file>.read_text()  (byte-identical against the working tree)
        if isinstance(n, ast.Compare) and len(n.comparators) == 1:
            sides = [n.left, n.comparators[0]]
            if any(_calls(s, "git_show") for s in sides) and any(_is_read_text(s) or any(
                    _is_read_text(x) for x in ast.walk(s)) for s in sides):
                bad.append(f"{n.lineno}: git_show(...) compared with a working-tree read_text()")
        # VC.b1_diff_bad(base, <working tree>)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "b1_diff_bad":
            if any(_is_read_text(x) for a in n.args for x in ast.walk(a)):
                bad.append(f"{n.lineno}: b1_diff_bad(..., <working tree>)")
    # a function reading AUTHZ1_ROUTES.json at a base ref AND from the working tree
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        base_read = any("AUTHZ1_ROUTES.json" in " ".join(_str_parts(c)) for c in _calls(fn, "git_show"))
        wt_read = any(_is_read_text(n) and "AUTHZ1_ROUTES.json" in " ".join(_str_parts(n.func.value))
                      for n in ast.walk(fn))
        if base_read and wt_read:
            bad.append(f"{fn.lineno}: {fn.name} reads AUTHZ1_ROUTES.json at a base ref AND from the working tree")
    return bad


def module_dates(src: str) -> list[str]:
    tree = ast.parse(src)
    out = []
    for n in tree.body:
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        for t in targets:
            for nm in [x for x in ast.walk(t) if isinstance(x, ast.Name)]:
                if nm.id in DATE_NAMES:
                    out.append(f"{n.lineno}: {nm.id} = …")
    return out


def dispatch_order(src: str) -> tuple[bool, str]:
    """The --self-checks-only dispatch is a module-level `if` naming VS.requested(), BEFORE hydrate() and before
    any import of main / asyncpg / httpx / services."""
    tree = ast.parse(src)
    disp = heavy = None
    for n in tree.body:
        if disp is None and isinstance(n, ast.If) and "requested" in ast.unparse(n.test):
            disp = n.lineno
        if heavy is None:
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and ast.unparse(n.value.func) == "hydrate":
                heavy = n.lineno
            elif isinstance(n, ast.Import) and any(a.name.split(".")[0] in ("main", "asyncpg", "httpx", "services")
                                                   for a in n.names):
                heavy = n.lineno
            elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in ("main", "services",
                                                                                      "_db_connect", "asyncpg"):
                heavy = n.lineno
    if disp is None:
        return False, "no module-level `if … VS.requested()` dispatch"
    if heavy is not None and heavy < disp:
        return False, f"dispatch at line {disp} comes after a heavy import / hydrate() at line {heavy}"
    return True, f"dispatch at line {disp}" + (f", first heavy import / hydrate() at {heavy}" if heavy else "")


def y_static(names: list[str]) -> None:
    section("[Y] Static: no working-tree since-base check; no module-level TODAY; --self-checks-only (before any "
            "heavy import) and --cleanup-only in every script; every script imports _verify_cleanup")
    check(len(names) >= 25 and "verify_vhk1.py" in names, "scripts/regression_set.txt lists the regression set and "
          "verify_vhk1.py", f"{len(names)} names")
    for name in names:
        path = HERE / name
        if not path.exists():
            check(False, f"{name}: exists", str(path))
            continue
        src = path.read_text(encoding="utf-8")
        wt = working_tree_comparisons(src)
        check(not wt, f"{name}: no since-base comparison against the working tree remains", f"{name}: {wt[:4]}")
        md = module_dates(src)
        check(not md, f"{name}: no module-level TODAY / NOW / VAL / YEAR / YEAR_START", f"{name}: {md}")
        ok, how = dispatch_order(src)
        check(ok, f"{name}: --self-checks-only dispatches before hydrate() / any heavy import", f"{name}: {how}")
        check('"--cleanup-only"' in src or "VS.CLEANUP_FLAG" in src, f"{name}: supports --cleanup-only",
              f"{name}: no --cleanup-only")
        check(re.search(r"^import _verify_cleanup\b", src, re.M) is not None, f"{name}: imports _verify_cleanup",
              f"{name}: no `import _verify_cleanup`")
        if name in WHEN_EMPTY:
            k = len(re.findall(r"\bVS\.when_empty\(", src))
            check(k >= WHEN_EMPTY[name], f"{name}: its empty-real-data checks go through VS.when_empty (FIND when "
                  "none exist; the rule per row otherwise)", f"{name}: {k} call(s), want ≥ {WHEN_EMPTY[name]}")


def y_helper() -> None:
    section("[Y] The helper's rules, in-process (no database)")
    end, how = VS.sprint_end_ref("rep6")
    # VHK2 FIX 1: a merged sprint ends at its own BRANCH TIP — its merge's second parent — never the merge commit
    # (whose tree also carries sprints merged into main meanwhile).
    tip = VS.git("rev-parse", "--short", "ead5a7f^2")[1].strip()
    check(end == tip == "f8f5741" and VS.sprint_merge("rep6") == "ead5a7f" and "own merge ead5a7f" in how,
          "a merged sprint ends at its own branch tip, its merge's second parent (rep6 → merge ead5a7f → f8f5741)", how)
    end, how = VS.sprint_end_ref("zz-not-a-sprint-vhk1")
    check(end == "HEAD" and "not merged" in how, "a sprint with no merge on main ends at HEAD (its branch)", how)
    bad = [k for k, sha in VS.SPRINT_MERGES.items() if VS.git("rev-parse", "--verify", f"{sha}^2")[0] != 0]
    check(not bad, "every SPRINT_MERGES entry is a merge commit (has a second parent)", f"{bad}")
    finds, checks = [], []
    r0 = VS.when_empty([], "x", lambda r: True, check=lambda p, l, d="": checks.append(p) or p,
                       find=lambda l, d="": finds.append(l))
    check(r0 is None and finds == ["x"] and checks == [], "when_empty: no real rows → one [FIND], no check", "")
    finds.clear()
    r1 = VS.when_empty([1, 2, 3], "x", lambda r: r != 2, check=lambda p, l, d="": checks.append(p) or p,
                       find=lambda l, d="": finds.append(l))
    r2 = VS.when_empty([1, 3], "x", lambda r: r != 2, check=lambda p, l, d="": checks.append(p) or p,
                       find=lambda l, d="": finds.append(l))
    check(r1 is False and r2 is True and checks == [False, True] and not finds,
          "when_empty: rows exist → the rule is asserted for every row (one bad row fails it)", f"{checks}")
    sample_bad = 'def f():\n    a = run_cmd(["git", "diff", BEFORE_REF, "--", "x.py"], REPO, 60)\n'
    sample_ok = 'def f():\n    a = run_cmd(["git", "diff", "-U0", BEFORE_REF, END, "--", "x.py"], REPO, 60)\n'
    check(working_tree_comparisons(sample_bad) and not working_tree_comparisons(sample_ok),
          "the static scan flags a one-ref `git diff` and passes a two-ref one", "")


# ── (5) scope ────────────────────────────────────────────────────────────────

def self_checks(check, find):
    """VHK1's own since-base check: its diff (BEFORE_REF → its own end) touches no application code or schema."""
    code, out, err = VS.own_diff(BEFORE_REF, SPRINT_KEY, args=("--name-only",))
    files = [f for f in out.splitlines() if f.strip()]
    bad = [f for f in files if not any(p.match(f) for p in ALLOWED)]
    end, how = VS.sprint_end_ref(SPRINT_KEY)
    check(code == 0 and bool(files) and not bad, "VHK1 changed only verify scripts, the shared helpers, docs, "
          "sprint_prompts and scripts/regression_set.txt — no application code, no schema",
          f"{BEFORE_REF} → {how}; {len(files)} files; outside scope: {bad[:8]} {err[-200:]}")
    code, stat, _ = VS.own_diff(BEFORE_REF, SPRINT_KEY, args=("--stat",))
    print(f"[INFO] git diff --stat {BEFORE_REF} {end}: {stat.strip().splitlines()[-1] if stat.strip() else '-'}")


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))


def y_scope() -> None:
    section("[Y] No application code or schema changed (git diff --stat scope check)")
    self_checks(check, find)
    code, out, _ = run_cmd(["git", "status", "--porcelain"], REPO, 60)
    dirty = [ln[3:] for ln in out.splitlines() if ln.strip()]
    outside = [f for f in dirty if not any(p.match(f.strip('"')) for p in ALLOWED)]
    # VHK2: the working tree's uncommitted state belongs to vhk1 only while vhk1 is still on its branch. Once
    # merged, whatever the operator (or a parallel sprint) has uncommitted is not vhk1's change — a [FIND], not a
    # FAIL (2026-11-02: an operator's 'Claude outputs/' folder failed this).
    end, _how = VS.sprint_end_ref(SPRINT_KEY)
    if end == "HEAD":
        check(code == 0 and not outside, "no uncommitted change outside the VHK1 scope either", f"{outside[:8]}")
    elif outside:
        find("uncommitted changes in the working tree (not vhk1's — it is merged)", f"{outside[:8]}")


def y_doc(names: list[str]) -> None:
    section("[Y] Task 1 table 1a–1d, the self-check and cleanup records (docs/VHK1_SELF_CHECKS.md)")
    text = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    heads = [h for h in ("1a", "1b", "1c", "1d") if re.search(rf"^#+ .*\b{h}\b", text, re.M)]
    check(len(heads) == 4, "the doc carries the Task 1 sections 1a, 1b, 1c, 1d", f"{heads}")
    missing = [n for n in names if n != "verify_vhk1.py" and n not in text]
    check(not missing, "every regression-set script is named in the doc", f"{missing}")
    check("--self-checks-only" in text and "--cleanup-only" in text and "simulation" in text.lower(),
          "the doc records the --self-checks-only, --cleanup-only and simulation runs", "")


# ── (2) / (3) / (4) subprocesses ─────────────────────────────────────────────

def run_self_only(names: list[str], root: pathlib.Path, tag: str) -> None:
    for name in names:
        t0 = time.monotonic()
        code, out, err = run_cmd([sys.executable, "-u", str(root / "apps" / "api" / "scripts" / name),
                                  VS.SELF_CHECKS_FLAG], root, 300, env=os.environ | {"VERIFY_NESTED": "1"})
        tot = total_of(out)
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        check(code == 0 and tot is not None and tot[1] == 0 and tot[0] > 0,
              f"{tag}: {name} --self-checks-only → 0 FAIL ({tot[0] if tot else '?'} PASS, "
              f"{time.monotonic() - t0:.1f} s)", f"{name}: exit {code}; {fails[:3]} {tail(out + err)}")


def y_self_only(names: list[str]) -> None:
    section("[Y] --self-checks-only in every script → 0 FAIL (synchronous, 300 s each)")
    run_self_only(names, REPO, "real tree")


def stale_sim_worktrees() -> list[str]:
    code, out, _ = run_cmd(["git", "worktree", "list", "--porcelain"], REPO, 60)
    return [ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("worktree ")
            and pathlib.Path(ln.split(" ", 1)[1]).name.startswith(SIM_PREFIX)]


def remove_worktree(path: str) -> int:
    code, _, _ = run_cmd(["git", "worktree", "remove", "--force", path], REPO, 300)
    run_cmd(["git", "worktree", "prune"], REPO, 60)
    if pathlib.Path(path).exists():
        shutil.rmtree(path, ignore_errors=True)
    return code


def y_simulation(names: list[str]) -> None:
    section("[Y] Simulated later sprint (new route + an edited older verify) in a TEMPORARY worktree → still 0 FAIL")
    code, st_before, _ = run_cmd(["git", "status", "--porcelain"], REPO, 60)
    code, dirty, _ = run_cmd(["git", "status", "--porcelain", "--", "apps/api/scripts", "scripts", "docs"], REPO, 60)
    if dirty.strip():
        find("uncommitted verify changes", "the simulation runs HEAD's scripts — commit first for it to test them: "
             + tail(dirty, 300))
    tmp = pathlib.Path(tempfile.gettempdir()) / f"{SIM_PREFIX}{os.getpid()}"
    head = run_cmd(["git", "rev-parse", "--short", "HEAD"], REPO, 60)[1].strip()
    code, out, err = run_cmd(["git", "worktree", "add", "--detach", str(tmp), "HEAD"], REPO, 900)
    if not check(code == 0, f"temporary worktree at HEAD ({head}) created outside the working tree", f"{tail(out + err)}"):
        return
    try:
        rj = tmp / "docs" / "AUTHZ1_ROUTES.json"
        data = json.loads(rj.read_text(encoding="utf-8"))
        data["routes"].append(dict(FAKE_ROUTE))
        rj.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        r5 = tmp / "apps" / "api" / "scripts" / "verify_rep5.py"
        r5.write_text(r5.read_text(encoding="utf-8") + "# VHK1 simulation: a later sprint edits an older verify\n",
                      encoding="utf-8")
        ident = ["-c", "user.name=vhk1-sim", "-c", "user.email=vhk1-sim@test.local", "-c", "commit.gpgsign=false"]
        c1, _, e1 = run_cmd(["git", *ident, "commit", "-q", "-am", "VHK1 simulation: a later sprint (throwaway)"],
                            tmp, 300)
        sim = run_cmd(["git", "rev-parse", "--short", "HEAD"], tmp, 60)[1].strip()
        check(c1 == 0 and sim != head, "a throwaway commit in the worktree adds a fake route and edits verify_rep5.py",
              f"{c1} {e1[-200:]}")
        run_self_only(names, tmp, f"simulated later sprint ({sim})")
    finally:
        rc = remove_worktree(str(tmp))
        code, st_after, _ = run_cmd(["git", "status", "--porcelain"], REPO, 60)
        check(rc == 0 and not tmp.exists() and str(tmp) not in stale_sim_worktrees(),
              "the temporary worktree is removed", f"{rc} {tmp.exists()}")
        check(st_after == st_before, "git status in the real tree is unchanged by the simulation",
              f"{tail(st_before, 200)} → {tail(st_after, 200)}")


def y_cleanup_only(names: list[str]) -> None:
    section("[Y] --cleanup-only in every script → exit 0, zero fixture rows (synchronous, 900 s each)")
    for name in names:
        t0 = time.monotonic()
        code, out, err = run_cmd([sys.executable, "-u", str(HERE / name), VS.CLEANUP_FLAG], REPO, 900,
                                 env=os.environ | {"VERIFY_NESTED": "1"})
        left = re.findall(r"^LEFTOVERS: (\d+)", out, re.M)
        secs = re.findall(r"^CLEANUP_SECONDS: ([\d.]+)", out, re.M)
        tot = total_of(out)
        print(f"[INFO] {name} --cleanup-only: {secs[-1] if secs else '?'} s cleanup, {time.monotonic() - t0:.1f} s wall")
        check(code == 0 and left and int(left[-1]) == 0 and tot is not None and tot[1] == 0,
              f"{name} --cleanup-only: exits 0, zero fixture rows remain", f"{name}: exit {code}; LEFTOVERS "
              f"{left[-1] if left else '?'}; {tail(out + err)}")


def cleanup_only() -> int:
    """VHK1's only fixtures are temporary simulation worktrees: remove any left by an interrupted run."""
    t0 = time.monotonic()
    for p in stale_sim_worktrees():
        print(f"[INFO] removing stale simulation worktree {p}: exit {remove_worktree(p)}")
    left = len(stale_sim_worktrees())
    print(f"CLEANUP_SECONDS: {time.monotonic() - t0:.1f}")
    print(f"LEFTOVERS: {left}")
    check(left == 0, "--cleanup-only: no VHK1 simulation worktree remains", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


def main() -> int:
    if VS.CLEANUP_FLAG in sys.argv:
        return cleanup_only()
    names = regression_set()
    for fn, args in ((y_static, (names,)), (y_helper, ()), (y_doc, (names,)), (y_scope, ())):
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001
            check(False, f"{fn.__name__} raised", f"{type(exc).__name__}: {exc}")
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        others = [n for n in names if n != "verify_vhk1.py"]
        for fn, args in ((y_self_only, (names,)), (y_simulation, (names,)), (y_cleanup_only, (others,))):
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                check(False, f"{fn.__name__} raised", f"{type(exc).__name__}: {exc}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
