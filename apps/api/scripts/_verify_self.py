"""_verify_self — self-checks that later sprints cannot break (VHK1).

WHY
---
In two weeks nine fix rounds went to verify checks that failed because a LATER
sprint added routes, edited an older verify script, or cleaned up test data —
never because the checked feature broke. Every one compared a base commit
(``BEFORE_REF``) with the WORKING TREE, so each new sprint's own work showed up
as a "change" in every older sprint's verify.

RULE
----
A self-check about what a sprint did (its new routes, the gates it left alone,
the lines it changed in another script, the files it kept byte-identical)
compares ``BEFORE_REF`` with **the sprint's own end point**:

* its merge's SECOND parent (the sprint branch's own tip) once merged — a sprint's own change set is that tip
  against the merge base, ``merge-base(<merge>^1, <merge>^2)..<merge>^2``. NEVER the merge commit itself: the
  merge's tree also carries every sprint merged into main while this one was on its branch (VHK2 FIX 1 —
  aibudget1 merged first, so "265f15c → merge vhk2" showed aibudget1's router, route record and menu as VHK2's);
* ``HEAD`` while the sprint is still on its branch.

``BEFORE_REF`` must lie on the sprint's own line — the merge base is its ancestor and it is an ancestor of the tip
— so ``BEFORE_REF..tip`` holds only the sprint's own commits; ``own_base_ok`` asserts it, and every
``own_routes`` / ``own_lines`` / ``own_diff`` call raises when it does not hold.

What the sprint added must still exist today with the same gate
(``still_gated``) — that part reads the working tree on purpose. A later
sprint may edit an older verify script freely: its own guard covers its edits.

Empty real data (no existing accounts, no held-away drafts …) is a [FIND],
never a FAIL: ``when_empty`` asserts the rule for every row when rows exist.

--self-checks-only
------------------
``run_self_checks`` runs one script's git / route / changed-line self-checks
with stdlib + git only — no Doppler, no database, no network, no npm — prints
'TOTAL: N PASS, M FAIL' and returns the exit code. Every regression-set script
dispatches to it BEFORE ``hydrate()`` or any heavy import::

    import _verify_self as VS  # noqa: E402
    SPRINT_KEY = "rep6"
    BEFORE_REF = "f1b0eb0"

    def self_checks(check, find):
        ...

    if __name__ == "__main__" and VS.SELF_CHECKS_FLAG in sys.argv:
        sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

``authz1_routes.py --check`` (routes vs the docs, working tree vs working tree,
~60 s because it imports the whole app) is NOT a since-base check and stays in
the full run only.

Self-contained: stdlib only.
"""

from __future__ import annotations

import difflib
import json
import pathlib
import re
import subprocess
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROUTES_JSON = "docs/AUTHZ1_ROUTES.json"
SELF_CHECKS_FLAG = "--self-checks-only"
CLEANUP_FLAG = "--cleanup-only"

#: Each sprint's merge commit on main (``git log --merges --format='%h %s' main``), stated explicitly because
#: two subjects can name one sprint ("merge usermgmt1 …" and "merge bugfix1 + usermgmt1 implementation").
#: ``sprint_end_ref`` re-checks every entry is a merge commit reachable from main whose subject names the key.
#: A key not listed here is looked up by subject; a sprint not merged yet ends at HEAD.
SPRINT_MERGES = {
    "bulkfetch": "4af0f9d",       # merge bulkfetch + fix1
    "bulkfetchui": "a911ac7",     # merge bulkfetchui + bulkfetchui2 + flat verify regressions + navregroup
    "bulkfetchui2": "a911ac7",
    "navregroup": "a911ac7",
    "authz1": "f115737",
    "usermgmt1": "62364b4",       # merge usermgmt1 (one row per person …); its base is f115737
    "bugfix1": "b07c839",         # merge bugfix1 + usermgmt1 implementation
    "dealroom1": "5760301",
    "dealroom2": "416a763",
    "bulkfetchui3": "fb4205d",
    "rep1": "14200f8",
    "rep2": "b901a01",
    "rep3": "34b3691",
    "rep4": "0b2f7ff",
    "rep5": "2667e7e",
    "rep6": "ead5a7f",
    "rep7": "9ed75ae",
    "rep7b": "a5ffe9a",
    "lotmethod": "376f8ef",
    "dealroom3": "5681d30",
    "rep8": "24962f1",
    "marketdata1": "ab9d823",
    "rep9": "afcae0b",
    "rep10": "c6f7077",
    "vhk1": "bfdfe16",
    "aibudget1": "bd63420",
    "vhk2": "9dbfee0",            # merged AFTER aibudget1: its first parent bd63420 is not its base (c6f7077)
}

#: Sprints that never had a merge commit — committed on main's first-parent line directly. Their end point is the
#: sprint commit itself (checked: an ancestor of main whose subject names the key).
SPRINT_LANDED = {
    "edgarpipelinea": "4080f41",  # sprint: edgarpipelinea.structural (on main directly; later fixes dc087c3 …)
}


# ── git ──────────────────────────────────────────────────────────────────────

def git(*args: str, repo: pathlib.Path | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Foreground ``git`` in the repo; never backgrounded, never interactive."""
    try:
        p = subprocess.run(["git", *args], cwd=str(repo or REPO), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"timeout: {exc}"


def git_show(ref: str, path: str) -> str | None:
    code, out, _ = git("show", f"{ref}:{path}")
    return out if code == 0 else None


def _merge_named(sha: str, key: str) -> tuple[bool, str]:
    code, out, _ = git("log", "-1", "--format=%P%x00%s", sha)
    if code != 0 or "\x00" not in out:
        return False, f"{sha} is not a commit"
    parents, subject = out.strip().split("\x00", 1)
    names = re.search(rf"\bmerge\b.*(?<![\w-]){re.escape(key)}(?![\w-])", subject, re.I) is not None
    on_main = git("merge-base", "--is-ancestor", sha, "main")[0] == 0
    ok = len(parents.split()) >= 2 and names and on_main
    return ok, subject.strip()


_END_CACHE: dict[str, tuple[str, str]] = {}
_MERGE_OF: dict[str, str] = {}


def _tip_of(sprint_key: str, merge: str, subject: str) -> tuple[str, str]:
    """(the merge's second parent, how) — the sprint branch's own tip."""
    code, tip, err = git("rev-parse", "--short", f"{merge}^2")
    if code != 0:
        raise RuntimeError(f"{merge} has no second parent: {err.strip()[:120]}")
    tip = tip.strip()
    _MERGE_OF[sprint_key] = merge
    return tip, (f"{sprint_key}'s own branch tip {tip} (second parent of its own merge {merge} on main, "
                 f"'{subject.strip()[:50]}')")


def sprint_merge(sprint_key: str) -> str | None:
    """The sprint's merge commit on main, or None (landed directly, or not merged yet)."""
    sprint_end_ref(sprint_key)
    return _MERGE_OF.get(sprint_key)


def sprint_base_ref(sprint_key: str) -> str:
    """Where the sprint's own line starts: merge-base(<merge>^1, <merge>^2) once merged (main as it stood when the
    branch last took it — NOT the merge's first parent, which may carry sprints merged meanwhile); the landed
    commit's parent; merge-base(main, HEAD) while still on its branch."""
    end, _ = sprint_end_ref(sprint_key)
    merge = sprint_merge(sprint_key)
    if merge:
        code, out, _ = git("merge-base", f"{merge}^1", f"{merge}^2")
    elif end == "HEAD":
        code, out, _ = git("merge-base", "main", "HEAD")
    else:
        code, out, _ = git("rev-parse", f"{end}^1")
    if code != 0 or not out.strip():
        raise RuntimeError(f"cannot resolve {sprint_key}'s base")
    return git("rev-parse", "--short", out.strip())[1].strip()


def own_base_ok(before_ref: str, sprint_key: str) -> tuple[bool, str]:
    """BEFORE_REF lies on the sprint's own line: the sprint's base is an ancestor of it (or it), and it is an
    ancestor of the sprint's end. Then ``BEFORE_REF..end`` holds only the sprint's own commits."""
    end, _ = sprint_end_ref(sprint_key)
    base = sprint_base_ref(sprint_key)
    a = git("merge-base", "--is-ancestor", base, before_ref)[0] == 0
    b = git("merge-base", "--is-ancestor", before_ref, end)[0] == 0
    return a and b, (f"{sprint_key}: base {base} {'≤' if a else 'NOT an ancestor of'} {before_ref}; "
                     f"{before_ref} {'≤' if b else 'NOT an ancestor of'} end {end}")


def _require_own_base(before_ref: str, sprint_key: str) -> None:
    ok, why = own_base_ok(before_ref, sprint_key)
    if not ok:
        raise RuntimeError(f"{before_ref} → {sprint_key}'s end would include commits that are not its own: {why}")


def sprint_end_ref(sprint_key: str) -> tuple[str, str]:
    """(ref, how) — the sprint's own branch tip (its merge's SECOND parent) once merged, the commit itself when it
    landed on main directly, else HEAD (still on its branch). Never the merge commit: see RULE above."""
    if sprint_key in _END_CACHE:
        return _END_CACHE[sprint_key]
    sha = SPRINT_MERGES.get(sprint_key)
    landed = SPRINT_LANDED.get(sprint_key)
    if landed:
        code, out, _ = git("log", "-1", "--format=%s", landed)
        on_main = git("merge-base", "--is-ancestor", landed, "main")[0] == 0
        if code != 0 or not on_main or sprint_key not in out:
            raise RuntimeError(f"SPRINT_LANDED[{sprint_key!r}] = {landed} is not a commit on main naming it")
        res = (landed, f"{sprint_key} landed on main directly as {landed} ('{out.strip()[:60]}'; no merge commit)")
    elif sha:
        ok, subject = _merge_named(sha, sprint_key)
        if not ok:
            raise RuntimeError(f"SPRINT_MERGES[{sprint_key!r}] = {sha} is not a merge on main naming it ({subject})")
        res = _tip_of(sprint_key, sha, subject)
    else:
        code, out, _ = git("log", "--merges", "--reverse", "--format=%h%x00%s", "main")
        hit = None
        for line in out.splitlines() if code == 0 else []:
            h, _, subject = line.partition("\x00")
            if re.search(rf"^merge\b.*(?<![\w-]){re.escape(sprint_key)}(?![\w-])", subject, re.I):
                hit = (h, subject)
                break
        if hit:
            res = _tip_of(sprint_key, hit[0], hit[1])
        else:
            head = git("rev-parse", "--short", "HEAD")[1].strip()
            res = ("HEAD", f"{sprint_key} is not merged on main yet — HEAD ({head}, the sprint's own branch)")
    _END_CACHE[sprint_key] = res
    return res


# ── routes ───────────────────────────────────────────────────────────────────

def gates(d: dict) -> dict:
    """AUTHZ1_ROUTES.json -> {(methods tuple, path): route record}."""
    return {(tuple(r.get("methods") or []), r["path"]): r for r in (d or {}).get("routes", [])}


def gate_of(rec: dict) -> tuple:
    return (rec.get("after"), json.dumps(rec.get("expect"), sort_keys=True))


def routes_at(ref: str) -> dict:
    src = git_show(ref, ROUTES_JSON)
    if src is None:
        raise RuntimeError(f"cannot read {ROUTES_JSON} at {ref}")
    return gates(json.loads(src))


def routes_today() -> dict:
    return gates(json.loads((REPO / ROUTES_JSON).read_text(encoding="utf-8")))


class OwnRoutes:
    """The routes a sprint added, BEFORE_REF -> its own end ref (its branch tip; never the merge commit, never the
    working tree).

    before / end / today: {key: record}; new: keys added by the sprint; changed / removed: routes that existed at
    BEFORE_REF whose gate the SPRINT changed or removed (before -> end); end_ref / how: the end point and why."""

    def __init__(self, before_ref: str, sprint_key: str):
        self.before_ref = before_ref
        self.end_ref, self.how = sprint_end_ref(sprint_key)
        _require_own_base(before_ref, sprint_key)
        self.before = routes_at(before_ref)
        self.end = routes_at(self.end_ref)
        self.today = routes_today()
        self.new = [k for k in self.end if k not in self.before]
        self.changed = [k for k in self.before if k in self.end and gate_of(self.before[k]) != gate_of(self.end[k])]
        self.removed = [k for k in self.before if k not in self.end]

    def where(self) -> str:
        return f"{self.before_ref} → {self.how}"


def own_routes(before_ref: str, sprint_key: str) -> OwnRoutes:
    return OwnRoutes(before_ref, sprint_key)


def still_gated(own: OwnRoutes, keys=None) -> list:
    """Each of the sprint's routes (or ``keys``) exists TODAY with the gate it had at the sprint's end ref; returns
    the ones that do not (empty = all fine)."""
    out = []
    for k in (own.new if keys is None else keys):
        if k not in own.today:
            out.append((k, "absent today"))
        elif gate_of(own.today[k]) != gate_of(own.end[k]):
            out.append((k, f"{own.end[k].get('after')} → {own.today[k].get('after')}"))
    return out


# ── lines ────────────────────────────────────────────────────────────────────

def fn_at(lines: list[str], i: int) -> str | None:
    for k in range(min(i, len(lines) - 1), -1, -1):
        m = re.match(r"(?:async )?def (\w+)", lines[k])
        if m:
            return m.group(1)
    return None


class OwnLines:
    """A file at BEFORE_REF and at the sprint's own end ref. ``old`` / ``new`` are the two texts (None when
    absent); ``changed`` is [(sign, lineno, enclosing def, stripped text)], difflib opcodes, junk off."""

    def __init__(self, path: str, before_ref: str, sprint_key: str, base_ref: str | None = None):
        self.path = path
        self.before_ref = base_ref or before_ref
        self.end_ref, self.how = sprint_end_ref(sprint_key)
        _require_own_base(self.before_ref, sprint_key)
        self.old = git_show(self.before_ref, path)
        self.new = git_show(self.end_ref, path)
        o = (self.old or "").splitlines()
        n = (self.new or "").splitlines()
        self.old_lines, self.new_lines = o, n
        self.changed = []
        self.opcodes = difflib.SequenceMatcher(None, o, n, autojunk=False).get_opcodes()
        for tag, i1, i2, j1, j2 in self.opcodes:
            if tag == "equal":
                continue
            self.changed += [("-", i + 1, fn_at(o, i), o[i].strip()) for i in range(i1, i2)]
            self.changed += [("+", j + 1, fn_at(n, j), n[j].strip()) for j in range(j1, j2)]

    def where(self) -> str:
        return f"{self.path}: {self.before_ref} → {self.how}"


def own_lines(path: str, before_ref: str, sprint_key: str, base_ref: str | None = None) -> OwnLines:
    """A changed-line guard over another file compares BEFORE_REF (or ``base_ref``) -> the sprint's own end ref,
    never the working tree: later sprints may edit that file freely; their own guards cover their edits."""
    return OwnLines(path, before_ref, sprint_key, base_ref)


def own_diff(before_ref: str, sprint_key: str, *paths: str, args: tuple = ()) -> tuple[int, str, str]:
    """``git diff [args] BEFORE_REF <end ref> -- paths`` — the sprint's own diff (BEFORE_REF on its own line → its
    branch tip), never the merge commit, never the working tree."""
    end, _ = sprint_end_ref(sprint_key)
    _require_own_base(before_ref, sprint_key)
    return git("diff", *args, before_ref, end, "--", *paths)


def own_text(sprint_key: str, path: str) -> str | None:
    """A file as the sprint left it (its end ref — its branch tip once merged)."""
    return git_show(sprint_end_ref(sprint_key)[0], path)


# ── empty real data ──────────────────────────────────────────────────────────

def when_empty(rows, label: str, rule, *, check, find, detail=None) -> bool | None:
    """Real-data rule: a [FIND] ('none exist today') when ``rows`` is empty; otherwise ``rule(row)`` is asserted
    for EVERY row (one check, the offending rows in its detail). Returns None for the FIND, else the check."""
    rows = list(rows or [])
    if not rows:
        find(label, "none exist today — nothing real to assert the rule on (the fixtures above prove it)")
        return None
    bad = [r for r in rows if not rule(r)]
    shown = detail(bad) if (detail and bad) else str([dict(r) if hasattr(r, "keys") else r for r in bad[:3]])
    return check(not bad, label, f"{len(rows)} row(s); {len(bad)} break the rule: {shown}" if bad else
                 f"{len(rows)} row(s)")


# ── --self-checks-only ───────────────────────────────────────────────────────

def run_self_checks(fn, sprint_key: str, script: str) -> int:
    """Run one script's self-checks with local counters; print 'TOTAL: N PASS, M FAIL'; return the exit code."""
    n = {"pass": 0, "fail": 0}
    name = pathlib.Path(script).name

    def check(passed, label, detail=""):
        ok = bool(passed)
        n["pass" if ok else "fail"] += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok or not detail else f" — {name}: {detail}"),
              flush=True)
        return ok

    def find(label, detail=""):
        print(f"[FIND] {label}" + (f" — {detail}" if detail else ""), flush=True)

    print(f"[INFO] {name} --self-checks-only (git / route / changed-line self-checks; no database, no network)")
    try:
        end, how = sprint_end_ref(sprint_key)
        anc = git("merge-base", "--is-ancestor", end, "HEAD")[0] == 0
        check(anc, f"{sprint_key}'s end point resolves and is in HEAD's history: {how}", f"{end}")
        fn(check, find)
    except Exception as exc:  # noqa: BLE001
        check(False, "self-checks raised", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}")
    print(f"\nTOTAL: {n['pass']} PASS, {n['fail']} FAIL")
    return 0 if n["fail"] == 0 else 1


def requested() -> bool:
    return SELF_CHECKS_FLAG in sys.argv
