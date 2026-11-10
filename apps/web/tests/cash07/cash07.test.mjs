// CASH07 — Household → Treasury → Payments: the REAL components rendered with react-dom/server from reads shaped
// exactly like routers/payments.py returns them (services/payments.payment_out / detail / options): the list (filters,
// the server's own row actions), the new-instruction form's limits (eligible sources only — the others with both
// refusal reasons; the chosen source's same-owner destinations; its declared rails), the detail with its lifecycle
// timeline, the sheet / approve controls only where the server lists them, the member's read-only view (last four only,
// the cash.payments disclosure), the member overview's sweep-rate label, and every screen FAILING CLOSED on a lost read.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/cashPayments.mjs");
const P = await import("../../components/treasury/TreasuryPayments.jsx");
const TreasuryPayments = P.default;
const { PaymentList, NewPaymentForm, PaymentDetail } = P;
const Overview = (await import("../../components/treasury/TreasuryOverview.jsx")).default;

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/\s+/g, " ");
const html = (el) => renderToStaticMarkup(el);
const render = (el) => text(html(el));
const buttons = (el) => [...html(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());
const options = (el, label) => {
  const m = html(el).match(new RegExp(`<select[^>]*aria-label="${label}"[^>]*>([\\s\\S]*?)</select>`));
  return m ? [...m[1].matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((x) => [x[1], x[2]]) : null;
};

const DISC = { "cash.payments": { key: "cash.payments", text: "ACH settles provisionally (library)." } };
const STAFF = { can_read: true, can_write: true, can_approve: true, is_super_admin: false, read_permission: "view_portfolio",
  write_permission: "initiate_payment", approve_permission: "approve_payment" };
const VOCAB = {
  statuses: [{ key: "draft", label: "Draft (srv)" }, { key: "pending_approval", label: "Awaiting approval (srv)" },
    { key: "settled", label: "Settled (srv)" }],
  rails: [{ key: "ach", label: "ACH (srv)" }, { key: "wire", label: "Wire (srv)" }],
  channels: [{ key: "custodian_portal", label: "Custodian portal (srv)" }, { key: "email", label: "Email (srv)" }],
  reconcile_methods: [], purpose_links: [], editable: ["amount"], inline_editable: [],
};
const ACC = (n, owner = "Trust A") => ({ id: `a-${n}`, account_number_masked: `****${n}`, custodian_label: "custx",
  owner_id: "e-1", owner_name: owner });
const ITEM = (over = {}) => ({ id: "p-1", household_id: "h-1", status: "pending_approval",
  status_label: "Awaiting a second person's approval (srv)", amount: "25000.00", currency_code: "USD", rail: "ach",
  rail_label: "ACH (srv)", value_date: "2026-11-12", purpose: "Fund the property tax", source: ACC("5001"),
  destination: ACC("5002"), provisional: false, provisional_until: null, actions: [], ...over });
const LIST = (over = {}) => ({ household: { id: "h-1" }, member_view: false, labels: DISC, permissions: STAFF,
  vocabularies: VOCAB, today: "2026-11-09", items: [ITEM({ actions: [{ key: "cancel", label: "Cancel (srv)" }] }),
    ITEM({ id: "p-2", status: "settled", status_label: "Settled (srv)", provisional: true, provisional_until: "2026-11-13" })],
  actions: [{ key: "new", label: "New payment instruction" }], ...over });
const OPTIONS = (over = {}) => ({ household: { id: "h-1" }, member_view: false, labels: DISC, permissions: STAFF,
  vocabularies: VOCAB, today: "2026-11-09", next_business_day: "2026-11-10",
  value_date_rule: "a US business day, today or later (srv).",
  sources: [
    { ...ACC("5001"), eligible: true, reasons: [], rails: [{ key: "ach", label: "ACH (srv)" }],
      destinations: [ACC("5002")] },
    { ...ACC("5003"), eligible: false, reasons: ["trading authority (custody): tier 'inquiry' … (srv)",
      "account authority: ****5003 (authority advise …) (srv)"], rails: [], destinations: [ACC("5004")] },
  ],
  links: [{ type: "cash_obligation", id: "o-1", label: "Property tax — due 2026-12-01 ($25,000.00)" }], ...over });
const DETAIL = (pay = {}, over = {}) => ({ household: { id: "h-1" }, member_view: false, labels: DISC, permissions: STAFF,
  vocabularies: VOCAB, payment: ITEM({ timeline: [
    { id: "e1", action: "create", label: "Drafted (srv)", to_label: "Draft", at: "2026-11-09T14:00:00+00:00", who: "Ann" },
    { id: "e2", action: "submit", label: "Submitted for approval (srv)", to_label: "Awaiting approval", at: "2026-11-09T14:05:00+00:00", who: "Ann" },
  ], ...pay }), ...over });

test("list: rows from the server (last four only), filters from its vocabularies, the New offer, the disclosure", () => {
  const el = h(PaymentList, { data: LIST(), filters: {} });
  const t = render(el);
  assert.ok(t.includes("$25,000.00") && t.includes("····5001") && t.includes("····5002") && !t.includes("****5001"));
  assert.ok(t.includes("Awaiting a second person's approval (srv)") && t.includes("provisional until 2026-11-13"));
  assert.ok(t.includes("ACH settles provisionally (library)."));
  assert.deepEqual(options(el, "Status").map(([k]) => k), ["", "open", "draft", "pending_approval", "settled"]);
  assert.deepEqual(options(el, "Rail").map(([k]) => k), ["", "ach", "wire"]);
  assert.deepEqual(buttons(el), ["New payment instruction"]);
  // no New without can_write, even if the server's action list were stale
  assert.deepEqual(buttons(h(PaymentList, { data: LIST({ permissions: { ...STAFF, can_write: false } }), filters: {} })), []);
});

test("new form: only eligible sources; the refused ones listed with BOTH reasons; destinations and rails follow the source", () => {
  const el = h(NewPaymentForm, { options: OPTIONS() });
  assert.deepEqual(options(el, "Source account").map(([k]) => k), ["", "a-5001"]);
  const t = render(el);
  assert.ok(t.includes("tier 'inquiry'") && t.includes("authority advise"), "both refusal reasons shown");
  // nothing chosen yet: no destination, no rail
  assert.deepEqual(options(el, "Destination account").map(([k]) => k), [""]);
  assert.deepEqual(options(el, "Rail").map(([k]) => k), [""]);
  assert.deepEqual(L.destinationChoices(OPTIONS(), "a-5001").map((d) => d.key), ["a-5002"]);
  assert.deepEqual(L.railChoices(OPTIONS(), "a-5001").map((r) => r.key), ["ach"]);
  // a refused source offers NOTHING, even though the server listed destinations for it
  assert.deepEqual(L.destinationChoices(OPTIONS(), "a-5003"), []);
  assert.deepEqual(L.railChoices(OPTIONS(), "a-5003"), []);
  assert.ok(t.includes("a US business day, today or later (srv).") && t.includes("2026-11-10"));
  assert.ok(html(el).includes('min="2026-11-09"'));
  assert.deepEqual(buttons(el).includes("Save draft"), true);
});

test("new form: the draft body is built only from offered choices (a wire the custodian did not declare is refused)", () => {
  const ok = L.draftBody(OPTIONS(), { source: "a-5001", destination: "a-5002", rail: "ach", amount: "25000.00",
    valueDate: "2026-11-12", purpose: "Property tax", link: "cash_obligation:o-1" });
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.body, { source_account_id: "a-5001", destination_account_id: "a-5002", amount: "25000.00", rail: "ach",
    value_date: "2026-11-12", purpose: "Property tax", purpose_ref_type: "cash_obligation", purpose_ref_id: "o-1" });
  const bad = L.draftBody(OPTIONS(), { source: "a-5001", destination: "a-5004", rail: "wire", amount: "1.234",
    valueDate: "", purpose: "" });
  assert.equal(bad.ok, false);
  assert.equal(bad.errors.length, 5);
  assert.equal(L.draftBody(OPTIONS(), { source: "a-5003", destination: "a-5004", rail: "ach", amount: "5",
    valueDate: "2026-11-12", purpose: "x" }).ok, false, "a refused source never drafts");
});

test("detail: the lifecycle timeline, and ONLY the server's actions (no Approve for a maker)", () => {
  const maker = h(PaymentDetail, { data: DETAIL({ actions: [{ key: "cancel", label: "Cancel (srv)" }] }) });
  const t = render(maker);
  assert.ok(t.includes("Drafted (srv)") && t.includes("Submitted for approval (srv)") && t.includes("Ann"));
  assert.deepEqual(buttons(maker).filter((b) => b !== "Close"), ["Cancel (srv)"]);
  const checker = h(PaymentDetail, { data: DETAIL({ actions: [{ key: "approve", label: "Approve (srv)" },
    { key: "reject", label: "Return to draft (srv)" }] }) });
  assert.deepEqual(buttons(checker).filter((b) => b !== "Close"), ["Approve (srv)", "Return to draft (srv)"]);
  assert.deepEqual(L.actionRequest("approve", { note: "ok", channel: "email" }), ["approve", { note: "ok" }]);
});

test("detail: the sheet + confirm-sent controls, the channel from the server; reconcile needs a match or a reason", () => {
  const el = h(PaymentDetail, { data: DETAIL({ status: "approved", actions: [
    { key: "sheet", label: "Download payment sheet (srv)" }, { key: "mark_sent", label: "Confirm sent (srv)" }] }) });
  assert.deepEqual(buttons(el).filter((b) => b !== "Close"), ["Download payment sheet (srv)", "Confirm sent (srv)"]);
  assert.deepEqual(options(el, "Channel").map(([k]) => k), ["", "custodian_portal", "email"]);
  assert.deepEqual(L.actionRequest("mark_sent", { channel: "email", reason: "x" }), ["mark_sent", { channel: "email" }]);
  const m = { matches: [{ id: "t-1", leg: "out", amount: "25000.00", date: "2026-11-12", type: "sell" }] };
  assert.deepEqual(L.reconcileBody(m, ["t-1", "t-x"], ""), { ok: true, body: { method: "custodian_data", transaction_ids: ["t-1"] } });
  assert.deepEqual(L.reconcileBody(m, [], "  confirmed on the custodian statement "),
    { ok: true, body: { method: "manual", note: "confirmed on the custodian statement" } });
  assert.equal(L.reconcileBody(m, [], "").ok, false);
});

test("member: their family's payments READ-ONLY — no control at all, last four only, the cash.payments text", () => {
  const mem = LIST({ member_view: true, permissions: { can_read: true, can_write: false, is_super_admin: false,
    read_permission: "view_portfolio", write_permission: null }, actions: [{ key: "new", label: "New" }],
  items: [ITEM({ actions: [{ key: "cancel", label: "Cancel" }] })] });
  const el = h(PaymentList, { data: mem, filters: {} });
  assert.deepEqual(buttons(el), []);
  assert.equal(options(el, "Status"), null);
  const t = render(el);
  assert.ok(t.includes("····5001") && !t.includes("****5001") && t.includes("ACH settles provisionally (library)."));
  assert.deepEqual(L.paymentRows(mem)[0].actions, [], "a member never gets a row action, even a stale one");
  const d = h(PaymentDetail, { data: DETAIL({ actions: [{ key: "approve", label: "Approve" }] }, { member_view: true,
    permissions: { can_read: true, can_write: false } }) });
  assert.deepEqual(buttons(d).filter((b) => b !== "Close"), []);
  assert.equal(L.canWrite(mem), false);
  assert.equal(render(h(NewPaymentForm, { options: OPTIONS({ member_view: true }) })), "");
});

test("fails closed: a lost or partial envelope renders nothing to click", () => {
  for (const lost of [{}, null, LIST({ permissions: undefined }), LIST({ household: null }),
    LIST({ permissions: { can_read: "true" } })]) {
    assert.equal(render(h(PaymentList, { data: lost, filters: {} })), "");
    assert.deepEqual(L.paymentRows(lost), []);
  }
  assert.equal(render(h(NewPaymentForm, { options: OPTIONS({ permissions: { can_read: true } }) })), "");
  assert.equal(render(h(PaymentDetail, { data: DETAIL({}, { permissions: undefined }) })), "");
  assert.equal(L.newOffer(LIST({ permissions: { ...STAFF, can_write: "yes" } })), false);
  assert.equal(L.lastFour("990055501234"), "····1234");
});

test("the Treasury mount: the payments section renders from its read; a member's overview shows the sweep-rate text", () => {
  const t = render(h(TreasuryPayments, { householdId: "h-1", initial: { list: LIST() } }));
  assert.ok(t.includes("Payments between your accounts") && t.includes("task inbox"));
  const mt = render(h(TreasuryPayments, { householdId: "h-1", initial: { list: LIST({ member_view: true,
    permissions: { can_read: true, can_write: false } }) } }));
  assert.ok(mt.includes("Payments between your accounts") && !mt.includes("task inbox"));
  const ov = { household: { id: "h-1" }, scope: { entity_id: null }, member_view: true,
    permissions: { can_read: true, can_write: false }, screen: "overview", today: "2026-11-09",
    range: { key: "90d", start: "2026-08-11", end: "2026-11-09" }, points: [], markers: [], balance: "1.00",
    change_in_range: "0.00", series_basis: "core", yields: { estimated: false, text: "not estimated" }, coverage: null,
    coverage_entities: [], actions: [], vocabularies: { ranges: [], flags: [], editable: [], inline_editable: [] },
    labels: { "cash.sweep_rate": { key: "cash.sweep_rate", text: "The cash sweep rate and any spread are disclosed in our Form ADV." } } };
  assert.ok(render(h(Overview, { data: ov })).includes("disclosed in our Form ADV"));
  const staff = { ...ov, member_view: false, labels: {} };
  assert.ok(!render(h(Overview, { data: staff })).includes("Form ADV"));
});
