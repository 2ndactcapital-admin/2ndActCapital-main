/**
 * Renders the REAL market components with react-dom/server (mkt04a):
 *   - fail closed: an envelope-less catalog renders the error state and NO
 *     selection or grid controls; a real envelope renders them;
 *   - the grid table: em dash for null, floating / warning / unavailable
 *     notes, server strings never parsed to floats;
 *   - navigation: the /market entry is visible to any signed-in user.
 * JSX is compiled by ./jsxLoader.mjs; the components are imported unchanged.
 */
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: MarketIndicatorsView } = await import("../../components/market/MarketIndicatorsView.jsx");
const { default: MarketGridTable } = await import("../../components/market/MarketGridTable.jsx");
const { interpretCatalog, NO_ENVELOPE_MESSAGE } = await import("../../lib/market/catalogModel.mjs");
const { buildGridView, formatCell, EM_DASH, FLOATING_NOTE } = await import("../../lib/market/gridView.mjs");
const { MENU_ITEMS, visibleMenuItems } = await import("../../lib/menuVisibility.mjs");
const { catalogBody, gridBody } = await import("./fixtures.mjs");

const TODAY = "2026-10-07";
const CONTROL_MARKERS = [
  'data-market-control="selection-panel"',
  'data-market-control="category-chips"',
  'data-market-control="tabs"',
  'data-market-control="grid"',
  'data-market-control="grid-controls"',
  "<select",
  'type="checkbox"',
  'type="date"',
];

const renderView = (state, props = {}) =>
  renderToStaticMarkup(h(MarketIndicatorsView, { catalogState: state, today: TODAY, initialTab: "grid", ...props }));

function assertNoControls(html) {
  for (const m of CONTROL_MARKERS) assert.ok(!html.includes(m), `control rendered: ${m}`);
}

// ── Fail closed ─────────────────────────────────────────────────────────────

test("an envelope-less catalog renders the error state and NO selection or grid controls", () => {
  for (const mutate of [
    (b) => delete b.permissions,
    (b) => (b.permissions = null),
    (b) => (b.permissions = { can_read: "true" }),
    (b) => (b.permissions = { can_read: false }),
    (b) => delete b.vocabularies,
  ]) {
    const body = catalogBody();
    mutate(body);
    const state = interpretCatalog({ ok: true, body });
    assert.equal(state.kind, "error");
    const html = renderView(state);
    assert.ok(html.includes('data-market-state="error"'));
    assert.ok(html.includes(NO_ENVELOPE_MESSAGE));
    assertNoControls(html);
  }
});

test("a refused catalog shows the server's own message and no controls", () => {
  const state = interpretCatalog({ ok: false, body: { detail: "Authentication required" } });
  const html = renderView(state);
  assert.ok(html.includes("Authentication required"));
  assertNoControls(html);
  assertNoControls(renderView({ kind: "loading" }));
});

test("a normal envelope renders the selection panel and the grid controls", () => {
  const state = interpretCatalog({ ok: true, body: catalogBody() });
  assert.equal(state.kind, "ready");
  const html = renderView(state);
  for (const m of CONTROL_MARKERS) assert.ok(html.includes(m), `missing control: ${m}`);
  // Server-driven labels, colours and vocabularies reach the markup.
  for (const text of ["Alpha group", "Beta group", "#123456", "Mode label sigma", "Freq label quarterly", "prov_one", "lic restricted"]) {
    assert.ok(html.includes(text), `missing server text: ${text}`);
  }
  // public_domain carries no marker; the others do.
  assert.ok(!html.includes(">public domain<"));
  assert.ok(html.includes("0 / 40 selected"));
  // Notes are listed disabled with the server's reason, collapsed.
  assert.ok(html.includes('data-market="security-group-collapsed"'));
  assert.ok(html.includes("no price history"));
  // Empty selection: the quiet prompt, no table.
  assert.ok(html.includes("Select indicators to see their values."));
  assert.ok(!html.includes('data-market="grid-table"'));
});

// mkt04b replaced the mkt04a placeholder ("The chart arrives in the next
// release.") with the chart; this test used to assert the placeholder.
test("the Chart tab renders the chart's controls, not the grid's and not the old placeholder", () => {
  const state = interpretCatalog({ ok: true, body: catalogBody() });
  const html = renderView(state, { initialTab: "chart" });
  assert.ok(html.includes('data-market-tab="chart"'));
  assert.ok(html.includes('data-market-control="chart-controls"'));
  assert.ok(!html.includes("The chart arrives in the next release."));
  assert.ok(!html.includes('data-market-control="grid-controls"'));
});

// ── Grid rendering ──────────────────────────────────────────────────────────

const catalog = interpretCatalog({ ok: true, body: catalogBody() }).catalog;
const SEL = [
  { kind: "indicator", key: "t.cat_alpha.1" },
  { kind: "indicator", key: "t.cat_beta.2" },
  { kind: "indicator", key: "t.cat_gamma.3" },
];
const KEYS = SEL.map((s) => s.key);

test("null cells render an em dash; six-decimal and long values render exactly as returned", () => {
  const rows = [
    { date: "2026-10-07", is_period_end: false, cells: { [KEYS[0]]: "0.123456", [KEYS[1]]: null, [KEYS[2]]: "12345678901234567890.000001" } },
    { date: "2026-09-30", is_period_end: true, cells: { [KEYS[0]]: "-1234.500000", [KEYS[1]]: "100", [KEYS[2]]: "7" } },
  ];
  const view = buildGridView(gridBody(KEYS, { rows }), SEL, catalog);
  const html = renderToStaticMarkup(h(MarketGridTable, { view }));
  assert.ok(html.includes(">0.123456<"), "six decimals visibly unchanged");
  assert.ok(html.includes(">12,345,678,901,234,567,890.000001<"), "no float rounding of a 20-digit value");
  assert.ok(html.includes(">-1,234.500000<"), "trailing zeros kept");
  assert.ok(html.includes(`>${EM_DASH}<`));
  // Rows stay in the server's order (newest first).
  assert.ok(html.indexOf("2026-10-07") < html.indexOf("2026-09-30"));
  // Headers carry the series name and server colour.
  assert.ok(html.includes("Alpha group series 1"));
  assert.ok(html.includes(catalog.indicators.find((i) => i.series_key === KEYS[0]).color));
});

test("formatCell never parses: a float round-trip would change these, the text does not", () => {
  for (const [input, out] of [
    ["0.123456", "0.123456"],
    ["0.1000000000000000055511", "0.1000000000000000055511"],
    ["9007199254740993", "9,007,199,254,740,993"],
    ["1e5", "1e5"],
    [null, EM_DASH],
  ]) {
    assert.equal(formatCell(input), out);
  }
});

test("floating, warning and unavailable series render their notes", () => {
  const rows = [
    { date: "2026-10-07", is_period_end: false, cells: { [KEYS[0]]: "1", [KEYS[1]]: "2", [KEYS[2]]: null } },
    { date: "2026-09-30", is_period_end: true, cells: { [KEYS[0]]: "3", [KEYS[1]]: "4", [KEYS[2]]: null } },
  ];
  const series = {
    [KEYS[0]]: { floating: true },
    [KEYS[1]]: { warnings: ["native_frequency_coarser_than_grid"] },
    [KEYS[2]]: { unavailable_reason: "zero_variance" },
  };
  const view = buildGridView(gridBody(KEYS, { rows, series }), SEL, catalog);
  const html = renderToStaticMarkup(h(MarketGridTable, { view }));
  assert.ok(html.includes('data-floating="true"'));
  assert.ok(html.includes(FLOATING_NOTE));
  assert.ok(html.includes("border-dashed"));
  assert.ok(html.includes("native frequency coarser than grid"));
  assert.ok(html.includes('data-market="column-unavailable"'));
  assert.ok(html.includes("zero variance"));
  assert.ok(/rowspan="2"/i.test(html), "the reason spans every row in place of the values");
});

test("a server vocabulary for warnings, when present, supplies the text", () => {
  const rows = [{ date: "2026-10-07", is_period_end: false, cells: { [KEYS[0]]: "1" } }];
  const body = gridBody([KEYS[0]], { rows, series: { [KEYS[0]]: { warnings: ["w_code"] } } });
  body.vocabularies.warnings = [{ key: "w_code", label: "Server wording" }];
  const view = buildGridView(body, [SEL[0]], catalog);
  assert.deepEqual(view.columns[0].notes, ["Server wording"]);
});

test("no rows and a single series both render cleanly", () => {
  const empty = buildGridView(gridBody(KEYS, { rows: [] }), SEL, catalog);
  assert.ok(renderToStaticMarkup(h(MarketGridTable, { view: empty })).includes('data-market="grid-no-rows"'));
  const one = buildGridView(gridBody([KEYS[0]], { rows: [{ date: "2026-10-07", is_period_end: false, cells: { [KEYS[0]]: "5.5" } }] }), [SEL[0]], catalog);
  const html = renderToStaticMarkup(h(MarketGridTable, { view: one }));
  assert.ok(html.includes(">5.5<"));
  assert.equal(one.columns.length, 1);
});

// ── Navigation ──────────────────────────────────────────────────────────────

test("the /market entry exists with no gate and is visible to any signed-in user", () => {
  const item = MENU_ITEMS.find((i) => i.href === "/market");
  assert.ok(item);
  assert.equal(item.gate, null);
  for (const me of [
    { account_role: "member", roles: ["member"], permissions: [] },
    { account_role: "member", roles: [], permissions: [] },
    { account_role: "super_admin", roles: ["admin"], permissions: ["manage_members"] },
    null, // a lost /users/me still shows an ungated item, as for Dashboard
  ]) {
    assert.ok(visibleMenuItems(me).some((i) => i.href === "/market"));
  }
});
