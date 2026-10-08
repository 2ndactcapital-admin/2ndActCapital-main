/**
 * Chart request behaviour (mkt04b Task 5): one request per burst of changes,
 * newest wins, keys over the server's limit blocked before sending, the
 * frequency and measure lists are the server's, fail-closed interpretation,
 * keyboard stepping and frame coalescing.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { NO_ENVELOPE_MESSAGE, interpretCatalog } from "../../lib/market/catalogModel.mjs";
import {
  CHART_DEBOUNCE_MS,
  KEY_DATES_ROUTE,
  KEY_DATES_UNAVAILABLE,
  SERIES_ROUTE,
  buildSeriesRequest,
  chartMeasureOptions,
  createFrameScheduler,
  createSeriesLoader,
  initialChartSettings,
  interpretKeyDates,
  interpretSeries,
  resolutionOptions,
  stepAnchorIndex,
} from "../../lib/market/chartRequest.mjs";
import { catalogBody, keyDatesBody, seriesBody } from "./fixtures.mjs";

const catalog = interpretCatalog({ ok: true, body: catalogBody() }).catalog;
const TODAY = "2026-10-07";

/** Manual timers: nothing fires until the test says so. */
function fakeTimers() {
  let id = 0;
  const pending = new Map();
  return {
    setTimeout(fn, ms) {
      id += 1;
      pending.set(id, { fn, ms });
      return id;
    },
    clearTimeout(h) {
      pending.delete(h);
    },
    flush() {
      const due = [...pending.values()];
      pending.clear();
      for (const t of due) t.fn();
      return due.length;
    },
    get size() {
      return pending.size;
    },
  };
}

const tick = () => new Promise((r) => setImmediate(r));

test("the routes are Next.js market routes", () => {
  assert.equal(SERIES_ROUTE, "/api/market/series");
  assert.equal(KEY_DATES_ROUTE, "/api/market/key-dates");
});

test("the request carries only keys and frequency; nothing for an empty selection", () => {
  assert.equal(buildSeriesRequest([], catalog, "monthly"), null);
  const sel = [
    { kind: "indicator", key: "t.cat_beta.1" },
    { kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000001" },
    { kind: "indicator", key: "t.cat_alpha.0" },
  ];
  const r = buildSeriesRequest(sel, catalog, "monthly");
  assert.ok(r.url.startsWith(`${SERIES_ROUTE}?`));
  const qs = new URLSearchParams(r.url.split("?")[1]);
  assert.deepEqual([...qs.keys()].sort(), ["frequency", "keys"]);
  assert.equal(qs.get("keys"), "t.cat_beta.1,t.cat_alpha.0", "an indicator and the security it prices are asked for once");
  assert.equal(qs.get("frequency"), "monthly");
  assert.ok(!/org|user/i.test(r.url));
});

test("more keys than the server's limit are blocked before anything is sent", () => {
  const body = catalogBody();
  body.vocabularies.limits.max_keys = 2;
  const small = interpretCatalog({ ok: true, body }).catalog;
  const sel = ["t.cat_alpha.1", "t.cat_alpha.2", "t.cat_alpha.3"].map((key) => ({ kind: "indicator", key }));
  const r = buildSeriesRequest(sel, small, "monthly");
  assert.deepEqual(r, { blocked: true, message: "You can select up to 2 series at a time." });
  assert.equal(r.url, undefined);
  // At the limit is allowed.
  assert.ok(buildSeriesRequest(sel.slice(0, 2), small, "monthly").url);
  // A catalog with no limit allows nothing (fail closed).
  delete body.vocabularies.limits;
  assert.equal(buildSeriesRequest(sel.slice(0, 1), interpretCatalog({ ok: true, body }).catalog, "monthly").blocked, true);
});

test("a resolution the server does not offer is never sent", () => {
  const sel = [{ kind: "indicator", key: "t.cat_alpha.1" }];
  assert.equal(buildSeriesRequest(sel, catalog, "hourly").blocked, true);
});

test("the resolution and measure lists are the server's", () => {
  assert.deepEqual(resolutionOptions(catalog.vocabularies), catalog.vocabularies.frequencies);
  assert.deepEqual(
    chartMeasureOptions(catalog.vocabularies).map((m) => m.label),
    ["Mode label index", "Mode label sigma"],
    "only the two chart measures, with the server's labels, in its order",
  );
  const body = catalogBody();
  body.vocabularies.frequencies = [{ key: "quarterly", label: "Q only" }];
  body.vocabularies.modes = [{ key: "sigma", label: "S" }, { key: "level", label: "L" }];
  const c = interpretCatalog({ ok: true, body }).catalog;
  const s = initialChartSettings(c, TODAY);
  assert.equal(s.resolution, "quarterly", "falls back to the server's first option");
  assert.equal(s.measure, "sigma");
});

test("defaults: Index, log axis, monthly, anchor five years ago, every overlay on", () => {
  assert.deepEqual(initialChartSettings(catalog, TODAY), {
    measure: "index",
    scale: "log",
    resolution: "monthly",
    anchor: "2021-10-07",
    overlays: { events: true, band: true, emphasis: true },
  });
});

test("fail closed: a series response without its envelope is an error, never a chart", () => {
  const good = seriesBody([{ key: "k", points: [["2020-01-31", "1"]] }]);
  assert.equal(interpretSeries({ ok: true, body: good }).kind, "ready");
  for (const mutate of [
    (b) => delete b.permissions,
    (b) => (b.permissions = null),
    (b) => (b.permissions = { can_read: "true" }),
    (b) => (b.permissions = { can_read: false }),
    (b) => delete b.series,
  ]) {
    const b = seriesBody([{ key: "k", points: [["2020-01-31", "1"]] }]);
    mutate(b);
    assert.deepEqual(interpretSeries({ ok: true, body: b }), { kind: "error", message: NO_ENVELOPE_MESSAGE });
  }
  assert.equal(interpretSeries({ ok: true, body: null }).kind, "error");
});

test("a refused series request shows the server's own message", () => {
  const refusal = { detail: { message: "More than 30000 points for a series; ask for a coarser frequency or a shorter window", over_cap_keys: ["k"] } };
  assert.deepEqual(interpretSeries({ ok: false, body: refusal }), { kind: "error", message: refusal.detail.message });
});

test("key dates fail closed the same way", () => {
  assert.equal(interpretKeyDates({ ok: true, body: keyDatesBody() }).kind, "ready");
  const b = keyDatesBody();
  delete b.permissions;
  assert.deepEqual(interpretKeyDates({ ok: true, body: b }), { kind: "error", message: KEY_DATES_UNAVAILABLE });
  const v = keyDatesBody();
  delete v.vocabularies;
  assert.equal(interpretKeyDates({ ok: true, body: v }).kind, "error");
});

test("one request for a burst of control changes, with the LAST url", async () => {
  const timers = fakeTimers();
  const calls = [];
  const results = [];
  const loader = createSeriesLoader({
    fetchJson: async (url) => {
      calls.push(url);
      return { ok: true, body: seriesBody([]) };
    },
    onResult: (r) => results.push(r),
    timers,
  });
  for (const f of ["daily", "weekly", "monthly", "quarterly", "monthly"]) loader.request(`${SERIES_ROUTE}?keys=a&frequency=${f}`);
  assert.equal(calls.length, 0, "nothing is sent inside the debounce window");
  assert.equal(timers.size, 1);
  timers.flush();
  await tick();
  assert.deepEqual(calls, [`${SERIES_ROUTE}?keys=a&frequency=monthly`]);
  assert.equal(results.length, 1);
  assert.equal(results[0].url, calls[0]);
  assert.ok(CHART_DEBOUNCE_MS > 0);
});

test("newest wins: an older response arriving last is discarded", async () => {
  const timers = fakeTimers();
  const resolvers = {};
  const results = [];
  const loader = createSeriesLoader({
    fetchJson: (url) => new Promise((resolve) => (resolvers[url] = resolve)),
    onResult: (r) => results.push(r.url),
    timers,
  });
  loader.request("/api/market/series?keys=old");
  timers.flush();
  loader.request("/api/market/series?keys=new");
  timers.flush();
  resolvers["/api/market/series?keys=new"]({ ok: true, body: seriesBody([]) });
  await tick();
  resolvers["/api/market/series?keys=old"]({ ok: true, body: seriesBody([]) });
  await tick();
  assert.deepEqual(results, ["/api/market/series?keys=new"]);
  // cancel() orphans anything in flight (selection cleared).
  loader.request("/api/market/series?keys=third");
  timers.flush();
  loader.cancel();
  resolvers["/api/market/series?keys=third"]({ ok: true, body: seriesBody([]) });
  await tick();
  assert.deepEqual(results, ["/api/market/series?keys=new"]);
});

test("anchor keyboard: arrows move one period, Page Up / Page Down twelve, clamped", () => {
  assert.equal(stepAnchorIndex(50, "ArrowRight", 100), 51);
  assert.equal(stepAnchorIndex(50, "ArrowUp", 100), 51);
  assert.equal(stepAnchorIndex(50, "ArrowLeft", 100), 49);
  assert.equal(stepAnchorIndex(50, "ArrowDown", 100), 49);
  assert.equal(stepAnchorIndex(50, "PageUp", 100), 62);
  assert.equal(stepAnchorIndex(50, "PageDown", 100), 38);
  assert.equal(stepAnchorIndex(95, "PageUp", 100), 99);
  assert.equal(stepAnchorIndex(5, "PageDown", 100), 0);
  assert.equal(stepAnchorIndex(5, "Home", 100), 0);
  assert.equal(stepAnchorIndex(5, "End", 100), 99);
  assert.equal(stepAnchorIndex(5, "a", 100), null);
  assert.equal(stepAnchorIndex(0, "ArrowRight", 0), null);
});

test("drag frames: many pointer moves between two frames recompute ONCE, with the latest position", () => {
  const frames = [];
  const seen = [];
  const s = createFrameScheduler((v) => seen.push(v), (fn) => frames.push(fn) && frames.length, () => {});
  for (let x = 0; x < 25; x += 1) s.schedule(x);
  assert.equal(frames.length, 1, "one frame requested for 25 pointer events");
  frames.shift()();
  assert.deepEqual(seen, [24]);
  s.schedule(30);
  s.schedule(31);
  frames.shift()();
  assert.deepEqual(seen, [24, 31]);
  s.schedule(40);
  s.cancel();
  assert.deepEqual(seen, [24, 31]);
});
