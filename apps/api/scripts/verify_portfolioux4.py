"""Verification — Portfolio UX 4: permissions retrofit on Positions + Transactions.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END with an
EXACT before/after count on every table touched — never a truncate, because
these tables hold real production rows.

Real database, real ASGI app. The harness is UX 1 / UX 2 / UX 3's, deliberately:
a fourth, differently-shaped harness for the same kind of sprint would be a
fourth thing to keep honest.

────────────────────────────────────────────────────────────────────────────
THE ASSERTIONS THIS SPRINT IS EASIEST TO FAKE, AND HOW THEY ARE WRITTEN
────────────────────────────────────────────────────────────────────────────
**"A view-only user is refused."** An endpoint that refuses EVERYBODY passes
this trivially. Every refusal below is paired with a CONTROL: the same call,
same body, same row, made by a user who does hold ``manage_portfolio``,
asserted to SUCCEED. A gate that is merely broken fails the control.

**"A view-only user has no roles, so the check is vacuous."** THE TRAP FOUND IN
UX 3, and not repeated here. ``rbac.has_permission`` DEFAULT-ALLOWS a user with
zero rows in ``user_roles`` (single-admin stage), so a fixture with no role
holds ``manage_portfolio`` and every refusal test passes in the WRONG
direction. Both write-tier fixtures are given REAL DEPLOYED roles — ``member``
(view only) and ``admin`` (both) — and this script ASSERTS their effective
permission sets, read out of ``role_permissions``, BEFORE anything relies on
them.

**"Super admin bypasses."** Asserted on its own, never inferred from the write
tests passing. The super-admin fixture's ``user_roles`` grant is ``member`` —
VIEW ONLY — so its granular permission set does NOT contain
``manage_portfolio``. A successful write by that fixture can therefore only
have come from the ``is_super_admin`` bypass and not from a grant. Asserting it
via an ``admin``-roled super admin would prove nothing, because the grant alone
would carry the call.

**"The UI hides the controls."** Two claims that can fail SEPARATELY, checked
SEPARATELY, because this sprint exists to rule out both halves:
  · a HIDDEN BUTTON over an UNPROTECTED ENDPOINT — every write endpoint in both
    routers is asserted, from the AST, to pass ``WRITE_PERMISSION`` to the gate;
  · a PROTECTED ENDPOINT behind a VISIBLE BUTTON — every write control in the
    four components is asserted to sit inside a branch on a SERVER-published
    flag, with the ACTUAL envelope the view-only fixture received asserted to
    make every one of those branches false.
The second half is fed the REAL response body, not a hand-written one.

**"Cross-org isolation still holds."** A REGRESSION check, not a new finding —
UX 1 and UX 2 proved it and this sprint must not have broken it. An endpoint
that returns nothing for everybody would pass a one-directional check, so both
directions are asserted against the SAME call.

Run:
    python3 scripts/verify_portfolioux4.py
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
_REPO = os.path.join(_HERE, "..", "..", "..")
sys.path.insert(0, _API)
sys.path.extend(sorted(glob.glob(
    os.path.join(_API, "venv", "lib", "python3*", "site-packages")
)))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_API, ".env"), override=False)

from services.portfolio_assets import (  # noqa: E402
    TABLE_ASSETS,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    TABLE_VALUATIONS,
    create_asset,
    create_position,
    record_transaction,
)
from services.portfolio_positions import (  # noqa: E402
    EDITABLE_FIELDS,
    INLINE_EDITABLE_FIELDS,
    READ_PERMISSION,
    TABLE_DOC_RECORD_LINKS,
    WRITE_PERMISSION,
)
from services.portfolio_transactions import (  # noqa: E402
    CORRECTABLE_FIELDS,
    INLINE_CORRECTABLE_FIELDS,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org. A real row, not a minted one — an isolation test against
# an org that does not exist proves the FK, not the policy.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "VERIFY-PORTFOLIOUX4"

A_SUB = "auth0|verify_portfolioux4_admin"       # org A, role 'admin'  → BOTH
V_SUB = "auth0|verify_portfolioux4_viewonly"    # org A, role 'member' → view ONLY
S_SUB = "auth0|verify_portfolioux4_superadmin"  # users.role='super_admin',
#                                                 user_roles grant 'member'
B_SUB = "auth0|verify_portfolioux4_orgb"        # org B, role 'admin'

# `services.permissions.get_user_id` DERIVES the id from the sub rather than
# looking it up, so a fixture seeded under a hand-picked literal is a user no
# code path ever finds (Portfolio C's finding).
A_USER_ID = str(uuid5(NAMESPACE_URL, A_SUB))
V_USER_ID = str(uuid5(NAMESPACE_URL, V_SUB))
S_USER_ID = str(uuid5(NAMESPACE_URL, S_SUB))
B_USER_ID = str(uuid5(NAMESPACE_URL, B_SUB))

TODAY = date(2026, 8, 25)

BUY_QTY = Decimal("1000.00")
BUY_PRICE = Decimal("12.00")
BUY_GROSS = Decimal("12000.00")

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


def _strip_js_comments(src: str) -> str:
    """Executable JSX only.

    Only ever used to make an ABSENCE assertion stricter — a component that
    EXPLAINS a rule in a comment must not flag its own explanation, which is the
    false positive that trains the next person to delete the check rather than
    the bug.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ── Tables, in FK-safe teardown order (children first) ──────────────────────
TABLES = (
    TABLE_DOC_RECORD_LINKS,
    TABLE_TRANSACTIONS,
    TABLE_POSITIONS,
    TABLE_VALUATIONS,
    TABLE_ASSETS,
    "public.entities",
    "public.user_roles",
    "public.users",
)

SUBS = [A_SUB, V_SUB, S_SUB, B_SUB]


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def teardown(conn) -> None:
    """Delete every fixture row, children first. Touches nothing else.

    Matched through the TAGGED asset / entity names, never by org — org A is a
    real production org and org B is Hollisworks, and both are full of real
    rows. Transactions carry no name of their own, so they are reached through
    their tagged asset's positions.

    ``portfolio.transactions`` has a SELF-referencing FK
    (``related_transaction_id``), which a correction populates. ONE delete
    statement removes a whole chain safely: referential-integrity triggers for a
    NO ACTION constraint fire at end-of-statement, so parent and child going
    together is fine — two statements in the wrong order would not be.
    """
    tagged_assets = f"SELECT id FROM {TABLE_ASSETS} WHERE name LIKE '{TAG}%'"
    tagged_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({tagged_assets})"
    )
    tagged_txns = (
        f"SELECT id FROM {TABLE_TRANSACTIONS} "
        f"WHERE position_id IN ({tagged_positions})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_DOC_RECORD_LINKS} "
        f"WHERE record_id IN ({tagged_positions}) "
        f"   OR record_id IN ({tagged_txns})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_TRANSACTIONS} WHERE position_id IN ({tagged_positions})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN ({tagged_assets})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_VALUATIONS} WHERE asset_id IN ({tagged_assets})"
    )
    await conn.execute(f"DELETE FROM {TABLE_ASSETS} WHERE name LIKE '{TAG}%'")
    await conn.execute(
        "DELETE FROM public.entities WHERE display_name LIKE $1", f"{TAG}%"
    )
    await conn.execute(
        "DELETE FROM public.user_roles WHERE user_id IN "
        "(SELECT id FROM public.users WHERE auth0_sub = ANY($1::text[]))",
        SUBS,
    )
    await conn.execute(
        "DELETE FROM public.users WHERE auth0_sub = ANY($1::text[])", SUBS
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the FOUR findings, REPORTED and ASSERTED
# ═══════════════════════════════════════════════════════════════════════════

R_SEC = os.path.join(_API, "routers", "portfolio_securities.py")
R_POS = os.path.join(_API, "routers", "portfolio_positions.py")
R_TXN = os.path.join(_API, "routers", "portfolio_transactions.py")

G_SEC = os.path.join(_WEB, "components", "portfolio", "SecuritiesGrid.jsx")
P_SEC = os.path.join(_WEB, "components", "portfolio", "AssetDetailPane.jsx")
G_POS = os.path.join(_WEB, "components", "portfolio", "PositionsGrid.jsx")
P_POS = os.path.join(_WEB, "components", "portfolio", "PositionDetailPane.jsx")
G_TXN = os.path.join(_WEB, "components", "portfolio", "TransactionsGrid.jsx")
P_TXN = os.path.join(_WEB, "components", "portfolio", "TransactionDetailPane.jsx")


def _gate_calls(src: str) -> dict[str, str]:
    """``{endpoint function name: the permission constant it gates on}``.

    Read from the AST, not by grepping, so a gate inside a comment, a docstring
    or a dead branch cannot satisfy it. A route function with no gate call at
    all maps to ``'NONE'`` — an explicit absence rather than a missing key, so a
    typo in a function name shows up as a FAIL and not as a silently skipped
    endpoint.
    """
    tree = ast.parse(src)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_route = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in node.decorator_list
        )
        if not is_route:
            continue
        found = "NONE"
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fname = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
            if fname not in ("_tenant_gate", "require_permission"):
                continue
            for arg in sub.args:
                if isinstance(arg, ast.Name) and arg.id in (
                    "READ_PERMISSION", "WRITE_PERMISSION",
                ):
                    found = arg.id
        out[node.name] = found
    return out


def check_task1a() -> None:
    """1a — the REAL shipped enforcement shape in the Securities router."""
    src = read(R_SEC)
    tree = ast.parse(src)
    fns = {n.name for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    # The conditional that IS the mechanism: the editable lists are cut to the
    # caller inside `_vocabularies`, not published whole and filtered later.
    voc = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_vocabularies"), None
    )
    voc_src = ast.get_source_segment(src, voc) if voc else ""
    conditional = bool(voc_src) and voc_src.count('if perms["can_write"] else []') >= 2

    gates = _gate_calls(src)
    tenant_reads = {"get_securities", "get_security_detail", "get_security_versions"}
    tenant_writes = {"create_security_endpoint", "patch_security"}

    report(
        "1a — routers/portfolio_securities.py: the shape UX 4 copies",
        f"`_tenant_gate(request, permission)` resolves (org_id, user_id, pool) "
        f"from JWT claims and calls rbac.require_permission — 403 with the "
        f"permission NAME in the detail. `_permission_envelope(pool, user_id, "
        f"org_id)` publishes {{can_read, can_write, can_write_global, "
        f"is_super_admin, read_permission, write_permission}}. `_vocabularies` "
        f"is where the real work happens: `editable` and `inline_editable` are "
        f"`[] if not can_write`. The envelope rides on EVERY response — list, "
        f"detail, create and patch — because the pane fetches the detail "
        f"endpoint on its own and must not be told by its parent what the "
        f"caller may do.",
    )
    check(
        "[Y] 1a Securities' gate + envelope + permission-aware vocabularies all "
        "exist, and the editable lists really are conditional on can_write — "
        "that conditional IS the mechanism, not the envelope",
        {"_tenant_gate", "_permission_envelope", "_vocabularies"} <= fns
        and conditional,
        f"helpers present={sorted({'_tenant_gate', '_permission_envelope', '_vocabularies'} & fns)}, "
        f"conditional-editable={conditional}",
    )
    check(
        "[Y] 1a every Securities TENANT endpoint gates, reads on "
        "view_portfolio and writes on manage_portfolio — read from the AST, so "
        "a gate in a comment or a dead branch cannot satisfy it",
        all(gates.get(f) == "READ_PERMISSION" for f in tenant_reads)
        and all(gates.get(f) == "WRITE_PERMISSION" for f in tenant_writes),
        f"reads={[(f, gates.get(f)) for f in sorted(tenant_reads)]} "
        f"writes={[(f, gates.get(f)) for f in sorted(tenant_writes)]}",
    )


def check_task1b() -> None:
    """1b — how the Securities FRONTEND consumes the published flags."""
    grid = _strip_js_comments(read(G_SEC))
    pane = _strip_js_comments(read(P_SEC))

    report(
        "1b — SecuritiesGrid.jsx + AssetDetailPane.jsx: the consumption shape",
        f"Grid: `permissions = meta?.permissions`, `canWrite = "
        f"!!permissions?.can_write`, `inlineEditable = new "
        f"Set(vocabularies?.inline_editable || [])`. Pane: the same two reads "
        f"off its OWN fetch (`data?.permissions` / `data?.vocabularies`), then "
        f"`canWrite` gates the entire Save/Cancel toolbar and every field "
        f"renders an input ONLY inside an `editable.has(field)` branch "
        f"({pane.count('editable.has(')} such branches). The ONLY fallback "
        f"anywhere is the EMPTY array — there is no `|| DEFAULTS`, because a "
        f"defaults list is exactly what survives a missing envelope and puts "
        f"the controls back.",
    )
    check(
        "[Y] 1b the Securities frontend reads the SERVER's flags and keeps no "
        "permission logic of its own — grid and pane both",
        "permissions?.can_write" in grid
        and "vocabularies?.inline_editable" in grid
        and "vocabularies?.editable" in pane
        and pane.count("editable.has(") >= 8,
        f"grid canWrite + inline_editable; pane editable.has() x"
        f"{pane.count('editable.has(')}",
    )
    # The claim is about the FALLBACK, and it is checked as an absence of a
    # non-empty literal rather than a presence of `|| []` — the second would
    # pass on a file that had both.
    bad = re.findall(r"\|\|\s*\[\s*[\"'{]", grid + pane)
    check(
        "[Y] 1b there is NO client-side fallback LIST in either Securities "
        "component — every `||` fallback on a published list is the EMPTY array",
        not bad,
        f"non-empty fallback literals found: {bad or 'none'}",
    )


def check_task1c() -> None:
    """1c — what Positions and Transactions ACTUALLY had before this sprint.

    Read out of git, not asserted from memory and not taken from the prompt.
    The prompt's premise ("shipped with NO permission gating at all") is
    materially wrong in one direction and right in another, and reporting it
    accurately is the whole point of a discovery task.
    """
    def before(path: str) -> str:
        rel = os.path.relpath(path, _REPO).replace(os.sep, "/")
        try:
            return subprocess.run(
                ["git", "show", f"HEAD:{rel}"], cwd=_REPO,
                capture_output=True, text=True, timeout=120,
            ).stdout
        except Exception:  # noqa: BLE001 — a missing git is a measurement gap
            return ""

    pos_before, txn_before = before(R_POS), before(R_TXN)
    if not pos_before or not txn_before:
        check(
            "[Y] 1c the pre-sprint state of both routers was READ from git, "
            "not assumed",
            False,
            "git show failed — the before-state was NOT measured, which is not "
            "the same as it being what the prompt claimed",
        )
        return

    pos_gates, txn_gates = _gate_calls(pos_before), _gate_calls(txn_before)
    all_gated = (
        all(v != "NONE" for v in pos_gates.values())
        and all(v != "NONE" for v in txn_gates.values())
    )
    # ...and yet neither published an envelope, and both published the editable
    # lists to everybody. That is the actual hole.
    no_envelope = (
        '"permissions"' not in pos_before and '"permissions"' not in txn_before
    )
    unconditional = (
        '"editable": sorted(EDITABLE_FIELDS),' in pos_before
        and '"correctable": sorted(CORRECTABLE_FIELDS),' in txn_before
    )

    report(
        "1c — CORRECTION to the sprint premise: both routers were ALREADY "
        "gated. The hole was the other half",
        f"The prompt states Positions and Transactions 'shipped with NO "
        f"permission gating at all — org isolation only'. That is not what is "
        f"in the tree. At HEAD, all {len(pos_gates)} Positions endpoints and "
        f"all {len(txn_gates)} Transactions endpoints already called "
        f"rbac.require_permission with the correct constant, and a view-only "
        f"caller already got a real 403 on a write.\n"
        f"       What was genuinely missing: NEITHER router published a "
        f"`permissions` block anywhere, and BOTH published "
        f"`vocabularies.editable` / `.correctable` UNCONDITIONALLY — the full "
        f"field list, to every caller, regardless of permission. So the grids "
        f"rendered an editable taxonomy picker, a live reconciled checkbox, "
        f"inline settle-date and reference inputs, and both panes rendered a "
        f"Save/Correct button and a full form, for a caller the server would "
        f"then refuse. Every one of those controls led to a 403 the user had no "
        f"way to anticipate.\n"
        f"       PositionDetailPane also carried a REAL client-side fallback "
        f"list — `vocabularies?.ownership_basis || [\"units\",\"percent\","
        f"\"value\"]` — which is the exact `|| DEFAULTS` pattern UX 3 forbade.\n"
        f"       This sprint is therefore a PUBLISH-AND-HONOUR retrofit, not an "
        f"add-the-gate retrofit. Task 2/3 add the envelope, cut the editable "
        f"lists to the caller, and make the four components read them.",
    )
    check(
        "[Y] 1c the pre-sprint routers really were gated on every endpoint — "
        "the premise's 'no permission gating at all' is corrected, from git, "
        "rather than restated",
        all_gated,
        f"positions before: {sorted(pos_gates.items())}; "
        f"transactions before: {sorted(txn_gates.items())}",
    )
    check(
        "[Y] 1c ...and the REAL hole is confirmed at the same commit: no "
        "permissions envelope on either router, and the editable/correctable "
        "lists published unconditionally to every caller",
        no_envelope and unconditional,
        f"no envelope={no_envelope}, unconditional editable lists={unconditional}",
    )
    # And the fallback list is really gone NOW.
    pane_now = _strip_js_comments(read(P_POS))
    check(
        "[Y] 1c the client-side `|| [\"units\",\"percent\",\"value\"]` fallback "
        "is GONE from PositionDetailPane — a hardcoded vocabulary is what "
        "survives an empty envelope and puts a control back",
        '"units", "percent", "value"' not in pane_now
        and "vocabularies?.ownership_basis || []" in pane_now,
        "the only fallback on ownership_basis is the empty array",
    )


def check_task1d(role_perms: dict[str, set[str]]) -> None:
    """1d — the REAL deployed grants, read from role_permissions."""
    view_roles = sorted(r for r, p in role_perms.items() if READ_PERMISSION in p)
    write_roles = sorted(r for r, p in role_perms.items() if WRITE_PERMISSION in p)
    read_only = sorted(set(view_roles) - set(write_roles))

    report(
        "1d — deployed role→permission grants, read from the live database",
        f"view_portfolio → {view_roles}\n"
        f"       manage_portfolio → {write_roles}\n"
        f"       Read-only roles (view WITHOUT manage): {read_only}. So 'can "
        f"read but cannot write' is a real deployed state and not a fixture "
        f"invented for this script.\n"
        f"       CORRECTION to the prompt's 1d list: it names view=admin/"
        f"advisor/investment_staff/member/super_admin, omitting "
        f"'support_staff', which also holds view_portfolio in the deployed "
        f"grants. manage_portfolio matches exactly.\n"
        f"       THE TRAP, restated so it is not re-fallen-into: "
        f"services.rbac.has_permission DEFAULT-ALLOWS a user with ZERO rows in "
        f"user_roles. A 'view-only' fixture with no role would therefore hold "
        f"manage_portfolio and every refusal below would pass in the wrong "
        f"direction. This script gives 'member' to the view-only fixture and "
        f"'admin' to the write fixture, and asserts the resulting permission "
        f"sets before using them.",
    )
    check(
        "[Y] 1d the two permissions are really deployed, really distinct, and "
        "the read-only state is reachable with a REAL role — 'member' views "
        "without managing and 'admin' does both",
        READ_PERMISSION in role_perms.get("member", set())
        and WRITE_PERMISSION not in role_perms.get("member", set())
        and {READ_PERMISSION, WRITE_PERMISSION} <= role_perms.get("admin", set())
        and {READ_PERMISSION, WRITE_PERMISSION} <= role_perms.get("super_admin", set()),
        f"member={sorted(role_perms.get('member', set()))}, "
        f"admin={sorted(role_perms.get('admin', set()))}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASKS 2 / 3 / 4 — the fixed routers, at the AST layer
# ═══════════════════════════════════════════════════════════════════════════


def check_router_gates() -> None:
    """Every endpoint gates, and every WRITE gates on manage_portfolio.

    This is the "hidden button over an unprotected endpoint" half of the dual
    proof. It is deliberately structural rather than behavioural: the HTTP tests
    below exercise the endpoints a real screen calls, and a NEW write endpoint
    added later with no gate would slip past them while failing this.
    """
    for label, path, reads, writes in (
        (
            "Positions", R_POS,
            {"get_positions", "get_position_detail", "get_assets"},
            {"create_position_endpoint", "patch_position"},
        ),
        (
            "Transactions", R_TXN,
            {"get_transactions", "get_transaction_detail",
             "get_positions_for_picker"},
            {"create_transaction_endpoint", "correct_transaction_endpoint"},
        ),
    ):
        gates = _gate_calls(read(path))
        ungated = sorted(f for f, p in gates.items() if p == "NONE")
        check(
            f"[Y] 2/3 {label}: EVERY declared endpoint gates — none is "
            f"reachable on org isolation alone, and an endpoint added later "
            f"with no gate fails here rather than shipping",
            not ungated and len(gates) == len(reads | writes),
            f"{len(gates)} endpoints, ungated={ungated or 'none'}",
        )
        check(
            f"[Y] 2/3 {label}: reads gate on {READ_PERMISSION}, writes gate on "
            f"{WRITE_PERMISSION} — and the correction endpoint is a WRITE",
            all(gates.get(f) == "READ_PERMISSION" for f in reads)
            and all(gates.get(f) == "WRITE_PERMISSION" for f in writes),
            f"reads={[(f, gates.get(f)) for f in sorted(reads)]} "
            f"writes={[(f, gates.get(f)) for f in sorted(writes)]}",
        )


def check_super_admin_is_checked_first() -> None:
    """TASK 4 — the bypass is the codebase convention, checked FIRST.

    Asserted at the AST layer as well as behaviourally below, because the
    ORDER is the claim. ``rbac.has_permission`` must consult
    ``is_super_admin`` before it consults ``_has_any_role`` or
    ``get_user_permissions``; a super admin who happens to hold a role would
    otherwise fall through to the strict check and be locked out, and nothing
    about that failure is visible until somebody assigns them a role.
    """
    src = read(os.path.join(_API, "services", "rbac.py"))
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "has_permission"),
        None,
    )
    body = ast.get_source_segment(src, fn) if fn else ""
    su_at = body.find("is_super_admin(principal)")
    roles_at = body.find("_has_any_role(")
    perms_at = body.find("get_user_permissions(")
    check(
        "[Y] 4 rbac.has_permission checks is_super_admin FIRST — before the "
        "no-roles default-allow and before the granular permission lookup. The "
        "ORDER is the claim: a super admin holding any role would otherwise "
        "fall through to the strict check and be silently locked out",
        su_at != -1 and roles_at > su_at and perms_at > su_at,
        f"is_super_admin@{su_at} < _has_any_role@{roles_at} < "
        f"get_user_permissions@{perms_at}",
    )
    # Both fixed routers reach the bypass through that one function.
    for label, path in (("Positions", R_POS), ("Transactions", R_TXN)):
        code = read(path)
        check(
            f"[Y] 4 {label} reaches the bypass through the SHARED "
            f"rbac.has_permission / require_permission rather than "
            f"re-implementing an is_super_admin branch of its own — a second "
            f"copy of the escape hatch is a second thing that can drift",
            "from services.rbac import" in code
            and "require_permission" in code
            and not re.search(r"if\s+.*is_super_admin\(.*\)\s*:\s*\n\s*return True",
                              code),
            "no local bypass branch; both gates delegate",
        )


def check_no_org_id_from_body() -> None:
    """STANDING RULE — org_id never from a request body, in either router."""
    for label, path in (("Positions", R_POS), ("Transactions", R_TXN)):
        tree = ast.parse(read(path))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and \
                            getattr(stmt.target, "id", None) == "org_id":
                        offenders.append(f"{node.name}.org_id")
            if isinstance(node, ast.Attribute) and node.attr == "org_id" and \
                    isinstance(node.value, ast.Name) and node.value.id == "body":
                offenders.append("body.org_id")
        check(
            f"[Y] {label}: no request model declares org_id and no line reads "
            f"body.org_id — there is nothing for a caller to send and nothing "
            f"for a future edit to start trusting",
            not offenders,
            f"offenders: {offenders or 'none'}",
        )


def check_schema_qualification() -> None:
    """STANDING RULE — `portfolio` is NOT on app_service's search_path."""
    bad: list[str] = []
    for rel in ("services/portfolio_positions.py",
                "services/portfolio_transactions.py",
                "routers/portfolio_positions.py",
                "routers/portfolio_transactions.py"):
        src = read(os.path.join(_API, rel))
        for m in re.finditer(
            r"(?i)\b(FROM|JOIN|INTO|UPDATE)\s+(?!portfolio\.|public\.|\(|LATERAL|"
            r"UNNEST|generate_series|\{)([a-z_][a-z_0-9]*)\b", src
        ):
            table = m.group(2)
            if table in ("assets", "positions", "transactions", "valuations",
                         "asset_identifiers", "external_references"):
                bad.append(f"{rel}: {m.group(0)}")
    check(
        "[Y] every portfolio.* reference in the touched files is "
        "schema-qualified — `portfolio` is NOT on app_service's search_path, so "
        "a bare table name works as postgres and fails in production",
        not bad,
        f"unqualified: {bad or 'none'}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def seed_users(conn) -> None:
    """Four principals, each carrying BOTH permission systems deliberately.

    ``users.role`` is what ``rbac.is_super_admin`` reads. ``user_roles`` is what
    ``rbac.get_user_permissions`` reads. They are different systems and this
    sprint needs both, so each fixture sets each explicitly rather than relying
    on one to imply the other.

    The super-admin fixture's ``user_roles`` grant is ``member`` — VIEW ONLY —
    on purpose. Giving it ``admin`` would make its writes succeed on the grant
    and the bypass assertion would prove nothing.
    """
    for user_id, org, sub, users_role, role_name in (
        (A_USER_ID, DEFAULT_ORG_ID, A_SUB, "member", "admin"),
        (V_USER_ID, DEFAULT_ORG_ID, V_SUB, "member", "member"),
        (S_USER_ID, DEFAULT_ORG_ID, S_SUB, "super_admin", "member"),
        (B_USER_ID, OTHER_ORG_ID, B_SUB, "member", "admin"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users
                (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioUX4', $4, $5, true)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, org, f"{sub.split('|')[-1]}@test.local", sub, users_role,
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


async def _pick_txn_type(conn, market: str, *, prefer: str | None = None) -> str:
    """A REAL, active transaction type of the given market.

    Read from the deployed table rather than hardcoded: ``record_transaction``
    refuses a retired type and refuses a market mismatch, so a literal that
    happened to be renamed would fail the FIXTURE rather than the feature.
    """
    if prefer:
        code = await conn.fetchval(
            "SELECT code FROM public.transaction_types "
            "WHERE code = $1 AND is_active = true AND market = $2",
            prefer, market,
        )
        if code:
            return code
    code = await conn.fetchval(
        "SELECT code FROM public.transaction_types "
        "WHERE is_active = true AND market = $1 ORDER BY display_order, code "
        "LIMIT 1",
        market,
    )
    if code is None:  # pragma: no cover — the A2 backfill guarantees these
        raise RuntimeError(f"no active transaction_type with market={market!r}")
    return code


async def seed(conn) -> dict:
    """Two orgs, two assets, two positions, two transactions."""
    ids: dict = {}

    async def entity(org, name, etype="llc"):
        return str(await conn.fetchval(
            "INSERT INTO public.entities (org_id, entity_type, display_name) "
            "VALUES ($1::uuid, $2::entity_type, $3) RETURNING id",
            org, etype, name,
        ))

    ids["owner_a"] = await entity(DEFAULT_ORG_ID, f"{TAG} Alpha Trust", "trust")
    ids["owner_b"] = await entity(OTHER_ORG_ID, f"{TAG} OtherOrg LLC")

    ids["asset_a"] = await create_asset(
        conn, org_id=DEFAULT_ORG_ID, name=f"{TAG} Listed Equity",
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
    )
    ids["asset_b"] = await create_asset(
        conn, org_id=OTHER_ORG_ID, name=f"{TAG} OtherOrg Asset",
        asset_type="equity", ownership_basis="units",
        valuation_method="market_price", currency_code="USD",
    )

    ids["pos_a"] = await create_position(
        conn, org_id=DEFAULT_ORG_ID, owner_entity_id=ids["owner_a"],
        asset_id=ids["asset_a"], as_of_date=TODAY, authority="custodial",
        source_system="reporting_tool_bd", ownership_basis="units",
        quantity=BUY_QTY, cost_basis=BUY_GROSS, taxonomy_key="taxonomy_sc_1",
    )
    ids["pos_b"] = await create_position(
        conn, org_id=OTHER_ORG_ID, owner_entity_id=ids["owner_b"],
        asset_id=ids["asset_b"], as_of_date=TODAY, authority="custodial",
        source_system="manual", ownership_basis="units",
        quantity=Decimal("7.00"),
    )

    ids["type_buy"] = await _pick_txn_type(conn, "public", prefer="buy")

    ids["txn_a"] = await record_transaction(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["pos_a"],
        transaction_type_code=ids["type_buy"],
        trade_date=TODAY - timedelta(days=20), authority="custodial",
        source_system="reporting_tool_bd", quantity=BUY_QTY, price=BUY_PRICE,
        gross_amount=BUY_GROSS, net_amount=BUY_GROSS, currency_code="USD",
        external_ref=f"{TAG}-BUY-001",
    )
    ids["txn_b"] = await record_transaction(
        conn, org_id=OTHER_ORG_ID, position_id=ids["pos_b"],
        transaction_type_code=ids["type_buy"],
        trade_date=TODAY - timedelta(days=20), authority="custodial",
        source_system="manual", quantity=Decimal("7.00"),
        price=BUY_PRICE, gross_amount=Decimal("84.00"),
        net_amount=Decimal("84.00"), currency_code="USD",
        external_ref=f"{TAG}-B-BUY-001",
    )
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# TASK 5 — both screens, both directions, through the REAL ASGI app
# ═══════════════════════════════════════════════════════════════════════════


class _Principal:
    """Drives the real ASGI app as one specific user.

    ``verify_token`` is replaced, not the auth dependency: the request still
    passes through the RLS context middleware, the active-account gate and
    ``require_permission`` exactly as production does. Stubbing further up would
    skip the layers most likely to be wrong — which on THIS sprint is the whole
    point, since the layers most likely to be wrong ARE the permission layers.

    All four principals share ONE ``TestClient``, entered as a context manager.
    Used without ``with``, TestClient spins up a fresh portal — and a fresh
    event loop — for EVERY request, and the application's connection pool is a
    module global bound to the loop that created it. Request 1 succeeds and
    every request after it returns 500 from inside the RLS middleware, which
    reads as "the endpoint is broken" rather than "the harness is". Handing each
    principal its own client has the same problem for the same reason.
    """

    __slots__ = ("client", "org_id", "sub", "label")

    def __init__(self, client, org_id: str, sub: str, label: str):
        self.client = client
        self.org_id = org_id
        self.sub = sub
        self.label = label

    def _become(self) -> None:
        import main

        sub, org_id = self.sub, self.org_id
        main.verify_token = lambda _token: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org_id,
        }

    def get(self, url, **kw):
        self._become()
        return self.client.get(url, headers=HEADERS, **kw)

    def post(self, url, **kw):
        self._become()
        return self.client.post(url, headers=HEADERS, **kw)

    def patch(self, url, **kw):
        self._become()
        return self.client.patch(url, headers=HEADERS, **kw)


HEADERS = {"Authorization": "Bearer verify-token"}


def endpoint_tests(ids: dict) -> dict:
    """Everything that has to go through HTTP. Sync — TestClient is sync."""
    import main
    from starlette.testclient import TestClient

    shared = TestClient(main.app, raise_server_exceptions=False)
    shared.__enter__()
    try:
        return _endpoint_tests(shared, ids)
    finally:
        shared.__exit__(None, None, None)


def _position_body(ids: dict) -> dict:
    return {
        "owner_entity_id": ids["owner_a"],
        "asset_id": ids["asset_a"],
        "as_of_date": TODAY.isoformat(),
        "authority": "custodial",
        "source_system": "manual",
        "ownership_basis": "units",
        "quantity": "42.00",
    }


def _transaction_body(ids: dict) -> dict:
    return {
        "position_id": ids["pos_a"],
        "transaction_type_code": ids["type_buy"],
        "trade_date": TODAY.isoformat(),
        "authority": "custodial",
        "source_system": "manual",
        "quantity": "5.00",
        "price": "12.00",
        "gross_amount": "60.00",
        "net_amount": "60.00",
        "currency_code": "USD",
    }


def _refused_for(res, permission: str) -> bool:
    """A 403 that NAMES the permission. A bare 403 is not good enough.

    A 401 would mean the request never reached the gate, a 404 would mean the
    endpoint was hidden rather than refused, and a 403 with no permission name
    leaves the user unable to tell which grant they are missing.
    """
    if res.status_code != 403:
        return False
    detail = str(res.json().get("detail", ""))
    return permission in detail


def _endpoint_tests(client, ids: dict) -> dict:
    out: dict = {}

    view = _Principal(client, DEFAULT_ORG_ID, V_SUB, "view-only member")
    admin = _Principal(client, DEFAULT_ORG_ID, A_SUB, "admin")
    superu = _Principal(client, DEFAULT_ORG_ID, S_SUB, "super_admin")
    orgb = _Principal(client, OTHER_ORG_ID, B_SUB, "org B admin")

    # ══════════════════════════════════════════════════════════════════════
    # POSITIONS
    # ══════════════════════════════════════════════════════════════════════
    print("\n── TASK 5: POSITIONS ──")

    v_list = view.get(f"/api/v1/portfolio/positions?search={TAG}")
    v_body = v_list.json() if v_list.status_code == 200 else {}
    v_perms = v_body.get("permissions", {})
    v_voc = v_body.get("vocabularies", {})
    out["pos_view_envelope"] = {"permissions": v_perms, "vocabularies": v_voc}

    check(
        "[Y] POSITIONS view-only READS the grid — 200, with this org's real "
        "rows. A screen that refused the read too would pass every write "
        "refusal below for the wrong reason",
        v_list.status_code == 200
        and any(r["id"] == ids["pos_a"] for r in v_body.get("positions", [])),
        f"status={v_list.status_code} rows={v_body.get('returned')}",
    )
    check(
        "[Y] POSITIONS the server PUBLISHES the refusal in advance — "
        "can_read=true, can_write=false, and the permission NAMES so the UI can "
        "say which grant is missing rather than just 'read-only'",
        v_perms.get("can_read") is True
        and v_perms.get("can_write") is False
        and v_perms.get("read_permission") == READ_PERMISSION
        and v_perms.get("write_permission") == WRITE_PERMISSION,
        f"{v_perms}",
    )
    check(
        "[Y] POSITIONS the published editable list is EMPTY for the view-only "
        "caller — both `editable` and `inline_editable`. This is the mechanism, "
        "not the envelope: the components render a control per entry in these "
        "lists, so empty means no controls",
        v_voc.get("editable") == [] and v_voc.get("inline_editable") == [],
        f"editable={v_voc.get('editable')} "
        f"inline_editable={v_voc.get('inline_editable')}",
    )
    check(
        "[Y] POSITIONS the READ vocabularies are still published to the "
        "view-only caller — filtering and sorting are reads, and emptying those "
        "too would have broken the screen rather than secured it",
        bool(v_voc.get("authority")) and bool(v_voc.get("source_system"))
        and bool(v_voc.get("ownership_basis")) and bool(v_voc.get("superseded")),
        f"authority={len(v_voc.get('authority', []))} "
        f"source_system={len(v_voc.get('source_system', []))} "
        f"ownership_basis={len(v_voc.get('ownership_basis', []))}",
    )

    v_detail = view.get(f"/api/v1/portfolio/positions/{ids['pos_a']}")
    vd = v_detail.json() if v_detail.status_code == 200 else {}
    check(
        "[Y] POSITIONS view-only READS the detail pane — 200, with the "
        "restatement history, and the SAME empty editable list published on the "
        "pane's own response rather than only on the grid's",
        v_detail.status_code == 200
        and vd.get("permissions", {}).get("can_write") is False
        and vd.get("vocabularies", {}).get("editable") == []
        and "restatement_history" in vd,
        f"status={v_detail.status_code} "
        f"can_write={vd.get('permissions', {}).get('can_write')} "
        f"editable={vd.get('vocabularies', {}).get('editable')}",
    )

    v_create = view.post("/api/v1/portfolio/positions", json=_position_body(ids))
    check(
        f"[Y] POSITIONS view-only is REFUSED the create — 403 naming "
        f"{WRITE_PERMISSION}. Not a 401 (never reached the gate), not a 404 "
        f"(hidden rather than refused), not a bare 403 (which grant?)",
        _refused_for(v_create, WRITE_PERMISSION),
        f"status={v_create.status_code} detail={v_create.json().get('detail')!r}",
    )
    v_patch = view.patch(
        f"/api/v1/portfolio/positions/{ids['pos_a']}", json={"quantity": "1.00"},
    )
    check(
        f"[Y] POSITIONS view-only is REFUSED the restatement — 403 naming "
        f"{WRITE_PERMISSION}, on the same row it can read",
        _refused_for(v_patch, WRITE_PERMISSION),
        f"status={v_patch.status_code} detail={v_patch.json().get('detail')!r}",
    )
    v_picker = view.get("/api/v1/portfolio/assets")
    check(
        "[Y] POSITIONS the asset PICKER is readable by the view-only caller "
        "but carries can_write=false — reading an asset list is a read; the "
        "create form built on top of it is not",
        v_picker.status_code == 200
        and v_picker.json().get("permissions", {}).get("can_write") is False,
        f"status={v_picker.status_code} "
        f"can_write={v_picker.json().get('permissions', {}).get('can_write')}",
    )

    # ── THE CONTROL. Same calls, same bodies, a caller who may write. ────
    a_list = admin.get(f"/api/v1/portfolio/positions?search={TAG}")
    a_body = a_list.json() if a_list.status_code == 200 else {}
    a_voc = a_body.get("vocabularies", {})
    check(
        "[Y] POSITIONS manage_portfolio caller gets can_write=true and a "
        "NON-EMPTY editable list matching the service's own set — the control "
        "for every refusal above. A gate that is merely broken fails here",
        a_body.get("permissions", {}).get("can_write") is True
        and set(a_voc.get("editable", [])) == EDITABLE_FIELDS
        and set(a_voc.get("inline_editable", [])) == INLINE_EDITABLE_FIELDS,
        f"can_write={a_body.get('permissions', {}).get('can_write')} "
        f"editable={len(a_voc.get('editable', []))}/{len(EDITABLE_FIELDS)} "
        f"inline={a_voc.get('inline_editable')}",
    )
    a_create = admin.post("/api/v1/portfolio/positions", json=_position_body(ids))
    created = a_create.json().get("position", {}).get("id") if \
        a_create.status_code == 201 else None
    check(
        "[Y] POSITIONS manage_portfolio caller GENUINELY writes — 201, with a "
        "real row id back, on the byte-identical body the view-only caller was "
        "refused",
        a_create.status_code == 201 and bool(created),
        f"status={a_create.status_code} new_id={created}",
    )
    a_patch = admin.patch(
        f"/api/v1/portfolio/positions/{ids['pos_a']}", json={"quantity": "1234.00"},
    )
    a_patched = a_patch.json() if a_patch.status_code == 200 else {}
    check(
        "[Y] POSITIONS manage_portfolio caller genuinely RESTATES — 200, a NEW "
        "position id back (Rule 3: the old row is closed, not updated), and the "
        "response's own envelope says can_write",
        a_patch.status_code == 200
        and a_patched.get("position", {}).get("id") not in (None, ids["pos_a"])
        and a_patched.get("restated_from") == ids["pos_a"]
        and a_patched.get("permissions", {}).get("can_write") is True,
        f"status={a_patch.status_code} "
        f"new_id={a_patched.get('position', {}).get('id')} "
        f"restated_from={a_patched.get('restated_from')}",
    )
    out["pos_current"] = a_patched.get("position", {}).get("id") or ids["pos_a"]

    # ── TASK 4: the bypass, asserted on its OWN terms ───────────────────
    s_list = superu.get(f"/api/v1/portfolio/positions?search={TAG}")
    s_perms = s_list.json().get("permissions", {}) if s_list.status_code == 200 else {}
    check(
        "[Y] 4 POSITIONS super_admin's envelope reports BOTH is_super_admin and "
        "can_write — even though its only user_roles grant is 'member', which "
        "does NOT carry manage_portfolio. can_write is true here solely because "
        "the bypass ran",
        s_perms.get("is_super_admin") is True and s_perms.get("can_write") is True,
        f"{s_perms}",
    )
    s_create = superu.post("/api/v1/portfolio/positions", json=_position_body(ids))
    s_created = s_create.json().get("position", {}).get("id") if \
        s_create.status_code == 201 else None
    check(
        "[Y] 4 POSITIONS super_admin BYPASSES the write gate — 201 on the same "
        "body a 'member'-granted caller was refused. This is an independent "
        "assertion, not an inference from the admin control passing: the admin "
        "holds manage_portfolio and this fixture does not",
        s_create.status_code == 201 and bool(s_created),
        f"status={s_create.status_code} new_id={s_created}",
    )
    s_read = superu.get(f"/api/v1/portfolio/positions/{out['pos_current']}")
    check(
        "[Y] 4 POSITIONS super_admin bypasses the READ gate on the same screen "
        "— both checks, not just the write one",
        s_read.status_code == 200,
        f"status={s_read.status_code}",
    )

    # ══════════════════════════════════════════════════════════════════════
    # TRANSACTIONS
    # ══════════════════════════════════════════════════════════════════════
    print("\n── TASK 5: TRANSACTIONS ──")

    tv_list = view.get(f"/api/v1/portfolio/transactions?search={TAG}")
    tv_body = tv_list.json() if tv_list.status_code == 200 else {}
    tv_perms = tv_body.get("permissions", {})
    tv_voc = tv_body.get("vocabularies", {})
    out["txn_view_envelope"] = {"permissions": tv_perms, "vocabularies": tv_voc}

    check(
        "[Y] TRANSACTIONS view-only READS the grid — 200, with this org's real "
        "ledger entries",
        tv_list.status_code == 200
        and any(r["id"] == ids["txn_a"] for r in tv_body.get("transactions", [])),
        f"status={tv_list.status_code} rows={tv_body.get('returned')}",
    )
    check(
        "[Y] TRANSACTIONS the server publishes can_read=true, can_write=false "
        "AND can_correct=false — correcting is published as its own right so a "
        "firm can later split 'may record' from 'may amend' without touching "
        "the client",
        tv_perms.get("can_read") is True
        and tv_perms.get("can_write") is False
        and tv_perms.get("can_correct") is False,
        f"{tv_perms}",
    )
    check(
        "[Y] TRANSACTIONS the published correctable lists are EMPTY for the "
        "view-only caller — both `correctable` and `inline_correctable`",
        tv_voc.get("correctable") == [] and tv_voc.get("inline_correctable") == [],
        f"correctable={tv_voc.get('correctable')} "
        f"inline_correctable={tv_voc.get('inline_correctable')}",
    )
    check(
        "[Y] TRANSACTIONS the type vocabulary is STILL published to the "
        "view-only caller — the grid renders type LABELS from it and offers a "
        "type filter, and both are reads",
        bool(tv_voc.get("transaction_type_code"))
        and bool(tv_voc.get("transaction_type_category"))
        and bool(tv_body.get("transaction_types")),
        f"{len(tv_voc.get('transaction_type_code', []))} codes, "
        f"{len(tv_voc.get('transaction_type_category', []))} categories",
    )

    tv_detail = view.get(f"/api/v1/portfolio/transactions/{ids['txn_a']}")
    tvd = tv_detail.json() if tv_detail.status_code == 200 else {}
    check(
        "[Y] TRANSACTIONS view-only SEES THE CORRECTION HISTORY but cannot add "
        "to it — the detail returns correction_history in full AND publishes an "
        "empty correctable list. Reading what was already corrected and "
        "correcting are separate rights, and the second does not follow from "
        "the first",
        tv_detail.status_code == 200
        and isinstance(tvd.get("correction_history"), list)
        and len(tvd.get("correction_history", [])) >= 1
        and tvd.get("permissions", {}).get("can_correct") is False
        and tvd.get("vocabularies", {}).get("correctable") == [],
        f"status={tv_detail.status_code} "
        f"history={len(tvd.get('correction_history', []))} entries, "
        f"can_correct={tvd.get('permissions', {}).get('can_correct')}",
    )

    tv_create = view.post("/api/v1/portfolio/transactions",
                          json=_transaction_body(ids))
    check(
        f"[Y] TRANSACTIONS view-only is REFUSED the create — 403 naming "
        f"{WRITE_PERMISSION}",
        _refused_for(tv_create, WRITE_PERMISSION),
        f"status={tv_create.status_code} detail={tv_create.json().get('detail')!r}",
    )
    tv_correct = view.post(
        f"/api/v1/portfolio/transactions/{ids['txn_a']}/corrections",
        json={"external_ref": f"{TAG}-VIEWONLY-SHOULD-NOT-EXIST"},
    )
    check(
        f"[Y] TRANSACTIONS view-only is REFUSED the CORRECTION endpoint "
        f"SPECIFICALLY — 403 naming {WRITE_PERMISSION} on "
        f"POST /transactions/{{id}}/corrections, the same entry it just read "
        f"the history of. This is its own assertion because the correction path "
        f"is a sub-resource with its own decorator, and a retrofit that gated "
        f"POST /transactions while missing this one would look complete",
        _refused_for(tv_correct, WRITE_PERMISSION),
        f"status={tv_correct.status_code} "
        f"detail={tv_correct.json().get('detail')!r}",
    )
    # And it really did not write one.
    tv_after = view.get(f"/api/v1/portfolio/transactions/{ids['txn_a']}")
    check(
        "[Y] TRANSACTIONS the refused correction wrote NOTHING — the entry's "
        "correction chain is unchanged. A 403 returned after a partial write "
        "would be a worse bug than no gate at all",
        tv_after.status_code == 200
        and len(tv_after.json().get("correction_history", []))
        == len(tvd.get("correction_history", [])),
        f"before={len(tvd.get('correction_history', []))} "
        f"after={len(tv_after.json().get('correction_history', []))}",
    )

    # ── THE CONTROL. ────────────────────────────────────────────────────
    ta_list = admin.get(f"/api/v1/portfolio/transactions?search={TAG}")
    ta_voc = ta_list.json().get("vocabularies", {}) if ta_list.status_code == 200 else {}
    check(
        "[Y] TRANSACTIONS manage_portfolio caller gets can_correct=true and "
        "NON-EMPTY correctable lists matching the service's own sets — the "
        "control for every refusal above",
        ta_list.json().get("permissions", {}).get("can_correct") is True
        and set(ta_voc.get("correctable", [])) == CORRECTABLE_FIELDS
        and set(ta_voc.get("inline_correctable", [])) == INLINE_CORRECTABLE_FIELDS,
        f"correctable={len(ta_voc.get('correctable', []))}/"
        f"{len(CORRECTABLE_FIELDS)} inline={ta_voc.get('inline_correctable')}",
    )
    ta_create = admin.post("/api/v1/portfolio/transactions",
                           json=_transaction_body(ids))
    ta_new = ta_create.json().get("transaction", {}).get("id") if \
        ta_create.status_code == 201 else None
    check(
        "[Y] TRANSACTIONS manage_portfolio caller GENUINELY records — 201 on "
        "the byte-identical body the view-only caller was refused",
        ta_create.status_code == 201 and bool(ta_new),
        f"status={ta_create.status_code} new_id={ta_new}",
    )
    ta_correct = admin.post(
        f"/api/v1/portfolio/transactions/{ids['txn_a']}/corrections",
        json={"external_ref": f"{TAG}-CORRECTED-001"},
    )
    tac = ta_correct.json() if ta_correct.status_code == 201 else {}
    check(
        "[Y] TRANSACTIONS manage_portfolio caller GENUINELY corrects — 201, a "
        "NEW transaction id back, `corrected_from` pointing at the original "
        "(append-only ledger, Rule 3), on the same endpoint the view-only "
        "caller was refused",
        ta_correct.status_code == 201
        and tac.get("transaction", {}).get("id") not in (None, ids["txn_a"])
        and tac.get("corrected_from") == ids["txn_a"]
        and tac.get("transaction", {}).get("external_ref") == f"{TAG}-CORRECTED-001",
        f"status={ta_correct.status_code} "
        f"new_id={tac.get('transaction', {}).get('id')} "
        f"corrected_from={tac.get('corrected_from')}",
    )
    out["txn_current"] = tac.get("transaction", {}).get("id") or ids["txn_a"]

    # ── TASK 4: the bypass, on this screen too ──────────────────────────
    ts_list = superu.get(f"/api/v1/portfolio/transactions?search={TAG}")
    ts_perms = ts_list.json().get("permissions", {}) if ts_list.status_code == 200 else {}
    check(
        "[Y] 4 TRANSACTIONS super_admin's envelope reports is_super_admin, "
        "can_write and can_correct — on a 'member'-only granular grant",
        ts_perms.get("is_super_admin") is True
        and ts_perms.get("can_write") is True
        and ts_perms.get("can_correct") is True,
        f"{ts_perms}",
    )
    ts_create = superu.post("/api/v1/portfolio/transactions",
                            json=_transaction_body(ids))
    ts_correct = superu.post(
        f"/api/v1/portfolio/transactions/{out['txn_current']}/corrections",
        json={"external_ref": f"{TAG}-SUPERADMIN-001"},
    )
    check(
        "[Y] 4 TRANSACTIONS super_admin BYPASSES BOTH write gates — 201 on the "
        "create AND 201 on the correction, from a fixture whose only role grant "
        "is view-only. Asserted independently of the admin control, which would "
        "have passed on its grant alone",
        ts_create.status_code == 201 and ts_correct.status_code == 201,
        f"create={ts_create.status_code} correct={ts_correct.status_code}",
    )
    ts_read = superu.get(f"/api/v1/portfolio/transactions/{ids['txn_a']}")
    check(
        "[Y] 4 TRANSACTIONS super_admin bypasses the READ gate too — both "
        "checks on both screens",
        ts_read.status_code == 200,
        f"status={ts_read.status_code}",
    )

    # ══════════════════════════════════════════════════════════════════════
    # REGRESSION — cross-org isolation, UNCHANGED by this sprint
    # ══════════════════════════════════════════════════════════════════════
    print("\n── REGRESSION: cross-org isolation, both screens ──")

    b_pos = orgb.get(f"/api/v1/portfolio/positions?search={TAG}")
    b_pos_ids = {r["id"] for r in b_pos.json().get("positions", [])} \
        if b_pos.status_code == 200 else set()
    a_pos_ids = {r["id"] for r in a_body.get("positions", [])}
    check(
        "[Y] REGRESSION POSITIONS cross-org isolation is UNCHANGED — org B "
        "sees its own row and NONE of org A's, and org A sees the converse, "
        "asserted on the SAME call in BOTH directions. A one-directional check "
        "would pass on an endpoint that returned nothing to anybody",
        b_pos.status_code == 200
        and ids["pos_b"] in b_pos_ids
        and not (b_pos_ids & a_pos_ids)
        and ids["pos_a"] in a_pos_ids
        and ids["pos_b"] not in a_pos_ids,
        f"orgB sees {len(b_pos_ids)} (incl. its own={ids['pos_b'] in b_pos_ids}), "
        f"orgA sees {len(a_pos_ids)}, overlap={len(b_pos_ids & a_pos_ids)}",
    )
    b_detail = orgb.get(f"/api/v1/portfolio/positions/{ids['pos_a']}")
    check(
        "[Y] REGRESSION POSITIONS org B gets 404 on org A's position id — not "
        "403. Telling a caller that an id exists somewhere else is itself the "
        "leak, so 'not yours' and 'does not exist' deliberately look identical",
        b_detail.status_code == 404,
        f"status={b_detail.status_code}",
    )
    b_txn = orgb.get(f"/api/v1/portfolio/transactions?search={TAG}")
    b_txn_ids = {r["id"] for r in b_txn.json().get("transactions", [])} \
        if b_txn.status_code == 200 else set()
    a_txn_ids = {r["id"] for r in ta_list.json().get("transactions", [])} \
        if ta_list.status_code == 200 else set()
    check(
        "[Y] REGRESSION TRANSACTIONS cross-org isolation is UNCHANGED — both "
        "directions on the same call",
        b_txn.status_code == 200
        and ids["txn_b"] in b_txn_ids
        and not (b_txn_ids & a_txn_ids)
        and ids["txn_a"] in a_txn_ids
        and ids["txn_b"] not in a_txn_ids,
        f"orgB sees {len(b_txn_ids)}, orgA sees {len(a_txn_ids)}, "
        f"overlap={len(b_txn_ids & a_txn_ids)}",
    )
    b_txn_detail = orgb.get(f"/api/v1/portfolio/transactions/{ids['txn_a']}")
    b_txn_correct = orgb.post(
        f"/api/v1/portfolio/transactions/{ids['txn_a']}/corrections",
        json={"external_ref": f"{TAG}-CROSSORG-SHOULD-NOT-EXIST"},
    )
    check(
        "[Y] REGRESSION TRANSACTIONS org B — who DOES hold manage_portfolio in "
        "its own org — can neither read nor correct org A's entry. The "
        "permission and the org scope are independent boundaries, and this "
        "sprint's permission work must not have collapsed them into one",
        b_txn_detail.status_code == 404 and b_txn_correct.status_code in (400, 404),
        f"detail={b_txn_detail.status_code} correct={b_txn_correct.status_code}",
    )
    return out


# ═══════════════════════════════════════════════════════════════════════════
# TASK 5 — the UI half, checked INDEPENDENTLY of the server
# ═══════════════════════════════════════════════════════════════════════════


def check_ui_renders_no_write_controls(envelopes: dict) -> None:
    """The other half of the dual proof, fed the REAL response bodies.

    A hidden button over an unprotected endpoint, and a protected endpoint
    behind a visible button, are both real bugs. The HTTP tests above rule out
    the first. These rule out the second — and they are driven by the ACTUAL
    envelope the view-only fixture received, not a hand-written one, so a server
    that stopped emptying the lists fails here as well as there.
    """
    pos_env = envelopes.get("pos_view_envelope", {})
    txn_env = envelopes.get("txn_view_envelope", {})

    grid_pos = _strip_js_comments(read(G_POS))
    pane_pos = _strip_js_comments(read(P_POS))
    grid_txn = _strip_js_comments(read(G_TXN))
    pane_txn = _strip_js_comments(read(P_TXN))

    # ── POSITIONS ──────────────────────────────────────────────────────
    check(
        "[Y] POSITIONS the grid's write controls are driven ONLY by the server "
        "envelope — `canWrite` from permissions.can_write and `inlineEditable` "
        "from vocabularies.inline_editable, with no permission logic of its own",
        "permissions?.can_write" in grid_pos
        and "vocabularies?.inline_editable" in grid_pos
        and "meta?.permissions" in grid_pos,
        "canWrite + inlineEditable both read off meta",
    )
    # The two inline cells are the grid's only write controls, and each is
    # ABSENT (a plain <span>) rather than disabled when its flag is false.
    for cell in ("TaxonomyCell", "ReconciledCell"):
        body = _fn_body(grid_pos, cell)
        guard = re.search(r"if\s*\(!editable\)\s*\{\s*return", body)
        check(
            f"[Y] POSITIONS {cell} returns READ-ONLY TEXT when its server flag "
            f"is false — an early `if (!editable) return <span>`, so the "
            f"control is ABSENT rather than disabled and there is no path that "
            f"renders it anyway",
            bool(body) and bool(guard) and guard.start() < body.find("onChange"),
            f"guard@{guard.start() if guard else -1} "
            f"onChange@{body.find('onChange')}",
        )
    # And, driven by the REAL envelope: the grid's decision expressions.
    inline_list = pos_env.get("vocabularies", {}).get("inline_editable")
    can_write = pos_env.get("permissions", {}).get("can_write")
    check(
        "[Y] POSITIONS fed the REAL envelope the view-only fixture received, "
        "the grid's own decision expressions produce ZERO write controls — "
        "`new Set(inline_editable).has(...)` is false for every inline field "
        "and `!!can_write` is false, so the taxonomy picker, the reconciled "
        "checkbox and the pane's Save toolbar all fail their render conditions",
        inline_list == []
        and can_write is False
        and not (set(inline_list or []) & INLINE_EDITABLE_FIELDS),
        f"inline_editable={inline_list}, can_write={can_write}, "
        f"controls that would render: 0 of {len(INLINE_EDITABLE_FIELDS)}",
    )
    check(
        "[Y] POSITIONS the pane reads its OWN response, not a prop — "
        "`data?.permissions` / `data?.vocabularies`. A permission answer "
        "threaded down from the grid would be a second copy that could go stale "
        "while the one the pane just fetched is fresh",
        "data?.permissions" in pane_pos and "data?.vocabularies" in pane_pos
        and "vocabularies," not in pane_pos[
            pane_pos.find("export default function PositionDetailPane("):
            pane_pos.find("export default function PositionDetailPane(") + 200
        ],
        "pane derives both from its own fetch; no vocabularies prop",
    )
    save_at = pane_pos.find("canWrite ? (")
    check(
        "[Y] POSITIONS the pane's Save/Discard toolbar is inside a "
        "`canWrite ? ... : null` — ABSENT, not disabled. A disabled Save still "
        "tells a view-only member that editing is a thing this screen does",
        save_at != -1
        and pane_pos.find('{saving ? "Saving…" : "Save"}', save_at) > save_at,
        f"canWrite gate@{save_at}, Save button inside it",
    )
    field_branches = pane_pos.count("editable.has(")
    check(
        "[Y] POSITIONS every editable field in the pane renders an input ONLY "
        "inside an `editable.has(field)` branch, with a read-only <Field> in "
        "the else — so an empty server list yields a pane of plain text",
        field_branches >= 6
        and "vocabularies?.editable" in pane_pos
        and "canWrite &&" in pane_pos,
        f"editable.has() branches = {field_branches}",
    )

    # ── TRANSACTIONS ───────────────────────────────────────────────────
    check(
        "[Y] TRANSACTIONS the grid's correction controls are driven ONLY by "
        "the server envelope — `canCorrect` from permissions.can_correct and "
        "`inlineCorrectable` from vocabularies.inline_correctable",
        "permissions?.can_correct" in grid_txn
        and "vocabularies?.inline_correctable" in grid_txn
        and "meta?.permissions" in grid_txn,
        "canCorrect + inlineCorrectable both read off meta",
    )
    for cell in ("SettleDateCell", "ExternalRefCell"):
        body = _fn_body(grid_txn, cell)
        guard = re.search(r"if\s*\(!editable\)\s*\{\s*return", body)
        check(
            f"[Y] TRANSACTIONS {cell} returns READ-ONLY TEXT when its server "
            f"flag is false — the control is ABSENT rather than disabled, and "
            f"the guard precedes every write handler in the body",
            bool(body) and bool(guard)
            and guard.start() < body.find("onEdit(row,"),
            f"body={len(body)} chars, guard@{guard.start() if guard else -1}, "
            f"onEdit@{body.find('onEdit(row,')}",
        )
    t_inline = txn_env.get("vocabularies", {}).get("inline_correctable")
    t_correctable = txn_env.get("vocabularies", {}).get("correctable")
    t_can = txn_env.get("permissions", {}).get("can_correct")
    check(
        "[Y] TRANSACTIONS fed the REAL envelope the view-only fixture "
        "received, the grid's and the pane's decision expressions produce ZERO "
        "write or correct controls — both published lists are empty and "
        "can_correct is false",
        t_inline == [] and t_correctable == [] and t_can is False,
        f"inline_correctable={t_inline}, correctable={t_correctable}, "
        f"can_correct={t_can}",
    )
    check(
        "[Y] TRANSACTIONS the pane reads its OWN response — `data?.permissions` "
        "/ `data?.vocabularies`, no permission prop from the grid",
        "data?.permissions" in pane_txn and "data?.vocabularies" in pane_txn,
        "pane derives both from its own fetch",
    )
    correct_at = pane_txn.find("canCorrect ? (")
    check(
        "[Y] TRANSACTIONS the pane's Correct/Discard toolbar is inside a "
        "`canCorrect ? ... : null` — ABSENT, not disabled",
        correct_at != -1
        and pane_txn.find('{saving ? "Saving…" : "Correct"}', correct_at) > correct_at,
        f"canCorrect gate@{correct_at}, Correct button inside it",
    )
    ro_at = pane_txn.find("!canCorrect ? (")
    check(
        "[Y] TRANSACTIONS the pane renders a READ-ONLY entry block instead of "
        "the correction form when the caller may not correct — the fifteen "
        "inputs are not merely `disabled`, they are not rendered. A grid of "
        "greyed inputs still advertises the operation, and one future edit that "
        "forgets a `disabled=` turns it into a live control over a 403",
        ro_at != -1
        and "<input" not in pane_txn[ro_at:pane_txn.find(") : (", ro_at)]
        and "<select" not in pane_txn[ro_at:pane_txn.find(") : (", ro_at)]
        and "onChange" not in pane_txn[ro_at:pane_txn.find(") : (", ro_at)],
        f"read-only branch = "
        f"{pane_txn.find(') : (', ro_at) - ro_at if ro_at != -1 else 0} chars, "
        f"zero form controls",
    )
    # The correction CHAIN must stay visible. It is a read.
    versions_at = pane_txn.find('title="Versions"')
    check(
        "[Y] TRANSACTIONS the correction CHAIN ('Versions') is rendered "
        "OUTSIDE every canCorrect branch — a view-only member sees the full "
        "history of an entry and simply cannot add to it. Hiding the history "
        "with the form would have been a different, quieter bug",
        versions_at != -1 and versions_at > pane_txn.find("</Section>", correct_at)
        and "canCorrect" not in pane_txn[
            pane_txn.rfind("{data.correction_history.length > 1 &&", 0, versions_at):
            versions_at + 200
        ],
        f"Versions section@{versions_at}, no canCorrect in its condition",
    )

    # ── THE NO-FALLBACK DISCIPLINE, all four files ─────────────────────
    bad = []
    for label, code in (
        ("PositionsGrid", grid_pos), ("PositionDetailPane", pane_pos),
        ("TransactionsGrid", grid_txn), ("TransactionDetailPane", pane_txn),
    ):
        for m in re.finditer(r"\|\|\s*\[\s*[\"'{]", code):
            bad.append(f"{label}: {m.group(0)!r}")
    check(
        "[Y] NO client-side fallback LIST in any of the four components — "
        "every `||` fallback on a server-published list is the EMPTY array. A "
        "`|| DEFAULTS` is precisely what survives a missing envelope and puts "
        "the write controls back for a view-only user",
        not bad,
        f"non-empty fallback literals: {bad or 'none'}",
    )
    # And the grids both say so, so a read-only user is not left guessing.
    check(
        "[Y] both grids TELL the read-only user which grant they are missing, "
        "from the server-published permission NAMES rather than a hardcoded "
        "string — silence would leave them assuming the screen is broken",
        "permissions.write_permission" in grid_pos
        and "permissions.write_permission" in grid_txn
        and "read-only" in grid_pos and "read-only" in grid_txn,
        "both grids render the read-only note from the envelope",
    )


def _fn_body(code: str, name: str) -> str:
    """The source of one top-level JSX ``function name(...)``.

    Bounded by the NEXT top-level ``function``, and — when there is none,
    because the target is the last plain function before an
    ``export default function`` — by the export instead. A slicer that returned
    ``""`` in that case would fail the LAST component in every file it was
    pointed at, which reads as a missing guard rather than as a missing
    terminator.
    """
    start = code.find(f"function {name}(")
    if start == -1:
        return ""
    ends = [
        p for p in (
            code.find("\nfunction ", start + 1),
            code.find("\nexport default function ", start + 1),
            code.find("\nexport ", start + 1),
        ) if p > start
    ]
    return code[start:min(ends)] if ends else code[start:]


def _node_modules_dir() -> str | None:
    """apps/web is an npm WORKSPACE, so node_modules is hoisted to the root."""
    for candidate in (
        os.path.join(_WEB, "node_modules"),
        os.path.join(_WEB, "..", "..", "node_modules"),
    ):
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return None


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
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-6:]
    check(
        "[Y] npm run build exits 0",
        proc.returncode == 0,
        f"exit={proc.returncode}" + (
            "" if proc.returncode == 0 else " | " + " / ".join(tail)
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1

    conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")

    baseline: dict[str, int] = {}
    try:
        await teardown(conn)                                          # START
        baseline = await counts(conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        report(
            "TEARDOWN is by-fixture, never a truncate",
            f"portfolio.positions and portfolio.transactions hold real "
            f"production rows in both orgs. Fixtures are matched through the "
            f"{TAG!r} tag on asset and entity names, with an exact before/after "
            f"count on {len(TABLES)} tables as the backstop.",
        )

        role_perms: dict[str, set[str]] = {}
        for r in await conn.fetch(
            """
            SELECT r.name AS role, p.name AS perm
            FROM roles r
            JOIN role_permissions rp ON rp.role_id = r.id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE p.name IN ('view_portfolio', 'manage_portfolio')
            """
        ):
            role_perms.setdefault(r["role"], set()).add(r["perm"])

        print("\n── TASK 1: DISCOVERY — four findings ──")
        check_task1a()
        check_task1b()
        check_task1c()
        check_task1d(role_perms)

        print("\n── TASKS 2/3/4: the fixed routers, at the AST layer ──")
        check_router_gates()
        check_super_admin_is_checked_first()
        check_no_org_id_from_body()
        check_schema_qualification()

        print("\n── Fixtures ──")
        await seed_users(conn)
        ids = await seed(conn)
        print("   seeded: 2 orgs, 2 assets, 2 positions, 2 transactions, "
              "4 principals (admin / view-only member / super_admin / org-B admin)")

        # ── The fixtures' tiers, asserted BEFORE anything relies on them ──
        #
        # Read with direct SQL rather than by calling ``rbac.has_permission``.
        # That function takes the APPLICATION's pool, and creating that pool
        # here — on this loop — poisons the TestClient pass below, which runs in
        # an executor thread with its own event loop and would inherit a pool
        # bound to this one ("attached to a different loop", surfacing as a 500
        # from every endpoint). The SQL below is the same join
        # ``get_user_permissions`` runs, and the EFFECTIVE behaviour is asserted
        # for real by the 200-read / 403-write pairs through the ASGI app.
        async def _perms(user_id: str) -> set[str]:
            rows = await conn.fetch(
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
            return await conn.fetchval(
                "SELECT count(*) FROM user_roles WHERE user_id = $1::uuid", user_id
            )

        v_perms, a_perms = await _perms(V_USER_ID), await _perms(A_USER_ID)
        s_perms = await _perms(S_USER_ID)
        v_roles, a_roles = await _role_count(V_USER_ID), await _role_count(A_USER_ID)
        s_roles = await _role_count(S_USER_ID)
        check(
            "[Y] THE UX-3 TRAP IS NOT RE-FALLEN-INTO: the fixtures occupy "
            "different permission tiers and BOTH actually hold a role. "
            "rbac.has_permission DEFAULT-ALLOWS a user with ZERO rows in "
            "user_roles, so a role-less 'view-only' fixture would silently hold "
            "manage_portfolio and every refusal in this script would pass in "
            "the WRONG direction",
            v_roles > 0 and a_roles > 0
            and READ_PERMISSION in v_perms and WRITE_PERMISSION not in v_perms
            and {READ_PERMISSION, WRITE_PERMISSION} <= a_perms,
            f"viewer: {v_roles} role(s), portfolio perms="
            f"{sorted(v_perms & {READ_PERMISSION, WRITE_PERMISSION})}; "
            f"admin: {a_roles} role(s), portfolio perms="
            f"{sorted(a_perms & {READ_PERMISSION, WRITE_PERMISSION})}",
        )
        su_role = await conn.fetchval(
            "SELECT role FROM public.users WHERE id = $1::uuid", S_USER_ID
        )
        check(
            "[Y] 4 the super-admin fixture is one because users.role says so — "
            "which is what rbac.is_super_admin reads, NOT user_roles — and its "
            "granular grant is 'member', which does NOT carry "
            "manage_portfolio. Any write it makes below therefore came from the "
            "BYPASS and not from a permission it holds",
            su_role == "super_admin" and s_roles > 0
            and READ_PERMISSION in s_perms and WRITE_PERMISSION not in s_perms,
            f"users.role={su_role!r}, {s_roles} role grant(s), granular perms="
            f"{sorted(s_perms & {READ_PERMISSION, WRITE_PERMISSION})}",
        )

        print("\n── TASK 5: the real endpoints, driven through the ASGI app ──")
        loop = asyncio.get_running_loop()
        envelopes = await loop.run_in_executor(None, endpoint_tests, ids)

        print("\n── TASK 5: the UI half, checked independently of the server ──")
        check_ui_renders_no_write_controls(envelopes)

        print("\n── npm run build ──")
        await loop.run_in_executor(None, check_npm_build)

    finally:
        await teardown(conn)                                          # END
        if baseline:
            final = await counts(conn)
            drift = {t: (baseline[t], final[t]) for t in TABLES
                     if baseline[t] != final[t]}
            check(
                "[Y] TEARDOWN restores the EXACT before-count on every table "
                "touched — zero leftover rows",
                not drift,
                f"drift (before, after): {drift}" if drift
                else ", ".join(f"{t.split('.')[-1]}={final[t]}" for t in TABLES),
            )
        await conn.close()

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
