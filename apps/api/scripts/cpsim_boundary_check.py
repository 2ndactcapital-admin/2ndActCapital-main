"""cpsim_boundary_check — the Counterparty Simulator's separation rules, as a static scan (CPSIM01 Task 7).

    python3 apps/api/scripts/cpsim_boundary_check.py            # exit 0 when clean, 1 on any violation
    python3 apps/api/scripts/cpsim_boundary_check.py --root R   # scan another checkout (R = repo root)

Principle 1 (separate book, shared reference data) and principle 2 (semi-independent), docs/cpsim/CPSIM01_NOTES.md:

  B1  No reference to a ``cpsim.*`` database object (every table / function the docs/cpsim*_part*.sql migrations
      create) outside the simulator package, its admin router, the verify scripts and this script — Hollisworks'
      services never read the simulator's book directly; they talk to it over HTTP.
  B2  No import of the ``cpsim`` Python package outside main.py (the mount), routers/admin_counterparties.py and the
      verify / this script.
  B3  Inside apps/api/cpsim/: imports from ``services`` are ONLY services.us_business_days and services.bitemporal;
      nothing from ``routers`` or ``main``.
  B4  Inside apps/api/cpsim/: no SQL write (INSERT / UPDATE / DELETE) to a table outside ``cpsim.``, and every
      archive_live_row call names a ``cpsim.`` table — the simulator writes only its own book (audit_log is written by
      the admin router as the login role, never by the package).

Markers (principle 3): M1 the sub-app stamps X-Counterparty-Simulated on every response; M2 both admin pages render
the SIMULATED banner; M3 simulator secrets carry the ``cpsim_`` prefix; M4 the migration defines cpsim.may_connect and
the is_simulated CHECK.

Stdlib only. Code files only (``.py`` under apps/api; ``.js/.jsx/.mjs/.ts/.tsx`` under apps/web); ``venv``,
``node_modules``, ``.next`` and ``__pycache__`` are skipped.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parents[2]
SKIP_DIRS = {"venv", "node_modules", ".next", "__pycache__", ".git"}
WEB_EXT = {".js", ".jsx", ".mjs", ".ts", ".tsx"}
ALLOWED_SERVICE_IMPORTS = {"services.us_business_days", "services.bitemporal"}

# repo-relative paths (or prefixes ending '/') allowed to name cpsim.* objects / import the package
B1_ALLOWED = ("apps/api/cpsim/", "apps/api/routers/admin_counterparties.py", "apps/api/scripts/cpsim_boundary_check.py")
# cpsim02: the migration applier (_apply_cpsim02.py) reads the catalog for its own objects
B1_ALLOWED_RX = re.compile(r"^apps/api/scripts/(verify_cpsim\d+\w*|_apply_cpsim\d+)\.py$")
B2_ALLOWED = ("apps/api/main.py", "apps/api/routers/admin_counterparties.py", "apps/api/scripts/cpsim_boundary_check.py")

_OBJ_RX = re.compile(r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|(?:OR\s+REPLACE\s+)?FUNCTION|INDEX|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
                     r"(?:\w+\s+ON\s+)?cpsim\.([a-z_][a-z0-9_]*)", re.I)
_IMPORT_RX = re.compile(r"^\s*(?:from\s+(cpsim)(?:\.[\w.]+)?\s+import\b|import\s+(cpsim)(?:\.[\w.]+)?\b)", re.M)
_SVC_RX = re.compile(r"^\s*(?:from\s+(services(?:\.\w+)*)\s+import\s+([\w, ]+)|import\s+(services(?:\.\w+)*))", re.M)
_HOLLIS_RX = re.compile(r"^\s*(?:from|import)\s+(routers|main)\b", re.M)
_WRITE_RX = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_\"{][\w.\"{}]*)", re.I)
_ARCHIVE_RX = re.compile(r"archive_live_row\(\s*\w+\s*,\s*([^,)]+)")


def _rel(root: pathlib.Path, p: pathlib.Path) -> str:
    return p.relative_to(root).as_posix()


def _files(root: pathlib.Path):
    """Code files, pruning SKIP_DIRS BEFORE descending (node_modules / venv are never walked)."""
    for base, exts in ((root / "apps" / "api", {".py"}), (root / "apps" / "web", WEB_EXT)):
        if not base.exists():
            continue
        for d, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
            for f in files:
                if os.path.splitext(f)[1] in exts:
                    yield pathlib.Path(d) / f


def cpsim_objects(root: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for mig in sorted((root / "docs").glob("cpsim*_part*.sql")):
        names |= {m.group(1) for m in _OBJ_RX.finditer(mig.read_text(encoding="utf-8"))}
    return names


def _allowed_b1(rel: str) -> bool:
    return any(rel == a or (a.endswith("/") and rel.startswith(a)) for a in B1_ALLOWED) or bool(B1_ALLOWED_RX.match(rel))


def _allowed_b2(rel: str) -> bool:
    return rel.startswith("apps/api/cpsim/") or rel in B2_ALLOWED or bool(B1_ALLOWED_RX.match(rel))


def scan(root: pathlib.Path = DEFAULT_ROOT) -> tuple[list[str], int, set[str]]:
    root = root.resolve()
    objs = cpsim_objects(root)
    obj_rx = re.compile(r"\bcpsim\.(" + "|".join(sorted(map(re.escape, objs))) + r")\b") if objs else None
    bad: list[str] = []
    n = 0
    for p in _files(root):
        n += 1
        rel = _rel(root, p)
        try:
            src = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if obj_rx is not None and not _allowed_b1(rel):
            for i, line in enumerate(src.splitlines(), 1):
                m = obj_rx.search(line)
                if m:
                    bad.append(f"B1 {rel}:{i}: references cpsim.{m.group(1)} outside the simulator boundary")
        if p.suffix == ".py" and not _allowed_b2(rel):
            for m in _IMPORT_RX.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                bad.append(f"B2 {rel}:{line}: imports the cpsim package outside the mount / admin router")
        if rel.startswith("apps/api/cpsim/") and p.suffix == ".py" and "/tests/" not in rel:
            for m in _SVC_RX.finditer(src):
                mods = []
                if m.group(1):
                    mods = [m.group(1)] if m.group(1) != "services" else [
                        f"services.{x.strip()}" for x in m.group(2).split(",") if x.strip()]
                elif m.group(3):
                    mods = [m.group(3)]
                for mod in mods:
                    if mod not in ALLOWED_SERVICE_IMPORTS:
                        line = src.count("\n", 0, m.start()) + 1
                        bad.append(f"B3 {rel}:{line}: imports {mod} (allowed from services/: "
                                   f"{', '.join(sorted(ALLOWED_SERVICE_IMPORTS))})")
            for m in _HOLLIS_RX.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                bad.append(f"B3 {rel}:{line}: imports Hollisworks' {m.group(1)}")
            for m in _WRITE_RX.finditer(src):
                target = m.group(2).strip('"')
                if target.startswith("{") or target.lower() in ("set", "on"):
                    continue                     # an f-string table parameter (checked below) / not a table
                if not target.startswith("cpsim."):
                    line = src.count("\n", 0, m.start()) + 1
                    bad.append(f"B4 {rel}:{line}: {m.group(1).upper()} {target} — the simulator writes only cpsim.*")
            for m in re.finditer(r'f"[^"\n]*\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\{(\w+)\}', src):
                # the one parameterised writer (_close_live) must only ever be called with a cpsim. table
                for call in re.finditer(r"(?<!def )_close_live\(\s*\w+\s*,\s*([^,)]+)", src):
                    if not call.group(1).strip().strip("\"'").startswith("cpsim."):
                        line = src.count("\n", 0, call.start()) + 1
                        bad.append(f"B4 {rel}:{line}: _close_live on {call.group(1).strip()} (cpsim.* only)")
                break
            for m in _ARCHIVE_RX.finditer(src):
                if not m.group(1).strip().strip("\"'").startswith("cpsim."):
                    line = src.count("\n", 0, m.start()) + 1
                    bad.append(f"B4 {rel}:{line}: archive_live_row on {m.group(1).strip()} (cpsim.* only)")
    return bad, n, objs


def markers(root: pathlib.Path = DEFAULT_ROOT) -> list[str]:
    root = root.resolve()
    bad = []

    def text(rel):
        p = root / rel
        return p.read_text(encoding="utf-8") if p.exists() else ""

    app = text("apps/api/cpsim/app.py")
    init = text("apps/api/cpsim/__init__.py")
    if not ('SIMULATED_HEADER = "X-Counterparty-Simulated"' in init and "class SimulatedMarker" in app
            and "return SimulatedMarker(sub)" in app and "SIMULATED_HEADER.lower().encode()" in app):
        bad.append("M1 the sub-app does not stamp X-Counterparty-Simulated on every response (cpsim/app.py)")
    for page in ("apps/web/app/admin/counterparties/page.js", "apps/web/app/admin/counterparties/[id]/page.js"):
        src = text(page)
        if "SimulatedBanner" not in src and "CounterpartiesAdmin" not in src:
            bad.append(f"M2 {page} renders no SIMULATED banner")
    comp = text("apps/web/components/counterparties/Counterparties.jsx")
    if 'data-testid="simulated-banner"' not in comp or ">SIMULATED<" not in comp:
        bad.append("M2 components/counterparties/Counterparties.jsx: the SIMULATED banner is missing")
    gen = text("apps/api/cpsim/generators.py")
    if 'SECRET_PREFIX = "cpsim_"' not in gen or 'KEY_PREFIX = "cpsim_live_"' not in gen:
        bad.append("M3 cpsim/generators.py: simulator secrets do not carry the cpsim_ prefix")
    if "def is_simulator_secret" not in gen or "is_simulator_secret(presented)" not in text("apps/api/cpsim/registry.py"):
        bad.append("M3 the sub-app does not refuse a value without the cpsim_ prefix before any lookup")
    mig = text("docs/cpsim01_part1.sql")
    if "FUNCTION cpsim.may_connect" not in mig or "CHECK (is_simulated = true)" not in mig:
        bad.append("M4 docs/cpsim01_part1.sql lacks cpsim.may_connect or the is_simulated CHECK")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root)
    bad, n, objs = scan(root)
    bad += markers(root)
    if not objs:
        bad.append("B0 no cpsim.* objects found in docs/cpsim*_part*.sql — the scan would be vacuous")
    for b in bad:
        print(f"VIOLATION {b}")
    print(f"CPSIM BOUNDARY: {'OK' if not bad else 'FAIL'} ({n} files scanned, {len(objs)} cpsim objects, "
          f"{len(bad)} violation(s))")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
