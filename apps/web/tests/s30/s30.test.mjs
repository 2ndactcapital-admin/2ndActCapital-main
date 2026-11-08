// S30 — Admin → Segregation of duties: the REAL components rendered with react-dom/server from envelopes shaped exactly
// like routers/sod.py returns them. Every write control must FAIL CLOSED: none without the server's own flag being
// exactly true AND the action listed; a lost envelope renders nothing writable; the drafter never sees Approve on their
// own draft; a platform draft is decided only by a Super Admin; CSV links only for reports the server lists; the nav
// tab is strict on any of the three read keys.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/sod.mjs");
const M = await import("../../lib/menuVisibility.mjs");
const { ConflictsView, DecisionsView, DraftForm, RulesView } = await import("../../components/admin/SegregationOfDuties.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const ME = "11111111-1111-1111-1111-111111111111";
const OTHER = "22222222-2222-2222-2222-222222222222";
const ORG = "33333333-3333-3333-3333-333333333333";
const VOCAB = { editable: ["org_rule", "policy"], scopes: ["same_item", "same_household", "same_period"],
  severities: ["block", "warn"], self_approval: ["never", "when_no_other_checker"],
  reports: ["duties", "item_types", "rules", "conflicts", "self_approvals", "refusals"] };
const ADMIN = { can_read: true, can_write: true, is_super_admin: false };
const VIEWER = { can_read: true, can_write: false, is_super_admin: false };
const SUPER = { can_read: true, can_write: true, is_super_admin: true };

const ENV = (over = {}) => ({
  duties: [{ duty_key: "propose_trade", label: "Propose a trade (srv)", description: "Builds a trade.", permissions: ["manage_proposals"] },
    { duty_key: "approve_trade", label: "Approve a trade (srv)", description: "Approves a trade.", permissions: ["approve_proposals"] },
    { duty_key: "manage_roles", label: "Grant a role (srv)", description: "Grants.", permissions: ["manage_members"] },
    { duty_key: "receive_role", label: "Receive a role (srv)", description: "Is granted.", permissions: [] }],
  item_types: [
    { item_type: "rebalance_proposal", label: "Rebalance proposal (srv)", maker_duty: "propose_trade", checker_duty: "approve_trade",
      floor_class: "trade", policy: { self_approval: "never", enforce_floor: true, source: "platform" } },
    { item_type: "role_grant", label: "Role grant (srv)", maker_duty: "manage_roles", checker_duty: "receive_role",
      floor_class: "role_grant", policy: { self_approval: "never", enforce_floor: false, source: "platform" } }],
  rules: [
    { id: "r1", rule_key: "floor:rebalance_proposal", org_id: null, rule_kind: "floor", item_type: "rebalance_proposal",
      status: "active", version_no: 1, drafted_by: null, valid_to: null },
    { id: "r2", rule_key: "org:rebalance_proposal:same_household:propose_trade", org_id: ORG, rule_kind: "org",
      item_type: "rebalance_proposal", status: "draft", version_no: 1, drafted_by: ME, valid_to: null },
    { id: "r3", rule_key: "policy:role_grant", org_id: ORG, rule_kind: "policy", item_type: "role_grant",
      status: "draft", version_no: 1, drafted_by: OTHER, valid_to: null },
    { id: "r4", rule_key: "policy:rebalance_proposal", org_id: null, rule_kind: "policy", item_type: "rebalance_proposal",
      status: "draft", version_no: 2, drafted_by: OTHER, valid_to: null },
    { id: "r5", rule_key: "org:old", org_id: ORG, rule_kind: "org", item_type: "rebalance_proposal",
      status: "retired", version_no: 1, drafted_by: OTHER, valid_to: "2026-11-01T00:00:00Z" }],
  conflicts: [{ user_id: OTHER, name: "Fixture Person", roles: ["org_admin"], conflicts: [
    { item_type: "rebalance_proposal", label: "Rebalance proposal (srv)",
      still_stopped_by: [{ rule_key: "floor:rebalance_proposal" }] }] }],
  self_approvals: [{ id: "d1", created_at: "2026-11-08T10:00:00Z", item_type: "agent_proposal", item_label: "Agent proposal",
    checker_name: "Solo Adviser", rule_key: "policy:agent_proposal", reason: "self-approval allowed",
    disclosure: "Self-approved — no other eligible checker." }],
  refusals: [],
  viewer_id: ME,
  permissions: ADMIN,
  vocabularies: VOCAB,
  ...over,
});

test("fail closed: a lost or partial envelope offers no draft, no decide, no CSV", () => {
  assert.deepEqual(L.controls(undefined), { draftOrgRule: false, draftPolicy: false, draftPlatform: false });
  assert.deepEqual(L.controls({ permissions: { can_write: "true" }, vocabularies: VOCAB }),
    { draftOrgRule: false, draftPolicy: false, draftPlatform: false });
  assert.deepEqual(L.controls({ permissions: ADMIN }), { draftOrgRule: false, draftPolicy: false, draftPlatform: false });
  assert.equal(L.canDecide({}, { status: "draft", org_id: ORG, drafted_by: OTHER }, ME), false);
  assert.equal(L.csvHref({}, "conflicts"), null);
  assert.equal(render(h(DraftForm, { env: { item_types: [] }, onDraft: () => {}, busy: false })), "");
});

test("a view-only caller (editable []) sees the rules but no draft form and no Approve", () => {
  const env = ENV({ permissions: VIEWER, vocabularies: { ...VOCAB, editable: [] } });
  assert.equal(render(h(DraftForm, { env, onDraft: () => {}, busy: false })), "");
  const b = buttons(h(RulesView, { env, viewerId: ME, onDecide: () => {}, busy: false }));
  assert.ok(!b.includes("Approve") && !b.includes("Reject"), `buttons: ${b}`);
  assert.ok(render(h(RulesView, { env, viewerId: ME, onDecide: () => {}, busy: false })).includes("floor:rebalance_proposal"));
});

test("maker-checker on screen: the drafter never sees Approve on their own draft; another admin does; a platform draft only for a Super Admin", () => {
  const env = ENV();
  const [, mine, orgPolicy, platform] = env.rules;
  assert.equal(L.canDecide(env, mine, ME), false);
  assert.equal(L.canDecide(env, orgPolicy, ME), true);
  assert.equal(L.canDecide(env, platform, ME), false);
  const sup = ENV({ permissions: SUPER, vocabularies: { ...VOCAB, editable: [...VOCAB.editable, "platform_rule"] } });
  assert.equal(L.canDecide(sup, platform, ME), true);
  assert.equal(L.canDecide(sup, { ...platform, drafted_by: ME }, ME), false);
  // rendered: exactly one Approve (the org policy drafted by OTHER), never on r2 drafted by ME
  const b = buttons(h(RulesView, { env, viewerId: ME, onDecide: () => {}, busy: false }));
  assert.equal(b.filter((x) => x === "Approve").length, 1, `buttons: ${b}`);
});

test("labels come from the server's rows; history is kept and marked; the not-enforced floor is announced", () => {
  const html = render(h(RulesView, { env: ENV(), viewerId: ME, onDecide: () => {}, busy: false }));
  assert.ok(html.includes("Rebalance proposal (srv)") && html.includes("Propose a trade (srv)") && html.includes("Approve a trade (srv)"));
  assert.ok(html.includes("org:old") && html.includes("retired"));
  assert.ok(html.includes("The floor is not yet enforced for: Role grant (srv)"));
  const grouped = L.rulesByItem(ENV());
  assert.equal(grouped.rebalance_proposal.find((r) => r.id === "r5").history, true);
  assert.equal(grouped.rebalance_proposal.find((r) => r.id === "r1").current, true);
});

test("draft form: only the kinds the envelope lists; a Super Admin flag alone never opens platform drafting", () => {
  const onlyPolicy = ENV({ vocabularies: { ...VOCAB, editable: ["policy"] } });
  const html = renderToStaticMarkup(h(DraftForm, { env: onlyPolicy, onDraft: () => {}, busy: false }));
  assert.ok(!html.includes("A stricter rule") && html.includes("The policy for an item type"));
  assert.equal(L.controls(ENV({ permissions: SUPER })).draftPlatform, false);
  assert.equal(L.controls(ENV({ permissions: SUPER, vocabularies: { ...VOCAB, editable: ["platform_rule"] } })).draftPlatform, true);
});

test("reports: conflicts list the person and the rules that still stop them; self-approvals show the disclosure; CSV links per listed report", () => {
  const env = ENV();
  const c = render(h(ConflictsView, { env }));
  assert.ok(c.includes("Fixture Person") && c.includes("still stopped by floor:rebalance_proposal"));
  const s = render(h(DecisionsView, { env, name: "self_approvals", title: "Self-approvals", empty: "none" }));
  assert.ok(s.includes("Self-approved — no other eligible checker.") && s.includes("Solo Adviser"));
  const r = render(h(DecisionsView, { env, name: "refusals", title: "Refused", empty: "No refused checks." }));
  assert.ok(r.includes("No refused checks."));
  assert.equal(L.csvHref(env, "refusals"), "/api/sod/reports/refusals?format=csv");
  assert.equal(L.csvHref(env, "not_a_report"), null);
});

test("menu: Segregation of duties is a People tab for any of the three read keys (strict), never a member", () => {
  const me = (permissions, extra = {}) => ({ account_role: "member", role: "member", roles: ["x"], permissions, ...extra });
  const tabs = (u) => M.visibleTabs(u, "people").map((t) => t.href);
  assert.ok(tabs(me(["manage_org_settings"])).includes("/admin/sod"));
  assert.ok(tabs(me(["manage_compliance"])).includes("/admin/sod"));
  assert.ok(tabs(me(["view_audit_log"])).includes("/admin/sod"));
  assert.ok(!tabs(me(["view_dashboard", "view_marketplace"])).includes("/admin/sod"));
  assert.ok(!tabs(me(undefined)).includes("/admin/sod"));
  assert.equal(M.labelFor("/admin/sod"), "Segregation of duties");
});
