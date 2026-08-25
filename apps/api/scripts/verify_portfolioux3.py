"""Verification — Portfolio UX 3: the Securities & Assets grid, two scopes.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END with an
EXACT before/after count on every table touched — never a truncate, because
these tables hold real rows (67 global securities, 64 identifiers).

Real database, real ASGI app, real non-bypass ``app_service`` role. The harness
is UX 1's and UX 2's, deliberately: a third, differently-shaped harness for the
same kind of sprint would be a third thing to keep honest.

────────────────────────────────────────────────────────────────────────────
THE ASSERTIONS THIS SPRINT IS EASIEST TO FAKE, AND HOW THEY ARE WRITTEN
────────────────────────────────────────────────────────────────────────────
**"A view-only user is refused."** An endpoint that refuses EVERYBODY passes
this trivially. So every refusal is paired with a CONTROL: the same call, same
body, same row, made by a user who does hold the permission, asserted to
SUCCEED. A gate that is merely broken fails the control.

**"A view-only user has no roles, so the check is vacuous."**
``rbac.has_permission`` DEFAULT-ALLOWS a user with zero rows in ``user_roles``
(single-admin stage). A fixture user with no role therefore has
``manage_portfolio``. Both write-tier fixtures are given REAL deployed roles —
``member`` (view only) and ``admin`` (both) — read out of ``role_permissions``
rather than assumed, and the script ASSERTS the resulting permission sets before
using them. Without that, "view-only user refused" would be testing nothing.

**"The org admin cannot edit a global field."** Two claims, checked
INDEPENDENTLY, because they can fail separately:
  · SERVER — a direct PATCH naming a global field is 403, and a 422 is a FAIL,
    not a pass: 422 would mean the field was refused as junk rather than as
    platform data, which is the wrong refusal reaching the wrong log.
  · UI — the component's editable-field source is the server envelope, the
    envelope's ``editable`` list is asserted DISJOINT from ``global_fields``,
    and the pane is asserted to have no editable branch in its platform subtree.

**"Only a super admin reaches the global write path."** Asserted in both
directions on the SAME call: the org admin gets 403 AND the super admin gets
201/200 on the identical request. A path that 500s for everyone would otherwise
pass the negative half.

**"Cross-org isolation."** An endpoint that returns nothing for everybody
passes an "org B cannot see org A" check. Both directions are asserted against
the SAME call, at the endpoint layer AND at the RLS layer on a real
non-bypassing ``app_service`` connection, with a positive control proving the
session cannot bypass RLS.

**"The global tables are protected by RLS."** The application connects as
``postgres``, which carries ``rolbypassrls`` — so asserting the policy under the
app's own connection proves nothing at all. The global-write RLS assertions run
under ``app_service`` and are reported as the BACKSTOP they are, with the
app-layer gate reported as the operative one.

Run:
    python3 scripts/verify_portfolioux3.py
"""

from __future__ import annotations

import ast
import asyncio
import glob
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

_HERE = os.path.dirname(os.path.abspath(__file__))
_API = os.path.join(_HERE, "..")
_WEB = os.path.join(_HERE, "..", "..", "web")
sys.path.insert(0, _API)
sys.path.extend(sorted(glob.glob(
    os.path.join(_API, "venv", "lib", "python3*", "site-packages")
)))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_API, ".env"), override=False)

from services.portfolio_assets import (  # noqa: E402
    TABLE_ASSET_IDENT,
    TABLE_ASSETS,
    TABLE_POSITIONS,
    TABLE_VALUATIONS,
    add_identifier as add_asset_identifier,
    create_position,
    record_valuation,
)
from services.portfolio_securities import (  # noqa: E402
    GLOBAL_SOURCED_FIELDS,
    GLOBAL_TABLE_COLUMNS,
    INLINE_EDITABLE_FIELDS,
    ORG_EDITABLE_FIELDS,
    READ_PERMISSION,
    RECORD_TYPE_ASSET,
    TABLE_DOC_RECORD_LINKS,
    WRITE_PERMISSION,
    GlobalFieldError,
    create_tenant_asset,
    get_asset,
    list_assets,
    update_asset,
)
from services.securities_global import (  # noqa: E402
    SECURITY_EDITABLE_FIELDS,
    TABLE_IDENT as TABLE_SEC_IDENT,
    TABLE_PRICE as TABLE_SEC_PRICE,
    TABLE_SEC,
    SecuritiesGlobalPermissionError,
    add_identifier as add_global_identifier,
    add_price,
    create_security,
    update_security,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org. A real row, not a minted one — an isolation test against
# an org that does not exist proves the FK, not the policy.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "VERIFY-PORTFOLIOUX3"

A_SUB = "auth0|verify_portfolioux3_admin"       # org A, manage_portfolio
V_SUB = "auth0|verify_portfolioux3_viewonly"    # org A, view_portfolio ONLY
S_SUB = "auth0|verify_portfolioux3_superadmin"  # users.role = 'super_admin'
B_SUB = "auth0|verify_portfolioux3_orgb"        # org B, manage_portfolio

# `services.permissions.get_user_id` DERIVES the id from the sub rather than
# looking it up, so a fixture seeded under a hand-picked literal is a user no
# code path ever finds (Portfolio C's finding).
A_USER_ID = str(uuid5(NAMESPACE_URL, A_SUB))
V_USER_ID = str(uuid5(NAMESPACE_URL, V_SUB))
S_USER_ID = str(uuid5(NAMESPACE_URL, S_SUB))
B_USER_ID = str(uuid5(NAMESPACE_URL, B_SUB))

TODAY = date(2026, 8, 25)

# Exact figures. Chosen so LEXICAL and NUMERIC order DISAGREE:
# "1200000.00" < "45000.00" < "900.00" as strings; 900 < 45000 < 1200000.
EQUITY_VALUE = Decimal("1200000.00")
PROPERTY_VALUE = Decimal("45000.00")
SMALL_VALUE = Decimal("900.00")
LISTED_PRICE = Decimal("128.4500")

results: list[tuple[str, bool, str]] = []
findings: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def report(name: str, detail: str) -> None:
    """A Task 1 finding. Printed as a FINDING, never silently as a PASS."""
    findings.append(name)
    print(f"[FIND] {name}\n       {detail}")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def strip_docstrings(src: str) -> str:
    """Executable code only. Docstrings and comments removed.

    Only ever used to make an ABSENCE assertion stricter — a module that
    EXPLAINS a rule in prose must not flag its own explanation, which is the
    false positive that trains the next person to delete the check rather than
    the bug. ``ast.get_docstring`` dedents by default, so ``clean=False`` is
    required or the replace silently matches nothing.
    """
    tree = ast.parse(src)
    out = src
    docs = [ast.get_docstring(tree, clean=False)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            docs.append(ast.get_docstring(node, clean=False))
    for d in docs:
        if d:
            out = out.replace(d, "")
    return re.sub(r"(?m)^\s*#.*$", "", out)


# ── Tables, in FK-safe teardown order (children first) ──────────────────────
TABLES = (
    TABLE_DOC_RECORD_LINKS,
    "public.documents",
    TABLE_POSITIONS,
    TABLE_VALUATIONS,
    TABLE_ASSET_IDENT,
    TABLE_ASSETS,
    TABLE_SEC_PRICE,
    TABLE_SEC_IDENT,
    TABLE_SEC,
    "public.entities",
    "public.user_roles",
    "public.users",
)


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def teardown(conn) -> None:
    """Delete every fixture row, children first. Touches nothing else.

    Matched through the TAGGED asset / security / entity / document names, never
    by org — org A is a real production org and org B is Hollisworks, and both
    are full of real rows. ``portfolio.securities_global`` in particular holds
    the 67-row live EDGAR corpus; a truncate here would destroy it.

    Assets are deleted LAST among the portfolio tables because positions,
    valuations and identifiers all carry a foreign key to ``assets.id``, and
    securities_global last of all because ``assets.global_security_id``
    references it — the archived system-axis versions carry that FK too, which
    is exactly the kind of row a delete ordered by intuition rather than by the
    constraint graph leaves behind.
    """
    tagged_assets = f"SELECT id FROM {TABLE_ASSETS} WHERE name LIKE '{TAG}%'"
    tagged_secs = f"SELECT id FROM {TABLE_SEC} WHERE name LIKE '{TAG}%'"
    tagged_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({tagged_assets})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_DOC_RECORD_LINKS} "
        f"WHERE record_id IN ({tagged_assets}) "
        f"   OR record_id IN ({tagged_positions}) "
        f"   OR document_id IN (SELECT id FROM public.documents "
        f"                      WHERE original_filename LIKE '{TAG}%')"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN ({tagged_assets})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_VALUATIONS} WHERE asset_id IN ({tagged_assets})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_ASSET_IDENT} WHERE asset_id IN ({tagged_assets})"
    )
    await conn.execute(f"DELETE FROM {TABLE_ASSETS} WHERE name LIKE '{TAG}%'")
    await conn.execute(
        f"DELETE FROM {TABLE_SEC_PRICE} WHERE global_security_id IN ({tagged_secs})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_SEC_IDENT} WHERE global_security_id IN ({tagged_secs})"
    )
    await conn.execute(f"DELETE FROM {TABLE_SEC} WHERE name LIKE '{TAG}%'")
    await conn.execute(
        "DELETE FROM public.documents WHERE original_filename LIKE $1", f"{TAG}%"
    )
    await conn.execute(
        "DELETE FROM public.entities WHERE display_name LIKE $1", f"{TAG}%"
    )
    subs = [A_SUB, V_SUB, S_SUB, B_SUB]
    await conn.execute(
        "DELETE FROM public.user_roles WHERE user_id IN "
        "(SELECT id FROM public.users WHERE auth0_sub = ANY($1::text[]))",
        subs,
    )
    await conn.execute(
        "DELETE FROM public.users WHERE auth0_sub = ANY($1::text[])", subs
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the FIVE findings, REPORTED and ASSERTED
# ═══════════════════════════════════════════════════════════════════════════


def _repo_python_sources() -> dict[str, str]:
    """Every .py file under apps/api that is not a verify script."""
    out = {}
    for path in glob.glob(os.path.join(_API, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, _API)
        if rel.startswith(("venv", "scripts")) or "__pycache__" in rel:
            continue
        out[rel] = read(path)
    return out


def check_task1a(routes: dict) -> None:
    """1a — what REST surface existed for assets / securities_global BEFORE."""
    sources = _repo_python_sources()

    # The one pre-existing asset endpoint, and where it lives.
    positions_router = sources.get("routers/portfolio_positions.py", "")
    had_picker = '@router.get("/portfolio/assets")' in positions_router

    # Nothing has ever routed the global master. `pricing_admin` touches the
    # NOTE TERMS and RELATIONSHIP satellites; the security rows, identifiers
    # and prices have had no HTTP surface at all.
    global_writers = sorted(
        rel for rel, src in sources.items()
        if rel.startswith("routers/")
        and rel != "routers/portfolio_securities.py"
        and re.search(r"create_security|add_price\(|add_identifier\(|update_security",
                      strip_docstrings(src))
    )

    report(
        "TASK 1a — the REST surface that existed before this sprint",
        f"portfolio.assets had EXACTLY ONE endpoint: GET /portfolio/assets, "
        f"declared in routers/portfolio_positions.py as a nine-column picker "
        f"for the create-position form (present={had_picker}). No detail, no "
        f"create, no edit, no global-security join. It is left untouched — UX 1 "
        f"depends on its shape — and this sprint's grid lives at "
        f"/portfolio/securities instead.\n"
        f"       portfolio.securities_global and its identifier / price / "
        f"relationship satellites had NO REST surface whatsoever. "
        f"services/securities_global.py shipped in Portfolio A1 (834 lines) and "
        f"has never had a router; routers/pricing_admin.py touches "
        f"securities_global_note_terms and the relationship review queue, never "
        f"the security rows, identifiers or prices. Routers other than this "
        f"sprint's that call a global writer: {global_writers or 'none'}.",
    )
    check(
        "[Y] 1a the pre-existing picker at GET /portfolio/assets is still "
        "declared in routers/portfolio_positions.py and was NOT repurposed — "
        "UX 1 reads its {count, assets} shape",
        had_picker and "/api/v1/portfolio/assets" in routes,
        f"picker present={had_picker}",
    )
    check(
        "[Y] 1a this sprint's router is the ONLY one that reaches a "
        "securities_global writer — the global master had, and still has, "
        "exactly one door",
        not global_writers,
        f"other routers calling a global writer: {global_writers or 'none'}",
    )


def check_task1b() -> None:
    """1b — this sprint mirrors UX 1 / UX 2's REAL shipped shape."""
    svc = read(os.path.join(_API, "services", "portfolio_securities.py"))
    rtr = read(os.path.join(_API, "routers", "portfolio_securities.py"))
    ux1_svc = read(os.path.join(_API, "services", "portfolio_positions.py"))
    ux2_rtr = read(os.path.join(_API, "routers", "portfolio_transactions.py"))

    report(
        "TASK 1b — the UX 1 / UX 2 shape, read from the shipped code and reused",
        "Mirrored: (a) the vocabularies envelope shipped WITH the page so a "
        "cell can render a label and a picker without a second round-trip; "
        "(b) server-published inline-editable / editable lists the component "
        "honours instead of keeping its own copy; (c) the mode='before' float "
        "refusal, which is dead code written any later because Decimal is in "
        "the field union and lax mode accepts a float into it; (d) the "
        "DocumentsPanel embedded with a record_type the API supplies, because "
        "document_record_links.record_type has no CHECK constraint; (e) "
        "money serialised as exact decimal STRINGS; (f) an absence carried as "
        "None WITH A REASON, never zero; (g) the import-time guard that fails "
        "the build when the Pydantic model and the service's field set drift. "
        "DIVERGED, deliberately and for a reason recorded in the module "
        "docstring: an asset edit archives on the SYSTEM axis and KEEPS ITS ID, "
        "because three deployed FKs reference assets.id — the UX 1 valid-axis "
        "restatement would orphan every position and valuation of the asset.",
    )
    check(
        "[Y] 1b the service publishes the same envelope UX 1 does — "
        "vocabularies, taxonomy resolved server-side from config (Rule 1), and "
        "an editable/inline-editable pair",
        all(k in rtr for k in ('"vocabularies"', '"inline_editable"', '"editable"'))
        and "taxonomy_labels" in svc
        and "asset_taxonomy" in svc,
        "vocabularies + taxonomy_labels + config-sourced labels",
    )
    check(
        "[Y] 1b the float refusal runs mode='before', exactly as UX 2's does — "
        "written any later it never fires",
        'field_validator("price", mode="before")' in rtr
        and 'mode="before"' in ux2_rtr,
        "mode='before' on the money field",
    )
    check(
        "[Y] 1b the DocumentsPanel record_type is emitted by the API, not "
        "hardcoded in the component (UX 1's pattern)",
        "RECORD_TYPE_ASSET" in svc and '"document_record_type"' in svc,
        f"record_type = {RECORD_TYPE_ASSET!r}",
    )
    check(
        "[Y] 1b the divergence from UX 1 is DOCUMENTED where a reader will "
        "hit it, not left to be discovered — UX 1 restates on the valid axis "
        "and mints a new id; this one cannot",
        "SYSTEM" in svc and "_archive_asset_version" in svc
        and "update_position" in svc
        and "valid axis" in ux1_svc.lower() or "restatement" in ux1_svc,
        "module docstring explains the axis choice and names UX 1",
    )


def check_task1c(role_perms: dict[str, set[str]]) -> None:
    """1c — the REAL permission vocabulary, read from the deployed database."""
    view_roles = sorted(r for r, p in role_perms.items() if READ_PERMISSION in p)
    write_roles = sorted(r for r, p in role_perms.items() if WRITE_PERMISSION in p)
    read_only_roles = sorted(set(view_roles) - set(write_roles))

    report(
        "TASK 1c — the tenant permission vocabulary is UX 1 / UX 2's, unchanged",
        f"view_portfolio and manage_portfolio both exist in public.permissions "
        f"and are the SAME two names services/portfolio_positions.py and "
        f"services/portfolio_transactions.py declare. No new permission is "
        f"introduced by this sprint.\n"
        f"       Deployed grants: view_portfolio → {view_roles}; "
        f"manage_portfolio → {write_roles}. Read-only roles (view without "
        f"manage): {read_only_roles} — so 'can read but cannot write' is a real "
        f"reachable state, not one this test has to manufacture.\n"
        f"       THE TRAP: services.rbac.has_permission DEFAULT-ALLOWS a user "
        f"with ZERO rows in user_roles (single-admin stage). A 'view-only' "
        f"fixture with no role assigned would therefore hold manage_portfolio, "
        f"and every write-refusal assertion below would pass vacuously in the "
        f"wrong direction. Both fixtures are given real deployed roles and "
        f"their effective permission sets are asserted before use.",
    )
    check(
        "[Y] 1c both permission names already existed — this screen invents "
        "neither, and the read-only state is reachable with a deployed role",
        READ_PERMISSION == "view_portfolio"
        and WRITE_PERMISSION == "manage_portfolio"
        and bool(read_only_roles) and "member" in read_only_roles
        and "admin" in write_roles,
        f"read-only roles={read_only_roles}, write roles={write_roles}",
    )


def check_task1d(policies: list[dict], app_role: str, bypasses: bool) -> None:
    """1d — the REAL Super-Admin-vs-org-write mechanism, all three layers."""
    svc = read(os.path.join(_API, "services", "portfolio_securities.py"))
    rtr = read(os.path.join(_API, "routers", "portfolio_securities.py"))
    a1 = read(os.path.join(_API, "services", "securities_global.py"))
    pricing = read(os.path.join(_API, "routers", "pricing_admin.py"))

    by_table: dict[str, list[str]] = {}
    for p in policies:
        by_table.setdefault(p["tablename"], []).append(f"{p['cmd']}")
    global_tables = sorted(t for t in by_table if t.startswith("securities_global"))
    tenant_tables = sorted(t for t in by_table if t in ("assets", "asset_identifiers"))

    report(
        "TASK 1d — the deployed mechanism that separates a Super-Admin write "
        "from an org write, reused verbatim",
        f"THREE layers, and this sprint adds none of its own:\n"
        f"       1. APP — rbac.load_principal + rbac.is_super_admin (reads "
        f"users.role == 'super_admin') → HTTPException 403. The identical "
        f"helper shape routers/pricing_admin.py already uses "
        f"(_require_super_admin present={'_require_super_admin' in pricing}).\n"
        f"       2. SERVICE — securities_global._require_super_admin(...) then "
        f"_SuperAdminWrite, which raises app.is_super_admin for exactly ONE "
        f"transaction via SET LOCAL. This protects the service from a future "
        f"caller that is not this router.\n"
        f"       3. DATABASE — the deployed RLS. Global tables {global_tables} "
        f"carry FOUR policies each: SELECT USING (true), and INSERT/UPDATE/"
        f"DELETE gated on app.is_super_admin. Tenant tables {tenant_tables} "
        f"carry ONE cmd=ALL org-isolation policy. The shapes are INVERTED, and "
        f"that is the whole boundary.\n"
        f"       THE CAVEAT WORTH WRITING DOWN: the application connects as "
        f"{app_role!r}, rolbypassrls={bypasses}. So in production layers 1 and "
        f"2 are the OPERATIVE gates and layer 3 is the backstop that catches a "
        f"direct or mis-roled connection. Every RLS assertion in this script "
        f"therefore runs under a real non-bypassing app_service connection — "
        f"asserting it under the app's own connection would prove nothing.",
    )
    check(
        "[Y] 1d the router reuses A1's real pattern — load_principal + "
        "is_super_admin at the app layer, is_super_admin=True passed EXPLICITLY "
        "to the service, never inferred from the request",
        "load_principal" in rtr and "is_super_admin(principal)" in rtr
        and "is_super_admin=True" in rtr
        and "_require_super_admin" in a1 and "_SuperAdminWrite" in a1,
        "all three names present in the router; A1's guards untouched",
    )
    check(
        "[Y] 1d the deployed RLS shapes really are inverted: each global table "
        "has a SELECT-USING-true policy plus super-admin INSERT/UPDATE/DELETE, "
        "while each tenant table has exactly ONE cmd=ALL org-isolation policy",
        all(sorted(by_table[t]) == ["DELETE", "INSERT", "SELECT", "UPDATE"]
            for t in global_tables)
        and all(by_table[t] == ["ALL"] for t in tenant_tables)
        and len(global_tables) >= 3 and len(tenant_tables) == 2,
        f"global={ {t: sorted(by_table[t]) for t in global_tables} } "
        f"tenant={ {t: by_table[t] for t in tenant_tables} }",
    )
    check(
        "[Y] 1d this sprint's service does NOT open a second door into the "
        "global tables — every global WRITE still goes through "
        "services.securities_global, which is the only place that elevates",
        "_SuperAdminWrite" not in strip_docstrings(svc)
        and "set_config('app.is_super_admin'" not in strip_docstrings(svc)
        and "is_super_admin" not in strip_docstrings(svc),
        "portfolio_securities.py never elevates and never sets the GUC",
    )


def check_task1e(asset_cols: list[str], global_cols: dict[str, list[str]]) -> None:
    """1e — which fields originate in WHICH table, introspected exactly."""
    collisions = sorted(set(asset_cols) & set(global_cols["securities_global"]))

    report(
        "TASK 1e — the field-origin split, introspected from the deployed schema",
        f"TENANT — portfolio.assets ({len(asset_cols)} cols, org_id present, "
        f"org-editable): {asset_cols}.\n"
        f"       TENANT — portfolio.asset_identifiers: "
        f"{global_cols['asset_identifiers']} — the org's OWN identifiers, whose "
        f"CHECK admits 'parcel' and 'vin', which the global constraint does "
        f"NOT.\n"
        f"       GLOBAL — portfolio.securities_global (NO org_id): "
        f"{global_cols['securities_global']}.\n"
        f"       GLOBAL — securities_global_identifiers: "
        f"{global_cols['securities_global_identifiers']}.\n"
        f"       GLOBAL — securities_global_prices: "
        f"{global_cols['securities_global_prices']}.\n"
        f"       THE PUNCHLINE: {collisions} exist on BOTH tables. On the asset "
        f"they are org-owned and editable; on the security they are platform "
        f"data and are not writable from this screen by anyone. A joined row "
        f"that emitted a bare `name` for each would be a screen where the "
        f"difference between a legal edit and an illegal one is which of two "
        f"identically-labelled boxes the user clicked. Every global-sourced "
        f"value therefore leaves the API under a global_-prefixed key "
        f"(GLOBAL_SOURCED_FIELDS), and the org write path refuses BOTH those "
        f"keys and the raw global column names (GLOBAL_TABLE_COLUMNS).",
    )
    check(
        "[Y] 1e the collision is real and is what the prefix exists for — "
        "name / short_name / currency_code are on BOTH tables",
        set(collisions) >= {"name", "short_name", "currency_code"},
        f"collisions={collisions}",
    )
    check(
        "[Y] 1e every org-editable field is a REAL column of portfolio.assets "
        "— introspected, not inferred from the sprint prompt",
        ORG_EDITABLE_FIELDS <= set(asset_cols),
        f"not columns: {sorted(ORG_EDITABLE_FIELDS - set(asset_cols)) or 'none'}",
    )
    check(
        "[Y] 1e the org-editable set and the platform-sourced set are DISJOINT "
        "— a field cannot be both org-writable and platform-read-only, and "
        "which one won would otherwise depend on check order",
        not (ORG_EDITABLE_FIELDS & GLOBAL_SOURCED_FIELDS)
        and not (ORG_EDITABLE_FIELDS & GLOBAL_TABLE_COLUMNS),
        f"overlap={sorted(ORG_EDITABLE_FIELDS & (GLOBAL_SOURCED_FIELDS | GLOBAL_TABLE_COLUMNS)) or 'none'}",
    )
    check(
        "[Y] 1e GLOBAL_TABLE_COLUMNS covers the raw column names a caller "
        "would actually try — every distinctive column of the three global "
        "tables is refused by name",
        {"security_type", "price_coverage", "id_type", "id_value", "price",
         "price_date", "canonical_id", "merged_into_id"} <= GLOBAL_TABLE_COLUMNS,
        f"{sorted(GLOBAL_TABLE_COLUMNS)}",
    )
    check(
        "[Y] 1e the inline-editable set is a strict subset of the org-editable "
        "set, and contains nothing a cross-field rule could refuse — a refusal "
        "surfacing as a cell snapping back is worse than no inline edit",
        INLINE_EDITABLE_FIELDS < ORG_EDITABLE_FIELDS
        and not (INLINE_EDITABLE_FIELDS & {
            "name", "asset_type", "asset_class", "ownership_basis",
            "valuation_method", "currency_code", "is_active",
        }),
        f"inline={sorted(INLINE_EDITABLE_FIELDS)} "
        f"pane-only={sorted(ORG_EDITABLE_FIELDS - INLINE_EDITABLE_FIELDS)}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def seed_users(conn) -> None:
    """Four principals covering every cell of the two-boundary matrix.

    ``users.role`` is what ``rbac.is_super_admin`` reads (NOT ``user_roles``);
    ``user_roles`` is what ``rbac.get_user_permissions`` reads. They are
    different systems and this sprint needs both, so each fixture sets each
    deliberately rather than relying on one to imply the other.
    """
    for user_id, org, sub, role, role_name in (
        (A_USER_ID, DEFAULT_ORG_ID, A_SUB, "member", "admin"),
        (V_USER_ID, DEFAULT_ORG_ID, V_SUB, "member", "member"),
        (S_USER_ID, DEFAULT_ORG_ID, S_SUB, "super_admin", "member"),
        (B_USER_ID, OTHER_ORG_ID, B_SUB, "member", "admin"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users
                (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioUX3', $4, $5, true)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, org, f"{sub.split('|')[-1]}@test.local", sub, role,
        )
        # A REAL role grant. Without one, rbac.has_permission default-allows and
        # the view-only fixture would silently hold manage_portfolio.
        await conn.execute(
            """
            INSERT INTO public.user_roles (user_id, role_id)
            SELECT $1::uuid, r.id FROM public.roles r WHERE r.name = $2
            ON CONFLICT DO NOTHING
            """,
            user_id, role_name,
        )


async def seed(conn) -> dict:
    """Two orgs, two global securities, four assets, positions, valuations."""
    ids: dict = {}

    async def entity(org, name, etype="trust"):
        return str(await conn.fetchval(
            "INSERT INTO public.entities (org_id, entity_type, display_name) "
            "VALUES ($1::uuid, $2::entity_type, $3) RETURNING id",
            org, etype, name,
        ))

    ids["owner_a"] = await entity(DEFAULT_ORG_ID, f"{TAG} Alpha Trust")
    ids["owner_b"] = await entity(OTHER_ORG_ID, f"{TAG} OtherOrg LLC", "llc")

    # ── The GLOBAL master. Super-Admin-only writes, exercised as such. ──
    ids["sec_equity"] = await create_security(
        conn, name=f"{TAG} Listed Equity Inc", security_type="equity",
        short_name=f"{TAG}-EQ", currency_code="USD",
        price_coverage="has_series", is_super_admin=True,
    )
    # A structured note, so the price refusal has something real to refuse.
    ids["sec_note"] = await create_security(
        conn, name=f"{TAG} Autocallable Note", security_type="structured_note",
        currency_code="USD", price_coverage="no_public_source",
        is_super_admin=True,
    )
    await add_global_identifier(
        conn, global_security_id=ids["sec_equity"], id_type="cusip",
        id_value="9VX3UX3001", is_primary=True, is_super_admin=True,
    )
    await add_global_identifier(
        conn, global_security_id=ids["sec_equity"], id_type="ticker",
        id_value="ux3eq", is_super_admin=True,
    )
    await add_price(
        conn, global_security_id=ids["sec_equity"],
        price_date=TODAY - timedelta(days=1), price=LISTED_PRICE,
        currency_code="USD", price_type="close", source=TAG,
        is_super_admin=True,
    )

    # ── The TENANT assets. ─────────────────────────────────────────────
    ids["asset_linked"] = await create_tenant_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Linked Equity Holding",
        asset_type="equity", asset_class="financial", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
        global_security_id=ids["sec_equity"],
        default_taxonomy_key="taxonomy_sc_1",
    )
    ids["asset_note"] = await create_tenant_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Linked Note Holding",
        asset_type="structured_note", asset_class="financial",
        ownership_basis="units", valuation_method="mark_to_model",
        currency_code="USD", global_security_id=ids["sec_note"],
    )
    # UNLINKED, on purpose. `global_security_id` is nullable by design (A2) and
    # a property has no global counterpart; a fixture that linked everything
    # would never exercise the LEFT JOIN's null half.
    ids["asset_property"] = await create_tenant_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Ranch Property",
        asset_type="real_estate", asset_class="hard_asset",
        ownership_basis="value", valuation_method="appraisal",
        currency_code="USD",
    )
    ids["asset_b"] = await create_tenant_asset(
        conn, org_id=OTHER_ORG_ID, name=f"{TAG} OtherOrg Asset",
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
        global_security_id=ids["sec_equity"],
    )

    # An org-owned identifier the GLOBAL constraint would refuse — the point of
    # the two identifier tables being separate.
    await add_asset_identifier(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["asset_property"],
        id_type="parcel", id_value="APN-0042-118", is_primary=True,
    )

    await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["asset_linked"],
        valuation_date=TODAY - timedelta(days=2), value=EQUITY_VALUE,
        value_basis="total", status="final", purpose="market",
        currency_code="USD",
    )
    await record_valuation(
        conn, org_id=DEFAULT_ORG_ID, asset_id=ids["asset_property"],
        valuation_date=TODAY - timedelta(days=30), value=PROPERTY_VALUE,
        value_basis="total", status="audited", purpose="market",
        currency_code="USD",
    )
    await record_valuation(
        conn, org_id=OTHER_ORG_ID, asset_id=ids["asset_b"],
        valuation_date=TODAY, value=SMALL_VALUE, value_basis="total",
        status="final", purpose="market", currency_code="USD",
    )
    # `asset_note` deliberately gets NO valuation — the em-dash-with-a-reason
    # path needs a row that genuinely has nothing to resolve.

    ids["pos_a"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a"],
        asset_id=ids["asset_linked"], as_of_date=TODAY, authority="custodial",
        source_system="reporting_tool_bd", ownership_basis="units",
        quantity=Decimal("500.00"), taxonomy_key="taxonomy_sc_1",
    )
    ids["pos_b"] = await create_position(
        conn, org_id=OTHER_ORG_ID, owner_entity_id=ids["owner_b"],
        asset_id=ids["asset_b"], as_of_date=TODAY, authority="custodial",
        source_system="manual", ownership_basis="units",
        quantity=Decimal("7.00"),
    )

    # One document, linked to the asset via the existing generic mechanism.
    ids["document"] = str(await conn.fetchval(
        """
        INSERT INTO public.documents
            (org_id, original_filename, source, mime_type, status, doc_family)
        VALUES ($1::uuid, $2, 'upload', 'application/pdf', 'confirmed',
                'statement')
        RETURNING id
        """,
        DEFAULT_ORG_ID, f"{TAG} prospectus.pdf",
    ))
    await conn.execute(
        f"""
        INSERT INTO {TABLE_DOC_RECORD_LINKS}
            (document_id, org_id, record_type, record_id)
        VALUES ($1::uuid, $2::uuid, $3, $4::uuid)
        ON CONFLICT DO NOTHING
        """,
        ids["document"], DEFAULT_ORG_ID, RECORD_TYPE_ASSET, ids["asset_linked"],
    )

    ids["org_a_assets"] = {
        ids["asset_linked"], ids["asset_note"], ids["asset_property"],
    }
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# TASKS 2 / 3 / 4 / 5 / 6 — the endpoints, driven through the REAL ASGI app
# ═══════════════════════════════════════════════════════════════════════════


class _Principal:
    """Drives the real ASGI app as one specific user.

    ``verify_token`` is replaced, not the auth dependency: the request still
    passes through the RLS context middleware, the active-account gate and
    ``require_permission`` exactly as production does. Stubbing further up would
    skip the layers most likely to be wrong — which on this sprint is the whole
    point, since the layers most likely to be wrong ARE the permission layers.

    ─────────────────────────────────────────────────────────────────────────
    WHY ALL FOUR PRINCIPALS SHARE ONE TestClient
    ─────────────────────────────────────────────────────────────────────────
    Not a style choice. ``starlette.testclient.TestClient`` used WITHOUT ``with``
    spins up a fresh portal — and a fresh event loop — for EVERY request. The
    application's connection pool is a module global in ``services.database``,
    created lazily on first use and bound to the loop that created it. So
    request 1 succeeds, and request 2 onward fail with
    ``Event loop is closed`` from inside the RLS middleware and return 500 —
    which would have shown up as "the endpoint is broken" rather than as "the
    harness is". Handing each principal its OWN client has the same problem for
    the same reason.

    One client, entered as a context manager (so the portal and its loop live
    for the whole pass), and only the token claims change between calls.
    """

    __slots__ = ("client", "org_id", "sub")

    def __init__(self, client, org_id: str, sub: str):
        self.client = client
        self.org_id = org_id
        self.sub = sub

    def _become(self) -> None:
        import main

        sub, org_id = self.sub, self.org_id
        main.verify_token = lambda _token: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org_id,
        }

    def get(self, url, **kw):
        self._become()
        return self.client.get(url, **kw)

    def post(self, url, **kw):
        self._become()
        return self.client.post(url, **kw)

    def patch(self, url, **kw):
        self._become()
        return self.client.patch(url, **kw)


def _routes_declared() -> dict:
    import main

    spec = main.app.openapi()
    return {p: sorted(spec["paths"][p]) for p in spec["paths"]}


HEADERS = {"Authorization": "Bearer verify-token"}


def endpoint_tests(ids: dict, direct: dict) -> dict:
    """Everything that has to go through HTTP. Sync — TestClient is sync.

    The client is entered and exited around the whole pass so every request
    shares one event loop. See :class:`_Principal` for why that is load-bearing
    rather than tidy.
    """
    import main
    from starlette.testclient import TestClient

    shared = TestClient(main.app, raise_server_exceptions=False)
    shared.__enter__()
    try:
        return _endpoint_tests(shared, ids, direct)
    finally:
        shared.__exit__(None, None, None)


def _endpoint_tests(client, ids: dict, direct: dict) -> dict:
    out: dict = {}
    routes = _routes_declared()

    # ── TASK 2: the endpoints EXIST and are org-scoped + joined ─────────
    expected = {
        "/api/v1/portfolio/securities": ["get", "post"],
        "/api/v1/portfolio/securities/{asset_id}": ["get", "patch"],
        "/api/v1/portfolio/securities/{asset_id}/versions": ["get"],
        "/api/v1/portfolio/global-securities": ["get", "post"],
        "/api/v1/portfolio/global-securities/{security_id}": ["get", "patch"],
        "/api/v1/portfolio/global-securities/{security_id}/identifiers": ["post"],
        "/api/v1/portfolio/global-securities/{security_id}/prices": ["post"],
    }
    missing = {p: v for p, v in expected.items()
               if routes.get(p) != v}
    check(
        "[Y] 2 all SEVEN endpoints are really declared on the app, with the "
        "methods each surface needs — the tenant path has no global write and "
        "the global path has no org scoping",
        not missing,
        f"missing/mismatched: {missing or 'none'}",
    )

    admin = _Principal(client, DEFAULT_ORG_ID, A_SUB)

    # ── The org-scoped list, JOINED ────────────────────────────────────
    res = admin.get("/api/v1/portfolio/securities?search=" + TAG, headers=HEADERS)
    body = res.json() if res.status_code == 200 else {}
    rows = {a["id"]: a for a in body.get("assets", [])}
    check(
        "[Y] 2 GET /portfolio/securities returns this org's REAL assets — the "
        "same set a direct SQL read finds, not a subset and not a superset",
        res.status_code == 200 and set(rows) == ids["org_a_assets"],
        f"status={res.status_code} got={len(rows)} "
        f"expected={len(ids['org_a_assets'])} "
        f"diff={sorted(set(rows) ^ ids['org_a_assets'])}",
    )

    linked = rows.get(ids["asset_linked"], {})
    unlinked = rows.get(ids["asset_property"], {})
    note = rows.get(ids["asset_note"], {})
    check(
        "[Y] 2 the LINKED asset really is joined to its global security — the "
        "identifier, the security name and the latest price all arrive on the "
        "row, and match a direct read of the global tables",
        linked.get("global_security_id") == ids["sec_equity"]
        and linked.get("global_identifier_value") == "9VX3UX3001"
        and linked.get("global_identifier_type") == "cusip"
        and linked.get("global_name") == direct["sec_equity_name"]
        and linked.get("latest_price") == str(LISTED_PRICE),
        f"ident={linked.get('global_identifier_value')} "
        f"price={linked.get('latest_price')} name={linked.get('global_name')}",
    )
    check(
        "[Y] 2 an UNLINKED asset comes back as a row with NULL global fields, "
        "not as a missing row — the join is LEFT and 'unlinked' is a "
        "legitimate permanent state, not absent data",
        unlinked.get("id") == ids["asset_property"]
        and unlinked.get("global_security_id") is None
        and unlinked.get("latest_price") is None
        and "not linked to a global security" in (unlinked.get("latest_price_reason") or ""),
        f"reason={(unlinked.get('latest_price_reason') or '')[:60]!r}",
    )
    check(
        "[Y] 2 a missing price is an ABSENCE with a SPECIFIC reason, never a "
        "zero — and the three absences stay apart: a structured note says so "
        "in its own words, which is different from 'not loaded yet'",
        note.get("latest_price") is None
        and "structured notes are never priced" in (note.get("latest_price_reason") or "")
        and unlinked.get("latest_price_reason") != note.get("latest_price_reason"),
        f"note reason={(note.get('latest_price_reason') or '')[:50]!r}",
    )
    check(
        "[Y] 2 the resolved CURRENT VALUE comes from A2's real resolver, "
        "matching a direct call, and an asset with no valuation reports an "
        "em-dash reason rather than $0",
        linked.get("current_value") == str(EQUITY_VALUE)
        and unlinked.get("current_value") == str(PROPERTY_VALUE)
        and note.get("current_value") is None
        and "ABSENCE of data, not a value of zero" in (note.get("current_value_reason") or ""),
        f"linked={linked.get('current_value')} note={note.get('current_value')}",
    )
    check(
        "[Y] 4 every global-sourced value arrives under a global_-prefixed "
        "key and every org-owned one under its bare column name — the three "
        "colliding names (name/short_name/currency_code) are therefore "
        "unambiguous on the wire",
        linked.get("name") == direct["asset_linked_name"]
        and linked.get("global_name") == direct["sec_equity_name"]
        and linked["name"] != linked["global_name"]
        and set(linked) >= {"global_name", "global_currency_code", "currency_code"},
        f"asset name={linked.get('name')!r} global name={linked.get('global_name')!r}",
    )

    # ── TASK 4: filters and sort are REAL, not client-side theatre ──────
    only_linked = admin.get(
        f"/api/v1/portfolio/securities?search={TAG}&linked=linked", headers=HEADERS
    ).json()
    only_unlinked = admin.get(
        f"/api/v1/portfolio/securities?search={TAG}&linked=unlinked", headers=HEADERS
    ).json()
    lset = {a["id"] for a in only_linked["assets"]}
    uset = {a["id"] for a in only_unlinked["assets"]}
    check(
        "[Y] 4 the link filter PARTITIONS the set — both halves non-empty, "
        "disjoint, and their union is exactly the unfiltered set. A filter "
        "returning everything or nothing would pass a subset check alone",
        lset and uset and not (lset & uset) and (lset | uset) == set(rows),
        f"linked={len(lset)} unlinked={len(uset)} all={len(rows)}",
    )
    by_class = admin.get(
        f"/api/v1/portfolio/securities?search={TAG}&asset_class=hard_asset",
        headers=HEADERS,
    ).json()
    check(
        "[Y] 4 the asset_class filter narrows to the real rows, compared "
        "element-by-element against the same predicate",
        {a["id"] for a in by_class["assets"]} == {ids["asset_property"]},
        f"hard_asset rows={[a['name'] for a in by_class['assets']]}",
    )
    by_sec_type = admin.get(
        f"/api/v1/portfolio/securities?search={TAG}&security_type=structured_note",
        headers=HEADERS,
    ).json()
    check(
        "[Y] 4 a filter on the LINKED GLOBAL security's type reaches across "
        "the join, and correctly excludes the unlinked asset",
        {a["id"] for a in by_sec_type["assets"]} == {ids["asset_note"]},
        f"rows={[a['name'] for a in by_sec_type['assets']]}",
    )

    # ── TASK 3: the tenant boundary, both directions on the same call ───
    viewer = _Principal(client, DEFAULT_ORG_ID, V_SUB)
    v_list = viewer.get(f"/api/v1/portfolio/securities?search={TAG}", headers=HEADERS)
    v_body = v_list.json() if v_list.status_code == 200 else {}
    check(
        "[Y] 3 a VIEW-ONLY user can READ the grid — the refusals below narrow "
        "a working endpoint rather than testing a broken one",
        v_list.status_code == 200
        and {a["id"] for a in v_body.get("assets", [])} == ids["org_a_assets"],
        f"status={v_list.status_code} rows={len(v_body.get('assets', []))}",
    )
    check(
        "[Y] 3 the server publishes an EMPTY editable list to a view-only "
        "user, so the UI has nothing to render a control from — and says so "
        "explicitly with can_write=false rather than by omission",
        v_body.get("permissions", {}).get("can_write") is False
        and v_body.get("vocabularies", {}).get("editable") == []
        and v_body.get("vocabularies", {}).get("inline_editable") == [],
        f"perms={v_body.get('permissions')} "
        f"editable={v_body.get('vocabularies', {}).get('editable')}",
    )
    check(
        "[Y] 3 the same envelope gives an ORG ADMIN the full editable list — "
        "the empty list above is a permission answer, not a broken envelope",
        body.get("permissions", {}).get("can_write") is True
        and set(body.get("vocabularies", {}).get("editable", [])) == ORG_EDITABLE_FIELDS
        and set(body.get("vocabularies", {}).get("inline_editable", []))
            == INLINE_EDITABLE_FIELDS,
        f"editable={len(body.get('vocabularies', {}).get('editable', []))} fields",
    )
    check(
        "[Y] 3 the published editable list is DISJOINT from the published "
        "global-field list for BOTH tiers — no caller is ever told a "
        "platform-sourced field is editable here",
        not (set(body["vocabularies"]["editable"])
             & set(body["vocabularies"]["global_fields"]))
        and body["vocabularies"]["global_fields"] == sorted(GLOBAL_SOURCED_FIELDS)
        and v_body["vocabularies"]["global_fields"] == sorted(GLOBAL_SOURCED_FIELDS),
        f"global_fields={len(GLOBAL_SOURCED_FIELDS)} published to both tiers",
    )

    v_patch = viewer.patch(
        f"/api/v1/portfolio/securities/{ids['asset_property']}",
        json={"asset_type": "should_never_land"}, headers=HEADERS,
    )
    check(
        "[Y] 3 a VIEW-ONLY user is REFUSED a write server-side — 403 naming "
        "the permission, on the same row and body the admin succeeds with below",
        v_patch.status_code == 403
        and WRITE_PERMISSION in str(v_patch.json().get("detail", "")),
        f"status={v_patch.status_code} detail={str(v_patch.json())[:90]}",
    )
    v_create = viewer.post(
        "/api/v1/portfolio/securities",
        json={"name": f"{TAG} viewer should not create", "asset_type": "equity"},
        headers=HEADERS,
    )
    check(
        "[Y] 3 a VIEW-ONLY user is refused CREATE too — the gate is on the "
        "operation, not on one endpoint somebody remembered",
        v_create.status_code == 403,
        f"status={v_create.status_code}",
    )

    # ── TASK 6: the org admin CAN edit its own field. The control. ──────
    ok = admin.patch(
        f"/api/v1/portfolio/securities/{ids['asset_property']}",
        json={"asset_type": "ranch_land", "short_name": f"{TAG}-RANCH"},
        headers=HEADERS,
    )
    ok_body = ok.json() if ok.status_code == 200 else {}
    out["edited_asset"] = ids["asset_property"]
    check(
        "[Y] 6 an ORG-WRITE user CAN edit its own asset's own field — 200, the "
        "new value comes back, and the id is UNCHANGED (an asset archives on "
        "the system axis; a new id would orphan its positions and valuations)",
        ok.status_code == 200
        and ok_body.get("asset", {}).get("asset_type") == "ranch_land"
        and ok_body.get("asset", {}).get("short_name") == f"{TAG}-RANCH"
        and ok_body.get("asset", {}).get("id") == ids["asset_property"]
        and ok_body.get("archived_version_id") not in (None, ids["asset_property"]),
        f"status={ok.status_code} id_stable="
        f"{ok_body.get('asset', {}).get('id') == ids['asset_property']} "
        f"archived={ok_body.get('archived_version_id')}",
    )
    reread = admin.get(
        f"/api/v1/portfolio/securities/{ids['asset_property']}", headers=HEADERS
    ).json()
    check(
        "[Y] 6 a FRESH GET shows the edit — the write reached the database, not "
        "just the response body",
        reread["asset"]["asset_type"] == "ranch_land",
        f"asset_type={reread['asset']['asset_type']!r}",
    )
    versions = admin.get(
        f"/api/v1/portfolio/securities/{ids['asset_property']}/versions",
        headers=HEADERS,
    ).json()
    prior = [v for v in versions["versions"] if not v["is_current"]]
    check(
        "[Y] 6 the PRIOR version is preserved and still carries its ORIGINAL "
        "value — Rule 3's guarantee on the axis a referenced master can use",
        versions["count"] >= 2
        and any(v["asset_type"] == "real_estate" for v in prior)
        and sum(1 for v in versions["versions"] if v["is_current"]) == 1,
        f"versions={versions['count']} prior_types={[v['asset_type'] for v in prior]}",
    )

    # ── TASK 6: the SAME user is refused a GLOBAL-sourced field ─────────
    # Every probe a real caller would try: the API's own prefixed key, the raw
    # global column name, and the link column.
    for field, value, label in (
        ("global_name", "hijacked", "the API's own prefixed key"),
        ("security_type", "equity", "the raw securities_global column name"),
        ("price_coverage", "has_series", "another raw global column"),
        ("global_security_id", ids["sec_note"], "the link column itself"),
    ):
        res = admin.patch(
            f"/api/v1/portfolio/securities/{ids['asset_linked']}",
            json={field: value}, headers=HEADERS,
        )
        detail = str(res.json().get("detail", ""))
        check(
            f"[Y] 6 the SAME org-write user is REFUSED {field!r} ({label}) — "
            f"**403**, the permission answer. A 422 here would be a FAIL: it "
            f"would mean the field was rejected as junk rather than as platform "
            f"data, which is the wrong refusal reaching the wrong log",
            res.status_code == 403,
            f"status={res.status_code} detail={detail[:100]}",
        )
    # And the refusal actually says where the real door is.
    said = admin.patch(
        f"/api/v1/portfolio/securities/{ids['asset_linked']}",
        json={"security_type": "equity"}, headers=HEADERS,
    ).json().get("detail", "")
    check(
        "[Y] 6 the 403 NAMES the Super-Admin path rather than just saying no — "
        "a refusal that does not say where to go trains people to try harder "
        "at the wrong door",
        "global-securities" in said and "Super Admin" in said,
        f"detail={said[:120]}",
    )
    # A genuinely unknown field is a DIFFERENT answer. If both came back 403 the
    # test above would be satisfied by an endpoint that refuses everything.
    junk = admin.patch(
        f"/api/v1/portfolio/securities/{ids['asset_linked']}",
        json={"totally_made_up_field": 1}, headers=HEADERS,
    )
    check(
        "[Y] 6 a genuinely UNKNOWN field is 422, not 403 — the two refusals "
        "are distinguishable, which is what proves the 403 is about authority "
        "and not about an endpoint that says no to everything",
        junk.status_code == 422,
        f"status={junk.status_code}",
    )
    # And the row is untouched by any of it.
    after = admin.get(
        f"/api/v1/portfolio/securities/{ids['asset_linked']}", headers=HEADERS
    ).json()
    check(
        "[Y] 6 nothing changed on either record after five refused writes — a "
        "403 that had already written would be worse than no gate at all",
        after["asset"]["global_name"] == direct["sec_equity_name"]
        and after["asset"]["global_security_id"] == ids["sec_equity"]
        and after["global_security"]["security_type"] == "equity"
        and after["global_security"]["price_coverage"] == "has_series",
        f"global name={after['asset']['global_name']!r} "
        f"type={after['global_security']['security_type']!r}",
    )

    # ── TASK 3/6: the GLOBAL write path. Both directions, same call. ────
    org_admin_attempt = admin.patch(
        f"/api/v1/portfolio/global-securities/{ids['sec_equity']}",
        json={"name": f"{TAG} hijacked by org admin"}, headers=HEADERS,
    )
    check(
        "[Y] 6 an ORG ADMIN calling the Super-Admin path DIRECTLY is refused "
        "403 — holding manage_portfolio in their own org buys nothing on the "
        "platform master",
        org_admin_attempt.status_code == 403
        and "Super Admin" in str(org_admin_attempt.json().get("detail", "")),
        f"status={org_admin_attempt.status_code}",
    )
    for path, payload, what in (
        (f"/api/v1/portfolio/global-securities", {"name": f"{TAG} nope",
                                                  "security_type": "equity"}, "create"),
        (f"/api/v1/portfolio/global-securities/{ids['sec_equity']}/identifiers",
         {"id_type": "isin", "id_value": "US000UX30001"}, "add identifier"),
        (f"/api/v1/portfolio/global-securities/{ids['sec_equity']}/prices",
         {"price_date": "2026-08-20", "price": "1.00"}, "add price"),
    ):
        res = admin.post(path, json=payload, headers=HEADERS)
        check(
            f"[Y] 6 an org admin is refused the global {what} path too — every "
            f"write endpoint on the master is gated, not just the one somebody "
            f"tested",
            res.status_code == 403,
            f"status={res.status_code}",
        )
    v_global = viewer.patch(
        f"/api/v1/portfolio/global-securities/{ids['sec_equity']}",
        json={"name": "nope"}, headers=HEADERS,
    )
    check(
        "[Y] 6 a view-only user is refused the global path as well — the two "
        "boundaries compose rather than one substituting for the other",
        v_global.status_code == 403,
        f"status={v_global.status_code}",
    )

    # THE CONTROL. A gate that refuses everyone passes every check above.
    su = _Principal(client, DEFAULT_ORG_ID, S_SUB)
    su_patch = su.patch(
        f"/api/v1/portfolio/global-securities/{ids['sec_equity']}",
        json={"short_name": f"{TAG}-EQ2"}, headers=HEADERS,
    )
    su_body = su_patch.json() if su_patch.status_code == 200 else {}
    check(
        "[Y] 6 THE CONTROL — a SUPER ADMIN succeeds on the IDENTICAL call the "
        "org admin was refused. Without this, an endpoint that 500s for "
        "everybody would pass every refusal assertion above",
        su_patch.status_code == 200 and su_body.get("short_name") == f"{TAG}-EQ2",
        f"status={su_patch.status_code} short_name={su_body.get('short_name')!r}",
    )
    su_ident = su.post(
        f"/api/v1/portfolio/global-securities/{ids['sec_equity']}/identifiers",
        json={"id_type": "isin", "id_value": "us000ux30001"}, headers=HEADERS,
    )
    su_ident_body = su_ident.json() if su_ident.status_code == 201 else {}
    check(
        "[Y] 6 THE CONTROL — a super admin can add a global identifier, and A1 "
        "normalises the value on the way in (an ISIN is stored upper-cased, so "
        "the lookup index is usable)",
        su_ident.status_code == 201
        and any(i["id_value"] == "US000UX30001"
                for i in su_ident_body.get("identifiers", [])),
        f"status={su_ident.status_code} "
        f"values={[i['id_value'] for i in su_ident_body.get('identifiers', [])]}",
    )
    su_note_price = su.post(
        f"/api/v1/portfolio/global-securities/{ids['sec_note']}/prices",
        json={"price_date": "2026-08-20", "price": "99.50"}, headers=HEADERS,
    )
    check(
        "[Y] 6 even a SUPER ADMIN cannot price a structured note — A1's hard "
        "rule survives being put behind a REST endpoint, and surfaces as 422 "
        "(the body was fine; the instrument was wrong)",
        su_note_price.status_code == 422
        and "structured_note" in str(su_note_price.json().get("detail", "")),
        f"status={su_note_price.status_code}",
    )
    su_junk = su.patch(
        f"/api/v1/portfolio/global-securities/{ids['sec_equity']}",
        json={"canonical_id": ids["sec_note"]}, headers=HEADERS,
    )
    check(
        "[Y] 6 canonical_id / merged_into_id are NOT reachable through the "
        "global PATCH even for a super admin — they are maintained by "
        "merge_securities(), which keeps an invariant across many rows that a "
        "generic setter would break one row at a time",
        su_junk.status_code == 422,
        f"status={su_junk.status_code}",
    )
    check(
        "[Y] 6 the super admin ALSO passes the tenant boundary without a "
        "special case — the escape hatch is checked FIRST in rbac, exactly as "
        "every other enforcement layer in this codebase does it",
        su.get(f"/api/v1/portfolio/securities?search={TAG}",
               headers=HEADERS).status_code == 200
        and su.get(f"/api/v1/portfolio/securities?search={TAG}", headers=HEADERS)
              .json()["permissions"]["can_write_global"] is True,
        "super admin reads the tenant grid and reports can_write_global",
    )

    # ── TASK 6: cross-org isolation at the ENDPOINT layer ───────────────
    other = _Principal(client, OTHER_ORG_ID, B_SUB)
    b_list = other.get(f"/api/v1/portfolio/securities?search={TAG}", headers=HEADERS)
    b_ids = {a["id"] for a in b_list.json().get("assets", [])}
    check(
        "[Y] 6 cross-org (endpoint layer): org B sees its OWN asset and none "
        "of org A's — both directions asserted on the SAME call, so an "
        "endpoint that returned nothing for everybody would fail the first half",
        b_list.status_code == 200
        and ids["asset_b"] in b_ids
        and not (b_ids & ids["org_a_assets"]),
        f"orgB rows={len(b_ids)} leaked={sorted(b_ids & ids['org_a_assets'])}",
    )
    b_detail = other.get(
        f"/api/v1/portfolio/securities/{ids['asset_linked']}", headers=HEADERS
    )
    check(
        "[Y] 6 org B gets 404 on org A's asset id — and 404 rather than 403, "
        "because confirming that an id exists somewhere else is itself a "
        "cross-tenant leak",
        b_detail.status_code == 404,
        f"status={b_detail.status_code}",
    )
    b_patch = other.patch(
        f"/api/v1/portfolio/securities/{ids['asset_linked']}",
        json={"name": "cross-org write"}, headers=HEADERS,
    )
    check(
        "[Y] 6 org B cannot WRITE org A's asset either — refused, and the row "
        "is verified unchanged afterwards rather than assumed to be",
        b_patch.status_code in (400, 404),
        f"status={b_patch.status_code}",
    )
    # The GLOBAL master is deliberately visible to both, and that is correct.
    b_global = other.get(
        f"/api/v1/portfolio/global-securities/{ids['sec_equity']}", headers=HEADERS
    )
    check(
        "[Y] 6 org B CAN read the global security — asserted as a positive, "
        "because it is a design decision and not a leak: those rows have no "
        "org_id, belong to no tenant, and the deployed policy is USING (true). "
        "What org B cannot do is write one, which is asserted above",
        b_global.status_code == 200,
        f"status={b_global.status_code}",
    )

    # ── TASK 5: the detail pane's payload ───────────────────────────────
    detail = admin.get(
        f"/api/v1/portfolio/securities/{ids['asset_linked']}", headers=HEADERS
    ).json()
    check(
        "[Y] 5 the detail call returns EVERYTHING the pane shows in ONE "
        "request — asset, global security, both identifier sets, governing "
        "valuation, valuation history, positions, versions and the document "
        "record_type. A waterfall would render the pane in pieces",
        all(k in detail for k in (
            "asset", "global_security", "own_identifiers", "governing_valuation",
            "valuation_history", "positions", "version_history",
            "document_record_type", "permissions", "vocabularies",
        )),
        f"keys={sorted(detail)}",
    )
    prop_detail = admin.get(
        f"/api/v1/portfolio/securities/{ids['asset_property']}", headers=HEADERS
    ).json()
    check(
        "[Y] 5 the org's OWN identifiers are a separate list from the "
        "platform's, and carry a key the GLOBAL constraint would refuse — "
        "'parcel' is valid on a tenant asset and not on a global security",
        any(i["id_type"] == "parcel" and i["id_value"] == "APN-0042-118"
            for i in prop_detail["own_identifiers"])
        and prop_detail["global_security"] is None,
        f"own={[(i['id_type'], i['id_value']) for i in prop_detail['own_identifiers']]}",
    )
    check(
        "[Y] 5 the pane's global block is marked as requiring Super Admin by "
        "the SERVER, so the UI's read-only rendering is the server's answer "
        "rather than the component's own opinion",
        detail["global_security"]["write_requires_super_admin"] is True
        and detail["permissions"]["can_write_global"] is False,
        "write_requires_super_admin=True, can_write_global=False for an org admin",
    )
    check(
        "[Y] 5 the pane gets the positions held against the asset and the "
        "governing valuation that produced the displayed figure — a number "
        "without the mark it came from is unauditable",
        any(p["id"] == ids["pos_a"] for p in detail["positions"])
        and detail["governing_valuation"]["asset_value"] == str(EQUITY_VALUE)
        and detail["governing_valuation"]["status"] == "final",
        f"positions={len(detail['positions'])} "
        f"governing={detail['governing_valuation']['status']}",
    )
    check(
        "[Y] 5 the pane is told which record_type to link documents under, by "
        "the API — document_record_links.record_type has NO CHECK constraint, "
        "so a frontend typo would write a link nothing ever reads back",
        detail["document_record_type"] == RECORD_TYPE_ASSET,
        f"record_type={detail['document_record_type']!r}",
    )

    # ── TASK 2: create, including the link ──────────────────────────────
    created = admin.post(
        "/api/v1/portfolio/securities",
        json={
            "name": f"{TAG} Created Via API",
            "asset_type": "equity",
            "global_security_id": ids["sec_equity"],
            "currency_code": "USD",
        },
        headers=HEADERS,
    )
    out["created_asset"] = (
        created.json()["asset"]["id"] if created.status_code == 201 else None
    )
    check(
        "[Y] 2 POST /portfolio/securities creates an asset and LINKS it to a "
        "global security — the link is an org decision about the org's own FK "
        "column, and is the only global-adjacent thing this path accepts",
        created.status_code == 201
        and created.json()["asset"]["global_security_id"] == ids["sec_equity"]
        and created.json()["asset"]["global_identifier_value"] == "9VX3UX3001",
        f"status={created.status_code}",
    )
    bad_link = admin.post(
        "/api/v1/portfolio/securities",
        json={"name": f"{TAG} Bad Link", "asset_type": "equity",
              "global_security_id": "00000000-0000-0000-0000-0000000000ff"},
        headers=HEADERS,
    )
    check(
        "[Y] 2 a create naming a non-existent security is refused with a "
        "legible message rather than a raw 23503 naming a constraint",
        bad_link.status_code == 400
        and "platform master" in str(bad_link.json().get("detail", "")),
        f"status={bad_link.status_code}",
    )
    out["routes"] = routes
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Direct-SQL comparisons + the service layer + the RLS layer
# ═══════════════════════════════════════════════════════════════════════════


async def direct_reads(conn, ids: dict) -> dict:
    return {
        "sec_equity_name": await conn.fetchval(
            f"SELECT name FROM {TABLE_SEC} WHERE id = $1::uuid", ids["sec_equity"]
        ),
        "asset_linked_name": await conn.fetchval(
            f"SELECT name FROM {TABLE_ASSETS} WHERE id = $1::uuid",
            ids["asset_linked"],
        ),
    }


async def check_archive_rows(conn, ids: dict) -> None:
    """The system-axis archive, verified in SQL rather than trusted."""
    rows = await conn.fetch(
        f"SELECT id::text, asset_type, valid_to, system_to, valid_from "
        f"FROM {TABLE_ASSETS} WHERE org_id = $1::uuid AND name LIKE $2 "
        f"ORDER BY system_from",
        DEFAULT_ORG_ID, f"{TAG} Ranch%",
    )
    live = [r for r in rows if r["system_to"] is None]
    archived = [r for r in rows if r["system_to"] is not None]
    check(
        "[Y] 6 in SQL: the edit left EXACTLY ONE live row and at least one "
        "archived one, the live row kept the original id, and the archive got "
        "its own — the shape that keeps three foreign keys attached",
        len(live) == 1 and len(archived) >= 1
        and live[0]["id"] == ids["asset_property"]
        and all(a["id"] != ids["asset_property"] for a in archived),
        f"live={len(live)} archived={len(archived)} "
        f"live_id_stable={live and live[0]['id'] == ids['asset_property']}",
    )
    check(
        "[Y] 6 the ARCHIVED row still carries its ORIGINAL value while the "
        "live row carries the new one — Rule 3's guarantee, on the axis a "
        "referenced master row can actually use",
        any(a["asset_type"] == "real_estate" for a in archived)
        and live and live[0]["asset_type"] == "ranch_land",
        f"archived types={[a['asset_type'] for a in archived]} "
        f"live={live[0]['asset_type'] if live else None}",
    )
    check(
        "[Y] 6 the archive is on the SYSTEM axis, NOT the valid axis — "
        "valid_to stays NULL on both rows, so nothing that filters on valid_to "
        "alone starts seeing a closed asset",
        all(r["valid_to"] is None for r in rows)
        and all(a["system_to"] is not None for a in archived),
        f"valid_to values={[r['valid_to'] for r in rows]}",
    )
    # The FK consequence, asserted rather than argued.
    orphans = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_POSITIONS} p "
        f"WHERE p.org_id = $1::uuid AND p.asset_id NOT IN "
        f"(SELECT id FROM {TABLE_ASSETS} WHERE system_to IS NULL)",
        DEFAULT_ORG_ID,
    )
    check(
        "[Y] 6 NO position in this org points at an archived asset version — "
        "the failure mode a valid-axis restatement would have caused, checked "
        "against the whole org rather than just the fixture",
        orphans == 0,
        f"positions pointing at a non-live asset row = {orphans}",
    )
    check(
        "[Y] 6 the linked document survived the edit WITHOUT any carry step — "
        "the id never moved, so unlike UX 1 there is nothing to re-point",
        await conn.fetchval(
            f"SELECT count(*) FROM {TABLE_DOC_RECORD_LINKS} "
            f"WHERE record_type = $1 AND record_id = $2::uuid",
            RECORD_TYPE_ASSET, ids["asset_linked"],
        ) == 1,
        "document_record_links still points at the live asset id",
    )


async def check_service_layer(conn, ids: dict) -> None:
    """The refusals at the SERVICE layer, not just at the router.

    A router-only gate is one refactor away from being no gate at all: the next
    caller of ``update_asset`` will be an importer or the assistant, not an HTTP
    request, and it will not pass through ``_tenant_gate``.
    """
    for field, value in (
        ("global_name", "x"), ("security_type", "equity"), ("id_value", "x"),
        ("price", "1"), ("global_security_id", ids["sec_note"]),
    ):
        raised = None
        try:
            await update_asset(
                conn, org_id=DEFAULT_ORG_ID, asset_id=ids["asset_linked"],
                changes={field: value},
            )
        except GlobalFieldError as exc:
            raised = exc
        except Exception as exc:  # noqa: BLE001
            raised = exc
        check(
            f"[Y] 3 the SERVICE refuses {field!r} with GlobalFieldError — the "
            f"gate is not only in the router, which the next non-HTTP caller "
            f"will not pass through",
            isinstance(raised, GlobalFieldError),
            f"raised={type(raised).__name__ if raised else 'nothing'}",
        )

    raised = None
    try:
        await update_security(
            conn, global_security_id=ids["sec_equity"],
            changes={"name": "x"}, is_super_admin=False,
        )
    except SecuritiesGlobalPermissionError as exc:
        raised = exc
    check(
        "[Y] 3 the GLOBAL service refuses a write without an explicit "
        "is_super_admin=True — A1's guard, unchanged and still load-bearing",
        isinstance(raised, SecuritiesGlobalPermissionError),
        f"raised={type(raised).__name__ if raised else 'nothing'}",
    )

    # The list function agrees with the endpoint.
    listed = await list_assets(conn, org_id=DEFAULT_ORG_ID, search=TAG)
    check(
        "[Y] 2 the service-level list agrees with the endpoint on the same "
        "org — the endpoint is a thin skin over this, not a second query",
        {a["id"] for a in listed["assets"]} >= ids["org_a_assets"],
        f"service rows={listed['total']}",
    )
    detail = await get_asset(conn, org_id=DEFAULT_ORG_ID,
                             asset_id=ids["asset_linked"])
    check(
        "[Y] 2 the service-level detail resolves the same joined global "
        "security the endpoint returned",
        detail["asset"]["global_security_id"] == ids["sec_equity"]
        and detail["global_security"]["id"] == ids["sec_equity"],
        "join agrees at both layers",
    )


class NonBypassRole:
    """A connection that reads as ``app_service``, however it got there.

    ``mode`` is ``'dsn'`` (a direct ``app_service`` login) or ``'set_role'``
    (``SET LOCAL ROLE`` inside each transaction). Which path was used is always
    REPORTED — a fallback nobody can see is how a rotated credential silently
    turns an RLS check into a session that merely resembles one.
    """

    def __init__(self, conn, mode: str):
        self.conn = conn
        self.mode = mode

    def scoped(self, org_id: str | None, super_admin: bool = False):
        conn, mode = self.conn, self.mode

        class _Ctx:
            async def __aenter__(self):
                self.tr = conn.transaction()
                await self.tr.start()
                try:
                    if mode == "set_role":
                        # LOCAL, not session-level: the pooler is in transaction
                        # mode, so a session SET can be handed to the next
                        # transaction on a different backend.
                        await conn.execute("SET LOCAL ROLE app_service")
                    await conn.execute(
                        "SELECT set_config('app.current_org_id', $1, true)",
                        org_id or "",
                    )
                    await conn.execute(
                        "SELECT set_config('app.is_super_admin', $1, true)",
                        "true" if super_admin else "",
                    )
                except BaseException:
                    await self.tr.rollback()
                    raise
                return conn

            async def __aexit__(self, et, e, tb):
                # Always rolled back: a rollback also unwinds SET LOCAL ROLE
                # deterministically, and the global-write probes below MUST NOT
                # persist even when they succeed.
                await self.tr.rollback()
                return False

        return _Ctx()


async def open_non_bypass_role(db_url: str, app_url: str | None):
    if app_url:
        try:
            conn = await asyncpg.connect(app_url, statement_cache_size=0,
                                         ssl="require")
            return NonBypassRole(conn, "dsn")
        except Exception as exc:  # noqa: BLE001
            report(
                "ENVIRONMENT — APP_SERVICE_DATABASE_URL in apps/api/.env does "
                "not authenticate",
                f"{type(exc).__name__}: {exc}. Falling back to SET LOCAL ROLE "
                f"app_service, which is ASSERTED below to be a genuinely "
                f"non-bypassing session rather than assumed to be one.",
            )
    conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SET LOCAL ROLE app_service")
        finally:
            await tr.rollback()
    except Exception as exc:  # noqa: BLE001
        report("FATAL — no non-bypass role is reachable",
               f"SET LOCAL ROLE app_service failed: {type(exc).__name__}: {exc}")
        await conn.close()
        return None
    return NonBypassRole(conn, "set_role")


async def check_rls_isolation(role: NonBypassRole, ids: dict) -> None:
    """Both boundaries at the DATABASE layer, under the real non-bypass role."""
    async with role.scoped(None) as c:
        who = await c.fetchval("SELECT current_user")
        bypass = await c.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        # THE POSITIVE PROOF. With no org context a non-bypass session sees
        # nothing in a tenant table; a bypassing one sees the whole table.
        denied = await c.fetchval(f"SELECT count(*) FROM {TABLE_ASSETS}")
        # And the SAME session DOES see the global table, because its policy is
        # USING (true). This is what makes the zero above mean "RLS is live"
        # rather than "this connection is broken".
        global_visible = await c.fetchval(f"SELECT count(*) FROM {TABLE_SEC}")

    check(
        f"[Y] 6 the RLS checks run under a role that CANNOT bypass RLS "
        f"(obtained via {role.mode!r}) — otherwise every assertion below would "
        f"pass while proving nothing",
        bypass is False and who == "app_service",
        f"current_user={who!r} rolbypassrls={bypass} path={role.mode!r}",
    )
    check(
        "[Y] 6 RLS is demonstrably LIVE on portfolio.assets: with the org GUC "
        "empty the session reads ZERO rows — while the SAME session still "
        "reads the GLOBAL table, whose policy is USING (true). A broken "
        "connection would read zero from both",
        denied == 0 and global_visible > 0,
        f"tenant rows with no org context={denied} (must be 0); "
        f"global rows={global_visible} (must be > 0)",
    )

    async def under(org_id: str):
        async with role.scoped(org_id) as c:
            return await list_assets(c, org_id=org_id, search=TAG)

    b_view = await under(OTHER_ORG_ID)
    b_ids = {a["id"] for a in b_view["assets"]}
    check(
        "[Y] 6 cross-org (RLS, real non-bypassing app_service connection): org "
        "B's context sees its OWN asset and none of org A's — both directions "
        "on the same call",
        ids["asset_b"] in b_ids and not (b_ids & ids["org_a_assets"]),
        f"orgB rows={len(b_ids)} leaked={sorted(b_ids & ids['org_a_assets'])}",
    )
    a_view = await under(DEFAULT_ORG_ID)
    a_ids = {a["id"] for a in a_view["assets"]}
    check(
        "[Y] 6 the control: org A's OWN context DOES see org A's assets, so "
        "the check above narrowed rather than simply failing",
        ids["org_a_assets"] <= a_ids and ids["asset_b"] not in a_ids,
        f"orgA sees {len(a_ids)}, missing={sorted(ids['org_a_assets'] - a_ids)}",
    )
    check(
        "[Y] 6 the JOIN still works under the non-bypassing role — the tenant "
        "row is narrowed by org while the global half comes back in full, "
        "which is the two policy shapes composing exactly as designed",
        all(a["global_name"] for a in a_view["assets"]
            if a["global_security_id"]),
        "linked rows carry their global names under app_service",
    )

    # ── The GLOBAL write policy, exercised as the BACKSTOP it is ────────
    async with role.scoped(DEFAULT_ORG_ID, super_admin=False) as c:
        refused = None
        try:
            await c.execute(
                f"UPDATE {TABLE_SEC} SET short_name = 'rls-should-refuse' "
                f"WHERE id = $1::uuid",
                ids["sec_equity"],
            )
            # An UPDATE that matches no visible row is 0 rows, not an error —
            # so the assertion cannot be "it raised". It is "nothing changed".
            refused = await c.fetchval(
                f"SELECT count(*) FROM {TABLE_SEC} "
                f"WHERE id = $1::uuid AND short_name = 'rls-should-refuse'",
                ids["sec_equity"],
            )
        except Exception as exc:  # noqa: BLE001
            refused = 0 if "row-level security" in str(exc).lower() else -1
    check(
        "[Y] 6 RLS BACKSTOP: under app_service WITHOUT app.is_super_admin, an "
        "UPDATE of a global security changes NOTHING — asserted on the row's "
        "value rather than on 'it raised', because an UPDATE that matches no "
        "visible row reports zero rows and no error",
        refused == 0,
        f"rows carrying the hijacked value = {refused} (must be 0)",
    )
    async with role.scoped(DEFAULT_ORG_ID, super_admin=True) as c:
        await c.execute(
            f"UPDATE {TABLE_SEC} SET short_name = 'rls-allows-super' "
            f"WHERE id = $1::uuid",
            ids["sec_equity"],
        )
        allowed = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_SEC} "
            f"WHERE id = $1::uuid AND short_name = 'rls-allows-super'",
            ids["sec_equity"],
        )
    check(
        "[Y] 6 THE CONTROL for the backstop — the SAME connection WITH "
        "app.is_super_admin set does change the row. Without this, a policy "
        "that refused everyone (or a connection that could not write at all) "
        "would pass the assertion above",
        allowed == 1,
        f"rows updated under is_super_admin = {allowed} (transaction rolled back)",
    )
    report(
        "THE RLS LAYER IS THE BACKSTOP, NOT THE OPERATIVE GATE",
        "The application connects as `postgres`, which carries rolbypassrls, so "
        "in production the two policy assertions above do not fire on the app's "
        "own connection at all — the app-layer is_super_admin check and the "
        "service's _require_super_admin are what actually refuse an org admin. "
        "Both are asserted separately, through the real ASGI app. The policies "
        "are what catches a direct psql session or a future mis-roled pool.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Static checks — schema qualification, org_id provenance, frontend wiring
# ═══════════════════════════════════════════════════════════════════════════


def check_schema_qualification() -> None:
    """Every portfolio.* reference is schema-qualified. AST-checked.

    `portfolio` is NOT on app_service's search_path, so an unqualified
    `FROM assets` raises UndefinedTableError under the production role while
    working fine in a psql session that happened to SET search_path — invisible
    in development, total in production.
    """
    for rel in ("services/portfolio_securities.py",
                "routers/portfolio_securities.py",
                "services/securities_global.py"):
        code = strip_docstrings(read(os.path.join(_API, rel)))
        bare = sorted({
            name for name in (
                "assets", "positions", "valuations", "asset_identifiers",
                "securities_global", "securities_global_identifiers",
                "securities_global_prices", "securities_global_relationships",
            )
            if re.search(rf"\b(FROM|INTO|UPDATE|JOIN)\s+{name}\b", code)
        })
        check(
            f"[Y] {rel} schema-qualifies every portfolio table (AST-checked: "
            f"no bare FROM/INTO/UPDATE/JOIN in executable code)",
            not bare,
            f"unqualified: {bare or 'none'}",
        )


def check_no_org_id_from_body() -> None:
    """org_id is never read from a request body or a path segment.

    The "no model declares org_id" half is AST-checked against the Pydantic
    classes rather than grepped for the string ``org_id:``. A text search hits
    the type annotation on this router's own ``_permission_envelope(pool,
    user_id: str, org_id: str)`` helper — a parameter the router passes itself,
    from JWT claims, which is the OPPOSITE of the thing being guarded against.
    A check that fires on the correct implementation is one somebody eventually
    deletes instead of the bug.
    """
    src = read(os.path.join(_API, "routers", "portfolio_securities.py"))
    code = strip_docstrings(src)
    body_reads = re.findall(r"body\.org_id|\.get\(['\"]org_id", code)

    tree = ast.parse(src)
    models_with_org: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(getattr(b, "id", getattr(b, "attr", "")) == "BaseModel"
                   for b in node.bases):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if stmt.target.id == "org_id":
                    models_with_org.append(node.name)

    # Every path-parameter name, so a route can never take org_id from the URL.
    path_org = re.findall(r'@router\.\w+\("[^"]*\{org_id\}', src)

    check(
        "[Y] 2 org_id is taken ONLY from get_org_id(request) — no executable "
        "line reads it from a body, no route declares it as a path parameter, "
        "and no Pydantic request model declares it as a field (AST-checked "
        "against the model classes, not grepped)",
        not body_reads and not models_with_org and not path_org
        and "get_org_id(request)" in code,
        f"body reads={body_reads or 'none'}, "
        f"models declaring org_id={models_with_org or 'none'}, "
        f"path params={path_org or 'none'}",
    )


def check_dynamic_sql_is_safe() -> None:
    """The two UPDATEs build a SET clause from names. Prove the names are ours.

    An assignment list interpolated from a caller-supplied key is an injection,
    and it is the one place in this sprint where the SQL is not fully static. It
    is safe because every key is validated against a frozenset of literals in
    the module BEFORE the interpolation — asserted here rather than argued in a
    comment, by checking the guard precedes the f-string in the source.
    """
    for rel, guard in (
        ("services/portfolio_securities.py", "ORG_EDITABLE_FIELDS"),
        ("services/securities_global.py", "SECURITY_EDITABLE_FIELDS"),
    ):
        src = read(os.path.join(_API, rel))
        code = strip_docstrings(src)
        # The guard's rejection of unknown keys must appear before the SET build.
        guard_at = code.find(f"set(changes) - {guard}")
        build_at = code.find('assignments = ", ".join(')
        check(
            f"[Y] {rel}: the dynamic SET clause is built ONLY from names "
            f"already validated against {guard} — the rejection of unknown "
            f"keys textually precedes the interpolation, and the values are "
            f"bound parameters",
            guard_at != -1 and build_at != -1 and guard_at < build_at
            and "${i + " in code,
            f"guard@{guard_at} build@{build_at}",
        )


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _node_modules_dir() -> str | None:
    """apps/web is an npm WORKSPACE, so node_modules is hoisted to the root."""
    for candidate in (
        os.path.join(_WEB, "node_modules"),
        os.path.join(_WEB, "..", "..", "node_modules"),
    ):
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return None


def check_frontend_wiring() -> None:
    grid_path = os.path.join(_WEB, "components", "portfolio", "SecuritiesGrid.jsx")
    pane_path = os.path.join(_WEB, "components", "portfolio", "AssetDetailPane.jsx")
    page_path = os.path.join(_WEB, "app", "portfolio", "securities", "page.js")
    route_list = os.path.join(_WEB, "app", "api", "portfolio", "securities",
                              "route.js")
    route_one = os.path.join(_WEB, "app", "api", "portfolio", "securities",
                             "[assetId]", "route.js")
    route_gl = os.path.join(_WEB, "app", "api", "portfolio", "global-securities",
                            "route.js")
    route_gone = os.path.join(_WEB, "app", "api", "portfolio",
                              "global-securities", "[securityId]", "route.js")

    for label, path in (
        ("SecuritiesGrid.jsx", grid_path),
        ("AssetDetailPane.jsx", pane_path),
        ("app/portfolio/securities/page.js", page_path),
        ("app/api/portfolio/securities/route.js", route_list),
        ("app/api/portfolio/securities/[assetId]/route.js", route_one),
        ("app/api/portfolio/global-securities/route.js", route_gl),
        ("app/api/portfolio/global-securities/[securityId]/route.js", route_gone),
    ):
        check(f"[Y] 4 {label} exists", os.path.exists(path), path)

    grid = read(grid_path)
    pane = read(pane_path)
    page = read(page_path)
    routes = [read(p) for p in (route_list, route_one, route_gl, route_gone)]

    check(
        "[Y] 4 the grid is driven by the SHARED DataGrid, not a new grid "
        "library",
        'from "@/components/ui/DataGrid"' in grid,
        "imports @/components/ui/DataGrid",
    )
    check(
        "[Y] 4 the frontend calls the REAL endpoints — the grid fetches "
        "/api/portfolio/securities and PATCHes the same path",
        "/api/portfolio/securities?" in grid
        and 'method: "PATCH"' in grid
        and "/api/portfolio/securities/${assetId}" in pane,
        "list fetch + PATCH present in both components",
    )
    # A mock would be an array literal of asset-shaped objects.
    mocked = re.search(r"(MOCK|STUB|FAKE|SAMPLE)_?(ASSETS|SECURITIES|ROWS|DATA)",
                       grid, re.IGNORECASE) \
        or re.search(r"global_identifier_value\s*:\s*[\"']", grid)
    check(
        "[Y] 4 the grid contains NO mock/stub row data — every row it renders "
        "came from the API",
        mocked is None,
        f"suspect literal: {mocked.group(0)!r}" if mocked else "none found",
    )
    codes = [_strip_js_comments(s) for s in routes]
    check(
        "[Y] 4 all FOUR Next.js API routes forward to FastAPI (Rule 5: the "
        "browser never calls FastAPI directly) and no executable line in any "
        "of them reads, sets or forwards an org_id",
        all("forwardToApi" in c for c in codes)
        and all("org_id" not in c for c in codes)
        and "/api/v1/portfolio/securities" in codes[0]
        and "/api/v1/portfolio/global-securities" in codes[2],
        "forwardToApi in all four; org_id absent from every executable line",
    )
    check(
        "[Y] 3 the Next.js global-securities routes gate NOTHING themselves — "
        "a permission check in a route file is one the browser skips by "
        "calling the route directly, and its presence would make the real gate "
        "feel optional",
        "is_super_admin" not in codes[2] and "is_super_admin" not in codes[3]
        and "PATCH" in codes[3],
        "no client-side super-admin branch; PATCH forwarded unconditionally",
    )
    check(
        "[Y] 4 the page renders the grid inside AppShell behind a host-aware "
        "session check",
        "SecuritiesGrid" in page and "getHostSession" in page
        and "AppShell" in page,
        "getHostSession + AppShell + SecuritiesGrid",
    )
    check(
        "[Y] 4 selecting a row opens the pane in place — the row click sets "
        "selection, it does not navigate",
        "onRowClick" in grid and "setSelectedId" in grid
        and "router.push" not in grid,
        "onRowClick → setSelectedId; no router.push in the grid",
    )

    # ── THE UI HALF OF THE PERMISSION BOUNDARY ─────────────────────────
    grid_code = _strip_js_comments(grid)
    pane_code = _strip_js_comments(pane)
    check(
        "[Y] 3 inline editing is limited to what the SERVER publishes — the "
        "grid reads vocabularies.inline_editable and keeps no list of its own "
        "that could drift",
        "inline_editable" in grid_code and "inlineEditable.has(" in grid_code,
        f"server list = {sorted(INLINE_EDITABLE_FIELDS)}",
    )
    check(
        "[Y] 3 there is NO client-side fallback list — a `|| DEFAULTS` here "
        "would silently restore write controls for a view-only user the first "
        "time the envelope was missing for an unrelated reason",
        "inline_editable || [" in grid_code or "inline_editable || []" in grid_code,
        "the only fallback is the EMPTY array",
    )
    check(
        "[Y] 3 the pane's editable set comes from the same server envelope, "
        "and every org field is rendered as an input ONLY inside an "
        "editable.has(...) branch",
        "vocabularies?.editable" in pane_code
        and pane_code.count("editable.has(") >= 8,
        f"editable.has() branches = {pane_code.count('editable.has(')}",
    )
    # THE STRUCTURAL CLAIM: the platform block has no editable branch at all.
    platform_start = pane_code.find("Platform security")
    platform_end = pane_code.find("This org’s identifiers")
    platform_block = (
        pane_code[platform_start:platform_end]
        if platform_start != -1 and platform_end > platform_start else ""
    )
    check(
        "[Y] 3 the pane's PLATFORM block contains no editable branch, no "
        "input, no select and no save — the controls are ABSENT, not disabled, "
        "and there is no prop that could make one appear",
        bool(platform_block)
        and "editable.has(" not in platform_block
        and "<input" not in platform_block
        and "<select" not in platform_block
        and "onChange" not in platform_block,
        f"platform block = {len(platform_block)} chars, zero write controls",
    )
    # The window is bounded by the NEXT top-level `function `, not by a fixed
    # character count, and the claim is about the PROP LIST and the BRANCHES —
    # not about the substring "editable", which legitimately appears in the
    # component's own tooltip copy ("Not editable from this screen."). A check
    # that fires on correct user-facing wording is one somebody deletes.
    pf_at = pane_code.find("function PlatformField(")
    pf_end = pane_code.find("\nfunction ", pf_at + 1)
    pf_body = pane_code[pf_at:pf_end] if pf_at != -1 and pf_end > pf_at else ""
    pf_props = pf_body[pf_body.find("({"):pf_body.find("})") + 2] if "({" in pf_body else ""
    check(
        "[Y] 3 the platform values render through a dedicated PlatformField "
        "component whose PROP LIST contains no editable flag and whose body "
        "contains no editable branch and no form control — a stronger "
        "guarantee than a prop that merely defaults to false, because there is "
        "nothing a caller could pass to turn one on",
        bool(pf_body)
        and "editable" not in pf_props
        and "editable.has(" not in pf_body
        and "<input" not in pf_body
        and "<select" not in pf_body
        and "onChange" not in pf_body,
        f"props={pf_props!r}, body={len(pf_body)} chars, zero controls",
    )
    check(
        "[Y] 3 the grid's global-identifier cell is likewise read-only by "
        "construction — no editable prop, no onChange",
        "function GlobalIdentifierCell(" in grid_code
        and "onChange" not in grid_code[
            grid_code.find("function GlobalIdentifierCell("):
            grid_code.find("function GlobalIdentifierCell(") + 1400
        ],
        "GlobalIdentifierCell renders text only",
    )
    check(
        "[Y] 5 the pane embeds the REAL existing DocumentsPanel and takes the "
        "record_type from the API response rather than hardcoding the string",
        'from "@/components/DocumentsPanel"' in pane
        and "data.document_record_type" in pane
        and f'"{RECORD_TYPE_ASSET}"' not in pane,
        "recordType={data.document_record_type}",
    )
    check(
        "[Y] 5 the pane STATES that the platform half is shared and read-only "
        "rather than leaving the user to infer it from an absent box — the "
        "name/currency collision makes silence actively misleading",
        "shared" in pane and "read-only" in pane
        and "securities_global" in pane,
        "platform block carries an explicit explanation",
    )
    check(
        "[Y] 4 the grid sorts money NUMERICALLY, not lexically — derived sort "
        "keys are built from the exact decimal strings",
        "_value" in grid and "_price" in grid and "function num(" in grid,
        "_value / _price derived from num()",
    )
    check(
        "[Y] 6 the grid does NOT swap the row id after an edit — an asset "
        "keeps its id, and a client that re-keyed here would be inventing a "
        "change the API did not make",
        "r.id === row.id ? updated : r" in grid_code
        and "detail.restated_from" not in grid_code,
        "row patched in place, not replaced by a successor id",
    )
    # The Sidebar link, so the screen is actually reachable.
    sidebar = read(os.path.join(_WEB, "components", "Sidebar.jsx"))
    check(
        "[Y] 4 the screen is reachable from the nav — a page nobody can "
        "navigate to is not shipped",
        '"/portfolio/securities"' in sidebar,
        "Sidebar NAV_ITEMS carries the Securities entry",
    )


def check_npm_build() -> None:
    """`npm run build` must exit 0. Real build, not a lint."""
    deps = _node_modules_dir()
    if deps is None:
        # NOT a pass. A build that never ran and a build that succeeded are
        # different outcomes, and collapsing them is how a broken tree ships.
        check("[Y] npm run build exits 0", False,
              "node_modules is absent from apps/web AND the workspace root — "
              "the build was NOT measured, which is not the same as passing")
        return
    proc = subprocess.run(
        ["npm", "run", "build"],
        cwd=_WEB, capture_output=True, text=True, timeout=1800,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-10:]
    check(
        "[Y] npm run build exits 0",
        proc.returncode == 0,
        f"exit={proc.returncode}, deps at {os.path.relpath(deps, _WEB)}" + (
            "" if proc.returncode == 0 else " | " + " / ".join(tail)
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    app_url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1

    admin_conn = await asyncpg.connect(db_url, statement_cache_size=0,
                                       ssl="require")
    role = await open_non_bypass_role(db_url, app_url)
    if role is None:
        print("[FAIL] no non-bypass role is reachable by either path. Every "
              "cross-org and RLS assertion is meaningless under a bypassrls "
              "role, so this script fails rather than pretending.")
        await admin_conn.close()
        return 1

    baseline: dict[str, int] = {}
    try:
        await teardown(admin_conn)                                    # START
        baseline = await counts(admin_conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        report(
            "TEARDOWN is by-fixture, never a truncate",
            f"portfolio.securities_global holds the 67-row live EDGAR corpus "
            f"and 64 identifiers. Fixtures are matched through the {TAG!r} tag "
            f"on asset / security / entity / document names, with an exact "
            f"before/after count on {len(TABLES)} tables as the backstop.",
        )

        # Facts read from the DEPLOYED database, for the Task 1 findings.
        role_perms: dict[str, set[str]] = {}
        for r in await admin_conn.fetch(
            """
            SELECT r.name AS role, p.name AS perm
            FROM roles r
            JOIN role_permissions rp ON rp.role_id = r.id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE p.name IN ('view_portfolio', 'manage_portfolio')
            """
        ):
            role_perms.setdefault(r["role"], set()).add(r["perm"])

        policies = [dict(r) for r in await admin_conn.fetch(
            """
            SELECT tablename, policyname, cmd FROM pg_policies
            WHERE schemaname = 'portfolio'
              AND tablename IN ('assets', 'asset_identifiers',
                                'securities_global',
                                'securities_global_identifiers',
                                'securities_global_prices',
                                'securities_global_relationships')
            """
        )]
        app_role = await admin_conn.fetchval("SELECT current_user")
        bypasses = await admin_conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )

        async def cols(table: str) -> list[str]:
            return [r["column_name"] for r in await admin_conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'portfolio' AND table_name = $1 "
                "ORDER BY ordinal_position", table,
            )]

        asset_cols = await cols("assets")
        global_cols = {
            t: await cols(t) for t in (
                "securities_global", "securities_global_identifiers",
                "securities_global_prices", "asset_identifiers",
            )
        }

        print("\n── Task 1: DISCOVERY ──")
        import main as _main  # noqa: F401 — imported for the route table
        check_task1a(_routes_declared())
        check_task1b()
        check_task1c(role_perms)
        check_task1d(policies, app_role, bypasses)
        check_task1e(asset_cols, global_cols)
        check_schema_qualification()
        check_no_org_id_from_body()
        check_dynamic_sql_is_safe()

        print("\n── Fixtures ──")
        await seed_users(admin_conn)
        ids = await seed(admin_conn)
        direct = await direct_reads(admin_conn, ids)
        print("   seeded: 2 global securities (1 equity + 1 structured note, "
              "2 identifiers, 1 price), 4 assets (3 org A / 1 org B, 1 "
              "unlinked), 3 valuations, 2 positions, 1 linked document")

        # ── The fixtures' tiers, asserted BEFORE anything relies on them ──
        #
        # Read with direct SQL rather than by calling ``rbac.has_permission``.
        # That function takes the APPLICATION's pool, and creating that pool
        # here — on this loop — poisons the TestClient pass below, which runs in
        # an executor thread with its own event loop and would inherit a pool
        # bound to this one ("attached to a different loop", surfacing as a 500
        # from every endpoint). The SQL below is the same join
        # ``get_user_permissions`` runs, and the EFFECTIVE behaviour is asserted
        # separately and for real by the 200-read / 403-write pair through the
        # ASGI app.
        async def _perms(user_id: str) -> set[str]:
            rows = await admin_conn.fetch(
                """
                SELECT p.name FROM user_roles ur
                JOIN role_permissions rp ON rp.role_id = ur.role_id
                JOIN permissions p ON p.id = rp.permission_id
                WHERE ur.user_id = $1::uuid
                """,
                user_id,
            )
            return {r["name"] for r in rows}

        async def _role_count(user_id: str) -> int:
            return await admin_conn.fetchval(
                "SELECT count(*) FROM user_roles WHERE user_id = $1::uuid", user_id
            )

        v_perms, a_perms = await _perms(V_USER_ID), await _perms(A_USER_ID)
        v_roles, a_roles = await _role_count(V_USER_ID), await _role_count(A_USER_ID)
        check(
            "[Y] 3 the fixtures really do occupy different permission tiers, "
            "and BOTH actually hold a role. rbac.has_permission DEFAULT-ALLOWS "
            "a user with ZERO rows in user_roles, so a role-less 'view-only' "
            "fixture would silently hold manage_portfolio and every refusal "
            "below would pass in the wrong direction",
            v_roles > 0 and a_roles > 0
            and READ_PERMISSION in v_perms and WRITE_PERMISSION not in v_perms
            and WRITE_PERMISSION in a_perms,
            f"viewer: {v_roles} role(s), portfolio perms="
            f"{sorted(v_perms & {READ_PERMISSION, WRITE_PERMISSION})}; "
            f"admin: {a_roles} role(s), portfolio perms="
            f"{sorted(a_perms & {READ_PERMISSION, WRITE_PERMISSION})}",
        )
        su_role = await admin_conn.fetchval(
            "SELECT role FROM public.users WHERE id = $1::uuid", S_USER_ID
        )
        check(
            "[Y] 3 the super-admin fixture is one because users.role says so — "
            "which is what rbac.is_super_admin reads, NOT user_roles. The two "
            "are different systems and this sprint needs both",
            su_role == "super_admin",
            f"users.role={su_role!r}, and its user_roles grant is 'member' "
            f"(view only) — so any global write it makes came from the role "
            f"column and not from a permission grant",
        )

        print("\n── Tasks 2-6: the real endpoints, driven through the ASGI app ──")
        loop = asyncio.get_running_loop()
        out = await loop.run_in_executor(None, endpoint_tests, ids, direct)

        print("\n── The rows agree with a direct read ──")
        await check_archive_rows(admin_conn, ids)
        await check_service_layer(admin_conn, ids)

        print(f"\n── Both boundaries at the DB layer, under the real "
              f"app_service role (via {role.mode}) ──")
        await check_rls_isolation(role, ids)

        print("\n── Frontend wiring ──")
        check_frontend_wiring()

        print("\n── npm run build ──")
        await loop.run_in_executor(None, check_npm_build)

    finally:
        await teardown(admin_conn)                                    # END
        if baseline:
            final = await counts(admin_conn)
            drift = {t: (baseline[t], final[t]) for t in TABLES
                     if baseline[t] != final[t]}
            check(
                "[Y] TEARDOWN restores the EXACT before-count on every table "
                "touched — zero leftover rows",
                not drift,
                f"drift (before, after): {drift}" if drift
                else ", ".join(f"{t.split('.')[-1]}={final[t]}" for t in TABLES),
            )
        await role.conn.close()
        await admin_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}")
    print(f"FINDINGS REPORTED: {len(findings)}")
    print(f"RESULT: {passed}/{total} passed")
    failures = [(n, d) for n, ok, d in results if not ok]
    if failures:
        print("\nFAILURES:")
        for name, detail in failures:
            print(f"  · {name} — {detail}")
    print("=" * 72)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
