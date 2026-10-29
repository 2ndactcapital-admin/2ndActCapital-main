// MARKETDATA1 — the security-data screens' REAL components rendered with react-dom/server
// from envelopes shaped exactly like the API returns them. Every write control (provider
// status / budget, platform rank, org rank override, upload, approve / reject) must FAIL
// CLOSED: none without the server's own flag being exactly true AND the field listed.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { ProvidersView } = await import("../../components/securityData/ProvidersAdmin.jsx");
const { RankOverridesView, UploadsView } = await import("../../components/securityData/OrgSources.jsx");
const { default: QualityView } = await import("../../components/securityData/QualityView.jsx");
const { sourceTitle } = await import("../../components/securityData/sourceLabel.mjs");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const render = (el) => renderToStaticMarkup(el);

const VOCAB = {
  data_types: [{ key: "eod_price", label: "End-of-day price" }, { key: "index_level", label: "Index level / rate" }],
  provider_kinds: [{ key: "custodian", label: "Custodian valuation" }, { key: "vendor", label: "Data vendor" }],
  provider_statuses: [{ key: "active", label: "Active" }, { key: "not_connected", label: "Not connected" }],
  member_display: [{ key: "allowed", label: "Allowed" }, { key: "own_holdings_only", label: "The member's own holdings only" },
    { key: "not_allowed", label: "Not allowed" }],
  retention: [{ key: "keep", label: "May keep after cancellation" }],
  quality_kinds: [{ key: "stale", label: "Stale" }, { key: "budget_exhausted", label: "Daily call budget exhausted" }],
  upload_types: ["eod_price", "index_level"],
};
const WRITE = { ...VOCAB, editable: ["status", "daily_call_budget"], rank_editable: ["providers"],
  upload_editable: ["file", "data_type", "licence", "mapping"] };
const VIEW = { ...VOCAB, editable: [], rank_editable: [], upload_editable: [] };

const providers = [
  { key: "custodian", name: "Custodian account valuations", kind: "custodian", status: "active", connected: true,
    lic_internal_use: true, lic_member_display: "own_holdings_only", lic_redistribution: false,
    retention_after_cancel: "keep", daily_call_budget: null, calls_today: 0, budget_left_today: null },
  { key: "vendor_slot", name: "Vendor slot (not connected)", kind: "vendor", status: "not_connected", connected: false,
    lic_internal_use: false, lic_member_display: "not_allowed", lic_redistribution: false,
    retention_after_cancel: "keep", daily_call_budget: 0, calls_today: 0, budget_left_today: 0 },
];
const ranks = [{ data_type: "eod_price", platform: ["custodian", "csv_upload"], org_override: [], effective: ["custodian", "csv_upload"],
  origin: "platform", allowed: ["custodian", "csv_upload"] }];
const env = (body, perms, vocab) => ({ ...body, permissions: perms, vocabularies: vocab });
const PROVIDER_CONTROLS = ["Set budget", "Edit rank", "Status of custodian"];
const ORG_CONTROLS = ["Change order", "Upload for approval", "Approve", "Reject"];
const uploads = [{ id: "u1", file_name: "px.csv", data_type: "eod_price", row_count: 3, accepted_count: 2, status: "pending_approval",
  licence: { internal_use: true, member_display: "not_allowed" }, rejects: [{ row: 4, reason: "Date is in the future" }], can_decide: true }];

test("super admin view: provider + rank controls render with the flags", () => {
  const html = render(h(ProvidersView, { envelope: env({ providers }, { can_write: true }, WRITE),
    rankEnvelope: env({ ranks }, { can_write: true }, WRITE) }));
  for (const w of ["Set budget", "Edit rank"]) assert.ok(text(html).includes(w), `missing ${w}`);
  assert.ok(html.includes('aria-label="Status of custodian"'));
  assert.ok(text(html).includes("The member's own holdings only"), "licence label from the vocabulary");
  assert.ok(text(html).includes("(no live module)"), "the vendor slot shows it is not connected");
});

test("no flags / no envelope: no provider or rank control (fails closed)", () => {
  for (const [e, r] of [[env({ providers }, { can_write: false }, VIEW), env({ ranks }, { can_write: false }, VIEW)],
    [env({ providers }, { can_write: true }, VIEW), env({ ranks }, { can_write: true }, VIEW)],
    [env({ providers }, { can_write: "true" }, WRITE), env({ ranks }, { can_write: 1 }, WRITE)],
    [{ providers }, { ranks }], [null, null]]) {
    const html = render(h(ProvidersView, { envelope: e, rankEnvelope: r }));
    for (const w of PROVIDER_CONTROLS) assert.ok(!html.includes(w), `control ${w} rendered without the flag`);
    assert.ok(!html.includes("<select"), "a select rendered without the flag");
  }
});

test("org view: rank override, upload and decision controls render only with the flags", () => {
  const w = render(h("div", null, h(RankOverridesView, { envelope: env({ ranks }, { can_write: true }, WRITE) }),
    h(UploadsView, { envelope: env({ uploads }, { can_write: true, can_approve: true }, WRITE) })));
  for (const c of ORG_CONTROLS) assert.ok(text(w).includes(c), `missing ${c}`);
  for (const [perms, vocab] of [[{ can_write: false, can_approve: false }, VIEW], [{ can_write: true, can_approve: true }, VIEW],
    [{}, WRITE], [{ can_write: "yes", can_approve: "yes" }, WRITE]]) {
    const html = render(h("div", null, h(RankOverridesView, { envelope: env({ ranks }, perms, vocab) }),
      h(UploadsView, { envelope: env({ uploads: uploads.map((u) => ({ ...u, can_decide: vocab === VIEW ? true : u.can_decide })) }, perms, vocab) })));
    for (const c of ["Change order", "Upload for approval"]) assert.ok(!text(html).includes(c), `${c} rendered: ${JSON.stringify(perms)}`);
    if (perms.can_approve !== true) for (const c of ["Approve", "Reject"]) assert.ok(!text(html).includes(c), `${c} rendered`);
  }
  const maker = render(h(UploadsView, { envelope: env({ uploads: [{ ...uploads[0], can_decide: false }] },
    { can_write: true, can_approve: true }, WRITE) }));
  assert.ok(!text(maker).includes("Approve"), "the maker (can_decide false) never sees Approve");
  assert.ok(maker.includes("row 4: Date is in the future"), "reject reasons on hover");
});

test("quality view renders events with server labels; empty says so", () => {
  const html = render(h(QualityView, { envelope: env({ events: [{ id: "e1", event_date: "2026-10-28", kind: "budget_exhausted",
    data_type: "index_level", subject_key: "DGS10", provider_key: "fred", owner_scope: "platform", detail: { budget: 0 } }] }, {}, VOCAB) }));
  assert.ok(text(html).includes("Daily call budget exhausted") && text(html).includes("DGS10 (platform)"));
  assert.ok(text(render(h(QualityView, { envelope: env({ events: [] }, {}, VOCAB) }))).includes("No data-quality events."));
});

test("hover line: source + as-of for a found value, the missing data for a not-priced one", () => {
  assert.equal(sourceTitle({ found: true, provider_name: "Custodian account valuations", as_of: "2026-10-27", quality: [] }),
    "estimate — Custodian account valuations, as of 2026-10-27");
  assert.equal(sourceTitle({ found: false, label: "not priced — eod_price (end-of-day price) — custodian: no value" }),
    "not priced — eod_price (end-of-day price) — custodian: no value");
  assert.equal(sourceTitle(null), "not priced");
});
