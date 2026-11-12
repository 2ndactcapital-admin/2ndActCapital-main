// TRACK1 — the Tracked view (Security master) and the Market Indicators "Track" offer. The REAL components rendered
// with react-dom/server from envelopes shaped exactly like GET /admin/security-data/master/tracked, /securities and
// GET /market/securities/search return them; the pure helpers (lib/track1.mjs) the click handlers run.
// Write controls exist ONLY when the envelope's can_track / can_untrack is literally true (fails closed).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { TrackedView, runBulk } = await import("../../components/securityData/SecurityMaster.jsx");
const { default: SelectionPanel } = await import("../../components/market/SelectionPanel.jsx");
const { interpretSecuritySearch } = await import("../../lib/market/catalogModel.mjs");
const { catalogBody } = await import("../market/fixtures.mjs");
const T = await import("../../lib/track1.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/\s+/g, " ");
const FILTERS = ["security_type", "fund_structure", "id_type", "missing_id_type", "provider", "field", "missing_field",
  "price_coverage", "has_price", "q", "exchange", "category", "tracked", "in_list"];
const SU = { can_read: true, can_write: true, is_super_admin: true, can_track: true, can_untrack: true };
const VIEW_ONLY = { can_read: true, can_write: false, is_super_admin: false, can_track: false, can_untrack: false };
const tracked = (perms = SU, over = {}) => ({
  permissions: perms,
  progress: { tracked: 2700, loaded: 1240, waiting: 1460, label: "1,240 of 2,700 loaded" },
  lists: [{ list_key: "sp500", name: "S&P 500", source_detail: "SPY holdings via ssga_spdr", as_of: "2026-11-11",
    member_count: 503, tracked: 0, miss_count: 1, description: "x" }],
  vocabularies: { filters: FILTERS, editable: perms.can_track ? ["track", "untrack", "note"] : [], track_cap: 3000,
    security_types: [{ key: "fund", label: "fund" }], exchanges: [{ key: "XNYS", label: "XNYS", count: 9 }],
    categories: [], lists: [{ key: "sp500", label: "S&P 500" }], has_price: [], tracked: [] },
  ...over,
});
const LIST = { permissions: SU, total: 2, filters: { exchange: "XNYS" },
  rows: [{ id: "a1", name: "Alpha Inc", security_type: "equity", identifiers: { ticker: "ALP" }, tracked: true, has_price: true },
    { id: "b2", name: "Beta Corp", security_type: "equity", identifiers: { ticker: "BET" }, tracked: false, has_price: false }] };

test("Tracked view, Super Admin: progress, lists with Track list…, the filter, rows, and every bulk action", () => {
  const html = render(h(TrackedView, { envelope: tracked(), list: LIST, filter: { exchange: "XNYS" }, selected: ["b2"] }));
  const t = text(html);
  for (const w of ["1,240 of 2,700 loaded", "S&P 500", "SPY holdings via ssga_spdr", "2026-11-11", "503", "Track list…",
    "Track selected (1)", "Untrack selected (1)", "Track all 2 matching…", "Untrack all 2 matching…", "Alpha Inc", "Beta Corp",
    "loaded", "none yet", "tracked"]) assert.ok(t.includes(w), w);
  assert.ok(html.includes('href="/admin/security-data/master?view=tracked&amp;in_list=sp500"'), "list links to its members");
  assert.equal((html.match(/data-testid="tracked-row"/g) || []).length, 2);
});

test("Tracked view fails closed: view-only → no write control at all; no envelope → nothing", () => {
  const html = render(h(TrackedView, { envelope: tracked(VIEW_ONLY), list: LIST, filter: { exchange: "XNYS" } }));
  for (const w of ["Track list", "Track selected", "Untrack", "Track all", 'type="checkbox"', "bulk-actions"]) {
    assert.ok(!html.includes(w), `view-only renders no ${w}`);
  }
  assert.ok(html.includes("tracked-view") && html.includes("Alpha Inc"), "still reads");
  for (const e of [null, {}, { permissions: { can_read: "true" } }]) assert.equal(render(h(TrackedView, { envelope: e, list: LIST })), "");
  const str = render(h(TrackedView, { envelope: tracked({ ...SU, can_track: "true", can_untrack: 1 }), list: LIST, filter: { exchange: "XNYS" } }));
  assert.ok(!str.includes("Track selected") && !str.includes("Untrack"), "truthy-but-not-true never grants");
});

test("filters: only the server's names reach a link or a request; no filter → no 'all matching' action", () => {
  const env = tracked();
  assert.deepEqual(T.cleanTrackedFilter({ exchange: " XNYS ", org_id: "x", evil: "1", q: "" }, env), { exchange: "XNYS" });
  assert.deepEqual(T.cleanTrackedFilter({ exchange: "XNYS" }, {}), {}, "no vocabulary → nothing");
  assert.equal(T.selectionBody({ filters: { org_id: "x" } }, env), null);
  assert.deepEqual(T.selectionBody({ ids: ["a", "a", "b"] }, env), { ids: ["a", "b"] });
  assert.deepEqual(T.selectionBody({ listKey: "sp500" }, env), { list_key: "sp500" });
  const html = render(h(TrackedView, { envelope: env, list: LIST, filter: {} }));
  assert.ok(!html.includes("Track all") && html.includes("Track selected (0)"));
});

test("confirm text carries the server's count and estimate; over the cap → a refusal, nothing to confirm", () => {
  const est = { total: 2700, cap: 3000, over_cap: false, estimate: { provider: "yahoo_chart", to_load: 2650, estimated_calls: 2650,
    days_at_budget: 2, daily_budget: 2000, nights_nightly_only: 21, coverage_after: 2729, upkeep_calls_per_day: 2729,
    coverage_now: 29, warnings: ["keeping 2,729 securities current needs 2,729 calls a day — more than the daily budget of 2,000; some will go stale"] } };
  const c = T.confirmText("track", est);
  for (const w of ["Track 2,700 securities?", "2,650 need their 5-year history", "2,650 calls to yahoo_chart", "2 day(s)",
    "2,000", "21 night(s)", "2,729 calls a day", "Note: keeping 2,729"]) assert.ok(c.includes(w), w);
  const u = T.confirmText("untrack", { total: 5, estimate: { coverage_now: 40, coverage_after: 35, warnings: [] } });
  assert.ok(u.startsWith("Untrack 5 securities?") && u.includes("from 40 to 35") && u.includes("kept"));
  const over = { total: 8666, cap: 3000, over_cap: true, estimate: null };
  assert.equal(T.confirmText("track", over), null);
  assert.equal(T.refusalText(over), "8,666 securities match — one action may change at most 3,000. Narrow the filter.");
  assert.equal(T.actionBody({ filters: { exchange: "XNYS" } }, over, "x"), null);
  assert.deepEqual(T.actionBody({ filters: { exchange: "XNYS" } }, est, "  why "), { filters: { exchange: "XNYS" }, expected_total: 2700, note: "why" });
});

test("runBulk: estimate → confirm with the server's text → note → write carrying the confirmed count; cancel writes nothing", async () => {
  const env = tracked();
  const calls = [];
  const post = async (url, opts) => {
    calls.push([url, JSON.parse(opts.body)]);
    if (url.endsWith("/estimate")) return { total: 2, cap: 3000, over_cap: false, estimate: { to_load: 1, estimated_calls: 1, days_at_budget: 1, daily_budget: 2000, coverage_after: 31, upkeep_calls_per_day: 31, warnings: [] } };
    return { result: { tracked: 2, already_tracked: 0, unknown: [] }, progress: { label: "0 of 2 loaded" } };
  };
  let asked = null;
  const out = await runBulk({ verb: "track", selection: { filters: { exchange: "XNYS", bogus: "1" } }, envelope: env,
    ask: (m) => { asked = m; return true; }, askNote: () => "core universe", post });
  assert.ok(asked.startsWith("Track 2 securities?"));
  assert.deepEqual(calls[0], ["/api/admin/security-data/master/tracked/estimate", { filters: { exchange: "XNYS" }, removing: false }]);
  assert.deepEqual(calls[1], ["/api/admin/security-data/master/tracked/track", { filters: { exchange: "XNYS" }, expected_total: 2, note: "core universe" }]);
  assert.equal(out.result.tracked, 2);
  calls.length = 0;
  const no = await runBulk({ verb: "untrack", selection: { ids: ["a1"] }, envelope: env, ask: () => false, askNote: () => "x", post });
  assert.ok(no.cancelled && calls.length === 1 && calls[0][1].removing === true, "declined → only the estimate was asked");
  calls.length = 0;
  const over = await runBulk({ verb: "track", selection: { filters: { security_type: "fund" } }, envelope: env, ask: () => true,
    askNote: () => "x", post: async (u, o) => { calls.push(u); return { total: 6133, cap: 3000, over_cap: true }; } });
  assert.ok(over.error.includes("6,133") && calls.length === 1, "over the cap → refused before any write");
});

// ── Market Indicators: the per-result Track offer ─────────────────────────────
const SEARCH = { min_chars: 3, max_chars: 50, max_results: 7 };
function catalog() { const b = catalogBody(); b.vocabularies.search = { ...SEARCH }; return b; }
const UNPRICED = [{ id: "u1", name: "Unpriced One", ticker: "UNO", selectable: false, unselectable_reason: "no_price_history", security_type: "equity" },
  { id: "u2", name: "Unpriced Two", ticker: "UNT", selectable: false, unselectable_reason: "no_price_history", security_type: "fund" }];
const searchBody = (tracking) => ({ query: "unp", securities: UNPRICED, permissions: { can_read: true },
  ...(tracking === undefined ? {} : { tracking }) });
const panel = (body) => render(h(SelectionPanel, { catalog: catalog(), selection: [], onChange: () => {},
  initialSearch: { text: "unp", state: interpretSecuritySearch({ ok: true, body }) } }));

test("market search: a Super Admin gets Track on an unpriced result; a tracked one says so", () => {
  const html = panel(searchBody({ can_track: true, disabled_reason: null, tracked: ["u2"], note: "Tracking queues prices." }));
  assert.equal((html.match(/data-market="track"/g) || []).length, 1, "Track only on the untracked result");
  assert.ok(text(html).includes("tracked — prices queued") && text(html).includes("Tracking queues prices."));
});

test("market search: anyone else sees the server's disabled reason and no Track; no tracking block → nothing", () => {
  const html = panel(searchBody({ can_track: false, disabled_reason: "Only a Super Admin can track a security for prices.", tracked: [] }));
  assert.ok(!html.includes('data-market="track"'));
  assert.equal((html.match(/Only a Super Admin can track a security for prices\./g) || []).length, 2);
  const none = panel(searchBody(undefined));
  assert.ok(!none.includes('data-market="track"') && !none.includes("track-disabled"), "no block → no control, no reason");
  const truthy = panel(searchBody({ can_track: "true", tracked: [] }));
  assert.ok(!truthy.includes('data-market="track"'), "can_track must be literally true");
  assert.deepEqual(T.singleTrackBody({ id: "u1" }), { ids: ["u1"], expected_total: 1, note: "Tracked from the Market Indicators search" });
  assert.equal(T.trackOffer(T.searchTracking(searchBody({ can_track: true, tracked: [] })), { id: "x", selectable: true }, null), null,
    "a selectable result is never offered Track");
});
