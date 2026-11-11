// CPSIM03 — the custodian-API route on the instruction page (Send to custodian (simulated), the order's status and
// executions, Import fills, the reconcile picker) and Admin → Counterparties' Orders tab + the clock's "what the
// advance filled", rendered with react-dom/server from envelopes shaped exactly like routers/execution.py and
// routers/admin_counterparties.py return them. Every control FAILS CLOSED: Send / Import only when can_write is exactly
// true AND the server's actions list names them; a SIMULATED badge wherever a simulator order appears.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const ex = await import("../../lib/execution.mjs");
const D = await import("../../components/execution/InstructionDetail.jsx");
const C = await import("../../components/counterparties/Counterparties.jsx");
const L = await import("../../lib/counterparties.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());
const badges = (html) => (html.match(/data-testid="simulated-badge"/g) || []).length;

const VOCAB = {
  statuses: [{ key: "approved", label: "Approved" }, { key: "sent", label: "Sent" }, { key: "filled", label: "Filled" }],
  sides: [{ key: "buy", label: "Buy" }], price_kinds: [{ key: "market", label: "At market" }],
  executor_types: [{ key: "custodian_desk", label: "Custodian trading desk" }], if_not_filled: [{ key: "ask", label: "Ask me" }],
  sent_channels: [{ key: "email", label: "Email" }, { key: "custodian_api", label: "Custodian API (simulated)" }],
  manual_sent_channels: ["email"],
  reconcile_methods: [{ key: "custodian_data", label: "Matched to custodian data" }], actions: [], editable: [],
};
const WRITER = { can_read: true, can_write: true, can_approve: false, is_super_admin: false,
  read_permission: "view_execution", write_permission: "manage_execution" };
const VIEWER = { ...WRITER, can_write: false };
const ROW = { id: "i1", side: "buy", quantity: "10", notional: null, price_kind: "market", executor_type: "custodian_desk",
  execute_by: "2026-11-20", security: { name: "Fixture Co", ticker: "FXC" }, account: { account_number_masked: "*****0001" },
  maker_ids: ["u1"], filled_quantity: "0", lots: [], fills: [], timeline: [], tasks: [] };
const SIM = { routed_via: "custodian_api", custodian_order_id: null, simulated: true, send_available: true,
  send_refused: null, counterparty: "harbor-sim", import_available: false };
const env = (perms, actions, extra = {}, custodian = SIM) => ({
  instruction: { ...ROW, status: "approved", custodian, ...extra }, permissions: perms, actions, vocabularies: VOCAB,
});
const ORDER_VIEW = {
  simulated: true, reconcile_reference: "sim-o-0123456789abcdef", preselect_transaction_ids: ["t1"],
  order: { order_id: "sim-o-0123456789abcdef", status: "partially_filled", filled_quantity: "4", reject_reason: null,
    executions: [{ exec_id: "sim-e-aaaaaaaaaaaaaaaa", quantity: "4", price: "101.25", trade_date: "2026-11-13",
      settle_date: "2026-11-16", booked: true },
    { exec_id: "sim-e-bbbbbbbbbbbbbbbb", quantity: "2", price: "101.25", trade_date: "2026-11-16",
      settle_date: "2026-11-17", booked: false }] },
  synced_transactions: [{ id: "t1", external_ref: "sim-x-sim-e-aaaaaaaaaaaaaaaa", trade_date: "2026-11-13", quantity: "4",
    price: "101.25", same_security: true }, { id: "t2", external_ref: "sim-x-other", trade_date: "2026-11-13",
    quantity: "1", price: "5", same_security: false }],
};

test("Send to custodian (simulated): only for can_write === true AND the server's send_to_custodian action", () => {
  const yes = render(h(D.default, { initialEnvelope: env(WRITER, ["send_to_custodian", "mark_sent", "cancel"]) }));
  assert.ok(buttons(yes).includes("Send to custodian (simulated)"), buttons(yes).join("|"));
  assert.ok(badges(yes) >= 1, "the panel carries the SIMULATED badge");
  assert.match(text(yes), /simulated custodian harbor-sim/);
  for (const e of [env(VIEWER, ["send_to_custodian"]), env({ ...WRITER, can_write: "true" }, ["send_to_custodian"]),
    env(WRITER, []), env(undefined, ["send_to_custodian"]), { instruction: { ...ROW, status: "approved", custodian: SIM } }]) {
    const b = buttons(render(h(D.default, { initialEnvelope: e })));
    assert.ok(!b.includes("Send to custodian (simulated)"), `fail closed: ${JSON.stringify(e.permissions)} ${e.actions}`);
  }
});

test("a manual instruction says why it is manual; no Send, no badge", () => {
  const manual = { routed_via: "altruist", custodian_order_id: null, simulated: false, send_available: false,
    send_refused: "routed to manual execution — not a demo household: in a real organization only a household tagged demo may use a simulated custodian",
    counterparty: null, import_available: false };
  const html = render(h(D.default, { initialEnvelope: env(WRITER, ["mark_sent"], {}, manual) }));
  assert.match(text(html), /Manual: routed to manual execution — not a demo household/);
  assert.ok(!buttons(html).includes("Send to custodian (simulated)"));
  assert.equal(badges(html), 0);
  const none = render(h(D.default, { initialEnvelope: env(WRITER, ["mark_sent"], {}, null) }));
  assert.ok(!/custodian-panel/.test(none), "no custodian block without the server's custodian panel");
});

test("a simulated instruction whose route closed since routing shows the server's reason, no Send", () => {
  const closed = { ...SIM, send_available: false, send_refused: "the organization has no active connection to its simulated custodian" };
  const html = render(h(D.default, { initialEnvelope: env(WRITER, ["mark_sent", "cancel"], {}, closed) }));
  assert.match(text(html), /Send to custodian is not available: the organization has no active connection/);
  assert.ok(!buttons(html).includes("Send to custodian (simulated)"));
});

test("the order's status and executions (booked or not) and Import fills only with the server's import_fills", () => {
  const sent = { ...SIM, custodian_order_id: "sim-o-0123456789abcdef", send_available: false, import_available: true };
  const html = render(h(D.default, { initialEnvelope: env(WRITER, ["import_fills", "fill", "cancel"], { status: "sent",
    custodian_order_id: "sim-o-0123456789abcdef" }, sent), initialOrderView: ORDER_VIEW }));
  const t = text(html);
  assert.match(t, /Order sim-o-0123456789abcdef — partially_filled/);
  assert.match(t, /sim-e-aaaaaaaaaaaaaaaa 4 101\.25 2026-11-13 2026-11-16 Booked/);
  assert.match(t, /sim-e-bbbbbbbbbbbbbbbb 2 101\.25 2026-11-16 2026-11-17 Not yet imported/);
  assert.ok(buttons(html).includes("Import fills"));
  const viewer = render(h(D.default, { initialEnvelope: env(VIEWER, ["import_fills"], { status: "sent" }, sent),
    initialOrderView: ORDER_VIEW }));
  assert.ok(!buttons(viewer).includes("Import fills"), "no Import for a reader");
  const noAction = render(h(D.default, { initialEnvelope: env(WRITER, ["fill"], { status: "sent" }, sent),
    initialOrderView: ORDER_VIEW }));
  assert.ok(!buttons(noAction).includes("Import fills"), "no Import unless the server lists it");
});

test("Mark sent offers the MANUAL channels only — custodian API is recorded by Send to custodian", () => {
  const ch = ex.manualChannels(VOCAB).map((c) => c.key);
  assert.deepEqual(ch, ["email"]);
  assert.deepEqual(ex.manualChannels({ sent_channels: VOCAB.sent_channels }).map((c) => c.key), ["email"],
    "without the server's manual list, custodian_api is still never offered");
  assert.deepEqual(ex.manualChannels(null), []);
});

test("the reconcile picker preselects the server's same-security synced transactions; the reference is the order id", () => {
  const p = ex.reconcilePicker(ORDER_VIEW);
  assert.equal(p.reference, "sim-o-0123456789abcdef");
  assert.deepEqual(p.rows.map((r) => [r.id, r.checked]), [["t1", true], ["t2", false]]);
  const html = render(h(D.ReconcilePicker, { rows: p.rows, picked: new Set(["t1"]), setPicked: () => {} }));
  assert.match(html, /data-txn="t1"[^>]*checked|checked=""[^>]*data-txn="t1"/);
  assert.ok(!/checked=""[^>]*data-txn="t2"|data-txn="t2"[^>]*checked/.test(html), "t2 (another security) is not preselected");
  assert.ok(badges(html) >= 1, "the synced custodian transactions carry the SIMULATED badge");
  assert.deepEqual(ex.reconcilePicker(null), { rows: [], reference: "" });
});

test("a fill imported from the custodian carries the SIMULATED badge; a person's fill does not", () => {
  const fills = [{ id: "f1", quantity: "4", price: "101.25", filled_at: "2026-11-13T15:00:00Z", entered_by_name: "Sender",
    custodian_exec_id: "sim-e-aaaaaaaaaaaaaaaa" }, { id: "f2", quantity: "1", price: "100", filled_at: "2026-11-12T15:00:00Z",
    entered_by_name: "Pat", custodian_exec_id: null }];
  const html = render(h(D.Fills, { envelope: env(VIEWER, [], { status: "partially_filled", fills }) }));
  assert.equal(badges(html), 1);
});

test("Admin → Counterparties: the Orders tab (read-only) lists orders, outcome, executions, SIMULATED, and the v1 limits", () => {
  const orders = { simulated: true, permissions: { can_read: true, can_write: true, is_super_admin: true },
    rows: [{ order_id: "sim-o-0123456789abcdef", account_id: "acct-1", side: "buy", quantity: "10", notional: null,
      order_type: "limit", limit_price: "100", outcome: "partial", status: "partially_filled", reject_reason: null,
      created_at: "2026-11-13T15:00:00Z", executions: [{ exec_id: "sim-e-aaaaaaaaaaaaaaaa", quantity: "4", price: "99.5",
        trade_date: "2026-11-13" }] },
    { order_id: "sim-o-fedcba9876543210", account_id: "acct-1", side: "sell", quantity: "5", notional: null,
      order_type: "market", limit_price: null, outcome: "accept", status: "rejected", reject_reason: "insufficient position",
      created_at: "2026-11-13T15:01:00Z", executions: [] }],
    status_labels: [{ key: "partially_filled", label: "Partially filled" }, { key: "rejected", label: "Rejected" }],
    outcome_labels: [{ key: "partial", label: "Partial" }, { key: "accept", label: "Accept" }],
    v1_limits: ["Fills move positions and add buy / sell transactions — there is no cash ledger, no corporate actions and no fees (v2)."] };
  const html = render(h(C.OrdersTab, { id: "c1", initialOrders: orders }));
  const t = text(html);
  assert.match(t, /sim-o-0123456789abcdef SIMULATED acct-1 buy 10 limit 100 Partial Partially filled 4 @ 99\.5/);
  assert.match(t, /Rejected — insufficient position/);
  assert.match(t, /no cash ledger, no corporate actions and no fees/);
  assert.equal(buttons(html).length, 0, "read-only: no control at all");
  const empty = text(render(h(C.OrdersTab, { id: "c1", initialOrders: { rows: [] } })));
  assert.match(empty, /No orders yet/);
});

test("the clock tab shows what the advance filled; the Orders tab is a BOOK tab enabled for REST + OAuth types only", () => {
  const d = { clock: [{ business_date: "2026-11-16", reason: "advance" }] };
  const su = { permissions: { can_write: true } };
  const html = render(h(C.ClockTab, { env: su, d, act: () => {}, advance: { from: "2026-11-13", to: "2026-11-16",
    filled: [{ order_id: "sim-o-0123456789abcdef", status: "filled", executions: ["sim-e-cccccccccccccccc"], price: "102" }] } }));
  assert.match(text(html), /Advanced 2026-11-13 → 2026-11-16\. Working orders re-evaluated: 1\. sim-o-0123456789abcdef — filled · 1 execution\(s\) at 102/);
  const nothing = text(render(h(C.ClockTab, { env: su, d, act: () => {}, advance: { from: "a", to: "b", filled: [] } })));
  assert.match(nothing, /No working order filled/);
  const vocab = { tabs: [{ key: "clock", label: "Clock" }, { key: "orders", label: "Orders" }], book_tabs: ["orders"],
    book_institution_types: ["altruist"], institution_types: [{ key: "altruist", field_groups: ["clock"] },
      { key: "domestic_bank", field_groups: ["clock"] }] };
  const on = L.tabsFor({ vocabularies: vocab }, "altruist").find((x) => x.key === "orders");
  const off = L.tabsFor({ vocabularies: vocab }, "domestic_bank").find((x) => x.key === "orders");
  assert.equal(on.enabled, true);
  assert.equal(off.enabled, false);
  assert.equal(L.tabsFor({ vocabularies: { ...vocab, book_tabs: undefined } }, "altruist").find((x) => x.key === "orders").enabled,
    false, "a lost vocabulary keeps the Orders tab disabled");
});

test("custodianModel / orderExecutions fail closed on a missing or malformed panel", () => {
  assert.deepEqual(ex.custodianModel({}), { simulated: false, routedVia: null, orderId: null, counterparty: null,
    sendRefused: null });
  assert.equal(ex.custodianModel({ instruction: { custodian: { simulated: "true" } } }).simulated, false);
  assert.deepEqual(ex.orderExecutions(null), []);
  assert.equal(ex.instructionControls({ permissions: WRITER, actions: ["send_to_custodian"] }).sendToCustodian, true);
  assert.equal(ex.instructionControls({ permissions: WRITER, actions: [] }).importFills, false);
});
