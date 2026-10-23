// REP5 — the REAL accounts / account-model / proposal components rendered with
// react-dom/server from envelopes shaped exactly like routers/account_models.py
// returns them. Every write or approve control (Join a model, Change model,
// Change settings, Leave the model, Rebalance now, Propose for a withdrawal,
// Approve / Reject a change, Submit for approval, Approve / Reject a proposal,
// Create instructions) must FAIL CLOSED: none without the server's own flag
// being exactly true AND the action in the server's `actions` list; Approve /
// Reject never for the maker.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const lib = await import("../../lib/proposals.mjs");
const { default: AccountModelPanel } = await import("../../components/proposals/AccountModelPanel.jsx");
const { default: ProposalDetail } = await import("../../components/proposals/ProposalDetail.jsx");
const { default: AccountsList } = await import("../../components/proposals/AccountsList.jsx");
const menu = await import("../../lib/menuVisibility.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());

const VOCAB = {
  triggers: [{ key: "join", label: "Joined the model" }, { key: "drift", label: "Drift over threshold" }],
  statuses: [{ key: "draft", label: "Draft" }, { key: "pending_approval", label: "Awaiting approval" }],
  warning_codes: [{ key: "unpriced_buy", label: "Bought by amount (no price)" }],
  price_sources: [{ key: "account_position", label: "The account's own position valuation" }],
  plan_actions: [{ key: "sell_later", label: "Sell later" }],
  exclusion_policies: [{ key: "pro_rata", label: "Spread its weight pro rata" }],
  harvesting_engines: [{ key: "hollisworks", label: "Hollisworks" }, { key: "custodian", label: "The custodian's own harvesting" }],
  rebalance_frequencies: [{ key: "monthly", label: "Monthly" }],
  changes: [{ key: "join", label: "Join a model" }],
  account_filters: [{ key: "following", label: "Following a model" }],
  editable: [],
};
const PERMS_VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false,
  read_permission: "view_proposals", write_permission: "manage_proposals", approve_permission: "approve_proposals" };
const PERMS_ALL = { ...PERMS_VIEW, can_write: true, can_approve: true };
const ACCOUNT = { id: "a1", account_number_masked: "*****6789", custodian_code: "ALTRUIST", registration_type: "individual",
  is_held_away: false, is_discretionary: true, closed: false };
const PENDING = { id: "l2", change_kind: "join", model_name: "Holdings of QQQ — Manual", maker_id: "u1",
  proposed_fee: { schedule: "AM1 v1 — Advisory", exclusions: [{ asset_id: "as1", label: "QQQ" }] } };
const ALL_ACCOUNT_ACTIONS = ["join", "change_model", "change_settings", "end", "generate", "approve_change", "reject_change"];
const accountEnv = (permissions, actions, extra = {}) => ({
  account: ACCOUNT, links: { active: null, pending: PENDING, history: [] }, registry: [{ id: "m1", name: "Core", live_version_no: 2 }],
  harvesting_engines: [{ key: "hollisworks", label: "Hollisworks" }], transition_plan: null, proposals: [],
  permissions, actions, vocabularies: VOCAB, ...extra,
});
const ACCOUNT_WRITE = ["Join a model", "Change model", "Change settings", "Leave the model", "Rebalance now",
  "Propose for a withdrawal", "Approve", "Reject", "Send for approval"];

test("account page: a view-only, lost or not-exactly-true envelope renders NO join / change / approve control", () => {
  for (const env of [
    accountEnv(PERMS_VIEW, ALL_ACCOUNT_ACTIONS),
    accountEnv(undefined, ALL_ACCOUNT_ACTIONS),
    accountEnv({ ...PERMS_ALL, can_write: "true", can_approve: "true", can_decide_change: "true" }, ALL_ACCOUNT_ACTIONS),
    accountEnv(PERMS_ALL, undefined),
    { account: ACCOUNT },
  ]) {
    const b = buttons(render(h(AccountModelPanel, { initialEnvelope: env })));
    for (const w of ACCOUNT_WRITE) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(env.permissions)}: ${b}`);
  }
});

test("account page: Join renders only with can_write AND 'join' in the server's actions", () => {
  const yes = buttons(render(h(AccountModelPanel, { initialEnvelope: accountEnv(PERMS_ALL, ["join"]) })));
  assert.ok(yes.includes("Join a model"));
  const no = buttons(render(h(AccountModelPanel, { initialEnvelope: accountEnv(PERMS_ALL, ["generate"]) })));
  assert.ok(!no.includes("Join a model"));
});

test("account page: Approve / Reject a change only with can_approve, can_decide_change and the actions (never the maker)", () => {
  const maker = accountEnv({ ...PERMS_ALL, can_decide_change: false }, ["approve_change", "reject_change"]);
  const bm = buttons(render(h(AccountModelPanel, { initialEnvelope: maker })));
  assert.ok(!bm.includes("Approve") && !bm.includes("Reject"), `the maker sees no decision: ${bm}`);
  const checker = accountEnv({ ...PERMS_ALL, can_decide_change: true }, ["approve_change", "reject_change"]);
  const bc = buttons(render(h(AccountModelPanel, { initialEnvelope: checker })));
  assert.ok(bc.includes("Approve") && bc.includes("Reject"));
  const html = render(h(AccountModelPanel, { initialEnvelope: checker }));
  assert.match(text(html), /Not billed while held: QQQ/);
});

test("harvesting engine: the custodian option only when the server returned it", () => {
  assert.deepEqual(lib.engineOptions(accountEnv(PERMS_ALL, [])).map((o) => o.key), ["hollisworks"]);
  assert.deepEqual(lib.engineOptions({ harvesting_engines: [{ key: "hollisworks" }, { key: "custodian" }] })
    .map((o) => o.key), ["hollisworks", "custodian"]);
  assert.deepEqual(lib.engineOptions(null), []);
});

const PROPOSAL = { id: "p1", status: "pending_approval", trigger: "drift", maker_kind: "user", maker_id: "u1",
  advice_only: false, account: { account_number_masked: "*****6789" }, model_name: "Core",
  before_weights: [{ key: "s1", label: "S1", weight: "0.6" }, { key: "cash", label: "Cash", weight: "0.4" }],
  target_weights: [{ key: "s1", label: "S1", weight: "0.95" }, { key: "cash", label: "Cash", weight: "0.05" }],
  after_weights: [{ key: "s1", label: "S1", weight: "0.94" }, { key: "cash", label: "Cash", weight: "0.06" }],
  drift_before: "0.35", drift_after: "0.01", warnings: [{ code: "unpriced_buy", message: "X: no price — bought by notional" }],
  trades: [{ id: "t1", side: "buy", quantity: null, notional: "1200.00", security: { name: "X Co", ticker: "XCO" },
    price: null, price_source: "none", lots: [], reason: "Buy to the model weight", est_st_gain: "0", est_lt_gain: "0",
    wash_sale_flag: { note: "the household sold this security at a loss in the prior 30 days" } }],
  tasks: [{ id: "k1", status: "open", eligible_permission: "approve_proposals", maker_id: "u1" }], instructions: [],
};
const PROPOSAL_WRITE = ["Submit for approval", "Approve", "Reject", "Create instructions", "Check instructions"];
const proposalEnv = (permissions, actions) => ({ proposal: PROPOSAL, permissions, actions, vocabularies: VOCAB });

test("proposal: a view-only / lost / malformed envelope renders no submit, approve, reject or execute control", () => {
  const all = ["submit", "approve", "reject", "execute", "refresh_execution"];
  for (const env of [proposalEnv(PERMS_VIEW, all), proposalEnv(undefined, all), proposalEnv({}, all),
    { proposal: PROPOSAL }]) {
    const b = buttons(render(h(ProposalDetail, { initialEnvelope: env })));
    for (const w of PROPOSAL_WRITE) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(env.permissions)}: ${b}`);
  }
  // can_decide not exactly true → no decision even with every other flag and action
  const b = buttons(render(h(ProposalDetail, { initialEnvelope: proposalEnv({ ...PERMS_ALL, can_decide: "true" }, all) })));
  assert.ok(!b.includes("Approve") && !b.includes("Reject"), `${b}`);
});

test("proposal: Approve never for the maker, even holding approve_proposals; a checker sees it", () => {
  const maker = proposalEnv({ ...PERMS_ALL, is_maker: true, can_decide: true }, ["approve", "reject"]);
  assert.deepEqual(buttons(render(h(ProposalDetail, { initialEnvelope: maker }))).filter((b) => ["Approve", "Reject"].includes(b)), []);
  const checker = proposalEnv({ ...PERMS_ALL, is_maker: false, can_decide: true }, ["approve", "reject"]);
  const b = buttons(render(h(ProposalDetail, { initialEnvelope: checker })));
  assert.ok(b.includes("Approve") && b.includes("Reject"));
  const t = text(render(h(ProposalDetail, { initialEnvelope: checker })));
  assert.match(t, /\$1,200\.00 \(by amount\)/, "an unpriced buy shows as an amount, never a share count");
  assert.match(t, /Wash-sale flag: the household sold/, "the flag shows on the trade");
  assert.match(t, /Bought by amount \(no price\)/, "warning label from the envelope's vocabulary");
});

test("proposal: Submit only for can_write + 'submit' in actions", () => {
  assert.ok(buttons(render(h(ProposalDetail, { initialEnvelope: proposalEnv(PERMS_ALL, ["submit"]) }))).includes("Submit for approval"));
  assert.ok(!buttons(render(h(ProposalDetail, { initialEnvelope: proposalEnv(PERMS_ALL, []) }))).includes("Submit for approval"));
  assert.ok(!buttons(render(h(ProposalDetail, { initialEnvelope: proposalEnv(PERMS_VIEW, ["submit"]) }))).includes("Submit for approval"));
});

test("accounts list: read-only rows from the envelope; no write control at all", () => {
  const env = { rows: [{ id: "a1", account_number_masked: "*****6789", custodian_code: "ALTRUIST", is_held_away: true,
    is_discretionary: false, model: { id: "m1", name: "Core" }, last_drift: "0.0215", last_valuation: "2026-10-22",
    last_proposal: { id: "p1", status: "draft", trigger: "drift" }, open_instructions: 2 }],
  models: [], permissions: PERMS_ALL, vocabularies: VOCAB };
  const html = render(h(AccountsList, { initialEnvelope: env }));
  assert.deepEqual(buttons(html), []);
  assert.match(text(html), /\*\*\*\*\*6789 ALTRUIST held away advice only/);
  assert.match(text(html), /2\.15%/);
  assert.match(text(html), /Draft — Drift over threshold/);
});

test("lib: percent → fraction is exact; settings body carries strings, never floats", () => {
  assert.equal(lib.pctToFraction("2.5"), "0.025");
  assert.equal(lib.pctToFraction("10"), "0.1");
  assert.equal(lib.pctToFraction("0.5"), "0.005");
  assert.equal(lib.pctToFraction("100"), "1");
  assert.equal(lib.pctToFraction("abc"), null);
  const b = lib.settingsBody({ cashFloorPct: "2", driftPct: "", gainBudget: "10000.50", exclusionPolicy: "to_cash",
    avoidShortTerm: true, waitDays: "45", engine: "hollisworks", exclusions: [{ global_security_id: "g", reason: "r" }] });
  assert.deepEqual(b, { cash_floor: "0.02", drift_threshold: null, gain_budget: "10000.50", exclusion_policy: "to_cash",
    avoid_short_term: true, short_term_wait_days: 45, harvesting_engine: "hollisworks",
    exclusions: [{ global_security_id: "g", reason: "r" }] });
  assert.deepEqual(lib.weightRows(PROPOSAL).map((r) => [r.key, r.before, r.target, r.after]),
    [["s1", "0.6", "0.95", "0.94"], ["cash", "0.4", "0.05", "0.06"]]);
});

test("menu: Accounts gated on view_proposals (strict) in the Portfolio group", () => {
  const item = menu.MENU_ITEMS.find((i) => i.href === "/portfolio/accounts");
  assert.ok(item, "Accounts tab exists");
  assert.equal(item.group, "portfolio");
  assert.deepEqual(item.gate, { perm: "view_proposals", strict: true });
});
