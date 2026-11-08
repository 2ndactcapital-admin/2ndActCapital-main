// WFTEMPLATES1 — Workflows → Templates and Platform → Workflow templates: the REAL components rendered with
// react-dom/server from envelopes shaped exactly like routers/workflow_templates.py returns them. Every write control
// must FAIL CLOSED: none without the server's own flag being exactly true AND the action listed; a lost envelope
// renders nothing writable; the drafter never sees Publish on their own draft; a retired version offers no "use";
// usage is counts only; a newer template version is announced and never overwrites the copy.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/workflowTemplates.mjs");
const M = await import("../../lib/menuVisibility.mjs");
const { CatalogView, CopiesList, PlatformView, SaveOrgTemplate, TemplateView } = await import(
  "../../components/admin/WorkflowTemplates.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const VOCAB = {
  categories: [{ key: "alternatives", label: "Alternatives (vocab)" }, { key: "tax", label: "Tax (vocab)" },
    { key: "cash_transfers", label: "Cash (vocab)" }],
  statuses: [{ key: "draft", label: "Draft (vocab)" }, { key: "published", label: "Published (vocab)" },
    { key: "retired", label: "Retired (vocab)" }],
  scopes: [{ key: "platform", label: "Hollisworks (vocab)" }, { key: "org", label: "Your firm (vocab)" }],
  editable: ["use", "save", "retire"], inline_editable: [], platform_editable: [],
};
const VIEW_VOCAB = { ...VOCAB, editable: [] };
const AUTHOR = { can_read: true, can_write: true, is_super_admin: false };
const VIEWER = { can_read: true, can_write: false, is_super_admin: false };

const STEPS = [
  { step_key: "Service_1", step_type: "service", display_name: "Open SPVs", action_registry_key: "spv.list_open",
    effective_tier: 3, needs_approval: false, label: "Reference step — the workflow records it; no action runs (Tier 3)" },
  { step_key: "Service_2", step_type: "service", display_name: "Propose the carry",
    action_registry_key: "spv_carry.propose_from_realization", effective_tier: 1, needs_approval: true,
    label: "Needs approval (Tier 1) — a second person approves before it runs" },
  { step_key: "Task_3", step_type: "user", display_name: "Record the distribution", action_registry_key: null,
    effective_tier: 1, needs_approval: false, label: "A person does this step" },
];
const DETAIL = (over = {}) => ({
  template: { id: "t1", key: "distribution_received", scope: "platform", is_own: false },
  version: { id: "v2", version_no: 2, status: "published", scope: "platform", title: "Distribution received",
    category: "cash_transfers", description: "When a distribution arrives…\n\nThe carry step needs approval.",
    steps: STEPS, drawable_xml: "<x/>",
    parameters: [{ name: "notify_channel", label: "How members are told", kind: "choice", default: "email",
      choices: [{ key: "email", label: "Email" }, { key: "a phone call", label: "Phone call" }] }] },
  latest_published_version_no: 2, can_use_version: true, profiles: [],
  prerequisites: [
    { kind: "permission", key: "author_workflows", why: "to copy this template", met: true, missing: null },
    { kind: "permission", key: "manage_billing", why: "the run starter must hold it", met: false,
      missing: "you do not hold manage_billing" },
    { kind: "reviewer", key: "review_agent_proposals", why: "a second person approves", met: false,
      missing: "no one in your firm holds review_agent_proposals" }],
  my_copies: [],
  ...over,
});

test("catalog: lists the server's templates with vocabulary labels; category + search filter them", () => {
  const env = { permissions: VIEWER, vocabularies: VIEW_VOCAB, templates: [
    { template_id: "t1", key: "capital_call_received", scope: "platform", title: "Capital call received", category: "alternatives",
      description: "Match the notice", version_no: 1, step_count: 6, person_steps: 5, approval_steps: 0, my_copies: 0 },
    { template_id: "t2", key: "rmd_reminder", scope: "org", title: "RMD reminder", category: "tax",
      description: "Estimate each RMD", version_no: 3, step_count: 5, person_steps: 4, approval_steps: 0, my_copies: 2 }] };
  const out = render(h(CatalogView, { envelope: env }));
  assert.match(out, /Capital call received/);
  assert.match(out, /Alternatives \(vocab\)/);
  assert.match(out, /Your firm \(vocab\)/);
  assert.match(out, /2 copies in your firm/);
  assert.deepEqual(L.filterCatalog(env.templates, { category: "tax" }).map((t) => t.key), ["rmd_reminder"]);
  assert.deepEqual(L.filterCatalog(env.templates, { q: "notice" }).map((t) => t.key), ["capital_call_received"]);
  assert.equal(L.filterCatalog(env.templates, { q: "zzz-none" }).length, 0);
  const one = render(h(CatalogView, { envelope: env, initialCategory: "tax" }));
  assert.match(one, /RMD reminder/);
  assert.doesNotMatch(one, /Capital call received/);
});

test("template page: steps carry the server's labels — the Tier-1 step reads 'Needs approval'; missing prerequisites are named", () => {
  const out = render(h(TemplateView, { envelope: { ...DETAIL(), permissions: AUTHOR, vocabularies: VOCAB } }));
  assert.match(out, /Needs approval \(Tier 1\)/);
  assert.match(out, /A person does this step/);
  assert.match(out, /missing: you do not hold manage_billing/);
  assert.match(out, /missing: no one in your firm holds review_agent_proposals/);
  assert.match(out, /author_workflows — to copy this template · in place/);
  assert.match(out, /How members are told/);
});

test("use: an author sees 'Create the draft workflow'; a viewer, a lost envelope, a string 'true' and a retired version see none", () => {
  const author = { ...DETAIL(), permissions: AUTHOR, vocabularies: VOCAB };
  assert.ok(buttons(h(TemplateView, { envelope: author })).includes("Create the draft workflow"));
  const viewer = { ...DETAIL(), permissions: VIEWER, vocabularies: VIEW_VOCAB };
  assert.deepEqual(buttons(h(TemplateView, { envelope: viewer })), []);
  const lost = { ...DETAIL() };
  assert.deepEqual(buttons(h(TemplateView, { envelope: lost })), []);
  const stringy = { ...DETAIL(), permissions: { ...AUTHOR, can_write: "true" }, vocabularies: VOCAB };
  assert.deepEqual(buttons(h(TemplateView, { envelope: stringy })), []);
  const notListed = { ...DETAIL(), permissions: AUTHOR, vocabularies: VIEW_VOCAB };
  assert.deepEqual(buttons(h(TemplateView, { envelope: notListed })), []);
  const retired = { ...DETAIL({ can_use_version: false, version: { ...DETAIL().version, status: "retired" } }),
    permissions: AUTHOR, vocabularies: VOCAB };
  assert.deepEqual(buttons(h(TemplateView, { envelope: retired })), []);
});

test("use body: only declared parameters, an integer as a number, empty values dropped, the version pinned", () => {
  const params = [{ name: "days", kind: "integer" }, { name: "who", kind: "profile" }, { name: "c", kind: "choice" }];
  assert.deepEqual(L.initialValues([{ name: "days", kind: "integer", default: 5 }, { name: "who", kind: "profile" }]),
    { days: "5", who: "" });
  assert.deepEqual(L.useBody(params, { days: "7", who: "", c: "email", extra: "x" }, { versionId: "v2", name: " Mine " }),
    { parameters: { days: 7, c: "email" }, version_id: "v2", name: "Mine" });
});

test("retire: only for the firm's OWN org template, published, with the action listed", () => {
  const own = DETAIL({ template: { id: "t9", key: "mine", scope: "org", is_own: true } });
  assert.ok(buttons(h(TemplateView, { envelope: { ...own, permissions: AUTHOR, vocabularies: VOCAB } }))
    .includes("Retire this version"));
  assert.ok(!buttons(h(TemplateView, { envelope: { ...DETAIL(), permissions: AUTHOR, vocabularies: VOCAB } }))
    .includes("Retire this version"));
  assert.ok(!buttons(h(TemplateView, { envelope: { ...own, permissions: VIEWER, vocabularies: VIEW_VOCAB } }))
    .includes("Retire this version"));
});

test("save as org template: shown only with can_write true and 'save' listed", () => {
  const wf = [{ id: "w1", name: "My review" }];
  assert.ok(buttons(h(SaveOrgTemplate, { envelope: { permissions: AUTHOR, vocabularies: VOCAB }, workflows: wf }))
    .includes("Save as template"));
  assert.equal(renderToStaticMarkup(h(SaveOrgTemplate, { envelope: { permissions: VIEWER, vocabularies: VIEW_VOCAB }, workflows: wf })), "");
  assert.equal(renderToStaticMarkup(h(SaveOrgTemplate, { envelope: {}, workflows: wf })), "");
  assert.equal(renderToStaticMarkup(h(SaveOrgTemplate, { envelope: { permissions: AUTHOR, vocabularies: VIEW_VOCAB }, workflows: wf })), "");
});

test("copies: a newer template version is announced with the step diff and never offered as an overwrite", () => {
  const copies = [{ id: "c1", template_id: "t1", template_version_no: 1, definition_name: "Distribution (ours)",
    definition_exists: true, workflow_definition_id: "d1",
    newer_version: { version_id: "v2", version_no: 2, diff: { added: [{ step_key: "Task_9", display_name: "Notify the GP" }],
      removed: [], changed: [{ step_key: "Task_3", field: "display_name", before: "Record", after: "Record it" }] } } },
    { id: "c2", template_id: "t1", template_version_no: 2, definition_name: "Current copy", definition_exists: true,
      workflow_definition_id: "d2", newer_version: null }];
  const out = render(h(CopiesList, { copies }));
  assert.match(out, /A newer version of this template exists \(version 2; your copy came from version 1\)/);
  assert.match(out, /Your workflow is unchanged/);
  assert.match(out, /Added: Notify the GP/);
  assert.match(out, /Changed: Task_3: display_name "Record" → "Record it"/);
  assert.equal(L.newerNotice(copies[1]), null);
  assert.deepEqual(buttons(h(CopiesList, { copies })), []);
});

test("platform: a second Super Admin sees Publish / Reject; the drafter does not; a non-Super-Admin envelope shows nothing; usage is counts only", () => {
  const PV = { ...VOCAB, editable: [], platform_editable: ["draft", "decide", "retire"] };
  const ver = (over) => ({ id: "v1", version_no: 1, title: "Capital call received", status: "draft", copies: 3, orgs: 2,
    can_decide: true, can_retire: false, ...over });
  const env = (versions, perms = { can_read: true, can_write: true, is_super_admin: true }, vocab = PV) => ({
    permissions: perms, vocabularies: vocab, platform_org_allowed: true,
    templates: [{ template_id: "t1", key: "capital_call_received", versions }] });
  const checker = buttons(h(PlatformView, { envelope: env([ver({})]) }));
  assert.ok(checker.includes("Publish") && checker.includes("Reject"));
  const drafter = buttons(h(PlatformView, { envelope: env([ver({ can_decide: false })]) }));
  assert.ok(!drafter.includes("Publish") && !drafter.includes("Reject"));
  const published = buttons(h(PlatformView, { envelope: env([ver({ status: "published", can_decide: false, can_retire: true })]) }));
  assert.ok(published.includes("Retire") && !published.includes("Publish"));
  const notSuper = buttons(h(PlatformView, { envelope: env([ver({})], { can_read: true, can_write: true, is_super_admin: false }) }));
  assert.deepEqual(notSuper, []);
  const lost = buttons(h(PlatformView, { envelope: { templates: [{ template_id: "t1", key: "k", versions: [ver({})] }] } }));
  assert.deepEqual(lost, []);
  const out = render(h(PlatformView, { envelope: env([ver({})]) }));
  assert.match(out, /3 copies in 2 organisations/);
  assert.equal(L.usageText({ copies: 1, orgs: 1 }), "1 copy in 1 organisation");
});

test("menu: Templates is a Workflows tab for any of the three workflow keys (strict), never a member; the platform tab is Super Admin only", () => {
  const me = (permissions, extra = {}) => ({ account_role: "member", role: "member", roles: ["x"], permissions, ...extra });
  const tabs = (u) => M.visibleTabs(u, "workflows").map((t) => t.href);
  assert.ok(tabs(me(["view_workflow_runs"])).includes("/admin/workflows/templates"));
  assert.ok(tabs(me(["author_workflows"])).includes("/admin/workflows/templates"));
  assert.ok(tabs(me(["configure_workflow_triggers"])).includes("/admin/workflows/templates"));
  assert.ok(!tabs(me(["view_dashboard", "view_marketplace"])).includes("/admin/workflows/templates"));
  assert.ok(!tabs(me(undefined)).includes("/admin/workflows/templates"));
  const plat = (u) => M.visibleTabs(u, "controls").map((t) => t.href);
  assert.ok(!plat(me(["author_workflows"])).includes("/admin/platform/workflow-templates"));
  assert.equal(M.labelFor("/admin/workflows/templates"), "Templates");
  assert.equal(M.labelFor("/admin/platform/workflow-templates"), "Workflow templates");
});

test("errors: a 422 shows the server's own message and every named finding", () => {
  assert.equal(L.errorText({ detail: { message: "the copy failed validation", errors: ["unknown actionRegistryKey 'x'"] } }),
    "the copy failed validation — unknown actionRegistryKey 'x'");
  assert.equal(L.errorText({ message: "Permission required: author_workflows" }), "Permission required: author_workflows");
});
