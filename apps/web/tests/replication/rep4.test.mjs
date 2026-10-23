// REP4 — the REAL Model tab component rendered with react-dom/server from
// envelopes shaped exactly like GET /replication/registry/{id}/model returns
// them. Build, Preview and "Use this model" must FAIL CLOSED: none without the
// server's own can_build flag being exactly true (manage_models AND
// view_replication). Viability answers and the cost table show every unknown
// as unknown, and the tax line never carries a number.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const rep = await import("../../lib/replication.mjs");
const { ModelTabView, ViabilityChecklist, CostTable } = await import("../../components/replication/ModelTab.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");

const VOCAB = {
  questions: [
    { key: "rules_based", label: "Is the fund rules-based and are its holdings public?" },
    { key: "rebuildable", label: "Can it be rebuilt with stocks or listed options?" },
    { key: "account_size", label: "Is the account big enough at the target tracking error?" },
    { key: "custodian", label: "Does the custodian allow every leg?" },
    { key: "cost", label: "Does the replica beat the fund after fees and tax?" },
  ],
  answer_statuses: [{ key: "yes", label: "Yes" }, { key: "no", label: "No" }, { key: "unknown", label: "Unknown" }],
  cost_lines: [{ key: "fund_expense", label: "Fund expense ratio (net)" }, { key: "strategy_fee", label: "Model strategy fee" },
    { key: "trading_cost", label: "Estimated trading cost" }, { key: "replica_total", label: "Replica total" },
    { key: "tax_benefit", label: "Tax benefit" }],
  tax_line: "Estimated by the tax engine (rep6) — not estimated here",
  methods: [{ key: "full", label: "Every holding" }, { key: "top_n", label: "Largest holdings only (top N)" }],
  unresolved_policies: [{ key: "block", label: "Keep them" }, { key: "exclude_and_reweight", label: "Leave them out" }],
  custodians: [{ key: "altruist", label: "Altruist" }, { key: "manual", label: "Manual — execution instruction" }],
  drop_reasons: [{ key: "below_min_weight", label: "Under the minimum weight" }],
  monitor_outcomes: [{ key: "within_tolerance", label: "Within tolerance — no draft" }],
  rebalance_frequencies: [{ key: "quarterly", label: "Quarterly" }],
  events: [],
};
const BUILD_FIELDS = ["custodian_code", "method", "top_n", "min_weight_pct", "cash_target_pct", "unresolved_policy"];
const PERMS_VIEW = { can_read: true, can_build: false, can_view_models: true, is_super_admin: false,
  read_permission: "view_replication", write_permission: "manage_models", model_read_permission: "view_models" };
const PERMS_BUILD = { ...PERMS_VIEW, can_build: true };
const MODEL = { model: { id: "m1", name: "Holdings of QQQ" }, key: { custodian_code: "altruist", method: "full", top_n: null },
  live_version: { id: "v3", version_no: 3 }, open_version: null, accounts_using: 0, positions: 101 };
const REPORT = {
  id: "r1", custodian_code: "altruist", account_size: "250000.00", overall: "unknown", computed_at: "2026-10-23T12:00:00",
  answers: [
    { key: "rules_based", status: "yes", reason: "An index fund follows published rules.", missing: [] },
    { key: "rebuildable", status: "yes", reason: "99% stocks.", missing: [] },
    { key: "account_size", status: "unknown", reason: "unknown — needs prices for 3 of 3 constituents",
      missing: ["price: AAA (no close price)"] },
    { key: "custodian", status: "yes", reason: "Yes, via execution instructions.", missing: [] },
    { key: "cost", status: "unknown", reason: "unknown — needs a default fee schedule", missing: ["a default fee schedule"] },
  ],
  cost_lines: [
    { key: "fund_expense", status: "known", amount: "500.00", reason: "0.20% a year" },
    { key: "strategy_fee", status: "unknown", amount: null, reason: "unknown — no model" },
    { key: "trading_cost", status: "known", amount: "12.34", reason: "turnover" },
    { key: "replica_total", status: "unknown", amount: null, reason: "unknown" },
    { key: "tax_benefit", status: "not_estimated", amount: "999.99", reason: "Estimated by the tax engine (rep6)" },
  ],
};
const env = (permissions, extra = {}) => ({
  entry: { id: "e1", ticker: "QQQ", display_name: "Holdings of QQQ", disclosure: rep.disclosure("QQQ") },
  permissions,
  vocabularies: { ...VOCAB, editable: permissions?.can_build === true ? BUILD_FIELDS : [] },
  sources: { terms_version: { id: "t1", version_no: 2 }, snapshot: { id: "s1", as_of: "2026-08-31", age_days: 53,
    row_count: 3, nport_stale: true }, missing: [] },
  defaults: { account_size: "250000", custodian_code: "altruist", method: "full", unresolved_policy: "block" },
  models: [], reports: [REPORT], monitoring: [],
  ...extra,
});
const FORM = { custodian_code: "altruist", method: "full", unresolved_policy: "block", account_size: "250000" };
const PREVIEW = { existing: null, name: "Holdings of QQQ", snapshot: { as_of: "2026-08-31" }, terms_version: { version_no: 2 },
  departures: [{ kind: "min_weight", text: "Holdings under 0.5000% are dropped." }],
  construction: { counts: { positions: 2, dropped: 1 }, fund_rows: [
    { row_num: 1, name: "Alpha", ticker: "AAA", fund_weight: "0.6", model_weight: "0.6", resolution_state: "resolved" },
    { row_num: 2, name: "Beta", ticker: "BBB", fund_weight: "0.004", model_weight: null, dropped_reason: "below_min_weight",
      resolution_state: "resolved" }] } };

const page = (envelope, extra = {}) => text(render(h(ModelTabView, { envelope, form: FORM, onForm: () => {}, ...extra })));

test("model tab: no Build / Preview / Use controls without manage_models (view-only envelope)", () => {
  const withOffer = env(PERMS_VIEW, { models: [MODEL] });
  const t1 = page(withOffer, { preview: PREVIEW });
  assert.ok(t1.includes("An approved model already exists"), "the offer is still shown, read-only");
  assert.ok(!t1.includes("Use this model"));
  const t2 = page(env(PERMS_VIEW), { preview: PREVIEW });
  for (const label of ["Build model", "Preview", "Use this model"]) assert.ok(!t2.includes(label), label);
  assert.ok(t2.includes("Check viability"), "viability is open to view_replication");
});

test("model tab: a missing or malformed envelope renders no write control (fails closed)", () => {
  for (const e of [null, {}, { permissions: null }, { permissions: { can_build: "true" } },
    { permissions: { can_build: 1 }, vocabularies: { editable: BUILD_FIELDS } }]) {
    const t = page(e, { preview: PREVIEW });
    for (const label of ["Build model", "Preview", "Use this model", "Check viability"]) assert.ok(!t.includes(label), `${label} on ${JSON.stringify(e)}`);
  }
  // can_build true but the server published no editable fields → still no Build / Preview
  const t = page(env(PERMS_BUILD, { vocabularies: { ...VOCAB, editable: [] } }), { preview: PREVIEW });
  assert.ok(!t.includes("Build model") && !t.includes("Preview"));
});

test("model tab: with manage_models the existing model is offered FIRST; Build only when none matches the key", () => {
  const t1 = page(env(PERMS_BUILD, { models: [MODEL] }), { preview: PREVIEW });
  assert.ok(t1.includes("An approved model already exists: Holdings of QQQ (v3, built for Altruist, Every holding)"));
  assert.ok(t1.includes("Use this model"));
  assert.ok(!t1.includes("Build model"), "no Build while an existing model matches");
  const t2 = page(env(PERMS_BUILD, { models: [MODEL] }), { form: { ...FORM, custodian_code: "manual" }, onForm: () => {}, preview: PREVIEW });
  assert.ok(!t2.includes("Use this model") && t2.includes("Build model") && t2.includes("Preview"),
    "a different custodian is a different key");
  const t3 = page(env(PERMS_BUILD), {});
  assert.ok(t3.includes("Preview") && !t3.includes("Build model"), "Build appears only after a preview");
});

test("model tab: preview shows fund vs model weights, dropped rows and departures in plain words", () => {
  const t = page(env(PERMS_BUILD), { preview: PREVIEW });
  assert.ok(t.includes("Will create \"Holdings of QQQ\""));
  assert.ok(t.includes("Departs from the fund: Holdings under 0.5000% are dropped."));
  assert.ok(t.includes("60.00%") && t.includes("Under the minimum weight"));
});

test("viability: five answers in order, unknown shown as unknown with what is missing; N-PORT staleness warned", () => {
  const html = render(h(ViabilityChecklist, { report: REPORT, vocab: VOCAB }));
  const t = text(html);
  const order = VOCAB.questions.map((q) => t.indexOf(q.label));
  assert.ok(order.every((i) => i >= 0) && order.every((v, i, a) => i === 0 || v > a[i - 1]), "question order");
  assert.ok(t.includes("unknown — needs prices for 3 of 3 constituents") && t.includes("Missing: price: AAA (no close price)"));
  assert.equal((html.match(/>Unknown</g) || []).length, 2, "the two unknown answers");
  assert.ok(t.includes("Unknown overall"), "the overall stamp");
  assert.ok(page(env(PERMS_VIEW)).includes("N-PORT filing 53 days old"));
});

test("cost table: unknown lines say Unknown; the tax line never shows a number", () => {
  const t = text(render(h(CostTable, { report: REPORT, vocab: VOCAB })));
  assert.ok(t.includes("$500.00") && t.includes("$12.34"));
  assert.ok(t.includes("Model strategy fee Unknown") && t.includes("Replica total Unknown"));
  assert.ok(t.includes("Tax benefit Estimated by the tax engine (rep6)"));
  assert.ok(!t.includes("999.99"), "the tax line carries no number even if one were sent");
});

test("lib: modelControls fails closed and needs both the flag and editable fields", () => {
  assert.deepEqual(rep.modelControls(null), { build: false, preview: false, use: false, viability: false, openModel: false });
  assert.equal(rep.modelControls(env(PERMS_VIEW)).build, false);
  assert.equal(rep.modelControls(env(PERMS_BUILD)).build, true);
  assert.equal(rep.findOffer(env(PERMS_BUILD, { models: [MODEL] }), { custodian_code: "altruist", method: "top_n", top_n: 50 }), null);
  assert.deepEqual(rep.buildBody({ ...FORM, method: "top_n", top_n: "50", min_weight_pct: " 0.5 ", cash_target_pct: "" }),
    { custodian_code: "altruist", method: "top_n", unresolved_policy: "block", top_n: 50, min_weight_pct: "0.5" });
});
