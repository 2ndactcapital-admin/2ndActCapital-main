// REP10 — tilts, completion, consolidation, charitable gifts, backtests and the
// hypothetical-performance policy: the REAL components rendered with
// react-dom/server from envelopes shaped exactly like the API returns them.
// Every write control (create / submit / approve a tilt set, completion
// settings, consolidate, run a backtest, propose / approve / letter / receive a
// gift, request / approve a policy) must FAIL CLOSED: none without the
// server's own flag being exactly true AND the action listed. Every backtest
// surface carries HYPOTHETICAL and the disclosure; unpriced figures read
// "not estimated".
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const R = await import("../../lib/rep10.mjs");
const { TiltsView, CompletionView, PreviewView } = await import("../../components/replication/TiltsWorkspace.jsx");
const { BacktestsView, BacktestResultView } = await import("../../components/replication/BacktestWorkspace.jsx");
const { ConsolidationView } = await import("../../components/replication/ConsolidatePanel.jsx");
const { GiftDetailView, GiftsView } = await import("../../components/tax/CharitableGifts.jsx");
const { PolicyView } = await import("../../components/admin/HypotheticalPolicy.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));

const VOCAB = {
  tilt_kinds: [{ key: "security", label: "Security over- / underweight" }, { key: "sector", label: "Sector over- / underweight" }, { key: "factor", label: "Factor tilt" }],
  tilt_statuses: [{ key: "draft", label: "Draft" }, { key: "pending_approval", label: "Awaiting approval" }, { key: "approved", label: "Approved — in effect" }],
  renormalise_rule: "the net is taken pro rata from the untilted securities",
  hypothetical_note: "Projected tracking error is hypothetical — an estimate from past prices, not a promise.",
  authorities: [{ key: "read_only", label: "Read-only" }, { key: "advise", label: "Advise" }, { key: "managed", label: "Managed" }],
  consolidation_actions: [{ key: "rebalance", label: "Rebalance into the replica (rep5 proposal)" }, { key: "one_off", label: "Advice letter (one-off advice proposal)" }, { key: "listed", label: "Read-only — listed, never traded" }],
  windows: [{ key: "2008", label: "2008 — the financial crisis" }, { key: "2022", label: "2022 — rates and inflation" }, { key: "custom", label: "Custom window" }],
  cadences: [{ key: "quarterly", label: "Quarterly" }],
  audiences: [{ key: "staff_only", label: "Staff only" }, { key: "staff_and_members", label: "Staff, and members with the disclosure" }],
  policy_statuses: [{ key: "draft", label: "Awaiting a second person's approval" }, { key: "approved", label: "Approved" }],
  no_policy_label: "No hypothetical-performance policy approved — staff use only",
  open_items: ["The hypothetical-performance policy itself (SEC Marketing Rule) is for the firm and its counsel."],
  editable: ["name", "reason", "lines"], completion_editable: ["count_elsewhere"], policy_editable: ["policy_text"],
};
const VIEW_ONLY = { ...VOCAB, editable: [], completion_editable: [], policy_editable: [] };
const GIFT_VOCAB = {
  donee_kinds: [{ key: "daf", label: "Donor-advised fund" }],
  gift_statuses: [{ key: "pending_approval", label: "Awaiting approval" }, { key: "approved", label: "Approved — print the letter" }, { key: "sent", label: "Letter sent" }],
  open_items: ["Form 8283 is needed for non-cash gifts over $500 — the CPA confirms."],
  editable: ["lots"],
};

const tiltSet = (over = {}) => ({ id: "t1", version_no: 1, name: "Views", reason: "why", status: "pending_approval", maker_id: "u1",
  lines: [{ id: "l1", kind: "security", subject: "s1", subject_label: "ACME", delta: "0.02", reason: "conviction" }], actions: ["approve", "reject"], ...over });
const preview = { ok: true, metrics: { active_share: "0.02", largest_single_name: { label: "ACME", deviation: "0.02" }, largest_sector: null },
  tracking_error: { estimated: false, label: "not estimated — needs prices (0.00% of weight covered)" },
  weights: [{ key: "s1", label: "ACME", model_weight: "0.4", tilt: "0.02", final_weight: "0.42" }], notes: [] };
const tiltEnv = (perms, vocab = VOCAB, actions = ["create"], rows = [tiltSet()]) => ({ rows, base: preview, permissions: { can_read: true, ...perms }, vocabularies: vocab, actions });

test("tilts: view-only renders no create / submit / approve control", () => {
  const html = render(h(TiltsView, { envelope: tiltEnv({}, VIEW_ONLY), lines: [{ kind: "security", subject: "", points: "", reason: "" }] }));
  assert.ok(!html.includes("Save draft") && !html.includes("Approve") && !html.includes("Submit for approval"));
  assert.ok(html.includes("not estimated — needs prices (0.00% of weight covered)"));
});

test("tilts: approve only with can_approve === true AND the row's action", () => {
  assert.ok(render(h(TiltsView, { envelope: tiltEnv({ can_approve: true }) })).includes("Approve"));
  assert.ok(!render(h(TiltsView, { envelope: tiltEnv({ can_approve: "true" }) })).includes("Approve"));
  assert.ok(!render(h(TiltsView, { envelope: tiltEnv({ can_approve: true }, VOCAB, [], [tiltSet({ actions: [] })]) })).includes("Approve"));
  assert.ok(render(h(TiltsView, { envelope: tiltEnv({ can_write: true }) })).includes("Save draft"));
  assert.ok(!render(h(TiltsView, { envelope: { rows: [tiltSet()] } })).includes("Approve"));
});

test("tilts preview: model / tilt / final and an estimated TE is labelled hypothetical", () => {
  const html = render(h(PreviewView, { preview: { ...preview, tracking_error: { estimated: true, tracking_error: "0.0123" } }, envelope: tiltEnv({}) }));
  assert.ok(html.includes("1.23% (hypothetical)") && html.includes("40.00%") && html.includes("42.00%") && html.includes("2.00%"));
});

test("completion: settings and confirm only with can_write and the listed action", () => {
  const env = (perms, actions, vocab = VOCAB) => ({ settings: { count_elsewhere: false, count_authorities: ["read_only"] }, rule: "D = T × H − E",
    needs_confirmation: [{ account_id: "a9", account: "****0009", as_of: "2026-01-01" }], permissions: perms, vocabularies: vocab, actions });
  const off = render(h(CompletionView, { envelope: env({ can_write: false }, ["settings", "confirm"]) }));
  assert.ok(!off.includes("Turn completion on") && !off.includes("Ask to confirm") && off.includes("needs an adviser's confirmation"));
  const on = render(h(CompletionView, { envelope: env({ can_write: true }, ["settings", "confirm"]) }));
  assert.ok(on.includes("Turn completion on") && on.includes("Ask to confirm"));
  assert.ok(!render(h(CompletionView, { envelope: env({ can_write: true }, ["settings"], VIEW_ONLY) })).includes("Turn completion on"));
});

test("consolidation: read-only is listed, never a button; managed needs can_write and the line's action", () => {
  const env = (perms) => ({ threshold: "0.25", permissions: perms, vocabularies: VOCAB, lines: [
    { account_id: "a1", fund_security_id: "f1", label: "Big Fund", account: "****0001", authority: "managed", action: "rebalance", actions: ["consolidate"] },
    { account_id: "a2", fund_security_id: "f1", label: "Big Fund", account: "****0002", authority: "read_only", action: "listed", actions: [] }] });
  const on = renderToStaticMarkup(h(ConsolidationView, { envelope: env({ can_write: true }) }));
  assert.equal((on.match(/<button/g) || []).length, 1);
  assert.ok(text(on).includes("Read-only — listed, never traded"));
  assert.equal((renderToStaticMarkup(h(ConsolidationView, { envelope: env({}) })).match(/<button/g) || []).length, 0);
});

test("backtest: always HYPOTHETICAL with the disclosure; staff-only line without a policy; run fails closed", () => {
  const bt = { id: "b1", window_preset: "2022", start_date: "2022-01-01", end_date: "2022-12-31", status: "computed",
    result: { statistics: { replica_return: "-0.18", fund_return: "-0.19", tracking_error: "0.004", max_drawdown_replica: "-0.25", max_drawdown_fund: "-0.26" },
      harvest: { losses_total: "1000.00", count: 2, tax_value: null, tax_value_note: "not estimated — no tax profile" }, method_notes: [] } };
  const html = render(h(BacktestResultView, { envelope: { vocabularies: VOCAB }, backtest: bt }));
  assert.ok(html.includes("HYPOTHETICAL") && html.includes("No hypothetical-performance policy approved — staff use only"));
  const withPolicy = render(h(BacktestResultView, { envelope: { vocabularies: VOCAB }, backtest: { ...bt, disclosure: "Hypothetical results have inherent limitations." } }));
  assert.ok(withPolicy.includes("Hypothetical results have inherent limitations."));
  const ne = render(h(BacktestResultView, { envelope: { vocabularies: VOCAB }, backtest: { ...bt, status: "not_estimated", result: null,
    not_estimated_reason: "not estimated — needs prices — 0.00% of the replica's weight covered", coverage: { weight_covered: "0", min_weight_coverage: "0.95", missing: ["X1"] } } }));
  assert.ok(ne.includes("not estimated — needs prices") && ne.includes("X1") && !ne.includes("Tracking error"));
  assert.ok(!render(h(BacktestsView, { envelope: { rows: [], permissions: { can_write: true }, vocabularies: VOCAB, actions: [] } })).includes("Run backtest"));
  assert.ok(render(h(BacktestsView, { envelope: { rows: [], permissions: { can_write: true }, vocabularies: VOCAB, actions: ["run"] } })).includes("Run backtest"));
});

test("gifts: no approve / letter / receive control without the flags; estimates labelled", () => {
  const gift = { id: "g1", status: "pending_approval", donee_kind: "daf", donee: { name: "ZZ DAF" }, maker_id: "u1", lots: [],
    comparison: { fmv: "20000.00", in_kind: { cg_tax_avoided: "4320.00", deduction: { value: null, note: "no deduction value — does not itemize" } },
      cash: { cg_tax_paid: "4320.00", deduction: { value: null, note: "no deduction value — does not itemize" } }, tax_saved_in_kind: "4320.00" },
    reminders: [{ code: "form_8283", message: "Form 8283 is needed" }], actions: ["approve", "reject", "letter", "mark_received"] };
  const off = render(h(GiftDetailView, { envelope: { gift, permissions: {}, vocabularies: GIFT_VOCAB } }));
  assert.ok(!off.includes("Approve") && !off.includes("Print transfer letter") && !off.includes("Mark received"));
  assert.ok(off.includes("$4,320.00 (estimate)") && off.includes("no deduction value — does not itemize") && off.includes("Form 8283"));
  const on = render(h(GiftDetailView, { envelope: { gift, permissions: { can_write: true, can_approve: true }, vocabularies: GIFT_VOCAB } }));
  assert.ok(on.includes("Approve") && on.includes("Print transfer letter") && on.includes("Mark received"));
  const listed = render(h(GiftsView, { envelope: { rows: [gift], donees: [], vocabularies: GIFT_VOCAB } }));
  assert.ok(listed.includes("Open for the CPA and counsel"));
});

test("hypothetical policy: request / approve only with can_manage_policy and the listed action", () => {
  const env = (perms, actions, rowActions) => ({ rows: [{ id: "p1", version_no: 1, status: "draft", audience: "staff_and_members", policy_text: "Ours.", disclosure_text: "D.", actions: rowActions }],
    gate: { disclosure: "No hypothetical-performance policy approved — staff use only" }, permissions: perms, vocabularies: VOCAB, actions });
  const off = render(h(PolicyView, { envelope: env({ can_write: true }, ["request"], ["approve"]) }));
  assert.ok(!off.includes("Submit for approval") && !off.includes("Approve") && off.includes("staff use only"));
  const on = render(h(PolicyView, { envelope: env({ can_manage_policy: true }, ["request"], ["approve", "reject"]) }));
  assert.ok(on.includes("Submit for approval") && on.includes("Approve"));
});

test("lib: deltaFromPoints and disclosureOf never invent", () => {
  assert.equal(R.deltaFromPoints("2"), "0.02");
  assert.equal(R.deltaFromPoints("-1.5"), "-0.015");
  assert.equal(R.deltaFromPoints("abc"), null);
  assert.equal(R.disclosureOf(null), "No hypothetical-performance policy approved — staff use only");
  assert.equal(R.teText(null), "not estimated — needs prices");
  assert.deepEqual(R.giftControls(null, { actions: ["approve"] }), { submit: false, cancel: false, letter: false, markSent: false,
    markReceived: false, acknowledge: false, rebuy: false, approve: false, reject: false });
});
