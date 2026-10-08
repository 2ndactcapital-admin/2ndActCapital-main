/**
 * lib/market/correlationModel.mjs (mkt04c Task 2, 5b): the request excludes
 * the focus and respects the server's lag range and key limit; nothing is sent
 * with fewer than two series; one request per burst and the newest response
 * wins; an envelope-less response is an error; rows keep the server's order,
 * show r exactly as returned, and size the bar from the r TEXT.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { interpretCatalog, NO_ENVELOPE_MESSAGE } from "../../lib/market/catalogModel.mjs";
import {
  CORRELATIONS_ROUTE,
  SIGN_NEGATIVE,
  SIGN_POSITIVE,
  buildCorrelationRequest,
  chooseFocus,
  clampLag,
  correlationRows,
  correlationSeries,
  createCorrelationLoader,
  interpretCorrelations,
  lagOptions,
  lagRange,
  rBarWidth,
  rSign,
} from "../../lib/market/correlationModel.mjs";
import { catalogBody } from "./fixtures.mjs";
import { correlationsBody } from "./mkt04cFixtures.mjs";

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

const body = catalogBody();
body.vocabularies.limits.lag_months = [-24, 24];
const catalog = interpretCatalog({ ok: true, body }).catalog;
const ind = (key) => ({ kind: "indicator", key });
const SEL = [ind("t.cat_alpha.0"), ind("t.cat_alpha.1"), ind("t.cat_beta.2")];

test("focus and candidates come from the selection; a security sharing a series counts once", () => {
  const withSec = [...SEL, { kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000001" }]; // linked to t.cat_alpha.0
  const s = correlationSeries(withSec, catalog);
  assert.deepEqual(s.map((x) => x.key), ["t.cat_alpha.0", "t.cat_alpha.1", "t.cat_beta.2"]);
  assert.equal(s[0].label, "Alpha group series 0", "names are the catalog's");
  assert.equal(chooseFocus("t.cat_beta.2", s), "t.cat_beta.2");
  assert.equal(chooseFocus("gone", s), "t.cat_alpha.0", "a focus no longer selected falls back to the first");
  assert.equal(chooseFocus(null, []), null);
});

test("the request: focus excluded from keys, window from the anchor (and end only when set), lag clamped", () => {
  const r = buildCorrelationRequest({ selection: SEL, catalog, focus: "t.cat_alpha.1", lag: 6, anchor: "2021-06-30", end: null });
  assert.equal(r.url, CORRELATIONS_ROUTE);
  assert.equal(CORRELATIONS_ROUTE, "/api/market/correlations");
  assert.deepEqual(r.body, { focus_key: "t.cat_alpha.1", keys: ["t.cat_alpha.0", "t.cat_beta.2"], anchor: "2021-06-30", lag_months: 6 });
  assert.ok(!r.body.keys.includes(r.body.focus_key));
  assert.deepEqual(Object.keys(r.body).sort(), ["anchor", "focus_key", "keys", "lag_months"], "min_periods left to the server's default");
  const withEnd = buildCorrelationRequest({ selection: SEL, catalog, focus: null, lag: 0, anchor: "2021-06-30", end: "2024-12-31" });
  assert.equal(withEnd.body.end, "2024-12-31");
  assert.equal(withEnd.body.focus_key, "t.cat_alpha.0");
  assert.equal(buildCorrelationRequest({ selection: SEL, catalog, focus: null, lag: 99, anchor: "2021-06-30" }).body.lag_months, 24);
  assert.equal(buildCorrelationRequest({ selection: SEL, catalog, focus: null, lag: -99, anchor: "2021-06-30" }).body.lag_months, -24);
  assert.ok(!/org_id|user_id/.test(JSON.stringify(r)));
});

test("the lag range is the server's; without one the lag is 0 and the control is a single option", () => {
  assert.deepEqual(lagRange(catalog), [-24, 24]);
  assert.equal(lagOptions(lagRange(catalog)).length, 49);
  assert.deepEqual(lagOptions([-2, 2]).map((o) => o.label), ["-2", "-1", "0", "+1", "+2"]);
  const plain = interpretCatalog({ ok: true, body: catalogBody() }).catalog; // fixture without lag_months
  assert.equal(lagRange(plain), null);
  assert.equal(clampLag(12, null), 0);
  assert.deepEqual(lagOptions(null), [{ value: 0, label: "0" }]);
  assert.equal(clampLag(3.7, [-24, 24]), 3);
  assert.equal(clampLag(Number.NaN, [-24, 24]), 0);
});

test("at most the server's key limit is sent; nothing is sent with fewer than two series", () => {
  const b = catalogBody({ indicatorsPerCategory: 15 });
  b.vocabularies.limits.max_keys = 3;
  const small = interpretCatalog({ ok: true, body: b }).catalog;
  const many = ["t.cat_alpha.0", "t.cat_alpha.1", "t.cat_alpha.2", "t.cat_alpha.3", "t.cat_alpha.4"].map(ind);
  const r = buildCorrelationRequest({ selection: many, catalog: small, focus: "t.cat_alpha.0", lag: 0, anchor: "2021-06-30" });
  assert.deepEqual(r.body.keys, ["t.cat_alpha.1", "t.cat_alpha.2", "t.cat_alpha.3"], "limit 3 from vocabularies.limits.max_keys");
  assert.equal(buildCorrelationRequest({ selection: [ind("t.cat_alpha.0")], catalog, focus: null, lag: 0, anchor: "2021-06-30" }), null);
  assert.equal(buildCorrelationRequest({ selection: [], catalog, focus: null, lag: 0, anchor: "2021-06-30" }), null);
  assert.equal(buildCorrelationRequest({ selection: SEL, catalog, focus: null, lag: 0, anchor: "" }), null, "no anchor, no request");
  const noLimit = catalogBody();
  delete noLimit.vocabularies.limits;
  assert.equal(
    buildCorrelationRequest({ selection: SEL, catalog: interpretCatalog({ ok: true, body: noLimit }).catalog, focus: null, lag: 0, anchor: "2021-06-30" }),
    null,
    "no published key limit -> nothing is sent (fail closed)",
  );
});

test("one request per burst: dragging the anchor sends once, after it stops", async () => {
  const timers = fakeTimers();
  const calls = [];
  const results = [];
  const loader = createCorrelationLoader({
    fetchJson: async (url, b) => {
      calls.push([url, b.anchor]);
      return { ok: true, body: correlationsBody(b.focus_key, []) };
    },
    onResult: (r) => results.push(r),
    timers,
  });
  for (const anchor of ["2021-01-31", "2021-02-28", "2021-03-31", "2021-04-30"]) {
    loader.request(buildCorrelationRequest({ selection: SEL, catalog, focus: null, lag: 0, anchor }));
  }
  assert.equal(calls.length, 0, "nothing is sent while the anchor is moving");
  assert.equal(timers.size, 1);
  timers.flush();
  await tick();
  assert.deepEqual(calls, [[CORRELATIONS_ROUTE, "2021-04-30"]], "exactly one request, for where the anchor stopped");
  assert.equal(results.length, 1);
  assert.equal(results[0].kind, "ready");
});

test("stale responses are discarded: the newest wins, and a cancelled request never renders", async () => {
  const timers = fakeTimers();
  const resolvers = {};
  const results = [];
  const loader = createCorrelationLoader({
    fetchJson: (url, b) => new Promise((res) => (resolvers[b.anchor] = res)),
    onResult: (r) => results.push(r),
    timers,
  });
  const req = (anchor) => buildCorrelationRequest({ selection: SEL, catalog, focus: null, lag: 0, anchor });
  loader.request(req("2020-01-31"));
  timers.flush();
  loader.request(req("2021-01-31"));
  timers.flush();
  resolvers["2021-01-31"]({ ok: true, body: correlationsBody("t.cat_alpha.0", [["t.cat_alpha.1", "0.5000", 30, "monthly"]]) });
  await tick();
  resolvers["2020-01-31"]({ ok: true, body: correlationsBody("t.cat_alpha.0", [["t.cat_alpha.1", "0.9999", 30, "monthly"]]) });
  await tick();
  assert.equal(results.length, 1, "the older response arrived last and was dropped");
  assert.equal(results[0].key, req("2021-01-31").key);
  assert.equal(results[0].response.results[0].r, "0.5000");
  loader.request(req("2022-01-31"));
  timers.flush();
  loader.cancel();
  resolvers["2022-01-31"]({ ok: true, body: correlationsBody("t.cat_alpha.0", []) });
  await tick();
  assert.equal(results.length, 1, "a cancelled request never renders");
});

test("fail closed: an envelope-less or list-less response is an error with no results", () => {
  for (const mutate of [(b) => delete b.permissions, (b) => (b.permissions = { can_read: false }), (b) => delete b.results]) {
    const b = correlationsBody("x", [["y", "0.1000", 30, "monthly"]]);
    mutate(b);
    assert.deepEqual(interpretCorrelations({ ok: true, body: b }), { kind: "error", message: NO_ENVELOPE_MESSAGE });
  }
  assert.deepEqual(interpretCorrelations({ ok: false, body: { detail: { message: "Server refusal" } } }), { kind: "error", message: "Server refusal" });
});

test("the bar width is the r TEXT read as a percentage — never a parsed number", () => {
  const known = {
    "0.8312": "83.12%",
    "-0.8312": "83.12%",
    "-0.0450": "4.50%",
    "0.0000": "0.00%",
    "0.0500": "5.00%",
    "0.5": "50%",
    "1.0000": "100%",
    "-1.0000": "100%",
  };
  for (const [r, w] of Object.entries(known)) assert.equal(rBarWidth(r), w, r);
  for (const junk of [null, undefined, "", "abc", "0.12e3", "2.0000", "NaN"]) assert.equal(rBarWidth(junk), "0%", String(junk));
  assert.equal(rSign("-0.0450"), SIGN_NEGATIVE);
  assert.equal(rSign("0.0000"), SIGN_POSITIVE);
  assert.equal(rSign(null), null);
});

test("rows: the server's order, r exactly as returned, n and frequency label, a null r with its reason", () => {
  const series = correlationSeries(SEL, catalog);
  const resp = correlationsBody("t.cat_alpha.0", [
    ["t.cat_beta.2", "-0.8312", 61, "monthly"],
    ["t.cat_alpha.1", "0.1200", 61, "quarterly"],
    ["t.unknown", null, 7, "monthly", "insufficient_overlap"],
  ]);
  const rows = correlationRows(resp, { series, frequencies: catalog.vocabularies.frequencies });
  assert.deepEqual(rows.map((r) => r.key), ["t.cat_beta.2", "t.cat_alpha.1", "t.unknown"], "server order kept");
  assert.deepEqual(rows[0], {
    key: "t.cat_beta.2", name: "Beta group series 2", r: "-0.8312", width: "83.12%", sign: SIGN_NEGATIVE,
    n: 61, frequency: "Res label monthly", reason: null,
  });
  assert.equal(rows[1].frequency, "Res label quarterly", "the frequency label is the catalog's");
  assert.equal(rows[2].r, null);
  assert.equal(rows[2].width, "0%");
  assert.equal(rows[2].reason, "insufficient overlap", "the server publishes no reason labels: its code as text");
  assert.equal(rows[2].name, "t.unknown", "a key the selection does not name shows as itself");
});
