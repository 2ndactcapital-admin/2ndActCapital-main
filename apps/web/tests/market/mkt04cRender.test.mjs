/**
 * Renders the REAL mkt04c components (react-dom/server, JSX compiled by
 * ./jsxLoader.mjs, components imported unchanged) and fires the hookless
 * views' handlers directly:
 *   - both date dropdowns render the server's options; My dates is disabled
 *     with "None saved yet" when empty;
 *   - Start/End appear only for a range entry and a new pick resets to Start;
 *   - the selected-period band draws even with the events overlay off;
 *     personal dates draw only under it; the flag names the picked date;
 *   - delete is two steps; the add form keeps its inputs and shows the
 *     server's message on a 422 / 409;
 *   - with can_write false, or no envelope, no add / delete / save / update
 *     control renders; presets never show update or delete;
 *   - the correlations card: empty, loading, results, null reasons, fail closed.
 */
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: KeyDatesBar } = await import("../../components/market/KeyDatesBar.jsx");
const { default: SavedViewsView } = await import("../../components/market/SavedViewsView.jsx");
const { default: CorrelationsView } = await import("../../components/market/CorrelationsView.jsx");
const { default: ChartView } = await import("../../components/market/ChartView.jsx");
const { default: MarketIndicatorsView } = await import("../../components/market/MarketIndicatorsView.jsx");
const { interpretCatalog, NO_ENVELOPE_MESSAGE } = await import("../../lib/market/catalogModel.mjs");
const { buildSeriesRequest, interpretKeyDates, interpretSeries } = await import("../../lib/market/chartRequest.mjs");
const { prepareChartData } = await import("../../lib/market/chartModel.mjs");
const { afterSubmit, canWriteDates, closeForm, editForm, openForm, requestDelete, submitForm } = await import(
  "../../lib/market/customDates.mjs"
);
const { EDGE_END, SOURCE_KEY, SOURCE_MY, anchorDateFor, pickDate, resolveDateSelection, setEdge } = await import(
  "../../lib/market/keyDatesModel.mjs"
);
const { EMPTY_SAVE, initialPageSettings, interpretViews } = await import("../../lib/market/viewsModel.mjs");
const { correlationRows, correlationSeries, interpretCorrelations, lagOptions } = await import(
  "../../lib/market/correlationModel.mjs"
);
const { catalogBody, monthly, seriesBody } = await import("./fixtures.mjs");
const { correlationsBody, keyDates04cBody, viewsBody } = await import("./mkt04cFixtures.mjs");

const TODAY = "2026-10-07";
const catalog = interpretCatalog({ ok: true, body: catalogBody() }).catalog;
const KD = interpretKeyDates({ ok: true, body: keyDates04cBody() });
const MY1 = "11111111-0000-0000-0000-000000000001";
const noop = () => {};
const count = (html, needle) => html.split(needle).length - 1;

/** Every element in a returned tree (hookless components are called, not rendered). */
function elements(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => elements(n, out));
  else if (node && typeof node === "object" && node.props) {
    out.push(node);
    elements(node.props.children, out);
  }
  return out;
}
const byData = (tree, attr, value) => elements(tree).filter((e) => e.props[attr] === value);

function barProps(over = {}) {
  const dateSel = over.dateSel ?? null;
  const kd = over.keyDates ?? KD;
  return {
    keyDates: kd,
    dateSel,
    resolved: resolveDateSelection(dateSel, kd),
    canWrite: canWriteDates(kd),
    nameMax: kd.kind === "ready" ? kd.limits.name_max : undefined,
    form: closeForm(),
    pendingDelete: null,
    onPick: noop,
    onEdge: noop,
    onOpenForm: noop,
    onFormEdit: noop,
    onFormSave: noop,
    onFormCancel: noop,
    onDeleteRequest: noop,
    onDeleteConfirm: noop,
    onDeleteCancel: noop,
    ...over,
  };
}
const bar = (over) => renderToStaticMarkup(h(KeyDatesBar, barProps(over)));
const WRITE_MARKERS = ['data-key-dates="add"', 'data-key-dates="delete"', 'data-key-dates="add-form"', 'data-key-dates="confirm"', 'data-key-dates="save"'];

// ── Dropdowns ───────────────────────────────────────────────────────────────

test("both dropdowns render the server's options in its order; personal dates never mix into Key dates", () => {
  const html = bar();
  const keySel = html.split('data-key-dates="key"')[1].split("</select>")[0];
  const mySel = html.split('data-key-dates="my"')[1].split("</select>")[0];
  const labels = ["Oct 19, 1987 · Key date before the data", "Feb 2020 – Apr 2020 · Month range kd", "Mar 16, 2020 · Key date one", "Mar 16, 2022 – Jul 26, 2023 · Day range kd"];
  let at = -1;
  for (const l of labels) {
    const i = keySel.indexOf(l);
    assert.ok(i > at, `${l} in order`);
    at = i;
  }
  assert.ok(!keySel.includes("Personal alpha"));
  assert.ok(mySel.includes("Jan 5, 2021 · Personal alpha") && mySel.includes("Jul 9, 2024 · Personal beta"));
  assert.ok(!mySel.includes("Key date one"));
  assert.ok(!/<select[^>]*data-key-dates="my"[^>]*disabled/.test(html) && !/disabled=""[^>]*data-key-dates="my"/.test(html));
});

test("My dates is disabled and reads 'None saved yet' when the caller has none", () => {
  const html = bar({ keyDates: interpretKeyDates({ ok: true, body: keyDates04cBody({ custom: false }) }) });
  const sel = html.match(/<select[^>]*data-key-dates="my"[^>]*>/)[0];
  assert.ok(sel.includes('disabled=""'));
  assert.ok(html.split('data-key-dates="my"')[1].split("</select>")[0].includes("None saved yet"));
  assert.ok(!html.includes('data-key-dates="delete"'), "nothing to delete");
});

test("Start/End appear only for a range entry, and a new pick resets to Start", () => {
  assert.ok(!bar().includes('data-key-dates="edges"'), "nothing picked");
  assert.ok(!bar({ dateSel: pickDate(SOURCE_KEY, "kd-one") }).includes('data-key-dates="edges"'), "a single date");
  assert.ok(!bar({ dateSel: pickDate(SOURCE_MY, MY1) }).includes('data-key-dates="edges"'), "a personal date");
  const atEnd = bar({ dateSel: setEdge(pickDate(SOURCE_KEY, "kd-day-range"), EDGE_END) });
  assert.ok(atEnd.includes("Start of period") && atEnd.includes("End of period"));
  assert.match(atEnd, /aria-pressed="true"[^>]*data-key-dates-edge="end"/);

  // Fire the Key dates select: the bar reports (source, id); the pick that
  // results starts at Start whatever the previous edge was.
  const picks = [];
  const tree = KeyDatesBar(barProps({ dateSel: setEdge(pickDate(SOURCE_KEY, "kd-day-range"), EDGE_END), onPick: (s, id) => picks.push([s, id]) }));
  byData(tree, "data-key-dates", "key")[0].props.onChange({ target: { value: "kd-month-range" } });
  assert.deepEqual(picks, [[SOURCE_KEY, "kd-month-range"]]);
  const next = pickDate(...picks[0]);
  const after = bar({ dateSel: next });
  assert.match(after, /aria-pressed="true"[^>]*data-key-dates-edge="start"/);
  assert.match(after, /aria-pressed="false"[^>]*data-key-dates-edge="end"/);
  // The edge buttons report the edge.
  const edges = [];
  const t2 = KeyDatesBar(barProps({ dateSel: next, onEdge: (e) => edges.push(e) }));
  byData(t2, "data-key-dates-edge", "end")[0].props.onClick();
  assert.deepEqual(edges, [EDGE_END]);
});

// ── Writes ──────────────────────────────────────────────────────────────────

test("delete takes two steps: Delete only asks; the confirm row names the date; only it can confirm", () => {
  const asked = [];
  const confirmed = [];
  const props = barProps({ dateSel: pickDate(SOURCE_MY, MY1), onDeleteRequest: (id) => asked.push(id), onDeleteConfirm: () => confirmed.push(1) });
  const first = renderToStaticMarkup(h(KeyDatesBar, props));
  assert.ok(first.includes('data-key-dates="delete"'));
  assert.ok(!first.includes('data-key-dates="confirm"'), "no confirm row before Delete is clicked");
  byData(KeyDatesBar(props), "data-key-dates", "delete")[0].props.onClick();
  assert.deepEqual(asked, [MY1]);
  assert.equal(confirmed.length, 0, "clicking Delete confirms nothing");

  const second = barProps({ ...props, pendingDelete: requestDelete(MY1) });
  const html = renderToStaticMarkup(h(KeyDatesBar, second));
  assert.ok(html.includes("Delete &quot;Personal alpha&quot; from your dates?"));
  assert.ok(html.includes('data-key-dates="confirm-delete"') && html.includes('data-key-dates="cancel-delete"'));
  assert.ok(!html.includes('data-key-dates="delete"'), "the first Delete is replaced by the confirm row");
  byData(KeyDatesBar(second), "data-key-dates", "confirm-delete")[0].props.onClick();
  assert.equal(confirmed.length, 1);
  // Delete shows only while a personal date is picked.
  assert.ok(!bar({ dateSel: pickDate(SOURCE_KEY, "kd-one") }).includes('data-key-dates="delete"'));
});

test("the add form keeps its inputs and shows the server's message on a 422 or a 409", () => {
  const typed = editForm(editForm(openForm(), "name", "Typed name"), "date", "1850-01-01");
  for (const [msg, status] of [
    ["That date is outside the available data (Jan 1919 to Oct 2026).", 422],
    ["You already saved that name on that date.", 409],
  ]) {
    const { form } = afterSubmit(submitForm(typed).form, { ok: false, status, body: { detail: { message: msg, errors: [{ msg }] } } });
    const html = bar({ form });
    assert.ok(html.includes('data-key-dates="add-form"'));
    assert.ok(html.includes('value="Typed name"') && html.includes('value="1850-01-01"'), "inputs kept");
    assert.equal(count(html, msg.replace(/"/g, "&quot;")), 1, "the server's message, once");
    assert.match(html, /role="alert"/);
  }
  const fresh = bar({ form: openForm() });
  assert.match(fresh, /maxLength="60"|maxlength="60"/, "the Name input's limit is the server's name_max");
  assert.match(fresh, /<button[^>]*disabled=""[^>]*data-key-dates="save"/, "Save is disabled until a name and a date");
  assert.ok(fresh.includes(">Save date<") && fresh.includes(">Cancel<"));
});

test("with can_write false, or without an envelope, no add, delete or form renders", () => {
  const ro = keyDates04cBody();
  ro.permissions.can_write = false;
  const html = bar({ keyDates: interpretKeyDates({ ok: true, body: ro }), dateSel: pickDate(SOURCE_MY, MY1), form: openForm(), pendingDelete: requestDelete(MY1) });
  for (const m of WRITE_MARKERS) assert.ok(!html.includes(m), `rendered ${m}`);
  assert.ok(html.includes('data-key-dates="my"'), "reading your dates still works");
  // canWrite must be exactly true: a truthy non-boolean is not a grant.
  const truthy = bar({ canWrite: "true", dateSel: pickDate(SOURCE_MY, MY1), form: openForm() });
  for (const m of WRITE_MARKERS) assert.ok(!truthy.includes(m), `truthy rendered ${m}`);
  // No envelope: the key-dates state is an error and the bar renders nothing.
  const lost = keyDates04cBody();
  delete lost.permissions;
  assert.equal(bar({ keyDates: interpretKeyDates({ ok: true, body: lost }), form: openForm() }), "");
});

// ── The chart: band, personal lines, flag, legend ───────────────────────────

const KEYS = ["t.cat_alpha.1", "t.cat_alpha.2", "t.cat_alpha.3", "t.cat_beta.1", "t.cat_beta.2", "t.cat_beta.5"];
const SEL = KEYS.map((key) => ({ kind: "indicator", key }));
const ramp = (start, step, n = 84) => Array.from({ length: n }, (_, i) => (start + step * i).toFixed(3));
const RESPONSE = seriesBody(KEYS.map((key, i) => ({ key, points: monthly(2019, 1, ramp(100 + i, 0.2 + i * 0.01)) })));
const SERIES = interpretSeries({ ok: true, body: RESPONSE });
const PERIODS = prepareChartData(RESPONSE, SEL, catalog, "monthly").periods;

function chart({ dateSel = null, overlays, anchor = "2021-06-30", selection = SEL, keyDates = KD } = {}) {
  const s = { ...initialPageSettings(catalog, TODAY), anchor, anchorYears: null, ...(overlays ? { overlays } : {}) };
  return renderToStaticMarkup(
    h(ChartView, {
      catalog,
      selection,
      settings: s,
      onSettingsChange: noop,
      today: TODAY,
      request: buildSeriesRequest(selection, catalog, s.resolution),
      seriesState: { ...SERIES, url: "" },
      keyDatesState: keyDates,
      stale: false,
      initialWidth: 960,
      dateSel,
      onPickDate: noop,
      onKeyDatesChanged: noop,
    }),
  );
}
const OFF = { events: false, band: false, emphasis: false };

test("a picked range shades its span even with every overlay off; nothing is shaded otherwise", () => {
  const sel = pickDate(SOURCE_KEY, "kd-day-range");
  const anchor = anchorDateFor(PERIODS, "2022-03-16");
  const html = chart({ dateSel: sel, overlays: OFF, anchor });
  assert.equal(count(html, 'data-chart="selected-band"'), 1);
  assert.ok(html.includes('fill="rgba(43,95,158,0.16)"'), "the distinct light blue");
  assert.ok(html.includes("Selected period"), "legend entry");
  assert.ok(!html.includes('data-chart="regime-band"') && !html.includes('data-chart="key-date"'), "overlays really are off");
  const none = chart({ overlays: OFF });
  assert.ok(!none.includes('data-chart="selected-band"') && !none.includes("Selected period"));
  assert.ok(!chart({ dateSel: pickDate(SOURCE_KEY, "kd-one"), overlays: OFF }).includes('data-chart="selected-band"'), "a single date has no span");
});

test("personal dates draw as darker lines under 'Regimes & events' only, with a 'My date' legend entry", () => {
  const on = chart();
  assert.equal(count(on, 'data-chart="my-date"'), 2);
  assert.ok(on.includes('stroke="#334155"'), "darker than the gold key-date lines");
  assert.ok(on.includes(">My date<"));
  const off = chart({ overlays: { events: false, band: true, emphasis: true } });
  assert.equal(count(off, 'data-chart="my-date"'), 0);
  assert.ok(!off.includes(">My date<"));
});

test("the anchor flag names the picked date while it is the anchor; '(end)' for an end anchor", () => {
  const one = pickDate(SOURCE_KEY, "kd-one");
  const html = chart({ dateSel: one, anchor: anchorDateFor(PERIODS, "2020-03-16") });
  assert.ok(html.includes("Anchor · Mar 2020 · Key date one"), "a day-precision date lands on its own month");
  const end = setEdge(pickDate(SOURCE_KEY, "kd-day-range"), EDGE_END);
  assert.ok(chart({ dateSel: end, anchor: anchorDateFor(PERIODS, "2023-07-26") }).includes("Anchor · Jul 2023 · Day range kd (end)"));
  // An anchor elsewhere (e.g. the page has not caught up) shows only the date.
  assert.ok(chart({ dateSel: one, anchor: "2021-06-30" }).includes(">Anchor · Jun 2021<"));
  // The bar and the correlations card render with the chart.
  assert.ok(html.includes('data-market="key-dates-bar"'));
  assert.ok(html.indexOf('data-market="correlations"') < html.indexOf('data-market="trends-table"'), "correlations above the trends table");
  assert.ok(html.indexOf('data-market="key-dates-bar"') < html.indexOf('data-market="correlations"'));
});

test("inside the chart, write controls follow the key-dates envelope (container wiring)", () => {
  assert.ok(chart().includes('data-key-dates="add"'), "can_write true -> Add");
  const ro = keyDates04cBody();
  ro.permissions.can_write = false;
  const html = chart({ keyDates: interpretKeyDates({ ok: true, body: ro }), dateSel: pickDate(SOURCE_MY, MY1) });
  for (const m of WRITE_MARKERS) assert.ok(!html.includes(m), `rendered ${m}`);
});

// ── Saved views ─────────────────────────────────────────────────────────────

function viewsProps(over = {}) {
  return {
    state: interpretViews({ ok: true, body: viewsBody() }),
    activeId: null,
    notices: [],
    form: EMPTY_SAVE,
    pendingDelete: null,
    updateError: null,
    updating: false,
    onLoad: noop,
    onUpdate: noop,
    onDeleteRequest: noop,
    onDeleteConfirm: noop,
    onDeleteCancel: noop,
    onNameChange: noop,
    onSave: noop,
    ...over,
  };
}
const views = (over) => renderToStaticMarkup(h(SavedViewsView, viewsProps(over)));
const PRESET = "22222222-0000-0000-0000-000000000001";
const ETA = "33333333-0000-0000-0000-000000000001";
const item = (html, id) => html.split(`data-view-id="${id}"`)[1].split("</li>")[0];

test("presets first, then the caller's views; presets show no update or delete control, even when active", () => {
  const html = views({ activeId: PRESET });
  assert.ok(html.indexOf("Preset view zeta") < html.indexOf("User view eta"));
  assert.ok(!item(html, PRESET).includes('data-view-control="update"') && !item(html, PRESET).includes('data-view-control="delete"'));
  assert.match(item(html, PRESET), /aria-pressed="true"/, "the active view is marked");
  const userActive = views({ activeId: ETA });
  assert.ok(item(userActive, ETA).includes('data-view-control="update"'), "the active user view can be updated");
  assert.ok(item(userActive, ETA).includes('data-view-control="delete"'));
  assert.ok(!item(userActive, "33333333-0000-0000-0000-000000000002").includes('data-view-control="update"'), "Update only on the active one");
  assert.match(views(), /maxLength="77"|maxlength="77"/, "the name field's limit is the server's");
});

test("clicking a view loads it; deleting a view asks first", () => {
  const loaded = [];
  const asked = [];
  const props = viewsProps({ onLoad: (v) => loaded.push(v.id), onDeleteRequest: (id) => asked.push(id) });
  const tree = SavedViewsView(props);
  elements(tree).filter((e) => e.props["data-view-control"] === "load")[0].props.onClick();
  assert.deepEqual(loaded, [PRESET]);
  elements(tree).filter((e) => e.props["data-view-control"] === "delete")[0].props.onClick();
  assert.deepEqual(asked, [ETA]);
  assert.ok(!views().includes('data-views="confirm"'));
  const confirm = views({ pendingDelete: { id: ETA, busy: false, error: null } });
  assert.ok(confirm.includes('data-views="confirm"') && confirm.includes("User view eta"));
});

test("a refused save shows the server's message and keeps the name", () => {
  const html = views({ form: { name: "Kept name", saving: false, error: "You already have a view with that name." } });
  assert.ok(html.includes('value="Kept name"'));
  assert.ok(html.includes("You already have a view with that name."));
});

test("with can_write false, or no envelope, no save, update or delete control renders", () => {
  const MARKERS = ['data-view-control="save"', 'data-view-control="name"', 'data-view-control="update"', 'data-view-control="delete"', 'data-views="confirm"'];
  const ro = views({ state: interpretViews({ ok: true, body: viewsBody({ canWrite: false }) }), activeId: ETA, pendingDelete: { id: ETA, busy: false, error: null } });
  for (const m of MARKERS) assert.ok(!ro.includes(m), `read-only rendered ${m}`);
  assert.ok(ro.includes("User view eta"), "views can still be loaded");
  const b = viewsBody();
  delete b.permissions;
  const lost = views({ state: interpretViews({ ok: true, body: b }), activeId: ETA });
  for (const m of MARKERS) assert.ok(!lost.includes(m), `envelope-less rendered ${m}`);
  assert.ok(lost.includes(NO_ENVELOPE_MESSAGE) && !lost.includes("User view eta"), "fail closed: an error, no list");
});

test("the notices of a loaded view show quietly in the card", () => {
  const html = views({ notices: ["Not available any more, left out: t.retired.series"] });
  assert.ok(html.includes('data-views="notices"') && html.includes("t.retired.series"));
});

test("the page puts Saved views at the top of the left column, inside the ready branch only", () => {
  const ready = renderToStaticMarkup(h(MarketIndicatorsView, { catalogState: interpretCatalog({ ok: true, body: catalogBody() }), today: TODAY }));
  assert.ok(ready.includes('data-market="saved-views"'));
  assert.ok(ready.indexOf('data-market="saved-views"') < ready.indexOf('data-market-control="selection-panel"'));
  const lost = catalogBody();
  delete lost.permissions;
  assert.ok(!renderToStaticMarkup(h(MarketIndicatorsView, { catalogState: interpretCatalog({ ok: true, body: lost }), today: TODAY })).includes("saved-views"));
});

// ── Correlations ────────────────────────────────────────────────────────────

function corr(state) {
  const series = correlationSeries(SEL, catalog);
  return renderToStaticMarkup(
    h(CorrelationsView, { series, focusKey: series[0].key, onFocus: noop, lag: 0, lagOptions: lagOptions([-24, 24]), lagEnabled: true, onLag: noop, state }),
  );
}

test("correlations: empty, loading, results with r as returned, nulls with their reason, and fail closed", () => {
  const empty = corr({ kind: "empty" });
  assert.ok(empty.includes('data-correlations="empty"') && !empty.includes('data-correlations="focus"'));
  assert.ok(corr({ kind: "loading" }).includes('data-correlations="loading"'));

  const resp = correlationsBody(KEYS[0], [
    [KEYS[4], "-0.8312", 61, "monthly"],
    [KEYS[1], "0.1200", 61, "monthly"],
    [KEYS[2], null, 7, "monthly", "insufficient_overlap"],
  ]);
  const ready = interpretCorrelations({ ok: true, body: resp });
  const rows = correlationRows(ready.response, { series: correlationSeries(SEL, catalog), frequencies: catalog.vocabularies.frequencies });
  const html = corr({ kind: "ready", rows, lagConvention: resp.lag_convention });
  assert.ok(html.indexOf(`data-correlation-row="${KEYS[4]}"`) < html.indexOf(`data-correlation-row="${KEYS[1]}"`), "server order");
  assert.ok(html.includes(">-0.8312<") && html.includes(">0.1200<"), "r exactly as returned");
  assert.ok(html.includes("width:83.12%") && html.includes("background:#9B2335"), "negative: error red, |r| width");
  assert.ok(html.includes("width:12.00%") && html.includes("background:#1B2B4B"), "positive: navy");
  assert.ok(html.includes(">insufficient overlap<"), "null r shows the reason");
  assert.ok(html.includes("n 61") && html.includes("Res label monthly"));
  assert.ok(html.includes("Lag convention text from the server"));
  assert.ok(html.includes("not levels"), "the footnote");
  assert.equal(count(html, "<option"), correlationSeries(SEL, catalog).length + 49, "focus = the selected series; lag = the server's range");

  const b = correlationsBody(KEYS[0], [[KEYS[4], "-0.8312", 61, "monthly"]]);
  delete b.permissions;
  const closed = interpretCorrelations({ ok: true, body: b });
  const err = corr(closed);
  assert.ok(err.includes('data-correlations="error"') && err.includes(NO_ENVELOPE_MESSAGE));
  assert.ok(!err.includes("data-correlation-row"), "no results without an envelope");
});

test("the chart's correlations card: loading once two series are selected, empty with one", () => {
  assert.ok(chart().includes('data-correlations="loading"'), "a request is pending for six series");
  const one = chart({ selection: SEL.slice(0, 1) });
  assert.ok(one.includes('data-correlations="empty"'));
});
