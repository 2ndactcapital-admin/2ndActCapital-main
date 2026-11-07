// REP13 — the long/short panel and the long/short part of a proposal: the REAL components rendered with
// react-dom/server from envelopes shaped exactly like the API returns them. Every write control (save settings,
// save draft, record / remove / approve / reject the margin agreement, acknowledge, route to the custodian, route
// manually) must FAIL CLOSED: none without the server's own flag being exactly true AND the action listed. The
// tracking error is shown only as the server states it ("needs prices" otherwise); SELL SHORT is printed plainly.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/longShort.mjs");
const { AccountLongShortView, LongShortProposalView } = await import("../../components/replication/LongShortPanel.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
// the write controls actually rendered (button texts) — a status label such as "Approved" is not a control
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const VOCAB = {
  extensions: [{ key: "130_30", label: "130/30 — $130 long and $30 short per $100 of capital" },
    { key: "150_50", label: "150/50" }, { key: "custom", label: "Custom pair" }],
  sides: [{ key: "sell_short", label: "SELL SHORT" }, { key: "buy_to_cover", label: "BUY TO COVER" }],
  editable: ["extension", "tracking_error_limit"], inline_editable: [],
};
const VIEW_ONLY = { ...VOCAB, editable: [] };
const ELIG = { eligible: true, pieces: [{ key: "custodian_margin", ok: true, label: "Custodian declares margin", detail: "zz: margin is 'yes'" }], missing: [] };
const PANEL = {
  eligibility: ELIG, margin_agreement: { id: "m1", status: "approved", status_label: "Approved", agreement_date: "2026-10-01", document_id: "d1", maker_id: "u1" },
  settings: { extension: "130_30", tracking_error_limit: "0.05", max_short_per_name: "0.05", borrow_cost_ceiling_bps: "500", version_no: 1 },
  short_book: [{ security_id: "s1", label: "REP13 Short Co", signed_quantity: "-375", unrealized: "-120.00" }],
};
const WRITER = { can_read: true, can_write: true, can_approve: true, is_super_admin: false };

test("the panel shows every control only with can_write AND the listed action AND an editable vocabulary", () => {
  const env = { ...PANEL, permissions: WRITER, vocabularies: VOCAB, actions: ["save_settings", "save_draft", "remove_margin_agreement"] };
  const out = render(h(AccountLongShortView, { envelope: env }));
  assert.ok(out.includes("Save settings") && out.includes("Save draft") && out.includes("Remove"), out);
  assert.ok(out.includes("-375") && out.includes("short-term"), "the short book shows a negative quantity, short-term");
});

test("a view-only envelope (empty editable, no actions) renders NO write control", () => {
  const env = { ...PANEL, permissions: { can_read: true, can_write: false, can_approve: false }, vocabularies: VIEW_ONLY, actions: [] };
  const out = buttons(h(AccountLongShortView, { envelope: env }));
  assert.deepEqual(out, [], `no control: ${out}`);
});

test("a lost envelope fails closed: no flags, no actions → no control; eligibility never assumed", () => {
  const el = h(AccountLongShortView, { envelope: { ...PANEL, eligibility: undefined } });
  assert.deepEqual(buttons(el), [], "no control");
  assert.ok(render(el).includes("Not eligible"), render(el));
  assert.deepEqual(L.panelControls(null), { saveSettings: false, saveDraft: false, recordMargin: false, removeMargin: false, approveMargin: false, rejectMargin: false });
  assert.deepEqual(L.panelControls({ permissions: { can_write: "true" }, actions: ["save_settings"], vocabularies: VOCAB }).saveSettings, false);
});

test("an action listed without the flag shows nothing; a flag without the action shows nothing", () => {
  assert.equal(L.panelControls({ permissions: { can_write: false }, actions: ["save_settings"], vocabularies: VOCAB }).saveSettings, false);
  assert.equal(L.panelControls({ permissions: { can_write: true }, actions: [], vocabularies: VOCAB }).saveSettings, false);
  assert.equal(L.panelControls({ permissions: { can_approve: true }, actions: ["approve_margin_agreement"] }).approveMargin, true);
});

const DETAILS = {
  disclosures: { structure: "130.00% long and 30.00% short", risks: [{ key: "pretax_underperformance", text: "Pre-tax underperformance: …" }],
    borrow_estimate: { text: "Estimated borrow cost $525.00 a year" } },
  margin: { initial: { text: "custodian to confirm" }, maintenance: { text: "custodian to confirm" } },
  member_summary: "For account ****1301: …", tracking_error: { estimated: false, text: "needs prices — 4 of 4 names have no price series of 61 closes" },
  borrow: { lines: [{ line: 0, quantity: "375", label: "REP13 Short Co", rate_bps: "50", est_borrow_fee_annual: "75.00" }] },
  cost_comparison: [{ key: "borrow_cost", label: "Borrow cost (long/short only) — estimated", amount: "525.00" }],
  blocking_labels: [], constructive_sale: [], acknowledgement_items: [{ key: "pretax_underperformance", label: "Pre-tax" }, { key: "member_summary", label: "The member summary" }],
};

test("the proposal view prints SELL SHORT, the borrow line, the comparison line and 'needs prices' as stated", () => {
  const env = { details: DETAILS, permissions: { can_write: true, is_maker: true }, vocabularies: VOCAB, actions: ["acknowledge"] };
  const out = render(h(LongShortProposalView, { envelope: env }));
  assert.ok(out.includes("SELL SHORT 375 REP13 Short Co"), out);
  assert.ok(out.includes("Borrow cost (long/short only) — estimated") && out.includes("$525.00"), out);
  assert.ok(out.includes("needs prices — 4 of 4"), out);
  assert.ok(out.includes("custodian to confirm"), out);
  assert.ok(out.includes("Acknowledge"), "the maker with the action sees the acknowledgement");
});

test("acknowledge only for the maker with can_write and the action; routing only with its actions", () => {
  const notMaker = { details: DETAILS, permissions: { can_write: true, is_maker: false }, vocabularies: VOCAB, actions: ["acknowledge"] };
  assert.ok(!render(h(LongShortProposalView, { envelope: notMaker })).includes("Acknowledge"));
  const none = { details: DETAILS, permissions: {}, vocabularies: VOCAB, actions: ["acknowledge", "execute_manual"] };
  const out = render(h(LongShortProposalView, { envelope: none }));
  assert.ok(!out.includes("Acknowledge") && !out.includes("Route manually"), out);
  const route = { details: DETAILS, permissions: { can_write: true }, vocabularies: VOCAB, actions: ["execute_manual"] };
  const out2 = render(h(LongShortProposalView, { envelope: route }));
  assert.ok(out2.includes("Route manually") && !out2.includes("Route to the custodian"), out2);
});

test("the acknowledgement is ready only when EVERY disclosure item is ticked", () => {
  const env = { details: DETAILS };
  assert.equal(L.ackReady(env, ["pretax_underperformance"]), false);
  assert.equal(L.ackReady(env, ["pretax_underperformance", "member_summary"]), true);
  assert.equal(L.ackReady({}, []), false);
});

test("tracking error and side text come only from the server", () => {
  assert.equal(L.teText({}), "—");
  assert.equal(L.teText({ tracking_error: { text: "3.20% a year (estimate)" } }), "3.20% a year (estimate)");
  assert.equal(L.sideText({ vocabularies: VOCAB }, "buy_to_cover"), "BUY TO COVER");
  assert.equal(L.sideText({}, "sell_short", "SELL SHORT"), "SELL SHORT");
});
