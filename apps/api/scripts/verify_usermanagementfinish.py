"""usermanagementfinish sprint verify — completing the user-management list.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent: teardown
runs at START and at END, keyed on fixed test UUIDs and a stable marker.

WHAT IS REAL HERE, AND WHAT IS NOT — stated up front so no assertion reads as
stronger than it is.

  REAL:
  * Every endpoint is driven through Starlette's TestClient against the LIVE
    database named by DATABASE_URL. The middleware stack (JWT -> RLS context ->
    the new active-account gate), get_org_id, ensure_user, require_permission,
    the routers and all SQL run exactly as deployed.
  * Every claim about a row is read back out of that same live database with a
    separate connection afterwards. No assertion trusts a response body alone.
  * The FK finding behind Task 5 is MEASURED, not asserted: the dependents are
    counted from pg_constraint, and a real hard DELETE is attempted inside a
    transaction that is then rolled back, so "it would fail" is an observed
    ForeignKeyViolation rather than a claim.
  * The Task 1 findings are proven against git history where they are claims
    about what the code used to do — the pre-sprint files are read with
    ``git show`` and asserted.

  NOT REAL (and never reported as PASS):
  * The Auth0 JWT SIGNATURE leg. ``main.verify_token`` is stubbed, because no
    Auth0 client credentials for either tenant exist in this environment. What
    IS exercised is everything downstream of a validated token. The ISSUER
    itself is real input to the code under test: the Hollisworks org assertions
    feed the exact ``iss`` string ``main.is_hollisworks_claims`` compares
    against, which is the claim the fix keys off.
  * ``HOLLISWORKS_AUTH0_DOMAIN`` is not set in this environment, so the
    Hollisworks tenant would be reported as disabled and the issuer branch would
    never run. The script sets it BEFORE importing main so the code path under
    test is reachable at all. That is a configuration input, not a stub of the
    logic — ``is_hollisworks_claims`` and ``org_id_from_claims`` run unmodified.
  * Browser rendering. The UI is not rendered; the endpoints it calls are driven
    for real, and the frontend is checked separately with ``next build``.

DSN:
  DATABASE_URL             — bypass (postgres) role: seeding, reads, teardown.
  APP_SERVICE_DATABASE_URL — non-bypass role, for the RLS cross-org leg. When
                             absent that ONE assertion is reported BLOCKED.
"""

import asyncio
import glob
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# ── Make runnable via allowlisted system python3 OR venv python ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

# Load apps/api/.env so DATABASE_URL / APP_SERVICE_DATABASE_URL are available
# even when the shell didn't export them.
_ENV = os.path.join(_API_ROOT, ".env")
try:
    with open(_ENV) as _fh:
        for _line in _fh:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
except OSError:
    pass

# MUST happen before `import main` — Settings is constructed once and cached, and
# `hollisworks_enabled` is False for an empty domain, which would make the whole
# Hollisworks branch unreachable and every Task-2 assertion pass vacuously.
_HW_DOMAIN_WAS_SET = bool(os.environ.get("HOLLISWORKS_AUTH0_DOMAIN"))
os.environ.setdefault("HOLLISWORKS_AUTH0_DOMAIN", "hollisworks-verify.us.auth0.com")

import asyncpg  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")

# ── stable ids / markers ────────────────────────────────────────────────────
MARKER = "usermgmtfinish_verify"

ORG_2A = "00000000-0000-0000-0000-000000000001"   # 2nd Act Capital (live)
ORG_HW = "bb347258-8f28-4f49-8cc9-e29ccad82884"   # Hollisworks (live)

ADMIN_2A_ID = "99000000-0000-0000-0000-0000f0110001"
ADMIN_HW_ID = "99000000-0000-0000-0000-0000f0110002"
MEMBER_2A_ID = "99000000-0000-0000-0000-0000f0110003"
MEMBER_HW_ID = "99000000-0000-0000-0000-0000f0110004"
DEACT_2A_ID = "99000000-0000-0000-0000-0000f0110005"
DELETE_2A_ID = "99000000-0000-0000-0000-0000f0110006"
LOGIN_2A_ID = "99000000-0000-0000-0000-0000f0110007"
STAFF_EXISTING_ID = "99000000-0000-0000-0000-0000f0110008"

PROFILE_2A_ID = "99000000-0000-0000-0000-0000f0220001"
PROFILE_HW_ID = "99000000-0000-0000-0000-0000f0220002"

ADMIN_2A_SUB = f"auth0|{MARKER}_admin_2a"
ADMIN_HW_SUB = f"auth0|{MARKER}_admin_hw"
MEMBER_2A_SUB = f"auth0|{MARKER}_member_2a"
MEMBER_HW_SUB = f"auth0|{MARKER}_member_hw"
DEACT_2A_SUB = f"auth0|{MARKER}_deact_2a"
DELETE_2A_SUB = f"auth0|{MARKER}_delete_2a"
LOGIN_2A_SUB = f"auth0|{MARKER}_login_2a"
# Hollisworks-issued identities. `_new` has NO row yet (tests the INSERT path);
# `_existing` is seeded holding the WRONG org (tests the repair path).
STAFF_NEW_SUB = f"auth0|{MARKER}_staff_new"
STAFF_EXISTING_SUB = f"auth0|{MARKER}_staff_existing"

TEST_USER_IDS = [
    ADMIN_2A_ID, ADMIN_HW_ID, MEMBER_2A_ID, MEMBER_HW_ID,
    DEACT_2A_ID, DELETE_2A_ID, LOGIN_2A_ID, STAFF_EXISTING_ID,
]
TEST_SUBS = [
    ADMIN_2A_SUB, ADMIN_HW_SUB, MEMBER_2A_SUB, MEMBER_HW_SUB,
    DEACT_2A_SUB, DELETE_2A_SUB, LOGIN_2A_SUB,
    STAFF_NEW_SUB, STAFF_EXISTING_SUB,
]
TEST_PROFILE_IDS = [PROFILE_2A_ID, PROFILE_HW_ID]

# Settings keys this script writes and MUST remove again — they are written on a
# LIVE org row, so leaving one behind would change real invite behaviour.
TEST_SETTING_KEYS = ["invite.expiry_days", "user.inactivity_timeout_days"]

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def blocked(name, detail=""):
    _RESULTS.append(("BLOCKED", name, detail))
    print(f"[BLOCKED] {name}" + (f" — {detail}" if detail else ""))


def info(line):
    print(f"       {line}")


def check(name, condition, detail_ok="", detail_bad=""):
    if condition:
        ok(name, detail_ok)
    else:
        fail(name, detail_bad or detail_ok)
    return bool(condition)


# ── teardown ────────────────────────────────────────────────────────────────
async def teardown(conn):
    """Remove every row this script can create. FK-safe order.

    Order matters and is not cosmetic: audit_log.user_id and audit_log.resource_id
    both reach users(id); users.invited_by and users.deactivated_by are
    self-references, so an invitee (which points at an admin) must go before the
    admin; users.profile_id points at profiles, so users go before profiles; and
    org_settings.updated_by ALSO reaches users(id), so the settings rows this
    script writes must go before the admin that wrote them. (That last one is
    not hypothetical — deleting users first raised
    ``org_settings_updated_by_fkey`` on the first run of this script.)

    The org_settings deletes matter for a second reason: those keys are written
    on LIVE org rows (2nd Act / Hollisworks), and a leftover invite.expiry_days
    would silently change real invite expiry in production.
    """
    await conn.execute(
        "DELETE FROM org_settings WHERE org_id = ANY($1::uuid[]) AND setting_key = ANY($2::text[])",
        [ORG_2A, ORG_HW], TEST_SETTING_KEYS,
    )
    await conn.execute(
        """
        DELETE FROM audit_log
        WHERE user_id IN (
            SELECT id FROM users
            WHERE email LIKE '%' || $1 || '%' OR auth0_sub = ANY($2::text[])
        )
        OR resource_id IN (
            SELECT id FROM users
            WHERE email LIKE '%' || $1 || '%' OR auth0_sub = ANY($2::text[])
        )
        OR resource_id = ANY($3::uuid[])
        """,
        MARKER, TEST_SUBS, TEST_USER_IDS,
    )
    for table, col in (
        ("user_roles", "user_id"),
        ("user_permission_sets", "user_id"),
    ):
        await conn.execute(
            f"""
            DELETE FROM {table} WHERE {col} IN (
                SELECT id FROM users
                WHERE email LIKE '%' || $1 || '%' OR auth0_sub = ANY($2::text[])
                   OR id = ANY($3::uuid[])
            )
            """,
            MARKER, TEST_SUBS, TEST_USER_IDS,
        )
    # Break self-references before deleting, so no ordering can trip a FK.
    await conn.execute(
        """
        UPDATE users SET invited_by = NULL, deactivated_by = NULL, manager_id = NULL
        WHERE email LIKE '%' || $1 || '%'
           OR auth0_sub = ANY($2::text[])
           OR id = ANY($3::uuid[])
        """,
        MARKER, TEST_SUBS, TEST_USER_IDS,
    )
    await conn.execute(
        """
        DELETE FROM users
        WHERE email LIKE '%' || $1 || '%'
           OR auth0_sub = ANY($2::text[])
           OR id = ANY($3::uuid[])
        """,
        MARKER, TEST_SUBS, TEST_USER_IDS,
    )
    await conn.execute("DELETE FROM profiles WHERE id = ANY($1::uuid[])", TEST_PROFILE_IDS)


async def leftover_count(conn) -> int:
    users = await conn.fetchval(
        """
        SELECT count(*) FROM users
        WHERE email LIKE '%' || $1 || '%'
           OR auth0_sub = ANY($2::text[])
           OR id = ANY($3::uuid[])
        """,
        MARKER, TEST_SUBS, TEST_USER_IDS,
    )
    profiles = await conn.fetchval(
        "SELECT count(*) FROM profiles WHERE id = ANY($1::uuid[])", TEST_PROFILE_IDS
    )
    settings = await conn.fetchval(
        "SELECT count(*) FROM org_settings WHERE org_id = ANY($1::uuid[]) "
        "AND setting_key = ANY($2::text[])",
        [ORG_2A, ORG_HW], TEST_SETTING_KEYS,
    )
    audit = await conn.fetchval(
        "SELECT count(*) FROM audit_log WHERE resource_id = ANY($1::uuid[])",
        TEST_USER_IDS,
    )
    return int(users) + int(profiles) + int(settings) + int(audit)


async def seed(conn):
    """Real rows in the two LIVE orgs.

    role='org_admin' with NO user_roles rows: services.rbac.has_permission
    default-allows a user holding no roles (the documented single-admin
    posture), so manage_members passes WITHOUT making these super_admins. That
    matters for every cross-org assertion — a super_admin bypass would make them
    pass for the wrong reason, since _resolve_target lets a Super Admin act
    across orgs by design.
    """
    for uid, org, sub, role, name in (
        (ADMIN_2A_ID, ORG_2A, ADMIN_2A_SUB, "org_admin", "Verify Admin 2A"),
        (ADMIN_HW_ID, ORG_HW, ADMIN_HW_SUB, "org_admin", "Verify Admin HW"),
        (MEMBER_2A_ID, ORG_2A, MEMBER_2A_SUB, "member", "Verify Member 2A"),
        (MEMBER_HW_ID, ORG_HW, MEMBER_HW_SUB, "member", "Verify Member HW"),
        (DEACT_2A_ID, ORG_2A, DEACT_2A_SUB, "member", "Verify Deact 2A"),
        (DELETE_2A_ID, ORG_2A, DELETE_2A_SUB, "member", "Verify Delete 2A"),
        (LOGIN_2A_ID, ORG_2A, LOGIN_2A_SUB, "member", "Verify Login 2A"),
        # Pre-fix state on purpose: a Hollisworks-tenant identity whose row was
        # created when get_org_id had no issuer branch, so it holds 2nd Act's
        # org. This is the row the repair path has to correct.
        (STAFF_EXISTING_ID, ORG_2A, STAFF_EXISTING_SUB, "member", "Verify Staff Existing"),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO NOTHING
            """,
            uid, org, f"{sub.split('|')[1]}@example.com", name, sub, role,
        )
    for pid, org, name in (
        (PROFILE_2A_ID, ORG_2A, f"Verify Profile 2A {MARKER}"),
        (PROFILE_HW_ID, ORG_HW, f"Verify Profile HW {MARKER}"),
    ):
        await conn.execute(
            "INSERT INTO profiles (id, org_id, name) VALUES ($1, $2, $3) "
            "ON CONFLICT (id) DO NOTHING",
            pid, org, name,
        )


# ── the app, with only the JWT SIGNATURE stubbed ────────────────────────────
_CLAIMS: dict = {}


def _install_app():
    import main
    from starlette.testclient import TestClient

    # ONLY the signature check is replaced. Everything downstream — the RLS
    # context middleware, the new active-account gate, get_org_id, ensure_user,
    # require_permission, the routers and all SQL — runs exactly as deployed.
    main.verify_token = lambda _t: dict(_CLAIMS)
    return main, TestClient


def _as(sub, org_id=None, iss=None):
    """Switch the identity the stubbed verify_token will report.

    `org_id=None, iss=<hollisworks issuer>` is the interesting case: with no
    org_id claim present, org resolution is decided ENTIRELY by the issuer
    branch this sprint added. Passing an explicit org_id would mask it.
    """
    _CLAIMS.clear()
    _CLAIMS["sub"] = sub
    if org_id:
        _CLAIMS["org_id"] = org_id
    if iss:
        _CLAIMS["iss"] = iss


# ── main ────────────────────────────────────────────────────────────────────
async def run():
    if not DATABASE_URL:
        fail("env", "DATABASE_URL is not set — cannot verify anything against the DB")
        return

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        await teardown(conn)  # teardown-at-START
        await seed(conn)

        main, TestClient = _install_app()
        with TestClient(main.app) as client:
            await task1_findings(conn, main)
            await task2_hollisworks_org(conn, main, client)
            await task3_profile_invite(conn, client)
            await task4_edit_name(conn, client)
            await task5_deactivate_and_delete(conn, main, client)
            await task6_org_settings(conn, client)
            await task7_last_login(conn, client)
            await task8_cross_org(conn, client)

        # Teardown at END, then prove it left nothing behind.
        await teardown(conn)
        left = await leftover_count(conn)
        check(
            "T9: teardown leaves zero leftover rows",
            left == 0,
            "0 rows match the test marker / ids / settings keys",
            f"{left} rows remain",
        )
    finally:
        await conn.close()
        try:
            from services.database import close_pool

            await close_pool()
        except Exception:  # noqa: BLE001
            pass


# ══════════════════════════════════════════════════════════════════════════
# TASK 1 — the four discovery findings, each MEASURED
# ══════════════════════════════════════════════════════════════════════════
def _git_show(path: str) -> str:
    try:
        return subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        info(f"git show {path} failed: {exc}")
        return ""


def _read(rel: str) -> str:
    with open(os.path.join(_API_ROOT, rel)) as fh:
        return fh.read()


async def task1_findings(conn, main):
    print("\n=== TASK 1 — discovery findings (each asserted, not just stated) ===")

    # ── 1a ────────────────────────────────────────────────────────────────
    print(
        "\n[1a] A Hollisworks login landed in the DEFAULT org because "
        "routers/entities.py::get_org_id read ONLY the three ORG_ID_CLAIMS and "
        "otherwise returned DEFAULT_ORG_ID. An Auth0 access token minted for a "
        "custom API audience carries none of them, so EVERY token — from either "
        "tenant — fell through to the default. ensure_user writes that value "
        "into the users row. The issuer, which was ALREADY trusted two lines "
        "later to grant role='super_admin', was never consulted for org."
    )
    print("     FIX POINT: get_org_id itself (now org_id_from_claims), plus an "
          "org repair on ensure_user's existing-row path.")

    old_entities = _git_show("apps/api/routers/entities.py")
    had_bare_fallback = (
        "def get_org_id" in old_entities
        and "return DEFAULT_ORG_ID" in old_entities
        and "is_hollisworks_claims" not in old_entities
    )
    check(
        "T1a: pre-sprint get_org_id had no issuer branch at all",
        had_bare_fallback,
        "git HEAD copy of routers/entities.py: ORG_ID_CLAIMS loop then "
        "`return DEFAULT_ORG_ID`, zero references to is_hollisworks_claims",
        "git HEAD copy did not match the expected pre-sprint shape",
    )

    now_entities = _read("routers/entities.py")
    check(
        "T1a: shipped get_org_id resolves the Hollisworks issuer to the real org",
        "is_hollisworks_claims" in now_entities
        and "HOLLISWORKS_ORG_ID" in now_entities
        and ORG_HW in now_entities,
        f"routers/entities.py now maps a Hollisworks-issued token to {ORG_HW}",
        "the issuer branch is not present in the shipped file",
    )

    # ── 1b ────────────────────────────────────────────────────────────────
    from services.invites import ALLOWED_INVITE_ROLES

    print(
        "\n[1b] ALLOWED_INVITE_ROLES == ('member', 'org_admin') — 'super_admin' "
        "is deliberately excluded so an org admin cannot mint platform staff. "
        "profile_id was accepted NOWHERE in the create-invite path: not on "
        "InviteCreateRequest, not as a create_invite() parameter, and not in "
        "its INSERT column list. users.profile_id existed but was only ever set "
        "afterwards, by PUT /admin/users/{id}/profile."
    )
    check(
        "T1b: ALLOWED_INVITE_ROLES is exactly ('member', 'org_admin')",
        tuple(ALLOWED_INVITE_ROLES) == ("member", "org_admin"),
        f"{tuple(ALLOWED_INVITE_ROLES)}",
        f"got {tuple(ALLOWED_INVITE_ROLES)}",
    )
    old_svc_invites = _git_show("apps/api/services/invites.py")
    old_rtr_invites = _git_show("apps/api/routers/invites.py")
    check(
        "T1b: pre-sprint create-invite path had no profile_id anywhere",
        "profile_id" not in old_svc_invites and "profile_id" not in old_rtr_invites,
        "neither services/invites.py nor routers/invites.py mentioned profile_id at HEAD",
        "profile_id was already present pre-sprint — finding is wrong",
    )

    # ── 1c ────────────────────────────────────────────────────────────────
    print(
        "\n[1c] There was NO general /admin/users edit endpoint. routers/admin.py "
        "exposed exactly two routes — GET /admin/users and PUT "
        "/admin/users/{id}/role — and the PUT writes the user_roles JOIN table, "
        "not the users row. routers/profiles.py added PUT /admin/users/{id}/profile, "
        "which writes users.profile_id and nothing else. So full_name, email and "
        "account state were un-editable by an admin: the ONLY writes to a users "
        "row from a UI were PATCH /users/me (self; nav_pinned + "
        "assistant_panel_posture only)."
    )
    old_admin = _git_show("apps/api/routers/admin.py")
    old_routes = [
        ln.strip() for ln in old_admin.splitlines() if ln.strip().startswith("@router.")
    ]
    info(f"pre-sprint routers/admin.py routes: {old_routes}")
    check(
        "T1c: pre-sprint admin router had no PATCH/DELETE/lifecycle route",
        "@router.patch" not in old_admin
        and "@router.delete" not in old_admin
        and "deactivate" not in old_admin
        and old_admin.count("@router.") == 3,
        f"3 routes at HEAD ({old_routes}) — none of them edit the users row",
        f"unexpected pre-sprint routes: {old_routes}",
    )
    old_users_router = _git_show("apps/api/routers/users.py")
    check(
        "T1c: the only pre-sprint users-row write from a UI was PATCH /users/me",
        "nav_pinned" in old_users_router
        and "assistant_panel_posture" in old_users_router
        and "full_name" not in old_users_router.split("class MePatch")[-1].split("@router")[0],
        "MePatch carried nav_pinned + assistant_panel_posture only — no name field",
        "MePatch already allowed more than the two preference fields",
    )

    # ── 1d ────────────────────────────────────────────────────────────────
    from services.org_settings import (
        DEFAULT_SETTINGS,
        INVITE_EXPIRY_DAYS_KEY,
        USER_INACTIVITY_TIMEOUT_DAYS_KEY,
        category_for,
    )

    print(
        "\n[1d] org_settings convention: dotted keys, jsonb NOT NULL values "
        "(scalars json-encoded on write, decoded on read), UNIQUE on "
        "(org_id, setting_key), plain upsert — Rule 3 does NOT apply. "
        "DEFAULT_SETTINGS *is* the default data and is the one place in "
        "application code allowed to hold literal defaults; CATEGORY_BY_PREFIX "
        "derives `category` from the key namespace; _validate_setting is the "
        "write-time value gate (400)."
    )
    live = await conn.fetch(
        "SELECT setting_key, setting_value, category FROM org_settings "
        "WHERE org_id = $1 ORDER BY setting_key LIMIT 4",
        ORG_2A,
    )
    for r in live:
        info(f"live row: {r['setting_key']!r} = {r['setting_value']!r} (category={r['category']!r})")
    dotted_and_jsonb = all(
        "." in r["setting_key"] and isinstance(r["setting_value"], str)
        for r in live
    )
    check(
        "T1d: live org_settings rows are dotted keys with json-encoded values",
        bool(live) and dotted_and_jsonb,
        f"{len(live)} sampled rows all match the convention",
        "live rows did not match the documented convention",
    )
    check(
        "T1d: this sprint's two keys follow that convention exactly",
        DEFAULT_SETTINGS.get(INVITE_EXPIRY_DAYS_KEY) == 7
        and DEFAULT_SETTINGS.get(USER_INACTIVITY_TIMEOUT_DAYS_KEY) == 90
        and category_for(INVITE_EXPIRY_DAYS_KEY) == "membership"
        and category_for(USER_INACTIVITY_TIMEOUT_DAYS_KEY) == "membership",
        f"{INVITE_EXPIRY_DAYS_KEY}=7, {USER_INACTIVITY_TIMEOUT_DAYS_KEY}=90, "
        "both category 'membership' (derived from the key namespace)",
        "the new keys are not registered in DEFAULT_SETTINGS/CATEGORY_BY_PREFIX",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 2 — Hollisworks-tenant users land in the Hollisworks org
# ══════════════════════════════════════════════════════════════════════════
async def task2_hollisworks_org(conn, main, client):
    print("\n=== TASK 2 — Hollisworks-tenant org assignment (+ 2nd Act regression) ===")

    settings = main.get_settings()
    hw_issuer = settings.hollisworks_issuer
    if not _HW_DOMAIN_WAS_SET:
        info(
            "HOLLISWORKS_AUTH0_DOMAIN was absent from this environment; the "
            f"script set it so the issuer branch is reachable. issuer={hw_issuer!r}"
        )
    check(
        "T2: the Hollisworks tenant is enabled for this run",
        settings.hollisworks_enabled and bool(hw_issuer),
        f"is_hollisworks_claims will compare iss against {hw_issuer!r}",
        "Hollisworks tenant disabled — every Task-2 assertion would pass vacuously",
    )

    from routers.entities import DEFAULT_ORG_ID, HOLLISWORKS_ORG_ID, org_id_from_claims

    # The live org row this must resolve to — read from the database, not
    # trusted from the constant, so a constant that drifts is caught.
    hw_org = await conn.fetchrow(
        "SELECT id, name, slug FROM organizations WHERE id = $1", ORG_HW
    )
    check(
        "T2: the target org is the REAL Hollisworks row in the live database",
        hw_org is not None and hw_org["slug"] == "hollisworks"
        and str(hw_org["id"]) == HOLLISWORKS_ORG_ID,
        f"organizations {ORG_HW} = {hw_org['name']!r} (slug={hw_org['slug']!r})"
        if hw_org else "",
        "no organizations row with the Hollisworks id / slug",
    )

    # ── pure resolution, both directions ──────────────────────────────────
    hw_claims = {"sub": STAFF_NEW_SUB, "iss": hw_issuer}
    a2_claims = {"sub": ADMIN_2A_SUB, "iss": settings.issuer}
    check(
        "T2: a Hollisworks-issued claim set resolves to the Hollisworks org",
        org_id_from_claims(hw_claims) == HOLLISWORKS_ORG_ID,
        f"org_id_from_claims(iss={hw_issuer!r}) -> {HOLLISWORKS_ORG_ID}",
        f"got {org_id_from_claims(hw_claims)!r}",
    )
    check(
        "T2 REGRESSION: a 2nd Act claim set still resolves to the default org",
        org_id_from_claims(a2_claims) == DEFAULT_ORG_ID,
        f"org_id_from_claims(iss={settings.issuer!r}) -> {DEFAULT_ORG_ID} (unchanged)",
        f"got {org_id_from_claims(a2_claims)!r} — 2nd Act behaviour CHANGED",
    )
    check(
        "T2 REGRESSION: an explicit org_id claim still wins over both branches",
        org_id_from_claims({"sub": "x", "iss": hw_issuer, "org_id": ORG_2A}) == ORG_2A,
        "the ORG_ID_CLAIMS loop is still checked first",
        "the issuer branch overrode an explicit org_id claim",
    )
    check(
        "T2 REGRESSION: a claimless request still resolves to the default org",
        org_id_from_claims({}) == DEFAULT_ORG_ID and org_id_from_claims(None) == DEFAULT_ORG_ID,
        "empty/None claims -> DEFAULT_ORG_ID, exactly as before",
        "claimless resolution changed",
    )

    # ── through the REAL endpoint: a brand-new staff identity ─────────────
    _as(STAFF_NEW_SUB, iss=hw_issuer)
    res = client.get("/api/v1/users/me", headers={"Authorization": "Bearer stub"})
    row = await conn.fetchrow(
        "SELECT id, org_id, role FROM users WHERE auth0_sub = $1", STAFF_NEW_SUB
    )
    check(
        "T2: a REAL Hollisworks login creates its users row in the Hollisworks org",
        res.status_code == 200 and row is not None and str(row["org_id"]) == ORG_HW,
        f"GET /users/me -> {res.status_code}; users.org_id = "
        f"{str(row['org_id']) if row else None} (role={row['role'] if row else None})",
        f"status={res.status_code}, org_id={str(row['org_id']) if row else None}",
    )

    # ── the repair path: a row that predates the fix ──────────────────────
    before = await conn.fetchval("SELECT org_id FROM users WHERE id = $1", STAFF_EXISTING_ID)
    _as(STAFF_EXISTING_SUB, iss=hw_issuer)
    res = client.get("/api/v1/users/me", headers={"Authorization": "Bearer stub"})
    after = await conn.fetchrow(
        "SELECT org_id, role FROM users WHERE id = $1", STAFF_EXISTING_ID
    )
    check(
        "T2: an EXISTING staff row holding the wrong org is repaired on next login",
        res.status_code == 200
        and str(before) == ORG_2A
        and str(after["org_id"]) == ORG_HW,
        f"org_id {str(before)} -> {str(after['org_id'])} "
        "(the Hollisworks org has no users, so fixing only new inserts would fix nothing)",
        f"before={str(before)}, after={str(after['org_id']) if after else None}",
    )

    # ── through the REAL endpoint: 2nd Act is untouched ───────────────────
    a2_before = await conn.fetchval("SELECT org_id FROM users WHERE id = $1", MEMBER_2A_ID)
    _as(MEMBER_2A_SUB, iss=settings.issuer)
    res = client.get("/api/v1/users/me", headers={"Authorization": "Bearer stub"})
    a2_after = await conn.fetchval("SELECT org_id FROM users WHERE id = $1", MEMBER_2A_ID)
    check(
        "T2 REGRESSION: a REAL 2nd Act login's org assignment is UNCHANGED",
        res.status_code == 200 and str(a2_before) == ORG_2A and str(a2_after) == ORG_2A,
        f"GET /users/me -> {res.status_code}; users.org_id stayed {str(a2_after)}",
        f"2nd Act org moved: {str(a2_before)} -> {str(a2_after)}",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 3 — profile-aware invite
# ══════════════════════════════════════════════════════════════════════════
def _hdr():
    return {"Authorization": "Bearer stub"}


async def task3_profile_invite(conn, client):
    print("\n=== TASK 3 — optional, validated profile_id on the invite ===")

    _as(ADMIN_2A_SUB, ORG_2A)

    # role still required and still enforced
    res = client.post(
        "/api/v1/admin/invites",
        headers=_hdr(),
        json={"email": f"badrole.{MARKER}@example.com", "role": "super_admin"},
    )
    check(
        "T3: role is still required and still refuses super_admin",
        res.status_code == 400,
        f"POST with role='super_admin' -> {res.status_code} {res.json().get('detail')!r}",
        f"got {res.status_code}",
    )

    # with a valid own-org profile
    email_with = f"withprofile.{MARKER}@example.com"
    res = client.post(
        "/api/v1/admin/invites",
        headers=_hdr(),
        json={"email": email_with, "full_name": "With Profile",
              "role": "member", "profile_id": PROFILE_2A_ID},
    )
    body = res.json() if res.status_code < 500 else {}
    row = await conn.fetchrow(
        "SELECT id, org_id, role, profile_id, invite_status FROM users WHERE email = $1",
        email_with,
    )
    check(
        "T3: an invite can carry an optional, validated profile_id",
        res.status_code == 201
        and row is not None
        and str(row["profile_id"]) == PROFILE_2A_ID
        and row["role"] == "member"
        and row["invite_status"] == "pending",
        f"201; users row has profile_id={str(row['profile_id']) if row else None}, "
        f"role={row['role'] if row else None} — profile is ADDITIVE, role kept",
        f"status={res.status_code} body={body}",
    )

    # without one — still works, profile NULL
    email_without = f"noprofile.{MARKER}@example.com"
    res = client.post(
        "/api/v1/admin/invites",
        headers=_hdr(),
        json={"email": email_without, "role": "org_admin"},
    )
    row = await conn.fetchrow(
        "SELECT role, profile_id FROM users WHERE email = $1", email_without
    )
    check(
        "T3: profile_id is genuinely optional (omitting it still works)",
        res.status_code == 201 and row is not None and row["profile_id"] is None
        and row["role"] == "org_admin",
        "201; profile_id NULL, role='org_admin' — the existing shape is unchanged",
        f"status={res.status_code}",
    )

    # another org's profile is refused
    email_cross = f"crossprofile.{MARKER}@example.com"
    res = client.post(
        "/api/v1/admin/invites",
        headers=_hdr(),
        json={"email": email_cross, "role": "member", "profile_id": PROFILE_HW_ID},
    )
    leaked = await conn.fetchval("SELECT count(*) FROM users WHERE email = $1", email_cross)
    check(
        "T3: a profile from ANOTHER org is refused, and no row is created",
        res.status_code == 404 and int(leaked) == 0,
        f"POST with the Hollisworks org's profile -> {res.status_code} "
        f"{res.json().get('detail')!r}; 0 users rows created",
        f"status={res.status_code}, rows_created={leaked}",
    )

    # an id that exists nowhere
    res = client.post(
        "/api/v1/admin/invites",
        headers=_hdr(),
        json={"email": f"ghostprofile.{MARKER}@example.com", "role": "member",
              "profile_id": "99000000-0000-0000-0000-0000ffff0000"},
    )
    check(
        "T3: an unknown profile_id is refused",
        res.status_code == 404,
        f"-> {res.status_code}",
        f"got {res.status_code}",
    )

    # org_id in the body is rejected outright, not ignored
    res = client.post(
        "/api/v1/admin/invites",
        headers=_hdr(),
        json={"email": f"orgbody.{MARKER}@example.com", "role": "member",
              "org_id": ORG_HW},
    )
    check(
        "T3: org_id in the invite body is REJECTED (422), never silently ignored",
        res.status_code == 422,
        "InviteCreateRequest sets extra='forbid' — the standing rule is mechanical",
        f"got {res.status_code} — a stray org_id was accepted or dropped silently",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 4 — edit user name
# ══════════════════════════════════════════════════════════════════════════
async def task4_edit_name(conn, client):
    print("\n=== TASK 4 — edit full_name within the caller's own org ===")

    _as(ADMIN_2A_SUB, ORG_2A)
    new_name = f"Renamed By Verify {MARKER}"
    res = client.patch(
        f"/api/v1/admin/users/{MEMBER_2A_ID}",
        headers=_hdr(),
        json={"full_name": new_name},
    )
    row = await conn.fetchrow(
        "SELECT full_name, org_id FROM users WHERE id = $1", MEMBER_2A_ID
    )
    check(
        "T4: full_name can be edited for a user in the caller's own org",
        res.status_code == 200 and row["full_name"] == new_name,
        f"PATCH -> 200; users.full_name is now {row['full_name']!r} (read back from the DB)",
        f"status={res.status_code}, db full_name={row['full_name']!r}",
    )

    # cross-org edit refused, and provably did not write
    hw_before = await conn.fetchval("SELECT full_name FROM users WHERE id = $1", MEMBER_HW_ID)
    res = client.patch(
        f"/api/v1/admin/users/{MEMBER_HW_ID}",
        headers=_hdr(),
        json={"full_name": f"SHOULD NOT APPLY {MARKER}"},
    )
    hw_after = await conn.fetchval("SELECT full_name FROM users WHERE id = $1", MEMBER_HW_ID)
    check(
        "T4: a cross-org edit is refused AND the target row is unchanged",
        res.status_code == 404 and hw_before == hw_after,
        f"PATCH a Hollisworks user as a 2nd Act admin -> 404; "
        f"full_name still {hw_after!r} (404 not 403: confirming the id exists "
        "elsewhere would itself be a disclosure)",
        f"status={res.status_code}, before={hw_before!r} after={hw_after!r}",
    )

    # org_id can't be smuggled in
    res = client.patch(
        f"/api/v1/admin/users/{MEMBER_2A_ID}",
        headers=_hdr(),
        json={"full_name": "x", "org_id": ORG_HW},
    )
    check(
        "T4: org_id in the edit body is REJECTED (422), never accepted from a body",
        res.status_code == 422,
        "UserUpdateRequest sets extra='forbid'",
        f"got {res.status_code}",
    )

    # role/is_active are not smuggleable through the rename endpoint either
    res = client.patch(
        f"/api/v1/admin/users/{MEMBER_2A_ID}",
        headers=_hdr(),
        json={"full_name": "x", "is_active": False},
    )
    still_active = await conn.fetchval("SELECT is_active FROM users WHERE id = $1", MEMBER_2A_ID)
    check(
        "T4: is_active cannot be set through the rename endpoint",
        res.status_code == 422 and still_active is True,
        "422; the account is still active — lifecycle has its own audited endpoints",
        f"status={res.status_code}, is_active={still_active}",
    )

    # empty name refused
    res = client.patch(
        f"/api/v1/admin/users/{MEMBER_2A_ID}", headers=_hdr(), json={"full_name": "   "}
    )
    check(
        "T4: a blank full_name is refused",
        res.status_code == 400,
        f"-> {res.status_code}",
        f"got {res.status_code}",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 5 — deactivate (with a REAL session refusal) + the delete decision
# ══════════════════════════════════════════════════════════════════════════
async def task5_deactivate_and_delete(conn, main, client):
    print("\n=== TASK 5 — deactivate / reactivate / delete ===")

    # ── CONTROL: the target can use the API BEFORE deactivation ───────────
    # Without this the 403 below could be caused by anything at all.
    _as(DEACT_2A_SUB, ORG_2A)
    pre = client.get("/api/v1/users/me", headers=_hdr())
    check(
        "T5 CONTROL: the target's own request SUCCEEDS before deactivation",
        pre.status_code == 200,
        f"GET /users/me as the target -> {pre.status_code}",
        f"target could not use the API even before deactivation ({pre.status_code}) "
        "— the refusal test below would prove nothing",
    )

    # ── deactivate, as the admin ──────────────────────────────────────────
    _as(ADMIN_2A_SUB, ORG_2A)
    res = client.post(f"/api/v1/admin/users/{DEACT_2A_ID}/deactivate", headers=_hdr())
    row = await conn.fetchrow(
        "SELECT is_active, deactivated_at, deactivated_by FROM users WHERE id = $1",
        DEACT_2A_ID,
    )
    check(
        "T5: deactivation sets is_active / deactivated_at / deactivated_by for real",
        res.status_code == 200
        and row["is_active"] is False
        and row["deactivated_at"] is not None
        and str(row["deactivated_by"]) == ADMIN_2A_ID,
        f"is_active={row['is_active']}, deactivated_at={row['deactivated_at']}, "
        f"deactivated_by={str(row['deactivated_by'])} (= the calling admin)",
        f"status={res.status_code}, row={dict(row) if row else None}",
    )

    # ── the part that matters: the session check genuinely fails ──────────
    _as(DEACT_2A_SUB, ORG_2A)
    after = client.get("/api/v1/users/me", headers=_hdr())
    detail = after.json().get("detail") if after.status_code < 500 else None
    check(
        "T5: a deactivated user's SUBSEQUENT request genuinely fails (not just the flag)",
        after.status_code == 403 and detail == main.ACCOUNT_DEACTIVATED_DETAIL,
        f"same valid token, same endpoint: 200 before -> {after.status_code} after, "
        f"detail={detail!r}",
        f"status={after.status_code}, detail={detail!r}",
    )
    # and it is refused on an unrelated endpoint too — the gate is in the
    # middleware, not bolted onto one route
    other = client.get("/api/v1/admin/users", headers=_hdr())
    check(
        "T5: the refusal applies to EVERY authenticated route (middleware gate)",
        other.status_code == 403,
        f"GET /admin/users as the deactivated user -> {other.status_code}; the check "
        "lives in main.rls_context_middleware, so no endpoint can forget it",
        f"got {other.status_code}",
    )
    # ...but /health, which is public, is unaffected
    health = client.get("/health")
    check(
        "T5: the gate does not affect public routes",
        health.status_code == 200,
        "/health still 200 while the deactivated identity is in play",
        f"/health -> {health.status_code}",
    )

    # ── reactivate restores access ────────────────────────────────────────
    _as(ADMIN_2A_SUB, ORG_2A)
    res = client.post(f"/api/v1/admin/users/{DEACT_2A_ID}/reactivate", headers=_hdr())
    row = await conn.fetchrow(
        "SELECT is_active, deactivated_at, deactivated_by FROM users WHERE id = $1",
        DEACT_2A_ID,
    )
    _as(DEACT_2A_SUB, ORG_2A)
    back = client.get("/api/v1/users/me", headers=_hdr())
    check(
        "T5: reactivation clears the stamps and restores access",
        res.status_code == 200 and row["is_active"] is True
        and row["deactivated_at"] is None and row["deactivated_by"] is None
        and back.status_code == 200,
        f"is_active=True, stamps cleared; the same token gets {back.status_code} again",
        f"status={res.status_code}, row={dict(row) if row else None}, "
        f"post-reactivate request={back.status_code}",
    )

    # ── self-deactivation is refused ──────────────────────────────────────
    _as(ADMIN_2A_SUB, ORG_2A)
    res = client.post(f"/api/v1/admin/users/{ADMIN_2A_ID}/deactivate", headers=_hdr())
    self_active = await conn.fetchval("SELECT is_active FROM users WHERE id = $1", ADMIN_2A_ID)
    check(
        "T5: an admin cannot deactivate their own account",
        res.status_code == 400 and self_active is True,
        "400 — the gate would then refuse the very request that undoes it, so a "
        "lone org admin would lock the tenant out of member management",
        f"status={res.status_code}, self is_active={self_active}",
    )

    # ══ the hard-delete safety finding, MEASURED ══════════════════════════
    print("\n--- Task 5 hard-delete safety: measured, then decided ---")
    fks = await conn.fetch(
        """
        SELECT con.conrelid::regclass::text AS tbl, att.attname AS col,
               con.confdeltype AS del
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.confrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        JOIN unnest(con.conkey) WITH ORDINALITY k(attnum, ord) ON true
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
        WHERE con.contype = 'f' AND rel.relname = 'users' AND ns.nspname = 'public'
        """
    )
    tables = sorted({r["tbl"] for r in fks})
    no_action = [r for r in fks if r["del"] == b"a"]
    cascade = [r for r in fks if r["del"] == b"c"]
    print(
        f"\nFINDING: users.id has {len(fks)} foreign-key columns pointing at it, "
        f"across {len(tables)} distinct public tables. {len(no_action)} are "
        f"ON DELETE NO ACTION and {len(cascade)} are ON DELETE CASCADE."
    )
    info("NO ACTION examples: " + ", ".join(f"{r['tbl']}.{r['col']}" for r in no_action[:6]))
    info("They include audit_log.user_id, deals.created_by, documents.created_by,")
    info("entities.created_by, member_investments.user_id and users.invited_by —")
    info("i.e. the audit trail and the provenance of essentially every record.")
    info("CASCADE: " + ", ".join(f"{r['tbl']}.{r['col']}" for r in cascade))
    info("Those three make the case STRONGER, not weaker: if the NO ACTION")
    info("constraints were ever relaxed to let a delete succeed, they would")
    info("silently take the member's votes and expressions of interest with them.")
    print(
        "DECISION: a hard delete is NOT safe and was NOT built. DELETE "
        "/admin/users/{id} is implemented as ANONYMIZATION — the row is kept so "
        "every FK still resolves, and the PII on it is destroyed: auth0_sub "
        "cleared (this is what severs the login), email/full_name replaced with "
        "sentinels, avatar/profile/manager/invite cleared, every user_roles and "
        "user_permission_sets grant revoked, account marked inactive. The "
        "response says so in its own body (hard_deleted=false, anonymized=true) "
        "so the caller is never told a deletion happened."
    )
    check(
        "T5: the hard-delete-safety finding is real and measured",
        len(fks) == 92 and len(tables) == 69
        and len(no_action) == 89 and len(cascade) == 3
        and any(r["tbl"] == "audit_log" for r in no_action),
        f"{len(fks)} FK columns across {len(tables)} public tables: "
        f"{len(no_action)} NO ACTION (incl. audit_log.user_id) + {len(cascade)} CASCADE",
        f"counted {len(fks)} FKs / {len(tables)} tables / {len(no_action)} NO ACTION "
        f"/ {len(cascade)} CASCADE — the reported numbers are stale",
    )

    # Prove it, rather than reasoning about it: create a real dependent row and
    # attempt the hard delete for real, inside a transaction that is rolled back.
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id) "
        "VALUES ($1, $2, $3, 'users', $2)",
        ORG_2A, DELETE_2A_ID, f"{MARKER}_fk_probe",
    )
    raised = None
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM users WHERE id = $1", DELETE_2A_ID)
            raise RuntimeError("__rollback__")
    except asyncpg.ForeignKeyViolationError as exc:
        raised = f"{type(exc).__name__}: {getattr(exc, 'detail', '') or exc}"
    except RuntimeError:
        raised = None  # the DELETE unexpectedly succeeded
    still_there = await conn.fetchval(
        "SELECT count(*) FROM users WHERE id = $1", DELETE_2A_ID
    )
    check(
        "T5: a REAL hard DELETE on a user with one dependent row actually raises",
        raised is not None and int(still_there) == 1,
        f"attempted DELETE FROM users -> {raised}; row still present after rollback "
        "(one audit_log row was enough — a real member has hundreds)",
        "the hard delete did NOT raise — the anonymize decision needs revisiting",
    )

    # ── the shipped DELETE: anonymization ─────────────────────────────────
    from routers.admin import ANONYMIZED_EMAIL_DOMAIN, ANONYMIZED_FULL_NAME

    before = await conn.fetchrow(
        "SELECT email, full_name, auth0_sub, profile_id FROM users WHERE id = $1",
        DELETE_2A_ID,
    )
    _as(ADMIN_2A_SUB, ORG_2A)
    res = client.delete(f"/api/v1/admin/users/{DELETE_2A_ID}", headers=_hdr())
    body = res.json() if res.status_code < 500 else {}
    after_row = await conn.fetchrow(
        "SELECT email, full_name, auth0_sub, avatar_url, profile_id, manager_id, "
        "invite_token, invite_status, is_active, deactivated_at, deactivated_by "
        "FROM users WHERE id = $1",
        DELETE_2A_ID,
    )
    check(
        "T5: DELETE anonymizes and SAYS SO (hard_deleted=false, anonymized=true)",
        res.status_code == 200
        and body.get("hard_deleted") is False
        and body.get("anonymized") is True,
        f"response body reports hard_deleted={body.get('hard_deleted')}, "
        f"anonymized={body.get('anonymized')}",
        f"status={res.status_code} body={body}",
    )
    check(
        "T5: the row SURVIVES (so the dependent audit_log row still resolves)",
        after_row is not None,
        "users row retained — every one of the 92 FK references stays valid",
        "the row was removed",
    )
    check(
        "T5: the PII is genuinely gone and the login is severed",
        after_row is not None
        and after_row["email"].endswith(f"@{ANONYMIZED_EMAIL_DOMAIN}")
        and after_row["email"] != before["email"]
        and after_row["full_name"] == ANONYMIZED_FULL_NAME
        and after_row["auth0_sub"] is None
        and after_row["avatar_url"] is None
        and after_row["profile_id"] is None
        and after_row["manager_id"] is None
        and after_row["invite_token"] is None
        and after_row["is_active"] is False
        and after_row["deactivated_at"] is not None,
        f"email {before['email']!r} -> {after_row['email']!r}, full_name -> "
        f"{after_row['full_name']!r}, auth0_sub -> NULL, is_active -> False",
        f"row after anonymization: {dict(after_row) if after_row else None}",
    )
    # the severed login is real: that identity now gets a brand-new empty row
    _as(DELETE_2A_SUB, ORG_2A)
    relog = client.get("/api/v1/users/me", headers=_hdr())
    reclaimed = await conn.fetchrow(
        "SELECT id FROM users WHERE auth0_sub = $1", DELETE_2A_SUB
    )
    check(
        "T5: the anonymized identity can no longer reclaim its old row",
        relog.status_code == 200
        and reclaimed is not None
        and str(reclaimed["id"]) != DELETE_2A_ID,
        "signing in again with the same sub creates a NEW, empty account "
        f"({str(reclaimed['id']) if reclaimed else None}) rather than resuming the old one",
        f"status={relog.status_code}, resolved id={str(reclaimed['id']) if reclaimed else None}",
    )
    # anonymizing twice is refused rather than silently re-running
    _as(ADMIN_2A_SUB, ORG_2A)
    res = client.delete(f"/api/v1/admin/users/{DELETE_2A_ID}", headers=_hdr())
    res2 = client.post(f"/api/v1/admin/users/{DELETE_2A_ID}/reactivate", headers=_hdr())
    check(
        "T5: an anonymized account cannot be re-deleted or reactivated",
        res.status_code == 409 and res2.status_code == 409,
        f"second DELETE -> {res.status_code}; reactivate -> {res2.status_code} "
        "(reactivating would produce an active account nobody can sign into)",
        f"delete={res.status_code}, reactivate={res2.status_code}",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 6 — org-configurable expiry + inactivity
# ══════════════════════════════════════════════════════════════════════════
async def task6_org_settings(conn, client):
    print("\n=== TASK 6 — invite.expiry_days + user.inactivity_timeout_days ===")

    from services.org_settings import (
        INVITE_EXPIRY_DAYS_KEY,
        USER_INACTIVITY_TIMEOUT_DAYS_KEY,
    )

    _as(ADMIN_2A_SUB, ORG_2A)

    # readable through the real settings surface, defaults filled in
    res = client.get(f"/api/v1/orgs/{ORG_2A}/settings?detail=1", headers=_hdr())
    detail = {r["key"]: r for r in res.json().get("settings", [])}
    check(
        "T6: both keys are readable via the real org-settings admin surface",
        res.status_code == 200
        and detail.get(INVITE_EXPIRY_DAYS_KEY, {}).get("value") == 7
        and detail.get(USER_INACTIVITY_TIMEOUT_DAYS_KEY, {}).get("value") == 90
        and detail[INVITE_EXPIRY_DAYS_KEY]["is_default"] is True
        and detail[INVITE_EXPIRY_DAYS_KEY]["category"] == "membership",
        "GET /orgs/{id}/settings?detail=1 returns both keys at their defaults "
        "(7 / 90), flagged is_default, category 'membership'",
        f"status={res.status_code}, invite={detail.get(INVITE_EXPIRY_DAYS_KEY)}, "
        f"inactivity={detail.get(USER_INACTIVITY_TIMEOUT_DAYS_KEY)}",
    )

    # baseline invite uses the DEFAULT
    email_default = f"expiry.default.{MARKER}@example.com"
    client.post("/api/v1/admin/invites", headers=_hdr(),
                json={"email": email_default, "role": "member"})
    default_days = await conn.fetchval(
        "SELECT round(extract(epoch FROM (invite_expires_at - invited_at)) / 86400) "
        "FROM users WHERE email = $1",
        email_default,
    )
    check(
        "T6: with the key unset, a new invite still expires in the default 7 days",
        default_days is not None and int(default_days) == 7,
        f"invite_expires_at - invited_at = {int(default_days) if default_days else None} days",
        f"got {default_days} days",
    )

    # ── write a CUSTOM value through the real endpoint ────────────────────
    # Sent as the STRING "21", exactly as the settings editor's text field posts
    # it — proving the coercion, not just the happy path.
    res = client.put(
        f"/api/v1/orgs/{ORG_2A}/settings",
        headers=_hdr(),
        json={"values": {INVITE_EXPIRY_DAYS_KEY: "21",
                         USER_INACTIVITY_TIMEOUT_DAYS_KEY: "30"}},
    )
    stored = await conn.fetchval(
        "SELECT setting_value FROM org_settings WHERE org_id = $1 AND setting_key = $2",
        ORG_2A, INVITE_EXPIRY_DAYS_KEY,
    )
    check(
        "T6: a value posted as a string is stored as a real jsonb NUMBER",
        res.status_code == 200 and stored == "21",
        f"org_settings.setting_value = {stored!r} (not '\"21\"')",
        f"status={res.status_code}, stored={stored!r}",
    )

    # ── the point: it changes the ACTUAL invite_expires_at written ────────
    email_custom = f"expiry.custom.{MARKER}@example.com"
    res = client.post("/api/v1/admin/invites", headers=_hdr(),
                      json={"email": email_custom, "role": "member"})
    custom_days = await conn.fetchval(
        "SELECT round(extract(epoch FROM (invite_expires_at - invited_at)) / 86400) "
        "FROM users WHERE email = $1",
        email_custom,
    )
    check(
        "T6: a custom invite.expiry_days changes the REAL invite_expires_at written",
        res.status_code == 201 and custom_days is not None and int(custom_days) == 21,
        f"same endpoint, same body: {int(default_days)} days before the setting -> "
        f"{int(custom_days)} days after. Read from users.invite_expires_at, "
        "computed by the DATABASE clock.",
        f"status={res.status_code}, days={custom_days}",
    )

    # ── the OTHER org is unaffected (settings are per-tenant) ─────────────
    _as(ADMIN_HW_SUB, ORG_HW)
    email_hw = f"expiry.hw.{MARKER}@example.com"
    res = client.post("/api/v1/admin/invites", headers=_hdr(),
                      json={"email": email_hw, "role": "member"})
    hw_days = await conn.fetchval(
        "SELECT round(extract(epoch FROM (invite_expires_at - invited_at)) / 86400) "
        "FROM users WHERE email = $1",
        email_hw,
    )
    check(
        "T6: the setting is per-org — the other tenant still gets its own default",
        res.status_code == 201 and hw_days is not None and int(hw_days) == 7,
        f"Hollisworks invite still expires in {int(hw_days)} days while 2nd Act's is 21",
        f"status={res.status_code}, days={hw_days}",
    )

    # ── clearing it falls back to the default again ───────────────────────
    _as(ADMIN_2A_SUB, ORG_2A)
    client.put(f"/api/v1/orgs/{ORG_2A}/settings", headers=_hdr(),
               json={"values": {INVITE_EXPIRY_DAYS_KEY: None}})
    email_cleared = f"expiry.cleared.{MARKER}@example.com"
    client.post("/api/v1/admin/invites", headers=_hdr(),
                json={"email": email_cleared, "role": "member"})
    cleared_days = await conn.fetchval(
        "SELECT round(extract(epoch FROM (invite_expires_at - invited_at)) / 86400) "
        "FROM users WHERE email = $1",
        email_cleared,
    )
    check(
        "T6: clearing the key falls back to the platform default",
        cleared_days is not None and int(cleared_days) == 7,
        f"back to {int(cleared_days)} days",
        f"got {cleared_days} days",
    )

    # ── nonsense values are rejected at WRITE time ────────────────────────
    bad = {}
    for value in ("two weeks", 0, -5, 99999):
        r = client.put(f"/api/v1/orgs/{ORG_2A}/settings/{INVITE_EXPIRY_DAYS_KEY}",
                       headers=_hdr(), json={"value": value})
        bad[repr(value)] = r.status_code
    check(
        "T6: nonsense expiry values are rejected at write time (400), not at read time",
        all(code == 400 for code in bad.values()),
        f"{bad} — an invite token is a bearer credential, so an unbounded expiry "
        "would make it a permanent one",
        f"{bad}",
    )

    # ── the user-management screen can read both ──────────────────────────
    res = client.get("/api/v1/admin/users/settings", headers=_hdr())
    body = res.json() if res.status_code < 500 else {}
    check(
        "T6: both keys are readable from the user-management surface too",
        res.status_code == 200
        and body.get("invite_expiry_days") == 7
        and body.get("user_inactivity_timeout_days") == 30,
        f"GET /admin/users/settings -> {body}",
        f"status={res.status_code} body={body}",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 7 — last_login_at is really written
# ══════════════════════════════════════════════════════════════════════════
async def task7_last_login(conn, client):
    print("\n=== TASK 7 — last_login_at written on a real login ===")

    from services.users import LOGIN_TOUCH_INTERVAL

    await conn.execute("UPDATE users SET last_login_at = NULL WHERE id = $1", LOGIN_2A_ID)
    before = await conn.fetchval("SELECT last_login_at FROM users WHERE id = $1", LOGIN_2A_ID)

    _as(LOGIN_2A_SUB, ORG_2A)
    res = client.get("/api/v1/users/me", headers=_hdr())
    after = await conn.fetchval("SELECT last_login_at FROM users WHERE id = $1", LOGIN_2A_ID)
    check(
        "T7: a real login writes last_login_at (proven by direct query, before/after)",
        res.status_code == 200 and before is None and after is not None,
        f"direct SELECT: {before!r} -> {after!r}",
        f"status={res.status_code}, before={before!r} after={after!r}",
    )

    # Prove the UPDATE path, not just the first write: back-date the column and
    # confirm the next real request moves it forward.
    stale = datetime.now(timezone.utc) - timedelta(days=1)
    await conn.execute(
        "UPDATE users SET last_login_at = $2 WHERE id = $1", LOGIN_2A_ID, stale
    )
    res = client.get("/api/v1/users/me", headers=_hdr())
    refreshed = await conn.fetchval(
        "SELECT last_login_at FROM users WHERE id = $1", LOGIN_2A_ID
    )
    check(
        "T7: a stale last_login_at is REFRESHED on the next login, not only set once",
        res.status_code == 200 and refreshed is not None and refreshed > stale,
        f"back-dated to {stale.isoformat()} -> refreshed to {refreshed.isoformat()}",
        f"stale={stale!r}, after={refreshed!r}",
    )

    # ...and the throttle is real, so this is not a row write per API call.
    res = client.get("/api/v1/users/me", headers=_hdr())
    again = await conn.fetchval("SELECT last_login_at FROM users WHERE id = $1", LOGIN_2A_ID)
    check(
        "T7: the write is throttled — a second immediate request does not re-write",
        again == refreshed,
        f"unchanged within the {LOGIN_TOUCH_INTERVAL} window; ensure_user runs on "
        "EVERY request, so an unconditional UPDATE would be a row write per API call",
        f"{refreshed!r} -> {again!r}",
    )

    # ── the admin list actually surfaces it ───────────────────────────────
    _as(ADMIN_2A_SUB, ORG_2A)
    res = client.get("/api/v1/admin/users", headers=_hdr(),
                     params={"search": MARKER, "limit": 200})
    rows = res.json() if res.status_code == 200 else []
    listed = next((r for r in rows if r["id"] == LOGIN_2A_ID), None)
    check(
        "T7: GET /admin/users surfaces last_login_at (what the list column renders)",
        res.status_code == 200 and listed is not None and listed.get("last_login_at"),
        f"row for the test user carries last_login_at={listed.get('last_login_at')!r}, "
        f"is_active={listed.get('is_active')}, is_deleted={listed.get('is_deleted')}",
        f"status={res.status_code}, row={listed}",
    )

    # ── and the frontend really renders it ────────────────────────────────
    ui = os.path.join(_REPO_ROOT, "apps", "web", "components", "admin", "UserManagement.jsx")
    with open(ui) as fh:
        src = fh.read()
    check(
        "T7: the UI has the deactivate/reactivate/delete controls and the column",
        "deactivateUserAction" in src
        and "reactivateUserAction" in src
        and "deleteUserAction" in src
        and "Last Sign-in" in src
        and "lastLoginLabel(u.last_login_at)" in src,
        "UserManagement.jsx wires all three lifecycle actions and renders a "
        "Last Sign-in column from last_login_at",
        "the UI is missing one of the controls or the column",
    )


# ══════════════════════════════════════════════════════════════════════════
# TASK 8 — cross-org isolation on EVERY new endpoint
# ══════════════════════════════════════════════════════════════════════════
async def task8_cross_org(conn, client):
    print("\n=== TASK 8 — cross-org isolation on every new endpoint ===")

    # A 2nd Act org_admin (NOT a super_admin — deliberately, since Super Admin
    # is allowed to cross orgs by design) reaching for a Hollisworks user.
    _as(ADMIN_2A_SUB, ORG_2A)
    attempts = {
        "PATCH /admin/users/{id}": client.patch(
            f"/api/v1/admin/users/{MEMBER_HW_ID}", headers=_hdr(),
            json={"full_name": f"CROSS {MARKER}"}),
        "POST /admin/users/{id}/deactivate": client.post(
            f"/api/v1/admin/users/{MEMBER_HW_ID}/deactivate", headers=_hdr()),
        "POST /admin/users/{id}/reactivate": client.post(
            f"/api/v1/admin/users/{MEMBER_HW_ID}/reactivate", headers=_hdr()),
        "DELETE /admin/users/{id}": client.delete(
            f"/api/v1/admin/users/{MEMBER_HW_ID}", headers=_hdr()),
    }
    statuses = {k: r.status_code for k, r in attempts.items()}
    victim = await conn.fetchrow(
        "SELECT full_name, is_active, auth0_sub, email FROM users WHERE id = $1",
        MEMBER_HW_ID,
    )
    check(
        "T8: every new lifecycle endpoint refuses a cross-org target",
        all(code == 404 for code in statuses.values()),
        f"{statuses} — all 404",
        f"{statuses}",
    )
    check(
        "T8: and the cross-org target row is provably untouched",
        victim["full_name"] == "Verify Member HW"
        and victim["is_active"] is True
        and victim["auth0_sub"] == MEMBER_HW_SUB
        and not victim["email"].endswith("@deleted.invalid"),
        "name, is_active, auth0_sub and email are all exactly as seeded",
        f"row was modified: {dict(victim)}",
    )

    # the reverse direction, so this is not a one-way accident
    _as(ADMIN_HW_SUB, ORG_HW)
    rev = client.patch(f"/api/v1/admin/users/{MEMBER_2A_ID}", headers=_hdr(),
                       json={"full_name": f"REVERSE {MARKER}"})
    check(
        "T8: isolation holds in the reverse direction too",
        rev.status_code == 404,
        f"a Hollisworks admin editing a 2nd Act user -> {rev.status_code}",
        f"got {rev.status_code}",
    )

    # the org-scoped read surfaces are scoped too
    _as(ADMIN_HW_SUB, ORG_HW)
    listing = client.get("/api/v1/admin/users", headers=_hdr(),
                         params={"search": MARKER, "limit": 200})
    ids = {r["id"] for r in (listing.json() if listing.status_code == 200 else [])}
    check(
        "T8: GET /admin/users returns only the caller's own org",
        listing.status_code == 200
        and MEMBER_HW_ID in ids
        and MEMBER_2A_ID not in ids
        and ADMIN_2A_ID not in ids,
        f"the Hollisworks admin sees {len(ids)} test rows, none of them 2nd Act's",
        f"leak: {ids}",
    )

    # ── the same isolation against the REAL app_service (RLS) connection ──
    #
    # What a BLOCKED here does and does not mean: the application connects as
    # `postgres` (rolbypassrls) in production today, so RLS is inert there too.
    # The isolation proven above — driven through the real endpoints — is the
    # APPLICATION's org predicates, which is the layer actually enforcing right
    # now. The RLS leg is defence in depth for the day the connection role
    # changes. It is reported BLOCKED rather than PASS because it was not
    # measured, not because it is known to fail.
    RLS_CAVEAT = (
        "The app connects as `postgres` (rolbypassrls) today, so RLS is inert in "
        "production too; the cross-org isolation PASSED above is the application's "
        "own org predicates, driven through the real endpoints. This leg is "
        "defence-in-depth and was NOT measured — not known-broken."
    )
    if not APP_SERVICE_DATABASE_URL:
        blocked(
            "T8: cross-org isolation on the real app_service connection",
            f"APP_SERVICE_DATABASE_URL is not set in this environment. {RLS_CAVEAT}",
        )
    else:
        app_conn = None
        try:
            app_conn = await asyncpg.connect(
                APP_SERVICE_DATABASE_URL, statement_cache_size=0
            )
            role = await app_conn.fetchval("SELECT current_user")
            bypass = await app_conn.fetchval(
                "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            if bypass:
                blocked(
                    "T8: cross-org isolation on the real app_service connection",
                    f"APP_SERVICE_DATABASE_URL connects as {role!r}, which has "
                    f"rolbypassrls — RLS cannot be measured on it. {RLS_CAVEAT}",
                )
            else:
                await app_conn.execute("SET LOCAL app.current_org_id = $1", ORG_2A)
                # A control first: without a control, "0 rows" could mean the
                # query is simply wrong rather than that RLS is working.
                own = await app_conn.fetchval(
                    "SELECT count(*) FROM users WHERE id = $1", MEMBER_2A_ID
                )
                other = await app_conn.fetchval(
                    "SELECT count(*) FROM users WHERE id = $1", MEMBER_HW_ID
                )
                check(
                    "T8: cross-org isolation on the real app_service connection",
                    int(own) == 1 and int(other) == 0,
                    f"as {role!r} with org={ORG_2A}: own row visible ({own}), "
                    f"other org's row invisible ({other})",
                    f"as {role!r}: own={own}, other={other}",
                )
        except Exception as exc:  # noqa: BLE001
            blocked(
                "T8: cross-org isolation on the real app_service connection",
                f"could not use APP_SERVICE_DATABASE_URL: {exc}. {RLS_CAVEAT}",
            )
        finally:
            if app_conn is not None:
                await app_conn.close()


# ── entry point ─────────────────────────────────────────────────────────────
def main_entry():
    asyncio.run(run())

    passes = sum(1 for s, _, _ in _RESULTS if s == "PASS")
    fails = sum(1 for s, _, _ in _RESULTS if s == "FAIL")
    blocks = sum(1 for s, _, _ in _RESULTS if s == "BLOCKED")
    total = len(_RESULTS)

    print("\n" + "=" * 72)
    print(f"verify_usermanagementfinish: {passes}/{total} PASS, {fails} FAIL, {blocks} BLOCKED")
    if fails:
        print("\nFAILURES:")
        for status, name, detail in _RESULTS:
            if status == "FAIL":
                print(f"  - {name}: {detail}")
    if blocks:
        print("\nBLOCKED (not counted as pass):")
        for status, name, detail in _RESULTS:
            if status == "BLOCKED":
                print(f"  - {name}: {detail}")
    print("=" * 72)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main_entry()
