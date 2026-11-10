// CASH08 — Household → Treasury → Payees / Invoices: the REAL components rendered with react-dom/server from reads
// shaped exactly like routers/cash_payees.py returns them (services/cash_payees.payee_out / detail, cash_invoices): the
// payee list (status, SLOA state, flags), the detail (last four only, the reference, the SLOA, the history), the
// bank-change request form, the call-back attestation form (only where the server lists approve_bank_change — never
// for the requester), the invoices screen + record form, the member's read-only list (name, kind, last four, the
// cash.third_party disclosure) — and every screen FAILING CLOSED on a lost read. No rendered text carries a digit run
// longer than four.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/cashPayees.mjs");
const P = await import("../../components/treasury/TreasuryPayees.jsx");
const I = await import("../../components/treasury/TreasuryInvoices.jsx");
const { PayeeList, PayeeDetail, NewPayeeForm, BankChangeForm, CallbackForm } = P;
const { InvoiceList, RecordInvoiceForm } = I;

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/\s+/g, " ");
const html = (el) => renderToStaticMarkup(el);
const render = (el) => text(html(el));
const buttons = (el) => [...html(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());
// a digit run > 4 in the TEXT (uuids live only in attributes; dates / times never exceed four in a row)
const longRun = (s) => /[0-9]{5,}/.test(s);

const DISC = { "cash.third_party": { key: "cash.third_party", text: "Third-party payments are not offered (library)." } };
const STAFF = { can_read: true, can_write: true, can_approve: true, is_super_admin: false, read_permission: "view_portfolio",
  write_permission: "manage_payees", approve_permission: "approve_payees" };
const VOCAB = {
  kinds: [{ key: "vendor", label: "Vendor (srv)" }, { key: "charity", label: "Charity (srv)" }],
  account_types: [{ key: "checking", label: "Checking (srv)" }],
  statuses: [{ key: "active", label: "Active (srv)" }],
  flags: [{ key: "sloa_missing", label: "No SLOA on file (srv)" }, { key: "sloa_expired", label: "SLOA expired (srv)" },
    { key: "bank_change_pending", label: "Bank change pending (srv)" }],
  editable: ["kind", "display_name", "last_four", "payee_reference", "contact_phone"], inline_editable: [],
};
const TP = { enabled: false, reason: "third-party payments are not enabled" };
const PAYEE = (over = {}) => ({ id: "p-1", household_id: "h-1", display_name: "Harbor Roofing", kind: "vendor",
  kind_label: "Vendor (srv)", last_four: "0042", status: "active", status_label: "Active (srv)",
  payee_reference: "STD-HARBOR-AB", bank_name: "First Fixture Bank", contact_phone: "212-555-0100",
  maker_ids: ["u-maker"], flags: [{ key: "sloa_expired", label: "SLOA expired (srv)" }],
  sloa: { document_id: "d-1", document_name: "sloa.pdf", state: "sloa_expired", expires_on: "2026-11-01", account_ids: [] },
  bank_change: null, last_callback: null, history: [], actions: [], ...over });
const LIST = (over = {}) => ({ household: { id: "h-1" }, member_view: false, labels: DISC, permissions: STAFF,
  vocabularies: VOCAB, third_party: TP, today: "2026-11-10",
  items: [PAYEE(), PAYEE({ id: "p-2", display_name: "Bay Charity", kind: "charity", kind_label: "Charity (srv)",
    last_four: "0917", status: "suspended", status_label: "Suspended (srv)", sloa: { state: "sloa_missing" },
    flags: [{ key: "sloa_missing", label: "No SLOA on file (srv)" }, { key: "bank_change_pending", label: "Bank change pending (srv)" }] })],
  actions: [{ key: "new", label: "New payee" }], ...over });
const DETAIL = (pay = {}, over = {}) => ({ household: { id: "h-1" }, member_view: false, labels: DISC, permissions: STAFF,
  vocabularies: VOCAB, third_party: TP, payee: PAYEE({ history: [
    { from: "2026-11-09T10:00:00+00:00", to: "2026-11-09T11:00:00+00:00", status: "draft", status_label: "Draft (srv)",
      last_four: "0042", payee_reference: "STD-HARBOR-AB", pending_last_four: null, current: false },
    { from: "2026-11-09T11:00:00+00:00", to: null, status: "active", status_label: "Active (srv)", last_four: "0042",
      payee_reference: "STD-HARBOR-AB", pending_last_four: null, callback_person: null, current: true }],
  ...pay }), ...over });
const PENDING = { pending_last_four: "0777", pending_payee_reference: "STD-HARBOR-CD", requested_by: "u-req",
  requested_by_name: "Rita Requester", request_contact: "email from ap@harbor.example, call 646-555-0199",
  callback_number_on_file: "212-555-0100" };
const INV = (over = {}) => ({ household: { id: "h-1" }, member_view: false, labels: DISC, permissions: STAFF,
  vocabularies: VOCAB, third_party: TP,
  awaiting: [{ document_id: "d-9", original_filename: "harbor-invoice-A7.pdf", uploaded_at: "2026-11-09T10:00:00+00:00" }],
  recorded: [{ obligation_id: "o-1", lineage_id: "l-1", name: "Harbor Roofing — invoice INV-A6", amount: "1250.00",
    due_date: "2026-12-01", payee_name: "Harbor Roofing", payee_last_four: "0042", original_filename: "a6.pdf" }],
  form: { payees: [{ id: "p-1", label: "Harbor Roofing (····0042)" }], payers: [{ id: "e-1", name: "Olive Trust" }] },
  actions: [{ key: "record", label: "Record invoice" }], ...over });

test("payee list: rows from the server (last four only), SLOA state + flags, the New offer, the disclosure, third party off", () => {
  const el = h(PayeeList, { data: LIST() });
  const t = render(el);
  assert.ok(t.includes("Harbor Roofing") && t.includes("····0042") && t.includes("····0917"));
  assert.ok(t.includes("SLOA expired (srv)") && t.includes("No SLOA on file (srv)") && t.includes("Bank change pending (srv)"));
  assert.ok(t.includes("Third-party payments are not offered (library).") && t.includes("third-party payments are not enabled"));
  assert.deepEqual(buttons(el), ["New payee"]);
  assert.ok(!longRun(t));
  assert.deepEqual(buttons(h(PayeeList, { data: LIST({ permissions: { ...STAFF, can_write: false } }) })), []);
});

test("payee detail: last four, reference, SLOA, history; only the server's actions render", () => {
  const el = h(PayeeDetail, { data: DETAIL({ actions: [{ key: "suspend", label: "Suspend (srv)" }, { key: "retire", label: "Retire (srv)" }] }) });
  const t = render(el);
  assert.ok(t.includes("····0042") && t.includes("STD-HARBOR-AB") && t.includes("sloa.pdf") && t.includes("expires 2026-11-01"));
  assert.ok(t.includes("Draft (srv)") && t.includes("Active (srv)"));
  assert.deepEqual(buttons(el).filter((b) => b !== "Close"), ["Suspend (srv)", "Retire (srv)"]);
  assert.ok(!longRun(t));
  // no actions listed → no controls; can_write false → write actions dropped even if the row lists them
  assert.deepEqual(buttons(h(PayeeDetail, { data: DETAIL() })).filter((b) => b !== "Close"), []);
  assert.deepEqual(buttons(h(PayeeDetail, { data: DETAIL({ actions: [{ key: "retire", label: "Retire" }] },
    { permissions: { ...STAFF, can_write: false } }) })).filter((b) => b !== "Close"), []);
  // a last four that is not four digits never renders as a number
  assert.equal(L.lastFourText("123456789"), "····");
});

test("new payee form: server kinds; refuses a longer last four or an account-like number before sending", () => {
  const el = h(NewPayeeForm, { data: LIST() });
  const t = render(el);
  assert.ok(t.includes("Vendor (srv)") && t.includes("Never a full account or routing number"));
  assert.equal(L.payeeBody(LIST(), { kind: "vendor", display_name: "X", last_four: "12345" }).ok, false);
  assert.equal(L.payeeBody(LIST(), { kind: "vendor", display_name: "X", payee_reference: "021000021" }).ok, false);
  const ok = L.payeeBody(LIST(), { kind: "vendor", display_name: "X", last_four: "0042", payee_reference: "STD-AB", notes: "n" });
  assert.equal(ok.ok, true);
  assert.deepEqual(Object.keys(ok.body).sort(), ["display_name", "kind", "last_four", "payee_reference"]);  // notes not editable here
  assert.equal(render(h(NewPayeeForm, { data: LIST({ permissions: { ...STAFF, can_write: false } }) })), "");
});

test("bank-change form: only where the server offers request_bank_change; the request needs how it arrived", () => {
  const offer = DETAIL({ actions: [{ key: "request_bank_change", label: "Change bank details" }] });
  const t = render(h(BankChangeForm, { data: offer, payee: offer.payee }));
  assert.ok(t.includes("suspended until a second person calls back") && t.includes("Currently ····0042"));
  assert.equal(render(h(BankChangeForm, { data: DETAIL(), payee: DETAIL().payee })), "");
  assert.equal(L.bankChangeBody(offer, { last_four: "0777", payee_reference: "STD-CD" }).ok, false);
  const b = L.bankChangeBody(offer, { last_four: "0777", payee_reference: "STD-CD", request_contact: "email from ap@x" });
  assert.equal(b.ok, true);
  assert.deepEqual(b.body.changes, { last_four: "0777", payee_reference: "STD-CD" });
});

test("call-back attestation form: the number ON FILE shown, all three fields required; never for the requester", () => {
  const approver = DETAIL({ status: "suspended", bank_change: PENDING,
    actions: [{ key: "approve_bank_change", label: "Approve bank change (call-back)" }, { key: "reject_bank_change", label: "Reject bank change" }] });
  const el = h(CallbackForm, { data: approver, payee: approver.payee });
  const t = render(el);
  assert.ok(t.includes("Call the number ON FILE: 212-555-0100") && t.includes("Never a number from the request"));
  assert.ok(t.includes("····0042 → ····0777") && t.includes("Rita Requester"));
  assert.deepEqual(buttons(el), ["Approve bank change", "Reject bank change"]);
  assert.ok(!longRun(t));
  // the requester's read lists no approve action → nothing renders
  const requester = DETAIL({ status: "suspended", bank_change: PENDING, actions: [] });
  assert.equal(render(h(CallbackForm, { data: requester, payee: requester.payee })), "");
  // can_approve false → nothing, even if a stale row lists it
  const lost = DETAIL({ status: "suspended", bank_change: PENDING, actions: approver.payee.actions },
    { permissions: { ...STAFF, can_approve: false } });
  assert.equal(render(h(CallbackForm, { data: lost, payee: lost.payee })), "");
  assert.equal(L.attestationBody({ person: "A", number: "212-555-0100" }).ok, false);
  assert.equal(L.attestationBody({ person: "A", number: "212-555-0100", called_at: "2026-11-10T10:00" }).ok, true);
});

test("invoices: awaiting + recorded from the server; the record form only with the record action", () => {
  const el = h(InvoiceList, { data: INV() });
  const t = render(el);
  assert.ok(t.includes("harbor-invoice-A7.pdf") && t.includes("Harbor Roofing — invoice INV-A6") && t.includes("$1,250.00"));
  assert.ok(t.includes("Harbor Roofing (····0042)"));
  assert.deepEqual(buttons(el), ["Record invoice"]);
  const f = render(h(RecordInvoiceForm, { data: INV(), documentId: "d-9" }));
  assert.ok(f.includes("Harbor Roofing (····0042)") && f.includes("Olive Trust") && f.includes("nothing is read from the document"));
  const noRec = INV({ actions: [] });
  assert.deepEqual(buttons(h(InvoiceList, { data: noRec })), []);
  assert.equal(render(h(RecordInvoiceForm, { data: noRec, documentId: "d-9" })), "");
  assert.equal(L.recordBody(INV(), { document_id: "d-9", payee_id: "p-1", entity_id: "e-1", invoice_number: "INV-A7",
    amount: "1,250.00", due_date: "2026-12-01" }).body.amount, "1250.00");
  assert.equal(L.recordBody(INV(), { document_id: "d-9" }).ok, false);
  assert.ok(!longRun(t));
});

test("member: read-only — name, kind, last four, the disclosure; no SLOA, no flags, no control, no invoices", () => {
  const member = LIST({ member_view: true, permissions: { ...STAFF, can_write: false, can_approve: false },
    items: [{ id: "p-1", household_id: "h-1", display_name: "Harbor Roofing", kind: "vendor", kind_label: "Vendor (srv)",
      last_four: "0042", status: "active", status_label: "Active (srv)", actions: [] }], actions: [] });
  const el = h(PayeeList, { data: member });
  const t = render(el);
  assert.ok(t.includes("Harbor Roofing") && t.includes("Vendor (srv)") && t.includes("····0042"));
  assert.ok(t.includes("Third-party payments are not offered (library)."));
  assert.ok(!t.includes("SLOA") && !t.includes("Flags") && !t.includes("STD-"));
  assert.deepEqual(buttons(el), []);
  assert.deepEqual(L.payeeActions(member, { actions: [{ key: "retire" }] }), []);
  assert.deepEqual(L.invoiceRows({ ...INV(), member_view: true }), { awaiting: [], recorded: [] });
});

test("every screen fails closed on a lost read", () => {
  for (const bad of [null, {}, { permissions: {} }, { permissions: { can_read: true } }, { household: { id: "h" } }]) {
    assert.equal(render(h(PayeeList, { data: bad })), "");
    assert.equal(render(h(PayeeDetail, { data: bad })), "");
    assert.equal(render(h(NewPayeeForm, { data: bad })), "");
    assert.equal(render(h(InvoiceList, { data: bad })), "");
    assert.equal(render(h(RecordInvoiceForm, { data: bad, documentId: "d" })), "");
    assert.deepEqual(L.payeeRows(bad), []);
  }
  assert.equal(L.newOffer({ ...LIST(), permissions: { ...STAFF, can_write: "true" } }), false);
});
