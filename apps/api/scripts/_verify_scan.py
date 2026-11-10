"""_verify_scan — STATIC scan of the regression-set verifies against today's verify rules (VERIFYFIX1).

Stdlib only; reads source, never imports or runs a verify. Used by:

* ``verify_verifyfix1.py`` — the gate's static items (1)–(4);
* ``python3 apps/api/scripts/_verify_scan.py --inventory`` — the rows of docs/VERIFYFIX1_INVENTORY.md.

Rules, each by NAME (AST string constants / call names), never by exact line:

ROLES      A verify that inserts ``public.users`` rows grants roles through ``user_roles`` (rbac.grant_named_role /
           grant_org_admin / an INSERT INTO user_roles) and asserts the app decides each fixture user as meant
           (``VR.check_roles_as_meant`` — _verify_roles — or its own "decides as meant" check); it never
           writes ``UPDATE users SET role`` to change a fixture user's standing.
COUNTS     No ``count(*)`` over a LIVE-WRITTEN table (_verify_scope.LIVE_WRITTEN) without a WHERE, and no generic
           whole-table ``f"SELECT count(*) FROM {t}"`` (a generic loop goes through ``VSC.count_sql``, which scopes
           the live-written tables and keeps every other table whole).
TOTAL      ``VT.install_guard(`` is called (TOTAL on every exit path) and ``--self-checks-only`` dispatches to
           ``VS.run_self_checks`` before any heavy import.
NESTED     A verify that runs another verify parses its result with ``_verify_total`` (``nested_total`` /
           ``run_nested``) — no bespoke ``re.findall(r"^TOTAL …")``.
GUCS       Every guarded table the verify's own SQL writes (docs/VERIFYFIX1_TEARDOWN_GUARDS.json, read from the live
           pg_trigger → function bodies) has its teardown GUC named in the file (``sod.teardown`` is set by
           ``_verify_cleanup.cleanup`` for every caller).
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
REGRESSION_SET = REPO / "scripts" / "regression_set.txt"
GUARDS_JSON = REPO / "docs" / "VERIFYFIX1_TEARDOWN_GUARDS.json"

sys.path.insert(0, str(HERE))
import _verify_scope as VSC  # noqa: E402  (stdlib only)

BARE_RE = re.compile(r"^verify_[A-Za-z0-9_]+\.py$")
COUNT_RE = re.compile(r"count\(\s*\*\s*\)(?:\s+AS\s+\w+)?\s+FROM\s+((?:public\.|portfolio\.)?\w+)\b(.*)$",
                      re.I | re.S)
GENERIC_WHOLE_RE = re.compile(r"count\(\s*\*\s*\)\s+FROM\s+\{[^}]+\}\s*$", re.I)
UPDATE_ROLE_RE = re.compile(r"UPDATE\s+(?:public\.)?users\s+SET\b[^;]*?\brole\s*=", re.I | re.S)
INSERT_USERS_RE = re.compile(r"INSERT\s+INTO\s+(?:public\.)?users\s*\(", re.I)
GRANT_RE = re.compile(r"grant_named_role\s*\(|grant_org_admin\s*\(|INSERT\s+INTO\s+(?:public\.)?user_roles\b", re.I)
DECIDES_RE = re.compile(r"VR\.check_roles_as_meant\s*\(|decides as meant")
BESPOKE_TOTAL_RE = re.compile(r"re\.(?:findall|search|match)\(\s*r?[\"']\^?TOTAL")
NESTED_RUN_RE = re.compile(r"[\"'](?:verify_[A-Za-z0-9_]+\.py)[\"']")
GUC_RE = re.compile(r"[\"']([a-z_]+\.[a-z_]*teardown[a-z_]*)[\"']")
#: crm.demo_teardown guards only rows of a record tagged demo-* (CRMDETAIL1); a fixture's own rows never need it and
#: _verify_cleanup sets it LOCAL for a caller's is_test demo_fixture_orgs — not a per-verify requirement.
CONDITIONAL_GUCS = frozenset({"crm.demo_teardown"})
WRITE_RE = r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:{schema}\.)?{table}\b"


# ── the list ────────────────────────────────────────────────────────────────────────────────────────────────────

def list_entries(path: pathlib.Path = REGRESSION_SET) -> list[str]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        name = raw.split("#", 1)[0].strip()
        if name:
            out.append(name)
    return out


def list_problems(path: pathlib.Path = REGRESSION_SET, scripts_dir: pathlib.Path = HERE) -> list[str]:
    """Every entry that is not ONE bare verify_*.py name resolving to a file in scripts_dir; duplicates too."""
    bad, seen = [], set()
    for e in list_entries(path):
        if not BARE_RE.match(e):
            bad.append(f"not a bare verify_*.py name: {e!r}")
        elif not (scripts_dir / e).is_file():
            bad.append(f"no such script: {e!r}")
        if e in seen:
            bad.append(f"listed twice: {e!r}")
        seen.add(e)
    return bad


# ── one file ────────────────────────────────────────────────────────────────────────────────────────────────────

def _strings(tree: ast.AST) -> list[tuple[int, str, bool]]:
    """(line, text, is_fstring) for every string constant and every f-string (its literal parts, '{x}' for each
    expression) — implicit concatenation already joined by the parser."""
    out = []
    in_fstring = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                    in_fstring.add(id(v))
                else:
                    parts.append("{" + (ast.unparse(v.value) if isinstance(v, ast.FormattedValue) else "x") + "}")
            out.append((node.lineno, "".join(parts), True))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in in_fstring:
            out.append((node.lineno, node.value, False))
    return out


def _calls(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            try:
                names.add(ast.unparse(node.func))
            except Exception:  # noqa: BLE001
                pass
    return names


def _guards() -> dict[str, list[str]]:
    try:
        return json.loads(GUARDS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def scan_file(path: pathlib.Path) -> dict:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    strings = _strings(tree)
    calls = _calls(tree)
    r: dict = {"name": path.name, "lines": src.count("\n") + 1}

    # ROLES
    inserts_users = any(INSERT_USERS_RE.search(s) for _, s, _ in strings)
    grants = any(GRANT_RE.search(s) for _, s, _ in strings) or any(
        c.endswith(("grant_named_role", "grant_org_admin")) for c in calls)
    decides = bool(DECIDES_RE.search(src))
    upd = sorted({ln for ln, s, _ in strings if UPDATE_ROLE_RE.search(s)})
    r["inserts_users"] = inserts_users
    r["role_writes"] = upd
    if upd:
        r["roles"] = "users.role"
    elif not inserts_users:
        r["roles"] = "none"
    elif grants:
        r["roles"] = "user_roles"
    else:
        r["roles"] = "users.role"
    r["decides_as_meant"] = decides
    r["roles_ok"] = not upd and (not inserts_users or (grants and decides))

    # COUNTS
    unscoped = []
    for ln, s, is_f in strings:
        if GENERIC_WHOLE_RE.search(s.strip().rstrip('"')):
            unscoped.append((ln, "generic f\"SELECT count(*) FROM {t}\""))
            continue
        for m in COUNT_RE.finditer(s):
            table, rest = m.group(1), m.group(2)
            if VSC.is_live_written(table) and not re.match(r"\s*(?:\w+\s+)?(?:WHERE|JOIN|,)", rest, re.I):
                unscoped.append((ln, VSC.qualify(table)))
    r["unscoped_counts"] = sorted(set(unscoped))
    # the gate: no generic whole-table loop and no unscoped count of the three tables verify_verifyfix1 (3) names;
    # any other live-written table counted whole is reported (inventory) — e.g. verify_s28's deliberate read that the
    # live decision log is NON-EMPTY, which is not a before/after comparison.
    r["unscoped_gated"] = [u for u in r["unscoped_counts"] if u[1].startswith("generic") or u[1] in VSC.GATED]
    r["counts_ok"] = not r["unscoped_gated"]
    r["uses_scope_helper"] = "VSC.count_sql" in src or "VSC.predicate" in src

    # TOTAL / self-checks
    r["self_checks"] = ("VS.requested()" in src or "VS.SELF_CHECKS_FLAG in sys.argv" in src) and \
        "VS.run_self_checks(" in src
    r["total_guard"] = "VT.install_guard(" in src
    r["cleanup_only"] = "--cleanup-only" in src or "CLEANUP_FLAG" in src

    # NESTED
    nested_targets = sorted({m.group(0).strip("\"'") for m in NESTED_RUN_RE.finditer(src)} - {path.name})
    runs_nested = bool(re.search(r"run_cmd\(|subprocess\.run\(|VT\.run_nested\(", src)) and bool(nested_targets)
    r["runs_nested"] = runs_nested
    r["bespoke_total_parse"] = bool(BESPOKE_TOTAL_RE.search(src))
    r["nested_helper"] = any(k in src for k in ("VT.nested_total(", "VT.run_nested(", "VT.final_total(", "nested_total("))
    r["nested_ok"] = not r["bespoke_total_parse"] and (not runs_nested or r["nested_helper"])
    r["timeouts"] = sorted({int(x) for x in re.findall(r"timeout\s*=\s*(\d{3,5})", src)})

    # GUCS
    named = set(GUC_RE.findall(src))
    if "VC.cleanup(" in src:
        named.add("sod.teardown")
    required: dict[str, set] = {}
    for t, gucs in _guards().items():
        schema, table = t.split(".", 1)
        rx = WRITE_RE.format(schema=re.escape(schema), table=re.escape(table))
        if any(re.search(rx, s, re.I) for _, s, _ in strings):
            for g in gucs:
                if g not in CONDITIONAL_GUCS:
                    required.setdefault(g, set()).add(t)
    r["gucs_named"] = sorted(named)
    r["gucs_missing"] = {g: sorted(ts) for g, ts in sorted(required.items()) if g not in named}
    return r


def scan_set(path: pathlib.Path = REGRESSION_SET, scripts_dir: pathlib.Path = HERE) -> list[dict]:
    out = []
    for e in list_entries(path):
        p = scripts_dir / pathlib.Path(e).name
        out.append(scan_file(p) if p.is_file() else {"name": e, "missing": True})
    return out


def _cell(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "**no**"
    if isinstance(v, (list, tuple, set)):
        return ", ".join(str(x) for x in v) or "—"
    if isinstance(v, dict):
        return "; ".join(f"{k}: {', '.join(t)}" for k, t in v.items()) or "—"
    return str(v)


if __name__ == "__main__":
    rows = scan_set()
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=1, default=list))
    else:
        print("| verify | roles | decides-as-meant | unscoped counts | missing teardown GUCs | --self-checks-only | "
              "TOTAL guard | nested TOTAL | timeouts |")
        print("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            if r.get("missing"):
                print(f"| {r['name']} | MISSING | | | | | | | |")
                continue
            uc = sorted({t for _, t in r["unscoped_counts"]})
            print(f"| {r['name']} | {r['roles']} | {_cell(r['decides_as_meant'])} | {_cell(uc)} | "
                  f"{_cell(r['gucs_missing'])} | {_cell(r['self_checks'])} | {_cell(r['total_guard'])} | "
                  f"{'helper' if r['nested_helper'] and not r['bespoke_total_parse'] else ('bespoke' if r['bespoke_total_parse'] else '—')} | "
                  f"{_cell(r['timeouts'])} |")
