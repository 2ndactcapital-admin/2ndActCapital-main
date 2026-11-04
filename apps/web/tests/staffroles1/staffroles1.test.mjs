// STAFFROLES1 — "may SEE staff screens" (is_internal) is separate from "skips
// permission checks" (is_staff). Personas are /users/me-shaped with the LIVE
// role grants of org 2nd Act read on 2026-11-04 (roles ⋈ role_permissions),
// written by hand — never derived from the module under test.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

process.env.NAV_TEST_STUBS = "1";
register("../market/jsxLoader.mjs", import.meta.url);
register("../nav/navLoader.mjs", import.meta.url);

const menu = await import("../../lib/menuVisibility.mjs");

const ORG_ADMIN_PERMS = ["approve_authority", "approve_blocks", "approve_execution", "approve_held_away",
  "approve_models", "approve_proposals", "approve_replication", "approve_tax", "manage_authority", "manage_blocks",
  "manage_execution", "manage_held_away", "manage_hypothetical_policy", "manage_models", "manage_org_settings",
  "manage_proposals", "manage_replication", "manage_tax", "review_agent_proposals", "view_authority", "view_blocks",
  "view_execution", "view_held_away", "view_models", "view_proposals", "view_replication", "view_tax"];
const SUPPORT_PERMS = ["review_agent_proposals", "view_community", "view_compliance", "view_dashboard", "view_deals",
  "view_members", "view_portfolio", "view_workflow_runs"];
const MEMBER_PERMS = ["indicate_interest", "submit_deal", "view_community", "view_dashboard", "view_deals",
  "view_insurance", "view_marketplace", "view_portfolio", "view_spv", "vote_deal"];

export const P = {
  org_admin: { status: "active", account_role: "org_admin", role: "org_admin", roles: ["org_admin"],
    is_staff: false, is_internal: true, is_super_admin: false, permissions: ORG_ADMIN_PERMS },
  support_staff: { status: "active", account_role: "member", role: "support_staff", roles: ["support_staff"],
    is_staff: false, is_internal: true, is_super_admin: false, permissions: SUPPORT_PERMS },
  member: { status: "active", account_role: "member", role: "member", roles: ["member"],
    is_staff: false, is_internal: false, is_super_admin: false, permissions: MEMBER_PERMS },
  next_gen: { status: "active", account_role: "member", role: "next_gen", roles: ["next_gen"],
    is_staff: false, is_internal: false, is_super_admin: false, permissions: ["view_community", "view_dashboard"] },
  investment_staff: { status: "active", account_role: "member", role: "investment_staff",
    roles: ["investment_staff"], is_staff: true, is_internal: true, is_super_admin: false,
    permissions: ["indicate_interest", "manage_deals", "manage_documents", "override_compliance", "score_deal",
      "submit_deal", "view_compliance", "view_dashboard", "view_deals", "view_members", "view_portfolio",
      "view_workflow_runs", "vote_deal"] },
  super_admin: { status: "active", account_role: "super_admin", role: "super_admin", roles: ["super_admin"],
    is_staff: true, is_internal: true, is_super_admin: true, permissions: [] },
  // a role nobody classified: the server publishes is_internal false for it
  unknown_role: { status: "active", account_role: "member", role: "brand_new_role", roles: ["brand_new_role"],
    is_staff: false, is_internal: false, is_super_admin: false, permissions: ["view_members", "view_spv"] },
};

const allHrefs = (me) => [...new Set([
  ...menu.visibleMenuItems(me).map((i) => i.href),
  ...menu.visibleSections(me).flatMap((s) => s.entries.flatMap((e) => e.tabHrefs)),
  ...menu.visibleAdminGroups(me).flatMap((g) => g.tabs.map((t) => t.href)),
])];

// Staff screens (Task 1c) — hand list.
const STAFF_SCREENS = ["/crm", "/spvs", "/admin/billing-groups", "/admin/custody-import"];

test("org admin: sees CRM, the documents screens, Instructions and its settings; not SPV / billing (no view_spv / view_portfolio)", () => {
  const seen = allHrefs(P.org_admin);
  for (const h of ["/crm", "/documents", "/portfolio/instructions", "/portfolio/replication", "/admin/settings",
    "/admin/profiles"]) assert.ok(seen.includes(h), h);
  for (const h of ["/spvs", "/admin/billing-groups", "/admin/custody-import"]) assert.ok(!seen.includes(h), h);
  assert.equal(menu.canOpenArea(P.org_admin, "/crm"), true);
  assert.equal(menu.canOpenArea(P.org_admin, "/spvs"), false);
  assert.equal(menu.canOpenArea(P.org_admin, "/admin"), true);
});

test("support staff: sees CRM, documents and Billing & Custody (holds view_portfolio); not SPV, settings or replication", () => {
  const seen = allHrefs(P.support_staff);
  for (const h of ["/crm", "/documents", "/admin/billing-groups", "/admin/custody-import"]) {
    assert.ok(seen.includes(h), h);
  }
  for (const h of ["/spvs", "/admin/settings", "/portfolio/replication", "/portfolio/instructions"]) {
    assert.ok(!seen.includes(h), h);
  }
  assert.equal(menu.canOpenArea(P.support_staff, "/crm"), true);
  assert.equal(menu.canOpenArea(P.support_staff, "/spvs"), false);
});

test("seeing is not staff: org admin and support staff are internal but NOT staff (no bypass)", () => {
  for (const me of [P.org_admin, P.support_staff]) {
    assert.equal(menu.isInternalStrict(me), true);
    assert.equal(menu.isStaffStrict(me), false);
    assert.equal(menu.isMemberOnly(me), false);
  }
});

test("a member and a next-gen client see no staff screen at all", () => {
  for (const me of [P.member, P.next_gen]) {
    const seen = allHrefs(me);
    assert.deepEqual(STAFF_SCREENS.filter((h) => seen.includes(h)), []);
    assert.equal(menu.isInternalStrict(me), false);
    assert.equal(menu.isMemberOnly(me), true);
    for (const area of ["/crm", "/spvs", "/admin"]) assert.equal(menu.canOpenArea(me, area), false, area);
  }
});

test("the member holds view_spv / view_portfolio and is still refused SPV Manager and billing (client role)", () => {
  assert.ok(P.member.permissions.includes("view_spv") && P.member.permissions.includes("view_portfolio"));
  const seen = allHrefs(P.member);
  for (const h of ["/spvs", "/admin/billing-groups"]) assert.ok(!seen.includes(h), h);
});

test("an unknown role is not internal, even holding view_members / view_spv", () => {
  assert.equal(menu.isInternalStrict(P.unknown_role), false);
  const seen = allHrefs(P.unknown_role);
  assert.deepEqual(STAFF_SCREENS.filter((h) => seen.includes(h)), []);
});

test("investment staff and Super Admin unchanged: every staff screen", () => {
  for (const me of [P.investment_staff, P.super_admin]) {
    const seen = allHrefs(me);
    for (const h of STAFF_SCREENS) assert.ok(seen.includes(h), h);
    for (const area of ["/crm", "/spvs"]) assert.equal(menu.canOpenArea(me, area), true, area);
  }
});

test("fail closed: a lost envelope, a string flag, a pending account", () => {
  for (const me of [null, undefined, {}, { is_internal: "true" }, { ...P.org_admin, status: "pending" },
    { ...P.support_staff, is_internal: undefined }]) {
    assert.equal(menu.isInternalStrict(me), false, JSON.stringify(me));
    assert.equal(menu.canOpenArea(me, "/crm"), false, JSON.stringify(me));
  }
});
