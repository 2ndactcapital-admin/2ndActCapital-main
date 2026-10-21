"""AUTHZ1 shared definitions — the Task 3 role seed and the real-user report.

Imported by ``_apply_authz1.py`` (which applies the seed) and
``verify_authz1.py`` (which asserts it). One definition, so the verify can
never drift from what was applied.
"""

from __future__ import annotations

# Orgs whose id starts with this are verify fixtures — never seeded.
FIXTURE_ORG_PREFIX = "99000000-"
# Real (non-fixture) users: every address NOT matching these.
FIXTURE_EMAIL_PATTERNS = ("%@test.local", "authz1verify%")

MEMBER_PERMS = frozenset({
    "indicate_interest", "submit_deal", "view_community", "view_dashboard",
    "view_deals", "view_insurance", "view_marketplace", "view_portfolio",
    "view_spv", "vote_deal",
})
INVESTMENT_STAFF_PERMS = frozenset({
    "indicate_interest", "manage_deals", "manage_documents", "override_compliance",
    "score_deal", "submit_deal", "view_compliance", "view_dashboard", "view_deals",
    "view_members", "view_portfolio", "view_workflow_runs", "vote_deal",
})
# 2nd Act's live admin set, PLUS what the token-claim "staff implies every
# permission" rule used to hand an admin implicitly (override_compliance,
# manage_documents, score_deal, vote_deal, view_deals, indicate_interest) and
# AUTHZ1's new manage_ledger. Exact checks would otherwise take these away.
ADMIN_PERMS = frozenset({
    "author_workflows", "configure_workflow_triggers", "manage_billing",
    "manage_compliance", "manage_content", "manage_custody_connections",
    "manage_deals", "manage_members", "manage_portfolio", "manage_spv",
    "manage_users", "submit_deal", "view_audit_log", "view_community",
    "view_compliance", "view_custody_connections", "view_dashboard",
    "view_insurance", "view_marketplace", "view_members", "view_portfolio",
    "view_spv", "view_workflow_runs",
    "override_compliance", "manage_documents", "score_deal", "vote_deal",
    "view_deals", "indicate_interest", "manage_ledger",
})
# A super_admin row passes every check on users.role alone; the role row is
# the RBAC mirror of that string (Task 3: "the role matching users.role").
SUPER_ADMIN_PERMS = ADMIN_PERMS | {"manage_roles", "manage_org_settings"}

BASELINE_ROLES = {
    "member": ("Member — the member-facing pages.", MEMBER_PERMS),
    "investment_staff": ("Investment staff.", INVESTMENT_STAFF_PERMS),
    "admin": ("Firm administrator (staff).", ADMIN_PERMS),
    "super_admin": ("Platform staff — RBAC mirror of users.role 'super_admin'.", SUPER_ADMIN_PERMS),
}

# users.role → the role granted to a real user by Task 3. org_admin is granted
# through services.rbac.grant_org_admin (unchanged mechanism).
ACCOUNT_ROLE_TO_ROLE = {
    "member": "member",
    "org_admin": "org_admin",
    "super_admin": "super_admin",
}


async def real_users(conn) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, org_id, email, role, status, is_active, auth0_sub
        FROM users
        WHERE NOT (email ILIKE ANY($1::text[]))
          AND org_id::text NOT LIKE $2
        ORDER BY email
        """,
        list(FIXTURE_EMAIL_PATTERNS), FIXTURE_ORG_PREFIX + "%",
    )
    return [dict(r) for r in rows]


async def granted(conn, user_id, org_id) -> tuple[list[str], set[str]]:
    roles = [r["name"] for r in await conn.fetch(
        "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
        "WHERE ur.user_id = $1 AND r.org_id = $2 ORDER BY r.name", user_id, org_id)]
    perms = {r["name"] for r in await conn.fetch(
        "SELECT DISTINCT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
        "JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id "
        "WHERE ur.user_id = $1 AND r.org_id = $2", user_id, org_id)}
    return roles, perms


async def effective_report(conn, *, legacy: bool) -> dict[str, dict]:
    """Per real user: effective DB-gate permissions.

    ``legacy=True`` reproduces the pre-AUTHZ1 rbac rule (super admin → all;
    NO roles → all, the default-allow; else the role set). ``legacy=False`` is
    services.authz (super admin → all; not admitted → none; else exact).
    Token-claim gates are reported separately: before AUTHZ1 they admitted
    every real caller unconditionally.
    """
    catalog = {r["name"] for r in await conn.fetch("SELECT name FROM permissions")}
    out = {}
    for u in await real_users(conn):
        roles, perms = await granted(conn, u["id"], u["org_id"])
        admitted = (u["status"] == "active") and bool(u["is_active"])
        if legacy:
            eff = catalog if (u["role"] == "super_admin" or not roles) else perms
        else:
            eff = set() if not admitted else (catalog if u["role"] == "super_admin" else perms)
        out[u["email"]] = {
            "user_id": str(u["id"]), "org_id": str(u["org_id"]), "account_role": u["role"],
            "status": u["status"], "roles": roles, "effective": sorted(eff),
            "is_super_admin": admitted and u["role"] == "super_admin" if not legacy else u["role"] == "super_admin",
        }
    return out
