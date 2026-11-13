// edgarops1 — the Institutions table adds up (Earlier / To fetch / Downloaded /
// Stored / Skipped / Failed / Eligible / % done) and every bulk run's summary
// (cap reached, waiting reason, "—" for a run without one). The REAL
// InstitutionPicker and RunSummary are rendered with react-dom/server from the
// API's own shapes (services/edgar_bulk_queue.institutions / run_out).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: InstitutionPicker, institutionYears } = await import("../../components/admin/InstitutionPicker.jsx");
const { default: RunSummary } = await import("../../components/admin/RunSummary.jsx");
const { capNote, earlierCount, rowAddsUp, runSummaryItems, scheduledWaitingNote } =
  await import("../../lib/edgarBulkDownload.mjs");

const COLUMNS = Object.fromEntries(
  ["earlier", "to_fetch", "downloaded", "stored", "skipped", "failed", "eligible", "pct_done"]
    .map((k) => [k, { label: `L:${k}`, help: `help ${k}` }]),
);
// The server's own row shape: to_fetch (= total) by year, downloaded ⊇ stored, skipped, failed, eligible, pct_done.
function row(name, years, downloaded, stored, skipped, failed) {
  const toFetch = Object.values(years).reduce((s, n) => s + n, 0);
  const eligible = toFetch + downloaded + skipped + failed;
  return { name, years, total: toFetch, to_fetch: toFetch, downloaded, stored, skipped, failed, eligible,
    pct_done: eligible ? Math.round((1000 * (downloaded + skipped + failed)) / eligible) / 10 : null };
}
const filer = (cik, r) => ({ ...r, cik, role: "issuer" });
const A1 = row("A One", { 2021: 4, 2024: 10, 2025: 3, 2026: 7 }, 20, 18, 5, 1);
const A2 = row("A Two", { 2019: 2, 2026: 1 }, 3, 3, 0, 0);
const sum = (k) => A1[k] + A2[k];
const INST_A = { ...row("Alpha", { 2019: 2, 2021: 4, 2024: 10, 2025: 3, 2026: 8 }, sum("downloaded"), sum("stored"),
  sum("skipped"), sum("failed")), filers: [filer("1", A1), filer("2", A2)] };
const INST_B = { ...row("Beta", { 2026: 5 }, 0, 0, 0, 0), filers: [filer("3", row("B One", { 2026: 5 }, 0, 0, 0, 0))] };
const ROWS = [INST_A, INST_B];

const render = (rows, props = {}) => renderToStaticMarkup(h(InstitutionPicker, {
  rows, selected: new Set(), onToggle() {}, onAll() {}, onNone() {}, columns: COLUMNS, initialOpen: ["Alpha"], ...props,
}));
function cells(html, testid) {
  return [...html.matchAll(new RegExp(`<tr[^>]*data-testid="${testid}"[^>]*>([\\s\\S]*?)</tr>`, "g"))].map(([, inner]) =>
    Object.fromEntries([...inner.matchAll(/data-col="([^"]+)">([^<]*)</g)].map(([, k, v]) => [k, v])));
}
const n = (s) => Number(String(s).replace(/,/g, ""));

test("every rendered row adds up: years shown + Earlier = To fetch; Eligible = To fetch + Downloaded + Skipped + Failed", () => {
  const html = render(ROWS);
  const years = institutionYears(ROWS);
  assert.deepEqual(years, ["2024", "2025", "2026"]);
  const rendered = [...cells(html, "institution-row"), ...cells(html, "filer-row")];
  assert.equal(rendered.length, 4, "2 institutions + Alpha's 2 filers");
  for (const c of rendered) {
    assert.equal(n(c.eligible), n(c.total) + n(c.downloaded) + n(c.skipped) + n(c.failed), JSON.stringify(c));
    assert.ok(n(c.stored) <= n(c.downloaded), "Stored is of Downloaded");
  }
  for (const r of [...ROWS, ...INST_A.filers]) assert.ok(rowAddsUp(r, years), r.name);
  // the institution is the sum of its filers, column by column
  const [inst, f1, f2] = [cells(html, "institution-row")[0], ...cells(html, "filer-row")];
  for (const k of ["earlier", "total", "downloaded", "stored", "skipped", "failed", "eligible"]) {
    assert.equal(n(inst[k]), n(f1[k]) + n(f2[k]), `Alpha ${k} = its filers`);
  }
  assert.equal(inst.pct_done, `${INST_A.pct_done.toFixed(1)}%`);
  // a negative: a row that does NOT add up is caught
  assert.equal(rowAddsUp({ ...A1, eligible: A1.eligible + 1 }, years), false);
  assert.equal(rowAddsUp({ ...A1, stored: A1.downloaded + 1 }, years), false);
});

test("Earlier holds exactly the to-fetch years before the ones shown", () => {
  const html = render(ROWS);
  const inst = cells(html, "institution-row")[0];
  assert.equal(n(inst.earlier), 2 + 4, "2019 + 2021");
  assert.equal(earlierCount(A2, ["2024", "2025", "2026"]), 2);
  assert.equal(earlierCount(INST_B, ["2024", "2025", "2026"]), 0);
  const head = html.match(/<thead[\s\S]*?<\/thead>/)[0];
  const order = ["2024", "2025", "2026", "L:earlier", "L:to_fetch", "L:downloaded", "L:stored", "L:skipped",
    "L:failed", "L:eligible", "L:pct_done"].map((l) => head.indexOf(l));
  assert.ok(order.every((i, k) => i >= 0 && (k === 0 || i > order[k - 1])), `header order ${order}`);
  assert.match(head, /title="help eligible"/);
  assert.doesNotMatch(head, />2019<|>2021</, "older years are not columns");
});

test("2027 data: the year columns roll to 2025–2027 and 2024 moves into Earlier (nothing hard-coded)", () => {
  const r27 = { ...row("Zeta", { 2023: 1, 2024: 6, 2025: 2, 2026: 3, 2027: 9 }, 1, 1, 0, 0), filers: [] };
  const rows = [r27];
  assert.deepEqual(institutionYears(rows), ["2025", "2026", "2027"]);
  const html = render(rows, { initialOpen: [] });
  const head = html.match(/<thead[\s\S]*?<\/thead>/)[0];
  assert.match(head, />2025</);
  assert.match(head, />2027</);
  assert.doesNotMatch(head, />2024</);
  const c = cells(html, "institution-row")[0];
  assert.equal(n(c.earlier), 7, "2023 + 2024");
  assert.equal(n(c.total), 21);
  assert.ok(rowAddsUp(r27, ["2025", "2026", "2027"]));
});

const VOCAB = {
  summary_fields: [
    { key: "requested", label: "S:requested" }, { key: "fetched", label: "S:fetched" },
    { key: "stored", label: "S:stored" }, { key: "skipped_total", label: "S:skipped" },
    { key: "failed", label: "S:failed" }, { key: "carried_over", label: "S:carried" },
    { key: "duration_seconds", label: "S:duration" },
  ],
  skip_reason_labels: { not_pricing_supplement: "R:not final", prefilter_skipped: "R:prefilter" },
};
const SCHEDULED = {
  id: "r1", run_kind: "bulk_fetch", trigger_source: "schedule", status: "succeeded", display_status: "succeeded",
  summary: { requested: 2000, fetched: 900, stored: 900, skipped: { not_pricing_supplement: 1000, prefilter_skipped: 90 },
    skipped_total: 1090, failed: 10, still_to_fetch: 0, carried_over: 500, duration_seconds: 5400 },
  auto_download: { date: "2026-11-16", listed: 2000, eligible: 2500, cap: 2000, cap_reached: true, carried_over: 500 },
};

test("a scheduled run's summary: every field labelled from the envelope; cap reached — N carried over", () => {
  const html = renderToStaticMarkup(h(RunSummary, { run: SCHEDULED, vocab: VOCAB }));
  for (const f of VOCAB.summary_fields) assert.match(html, new RegExp(f.label));
  assert.match(html, /data-summary="requested"[^>]*>[\s\S]*?2,000/);
  assert.match(html, /data-summary="carried_over"[^>]*>[\s\S]*?500/);
  assert.match(html, /title="R:not final: 1000\nR:prefilter: 90"/);
  assert.match(html, /data-testid="run-cap-note">cap reached — 500 carried over</);
  assert.equal(capNote(SCHEDULED), "cap reached — 500 carried over");
  assert.equal(capNote({ ...SCHEDULED, auto_download: { ...SCHEDULED.auto_download, cap_reached: false } }), null);
  assert.match(html, /1h 30m/);
});

test("a waiting scheduled run shows its reason; a screen run has no Carried over; an old run shows —", () => {
  const waiting = { ...SCHEDULED, status: "queued", display_status: "waiting",
    auto_download: { ...SCHEDULED.auto_download, cap_reached: false,
      waiting: "waiting — Render cannot store files: R2_ACCOUNT_ID missing on the scheduler; a laptop worker can run it" } };
  const html = renderToStaticMarkup(h(RunSummary, { run: waiting, vocab: VOCAB }));
  assert.match(html, /data-testid="run-waiting">waiting — Render cannot store files/);
  const manual = { id: "m", run_kind: "bulk_fetch", trigger_source: "manual", status: "succeeded",
    summary: { requested: 62, fetched: 29, stored: 29, skipped: {}, skipped_total: 33, failed: 0, carried_over: null,
      duration_seconds: 104 } };
  const items = runSummaryItems(manual, VOCAB);
  assert.deepEqual(items.map((i) => i.key), ["requested", "fetched", "stored", "skipped_total", "failed", "duration_seconds"]);
  const old = renderToStaticMarkup(h(RunSummary, { run: { id: "o", run_kind: "refresh_index", summary: null }, vocab: VOCAB }));
  assert.match(old, /data-summary="none">—</);
  // no envelope field list -> no invented labels
  assert.equal(runSummaryItems(SCHEDULED, {}), null);
});

test("the runs list says how many scheduled runs are waiting and how many filings they hold", () => {
  assert.equal(scheduledWaitingNote({ scheduled_waiting: { runs: 2, filings: 2341 } }),
    "2 scheduled runs waiting — 2,341 filings to download");
  assert.equal(scheduledWaitingNote({ scheduled_waiting: { runs: 1, filings: 1 } }),
    "1 scheduled run waiting — 1 filing to download");
  assert.equal(scheduledWaitingNote({ scheduled_waiting: { runs: 0, filings: 0 } }), null);
  assert.equal(scheduledWaitingNote(null), null);
});
