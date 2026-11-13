// DAILYREBAL1 — the Accounts screen's "Scheduled rebalancing" panel: the REAL ScheduledRebalancingPanel rendered with
// react-dom/server from a `schedule` block shaped exactly like GET /proposals/accounts returns it
// (services.rebalance_schedule.panel / day_out). Every word is the server's; no control renders; no block → nothing.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: Panel } = await import("../../components/proposals/ScheduledRebalancingPanel.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const DAY = {
  id: "d1", run_day: "2026-11-13", status: "completed", status_label: "Completed", run_time_local: "07:30",
  started_at: "2026-11-13T12:31:04+00:00", finished_at: "2026-11-13T12:31:40+00:00", prices_day: "2026-11-12",
  prices_status: "complete", partial_prices: false, oldest_price_date: "2026-11-12", nightly_ok: true,
  waiting_reason: null, checked: 3, proposed: 1, skipped: 2, failed: 0, errors: [],
  results: [
    { account_id: "a1", account: "****1001", household: "Olivo Household", action: "proposed", reason: "scheduled",
      action_label: "Proposal made", reason_label: "Scheduled rebalance", detail: "daily rebalance due", proposal_id: "p1" },
    { account_id: "a2", account: "****1002", household: "Olivo Household", action: "skipped", reason: "open_proposal",
      action_label: "Skipped", reason_label: "An open proposal already exists", detail: "proposal p0 is awaiting approval" },
    { account_id: "a3", account: "****1003", household: "Olivo Household", action: "skipped", reason: "advice_only",
      action_label: "Skipped", reason_label: "Advice only — a person rebalances a non-discretionary account",
      detail: "Advice only — a person rebalances a non-discretionary account" },
  ],
};
const SCHED = {
  today: "2026-11-13", is_business_day: true, run_time_local: "07:30", prices_fallback_local: "10:00",
  opted_in_accounts: 3, day: DAY, is_today: true,
  proposals: [{ id: "p1", account_id: "a1", status: "pending_approval", status_label: "Awaiting approval",
    trigger: "scheduled", trigger_label: "Scheduled rebalance" }],
};

test("today's run: time, inputs, counts, each account's outcome and reason, the proposal waiting — server words", () => {
  const html = renderToStaticMarkup(h(Panel, { schedule: SCHED }));
  const t = text(html);
  for (const w of ["Scheduled rebalancing", "Today", "Completed", "07:30 New York", "2026-11-12 · complete",
    "Ran", "3 checked · 1 proposed · 2 skipped", "****1001", "Proposal made — Scheduled rebalance",
    "Skipped — An open proposal already exists", "proposal p0 is awaiting approval",
    "Skipped — Advice only — a person rebalances a non-discretionary account",
    "Scheduled rebalance — Awaiting approval", "2026-11-13 12:31 UTC"]) assert.ok(t.includes(w), w);
  assert.ok(html.includes('href="/portfolio/proposals/p1"'));
  assert.ok(!/<button|<form|<input|<select/.test(html), "read-only: no control renders");
  assert.ok(!html.includes("schedule-partial"), "complete prices → no partial flag");
});

test("partial prices are flagged with the oldest valuation date; a waiting day shows the server's reason", () => {
  const partial = { ...SCHED, day: { ...DAY, partial_prices: true, prices_status: "running", oldest_price_date: "2026-11-10" } };
  const t = text(renderToStaticMarkup(h(Panel, { schedule: partial })));
  assert.ok(t.includes("Ran on partial prices — oldest valuation date 2026-11-10"));
  const waiting = { ...SCHED, proposals: [], day: { ...DAY, status: "waiting", status_label: "Waiting for today's inputs",
    waiting_reason: "prices for the last close are running — waiting until complete or 10:00 New York", results: [],
    checked: 0, proposed: 0, skipped: 0, started_at: null } };
  const tw = text(renderToStaticMarkup(h(Panel, { schedule: waiting })));
  assert.ok(tw.includes("Waiting for today's inputs"));
  assert.ok(tw.includes("prices for the last close are running — waiting until complete or 10:00 New York"));
});

test("errors render; no schedule block, or nothing on a schedule and no run → nothing (never a client default)", () => {
  const failed = { ...SCHED, day: { ...DAY, failed: 1, errors: ["account a9: RuntimeError: boom"] } };
  const t = text(renderToStaticMarkup(h(Panel, { schedule: failed })));
  assert.ok(t.includes("1 failed") && t.includes("account a9: RuntimeError: boom"));
  assert.equal(renderToStaticMarkup(h(Panel, { schedule: null })), "");
  assert.equal(renderToStaticMarkup(h(Panel, { schedule: undefined })), "");
  assert.equal(renderToStaticMarkup(h(Panel, { schedule: { ...SCHED, opted_in_accounts: 0, day: null, proposals: [] } })), "");
  const none = text(renderToStaticMarkup(h(Panel, { schedule: { ...SCHED, day: null, proposals: [] } })));
  assert.ok(none.includes("No run recorded yet."));
});
