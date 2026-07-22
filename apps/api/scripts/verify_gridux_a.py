"""verify_gridux_a.py — Grid UX mini-sprint A: reusable DataGrid component.

This is a frontend-only sprint with no database changes, so the usual
SQL-assertion pattern does not apply. Instead we assert on file contents
and the real correctness gate for a frontend change: a clean production
build.

Implementation note: per the chosen approach (Option C), the DataGrid is
built on TanStack Table (headless state) + @dnd-kit (column drag-reorder),
rendered through hand-rolled markup and the `--2a-*` design tokens — not
AG-Grid. The assertions below are agnostic to that choice; they check the
stable prop API, the pilot wiring, the build, and theme-purity.

Assertions:
  [1] The DataGrid component exists at the expected path and exposes the
      stable prop API (columnDefs, rowData, gridId).
  [2] The SPV Ledger screen imports and uses the shared DataGrid, and no
      longer carries a separate/unmigrated grid setup (no AG-Grid).
  [3] `npm run build` in apps/web completes with exit code 0.
  [4] No hardcoded Signature-palette hex in the DataGrid file itself
      (same hex set as scripts/brand_sweep_grep.sh — theme-driven from
      day one, the whole point of building it after S24).

Run from anywhere:  python apps/api/scripts/verify_gridux_a.py
"""
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DATAGRID = REPO_ROOT / "apps" / "web" / "components" / "ui" / "DataGrid.jsx"
LEDGER = REPO_ROOT / "apps" / "web" / "components" / "spv" / "SPVLedgerClient.jsx"
WEB_DIR = REPO_ROOT / "apps" / "web"

# Exact hex set from scripts/brand_sweep_grep.sh (HEX_RE) so this gate counts
# precisely what the S24 brand sweep counts.
HEX_RE = re.compile(
    r"#?(1B2B4B|C5A880|E8D5A3|9AA6BF|FAF9F6|F5F1EB|FFFFFF|0F172A|334155|64748B|E2E8F0)"
)

results = []


def check(num, label, passed, detail=""):
    tag = "PASS" if passed else "FAIL"
    line = f"[{num}] {tag} — {label}"
    if detail:
        line += f"\n        {detail}"
    print(line)
    results.append(passed)
    return passed


# ── [1] Component exists + stable prop API ────────────────────────────────────
if not DATAGRID.exists():
    check(1, "DataGrid component file exists", False, f"missing: {DATAGRID}")
else:
    src = DATAGRID.read_text()
    required_props = ["columnDefs", "rowData", "gridId"]
    missing = [p for p in required_props if p not in src]
    has_export = bool(
        re.search(r"export\s+default\s+function\s+DataGrid", src)
        or re.search(r"export\s+default\s+DataGrid", src)
    )
    ok = not missing and has_export
    detail = ""
    if missing:
        detail = f"missing props: {', '.join(missing)}"
    elif not has_export:
        detail = "no `export default ... DataGrid`"
    else:
        detail = f"exists, exports DataGrid with props {', '.join(required_props)}"
    check(1, "DataGrid exists and exposes columnDefs/rowData/gridId", ok, detail)


# ── [2] SPV Ledger migrated onto the shared component ─────────────────────────
if not LEDGER.exists():
    check(2, "SPV Ledger screen file exists", False, f"missing: {LEDGER}")
else:
    lsrc = LEDGER.read_text()
    imports_datagrid = bool(
        re.search(r"import\s+DataGrid\s+from\s+['\"]@/components/ui/DataGrid['\"]", lsrc)
    )
    uses_datagrid = "<DataGrid" in lsrc
    # Must not still carry a separate/unmigrated AG-Grid setup.
    no_aggrid = not re.search(r"ag-?grid|AgGridReact", lsrc, re.IGNORECASE)
    ok = imports_datagrid and uses_datagrid and no_aggrid
    detail = (
        f"imports={imports_datagrid}, uses=<DataGrid>={uses_datagrid}, "
        f"no_aggrid={no_aggrid}"
    )
    check(2, "SPV Ledger imports and renders the shared DataGrid", ok, detail)


# ── [3] Production build passes (real correctness gate) ───────────────────────
print("[3] running `npm run build` in apps/web (this can take a minute)…")
try:
    proc = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(WEB_DIR),
        capture_output=True,
        text=True,
        timeout=900,
    )
    ok = proc.returncode == 0
    if ok:
        check(3, "apps/web `npm run build` exits 0", True, "build succeeded")
    else:
        tail = "\n        ".join(
            (proc.stdout + proc.stderr).strip().splitlines()[-25:]
        )
        check(3, "apps/web `npm run build` exits 0", False, f"exit={proc.returncode}\n        {tail}")
except FileNotFoundError:
    check(3, "apps/web `npm run build` exits 0", False, "npm not found on PATH")
except subprocess.TimeoutExpired:
    check(3, "apps/web `npm run build` exits 0", False, "build timed out (>900s)")


# ── [4] No hardcoded Signature-palette hex in the DataGrid ─────────────────────
if not DATAGRID.exists():
    check(4, "No Signature-palette hex in DataGrid", False, "component missing")
else:
    hits = []
    for i, line in enumerate(DATAGRID.read_text().splitlines(), start=1):
        if HEX_RE.search(line):
            hits.append(f"L{i}: {line.strip()}")
    ok = not hits
    detail = "clean — fully token-driven" if ok else "\n        ".join(hits)
    check(4, "No hardcoded Signature-palette hex in DataGrid", ok, detail)


# ── Summary ───────────────────────────────────────────────────────────────────
print("─" * 60)
passed = sum(1 for r in results if r)
total = len(results)
print(f"{passed}/{total} assertions passed")
sys.exit(0 if passed == total else 1)
