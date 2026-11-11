"""_verify_total — ONE parser for a nested verify script's own final TOTAL line (stdlib only).

WHY (PORTENTRY2 FIX 1)
----------------------
Twelve verify scripts read a nested script's result with
``re.search(r"TOTAL: (\\d+) PASS, (\\d+) FAIL", out)`` — the FIRST match anywhere in
the output. A nested script's detail lines can themselves contain other scripts'
totals (verify_portentry1's _verify_self section prints
"verify_perms1.py --self-checks-only: 0 FAIL — TOTAL: 23 PASS, 0 FAIL"), so
verify_portentry2's regression reported "verify_portentry1.py: 0 FAIL — TOTAL: 23
PASS, 0 FAIL" for a 167-check script: the gate read someone else's number and
would have passed while the nested script failed.

RULE
----
The nested script's result is the LAST line that STARTS with ``TOTAL: `` (anchored,
multiline). The process exit code must agree with it (0 FAIL ⇔ exit 0). No such
line → not a pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TOTAL_RE = re.compile(r"^TOTAL: (\d+) PASS, (\d+) FAIL\b.*$", re.M)


@dataclass(frozen=True)
class NestedTotal:
    ok: bool            # True only when: a final TOTAL line exists, FAIL == 0, PASS > 0 and the exit code is 0
    passed: int | None
    failed: int | None
    line: str | None    # the final TOTAL line itself (None when absent)
    reason: str         # why not ok ("" when ok)

    def detail(self) -> str:
        return self.line if self.ok else f"{self.line or 'no final TOTAL line'}; {self.reason}"


def final_total(out: str) -> tuple[int, int, str] | None:
    """(pass, fail, line) of the LAST line starting with 'TOTAL: ', or None."""
    last = None
    for m in _TOTAL_RE.finditer(out or ""):
        last = m
    if last is None:
        return None
    return int(last.group(1)), int(last.group(2)), last.group(0)


def nested_total(out: str, returncode: int | None) -> NestedTotal:
    """Judge a nested verify run from its own final TOTAL line AND its exit code.

    `returncode` None means the caller has no exit code (e.g. a timeout) — never a pass."""
    t = final_total(out)
    if t is None:
        return NestedTotal(False, None, None, None, f"exit {returncode}, no line starting with 'TOTAL: '")
    p, f, line = t
    if returncode is None:
        return NestedTotal(False, p, f, line, "no exit code (timeout or not run)")
    if f == 0 and returncode != 0:
        return NestedTotal(False, p, f, line, f"final TOTAL says 0 FAIL but exit code is {returncode}")
    if f != 0 and returncode == 0:
        return NestedTotal(False, p, f, line, f"final TOTAL says {f} FAIL but exit code is 0")
    if f != 0:
        return NestedTotal(False, p, f, line, f"{f} FAIL (exit {returncode})")
    if p == 0:
        return NestedTotal(False, p, f, line, "0 PASS")
    return NestedTotal(True, p, f, line, "")


# ── VERIFYFIX1 — TOTAL on EVERY exit path, one shared guard ──────────────────────────────────────────────────────
#
# A verify that crashes at import (hydrate, `import main`), raises out of asyncio.run, calls sys.exit() early or is
# killed by `timeout` used to end with no TOTAL line, and the runner reported "CRASH (no TOTAL line)" with nothing to
# read. ``install_guard(counts)`` — called right after the --self-checks-only dispatch, before any heavy import —
# makes every exit print one:
#
#   * an uncaught exception (import time included): its traceback, ``[FAIL] the verify crashed — <exc>``;
#   * SIGTERM (``timeout``'s signal): ``[FAIL] terminated by SIGTERM (a timeout?)`` and exit 124;
#   * at interpreter exit: if no ``TOTAL:`` line was written since the last crash, ``TOTAL: N PASS, M FAIL`` from the
#     script's own counters (+1 FAIL for the crash / termination / a missing TOTAL).
#
# A script that already prints its TOTAL on every path prints exactly one (the guard watches stdout for it).

import atexit as _atexit
import os as _os
import signal as _signal
import sys as _sys
import traceback as _traceback

_TOTAL_SEEN_RE = re.compile(r"(?:^|\n)TOTAL: \d+ PASS, \d+ FAIL")
_GUARD = {"installed": False, "seen": False, "extra": 0, "counts": None}


class _StdoutWatch:
    """Pass-through stdout that notes when a TOTAL line is written."""

    def __init__(self, inner):
        self._inner = inner

    def write(self, s):
        if s and "TOTAL: " in s and _TOTAL_SEEN_RE.search(s if s.startswith("\n") else "\n" + s):
            _GUARD["seen"] = True
        return self._inner.write(s)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _counts() -> tuple[int, int]:
    fn = _GUARD["counts"]
    try:
        p, f = fn() if fn else (0, 0)
        return int(p or 0), int(f or 0)
    except Exception:  # noqa: BLE001 — counters not defined yet (crash at import): count from zero
        return 0, 0


def total_line(passed: int, failed: int) -> str:
    return f"\nTOTAL: {passed} PASS, {failed} FAIL"


def _at_exit() -> None:
    if _GUARD["seen"]:          # a TOTAL line was written after the last crash / termination (if any)
        return
    p, f = _counts()
    if not _GUARD["seen"] and not _GUARD["extra"]:
        print("[FAIL] the verify exited without printing its TOTAL line")
        f += 1
    try:
        print(total_line(p, f + _GUARD["extra"]), flush=True)
    except Exception:  # noqa: BLE001
        pass


def _excepthook(etype, value, tb) -> None:
    _traceback.print_exception(etype, value, tb)
    if issubclass(etype, KeyboardInterrupt):
        print("[FAIL] interrupted (KeyboardInterrupt)")
    else:
        print(f"[FAIL] the verify crashed — {etype.__name__}: {value}")
    _GUARD["extra"] += 1
    _GUARD["seen"] = False


def _on_sigterm(signum, frame) -> None:  # noqa: ARG001
    print(f"[FAIL] terminated by signal {signum} (a timeout?)", flush=True)
    _GUARD["extra"] += 1
    _GUARD["seen"] = False
    raise SystemExit(124)


def install_guard(counts) -> None:
    """Make every exit path print ``TOTAL: N PASS, M FAIL``; ``counts`` returns the script's (pass, fail) now."""
    _GUARD["counts"] = counts
    if _GUARD["installed"]:
        return
    _GUARD["installed"] = True
    if not isinstance(_sys.stdout, _StdoutWatch):
        _sys.stdout = _StdoutWatch(_sys.stdout)
    _sys.excepthook = _excepthook
    try:
        _signal.signal(_signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):  # not the main thread — the atexit TOTAL still holds
        pass
    _atexit.register(_at_exit)


def guard_installed() -> bool:
    return bool(_GUARD["installed"])


# ── VERIFYFIX1 — one nested-run helper: run a verify flat, judge it by its OWN final TOTAL line + exit code ────────

# ── CPSIM04 FIX 1 — REGRESSION MODE: one full regression run at the end of the sprint cycle ─────────────────────────
#
# ``VERIFY_REGRESSIONS`` decides how every nested verify runs (Joe, 2026-11-11):
#   * unset / ``fast`` (the DEFAULT — what run_sprint.sh gets): the nested verify runs with ``--self-checks-only``
#     (its static / self-check proof, seconds), still judged by its OWN final TOTAL line + exit code, still a [FAIL]
#     on any FAIL. A nested script that has no ``--self-checks-only`` mode is a FAIL ("no --self-checks-only") —
#     never silently skipped. One "[INFO] full regression deferred …" line is printed per process.
#   * ``full`` (scripts/run_regressions.sh sets it): today's behaviour exactly — the full nested verify, flat, after
#     teardown, VERIFY_NESTED=1, the caller's timeout (2700 s).
# Any other value is refused (a typo never silently picks a mode).

REGRESSION_MODES = ("fast", "full")
FAST_TIMEOUT = 600
_MODE_NOTED = {"done": False}


def regression_mode() -> str:
    v = (_os.environ.get("VERIFY_REGRESSIONS") or "fast").strip().lower()
    if v not in REGRESSION_MODES:
        raise ValueError(f"VERIFY_REGRESSIONS={v!r} — expected one of {REGRESSION_MODES} (unset = fast)")
    return v


def has_self_checks_only(script) -> bool:
    """True when the nested verify's own source dispatches on ``--self-checks-only``."""
    try:
        with open(str(script), encoding="utf-8") as fh:
            return "--self-checks-only" in fh.read()
    except OSError:
        return False


def run_nested(script, *args: str, cwd=None, timeout: int = 2700, env_extra: dict | None = None,
               python: str | None = None):
    """Run ``python -u <script> *args`` in the foreground with VERIFY_NESTED=1; return (NestedTotal, out, err, code).

    Honours ``VERIFY_REGRESSIONS`` (see above): in ``fast`` mode ``--self-checks-only`` is appended (once) and the
    timeout is capped at FAST_TIMEOUT; ``full`` is the unchanged full run.
    A timeout returns code 124 and a NestedTotal that is never ok (``nested_total(out, None)``)."""
    import subprocess
    mode = regression_mode()
    args = tuple(args)
    if mode == "fast":
        if not _MODE_NOTED["done"]:
            _MODE_NOTED["done"] = True
            print("[INFO] full regression deferred to the cycle-end run (VERIFY_REGRESSIONS=full)", flush=True)
        if not has_self_checks_only(script):
            return (NestedTotal(False, None, None, None, "no --self-checks-only (fast regression mode cannot run it)"),
                    "", "no --self-checks-only", 2)
        if "--self-checks-only" not in args:
            args = args + ("--self-checks-only",)
        timeout = min(timeout, FAST_TIMEOUT)
    env = dict(_os.environ)
    env["VERIFY_NESTED"] = "1"
    env.update(env_extra or {})
    cmd = [python or _sys.executable, "-u", str(script), *args]
    try:
        p = subprocess.run(cmd, cwd=None if cwd is None else str(cwd), capture_output=True, text=True,
                           timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        return nested_total(p.stdout, p.returncode), p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return nested_total(out, None), out, f"timeout after {timeout}s", 124
