// BUGFIX2 — member data isolation, menu side. Fed the REAL /users/me personas
// (apps/api/scripts/navregroup_personas.mjs) into the ONE menu definition
// (lib/menuVisibility.mjs): a member's visible menu has no staff item, a staff
// user's keeps them; the area gates the server layouts apply refuse a member on
// a pasted staff URL (CRM, SPV Manager, admin, tilts) and admit staff. Expected
// lists are written by hand, independent of the module.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

process.env.NAV_TEST_STUBS = "1";
register("../market/jsxLoader.mjs", import.meta.url);
register("../nav/navLoader.mjs", import.meta.url);

const menu = await import("../../lib/menuVisibility.mjs");
const { PERSONAS } = await import("../../../api/scripts/navregroup_personas.mjs");
const { shouldFlipToOwners } = await import("../../components/graph/OwnershipGraph.jsx");

// Hand list: every href a MEMBER must never see in any menu surface.
const STAFF_ONLY_HREFS = ["/crm", "/spvs", "/admin", "/admin/billing-groups", "/admin/custody-import",
  "/admin/users", "/admin/settings", "/admin/hypothetical-policy", "/admin/security-data/sources",
  "/portfolio/replication/tilts", "/portfolio/replication/backtest", "/portfolio/replication",
  "/admin/model-catalog", "/admin/workflows"];

const allHrefs = (me) => [
  ...menu.visibleMenuItems(me).map((i) => i.href),
  ...menu.visibleSections(me).flatMap((s) => s.entries.flatMap((e) => e.tabHrefs)),
  ...menu.visibleAdminGroups(me).flatMap((g) => g.tabs.map((t) => t.href)),
];

test("a member's visible menu contains no staff item (sidebar, tabs, /admin index)", () => {
  const seen = allHrefs(PERSONAS.plain_member);
  assert.ok(seen.length > 0, "the member still has a menu");
  const leaked = STAFF_ONLY_HREFS.filter((h) => seen.includes(h));
  assert.deepEqual(leaked, []);
  assert.deepEqual(menu.visibleSections(PERSONAS.plain_member).map((s) => s.id), ["main"],
    "no admin section at all for a member");
});

test("the member keeps the member screens", () => {
  const seen = allHrefs(PERSONAS.plain_member);
  for (const h of ["/dashboard", "/marketplace", "/portfolio", "/market", "/documents", "/notifications"]) {
    assert.ok(seen.includes(h), h);
  }
});

test("a staff user's menu still carries CRM, SPV Manager and Billing & Custody", () => {
  const seen = allHrefs(PERSONAS.staff_member);
  for (const h of ["/crm", "/spvs", "/admin/billing-groups", "/admin/custody-import"]) assert.ok(seen.includes(h), h);
  const sa = allHrefs(PERSONAS.super_admin);
  for (const h of ["/crm", "/spvs", "/admin/billing-groups", "/admin/model-catalog"]) assert.ok(sa.includes(h), h);
});

test("area gates: a member is refused every staff area; staff / super admin admitted", () => {
  for (const area of ["/crm", "/spvs", "/admin", "/portfolio/replication"]) {
    assert.equal(menu.canOpenArea(PERSONAS.plain_member, area), false, `member ${area}`);
    assert.equal(menu.canOpenArea(PERSONAS.super_admin, area), true, `super ${area}`);
  }
  for (const area of ["/crm", "/spvs", "/admin"]) {
    assert.equal(menu.canOpenArea(PERSONAS.staff_member, area), true, `staff ${area}`);
  }
  // org admin / member manager keep /admin (their own admin items), not the CRM.
  assert.equal(menu.canOpenArea(PERSONAS.org_admin_manage_org_settings, "/admin"), true);
  assert.equal(menu.canOpenArea(PERSONAS.org_admin_manage_members, "/admin"), true);
  assert.equal(menu.canOpenArea(PERSONAS.org_admin_manage_org_settings, "/crm"), false);
});

test("area gates fail CLOSED on a lost or malformed envelope, and on an unknown area", () => {
  for (const me of [null, undefined, {}, { permissions: "x" }, { status: "pending", is_staff: true }]) {
    for (const area of ["/crm", "/spvs", "/admin", "/portfolio/replication"]) {
      assert.equal(menu.canOpenArea(me, area), false, `${JSON.stringify(me)} ${area}`);
    }
  }
  assert.equal(menu.canOpenArea(PERSONAS.super_admin, "/not-an-area"), false);
});

test("a member holding a stray staff flag string in permissions is still not staff", () => {
  const me = { ...PERSONAS.plain_member, permissions: [...PERSONAS.plain_member.permissions, "staff"] };
  assert.equal(menu.canOpenArea(me, "/crm"), false);
  assert.equal(menu.isMemberOnly(me), true);
});

test("WF-03: an entity that owns nothing flips to its owners once, until the user picks", () => {
  const rootOnly = { id: "llc", children: [] };
  assert.equal(shouldFlipToOwners("owns", false, rootOnly), true);
  assert.equal(shouldFlipToOwners("owns", true, rootOnly), false, "the user's own choice is kept");
  assert.equal(shouldFlipToOwners("owned_by", false, rootOnly), false);
  assert.equal(shouldFlipToOwners("owns", false, { id: "x", children: [{ id: "y", children: [] }] }), false);
  assert.equal(shouldFlipToOwners("owns", false, null), false);
});
