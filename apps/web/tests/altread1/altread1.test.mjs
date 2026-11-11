// ALTREAD1 — the account page's custodian section (balance as of its date, custodian lots, the reconciliation with
// differences highlighted), the lot-source setting on the lot-method account change (gated exactly as the lot-method
// panel: can_write === true AND request_change AND lot_source editable), and the connection status's refresh-token
// expiry — rendered with react-dom/server from envelopes shaped exactly like routers/custodian_feed.py,
// routers/lot_method.py and routers/admin_counterparties.py return them. Every control FAILS CLOSED.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const F = await import("../../lib/custodianFeed.mjs");
const LM = await import("../../lib/lotMethod.mjs");
const Panel = (await import("../../components/custodian/CustodianFeedPanel.jsx")).default;
const LMP = await import("../../components/lotmethod/LotMethodPanel.jsx");
const C = await import("../../components/counterparties/Counterparties.jsx");

const html = (el) => renderToStaticMarkup(el);
const text = (s) => s.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const count = (s, id) => (s.match(new RegExp(`data-testid="${id}"`, "g")) || []).length;

const LOT_SOURCES = [{ key: "hollisworks", label: "Hollisworks-computed lots (default)" },
  { key: "custodian", label: "The custodian's reported lots" }];
const FEED = {
  account_id: "a-1",
  balances: [{ source_system: "altruist", environment: "sandbox", account_balance: "125430.17", as_of_date: "2026-11-10",
    custodian_status: "ACTIVE", fetched_at: "2026-11-11T06:00:00+00:00" }],
  lots: [{ id: "l-1", external_id: "lot-9", source_system: "altruist", environment: "sandbox", global_security_id: "s-1",
    label: "Example ETF", cusip: "000000000", symbol: "EXMPL", quantity: "6", market_value: "600", cost_basis_unadjusted: "500",
    cost_basis_adjusted: "510", unrealized_gain_loss: "90", open_date: "2024-11-11", as_of_date: "2026-11-10" }],
  reconciliation: { rows: [
    { key: "s-1", label: "Example ETF", status: "differs", status_label: "Differs",
      hollisworks: { quantity: "10", basis: "900", lots: 2 }, custodian: { quantity: "6", basis: "510", lots: 1 },
      quantity_difference: "-4", basis_difference: "-390", quantity_differs: true, basis_differs: true, lot_count_differs: true },
    { key: "s-2", label: "Other fund", status: "match", status_label: "Matches",
      hollisworks: { quantity: "3", basis: "30", lots: 1 }, custodian: { quantity: "3", basis: "30", lots: 1 },
      quantity_difference: "0", basis_difference: "0", quantity_differs: false, basis_differs: false, lot_count_differs: false }],
  differences: 1, hollisworks_lots: 3, custodian_lots: 1 },
  lot_source: "hollisworks", lot_source_label: "Hollisworks-computed lots (default)",
  permissions: { can_read: true, can_write: false, is_super_admin: false, read_permission: "view_proposals", write_permission: null },
  vocabularies: { editable: [], inline_editable: [], lot_sources: LOT_SOURCES },
};

test("custodian balance renders with its as-of date; lots and the reconciliation render; differences are highlighted", () => {
  const out = html(h(Panel, { envelope: FEED }));
  assert.match(text(out), /Custodian balance \(as of 2026-11-10\) 125430\.17/);
  assert.equal(count(out, "custodian-balance"), 1);
  assert.equal(count(out, "custodian-lots"), 1);
  assert.match(text(out), /Example ETF 2024-11-11 6 510 500 2026-11-10/);
  assert.equal(count(out, "recon-differs"), 1, "the planted quantity / basis difference is highlighted");
  assert.equal(count(out, "recon-match"), 1, "a matching security is not highlighted");
  assert.match(text(out), /-4/); assert.match(text(out), /-390/);
  assert.match(text(out), /Lots used by the tax engine: Hollisworks-computed lots \(default\)/);
  assert.ok(!/<button/.test(out), "the custodian panel has no write control at all");
});

test("the custodian panel fails closed: no envelope, no can_read === true → nothing", () => {
  for (const env of [null, undefined, {}, { ...FEED, permissions: undefined }, { ...FEED, permissions: { can_read: "true" } },
    { ...FEED, permissions: { can_read: false } }]) {
    assert.equal(html(h(Panel, { envelope: env })), "", JSON.stringify(env?.permissions));
  }
  const none = html(h(Panel, { envelope: { ...FEED, balances: [], lots: [], reconciliation: { rows: [] } } }));
  assert.equal(count(none, "custodian-balance-none"), 1);
  assert.equal(count(none, "custodian-lots-none"), 1);
});

const LM_ENV = (perms, editable, actions = ["request_change"]) => ({
  account: { id: "a-1" }, effective: { lot_method: "fifo", lot_method_source: "default", gain_preference: "prefer_long_term",
    custodian_cost_basis_method: "unknown", lot_source: "custodian", today_rule: true },
  history: [], actions, permissions: perms,
  vocabularies: { methods: [{ key: "fifo", label: "FIFO" }], gain_preferences: [{ key: "prefer_long_term", label: "Prefer LT" }],
    custodian_methods: [{ key: "unknown", label: "Unknown" }], custodian_sources: [], sources: [], statuses: [],
    lot_sources: LOT_SOURCES, editable, inline_editable: [] },
});
const WRITER = { can_read: true, can_write: true, can_approve: false, is_super_admin: false };
const EDITABLE = ["lot_method_override", "custodian_cost_basis_method", "lot_source", "reason"];

test("the lot-source setting: offered only to can_write === true + request_change + lot_source editable", () => {
  assert.equal(LM.lotSourceControl(LM_ENV(WRITER, EDITABLE)), true);
  const form = html(h(LMP.RequestForm, { envelope: LM_ENV(WRITER, EDITABLE), kind: "account", onSubmit: () => {} }));
  assert.equal(count(form, "lot-source-select"), 1);
  assert.match(text(form), /The custodian's reported lots/);
  for (const env of [LM_ENV({ ...WRITER, can_write: false }, EDITABLE), LM_ENV({ ...WRITER, can_write: "true" }, EDITABLE),
    LM_ENV(WRITER, EDITABLE.filter((k) => k !== "lot_source")), LM_ENV(WRITER, EDITABLE, []), LM_ENV(undefined, EDITABLE),
    { ...LM_ENV(WRITER, EDITABLE), vocabularies: { ...LM_ENV(WRITER, EDITABLE).vocabularies, lot_sources: undefined } }]) {
    assert.equal(LM.lotSourceControl(env), false, JSON.stringify(env.permissions));
    const out = html(h(LMP.RequestForm, { envelope: env, kind: "account", onSubmit: () => {} }));
    assert.equal(count(out, "lot-source-select"), 0, "fails closed");
  }
  const panel = html(h(LMP.default, { initialEnvelope: LM_ENV(WRITER, EDITABLE), kind: "account" }));
  assert.match(text(panel), /Lots used by the tax engine: The custodian's reported lots/);
});

test("the change body carries lot_source only when chosen (omitted → the server keeps the current one)", () => {
  const base = { reason: "use the custodian's lots", custodian_cost_basis_method: "unknown" };
  assert.equal("lot_source" in LM.accountChangeBody(base), false);
  assert.equal(LM.accountChangeBody({ ...base, lot_source: "custodian" }).lot_source, "custodian");
});

test("connection status shows the refresh-token expiry; an expired one says reconnect", () => {
  assert.deepEqual(F.refreshExpiryText({ refresh_token_expires_at: "2026-12-09T10:00:00+00:00", refresh_token_expired: false }),
    { expired: false, text: "Refresh token expires 2026-12-09" });
  assert.equal(F.refreshExpiryText({ refresh_token_expires_at: "2026-11-01T00:00:00+00:00", refresh_token_expired: true }).expired, true);
  assert.equal(F.refreshExpiryText(null), null);
  const env = { rows: [
    { id: "o-1", name: "Test org", is_test: true, points_here: true, counterparty_slug: "harbor", connection_status: "connected",
      refresh_token_expires_at: "2026-11-01T00:00:00+00:00", refresh_token_expired: true, last_sync: null },
    { id: "o-2", name: "Other org", is_test: true, points_here: true, counterparty_slug: "harbor", connection_status: "connected",
      refresh_token_expires_at: "2026-12-09T00:00:00+00:00", refresh_token_expired: false, last_sync: null }],
  permissions: { can_read: true, can_write: false }, vocabularies: {} };
  const out = html(h(C.ConnectionsTab, { id: "cp-1", initialConnections: env }));
  assert.equal(count(out, "refresh-expired"), 1);
  assert.match(text(out), /Refresh token expired 2026-11-01 — reconnect to authorize again/);
  assert.match(text(out), /Refresh token expires 2026-12-09/);
});
