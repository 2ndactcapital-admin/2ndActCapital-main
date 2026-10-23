// REP3 — the REAL model-management components rendered with react-dom/server
// from envelopes shaped exactly like routers/models.py returns them. Every
// write / approve / publish control (Upload model, Load from library, New
// composite, New draft, Upload a new version, Publish, Edit, Submit, Approve,
// Reject, Abandon, Copy, Subscribe, Confirm agreement, Save sleeves) must FAIL
// CLOSED: none without the server's own flag being exactly true AND, for a
// model or version, the action in the server's `actions` list; Approve /
// Reject also never for a maker (is_maker must be exactly false).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const lib = await import("../../lib/models.mjs");
const { default: ModelList } = await import("../../components/models/ModelList.jsx");
const { default: ModelPage } = await import("../../components/models/ModelPage.jsx");
const { default: UploadWizard, WizardReport } = await import("../../components/models/UploadWizard.jsx");
const { default: LibraryView } = await import("../../components/models/LibraryView.jsx");
const { default: UploadLog } = await import("../../components/models/UploadLog.jsx");
const menu = await import("../../lib/menuVisibility.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const controls = (html) => [
  ...(html.match(/<button[^>]*>[^<]*<\/button>/g) || []),
  ...(html.match(/<a [^>]*>[^<]*<\/a>/g) || []),
].map((b) => text(b).trim());

const VOCAB = {
  sources: [{ key: "upload", label: "Uploaded file" }, { key: "composite", label: "Composite (sleeves)" }],
  model_statuses: [{ key: "active", label: "Active" }, { key: "draft", label: "Draft" }],
  version_statuses: [{ key: "draft", label: "Draft" }, { key: "submitted", label: "Awaiting approval" },
    { key: "approved", label: "Approved" }],
  origins: [{ key: "upload", label: "Upload" }], upload_outcomes: [{ key: "draft_created", label: "Draft created" }],
  upload_sources: [{ key: "manual", label: "Uploaded by hand" }], cadences: [{ key: "monthly", label: "Monthly" }],
  shelves: [{ key: "platform", label: "Platform shelf" }, { key: "org", label: "Our shelf" }],
  follow_modes: [{ key: "live", label: "Follow its approved version" }], events: [],
  mapping_fields: [{ key: "ticker", label: "Ticker" }, { key: "weight", label: "Target Weight" }],
  validation_codes: [{ key: "unresolved", label: "Identifier not resolved (queued)" }],
  weight_bases: [{ key: "percent", label: "Percent (sums to 100)" }], editable: [], inline_editable: [],
};
const VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false, is_org_admin: false,
  can_publish_org: false, can_publish_platform: false, can_confirm_agreement: false,
  read_permission: "view_models", write_permission: "manage_models", approve_permission: "approve_models" };
const FULL = { ...VIEW, can_write: true, can_approve: true, is_org_admin: true, can_publish_org: true,
  can_confirm_agreement: true };
const SUPER = { ...FULL, is_super_admin: true, can_publish_platform: true };
const ROW = { id: "m1", name: "Core Equity", source: "upload", status: "active", live_version_no: 2, positions: 3,
  sleeves: 0, last_change: "2026-10-22T10:00:00" };
const LIST_ACTIONS = ["upload", "load_from_library", "create_composite"];
const LIST_BUTTONS = ["Upload model", "Load from library", "New composite"];

const POS = [
  { id: "p1", row_num: 2, raw_ticker: "AAA", target_weight: "0.6000000000", drift_min: "0.5", drift_max: "0.7",
    resolution_state: "resolved" },
  { id: "p2", row_num: 3, raw_cusip: "ZZ0000001", target_weight: "0.4000000000", resolution_state: "unresolved",
    resolution_note: "CUSIP not in the security master" },
];
const version = (status, extra = {}) => ({ id: `v-${status}`, model_id: "m1", version_no: status === "approved" ? 2 : 3,
  status, origin: "upload", maker_ids: ["u1"], totals: { positions: 2, unresolved: 1, cash: 0, effective_total: "1" },
  diff: { has_base: true, added: [], removed: [], changed: [{ key: "x", label: "AAA", from: "0.5", to: "0.6", delta: "0.1" }],
    threshold: "0.0001", turnover: "0.1" }, tradability: [{ text: "fractional_shares unknown at altruist" }], ...extra });
const vEnv = (status, actions, is_maker) => ({ version: version(status), positions: POS, sleeves: [], look_through: null,
  task: null, actions, is_maker });
const pageEnv = (permissions, { actions = [], openActions = [], isMaker = false, source = "upload" } = {}) => ({
  model: { id: "m1", name: "Core Equity", source, status: "active", base_currency: "USD" },
  versions: [version("submitted"), version("approved")],
  live: vEnv("approved", [], false),
  open: vEnv("submitted", openActions, isMaker),
  uploads: [], events: [], subscription: null, library_entries: [], available_children: [], fee_schedules: [],
  actions, permissions, vocabularies: VOCAB,
});
const PAGE_BUTTONS = ["New draft", "Upload a new version", "Publish to our shelf", "Publish to the platform shelf",
  "Submit for approval", "Approve", "Reject", "Abandon draft", "Edit", "Add a position", "Save sleeves", "Save row"];

test("models list: no Upload / Load from library / New composite unless can_write === true AND the action is listed", () => {
  for (const env of [
    { rows: [ROW], permissions: VIEW, vocabularies: VOCAB, actions: LIST_ACTIONS },   // view-only, actions present
    { rows: [ROW], vocabularies: VOCAB, actions: LIST_ACTIONS },                      // permissions lost
    { rows: [ROW], permissions: { ...FULL, can_write: "true" }, vocabularies: VOCAB, actions: LIST_ACTIONS },
    { rows: [ROW], permissions: FULL, vocabularies: VOCAB },                          // actions lost
    null,
  ]) {
    const b = controls(render(h(ModelList, { initialEnvelope: env })));
    for (const w of LIST_BUTTONS) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(env?.permissions)}`);
  }
  const html = render(h(ModelList, { initialEnvelope: { rows: [ROW], permissions: FULL, vocabularies: VOCAB,
    actions: LIST_ACTIONS } }));
  for (const w of LIST_BUTTONS) assert.ok(controls(html).includes(w), `${w} for a manage_models caller`);
  assert.match(text(html), /Core Equity/);
  assert.match(text(html), /Uploaded file/, "source label from the envelope vocabulary");
});

test("model page: a view-only envelope renders no write, approve or publish control", () => {
  const all = ["new_draft", "upload_version", "edit_details", "retire", "publish_org", "publish_platform"];
  for (const env of [
    pageEnv(VIEW, { actions: all, openActions: ["edit", "submit", "abandon", "approve", "reject"] }),
    pageEnv({}, { actions: all, openActions: ["edit", "submit", "abandon", "approve", "reject"] }),
    { ...pageEnv(FULL, { openActions: ["approve", "reject"] }), permissions: undefined },
  ]) {
    const b = controls(render(h(ModelPage, { initialEnvelope: env })));
    for (const w of PAGE_BUTTONS) assert.ok(!b.includes(w), `no ${w}: ${b}`);
  }
});

test("model page: Approve / Reject never for a maker, nor when is_maker is missing; present for a checker", () => {
  const maker = controls(render(h(ModelPage, { initialEnvelope: pageEnv(FULL, { openActions: ["approve", "reject"], isMaker: true }) })));
  assert.ok(!maker.includes("Approve") && !maker.includes("Reject"), "a maker sees no Approve / Reject");
  const missing = pageEnv(FULL, { openActions: ["approve", "reject"] });
  delete missing.open.is_maker;
  const m2 = controls(render(h(ModelPage, { initialEnvelope: missing })));
  assert.ok(!m2.includes("Approve"), "is_maker missing → fails closed");
  const noPerm = controls(render(h(ModelPage, { initialEnvelope: pageEnv({ ...FULL, can_approve: false }, {
    openActions: ["approve", "reject"] }) })));
  assert.ok(!noPerm.includes("Approve"), "without can_approve no Approve");
  const checker = controls(render(h(ModelPage, { initialEnvelope: pageEnv(FULL, { openActions: ["approve", "reject", "abandon"] }) })));
  assert.ok(checker.includes("Approve") && checker.includes("Reject"), `checker: ${checker}`);
  assert.ok(!checker.includes("Submit for approval"), "submit only when the server lists it");
});

test("model page: publish controls need the server's flag AND its action; platform also needs Super Admin", () => {
  const acts = ["publish_org", "publish_platform", "new_draft"];
  const org = controls(render(h(ModelPage, { initialEnvelope: pageEnv(FULL, { actions: acts }) })));
  assert.ok(org.includes("Publish to our shelf") && !org.includes("Publish to the platform shelf"), `org admin: ${org}`);
  const sa = controls(render(h(ModelPage, { initialEnvelope: pageEnv(SUPER, { actions: acts }) })));
  assert.ok(sa.includes("Publish to the platform shelf"));
  const noAct = controls(render(h(ModelPage, { initialEnvelope: pageEnv(SUPER, { actions: [] }) })));
  assert.ok(!noAct.includes("Publish to our shelf") && !noAct.includes("New draft"));
});

test("model page: a draft with edit for a manage_models caller shows Edit; positions, bands, diff and tradability render", () => {
  const env = pageEnv(FULL, { openActions: ["edit", "submit", "abandon"] });
  env.open = vEnv("draft", ["edit", "submit", "abandon"], true);
  const html = render(h(ModelPage, { initialEnvelope: env }));
  const b = controls(html);
  assert.ok(b.includes("Edit") && b.includes("Submit for approval") && b.includes("Abandon draft"), `${b}`);
  const t = text(html);
  assert.match(t, /60\.0000%/);
  assert.match(t, /50\.00% – 70\.00%/);
  assert.match(t, /No — CUSIP not in the security master/, "unresolved row flagged");
  assert.match(t, /turnover 10\.0000%/);
  assert.match(t, /fractional_shares unknown at altruist/);
});

test("upload wizard: renders nothing without can_write === true; the report shows row-numbered messages", () => {
  for (const env of [{ permissions: VIEW, vocabularies: VOCAB }, { vocabularies: VOCAB }, null,
    { permissions: { ...FULL, can_write: 1 }, vocabularies: VOCAB }]) {
    assert.equal(render(h(UploadWizard, { envelope: env })), "", `nothing for ${JSON.stringify(env?.permissions)}`);
  }
  const html = render(h(UploadWizard, { envelope: { permissions: FULL, vocabularies: VOCAB } }));
  assert.ok(controls(html).includes("Upload"));
  const rep = text(render(h(WizardReport, { envelope: { vocabularies: VOCAB }, report: {
    sheet: "CSV", header_row: 1, weight_basis: "percent", counts: { read: 2, resolved: 1, unresolved: 1, cash: 0 },
    messages: [{ code: "unresolved", severity: "warning", row: 3, text: "row 3: CUSIP ZZ0000001 not in the security master" }],
    positions: POS }, tradability: [{ text: "fractional_shares unknown at altruist" }], diff: null })));
  assert.match(rep, /Row 3 — Identifier not resolved \(queued\): row 3: CUSIP ZZ0000001/);
  assert.match(rep, /Percent \(sums to 100\)/i);
  assert.match(rep, /fractional_shares unknown at altruist/);
});

test("library: no Copy / Subscribe / Confirm agreement without the flags; agreement gates loading", () => {
  const entry = { id: "e1", shelf: "platform", title: "Hollis Core", provider: "Hollisworks", update_cadence: "monthly",
    requires_agreement: true, agreement_on_file: false, usage_count: 3, loaded_as: [] };
  const free = { ...entry, id: "e2", shelf: "org", title: "Our Income", requires_agreement: false };
  const viewer = controls(render(h(LibraryView, { initialEnvelope: { rows: [entry, free], permissions: VIEW, vocabularies: VOCAB } })));
  for (const w of ["Copy", "Subscribe", "Confirm agreement on file"]) assert.ok(!viewer.includes(w), `viewer: no ${w}`);
  const lost = controls(render(h(LibraryView, { initialEnvelope: { rows: [entry, free], vocabularies: VOCAB } })));
  assert.ok(!lost.includes("Copy"));
  const writer = render(h(LibraryView, { initialEnvelope: { rows: [entry], permissions: { ...FULL, can_confirm_agreement: false },
    vocabularies: VOCAB } }));
  assert.ok(!controls(writer).includes("Copy"), "agreement not on file → no Copy");
  assert.ok(!controls(writer).includes("Confirm agreement on file"), "not an org admin → cannot confirm");
  const admin = controls(render(h(LibraryView, { initialEnvelope: { rows: [entry, free], permissions: FULL, vocabularies: VOCAB } })));
  assert.ok(admin.includes("Confirm agreement on file") && admin.includes("Copy") && admin.includes("Subscribe"));
  const onFile = controls(render(h(LibraryView, { initialEnvelope: { rows: [{ ...entry, agreement_on_file: true }],
    permissions: FULL, vocabularies: VOCAB } })));
  assert.ok(onFile.includes("Copy") && !onFile.includes("Confirm agreement on file"));
  const t = text(render(h(LibraryView, { initialEnvelope: { rows: [entry, free], permissions: VIEW, vocabularies: VOCAB } })));
  assert.match(t, /Platform shelf/);
  assert.match(t, /Our shelf/);
  assert.match(t, /used by 3/);
});

test("upload log: export only for can_read === true; every outcome label from the vocabulary", () => {
  const row = { id: "u1", uploaded_at: "2026-10-22T10:00:00", file_name: "m.csv", source: "manual", outcome: "draft_created",
    rows_read: 3, rows_resolved: 2, rows_unresolved: 1, rows_cash: 0, messages: [{ row: 4, text: "unresolved" }] };
  const yes = render(h(UploadLog, { initialEnvelope: { rows: [row], permissions: VIEW, vocabularies: VOCAB } }));
  assert.ok(controls(yes).includes("Export CSV"));
  assert.match(text(yes), /Draft created/);
  assert.match(text(yes), /Row 4: unresolved/);
  const no = render(h(UploadLog, { initialEnvelope: { rows: [row], permissions: { can_read: "true" }, vocabularies: VOCAB } }));
  assert.ok(!controls(no).includes("Export CSV"));
});

test("exact decimal display: fractions shown as percentages without floats", () => {
  assert.equal(lib.pctText("0.0450000000"), "4.50%");
  assert.equal(lib.pctText("0.333333", 4), "33.3333%");
  assert.equal(lib.pctText("0.1", 2), "10.00%");
  assert.equal(lib.sumPct(["33.3333", "66.6667"]), "100");
  assert.equal(lib.sumPct(["60", "39.99"]), "99.99");
});

test("menu: Models gated on view_models (strict) in the Portfolio group", () => {
  const item = menu.MENU_ITEMS.find((i) => i.href === "/portfolio/models");
  assert.ok(item, "Models tab exists");
  assert.equal(item.group, "portfolio");
  assert.deepEqual(item.gate, { perm: "view_models", strict: true });
});
