// NAV3 — one Entities record page. Pure rules + node renders (no browser, no network). Expectations are written by
// hand from the sprint (sprint_prompts/nav3.structural.md) and the gates in docs/AUTHZ1_ROUTES.md, never derived from
// the module under test; the personas are nav1's (apps/api/scripts/nav1_personas.mjs).
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

process.env.NAV_TEST_STUBS = "1";
register("../market/jsxLoader.mjs", import.meta.url);
register("../nav/navLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const T = await import("../../lib/recordTabs.mjs");
const V = await import("../../components/record/RecordViews.jsx");
const CrossBookSwitch = (await import("../../components/nav/CrossBookSwitch.jsx")).default;
const Chip = await import("../../components/nav/EntityChip.jsx");
const Palette = await import("../../components/nav/CommandPalette.jsx");
const { PERSONAS } = await import("../../../api/scripts/nav1_personas.mjs");
const SRC = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");

const keys = (opts) => T.tabsFor(opts).map((t) => t.key);
const ALL10 = ["overview", "holdings", "transactions", "allocation", "cash", "models", "ownership", "details", "documents", "activity"];
const VOCAB = {
  entity_statuses: [
    { value: "prospect", label: "Prospect", selectable: true }, { value: "client", label: "Client", selectable: true },
    { value: "contact", label: "Contact", selectable: true }, { value: "active", label: "Active", selectable: true },
    { value: "partner", label: "Partner", selectable: true }, { value: "former_client", label: "Former client", selectable: true },
    { value: "deceased", label: "Deceased", selectable: false },
  ],
  status_filters: [{ value: "", label: "All" }, { value: "client", label: "Client" }, { value: "partner", label: "Partner" },
    { value: "former_client", label: "Former client" }],
};

// ── tab × type × role ─────────────────────────────────────────────────────────────────────────────────────────
test("the ten tabs, in the sprint's order", () => {
  assert.deepEqual(T.TAB_KEYS, ALL10);
  assert.deepEqual(T.RECORD_TABS.map((t) => t.label), ["Overview", "Holdings", "Transactions", "Allocation & X-ray",
    "Cash & Treasury", "Models & rebalancing", "Ownership & relationships", "Details", "Documents", "Tasks & activity"]);
});

test("Super Admin sees every tab for an individual with a household; a household entity has no Models tab", () => {
  assert.deepEqual(keys({ me: PERSONAS.super_admin_bare, entityType: "individual", hasHousehold: true }), ALL10);
  assert.deepEqual(keys({ me: PERSONAS.super_admin_bare, entityType: "household", hasHousehold: true }),
    ALL10.filter((k) => k !== "models"));
});

test("no primary household (or one the person may not read) → no Cash & Treasury tab", () => {
  assert.ok(!keys({ me: PERSONAS.super_admin_bare, entityType: "trust", hasHousehold: false }).includes("cash"));
});

test("member, next-gen and a lost envelope get no record page at all (GET /full is internal) — unchanged", () => {
  for (const p of ["member", "next_gen", "lost_envelope"]) {
    assert.equal(T.canOpenRecord(PERSONAS[p]), false, p);
    assert.deepEqual(keys({ me: PERSONAS[p], entityType: "individual", hasHousehold: true }), [], p);
  }
});

test("support staff (internal, view_portfolio, no view_proposals): Transactions + Cash yes, Models no", () => {
  const k = keys({ me: PERSONAS.support_staff, entityType: "llc", hasHousehold: true });
  assert.deepEqual(k, ALL10.filter((x) => x !== "models"));
});

test("org admin (live grants: view_proposals, no view_portfolio): Models yes, Transactions + Cash no", () => {
  const k = keys({ me: PERSONAS.org_admin, entityType: "individual", hasHousehold: true });
  assert.ok(k.includes("models"));
  assert.ok(!k.includes("transactions"));
  assert.ok(!k.includes("cash"));
});

test("a tab whose API refused the person is hidden (never an empty tab)", () => {
  const k = keys({ me: PERSONAS.super_admin_bare, entityType: "individual", hasHousehold: true, refused: ["holdings", "models"] });
  assert.ok(!k.includes("holdings") && !k.includes("models"));
  assert.equal(k.length, 8);
});

test("old ?tab= keys open the new tab that contains them", () => {
  const want = { overview: "overview", relationships: "ownership", addresses: "details", employment: "details",
    tax_ids: "details", social: "details", notes: "activity", documents: "documents", linked_documents: "documents",
    ownership: "ownership", holdings: "holdings", liabilities: "holdings", compliance: "details", nonsense: "overview" };
  for (const [k, v] of Object.entries(want)) assert.equal(T.tabForKey(k), v, k);
  const vis = T.tabsFor({ me: PERSONAS.org_admin, entityType: "individual", hasHousehold: false });
  assert.equal(T.activeTab("transactions", vis), "overview");   // hidden for this person → first visible
  assert.equal(T.activeTab("tax_ids", vis), "details");
});

// ── one as-of date ────────────────────────────────────────────────────────────────────────────────────────────
test("as-of: default today; malformed, impossible or future dates fall back to today", () => {
  const today = "2026-11-13";
  assert.equal(T.normalizeAsOf(undefined, today), today);
  assert.equal(T.normalizeAsOf("2026-02-30", today), today);
  assert.equal(T.normalizeAsOf("13/11/2026", today), today);
  assert.equal(T.normalizeAsOf("2027-01-01", today), today);
  assert.equal(T.normalizeAsOf("2025-12-31", today), "2025-12-31");
});

test("as-of: which tabs follow it, and the 'Shows today' note only when the date is not today", () => {
  const follows = ALL10.filter((k) => T.followsAsOf(k));
  assert.deepEqual(follows, ["holdings", "transactions", "allocation", "ownership"]);
  assert.equal(T.showsTodayNote("cash", "2025-12-31", "2026-11-13"), true);
  assert.equal(T.showsTodayNote("holdings", "2025-12-31", "2026-11-13"), false);
  assert.equal(T.showsTodayNote("cash", "2026-11-13", "2026-11-13"), false);
  assert.equal(T.recordHref("X", { tab: "holdings", asOf: "2025-12-31", today: "2026-11-13", entityId: "X" }),
    "/crm/X?tab=holdings&as_of=2025-12-31&entity_id=X");
  assert.equal(T.recordHref("X", { asOf: "2026-11-13", today: "2026-11-13" }), "/crm/X");
});

// ── accessible tabs ───────────────────────────────────────────────────────────────────────────────────────────
test("tab list: role=tablist / tab, aria-selected, roving tabindex, aria-controls; keyboard wraps", () => {
  const tabs = T.tabsFor({ me: PERSONAS.super_admin_bare, entityType: "individual", hasHousehold: true });
  const html = renderToStaticMarkup(h(V.RecordTabList, { tabs, active: "details", onSelect() {} }));
  assert.match(html, /role="tablist"/);
  assert.equal((html.match(/role="tab"/g) || []).length, 10);
  assert.equal((html.match(/aria-selected="true"/g) || []).length, 1);
  assert.match(html, /id="record-tab-details"[^>]*aria-selected="true"[^>]*aria-controls="record-panel-details"[^>]*tabindex="0"/);
  assert.equal((html.match(/tabindex="-1"/g) || []).length, 9);
  assert.equal(T.nextTabIndex(9, "ArrowRight", 10), 0);
  assert.equal(T.nextTabIndex(0, "ArrowLeft", 10), 9);
  assert.equal(T.nextTabIndex(4, "Home", 10), 0);
  assert.equal(T.nextTabIndex(4, "End", 10), 9);
});

test("the page source renders role=tabpanel with aria-labelledby for each visible tab", () => {
  const src = SRC("components/record/RecordPage.jsx");
  assert.match(src, /role="tabpanel" id=\{`record-panel-\$\{t\.key\}`\} aria-labelledby=\{`record-tab-\$\{t\.key\}`\}/);
});

// ── header + status badge ─────────────────────────────────────────────────────────────────────────────────────
test("header: name, type, status badge from the vocabulary, household / owner line, the as-of control", () => {
  const entity = { id: "e1", display_name: "Olivo Family Trust", entity_type: "trust", status: "former_client" };
  const html = renderToStaticMarkup(h(V.RecordHeader, { entity, vocab: VOCAB, household: { id: "h", name: "Olivo" },
    owners: [{ parent_name: "Marguerite Olivo" }], asOf: "2026-11-13", today: "2026-11-13", todayTabs: ["Overview"] }));
  assert.match(html, /Olivo Family Trust/);
  assert.match(html, /data-status="former_client"[^>]*>Former client</);
  assert.match(html, /Household: Olivo · Owned by Marguerite Olivo/);
  assert.match(html, /type="date"[^>]*max="2026-11-13"/);
  assert.doesNotMatch(html, /Shows today/);
  const past = renderToStaticMarkup(h(V.RecordHeader, { entity, vocab: VOCAB, asOf: "2025-12-31", today: "2026-11-13",
    todayTabs: ["Overview", "Cash &amp; Treasury"] }));
  assert.match(past, /Shows today: Overview/);
  assert.match(past, /Back to today/);
});

test("status labels come from the vocabulary; an unknown key shows as stored; no vocabulary → no badge text invented", () => {
  assert.equal(T.statusLabel(VOCAB, "partner"), "Partner");
  assert.equal(T.statusLabel(VOCAB, "active"), "Active");
  assert.equal(T.statusLabel(VOCAB, "weird"), "weird");
  assert.equal(T.statusLabel(null, "client"), "client");
  assert.equal(renderToStaticMarkup(h(V.StatusBadge, { status: null, vocab: VOCAB })), "");
});

// ── Holdings split ────────────────────────────────────────────────────────────────────────────────────────────
const fig = (gross, top = []) => ({ gross, liabilities: "0.00", net: gross, top_holdings: top });
const SPLIT = { rows: { as_of: "2026-11-13", direct: fig("100.00", [{ key: "a", label: "SPY", value: "100.00" }]),
  through: fig("250.50"), total: fig("350.50"),
  owned: [{ entity_id: "c1", display_name: "Holdco LLC", share_pct: "50.00", gross: "250.50" }] },
  vocabularies: { split_parts: [{ key: "direct", label: "Held directly" }, { key: "through", label: "Through ownership" }] } };

test("holdings: direct / through switch with labels from the envelope; through lists owned entities with %", () => {
  const d = renderToStaticMarkup(h(V.HoldingsSplitView, { envelope: SPLIT, part: "direct" }));
  assert.match(d, /data-holdings-split="direct"/);
  assert.match(d, /Held directly/);
  assert.match(d, /Through ownership/);
  assert.match(d, /direct \+ through ownership = total/);
  assert.match(d, /SPY/);
  assert.doesNotMatch(d, /Holdco LLC/);
  const t = renderToStaticMarkup(h(V.HoldingsSplitView, { envelope: SPLIT, part: "through" }));
  assert.match(t, /Holdco LLC/);
  assert.match(t, /50\.00%/);
  assert.equal(T.splitDifferenceCents(SPLIT.rows), 0);
  assert.equal(T.splitDifferenceCents({ direct: { gross: "1.00" }, through: { gross: "1.00" }, total: { gross: "2.01" } }), -1);
  assert.equal(renderToStaticMarkup(h(V.HoldingsSplitView, { envelope: null })), "");
});

// ── UDF custom fields ─────────────────────────────────────────────────────────────────────────────────────────
const LAYOUT = (canWrite) => ({ permissions: { can_write: canWrite }, sections: [{ id: "s1", title: "Planning", items: [
  { id: "i1", definition_id: "d-edit", label: "Risk score", is_read_only: false, data_type: "number" },
  { id: "i2", definition_id: "d-read", label: "Advisor code", is_read_only: true, data_type: "text" },
] }] });
const VALUES = { rows: [{ definition_id: "d-edit", value_numeric: 7 }, { definition_id: "d-read", value_text: "AX-1" }] };

test("UDF: editable only when the server's layout says so AND the envelope grants the write", () => {
  const w = renderToStaticMarkup(h(V.UdfTabView, { tab: { id: "t1", label: "Planning tab" }, layout: LAYOUT(true), values: VALUES }));
  assert.match(w, /data-udf-field="d-edit" data-mode="edit"/);
  assert.match(w, /data-udf-field="d-read" data-mode="read"/);
  assert.match(w, /<input id="udf-d-edit"[^>]*value="7"/);
  assert.doesNotMatch(w, /<input id="udf-d-read"/);
  assert.match(w, /AX-1/);
  const r = renderToStaticMarkup(h(V.UdfTabView, { tab: { id: "t1", label: "Planning tab" }, layout: LAYOUT(false), values: VALUES }));
  assert.doesNotMatch(r, /<input/);
  assert.doesNotMatch(r, /Save/);
  const lost = renderToStaticMarkup(h(V.UdfTabView, { tab: { id: "t1", label: "x" }, layout: { sections: LAYOUT(true).sections }, values: VALUES }));
  assert.doesNotMatch(lost, /<input/);   // a lost envelope fails CLOSED
});

test("UDF: a field the server hid (absent from the layout) never renders, even with a value", () => {
  const html = renderToStaticMarkup(h(V.UdfTabView, { tab: { id: "t1", label: "x" }, layout: LAYOUT(true),
    values: { rows: [...VALUES.rows, { definition_id: "d-hidden", value_text: "SECRET" }] } }));
  assert.doesNotMatch(html, /SECRET|d-hidden/);
});

// ── relationship status filters + Cross Book default ──────────────────────────────────────────────────────────
test("Cross Book: staff default to clients only; All relationships lifts it; a member is unaffected; an owner wins", () => {
  for (const p of ["support_staff", "org_admin", "super_admin_bare"]) {
    assert.equal(T.crossBookStatus(PERSONAS[p], "clients"), "client", p);
    assert.equal(T.crossBookStatus(PERSONAS[p], "all"), null, p);
    assert.equal(T.crossBookStatus(PERSONAS[p], "clients", true), null, p);
  }
  assert.equal(T.crossBookStatus(PERSONAS.member, "clients"), null);
  assert.equal(T.crossBookStatus(undefined, "clients"), undefined);   // still loading → wait
  const s = renderToStaticMarkup(h(CrossBookSwitch, { me: PERSONAS.support_staff, mode: "clients" }));
  assert.match(s, /aria-checked="true"[^>]*>Clients only/);
  assert.match(s, /All relationships/);
  assert.equal(renderToStaticMarkup(h(CrossBookSwitch, { me: PERSONAS.member, mode: "clients" })), "");
  assert.equal(renderToStaticMarkup(h(CrossBookSwitch, { me: PERSONAS.support_staff, mode: "clients", ownerSelected: true })), "");
});

test("status filter in the chip picker and Ctrl K: options from the envelope; none → no control", () => {
  const base = { query: "ol", onQuery() {}, results: [], recent: [], searching: false, activeIndex: -1, onChoose() {},
    onClear() {}, hasSelection: false };
  const with_ = renderToStaticMarkup(h(Chip.EntityPickerView, { ...base, statusOptions: VOCAB.status_filters, status: "partner" }));
  assert.match(with_, /id="nav3-entity-status"/);
  assert.match(with_, /<option value="partner" selected="">Partner<\/option>/);
  const without = renderToStaticMarkup(h(Chip.EntityPickerView, base));
  assert.doesNotMatch(without, /nav3-entity-status/);
  const pal = renderToStaticMarkup(h(Palette.PaletteView, { query: "ol", onQuery() {}, options: [], activeIndex: 0,
    onChoose() {}, statusOptions: VOCAB.status_filters, status: "" }));
  assert.match(pal, /id="nav3-cmdk-status"/);
  assert.match(SRC("components/nav/EntityChip.jsx"), /if \(status\) params\.set\("status", status\)/);
});

test("the grids send owner_status only through the Cross Book rule, and their Next routes allow it", () => {
  for (const g of ["components/portfolio/PositionsGrid.jsx", "components/portfolio/TransactionsGrid.jsx"]) {
    const s = SRC(g);
    assert.match(s, /if \(ownerStatus\) params\.set\("owner_status", ownerStatus\)/, g);
    assert.match(s, /if \(ownerStatus === undefined\) return;/, g);
  }
  for (const r of ["app/api/portfolio/positions/route.js", "app/api/portfolio/transactions/route.js"]) {
    assert.match(SRC(r), /"owner_status",/, r);
  }
});

// ── URLs ──────────────────────────────────────────────────────────────────────────────────────────────────────
test("the record page stays at /crm/[id] (old URLs work); sub-routes kept; the old tab component is gone", () => {
  const page = SRC("app/crm/[id]/page.js");
  assert.match(page, /RecordPage/);
  assert.match(page, /returnTo=\/crm\/\$\{id\}/);
  assert.match(page, /\/api\/v1\/entities\/\$\{id\}\/full/);
  for (const sub of ["hierarchy", "ownership-graph", "targets"]) {
    assert.ok(existsSync(new URL(`../../app/crm/[id]/${sub}/page.js`, import.meta.url)), sub);
  }
  assert.ok(!existsSync(new URL("../../components/crm/EntityDetailTabs.jsx", import.meta.url)));
});
