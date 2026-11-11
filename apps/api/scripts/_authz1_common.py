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


# Role names copied from services.authz (STAFF_ROLE_NAMES | INTERNAL_ROLE_NAMES) so the recompute stays a SECOND
# reader of the rule, not a call into the code under test. verify_authz1 asserts the copy still equals the code's.
NON_CLIENT_ROLE_NAMES = frozenset({
    "investment_staff", "admin", "super_admin", "owner",
    "org_admin", "support_staff", "advisor", "compliance", "fund_finance",
    "investment_committee", "ir_member_relations", "member_manager",
})


async def profile_granted(conn, user_id, org_id, roles, role_perms) -> set[str]:
    """PERMS1's profile + permission-set keys for one user (catalog keys, own org), member-safe cut if client-like."""
    keys = {r["k"] for r in await conn.fetch(
        "SELECT pp.permission_key AS k FROM users u JOIN profiles pr ON pr.id = u.profile_id AND pr.org_id = u.org_id "
        "JOIN profile_permissions pp ON pp.profile_id = pr.id WHERE u.id = $1 "
        "UNION SELECT psp.permission_key FROM user_permission_sets ups "
        "JOIN permission_sets ps ON ps.id = ups.permission_set_id AND ps.org_id = $2 "
        "JOIN permission_set_permissions psp ON psp.permission_set_id = ps.id WHERE ups.user_id = $1",
        user_id, org_id)}
    client_like = not (NON_CLIENT_ROLE_NAMES.intersection(roles)
                       or "manage_org_settings" in role_perms or "manage_members" in role_perms)
    if client_like:
        keys &= {r["name"] for r in await conn.fetch(
            "SELECT p.name FROM roles r JOIN role_permissions rp ON rp.role_id = r.id "
            "JOIN permissions p ON p.id = rp.permission_id WHERE r.org_id = $1 AND r.name = 'member'", org_id)}
    return keys


async def effective_report(conn, *, legacy: bool, profiles: bool = False) -> dict[str, dict]:
    """Per real user: effective DB-gate permissions.

    ``legacy=True`` reproduces the pre-AUTHZ1 rbac rule (super admin → all;
    NO roles → all, the default-allow; else the role set). ``legacy=False`` is
    services.authz (super admin → all; not admitted → none; else exact).
    Token-claim gates are reported separately: before AUTHZ1 they admitted
    every real caller unconditionally.

    ``profiles=True`` (CPSIM01 FIX 1) adds PERMS1's second grant source, which
    services.authz has unioned since PERMS1 and this recompute never read: the
    user's profile keys + permission-set keys (own org, catalog keys only),
    cut to the org's ``member`` role keys when the user is client-like BY ROLE
    STANDING (BUGFIX2). Each key's source is recorded under ``sources``.
    """
    catalog = {r["name"] for r in await conn.fetch("SELECT name FROM permissions")}
    out = {}
    for u in await real_users(conn):
        roles, perms = await granted(conn, u["id"], u["org_id"])
        admitted = (u["status"] == "active") and bool(u["is_active"])
        extra: set[str] = set()
        if profiles and not legacy:
            extra = await profile_granted(conn, u["id"], u["org_id"], roles, perms) & catalog
        if legacy:
            eff = catalog if (u["role"] == "super_admin" or not roles) else perms
        else:
            eff = set() if not admitted else (catalog if u["role"] == "super_admin" else perms | extra)
        out[u["email"]] = {
            "user_id": str(u["id"]), "org_id": str(u["org_id"]), "account_role": u["role"],
            "status": u["status"], "roles": roles, "effective": sorted(eff),
            "sources": {"roles": sorted(perms), "profile_or_sets_only": sorted(extra - perms)},
            "is_super_admin": admitted and u["role"] == "super_admin" if not legacy else u["role"] == "super_admin",
        }
    return out
