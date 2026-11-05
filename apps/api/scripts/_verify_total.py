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
