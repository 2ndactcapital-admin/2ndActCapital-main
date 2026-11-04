// PERMS1 — the front end of "one source of permissions": /users/me now carries
// role ∪ profile ∪ permission-set keys, so the menu gates on the keys the APIs
// check. Personas are /users/me-shaped, written by hand from the live grants
// AFTER docs/perms1_part1.sql (2026-11-04) — never derived from the module.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

process.env.NAV_TEST_STUBS = "1";
register("../market/jsxLoader.mjs", import.meta.url);
register("../nav/navLoader.mjs", import.meta.url);

const menu = await import("../../lib/menuVisibility.mjs");
const perms1 = await import("../../lib/perms1.mjs");
const wf = await import("../../lib/workflowLibrary.mjs");
const owners = await import("../../lib/hierarchyOwners.mjs");

const ORG_ADMIN_BEFORE = ["approve_authority", "approve_blocks", "approve_execution", "approve_held_away",
  "approve_models", "approve_proposals", "approve_replication", "approve_tax", "manage_authority", "manage_blocks",
  "manage_execution", "manage_held_away", "manage_hypothetical_policy", "manage_models", "manage_org_settings",
  "manage_proposals", "manage_replication", "manage_tax", "review_agent_proposals", "view_authority", "view_blocks",
  "view_execution", "view_held_away", "view_models", "view_proposals", "view_replication", "view_tax"];
const PERMS1_ADDED = ["view_portfolio", "view_members", "view_dashboard", "view_deals", "view_workflow_runs",
  "author_workflows", "configure_workflow_triggers", "manage_deals", "manage_members"];
const SUPPORT = ["manage_documents", "review_agent_proposals", "view_community", "view_compliance", "view_dashboard",
  "view_deals", "view_marketplace", "view_members", "view_portfolio", "view_workflow_runs"];
const MEMBER = ["indicate_interest", "submit_deal", "view_community", "view_dashboard", "view_deals",
  "view_insurance", "view_marketplace", "view_portfolio", "view_spv", "vote_deal"];

const persona = (roles, permissions, extra = {}) => ({ status: "active", account_role: "member", role: roles[0],
  roles, is_staff: false, is_internal: true, is_super_admin: false, permissions, ...extra });
const P = {
  orgAdminBefore: persona(["org_admin"], ORG_ADMIN_BEFORE, { account_role: "org_admin" }),
  orgAdmin: persona(["org_admin"], [...ORG_ADMIN_BEFORE, ...PERMS1_ADDED], { account_role: "org_admin" }),
  support: persona(["support_staff"], SUPPORT),
  member: persona(["member"], MEMBER, { is_internal: false }),
};
const hrefs = (me) => menu.visibleMenuItems(me).map((i) => i.href);

test("org admin, after PERMS1: Workflows, Runs and Triggers tabs (before: Runs / Triggers super-admin only)", () => {
  for (const h of ["/admin/workflows", "/admin/workflows/runs", "/admin/workflows/triggers"]) {
    assert.ok(hrefs(P.orgAdmin).includes(h), h);
  }
  assert.ok(!hrefs(P.orgAdminBefore).includes("/admin/workflows/runs"));
  assert.ok(!hrefs(P.orgAdminBefore).includes("/admin/workflows/triggers"));
});

test("WF-04: the fee screens are menu entries for an internal role holding view_portfolio, never a member", () => {
  for (const h of ["/fee-schedules/chat", "/profitability"]) {
    assert.ok(hrefs(P.orgAdmin).includes(h), `org admin ${h}`);
    assert.ok(hrefs(P.support).includes(h), `support ${h}`);
    assert.ok(!hrefs(P.member).includes(h), `member ${h}`);
    assert.ok(!hrefs(P.orgAdminBefore).includes(h), `org admin before ${h}`);
  }
});

test("support staff: Run Console and Triggers read (view_workflow_runs), no library (no author_workflows)", () => {
  assert.ok(hrefs(P.support).includes("/admin/workflows/runs"));
  assert.ok(hrefs(P.support).includes("/admin/workflows/triggers"));
  assert.ok(!hrefs(P.support).includes("/admin/workflows"));
});

test("member: no workflow screen at all", () => {
  for (const h of ["/admin/workflows", "/admin/workflows/runs", "/admin/workflows/triggers"]) {
    assert.ok(!hrefs(P.member).includes(h), h);
  }
});

test("DEAL-01: New deal for an internal role with manage_deals only; fails closed", () => {
  assert.equal(perms1.canCreateDeal(P.orgAdmin), true);
  assert.equal(perms1.canCreateDeal(P.orgAdminBefore), false);
  assert.equal(perms1.canCreateDeal(P.support), false);
  assert.equal(perms1.canCreateDeal({ ...P.member, permissions: [...MEMBER, "manage_deals"] }), false);
  assert.equal(perms1.canCreateDeal(null), false);
  assert.equal(perms1.canCreateDeal({ ...P.orgAdmin, permissions: undefined }), false);
});

test("REP-01: households for an internal role with manage_members only; fails closed", () => {
  assert.equal(perms1.canManageHouseholds(P.orgAdmin), true);
  assert.equal(perms1.canManageHouseholds(P.support), false);
  assert.equal(perms1.canManageHouseholds({ ...P.member, permissions: [...MEMBER, "manage_members"] }), false);
  assert.equal(perms1.canManageHouseholds(undefined), false);
});

test("WF-06: Delete only when the API row says can_delete === true; the confirmation names the workflow", () => {
  assert.equal(wf.canDeleteWorkflow({ can_delete: true }), true);
  assert.equal(wf.canDeleteWorkflow({ can_delete: "true" }), false);
  assert.equal(wf.canDeleteWorkflow({ name: "system:human_task:approve_models", can_delete: false }), false);
  assert.equal(wf.canDeleteWorkflow(null), false);
  assert.match(wf.deleteConfirmText({ name: "Quarterly review" }), /“Quarterly review”/);
});

test("WF-03: owners are laid out above the root with an edge each, carrying the owner's stake", () => {
  const tree = { id: "root", children: [], owners: [
    { id: "trust", ownership_pct: "60.0000", effective_pct: "60.000000", owners: [] },
    { id: "james", ownership_pct: "40.0000", effective_pct: "40.000000", owners: [] }] };
  const lay = owners.layoutOwners(tree);
  assert.deepEqual(lay.nodes.map((n) => n.node.id).sort(), ["james", "trust"]);
  assert.ok(lay.nodes.every((n) => n.y < 0));
  assert.deepEqual(lay.edges.map((e) => [e.parent.id, e.child.id, e.pct]).sort(),
    [["james", "root", "40.0000"], ["trust", "root", "60.0000"]]);
  assert.deepEqual(owners.ownerLookthrough(tree), { trust: "60.000000", james: "40.000000" });
  assert.deepEqual(owners.layoutOwners({ id: "x", children: [] }), { nodes: [], edges: [] });
});
