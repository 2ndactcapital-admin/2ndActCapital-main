// S28 — the AI drift monitor's REAL components rendered with react-dom/server from envelopes shaped exactly like
// routers/ai_drift.py returns them. The threshold editor must FAIL CLOSED: no Edit / Save control unless
// permissions.can_write === true AND the server listed the editable fields; labels come only from the envelope.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const lib = await import("../../lib/aiDrift.mjs");
const { TaskTable, TaskDetail, ThresholdsPanel, Spark } = await import("../../components/admin/AiDriftMonitor.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

const VOCAB = {
  rules: [
    { key: "cost_per_call", label: "Cost per call", unit: "percent", unit_label: "% above baseline", has_threshold: true },
    { key: "model_change", label: "Model change", unit: null, unit_label: null, has_threshold: false },
  ],
  statuses: [{ key: "ok", label: "OK" }, { key: "drift", label: "Drift" }, { key: "insufficient", label: "Not enough data" },
    { key: "disabled", label: "Off" }],
  alert_statuses: [{ key: "open", label: "Open" }, { key: "closed", label: "Closed" }],
  editable: ["threshold", "min_recent", "min_baseline", "enabled", "note"], inline_editable: [],
};
const SU = { can_read: true, can_write: true, is_super_admin: true, scope: "platform" };
const OA = { can_read: true, can_write: false, is_super_admin: false, scope: "org" };
const LIVE = [
  { id: "t1", rule_key: "cost_per_call", threshold: "50", min_recent: 5, min_baseline: 20, enabled: true, version: 2, scope: "platform", task_type: null },
  { id: "t2", rule_key: "model_change", threshold: null, min_recent: 1, min_baseline: 20, enabled: true, version: 2, scope: "platform", task_type: null },
  { id: "t3", rule_key: "cost_per_call", threshold: "25", min_recent: 3, min_baseline: 10, enabled: true, version: 1, scope: "task", task_type: "member_brief" },
];
const thresholds = (permissions, vocab = VOCAB) => ({ live: LIVE, history: [], permissions, vocabularies: vocab });
const summary = (calls, cost) => ({ calls, cost_median: cost, latency_p95: 4000, fallback_rate: "0", failure_rate: "0",
  bypass_rate: "0", models: [{ model: "claude-sonnet", calls }] });
const overview = (permissions) => ({
  permissions, vocabularies: VOCAB,
  tasks: [{ task_type: "member_brief", status: "drift", recent_24h: summary(16, "0.003100"), view_7d: summary(174, "0.0017"),
    baseline: summary(174, "0.001484"), rule_status: { cost_per_call: "drift" }, open_alerts: 1,
    link: "/admin/ai-drift?task=member_brief" }],
});
const noop = async () => {};

test("lib: canWrite and editableFields are true / listed only for exactly true", () => {
  assert.equal(lib.canWrite({ permissions: { can_write: true } }), true);
  for (const env of [null, {}, { permissions: {} }, { permissions: { can_write: "true" } }, { permissions: { can_write: 1 } }]) {
    assert.equal(lib.canWrite(env), false);
    assert.deepEqual(lib.editableFields({ ...env, vocabularies: VOCAB }), []);
  }
  assert.deepEqual(lib.editableFields({ permissions: SU, vocabularies: { editable: "threshold" } }), []);
  assert.equal(lib.editableFields(thresholds(SU)).length, 5);
});

test("lib: no vocabulary → no rules and no status labels (no client fallback)", () => {
  assert.deepEqual(lib.rules({}), []);
  assert.equal(lib.statusLabel({}, "drift"), "");
  assert.equal(lib.statusLabel(overview(SU), "drift"), "Drift");
});

test("lib: thresholdDraft sends the threshold as a string, refuses bad numbers and an empty reason", () => {
  const rule = VOCAB.rules[0];
  const ok = lib.thresholdDraft(rule, { threshold: "40.5", min_recent: "5", min_baseline: "20", enabled: true, note: "why" });
  assert.deepEqual(ok.body, { rule_key: "cost_per_call", task_type: null, enabled: true, note: "why", threshold: "40.5",
    min_recent: 5, min_baseline: 20 });
  assert.ok(lib.thresholdDraft(rule, { threshold: "-1", min_recent: "5", min_baseline: "20", note: "x" }).error);
  assert.ok(lib.thresholdDraft(rule, { threshold: "1", min_recent: "0", min_baseline: "20", note: "x" }).error);
  assert.ok(lib.thresholdDraft(rule, { threshold: "1", min_recent: "5", min_baseline: "2.5", note: "x" }).error);
  assert.ok(lib.thresholdDraft(rule, { threshold: "1", min_recent: "5", min_baseline: "20", note: "  " }).error);
  const mc = lib.thresholdDraft(VOCAB.rules[1], { threshold: "9", min_recent: "1", min_baseline: "20", enabled: true, note: "n" });
  assert.equal(mc.body.threshold, null, "a rule with no threshold never sends one");
});

test("lib: sparkPath draws a line, breaks at gaps, and is empty for no data", () => {
  assert.equal(lib.sparkPath([]), "");
  assert.equal(lib.sparkPath([null, null]), "");
  const d = lib.sparkPath([0, 10, null, 5], 30, 10);
  assert.equal(d, "M0.0,10.0L10.0,0.0M30.0,5.0");
  assert.ok(render(h(Spark, { values: [1, 2], label: "Calls" })).includes("<path"));
});

test("super admin: the task table shows the server's status label and numbers; the editor renders", () => {
  const t = text(render(h(TaskTable, { envelope: overview(SU) })));
  assert.ok(t.includes("member_brief") && t.includes("Drift") && t.includes("$0.003100") && t.includes("every organization"));
  const p = text(render(h(ThresholdsPanel, { envelope: thresholds(SU), onSave: noop })));
  assert.ok(p.includes("Edit") && p.includes("Every change is a new version"), p);
  assert.ok(p.includes("always an alert") && p.includes("v2"));
});

test("org admin (can_write false): same page, NO Edit control, read-only wording; scope says your organization", () => {
  const t = text(render(h(TaskTable, { envelope: overview(OA) })));
  assert.ok(t.includes("your organization only"));
  const p = text(render(h(ThresholdsPanel, { envelope: thresholds(OA), onSave: noop })));
  assert.ok(!p.includes("Edit") && !p.includes("Save new version") && p.includes("Read-only"), p);
});

test("a lost or malformed envelope fails closed: no editor, and no rules without the server's vocabulary", () => {
  for (const env of [{ live: LIVE, vocabularies: VOCAB }, { live: LIVE, permissions: { can_write: "true" }, vocabularies: VOCAB },
    { live: LIVE, permissions: SU, vocabularies: { ...VOCAB, editable: [] } }]) {
    const p = text(render(h(ThresholdsPanel, { envelope: env, onSave: noop })));
    assert.ok(!p.includes("Edit"), JSON.stringify(env.permissions));
  }
  assert.equal(render(h(ThresholdsPanel, { envelope: { live: LIVE, permissions: SU }, onSave: noop })), "");
  assert.equal(render(h(TaskTable, { envelope: {} })), "");
  const noOnSave = text(render(h(ThresholdsPanel, { envelope: thresholds(SU) })));
  assert.ok(!noOnSave.includes("Edit"), "no save handler → no control");
});

test("a task's per-task override is shown for that task; the platform default elsewhere", () => {
  const p = text(render(h(ThresholdsPanel, { envelope: thresholds(OA), taskType: "member_brief" })));
  assert.ok(p.includes("25") && p.includes("task override") && p.includes("member_brief"));
  const d = text(render(h(ThresholdsPanel, { envelope: thresholds(OA) })));
  assert.ok(d.includes("50") && !d.includes("task override"));
});

test("task detail: series sparklines, per-org findings and alerts (open and closed) with server labels", () => {
  const env = {
    task_type: "member_brief", permissions: OA, vocabularies: VOCAB,
    series: [{ day: "2026-11-06", calls: 3, cost_median: "0.0015" }, { day: "2026-11-07", calls: 16, cost_median: "0.0031" }],
    orgs: [{ org_id: "o1", org: "2nd Act Capital", status: "drift",
      findings: [{ rule: "cost_per_call", status: "drift", sentence: "Cost per call: recent median $0.003100" }] }],
    alerts: [
      { id: "a1", status: "open", rule_key: "cost_per_call", org_name: "2nd Act Capital", detail: "x", opened_at: "t", evaluations: 2 },
      { id: "a2", status: "closed", rule_key: "model_change", org_name: "2nd Act Capital", detail: "y", opened_at: "t",
        closed_at: "u", close_reason: "back inside", evaluations: 5 },
    ],
  };
  const t = text(render(h(TaskDetail, { envelope: env })));
  assert.ok(t.includes("Open") && t.includes("Closed") && t.includes("back inside") && t.includes("Cost per call"));
  assert.ok((render(h(TaskDetail, { envelope: env })).match(/<path/g) || []).length >= 2);
  assert.equal(render(h(TaskDetail, { envelope: {} })), "");
});
