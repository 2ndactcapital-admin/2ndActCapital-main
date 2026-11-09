// CASH02 — Household → Treasury → Cash policy: the REAL components rendered with react-dom/server from envelopes shaped
// exactly like routers/cash.py returns them (the treasury envelope's `policy` block, services/cash_policy.
// household_block). The editor shows two boundary sliders, rung count, preset chips, the derived sleeves in months /
// $ / % with estimated yields and their labels; the questionnaire has 3 steps; the approval status offers Approve /
// Reject ONLY when the server says this caller may decide; a member sees the active policy read-only with the
// disclosure library's labels; every control FAILS CLOSED.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/cashPolicy.mjs");
const P = await import("../../components/treasury/PolicyPanel.jsx");
const { SleeveTable, PolicyEditor, Questionnaire, ApprovalStatus } = P;
const PolicyPanel = P.default;

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());
const ranges = (el) => [...renderToStaticMarkup(el).matchAll(/<input[^>]*type="range"[^>]*>/g)].length;

const AUTHOR = { can_read: true, can_write: true, is_super_admin: false, can_author_policy: true,
  policy_permission: "manage_cash_policy" };
const READER = { ...AUTHOR, can_write: false, can_author_policy: false };
const VOC = (editable) => ({
  presets: [{ key: "stay_liquid", label: "Stay liquid (srv)" }, { key: "balanced", label: "Balanced (srv)" },
    { key: "recommended", label: "Recommended (srv)" }, { key: "custom", label: "Custom (srv)" }],
  statuses: [{ key: "draft", label: "Draft (srv)" }, { key: "pending_approval", label: "Awaiting approval (srv)" },
    { key: "active", label: "Active (srv)" }, { key: "retired", label: "Retired (srv)" }, { key: "rejected", label: "Rejected (srv)" }],
  objective_scale: [{ score: 1, label: "Most stable (srv)", near_months: 6, reserve_months: 18 },
    { score: 3, label: "Balanced (srv)", near_months: 3, reserve_months: 12 },
    { score: 5, label: "Most yield (srv)", near_months: 1, reserve_months: 6 }],
  bounds: { near_months: { min: 0, max: 120 }, reserve_months: { min: 0, max: 120 }, horizon_months: { min: 1, max: 120 } },
  defaults: { near_months: 3, reserve_months: 12, horizon_months: 24, rung_count: 8, buffer_months: "0",
    objective_score: 3, preset: "custom", min_coverage_months: "6" },
  editable, inline_editable: [],
});
const Y = (pct, basis) => ({ estimated: true, yield_pct: pct, basis, as_of: "2026-11-05",
  label: "estimate — Treasury curve proxy, as of 2026-11-05" });
const DERIVED = {
  as_of: "2026-11-09", liquid: "1000000.00", total: "220000.00", unallocated: "780000.00",
  sleeves: [
    { key: "near_term", label: "Near-term (srv)", start_month: 0, end_month: 3, months: 3, from: "2026-11-09",
      until: "2027-02-09", amount: "100000.00", pct: "10.0", count: 1, note: null, yield: Y("3.94", "3-month Treasury, derived"),
      after_tax: { estimated: false, text: "after-tax: needs a tax profile" } },
    { key: "reserve", label: "Reserve (srv)", start_month: 3, end_month: 12, months: 9, from: "2027-02-09",
      until: "2027-11-09", amount: "50000.00", pct: "5.0", count: 1, note: null,
      yield: { estimated: false, text: "not estimated — needs fred.dgs6mo or fred.dgs1" },
      after_tax: { estimated: false, text: "not estimated — needs fred.dgs6mo or fred.dgs1" } },
    { key: "long_term", label: "Long-term (srv)", start_month: 12, end_month: 24, months: 12, from: "2027-11-09",
      until: "2028-11-09", amount: "70000.00", pct: "7.0", count: 1, note: null, yield: Y("4.46", "2-year Treasury"),
      after_tax: { estimated: true, yield_pct: "2.64", label: "after-tax estimate — federal ordinary 0.37 + NIIT 0.038 (srv)" } },
  ],
  blended: { estimated: false, text: "not estimated — Reserve: needs fred.dgs6mo (srv)" },
  coverage: { months: "80.0", min_months: "6", below_minimum: false },
};
const VERSION = (over = {}) => ({ id: "p1", version_no: 2, scope_type: "household", preset: "custom", near_months: 3,
  reserve_months: 12, horizon_months: 24, rung_count: 8, buffer_months: "0", objective: "balanced", objective_score: 3,
  min_coverage_months: "6", status: "active", maker_names: ["Ann Maker"], approved_by_name: "Cal Checker",
  approved_at: "2026-11-08T10:00:00", derived: DERIVED, ...over });
const SCOPE = (over = {}) => ({
  scope_type: "household", scope_id: "h1", label: "The household (srv)", active: VERSION(),
  open: VERSION({ id: "p2", version_no: 3, status: "pending_approval", near_months: 4, maker_names: ["Ann Maker"],
    submitted_at: "2026-11-09T09:00:00", can_submit: false, can_decide: true, decide_blocked: null,
    diff: [{ field: "near_months", label: "Near-term / Reserve boundary (months) (srv)", from: "3", to: "4" }] }),
  history: [VERSION({ id: "p2", version_no: 3, status: "pending_approval" }), VERSION()],
  questionnaire: { scheduled_12m: "150000.00", trailing_3m: null, trailing_basis: "calls and fees (srv)",
    warnings: [{ key: "no_history", text: "no outflow history" }] },
  ...over,
});
const ENV = (over = {}, scopeOver = {}) => ({
  household: { id: "h1", name: "Fixture Family" }, member_view: false, permissions: AUTHOR,
  labels: { "cash.yield_estimate": { text: "Estimates use the Treasury curve (library)" },
    "cash.coverage_estimate": { text: "Coverage estimate (library)" } },
  policy: { scopes: [SCOPE(scopeOver)], member_view: false, actions: ["save_draft", "submit", "decide", "preview"],
    vocabularies: VOC(["near_months", "reserve_months"]), override_candidates: [] },
  ...over,
});
const MEMBER = () => ({ ...ENV({ member_view: true, permissions: READER }),
  policy: { scopes: [{ scope_type: "household", scope_id: "h1", label: "The household (srv)", active: VERSION() }],
    member_view: true, actions: [], vocabularies: VOC([]) } });

test("cash02: the sleeve table shows months, $ and % with each yield and its label from the server", () => {
  const out = render(h(SleeveTable, { derived: DERIVED, env: ENV() }));
  assert.ok(out.includes("Near-term (srv)") && out.includes("months 0–3") && out.includes("$100,000.00") && out.includes("10.0%"), out);
  assert.ok(out.includes("3.94% — 3-month Treasury, derived (estimate — Treasury curve proxy, as of 2026-11-05)"), out);
  assert.ok(out.includes("not estimated — needs fred.dgs6mo or fred.dgs1"), "a missing series names what it needs");
  assert.ok(out.includes("after-tax: needs a tax profile") && out.includes("2.64%"), "after-tax only where a profile exists");
  assert.ok(out.includes("Estimates use the Treasury curve (library)"), "the yield label comes from the library");
  assert.ok(out.includes("unallocated $780,000.00"));
});

test("cash02: the editor has two boundary sliders, rung count, preset chips, the diff and 'draft — not saved' only once edited", () => {
  const env = ENV();
  const el = h(PolicyEditor, { env, scope: env.policy.scopes[0] });
  assert.equal(ranges(el), 2, "two boundary sliders");
  const b = buttons(el);
  for (const p of ["Stay liquid (srv)", "Balanced (srv)", "Recommended (srv)", "Custom (srv)", "Save draft"]) assert.ok(b.includes(p), b.join("|"));
  const out = render(el);
  assert.ok(out.includes("Rungs") && out.includes("Minimum coverage (months)") && out.includes("Most yield (srv)"), out);
  assert.ok(out.includes("Near-term / Reserve boundary (months) (srv): 3 → 4"), "the diff vs active");
  assert.ok(!out.includes("draft — not saved"), "nothing is unsaved before an edit");
  assert.ok(out.includes("It takes effect only when a different person approves it"));
  // a non-custom preset sends no boundaries (the server applies the preset); whole months stay numbers, minimum text
  const body = L.policyBody({ scope_type: "household", preset: "stay_liquid", near_months: 3, reserve_months: 12,
    horizon_months: "24", rung_count: 8, buffer_months: "0", objective_score: 3, min_coverage_months: "6" });
  assert.deepEqual(body, { scope_type: "household", preset: "stay_liquid", horizon_months: 24, rung_count: 8,
    objective_score: 3, buffer_months: "0", min_coverage_months: "6" });
  const f = L.setBoundary({ near_months: 3, reserve_months: 12, preset: "balanced" }, "near_months", "15", env);
  assert.deepEqual([f.near_months, f.reserve_months, f.preset], [15, 15, "custom"], "N never passes R");
});

test("cash02: the questionnaire opens at step 1 prefilled from the schedule with the cross-check warning", () => {
  const env = ENV();
  const out = render(h(Questionnaire, { env, scope: env.policy.scopes[0] }));
  assert.ok(out.includes("step 1 of 3") && out.includes("$150,000.00") && out.includes("no outflow history"), out);
  assert.deepEqual(buttons(h(Questionnaire, { env, scope: env.policy.scopes[0] })), ["Next"]);
  const body = L.recommendationBody(L.initialForm(env, env.policy.scopes[0]), 5, "200000.00");
  assert.equal(body.preset, "recommended");
  assert.equal(body.objective_score, 5);
  assert.deepEqual(body.questionnaire, { outflow_12m: "200000.00" });
  assert.ok(!("near_months" in body), "the recommendation's boundaries are the server's mapping");
});

test("cash02: approval status — Approve / Reject only for a different eligible person; history listed", () => {
  const env = ENV();
  const s = env.policy.scopes[0];
  const out = render(h(ApprovalStatus, { env, scope: s }));
  assert.ok(out.includes("Version 3: Awaiting approval (srv)") && out.includes("Made by Ann Maker"), out);
  assert.ok(out.includes("approved by Cal Checker"), "history names the approver");
  assert.deepEqual(buttons(h(ApprovalStatus, { env, scope: s })), ["Approve", "Reject"]);
  const maker = SCOPE({ open: { ...s.open, can_decide: false, decide_blocked: "You made this version (srv)" } });
  assert.deepEqual(buttons(h(ApprovalStatus, { env: ENV({}, maker), scope: maker })), [], "the maker gets no decision");
  assert.ok(render(h(ApprovalStatus, { env: ENV({}, maker), scope: maker })).includes("You made this version (srv)"));
  const draft = SCOPE({ open: { ...s.open, status: "draft", can_submit: true, can_decide: false } });
  assert.deepEqual(buttons(h(ApprovalStatus, { env: ENV({}, draft), scope: draft })), ["Submit for approval"]);
});

test("cash02: a member sees the active policy read-only with the library labels; a lost envelope fails CLOSED", () => {
  const m = MEMBER();
  assert.deepEqual(buttons(h(PolicyPanel, { env: m })), []);
  assert.equal(ranges(h(PolicyPanel, { env: m })), 0);
  const out = render(h(PolicyPanel, { env: m }));
  assert.ok(out.includes("Cash policy — The household (srv)") && out.includes("$100,000.00"), out);
  assert.ok(out.includes("Estimates use the Treasury curve (library)") && out.includes("Coverage estimate (library)"), out);
  assert.ok(!out.includes("History") && !out.includes("Awaiting approval"), "no drafts or history for a member");
  // a member envelope that (wrongly) claimed authoring renders nothing writable either
  const forged = { ...m, permissions: AUTHOR, policy: { ...m.policy, actions: ["save_draft", "submit", "decide", "preview"],
    vocabularies: VOC(["near_months"]) } };
  assert.deepEqual(buttons(h(PolicyPanel, { env: forged })), []);
  const s = SCOPE();
  for (const env of [null, {}, { permissions: null }, ENV({ permissions: { ...AUTHOR, can_author_policy: "true" } }),
    { ...ENV(), policy: { ...ENV().policy, actions: [] } }, { ...ENV(), policy: { ...ENV().policy, vocabularies: VOC([]) } },
    { ...ENV(), policy: { ...ENV().policy, vocabularies: { editable: "near_months" } } }]) {
    const c = L.policyControls(env, s);
    assert.ok(Object.values(c).every((v) => v === false), JSON.stringify(env)?.slice(0, 80));
  }
  assert.equal(L.policyControls(ENV(), SCOPE({ open: { ...s.open, can_decide: "yes" } })).decide, false);
  const writer = buttons(h(PolicyPanel, { env: ENV() }));
  assert.ok(writer.includes("Save draft") && writer.includes("Approve") && writer.includes("Next"), writer.join("|"));
  assert.equal(render(h(PolicyPanel, { env: { ...MEMBER(), policy: { scopes: [], member_view: true } } })).includes("No cash policy is in force"), true);
});
