// REP14 — Replication → Section 351: the REAL components rendered with react-dom/server from envelopes shaped
// exactly like the API returns them (routers/section351.py). Every write control must FAIL CLOSED: none without the
// server's own flag being exactly true AND the action listed; a lost envelope renders nothing writable; the maker is
// never offered approve (the server never lists it for them); labels come from the vocabularies; the timeline
// geometry and the gate lines are computed exactly.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/section351.mjs");
const { CandidatesView, PlanView, ProfilesView } = await import("../../components/replication/Section351.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].replace(/&#x27;/g, "'").trim());

const LABEL = "estimate — confirm with counsel";
const VOCAB = {
  owner_types: [{ key: "individual", label: "Individual (vocab)" }, { key: "partnership", label: "Partnership / LLC (vocab)" }],
  asset_classes: [{ key: "crypto", label: "Crypto asset (vocab)" }, { key: "government", label: "Government security" }],
  cash_rules: [{ key: "not_contributed", label: "Cash stays (vocab)" }],
  plan_statuses: [{ key: "pending_approval", label: "Awaiting approval (vocab)" }, { key: "draft", label: "Draft (vocab)" }],
  transfer_statuses: [{ key: "sent", label: "Sent to the custodian (vocab)" }],
  verdicts: [{ key: "pass", label: "Passes (vocab)" }, { key: "fail", label: "Fails (vocab)" }],
  steps: [], editable: ["plan", "accounts", "consent", "tax_adviser", "us_person", "boot", "acknowledge", "steps"],
  admin_editable: [], inline_editable: [], rules: [], label: LABEL, open_items: ["The default disclosure text — counsel reviews it."],
};
const VIEW_ONLY_VOCAB = { ...VOCAB, editable: [], admin_editable: [] };
const WRITER = { can_read: true, can_write: true, can_approve: true, can_admin: true, is_super_admin: false };
const VIEWER = { can_read: true, can_write: false, can_approve: false, can_admin: false, is_super_admin: false };

const TEST = {
  verdict: "fail", largest_issuer_pct: "25.01%", top_five_pct: "40.00%", margin_one_pct: "-0.01%", margin_five_pct: "10.00%",
  total_assets: "100000.00", cash_excluded: "0.00", reasons: ["REP14 Big Co is 25.01% of the contribution"], blocked: [],
  issuers: [{ issuer_key: "cik:1", label: "REP14 Big Co", weight_pct: "25.01%", value: "25010.00", direct: "20000.00",
    look_through: "5010.00", via_funds: ["REP14 ETF"] }],
  lines: [{ label: "REP14 Big Co", valuation_source: "entered value", valuation_as_of: "2026-11-06", holdings_as_of: null }],
  excluded: [{ label: "REP14 Coin", reason: "ineligible asset (Crypto asset)" }], label: LABEL,
};
const ACCOUNT = {
  id: "pa1", account_id: "a1", account: "****1401", owner_type: "partnership", eligibility: { decision: "needs_sponsor_review", reason: "needs review" },
  us_person: "unconfirmed", contributed_value: "100000.00", cash_to_member: "0", status: "active", consent: null, tax_adviser: null,
  test_verdict: "fail", test_result: TEST,
  disclosure: { in_force: true, current: false, reason: "a newer version was published since the acknowledgement — acknowledge it again",
    resolved: { title: "Section 351 ETF conversion (exit)", version_no: 2, version_id: "v2", text: "REP14 library text v2" } },
};
const READINESS = {
  ok: false, plan_gates: [{ gate: "accounts", ok: true, detail: "1 contributing account(s)" }, { gate: "control", ok: false, detail: "contributors 79.99%" }],
  accounts: [{ account_id: "a1", ok: false, gates: [{ gate: "consent", ok: false, detail: "the member's written consent is missing" }] }],
  control: { contributors_share_pct: "79.99%", minimum: "0.80", contributed: "79990.00", cash_to_contributors: "1000.00", seed_capital: "20010.00",
    etf_total: "100000.00", boot_taxable: true, boot_note: "Cash or other property received is boot — TAXABLE", label: LABEL },
};
const TIMELINE = { start: "2026-01-01", note: "6–9 months", steps: [
  { id: "s1", seq: 1, label: "Counsel engaged", due_on: "2026-01-15", status: "done" },
  { id: "s2", seq: 2, label: "Sponsor / trust selected", due_on: "2026-01-31", status: "open" },
  { id: "s8", seq: 8, label: "Confirmation", due_on: "2026-08-29", status: "open" }] };
const plan = (over = {}) => ({
  plan: { id: "p1", name: "REP14 Pool", target_etf: "REP14 New ETF", status: "pending_approval", valuation_date: "2026-11-06",
    sponsor_profile_version: 1, maker_id: "u1", submitted_by: "u1" },
  accounts: [ACCOUNT], readiness: READINESS, timeline: TIMELINE,
  basis_records: [{ id: "b1", version_no: 1, live: true, totals: { fmv: "100000.00", basis: "40000.00", etf_basis: "40000.00", reconciles_to_plan: true } }],
  transfers: [{ id: "t1", account_id: "a1", tax_lot_id: "lot00001xx", quantity: "100", basis: "4000.00", acquired_date: "2015-01-02", status: "sent",
    etf_shares: null, realized_gain: null }], open_items: VOCAB.open_items, ...over });

test("a view-only caller sees every figure and no write control", () => {
  const env = { ...plan(), permissions: VIEWER, vocabularies: VIEW_ONLY_VOCAB, actions: [] };
  const el = h(PlanView, { envelope: env, onAct: () => {} });
  assert.deepEqual(buttons(el), []);
  const out = render(el);
  assert.ok(out.includes("25.01%") && out.includes("REP14 Big Co") && out.includes("79.99%"));
  assert.ok(!out.includes("Acknowledge this version"));
  assert.deepEqual(Object.values(L.planControls(env)).filter(Boolean), []);
});

test("a lost or malformed envelope fails closed everywhere", () => {
  for (const env of [null, undefined, {}, { permissions: null }, { permissions: WRITER }, { permissions: { can_write: "true" }, actions: ["edit", "submit"] }]) {
    assert.deepEqual(Object.values(L.planControls(env)).filter(Boolean), [], JSON.stringify(env));
    assert.deepEqual(Object.values(L.candidateControls(env)).filter(Boolean), []);
    assert.deepEqual(Object.values(L.profileControls(env)).filter(Boolean), []);
    assert.equal(L.thresholdControls(env).save, false);
  }
  assert.equal(L.allReady(undefined), false);
  assert.deepEqual(L.gateLines(undefined), [{ scope: "plan", gate: "readiness", ok: false, detail: "readiness not available" }]);
});

test("approve / reject render only when the server lists them — never for the maker", () => {
  const maker = { ...plan(), permissions: WRITER, vocabularies: VOCAB, actions: ["cancel", "export_basis_record"] };
  const b1 = buttons(h(PlanView, { envelope: maker, onAct: () => {} }));
  assert.ok(!b1.includes("Approve") && !b1.includes("Reject"));
  assert.ok(b1.includes("Cancel the plan"));
  const checker = { ...maker, actions: ["approve", "reject"] };
  const b2 = buttons(h(PlanView, { envelope: checker, onAct: () => {} }));
  assert.ok(b2.includes("Approve") && b2.includes("Reject"));
  const noPerm = { ...checker, permissions: { ...WRITER, can_approve: false } };
  assert.ok(!buttons(h(PlanView, { envelope: noPerm, onAct: () => {} })).includes("Approve"));
});

test("labels come from the vocabularies, the estimate label is the server's", () => {
  const env = { ...plan(), permissions: VIEWER, vocabularies: VIEW_ONLY_VOCAB, actions: [] };
  const out = render(h(PlanView, { envelope: env, onAct: () => {} }));
  for (const s of ["Awaiting approval (vocab)", "Partnership / LLC (vocab)", "Fails (vocab)", "Sent to the custodian (vocab)", LABEL]) {
    assert.ok(out.includes(s), s);
  }
  assert.equal(L.labelOf(VOCAB.owner_types, "joint"), "joint");
  assert.equal(L.estimateLabel({ vocabularies: { label: LABEL } }), LABEL);
  assert.equal(L.estimateLabel({}), "");
});

test("the disclosure shows the library's in-force version and the re-acknowledge note; acknowledge needs the editable vocabulary", () => {
  const env = { ...plan({ plan: { ...plan().plan, status: "draft" } }), permissions: WRITER, vocabularies: VOCAB, actions: ["edit", "mark_ready"] };
  const el = h(PlanView, { envelope: env, onAct: () => {} });
  const out = render(el);
  assert.ok(out.includes("REP14 library text v2") && out.includes("version 2") && out.includes("acknowledge it again"));
  assert.ok(buttons(el).includes("Acknowledge this version"));
  assert.ok(buttons(el).includes("Record consent") && buttons(el).includes("Sponsor accepted"));
  const noAck = { ...env, vocabularies: VIEW_ONLY_VOCAB };
  assert.ok(!buttons(h(PlanView, { envelope: noAck, onAct: () => {} })).includes("Acknowledge this version"));
  const none = L.disclosureLine({ disclosure: { in_force: false, reason: "no section351.exit disclosure version is published" } });
  assert.deepEqual([none.inForce, none.current, none.text], [false, false, ""]);
});

test("gate lines: plan gates first, then each account by its masked name; boot shown taxable", () => {
  const g = L.gateLines(READINESS, { a1: "****1401" });
  assert.deepEqual(g.map((x) => [x.scope, x.gate, x.ok]), [["plan", "accounts", true], ["plan", "control", false], ["****1401", "consent", false]]);
  assert.equal(L.allReady(READINESS), false);
  assert.equal(L.allReady({ ...READINESS, ok: true }), false, "a server 'ok' with an open gate line is still not ready");
  const out = render(h(PlanView, { envelope: { ...plan(), permissions: VIEWER, vocabularies: VIEW_ONLY_VOCAB, actions: [] }, onAct: () => {} }));
  assert.ok(out.includes("TAXABLE") && out.includes("Not ready."));
});

test("timeline geometry: bars from the previous due date, over at least nine months", () => {
  const t = L.timelineBars(TIMELINE);
  assert.equal(t.spanDays, 274);   // last due day 240 < 274 (nine months) → the span is nine months
  assert.deepEqual(t.bars.map((b) => [b.id, b.left, b.width]), [
    ["s1", 0, +(14 / 274 * 100).toFixed(2)], ["s2", +(14 / 274 * 100).toFixed(2), +(16 / 274 * 100).toFixed(2)],
    ["s8", +(30 / 274 * 100).toFixed(2), +(210 / 274 * 100).toFixed(2)]]);
  assert.equal(t.months[0].left, 0);
  assert.deepEqual(L.timelineBars({}), { bars: [], spanDays: 0, months: [] });
  assert.deepEqual(L.timelineBars({ start: "2026-01-01", steps: [{ id: "x", label: "x" }] }).bars, []);
});

test("transfer routing: controls only with the actions; the adapter's 409 refusal names the manual route", () => {
  const env = { ...plan({ plan: { ...plan().plan, status: "transferring" } }), permissions: WRITER, vocabularies: VOCAB,
    actions: ["route_manual", "route_adapter", "instruction_sheet", "confirm_transfer"] };
  const html = renderToStaticMarkup(h(PlanView, { envelope: env, onAct: () => {},
    routeError: L.adapterRefusal({ error: "x", detail: { message: "the zz adapter refuses: zz does not declare in_kind_transfers", manual_route: true } }) }));
  const out = text(html);
  assert.ok(out.includes("does not declare in_kind_transfers") && out.includes("The manual route is offered."));
  assert.ok(html.includes('href="/api/section351/plans/p1/instruction-sheet"'));
  assert.ok(buttons(h(PlanView, { envelope: env, onAct: () => {} })).includes("Record the custodian's confirmation"));
  assert.deepEqual(L.adapterRefusal({ error: "plain" }), { message: "plain", manualOffered: false });
  const viewer = { ...env, permissions: VIEWER };
  const b = buttons(h(PlanView, { envelope: viewer, onAct: () => {} }));
  assert.ok(!b.includes("Mark sent (manual)") && !b.includes("Send to the custodian") && !b.includes("Record the custodian's confirmation"));
});

test("basis-record links go through the web proxy, never FastAPI", () => {
  assert.deepEqual(L.basisLinks({ id: "b 1" }), { csv: "/api/section351/basis-records/b%201/file/csv", pdf: "/api/section351/basis-records/b%201/file/pdf" });
  assert.equal(L.basisLinks({}), null);
  const html = renderToStaticMarkup(h(PlanView, { envelope: { ...plan(), permissions: VIEWER, vocabularies: VIEW_ONLY_VOCAB, actions: [] }, onAct: () => {} }));
  assert.ok(html.includes('href="/api/section351/basis-records/b1/file/csv"') && !html.includes("/api/v1/"));
});

test("candidates and thresholds: run-dry checks shown; edit and fix-up only with the server's flags", () => {
  const cand = { thresholds: { version_no: 2, source: "org", max_harvestable_loss_pct: "0.02", min_unrealized_gain_pct: "0.25", min_years_since_inception: "3" },
    rule: "harvesting has run dry when …", eligible: [{ account_id: "a1", account: "****1401", valuation_as_of: "2026-11-06",
      metrics: { appreciated_value: "90000.00", unrealized_gain: "50000.00", harvestable_loss: "100.00",
        decay: { estimated: true, year_1: 2023, year_1_harvested: "9000.00", this_year: 2026, this_year_harvested: "900.00", decay_pct: "90.00%" } },
      run_dry: { run_dry: true, checks: [{ key: "harvestable_loss", met: true, rule: "harvestable loss ≤ threshold", value_pct: "0.10%", threshold_pct: "2.00%" }] } }],
    ineligible: [{ account_id: "a2", account: "****9999", reasons: [{ code: "average_cost", reason: "average cost only" }] }] };
  const viewEnv = { ...cand, permissions: VIEWER, vocabularies: VIEW_ONLY_VOCAB, actions: [] };
  const el = h(CandidatesView, { envelope: viewEnv, thresholdsEnvelope: { permissions: VIEWER, actions: [] } });
  assert.deepEqual(buttons(el), []);
  const out = render(el);
  assert.ok(out.includes("Harvesting has run dry") && out.includes("Year 1 (2023)") && out.includes("90.00%") && out.includes("average cost only"));
  const adminEnv = { ...cand, permissions: WRITER, vocabularies: VOCAB, actions: ["create_plan", "fixup", "edit_thresholds"] };
  const PROFILES = { rows: [{ id: "pf1", name: "REP14 Sponsor", version_no: 2, live: true, status: "active" },
    { id: "pf0", name: "REP14 Sponsor", version_no: 1, live: false, status: "active" }] };
  const noProfiles = buttons(h(CandidatesView, { envelope: adminEnv, thresholdsEnvelope: { permissions: WRITER, actions: ["save_thresholds"] } }));
  assert.ok(!noProfiles.includes("Fix-up proposal"), "no sponsor profile → no silent default, no fix-up form");
  const html = renderToStaticMarkup(h(CandidatesView, { envelope: adminEnv, profilesEnvelope: PROFILES,
    thresholdsEnvelope: { permissions: WRITER, actions: ["save_thresholds"] } }));
  const b = buttons(h(CandidatesView, { envelope: adminEnv, profilesEnvelope: PROFILES, thresholdsEnvelope: { permissions: WRITER, actions: ["save_thresholds"] } }));
  assert.ok(b.includes("Fix-up proposal") && b.includes("Save a new version"));
  assert.ok(html.includes('value="pf1"') && !html.includes('value="pf0"'), "only the live version is offered");
  assert.ok(html.includes("Valuation date"));
  assert.deepEqual(L.fixupProfiles(PROFILES).map((r) => r.id), ["pf1"]);
  assert.deepEqual(L.fixupProfiles(undefined), []);
  const noThr = buttons(h(CandidatesView, { envelope: adminEnv, thresholdsEnvelope: { permissions: WRITER, actions: [] } }));
  assert.ok(!noThr.includes("Save a new version"));
});

test("sponsor profiles: forms only for an org admin with the actions; labels from the vocabularies", () => {
  const rows = [{ id: "pf1", profile_key: "k1", name: "REP14 Sponsor", version_no: 3, status: "active", live: true,
    eligible_owner_types: ["individual"], review_owner_types: ["partnership"], ineligible_assets: ["crypto"], cash_rule_label: "Cash stays (vocab)" }];
  const view = h(ProfilesView, { envelope: { rows, defaults: {}, permissions: VIEWER, vocabularies: VIEW_ONLY_VOCAB, actions: [] } });
  assert.deepEqual(buttons(view), []);
  const out = render(view);
  assert.ok(out.includes("Individual (vocab)") && out.includes("Crypto asset (vocab)") && out.includes("version 3"));
  const admin = buttons(h(ProfilesView, { envelope: { rows, defaults: {}, permissions: WRITER, vocabularies: VOCAB, actions: ["create_profile", "new_version"] } }));
  assert.ok(admin.includes("New version") && admin.includes("Create the profile"));
  const writerOnly = buttons(h(ProfilesView, { envelope: { rows, defaults: {}, permissions: { ...WRITER, can_admin: false }, vocabularies: VOCAB,
    actions: ["create_profile", "new_version"] } }));
  assert.deepEqual(writerOnly, []);
});

test("addable_accounts: the picker only for a writer on a draft (edit + add_account); missing or malformed → none", () => {
  const ADD = [{ account_id: "a9", account: "****0009", custodian_code: "ZZ" }, { account: "no id" }, null];
  const draft = { ...plan({ plan: { ...plan().plan, status: "draft" } }), addable_accounts: ADD, permissions: WRITER, vocabularies: VOCAB,
    actions: ["edit", "add_account"] };
  assert.deepEqual(L.addableAccounts(draft).map((a) => a.account_id), ["a9"]);
  const html = renderToStaticMarkup(h(PlanView, { envelope: draft, onAct: () => {} }));
  assert.ok(html.includes('value="a9"') && html.includes("****0009 (ZZ)") && buttons(h(PlanView, { envelope: draft, onAct: () => {} })).includes("Add account"));
  for (const env of [{ ...draft, addable_accounts: undefined }, { ...draft, addable_accounts: "a9" }, { ...draft, addable_accounts: [] },
    { ...draft, actions: ["edit"] }, { ...draft, permissions: VIEWER }, { ...draft, permissions: { ...WRITER, can_write: 1 } }]) {
    assert.deepEqual(L.addableAccounts(env), []);
    assert.ok(!buttons(h(PlanView, { envelope: env, onAct: () => {} })).includes("Add account"));
  }
});

test("tracker steps: Mark done on the step's 'done', the edit form on 'edit', both needing can_write", () => {
  const steps = [{ id: "s1", seq: 1, label: "Counsel engaged", due_on: "2026-01-15", status: "open", actions: ["done", "edit"] },
    { id: "s2", seq: 2, label: "Sponsor / trust selected", due_on: "2026-01-31", status: "open", actions: ["edit"] },
    { id: "s3", seq: 3, label: "Custodian", due_on: "2026-03-01", status: "done", actions: [] }];
  const env = { ...plan({ timeline: { ...TIMELINE, steps } }), permissions: WRITER, vocabularies: VOCAB, actions: [] };
  const b = buttons(h(PlanView, { envelope: env, onAct: () => {} }));
  assert.equal(b.filter((x) => x === "Mark done").length, 1);
  assert.equal(b.filter((x) => x === "Save step").length, 2);
  assert.deepEqual(L.stepControls(env, steps[0]), { done: true, edit: true });
  assert.deepEqual(L.stepControls(env, steps[2]), { done: false, edit: false });
  assert.deepEqual(L.stepControls(env, { actions: "done" }), { done: false, edit: false });
  assert.deepEqual(L.stepControls({ ...env, permissions: VIEWER }, steps[0]), { done: false, edit: false });
  assert.deepEqual(L.stepControls(undefined, steps[0]), { done: false, edit: false });
  const vb = buttons(h(PlanView, { envelope: { ...env, permissions: VIEWER }, onAct: () => {} }));
  assert.ok(!vb.includes("Mark done") && !vb.includes("Save step"));
});

test("the step edit PATCHes, and approve's note is separate from the reject / cancel reason", () => {
  const calls = [];
  const steps = [{ id: "s1", seq: 1, label: "Counsel engaged", due_on: "2026-01-15", status: "open", actions: ["edit"] }];
  const env = { ...plan({ timeline: { ...TIMELINE, steps } }), permissions: WRITER, vocabularies: VOCAB, actions: ["approve", "reject", "cancel"] };
  const html = renderToStaticMarkup(h(PlanView, { envelope: env, onAct: (...a) => calls.push(a) }));
  assert.ok(html.includes('placeholder="Approval note (optional)"') && html.includes('placeholder="Reason (reject / cancel)"'));
  assert.ok(html.includes('placeholder="Owner (user id)"') && html.includes('placeholder="Due on"'));
  // reject / cancel are disabled until a reason is typed (the API needs one)
  assert.match(html, /<button[^>]*disabled=""[^>]*>Reject<\/button>/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>Cancel the plan<\/button>/);
  const consent = renderToStaticMarkup(h(PlanView, { envelope: { ...plan({ plan: { ...plan().plan, status: "draft" } }), permissions: WRITER,
    vocabularies: VOCAB, actions: ["edit"] }, onAct: () => {} }));
  assert.ok(consent.includes('placeholder="Chancery document id"'));
});
