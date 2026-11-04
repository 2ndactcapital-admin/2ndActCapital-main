// SECMASTER1 — the Security master screen's REAL components rendered with react-dom/server from envelopes
// shaped exactly like the API returns them. Run now / close held / review decisions must FAIL CLOSED: no
// control without the server's own flag being exactly true (and, for decisions, the outcome listed).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { StatusPanel, JobsView, ReviewQueueView, PlatformJobsPanel } =
  await import("../../components/securityData/SecurityMaster.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const render = (el) => renderToStaticMarkup(el);
const SU = { can_read: true, can_write: true, can_run: true, can_decide: true, can_close_held: true, is_super_admin: true };
const NONE = { can_read: false, can_write: false, can_run: false, can_decide: false, can_close_held: false };
const VOCAB = { review_kinds: [{ key: "ticker_conflict", label: "Ticker held by a security on another exchange (or with none)" }],
  outcomes: [{ key: "resolved", label: "Resolved" }, { key: "dismissed", label: "Dismissed" }], editable: ["outcome", "note"] };
const jobs = [{ key: "security_master_nightly", title: "Security master — nightly", schedule_note: "Weekdays 18:45 New York",
  trigger_active: true, last_run: { id: "r1", status: "held", started_at: "2026-11-04T23:45:00Z", error_detail: "PlatformJobError: x" } }];
const rows = [{ id: "v1", kind: "ticker_conflict", kind_label: "Ticker held by a security on another exchange (or with none)",
  subject_key: "ticker:SPX", incoming_value: "SPX@XNAS", existing_value: "no exchange (index)", provider_key: "nasdaq_trader",
  actions: ["resolved", "dismissed"] }];

test("super admin: Run now, Close held run and both decisions render", () => {
  const j = text(render(h(JobsView, { envelope: { jobs, permissions: SU, vocabularies: VOCAB } })));
  assert.ok(j.includes("Run now") && j.includes("Close held run"));
  const r = text(render(h(ReviewQueueView, { envelope: { rows, permissions: SU, vocabularies: VOCAB } })));
  assert.ok(r.includes("Resolved") && r.includes("Dismissed") && r.includes("ticker:SPX"));
});

test("view without the flags: no Run now, no close, no decision — and a lost envelope fails closed", () => {
  for (const env of [{ jobs, permissions: NONE, vocabularies: VOCAB }, { jobs }, { jobs, permissions: { can_run: "true" } }]) {
    const j = text(render(h(JobsView, { envelope: env })));
    assert.ok(!j.includes("Run now") && !j.includes("Close held run"), JSON.stringify(env.permissions));
  }
  const r = text(render(h(ReviewQueueView, { envelope: { rows, permissions: SU, vocabularies: { ...VOCAB, outcomes: [] } } })));
  assert.ok(!r.includes("Dismissed"), "no outcome listed by the server → no decision control");
  const r2 = text(render(h(ReviewQueueView, { envelope: { rows } })));
  assert.ok(!r2.includes("Resolved") && !r2.includes("Dismissed"));
  assert.equal(render(h(PlatformJobsPanel, { initialJobs: null })), "");
  assert.equal(render(h(PlatformJobsPanel, { initialJobs: { jobs, permissions: NONE } })), "");
});

test("close held run only for a held last run", () => {
  const ok = [{ ...jobs[0], last_run: { ...jobs[0].last_run, status: "completed" } }];
  const j = text(render(h(JobsView, { envelope: { jobs: ok, permissions: SU, vocabularies: VOCAB } })));
  assert.ok(j.includes("Run now") && !j.includes("Close held run"));
});

test("status panel prints the server's counts", () => {
  const st = { securities_total: 12034, securities_by_type: { equity: 9000, fund: 3034 }, securities_by_source: { nasdaq_trader: 11000 },
    identifiers_by_type: { ticker: 12000 }, review_open: 7, review_open_by_kind: { ticker_conflict: 7 },
    sec_13f: { quarter: "2026q3", unmapped: 412, unmapped_reasons: { "not mapped — OpenFIGI found no identifier": 12 } } };
  const t = text(render(h(StatusPanel, { envelope: { status: st, permissions: SU, vocabularies: VOCAB } })));
  for (const w of ["12034", "equity: 9000", "nasdaq_trader: 11000", "2026q3", "412", "Ticker held by a security"]) assert.ok(t.includes(w), w);
});
