// REP6 — the REAL Tax components rendered with react-dom/server from envelopes
// shaped exactly like routers/tax.py returns them. Every write / approve control
// (Refresh loss bank, Seed the rule denials, Send for approval, Approve, Reject,
// Retire, Declare, Withdraw, Scan now) must FAIL CLOSED: none without the
// server's own flag being exactly true AND the action in the server's list (on
// the envelope or on the row). Every tax number shows as an estimate, or with
// the server's reason it is not estimated.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const lib = await import("../../lib/tax.mjs");
const { default: HarvestDashboard } = await import("../../components/tax/HarvestDashboard.jsx");
const { default: Substitutions } = await import("../../components/tax/Substitutions.jsx");
const { default: Profiles } = await import("../../components/tax/Profiles.jsx");
const { default: DeclaredTrades } = await import("../../components/tax/DeclaredTrades.jsx");
const { default: AccountTaxPanel } = await import("../../components/tax/AccountTaxPanel.jsx");
const { default: ProposalDetail } = await import("../../components/proposals/ProposalDetail.jsx");
const menu = await import("../../lib/menuVisibility.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());

const VOCAB = {
  wash_sale_outcomes: [{ key: "clear", label: "Clear" }, { key: "replaced", label: "Replaced by an approved substitute" },
    { key: "deferred", label: "Deferred until it is clear" }, { key: "blocked", label: "Blocked" }],
  skip_reasons: [{ key: "below_threshold", label: "Below the loss thresholds (net of trading cost)" },
    { key: "needs_price", label: "Needs a price — the account cannot value the holding" }],
  run_outcomes: [{ key: "proposed", label: "A harvest proposal was made" }],
  set_statuses: [{ key: "pending_approval", label: "Awaiting approval" }, { key: "approved", label: "Approved (in use)" }],
  denial_statuses: [{ key: "active", label: "Active" }], denial_rules: [{ key: "same_index", label: "Two funds tracking the same index" }],
  candidate_sources: [{ key: "adviser", label: "Entered by an adviser" }],
  filing_statuses: [{ key: "married_filing_jointly", label: "Married filing jointly" }],
  tax_input_statuses: [{ key: "pending_approval", label: "Awaiting approval" }, { key: "approved", label: "Approved" }],
  subject_types: [{ key: "household", label: "Household" }], tax_classes: [{ key: "taxable", label: "Taxable" }],
  loss_values: [{ key: "yes", label: "At a loss" }], terms: [{ key: "short", label: "Short-term" }],
  sides: [{ key: "buy", label: "Buy" }, { key: "sell", label: "Sell" }], harvesting_engines: [{ key: "hollisworks", label: "Hollisworks" }],
  no_profile_label: "not estimated — no tax profile", editable: [],
};
const PERMS_VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false,
  read_permission: "view_tax", write_permission: "manage_tax", approve_permission: "approve_tax" };
const PERMS_ALL = { ...PERMS_VIEW, can_write: true, can_approve: true };
const RATES = { estimated: true, label: "estimate", st: "0.458", lt: "0.288", as_of: "2026-10-01", tax_year: 2026,
  parts: { federal_ordinary: "0.37", federal_lt: "0.2", niit: "0.038", state: "0.05", state_lt: "0.05", city: "0", city_lt: "0" } };
const WRITE_WORDS = ["Refresh loss bank", "Seed the rule denials", "Send for approval", "Approve", "Reject", "Retire",
  "Declare", "Withdraw", "Scan now", "Add a candidate"];
const BAD_PERMS = [PERMS_VIEW, undefined, {}, { ...PERMS_ALL, can_write: "true", can_approve: "true" }];

const DASH = { as_of: "2026-10-23", harvestable: { rows: [{ id: "c1", account: "*****0001", security: "Alpha (ALP)", quantity: "100",
  term: "short", unrealized: "-1000", net_loss: "995", est_tax_value: "458.00", value_note: "estimate: loss × the short-term combined rate 0.458", outcome: "proposed",
  replacement: "Beta (BET)", proposal_id: "p1" }, { id: "c2", account: "*****0001", security: "Gamma", quantity: "5", term: "long",
  unrealized: "-700", net_loss: "700", est_tax_value: null, value_note: "not estimated — no tax profile", outcome: "skipped", skip_reason: "below_threshold" }],
  loss: "1700", est_value: "458.00", estimated: 1, not_estimated: 1, label: "estimate" },
  skipped: [{ id: "c2", account: "*****0001", security: "Gamma", skip_reason: "below_threshold", skip_detail: "net loss 700 vs thresholds" }],
  gain_budgets: [], wash_windows: [{ kind: "loss_sale", label: "Alpha (ALP)", account: "*****0002", to: "2026-10-20", pending: false, blocks: "buys", window_ends: "2026-11-19", clear_from: "2026-11-20" }],
  loss_bank: [], not_estimated: { after_tax_performance: "not estimated — needs prices", tracking_error_cost: "not estimated — needs prices" } };
const dashEnv = (permissions, actions) => ({ dashboard: DASH, permissions, actions, vocabularies: VOCAB });

test("harvest dashboard: no Refresh control without can_write AND the action; estimates labelled", () => {
  for (const p of BAD_PERMS) {
    const b = buttons(render(h(HarvestDashboard, { envelope: dashEnv(p, ["refresh_loss_bank", "scan_account"]) })));
    for (const w of WRITE_WORDS) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(p)}: ${b}`);
  }
  assert.ok(!buttons(render(h(HarvestDashboard, { envelope: dashEnv(PERMS_ALL, []) }))).includes("Refresh loss bank"));
  const html = render(h(HarvestDashboard, { envelope: dashEnv(PERMS_ALL, ["refresh_loss_bank"]) }));
  assert.ok(buttons(html).includes("Refresh loss bank"));
  const t = text(html);
  assert.match(t, /\$458\.00 \(estimate\)/, "a tax value always carries 'estimate'");
  assert.match(t, /not estimated — no tax profile/, "no profile → the server's reason, never a number");
  assert.match(t, /Below the loss thresholds/, "skip reasons from the envelope's vocabulary");
  assert.match(t, /until 2026-11-19; clear from 2026-11-20/, "open windows with their end dates");
  assert.match(t, /After-tax performance: not estimated — needs prices/);
  assert.match(html, /title="estimate: loss × the short-term combined rate 0\.458/, "the rate on hover");
});

const SETS = [{ id: "s1", security: "Alpha", version_no: 2, status: "pending_approval", current: true, maker_id: "u1",
  candidates: [{ replacement_security_id: "b", label: "Beta", reason: "same industry", evidence: "different index provider", source: "adviser" }],
  actions: ["approve", "reject"] },
{ id: "s0", security: "Alpha", version_no: 1, status: "approved", current: true, maker_id: "u2", candidates: [], actions: ["retire"] }];
const DENIALS = [{ id: "d1", security_a: "Alpha", security_b: "Alpha Admiral", rule: "same_index", status: "active", reason: "same index", actions: ["retire"] }];
const subEnv = (permissions, actions, rowActs) => ({ sets: SETS.map((s) => ({ ...s, actions: rowActs ?? s.actions })),
  denials: DENIALS.map((d) => ({ ...d, actions: rowActs ?? d.actions })), permissions, actions, vocabularies: VOCAB });

test("substitutions: a view-only / lost / not-exactly-true envelope renders no propose, approve, reject or retire", () => {
  for (const p of BAD_PERMS) {
    const b = buttons(render(h(Substitutions, { envelope: subEnv(p, ["propose_set", "propose_denial", "seed_denials"]) })));
    for (const w of WRITE_WORDS) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(p)}: ${b}`);
  }
  // the flags but the server listed no row action (the maker's view) → no decision
  const maker = buttons(render(h(Substitutions, { envelope: subEnv(PERMS_ALL, [], []) })));
  assert.ok(!maker.includes("Approve") && !maker.includes("Reject") && !maker.includes("Retire"), `${maker}`);
  const checker = buttons(render(h(Substitutions, { envelope: subEnv(PERMS_ALL, ["propose_set", "seed_denials"]) })));
  assert.ok(checker.includes("Approve") && checker.includes("Reject") && checker.includes("Retire"));
  assert.ok(checker.includes("Seed the rule denials") && checker.includes("Send for approval"));
  const t = text(render(h(Substitutions, { envelope: subEnv(PERMS_VIEW, []) })));
  assert.match(t, /same industry; evidence: different index provider/, "every candidate's reason and evidence");
  assert.match(t, /v1 · Approved \(in use\)/, "every version kept and labelled");
});

const PROFILES = [{ id: "pr1", subject_type: "household", subject_id: "h1", tax_year: 2026, version_no: 1, status: "pending_approval",
  current: true, filing_status: "married_filing_jointly", federal_ordinary_rate: "0.37", federal_lt_rate: "0.2", niit_applies: true,
  state_code: "NY", state_rate: "0.0685", city: "NYC", city_rate: "0.03876", as_of: "2026-10-01", source_note: "2025 return",
  entered_by: "u1", combined: { estimated: false, label: "not in use until approved" }, actions: ["approve", "reject"] }];
const profEnv = (permissions, actions, rowActs) => ({ rows: PROFILES.map((p) => ({ ...p, actions: rowActs ?? p.actions })),
  carryforwards: [], permissions, actions, vocabularies: VOCAB });

test("profiles: no Enter / Approve / Reject without the flags and the server's actions", () => {
  for (const p of BAD_PERMS) {
    const b = buttons(render(h(Profiles, { envelope: profEnv(p, ["create_profile", "create_carryforward"]), subjects: [] })));
    for (const w of WRITE_WORDS) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(p)}: ${b}`);
  }
  const maker = buttons(render(h(Profiles, { envelope: profEnv(PERMS_ALL, [], []), subjects: [] })));
  assert.ok(!maker.includes("Approve") && !maker.includes("Reject"), "the maker is offered no decision");
  const all = buttons(render(h(Profiles, { envelope: profEnv(PERMS_ALL, ["create_profile"]), subjects: [] })));
  assert.ok(all.includes("Approve") && all.includes("Send for approval"));
  assert.match(text(render(h(Profiles, { envelope: profEnv(PERMS_VIEW, []), subjects: [] }))), /not in use until approved/);
});

test("declared trades: Declare / Withdraw only with can_write and the actions", () => {
  const rows = [{ id: "t1", side: "sell", trade_date: "2026-10-01", security: "Alpha", account_description: "spouse 401(k)",
    account_tax_class: "taxable", loss: "yes", subject_type: "household", subject_id: "h1", source_note: "phone", live: true, actions: ["withdraw"] }];
  for (const p of BAD_PERMS) {
    const b = buttons(render(h(DeclaredTrades, { envelope: { rows, permissions: p, actions: ["declare"], vocabularies: VOCAB }, subjects: [] })));
    for (const w of WRITE_WORDS) assert.ok(!b.includes(w), `no ${w}: ${b}`);
  }
  const b = buttons(render(h(DeclaredTrades, { envelope: { rows, permissions: PERMS_ALL, actions: ["declare"], vocabularies: VOCAB }, subjects: [] })));
  assert.ok(b.includes("Declare") && b.includes("Withdraw"));
});

test("account Tax panel: no panel without the server's panel; Scan now only with can_write and the action", () => {
  assert.equal(render(h(AccountTaxPanel, { envelope: null })), "", "no envelope (403 — no view_tax) → no panel at all");
  assert.equal(render(h(AccountTaxPanel, { envelope: { permissions: PERMS_ALL } })), "");
  const panel = { account: { id: "a1", tax_class: "taxable" }, rates: { estimated: false, label: "not estimated — no tax profile" },
    loss_bank: { tax_year: 2026, totals: {}, tax_value: { estimated: false, label: "not estimated — no tax profile" } },
    wash_windows: [], last_harvest_run: null, last_harvest_candidates: [], harvesting_engine: "hollisworks" };
  for (const p of BAD_PERMS) {
    assert.ok(!buttons(render(h(AccountTaxPanel, { envelope: { panel, permissions: p, actions: ["scan_account"], vocabularies: VOCAB } }))).includes("Scan now"));
  }
  const html = render(h(AccountTaxPanel, { envelope: { panel, permissions: PERMS_ALL, actions: ["scan_account"], vocabularies: VOCAB } }));
  assert.ok(buttons(html).includes("Scan now"));
  assert.match(text(html), /Tax values: not estimated — no tax profile/, "no profile → never a default rate");
  const est = render(h(AccountTaxPanel, { envelope: { panel: { ...panel, rates: RATES }, permissions: PERMS_VIEW, actions: [], vocabularies: VOCAB } }));
  assert.match(text(est), /short-term 45\.800%, long-term 28\.800% \(estimate, as of 2026-10-01\)/);
});

test("proposal detail: each trade shows its wash-sale outcome and the matches; a pre-rep6 flag still shows", () => {
  const trades = [
    { id: "t1", side: "buy", quantity: "10", security: { name: "Beta", ticker: "BET" }, price: "50", price_source: "account_position",
      lots: [], reason: "Buy", est_st_gain: "0", est_lt_gain: "0",
      wash_sale: { outcome: "replaced", replacement: { label: "Beta (BET)" }, reason: "wash sale: bought Beta instead",
        matches: [{ side: "sell", label: "Alpha", account: "*****0002", to: "2026-10-20", pending: false, identical_because: "the same security" }] } },
    { id: "t2", side: "buy", quantity: "10", security: { name: "Delta" }, price: "5", price_source: "account_position", lots: [],
      reason: "Buy", est_st_gain: "0", est_lt_gain: "0", wash_sale: { outcome: "blocked", reason: "blocked: buying Delta in this IRA / Roth now would permanently forfeit", matches: [] } },
    { id: "t3", side: "buy", quantity: "1", security: { name: "Old" }, price: "5", price_source: "account_position", lots: [],
      reason: "Buy", est_st_gain: "0", est_lt_gain: "0", wash_sale_flag: { note: "the household sold this security at a loss" } },
  ];
  const env = { proposal: { id: "p1", status: "pending_approval", trigger: "harvest", trades, tasks: [], instructions: [] },
    permissions: PERMS_VIEW, actions: [], vocabularies: { ...VOCAB, triggers: [{ key: "harvest", label: "Tax-loss harvest" }] } };
  const t = text(render(h(ProposalDetail, { initialEnvelope: env })));
  assert.match(t, /Wash sale: Replaced by an approved substitute: Beta \(BET\)/);
  assert.match(t, /loss sale of Alpha in \*\*\*\*\*0002 on 2026-10-20 — the same security/);
  assert.match(t, /Wash sale: Blocked — blocked: buying Delta in this IRA \/ Roth now would permanently forfeit/);
  assert.match(t, /Wash-sale flag: the household sold/, "a proposal made before rep6 keeps its flag");
  assert.match(t, /Tax-loss harvest/);
});

test("lib: estimates never print a bare number; percents convert exactly; rates on hover", () => {
  assert.equal(lib.estimateText(null, "not estimated — no tax profile"), "not estimated — no tax profile");
  assert.equal(lib.estimateText("458", "x"), "$458.00 (estimate)");
  assert.equal(lib.pctToFraction("37"), "0.37");
  assert.equal(lib.pctToFraction("6.85"), "0.0685");
  assert.equal(lib.pctToFraction("3.876"), "0.03876");
  assert.match(lib.ratesTitle(RATES), /as of 2026-10-01\): short-term 45\.800% = federal 37\.000% \+ NIIT 3\.800%/);
  assert.equal(lib.ratesTitle({ estimated: false, label: "not estimated — no tax profile" }), "not estimated — no tax profile");
  const body = lib.profileBody({ subjectType: "household", subjectId: "h1", taxYear: "2026", filingStatus: "single",
    federalOrdinaryPct: "37", federalLtPct: "20", niitApplies: true, asOf: "2026-10-01", sourceNote: "s", statePct: "6.85" });
  assert.deepEqual([body.federal_ordinary_rate, body.federal_lt_rate, body.state_rate, body.niit_applies], ["0.37", "0.2", "0.0685", true]);
  assert.ok(!("state_lt_rate" in body), "a blank LT rate is not sent (state treats LT as ordinary)");
});

test("menu: Tax gated on view_tax (strict) in the Portfolio group", () => {
  const item = menu.MENU_ITEMS.find((i) => i.href === "/portfolio/tax");
  assert.ok(item, "Tax tab exists");
  assert.equal(item.group, "portfolio");
  assert.deepEqual(item.gate, { perm: "view_tax", strict: true });
});
