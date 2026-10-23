// REP1 — the REAL Replication Studio components rendered with
// react-dom/server from envelopes shaped exactly like routers/replication.py
// returns them. Every write control (Add, Correct, Change, Approve, Reject,
// Refresh, Re-run extraction) must FAIL CLOSED: none without the server's own
// flag being exactly true; Approve / Reject also never for a maker of the draft.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const rep = await import("../../lib/replication.mjs");
const { default: RegistryList, RegistryTable } = await import("../../components/replication/RegistryList.jsx");
const { default: EntryView, TermsPanel, HoldingsPanel } = await import("../../components/replication/EntryView.jsx");
const menu = await import("../../lib/menuVisibility.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());

const VOCAB = {
  instrument_classes: [{ key: "index_etf", label: "Index ETF", enabled: true },
    { key: "active_etf", label: "Active ETF", enabled: false }],
  entry_statuses: [{ key: "draft", label: "Draft" }, { key: "active", label: "Active" }],
  version_statuses: [{ key: "draft", label: "Draft" }, { key: "approved", label: "Approved" }],
  origins: [{ key: "extracted", label: "Extracted" }, { key: "corrected", label: "Corrected" },
    { key: "deliberate", label: "Deliberate change" }],
  confidence: [{ key: "agreed", label: "Both readers agree" }],
  holdings_sources: [], editable: [],
};
const PERMS_VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false,
  read_permission: "view_replication", write_permission: "manage_replication",
  approve_permission: "approve_replication", can_approve_draft: false, is_maker_of_draft: false };
const ROW = { id: "e1", display_name: "Holdings of QQQ", ticker: "QQQ", instrument_class: "index_etf",
  issuer: "Invesco", status: "active", latest_as_of: "2026-10-21", latest_status: "live",
  last_refreshed: "2026-10-22T11:05:00+00:00", constituents_total: 101, constituents_resolved: 99,
  terms: { approved_version: 1, draft_version: 2, draft_needs_review: 3 } };
const FIELDS = [
  { field_key: "index_name", label: "Index (disclosed term)", value: "Nasdaq-100 Index", origin: "extracted",
    confidence: "agreed", needs_review: false, quote: "tracks the Nasdaq-100 Index", quote_verified: true,
    document_url: "https://www.sec.gov/x", form_type: "497K", disclosure_only: true, data_type: "text" },
  { field_key: "rebalance_frequency", label: "Rebalance frequency", value: "monthly", origin: "deliberate",
    reason: "Monthly is cheaper for the member", confidence: "human", needs_review: false, data_type: "text",
    enum_values: ["monthly", "quarterly"] },
  { field_key: "expense_ratio_net_pct", label: "Expense ratio — net", value: 0.2, origin: "corrected",
    needs_review: true, review_reasons: ["its quote was not found in the document"], data_type: "numeric" },
];
const entryEnvelope = (permissions, vocabExtra = {}, termsExtra = {}) => ({
  entry: { id: "e1", display_name: "Holdings of QQQ", ticker: "QQQ",
    disclosure: rep.disclosure("QQQ") },
  permissions,
  vocabularies: { ...VOCAB, ...vocabExtra },
  snapshot: { id: "s2", as_of: "2026-10-21", status: "live", row_count: 3, total_weight_ex_cash: "1.0",
    diff: { first_snapshot: false, added: [{ key: "AAA", name: "Alpha", ticker: "AAA", weight: "0.01" }],
      removed: [], moves: [], turnover: "0.01" } },
  snapshots: [{ id: "s2", as_of: "2026-10-21", status: "live", row_count: 3, file_sha256: "a".repeat(64),
    fetched_at: "2026-10-22T11:00:00" }],
  holdings: { rows: [{ row_num: 1, name: "Alpha", ticker: "AAA", cusip: "ZZREP0001", weight: "0.5", shares: "10",
    resolution_state: "unresolved" }], total: 1 },
  terms: { draft_version_id: "v2", approved_version_id: "v1",
    shown_version: { id: "v2", version_no: 2, status: "draft", needs_review_fields: ["expense_ratio_net_pct"] },
    departures: [{ field_key: "rebalance_frequency", label: "Rebalance frequency", fund_value: "quarterly",
      our_value: "monthly", reason: "Monthly is cheaper for the member" }],
    fields: FIELDS, versions: [{ id: "v1", version_no: 1, status: "approved", departures: 0 }], ...termsExtra },
  sources: { documents: [] },
});
const ALL_KEYS = FIELDS.map((f) => f.field_key);
const WRITE_VOCAB = { editable: ALL_KEYS, correctable: ALL_KEYS,
  changeable: ALL_KEYS.filter((k) => k !== "index_name") };

test("registry: no Add control unless the envelope says can_write === true", () => {
  for (const env of [
    { rows: [ROW], permissions: PERMS_VIEW, vocabularies: VOCAB },
    { rows: [ROW], vocabularies: VOCAB },                                   // envelope lost its permissions
    { rows: [ROW], permissions: { ...PERMS_VIEW, can_write: "true" }, vocabularies: VOCAB },   // not exactly true
    null,
  ]) {
    assert.equal(rep.registryControls(env).add, false);
    const html = render(h(RegistryList, { initialEnvelope: env }));
    assert.ok(!buttons(html).some((b) => /Add/.test(b)), `no Add button for ${JSON.stringify(env?.permissions)}`);
  }
  const html = render(h(RegistryList, { initialEnvelope: { rows: [ROW], vocabularies: VOCAB,
    permissions: { ...PERMS_VIEW, can_write: true } } }));
  assert.ok(buttons(html).includes("Add a fund"), "manage_replication → the Add button renders");
});

test("registry rows: 'Holdings of <ticker>', class / statuses from the vocabulary, resolved / total", () => {
  const html = text(render(h(RegistryTable, { envelope: { rows: [ROW], vocabularies: VOCAB } })));
  assert.match(html, /Holdings of QQQ/);
  assert.match(html, /Index ETF/);
  assert.match(html, /99 \/ 101/);
  assert.match(html, /Draft v2 — 3 need review/);
  assert.doesNotMatch(html, /Nasdaq-100/, "a licensed index name is never the product name");
});

test("terms: view-only envelope renders no Correct / Change / Approve / Reject (and no Refresh)", () => {
  const env = entryEnvelope(PERMS_VIEW);
  const html = render(h(EntryView, { initialEnvelope: env }));
  const b = buttons(html);
  for (const label of ["Correct", "Change", "Approve", "Reject", "Refresh holdings", "Re-run extraction"]) {
    assert.ok(!b.includes(label), `no ${label} for a view-only caller`);
  }
  // even if a stale vocabulary lists editable fields, can_write false keeps them hidden
  const stale = entryEnvelope(PERMS_VIEW, WRITE_VOCAB);
  const t = render(h(TermsPanel, { envelope: stale }));
  assert.ok(!buttons(t).includes("Correct") && !buttons(t).includes("Change"));
});

test("terms: manage_replication shows Correct on every draft field and Change except disclosure-only", () => {
  const env = entryEnvelope({ ...PERMS_VIEW, can_write: true }, WRITE_VOCAB);
  const b = buttons(render(h(TermsPanel, { envelope: env })));
  assert.equal(b.filter((x) => x === "Correct").length, 3);
  assert.equal(b.filter((x) => x === "Change").length, 2, "the index name is a disclosed term — never changed");
  assert.ok(!b.includes("Approve"), "can_write alone is not approve_replication");
  // no draft open → no Correct (corrections edit only the draft)
  const noDraft = entryEnvelope({ ...PERMS_VIEW, can_write: true }, WRITE_VOCAB, { draft_version_id: null });
  assert.ok(!buttons(render(h(TermsPanel, { envelope: noDraft }))).includes("Correct"));
});

test("approve / reject: only with approve_replication AND not a maker of the draft", () => {
  const approver = entryEnvelope({ ...PERMS_VIEW, can_approve: true, can_approve_draft: true });
  let b = buttons(render(h(TermsPanel, { envelope: approver })));
  assert.ok(b.includes("Approve") && b.includes("Reject"));
  const maker = entryEnvelope({ ...PERMS_VIEW, can_approve: true, can_approve_draft: false, is_maker_of_draft: true });
  b = buttons(render(h(TermsPanel, { envelope: maker })));
  assert.ok(!b.includes("Approve") && !b.includes("Reject"), "hidden for the draft's maker");
  const inconsistent = entryEnvelope({ ...PERMS_VIEW, can_approve: true, can_approve_draft: true, is_maker_of_draft: true });
  assert.equal(rep.approvalControls(inconsistent).approve, false, "the maker flag always wins");
  const noFlag = entryEnvelope({ ...PERMS_VIEW, can_approve: true });
  assert.equal(rep.approvalControls(noFlag).approve, false, "no can_approve_draft from the server → hidden");
  assert.equal(rep.approvalControls({}).approve, false);
});

test("departures in plain words at the top; origin chips and quotes per field", () => {
  const html = text(render(h(TermsPanel, { envelope: entryEnvelope(PERMS_VIEW) })));
  assert.match(html, /Departs from the fund: Rebalance frequency is monthly instead of the fund's quarterly — Monthly is cheaper for the member\./);
  assert.match(html, /Deliberate change/);
  assert.match(html, /Corrected/);
  assert.match(html, /Extracted/);
  assert.match(html, /“tracks the Nasdaq-100 Index”/);
  assert.match(html, /Needs review/);
});

test("holdings: unresolved rows flagged, diff listed, weights as percent", () => {
  const html = text(render(h(HoldingsPanel, { envelope: entryEnvelope(PERMS_VIEW) })));
  assert.match(html, /No — queued/);
  assert.match(html, /Added: Alpha \(AAA\) 1\.00%/);
  assert.match(html, /50\.00%/);
  assert.equal(rep.formatWeight("0.0803"), "8.03%");
});

test("copy: display name and the disclosure line", () => {
  assert.equal(rep.displayName("qqq"), "Holdings of QQQ");
  assert.equal(rep.disclosure("QQQ"), "Replicates the published holdings of QQQ; not affiliated with or endorsed by the fund's sponsor or index provider.");
  const html = text(render(h(EntryView, { initialEnvelope: entryEnvelope(PERMS_VIEW) })));
  assert.match(html, /Replicates the published holdings of QQQ; not affiliated with or endorsed by the fund's sponsor or index provider\./);
});

test("menu: Portfolio → Replication is gated on view_replication (strict, Super Admin first)", () => {
  const item = menu.MENU_ITEMS.find((i) => i.href === "/portfolio/replication");
  assert.ok(item && item.group === "portfolio" && item.label === "Replication");
  assert.deepEqual(item.gate, { perm: "view_replication", strict: true });
  const member = { account_role: "member", permissions: ["view_portfolio"] };
  const admin = { account_role: "member", permissions: ["view_replication"] };
  const sa = { account_role: "super_admin", permissions: [] };
  assert.equal(menu.canAccess(member, item.gate), false);
  assert.equal(menu.canAccess(admin, item.gate), true);
  assert.equal(menu.canAccess(sa, item.gate), true);
  assert.equal(menu.canAccess({ account_role: "member" }, item.gate), false, "no permissions array → hidden");
  assert.equal(menu.labelFor("/portfolio/replication"), "Replication");
});
