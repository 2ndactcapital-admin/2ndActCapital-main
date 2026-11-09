// CASH04 — Household → Treasury → Proposed trades: the REAL components rendered with react-dom/server from envelopes
// shaped exactly like routers/cash.py returns them (`proposals` = services/cash_proposals.household_panel). Lines are
// grouped by sleeve / rung with their reasons; the early-sale warning, the shortfall and the after-tax effect show; the
// waterfall's run history lists its periods; approve / route are LINKS into the existing proposal screen; every control
// FAILS CLOSED (a member, a missing block or a missing permission / server action → no control, no panel).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/cashProposals.mjs");
const P = await import("../../components/treasury/ProposedTradesPanel.jsx");
const Panel = P.default;
const { WaterfallHistory } = P;

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const VOC = {
  sleeves: [{ key: "near_term", label: "Near-term (srv)" }, { key: "reserve", label: "Reserve (srv)" },
    { key: "long_term", label: "Long-term (srv)" }],
  steps: [{ key: "withdrawal", label: "Sell over target (srv)" }, { key: "top_up", label: "Top up (srv)" },
    { key: "rung", label: "Ladder rung (srv)" }, { key: "reinvest", label: "Reinvest maturity (srv)" }],
  statuses: [], editable: [], inline_editable: [],
};
const LINES = [
  { line_no: 1, side: "sell", side_label: "Sell", security: "Old bill 2027-03-01", quantity: "30000", notional: null,
    units: "par", price_kind: "limit", limit_price: "99.0000", limit_text: "limit 99.0000 per 100 — the account's own valuation",
    sleeve: "reserve", rung_index: null, maturity: "2027-03-01", step: "withdrawal", obligations: [],
    reason: "Reserve over target by 30000.00 — sell the rung closest to maturity first (2027-03-01)",
    warning: "early sale before maturity — may lose principal", amount: "29700.00", est_after_tax_yield: null },
  { line_no: 2, side: "buy", side_label: "Buy", security: "Fixture Treasury MMF", quantity: null, notional: "100000.00",
    units: "notional", price_kind: "market", limit_price: null, sleeve: "near_term", rung_index: null, maturity: null,
    step: "top_up", obligations: [], reason: "Near-term under target by 100000.00 — buy the chosen Treasury MMF by notional",
    warning: null, amount: "100000.00", est_after_tax_yield: { estimated: true, yield_pct: "2.3680" } },
  { line_no: 3, side: "buy", side_label: "Buy", security: "Treasury note 2028-01-13", quantity: "229700", notional: null,
    units: "par", price_kind: "limit", limit_price: "100.0000", limit_text: "limit at par (100.0000)", sleeve: "long_term",
    rung_index: 1, maturity: "2028-01-13", step: "rung", obligations: ["January estimated tax"],
    reason: "ladder rung 1 (targeted) maturing 2028-01-13", warning: null, amount: "229700.00",
    est_after_tax_yield: { estimated: true, yield_pct: "2.3000" }, instruction_id: null },
];
const PROPOSAL = (over = {}) => ({
  id: "p-1", account_id: "a-1", status: "draft", status_label: "Draft (srv)", trigger: "manual",
  trigger_label: "Rebalance now (srv)", maker_kind: "user", account: { account_number_masked: "****4001" },
  policy_version_no: 1, period: null, shortfall: "170300.00",
  after_tax_effect: { estimated: true, after_tax_yield_pct: "2.3214", annual_after_tax_income: "7656.82" },
  warnings: [{ code: "early_sale", message: "Old bill: early sale before maturity — may lose principal" },
    { code: "cash_shortfall", message: "short by 170300.00" }],
  notes: ["rung 2 (2028-06-30, par 50000.00): not bought — indicative (no CUSIP in the security master); needs an issue"],
  untouched: [{ label: "Equity Fund", why: "not a cash instrument — never sold" }],
  lines: LINES, actions: ["submit"], link: "/portfolio/proposals/p-1", instruction_links: [], ...over,
});
const HISTORY = [{ period: "2026-11", proposal_id: "w-1", status: "draft", status_label: "Draft (srv)",
  created_at: "2026-11-09T12:00:00Z", account: "****4001", maturities: [{ label: "Matured bill", maturity: "2026-11-06",
    par: "120000.00" }], earnings: "10000.00" }];
const BLOCK = (over = {}) => ({ proposals: [PROPOSAL()], waterfall_history: HISTORY,
  routes: [{ key: "manual", label: "Manual (srv)", refusal: null },
    { key: "fix", label: "FIX router — not connected (srv)", refusal: "route fix refused: not connected (srv)" }],
  actions: ["generate"], vocabularies: VOC, ...over });
const PERMS = { can_read: true, can_write: true, is_super_admin: false, can_view_proposals: true,
  can_generate_proposals: true, proposal_permission: "manage_proposals" };
const ENV = (over = {}) => ({ household: { id: "h-1", name: "Fixture" }, member_view: false, permissions: PERMS,
  proposals: BLOCK(), ...over });

test("proposed trades: lines grouped by sleeve / rung with reasons, amounts by par or notional, limits", () => {
  const out = render(h(Panel, { env: ENV() }));
  for (const s of ["Reserve (srv)", "Near-term (srv)", "Long-term (srv) — rung 1", "$30,000 par", "$100,000.00 notional",
    "$229,700 par", "Limit 99.0000 per 100 of par", "At market", "Limit 100.0000 per 100 of par",
    "sell the rung closest to maturity first", "funds January estimated tax", "Sell over target (srv)",
    "Draft (srv)", "policy v1", "Open proposal (approve / route)"]) {
    assert.ok(out.includes(s), `missing ${s}`);
  }
  const groups = L.groupedLines(ENV(), PROPOSAL()).map((g) => g.title);
  assert.deepEqual(groups, ["Reserve (srv)", "Near-term (srv)", "Long-term (srv) — rung 1"]);
  assert.ok(renderToStaticMarkup(h(Panel, { env: ENV() })).includes('href="/portfolio/proposals/p-1"'));
});

test("warnings: early sale, shortfall (never borrowed), after-tax effect, untouched non-cash, needs-an-issue note", () => {
  const html = renderToStaticMarkup(h(Panel, { env: ENV() }));
  const out = text(html);
  assert.ok(html.includes('data-warning="early_sale"'));
  assert.ok(out.includes("Old bill: early sale before maturity — may lose principal"));
  assert.ok(out.includes("Short by $170,300.00") && out.includes("never borrowed"));
  assert.ok(out.includes("2.3214% after tax") && out.includes("$7,656.82 a year"));
  assert.ok(out.includes("Never sold (not cash instruments): Equity Fund"));
  assert.ok(out.includes("needs an issue"));
  assert.equal(L.shortfallText(PROPOSAL({ shortfall: "0.00" })), null);
});

test("waterfall run history lists each period with its maturities and earnings", () => {
  const out = render(h(WaterfallHistory, { env: ENV() }));
  for (const s of ["Maturity waterfall — run history", "2026-11", "****4001", "Matured bill (2026-11-06)", "10000.00"]) {
    assert.ok(out.includes(s), `missing ${s}`);
  }
  const empty = render(h(WaterfallHistory, { env: ENV({ proposals: BLOCK({ waterfall_history: [] }) }) }));
  assert.ok(empty.includes("has not run for this household (it ships switched off)"));
});

test("controls fail closed: generate needs the permission AND the server action; adopt only from the server", () => {
  assert.deepEqual(buttons(h(Panel, { env: ENV() })), ["Propose trades"]);
  assert.deepEqual(buttons(h(Panel, { env: ENV({ permissions: { ...PERMS, can_generate_proposals: false } }) })), []);
  assert.deepEqual(buttons(h(Panel, { env: ENV({ proposals: BLOCK({ actions: [] }) }) })), []);
  const { can_generate_proposals: _drop, ...noFlag } = PERMS;
  assert.deepEqual(buttons(h(Panel, { env: ENV({ permissions: noFlag }) })), []);
  const wf = BLOCK({ proposals: [PROPOSAL({ trigger: "cash_waterfall", maker_kind: "system", actions: ["adopt"] })] });
  assert.ok(buttons(h(Panel, { env: ENV({ proposals: wf }) })).includes("Adopt and submit"));
  const noAdopt = BLOCK({ proposals: [PROPOSAL({ trigger: "cash_waterfall", maker_kind: "system", actions: [] })] });
  assert.ok(!buttons(h(Panel, { env: ENV({ proposals: noAdopt }) })).includes("Adopt and submit"));
});

test("a member view, a lost block or no view permission renders NO panel (member view unchanged)", () => {
  assert.equal(renderToStaticMarkup(h(Panel, { env: ENV({ member_view: true }) })), "");
  assert.equal(renderToStaticMarkup(h(Panel, { env: ENV({ proposals: null }) })), "");
  assert.equal(renderToStaticMarkup(h(Panel, { env: ENV({ permissions: { ...PERMS, can_view_proposals: false } }) })), "");
  assert.equal(renderToStaticMarkup(h(Panel, { env: ENV({ permissions: undefined }) })), "");
  assert.deepEqual(L.proposalRows(ENV({ member_view: true })), []);
  assert.deepEqual(L.waterfallHistory(ENV({ proposals: undefined })), []);
});

test("routes: manual offered; fix shown refused with the server's reason; approve / route are links, not controls", () => {
  const out = render(h(Panel, { env: ENV() }));
  assert.ok(out.includes("Manual (srv)") && out.includes("refused: route fix refused: not connected (srv)"));
  const btns = buttons(h(Panel, { env: ENV() }));
  for (const b of ["Approve", "Route", "Execute", "Submit"]) assert.ok(!btns.includes(b), `unexpected control ${b}`);
});
