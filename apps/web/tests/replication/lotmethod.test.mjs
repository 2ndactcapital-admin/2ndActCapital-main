// LOTMETHOD — the lot-method panels (household default, account override +
// custodian method), the proposal's per-sale method and each lot's reason,
// and the override / lot picker. The REAL components rendered with
// react-dom/server from envelopes shaped exactly as routers/lot_method.py and
// routers/account_models.py return them. Every change / approve / override /
// pick control FAILS CLOSED: none without the server's own flag being exactly
// true AND the action in the server's list (and, for the picker, "lots"
// editable). Labels come from the envelope's vocabularies.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const lib = await import("../../lib/lotMethod.mjs");
const { default: LotMethodPanel } = await import("../../components/lotmethod/LotMethodPanel.jsx");
const { default: ProposalLotMethod, LotPicker } = await import("../../components/lotmethod/ProposalLotMethod.jsx");
const { Trades } = await import("../../components/proposals/ProposalDetail.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/&gt;/g, ">").replace(/&lt;/g, "<")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());

const VOCAB = {
  methods: [{ key: "tax_optimized", label: "Tax-optimized — lowest estimated tax per dollar raised" },
    { key: "fifo", label: "FIFO — oldest first" }, { key: "specific", label: "Specific lots — the adviser picks them on each proposal" }],
  gain_preferences: [{ key: "prefer_long_term", label: "Prefer long-term" }, { key: "realize_gains", label: "Realize gains up to a target" }],
  custodian_methods: [{ key: "unknown", label: "Not on record" }, { key: "fifo", label: "FIFO" }],
  custodian_sources: [{ key: "custodian", label: "As reported by the custodian" }, { key: "adviser", label: "Entered by the adviser" }],
  sources: [{ key: "default", label: "No method set — today's rule" }, { key: "account", label: "Set on this account" },
    { key: "household", label: "The household's default" }, { key: "proposal", label: "Chosen on this proposal" }],
  statuses: [{ key: "draft", label: "Awaiting a second person's approval" }, { key: "approved", label: "Approved" }],
  line_methods: [{ key: "fifo", label: "FIFO — oldest first" }, { key: "harvest_loss", label: "Tax-loss harvest — each qualifying loss lot, whole" }],
  today_rule: "today's rule: losses first", precedence: "wash-sale holds and hard blocks > the guards > the method",
  editable: ["lot_method", "gain_preference", "realize_gains_target", "reason"], inline_editable: [],
};
const P_VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false, read_permission: "view_proposals",
  write_permission: "manage_proposals", approve_permission: "approve_proposals" };
const P_ALL = { ...P_VIEW, can_write: true, can_approve: true };
const BAD = [P_VIEW, undefined, {}, { ...P_ALL, can_write: "true", can_approve: "true" }];
const change = (actions) => ({ id: "c1", kind: "household", lot_method: "fifo", gain_preference: "prefer_long_term", reason: "member asked",
  status: "draft", maker_id: "u1", actions });
const hhEnv = (permissions, actions = ["request_change"], changeActions = ["approve", "reject"], editable = VOCAB.editable) => ({
  household: { id: "h1", name: "ZZ Household" },
  current: { lot_method: "tax_optimized", lot_method_source: "default", gain_preference: "prefer_long_term", today_rule: true, mismatch: null },
  history: [change(changeActions)], pending: change(changeActions), actions, permissions, vocabularies: { ...VOCAB, editable },
});

test("lot-method panel: no Request / Approve / Reject without the flags AND the actions", () => {
  for (const p of BAD) {
    const b = buttons(render(h(LotMethodPanel, { initialEnvelope: hhEnv(p), kind: "household" })));
    for (const w of ["Request a change", "Approve", "Reject"]) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(p)}: ${b}`);
  }
  const none = buttons(render(h(LotMethodPanel, { initialEnvelope: hhEnv(P_ALL, [], []), kind: "household" })));
  assert.deepEqual(none.filter((x) => ["Request a change", "Approve", "Reject"].includes(x)), [], "no action listed → no control");
  const empty = buttons(render(h(LotMethodPanel, { initialEnvelope: hhEnv(P_ALL, ["request_change"], [], []), kind: "household" })));
  assert.ok(!empty.includes("Request a change"), "an empty editable list → no request control");
  const all = buttons(render(h(LotMethodPanel, { initialEnvelope: hhEnv(P_ALL), kind: "household" })));
  for (const w of ["Request a change", "Approve", "Reject"]) assert.ok(all.includes(w), `${w} with the flags + actions: ${all}`);
});

test("lot-method panel: labels from the envelope; no method set says today's rule; the mismatch shows", () => {
  const t = text(render(h(LotMethodPanel, { initialEnvelope: hhEnv(P_VIEW), kind: "household" })));
  assert.ok(t.includes("Tax-optimized — lowest estimated tax per dollar raised"), t);
  assert.ok(t.includes("No method set — today's rule") && t.includes("today's rule: losses first"), t);
  assert.ok(t.includes("wash-sale holds and hard blocks > the guards > the method"), "precedence from the envelope");
  const acct = { account: { id: "a1", account_number_masked: "*****0001" }, history: [], pending: null, actions: [],
    permissions: P_VIEW, vocabularies: VOCAB,
    effective: { lot_method: "fifo", lot_method_source: "account", gain_preference: "prefer_long_term", custodian_cost_basis_method: "unknown",
      custodian_method_source: null, average_cost_security_ids: ["s1"], mismatch: { code: "lot_method_mismatch", message: "MISMATCH-TEXT" } } };
  const a = text(render(h(LotMethodPanel, { initialEnvelope: acct, kind: "account" })));
  assert.ok(a.includes("FIFO — oldest first"), a);
  assert.ok(a.includes("Set on this account"), a);
  assert.ok(a.includes("Not on record"), a);
  assert.ok(a.includes("1 security(ies) at average cost") && a.includes("MISMATCH-TEXT"), a);
});

const propEnv = (permissions, actions) => ({
  proposal: { id: "p1", lot_method: "fifo", lot_method_source: "account", trades: [
    { id: "t1", side: "sell", quantity: "10", global_security_id: "s1", security: { name: "ZZ FUND", ticker: "ZZF" }, price: "100",
      price_source: "account_position", est_st_gain: "0", est_lt_gain: "50", reason: "Trim", lot_method: "fifo",
      lots: [{ lot_id: "l1", quantity: "10", unit_cost: "95", term: "long", est_gain: "50", acquired_date: "2020-01-02", why: "oldest first (acquired 2020-01-02)" }] }] },
  permissions, actions, vocabularies: { price_sources: [], lot_method: VOCAB },
});

test("proposal: each sale states its method (label from the envelope) and why each lot was chosen", () => {
  const t = text(render(h(Trades, { envelope: propEnv(P_VIEW, []) })));
  assert.ok(t.includes("Lots: FIFO — oldest first"), t);
  assert.ok(t.includes("oldest first (acquired 2020-01-02)"), t);
});

test("proposal override: no control without can_write === true AND override_lot_method listed", () => {
  for (const p of BAD) {
    assert.equal(render(h(ProposalLotMethod, { envelope: propEnv(p, ["override_lot_method"]) })), "", `${JSON.stringify(p)}`);
  }
  assert.equal(render(h(ProposalLotMethod, { envelope: propEnv(P_ALL, ["submit"]) })), "", "not listed → nothing");
  assert.equal(render(h(ProposalLotMethod, { envelope: undefined })), "", "a lost envelope → nothing");
  const b = buttons(render(h(ProposalLotMethod, { envelope: propEnv(P_ALL, ["override_lot_method"]) })));
  assert.ok(b.includes("Choose the lot method"), `${b}`);
});

const lotsEnv = (permissions, actions, editable) => ({
  proposal: { id: "p1", status: "draft" }, actions, permissions, vocabularies: { ...VOCAB, editable },
  lines: [{ global_security_id: "s1", label: "ZZ FUND", line_quantity: "10", lots: [
    { lot_id: "l1", quantity: "10", unit_cost: "95", term: "long", acquired_date: "2020-01-02", est_gain: "50" }] }],
});

test("lot picker: only with can_write === true, the action AND 'lots' editable", () => {
  for (const p of BAD) assert.equal(render(h(LotPicker, { lotsEnvelope: lotsEnv(p, ["override_lot_method"], ["lots"]), picks: {}, onPick() {} })), "");
  assert.equal(render(h(LotPicker, { lotsEnvelope: lotsEnv(P_ALL, [], ["lots"]), picks: {}, onPick() {} })), "");
  assert.equal(render(h(LotPicker, { lotsEnvelope: lotsEnv(P_ALL, ["override_lot_method"], []), picks: {}, onPick() {} })), "");
  const html = render(h(LotPicker, { lotsEnvelope: lotsEnv(P_ALL, ["override_lot_method"], ["lots"]), picks: {}, onPick() {} }));
  assert.ok(text(html).includes("ZZ FUND") && html.includes("quantity for lot l1"), html.slice(0, 200));
});

test("bodies: decimals stay strings; a target only with realize_gains; picks only for specific", () => {
  assert.deepEqual(lib.overrideBody({ lot_method: "specific", picks: { "s1|l1": "5", "s1|l2": "" } }),
    { lot_method: "specific", lots: [{ global_security_id: "s1", lot_id: "l1", quantity: "5" }] });
  assert.deepEqual(lib.overrideBody({ lot_method: "fifo", picks: { "s1|l1": "5" } }), { lot_method: "fifo" });
  assert.deepEqual(lib.overrideBody({ gain_preference: "realize_gains", realize_gains_target: "12000.50" }),
    { gain_preference: "realize_gains", realize_gains_target: "12000.50" });
  assert.equal(lib.overrideBody({ gain_preference: "realize_gains", realize_gains_target: "1e3" }).realize_gains_target, null,
    "never a float-ish string");
  const a = lib.accountChangeBody({ custodian_cost_basis_method: "unknown", custodian_method_source: "adviser", reason: " r " });
  assert.equal(a.custodian_method_source, null, "unknown custodian method carries no source");
  assert.equal(a.reason, "r");
});
