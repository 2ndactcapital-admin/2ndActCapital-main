"""AUTHZ1 Task 1a — every FastAPI route, its gate before and after.

    python3 apps/api/scripts/authz1_routes.py           # write docs/AUTHZ1_ROUTES.{md,json}
    python3 apps/api/scripts/authz1_routes.py --check   # exit 1 if the docs are stale

Routes come from the real app (``main.app``), flattened through the lazy
``_IncludedRouter`` wrappers (a naive ``app.routes`` walk sees only the
wrappers). Each endpoint's gate is read from its source with ``ast``: the
endpoint body plus every same-module helper it calls (three levels), and
module constants are resolved by import. "Before" is the source at
``BEFORE_REF`` (main @ a911ac7, pre-AUTHZ1); "after" is the working tree.

PRODUCTION EFFECT of a "before" gate (why a gate on paper was not a gate):
  token-claim (services.permissions)  OPEN — real tokens carry no roles /
                                       permissions claim, and absent → allow
  rbac via get_user_id                OPEN — get_user_id returned uuid5(sub),
                                       a phantom id with no roles → allow
  rbac via ensure_user                OPEN for a user with no roles
                                       (default-allow), enforced otherwise
  super admin                          enforced
  none beyond auth                     OPEN
A write whose before-effect is OPEN for a role-less caller is a [FIND].

The JSON carries, per route, the after-gate ``expect`` the verify's route
matrix asserts per persona.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
API = HERE.parent
REPO = API.parents[1]
for _site in sorted(API.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(_site))
sys.path.insert(0, str(API))

BEFORE_REF = "a911ac7"
DOC_MD = REPO / "docs" / "AUTHZ1_ROUTES.md"
DOC_JSON = REPO / "docs" / "AUTHZ1_ROUTES.json"
DOCS_ROUTES = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

# Gate stated for every write route that had none (Task 4) — the feature it
# implies. Read off the after source too; listed here so the doc says WHY.
FEATURE_GATES = {
    "routers.entities": "CRM writes → manage_members (compliance → manage_compliance; stub → manage_members|manage_portfolio)",
    "routers.entity_documents": "entity document writes → manage_documents",
    "routers.documents": "document intake → manage_documents",
    "routers.document_links": "document↔record linking + proposal decisions → manage_documents",
    "routers.document_review": "per-user document visibility (document_visible) on every route; corrections/confirm also manage_documents",
    "routers.ledger": "GL writes → manage_ledger (new permission; GL posting is never agent-reachable)",
    "routers.investment_profile": "entity access: staff any entity, member only their own (visible_entity_ids)",
    "routers.spv": "subscriptions: entity access + indicate_interest for a non-staff caller",
}
# Writes that stay "active account only", with the reason.
SELF_SCOPED = {
    "routers.assistant": "own conversation/activity (WHERE user_id = caller); each assistant ACTION carries its own gate in services.assistant_actions (unchanged — custody-cliff classes stay unreachable)",
    "routers.dashboard": "own todos (WHERE user_id = caller)",
    "routers.notifications": "own notifications",
    "routers.market_data_personal": "own saved views / custom dates",
    "routers.market_data": "POST is a read (grid/correlations compute)",
    "routers.users": "own preferences",
    "routers.enroll": "the invite token is the credential (provisioning-exempt)",
    "routers.marketing": "public by design (anonymous lead)",
    "routers.altruist_webhook": "public, HMAC-signed",
}


def _flat(routes, prefix=""):
    for r in routes:
        if hasattr(r, "original_router"):
            yield from _flat(r.original_router.routes, prefix + (r.include_context.prefix or ""))
        else:
            yield prefix, r


def app_routes():
    import main
    from fastapi.routing import APIRoute

    out = []
    for prefix, r in _flat(main.app.routes):
        path = prefix + getattr(r, "path", "")
        if not isinstance(r, APIRoute):
            out.append({"path": path, "kind": "docs" if path in DOCS_ROUTES else type(r).__name__})
            continue
        out.append({
            "path": path, "methods": sorted(set(r.methods) - {"HEAD"}),
            "module": r.endpoint.__module__, "name": r.endpoint.__name__,
        })
    return out


def _source(module: str, ref: str | None) -> str | None:
    rel = "apps/api/" + module.replace(".", "/") + ".py"
    if ref is None:
        p = REPO / rel
        return p.read_text(encoding="utf-8") if p.exists() else None
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{rel}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


_FN_CACHE: dict = {}


def _functions(module, ref):
    key = (module, ref)
    if key not in _FN_CACHE:
        src = _source(module, ref)
        fns = {}
        if src:
            tree = ast.parse(src)
            for n in tree.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fns[n.name] = ast.get_source_segment(src, n)
        _FN_CACHE[key] = fns
    return _FN_CACHE[key]


def expanded(module, name, ref):
    fns = _functions(module, ref)
    if name not in fns:
        return None
    full, seen, frontier = fns[name], {name}, [fns[name]]
    for _ in range(3):
        nxt = []
        for s in frontier:
            for call in set(re.findall(r"\b(_?\w+)\(", s)):
                if call in fns and call not in seen:
                    seen.add(call)
                    nxt.append(fns[call])
                    full += "\n" + fns[call]
        frontier = nxt
    return full


def _const(module, ident):
    try:
        m = importlib.import_module(module)
        v = getattr(m, ident, None)
        return v if isinstance(v, (str, tuple)) else None
    except Exception:
        return None


def gates(module, src: str) -> dict:
    """What gates ``src`` (expanded endpoint source) applies."""
    g = {"perms": set(), "any_of": [], "super": False, "staff": False, "token": False,
         "rbac": False, "phantom": False, "entity": False, "document": False,
         "workflow": False, "org_settings": False, "self_read_org": False}
    if src is None:
        return g
    for m in re.finditer(r"(?<![\w.])require_permission\(\s*request\s*,\s*(\"(\w+)\"|(\w+))", src):
        g["perms"].add(m.group(2) or _const(module, m.group(3)) or m.group(3))
        g["token"] = True
    for m in re.finditer(r"require_permission\(\s*pool\s*,\s*\w+\s*,\s*\w+\s*,\s*(\"(\w+)\"|(\w+))", src):
        val = m.group(2) or _const(module, m.group(3))
        if val:
            g["perms"].add(val)
        g["rbac"] = True
    for m in re.finditer(r"(?<![\w.])(_\w*gate|_require)\(\s*request\s*(?:,\s*(\"(\w+)\"|(\w+)))?\s*\)", src):
        val = m.group(3) or (_const(module, m.group(4)) if m.group(4) else None)
        if val:
            g["perms"].add(val)
        g["rbac"] = True
    if "_require_manage_members(" in src:
        g["perms"].add("manage_members"); g["rbac"] = True
    if "_require_people_access(" in src:
        g["any_of"].append(("manage_members", "manage_org_settings"))
    for m in re.finditer(r"require_any_permission\(\s*request\s*,\s*(\(([^)]*)\)|(\w+))", src):
        vals = re.findall(r"\"(\w+)\"", m.group(2) or "") or list(_const(module, m.group(3) or "") or ())
        if vals:
            g["any_of"].append(tuple(vals))
    # routers.org_settings hands the principal to services.org_settings, whose
    # write functions raise SettingsPermissionError unless can_manage_org_settings.
    if re.search(r"can_manage_org_settings|_require_write_access|_require_admin\(|principal=principal", src):
        g["org_settings"] = True; g["rbac"] = True
    if re.search(r"is_super_admin\(|_require_super_admin|require_super_admin\(", src) and not g["perms"] and not g["any_of"] and not g["org_settings"]:
        g["super"] = bool(re.search(r"_require_super_admin|require_super_admin\(|if not is_super_admin\(", src))
    if re.search(r"(?<![\w.])require_staff\(\s*request", src):
        g["staff"] = True; g["token"] = True
    if "require_entity_access(" in src or "_require_subscribe(" in src:
        g["entity"] = True
    if "_require_document_visible(" in src:
        g["document"] = True
    if re.search(r"_require_workflow_permission|_require_review_permission|_require_trigger_read", src):
        g["workflow"] = True
    if "_require_read_access(" in src:
        g["self_read_org"] = True
    if g["rbac"] and "get_user_id(request)" in src:
        g["phantom"] = True
    return g


def describe(g, after: bool = False) -> str:
    parts = []
    if g["super"]:
        parts.append("super_admin")
    if g["perms"]:
        kind = "authz" if after else ("token-claim" if g["token"] and not g["rbac"] else "rbac")
        parts.append(f"{kind} " + "+".join(sorted(g["perms"])))
    for a in g["any_of"]:
        parts.append("any(" + "|".join(a) + ")")
    if g["staff"]:
        parts.append("staff")
    if g["org_settings"]:
        parts.append("manage_org_settings (org admin)")
    if g["workflow"]:
        parts.append("workflow profile-axis")
    if g["entity"]:
        parts.append("entity access")
    if g["document"]:
        parts.append("document visibility")
    if g["self_read_org"]:
        parts.append("own org (read)")
    return "; ".join(parts) or "none beyond auth"


def before_effect(g) -> str:
    if g["super"] or g["workflow"]:
        return "enforced"
    if not (g["perms"] or g["any_of"] or g["staff"] or g["org_settings"]):
        return "OPEN"
    if g["token"] and not g["rbac"]:
        return "OPEN (token claim absent → allow)"
    if g["phantom"]:
        return "OPEN (get_user_id phantom id → no roles → allow)"
    return "OPEN for a role-less user (default-allow)"


def expect(route, g) -> dict:
    """The after-gate rule the matrix asserts. ``rule`` is one of:
    public | super | perm (all of ``perms``, or any of ``any_of``) | staff |
    org_admin | scoped (entity/document — gate passes, data 404s) | workflow |
    active (any admitted user) | mixed (several branches — only the global
    rules are asserted)."""
    if route["path"] in PUBLIC or route["path"].startswith("/debug/"):
        return {"rule": "public"}
    distinct = len(g["perms"])
    if g["super"]:
        return {"rule": "super"}
    if g["workflow"]:
        return {"rule": "workflow"}
    if g["any_of"] and not g["perms"]:
        return {"rule": "perm", "any_of": sorted({p for a in g["any_of"] for p in a})}
    if g["org_settings"] and not g["perms"]:
        return {"rule": "org_admin"}
    if distinct == 1 and not g["any_of"] and not g["org_settings"]:
        return {"rule": "perm", "any_of": sorted(g["perms"]), "scoped": g["entity"] or g["document"]}
    if distinct > 1 or (g["perms"] and (g["any_of"] or g["org_settings"])):
        return {"rule": "mixed"}
    if g["staff"]:
        return {"rule": "staff"}
    if g["entity"] or g["document"]:
        return {"rule": "scoped"}
    return {"rule": "active"}


PUBLIC: set = set()


def build():
    global PUBLIC
    import main

    PUBLIC = set(main.PUBLIC_PATHS)
    routes = app_routes()
    rows = []
    for r in routes:
        if "methods" not in r:
            continue
        before_src = expanded(r["module"], r["name"], BEFORE_REF)
        after_src = expanded(r["module"], r["name"], None)
        gb, ga = gates(r["module"], before_src), gates(r["module"], after_src)
        write = any(m != "GET" for m in r["methods"])
        public = r["path"] in PUBLIC or r["path"].startswith("/debug/")
        eff = "public" if public else ("new route" if before_src is None else before_effect(gb))
        find = write and not public and eff.startswith("OPEN")
        note = ""
        if find:
            same = bool(gb["perms"]) and gb["perms"] == ga["perms"] and not (ga["entity"] or ga["document"])
            if same:
                note = "same permission, now enforced from the DB Principal"
            elif gb["staff"] and ga["staff"]:
                note = "staff, now from DB role membership"
            else:
                note = FEATURE_GATES.get(r["module"]) or SELF_SCOPED.get(r["module"]) or ""
        elif not public and describe(ga, after=True) == "none beyond auth" and write:
            note = SELF_SCOPED.get(r["module"], "")
        rows.append({
            **r, "write": write, "public": public,
            "before": "public" if public else ("—" if before_src is None else describe(gb)),
            "before_effect": eff, "after": "public" if public else describe(ga, after=True),
            "find": find, "note": note, "expect": expect(r, ga),
        })
    return routes, rows


def render(routes, rows) -> str:
    n_docs = sum(1 for r in routes if r.get("kind") == "docs")
    finds = [r for r in rows if r["find"]]
    out = [
        "# AUTHZ1 — every API route, its gate before and after",
        "",
        f"Generated by `apps/api/scripts/authz1_routes.py` from the live app (`main.app`, "
        f"lazy routers flattened) and the endpoint source (+ same-module helpers). "
        f"Before = `{BEFORE_REF}` (pre-AUTHZ1); after = this commit.",
        "",
        f"**{len(rows)} routes** = {len(routes)} flattened app routes minus {n_docs} docs routes "
        f"(`/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc`). "
        f"**{len(finds)} writes had no effective gate in production** and are marked [FIND].",
        "",
        "Production effect of a before-gate: token-claim helpers allowed when the claim was absent "
        "(always, for real tokens); an rbac gate fed `get_user_id` got a uuid5 phantom id with no roles "
        "and default-allowed; an rbac gate fed `ensure_user` default-allowed any role-less user. "
        "After AUTHZ1 every gate reads the DB Principal (`services.authz`) with no default-allow; "
        "every authenticated route additionally requires an admitted account (pending / retired / "
        "rejected → 403) except `GET /api/v1/users/me`, and an unresolvable user is a 401.",
        "",
        "| Method | Path | Router | R/W | Gate before | Production effect before | Gate after | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["path"], x["methods"])):
        mark = "[FIND] " if r["find"] else ""
        out.append(
            f"| {','.join(r['methods'])} | `{r['path']}` | {r['module'].replace('routers.', '')} | "
            f"{'W' if r['write'] else 'R'} | {r['before']} | {r['before_effect']} | {r['after']} | "
            f"{mark}{r['note']} |"
        )
    out += ["", "## Read-side [FIND]s left for a product decision", "",
            "- CRM reads (`/entities`, `/entities/search`, `/entities/{id}`, `/full`, notes, ownership) "
            "are visible to every admitted user in the org. Writes are now gated; who may READ the CRM "
            "is a product call (members currently see the CRM link in the nav).",
            "- Ledger reads (accounts, entries, trial balance, capital accounts) are readable by any "
            "admitted user in the org; writes now need manage_ledger.",
            "- Document link reads (`/documents/{id}/links`, `/records/.../documents`, "
            "`/document-link-proposals`) and entity-document list/download have no per-user "
            "visibility filter yet (document review now does).",
            "- `resolve_tab_visibility` (UDF tabs) defaults a tab with no grant row to visible — a "
            "display default, not a capability grant; recorded, not changed.",
            ""]
    return "\n".join(out)


def main_cli() -> int:
    routes, rows = build()
    md = render(routes, rows)
    js = json.dumps({"before_ref": BEFORE_REF, "app_route_count": len(routes),
                     "docs_route_count": sum(1 for r in routes if r.get("kind") == "docs"),
                     "routes": rows}, indent=1, sort_keys=True, default=sorted) + "\n"
    if "--check" in sys.argv:
        stale = (not DOC_MD.exists() or DOC_MD.read_text(encoding="utf-8") != md
                 or not DOC_JSON.exists() or DOC_JSON.read_text(encoding="utf-8") != js)
        print("STALE" if stale else "CURRENT")
        return 1 if stale else 0
    DOC_MD.write_text(md, encoding="utf-8")
    DOC_JSON.write_text(js, encoding="utf-8")
    print(f"{len(rows)} routes, {sum(r['find'] for r in rows)} [FIND] → {DOC_MD.name}, {DOC_JSON.name}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("AUTHZ_TEST_MODE", "0")
    sys.exit(main_cli())
