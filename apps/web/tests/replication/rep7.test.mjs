// REP7 — the REAL held-away and X-ray components rendered with react-dom/server
// from envelopes shaped exactly like routers/held_away.py and routers/xray.py
// return them. Every write / approve / join control must FAIL CLOSED: none
// without the server's own flag being exactly true AND the action in the
// server's list. A held-away account never shows a trade control; a held-away
// fund line never shows "Open join request". Every X-ray figure is an estimate
// (or the server's reason it is not), with its inputs on hover.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const lib = await import("../../lib/heldAway.mjs");
const { default: HeldAwayPanel } = await import("../../components/heldaway/HeldAwayPanel.jsx");
const { default: XrayView } = await import("../../components/xray/XrayView.jsx");
const { default: ProposalDetail } = await import("../../components/proposals/ProposalDetail.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());

const HA_VOCAB = {
  source_kinds: [{ key: "custodian_feed", label: "Direct custodian feed" }, { key: "aggregator", label: "Aggregator" },
    { key: "statement", label: "Statement filed in Chancery" }, { key: "file", label: "File upload (xlsx / xls / csv)" },
    { key: "manual", label: "Entered by an adviser" }],
  source_statuses: [{ key: "active", label: "Active" }, { key: "not_connected", label: "Not connected" }],
  import_statuses: [{ key: "draft", label: "Draft — awaiting a second person's approval" }, { key: "approved", label: "Approved" }],
  reject_reasons: [{ key: "unresolved", label: "Identifier not in the security master" }, { key: "not_number", label: "A number field is not a number" }],
  confirmation_statuses: [{ key: "pending", label: "Awaiting an adviser's confirmation" }],
  file_fields: [{ key: "ticker", label: "Ticker" }, { key: "quantity", label: "Quantity" }],
  providers: [{ key: "plaid", label: "Plaid Investments" }], editable: [],
};
const P_VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false,
  read_permission: "view_held_away", write_permission: "manage_held_away", approve_permission: "approve_held_away" };
const P_ALL = { ...P_VIEW, can_write: true, can_approve: true };
const BAD = [P_VIEW, undefined, {}, { ...P_ALL, can_write: "true", can_approve: "true" }];
const ALL_ACTIONS = ["add_source", "manual_import", "file_import", "link_statement", "request_confirmation", "statement_import"];
const TRADE_WORDS = ["Join", "Join a model", "Generate proposal", "Rebalance", "Harvest", "Create instructions", "Trade", "Buy", "Sell", "Scan now"];
const WRITE_WORDS = ["Approve", "Reject", "Save as draft", "Upload file", "Link statement", "Extract statement", "Confirm the data", "Ask for a confirmation"];

const panelEnv = (permissions, actions, importActions = ["approve", "reject"], heldAway = true) => ({
  panel: {
    account: { id: "a1", account_number_masked: "*****0001", is_held_away: heldAway, registration_type: "ira", tax_status: "tax_deferred" },
    as_of: "2026-09-30", stale_after_days: 30, threshold_from: "organisation", stale: true,
    sources: [{ id: "s1", kind: "file", provider: null, status: "active", last_as_of: "2026-09-30", stale: true, effective_stale_after_days: 30 },
      { id: "s2", kind: "aggregator", provider: "plaid", status: "not_connected", last_as_of: null, stale: false }],
    providers: [{ key: "plaid", label: "Plaid Investments", kind: "aggregator", status: "not_connected" }],
    statement_extraction: { available: false, message: "no extraction models configured for statements ('statement_positions') — enter the positions manually from the document" },
    statements: [], confirmations: [{ id: "c1", status: "pending", task_id: "t1", as_of_confirmed: "2026-09-30" }],
    trade_controls: false, trade_note: "held away — Hollisworks never trades this account; moving the assets comes first",
  },
  imports: [{ id: "i1", kind: "file", as_of: "2026-09-30", status: "draft", rows_read: 3, rows_accepted: 2, rows_rejected: 1,
    file_name: "zz.csv", rejects: [{ row: 4, code: "not_number", detail: "'abc' is not a number" }], extraction: {}, actions: importActions }],
  positions: { positions: [{ id: "p1", label: "Alpha (ZZA)", quantity: "10", market_value: "1000", as_of: "2026-09-30", source: "file",
    lots: [{ id: "l1", acquired_date: "2020-01-02", quantity: "10", cost: "800", term: "long", status: "open" }] }],
  versions: [{ as_of: "2026-09-30", current: true }, { as_of: "2026-06-30", current: false }] },
  transactions: [], permissions, actions, trade_actions: [], vocabularies: HA_VOCAB,
});

test("held-away panel: no import / approve / confirm control without the flags AND the actions", () => {
  for (const p of BAD) {
    const b = buttons(render(h(HeldAwayPanel, { initialEnvelope: panelEnv(p, ALL_ACTIONS) })));
    for (const w of WRITE_WORDS) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(p)}: ${b}`);
  }
  const none = buttons(render(h(HeldAwayPanel, { initialEnvelope: panelEnv(P_ALL, [], []) })));
  for (const w of ["Approve", "Reject", "Save as draft", "Upload file", "Link statement", "Ask for a confirmation"]) {
    assert.ok(!none.includes(w), `no ${w} without the server's action: ${none}`);
  }
  const b = buttons(render(h(HeldAwayPanel, { initialEnvelope: panelEnv(P_ALL, ALL_ACTIONS) })));
  for (const w of ["Approve", "Reject", "Save as draft", "Upload file", "Link statement", "Extract statement", "Confirm the data", "Ask for a confirmation"]) {
    assert.ok(b.includes(w), `${w} with the flags and the action: ${b}`);
  }
});

test("held-away panel: NO trade control at all, whatever the envelope lists", () => {
  for (const p of [P_ALL, { ...P_ALL, is_super_admin: true }]) {
    const env = panelEnv(p, [...ALL_ACTIONS, "join", "generate", "trade", "scan_account", "execute"]);
    const b = buttons(render(h(HeldAwayPanel, { initialEnvelope: env })));
    for (const w of TRADE_WORDS) assert.ok(!b.includes(w), `no trade control ${w}: ${b}`);
    assert.equal(lib.panelControls(env).trade, false);
  }
});

test("held-away panel: sources, stale badge, rejected rows with reasons, positions with source and as-of", () => {
  const t = text(render(h(HeldAwayPanel, { initialEnvelope: panelEnv(P_VIEW, []) })));
  assert.match(t, /File upload \(xlsx \/ xls \/ csv\)/);
  assert.match(t, /Aggregator — Plaid Investments Not connected/);
  assert.match(t, /Stale — data as of 2026-09-30 is older than 30 day\(s\)/);
  assert.match(t, /Row 4: A number field is not a number — 'abc' is not a number/);
  assert.match(t, /A draft changes nothing until a second person approves it/);
  assert.match(t, /Alpha \(ZZA\) .*10 .*\$1,000\.00 .*2026-09-30 .*File upload/);
  assert.match(t, /Earlier versions kept: 2026-06-30/);
  assert.match(t, /no extraction models configured for statements/);
  assert.match(t, /moving the assets comes first/);
});

test("held-away panel renders nothing for a managed account or a missing envelope", () => {
  assert.equal(render(h(HeldAwayPanel, { initialEnvelope: panelEnv(P_ALL, ALL_ACTIONS, ["approve"], false) })), "");
  assert.equal(render(h(HeldAwayPanel, { initialEnvelope: null })), "");
});

const X_VOCAB = {
  xray_status_lines: [{ key: "no_model", label: "no model yet" }],
  viability_verdicts: [{ key: "yes", label: "Replicable" }, { key: "no", label: "Not replicable" }],
  held_away_note: "held away — moving the assets comes first", editable: [], inline_editable: [],
};
const XP_VIEW = { can_read: true, can_write: false, can_join: false, is_super_admin: false, read_permission: "view_replication",
  write_permission: "manage_replication", join_permission: "manage_proposals" };
const XP_ALL = { ...XP_VIEW, can_write: true, can_join: true };
const fund = (over) => ({ account_id: "a1", account: "*****0001", held_away: false, fund_security_id: "f1", label: "Fund ZZF (ZZF)",
  market_value: "100000.00", as_of: "2026-10-01", look_through_as_of: "2026-09-30",
  viability: { overall: "yes" }, model: { id: "m1", name: "Holdings of ZZF" }, replicable: true,
  fee: { annual_difference: "50.00", note: "estimate: 100000.00 × 0.09% − the model's strategy fee 40.00",
    inputs: { market_value: "100000.00", expense_ratio_net_pct: "0.09", as_of: "2026-10-01" } },
  embedded_gain: { amount: "20000.00", inputs: { lots: 1 } },
  transition_tax: { amount: "4760.00", note: "estimate: embedded gain netted by character × the profile's combined rates", inputs: { lt_rate: "0.238" } },
  break_even: { years: "95.20", note: "estimate: transition tax ÷ annual fee difference" },
  join: { available: true, model_id: "m1" }, actions: ["open_join_request"], ...over });
const xEnv = (permissions, funds, actions = ["run"]) => ({
  xray: { household: { id: "h1", name: "ZZ Household" }, as_of: "2026-10-26", total_market_value: "150000.00", unvalued_holdings: 0,
    holdings: [{ account: "*****0001", held_away: false, label: "Fund ZZF (ZZF)", market_value: "100000.00", as_of: "2026-10-01", source: "altruist" },
      { account: "*****0002", held_away: true, label: "Fund ZZF (ZZF)", market_value: "50000.00", as_of: "2026-09-30", source: "statement" }],
    by_security: [{ key: "c1", label: "Apple (ZZAPL)", exposure: "21000.00", weight: "0.14", flag: true, via: ["Fund ZZF (ZZF)"] }],
    by_sector: [{ sector: "Information Technology", exposure: "21000.00", weight: "0.14" }, { sector: "unclassified", exposure: "100.00", weight: "0.0006" }],
    concentration: { threshold: "0.10", flags: [{ key: "c1", message: "Apple (ZZAPL) is 14.00% of the household — at or above the 10.00% single-name threshold" }] },
    overlap: [], replica_overlap: [], funds,
    status_lines: ["Tracking error: not estimated — needs prices", "Harvest value of switching: not estimated — needs prices", "After-tax performance: not estimated — needs prices"] },
  runs: [], permissions, actions, vocabularies: X_VOCAB });

test("X-ray: 'Open join request' only with can_join AND the server's action, never on a held-away line", () => {
  for (const p of [XP_VIEW, undefined, {}, { ...XP_ALL, can_join: "true" }]) {
    assert.ok(!buttons(render(h(XrayView, { initialEnvelope: xEnv(p, [fund()]) }))).includes("Open join request"), JSON.stringify(p));
  }
  assert.ok(!buttons(render(h(XrayView, { initialEnvelope: xEnv(XP_ALL, [fund({ actions: [] })]) }))).includes("Open join request"));
  assert.ok(buttons(render(h(XrayView, { initialEnvelope: xEnv(XP_ALL, [fund()]) }))).includes("Open join request"));
  const held = fund({ held_away: true, account_id: "a2", account: "*****0002", join: { available: false, reason: "held away — moving the assets comes first" } });
  const html = render(h(XrayView, { initialEnvelope: xEnv(XP_ALL, [held]) }));
  assert.ok(!buttons(html).includes("Open join request"), "a held-away line never carries the button, even if listed");
  assert.match(text(html), /held away — moving the assets comes first/);
  assert.ok(!buttons(render(h(XrayView, { initialEnvelope: xEnv(XP_VIEW, [fund()]) }))).includes("Record a run"));
});

test("X-ray: estimates labelled with inputs on hover; needs-prices lines; sectors; concentration", () => {
  const html = render(h(XrayView, { initialEnvelope: xEnv(XP_VIEW, [fund(), fund({ account_id: "a3", transition_tax: { amount: null, note: "not estimated — no tax profile" },
    fee: { annual_difference: null, note: "no fee schedule" }, break_even: { years: null, note: "not estimated — needs the transition tax and the fee difference" } })]) }));
  const t = text(html);
  assert.match(t, /\$50\.00 \(estimate\)/);
  assert.match(t, /\$4,760\.00 \(estimate\)/);
  assert.match(t, /95\.20 years \(estimate\)/);
  assert.match(t, /not estimated — no tax profile/);
  assert.match(t, /no fee schedule/);
  assert.match(t, /Tracking error: not estimated — needs prices/);
  assert.match(t, /After-tax performance: not estimated — needs prices/);
  assert.match(t, /holdings as of 2026-09-30/);
  assert.match(t, /Information Technology/);
  assert.match(t, /at or above the 10\.00% single-name threshold/);
  assert.match(html, /title="estimate: 100000\.00 × 0\.09% − the model&#x27;s strategy fee 40\.00 · market value: 100000\.00/);
});

test("proposal view: stale held-away badge, the confirm link, and no Approve while blocking", () => {
  const prop = (blocking) => ({
    proposal: { id: "p1", status: "pending_approval", trigger: "drift", account: { account_number_masked: "*****0003" }, warnings: [], trades: [],
      tasks: [{ status: "open", eligible_permission: "approve_proposals", maker_id: "u1" }],
      held_away: { blocking, dependencies: [{ account_id: "a1", stale: true, blocking, message: "held-away account *****0001: data as of 2026-08-01 is older than 30 day(s) — an adviser must confirm it before approval" }] } },
    permissions: { can_read: true, can_write: true, can_approve: true, can_decide: true, is_maker: false }, actions: ["approve", "reject"],
    vocabularies: { statuses: [], triggers: [], warning_codes: [] } });
  const html = render(h(ProposalDetail, { initialEnvelope: prop(true) }));
  const t = text(html);
  assert.match(t, /Stale — confirm before approval/);
  assert.match(t, /data as of 2026-08-01 is older than 30 day\(s\)/);
  assert.match(html, /href="\/portfolio\/accounts\/a1"/);
  assert.ok(!buttons(html).includes("Approve"), "approval waits for the confirmation");
  assert.ok(buttons(render(h(ProposalDetail, { initialEnvelope: prop(false) }))).includes("Approve"));
});

test("lib: helpers fail closed and keep decimals as strings", () => {
  assert.deepEqual(lib.importControls({ permissions: { can_approve: "true" } }, { actions: ["approve"] }), { approve: false, reject: false });
  assert.equal(lib.joinControl({ permissions: { can_join: true } }, { actions: ["open_join_request"], held_away: true, join: { available: true } }), false);
  assert.equal(lib.estimateText(null, "not estimated — needs prices"), "not estimated — needs prices");
  assert.deepEqual(lib.manualRow({ ticker: "ZZA", quantity: "1,000.50", cost: "x", acquired_date: "2020-01-02" }),
    { ticker: "ZZA", quantity: "1000.50", acquired_date: "2020-01-02" });
  assert.equal(lib.barWidth("0.14"), 14);
});
