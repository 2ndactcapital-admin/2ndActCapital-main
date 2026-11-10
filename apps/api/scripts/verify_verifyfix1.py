"""verify_verifyfix1 — the regression set is up to today's verify rules (VERIFYFIX1 + fix 1).

STATIC + self-check proof only: this script never runs a full verify, never runs the real regression set, never
connects to a database, never makes a network or model call.

    python3 apps/api/scripts/verify_verifyfix1.py                 # every item
    python3 apps/api/scripts/verify_verifyfix1.py --only=1,2,3,4,6  # skip (5), the 63 nested self-check runs
    python3 apps/api/scripts/verify_verifyfix1.py --self-checks-only

ITEMS (one [Y] section each)
---------------------------
(1) scripts/regression_set.txt: every entry is ONE bare verify_*.py name, resolves in apps/api/scripts/, no
    duplicates — and the list check BITES (a temp list with a path entry, a missing name and a duplicate is refused).
(2) No verify in the set seeds or decides roles via users.role: by NAME (_verify_scan: UPDATE users SET role, a
    users query selecting BY the role string, users inserted without a user_roles grant + the "decides as meant"
    assertion) — and the rule BITES on synthetic bad files and on verify_previewperf.py as it stood before fix 1.
(3) No unscoped count of audit_log / sod_decisions / payment_events (or a generic whole-table loop) in any verify in
    the set — and the rule BITES on a synthetic file; a scoped count passes.
(4) Every verify in the set installs the TOTAL guard (TOTAL on every exit path), supports --self-checks-only, and
    parses any nested verify by its OWN final ^TOTAL line (_verify_total) — and the parser takes the LAST anchored
    line, not an embedded one.
(5) Each verify's --self-checks-only, run as a subprocess (VERIFY_NESTED=1, 600 s each), judged by its OWN final
    ^TOTAL line and exit code — 0 FAIL each.
(6) scripts/run_regressions.sh on fixture lists of tiny stub verifies in a temp dir (never the real set): a missing
    entry (also in a temp COPY of the real list) stops it with exit 2 and runs nothing; a failing entry does not stop
    it; it prints the per-verify table and the summary and writes the summary file.
(T) Teardown leaves zero fixture rows: this script's fixtures are temp files only (no database rows) — the temp dir
    is gone, the work tree is unchanged, nothing was written under the real log root; every guarded table a verify
    writes has its teardown GUC named, and the shared cleanup sets every GUC its delete set needs.

'TOTAL: N PASS, M FAIL' on every exit path (_verify_total.install_guard); exit 1 on any FAIL.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

import _verify_self as VS  # noqa: E402  (stdlib only)

SPRINT_KEY = "verifyfix1"
WIP_COMMITS = {"0af412b": "shared tooling", "9c51561": "counts scoped", "c7e748d": "roles",
               "30b8440": "roles"}
PREVIEWPERF_PRE_FIX = "30b8440"     # verify_previewperf.py still selected its Super Admin by users.role here


def self_checks(check, find) -> None:  # noqa: ARG001
    for sha, what in WIP_COMMITS.items():
        code, subj, _ = VS.git("log", "-1", "--format=%s", sha)
        anc = VS.git("merge-base", "--is-ancestor", sha, "HEAD")[0] == 0
        check(code == 0 and anc and subj.startswith("verifyfix1 (wip)") and what.split()[0] in subj,
              f"verifyfix1 wip commit {sha} ({what}) is in HEAD's history", subj.strip()[:100])
    for rel in ("apps/api/scripts/_verify_scan.py", "apps/api/scripts/_verify_roles.py",
                "apps/api/scripts/_verify_scope.py", "apps/api/scripts/_verify_total.py",
                "apps/api/scripts/_verify_cleanup.py", "scripts/run_regressions.sh", "scripts/regression_set.txt",
                "docs/VERIFYFIX1_INVENTORY.md", "docs/VERIFYFIX1_TEARDOWN_GUARDS.json"):
        check((REPO / rel).is_file(), f"{rel} exists")


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

import _verify_scan as SC  # noqa: E402  (stdlib only)
import _verify_scope as VSC  # noqa: E402
import _verify_total as VT  # noqa: E402

_n_pass = 0
_n_fail = 0

if __name__ == "__main__":
    VT.install_guard(lambda: (_n_pass, _n_fail))


def check(passed, label: str, detail: str = "") -> bool:
    global _n_pass, _n_fail
    ok = passed is True or (isinstance(passed, bool) and passed)
    if ok:
        _n_pass += 1
    else:
        _n_fail += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f" — {detail}" if detail else ""), flush=True)


def section(title: str) -> None:
    print(f"\n{title}", flush=True)


def _only() -> set[str] | None:
    for a in sys.argv[1:]:
        if a.startswith("--only="):
            return {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
    return None


def _scan_text(tmp: pathlib.Path, name: str, src: str) -> dict:
    p = tmp / name
    p.write_text(src, encoding="utf-8")
    return SC.scan_file(p)


# ── (1) the list ────────────────────────────────────────────────────────────────────────────────────────────────

def y_list(tmp: pathlib.Path) -> list[str]:
    section("[Y] (1) regression_set.txt — every entry resolves, one format")
    entries = SC.list_entries()
    probs = SC.list_problems()
    check(len(entries) > 0 and not probs, "every entry is one bare verify_*.py name resolving in apps/api/scripts/, "
          "no duplicates", f"{len(entries)} entries; problems: {probs[:5]}" if probs else f"{len(entries)} entries")
    check(all(SC.BARE_RE.match(e) for e in entries) and not any("/" in e for e in entries),
          "ONE format: bare names only (no path entries)")
    bad = tmp / "bad_list.txt"
    bad.write_text("verify_authz1.py\napps/api/scripts/verify_s30.py\nverify_no_such_vf1.py\nverify_authz1.py\n",
                   encoding="utf-8")
    p = SC.list_problems(bad)
    kinds = {k for k in ("not a bare", "no such script", "listed twice") if any(x.startswith(k) for x in p)}
    check(kinds == {"not a bare", "no such script", "listed twice"},
          "the list check BITES: a path entry, a missing name and a duplicate are each refused", f"{p}")
    return entries


# ── (2) roles ───────────────────────────────────────────────────────────────────────────────────────────────────

GOOD_ROLES = '''
async def seed(c):
    await c.execute("INSERT INTO users (id, org_id, role) VALUES ($1, $2, 'member')", U, ORG)
    await RB.grant_named_role(c, ORG, U, "org_admin")
    await VR.check_roles_as_meant(c, check, {U: (ORG, {"org_admin"})})
'''
BAD_ROLE_WRITE = GOOD_ROLES + '''
async def promote(c):
    await c.execute("UPDATE public.users SET role = 'super_admin' WHERE id = $1", U)
'''
BAD_ROLE_READ = '''
async def pick(c):
    return await c.fetch("SELECT id FROM users WHERE role = 'super_admin' AND is_active")
'''
BAD_NO_GRANT = '''
async def seed(c):
    await c.execute("INSERT INTO users (id, org_id, role) VALUES ($1, $2, 'org_admin')", U, ORG)
'''


def y_roles(tmp: pathlib.Path, rows: list[dict]) -> None:
    section("[Y] (2) no verify in the set seeds or decides roles via users.role (by name: AST strings + calls)")
    bad = [(r["name"], r["roles"], r["role_writes"], r.get("role_reads"), r["decides_as_meant"])
           for r in rows if not r.get("missing") and not r["roles_ok"]]
    check(not bad, "every verify in the set: roles via user_roles grants + the decides-as-meant assertion, no "
          "users.role write or role-string selection", f"{len(rows)} verifies; offending: {bad[:6]}")
    seeders = [r["name"] for r in rows if not r.get("missing") and r["inserts_users"]]
    check(all(next(x for x in rows if x["name"] == n)["decides_as_meant"] for n in seeders),
          "every verify that inserts fixture users asserts the app decides them as meant",
          f"{len(seeders)} seed users")
    g = _scan_text(tmp, "verify_vf1_good_roles.py", GOOD_ROLES)
    w = _scan_text(tmp, "verify_vf1_bad_write.py", BAD_ROLE_WRITE)
    rd = _scan_text(tmp, "verify_vf1_bad_read.py", BAD_ROLE_READ)
    ng = _scan_text(tmp, "verify_vf1_bad_nogrant.py", BAD_NO_GRANT)
    check(g["roles_ok"] and not w["roles_ok"] and w["role_writes"] and not rd["roles_ok"] and rd["role_reads"]
          and not ng["roles_ok"],
          "the roles rule BITES: a users.role UPDATE, a selection BY the role string and users without a grant are "
          "each flagged; a granted + asserted seed passes",
          f"good={g['roles_ok']} write={w['role_writes']} read={rd['role_reads']} nogrant={ng['roles']}")
    old = VS.git_show(PREVIEWPERF_PRE_FIX, "apps/api/scripts/verify_previewperf.py")
    if old is None:
        check(False, "verify_previewperf.py at its pre-fix ref is readable", PREVIEWPERF_PRE_FIX)
    else:
        o = _scan_text(tmp, "verify_previewperf_prefix.py", old)
        now = next((r for r in rows if r["name"] == "verify_previewperf.py"), None)
        check(not o["roles_ok"] and bool(o["role_reads"]) and now is not None and now["roles_ok"],
              "reproduced then fixed: verify_previewperf.py picked its real Super Admin BY users.role before fix 1 "
              "(flagged); today it requires a user_roles super_admin grant (clean)",
              f"pre-fix role-string read at line(s) {o['role_reads']}")


# ── (3) counts ──────────────────────────────────────────────────────────────────────────────────────────────────

BAD_COUNT = '''
async def counts(c):
    return await c.fetchval("SELECT count(*) FROM public.audit_log")
'''
BAD_GENERIC = '''
async def counts(c, t):
    return await c.fetchval(f"SELECT count(*) FROM {t}")
'''
GOOD_COUNT = '''
async def counts(c):
    return await c.fetchval("SELECT count(*) FROM public.audit_log WHERE org_id = ANY($1::uuid[])", ORGS)
'''


def y_counts(tmp: pathlib.Path, rows: list[dict]) -> None:
    section("[Y] (3) no unscoped count of audit_log / sod_decisions / payment_events in any verify in the set")
    check({"public.audit_log", "public.sod_decisions", "public.payment_events"} <= set(VSC.GATED),
          "the gated tables include audit_log, sod_decisions and payment_events", f"{sorted(VSC.GATED)}")
    bad = [(r["name"], r["unscoped_gated"]) for r in rows if not r.get("missing") and not r["counts_ok"]]
    check(not bad, "no verify in the set counts a gated table whole (or loops a generic whole-table count)",
          f"offending: {bad[:6]}")
    other = [(r["name"], r["unscoped_counts"]) for r in rows
             if not r.get("missing") and r["unscoped_counts"] and r["counts_ok"]]
    if other:
        find("other live-written tables read whole (not a before/after count — see the inventory)", f"{other}")
    b = _scan_text(tmp, "verify_vf1_bad_count.py", BAD_COUNT)
    gn = _scan_text(tmp, "verify_vf1_bad_generic.py", BAD_GENERIC)
    ok = _scan_text(tmp, "verify_vf1_good_count.py", GOOD_COUNT)
    check(not b["counts_ok"] and not gn["counts_ok"] and ok["counts_ok"],
          "the counts rule BITES: a whole audit_log count and a generic f-string loop are flagged; a fixture-org "
          "scoped count passes", f"bad={b['unscoped_gated']} generic={gn['unscoped_gated']}")


# ── (4) TOTAL / self-checks / nested ────────────────────────────────────────────────────────────────────────────

def y_total(rows: list[dict]) -> None:
    section("[Y] (4) TOTAL on every exit path, --self-checks-only, nested TOTAL by the shared parser")
    no_guard = [r["name"] for r in rows if not r.get("missing") and not r["total_guard"]]
    check(not no_guard, "every verify in the set installs VT.install_guard (TOTAL on every exit path)",
          f"missing: {no_guard}" if no_guard else f"{len(rows)} verifies")
    no_sc = [r["name"] for r in rows if not r.get("missing") and not r["self_checks"]]
    check(not no_sc, "every verify in the set supports --self-checks-only (VS.run_self_checks)",
          f"missing: {no_sc}" if no_sc else f"{len(rows)} verifies")
    nested = [r["name"] for r in rows if not r.get("missing") and r["runs_nested"]]
    bad = [r["name"] for r in rows if not r.get("missing") and not r["nested_ok"]]
    check(not bad, "every verify that runs another parses its OWN final ^TOTAL line via _verify_total (no bespoke "
          "regex)", f"offending: {bad}" if bad else f"{len(nested)} run nested verifies")
    out = ("[PASS] x — verify_perms1.py --self-checks-only: 0 FAIL — TOTAL: 23 PASS, 0 FAIL\n"
           "[FAIL] y\n\nTOTAL: 166 PASS, 1 FAIL\n")
    t = VT.nested_total(out, 1)
    t0 = VT.nested_total(out.replace("1 FAIL\n", "0 FAIL\n"), 0)
    t_none = VT.nested_total("no total here", 0)
    check(t.failed == 1 and t.passed == 166 and not t.ok and t0.ok and not t_none.ok
          and not VT.nested_total("TOTAL: 3 PASS, 0 FAIL", None).ok,
          "the shared parser takes the LAST anchored ^TOTAL line (not an embedded one), needs exit 0, and a missing "
          "line or a timeout is never a pass", t.detail())


# ── (5) each verify's --self-checks-only ────────────────────────────────────────────────────────────────────────

def y_self_checks(entries: list[str]) -> None:
    section("[Y] (5) each verify's --self-checks-only as a subprocess (VERIFY_NESTED=1, 600 s), its OWN final TOTAL")
    bad = []
    for name in entries:
        t0 = time.monotonic()
        res, out, err, code = VT.run_nested(HERE / name, "--self-checks-only", cwd=REPO, timeout=600)
        secs = time.monotonic() - t0
        ok = check(res.ok, f"{name} --self-checks-only: 0 FAIL", f"{res.detail()} ({secs:.0f} s)"
                   + ("" if res.ok else f"; stderr tail: {err.strip()[-300:]}"))
        if not ok:
            bad.append(name)
    check(not bad, f"all {len(entries)} verifies' self-checks: 0 FAIL", f"not clean: {bad}" if bad else "")


# ── (6) run_regressions.sh on stub verifies ─────────────────────────────────────────────────────────────────────

STUB_PASS = 'print("[PASS] stub ok")\nprint()\nprint("TOTAL: 2 PASS, 0 FAIL")\n'
STUB_FAIL = 'import sys\nprint("[FAIL] stub fails")\nprint()\nprint("TOTAL: 1 PASS, 1 FAIL")\nsys.exit(1)\n'


def _runner(tmp: pathlib.Path, lst: pathlib.Path, tag: str, runner: str | None = None) -> tuple[int, str, dict]:
    env = dict(os.environ)
    paths = {"logs": tmp / f"logs_{tag}", "summary": tmp / f"logs_{tag}" / "regressions_stub.out"}
    env.update({"REGRESSION_SET": str(lst), "REGRESSION_SCRIPTS_DIR": str(tmp / "scripts"),
                "REGRESSION_RUNNER": runner or f"{sys.executable} -u", "REGRESSION_LOG_ROOT": str(paths["logs"]),
                "REGRESSION_SUMMARY_OUT": str(paths["summary"]), "REGRESSION_TIMEOUT": "60",
                "REGRESSION_WORKER_CHECK": "echo 0"})
    env.pop("VERIFY_NESTED", None)
    p = subprocess.run(["bash", str(REPO / "scripts" / "run_regressions.sh")], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout + p.stderr, paths


def y_runner(tmp: pathlib.Path) -> None:
    section("[Y] (6) run_regressions.sh — missing entry stops it, a failing entry does not, summary table printed")
    sd = tmp / "scripts"
    sd.mkdir()
    (sd / "verify_stubfail.py").write_text(STUB_FAIL, encoding="utf-8")
    (sd / "verify_stubpass.py").write_text(STUB_PASS, encoding="utf-8")
    sh = (REPO / "scripts" / "run_regressions.sh").read_text(encoding="utf-8")
    check("regressions_$DAY.out" in sh and "VERIFY_NESTED=1" in sh,
          "the real run writes its summary to sprint_prompts/logs/regressions_<date>.out and runs each verify with "
          "VERIFY_NESTED=1 (default paths, read from the script)")

    # run 1 — two entries: the failing one FIRST, so 'continues after a failure' is visible
    l1 = tmp / "list_two.txt"
    l1.write_text("# stub list\nverify_stubfail.py\nverify_stubpass.py\n", encoding="utf-8")
    code, out, paths = _runner(tmp, l1, "two")
    rows = [ln for ln in out.splitlines() if re.match(r"^verify_stub(fail|pass)\.py\s", ln)]
    fail_rows = [r for r in rows if r.startswith("verify_stubfail.py") and " FAIL " in r]
    pass_rows = [r for r in rows if r.startswith("verify_stubpass.py") and " PASS " in r]
    check(code == 1, "a failing entry makes the run exit 1", f"exit {code}")
    check(len(fail_rows) == 2 and len(pass_rows) == 2,
          "it CONTINUES after the failing entry: the passing stub after it ran, each listed live and again in the "
          "per-verify table", f"{rows}")
    check("PER-VERIFY TOTALS" in out and re.search(r"^SUMMARY: 2 script\(s\): 1 clean, 1 with failures, 0 timed out, "
                                                   r"0 crashed", out, re.M) is not None
          and "NOT CLEAN: verify_stubfail.py" in out,
          "it prints the per-verify table and the final summary", (re.search(r"^SUMMARY:.*$", out, re.M) or [""])[0])
    summ = paths["summary"].read_text(encoding="utf-8") if paths["summary"].is_file() else ""
    check("SUMMARY: 2 script(s)" in summ and (paths["logs"] / time.strftime("%Y-%m-%d") / "verify_stubpass.py.out")
          .is_file(), "it writes the summary file and one log per verify", str(paths["summary"].name))
    check(re.search(r"^verify_stubfail\.py\s+FAIL\s+1\s+1\s", out, re.M) is not None
          and re.search(r"^verify_stubpass\.py\s+PASS\s+2\s+0\s", out, re.M) is not None,
          "each row carries the verify's OWN PASS / FAIL numbers")

    # run 2 — a missing entry: refused before anything runs (a runner that would leave a mark if it ran)
    marker = tmp / "ran_marker"
    mark = tmp / "mark.sh"      # a runner that only leaves a mark — never touches the script it is handed
    mark.write_text(f"#!/usr/bin/env bash\ntouch '{marker}'\n", encoding="utf-8")
    l2 = tmp / "list_missing.txt"
    l2.write_text("verify_stubpass.py\nverify_stubmissing.py\n", encoding="utf-8")
    code, out, paths = _runner(tmp, l2, "missing", runner=f"bash {mark}")
    check(code == 2 and "MISSING/BAD" in out and "verify_stubmissing.py" in out and "NOTHING was run" in out,
          "a missing entry fails LOUDLY (exit 2, the entry named)", f"exit {code}")
    check(not marker.exists() and not paths["logs"].exists(), "… and NOTHING was run (no runner call, no log dir)")

    # run 3 — a temp COPY of the real list with one missing entry appended: the whole real set is refused unrun
    l3 = tmp / "list_real_copy.txt"
    l3.write_text((REPO / "scripts" / "regression_set.txt").read_text(encoding="utf-8")
                  + "verify_vf1_not_a_real_script.py\n", encoding="utf-8")
    env_dir = tmp / "scripts_real_link"
    env_dir.symlink_to(HERE, target_is_directory=True)
    env = dict(os.environ)
    env.update({"REGRESSION_SET": str(l3), "REGRESSION_SCRIPTS_DIR": str(env_dir),
                "REGRESSION_RUNNER": f"bash {mark}", "REGRESSION_LOG_ROOT": str(tmp / "logs_real"),
                "REGRESSION_SUMMARY_OUT": str(tmp / "logs_real" / "s.out"), "REGRESSION_WORKER_CHECK": "echo 0"})
    p = subprocess.run(["bash", str(REPO / "scripts" / "run_regressions.sh")], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    check(p.returncode == 2 and "verify_vf1_not_a_real_script.py" in p.stderr and "1 bad entry" in p.stderr
          and not marker.exists() and not (tmp / "logs_real").exists(),
          "a temp copy of the REAL list plus one missing entry: refused whole (exactly 1 bad entry), nothing run",
          f"exit {p.returncode}; {p.stderr.strip().splitlines()[0][:120] if p.stderr.strip() else ''}")


# ── (T) teardown ────────────────────────────────────────────────────────────────────────────────────────────────

def y_teardown(tmp: pathlib.Path, status_before: str, real_log_before: bool, rows: list[dict]) -> None:
    section("[Y] (T) teardown leaves zero fixture rows")
    shutil.rmtree(tmp, ignore_errors=True)
    check(not tmp.exists(), "every fixture (stub verifies, fixture lists, synthetic scan files, stub logs) was a temp "
          "file and is gone — this script writes no database row")
    after = VS.git("status", "--porcelain")[1]
    check(after == status_before, "the work tree is exactly as before the run (no file left in the repo)",
          "" if after == status_before else f"before {status_before!r} after {after!r}")
    real_log = REPO / "sprint_prompts" / "logs" / "regressions" / time.strftime("%Y-%m-%d")
    check(real_log.exists() == real_log_before, "nothing was written under the real regression log root")
    miss = {r["name"]: r["gucs_missing"] for r in rows if not r.get("missing") and r["gucs_missing"]}
    check(not miss, "every guarded table a verify in the set writes has its teardown GUC named (sod.teardown, "
          "execution.teardown, payments.teardown, …)", f"missing: {miss}" if miss else "")
    import _verify_cleanup as VC
    src = (HERE / "_verify_cleanup.py").read_text(encoding="utf-8")
    need = {"public.payment_events": "payments.teardown", "public.custodian_capabilities": "execution.teardown",
            "public.sod_decisions": "sod.teardown"}
    check(all(g in VC.TEARDOWN_GUARDS.get(t, []) for t, g in need.items()) and "TEARDOWN_GUARDS.get(t" in src,
          "the shared cleanup sets every teardown GUC a guard on a table in its delete set reads (payment_events, "
          "custodian_capabilities, sod_decisions included)", f"{len(VC.TEARDOWN_GUARDS)} guarded tables")


def main() -> int:
    only = _only()
    want = (lambda k: only is None or k in only)
    print(f"[INFO] verify_verifyfix1 — static + self-check proof; items {sorted(only) if only else 'all'}")
    status_before = VS.git("status", "--porcelain")[1]
    real_log_before = (REPO / "sprint_prompts" / "logs" / "regressions" / time.strftime("%Y-%m-%d")).exists()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="verifyfix1_"))
    rows = SC.scan_set()
    entries = SC.list_entries()
    if want("1"):
        entries = y_list(tmp)
    if want("2"):
        y_roles(tmp, rows)
    if want("3"):
        y_counts(tmp, rows)
    if want("4"):
        y_total(rows)
    if want("5"):
        y_self_checks(entries)
    if want("6"):
        y_runner(tmp)
    y_teardown(tmp, status_before, real_log_before, rows)
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:  # noqa: BLE001
        check(False, "verify_verifyfix1 raised", f"{type(exc).__name__}: {exc}")
        code = 1
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL", flush=True)
    sys.exit(code)
