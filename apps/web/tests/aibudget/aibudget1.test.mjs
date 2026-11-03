// AIBUDGET1 — the REAL budget control and daily-run components rendered with
// react-dom/server from envelopes shaped exactly like routers/ai_ensembles.py
// returns them. Every write control (the budget control, "Run now") must FAIL
// CLOSED: none without permissions.can_write === true. Vocabularies, labels and
// sentences come from the envelope — a missing vocabulary yields no options.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const lib = await import("../../lib/aiBudget.mjs");
const { BudgetControl, BudgetCell } = await import("../../components/admin/EnsembleBudget.jsx");
const { default: DailyRunsView, DailyJobCard } = await import("../../components/admin/DailyRunsView.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) =>
  html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"')
    .replace(/\s+/g, " ");

const KINDS = [
  { key: "unlimited", label: "Unlimited", unit: null, input: null },
  { key: "tokens_per_day", label: "Tokens per day", unit: "tokens", input: "whole_number" },
  { key: "usd_per_day", label: "Dollars per day", unit: "USD", input: "dollars" },
];
const picker = (canWrite) => ({
  task_key: "note_terms_extraction",
  permissions: { can_read: true, can_write: canWrite, is_super_admin: canWrite },
  vocabularies: {
    budget_kinds: KINDS,
    semantics_template: "This job stops for the day when {model} has used {budget} today across Hollisworks.",
    unlimited_template: "No daily limit on {model} for this job.",
    shared_rule: "Usage is counted per model across every job that calls it.",
  },
  day_boundary: { timezone: "UTC", label: "Each budget day starts at midnight UTC", today: "2026-11-03" },
  usage_today: { "gpt-5-mini": { total_tokens: 1234, cost_usd: "0.0100" } },
});

test("lib: canWrite is true only for exactly true", () => {
  assert.equal(lib.canWrite({ permissions: { can_write: true } }), true);
  for (const env of [null, {}, { permissions: {} }, { permissions: { can_write: "true" } }, { permissions: { can_write: 1 } }]) {
    assert.equal(lib.canWrite(env), false);
  }
});

test("lib: no vocabulary in the envelope → no budget kinds (no client fallback)", () => {
  assert.deepEqual(lib.budgetKinds({}), []);
  assert.deepEqual(lib.budgetKinds(null), []);
  assert.equal(lib.budgetKinds(picker(true)).length, 3);
});

test("lib: validation mirrors the server — positive, whole tokens, dollars in cents", () => {
  assert.equal(lib.budgetError(KINDS, "unlimited", ""), null);
  assert.ok(lib.budgetError(KINDS, "tokens_per_day", ""));
  assert.ok(lib.budgetError(KINDS, "tokens_per_day", "0"));
  assert.ok(lib.budgetError(KINDS, "tokens_per_day", "-5"));
  assert.ok(lib.budgetError(KINDS, "tokens_per_day", "1000.5"));
  assert.equal(lib.budgetError(KINDS, "tokens_per_day", "1000000"), null);
  assert.ok(lib.budgetError(KINDS, "usd_per_day", "2.555"));
  assert.equal(lib.budgetError(KINDS, "usd_per_day", "2.55"), null);
  assert.ok(lib.budgetError(KINDS, "nonsense", "1"));
});

test("lib: the payload carries both slots; unlimited sends no amount", () => {
  const p = lib.budgetPayload({
    model_1_budget: { kind: "tokens_per_day", value: " 250000 " },
    model_2_budget: { kind: "unlimited", value: "" },
  });
  assert.deepEqual(p, {
    model_1_budget_kind: "tokens_per_day", model_1_budget_value: "250000",
    model_2_budget_kind: "unlimited", model_2_budget_value: null,
  });
});

test("budget control: shown right after a chosen model for a writer, with the server's sentence", () => {
  const html = render(h(BudgetControl, {
    envelope: picker(true), slot: "model_1", slotLabel: "Model 1", model: "gpt-5-mini",
    budget: { kind: "tokens_per_day", value: "1000000" }, onChange: () => {},
  }));
  const t = text(html);
  assert.ok(html.includes('name="model_1_budget_kind"'));
  assert.ok(html.includes('name="model_1_budget_value"'), "a number input for a limited kind");
  for (const k of KINDS) assert.ok(t.includes(k.label), `option ${k.label} from the vocabulary`);
  assert.ok(t.includes("This job stops for the day when gpt-5-mini has used 1,000,000 tokens today across Hollisworks."), t);
  assert.ok(t.includes("1,234 tokens"), "today's shared usage is shown");
});

test("budget control: unlimited shows no amount input and the unlimited sentence", () => {
  const html = render(h(BudgetControl, {
    envelope: picker(true), slot: "model_2", slotLabel: "Model 2", model: "deepseek-v3.2",
    budget: { kind: "unlimited", value: "" }, onChange: () => {},
  }));
  assert.ok(!html.includes('name="model_2_budget_value"'));
  assert.ok(text(html).includes("No daily limit on deepseek-v3.2 for this job."));
});

test("budget control: FAILS CLOSED — nothing for a view-only or envelope-less caller, or before a model", () => {
  const props = { slot: "model_1", slotLabel: "Model 1", model: "gpt-5-mini", budget: { kind: "", value: "" },
    onChange: () => {} };
  assert.equal(render(h(BudgetControl, { ...props, envelope: picker(false) })), "");
  assert.equal(render(h(BudgetControl, { ...props, envelope: { ...picker(true), permissions: undefined } })), "");
  assert.equal(render(h(BudgetControl, { ...props, envelope: null })), "");
  assert.equal(render(h(BudgetControl, { ...props, envelope: picker(true), model: "" })), "");
});

test("history cell: each config's budget, formatted from the vocabulary", () => {
  const row = { model_1_budget_kind: "usd_per_day", model_1_budget_value: "25.00",
    model_2_budget_kind: "unlimited", model_2_budget_value: null };
  assert.ok(text(render(h(BudgetCell, { envelope: picker(false), row, slot: "model_1" }))).includes("$25.00 per day"));
  assert.ok(text(render(h(BudgetCell, { envelope: picker(false), row, slot: "model_2" }))).includes("Unlimited"));
});

const runsEnvelope = (canWrite, runs, active = true) => ({
  permissions: { can_read: true, can_write: canWrite },
  vocabularies: {
    statuses: [
      { key: "running", label: "Running" },
      { key: "finished_queue_empty", label: "Finished — queue empty" },
      { key: "stopped_budget", label: "Stopped — daily budget reached" },
      { key: "stopped_error", label: "Stopped — error" },
    ],
    budget_kinds: KINDS,
    never_scheduled: [{ task_key: "statement_positions", label: "Member account statements" }],
  },
  day_boundary: { timezone: "UTC", label: "Each budget day starts at midnight UTC", today: "2026-11-03" },
  schedule: { starts_after_utc: "00:05", enabled: true, history_days: 30 },
  rows: [{
    task_key: "note_terms_extraction", label: "Structured-note terms", data_label: "Public SEC filings",
    active: active ? { model_1: "gpt-5-mini", model_2: "gpt-5-nano", model_1_budget_kind: "tokens_per_day",
      model_1_budget_value: "250000", model_2_budget_kind: "unlimited", model_2_budget_value: null } : null,
    queue_pending: 12, queue_pending_capped_at: 200, today: "2026-11-03",
    today_runs: runs, history: runs,
  }],
});
const stopped = {
  id: "r1", budget_day: "2026-11-03", trigger_label: "Daily schedule", status: "stopped_budget",
  stopped_model: "gpt-5-mini", stopped_budget_kind: "tokens_per_day", stopped_budget_value: "250000",
  documents_done: 7, documents_failed: 0,
  usage: { "gpt-5-mini": { input_tokens: 200000, output_tokens: 40000, cost_usd: "0.13", calls: 7 } },
};

test("daily runs: today's run shows status, documents, tokens/cost per model and which budget stopped it", () => {
  const t = text(render(h(DailyRunsView, { envelope: runsEnvelope(true, [stopped]), onRunNow: () => {} })));
  assert.ok(t.includes("Stopped — daily budget reached"));
  assert.ok(t.includes("gpt-5-mini reached its budget (250,000 tokens per day)"), t);
  assert.ok(t.includes("gpt-5-mini: 200,000 in / 40,000 out"));
  assert.ok(t.includes("Never run automatically: Member account statements"));
  assert.ok(t.includes("00:05 UTC"));
});

test("daily runs: Run now only for a writer, with an active config and no run going (fail closed)", () => {
  const has = (env, onRunNow = () => {}) => render(h(DailyRunsView, { envelope: env, onRunNow })).includes(">Run now<");
  assert.equal(has(runsEnvelope(true, [stopped])), true);
  assert.equal(has(runsEnvelope(false, [stopped])), false, "view-only");
  assert.equal(has({ ...runsEnvelope(true, [stopped]), permissions: undefined }), false, "lost envelope");
  assert.equal(has(runsEnvelope(true, [{ ...stopped, status: "running", stopped_model: null }])), false, "already going");
  assert.equal(has(runsEnvelope(true, [], false)), false, "no models assigned");
  assert.equal(has(runsEnvelope(true, [stopped]), null), false, "no handler");
});

test("daily job card: no models → says the job does not run", () => {
  const env = runsEnvelope(true, [], false);
  const t = text(render(h(DailyJobCard, { envelope: env, job: env.rows[0], onRunNow: () => {} })));
  assert.ok(t.includes("No models are assigned to this job yet"));
});
