// NAV1 — the new menu, proved against the FROZEN pre-NAV1 menu (apps/api/scripts/nav1_baseline.json, written by
// nav1_snapshot.mjs over the module as it was before NAV1, commit e96dce5). The new structure below is written out by
// hand from Joe's decisions (sprint_prompts/nav1.structural.md), never derived from the module under test.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

process.env.NAV_TEST_STUBS = "1";
register("../market/jsxLoader.mjs", import.meta.url);
register("../nav/navLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { AppRouterContext } = await import("next/dist/shared/lib/app-router-context.shared-runtime.js");
const menu = await import("../../lib/menuVisibility.mjs");
const { SidebarNav } = await import("../../components/Sidebar.jsx");
const { tabBarModel } = await import("../../components/TabbedSection.jsx");
const { PERSONAS } = await import("../../../api/scripts/nav1_personas.mjs");
const { snapshot } = await import("../../../api/scripts/nav1_snapshot.mjs");
const pref = await import("../../lib/assistantPanelPref.mjs");
const Panel = await import("../../components/assistant/AssistantPanel.jsx");
const Toggle = await import("../../components/assistant/AssistantToggle.jsx");
const TopBar = (await import("../../components/TopBar.jsx")).default;

const BASE = JSON.parse(readFileSync(new URL("../../../api/scripts/nav1_baseline.json", import.meta.url), "utf8"));
const NOW = await snapshot(new URL("../../lib/menuVisibility.mjs", import.meta.url).pathname);
const byHref = (items) => Object.fromEntries(items.map((i) => [i.href, i]));
const OLD = byHref(BASE.items);
const NEW = byHref(NOW.items);
const TREASURY = "/treasury";

// ── The new structure, by hand ────────────────────────────────────────────────
const WANT_GROUPS = {
  main: [["portfolio", "Cross Book"], ["model_portfolios", "Model Portfolios"], ["marketplace", "Marketplace"],
    ["notes", "Structured Notes"], ["documents", "Documents"]],
  org_admin: [["settings", "Firm settings"], ["people", "People & Access"], ["model_admin", "Model Portfolio Admin"],
    ["data_pricing", "Data & pricing"], ["billing", "Custody & Billing"], ["workflows", "Automation"],
    ["deal_intake", "Deal Intake"]],
  platform: [["controls", "Tenants & controls"], ["security_data", "Security data"], ["ai_models", "AI"],
    ["structured_notes", "Structured notes pipeline"], ["publishing", "Publishing"]],
};
const WANT_TABS = {
  portfolio: ["/portfolio", "/portfolio/positions", "/portfolio/transactions", "/portfolio/securities",
    "/portfolio/commitments", "/portfolio/households", "/portfolio/allocation", "/portfolio/ownership-tree",
    "/portfolio/approvals"],
  model_portfolios: ["/portfolio/models", "/portfolio/replication/catalog", "/portfolio/replication",
    "/portfolio/replication/tilts", "/portfolio/replication/backtest", "/portfolio/accounts", "/portfolio/tax",
    "/portfolio/replication/section351", "/portfolio/blocks", "/portfolio/instructions"],
  notes: ["/portfolio/replication/notes", "/portfolio/replication/notes/new-issues"],
  marketplace: ["/marketplace", "/marketplace/my-access", "/marketplace/access"], // unchanged
  documents: ["/documents", "/documents/semantic"], // unchanged
  settings:["/admin/settings", "/admin/disclosures", "/admin/hypothetical-policy", "/admin/modeling/ta"],
  people: ["/admin/users", "/admin/staff-visibility", "/admin/profiles", "/admin/permission-sets", "/admin/sod"],
  model_admin: ["/admin/allocation-policy", "/admin/executing-accounts"],
  data_pricing: ["/admin/security-data/sources", "/admin/security-data/quality"],
  billing: ["/admin/custodian-capabilities", "/admin/counterparties", "/admin/custody-import", "/fee-schedules/chat",
    "/admin/billing-groups", "/profitability"],
  workflows: ["/admin/workflows", "/admin/workflows/runs", "/admin/workflows/triggers", "/admin/workflows/templates",
    "/admin/workflows/autonomy"],
  deal_intake: ["/admin/vdr-proposals"],
  controls: ["/admin/platform", "/admin/restricted-access", "/admin/trading-authority", "/admin/integrated-import"],
  security_data: ["/admin/security-data", "/admin/security-data/master"],
  ai_models: ["/admin/model-catalog", "/admin/ai-jobs", "/admin/model-research", "/admin/ai-drift"],
  structured_notes: ["/admin/edgar-pipeline", "/admin/pricing/note-terms-queue", "/admin/note-extraction/gold",
    "/admin/note-extraction/results", "/admin/pricing/surface", "/portfolio/replication/notes/underlyings"],
  publishing: ["/admin/model-provider", "/admin/platform/workflow-templates"],
};

test("sections: Main, Organization admin, Platform · Super Admin", () => {
  assert.deepEqual(menu.SECTIONS.map((s) => [s.id, s.header]),
    [["main", null], ["org_admin", "Organization admin"], ["platform", "Platform · Super Admin"]]);
});

test("groups: exactly Joe's groups, labels, sections and order", () => {
  for (const [section, want] of Object.entries(WANT_GROUPS)) {
    const got = menu.GROUPS.filter((g) => g.section === section).sort((a, b) => a.order - b.order)
      .map((g) => [g.id, g.label]);
    assert.deepEqual(got, want, section);
  }
  assert.equal(menu.GROUPS.length, Object.values(WANT_GROUPS).flat().length, "no other group");
});

test("tabs: each group holds exactly its listed tabs, in order (coming-soon items aside)", () => {
  for (const [gid, hrefs] of Object.entries(WANT_TABS)) {
    const got = menu.MENU_ITEMS.filter((i) => i.group === gid && !i.comingSoon)
      .sort((a, b) => a.tab_order - b.tab_order).map((i) => i.href);
    assert.deepEqual(got, hrefs, gid);
  }
  assert.deepEqual(menu.GROUPS.map((g) => g.id).sort(), Object.keys(WANT_TABS).sort(), "every group is listed");
});

test("main standalone items: Dashboard, Entities, Treasury, SPV Manager, Market Indicators, Notifications", () => {
  const got = menu.MENU_ITEMS.filter((i) => i.section === "main" && !i.group && !i.comingSoon)
    .sort((a, b) => a.order - b.order).map((i) => [i.href, i.label]);
  assert.deepEqual(got, [["/dashboard", "Dashboard"], ["/crm", "Entities"], [TREASURY, "Treasury"],
    ["/spvs", "SPV Manager"], ["/market", "Market Indicators"], ["/notifications", "Notifications"]]);
  // sidebar order of the main section (groups and items interleaved)
  const sa = menu.visibleSectionEntries(PERSONAS.super_admin, "main").map((e) => e.label);
  assert.deepEqual(sa, ["Dashboard", "Entities", "Cross Book", "Model Portfolios", "Treasury", "Marketplace",
    "SPV Manager", "Structured Notes", "Market Indicators", "Documents", "Notifications"]);
});

test("nothing lost: every pre-NAV1 href is still in the menu, exactly once; the only new href is /treasury", () => {
  const counts = {};
  for (const i of menu.MENU_ITEMS) counts[i.href] = (counts[i.href] || 0) + 1;
  assert.deepEqual(Object.entries(counts).filter(([, n]) => n !== 1), [], "every href appears exactly once");
  assert.deepEqual(Object.keys(OLD).filter((h) => !NEW[h]), [], "no pre-NAV1 href lost");
  assert.deepEqual(Object.keys(NEW).filter((h) => !OLD[h]), [TREASURY], "only Treasury is new");
  assert.deepEqual(menu.MENU_ITEMS.filter((i) => i.href === "/portfolio/approvals").map((i) => i.group), ["portfolio"],
    "Approvals: ONE shared inbox, listed once");
});

test("every item keeps its exact gate (old vs new gate per href: empty diff) and its coming-soon flag", () => {
  const diff = Object.keys(OLD).filter((h) => JSON.stringify(OLD[h].gate) !== JSON.stringify(NEW[h].gate)
    || OLD[h].comingSoon !== NEW[h].comingSoon);
  assert.deepEqual(diff, []);
  assert.equal(BASE.items.length, 78, "the baseline is the real pre-NAV1 definition");
});

test("Treasury gate = its API's read gate (any of view_portfolio / manage_portfolio, strict)", () => {
  assert.deepEqual(NEW[TREASURY].gate, { anyOf: ["view_portfolio", "manage_portfolio"], strict: true });
  const api = readFileSync(new URL("../../../api/routers/cash.py", import.meta.url), "utf8");
  assert.match(api, /READ_PERMISSIONS = \(CC\.READ_PERMISSION, CC\.WRITE_PERMISSION\)/);
  const cc = readFileSync(new URL("../../../api/services/cash_coverage.py", import.meta.url), "utf8");
  assert.match(cc, /^READ_PERMISSION = "view_portfolio"$/m);
  assert.match(cc, /^WRITE_PERMISSION = "manage_portfolio"$/m);
  const route = api.slice(api.indexOf('@router.get("/cash/households")'));
  assert.match(route.slice(0, 400), /authz\.require_any_permission\(request, READ_PERMISSIONS\)/);
  const me = (perms) => ({ status: "active", account_role: "member", roles: ["x"], permissions: perms });
  assert.equal(menu.canAccess(me(["view_portfolio"]), NEW[TREASURY].gate), true);
  assert.equal(menu.canAccess(me(["manage_portfolio"]), NEW[TREASURY].gate), true);
  assert.equal(menu.canAccess(me(["view_dashboard"]), NEW[TREASURY].gate), false);
  assert.equal(menu.canAccess({ account_role: "member" }, NEW[TREASURY].gate), false, "no permissions array → hidden");
  assert.equal(menu.canAccess(null, NEW[TREASURY].gate), false, "lost envelope → hidden");
  assert.equal(menu.canAccess({ status: "active", account_role: "super_admin", permissions: [] }, NEW[TREASURY].gate),
    true, "Super Admin first");
});

for (const [name, me] of Object.entries(PERSONAS)) {
  test(`persona ${name}: the same hrefs as before NAV1 (regrouped), plus Treasury iff its API admits them`, () => {
    const before = BASE.personas[name].hrefs;
    const after = NOW.personas[name].hrefs;
    const treasury = menu.canAccess(me, menu.GATE_TREASURY);
    assert.deepEqual(after.filter((h) => h !== TREASURY), before, "same set of hrefs");
    assert.equal(after.includes(TREASURY), treasury);
    assert.deepEqual(NOW.personas[name].admin_area, BASE.personas[name].admin_area, "/admin area decision unchanged");
    assert.deepEqual(NOW.personas[name].areas, BASE.personas[name].areas, "every area gate unchanged");
  });
}

test("Treasury visible exactly to the personas holding view_portfolio / manage_portfolio (or Super Admin)", () => {
  const seen = Object.entries(PERSONAS).filter(([, me]) => NOW.personas[Object.keys(PERSONAS)
    .find((k) => PERSONAS[k] === me)].hrefs.includes(TREASURY)).map(([n]) => n).sort();
  assert.deepEqual(seen, ["member", "plain_member", "staff_member", "super_admin", "super_admin_bare",
    "support_staff"].sort());
});

test("empty groups are hidden: a member sees no admin section and no Model Portfolios / Structured Notes", () => {
  for (const name of ["member", "plain_member", "next_gen", "no_roles", "lost_envelope"]) {
    const ids = menu.visibleSections(PERSONAS[name]).map((s) => s.id);
    assert.deepEqual(ids, ["main"], name);
    const groups = menu.visibleSectionEntries(PERSONAS[name], "main").filter((e) => e.kind === "group")
      .map((e) => e.groupId);
    assert.ok(!groups.includes("model_portfolios") && !groups.includes("notes"), name);
  }
  for (const [name, me] of Object.entries(PERSONAS)) {
    for (const s of menu.visibleSections(me)) {
      for (const e of s.entries.filter((x) => x.kind === "group")) {
        assert.ok(menu.visibleTabs(me, e.groupId).length > 0, `${name}: ${e.groupId} shown with a visible tab`);
      }
    }
    for (const g of menu.GROUPS) {
      const shown = menu.visibleSections(me).some((s) => s.entries.some((e) => e.groupId === g.id));
      assert.equal(shown, menu.visibleTabs(me, g.id).length > 0, `${name}: ${g.id} hidden iff no visible tab`);
    }
  }
});

test("member labels: a member reads My accounts (and My family on the entities entry); staff read Cross Book / Entities", () => {
  const labelOf = (me, key) => menu.visibleSectionEntries(me, "main").find((e) => e.key === key)?.label;
  for (const name of ["member", "plain_member", "next_gen"]) {
    assert.equal(labelOf(PERSONAS[name], "group:portfolio"), "My accounts", name);
    assert.equal(labelOf(PERSONAS[name], "/crm"), undefined, `${name}: CRM gate unchanged, so no entities entry`);
  }
  for (const name of ["support_staff", "org_admin", "super_admin", "staff_member"]) {
    assert.equal(labelOf(PERSONAS[name], "group:portfolio"), "Cross Book", name);
    assert.equal(labelOf(PERSONAS[name], "/crm"), "Entities", name);
  }
  const crm = menu.MENU_ITEMS.find((i) => i.href === "/crm");
  assert.equal(menu.displayLabel(PERSONAS.member, crm), "My family", "the member label rule on the entities entry");
  assert.equal(menu.displayLabel(PERSONAS.org_admin, crm), "Entities");
  assert.equal(menu.displayLabel(null, crm), "My family", "lost envelope counts as a member (fail closed)");
  assert.equal(tabBarModel(PERSONAS.member, "/portfolio/positions").groupLabel, "My accounts");
  assert.equal(tabBarModel(PERSONAS.super_admin, "/portfolio/positions").groupLabel, "Cross Book");
});

// ── Render tests: the REAL SidebarNav per persona ────────────────────────────
const hrefsIn = (html) => [...html.matchAll(/<a [^>]*?href="([^"]+)"/g)].map((m) => m[1]);
const sidebarHtml = (me) => renderToStaticMarkup(h(SidebarNav, { me, pathname: "/dashboard", expanded: true }));
const WANT_SIDEBAR = {
  member: { labels: ["Dashboard", "My accounts", "Treasury", "Marketplace", "Market Indicators", "Documents",
    "Notifications"], headers: [] },
  support_staff: { labels: ["Dashboard", "Entities", "Cross Book", "Treasury", "Marketplace", "Market Indicators",
    "Documents", "Notifications", "Custody & Billing", "Automation"], headers: ["Organization admin"] },
  org_admin: { labels: ["Dashboard", "Entities", "Cross Book", "Model Portfolios", "Marketplace", "Structured Notes",
    "Market Indicators", "Documents", "Notifications", "Firm settings", "People & Access", "Model Portfolio Admin",
    "Data & pricing", "Custody & Billing", "Automation", "AI"], headers: ["Organization admin", "Platform · Super Admin"] },
  super_admin: { labels: ["Dashboard", "Entities", "Cross Book", "Model Portfolios", "Treasury", "Marketplace",
    "SPV Manager", "Structured Notes", "Market Indicators", "Documents", "Notifications", "Admin", "Firm settings",
    "People & Access", "Model Portfolio Admin", "Data & pricing", "Custody & Billing", "Automation", "Deal Intake",
    "Tenants & controls", "Security data", "AI", "Structured notes pipeline", "Publishing"],
  headers: ["Organization admin", "Platform · Super Admin"] },
};
for (const [name, want] of Object.entries(WANT_SIDEBAR)) {
  test(`sidebar render (${name}): exactly the expected entries, labels and section headings`, () => {
    const html = sidebarHtml(PERSONAS[name]);
    const labels = [...html.matchAll(/<span class="truncate">([^<]+)<\/span>/g)].map((m) => m[1].replace(/&amp;/g, "&"));
    assert.deepEqual(labels, want.labels);
    for (const hd of ["Organization admin", "Platform · Super Admin"]) {
      assert.equal(html.includes(hd), want.headers.includes(hd), `${name}: heading ${hd}`);
    }
    const model = menu.visibleSections(PERSONAS[name]).flatMap((s) => s.entries.map((e) => e.href));
    assert.deepEqual(hrefsIn(html), model, "renders exactly the definition's entries");
    assert.match(html, /<nav class="flex-1 space-y-0\.5 overflow-y-auto p-2">/, "the navy sidebar frame's nav is kept");
  });
}

test("Sidebar.jsx keeps no item list or label of its own (NAV1 labels included)", () => {
  const src = readFileSync(new URL("../../components/Sidebar.jsx", import.meta.url), "utf8");
  for (const l of ["Cross Book", "My accounts", "My family", "Entities", "Treasury", "Model Portfolios",
    "Organization admin", "Platform · Super Admin"]) {
    assert.ok(!src.includes(`"${l}"`), l);
  }
  assert.match(src, /className="absolute inset-y-0 left-0 flex flex-col bg-navy/, "navy sidebar frame kept");
});

// ── Assistant panel ──────────────────────────────────────────────────────────
function memStorage(init = {}) {
  const m = new Map(Object.entries(init));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), map: m };
}
const throwing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };

test("assistant: closed by default for everyone (no stored choice, any role, legacy postures)", () => {
  assert.equal(pref.resolveOpen({}), false);
  for (const legacy of [null, "", "collapsible", "weird"]) {
    assert.equal(pref.resolveOpen({ stored: null, accountPosture: legacy }), false, String(legacy));
  }
  const s = pref.createPanelStore({ storage: memStorage() });
  s.start();
  assert.equal(s.get(), false);
  const src = readFileSync(new URL("../../components/assistant/AssistantPanel.jsx", import.meta.url), "utf8");
  assert.ok(!src.includes("ROLE_POSTURE") && !src.includes("user.role"), "no role-based default left");
});

test("assistant: toggles, writes this browser and the account preference, and survives a reload", () => {
  const storage = memStorage();
  const persisted = [];
  const a = pref.createPanelStore({ storage, persist: (p) => persisted.push(p) });
  a.start();
  let notified = 0;
  a.subscribe(() => { notified += 1; });
  assert.equal(a.toggle(), true);
  assert.equal(a.get(), true);
  assert.equal(storage.map.get(pref.STORAGE_KEY), "true");
  assert.deepEqual(persisted, ["expanded"]);
  assert.equal(notified, 1, "subscribers (the top-bar button and the panel) are told");
  // reload: a fresh store over the same storage, before /users/me arrives
  const b = pref.createPanelStore({ storage });
  b.start();
  assert.equal(b.get(), true, "survives a reload");
  b.applyAccount("expanded");
  assert.equal(b.get(), true);
  // close again → remembered closed
  assert.equal(a.toggle(), false);
  assert.deepEqual(persisted, ["expanded", "collapsed"]);
  const c = pref.createPanelStore({ storage });
  c.start();
  assert.equal(c.get(), false);
});

test("assistant: the account choice follows the user to a new browser; a late /users/me never undoes a click", () => {
  const fresh = pref.createPanelStore({ storage: memStorage() });
  fresh.start();
  assert.equal(fresh.get(), false);
  fresh.applyAccount("expanded");
  assert.equal(fresh.get(), true, "opened on another browser → open here");
  const clicked = pref.createPanelStore({ storage: memStorage() });
  clicked.start();
  clicked.toggle();
  clicked.applyAccount("collapsed");
  assert.equal(clicked.get(), true, "the click wins over a stale account value arriving later");
});

test("assistant: storage that throws (private mode) is wrapped — closed default, toggle still works", () => {
  assert.equal(pref.readStored(throwing), null);
  assert.doesNotThrow(() => pref.writeStored(throwing, true));
  const s = pref.createPanelStore({ storage: throwing, persist: () => { throw new Error("offline"); } });
  s.start();
  assert.equal(s.get(), false);
  assert.equal(s.toggle(), true);
});

test("assistant render: the panel renders nothing while closed and the full panel while open", () => {
  assert.equal(renderToStaticMarkup(h(Panel.default, {})), "", "closed by default on first render");
  assert.equal(renderToStaticMarkup(h(Panel.AssistantPanelView, { open: false })), "");
  const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, prefetch() {} };
  const html = renderToStaticMarkup(h(AppRouterContext.Provider, { value: router },
    h(Panel.AssistantPanelView, { open: true })));
  assert.match(html, /data-testid="assistant-panel"/);
  assert.match(html, /AI Assistant/);
});

test("assistant render: ONE toggle button in the top bar, its pressed state follows open", () => {
  const top = renderToStaticMarkup(h(TopBar, { user: { name: "A B" } }));
  assert.equal((top.match(/data-testid="assistant-toggle"/g) || []).length, 1);
  assert.match(top, /aria-pressed="false"/);
  const on = renderToStaticMarkup(h(Toggle.AssistantToggleButton, { open: true, onToggle() {} }));
  assert.match(on, /aria-pressed="true"/);
  assert.match(on, /title="Close assistant"/);
  const shell = readFileSync(new URL("../../components/AppShell.jsx", import.meta.url), "utf8");
  assert.match(shell, /<AssistantPanel user=\{user\} \/>/, "the shell still mounts the panel");
});
