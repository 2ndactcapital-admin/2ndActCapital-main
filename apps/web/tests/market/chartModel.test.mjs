/**
 * The chart model (mkt04b Task 5): floating lines dashed, unavailable series
 * excluded with a notice, the axis title from the server's measure label, the
 * hovered line drawn last, highlighting fading ONLY in-pack lines, legend and
 * notice text from the server's vocabularies, the trends table.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { interpretCatalog } from "../../lib/market/catalogModel.mjs";
import { MEASURE_INDEX, MEASURE_SIGMA, SCALE_LINEAR, SCALE_LOG } from "../../lib/market/chartContract.mjs";
import {
  DIMMED_OPACITY,
  FADED_OPACITY,
  FLOATING_DASH,
  LINE_WIDTH,
  buildChartModel,
  hoverCard,
  prepareChartData,
  snapAnchorIndex,
} from "../../lib/market/chartModel.mjs";
import { interpretKeyDates } from "../../lib/market/chartRequest.mjs";
import { FLOATING_NOTE } from "../../lib/market/gridView.mjs";
import { catalogBody, keyDatesBody, monthly, seriesBody } from "./fixtures.mjs";

const body = catalogBody();
// One rate-like (default_transform level) indicator with units, for the raw value.
body.indicators.find((i) => i.series_key === "t.cat_gamma.0").default_transform = "level";
body.indicators.find((i) => i.series_key === "t.cat_gamma.0").units = "Percent";
const catalog = interpretCatalog({ ok: true, body }).catalog;
const MODES = catalog.vocabularies.modes;

const ramp = (start, step, n = 72) => Array.from({ length: n }, (_, i) => (start + step * i).toFixed(4));
const SEL = (keys) => keys.map((key) => ({ kind: "indicator", key }));
const settings = (over = {}) => ({
  anchor: "2021-06-30",
  measure: MEASURE_INDEX,
  scale: SCALE_LOG,
  resolution: "monthly",
  overlays: { events: true, band: true, emphasis: true },
  ...over,
});

/** Six series from Jan 2020: five in a tight pack and one far above it. */
function pack({ outlierKey = "t.cat_beta.5" } = {}) {
  const keys = ["t.cat_alpha.1", "t.cat_alpha.2", "t.cat_alpha.3", "t.cat_beta.1", "t.cat_beta.2", outlierKey];
  const entries = keys.map((key, i) => ({
    key,
    points: monthly(2020, 1, key === outlierKey ? ramp(100, 5) : ramp(100 + i, 0.1 + i * 0.01)),
  }));
  return { keys, response: seriesBody(entries) };
}

function model(keys, response, over = {}, ctx = {}) {
  const prepared = prepareChartData(response, SEL(keys), catalog, "monthly");
  return buildChartModel(prepared, settings(over), { modes: MODES, seriesVocab: response.vocabularies, ...ctx });
}

test("every line is measured from the snapped anchor: 100 at the anchor period", () => {
  const { keys, response } = pack();
  const m = model(keys, response);
  assert.equal(m.anchor.date, "2021-06-30");
  assert.equal(m.anchor.flag, "Anchor · Jun 2021");
  for (const key of keys) {
    const v = m.byKey.get(key).values[m.anchor.index];
    assert.ok(Math.abs(v - 100) < 1e-9, `${key} = ${v} at the anchor`);
  }
  // A mid-month anchor snaps back to the period on or before it.
  const prepared = prepareChartData(response, SEL(keys), catalog, "monthly");
  assert.equal(prepared.periods[snapAnchorIndex(prepared.periods, "2021-07-15")].date, "2021-06-30");
  assert.equal(snapAnchorIndex(prepared.periods, "1900-01-01"), 0);
});

test("a series starting after the anchor floats: dashed line and a notice", () => {
  const response = seriesBody([
    { key: "t.cat_alpha.1", points: monthly(2018, 1, ramp(100, 1)) },
    { key: "t.cat_alpha.2", points: monthly(2022, 1, ramp(50, 1, 30)) },
  ]);
  const m = model(["t.cat_alpha.1", "t.cat_alpha.2"], response, { anchor: "2019-06-30" });
  const late = m.lines.find((l) => l.key === "t.cat_alpha.2");
  const early = m.lines.find((l) => l.key === "t.cat_alpha.1");
  assert.equal(late.floating, true);
  assert.equal(late.dash, FLOATING_DASH);
  assert.equal(early.dash, null);
  const n = m.notices.find((x) => x.key === "floating");
  assert.equal(n.text, `Alpha group series 2: ${FLOATING_NOTE}`);
});

test("an unavailable series is excluded from the lines and named in a notice (server label when published)", () => {
  const response = seriesBody(
    [
      { key: "t.cat_alpha.1", points: monthly(2020, 1, ramp(100, 1)) },
      { key: "t.cat_alpha.2", points: monthly(2020, 1, ramp(-10, 0.1)) },
      { key: "t.cat_alpha.3", points: monthly(2020, 1, ramp(5, 0)), stddev: null },
    ],
    { vocabularies: { unavailable_reasons: [{ key: "non_positive_anchor", label: "Server reason label" }] } },
  );
  const keys = ["t.cat_alpha.1", "t.cat_alpha.2", "t.cat_alpha.3"];
  const idx = model(keys, response);
  assert.deepEqual(idx.lines.map((l) => l.key).sort(), ["t.cat_alpha.1", "t.cat_alpha.3"]);
  assert.ok(idx.notices.some((n) => n.text === "Server reason label: Alpha group series 2"));
  // Sigma: the negative series is fine, the constant one (stddev null) is not.
  const sig = model(keys, response, { measure: MEASURE_SIGMA });
  assert.deepEqual(sig.lines.map((l) => l.key).sort(), ["t.cat_alpha.1", "t.cat_alpha.2"]);
  assert.ok(sig.notices.some((n) => n.text === "zero variance: Alpha group series 3"), "code text when no label is published");
  assert.ok(!sig.table.some((r) => r.key === "t.cat_alpha.3"));
});

test("the axis title is the server's measure label plus the anchor and the scale", () => {
  const { keys, response } = pack();
  assert.equal(model(keys, response).axisTitle, "Mode label index (Jun 2021, log scale)");
  assert.equal(model(keys, response, { scale: SCALE_LINEAR }).axisTitle, "Mode label index (Jun 2021, linear scale)");
  const sig = model(keys, response, { measure: MEASURE_SIGMA, scale: SCALE_LOG });
  assert.equal(sig.axisTitle, "Mode label sigma (Jun 2021)");
  assert.equal(sig.log, false, "sigma is always linear");
  assert.ok(sig.yTicks.every((t) => t.label.endsWith("σ")));
});

test("the hovered line is drawn last, thicker, and every other line is dimmed", () => {
  const { keys, response } = pack();
  const hovered = "t.cat_alpha.2";
  const m = model(keys, response, {}, { hoverKey: hovered });
  assert.equal(m.lines[m.lines.length - 1].key, hovered);
  assert.equal(m.lines[m.lines.length - 1].width, LINE_WIDTH.hovered);
  assert.equal(m.lines[m.lines.length - 1].opacity, 1);
  for (const l of m.lines.slice(0, -1)) assert.ok(l.opacity <= DIMMED_OPACITY, `${l.key} not dimmed`);
  // Without hover, selection order is kept.
  assert.deepEqual(model(keys, response).lines.map((l) => l.key), keys);
});

test("highlighting fades ONLY the in-pack lines; the outlier stays full and thicker", () => {
  const { keys, response } = pack();
  const outlier = "t.cat_beta.5";
  const on = model(keys, response);
  const o = on.lines.find((l) => l.key === outlier);
  assert.equal(o.status, "above");
  assert.equal(o.width, LINE_WIDTH.outlier);
  assert.equal(o.opacity, 1);
  for (const l of on.lines.filter((x) => x.key !== outlier)) {
    assert.equal(l.status, "in");
    assert.equal(l.opacity, FADED_OPACITY, `${l.key} should be faded`);
    assert.equal(l.width, LINE_WIDTH.base);
  }
  const off = model(keys, response, { overlays: { events: true, band: true, emphasis: false } });
  assert.ok(off.lines.every((l) => l.opacity === 1 && l.width === LINE_WIDTH.base), "highlighting off: nothing fades");
  assert.equal(off.dots.length, 0, "big-move dots belong to highlighting");
});

test("under five series nothing is an outlier: status 'Too few series', no fading", () => {
  const { keys, response } = pack();
  const four = keys.slice(0, 3).concat("t.cat_beta.5");
  const m = model(four, response);
  assert.ok(m.lines.every((l) => l.opacity === 1));
  assert.ok(m.table.every((r) => r.statusText === "Too few series"));
});

test("securities draw at 2.2 px", () => {
  const response = seriesBody([{ key: "t.cat_alpha.0", points: monthly(2020, 1, ramp(100, 1)) }]);
  const prepared = prepareChartData(response, [{ kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000001" }], catalog, "monthly");
  const m = buildChartModel(prepared, settings(), { modes: MODES });
  assert.equal(m.lines[0].width, LINE_WIDTH.security);
  assert.equal(m.lines[0].label, "Linked Index One");
  assert.equal(m.table[0].group, "index", "a security's group is its type, as code text");
});

test("trends table: ordered by distance from the median, with the server's names and the pack status", () => {
  const { keys, response } = pack();
  const m = model(keys, response);
  assert.equal(m.table[0].key, "t.cat_beta.5");
  assert.equal(m.table[0].statusText, "Outlier above pack");
  assert.equal(m.table[0].label, "Beta group series 5");
  assert.equal(m.table[0].group, "Beta group");
  assert.match(m.table[0].since, /^\+\d+\.\d%$/);
  assert.match(m.table[0].twelve, /^↑ \+\d+\.\d%$/);
  assert.ok(m.table.slice(1).every((r) => r.statusText === "In pack"));
  for (let i = 1; i < m.table.length; i += 1) assert.ok(m.table[i - 1].distance >= m.table[i].distance);
  const sig = model(keys, response, { measure: MEASURE_SIGMA });
  assert.match(sig.table[0].since, /^\+\d+\.\d{2}σ$/);
});

test("overlays: band, regimes and key dates appear only when switched on; legend labels come from the server", () => {
  const { keys, response } = pack();
  const kd = interpretKeyDates({ ok: true, body: keyDatesBody() });
  const on = model(keys, response, {}, { keyDates: kd });
  assert.ok(on.band && on.band.d.startsWith("M") && on.band.median.startsWith("M"));
  assert.deepEqual(on.regimes.map((r) => r.label), ["Regime A 2020", "Regime B 2022"]);
  assert.deepEqual(on.keyDateLines.map((k) => k.label), ["Key date one"], "a key date outside the data is not drawn");
  const legend = on.legend.map((l) => l.label);
  assert.ok(legend.includes("Regime type label A") && legend.includes("Regime type label B"));
  assert.ok(on.dots.length > 0 && on.dots.every((d) => keys.includes(d.series)), "big-move dots are drawn with highlighting on");

  const off = model(keys, response, { overlays: { events: false, band: false, emphasis: false } }, { keyDates: kd });
  assert.equal(off.band, null);
  assert.equal(off.regimes.length, 0);
  assert.equal(off.keyDateLines.length, 0);
  assert.equal(off.legend.length, 0);

  // A key-dates response without its envelope draws no events (fail closed).
  const lost = keyDatesBody();
  delete lost.permissions;
  const closed = model(keys, response, {}, { keyDates: interpretKeyDates({ ok: true, body: lost }) });
  assert.equal(closed.regimes.length, 0);
  assert.equal(closed.keyDateLines.length, 0);
});

test("rate-like warning text and the hover card's raw value use the server's strings", () => {
  const response = seriesBody(
    [{ key: "t.cat_gamma.0", points: monthly(2020, 1, ["4.10", "4.25", "4.40", "4.55", "4.70", "4.85", "5.00", "5.15"]) }],
    { vocabularies: { warnings: [{ key: "rate_like_series_indexed", label: "Server warning label" }] } },
  );
  const prepared = prepareChartData(response, SEL(["t.cat_gamma.0"]), catalog, "monthly");
  const m = buildChartModel(prepared, settings({ anchor: "2020-01-31" }), { modes: MODES, seriesVocab: response.vocabularies });
  assert.ok(m.notices.some((n) => n.text === "Server warning label: Gamma group series 0"));
  const card = hoverCard(m, { key: "t.cat_gamma.0", index: 2, x: 0, y: 0 });
  assert.equal(card.date, "2020-03-31");
  assert.equal(card.valueText, "Index 107.3 · +7.3% vs anchor");
  assert.equal(card.rawText, "4.40 Percent", "the server's own string, unparsed, with the catalog's units");
  const sig = buildChartModel(prepared, settings({ anchor: "2020-01-31", measure: MEASURE_SIGMA }), { modes: MODES });
  assert.equal(hoverCard(sig, { key: "t.cat_gamma.0", index: 2, x: 0, y: 0 }).valueText, "+0.03σ vs anchor");
});

test("an empty response or a non-chart measure gives an empty model", () => {
  const prepared = prepareChartData(seriesBody([]), [], catalog, "monthly");
  assert.equal(buildChartModel(prepared, settings(), { modes: MODES }).empty, true);
  const { keys, response } = pack();
  const p2 = prepareChartData(response, SEL(keys), catalog, "monthly");
  assert.equal(buildChartModel(p2, settings({ measure: "level" }), { modes: MODES }).empty, true);
});

test("a series is not carried past its own last observation", () => {
  const response = seriesBody([
    { key: "t.cat_alpha.1", points: monthly(2020, 1, ramp(100, 1, 72)) },
    { key: "t.cat_alpha.2", points: monthly(2020, 1, ramp(100, 1, 24)) },
  ]);
  const m = model(["t.cat_alpha.1", "t.cat_alpha.2"], response, { anchor: "2020-06-30" });
  const short = m.byKey.get("t.cat_alpha.2").values;
  assert.notEqual(short[23], null);
  assert.equal(short[24], null);
  assert.equal(short[short.length - 1], null);
});
