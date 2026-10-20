/**
 * navregroup — the six personas, shaped exactly like a /api/v1/users/me payload.
 *
 * Shared by navregroup_baseline.mjs (the frozen pre-sprint snapshot), the
 * menu-visibility harness and the web node tests, so the baseline and the
 * after-check can never be computed over different fixtures.
 *
 * Permission lists are the LIVE role grants read from the dev database on
 * 2026-10-20 (roles ⋈ role_permissions ⋈ permissions, org 0000…0001), not
 * invented: 'member', 'investment_staff', 'member_manager', 'org_admin' and the
 * granted 'admin' role the live super_admin account holds.
 */

export const PERSONAS = {
  plain_member: {
    account_role: "member",
    role: "member",
    roles: ["member"],
    permissions: [
      "indicate_interest", "submit_deal", "view_community", "view_dashboard", "view_deals",
      "view_insurance", "view_marketplace", "view_portfolio", "view_spv", "vote_deal",
    ],
  },
  staff_member: {
    account_role: "member",
    role: "investment_staff",
    roles: ["investment_staff"],
    permissions: [
      "indicate_interest", "manage_deals", "manage_documents", "override_compliance", "score_deal",
      "submit_deal", "view_compliance", "view_dashboard", "view_deals", "view_members",
      "view_portfolio", "view_workflow_runs", "vote_deal",
    ],
  },
  org_admin_manage_members: {
    account_role: "member",
    role: "member_manager",
    roles: ["member_manager"],
    permissions: ["manage_members", "view_compliance", "view_dashboard", "view_deals", "view_members"],
  },
  org_admin_manage_org_settings: {
    account_role: "org_admin",
    role: "org_admin",
    roles: ["org_admin"],
    permissions: ["manage_org_settings", "review_agent_proposals"],
  },
  super_admin: {
    account_role: "super_admin",
    role: "admin",
    roles: ["admin"],
    permissions: [
      "author_workflows", "configure_workflow_triggers", "manage_billing", "manage_compliance",
      "manage_content", "manage_custody_connections", "manage_deals", "manage_members",
      "manage_portfolio", "manage_spv", "manage_users", "submit_deal", "view_audit_log",
      "view_community", "view_compliance", "view_custody_connections", "view_dashboard",
      "view_insurance", "view_marketplace", "view_members", "view_portfolio", "view_spv",
      "view_workflow_runs",
    ],
  },
  // The documented "no roles assigned yet → default-allow" branch.
  no_roles: {
    account_role: "member",
    role: null,
    roles: [],
    permissions: [],
  },
};

export const PERSONA_NAMES = Object.keys(PERSONAS);
