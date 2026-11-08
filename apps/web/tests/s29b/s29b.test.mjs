// S29B — Admin → Workflows → Autonomy and the run console's unattended markers: the REAL components rendered with
// react-dom/server from envelopes shaped exactly like routers/workflow_autonomy.py / routers/workflows.py return them.
// Every write control FAILS CLOSED: none without the server's own flag being exactly true AND the action listed; a lost
// envelope renders nothing writable; the requester never sees Approve on their own request; the run console marks
// unattended / shadow / held-for-person steps with the delegate line and the reason; the nav tab is strict.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/workflowAutonomy.mjs");
const M = await import("../../lib/menuVisibility.mjs");
const { SwitchesView, RequestsView, ShadowReviewsView, LimitsView, VerbsView } =
  await import("../../components/admin/WorkflowAutonomy.jsx");
const { StepAutonomyNote, RunAutonomyNote } = await import("../../components/admin/WorkflowAutonomyNotes.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const ME = "11111111-1111-1111-1111-111111111111";
const OTHER = "22222222-2222-2222-2222-222222222222";
const ADMIN = { can_read: true, can_write: true, can_manage_org: true, can_author: true, can_review_shadow: true,
  can_decide: true, is_super_admin: false };
const VIEWER = { can_read: true, can_write: false, can_manage_org: false, can_author: false, can_review_shadow: false,
  can_decide: false, is_super_admin: false };
const SUPER = { ...ADMIN, is_super_admin: true };
const EDIT_ADMIN = ["autonomy", "kill_switch", "limits", "shadow_runs_required", "version_autonomy", "shadow_review"];

const ENV = (over = {}) => ({
  switches: { org_switch: "workflows.autonomy (srv)", autonomy: false, org_kill_switch: false, platform_kill_switch: false },
  limits: { runs_per_day: 20, writes_per_run: 5, writes_per_day: 50, cap_per_run: 100, event_runs_per_hour: 20 },
  limit_keys: ["runs_per_day", "writes_per_run", "writes_per_day", "cap_per_run", "event_runs_per_hour"],
  usage_today: { writes: 7, runs: 2 },
  shadow_runs_required: 3,
  classes: ["never", "eligible", "eligible_with_cap"],
  verbs: [
    { key: "spv.record_transaction", autonomy_class: "never", writes: "spv_transactions (srv)", why_never: ["moves money"] },
    { key: "propose", autonomy_class: "eligible", writes: "agent_proposals (srv)", why_never: [] },
    { key: "made.up", autonomy_class: "not_a_class", writes: "x", why_never: [] }],
  drafts: [
    { id: "d1", setting_key: "autonomy", setting_value: true, status: "draft", maker_id: ME, reason: "mine" },
    { id: "d2", setting_key: "limits", setting_value: { writes_per_run: 9 }, status: "draft", maker_id: OTHER, reason: "theirs" },
    { id: "d3", setting_key: "version_autonomy", setting_value: { state: "on" }, status: "draft", maker_id: OTHER, reason: "opt in" }],
  shadow_reviews_pending: [{ run_id: "r1", name: "Fixture workflow (srv)", version_number: 2 }],
  viewer_id: ME,
  permissions: ADMIN,
  vocabularies: { editable: EDIT_ADMIN, inline_editable: [] },
  ...over,
});

test("s29b: an org admin sees the switch controls; a view-only caller sees none", () => {
  const admin = buttons(h(SwitchesView, { env: ENV(), onSet: () => {} }));
  assert.ok(admin.includes("Ask to turn on") && admin.includes("Stop all unattended steps"), admin.join("|"));
  assert.ok(!admin.includes("Stop everywhere"), "the platform kill switch is a Super Admin control");
  const viewer = buttons(h(SwitchesView, { env: ENV({ permissions: VIEWER, vocabularies: { editable: [], inline_editable: [] } }),
    onSet: () => {} }));
  assert.deepEqual(viewer, []);
  assert.ok(render(h(SwitchesView, { env: ENV(), onSet: () => {} })).includes("workflows.autonomy (srv)"),
    "the switch name comes from the server");
});

test("s29b: a lost or malformed envelope fails CLOSED (no control, autonomy reads as off)", () => {
  for (const env of [null, {}, { permissions: null }, { permissions: { can_manage_org: "true" }, vocabularies: { editable: EDIT_ADMIN } },
    { permissions: ADMIN }, { permissions: ADMIN, vocabularies: { editable: "autonomy" } }]) {
    const c = L.controls(env);
    assert.ok(Object.values(c).every((v) => v === false), JSON.stringify(env));
    assert.deepEqual(buttons(h(SwitchesView, { env, onSet: () => {} })), []);
  }
  assert.equal(L.switches({ switches: { autonomy: "yes" } }).autonomy, false);
});

test("s29b: the Super Admin alone gets the platform kill switch", () => {
  const sup = buttons(h(SwitchesView, { env: ENV({ permissions: SUPER, vocabularies: { editable: [...EDIT_ADMIN, "platform_kill_switch"] } }),
    onSet: () => {} }));
  assert.ok(sup.includes("Stop everywhere"), sup.join("|"));
  const flagOnly = buttons(h(SwitchesView, { env: ENV({ permissions: SUPER }), onSet: () => {} }));
  assert.ok(!flagOnly.includes("Stop everywhere"), "the flag alone is not enough — the vocabulary must list it");
});

test("s29b: the requester never sees Approve on their own request; a view-only caller sees none", () => {
  const el = h(RequestsView, { env: ENV(), viewerId: ME, onDecide: () => {} });
  const html = renderToStaticMarkup(el);
  const approves = buttons(el).filter((b) => b === "Approve").length;
  assert.equal(approves, 2, "d2 + d3 (not d1, which ME asked for)");
  assert.ok(html.includes("mine"));
  assert.equal(L.canDecide(ENV(), ENV().drafts[0], ME), false);
  assert.equal(L.canDecide(ENV({ permissions: VIEWER }), ENV().drafts[1], ME), false);
  assert.deepEqual(buttons(h(RequestsView, { env: ENV({ permissions: VIEWER }), viewerId: ME, onDecide: () => {} })), []);
  // an org setting needs can_manage_org; an opt-in needs can_decide only
  const author = { ...VIEWER, can_decide: true, can_author: true };
  assert.equal(L.canDecide(ENV({ permissions: author }), ENV().drafts[1], ME), false);
  assert.equal(L.canDecide(ENV({ permissions: author }), ENV().drafts[2], ME), true);
});

test("s29b: shadow reviews offered only with the server's review flag", () => {
  assert.deepEqual(buttons(h(ShadowReviewsView, { env: ENV(), onReview: () => {} })), ["Clean", "Not clean"]);
  assert.deepEqual(buttons(h(ShadowReviewsView, { env: ENV({ permissions: VIEWER }), onReview: () => {} })), []);
});

test("s29b: limits and verb classes come from the server's own keys", () => {
  const lim = render(h(LimitsView, { env: ENV() }));
  assert.ok(lim.includes("writes per day") && lim.includes("7") && lim.includes("3 clean shadow runs"), lim);
  assert.deepEqual(L.limitRows({ limit_keys: ["runs_per_day"], limits: { runs_per_day: 4 }, usage_today: { runs: 1 } }),
    [{ key: "runs_per_day", value: 4, used: 1 }]);
  const verbs = render(h(VerbsView, { env: ENV() }));
  assert.ok(verbs.includes("spv.record_transaction") && verbs.includes("moves money") && verbs.includes("propose"));
  assert.ok(!verbs.includes("made.up"), "a class the server does not list is not shown");
});

test("s29b: the run console marks unattended, shadow and held-for-person steps", () => {
  const un = render(h(StepAutonomyNote, { step: { autonomy_mode: "unattended",
    delegate_line: "on behalf of u-owner by workflow Fixture v2" } }));
  assert.ok(un.includes("Unattended") && un.includes("on behalf of u-owner by workflow Fixture v2"), un);
  const sh = render(h(StepAutonomyNote, { step: { autonomy_mode: "shadow", shadow: { target: "crm notes (srv)",
    would_produce: "would call x" } } }));
  assert.ok(sh.includes("Shadow — not executed") && sh.includes("Would write: crm notes (srv)"), sh);
  const held = render(h(StepAutonomyNote, { step: { autonomy_mode: "held_for_person",
    autonomy_reason: "the organization switch workflows.autonomy is off" } }));
  assert.ok(held.includes("Held for a person") && held.includes("workflows.autonomy is off"), held);
  assert.equal(renderToStaticMarkup(h(StepAutonomyNote, { step: { status: "completed" } })), "");
  assert.ok(render(h(RunAutonomyNote, { run: { autonomy_mode: "shadow" } })).includes("Awaiting the owner's review"));
  assert.ok(render(h(RunAutonomyNote, { run: { autonomy_mode: "shadow", shadow_review_outcome: "clean" } }))
    .includes("Reviewed: clean"));
  assert.equal(renderToStaticMarkup(h(RunAutonomyNote, { run: {} })), "");
});

test("s29b: the Autonomy tab is strict on any of its three read keys", () => {
  assert.deepEqual(M.GATE_WORKFLOW_AUTONOMY_READ.anyOf, ["manage_org_settings", "author_workflows", "view_workflow_runs"]);
  assert.equal(M.GATE_WORKFLOW_AUTONOMY_READ.strict, true);
  const item = M.MENU_ITEMS.find((i) => i.href === "/admin/workflows/autonomy");
  assert.ok(item && item.gate === M.GATE_WORKFLOW_AUTONOMY_READ, "the tab uses the strict gate");
});
