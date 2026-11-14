// NAV2 — the selected entity follows you; Ctrl K finds anything. Pure rules + node renders (no browser, no network).
// Expectations are written by hand from the sprint (sprint_prompts/nav2.structural.md), never derived from the module
// under test; the personas are nav1's (apps/api/scripts/nav1_personas.mjs), the screens list is the menu's groups.
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
const ROUTER = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, prefetch() {} };
const menu = await import("../../lib/menuVisibility.mjs");
const C = await import("../../lib/entityContext.mjs");
const S = await import("../../lib/entityScreens.mjs");
const K = await import("../../lib/commandPalette.mjs");
const { SidebarNav, sidebarModel } = await import("../../components/Sidebar.jsx");
const { tabBarModel, TabBar } = await import("../../components/TabbedSection.jsx");
const { FooterView } = await import("../../components/Footer.jsx");
const Chip = await import("../../components/nav/EntityChip.jsx");
const Screen = await import("../../components/nav/EntityScreen.jsx");
const Palette = await import("../../components/nav/CommandPalette.jsx");
const Panel = await import("../../components/assistant/AssistantPanel.jsx");
const TopBar = (await import("../../components/TopBar.jsx")).default;
const { PERSONAS } = await import("../../../api/scripts/nav1_personas.mjs");

const A = "11111111-2222-4333-8444-555555555555";
const B = "66666666-7777-4888-8999-aaaaaaaaaaaa";
const SRC = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
const memStore = () => {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m };
};
const throwing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); }, removeItem() { throw new Error("denied"); } };
const hrefsIn = (html) => [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1].replace(/&amp;/g, "&"));

// ── 1. ONE context: the URL wins ─────────────────────────────────────────────────
test("context: entity_id is THE parameter (the one /portfolio already read)", () => {
  assert.equal(C.PARAM, "entity_id");
  assert.match(SRC("app/portfolio/page.js"), /const \{ tab = "investments", entity_id \} = await searchParams/);
});

test("context: the URL always wins over the remembered value; an empty / malformed URL value is NOT a fallback", () => {
  assert.deepEqual(C.resolveSelection({ urlValue: A, remembered: B }), { id: A, source: "url" });
  assert.deepEqual(C.resolveSelection({ urlValue: "", remembered: B }), { id: null, source: "none" });
  assert.deepEqual(C.resolveSelection({ urlValue: "nope", remembered: B }), { id: "nope", source: "url" });
  assert.deepEqual(C.resolveSelection({ urlValue: undefined, remembered: B }), { id: B, source: "remembered" });
  assert.deepEqual(C.resolveSelection({ urlValue: undefined, remembered: "junk" }), { id: null, source: "none" });
  assert.equal(C.shouldAdoptRemembered({ id: B, source: "remembered" }), true);
  assert.equal(C.shouldAdoptRemembered({ id: A, source: "url" }), false);
  assert.equal(C.urlEntityParam("?tab=x"), undefined);
  assert.equal(C.urlEntityParam(`?entity_id=${A}`), A);
  assert.equal(C.urlEntityParam("?entity_id="), "");
});

test("context: withEntity carries / replaces / drops, keeps other params and the hash, leaves non-app links alone", () => {
  assert.equal(C.withEntity("/portfolio/positions", A), `/portfolio/positions?entity_id=${A}`);
  assert.equal(C.withEntity(`/treasury?household=h1#treasury-payments`, A), `/treasury?household=h1&entity_id=${A}#treasury-payments`);
  assert.equal(C.withEntity(`/portfolio?tab=gap&entity_id=${B}`, A), `/portfolio?tab=gap&entity_id=${A}`);
  assert.equal(C.withEntity(`/portfolio?tab=gap&entity_id=${B}`, null), "/portfolio?tab=gap");
  assert.equal(C.withEntity(`/x?entity_id=${B}`, null), "/x");
  for (const ext of ["https://example.com/a", "mailto:a@b.c", "//cdn/x", "/auth/logout", "/api/users/me"]) {
    assert.equal(C.withEntity(ext, A), ext, ext);
  }
});

test("context: remembered + Recent are per person, at most 8, newest first, deduplicated; storage that throws is wrapped", () => {
  const st = memStore();
  C.writeRemembered(st, "u1", A);
  assert.equal(C.readRemembered(st, "u1"), A);
  assert.equal(C.readRemembered(st, "u2"), null, "another person on the same browser inherits nothing");
  C.writeRemembered(st, "u1", null);
  assert.equal(C.readRemembered(st, "u1"), null);
  let list = [];
  for (let i = 0; i < 10; i++) list = C.pushRecent(list, { id: `00000000-0000-4000-8000-00000000000${i}`, display_name: `E${i}`, entity_type: "household" });
  assert.equal(list.length, 8);
  assert.equal(list[0].display_name, "E9");
  list = C.pushRecent(list, { id: "00000000-0000-4000-8000-000000000005", display_name: "E5", entity_type: "household" });
  assert.equal(list[0].display_name, "E5");
  assert.equal(list.filter((r) => r.display_name === "E5").length, 1);
  C.writeRecent(st, "u1", list);
  assert.deepEqual(C.readRecent(st, "u1"), list);
  assert.deepEqual(C.readRecent(st, "u2"), []);
  assert.equal(C.dropRecent(list, list[0].id).length, 7);
  assert.equal(C.readRemembered(throwing, "u1"), null);
  assert.deepEqual(C.readRecent(throwing, "u1"), []);
  assert.doesNotThrow(() => { C.writeRemembered(throwing, "u1", A); C.writeRecent(throwing, "u1", list); });
});

// ── 2. The chip: missing and forbidden give ONE answer ───────────────────────────
test("chip: none / loading / entity / Not available; a lookup for another id is Not available", () => {
  assert.equal(C.chipState(null, undefined).kind, "none");
  assert.equal(C.chipState(A, undefined).kind, "loading");
  assert.deepEqual(C.chipState(A, { id: A, display_name: "Olivo Household", entity_type: "household" }),
    { kind: "entity", id: A, name: "Olivo Household", type: "household" });
  assert.equal(C.chipState(A, null).kind, "unavailable");
  assert.equal(C.chipState(A, { id: B, display_name: "X" }).kind, "unavailable");
  assert.equal(C.chipState("not-a-uuid", undefined).kind, "unavailable");
  assert.equal(C.effectiveEntityId(C.chipState(A, null)), null, "a not-available id filters nothing");
  assert.equal(C.effectiveEntityId(C.chipState(A, undefined)), null, "a pending id filters nothing");
  assert.equal(C.effectiveEntityId(C.chipState(A, { id: A, display_name: "N" })), A);
});

test("chip: a forbidden id and a missing id render byte-identically — no name, no id, 'Not available'", () => {
  // the lookup route collapses every non-success (404 forbidden, 404 missing, 422 malformed) to null
  const forbidden = C.reduceLookup(false, { error: "Entity not found" });
  const missing = C.reduceLookup(false, { error: "Entity not found" });
  const malformed = C.reduceLookup(false, { detail: [{ msg: "uuid" }] });
  assert.equal(forbidden, null); assert.equal(missing, null); assert.equal(malformed, null);
  const r = (id, lookup) => renderToStaticMarkup(h(Chip.ChipButton, { state: C.chipState(id, lookup), open: false, onClick() {} }));
  const a = r(A, forbidden);
  const b = r(B, missing);
  const c = r("garbage", undefined);
  assert.equal(a, b);
  assert.equal(a, c);
  assert.match(a, /Not available/);
  assert.ok(!a.includes(A) && !b.includes(B), "the id never appears");
  // the Next lookup route returns only three fields and one 404 for every failure
  const route = SRC("app/api/entities/[id]/route.js");
  assert.match(route, /status: 404/);
  assert.match(route, /if \(!isEntityId\(id\)\) return notAvailable\(\)/);
  assert.match(route, /if \(!entity \|\| entity\.id !== id\) return notAvailable\(\)/);
  assert.deepEqual(Object.keys(C.reduceLookup(true, { entity: { id: A, display_name: "N", entity_type: "trust", tax_id: "secret" } })).sort(),
    ["display_name", "entity_type", "id"]);
});

test("chip: type + name, accessible button; the picker is a labelled combobox + listbox; Recent when no query", () => {
  const html = renderToStaticMarkup(h(Chip.ChipButton, { state: C.chipState(A, { id: A, display_name: "Olivo Household", entity_type: "household" }), open: false, onClick() {} }));
  assert.match(html, /<button type="button"/);
  assert.match(html, /aria-haspopup="dialog"/);
  assert.match(html, /Household/);
  assert.match(html, /Olivo Household/);
  const recent = [{ id: A, display_name: "Olivo Household", entity_type: "household" }];
  const pick = renderToStaticMarkup(h(Chip.EntityPickerView, { query: "", onQuery() {}, results: [], recent, searching: false,
    activeIndex: 0, onChoose() {}, onClear() {}, hasSelection: true, inputRef: null }));
  assert.match(pick, /<label for="nav2-entity-search"/);
  assert.match(pick, /id="nav2-entity-search"/);
  assert.match(pick, /role="combobox"/);
  assert.match(pick, /role="listbox"/);
  assert.match(pick, /role="option" aria-selected="true"/);
  assert.match(pick, /Recent/);
  assert.match(pick, /Clear — see everything you can see/);
  const search = SRC("components/nav/EntityChip.jsx");
  assert.match(search, /\/api\/entities\/search\?/, "the chip searches through the existing entity search route");
  assert.equal(C.RECENT_MAX, 8);
});

test("chip: choosing goes to the same screen when it filters, else the entity's own page (CRM gate unchanged), else /portfolio", () => {
  const sa = PERSONAS.super_admin;
  const member = PERSONAS.member;
  assert.equal(S.entityDestination({ me: sa, pathname: "/portfolio/positions", search: "?x=1", entityId: A }),
    `/portfolio/positions?x=1&entity_id=${A}`);
  assert.equal(S.entityDestination({ me: sa, pathname: "/treasury", search: "", entityId: A }), `/crm/${A}?entity_id=${A}`);
  assert.equal(S.entityDestination({ me: member, pathname: "/treasury", search: "", entityId: A }), `/portfolio?entity_id=${A}`);
  assert.equal(menu.canAccess(member, menu.GATE_CRM), false);
});

// ── 3. Every shell link carries entity_id when set and drops it when cleared ──────
test("shell links: sidebar, tab bar, top-bar menu and footer carry entity_id when set — and none do when cleared", () => {
  for (const name of Object.keys(PERSONAS)) {
    const me = PERSONAS[name];
    const on = hrefsIn(renderToStaticMarkup(h(SidebarNav, { me, pathname: "/portfolio", expanded: true, entityId: A })));
    const off = hrefsIn(renderToStaticMarkup(h(SidebarNav, { me, pathname: "/portfolio", expanded: true, entityId: null })));
    assert.equal(on.length, off.length, name);
    assert.ok(on.every((x) => x.includes(`entity_id=${A}`)), `${name}: every sidebar link carries it`);
    assert.ok(off.every((x) => !x.includes("entity_id")), `${name}: cleared → no link carries it`);
    // the active state still keys on the path
    assert.deepEqual(sidebarModel(me, "/portfolio/positions", A).flatMap((s) => s.entries.map((e) => e.active)),
      sidebarModel(me, "/portfolio/positions").flatMap((s) => s.entries.map((e) => e.active)), name);
  }
  const sa = PERSONAS.super_admin;
  const tOn = tabBarModel(sa, "/portfolio/positions", A);
  const tOff = tabBarModel(sa, "/portfolio/positions");
  assert.ok(tOn.tabs.every((t) => t.href.includes(`entity_id=${A}`)));
  assert.ok(tOff.tabs.every((t) => !t.href.includes("entity_id")));
  assert.deepEqual(tOn.tabs.map((t) => t.active), tOff.tabs.map((t) => t.active));
  assert.ok(hrefsIn(renderToStaticMarkup(h(TabBar, { model: tOn }))).every((x) => x.includes("entity_id=")));
  const foot = { termsUrl: "https://t.example/terms", privacyUrl: null, supportEmail: "s@example.com" };
  const fOn = hrefsIn(renderToStaticMarkup(h(FooterView, { footer: foot, entityId: A })));
  assert.ok(fOn.filter((x) => x.startsWith("/")).every((x) => x.includes(`entity_id=${A}`)));
  assert.ok(fOn.includes("https://t.example/terms") && fOn.includes("mailto:s@example.com"), "external links untouched");
  assert.ok(hrefsIn(renderToStaticMarkup(h(FooterView, { footer: foot }))).every((x) => !x.includes("entity_id")));
  // the top bar renders (no router) with the chip and the search button; with nothing selected no link carries it
  const top = renderToStaticMarkup(h(TopBar, { user: { name: "A B" } }));
  assert.match(top, /data-testid="entity-chip"/);
  assert.ok(hrefsIn(top).every((x) => !x.includes("entity_id")));
  assert.match(SRC("components/TopBar.jsx"), /<Carried href="\/settings">/);
  assert.match(SRC("components/TopBar.jsx"), /<Carried href="\/investment-profile">/);
});

// ── 4. Screens that filter, screens that say they don't ──────────────────────────
test("screens: every Cross Book / Model Portfolios / Structured Notes / Documents tab and Treasury has a decision", () => {
  const groups = ["portfolio", "model_portfolios", "notes", "documents"];
  const want = new Set([...menu.MENU_ITEMS.filter((i) => groups.includes(i.group) && !i.comingSoon).map((i) => i.href), "/treasury"]);
  assert.deepEqual([...want].filter((x) => !S.SCREENS[x]), [], "no screen left without a decision");
  for (const [href, s] of Object.entries(S.SCREENS)) {
    assert.ok(typeof s.mode === "function" || [S.FILTERS, S.NOT_FILTERED].includes(s.mode), href);
    if (s.mode === S.NOT_FILTERED) assert.ok(s.why && s.why.length > 10, `${href} states why`);
  }
  const filtering = Object.keys(S.SCREENS).filter((k) => S.screenMode(k, "") === S.FILTERS).sort();
  assert.deepEqual(filtering, ["/portfolio/allocation", "/portfolio/positions", "/portfolio/transactions"]);
  assert.equal(S.screenMode("/portfolio", "?tab=allocation"), S.FILTERS);
  assert.equal(S.screenMode("/portfolio", "tab=gap"), S.FILTERS);
  assert.equal(S.screenMode("/portfolio", ""), S.NOT_FILTERED, "the Investments tab ignores entity_id — it says so");
  assert.equal(S.screenMode("/admin/users", ""), null);
});

test("screens: banner 'Filtered to <name>' / notice 'isn't filtered' / nothing for a not-available id", () => {
  const vis = C.chipState(A, { id: A, display_name: "Olivo Household", entity_type: "household" });
  const gone = C.chipState(A, null);
  const f = S.screenDecision("/portfolio/positions", "", vis);
  assert.equal(f.banner, "filtered");
  assert.deepEqual(f.entity, { id: A, display_name: "Olivo Household", entity_type: "household" });
  const n = S.screenDecision("/treasury", "", vis);
  assert.equal(n.banner, "not_filtered");
  assert.equal(n.entity, null, "a non-filtering screen is handed no entity");
  for (const href of ["/portfolio/positions", "/treasury", "/portfolio"]) {
    const d = S.screenDecision(href, "", gone);
    assert.deepEqual([d.banner, d.entity], [null, null], `${href}: not available = as if nothing were selected`);
    assert.deepEqual(S.screenDecision(href, "", C.chipState(null)).banner, null);
  }
  const fb = renderToStaticMarkup(h(Screen.EntityBanner, { decision: f, onClear() {} }));
  assert.match(fb, /Filtered to Olivo Household — clear the chip to see everything you can see/);
  assert.match(fb, /role="status"/);
  const nb = renderToStaticMarkup(h(Screen.EntityBanner, { decision: n }));
  assert.match(nb, /This screen isn(&#x27;|')t filtered by entity/);
  assert.equal(renderToStaticMarkup(h(Screen.EntityBanner, { decision: S.screenDecision("/treasury", "", gone) })), "");
});

test("screens: the grids seed AND hold their existing owner filter from the screen's entity; the lens its entity", () => {
  for (const g of ["components/portfolio/PositionsGrid.jsx", "components/portfolio/TransactionsGrid.jsx"]) {
    const s = SRC(g);
    assert.match(s, /const screenEntity = useScreenEntity\(\);/);
    assert.match(s, /owner_entity_id: screenEntity\.id/);
    assert.match(s, /useState\(baseFilters\)/);
    assert.match(s, /if \(screenEntity\) return; \/\/ NAV2/);
    assert.match(s, /setFilters\(baseFilters\);/, `${g}: the grid's own Clear returns to the chip's entity, never past it`);
  }
  const lens = SRC("app/portfolio/allocation/page.js");
  assert.match(lens, /<AppShell user=\{user\}>\s*<AllocationBody \/>/);
  assert.match(lens, /useState\(screenEntity\)/);
  const shell = SRC("components/AppShell.jsx");
  assert.match(shell, /<EntityScreen>\{children\}<\/EntityScreen>/);
  const scr = SRC("components/nav/EntityScreen.jsx");
  assert.match(scr, /decision\.mode === FILTERS && ctx\.state\.kind === "loading"/, "a filtering screen waits for the lookup");
  const port = SRC("app/portfolio/page.js");
  assert.match(port, /allocations = \(await getEntityAllocations\(null\)\) \|\| \[\]/, "a 404 entity renders as no entity");
});

// ── 5. Ctrl K ────────────────────────────────────────────────────────────────────
test("Ctrl K: the Screens list is EXACTLY each persona's visible menu hrefs (never a screen they cannot open)", () => {
  for (const [name, me] of Object.entries(PERSONAS)) {
    assert.deepEqual(K.screensFor(me).map((s) => s.href), menu.visibleMenuItems(me).map((i) => i.href), name);
    const opts = K.paletteOptions({ me, query: "" });
    assert.deepEqual(opts.filter((o) => o.kind === "screen").map((o) => o.href), menu.visibleMenuItems(me).map((i) => i.href), name);
    const q = K.matchScreens(me, "positions").map((s) => s.href);
    assert.ok(q.every((x) => menu.visibleMenuItems(me).some((i) => i.href === x)), name);
  }
  const member = PERSONAS.member;
  assert.ok(!K.screensFor(member).some((s) => s.href.startsWith("/admin")), "a member is offered no admin screen");
  assert.ok(K.matchScreens(PERSONAS.super_admin, "positions").some((s) => s.href === "/portfolio/positions"));
  const carried = K.paletteOptions({ me: member, query: "", carryId: A }).filter((o) => o.kind === "screen");
  assert.ok(carried.every((o) => o.href.includes(`entity_id=${A}`)), "screen links carry the selected entity");
});

test("Ctrl K: Records labelled (entity type / Security); 'Ask about' only for a VISIBLE selected entity", () => {
  const me = PERSONAS.super_admin;
  const vis = C.chipState(A, { id: A, display_name: "Olivo Household", entity_type: "household" });
  const opts = K.paletteOptions({ me, query: "ol", entities: [{ id: A, display_name: "Olivo Household", entity_type: "household" }],
    securities: [{ id: "s1", name: "Apple Inc", ticker: "AAPL" }], selected: vis });
  assert.deepEqual(opts.slice(0, 3).map((o) => [o.section, o.kind, o.label, o.detail]), [
    ["Ask", "ask", "Ask about Olivo Household…", undefined],
    ["Records", "entity", "Olivo Household", "Household"],
    ["Records", "security", "AAPL — Apple Inc", "Security"],
  ]);
  for (const st of [C.chipState(A, null), C.chipState(A, undefined), C.chipState(null)]) {
    assert.equal(K.paletteOptions({ me, query: "", selected: st }).some((o) => o.kind === "ask"), false);
  }
  const pal = SRC("components/nav/CommandPalette.jsx");
  assert.match(pal, /\/api\/market\/securities\/search\?/, "securities through the existing market search route");
});

test("Ctrl K keyboard: Ctrl/Cmd K opens except inside inputs; arrows wrap; dialog + listbox semantics", () => {
  const ev = (o) => ({ key: "k", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, target: { tagName: "BODY" }, ...o });
  assert.equal(K.isOpenShortcut(ev({ ctrlKey: true })), true);
  assert.equal(K.isOpenShortcut(ev({ metaKey: true, key: "K" })), true);
  assert.equal(K.isOpenShortcut(ev({ ctrlKey: true, shiftKey: true })), false);
  assert.equal(K.isOpenShortcut(ev({ key: "k" })), false);
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
    assert.equal(K.isOpenShortcut(ev({ ctrlKey: true, target: { tagName } })), false, `never steals from ${tagName}`);
  }
  assert.equal(K.isOpenShortcut(ev({ ctrlKey: true, target: { tagName: "DIV", isContentEditable: true } })), false);
  assert.equal(Chip.moveIndex("ArrowDown", 2, 3), 0);
  assert.equal(Chip.moveIndex("ArrowUp", 0, 3), 2);
  assert.equal(Chip.moveIndex("ArrowDown", -1, 3), 0);
  assert.equal(Chip.moveIndex("ArrowDown", 0, 0), -1);
  const opts = K.paletteOptions({ me: PERSONAS.member, query: "" });
  const html = renderToStaticMarkup(h(Palette.PaletteView, { query: "", onQuery() {}, options: opts, activeIndex: 0, onChoose() {}, inputRef: null, searching: false }));
  assert.match(html, /<label for="nav2-cmdk-input"/);
  assert.match(html, /role="combobox"/);
  assert.match(html, /aria-activedescendant="nav2-cmdk-opt-0"/);
  assert.match(html, /role="listbox"/);
  const src = SRC("components/nav/CommandPalette.jsx");
  assert.match(src, /role="dialog"\s+aria-modal="true"\s+aria-label="Find or ask"/);
  assert.match(src, /e\.key === "Escape"/);
  assert.match(src, /returnFocus\.current/, "focus is returned to where it was");
  assert.match(src, /e\.key === "Tab"/, "focus is trapped");
  assert.match(src, /aria-keyshortcuts="Control\+K Meta\+K"/);
  assert.equal((src.match(/addEventListener\("keydown"/g) || []).length, 1, "ONE global key handler");
});

// ── 6. The assistant takes the entity as its context ─────────────────────────────
test("assistant: the selected VISIBLE entity is the context_ref in the existing request shape; explicit context wins", () => {
  const vis = C.chipState(A, { id: A, display_name: "Olivo Household", entity_type: "household" });
  assert.deepEqual(K.assistantContextFor(vis), { type: "entity", id: A, entity_type: "household" });
  assert.equal(K.assistantContextFor(C.chipState(A, null)), null);
  assert.equal(K.assistantContextFor(C.chipState(A, undefined)), null);
  assert.deepEqual(Panel.entityContextRef(null, vis), { ref: { type: "entity", id: A, entity_type: "household" }, label: "Household · Olivo Household" });
  assert.deepEqual(Panel.entityContextRef({ type: "deal", id: "d1" }, vis), { ref: { type: "deal", id: "d1" }, label: null });
  const src = SRC("components/assistant/AssistantPanel.jsx");
  assert.match(src, /context_ref: contextRef \|\| null/, "the message body shape is the existing one");
  assert.match(src, /params\.set\("context_type", contextRef\.type\)/);
  const open = renderToStaticMarkup(h(AppRouterContext.Provider, { value: ROUTER },
    h(Panel.AssistantPanelView, { open: true, contextRef: { type: "entity", id: A }, contextLabel: "Household · Olivo Household" })));
  assert.match(open, /data-testid="assistant-context"/);
  assert.match(open, /Household · Olivo Household/);
  assert.match(open, /aria-label="Message the assistant"/);
});
