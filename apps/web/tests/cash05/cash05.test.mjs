// CASH05 — Household → Treasury, day to day: the REAL components rendered with react-dom/server from reads shaped
// exactly like routers/cash.py returns them (services/cash_overview / cash_holdings / cash_activity / cash_earnings /
// cash_documents). Overview (chart, markers, tooltip, yields + method note, coverage card, actions), holdings (sleeves,
// every rung), activity + the drawer (only server-named links), earnings, documents (Eastern dates), the member's
// read-only view with the library labels, and every screen FAILS CLOSED on a lost read.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/cashTreasury.mjs");
const Overview = (await import("../../components/treasury/TreasuryOverview.jsx")).default;
const Holdings = (await import("../../components/treasury/TreasuryHoldings.jsx")).default;
const A = await import("../../components/treasury/TreasuryActivity.jsx");
const Activity = A.default;
const { EntryDrawer, EarningsDialog } = A;
const Documents = (await import("../../components/treasury/TreasuryDocuments.jsx")).default;
const Daily = (await import("../../components/treasury/TreasuryDaily.jsx")).default;
const TreasuryPanel = (await import("../../components/treasury/TreasuryPanel.jsx")).default;

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const html = (el) => renderToStaticMarkup(el);
const buttons = (el) => [...html(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const PERMS = { can_read: true, can_write: false, is_super_admin: false, read_permission: "view_portfolio",
  write_permission: null };
const LABELS = {
  "cash.coverage_estimate": { key: "cash.coverage_estimate", text: "Coverage estimate (library)" },
  "cash.yield_estimate": { key: "cash.yield_estimate", text: "Yield estimate (library)" },
  "cash.tax_estimate": { key: "cash.tax_estimate", text: "Tax estimate (library)" },
};
const BASE = (over = {}) => ({ household: { id: "h-1" }, scope: { entity_id: null, label: "The household" },
  member_view: false, permissions: PERMS, labels: LABELS, ...over });
const COV = { household_id: "h-1", as_of: "2026-11-09", liquid: "500000.00", has_cash: true,
  monthly_outflow: "20000.00", months: "25.0", min_months: "6", covered_through: "2028-11-09",
  covered_beyond_horizon: true, next_gap: null, obligations: 3, flags: [], liquid_basis: "core (srv)" };
const OVERVIEW = (over = {}) => BASE({
  screen: "overview", today: "2026-11-09", range: { key: "90d", start: "2026-08-11", end: "2026-11-09" },
  points: [{ date: "2026-08-11", value: "400000.00", pending_in: "0.00", pending_out: "0.00" },
    { date: "2026-10-01", value: "450000.00", pending_in: "50000.00", pending_out: "0.00" },
    { date: "2026-11-09", value: "500000.00", pending_in: "0.00", pending_out: "1250.00" }],
  markers: [{ id: "t-1", date: "2026-10-01", change: "50000.00", direction: "in", description: "Buy — Fund (srv)",
    pending: true }],
  balance: "500000.00", change_in_range: "100000.00", series_basis: "core as of each day (srv)",
  points_basis: "3 core evaluations (srv)",
  yields: { estimated: true, gross_yield_pct: "4.1000", fee_pct: "0.2500", fee_deducted: true,
    fee_text: "advisory fee (srv)", net_yield_pct: "3.8500", covered: "480000.00", total: "500000.00",
    without_yield: [{ name: "Cash", value: "20000.00", why: "not estimated — needs the custodian's cash rate" }],
    method: "Blended net yield = Σ (value × yield) … (srv)",
    after_tax: { estimated: true, yield_pct: "2.9100", label: "after-tax estimate (srv)" } },
  coverage: COV, coverage_entities: [],
  actions: [{ key: "documents", label: "Documents (srv)", href: "/portfolio/households/h-1?tab=treasury#treasury-documents" },
    { key: "edit_policy", label: "Edit cash policy (srv)", href: "/portfolio/households/h-1?tab=treasury#treasury-policy" }],
  vocabularies: { ranges: [{ key: "30d", label: "30 days (srv)" }, { key: "90d", label: "90 days (srv)" },
    { key: "all", label: "All (srv)" }], flags: [], editable: [], inline_editable: [],
  entities: [{ entity_id: "e-1", display_name: "Trust (srv)" }, { entity_id: "e-2", display_name: "LLC (srv)" }] }, ...over });
const HOLDINGS = (over = {}) => BASE({
  screen: "holdings", total: "500000.00", sleeve_basis: "policy boundaries (srv)", months_basis: "value ÷ outflow (srv)",
  groups: [
    { key: "cash", label: "Cash (srv)", value: "20000.00", months_covered: "1.0", holdings: [{ key: "asset:c",
      name: "Cash", kind: "cash", description: "Cash held at the custodian.", group: "cash", shares: "20000", units: "USD",
      value: "20000.00", yield: { estimated: false, text: "not estimated — needs the custodian's cash rate" },
      liquidity: "Available now", months_covered: "1.0", prospectus: { href: null, text: "prospectus: not recorded" } }] },
    { key: "near_term", label: "Near-term (srv)", value: "180000.00", months_covered: "9.0", holdings: [{ key: "s-mmf",
      name: "Treasury MMF", ticker: "TMMF", kind: "mmf", description: "A Treasury money-market fund (srv)",
      group: "near_term", shares: "180000", units: "shares", value: "180000.00",
      yield: { estimated: true, yield_pct: "4.0000", basis: "7-day net yield (SEC Form N-MFP)", as_of: "2026-10-31" },
      liquidity: "Same day if instructed by 14:00 ET", months_covered: "9.0",
      prospectus: { href: null, text: "prospectus: not recorded in the security master" } }] },
    { key: "reserve", label: "Reserve (srv)", value: "0.00", months_covered: "0.0", holdings: [],
      empty_text: "Nothing is held in the Reserve sleeve." },
    { key: "long_term", label: "Long-term (srv)", value: "300000.00", months_covered: "15.0", holdings: [],
      rungs: [{ held: true, maturity: "2027-12-01", type: "bill", par: "200000.00", market_value: "194000.00",
        par_vs_market: "-6000.00", ytm: { estimated: true, yield_pct: "3.0928", basis: "YTM from own valuation (srv)" },
        serves: "January estimated tax" },
      { held: false, maturity: "2028-06-30", type: "note", par: "50000.00", market_value: null, par_vs_market: null,
        ytm: { estimated: false, text: "planned — not held" }, serves: "a filler rung (monthly)" }], rungs_note: null }],
  vocabularies: { groups: [], kinds: [], editable: [], inline_editable: [], entities: [] }, ...over });
const ACTIVITY = (over = {}) => BASE({
  screen: "activity", entries: [
    { id: "x-1", date: "2026-11-06", type: "transfer", description: "Buy — Fund (srv)", change: "50000.00", pending: true,
      status_text: "Pending — settles 2026-11-10; T+0 (srv)", owner_name: "Trust", account: "****4001" },
    { id: "x-2", date: "2026-11-02", type: "dividend", description: "Dividend — Fund (srv)", change: "812.40",
      pending: false, status_text: "Settled 2026-11-02", owner_name: "Trust", account: "****4001" },
    { id: "x-3", date: "2026-10-31", type: "fee", description: "Fee — Fund (srv)", change: "-1250.00", pending: false,
      status_text: "no settlement date recorded — treated as settled on the trade date", owner_name: "Trust",
      account: "****4001" }],
  empty_text: null,
  vocabularies: { types: [{ key: "transfer", label: "Transfer (srv)" }, { key: "dividend", label: "Dividend (srv)" },
    { key: "fee", label: "Fee (srv)" }], editable: [], inline_editable: [], entities: [] }, ...over });
const DRAWER = (over = {}) => BASE({
  entry: { id: "x-1", date: "2026-11-06", type: "transfer", description: "Buy — Fund (srv)", change: "50000.00" },
  settlement: { trade_date: "2026-11-06", settle_date: "2026-11-10", pending: true, text: "Pending — settles 2026-11-10" },
  source: { table: "portfolio.transactions", id: "x-1", code_label: "Buy" }, account: { account: "****4001",
    custodian: "altruist" }, security: { name: "Treasury MMF" }, owner_name: "Trust", documents: [],
  links: [{ kind: "transaction", label: "Transaction record (srv)", api: "/api/v1/portfolio/transactions/x-1",
    href: "/portfolio/transactions?transaction_id=x-1" },
  { kind: "instruction", label: "Execution instruction (srv)", api: "/api/v1/execution/instructions/i-1",
    href: "/portfolio/instructions/i-1" },
  { kind: "proposal", label: "Dead link (no api)", href: "/portfolio/proposals/p-1" },
  { kind: "document", label: "Confirmation.pdf", api: "/api/v1/documents/d-1/download", href: "/api/documents/d-1/download" }],
  vocabularies: { types: [{ key: "transfer", label: "Transfer (srv)" }], editable: [], inline_editable: [] }, ...over });
const EARNINGS = (over = {}) => BASE({
  period: { key: "mtd", start: "2026-11-01", end: "2026-11-09" },
  dividends: { total: "812.40", funds: [{ holding: "Treasury MMF", amount: "812.40", entries: ["x-2"] }] },
  ladder_interest: { total: "1500.00", coupons: "500.00", realised_discount: "1000.00", method: "coupons + discount (srv)" },
  cash_interest: { total: "12.60", entries: [] },
  fee: { amount: "0.00", charged: false, text: "fee on cash: not charged (the fee schedule excludes cash)", lines: [] },
  total: "2325.00", formula: "dividends + ladder interest + cash interest − advisory fee on cash = total",
  reconciliation: { ledger_income: "2325.00", reconciles: true, ledger_fee_debits: "1250.00", text: "cash basis (srv)" },
  missing: [], accreting: [{ holding: "Bill 2027", amount: "312.55", text: "(par − cost) × 9 ÷ 300 days" }],
  vocabularies: { periods: [{ key: "mtd", label: "Month to date (srv)" }, { key: "ytd", label: "Year to date (srv)" }],
    editable: [], inline_editable: [] }, ...over });
const DOCUMENTS = (over = {}) => BASE({
  timezone: "America/New_York", date_basis: "filing time in ET (srv)",
  documents: [{ id: "d-1", name: "October statement.pdf", type: "statement", type_label: "Statement (srv)",
    account: "****4001", date: "2026-10-31", datetime: "2026-10-31 20:00 ET", has_file: true,
    links: [{ kind: "download", label: "Open / download", api: "/api/v1/documents/d-1/download",
      href: "/api/documents/d-1/download" }] },
  { id: "d-2", name: "1099.pdf", type: "tax_1099", type_label: "1099 (srv)", account: "****4001", date: "2026-02-14",
    datetime: "2026-02-14 09:00 ET", has_file: false, links: [], file_text: "no stored file yet" }],
  vocabularies: { types: [], editable: [], inline_editable: [] }, ...over });

test("overview: step chart, transfer markers, tooltip (value, pending in / out), range chips from the server", () => {
  const g = L.chartGeometry(OVERVIEW().points, OVERVIEW().markers, { width: 640, height: 200 });
  assert.equal(g.dots.length, 3);
  assert.match(g.path, /^M[\d.]+,[\d.]+ H[\d.]+ V[\d.]+ H[\d.]+ V[\d.]+$/, "a STEP line between core evaluations");
  assert.equal(g.markers.length, 1);
  const out = render(h(Overview, { data: OVERVIEW(), selected: 1 }));
  for (const s of ["Liquid balance $500,000.00", "2026-10-01", "Balance $450,000.00", "Pending in $50,000.00",
    "Pending out $0.00", "Transfers: 2026-10-01 $50,000.00 (pending)", "30 days (srv)", "90 days (srv)", "All (srv)"]) {
    assert.ok(out.includes(s), `missing ${s}`);
  }
  assert.ok(html(h(Overview, { data: OVERVIEW() })).includes('data-marker="in"'));
  assert.deepEqual(buttons(h(Overview, { data: OVERVIEW() })), ["30 days (srv)", "90 days (srv)", "All (srv)"],
    "the only controls are the server's range chips — nothing writes");
});

test("overview: blended net / after-tax yield with the method note, coverage card, the server's actions only", () => {
  const out = render(h(Overview, { data: OVERVIEW() }));
  for (const s of ["Net yield", "3.8500%", "4.1000% before the fee", "0.2500% advisory fee deducted", "After tax",
    "2.9100%", "Blended net yield = Σ (value × yield) … (srv)", "Not in the blend: Cash", "Months of coverage", "25.0",
    "Documents (srv)", "Edit cash policy (srv)", "Yield estimate (library)", "Tax estimate (library)",
    "Coverage estimate (library)"]) {
    assert.ok(out.includes(s), `missing ${s}`);
  }
  assert.ok(html(h(Overview, { data: OVERVIEW() })).includes('href="/portfolio/households/h-1?tab=treasury#treasury-policy"'));
  const noYield = render(h(Overview, { data: OVERVIEW({ yields: { estimated: false, text: "not estimated — no holding in this scope has a yield", without_yield: [] } }) }));
  assert.ok(noYield.includes("not estimated — no holding in this scope has a yield"));
  assert.deepEqual(L.actionLinks(OVERVIEW({ actions: undefined })), [], "no server actions → no links (fails closed)");
});

test("holdings: per sleeve with yield basis, cut-off wording, months; the ladder expands to EVERY rung", () => {
  const out = render(h(Holdings, { data: HOLDINGS(), expanded: true }));
  for (const s of ["Cash (srv)", "Near-term (srv)", "Reserve (srv)", "Long-term (srv)", "Treasury MMF (TMMF)",
    "4.0000%", "7-day net yield (SEC Form N-MFP) · as of 2026-10-31", "Same day if instructed by 14:00 ET",
    "180000 shares", "Nothing is held in the Reserve sleeve.", "2027-12-01", "$200,000.00", "$194,000.00",
    "-$6,000.00", "3.0928%", "YTM from own valuation (srv)", "January estimated tax", "2028-06-30 (planned — not held)",
    "prospectus: not recorded in the security master", "not estimated — needs the custodian's cash rate"]) {
    assert.ok(out.includes(s), `missing ${s}`);
  }
  const closed = render(h(Holdings, { data: HOLDINGS() }));
  assert.ok(closed.includes("Show every rung (2)") && !closed.includes("planned — not held)"));
  assert.equal(L.holdingGroups(HOLDINGS())[3].rungs.length, 2);
});

test("activity: ledger with types, pending marker + settlement guidance; the drawer renders ONLY server-named links", () => {
  const out = render(h(Activity, { data: ACTIVITY() }));
  for (const s of ["Buy — Fund (srv)", "Transfer (srv)", "$50,000.00", "Pending", "Pending — settles 2026-11-10; T+0 (srv)",
    "Dividend (srv)", "$812.40", "-$1,250.00", "treated as settled on the trade date", "****4001"]) {
    assert.ok(out.includes(s), `missing ${s}`);
  }
  assert.ok(html(h(Activity, { data: ACTIVITY() })).includes('data-pending="yes"'));
  const links = L.drawerLinks(DRAWER());
  assert.deepEqual(links.map((l) => l.kind), ["transaction", "instruction", "document"],
    "a link without the API path that opens it is dropped (never a dead link)");
  const d = html(h(EntryDrawer, { drawer: DRAWER() }));
  assert.ok(d.includes('href="/portfolio/transactions?transaction_id=x-1"') && d.includes('href="/portfolio/instructions/i-1"'));
  assert.ok(!d.includes("Dead link"));
  const dt = render(h(EntryDrawer, { drawer: DRAWER() }));
  for (const s of ["Pending — settles 2026-11-10", "****4001 · altruist", "Treasury MMF", "Buy · record x-1"]) {
    assert.ok(dt.includes(s), `missing ${s}`);
  }
  const empty = render(h(Activity, { data: ACTIVITY({ entries: [], empty_text: "No activity for these cash holdings yet." }) }));
  assert.ok(empty.includes("No activity for these cash holdings yet."));
  assert.equal(render(h(EntryDrawer, { drawer: { entry: {} } })), "", "a drawer without the read envelope renders nothing");
});

test("earnings: dividends + ladder interest + cash interest − fee = total; 'fee on cash: not charged'; reconciled", () => {
  const out = render(h(EarningsDialog, { data: EARNINGS() }));
  for (const s of ["Earnings 2026-11-01 – 2026-11-09", "Dividends", "$812.40", "Treasury MMF", "Ladder interest",
    "coupons $500.00 + discount realised $1,000.00", "$1,500.00", "Cash interest", "$12.60", "Advisory fee on cash",
    "fee on cash: not charged (the fee schedule excludes cash)", "not charged", "Total", "$2,325.00",
    "reconciled to the activity ledger", "Fee debits in the ledger: $1,250.00", "$312.55 accreting, not yet realised",
    "Month to date (srv)"]) {
    assert.ok(out.includes(s), `missing ${s}`);
  }
  assert.ok(html(h(EarningsDialog, { data: EARNINGS() })).includes('data-fee="not_charged"'));
  const charged = render(h(EarningsDialog, { data: EARNINGS({ fee: { amount: "41.10", charged: true, text: "accrued (srv)", lines: [] }, total: "2283.90" }) }));
  assert.ok(charged.includes("−$41.10") && charged.includes("$2,283.90"));
});

test("documents: the cash accounts' documents with Eastern-time dates; open / download only with a stored file", () => {
  const out = render(h(Documents, { data: DOCUMENTS() }));
  for (const s of ["Documents", "Eastern time (America/New_York)", "2026-10-31", "October statement.pdf",
    "Statement (srv)", "1099 (srv)", "Open / download", "no stored file yet"]) {
    assert.ok(out.includes(s), `missing ${s}`);
  }
  const m = html(h(Documents, { data: DOCUMENTS() }));
  assert.ok(m.includes('href="/api/documents/d-1/download"') && !m.includes("/api/documents/d-2/download"));
  assert.ok(m.includes('title="2026-10-31 20:00 ET"'));
  const empty = render(h(Documents, { data: DOCUMENTS({ documents: [], empty_text: "No statements … yet (srv)" }) }));
  assert.ok(empty.includes("No statements … yet (srv)"));
});

test("member: read-only with the library labels; no control writes; every screen fails closed on a lost read", () => {
  const mem = { member_view: true };
  const ov = render(h(Overview, { data: OVERVIEW({ ...mem, actions: [{ key: "documents", label: "Documents (srv)", href: "/treasury#treasury-documents" }] }) }));
  for (const s of ["Coverage estimate (library)", "Yield estimate (library)", "Tax estimate (library)", "Documents (srv)"]) {
    assert.ok(ov.includes(s), `member overview missing ${s}`);
  }
  assert.ok(!ov.includes("Edit cash policy") && !ov.includes("Proposed trades"));
  assert.ok(!ov.includes("sweep"), "cash.sweep_rate is reserved for cash07 — never shown");
  const hd = render(h(Holdings, { data: HOLDINGS(mem), expanded: true }));
  assert.ok(hd.includes("Yield estimate (library)"));
  for (const el of [h(Overview, { data: OVERVIEW(mem) }), h(Holdings, { data: HOLDINGS(mem) }),
    h(Activity, { data: ACTIVITY(mem) }), h(Documents, { data: DOCUMENTS(mem) }), h(EarningsDialog, { data: EARNINGS(mem) })]) {
    const m = html(el);
    assert.ok(!/<form|method="post"|<input/i.test(m), "no form, no input");
    for (const b of buttons(el)) assert.ok(!/save|submit|approve|adopt|propose|transfer|move/i.test(b), `write-like control ${b}`);
  }
  for (const [Comp, d] of [[Overview, OVERVIEW], [Holdings, HOLDINGS], [Activity, ACTIVITY], [Documents, DOCUMENTS]]) {
    assert.equal(html(h(Comp, { data: d({ permissions: undefined }) })), "", "a read without its permission envelope renders nothing");
    assert.equal(html(h(Comp, { data: null })), "");
  }
  assert.equal(L.earningsView(EARNINGS({ permissions: { can_read: false } })), null);
});

test("Treasury tab: the day-to-day screens render first from their reads; cash01's card is the fallback", () => {
  const all = render(h(Daily, { env: { household: { id: "h-1" } }, initial: { overview: OVERVIEW(), holdings: HOLDINGS(),
    activity: ACTIVITY(), documents: DOCUMENTS(), earnings: EARNINGS(), drawer: DRAWER() } }));
  for (const s of ["Liquid balance $500,000.00", "Holdings", "Activity", "Earnings 2026-11-01", "Settlement",
    "October statement.pdf", "Trust (srv)"]) {
    assert.ok(all.includes(s), `missing ${s}`);
  }
  const fb = render(h(Daily, { env: { household: { id: "h-1" } }, fallback: h("p", null, "cash01 card (fallback)") }));
  assert.ok(fb.includes("cash01 card (fallback)"));
  const panel = html(h(TreasuryPanel, { initialEnvelope: { household: { id: "h-1", name: "F" }, coverage: COV,
    entities: [], obligations: [], calendar: [], permissions: { can_read: true, can_write: false }, member_view: true,
    labels: LABELS, vocabularies: { flags: [] } } }));
  assert.ok(panel.includes("Cash coverage") && panel.includes('id="treasury-policy"'), "the existing tab keeps cash01's card and gains the anchors");
  assert.equal(L.readQuery({ entity_id: "e-1", range: null, type: "" }), "?entity_id=e-1");
});
