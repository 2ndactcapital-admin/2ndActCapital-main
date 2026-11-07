// CATCHALL1 — the small-fixes sweep, client side: the REAL libs and components rendered in Node from envelopes
// written by hand in the API's shape (never imported from it). Bulk approve wording + a confirmation that comes into
// view and keeps the selection on cancel; ONE busy indicator on every slow action (Task 1b's list); a direct asset's
// Edit / End / History controls only for a writer (none for a member); "Define this contract" only where rep8's gate
// is published, a duplicate never added twice; the Lows (pickers search on any change, lists follow a re-read).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const B = await import("../../lib/bulkApprove.mjs");
const { BulkApproveView, ConfirmView } = await import("../../components/approvals/BulkApprove.jsx");
const BA = await import("../../lib/busyAction.mjs");
const { BusyButton, BusyError } = await import("../../components/ui/Busy.jsx");
const P = await import("../../lib/portentry.mjs");
const { DirectHistory } = await import("../../components/crm/HoldingsTab.jsx");
const OD = await import("../../lib/optionDefine.mjs");
const { DefineContractForm } = await import("../../components/heldaway/OptionContractPicker.jsx");
const TA = await import("../../lib/typeahead.mjs");

const src = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
const noop = () => {};

// ── Bulk approve ────────────────────────────────────────────────────────────────────────────────────────────────
const VOCAB = {
  decisions: [{ key: "approve", label: "Approve" }, { key: "reject", label: "Reject" }],
  households: [], makers: [], subtypes: [], ages: [{ key: "any", label: "Any age" }],
  blocked: [{ key: "permission", label: "You do not hold {perm}" }],
};
const row = (id) => ({ id, type: "authority", subtype: "account", subtype_label: "Account authority", household_id: "h1",
  household_name: "Fixture Household", account_masked: `****00${id}`, to_label: "Advise", maker_name: "M",
  requested_at: "2026-11-06T10:00:00+00:00", age_days: 0, task_status: "open", actions: ["approve", "reject"] });
const ENV = { type: "authority", rows: [row("1"), row("2"), row("3")], limits: { max_items: 200 },
  permissions: { can_read: true, can_write: true, can_approve: true },
  vocabularies: { ...VOCAB, editable: ["decision"], inline_editable: [] } };
const VIEW_ONLY = { ...ENV, permissions: { can_read: true, can_write: false, can_approve: false },
  vocabularies: { ...VOCAB, editable: [], inline_editable: [] }, rows: ENV.rows.map((r) => ({ ...r, actions: [] })) };
const listView = (env, selected) => renderToStaticMarkup(h(BulkApproveView, { envelope: env, selected,
  filters: { age: "any" }, expanded: null, onToggle: noop, onSelectAll: noop, onClear: noop, onFilter: noop,
  onExpand: noop, onAsk: noop }));

test("bulk approve: the first button says what it does — 'Review N selected →', never 'Approve selected'", () => {
  const html = listView(ENV, ["1", "2"]);
  assert.match(html, /Review 2 selected →/);
  assert.match(html, /Review 2 to reject →/);
  assert.doesNotMatch(html, /Approve selected|Reject selected/);
  assert.equal(B.reviewLabel(ENV, ["1"], "approve"), "Review 1 selected →");
  assert.doesNotMatch(listView(VIEW_ONLY, []), /Review \d+/);   // a view-only caller: no decision control at all
});

test("bulk approve: the confirmation is a focusable dialog brought into view, final button 'Approve N' / 'Reject N'", () => {
  const html = renderToStaticMarkup(h(ConfirmView, { envelope: ENV, selected: ["1", "2"], decision: "approve", note: "",
    onNote: noop, onConfirm: noop, onCancel: noop, busy: false }));
  assert.match(html, /role="dialog"/);
  assert.match(html, /tabindex="-1"/);
  assert.match(html, /data-confirm="1"/);
  assert.match(html, />Approve 2</);
  assert.match(html, /\*\*\*\*001/);   // the items are listed
  assert.match(html, /Esc returns to the list with your selection kept/);
  assert.equal(B.finalLabel(ENV, ["1", "2", "3"], "reject"), "Reject 3");
  const code = src("components/approvals/BulkApprove.jsx");
  assert.match(code, /scrollIntoView/);
  assert.match(code, /\.focus\?\.\(/);
  assert.match(code, /e\.key === "Escape"/);
});

test("bulk approve: Cancel / Escape returns with the selection kept; the busy confirm uses the shared indicator", () => {
  const out = B.cancelConfirmation(["1", "3"]);
  assert.equal(out.asking, null);
  assert.deepEqual(out.selected, ["1", "3"]);
  const busy = renderToStaticMarkup(h(ConfirmView, { envelope: ENV, selected: ["1", "2"], decision: "approve", note: "",
    onNote: noop, onConfirm: noop, onCancel: noop, busy: true }));
  assert.match(busy, /aria-busy="true"/);
  assert.match(busy, /Working… \(2\)/);
});

// ── One busy indicator ──────────────────────────────────────────────────────────────────────────────────────────
//: Task 1b — every slow action (button → slow API call) found in the web app, by file
const SLOW_ACTION_FILES = [
  "components/approvals/BulkApprove.jsx", "components/households/HouseholdSummary.jsx",
  "components/crm/LiabilitiesTab.jsx", "components/admin/CustodyImportWizard.jsx",
  "components/heldaway/HeldAwayPanel.jsx", "components/portentry/IntegratedImport.jsx",
  "components/models/UploadWizard.jsx", "components/securityData/OrgSources.jsx",
  "components/crm/tabs/DocumentsTab.jsx", "components/spv/SPVDocumentsTab.jsx",
  "components/admin/VDRProposalsManager.jsx", "components/marketplace/DocumentsList.jsx",
  "components/dealroom/DataRoom.jsx", "components/execution/InstructionDetail.jsx",
  "components/blocks/BlockDetail.jsx", "components/tax/CharitableGifts.jsx", "components/tax/HarvestDashboard.jsx",
  "components/proposals/AccountModelPanel.jsx", "components/proposals/ProposalDetail.jsx",
  "components/replication/OptionsTaxGate.jsx", "components/replication/EntryView.jsx",
  "components/replication/BacktestWorkspace.jsx", "components/replication/ModelTab.jsx",
  "components/replication/OptionsTab.jsx", "components/replication/ConsolidatePanel.jsx",
  "components/investment-profile/ExtractionsTab.jsx", "components/securityData/SecurityMaster.jsx",
  "components/admin/DailyRunsPanel.jsx", "components/admin/EdgarPipelineMonitor.jsx",
  "components/admin/EdgarBulkDownload.jsx", "components/admin/ModelResearchGrid.jsx",
  "components/admin/SystemOneCatalogManager.jsx", "components/crm/HoldingsTab.jsx",
  "components/heldaway/OptionContractPicker.jsx",
];

test("busy: every slow action uses the ONE shared indicator and keeps no local busy copy", () => {
  const missing = [];
  const copies = [];
  for (const f of SLOW_ACTION_FILES) {
    const code = src(f);
    if (!code.includes('from "@/components/ui/Busy"') || !/BusyButton|useBusy/.test(code)) missing.push(f);
    if (/const \[(busy|saving|uploading|gateBusy|pendingTask), set(Busy|Saving|Uploading|GateBusy|PendingTask)\] = useState\(/.test(code)) copies.push(f);
  }
  assert.deepEqual(missing, []);
  assert.deepEqual(copies, []);
  assert.ok(SLOW_ACTION_FILES.length >= 30, `${SLOW_ACTION_FILES.length} files`);
});

test("busy: disabled + aria-busy + 'Working…' while pending; restored with the error shown when it fails", async () => {
  let state = BA.IDLE;
  const seen = [];
  const get = () => state;
  const set = (s) => { state = s; seen.push(s); };
  let release;
  const pending = BA.runBusy(get, set, () => new Promise((res, rej) => { release = rej; }));
  assert.equal(state.busy, true);
  const p = BA.busyButtonProps({ busy: state.busy, label: "Recalculate" });
  assert.equal(p.disabled, true);
  assert.equal(p["aria-busy"], "true");
  assert.equal(p.text, BA.WORKING_LABEL);
  assert.equal(await BA.runBusy(get, set, async () => "second"), undefined);   // a double click sends nothing
  release(new Error("the rollup is already running"));
  assert.equal(await pending, undefined);
  assert.deepEqual(state, { busy: false, error: "the rollup is already running" });
  const after = BA.busyButtonProps({ busy: state.busy, label: "Recalculate" });
  assert.equal(after.disabled, false);
  assert.equal(after.text, "Recalculate");
  assert.equal(await BA.runBusy(get, set, async () => 42), 42);
  assert.deepEqual(state, BA.IDLE);
  const html = renderToStaticMarkup(h(BusyButton, { busy: true, busyLabel: "Recalculating…" }, "Recalculate"));
  assert.match(html, /disabled=""/);
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /data-spinner="1"/);
  assert.match(html, /Recalculating…/);
  const idle = renderToStaticMarkup(h(BusyButton, { busy: false }, "Recalculate"));
  assert.doesNotMatch(idle, /disabled/);
  assert.match(idle, /aria-busy="false"/);
  assert.match(renderToStaticMarkup(h(BusyError, { error: "boom" })), /role="alert"[^>]*>boom</);
  assert.equal(renderToStaticMarkup(h(BusyError, { error: null })), "");
});

// ── Direct assets: Edit / End / History ─────────────────────────────────────────────────────────────────────────
const WRITE = { permissions: { can_read: true, can_write: true },
  vocabularies: { editable: ["add", "revalue", "edit", "end"], inline_editable: [] } };
const MEMBER = { permissions: { can_read: true, can_write: false }, vocabularies: { editable: [], inline_editable: [] } };
const asset = { asset_id: "a1", name: "Lake house", kind: "real_estate", notes: "", asset_value: "900000.00",
  as_of: "2026-10-01", valuation_method: "appraisal", actions: ["history", "edit", "end", "revalue"], ended: null };

test("direct assets: a writer gets Edit / End / History; a member (the real envelope) gets none; a lost envelope none", () => {
  assert.deepEqual(P.directRowControls(WRITE, asset), { edit: true, end: true, history: true, revalue: true });
  const none = { edit: false, end: false, history: false, revalue: false };
  assert.deepEqual(P.directRowControls(MEMBER, { ...asset, actions: [] }), none);
  assert.deepEqual(P.directRowControls(MEMBER, asset), none);   // even if a row carried actions
  assert.deepEqual(P.directRowControls(undefined, asset), none);
  assert.deepEqual(P.directRowControls(WRITE, { ...asset, actions: undefined }), none);
  const ended = { ...asset, actions: ["history"], ended: { end_date: "2026-10-20", reason: "sold" } };
  assert.deepEqual(P.directRowControls(WRITE, ended), { edit: false, end: false, history: true, revalue: false });
});

test("direct assets: Edit sends only what changed; End 'entered in error' sends no date or proceeds", () => {
  const f = P.directEditForm(asset);
  assert.deepEqual(P.directEditPayload(f, asset), {});
  assert.deepEqual(P.directEditPayload({ ...f, kind: "cash_holding", value: "950000.00" }, asset),
    { kind: "cash_holding", value: "950000.00" });
  assert.deepEqual(P.directEndPayload({ end_reason: "sold", end_date: "2026-10-20", proceeds: " 1000000 " }),
    { reason: "sold", end_date: "2026-10-20", proceeds: "1000000" });
  assert.deepEqual(P.directEndPayload({ end_reason: "entered_in_error", end_date: "2026-10-20", proceeds: "5" }),
    { reason: "entered_in_error" });
  for (const [m, rx] of [["PATCH", "direct-assets/00000000-0000-0000-0000-000000000001"],
    ["POST", "direct-assets/00000000-0000-0000-0000-000000000001/end"],
    ["GET", "direct-assets/00000000-0000-0000-0000-000000000001/history"]]) assert.ok(P.portentryAllowed(m, rx), rx);
  assert.equal(P.portentryAllowed("DELETE", "direct-assets/00000000-0000-0000-0000-000000000001"), false);
});

test("direct assets: History lists every version (current + prior), the valuations and the end", () => {
  const html = renderToStaticMarkup(h(DirectHistory, { methods: [{ value: "appraisal", label: "Appraisal" }], data: {
    versions: [{ id: "v2", current: true, name: "Lake house", kind_label: "Cash / working capital", system_to: null },
      { id: "v1", current: false, name: "Lake house", kind_label: "Real estate", system_to: "2026-11-07T10:00:00Z" }],
    valuations: [{ id: "x1", date: "2026-10-01", value: "900000.00", method: "appraisal" }],
    ends: [{ id: "e1", end_date: "2026-10-20", reason_label: "Sold", proceeds: "1000000.00" }] } }));
  assert.match(html, /data-version="current"/);
  assert.match(html, /data-version="prior"/);
  assert.match(html, /Real estate/);
  assert.match(html, /2026-10-20 · Sold/);
  const tab = src("components/crm/HoldingsTab.jsx");
  assert.match(tab, /directRowControls\(assets, a\)/);
  assert.match(tab, /\/api\/portentry\/direct-assets\/\$\{adding\.slice\(6\)\}`, \{ method: "PATCH"/);
});

// ── Option contract from the picker ─────────────────────────────────────────────────────────────────────────────
const PICK = { rows: [], empty_reason: "No option contract on SPY exists yet. You can define it here.",
  underlying: { global_security_id: "u1", ticker: "SPY", kind: "fund" },
  permissions: { can_read: true, can_write: true, write_permission: "manage_replication" },
  vocabularies: { editable: ["define_contract"], option_types: [{ value: "call", label: "Call" }, { value: "put", label: "Put" }],
    exercise_styles: [{ value: "european", label: "European" }, { value: "american", label: "American" }],
    default_exercise_style: "american", default_settlement: "physical", default_multiplier: "100" } };

test("option contract: 'Define this contract' only where the envelope publishes rep8's gate", () => {
  assert.equal(OD.canDefine(PICK), true);
  assert.equal(OD.canDefine({ ...PICK, permissions: { can_write: false }, vocabularies: { editable: [] } }), false);
  assert.equal(OD.canDefine({ ...PICK, underlying: null }), false);
  assert.equal(OD.canDefine(undefined), false);
  const form = { ...OD.defineForm(PICK), option_type: "call", strike: "660", expiry: "2026-12-18" };
  assert.deepEqual(OD.defineProblems(form), []);
  assert.ok(OD.defineProblems({ ...form, strike: "six" }).length > 0);
  const body = OD.defineBody(PICK, form);
  assert.deepEqual(body, { underlying_global_security_id: "u1", option_type: "call", strike: "660",
    expiry: "2026-12-18", exercise_style: "american", settlement: "physical", is_flex: false, multiplier: "100",
    exchange_listed: true, broad_based_index: false, is_mini: false });
  const html = renderToStaticMarkup(h(DefineContractForm, { env: PICK, form, setForm: noop, onSave: noop,
    onCancel: noop, busy: false, error: null }));
  assert.match(html, /Define a contract on SPY/);
  assert.match(html, /Save contract/);
});

test("option contract: the defined contract is selectable; the same terms again add no second line", () => {
  const resp = { contract: { global_security_id: "c1", option_type: "call", strike: "660", expiry: "2026-12-18",
    multiplier: "100", occ_symbol: null, exercise_style: "american" } };
  const first = OD.pickedFromDefine(PICK, resp);
  assert.equal(first.existed, false);
  const rows = OD.withContract(PICK.rows, first.row);
  assert.equal(rows.length, 1);
  const again = OD.pickedFromDefine({ ...PICK, rows }, resp);
  assert.equal(again.existed, true);
  assert.equal(OD.withContract(rows, again.row).length, 1);
  const code = src("components/heldaway/OptionContractPicker.jsx");
  assert.match(code, /\/api\/replication\/options\/contracts/);   // rep8's own route, never a second writer
});

// ── The Lows ────────────────────────────────────────────────────────────────────────────────────────────────────
test("lows: pickers search on ANY change of the text (paste / autofill), not on a key event or a button only", () => {
  assert.equal(TA.shouldSearch("SPY", ""), true);
  assert.equal(TA.shouldSearch("SPY", "SPY"), false);
  assert.equal(TA.shouldSearch("S", "", 2), false);
  const s = TA.sequence();
  const a = s.next();
  const b = s.next();
  assert.equal(s.isLatest(a), false);
  assert.equal(s.isLatest(b), true);
  for (const f of ["components/households/HouseholdManager.jsx", "components/tax/common.jsx",
    "components/execution/NewInstruction.jsx"]) {
    const code = src(f);
    assert.match(code, /useTypeahead\(/, f);
    assert.match(code, /onInput=\{\(e\) => (t\.)?setQuery\(e\.target\.value\)\}/, f);
    assert.doesNotMatch(code, /onKey(Up|Down|Press)=/, f);
  }
  const hook = src("components/ui/useTypeahead.js");
  assert.match(hook, /\}, \[query, tick\]\)/);   // driven by the VALUE, whatever changed it
  const opt = src("components/heldaway/OptionContractPicker.jsx");
  assert.match(opt, /\}, \[underlying\]\)/);       // the option picker searches when the underlying changes
});

test("lows: lists follow a re-read (no 'empty until reload'); the household card re-reads on every open", () => {
  for (const f of ["components/crm/tabs/EmploymentTab.jsx", "components/crm/tabs/TaxIdsTab.jsx",
    "components/crm/tabs/SocialTab.jsx"]) {
    assert.match(src(f), /useEffect\(\(\) => \{\n\s+if \(Array\.isArray\(initial\)\) setItems\(initial\);\n\s+\}, \[initial\]\)/, f);
  }
  assert.match(src("components/crm/tabs/ComplianceTab.jsx"), /if \(initial\) setRecord\(initial\);\n\s+\}, \[initial\]\)/);
  const hm = src("components/households/HouseholdManager.jsx");
  assert.match(hm, /if \(next\) await load\(\);/);
  assert.doesNotMatch(hm, /if \(next && !detail\) await load\(\);/);
  assert.match(hm, /cache: "no-store"/);
});
