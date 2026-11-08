// DISCLOSURES1 — the disclosure library's screens, client side: the REAL lib and components rendered in Node from
// envelopes written by hand in the API's shape (never imported from it). The list (in force + why), a key's
// versions (status, maker / checker, file link, summary), Upload → a draft only for a writer, Publish only where the
// server listed it (never the uploader's own), an override from a household page, the long/short panel's library
// version and its re-acknowledgement, the nav tab, and the proxy route. Every control FAILS CLOSED.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const D = await import("../../lib/disclosures.mjs");
const { DisclosureList, KeyView, RecordDisclosuresView, UploadForm, VersionRow } =
  await import("../../components/disclosures/Disclosures.jsx");
const { LongShortProposalView } = await import("../../components/replication/LongShortPanel.jsx");
const MV = await import("../../lib/menuVisibility.mjs");

const src = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const VOCAB = {
  scopes: [{ key: "platform", label: "Platform default" }, { key: "org", label: "Org override" },
    { key: "household", label: "Household override" }, { key: "account", label: "Account override" }],
  statuses: [{ key: "draft", label: "Draft — awaiting a second person to publish" }, { key: "published", label: "Published" },
    { key: "retired", label: "Retired" }],
  audiences: [{ key: "staff", label: "Staff" }, { key: "member", label: "Members" }, { key: "both", label: "Staff and members" }],
  file_types: ["docx", "html", "md", "pdf"], max_bytes: 5242880, editable: ["upload", "publish", "retire"],
  inline_editable: [], manageable_scopes: ["org", "household", "account"],
};
const VIEW_VOCAB = { ...VOCAB, editable: [], manageable_scopes: [] };
const WRITER = { can_read: true, can_write: true, is_super_admin: false, read_permission: "view_disclosures",
  write_permission: "manage_disclosures" };
const READER = { ...WRITER, can_write: false };

const LIST = {
  rows: [
    { key: "long_short.risks", title: "Long/short — risks", audience: "both", why: "Org override",
      in_force: { source: "library", why: "Org override", version_no: 2, text: "- [pretax_underperformance] DV1 org risks" } },
    { key: "tax.estimate_label", title: "Tax estimates", audience: "both", why: "Platform default",
      in_force: { source: "library", why: "Platform default", version_no: 1, text: "Estimate — DV1 platform" } },
    { key: "prices.internal_use_notice", title: "Price data", audience: "staff",
      why: "Built-in text (no library version in force)",
      in_force: { source: "fallback", why: "Built-in text (no library version in force)", version_no: null, text: "End-of-day …" } },
  ],
  permissions: WRITER, vocabularies: VOCAB, actions: ["upload"],
};

const V_DRAFT_OTHER = { id: "v2", scope: "org", version_no: 2, status: "draft", effective_from: "2026-11-08",
  drafted_by: "u-maker", drafted_by_name: "DV1 Maker", file_name: "risks-v2.pdf", file_href: "/api/disclosures/versions/v2/file",
  content_sha256: "ab".repeat(32), summary: "DV1 draft summary", actions: ["publish", "retire"] };
const V_DRAFT_MINE = { ...V_DRAFT_OTHER, id: "v3", version_no: 3, file_href: "/api/disclosures/versions/v3/file", actions: ["retire"] };
const V_PUB = { ...V_DRAFT_OTHER, id: "v1", version_no: 1, file_name: "risks-v1.pdf", file_href: "/api/disclosures/versions/v1/file", status: "published", published_by: "u-checker",
  published_by_name: "DV1 Checker", in_force: true, actions: ["retire"] };
const KEY_ENV = { disclosure: { key: "long_short.risks", title: "Long/short — risks", audience: "both", description: "…" },
  in_force: { source: "library", why: "Org override", version_no: 1 }, versions: [V_DRAFT_OTHER, V_DRAFT_MINE, V_PUB],
  permissions: WRITER, vocabularies: VOCAB, actions: ["upload"] };

test("the list shows every key with what is in force for this org and WHY (org override / platform default / built-in)", () => {
  const out = render(h(DisclosureList, { envelope: LIST }));
  assert.ok(out.includes("Long/short — risks") && out.includes("Org override — version 2"), out);
  assert.ok(out.includes("Platform default — version 1") && out.includes("Estimate — DV1 platform"), out);
  assert.ok(out.includes("Built-in text (no library version in force)"), out);
  assert.ok(out.includes("Staff and members") && out.includes("Staff"), out);
  assert.equal(render(h(DisclosureList, { envelope: null })).includes("No disclosures"), true);
});

test("a key's versions show status, effective date, maker and checker, the stored file and the summary", () => {
  const out = render(h(KeyView, { envelope: KEY_ENV }));
  assert.ok(out.includes("Org override · version 1 — Published (in force)"), out);
  assert.ok(out.includes("drafted by DV1 Maker") && out.includes("published by DV1 Checker"), out);
  assert.ok(out.includes("Draft — awaiting a second person to publish"), out);
  const html = renderToStaticMarkup(h(KeyView, { envelope: KEY_ENV }));
  assert.ok(html.includes('href="/api/disclosures/versions/v1/file"') && out.includes("risks-v2.pdf"), html);
  assert.ok(out.includes("DV1 draft summary"), out);
});

test("Upload → draft: the form renders for a writer only; view-only, a missing action or no envelope → no form", () => {
  assert.ok(render(h(UploadForm, { envelope: KEY_ENV })).includes("Upload a new version (draft)"));
  assert.ok(buttons(h(UploadForm, { envelope: KEY_ENV })).includes("Upload draft"));
  assert.equal(renderToStaticMarkup(h(UploadForm, { envelope: { ...KEY_ENV, permissions: READER, vocabularies: VIEW_VOCAB } })), "");
  assert.equal(renderToStaticMarkup(h(UploadForm, { envelope: { ...KEY_ENV, actions: [] } })), "");
  assert.equal(renderToStaticMarkup(h(UploadForm, { envelope: { ...KEY_ENV, vocabularies: { ...VOCAB, editable: [] } } })), "");
  assert.equal(renderToStaticMarkup(h(UploadForm, { envelope: null })), "");
  // can_write must be exactly true — a truthy string is not a grant
  assert.equal(D.uploadControls({ ...KEY_ENV, permissions: { can_write: "true" } }).upload, false);
  assert.deepEqual(D.uploadControls(KEY_ENV).scopes, ["org", "household", "account"]);
});

test("Publish by ANOTHER user: the button shows only where the server listed it — never on the uploader's own draft", () => {
  assert.deepEqual(buttons(h(VersionRow, { envelope: KEY_ENV, version: V_DRAFT_OTHER })), ["Publish", "Retire"]);
  assert.deepEqual(buttons(h(VersionRow, { envelope: KEY_ENV, version: V_DRAFT_MINE })), ["Retire"]);
  assert.deepEqual(buttons(h(VersionRow, { envelope: { ...KEY_ENV, permissions: READER }, version: V_DRAFT_OTHER })), []);
  assert.deepEqual(D.versionControls(KEY_ENV, { actions: [] }), { publish: false, retire: false });
  assert.deepEqual(D.versionControls(null, V_DRAFT_OTHER), { publish: false, retire: false });
});

const RECORD = {
  record: { type: "household", id: "hh-1" },
  rows: [{ key: "long_short.risks", title: "Long/short — risks", audience: "both", why: "Household override",
    in_force: { source: "library", why: "Household override", version_no: 1 },
    overrides: [{ ...V_PUB, scope: "household", household_id: "hh-1", actions: ["retire"] }] }],
  permissions: WRITER, vocabularies: VOCAB, actions: ["upload_override"],
};

test("an override from a household page: what is in force and why, its overrides, Upload fixed to the household", () => {
  const out = render(h(RecordDisclosuresView, { envelope: RECORD }));
  assert.ok(out.includes("Household override — version 1"), out);
  assert.ok(out.includes("Household override · version 1 — Published"), out);
  assert.ok(out.includes("Upload a new version (draft)"), out);
  const html = renderToStaticMarkup(h(RecordDisclosuresView, { envelope: RECORD }));
  assert.ok(!html.includes("<select"), "the scope is fixed to the household — no scope picker");
  const ro = render(h(RecordDisclosuresView, { envelope: { ...RECORD, permissions: READER, vocabularies: VIEW_VOCAB, actions: [] } }));
  assert.ok(!ro.includes("Upload a new version") && !ro.includes("Retire"), ro);
  const body = D.uploadBody({ scope: "household", file_name: "hh.md", content_base64: "ZHYx", summary: "S", household_id: "hh-1" });
  assert.deepEqual(body, { scope: "household", file_name: "hh.md", content_base64: "ZHYx", summary: "S", household_id: "hh-1" });
  assert.equal("org_id" in body, false, "the body never carries an org_id");
});

test("file rules come from the server's vocabularies (type and size)", () => {
  assert.equal(D.fileAccepted(KEY_ENV, "a.pdf", 10), true);
  assert.equal(D.fileAccepted(KEY_ENV, "a.htm", 10), true);
  assert.equal(D.fileAccepted(KEY_ENV, "a.exe", 10), false);
  assert.equal(D.fileAccepted(KEY_ENV, "a.pdf", 0), false);
  assert.equal(D.fileAccepted(KEY_ENV, "a.pdf", 5242881), false);
  assert.equal(D.fileAccepted(null, "a.pdf", 10), false);
});

const LS_DETAILS = {
  disclosures: { structure: "130% long", risks: [{ key: "pretax_underperformance", text: "built-in six risk" }],
    borrow_estimate: { text: "Estimated borrow" } },
  margin: { initial: { text: "custodian to confirm" }, maintenance: { text: "custodian to confirm" } },
  member_summary: "For account ****: …", tracking_error: { estimated: false, text: "needs prices" }, borrow: { lines: [] },
  cost_comparison: [], blocking_labels: [], constructive_sale: [], acknowledged_at: "2026-11-08T10:00:00+00:00",
  acknowledgement_items: [{ key: "pretax_underperformance", label: "DV1 v2 risk" }, { key: "member_summary", label: "The member summary" }],
  disclosure_library: { key: "long_short.risks", source: "library", why: "Org override", version_no: 2, title: "Long/short — risks",
    file_href: "/api/disclosures/versions/v2/file", required: true, current: false,
    acknowledged: { version_id: "v1" }, risks: [{ key: "pretax_underperformance", text: "DV1 v2 risk" }] },
};

test("the long/short proposal shows the library version in force and asks to acknowledge again after a new one", () => {
  const env = { details: LS_DETAILS, permissions: { can_write: true, is_maker: true }, actions: ["acknowledge"], vocabularies: {} };
  const out = render(h(LongShortProposalView, { envelope: env }));
  assert.ok(out.includes("Long/short — risks — version 2 (Org override)"), out);
  assert.ok(out.includes("A newer version was published since the acknowledgement"), out);
  assert.ok(out.includes("DV1 v2 risk") && !out.includes("built-in six risk"), out);
  const cur = { ...LS_DETAILS, disclosure_library: { ...LS_DETAILS.disclosure_library, current: true } };
  assert.ok(!render(h(LongShortProposalView, { envelope: { ...env, details: cur } })).includes("A newer version"));
  const none = { ...LS_DETAILS, disclosure_library: null };
  assert.ok(render(h(LongShortProposalView, { envelope: { ...env, details: none } })).includes("built-in six risk"));
});

test("nav: Admin → Settings → Disclosures, gated on view_disclosures (strict), and the page reads labelFor", () => {
  assert.deepEqual(MV.GATE_VIEW_DISCLOSURES, { perm: "view_disclosures", strict: true });
  assert.equal(MV.labelFor("/admin/disclosures"), "Disclosures");
  const admin = { account_role: "member", status: "active", is_active: true, permissions: ["view_disclosures", "manage_org_settings"], roles: ["org_admin"], is_internal: true };
  const member = { account_role: "member", status: "active", is_active: true, permissions: ["view_portfolio"], roles: ["member"] };
  assert.ok(MV.visibleTabs(admin, "settings").some((t) => t.href === "/admin/disclosures"));
  assert.ok(!MV.visibleTabs(member, "settings").some((t) => t.href === "/admin/disclosures"));
  assert.ok(src("app/admin/disclosures/page.js").includes('labelFor("/admin/disclosures")'));
});

test("the proxy route forwards with the caller's token, adds no org_id and passes a stored file through as bytes", () => {
  const r = src("app/api/disclosures/[[...path]]/route.js");
  assert.ok(r.includes("getRequestAuthClient") && r.includes("/api/v1/disclosures"), "host-aware client, the v1 path");
  assert.ok(!/org_id/.test(r.replace(/No org_id is ever added/g, "")), "no org_id on this side");
  assert.ok(r.includes("arrayBuffer()") && r.includes('!type.startsWith("application/json")'), "binary pass-through");
});
