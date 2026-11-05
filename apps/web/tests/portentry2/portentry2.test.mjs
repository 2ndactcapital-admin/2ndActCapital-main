// PORTENTRY2 — the date input, the picker, the permission-true rendering, the ownership views, the household helpers
// and the Households nav row. Envelopes below are written by hand in the API's shapes (services/record_permissions,
// routers/entity_graph GET /ownership, services/household_admin.household_detail), never imported from them.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const P = await import("../../lib/portentry2.mjs");
const menu = await import("../../lib/menuVisibility.mjs");
const { default: DateInput } = await import("../../components/ui/DateInput.jsx");
const { default: EntityPicker } = await import("../../components/EntityPicker.jsx");
const { default: OwnershipTree } = await import("../../components/crm/OwnershipTree.jsx");

const text = (html) => html.replace(/&#x27;/g, "'").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

// ── Dates ────────────────────────────────────────────────────────────────────────────────────────────────────
test("dates: typed / pasted ISO and US forms round-trip to ISO", () => {
  assert.deepEqual(P.parseDateInput("2019-03-07"), { iso: "2019-03-07", error: null });
  assert.deepEqual(P.parseDateInput("  2019/3/7 "), { iso: "2019-03-07", error: null });
  assert.deepEqual(P.parseDateInput("03/07/2019"), { iso: "2019-03-07", error: null });
  assert.deepEqual(P.parseDateInput("3-7-1951"), { iso: "1951-03-07", error: null });
  assert.deepEqual(P.parseDateInput("2008-02-09T00:00:00Z"), { iso: "2008-02-09", error: null });
  assert.deepEqual(P.parseDateInput(""), { iso: "", error: null });
});

test("dates: the mangled years Muse saw (275760, 511951, 199999999) and out-of-range years are refused", () => {
  for (const bad of ["275760-01-01", "511951-05-01", "199999999-01-01", "1799-12-31", "2201-01-01",
    "01/01/275760", "02/30/2019", "13/01/2019", "01/01/19", "yesterday"]) {
    const r = P.parseDateInput(bad);
    assert.equal(r.iso, "", bad);
    assert.ok(typeof r.error === "string" && r.error.length > 0, bad);
  }
  assert.match(P.parseDateInput("275760-01-01").error, /outside 1800–2200/);
  assert.equal(P.parseDateInput("1800-01-01").iso, "1800-01-01");
  assert.equal(P.parseDateInput("2200-12-31").iso, "2200-12-31");
});

test("DateInput renders a text box (no year spin-button) and submits the ISO value under its name", () => {
  const html = renderToStaticMarkup(h(DateInput, { name: "date_of_death", defaultValue: "2008-02-09" }));
  assert.ok(!html.includes('type="date"'), html);
  assert.match(html, /type="text"/);
  assert.match(html, /type="hidden" name="date_of_death" value="2008-02-09"/);
});

// ── Seasonal addresses ───────────────────────────────────────────────────────────────────────────────────────
test("seasonal: the address whose months hold today is current; the other says 'Seasonal, <months>'", () => {
  const florida = { is_seasonal: true, season_from_month: 11, season_to_month: 4 };
  const maine = { is_seasonal: true, season_from_month: 5, season_to_month: 10 };
  assert.deepEqual(P.seasonalStatus(florida, "2026-01-15"), { current: true, months: "Nov–Apr", label: "Current · Nov–Apr" });
  assert.equal(P.seasonalStatus(maine, "2026-01-15").label, "Seasonal, May–Oct");
  assert.equal(P.seasonalStatus(maine, "2026-07-04").current, true);
  assert.equal(P.seasonalStatus(florida, "2026-07-04").current, false);
  assert.equal(P.seasonalStatus({ is_seasonal: false }, "2026-07-04"), null);
});

// ── Percentages and the ownership views ──────────────────────────────────────────────────────────────────────
test("F-03: a holding's look-through fraction prints as 60.0%, not 0.6%", () => {
  const map = P.lookthroughPercentMap([{ entity_id: "a", effective_pct: "0.600000" },
    { entity_id: "b", effective_pct: "0.800000" }]);
  assert.equal(P.formatPct(map.a), "60.0%");
  assert.equal(P.formatPct(map.b), "80.0%");
  // the OLD path printed the fraction itself
  assert.equal(`${Number("0.600000").toFixed(1)}%`, "0.6%");
});

const G = {
  nodes: [{ id: "lp", display_name: "Olivo Holdings LP", entity_type: "lp" },
    { id: "aof", display_name: "AOF Investments LLC", entity_type: "llc" },
    { id: "sub", display_name: "AOF Sub LLC", entity_type: "llc" },
    { id: "stag", display_name: "Stag's Hollow LLC", entity_type: "llc" },
    { id: "t1", display_name: "Revocable Trust", entity_type: "trust" }],
  edges: [{ parent_id: "aof", child_id: "stag", ownership_pct: 50 },
    { parent_id: "aof", child_id: "sub", ownership_pct: 100 },
    { parent_id: "sub", child_id: "stag", ownership_pct: 50 },
    { parent_id: "t1", child_id: "aof", ownership_pct: 100 }],
};

test("F-02: Owns shows each direct holding once; look-through is the toggle and sums, never duplicates", () => {
  const direct = P.ownsRows(G, "aof");
  assert.deepEqual(direct.map((r) => r.id).sort(), ["stag", "sub"]);
  const lt = P.ownsRows(G, "aof", { lookThrough: true });
  assert.deepEqual(lt.map((r) => r.id).sort(), ["stag", "sub"]);
  assert.equal(lt.find((r) => r.id === "stag").effective_pct, 100);
  const htmlDirect = renderToStaticMarkup(h(OwnershipTree, { graph: G, rootId: "aof", direction: "down", title: "Owns", maxDepth: 1 }));
  assert.equal((text(htmlDirect).match(/Stag's Hollow/g) || []).length, 1, htmlDirect);
  const htmlAll = renderToStaticMarkup(h(OwnershipTree, { graph: G, rootId: "aof", direction: "down", title: "Owns" }));
  assert.equal((text(htmlAll).match(/Stag's Hollow/g) || []).length, 2); // the OLD unbounded tree: twice
  assert.deepEqual(P.ownedByRows(G, "aof").map((r) => r.id), ["t1"]);
});

// ── Permission-true rendering ────────────────────────────────────────────────────────────────────────────────
const SUPPORT = { permissions: { can_read: true, can_write: true, is_super_admin: false,
  can: { record: false, tax_ids: false, ownership: false, relationships: false, details: false, addresses: true,
    contact: true, notes: true, accounts: false } },
vocabularies: { editable: ["addresses", "contact", "notes"], inline_editable: [] } };
const ADMIN = { permissions: { can_read: true, can_write: true, is_super_admin: false,
  can: { record: true, tax_ids: true, ownership: true, relationships: true, details: true, addresses: true,
    contact: true, notes: true, accounts: true } },
vocabularies: { editable: ["accounts", "addresses", "ownership", "record", "tax_ids"], inline_editable: [] } };

test("PERS-01: support staff's envelope renders no ownership / record / tax-id control; the org admin's does", () => {
  for (const k of ["ownership", "record", "tax_ids", "relationships", "details", "accounts"]) {
    assert.equal(P.canEdit(SUPPORT, k), false, k);
    assert.equal(P.canEdit(ADMIN, k), true, k);
  }
  assert.equal(P.canEdit(SUPPORT, "addresses"), true);
  assert.equal(P.canEditOwnership({ permissions: { can_write: false } }), false);
  assert.equal(P.canEditOwnership({ permissions: { can_write: true } }), true);
});

test("a lost or malformed envelope fails closed", () => {
  for (const env of [null, undefined, {}, { permissions: { can_write: "true" } }, { permissions: { can_write: true } }]) {
    assert.equal(P.canEdit(env, "ownership"), false);
  }
  assert.equal(P.canEditOwnership(null), false);
  assert.equal(P.canEditOwnership({ permissions: { can_write: "true" } }), false);
});

// ── Picker ───────────────────────────────────────────────────────────────────────────────────────────────────
test("picker: every word is sent; include_inactive only when asked; family / grantor default ON", () => {
  const p = P.pickerSearchParams({ q: " Marguerite Olivo ", includeInactive: true });
  assert.equal(p.get("q"), "Marguerite Olivo");
  assert.equal(p.get("include_inactive"), "true");
  assert.equal(P.pickerSearchParams({ q: "x" }).get("include_inactive"), null);
  assert.equal(P.includeInactiveDefault({ key: "former_spouse", group: "family" }), true);
  assert.equal(P.includeInactiveDefault({ key: "grantor", group: "trust" }), true);
  assert.equal(P.includeInactiveDefault({ key: "employer", group: "business" }), false);
  assert.equal(P.includeInactiveDefault(undefined), false);
});

test("picker: a chosen record shows its NAME, never its id", () => {
  const id = "7e27f95d-a72e-4240-b510-ffa6f26a60ce";
  assert.equal(P.pickerDisplay({ id, display_name: "Theresa Zucchini Olivo" }), "Theresa Zucchini Olivo");
  assert.equal(P.pickerDisplay({ id, display_name: id }), "");
  const html = renderToStaticMarkup(h(EntityPicker, { value: { id, display_name: id }, onChange: () => {} }));
  assert.ok(!html.includes(`value="${id}"`), html);
  const named = renderToStaticMarkup(h(EntityPicker, { value: { id, display_name: "Theresa Zucchini Olivo" },
    onChange: () => {}, includeInactive: true }));
  assert.match(named, /value="Theresa Zucchini Olivo"/);
  assert.match(text(named), /Include inactive \/ deceased/);
  assert.match(named, /type="checkbox" checked=""/);
});

// ── Households ───────────────────────────────────────────────────────────────────────────────────────────────
const DETAIL = {
  id: "hh", is_demo: true,
  members: [{ id: "p1", display_name: "Dominic Olivo", entity_type: "individual" },
    { id: "t1", display_name: "Dominic J. Olivo Revocable Trust", entity_type: "trust" }],
  owner_candidates: [{ id: "llc1", display_name: "MTO Capital LLC", entity_type: "llc", owned_by: "t1",
    owned_by_name: "Dominic J. Olivo Revocable Trust" }],
  accounts: [{ id: "a1", account_number_masked: "****1234", custodian_code: "SCHWAB", registration_type: "trust",
    registration_type_label: "Trust", tax_status: "taxable", tax_status_label: "Taxable", is_billable: false,
    is_discretionary: true, is_held_away: true, owner_name: "Dominic J. Olivo Revocable Trust" }],
};

test("H-02: the Owner list holds every member (person AND legal entity) and the entities a member owns", () => {
  const opts = P.ownerOptions(DETAIL);
  assert.deepEqual(opts.map((o) => [o.value, o.addAsMember]), [["p1", false], ["t1", false], ["llc1", true]]);
  assert.equal(opts.find((o) => o.value === "t1").entity_type, "trust");
  assert.deepEqual(P.ownerOptions(null), []);
});

test("H-01 / H-03 / H-05: account line, DVP reason, billable default, live counts", () => {
  assert.equal(P.accountLine(DETAIL.accounts[0]),
    "****1234 · SCHWAB · Trust · Taxable · not billable · discretionary · held away · owner Dominic J. Olivo Revocable Trust");
  assert.deepEqual(P.dvpMissing("other_custodian", "", " "), ["receiving custodian", "DTC / participant number"]);
  assert.deepEqual(P.dvpMissing("other_custodian", "BNY", "0901"), []);
  assert.deepEqual(P.dvpMissing("integrated", "", ""), []);
  assert.match(P.dvpReason(["DTC / participant number"]), /needs the DTC \/ participant number/);
  assert.equal(P.billableDefault(DETAIL), false);
  assert.equal(P.billableDefault({ is_demo: false }), true);
  const rows = P.withCounts([{ id: "hh", members: 1, accounts: 0 }, { id: "x", members: 2, accounts: 2 }], DETAIL);
  assert.deepEqual(rows, [{ id: "hh", members: 2, accounts: 1 }, { id: "x", members: 2, accounts: 2 }]);
});

// ── Nav ──────────────────────────────────────────────────────────────────────────────────────────────────────
test("G-01: Households is in the Portfolio menu for an internal role holding manage_members, never for support staff", () => {
  const item = menu.MENU_ITEMS.find((i) => i.href === "/portfolio/households");
  assert.ok(item, "nav row");
  const orgAdmin = { status: "active", is_active: true, is_internal: true, is_staff: false, is_super_admin: false,
    permissions: ["manage_members", "view_portfolio"] };
  const support = { status: "active", is_active: true, is_internal: true, is_staff: false, is_super_admin: false,
    permissions: ["edit_crm_contact", "view_portfolio", "view_workflow_runs"] };
  const member = { status: "active", is_active: true, is_internal: false, is_staff: false, is_super_admin: false,
    permissions: ["manage_members", "view_portfolio"] };
  assert.equal(menu.canAccess(orgAdmin, item.gate), true);
  assert.equal(menu.canAccess(support, item.gate), false);
  assert.equal(menu.canAccess(member, item.gate), false);
  assert.equal(menu.canAccess(null, item.gate), false);
});
