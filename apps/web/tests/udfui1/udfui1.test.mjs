// UDFUI1 — the custom-fields builder: pure rules + node renders (no browser, no network). Expectations are written by
// hand from the sprint (sprint_prompts/udfui1.structural.md) and the route gates in apps/api/routers/udf.py, never
// derived from the module under test. Personas: nav1's (apps/api/scripts/nav1_personas.mjs).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

process.env.NAV_TEST_STUBS = "1";
register("../market/jsxLoader.mjs", import.meta.url);
register("../nav/navLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const U = await import("../../lib/udfBuilder.mjs");
const M = await import("../../lib/menuVisibility.mjs");
const V = await import("../../lib/crmVocab.mjs");
const FieldsPanel = (await import("../../components/admin/udf/FieldsPanel.jsx")).default;
const TabsPanel = (await import("../../components/admin/udf/TabsPanel.jsx")).default;
const LayoutEditor = (await import("../../components/admin/udf/LayoutEditor.jsx")).default;
const P = await import("../../components/admin/udf/PermissionsPanel.jsx");
const TAR = await import("../../components/admin/udf/TagsAuditRecords.jsx");
const Parts = await import("../../components/admin/udf/udfParts.jsx");
const Builder = await import("../../components/admin/udf/CustomFieldsBuilder.jsx");
const { PERSONAS } = await import("../../../api/scripts/nav1_personas.mjs");
const SRC = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
const render = (c, props) => renderToStaticMarkup(h(c, props));
const actionsIn = (html) => [...html.matchAll(/data-action="([a-zA-Z]+)"/g)].map((m) => m[1]);

// ── fixtures (the server's envelope shape, by hand) ───────────────────────────────────────────────────────────
const LABELS = {
  applies_to: { entity: "Entities", position: "Positions" },
  data_type: { text: "Text", select: "Picklist", tags: "Tags", currency: "Currency" },
  status: { active: "Active", deactivated: "Deactivated", deleted: "Deleted (can be undeleted)" },
  access: { hidden: "Hidden", read: "Read-only", edit: "Editable" },
  owner_scope: { org: "Firm", platform: "Platform" },
  type_param: { length: "Maximum length", value_set_id: "Picklist values", precision: "Total digits", scale: "Decimal places", currency_code: "Currency code", min: "Minimum", max: "Maximum" },
  change_kind: { create: "Created", update: "Edited", soft_delete: "Deleted" },
};
const TYPE_PARAMS = { text: { required: ["length"], optional: [] }, select: { required: ["value_set_id"], optional: [] },
  tags: { required: [], optional: [] }, currency: { required: ["precision", "scale", "currency_code"], optional: ["min", "max"] } };
const ROWS = [
  { id: "d-act", label: "Risk score", field_key: "risk", applies_to: "entity", data_type: "text", owner_scope: "org", status: "active", used_by: 3, placements: [{ tab_id: "t1", tab_label: "Planning", section_title: "Main" }], type_params: { length: 50 } },
  { id: "d-off", label: "Old code", field_key: "old", applies_to: "entity", data_type: "text", owner_scope: "org", status: "deactivated", used_by: 0, placements: [] },
  { id: "d-del", label: "Gone", field_key: "gone", applies_to: "position", data_type: "text", owner_scope: "org", status: "deleted", used_by: 0, placements: [] },
  { id: "d-plat", label: "Platform class", field_key: "pc", applies_to: "entity", data_type: "text", owner_scope: "platform", status: "active", used_by: 0, placements: [] },
  { id: "d-tags", label: "Interests", field_key: "interests", applies_to: "entity", data_type: "tags", owner_scope: "org", status: "active", used_by: 2, placements: [] },
];
const vocab = (write) => ({ applies_to: ["entity", "position"], data_type: ["currency", "select", "tags", "text"],
  owner_scope: ["platform", "org", "team", "user"], statuses: ["active", "deactivated", "deleted"], access_levels: ["hidden", "read", "edit"],
  editable: write ? ["label", "help_text", "description", "is_required", "default_value", "display_order", "type_params"] : [],
  inline_editable: write ? ["label"] : [], type_params: TYPE_PARAMS, labels: LABELS });
const ENV = (write, superAdmin = false) => ({ rows: ROWS, value_sets: [{ id: "vs1", label: "Tiers", items: [{ code: "a", label: "A" }] }],
  permissions: { can_read: true, can_write: write, is_super_admin: superAdmin }, vocabularies: vocab(write) });
const TABS = (write) => ({ rows: [
  { id: "t1", label: "Planning", applies_to: "entity", status: "active", display_order: 0, section_count: 1, item_count: 1 },
  { id: "t2", label: "Compliance", applies_to: "entity", status: "deactivated", display_order: 1, section_count: 0, item_count: 0 },
  { id: "t3", label: "Retired", applies_to: "entity", status: "deleted", display_order: 2, section_count: 0, item_count: 0 },
], permissions: { can_read: true, can_write: write }, vocabularies: { applies_to: ["entity", "position"], editable: write ? ["label", "display_order"] : [], labels: LABELS } });
const LAYOUT = { layout_id: "L1", tab_id: "t1", sections: [
  { id: "s1", title: "Main", column_count: 2, display_order: 0, items: [{ id: "i1", definition_id: "d-act", label: "Risk score", display_order: 0, column_index: 0, col_span: 1, is_read_only: false }] },
  { id: "s2", title: "Other", column_count: 1, display_order: 1, items: [] },
] };

// ── every control → its route ─────────────────────────────────────────────────────────────────────────────────
test("every builder action maps to a FastAPI route that exists in routers/udf.py (method + path)", () => {
  const src = SRC("../api/routers/udf.py");
  const routes = new Set([...src.matchAll(/@router\.(get|post|put|patch|delete)\("(\/udf\/[^"]+)"/g)]
    .map((m) => `${m[1].toUpperCase()} ${m[2].replace(/\{[^}]+\}/g, "{}")}`));
  for (const [action, [method, tpl]] of Object.entries(U.UDF_ROUTES)) {
    const key = `${method} /udf/${tpl.replace(/:[a-z]+/g, "{}")}`;
    assert.ok(routes.has(key), `${action} → ${key} is a real route`);
  }
});

test("the hand-written route for each lifecycle control (spot checks)", () => {
  assert.deepEqual(U.apiRoute("deleteDefinition", { id: "x" }), { method: "DELETE", path: "/udf/definitions/x" });
  assert.deepEqual(U.apiRoute("undeleteDefinition", { id: "x" }), { method: "POST", path: "/udf/definitions/x/undelete" });
  assert.deepEqual(U.apiRoute("orderSections", { tab: "t" }), { method: "PUT", path: "/udf/layouts/t/sections/order" });
  assert.deepEqual(U.apiRoute("moveItem", { tab: "t", item: "i" }), { method: "PATCH", path: "/udf/layouts/t/items/i" });
  assert.deepEqual(U.apiRoute("mergeTags", { id: "d" }), { method: "POST", path: "/udf/tags/d/merge" });
  assert.deepEqual(U.apiRoute("preview"), { method: "GET", path: "/udf/preview" });
  assert.deepEqual(U.builderRequest("records", { type: "entity" }, { limit: 50, offset: 0 }),
    { method: "GET", url: "/api/udf/builder/records/entity?limit=50&offset=0" });
  assert.throws(() => U.apiRoute("editTab", {}), /missing route parameter/);
});

test("the Next forwarder forwards ONLY listed routes and strips org_id", () => {
  assert.equal(U.isForwardable("GET", ["admin", "definitions"]), true);
  assert.equal(U.isForwardable("DELETE", ["definitions", "abc"]), true);
  assert.equal(U.isForwardable("PUT", ["layouts", "t", "sections", "order"]), true);
  assert.equal(U.isForwardable("POST", ["records", "entity", "import"]), false, "import is not a builder control");
  assert.equal(U.isForwardable("PUT", ["values", "entity", "x"]), false, "values are the record page's, not the builder's");
  assert.equal(U.isForwardable("DELETE", ["admin", "definitions"]), false);
  assert.equal(U.isForwardable("GET", ["definitions", "a", "audit", "extra"]), false);
  assert.deepEqual(U.stripOrg({ label: "x", org_id: "evil" }), { label: "x" });
  const route = SRC("app/api/udf/builder/[...path]/route.js");
  assert.ok(route.includes("isForwardable(request.method, segs)") && route.includes("stripOrg(raw)"));
  assert.ok(!/org_id["']?\s*:/.test(route), "the forwarder never adds an org_id");
});

// ── controls render only when the server allows (fail closed) ─────────────────────────────────────────────────
test("definition actions by status; none without can_write, none on a platform row for a non-super-admin", () => {
  const e = ENV(true);
  assert.deepEqual(U.definitionActions(ROWS[0], e), ["editDefinition", "deactivateDefinition", "deleteDefinition"]);
  assert.deepEqual(U.definitionActions(ROWS[1], e), ["editDefinition", "reactivateDefinition", "deleteDefinition"]);
  assert.deepEqual(U.definitionActions(ROWS[2], e), ["undeleteDefinition"]);
  assert.deepEqual(U.definitionActions(ROWS[3], e), []);
  assert.deepEqual(U.definitionActions(ROWS[3], ENV(true, true)), ["editDefinition", "deactivateDefinition", "deleteDefinition"]);
  for (const r of ROWS) {
    assert.deepEqual(U.definitionActions(r, ENV(false)), []);
    assert.deepEqual(U.definitionActions(r, null), [], "a lost envelope");
    assert.deepEqual(U.definitionActions(r, { permissions: { can_write: "true" } }), [], "only boolean true counts");
  }
  assert.deepEqual(U.tabActions(TABS(true).rows[0], TABS(true)), ["editTab", "deactivateTab", "deleteTab"]);
  assert.deepEqual(U.tabActions(TABS(true).rows[1], TABS(true)), ["editTab", "reactivateTab", "deleteTab"]);
  assert.deepEqual(U.tabActions(TABS(true).rows[2], TABS(true)), ["undeleteTab"]);
  assert.deepEqual(U.tabActions(TABS(true).rows[0], TABS(false)), []);
});

test("Fields: a writer gets New field + each row's lifecycle buttons; a reader and a lost envelope get none", () => {
  const w = render(FieldsPanel, { envelope: ENV(true) });
  const acts = actionsIn(w);
  assert.ok(acts.includes("createDefinition"));
  for (const a of ["editDefinition", "deactivateDefinition", "deleteDefinition", "reactivateDefinition", "undeleteDefinition"]) {
    assert.ok(acts.includes(a), a);
  }
  assert.ok(w.includes("Deleted (can be undeleted)") && w.includes("Planning › Main") && w.includes("Entities"));
  for (const env of [ENV(false), { rows: ROWS }, null]) {
    const r = render(FieldsPanel, { envelope: env });
    assert.deepEqual(actionsIn(r), [], "no write control");
    assert.ok(!r.includes("New field"));
  }
});

test("Fields filters are labelled controls; filtering narrows by target / status / type / text", () => {
  const w = render(FieldsPanel, { envelope: ENV(true) });
  for (const id of ["udf-f-target", "udf-f-status", "udf-f-type", "udf-f-q"]) assert.ok(w.includes(`for="${id}"`) && w.includes(`id="${id}"`), id);
  assert.deepEqual(U.filterDefinitions(ROWS, { status: "deleted" }).map((r) => r.id), ["d-del"]);
  assert.deepEqual(U.filterDefinitions(ROWS, { applies_to: "position" }).map((r) => r.id), ["d-del"]);
  assert.deepEqual(U.filterDefinitions(ROWS, { data_type: "tags" }).map((r) => r.id), ["d-tags"]);
  assert.deepEqual(U.filterDefinitions(ROWS, { q: "RISK" }).map((r) => r.id), ["d-act"]);
  assert.equal(U.filterDefinitions(ROWS, {}).length, ROWS.length);
});

test("the definition form shows exactly the type's options and ties errors to fields", () => {
  const html = render(Parts.Field, { id: "x", label: "Name", error: "A name is required.", children: (a) => h("input", a) });
  assert.ok(html.includes('aria-invalid="true"') && html.includes('aria-describedby="x-error"') && html.includes('role="alert"'));
  const e = ENV(true);
  assert.deepEqual(U.validateDefinitionForm({ label: "", field_key: "Bad Key", applies_to: "nope", data_type: "text", type_params: {} }, e),
    { label: "A name is required.", field_key: "Key: lower-case letters, digits and underscores, starting with a letter.",
      applies_to: "Choose a record type.", "type_params.length": "Required for this field type." });
  assert.deepEqual(U.validateDefinitionForm({ label: "Tier", field_key: "tier", applies_to: "entity", data_type: "select", type_params: { value_set_id: "vs1" } }, e), {});
  assert.deepEqual(U.validateDefinitionForm({ label: "Fee", field_key: "fee", applies_to: "entity", data_type: "currency", type_params: { precision: "18", scale: "4", currency_code: "usd", min: "x" } }, e),
    { "type_params.min": "Enter a number." });
  const body = U.definitionCreateBody({ label: " Fee ", field_key: "fee", applies_to: "entity", data_type: "currency",
    type_params: { precision: "18", scale: "4", currency_code: "usd", length: "9", min: "" }, is_required: true, help_text: "" }, e);
  assert.deepEqual(body.type_params, { precision: 18, scale: 4, currency_code: "usd" }, "only the type's params, typed");
  assert.equal(body.owner_scope, "org");
  assert.equal(body.label, "Fee");
  assert.equal(body.help_text, null);
  assert.ok(!("org_id" in body));
  const form = render(FieldsPanel, { envelope: e });
  assert.ok(!form.includes("udf-new-tp"), "the form is closed until New field");
});

test("the edit PATCH is sparse and limited to the server's editable list", () => {
  const orig = { id: "d", label: "Risk", help_text: null, is_required: false, display_order: 0, data_type: "text", type_params: { length: 50 } };
  assert.deepEqual(U.definitionPatchBody(orig, { ...orig, label: "Risk score" }, ENV(true)), { label: "Risk score" });
  assert.deepEqual(U.definitionPatchBody(orig, { ...orig, type_params: { length: "80" } }, ENV(true)), { type_params: { length: 80 } });
  assert.deepEqual(U.definitionPatchBody(orig, { ...orig, label: "X" }, ENV(false)), {}, "nothing editable → nothing sent");
});

test("a refused delete shows the engine's own reason and what still references the field", () => {
  const body = { error: "definition d is referenced and cannot be deleted", detail: { message: "definition d is referenced and cannot be deleted", references: { udf_values: 4, udf_tag_assignments: 1 } } };
  assert.equal(U.refusalText(body, 409), "definition d is referenced and cannot be deleted (still referenced: 4 values, 1 tag assignments)");
  assert.equal(U.refusalText({ error: "manage_portfolio required" }, 403), "manage_portfolio required");
  assert.equal(U.refusalText({}, 500), "Request failed (500)");
});

test("Tabs: writer gets create / order / lifecycle; reader gets the list only", () => {
  const w = render(TabsPanel, { envelope: TABS(true), definitions: ROWS });
  const a = actionsIn(w);
  for (const x of ["createTab", "editTab", "deactivateTab", "deleteTab", "reactivateTab", "undeleteTab"]) assert.ok(a.includes(x), x);
  assert.ok(w.includes('aria-label="Move Planning down"') && w.includes('aria-label="Move Planning up"'));
  const r = render(TabsPanel, { envelope: TABS(false), definitions: ROWS });
  assert.deepEqual(actionsIn(r), []);
  assert.ok(!r.includes("Move Planning") && r.includes("Planning") && r.includes("Compliance"));
  assert.deepEqual(U.tabOrderPatches(TABS(true).rows.slice(0, 2), ["t2", "t1"]),
    [{ id: "t2", body: { display_order: 0 } }, { id: "t1", body: { display_order: 1 } }]);
});

test("Layout editor: writer gets add section / remove / add field / reorder; reader gets the tree only", () => {
  const tab = TABS(true).rows[0];
  const w = render(LayoutEditor, { tab, envelope: TABS(true), definitions: ROWS, initialLayout: LAYOUT });
  const a = actionsIn(w);
  for (const x of ["addSection", "removeSection", "removeItem"]) assert.ok(a.includes(x), x);
  assert.ok(w.includes('aria-label="Move section Main down"') && w.includes('aria-label="Move Risk score up"'));
  assert.ok(w.includes("Old code") === false, "a deactivated field is not offered for placement");
  const r = render(LayoutEditor, { tab, envelope: TABS(false), definitions: ROWS, initialLayout: LAYOUT });
  assert.deepEqual(actionsIn(r), []);
  assert.ok(r.includes("Risk score") && r.includes("Main") && !r.includes("Move section"));
  assert.deepEqual(U.moveInList(["s1", "s2", "s3"], 2, -1), ["s1", "s3", "s2"]);
  assert.deepEqual(U.moveInList(["s1", "s2"], 0, -1), ["s1", "s2"]);
  assert.deepEqual(U.moveById(["a", "b", "c"], "c", "a"), ["c", "a", "b"]);
  assert.deepEqual(U.itemOrderPatches([{ id: "a", display_order: 0 }, { id: "b", display_order: 1 }], ["b", "a"]),
    [{ item: "b", body: { display_order: 0 } }, { item: "a", body: { display_order: 1 } }]);
  assert.deepEqual(U.placeableDefinitions(ROWS, tab, LAYOUT).map((d) => d.id), ["d-plat", "d-tags"]);
});

test("Permissions: grants by profile / permission set; the editor only for a writer; a refused picker list says so", () => {
  const grants = [{ profile_id: "p1", access: "read" }, { permission_set_id: "s1", access: "hidden" }];
  const props = { kind: "field", subject: ROWS[0], envelope: ENV(true), profiles: [{ id: "p1", name: "Advisors" }], permissionSets: [{ id: "s1", name: "Ops" }], initialGrants: grants };
  const w = render(P.GrantEditor, props);
  assert.ok(w.includes("Advisors") && w.includes("Ops") && w.includes("Read-only") && w.includes("Hidden"));
  assert.ok(actionsIn(w).includes("setFieldGrant"));
  assert.deepEqual(actionsIn(render(P.GrantEditor, { ...props, envelope: ENV(false) })), []);
  assert.deepEqual(actionsIn(render(P.GrantEditor, { ...props, subject: ROWS[3] })), [], "platform field: super admin only");
  const noLists = render(P.GrantEditor, { ...props, profiles: null });
  assert.deepEqual(actionsIn(noLists), []);
  assert.ok(noLists.includes("needs the Manage organization settings permission"));
  assert.deepEqual(U.grantBody("profile", "p1", "read", "field"), { profile_id: "p1", access: "read" });
  assert.deepEqual(U.grantBody("permission_set", "s1", false, "tab"), { permission_set_id: "s1", is_visible: false });
  assert.throws(() => U.grantBody("role", "r1", "edit", "field"));
  const tabW = render(P.GrantEditor, { ...props, kind: "tab", subject: TABS(true).rows[0], envelope: TABS(true), initialGrants: [{ profile_id: "p1", is_visible: false }] });
  assert.ok(tabW.includes("Hidden") && actionsIn(tabW).includes("setTabGrant"));
});

test("Preview as shows the server's resolution verbatim (hidden / read-only / editable, per tab)", () => {
  const preview = { tabs: [{ tab_id: "t1", label: "Planning", visible: true }, { tab_id: "t9", label: "Secret", visible: false }],
    fields: [{ definition_id: "d-act", label: "Risk score", access: "read", on_tabs: [{ tab_id: "t1", access: "read" }] },
      { definition_id: "d-tags", label: "Interests", access: "hidden", on_tabs: [] },
      { definition_id: "d-x", label: "Notes", access: "edit", on_tabs: [{ tab_id: "t9", access: "hidden" }] }] };
  const html = render(P.PreviewAs, { envelope: ENV(true), users: [{ id: "u1", full_name: "A", email: "a@x" }], targets: ["entity"], initialPreview: preview });
  assert.ok(html.includes('data-preview-field="d-act" data-access="read"'));
  assert.ok(html.includes('data-preview-field="d-tags" data-access="hidden"'));
  assert.ok(html.includes("Secret: Hidden") && html.includes("Planning: Visible") && html.includes("Secret: Hidden"));
  assert.ok(html.includes("Planning: Read-only"));
  assert.ok(render(P.PreviewAs, { envelope: ENV(true), users: null, targets: ["entity"] }).includes("needs the Manage members permission"));
});

test("Tags: vocabulary + merge for a writer; no merge for a reader", () => {
  const vocabRows = { rows: [{ tag_code: "alpha", normalized_code: "alpha", n: 2 }, { tag_code: "beta", normalized_code: "beta", n: 1 }] };
  const w = render(TAR.TagsPanel, { envelope: ENV(true), initialVocabulary: vocabRows });
  assert.ok(w.includes('data-tag="alpha"') && w.includes("alpha · 2 record(s)"));
  assert.ok(actionsIn(w).includes("mergeTags"));
  assert.deepEqual(actionsIn(render(TAR.TagsPanel, { envelope: ENV(false), initialVocabulary: vocabRows })), []);
});

test("Audit: who changed what, when — rows as the route returns them, labels from the envelope", () => {
  const rows = [{ id: "a1", change_kind: "update", changed_at: "2026-11-13T10:00:00Z", changed_by_name: "Org Admin", changed_fields: ["label"] },
    { id: "a2", change_kind: "create", changed_at: "2026-11-13T09:00:00Z", changed_by: "u1", changed_fields: [] }];
  const html = render(TAR.AuditPanel, { envelope: ENV(true), initialRows: rows, initialId: "d-act" });
  assert.ok(html.includes('data-audit-row="a1"') && html.includes("Org Admin") && html.includes("Edited") && html.includes("label"));
  assert.ok(html.includes("Created") && html.includes("u1"));
});

test("Records: the route's own columns and values, and Export = the export route", () => {
  const rec = { rows: [{ id: "e1", display_name: "Acme", udf_values: { "d-act": "7", "d-tags": ["a", "b"] } }],
    columns: [{ definition_id: "d-act", label: "Risk score" }, { definition_id: "d-tags", label: "Interests" }], total_count: 1 };
  assert.deepEqual(U.recordColumns(rec).map((c) => c.headerName), ["Record", "Risk score", "Interests"]);
  assert.deepEqual(U.recordRows(rec), [{ id: "e1", __name: "Acme", "d-act": "7", "d-tags": "a, b" }]);
  const html = render(TAR.RecordsView, { envelope: ENV(true), initialRecords: rec, initialTarget: "entity" });
  assert.ok(html.includes('href="/api/udf/builder/records/entity/export"') && html.includes("Acme") && html.includes("Risk score"));
});

test("the builder: six labelled sections in an accessible tablist", () => {
  const html = render(Builder.default, { initialDefinitions: ENV(true), initialTabs: TABS(true), profiles: [], permissionSets: [], users: [] });
  assert.deepEqual(Builder.SECTIONS.map((s) => s.label), ["Fields", "Tabs & layouts", "Permissions & preview", "Tags", "Change history", "Records"]);
  assert.ok(html.includes('role="tablist"') && html.includes('role="tabpanel"') && html.includes('aria-selected="true"'));
});

// ── menu + page gate ──────────────────────────────────────────────────────────────────────────────────────────
test("menu: Firm settings → Custom fields & tabs, gated manage_portfolio (strict) — the routes' own permission", () => {
  const item = M.MENU_ITEMS.find((i) => i.href === "/admin/custom-fields");
  assert.ok(item && item.section === "org_admin" && item.group === "settings" && item.label === "Custom fields & tabs");
  assert.deepEqual(item.gate, { perm: "manage_portfolio", strict: true });
  const withPerm = { ...PERSONAS.support_staff, permissions: [...PERSONAS.support_staff.permissions, "manage_portfolio"] };
  assert.equal(M.canAccess(withPerm, item.gate), true);
  assert.equal(M.canAccess(PERSONAS.super_admin_bare, item.gate), true);
  for (const p of ["support_staff", "member", "next_gen", "org_admin", "lost_envelope"]) {
    assert.equal(M.canAccess(PERSONAS[p], item.gate), false, `${p} (no manage_portfolio) gets no menu entry`);
  }
  const page = SRC("app/admin/custom-fields/page.js");
  assert.ok(page.includes('fetchAPI("/api/v1/udf/admin/definitions")') && page.includes("canWrite(definitions) && canWrite(tabs)"));
  assert.ok(page.includes("You do not have permission to manage custom fields."));
});

// ── relationship status ───────────────────────────────────────────────────────────────────────────────────────
test("status pickers no longer offer 'active'; a record that still carries it keeps it (labels from config)", () => {
  const vocab = { entity_statuses: [
    { value: "prospect", label: "Prospect", selectable: true, retired: false }, { value: "client", label: "Client", selectable: true, retired: false },
    { value: "contact", label: "Contact", selectable: true, retired: false }, { value: "active", label: "Active", selectable: false, retired: true },
    { value: "partner", label: "Partner", selectable: true, retired: false }, { value: "former_client", label: "Former client", selectable: true, retired: false },
    { value: "deceased", label: "Deceased", selectable: false, retired: false }] };
  const fresh = V.statusOptions(vocab, "prospect").map((o) => o.value);
  assert.ok(!fresh.includes("active") && fresh.includes("contact") && fresh.includes("client"));
  assert.deepEqual(V.statusOptions(vocab, "prospect").map((o) => o.label), ["Prospect", "Client", "Contact", "Partner", "Former client"]);
  assert.ok(V.statusOptions(vocab, "active").some((o) => o.value === "active" && o.label === "Active"), "kept on its own record");
  for (const [k, l] of [["client", "Client"], ["prospect", "Prospect"], ["contact", "Contact"], ["partner", "Partner"], ["former_client", "Former client"], ["deceased", "Deceased"]]) {
    assert.equal(V.statusLabel(vocab, k), l);
  }
  const cfg = SRC("../api/services/crm_config.py");
  assert.ok(/RETIRED_STATUSES = \("active",\)/.test(cfg) && cfg.includes('s["selectable"] or s.get("retired")'));
});
