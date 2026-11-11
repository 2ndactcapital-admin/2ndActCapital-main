"""ALTREAD1 — a ``--self-checks-only`` mode for the four older offline Altruist verifies (sprint 2 / 3 / 5 / 6).

Fast regression mode (VT.run_nested, VERIFY_REGRESSIONS unset / fast) runs every nested verify with
``--self-checks-only``; these four had none, so fast mode could only FAIL them. Their self-checks are STATIC (no
database, no network, no import of ``main``): the script parses, and the markers that keep it on the documented
Altruist call shapes (ALTREAD1) are present by name. The full run (scripts/run_regressions.sh) is unchanged.
"""

from __future__ import annotations

import ast
import pathlib
import sys


def run(script: str, needles: tuple[str, ...]) -> int:
    path = pathlib.Path(script)
    src = path.read_text(encoding="utf-8")
    n = {"pass": 0, "fail": 0}

    def check(ok: bool, label: str, detail: str = "") -> None:
        n["pass" if ok else "fail"] += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""), flush=True)

    print(f"[INFO] {path.name} --self-checks-only (static: parse + documented-shape markers; no DB, no network)")
    try:
        ast.parse(src)
        check(True, f"{path.name} parses")
    except SyntaxError as exc:
        check(False, f"{path.name} parses", str(exc))
    for nd in needles:
        check(nd in src, f"{path.name} carries {nd!r}")
    print(f"\nTOTAL: {n['pass']} PASS, {n['fail']} FAIL")
    return 0 if n["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], tuple(sys.argv[2:])))
