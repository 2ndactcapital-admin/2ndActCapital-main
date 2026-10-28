// REP8 — the options sleeve's REAL components rendered with react-dom/server
// from envelopes shaped exactly like GET /replication/options/entries/{id},
// /packages/{id}/payoff and /proposals/{id}/gate return them. Every write
// control (build, price, submit, approve / reject a package, enter a quote,
// propose / roll, re-run the gate, approve an options proposal) must FAIL
// CLOSED: none without the server's own flag being exactly true AND, where the
// server lists actions, the action listed. Unpriced figures read "not priced".
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const O = await import("../../lib/replicationOptions.mjs");
const { OptionsSleeveView, PayoffLabView } = await import("../../components/replication/OptionsTab.jsx");
const { TaxGatePanelView } = await import("../../components/replication/OptionsTaxGate.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const render = (el) => renderToStaticMarkup(el);

const VOCAB = {
  leg_roles: [{ key: "underlying", label: "Underlying (the core sleeve)" }, { key: "long_put_atm", label: "Long put at the money" },
    { key: "short_put_buffer_end", label: "Short put (ends the buffer)" }, { key: "short_call_cap", label: "Short call (sets the cap)" }],
  quote_kinds: [{ key: "premium", label: "Option premium" }, { key: "index_level", label: "Index level" }],
  package_statuses: [{ key: "draft", label: "Draft" }, { key: "approved", label: "Approved" }],
  package_kinds: [{ key: "buffer", label: "Buffer (defined outcome)" }],
  core_sources: [{ key: "etf_itself", label: "The ETF itself" }],
  call_rules: [], option_actions: [{ key: "open", label: "Open the legs" }, { key: "roll", label: "Roll" }],
  gate_rules: [{ key: "straddle", label: "Straddle" }, { key: "wash_sale", label: "Wash sale" }],
  gate_outcomes: [{ key: "pass", label: "Pass" }, { key: "flag", label: "Flag" }, { key: "block", label: "Block" }],
  pending_counsel_label: "pending counsel review",
  editable: ["underlying_global_security_id"], price_editable: ["start_level_quote_id"],
  quote_editable: ["global_security_id", "kind", "value", "source", "as_of"],
};
const VIEW_ONLY_VOCAB = { ...VOCAB, editable: [], price_editable: [], quote_editable: [] };

const pkg = (over = {}) => ({ id: "p1", package_no: 1, kind: "buffer", status: "draft", outcome_start: "2026-01-02",
  outcome_end: "2027-01-02", core_source: "etf_itself", start_level: null, cap_pct: "0.15", buffer_pct: "0.1",
  floor_pct: "-0.9", stated_cap_pct: "0.15", stated_buffer_pct: "0.1", maker_ids: ["u1"], rolled_from_id: null, ...over });

const envelope = ({ perms = {}, vocab = VOCAB, actions = [], packages = true, status = "draft", min = null, rolled = null } = {}) => ({
  is_options_class: true, kind: "buffer", terms: { missing: [] },
  viability: { note: "manual execution only — Altruist options access unconfirmed" },
  packages: packages ? [pkg({ status })] : [], accounts_following: [{ id: "a1", account_number_masked: "******0001" }],
  current: packages ? {
    package: pkg({ status, rolled_from_id: rolled }), actions, needs: [],
    legs: [{ id: "l1", role: "underlying", ratio: "1" }, { id: "l2", role: "short_call_cap", strike_pct: "1.15", ratio: "1", expiry: "2027-01-02", is_flex: true }],
    quotes: [], minimum_account: min || { status: "not_priced", message: "not priced — enter an index level" },
    basis_risk: { tracking_error: "not estimated — needs prices" }, underlying: { id: "g1", name: "SPX" },
  } : null,
  permissions: { can_read: true, ...perms }, vocabularies: vocab,
});

const FULL = { can_write: true, can_approve: true, can_build: true, can_propose: true };

test("view-only: no build, price, submit, approve, quote or propose control", () => {
  const html = text(render(h(OptionsSleeveView, { envelope: envelope({ vocab: VIEW_ONLY_VOCAB, actions: ["price", "submit", "approve"] }), onAct: () => {} })));
  for (const w of ["Build the options sleeve", "Price the draft", "Submit for approval", "Approve package", "Enter quote", "Open the legs"]) {
    assert.ok(!html.includes(w), `view-only shows ${w}`);
  }
  assert.ok(html.includes("manual execution only — Altruist options access unconfirmed"));
  assert.ok(html.includes("not priced — enter an index level"));
});

test("controls appear only with the flag AND the server's action", () => {
  const noActions = O.optionsControls(envelope({ perms: FULL }));
  assert.deepEqual([noActions.price, noActions.submit, noActions.approve, noActions.reject], [false, false, false, false]);
  const c = O.optionsControls(envelope({ perms: FULL, actions: ["price", "submit", "approve", "reject"] }));
  assert.deepEqual([c.price, c.submit, c.approve, c.reject, c.enterQuote], [true, true, true, true, true]);
  const truthy = O.optionsControls(envelope({ perms: { can_write: "yes", can_approve: 1 }, actions: ["price", "approve"] }));
  assert.equal(truthy.price || truthy.approve, false, "a truthy non-true flag must not open a control");
  assert.equal(O.optionsControls(null).build, false);
  assert.equal(O.optionsControls({}).enterQuote, false);
});

test("build only before any package and only with can_build + editable", () => {
  assert.equal(O.optionsControls(envelope({ perms: FULL, packages: false })).build, true);
  assert.equal(O.optionsControls(envelope({ perms: FULL })).build, false);
  assert.equal(O.optionsControls(envelope({ perms: { ...FULL, can_build: false }, packages: false })).build, false);
});

test("propose / roll: only on an approved package, roll only when it rolls an earlier one", () => {
  assert.deepEqual(O.proposeControls(envelope({ perms: FULL, status: "draft" })), { propose: false, roll: false });
  assert.deepEqual(O.proposeControls(envelope({ perms: FULL, status: "approved" })), { propose: true, roll: false });
  assert.deepEqual(O.proposeControls(envelope({ perms: FULL, status: "approved", rolled: "p0" })), { propose: true, roll: true });
  assert.deepEqual(O.proposeControls(envelope({ perms: { can_write: true }, status: "approved", rolled: "p0" })), { propose: false, roll: false });
  const html = text(render(h(OptionsSleeveView, { envelope: envelope({ perms: { can_read: true }, status: "approved", rolled: "p0" }), onAct: () => {} })));
  assert.ok(!html.includes("Roll") && !html.includes("Open the legs"), "no propose / roll control without can_propose");
  const shown = text(render(h(OptionsSleeveView, { envelope: envelope({ perms: FULL, status: "approved", rolled: "p0" }), onAct: () => {} })));
  assert.ok(shown.includes("Roll") && shown.includes("Open the legs"));
});

test("minimum account: exact text from the quote, else not priced", () => {
  assert.equal(O.minimumText({ status: "not_priced", message: "not priced — enter an index level" }), "not priced — enter an index level");
  const t = O.minimumText({ status: "priced", label: "estimate", multiplier: "100", level: "5000", tolerance: "0.05",
    quote: { as_of: "2026-10-28T15:00:00+00:00" }, standard: { minimum_account_cents: "476190.48", continuous_from_cents: "4761904.77" } });
  assert.ok(t.includes("$476190.48") && t.includes("estimate") && t.includes("level 5000"));
});

test("payoff lab: chart + accessible table of the same points; unpriced figures say so; note slot phase 3", () => {
  const payoff = {
    at_expiry_note: "exact at expiry from strikes and ratios",
    points: [{ underlying_return: "-0.5", replica_return: "-0.4", stated_return: "-0.4", difference: "0" },
      { underlying_return: "0", replica_return: "0", stated_return: "0", difference: "0" },
      { underlying_return: "0.3", replica_return: "0.15", stated_return: "0.15", difference: "0" }],
    mark_to_market: { status: "not_priced", message: "not priced — enter a quote" },
    reprice: { status: "not_priced", message: "not priced — enter a quote" },
    note_comparison: { label: "Structured-note comparison — phase 3", status: "empty" },
  };
  const html = render(h(PayoffLabView, { payoff }));
  assert.ok(html.includes("<svg") && html.includes("<table"));
  const t = text(html);
  assert.ok(t.includes("-40%") && t.includes("15%"));
  assert.equal((t.match(/not priced — enter a quote/g) || []).length, 2);
  assert.ok(t.includes("phase 3") && t.includes("empty"));
  assert.ok(!html.includes("#000") && !/dark/i.test(html), "light theme only");
});

test("tax gate: rows with labels and pending-counsel badge; re-run only with can_propose; approve hidden on a block", () => {
  const gate = { evaluated_at: "2026-10-28T12:00:00+00:00", permissions: {}, vocabularies: VOCAB,
    results: [{ id: "r1", rule: "straddle", outcome: "flag", reason: "straddle identified — pending counsel review", pending_counsel: true, leg_role: "short_call_cap" },
      { id: "r2", rule: "wash_sale", outcome: "block", reason: "wash sale", pending_counsel: false, leg_role: null }] };
  const t = text(render(h(TaxGatePanelView, { gate, onRerun: () => {} })));
  assert.ok(t.includes("Straddle") && t.includes("Block") && t.includes("pending counsel review") && t.includes("Whole proposal"));
  assert.ok(!t.includes("Run the gate again"));
  assert.ok(text(render(h(TaxGatePanelView, { gate: { ...gate, permissions: { can_propose: true } }, onRerun: () => {} }))).includes("Run the gate again"));
  assert.equal(O.optionsApproveVisible(true, gate), false, "a blocking rule hides approve");
  assert.equal(O.optionsApproveVisible(true, { ...gate, results: [gate.results[0]] }), true);
  assert.equal(O.optionsApproveVisible(true, null), false, "a gate that has not loaded hides approve");
  assert.equal(O.optionsApproveVisible("true", { results: [] }), false);
});
