// CPSIM02 — Admin → Counterparties: the book tabs (Import / Export, Connections, Webhooks, Conformance) and the account
// page's "Simulated custodian" badge, rendered with react-dom/server from envelopes shaped exactly like
// routers/admin_counterparties.py / routers/account_models.py return them. Write controls FAIL CLOSED (can_write must be
// exactly true); the book tabs are enabled only for the server's book_institution_types; reasons are the server's labels.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const C = await import("../../components/counterparties/Counterparties.jsx");
const B = await import("../../components/counterparties/SimulatedCustodianBadge.jsx");
const L = await import("../../lib/counterparties.mjs");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const render = (el) => renderToStaticMarkup(el);
const SU = { can_read: true, can_write: true, is_super_admin: true, read_permission: "super_admin", write_permission: "super_admin" };
const VIEW = { can_read: true, can_write: false, is_super_admin: false };
const TYPES = [
  { key: "altruist", label: "Altruist-compatible custodian", field_groups: ["connection", "behaviour", "clock"], description: "REST + OAuth" },
  { key: "schwab", label: "Schwab-compatible custodian", field_groups: ["connection", "fix", "behaviour", "clock"], description: "FIX" },
  { key: "domestic_bank", label: "Domestic bank", field_groups: ["connection", "bank_domestic", "behaviour", "clock"], description: "wire + ACH" },
];
const KINDS = [
  { key: "households", label: "Households", columns: ["household_id", "name"], required: ["name"] },
  { key: "accounts", label: "Accounts", columns: ["account_id", "household_id", "name", "account_type"], required: ["household_id", "name"] },
  { key: "positions", label: "Positions (holdings and values)", columns: ["position_id", "account_id", "as_of"], required: ["account_id", "as_of"] },
  { key: "transactions", label: "Transactions", columns: ["transaction_id", "account_id", "type"], required: ["account_id", "type", "trade_date"] },
];
const REASONS = [
  { key: "unknown_identifier", label: "the identifier matches no security in the security master" },
  { key: "ambiguous_identifier", label: "the identifier matches more than one security" },
  { key: "no_price", label: "no market value and no platform price on or before the as-of date" },
  { key: "bad_type", label: "the transaction type is not one the client maps" },
  { key: "no_position", label: "no position for this security in the same account" },
  { key: "bad_date", label: "a date is not ISO YYYY-MM-DD" },
];
const TABS = [{ key: "connection", label: "Connection" }, { key: "fix", label: "FIX" }, { key: "bank", label: "Bank details" },
  { key: "behaviour", label: "Behaviour" }, { key: "clock", label: "Clock" }, { key: "activity", label: "Activity" },
  { key: "import_export", label: "Import / Export" }, { key: "connections", label: "Connections" },
  { key: "webhooks", label: "Webhooks" }, { key: "conformance", label: "Conformance" }];
const BOOK_TABS = ["import_export", "connections", "webhooks", "conformance"];
const VOCAB = {
  institution_types: TYPES, statuses: [{ key: "active", label: "Active" }],
  outcomes: [{ key: "accept", label: "Accept" }], begin_strings: ["FIX.4.4"], tabs: TABS,
  book_tabs: BOOK_TABS, book_institution_types: ["altruist"], editable: ["book"], inline_editable: [],
};
const BOOK_VOCAB = { kinds: KINDS, reasons: REASONS, formats: ["csv", "json"], mode: "replace", max_bytes: 2097152, max_rows: 5000 };

function detail(type, perms = SU, vocab = VOCAB) {
  return {
    simulated: true, permissions: perms, vocabularies: vocab,
    counterparty: { id: "c1", slug: "harbor", name: "Harbor", institution_type: type, status: "active", destination_path: "/cpsim/c/harbor" },
    institution_type: TYPES.find((t) => t.key === type), destination_url: "https://api.example.test/cpsim/c/harbor",
    credentials: [{ id: "k1", kind: "api_key", last4: "9f3a", state: "live" }], fix_session: null, bank_details: [],
    behaviour: { default_outcome: "accept", webhooks_enabled: true, webhook_url: null },
    clock: [{ business_date: "2026-11-11", reason: "initial" }], history: [],
  };
}
const book = (perms = SU) => ({
  simulated: true, permissions: perms, vocabularies: { ...VOCAB, book: BOOK_VOCAB },
  counts: { households: 1, accounts: 2, positions: 5, transactions: 3 }, business_date: "2026-11-11",
  uploads: [{ id: "u1", uploaded_at: "2026-11-10T10:00:00Z", file_name: "positions.csv", kind: "positions",
    rows_accepted: 5, rows_rejected: 2 }],
});
const PREVIEW = {
  kind: "positions", file_name: "positions.csv", counts: { rows: 8, accepted: 2, rejected: 6 },
  rejected: [
    { row: 1, id: "p1", reason: "unknown_identifier", message: "x", detail: { id_type: "cusip", value: "000000000" } },
    { row: 2, id: "p2", reason: "ambiguous_identifier", message: "x", detail: { id_type: "ticker", value: "DUP", matches: 2 } },
    { row: 3, id: "p3", reason: "no_price", message: "x" }, { row: 4, id: "t1", reason: "bad_type", message: "x" },
    { row: 5, id: "t2", reason: "no_position", message: "x" }, { row: 6, id: "t3", reason: "bad_date", message: "x" }],
};

test("book tabs: enabled for the server's book types only; a lost vocabulary fails closed", () => {
  const alt = L.tabsFor(detail("altruist"), "altruist");
  for (const k of BOOK_TABS) assert.equal(alt.find((t) => t.key === k).enabled, true, `altruist: ${k} enabled`);
  for (const ty of ["schwab", "domestic_bank"]) {
    const tabs = L.tabsFor(detail(ty), ty);
    for (const k of BOOK_TABS) assert.equal(tabs.find((t) => t.key === k).enabled, false, `${ty}: ${k} disabled`);
  }
  const lost = { ...VOCAB }; delete lost.book_institution_types;
  const tabs = L.tabsFor(detail("altruist", SU, lost), "altruist");
  for (const k of BOOK_TABS) assert.equal(tabs.find((t) => t.key === k).enabled, false, `lost vocab: ${k} disabled`);
  const html = render(h(C.CounterpartyDetail, { envelope: detail("schwab"), requests: { rows: [] }, initialTab: "import_export" }));
  assert.ok(text(html).includes(L.IMPORT_EXPORT_NOTE), "a non-book type shows the note, not the book");
});

test("import / export: templates, upload, preview with every rejection reason labelled, history, export", () => {
  const html = render(h(C.ImportExportTab, { id: "c1", initialBook: book(), initialPreview: PREVIEW }));
  const t = text(html);
  assert.ok(html.includes('data-testid="book-templates"') && html.includes("book/template?kind=positions&amp;format=csv"));
  assert.ok(html.includes('data-testid="book-upload"'), "a writer sees the upload control");
  assert.ok(html.includes('data-testid="rejection-table"'));
  for (const r of REASONS) assert.ok(t.includes(r.label), `reason label rendered: ${r.key}`);
  assert.ok(html.includes('data-reason="ambiguous_identifier"') && t.includes("matches: 2"));
  assert.ok(html.includes('data-testid="book-history"') && t.includes("positions.csv"));
  assert.ok(html.includes("book/export?format=json") && html.includes("book/export?format=csv&amp;kind=transactions"));
  assert.ok(t.includes("5") && html.includes('data-testid="book-counts"'));
});

test("import / export: a view-only envelope renders NO upload / accept control (fails closed)", () => {
  for (const perms of [VIEW, { ...SU, can_write: "true" }, {}]) {
    const html = render(h(C.ImportExportTab, { id: "c1", initialBook: book(perms), initialPreview: PREVIEW }));
    assert.ok(!html.includes('data-testid="book-upload"'), "no upload control");
    assert.ok(!html.includes('data-testid="book-accept"'), "no accept control");
  }
  assert.equal(L.canAccept(book(), { counts: { accepted: 0 } }), false, "nothing to accept → no accept");
  assert.equal(L.canAccept(book(VIEW), PREVIEW), false);
  assert.equal(L.canAccept(book(), PREVIEW), true);
});

const CONNS = (perms = SU) => ({
  simulated: true, permissions: perms, vocabularies: VOCAB, slug: "harbor",
  rows: [{ id: "o1", name: "Test Org", is_test: true, counterparty_slug: "harbor", points_here: true,
    connection_status: "connected", last_sync: { accounts_synced: 2, positions_created: 5 } },
    { id: "o2", name: "Real Org", is_test: false, counterparty_slug: null, points_here: false, connection_status: null }],
});

test("connections: allowed orgs, which point here, connect / sync only for a writer", () => {
  const html = render(h(C.ConnectionsTab, { id: "c1", initialConnections: CONNS() }));
  const t = text(html);
  assert.ok(t.includes("Test Org") && t.includes("Real Org") && t.includes("demo households only"));
  assert.ok(t.includes("Connect") && t.includes("Sync now") && t.includes("Point here") && t.includes("2 account(s)"));
  const ro = render(h(C.ConnectionsTab, { id: "c1", initialConnections: CONNS(VIEW) }));
  assert.ok(!/Connect<|Sync now|Point here|Unpoint/.test(ro), "view-only: no action buttons");
  const empty = render(h(C.ConnectionsTab, { id: "c1", initialConnections: { ...CONNS(), rows: [] } }));
  assert.ok(empty.includes('data-testid="connections-empty"'));
});

test("webhooks: flag, target, send test event for a writer, the outbound delivery log only", () => {
  const reqs = { rows: [
    { id: "r1", direction: "out", at: "2026-11-10", message_type: "account.positions_updated", status: 200, auth_outcome: "delivered", latency_ms: 12 },
    { id: "r2", direction: "in", at: "2026-11-10", message_type: "altruist.positions", status: 200, auth_outcome: "ok" }] };
  const html = render(h(C.WebhooksTab, { env: detail("altruist"), d: detail("altruist"), act: () => {}, requests: reqs }));
  const t = text(html);
  assert.ok(html.includes('data-testid="webhook-actions"') && t.includes("Send test event"));
  assert.ok(html.includes('data-testid="webhook-deliveries"') && t.includes("account.positions_updated") && t.includes("12 ms"));
  assert.ok(!t.includes("altruist.positions"), "inbound requests are not deliveries");
  const ro = render(h(C.WebhooksTab, { env: detail("altruist", VIEW), d: detail("altruist", VIEW), act: () => {}, requests: reqs }));
  assert.ok(!ro.includes('data-testid="webhook-actions"'), "view-only: no webhook controls");
});

test("conformance: last run summary with failed checks citing their contract row; run control for a writer", () => {
  const env = { simulated: true, permissions: SU, vocabularies: VOCAB, rows: [], last: {
    started_at: "2026-11-10T10:00:00Z", passed: 18, failed: 1,
    results: [{ id: "C12", contract: "§6.5 / T6", label: "positions primary fields", passed: false }] } };
  const html = render(h(C.ConformanceTab, { id: "c1", initialConformance: env }));
  const t = text(html);
  assert.ok(t.includes("18 passed") && t.includes("1 failed") && t.includes("C12 · §6.5 / T6"));
  assert.ok(html.includes('data-testid="conformance-run"') && html.includes('type="password"'));
  const ro = render(h(C.ConformanceTab, { id: "c1", initialConformance: { ...env, permissions: VIEW } }));
  assert.ok(!ro.includes('data-testid="conformance-run"'));
  const none = render(h(C.ConformanceTab, { id: "c1", initialConformance: { ...env, last: null } }));
  assert.ok(none.includes('data-testid="conformance-none"'));
});

test("every book tab renders under the SIMULATED banner on the page", () => {
  const page = render(h(C.default, { initialList: { simulated: true, permissions: SU, vocabularies: VOCAB, rows: [] }, initialOrgAccess: null }));
  assert.ok(page.includes('data-testid="simulated-banner"') && text(page).includes("SIMULATED"));
});

test("account badge: only when the server says simulated === true (exactly)", () => {
  assert.ok(render(h(B.default, { envelope: { custodian_feed: { simulated: true } } })).includes('data-testid="simulated-custodian-badge"'));
  for (const env of [null, {}, { custodian_feed: {} }, { custodian_feed: { simulated: "true" } }, { custodian_feed: { simulated: false } }]) {
    assert.equal(render(h(B.default, { envelope: env })), "", `no badge for ${JSON.stringify(env)}`);
  }
});
