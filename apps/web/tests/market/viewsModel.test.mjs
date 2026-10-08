/**
 * lib/market/viewsModel.mjs (mkt04c Task 2): the config built is exactly the
 * canonical schema; build-then-apply round-trips; "N years ago" resolves
 * against a supplied today (leap day included); unavailable keys are skipped
 * without touching the stored view; a non-chart measure keeps the current one
 * with a note; the list fails closed and presets are never writable.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { interpretCatalog, NO_ENVELOPE_MESSAGE } from "../../lib/market/catalogModel.mjs";
import {
  CONFIG_FIELDS,
  applyViewConfig,
  buildViewConfig,
  canWriteViews,
  createViewRequest,
  deleteViewRequest,
  initialPageSettings,
  interpretViews,
  patchSettings,
  resolveEnd,
  updateViewRequest,
  viewEntries,
  viewNameMaxLength,
  viewNotices,
} from "../../lib/market/viewsModel.mjs";
import { catalogBody } from "./fixtures.mjs";
import { viewConfig, viewsBody } from "./mkt04cFixtures.mjs";

const TODAY = "2026-10-07";
const catalog = interpretCatalog({ ok: true, body: catalogBody() }).catalog;
const SEL = [
  { kind: "indicator", key: "t.cat_alpha.1" },
  { kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000001" },
];

function deepFreeze(o) {
  if (o && typeof o === "object") {
    Object.values(o).forEach(deepFreeze);
    Object.freeze(o);
  }
  return o;
}

test("the config built is exactly the canonical v1 schema, no extra field anywhere", () => {
  const settings = { ...initialPageSettings(catalog, TODAY), anchor: "2021-06-30", anchorYears: null, resolution: "quarterly" };
  const cfg = buildViewConfig(SEL, settings);
  assert.deepEqual(Object.keys(cfg), CONFIG_FIELDS);
  assert.deepEqual(CONFIG_FIELDS, ["v", "selection", "anchor", "end", "mode", "scale", "overlays"]);
  assert.deepEqual(cfg, {
    v: 1,
    selection: SEL,
    anchor: { type: "date", value: "2021-06-30" },
    end: null,
    mode: "index",
    scale: "log",
    overlays: { events: true, band: true, emphasis: true },
  });
  assert.ok(!JSON.stringify(cfg).includes("quarterly"), "the resolution is not part of a view");
  // Extra fields on the inputs never leak in.
  const dirty = buildViewConfig([{ kind: "indicator", key: "k", label: "x", org_id: "o" }], { ...settings, overlays: { events: 1, band: true, emphasis: false, extra: true } });
  assert.deepEqual(dirty.selection, [{ kind: "indicator", key: "k" }]);
  assert.deepEqual(dirty.overlays, { events: false, band: true, emphasis: false }, "only strict booleans, only the three keys");
  // The page's opening anchor is "5 years ago" and saves as such.
  assert.deepEqual(buildViewConfig(SEL, initialPageSettings(catalog, TODAY)).anchor, { type: "relative", years: 5 });
});

test("round trip: build then apply gives back the same selection and settings", () => {
  const cases = [
    { ...initialPageSettings(catalog, TODAY), anchor: "2019-03-31", anchorYears: null, measure: "sigma", scale: "linear", overlays: { events: false, band: true, emphasis: false } },
    initialPageSettings(catalog, TODAY),
    { ...initialPageSettings(catalog, TODAY), anchor: "2019-03-31", anchorYears: null, end: { type: "date", value: "2024-12-31" } },
  ];
  for (const settings of cases) {
    const view = deepFreeze({ config: buildViewConfig(SEL, settings), unavailable: [] });
    const other = { ...initialPageSettings(catalog, TODAY), anchor: "2001-01-31", anchorYears: null, measure: "index", scale: "log" };
    const applied = applyViewConfig(view, { catalog, today: TODAY, settings: other });
    assert.deepEqual(applied.selection, SEL);
    assert.deepEqual(applied.settings, settings);
    assert.deepEqual(applied.skipped, []);
    assert.equal(applied.measureKept, null);
  }
});

test("'N years ago' resolves against the supplied today, including a leap day", () => {
  const view = { config: viewConfig({ anchor: { type: "relative", years: 1 } }), unavailable: [] };
  const base = initialPageSettings(catalog, TODAY);
  assert.equal(applyViewConfig(view, { catalog, today: "2028-02-29", settings: base }).settings.anchor, "2027-02-28", "29 Feb falls back to the 28th");
  assert.equal(applyViewConfig(view, { catalog, today: "2028-02-29", settings: base }).settings.anchorYears, 1);
  const four = { config: viewConfig({ anchor: { type: "relative", years: 4 } }), unavailable: [] };
  assert.equal(applyViewConfig(four, { catalog, today: "2028-02-29", settings: base }).settings.anchor, "2024-02-29", "four years back is a leap year again");
  assert.equal(resolveEnd({ type: "relative", years: 1 }, "2028-02-29"), "2027-02-28");
  assert.equal(resolveEnd({ type: "date", value: "2024-12-31" }, TODAY), "2024-12-31");
  assert.equal(resolveEnd(null, TODAY), null);
});

test("unavailable keys are skipped and named; the stored view is not modified", () => {
  const body = viewsBody();
  const theta = deepFreeze(structuredClone(body.views[1]));
  const before = JSON.stringify(theta);
  const applied = applyViewConfig(theta, { catalog, today: TODAY, settings: initialPageSettings(catalog, TODAY) });
  assert.deepEqual(applied.selection, [{ kind: "indicator", key: "t.cat_alpha.1" }]);
  assert.deepEqual(applied.skipped, ["t.retired.series"], "not in the catalog any more: named by its key");
  assert.equal(JSON.stringify(theta), before, "loading never changes the view (it is also frozen: a write would throw)");
  // A key the catalog still knows by name is named by that name.
  const named = { config: viewConfig({ selection: [{ kind: "indicator", key: "t.cat_alpha.1" }, { kind: "indicator", key: "t.cat_beta.2" }] }), unavailable: ["t.cat_beta.2"] };
  const a2 = applyViewConfig(named, { catalog, today: TODAY, settings: initialPageSettings(catalog, TODAY) });
  assert.deepEqual(a2.skipped, ["Beta group series 2"]);
  assert.deepEqual(viewNotices(a2, catalog.vocabularies.modes), ["Not available any more, left out: Beta group series 2"]);
  // A security the catalog no longer lets anyone select is skipped too.
  const sec = { config: viewConfig({ selection: [{ kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000002" }, { kind: "indicator", key: "t.cat_alpha.1" }] }), unavailable: [] };
  assert.deepEqual(applyViewConfig(sec, { catalog, today: TODAY, settings: initialPageSettings(catalog, TODAY) }).skipped, ["Unlinked Index Two"]);
});

test("a stored mode that is not a chart measure keeps the current measure, with a note in the server's labels", () => {
  const settings = { ...initialPageSettings(catalog, TODAY), measure: "sigma" };
  for (const mode of ["level", "default"]) {
    const a = applyViewConfig({ config: viewConfig({ mode }), unavailable: [] }, { catalog, today: TODAY, settings });
    assert.equal(a.settings.measure, "sigma");
    assert.deepEqual(a.measureKept, { stored: mode, kept: "sigma" });
    const [note] = viewNotices(a, catalog.vocabularies.modes);
    assert.ok(note.includes(`Mode label ${mode}`) && note.includes("Mode label sigma"), note);
  }
  const ok = applyViewConfig({ config: viewConfig({ mode: "index" }), unavailable: [] }, { catalog, today: TODAY, settings });
  assert.equal(ok.settings.measure, "index");
  assert.deepEqual(viewNotices(ok, catalog.vocabularies.modes), []);
});

test("any anchor change that is not itself relative makes the anchor a plain date", () => {
  const s = initialPageSettings(catalog, TODAY);
  assert.equal(s.anchorYears, 5);
  assert.equal(patchSettings(s, { anchor: "2020-01-31" }).anchorYears, null, "a drag");
  assert.equal(patchSettings(s, { anchor: "2021-10-07", anchorYears: 5 }).anchorYears, 5, "the quick button");
  assert.equal(patchSettings(s, { measure: "sigma" }).anchorYears, 5, "other settings leave it alone");
});

test("requests: POST {name, config}, PUT {config} only, DELETE by id — no identity anywhere", () => {
  const cfg = viewConfig();
  assert.deepEqual(createViewRequest("A", cfg), { url: "/api/market/views", method: "POST", body: { name: "A", config: cfg } });
  assert.deepEqual(updateViewRequest("v1", cfg), { url: "/api/market/views/v1", method: "PUT", body: { config: cfg } });
  assert.deepEqual(deleteViewRequest("v1"), { url: "/api/market/views/v1", method: "DELETE" });
  assert.ok(!/org_id|user_id/.test(JSON.stringify([createViewRequest("A", cfg), updateViewRequest("v", cfg)])));
});

test("the list fails closed; presets are never updatable or deletable; can_write must be exactly true", () => {
  const ready = interpretViews({ ok: true, body: viewsBody() });
  assert.equal(ready.kind, "ready");
  const entries = viewEntries(ready);
  assert.deepEqual(entries.map((e) => [e.name, e.preset, e.canUpdate, e.canDelete]), [
    ["Preset view zeta", true, false, false],
    ["User view eta", false, true, true],
    ["User view theta", false, true, true],
  ]);
  assert.equal(viewNameMaxLength(ready), 77, "the server's name limit");
  for (const mutate of [(b) => delete b.permissions, (b) => (b.permissions = { can_read: false }), (b) => delete b.presets, (b) => delete b.views]) {
    const b = viewsBody();
    mutate(b);
    const st = interpretViews({ ok: true, body: b });
    assert.deepEqual(st, { kind: "error", message: NO_ENVELOPE_MESSAGE });
    assert.deepEqual(viewEntries(st), []);
    assert.equal(canWriteViews(st), false);
  }
  const ro = interpretViews({ ok: true, body: viewsBody({ canWrite: false }) });
  assert.equal(canWriteViews(ro), false);
  assert.ok(viewEntries(ro).every((e) => !e.canUpdate && !e.canDelete));
  assert.equal(interpretViews({ ok: false, body: { detail: "Server refusal text" } }).message, "Server refusal text");
});
