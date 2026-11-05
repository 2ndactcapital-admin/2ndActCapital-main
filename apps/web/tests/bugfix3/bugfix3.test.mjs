// BUGFIX3 — fixed-point money (the "$9.0E…" class), the list loading state (the "display lag"), private
// investments by commitment rule, the permission-true CRM / household controls, the option-contract picker and the
// manual-asset report, and the Positions page's scope text. Envelopes are written by hand in the API's shapes
// (services/record_permissions, routers/households, services/direct_holdings.vocab, routers/portentry), never
// imported from them.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { toFixedPoint } = await import("../../lib/fixedPoint.mjs");
const { listView } = await import("../../lib/listState.mjs");
const PE = await import("../../lib/portentry.mjs");
const P2 = await import("../../lib/portentry2.mjs");
const CD = await import("../../lib/crmDetail.mjs");
const DS = await import("../../lib/decimalString.js");
const B3 = await import("../../lib/bugfix3.mjs");
const { default: ManualAssetsReport } = await import("../../components/portentry/ManualAssetsReport.jsx");

const src = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");

test("toFixedPoint expands scientific notation exactly, string-only", () => {
  assert.equal(toFixedPoint("9.0E+5"), "900000");
  assert.equal(toFixedPoint("1E+6"), "1000000");
  assert.equal(toFixedPoint("1.25e-3"), "0.00125");
  assert.equal(toFixedPoint("-4.5E+2"), "-450");
  assert.equal(toFixedPoint("900000.00"), "900000.00");
  assert.equal(toFixedPoint("12345678901234567890.12E+2"), "1234567890123456789012");
});

test("a $900,000 cost renders 900,000.00 — never scientific notation (every money formatter)", () => {
  assert.equal(PE.money("900000.00"), "$900,000.00");
  assert.equal(PE.money("9.0E+5"), "$900,000");
  assert.ok(!PE.money("9.0E+5").includes("E"));
  assert.equal(CD.formatMoney("9.0E+5"), "$900,000.00");
  assert.equal(DS.formatMoneyExact("9.0E+5"), "$900,000.00");
  assert.equal(CD.formatMoney("900000.00"), "$900,000.00");
});

test("listView: null is LOADING (never the empty sentence), [] is empty, rows are rows", () => {
  assert.equal(listView(null).state, "loading");
  assert.equal(listView(undefined).state, "loading");
  assert.equal(listView({ permissions: {} }).state, "loading");
  assert.equal(listView({ rows: [] }).state, "empty");
  assert.equal(listView({ rows: [{ id: 1 }] }).state, "rows");
  assert.equal(listView([]).state, "empty");
  assert.equal(listView({ rows: [1] }, { error: "x" }).state, "error");
  // the Holdings tab reads it (the fix is wired, not just written)
  assert.match(src("components/crm/HoldingsTab.jsx"), /listView\(assets\)/);
  assert.match(src("components/crm/HoldingsTab.jsx"), /viewP\.state === "loading"/);
});

const PRIV_ENV = {
  permissions: { can_write: true },
  vocabularies: {
    editable: ["add", "flows", "nav"],
    commitment_rules: [
      { value: "fund", rule: "required" }, { value: "spv", rule: "optional" },
      { value: "hedge_fund", rule: "none" }, { value: "direct_deal", rule: "none" },
      { value: "safe", rule: "none" }, { value: "note", rule: "none" },
    ],
  },
};

test("private investments: commitment only where the kind has one; note travels", () => {
  assert.equal(PE.commitmentRule(PRIV_ENV, "fund"), "required");
  assert.equal(PE.commitmentRule(PRIV_ENV, "spv"), "optional");
  assert.equal(PE.commitmentRule(PRIV_ENV, "safe"), "none");
  assert.equal(PE.commitmentRule(null, "safe"), "required"); // lost envelope asks for MORE, the API decides
  const safe = PE.privatePayload({ kind: "safe", name: "Seed SAFE", amount_invested: "250000", nav: "300000",
    nav_date: "2026-10-01", notes: "MFN, cap 12M", amount: "" }, "none");
  assert.equal(safe.commitment, undefined);
  assert.equal(safe.amount_invested, "250000");
  assert.equal(safe.notes, "MFN, cap 12M");
  const fund = PE.privatePayload({ kind: "fund", name: "Fund IV", amount: "1000000", commitment_date: "2024-01-02" },
    "required");
  assert.deepEqual(fund.commitment, { amount: "1000000", date: "2024-01-02" });
  const spvNo = PE.privatePayload({ kind: "spv", name: "SPV", amount: "", amount_invested: "50000" }, "optional");
  assert.equal(spvNo.commitment, undefined);
  assert.equal(spvNo.amount_invested, "50000");
  const spvYes = PE.privatePayload({ kind: "spv", name: "SPV", amount: "75000", commitment_date: "2025-02-03" },
    "optional");
  assert.equal(spvYes.commitment.amount, "75000");
});

test("CRM record envelope: graph / new_entity / addresses flags drive the controls (no fallback)", () => {
  const support = { permissions: { can_write: true, can: { addresses: true, contact: true, notes: true,
    ownership: false, relationships: false, graph: false, new_entity: false } } };
  const admin = { permissions: { can_write: true, can: { addresses: true, ownership: true, relationships: true,
    graph: true, new_entity: true } } };
  assert.equal(P2.canEdit(support, "addresses"), true);
  assert.equal(P2.canEdit(support, "graph"), false);
  assert.equal(P2.canEdit(support, "relationships"), false);
  assert.equal(P2.canEdit(admin, "graph"), true);
  assert.equal(P2.canEdit(null, "addresses"), false);
  const hier = src("app/crm/[id]/hierarchy/page.js");
  assert.ok(!/isStaff\(/.test(hier), "the hierarchy page no longer decides from the token's role claim");
  assert.match(hier, /canEdit\(permsResult\.value, "graph"\)/);
  const crm = src("app/crm/page.js");
  assert.match(crm, /permissions\?\.can\?\.new_entity === true/);
  assert.match(crm, /\{canNewEntity && \(/);
  assert.match(src("app/crm/new/page.js"), /allowed \? <NewEntityForm \/>/);
  const addr = src("components/crm/tabs/AddressesTab.jsx");
  assert.match(addr, /canEdit && editing !== a\.id/);
  assert.match(src("components/crm/EntityDetailTabs.jsx"), /canEdit=\{canEdit\(perms, "addresses"\)\}/);
});

test("household Recalculate renders only for permissions.can.recalculate === true", () => {
  const hm = src("components/households/HouseholdManager.jsx");
  assert.match(hm, /household\?\.permissions\?\.can\?\.recalculate !== true\) return null/);
  assert.match(hm, /<ProvisionalHoldings household=\{detail\} \/>/);
});

test("Positions / Transactions: the scope sentence comes from the API, never 'across the firm' in the page", () => {
  for (const p of ["app/portfolio/positions/page.js", "app/portfolio/transactions/page.js"]) {
    assert.ok(!/Every (holding|ledger entry) across the firm/.test(src(p)), p);
  }
  assert.match(src("components/portfolio/PositionsGrid.jsx"), /meta\?\.scope\?\.description/);
  assert.match(src("components/portfolio/PositionsGrid.jsx"), /row\?\.is_provisional === true && provisionalLabel/);
  assert.match(src("components/portfolio/TransactionsGrid.jsx"), /meta\?\.scope\?\.description/);
});

test("option-contract picker label + terms; allow-listed proxy path", () => {
  const c = { global_security_id: "g", option_type: "call", strike: "150", expiry: "2027-01-15", multiplier: "100",
    occ_symbol: "AAPL270115C00150000" };
  assert.equal(B3.optionContractLabel(c), "Call 150 · 2027-01-15 · ×100 (AAPL270115C00150000)");
  assert.deepEqual(B3.contractTerms(c), { option_type: "call", strike: "150", expiry: "2027-01-15", multiplier: "100" });
  assert.equal(PE.portentryAllowed("GET", "portfolio/option-contracts"), true);
  assert.equal(PE.portentryAllowed("POST", "portfolio/option-contracts"), false);
  assert.match(src("components/heldaway/HeldAwayPanel.jsx"), /row\.asset_kind === "listed_option"/);
});

test("manual-asset report: counts per asset and a same-name flag", () => {
  const env = { rows: { assets: [
    { id: "a1", name: "Northline Fund (NLF)", position_count: 3, owner_count: 2, same_name_count: 1 },
    { id: "a2", name: "Harbor", position_count: 0, owner_count: 0, same_name_count: 2 },
  ] } };
  const html = renderToStaticMarkup(h(ManualAssetsReport, { envelope: env }));
  assert.match(html, /Northline Fund \(NLF\)/);
  assert.match(html, /3 positions · 2 owners/);
  assert.match(html, /not used by any position/);
  assert.match(html, /2 manual assets share this name/);
  assert.equal(renderToStaticMarkup(h(ManualAssetsReport, { envelope: null })), "");
});

// BUGFIX3 FIX 1 — the household's "not counted" sentence comes only from the API's skipped report
test("skippedSentence: renders the API's skipped report, nothing when none", async () => {
  const { skippedSentence } = await import("../../lib/bugfix3.mjs");
  assert.equal(skippedSentence(undefined), null);
  assert.equal(skippedSentence({ count: 0, unclassified: 0, unvalued: 0 }), null);
  assert.equal(skippedSentence({ count: 1, unclassified: 1, unvalued: 0 }),
    "1 holding is not counted in net worth (1 without an asset class).");
  assert.equal(skippedSentence({ count: 3, unclassified: 2, unvalued: 1 }),
    "3 holdings are not counted in net worth (2 without an asset class, 1 without a value).");
});
