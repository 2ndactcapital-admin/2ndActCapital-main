/**
 * Known answers for the chart's geometry (mkt04b Task 5): scales and ticks,
 * hit-testing (the 18-pixel rule and the display-scale correction), the pack
 * overlays (band quantiles, fences, big moves) and end-label layout.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { HIT_RADIUS, displayScale, hitTest, nearestIndex, toLogical } from "../../lib/market/hitTest.mjs";
import { LABEL_GAP, layoutLabels } from "../../lib/market/labelLayout.mjs";
import {
  STATUS_ABOVE,
  STATUS_BELOW,
  STATUS_FEW,
  STATUS_IN,
  bigMoves,
  cohortBand,
  direction,
  packFences,
  packStatus,
  quantile,
} from "../../lib/market/overlays.mjs";
import {
  AXIS_LINEAR_INDEX,
  AXIS_LOG_INDEX,
  AXIS_SIGMA,
  buildPeriods,
  formatNumber,
  isoToMs,
  linearTicks,
  logLadder,
  logTicks,
  makeXScale,
  makeYScale,
  monthLabel,
  periodEnd,
  signed,
  timeTicks,
  yDomain,
} from "../../lib/market/scales.mjs";

const close = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

// ── Scales ──────────────────────────────────────────────────────────────────

test("y range: 6% padding on each side, in axis space", () => {
  const [lo, hi] = yDomain([100, 200], AXIS_LINEAR_INDEX);
  close(lo, 94);
  close(hi, 206);
  const [s0, s1] = yDomain([-1, 1], AXIS_SIGMA);
  close(s0, -1.12);
  close(s1, 1.12);
  // Log: padding is 6% of the span in log10 space (one decade -> 0.06 decade).
  const [l0, l1] = yDomain([100, 1000], AXIS_LOG_INDEX);
  close(Math.log10(l0), 2 - 0.06);
  close(Math.log10(l1), 3 + 0.06);
});

test("y range clamps: log index 1..10,000, sigma +-6, linear index wider", () => {
  const [l0, l1] = yDomain([0.01, 1e9], AXIS_LOG_INDEX);
  close(l0, 1);
  close(l1, 10000);
  const [s0, s1] = yDomain([-50, 50], AXIS_SIGMA);
  assert.deepEqual([s0, s1], [-6, 6]);
  const [i0, i1] = yDomain([-5000, 500000], AXIS_LINEAR_INDEX);
  assert.deepEqual([i0, i1], [-1000, 100000]);
  assert.ok(i1 > l1, "linear index allows more than the log clamp");
  // Non-positive values are invisible on a log axis and do not stretch it.
  const [p0] = yDomain([-5, 0, 100, 110], AXIS_LOG_INDEX);
  assert.ok(p0 > 90);
});

test("log ticks come from the 1-2-5 ladder and are pruned to at most eight", () => {
  assert.deepEqual(logLadder(1, 100), [1, 2, 5, 10, 20, 50, 100]);
  assert.deepEqual(logTicks(1, 100), [1, 2, 5, 10, 20, 50, 100]);
  // 1..10,000 has 13 ladder values: the 2s go first (9), then the 5s (5).
  assert.deepEqual(logTicks(1, 10000), [1, 10, 100, 1000, 10000]);
  assert.deepEqual(logTicks(2, 600), [2, 5, 10, 20, 50, 100, 200, 500]);
  for (const [lo, hi] of [[1, 10000], [0.5, 9000], [3, 7], [95, 130]]) {
    const t = logTicks(lo, hi);
    assert.ok(t.length <= 8 && t.length >= 1, `${lo}..${hi} gave ${t.length}`);
    for (const v of t) assert.ok(v >= lo && v <= hi);
  }
  // Too narrow for two ladder values: linear ticks inside the range.
  assert.deepEqual(logTicks(95, 130), [95, 100, 105, 110, 115, 120, 125, 130]);
});

test("linear ticks use the smallest 1-2-5 step giving at most eight", () => {
  assert.deepEqual(linearTicks(0, 10), [0, 2, 4, 6, 8, 10]);
  assert.deepEqual(linearTicks(-6, 6), [-6, -4, -2, 0, 2, 4, 6]);
  assert.deepEqual(linearTicks(94, 206), [100, 120, 140, 160, 180, 200]);
  assert.deepEqual(linearTicks(0.1, 0.35), [0.1, 0.15, 0.2, 0.25, 0.3, 0.35]);
});

test("time ticks: every 5 years over long spans, finer when short", () => {
  const long = timeTicks(isoToMs("1971-03-31"), isoToMs("2026-09-30")).map((t) => t.label);
  assert.equal(long[0], "1975");
  assert.equal(long[long.length - 1], "2025");
  assert.ok(long.every((y) => Number(y) % 5 === 0));
  const mid = timeTicks(isoToMs("2016-01-31"), isoToMs("2026-09-30")).map((t) => t.label);
  assert.deepEqual(mid, ["2018", "2020", "2022", "2024", "2026"]);
  const short = timeTicks(isoToMs("2022-01-31"), isoToMs("2026-09-30")).map((t) => t.label);
  assert.deepEqual(short, ["2023", "2024", "2025", "2026"]);
  const tiny = timeTicks(isoToMs("2025-12-31"), isoToMs("2026-09-30")).map((t) => t.label);
  assert.deepEqual(tiny, ["Jan 2026", "Apr 2026", "Jul 2026"]);
});

test("pixel mapping round-trips both ways (linear and log)", () => {
  const y = makeYScale([1, 10000], true, 10, 410);
  close(y.toPx(10000), 10);
  close(y.toPx(1), 410);
  close(y.toPx(100), 210);
  close(y.fromPx(210), 100, 1e-9);
  assert.equal(y.toPx(-1), null, "a non-positive value has no log pixel");
  const x = makeXScale(isoToMs("2000-01-01"), isoToMs("2020-01-01"), 70, 790);
  close(x.fromPx(x.toPx(isoToMs("2010-06-15"))), isoToMs("2010-06-15"), 1e-3);
});

test("periods bucket by period end and never run past the latest observation", () => {
  assert.equal(periodEnd("2024-02-10", "monthly"), "2024-02-29");
  assert.equal(periodEnd("2024-05-02", "quarterly"), "2024-06-30");
  assert.equal(periodEnd("2020-12-28", "weekly"), "2021-01-03", "ISO week: Monday..Sunday");
  assert.equal(periodEnd("2021-01-03", "weekly"), "2021-01-03");
  const a = { dates: ["2026-08-31", "2026-09-30", "2026-10-06"] };
  const b = { dates: ["2026-09-01", "2026-10-01"] };
  const p = buildPeriods([a, b], "monthly").map((x) => x.date);
  assert.deepEqual(p, ["2026-08-31", "2026-09-30", "2026-10-06"]);
  assert.deepEqual(buildPeriods([a, b], "native").map((x) => x.date), ["2026-08-31", "2026-09-01", "2026-09-30", "2026-10-01", "2026-10-06"]);
});

test("number text: grouping, signs, month labels", () => {
  assert.equal(formatNumber(12345.678, 1), "12,345.7");
  assert.equal(formatNumber(-0.04, 1), "0.0");
  assert.equal(signed(18.44, 1), "+18.4");
  assert.equal(signed(-3, 2), "-3.00");
  assert.equal(signed(0, 1), "0.0");
  assert.equal(monthLabel("2021-09-30"), "Sep 2021");
});

// ── Hit-testing ─────────────────────────────────────────────────────────────

const XS = [100, 120, 140, 160];
const LINES = [
  { key: "a", ys: [50, 60, 70, 80] },
  { key: "b", ys: [90, 100, null, 120] },
];

test("hitTest: nearest line point at the nearest period, within 18 px", () => {
  assert.equal(HIT_RADIUS, 18);
  assert.equal(nearestIndex(XS, 129), 1);
  assert.equal(nearestIndex(XS, 131), 2);
  assert.deepEqual(hitTest({ x: 121, y: 64 }, XS, LINES), { key: "a", index: 1, x: 120, y: 60, distance: Math.hypot(1, 4) });
  assert.equal(hitTest({ x: 121, y: 96 }, XS, LINES).key, "b");
  // Index 2: b has no value there, so only a can be hit.
  assert.equal(hitTest({ x: 140, y: 80 }, XS, LINES).key, "a");
});

test("hitTest: the 18-pixel threshold is inclusive at 18 and refuses 18.01", () => {
  assert.equal(hitTest({ x: 100, y: 68 }, XS, LINES).distance, 18);
  assert.equal(hitTest({ x: 100, y: 68.01 }, XS, [LINES[0]]), null);
  assert.equal(hitTest({ x: 100, y: 0 }, XS, LINES), null);
  assert.equal(hitTest({ x: 0, y: 0 }, [], LINES), null);
});

test("display-scale correction: a pointer on a half-size rendering maps back to logical pixels", () => {
  assert.equal(displayScale(480, 960), 0.5);
  assert.equal(displayScale(0, 960), 1);
  const rect = { left: 10, top: 20, width: 480, height: 220 };
  // Logical (120, 60) is drawn at rendered (10 + 60, 20 + 30).
  const p = toLogical(70, 50, rect, 960, 440);
  assert.deepEqual(p, { x: 120, y: 60 });
  assert.equal(hitTest(p, XS, LINES).key, "a");
  // Without the correction the same pointer misses by 60 logical px.
  assert.equal(hitTest({ x: 70 - 10, y: 50 - 20 }, XS, LINES), null);
  // 8 rendered px at scale 0.5 is 16 logical px: still a hit. 10 rendered px is 20: a miss.
  assert.ok(hitTest(toLogical(70, 50 + 8, rect, 960, 440), XS, [LINES[0]]));
  assert.equal(hitTest(toLogical(70, 50 + 10, rect, 960, 440), XS, [LINES[0]]), null);
});

// ── Overlays ────────────────────────────────────────────────────────────────

test("quantiles (type 7) and the cohort band need three series per period", () => {
  assert.equal(quantile([1, 2, 3, 4], 0.25), 1.75);
  assert.equal(quantile([1, 2, 3, 4], 0.5), 2.5);
  assert.equal(quantile([1, 2, 3, 4], 0.75), 3.25);
  const band = cohortBand([
    [1, 1, null],
    [2, 5, 7],
    [3, null, 8],
  ]);
  assert.deepEqual(band[0], { q25: 1.5, q50: 2, q75: 2.5, n: 3 });
  assert.equal(band[1], null, "two series at period 1");
  assert.equal(band[2], null, "two series at period 2");
});

test("pack fences: q25/q75 with a 0.75 * IQR margin and a floor of 4% of the axis span", () => {
  const latest = [1, 2, 3, 4, 5, 100];
  const f = packFences(latest, 10);
  // q25 = 2.25, q75 = 4.75, IQR = 2.5, margin = max(1.875, 0.4) = 1.875
  close(f.q25, 2.25);
  close(f.q75, 4.75);
  close(f.margin, 1.875);
  close(f.lo, 0.375);
  close(f.hi, 6.625);
  assert.equal(packStatus(100, f), STATUS_ABOVE);
  assert.equal(packStatus(0.3, f), STATUS_BELOW);
  assert.equal(packStatus(6.6, f), STATUS_IN);
  // A tight pack: the IQR margin is tiny, so the 4% floor decides.
  const tight = packFences([10, 10, 10.01, 10.01, 10.02], 50);
  close(tight.margin, 2);
  assert.equal(packStatus(11.9, tight), STATUS_IN);
  assert.equal(packStatus(12.1, tight), STATUS_ABOVE);
  // Under five series: no fences, every status is "few".
  assert.equal(packFences([1, 2, 3, 4], 10), null);
  assert.equal(packStatus(1, null), STATUS_FEW);
});

test("big moves: |change| / own sd above the threshold, top three, largest first", () => {
  const base = Array.from({ length: 60 }, (_, i) => (i % 2 ? 0.1 : -0.1));
  const vals = [];
  let level = 0;
  base.forEach((c, i) => {
    level += c;
    if (i === 10) level += 5;
    if (i === 20) level -= 6;
    if (i === 30) level += 4;
    if (i === 40) level += 3.5;
    vals.push(level);
  });
  const moves = bigMoves(vals, 3, 3);
  assert.deepEqual(moves.map((m) => m.index), [20, 10, 30]);
  assert.ok(moves.every((m) => m.z > 3));
  assert.deepEqual(bigMoves([1, 1, 1, 1]), [], "no variance, no moves");
  assert.deepEqual(bigMoves(Array.from({ length: 30 }, (_, i) => (i % 2 ? 1 : -1))), [], "uniform moves are not big");
});

test("12-month direction", () => {
  assert.deepEqual(direction(5, 3), { change: 2, direction: "up" });
  assert.equal(direction(1, 3).direction, "down");
  assert.equal(direction(3, 3).direction, "flat");
  assert.equal(direction(null, 3), null);
});

// ── Labels ──────────────────────────────────────────────────────────────────

test("end labels: at least 14 px apart, inside the plot, order preserved", () => {
  assert.equal(LABEL_GAP, 14);
  const out = layoutLabels(
    [
      { key: "c", y: 105 },
      { key: "a", y: 100 },
      { key: "b", y: 101 },
      { key: "d", y: 300 },
    ],
    { top: 10, bottom: 310 },
  );
  assert.deepEqual(out.map((l) => l.key), ["a", "b", "c", "d"]);
  assert.deepEqual(out.map((l) => l.y), [100, 114, 128, 300]);
  // Crowded at the bottom: pushed back up inside the plot, still 14 apart.
  const bottom = layoutLabels(
    [
      { key: "x", y: 305 },
      { key: "y", y: 306 },
      { key: "z", y: 330 },
    ],
    { top: 10, bottom: 310 },
  );
  assert.deepEqual(bottom.map((l) => l.y), [282, 296, 310]);
  assert.deepEqual(bottom.map((l) => l.key), ["x", "y", "z"]);
  for (let i = 1; i < bottom.length; i += 1) assert.ok(bottom[i].y - bottom[i - 1].y >= 14);
  // Too many to fit: spread evenly, order kept, all inside.
  const many = layoutLabels(Array.from({ length: 30 }, (_, i) => ({ key: `k${i}`, y: 100 + i })), { top: 0, bottom: 290 });
  assert.equal(many[0].y, 0);
  assert.equal(many[29].y, 290);
  assert.deepEqual(many.map((l) => l.key), Array.from({ length: 30 }, (_, i) => `k${i}`));
});
