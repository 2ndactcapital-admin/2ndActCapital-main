/**
 * Browser rebasing against the server's own transforms (mkt04b Task 5).
 * golden_rebase.json is written by apps/api/scripts/gen_mkt04b_golden.py from
 * the PRODUCTION Python transforms; every browser value must match it to
 * within 5e-7 (the server's 6-decimal rounding), with identical floating
 * flags, unavailable reasons and warnings.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { MEASURE_INDEX, MEASURE_SIGMA } from "../../lib/market/chartContract.mjs";
import { anchorOf, asOf, asOfIndex, measureValue, parseSeries, rebaseAt, rebaseSpec } from "../../lib/market/rebase.mjs";

const golden = JSON.parse(readFileSync(new URL("./golden_rebase.json", import.meta.url), "utf8"));
const TOL = 5e-7;
// Float noise on top of the server's rounding: a few ulps of the value.
const tol = (expected) => TOL + 8 * Number.EPSILON * Math.abs(expected);

test("the golden file covers every case the sprint names", () => {
  const names = golden.cases.map((c) => c.name);
  for (const n of [
    "anchor_on_observation",
    "anchor_between_observations",
    "anchor_before_first_observation",
    "non_positive_anchor_negative",
    "zero_variance",
    "rate_like_level_series",
  ]) {
    assert.ok(names.includes(n), `missing golden case ${n}`);
  }
  const measures = new Set(golden.cases.flatMap((c) => Object.keys(c.measures)));
  assert.deepEqual([...measures].sort(), [MEASURE_INDEX, MEASURE_SIGMA].sort());
});

for (const c of golden.cases) {
  for (const [measure, exp] of Object.entries(c.measures)) {
    test(`golden ${c.name} / ${measure}: values within 5e-7, same flags`, () => {
      const series = parseSeries(c.series);
      const spec = rebaseSpec(series, { measure, anchor: c.anchor, defaultTransform: c.default_transform });
      assert.equal(spec.floating, exp.floating, "floating");
      assert.equal(spec.unavailable, exp.unavailable_reason, "unavailable reason");
      assert.deepEqual(spec.warnings, exp.warnings, "warnings");
      assert.equal(spec.anchorDate, exp.anchor_observation_date, "anchor observation date");
      let compared = 0;
      for (const [d, want] of Object.entries(exp.values)) {
        const got = rebaseAt(series, spec, d);
        if (want === null) {
          assert.equal(got, null, `${d}: expected null, got ${got}`);
          continue;
        }
        const w = Number(want);
        assert.ok(Math.abs(got - w) <= tol(w), `${d}: browser ${got} vs server ${want}`);
        compared += 1;
      }
      if (exp.unavailable_reason === null) assert.ok(compared > 0, "the case compared real values");
    });
  }
}

test("sd comes from the server's field, never from the points", () => {
  const s = parseSeries({ series_key: "x", stddev: "2.000000", points: [["2020-01-31", "1"], ["2020-02-29", "9"]] });
  const spec = rebaseSpec(s, { measure: MEASURE_SIGMA, anchor: "2020-01-31", defaultTransform: null });
  // The sample sd of [1, 9] is 5.657; the server said 2.
  assert.equal(spec.sd, 2);
  assert.equal(rebaseAt(s, spec, "2020-02-29"), 4);
  const none = parseSeries({ series_key: "x", stddev: null, points: [["2020-01-31", "1"], ["2020-02-29", "9"]] });
  assert.equal(rebaseSpec(none, { measure: MEASURE_SIGMA, anchor: "2020-01-31" }).unavailable, "zero_variance");
});

test("as-of: last observation on or before the date; none before the first", () => {
  const s = parseSeries({ points: [["2020-01-31", "1"], ["2020-03-31", "3"]] });
  assert.equal(asOfIndex(s.dates, "2020-01-30"), -1);
  assert.equal(asOf(s, "2020-01-30"), null);
  assert.equal(asOf(s, "2020-01-31"), 1);
  assert.equal(asOf(s, "2020-02-29"), 1);
  assert.equal(asOf(s, "2099-01-01"), 3);
  assert.deepEqual(anchorOf(s, "2019-01-01"), { value: 1, date: "2020-01-31", floating: true });
});

test("floating also follows the whole-history first observation, as on the server", () => {
  // The loaded points start later than the series' real history: not floating.
  const s = parseSeries({ first_observation_date: "1990-01-01", points: [["2020-01-31", "1"]] });
  assert.equal(anchorOf(s, "2019-12-31").floating, true, "no as-of point in the loaded window");
  assert.equal(anchorOf(s, "2020-02-01").floating, false);
  const late = parseSeries({ first_observation_date: "2021-01-01", points: [["2021-01-01", "1"]] });
  assert.equal(anchorOf(late, "2020-06-30").floating, true);
});

test("an unavailable spec measures nothing; only the two chart measures exist", () => {
  const s = parseSeries({ stddev: "1", points: [["2020-01-31", "-2"]] });
  const spec = rebaseSpec(s, { measure: MEASURE_INDEX, anchor: "2020-01-31" });
  assert.equal(spec.unavailable, "non_positive_anchor");
  assert.equal(measureValue(spec, 5), null);
  assert.throws(() => rebaseSpec(s, { measure: "level", anchor: "2020-01-31" }));
  const empty = parseSeries({ points: [] });
  assert.equal(rebaseSpec(empty, { measure: MEASURE_INDEX, anchor: "2020-01-31" }).unavailable, "no_observations");
});

test("parseSeries keeps the server's raw strings beside the floats and drops junk points", () => {
  const s = parseSeries({ points: [["2020-01-31", "4.123456789"], ["2020-02-29", "abc"], ["bad"], ["2020-03-31", "0.000000123"]] });
  assert.deepEqual(s.dates, ["2020-01-31", "2020-03-31"]);
  assert.deepEqual(s.raw, ["4.123456789", "0.000000123"]);
  assert.equal(s.values[0], 4.123456789);
});
