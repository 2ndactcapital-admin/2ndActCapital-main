// CPSIM04 — the custodian-API route on the payment page (Send to custodian (simulated), the transfer's custodian events,
// Refresh status from custodian, the reconcile picker preselecting the transfer's matched feed rows, the reason a
// payment stays manual) and Admin → Counterparties' Transfers tab (a custodian: received transfers, statuses, Record
// member consent, returns, the cash ledger) and Received tab (a bank: what it received, last-four references only, the
// linked platform accounts), rendered with react-dom/server from envelopes shaped exactly like routers/payments.py and
// routers/admin_counterparties.py return them. Every control FAILS CLOSED; SIMULATED badges wherever a transfer shows.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/cashPayments.mjs");
const P = await import("../../components/treasury/TreasuryPayments.jsx");
const C = await import("../../components/counterparties/Counterparties.jsx");
const CL = await import("../../lib/counterparties.mjs");

const html = (el) => renderToStaticMarkup(el);
const text = (s) => s.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"')
  .replace(/\s+/g, " ");
const buttons = (s) => [...s.matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());
const badges = (s) => (s.match(/data-testid="simulated-badge"/g) || []).length;

const STAFF = { can_read: true, can_write: true, can_approve: true, is_super_admin: false, read_permission: "view_portfolio",
  write_permission: "initiate_payment", approve_permission: "approve_payment" };
const VIEWER = { ...STAFF, can_write: false, can_approve: false, write_permission: null, approve_permission: null };
const VOCAB = { statuses: [], rails: [], channels: [{ key: "email", label: "Email to the custodian" }], reconcile_methods: [],
  purpose_links: [], editable: [], inline_editable: [] };
const ACC = (n) => ({ id: `a-${n}`, account_number_masked: `****${n}`, custodian_label: "ALTRUIST", owner_id: "e", owner_name: "Trust" });
const PANEL = { simulated: true, adapter: null, transfer_id: null, send_available: true, send_refused: null,
  refresh_available: false, counterparty: "harbor-sim", manual_offered: true };
const PAY = (over = {}, custodian = PANEL) => ({ id: "p-1", household_id: "h-1", status: "approved", status_label: "Approved — not sent",
  amount: "2500.00", rail: "journal", rail_label: "Journal between accounts", value_date: "2026-11-16", purpose: "Fund taxes",
  source: ACC("0001"), destination: ACC("0002"), timeline: [], custodian, actions: [], ...over });
const DETAIL = (perms, payment) => ({ household: { id: "h-1" }, member_view: false, labels: {}, permissions: perms,
  vocabularies: VOCAB, payment });
const SEND = { key: "send_to_custodian", label: "Send to custodian (simulated)" };
const REFRESH = { key: "refresh_from_custodian", label: "Refresh status from custodian" };
const MANUAL = { key: "mark_sent", label: "Confirm sent" };
const SHEET = { key: "sheet", label: "Download payment sheet" };

test("Send to custodian (simulated): only for can_write === true AND the server's action AND send_available", () => {
  const yes = html(h(P.PaymentDetail, { data: DETAIL(STAFF, PAY({ actions: [SEND, MANUAL, SHEET] })) }));
  const b = buttons(yes);
  assert.ok(b.includes("Send to custodian (simulated)"), b.join("|"));
  assert.ok(b.includes("Confirm sent") && b.includes("Download payment sheet"), "the manual route stays offered beside it");
  assert.equal(b.filter((x) => x === "Send to custodian (simulated)").length, 1, "Send is rendered once (the panel), not twice");
  assert.ok(badges(yes) >= 1, "the panel carries the SIMULATED badge");
  assert.match(text(yes), /simulated custodian harbor-sim/);
  for (const d of [DETAIL(VIEWER, PAY({ actions: [SEND] })), DETAIL({ ...STAFF, can_write: "true" }, PAY({ actions: [SEND] })),
    DETAIL(STAFF, PAY({ actions: [] })), DETAIL(STAFF, PAY({ actions: [SEND] }, { ...PANEL, send_available: false })),
    DETAIL(undefined, PAY({ actions: [SEND] })), { ...DETAIL(STAFF, PAY({ actions: [SEND] })), member_view: true }]) {
    const out = buttons(html(h(P.PaymentDetail, { data: d })));
    assert.ok(!out.includes("Send to custodian (simulated)"), `fail closed: ${JSON.stringify(d.permissions)}`);
  }
});

test("a payment that stays manual says why; no Send; no panel for a non-simulated payment", () => {
  const why = "manual: the organization has not enabled the simulated custodian's payment adapter";
  const s = html(h(P.PaymentDetail, { data: DETAIL(STAFF, PAY({ actions: [MANUAL, SHEET] },
    { ...PANEL, send_available: false, send_refused: why })) }));
  assert.match(text(s), /Manual: the organization has not enabled the simulated custodian's payment adapter/);
  assert.ok(!buttons(s).includes("Send to custodian (simulated)"));
  const none = html(h(P.PaymentDetail, { data: DETAIL(STAFF, PAY({ actions: [MANUAL] }, null)) }));
  assert.ok(!/custodian-panel/.test(none), "no custodian panel without the server's simulated panel");
  assert.equal(badges(none), 0);
});

test("Refresh status from custodian: initiate OR approve, the server's action, refresh_available; the custodian events", () => {
  const sent = { ...PANEL, transfer_id: "sim-t-0123456789abcdef", send_available: false, refresh_available: true };
  const view = { transfer: { transfer_id: "sim-t-0123456789abcdef", status: "settled",
    events: [{ status: "received", on: "2026-11-16" }, { status: "acknowledged", on: "2026-11-16" },
      { status: "settled", on: "2026-11-16" }] } };
  const approverOnly = { ...STAFF, can_write: false };
  for (const perms of [STAFF, approverOnly]) {
    const s = html(h(P.PaymentDetail, { data: DETAIL(perms, PAY({ status: "sent", actions: [REFRESH] }, sent)), transferView: view }));
    assert.ok(buttons(s).includes("Refresh status from custodian"), JSON.stringify(perms));
    assert.match(text(s), /Transfer sim-t-0123456789abcdef/);
    assert.deepEqual([...s.matchAll(/data-custodian-event="([a-z_]+)"/g)].map((m) => m[1]), ["received", "acknowledged", "settled"]);
  }
  for (const d of [DETAIL(VIEWER, PAY({ status: "sent", actions: [REFRESH] }, sent)),
    DETAIL(STAFF, PAY({ status: "sent", actions: [] }, sent)),
    DETAIL(STAFF, PAY({ status: "sent", actions: [REFRESH] }, { ...sent, refresh_available: false }))]) {
    assert.ok(!buttons(html(h(P.PaymentDetail, { data: d }))).includes("Refresh status from custodian"));
  }
});

test("the reconcile picker preselects the transfer's matched feed rows only", () => {
  const matches = { matches: [
    { id: "t-out", leg: "out", type: "sell", date: "2026-11-16", amount: "2500.00", external_ref: "sim-t-0123456789abcdef-out", source_system: "altruist_simulator" },
    { id: "t-in", leg: "in", type: "buy", date: "2026-11-16", amount: "2500.00", external_ref: "sim-t-0123456789abcdef-in", source_system: "altruist_simulator" },
    { id: "t-other", leg: "out", type: "sell", date: "2026-11-16", amount: "2500.00", external_ref: "x-1", source_system: "altruist" }] };
  const view = { preselect_transaction_ids: ["t-out", "t-in", "t-gone"], transfer: { events: [] } };
  assert.deepEqual(L.preselected(view, matches), ["t-out", "t-in"]);
  assert.deepEqual(L.preselected(null, matches), []);
  const s = html(h(P.PaymentDetail, { data: DETAIL(STAFF, PAY({ status: "settled", actions: [{ key: "reconcile", label: "Reconcile" }] },
    { ...PANEL, transfer_id: "sim-t-0123456789abcdef", send_available: false })), matches, transferView: view }));
  const checked = [...s.matchAll(/<input type="checkbox"( checked="")?/g)].map((m) => Boolean(m[1]));
  assert.deepEqual(checked, [true, true, false], "the two legs preselected, the other row not");
  assert.ok(badges(s) >= 2, "simulated feed rows carry the badge");
});

test("custodianPanel fails closed on a lost or partial read", () => {
  assert.equal(L.custodianPanel(null), null);
  assert.equal(L.custodianPanel(DETAIL(STAFF, PAY({}, { ...PANEL, simulated: "true" }))), null);
  assert.equal(L.custodianPanel({ ...DETAIL(STAFF, PAY({ actions: [SEND] })), permissions: undefined }), null);
  const c = L.custodianPanel(DETAIL(STAFF, PAY({ actions: [SEND] })));
  assert.equal(c.sendOffered, true);
  assert.deepEqual(L.CUSTODIAN_PATHS, { send_to_custodian: "send-to-custodian", refresh_from_custodian: "refresh-from-custodian" });
});

// ── Admin → Counterparties ─────────────────────────────────────────────────────────────────────────────────────────
const TSTAT = [{ key: "received", label: "Received" }, { key: "pending_consent", label: "Awaiting the member's consent" },
  { key: "acknowledged", label: "Acknowledged" }, { key: "settled", label: "Settled" }, { key: "returned", label: "Returned" },
  { key: "rejected", label: "Rejected" }];
const SU = { can_read: true, can_write: true, is_super_admin: true };
const T = (over) => ({ transfer_id: "sim-t-aaaaaaaaaaaaaaaa", client_transfer_id: "p-1", from_account_id: "acct-1",
  to_account_id: null, to_bank: { institution_slug: "harbor-bank", account_id: "bk-1", routing_last4: "4321" }, amount: "2500.00",
  rail: "ach", value_date: "2026-11-16", status: "returned", reject_reason: null, return_code: "R01",
  events: [{ status: "received", on: "2026-11-16" }, { status: "acknowledged", on: "2026-11-16" }, { status: "settled", on: "2026-11-17" },
    { status: "returned", on: "2026-11-19" }], ...over });

test("Transfers tab: received transfers, statuses, returns with their code, the cash ledger, SIMULATED badges", () => {
  const env = { permissions: SU, rows: [T({}), T({ transfer_id: "sim-t-bbbbbbbbbbbbbbbb", status: "pending_consent", return_code: null,
    events: [{ status: "received", on: "2026-11-16" }, { status: "pending_consent", on: "2026-11-16" }] })],
  status_labels: TSTAT, v1_limits: ["USD only."], behaviour: { consent_required: true },
  cash_movements: [{ account_id: "acct-1", kind: "return_credit", source_ref: "sim-t-aaaaaaaaaaaaaaaa", amount: "2500", balance_after: "10000", effective_date: "2026-11-19" }] };
  const s = html(h(C.TransfersTab, { id: "c1", initialTransfers: env }));
  const t = text(s);
  assert.match(t, /Returned — R01/);
  assert.match(t, /harbor-bank · bk-1 \(routing ····4321\)/);
  assert.ok(badges(s) >= 2);
  assert.ok(/data-testid="cash-ledger"/.test(s) && /return_credit/.test(s));
  assert.match(t, /Member consent is required/);
  assert.deepEqual(buttons(s).filter((b) => b === "Record member consent").length, 1, "consent only on the pending transfer");
  const ro = html(h(C.TransfersTab, { id: "c1", initialTransfers: { ...env, permissions: { ...SU, can_write: false } } }));
  assert.ok(!buttons(ro).includes("Record member consent"), "no consent control without can_write === true");
});

test("Received tab (a bank): what it received, last four only, the linked platform accounts", () => {
  const env = { permissions: SU, slug: "harbor-bank", status_labels: TSTAT,
    rows: [{ ...T({ status: "settled", return_code: null }), sender: "harbor-sim" },
      { ...T({ transfer_id: "sim-t-cccccccccccccccc", status: "rejected", reject_reason: "the routing number does not match the receiving bank's ACH routing number" }), sender: "harbor-sim" }],
    links: [{ org_id: "o1", org_name: "Fixture org", account_id: "a-9", account_number_masked: "****7788", bank_account_id: "bk-1",
      references: { ach_routing_last4: "4321", wire_routing_last4: "8765" } }], cash_movements: [] };
  const s = html(h(C.ReceivedTab, { id: "b1", initialReceived: env }));
  const t = text(s);
  assert.match(t, /harbor-sim/);
  assert.match(t, /Rejected — the routing number does not match/);
  assert.match(t, /account ····7788 → bk-1/);
  assert.match(t, /ach routing last4 ····4321/);
  assert.ok(!/\d{5,}/.test(t.replace(/sim-t-[0-9a-f]{16}/g, "").replace(/\d{4}-\d{2}-\d{2}/g, "")), "no run of 5+ digits (no full number)");
  assert.ok(badges(s) >= 2);
});

test("tabs: Transfers for a REST + OAuth custodian, Received + Import / Export for a bank (server vocabularies; fail closed)", () => {
  const vocab = { tabs: [{ key: "connection", label: "Connection" }, { key: "import_export", label: "Import / Export" },
    { key: "orders", label: "Orders" }, { key: "transfers", label: "Transfers" }, { key: "received", label: "Received" }],
    institution_types: [{ key: "altruist", field_groups: ["connection"] }, { key: "domestic_bank", field_groups: ["connection", "bank_domestic"] }],
    book_tabs: ["import_export", "orders", "transfers"], book_institution_types: ["altruist"],
    bank_tabs: ["import_export", "received"], bank_institution_types: ["domestic_bank", "international_bank"] };
  const on = (type, v = vocab) => CL.tabsFor({ vocabularies: v }, type).filter((t) => t.enabled).map((t) => t.key);
  assert.deepEqual(on("altruist"), ["connection", "import_export", "orders", "transfers"]);
  assert.deepEqual(on("domestic_bank"), ["connection", "import_export", "received"]);
  assert.deepEqual(on("domestic_bank", { ...vocab, bank_tabs: undefined }), ["connection"], "a lost bank vocabulary disables them");
  assert.equal(CL.lastFourRef("991234567"), "····4567");
  assert.equal(CL.consentOffered({ permissions: { can_write: true } }, { status: "pending_consent" }), true);
  assert.equal(CL.consentOffered({ permissions: { can_write: "true" } }, { status: "pending_consent" }), false);
});
