"""verify_gridux_b.py — Grid UX mini-sprint B: migrate screens onto DataGrid.

Frontend-only sprint (no DB changes), so — like verify_gridux_a.py — the
assertions are file-content checks plus the real correctness gate: a clean
production build.

Scope decision (reported by assertion [5]): mini-sprint B migrated the TWO
best-fit hand-rolled tables onto the shared DataGrid built in mini-sprint A:

  1. apps/web/components/marketplace/DealsTable.jsx — the flagship Marketplace
     deal list (Name / Asset Class / Stage / Target / Min / Return / Term /
     Score / [Interest, staff] / Votes / View). A genuine sortable record
     list; the interactive VoteButtons widget and staff-only Interest column
     transplant cleanly into DataGrid cell renderers, no DataGrid extension
     needed. Its separate mobile card layout is left untouched.

  2. apps/web/components/crm/EntityTable.jsx — the CRM entity list (Name /
     Type / Country / Created). The canonical clean DataGrid candidate: a
     shallow, sortable record list with a name link and a type badge.

Other candidates were deliberately SKIPPED to respect the capped scope and to
avoid forcing a poor fit / extending DataGrid:
  - spv/SPVTransactionsTab.jsx — expandable nested allocations sub-table
    (master/detail, per-row async fetch); needs an expandable-row slot
    DataGrid does not have.
  - crm/TargetEditor.jsx — editable hierarchical form-grid, not a record list.
  - admin/UserManagement.jsx, marketplace/MemberInvestmentTracker.jsx,
    spv/SPVSubscriptionsTab.jsx — reasonable future candidates, but the sprint
    caps at the two best; left for a later pass.
  - assistant/render/CapTable.jsx, assistant/render/AllocationSunburst.jsx —
    compact assistant-render widgets, not app list views; out of scope.

Assertions:
  [1] Each migrated screen imports and renders <DataGrid>.
  [2] The old hand-rolled <table>/<thead>/<tbody> markup is gone from each
      migrated file (not left dead alongside DataGrid).
  [3] `npm run build` in apps/web completes with exit code 0.
  [4] No hardcoded Signature-palette hex introduced in either migrated file
      (same hex set as scripts/brand_sweep_grep.sh), scoped to just these two.
  [5] Report which screens were migrated and why (informational, always pass).

Run from anywhere:  python apps/api/scripts/verify_gridux_b.py
"""
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB_DIR = REPO_ROOT / "apps" / "web"

MIGRATED = [
    WEB_DIR / "components" / "marketplace" / "DealsTable.jsx",
    WEB_DIR / "components" / "crm" / "EntityTable.jsx",
]

# Exact hex set from scripts/brand_sweep_grep.sh (HEX_RE) so this gate counts
# precisely what the S24 brand sweep counts.
HEX_RE = re.compile(
    r"#?(1B2B4B|C5A880|E8D5A3|9AA6BF|FAF9F6|F5F1EB|FFFFFF|0F172A|334155|64748B|E2E8F0)"
)

# Hand-rolled table markup: the opening tags a real <table> layout carries.
TABLE_RE = re.compile(r"<table\b|<thead\b|<tbody\b")

results = []


def check(num, label, passed, detail=""):
    tag = "PASS" if passed else "FAIL"
    line = f"[{num}] {tag} — {label}"
    if detail:
        line += f"\n        {detail}"
    print(line)
    results.append(passed)
    return passed


# ── [1] Each migrated screen imports + renders <DataGrid> ─────────────────────
all_ok = True
details = []
for path in MIGRATED:
    if not path.exists():
        all_ok = False
        details.append(f"{path.name}: MISSING")
        continue
    src = path.read_text()
    imports = bool(
        re.search(
            r"import\s+DataGrid\s+from\s+['\"]@/components/ui/DataGrid['\"]", src
        )
    )
    uses = "<DataGrid" in src
    ok = imports and uses
    all_ok = all_ok and ok
    details.append(f"{path.name}: import={imports}, <DataGrid>={uses}")
check(1, "Each migrated screen imports and renders <DataGrid>", all_ok,
      "\n        ".join(details))


# ── [2] Old hand-rolled <table> markup removed from each migrated file ────────
all_ok = True
details = []
for path in MIGRATED:
    if not path.exists():
        all_ok = False
        details.append(f"{path.name}: MISSING")
        continue
    hits = [
        f"L{i}: {line.strip()}"
        for i, line in enumerate(path.read_text().splitlines(), start=1)
        if TABLE_RE.search(line)
    ]
    ok = not hits
    all_ok = all_ok and ok
    details.append(
        f"{path.name}: {'clean — no hand-rolled table markup' if ok else 'still has: ' + '; '.join(hits)}"
    )
check(2, "Hand-rolled <table> markup removed from each migrated file", all_ok,
      "\n        ".join(details))


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
        check(3, "apps/web `npm run build` exits 0", False,
              f"exit={proc.returncode}\n        {tail}")
except FileNotFoundError:
    check(3, "apps/web `npm run build` exits 0", False, "npm not found on PATH")
except subprocess.TimeoutExpired:
    check(3, "apps/web `npm run build` exits 0", False, "build timed out (>900s)")


# ── [4] No Signature-palette hex introduced in the two migrated files ─────────
all_ok = True
details = []
for path in MIGRATED:
    if not path.exists():
        all_ok = False
        details.append(f"{path.name}: MISSING")
        continue
    hits = [
        f"L{i}: {line.strip()}"
        for i, line in enumerate(path.read_text().splitlines(), start=1)
        if HEX_RE.search(line)
    ]
    ok = not hits
    all_ok = all_ok and ok
    details.append(
        f"{path.name}: {'clean' if ok else 'palette hex: ' + '; '.join(hits)}"
    )
check(4, "No Signature-palette hex in the two migrated files", all_ok,
      "\n        ".join(details))


# ── [5] Report which screens were migrated and why (informational) ────────────
report = (
    "Migrated 2 of the best-fit candidates onto DataGrid:\n"
    "        - marketplace/DealsTable.jsx (flagship Marketplace deal list, "
    "10-11 cols incl. VoteButtons + staff Interest)\n"
    "        - crm/EntityTable.jsx (CRM entity list, 4 cols, name link + type badge)\n"
    "        Skipped (out of capped scope / poor fit): SPVTransactionsTab "
    "(nested master/detail), TargetEditor (editable form-grid), "
    "UserManagement / MemberInvestmentTracker / SPVSubscriptionsTab "
    "(deferred), assistant CapTable / AllocationSunburst (chat widgets)."
)
check(5, "Migration scope reported", True, report)


# ── Summary ───────────────────────────────────────────────────────────────────
print("─" * 60)
passed = sum(1 for r in results if r)
total = len(results)
print(f"{passed}/{total} assertions passed")
sys.exit(0 if passed == total else 1)
