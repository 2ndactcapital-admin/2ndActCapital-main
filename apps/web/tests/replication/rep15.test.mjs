// REP15 — Replication → Model catalog (tenant) and Platform → Published models (provider): the REAL components
// rendered with react-dom/server from envelopes shaped exactly like routers/model_provider.py returns them. Every
// write control must FAIL CLOSED: none without the server's own flag being exactly true AND the action listed; a lost
// envelope renders nothing writable; a closed gate renders no publish / subscribe control and shows the server's own
// refusal; the provider's subscriber table shows status, counts and assets only; the diff is shown exactly.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/modelProvider.mjs");
const { CatalogView, DiffTable, InvoiceView, ProviderView } = await import("../../components/replication/ModelCatalog.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const REFUSAL = "model publishing is not enabled — a registered adviser entity is required";
const VOCAB = {
  registration_statuses: [{ key: "registered", label: "Registered (vocab)" }, { key: "not_registered", label: "Not registered (vocab)" }],
  catalog_statuses: [{ key: "published", label: "Published (vocab)" }, { key: "retired", label: "Retired (vocab)" }],
  subscription_statuses: [{ key: "active", label: "Active (vocab)" }, { key: "suspended", label: "Suspended (vocab)" },
    { key: "requested", label: "Requested (vocab)" }],
  kinds: [{ key: "long_only", label: "Long only (vocab)" }], refusal: REFUSAL, line_label: "Model-provider fee",
  editable: ["subscribe", "end", "adopt", "copy", "refresh"], inline_editable: [],
  platform_editable: ["provider", "gate", "publish", "decide", "retire", "subscriptions", "fees"],
};
const VIEW_VOCAB = { ...VOCAB, editable: [], platform_editable: [] };
const ADMIN = { can_read: true, can_write: true, can_approve: true, can_admin: true, is_super_admin: false };
const VIEWER = { can_read: true, can_write: false, can_approve: false, can_admin: false, is_super_admin: false };
const SUPER = { ...ADMIN, is_super_admin: true };

const ITEM = (agreement, subscription = null) => ({
  catalog_model_id: "cm1", name: "Holdings of REP15 Test Index", description: "Replicates the holdings of the REP15 index.",
  kind: "long_only", provider: "REP15 Advisers LLC", provided_by_label: "Provided by REP15 Advisers LLC", fee_bps: "12.5000",
  subscribable: true, subscription,
  versions: [{ id: "cv1", catalog_version_no: 1, status: "published", holdings_as_of: "2026-10-30" }],
  agreement,
});
const AG_UNACKED = { in_force: true, current: false, scope: "org", agreement: { version_id: "dv9", version_no: 3, file_href: "/x" } };
const AG_ACKED = { ...AG_UNACKED, current: true };
const SUB = (status, pending = [{ version_id: "mv2", version_no: 2, status: "draft" }]) => ({
  id: "s1", catalog_model_id: "cm1", status, started_on: "2026-11-01", accounts: 2, tenant_adv_obligation: "Your own ADV.",
  model: { id: "m1", name: "Holdings of REP15 Test Index", provided_by_label: "Provided by REP15 Advisers LLC",
    read_only: true, on_catalog_version_no: 1, pending },
});

test("catalog: an org admin with an unacknowledged agreement sees Acknowledge only; after acknowledging, Request", () => {
  const env1 = { permissions: ADMIN, vocabularies: VOCAB, catalog: [ITEM(AG_UNACKED)], subscriptions: [] };
  assert.deepEqual(buttons(h(CatalogView, { envelope: env1 })), ["Acknowledge the agreement"]);
  const env2 = { ...env1, catalog: [ITEM(AG_ACKED)] };
  assert.deepEqual(buttons(h(CatalogView, { envelope: env2 })), ["Request a subscription"]);
});

test("catalog: no agreement in force → no subscribe control at all, and the screen says so", () => {
  const env = { permissions: ADMIN, vocabularies: VOCAB, catalog: [ITEM({ in_force: false, current: false })], subscriptions: [] };
  assert.deepEqual(buttons(h(CatalogView, { envelope: env })), []);
  assert.match(render(h(CatalogView, { envelope: env })), /No sub-advisory agreement is in force/);
});

test("catalog: a view-only caller, a flag that is merely truthy, and a lost envelope all render NO write control", () => {
  const view = { permissions: VIEWER, vocabularies: VIEW_VOCAB, catalog: [ITEM(AG_ACKED)], subscriptions: [SUB("active")] };
  assert.deepEqual(buttons(h(CatalogView, { envelope: view })), []);
  const truthy = { permissions: { ...ADMIN, can_admin: "true", can_write: 1 }, vocabularies: VOCAB,
    catalog: [ITEM(AG_ACKED)], subscriptions: [SUB("active")] };
  assert.deepEqual(buttons(h(CatalogView, { envelope: truthy })), []);
  const noVocab = { permissions: ADMIN, catalog: [ITEM(AG_ACKED)], subscriptions: [SUB("active")] };
  assert.deepEqual(buttons(h(CatalogView, { envelope: noVocab })), []);
  assert.deepEqual(buttons(h(CatalogView, { envelope: null })), []);
});

test("catalog: an empty catalog (gate closed) shows the server's own refusal text", () => {
  const env = { permissions: ADMIN, vocabularies: VOCAB, catalog: [], subscriptions: [] };
  assert.ok(render(h(CatalogView, { envelope: env })).includes(REFUSAL));
});

test("subscription: active → adopt / refresh / copy / end; suspended → only end; labels from the vocabulary", () => {
  const act = { permissions: ADMIN, vocabularies: VOCAB, catalog: [], subscriptions: [SUB("active")] };
  assert.deepEqual(buttons(h(CatalogView, { envelope: act })),
    ["Adopt the new version (a second person approves)", "Check for a new version", "Copy into our registry", "End the subscription"]);
  assert.match(render(h(CatalogView, { envelope: act })), /Active \(vocab\)/);
  assert.match(render(h(CatalogView, { envelope: act })), /Provided by REP15 Advisers LLC/);
  const sus = { ...act, subscriptions: [SUB("suspended")] };
  assert.deepEqual(buttons(h(CatalogView, { envelope: sus })), ["End the subscription"]);
  assert.deepEqual(L.subscriptionControls(sus, SUB("suspended")).adoptVersions, []);
});

test("subscription: no provided draft → no adopt control (a submitted one is already awaiting its checker)", () => {
  const env = { permissions: ADMIN, vocabularies: VOCAB, catalog: [],
    subscriptions: [SUB("active", [{ version_id: "mv2", version_no: 2, status: "submitted" }])] };
  assert.equal(L.subscriptionControls(env, env.subscriptions[0]).adopt, false);
  assert.ok(!buttons(h(CatalogView, { envelope: env })).some((b) => b.startsWith("Adopt")));
});

test("diff: the server's before / after / change strings are shown untouched, with direction", () => {
  const diff = [{ key: "gs:a", ticker: "ZZA", before: "0.4000000000", after: "0.3000000000", change: "-0.1000000000", kind: "changed" },
    { key: "gs:c", ticker: "ZZC", before: "0", after: "0.1000000000", change: "0.1000000000", kind: "added" }];
  const rows = L.diffRows(diff);
  assert.deepEqual(rows.map((r) => [r.label, r.before, r.after, r.change, r.direction]),
    [["ZZA", "0.4000000000", "0.3000000000", "-0.1000000000", "down"], ["ZZC", "0", "0.1000000000", "0.1000000000", "up"]]);
  const html = render(h(DiffTable, { diff }));
  assert.ok(html.includes("-0.1000000000") && html.includes("0.1000000000") && html.includes("ZZC"));
  assert.match(render(h(DiffTable, { diff: [] })), /No weight changed/);
});

const PROVIDER = (gateOpen, ready) => ({
  permissions: SUPER, vocabularies: VOCAB, provider: { legal_name: "REP15 Advisers LLC", registration_status: "registered" },
  gate: { is_open: gateOpen, label: gateOpen ? "Open" : "Off" }, ready_to_open: ready,
  readiness: [{ key: "legal_name", label: "The registered adviser entity's legal name", met: true },
    { key: "adv", label: "Its ADV Part 2A is a published library version", met: ready }],
  adv: { source: ready ? "library" : "none", version_no: 1, scope: "org" }, operator_items: ["The registered adviser entity."],
});

test("provider: the gate opens only for Super Admin with every readiness item met; closed shows the refusal", () => {
  assert.deepEqual(L.providerControls(PROVIDER(false, false)).openGate, false);
  assert.deepEqual(L.providerControls(PROVIDER(false, true)).openGate, true);
  assert.equal(L.providerControls(PROVIDER(false, true)).publish, false);
  assert.equal(L.providerControls(PROVIDER(true, true)).publish, true);
  assert.equal(L.providerControls(PROVIDER(true, true)).closeGate, true);
  const off = render(h(ProviderView, { envelope: PROVIDER(false, false), subscribers: {} }));
  assert.ok(off.includes(REFUSAL) && off.includes("Not met"));
  assert.ok(!buttons(h(ProviderView, { envelope: PROVIDER(false, false), subscribers: {} })).includes("Open the gate"));
  assert.ok(buttons(h(ProviderView, { envelope: PROVIDER(false, true), subscribers: {} })).includes("Open the gate"));
});

test("provider: a non-Super-Admin (or a lost flag) gets no platform control at all", () => {
  for (const p of [ADMIN, { ...SUPER, is_super_admin: "true" }, {}]) {
    const env = { ...PROVIDER(true, true), permissions: p };
    assert.deepEqual(Object.values(L.providerControls(env)).filter(Boolean), []);
    assert.deepEqual(buttons(h(ProviderView, { envelope: env, subscribers: {} })), []);
  }
});

test("provider: subscribers show status, counts and assets only — any other field is dropped", () => {
  const subs = { subscriptions: [{ subscription_id: "s1", tenant_name: "REP15 Tenant A", status: "active",
    member_name: "LEAK", account_number_masked: "LEAK" }],
  usage: [{ subscription_id: "s1", catalog_version_id: "cv1", account_count: 2, model_assets: "300000.00", household_id: "LEAK" }] };
  const rows = L.subscriberRows(subs);
  assert.deepEqual(Object.keys(rows[0]).sort(), ["status", "subscription_id", "tenant_name", "usage"]);
  assert.deepEqual(rows[0].usage, [{ subscription_id: "s1", catalog_version_id: "cv1", account_count: 2, model_assets: "300000.00" }]);
  const html = render(h(ProviderView, { envelope: PROVIDER(true, true), subscribers: subs }));
  assert.ok(html.includes("REP15 Tenant A") && html.includes("300000.00") && !html.includes("LEAK"));
});

test("invoice: the separate revenue line is shown exactly as stated", () => {
  const inv = { lines: [{ label: "Model-provider fee", fee_bps: "12.5000", days: 30, average_daily_assets: "300000.000000", amount: "30.82" }] };
  const html = render(h(InvoiceView, { invoice: inv }));
  assert.ok(html.includes("Model-provider fee") && html.includes("30.82") && html.includes("300000.000000"));
});

test("menu: the two REP15 screens are gated (view_models strict; Super Admin)", async () => {
  const M = await import("../../lib/menuVisibility.mjs");
  const cat = M.MENU_ITEMS.find((i) => i.href === "/portfolio/replication/catalog");
  const prov = M.MENU_ITEMS.find((i) => i.href === "/admin/model-provider");
  assert.ok(cat && prov);
  assert.equal(cat.gate.perm, "view_models");
  assert.equal(cat.gate.strict, true);
  assert.deepEqual(prov.gate, M.GATE_SUPER_ADMIN);
});
