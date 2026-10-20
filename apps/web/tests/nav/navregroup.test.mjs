// navregroup — the REAL Sidebar nav, tab bar and group landing route, rendered in
// Node from a fed /users/me payload per persona. The expected entries below are
// written out by hand, independently of lib/menuVisibility.mjs, so a change to
// the definition that moves a persona's menu fails here rather than silently
// passing a test that re-derives its expectation from the same module.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

// Set BEFORE register(): the hooks thread reads its environment when it starts.
process.env.NAV_TEST_STUBS = "1";
register("../market/jsxLoader.mjs", import.meta.url);
register("./navLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { SidebarNav, sidebarModel } = await import("../../components/Sidebar.jsx");
const { TabBar, tabBarModel } = await import("../../components/TabbedSection.jsx");
const { default: GroupLanding } = await import("../../components/GroupLanding.jsx");
const { PERSONAS } = await import("../../../api/scripts/navregroup_personas.mjs");
const menu = await import("../../lib/menuVisibility.mjs");

const MAIN = ["/dashboard", "/crm", "/marketplace", "/portfolio", "/market", "/spvs", "/insurance",
  "/community", "/admin/document-search", "/notifications"];
const EXPECTED = {
  plain_member: { main: MAIN, org_admin: ["/admin/billing-groups"], platform: [] },
  staff_member: { main: MAIN, org_admin: ["/admin/billing-groups", "/admin/vdr-proposals"], platform: [] },
  org_admin_manage_members: { main: MAIN, org_admin: ["/admin", "/admin/users"], platform: [] },
  org_admin_manage_org_settings: {
    main: MAIN, org_admin: ["/admin/profiles", "/admin/settings", "/admin/workflows"],
    platform: ["/admin/model-research"],
  },
  super_admin: {
    main: MAIN,
    org_admin: ["/admin", "/admin/users", "/admin/settings", "/admin/workflows", "/admin/billing-groups",
      "/admin/vdr-proposals"],
    platform: ["/admin/platform", "/admin/restricted-access", "/admin/model-catalog", "/admin/edgar-pipeline"],
  },
  no_roles: {
    main: MAIN,
    org_admin: ["/admin", "/admin/users", "/admin/settings", "/admin/workflows", "/admin/billing-groups",
      "/admin/vdr-proposals"],
    platform: [],
  },
};

const hrefsIn = (html) => [...html.matchAll(/<a [^>]*?href="([^"]+)"/g)].map((m) => m[1]);
function sectionsOf(html) {
  const out = { main: [], org_admin: [], platform: [] };
  for (const m of html.matchAll(/<div data-section="(\w+)"[^>]*>([\s\S]*?)<\/div>(?=<div data-section=|<\/nav>)/g)) {
    out[m[1]] = hrefsIn(m[2]);
  }
  return out;
}

for (const [name, me] of Object.entries(PERSONAS)) {
  test(`sidebar renders the expected entries for ${name}`, () => {
    const html = renderToStaticMarkup(h(SidebarNav, { me, pathname: "/dashboard", expanded: true }));
    assert.deepEqual(sectionsOf(html), EXPECTED[name]);
    for (const [section, header] of [["org_admin", "Admin — Organization"], ["platform", "Admin — Platform"]]) {
      assert.equal(html.includes(header), EXPECTED[name][section].length > 0,
        `${header} shown iff a group in that section is visible`);
    }
  });
}

test("collapsed sidebar shows icons with titles, no section headers", () => {
  const html = renderToStaticMarkup(h(SidebarNav, { me: PERSONAS.super_admin, pathname: "/x", expanded: false }));
  assert.ok(!html.includes("Admin — Organization") && !html.includes("Admin — Platform"));
  assert.ok(html.includes('title="Structured Notes"') && html.includes('title="Portfolio"'));
});

test("a group is active on any of its tabs and on its landing route; nothing else is", () => {
  const me = PERSONAS.super_admin;
  const activeKeys = (path) => sidebarModel(me, path).flatMap((s) => s.entries).filter((e) => e.active)
    .map((e) => e.key);
  assert.deepEqual(activeKeys("/portfolio/transactions"), ["group:portfolio"]);
  assert.deepEqual(activeKeys("/admin/workflows/runs/abc"), ["group:workflows"]);
  assert.deepEqual(activeKeys("/admin/note-extraction/gold"), ["group:structured_notes"]);
  assert.deepEqual(activeKeys("/admin/structured-notes"), ["group:structured_notes"]);
  assert.deepEqual(activeKeys("/admin"), ["/admin"]);
  assert.deepEqual(activeKeys("/admin/users"), ["group:people"]);
  assert.deepEqual(activeKeys("/notifications"), ["/notifications"]);
});

test("Sidebar.jsx keeps no item list of its own and imports from menuVisibility", () => {
  const src = readFileSync(new URL("../../components/Sidebar.jsx", import.meta.url), "utf8");
  assert.match(src, /from "@\/lib\/menuVisibility"/);
  assert.doesNotMatch(src, /href:\s*"/, "no hard-coded href entries");
  assert.doesNotMatch(src, /NAV_ITEMS|_ITEM\s*=|can\("|canAccess\(/, "no item constants, no second gate check");
  for (const item of menu.MENU_ITEMS) {
    assert.ok(!src.includes(`"${item.label}"`), `label ${item.label} is not hard-coded in Sidebar.jsx`);
  }
});

test("tab bar: every visible tab of the group, the current one active; none off-group or single-tab", () => {
  const m = tabBarModel(PERSONAS.plain_member, "/portfolio/positions");
  assert.deepEqual(m.tabs.map((t) => t.label),
    ["Overview", "Positions", "Transactions", "Securities", "Commitments", "Ownership tree", "Reporting"]);
  assert.deepEqual(m.tabs.filter((t) => t.active).map((t) => t.href), ["/portfolio/positions"]);
  const sa = tabBarModel(PERSONAS.super_admin, "/admin/edgar-pipeline");
  assert.deepEqual(sa.tabs.map((t) => t.label),
    ["Download", "Review queue", "Gold answers", "Accuracy results", "Volatility surface"]);
  assert.equal(tabBarModel(PERSONAS.super_admin, "/admin/platform"), null, "single-tab group: no bar");
  assert.equal(tabBarModel(PERSONAS.super_admin, "/dashboard"), null);
  assert.equal(tabBarModel(PERSONAS.org_admin_manage_org_settings, "/admin/model-research"), null,
    "org admin sees only Model Research in AI Models: no bar");
  const html = renderToStaticMarkup(h(TabBar, { model: m }));
  assert.equal(hrefsIn(html).length, 7);
  assert.match(html, /<a [^>]*aria-current="page"[^>]*>Positions</);
});

async function landing(groupId, me, session = { user: { sub: "s" } }) {
  globalThis.__NAV_SESSION = session;
  globalThis.__NAV_ME = me;
  try {
    const el = await GroupLanding({ groupId });
    return { kind: "render", el };
  } catch (err) {
    const digest = String(err?.digest || "");
    if (digest.startsWith("NEXT_REDIRECT")) return { kind: "redirect", to: digest.split(";")[2] };
    throw err;
  }
}
const textOf = (node) => (node == null || typeof node === "boolean" ? "" : typeof node !== "object" ? String(node)
  : Array.isArray(node) ? node.map(textOf).join("") : textOf(node.props?.children));

test("group landing redirects to the first visible tab (server side)", async () => {
  assert.deepEqual(await landing("structured_notes", PERSONAS.super_admin),
    { kind: "redirect", to: "/admin/edgar-pipeline" });
  assert.deepEqual(await landing("people", PERSONAS.org_admin_manage_org_settings),
    { kind: "redirect", to: "/admin/profiles" });
  assert.deepEqual(await landing("people", PERSONAS.org_admin_manage_members),
    { kind: "redirect", to: "/admin/users" });
  assert.deepEqual(await landing("documents", PERSONAS.plain_member),
    { kind: "redirect", to: "/admin/document-search" });
});

test("group landing with no visible tab renders the refusal, not a redirect", async () => {
  for (const [g, who] of [["structured_notes", "plain_member"], ["people", "plain_member"],
    ["controls", "org_admin_manage_org_settings"], ["deal_intake", "org_admin_manage_members"]]) {
    const r = await landing(g, PERSONAS[who]);
    assert.equal(r.kind, "render", `${g} for ${who}`);
    assert.match(textOf(r.el), /You do not have access to this area\./);
  }
  const lost = await landing("structured_notes", new Error("users/me failed"));
  assert.equal(lost.kind, "render", "a lost envelope fails closed for a Super Admin-only group");
});

test("group landing without a session goes to login with returnTo the landing", async () => {
  assert.deepEqual(await landing("people", PERSONAS.super_admin, null),
    { kind: "redirect", to: "/auth/login?returnTo=/admin/people" });
});
