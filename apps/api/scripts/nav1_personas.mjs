/**
 * NAV1 — the personas the nav1 proofs run over, /users/me-shaped.
 *
 * navregroup's six (navregroup_personas.mjs, live grants 2026-10-20) plus
 * STAFFROLES1's internal roles (live grants of org 2nd Act, 2026-11-04 — copied
 * by hand from apps/web/tests/staffroles1/staffroles1.test.mjs, never derived
 * from the module under test). Shared by nav1_snapshot.mjs (the frozen pre-NAV1
 * baseline), the web node test and verify_nav1.py, so the baseline and the
 * after-check can never run over different fixtures.
 */
import { PERSONAS as NAVREGROUP } from "./navregroup_personas.mjs";

const ORG_ADMIN_PERMS = ["approve_authority", "approve_blocks", "approve_execution", "approve_held_away",
  "approve_models", "approve_proposals", "approve_replication", "approve_tax", "manage_authority", "manage_blocks",
  "manage_execution", "manage_held_away", "manage_hypothetical_policy", "manage_models", "manage_org_settings",
  "manage_proposals", "manage_replication", "manage_tax", "review_agent_proposals", "view_authority", "view_blocks",
  "view_execution", "view_held_away", "view_models", "view_proposals", "view_replication", "view_tax"];
const SUPPORT_PERMS = ["review_agent_proposals", "view_community", "view_compliance", "view_dashboard", "view_deals",
  "view_members", "view_portfolio", "view_workflow_runs"];
const MEMBER_PERMS = ["indicate_interest", "submit_deal", "view_community", "view_dashboard", "view_deals",
  "view_insurance", "view_marketplace", "view_portfolio", "view_spv", "vote_deal"];

export const PERSONAS = {
  ...NAVREGROUP,
  member: { status: "active", account_role: "member", role: "member", roles: ["member"],
    is_staff: false, is_internal: false, is_super_admin: false, permissions: MEMBER_PERMS },
  next_gen: { status: "active", account_role: "member", role: "next_gen", roles: ["next_gen"],
    is_staff: false, is_internal: false, is_super_admin: false, permissions: ["view_community", "view_dashboard"] },
  support_staff: { status: "active", account_role: "member", role: "support_staff", roles: ["support_staff"],
    is_staff: false, is_internal: true, is_super_admin: false, permissions: SUPPORT_PERMS },
  org_admin: { status: "active", account_role: "org_admin", role: "org_admin", roles: ["org_admin"],
    is_staff: false, is_internal: true, is_super_admin: false, permissions: ORG_ADMIN_PERMS },
  super_admin_bare: { status: "active", account_role: "super_admin", role: "super_admin", roles: ["super_admin"],
    is_staff: true, is_internal: true, is_super_admin: true, permissions: [] },
  // A lost /users/me envelope: every strict gate must hide.
  lost_envelope: null,
};

export const PERSONA_NAMES = Object.keys(PERSONAS);
