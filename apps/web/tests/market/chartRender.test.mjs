/**
 * Renders the REAL chart components (mkt04b Task 5) with react-dom/server:
 *   - the chart renders for a normal envelope;
 *   - an envelope-less series response renders the error state and NO chart;
 *   - the anchor input's keyboard handler moves one period, Page Up/Down twelve;
 *   - each overlay toggle shows and hides its elements.
 * JSX is compiled by ./jsxLoader.mjs; the components are imported unchanged.
 */
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: ChartView } = await import("../../components/market/ChartView.jsx");
const { default: AnchorSlider } = await import("../../components/market/AnchorSlider.jsx");
const { default: MarketIndicatorsView } = await import("../../components/market/MarketIndicatorsView.jsx");
const { interpretCatalog, NO_ENVELOPE_MESSAGE } = await import("../../lib/market/catalogModel.mjs");
const { buildSeriesRequest, initialChartSettings, interpretKeyDates, interpretSeries } = await import(
  "../../lib/market/chartRequest.mjs"
);
const { catalogBody, keyDatesBody, monthly, seriesBody } = await import("./fixtures.mjs");

const TODAY = "2026-10-07";
const catalog = interpretCatalog({ ok: true, body: catalogBody() }).catalog;
const KEYS = ["t.cat_alpha.1", "t.cat_alpha.2", "t.cat_alpha.3", "t.cat_beta.1", "t.cat_beta.2", "t.cat_beta.5"];
const SEL = KEYS.map((key) => ({ kind: "indicator", key }));
const ramp = (start, step, n = 84) => Array.from({ length: n }, (_, i) => (start + step * i + (i === 40 ? 30 : 0)).toFixed(3));
const RESPONSE = seriesBody(
  KEYS.map((key, i) => ({ key, points: monthly(2019, 1, i === 5 ? ramp(100, 4) : ramp(100 + i, 0.2 + i * 0.01)) })),
);
const KEY_DATES = interpretKeyDates({ ok: true, body: keyDatesBody() });

function render({ series = interpretSeries({ ok: true, body: RESPONSE }), settings = {}, selection = SEL, keyDates = KEY_DATES } = {}) {
  const s = { ...initialChartSettings(catalog, TODAY), anchor: "2021-06-30", ...settings };
  return renderToStaticMarkup(
    h(ChartView, {
      catalog,
      selection,
      settings: s,
      onSettingsChange: () => {},
      today: TODAY,
      request: buildSeriesRequest(selection, catalog, s.resolution),
      seriesState: { ...series, url: "" },
      keyDatesState: keyDates,
      stale: false,
      initialWidth: 960,
    }),
  );
}

const count = (html, needle) => html.split(needle).length - 1;

test("a normal envelope renders the chart: one line per series, axes, anchor bar and input, table", () => {
  const html = render();
  assert.ok(html.includes('data-chart="svg"'));
  for (const k of KEYS) assert.ok(html.includes(`data-chart-line="${k}"`), `line ${k}`);
  assert.equal(count(html, "data-chart-line="), KEYS.length);
  assert.ok(html.includes('data-chart="anchor-bar"'));
  assert.ok(html.includes("Anchor · Jun 2021"));
  assert.ok(html.includes('aria-label="Anchor date"'));
  assert.ok(html.includes('type="range"'));
  assert.ok(html.includes('data-chart="y-title"'));
  assert.ok(html.includes("Mode label index (Jun 2021, log scale)"));
  assert.ok(html.includes('data-market="trends-table"'));
  assert.ok(html.includes("Trends and outliers"));
  // Server vocabularies drive the controls.
  for (const t of ["Mode label index", "Mode label sigma", "Res label monthly", "Res label quarterly"]) {
    assert.ok(html.includes(t), `missing ${t}`);
  }
  assert.ok(!html.includes("Mode label level"), "level is not a chart measure");
  // Series colours are the catalog's.
  assert.ok(html.includes(catalog.indicators.find((i) => i.series_key === KEYS[0]).color));
});

test("an envelope-less series response renders the error state and NO chart", () => {
  for (const mutate of [(b) => delete b.permissions, (b) => (b.permissions = { can_read: false }), (b) => delete b.series]) {
    const b = structuredClone(RESPONSE);
    mutate(b);
    const html = render({ series: interpretSeries({ ok: true, body: b }) });
    assert.ok(html.includes('data-market="chart-error"'));
    assert.ok(html.includes(NO_ENVELOPE_MESSAGE));
    assert.ok(!html.includes('data-chart="svg"'));
    assert.ok(!html.includes("data-chart-line="));
    assert.ok(!html.includes('data-market="trends-table"'));
  }
});

test("a refused request shows the server's message; an empty selection requests nothing", () => {
  const msg = "More than 300000 points in one request; ask for fewer keys, a coarser frequency or a shorter window";
  const html = render({ series: interpretSeries({ ok: false, body: { detail: { message: msg } } }) });
  assert.ok(html.includes(msg));
  assert.ok(!html.includes('data-chart="svg"'));
  const empty = render({ selection: [] });
  assert.ok(empty.includes("Select indicators to chart them."));
  assert.ok(!empty.includes('data-chart="svg"'));
});

test("Axis is disabled in Sigma", () => {
  const html = render({ settings: { measure: "sigma" } });
  const group = html.split('data-chart-control="scale"')[1].split("</div>")[0];
  assert.equal(count(group, 'disabled=""'), 2);
  assert.ok(!render().split('data-chart-control="scale"')[1].split("</div>")[0].includes('disabled=""'));
});

test("overlay toggles show and hide their elements", () => {
  const all = render();
  assert.ok(all.includes('data-chart="regime-band"'));
  assert.ok(all.includes('data-chart="key-date"'));
  assert.ok(all.includes('data-chart="cohort-band"'));
  assert.ok(all.includes('data-chart="median"'));
  assert.ok(all.includes('data-chart="big-move"'));
  assert.ok(all.includes("Regime type label A"), "legend from the server's regime_types");
  for (const t of ['data-chart-overlay="events"', 'data-chart-overlay="band"', 'data-chart-overlay="emphasis"']) {
    const input = all.match(new RegExp(`<input[^>]*${t}[^>]*>`))?.[0] ?? "";
    assert.ok(input.includes('checked=""'), `${t} on by default`);
  }

  const noEvents = render({ settings: { overlays: { events: false, band: true, emphasis: true } } });
  assert.ok(!noEvents.includes('data-chart="regime-band"') && !noEvents.includes('data-chart="key-date"'));
  assert.ok(!noEvents.includes("Regime type label A"));
  assert.ok(noEvents.includes('data-chart="cohort-band"'));

  const noBand = render({ settings: { overlays: { events: true, band: false, emphasis: true } } });
  assert.ok(!noBand.includes('data-chart="cohort-band"') && !noBand.includes('data-chart="median"'));
  assert.ok(noBand.includes('data-chart="regime-band"'));

  const noEmphasis = render({ settings: { overlays: { events: true, band: true, emphasis: false } } });
  assert.ok(!noEmphasis.includes('data-chart="big-move"'));
  assert.ok(!/stroke-opacity="0\.3"/.test(noEmphasis), "no faded lines");
  assert.ok(/stroke-opacity="0\.3"/.test(all), "in-pack lines fade with highlighting on");
});

test("a key-dates response without its envelope draws no events, and says so", () => {
  const b = keyDatesBody();
  delete b.permissions;
  const html = render({ keyDates: interpretKeyDates({ ok: true, body: b }) });
  assert.ok(!html.includes('data-chart="regime-band"'));
  assert.ok(!html.includes('data-chart="key-date"'));
  assert.ok(html.includes('data-market="key-dates-error"'));
  assert.ok(html.includes('data-chart="svg"'), "the chart itself still renders");
});

test("the anchor input: one period per arrow, twelve per Page Up / Page Down", () => {
  const periods = Array.from({ length: 60 }, (_, i) => ({ date: `p${i}` }));
  const moves = [];
  const el = AnchorSlider({ periods, index: 30, valueText: "Anchor · x", onAnchorChange: (d) => moves.push(d) });
  assert.equal(el.props["aria-label"], "Anchor date");
  assert.equal(el.props.max, 59);
  assert.equal(el.props.value, 30);
  let prevented = 0;
  const key = (k) => el.props.onKeyDown({ key: k, preventDefault: () => (prevented += 1) });
  key("ArrowRight");
  key("ArrowLeft");
  key("PageUp");
  key("PageDown");
  key("Tab");
  assert.deepEqual(moves, ["p31", "p29", "p42", "p18"]);
  assert.equal(prevented, 4, "only the handled keys are taken from the browser");
  el.props.onChange({ currentTarget: { valueAsNumber: 7 } });
  assert.equal(moves[moves.length - 1], "p7");
});

test("the page's Chart tab renders the chart panel inside the ready branch only", () => {
  const ready = renderToStaticMarkup(
    h(MarketIndicatorsView, { catalogState: interpretCatalog({ ok: true, body: catalogBody() }), today: TODAY, initialTab: "chart" }),
  );
  assert.ok(ready.includes('data-market-control="chart-controls"'));
  const lost = catalogBody();
  delete lost.permissions;
  const closed = renderToStaticMarkup(
    h(MarketIndicatorsView, { catalogState: interpretCatalog({ ok: true, body: lost }), today: TODAY, initialTab: "chart" }),
  );
  assert.ok(!closed.includes('data-market-control="chart-controls"'));
  assert.ok(!closed.includes('data-chart="svg"'));
});
