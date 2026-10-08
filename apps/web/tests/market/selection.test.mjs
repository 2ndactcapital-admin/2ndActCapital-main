/** The pure selection module (mkt04a Task 4). */
import assert from "node:assert/strict";
import { test } from "node:test";

import { interpretCatalog } from "../../lib/market/catalogModel.mjs";
import {
  categoryState,
  clearAll,
  indicatorGroups,
  limitMessage,
  resolveSelection,
  sanitize,
  securityGroups,
  selectionLimit,
  toggle,
  toggleCategory,
} from "../../lib/market/selection.mjs";
import { LIMIT, catalogBody } from "./fixtures.mjs";

const ready = (body) => interpretCatalog({ ok: true, body }).catalog;
const catalog = ready(catalogBody());
const [alpha, beta, gamma] = indicatorGroups(catalog);

const onlyKindKey = (sel) => sel.every((s) => Object.keys(s).sort().join(",") === "key,kind");

test("groups follow the server's categories, sort order and labels", () => {
  assert.deepEqual(indicatorGroups(catalog).map((g) => g.label), ["Alpha group", "Beta group", "Gamma group"]);
  assert.equal(alpha.color, "#123456");
  assert.equal(alpha.indicators.length, 15);
});

test("bulk category toggle selects every selectable indicator in that category only, reporting all/some/none", () => {
  assert.equal(categoryState([], alpha), "none");
  const r = toggleCategory([], catalog, alpha);
  assert.equal(r.blocked, false);
  assert.equal(r.selection.length, 15);
  assert.ok(r.selection.every((s) => s.kind === "indicator" && s.key.startsWith("t.cat_alpha.")));
  assert.equal(categoryState(r.selection, alpha), "all");
  assert.equal(categoryState(r.selection, beta), "none");
  const some = toggle(r.selection, catalog, "indicator", "t.cat_alpha.3").selection;
  assert.equal(categoryState(some, alpha), "some");
  // Clicking a "some" chip selects the rest; clicking an "all" chip clears it.
  const all = toggleCategory(some, catalog, alpha).selection;
  assert.equal(categoryState(all, alpha), "all");
  assert.deepEqual(toggleCategory(all, catalog, alpha).selection, []);
});

test("a chip with nothing selectable is 'none' and selects nothing", () => {
  const empty = { key: "x", label: "x", color: null, indicators: [{ name: "no key", series_key: null }] };
  assert.equal(categoryState([], empty), "none");
  assert.deepEqual(toggleCategory([], catalog, empty).selection, []);
});

test("toggling is reversible", () => {
  const a = toggle([], catalog, "indicator", "t.cat_beta.2").selection;
  const b = toggle(a, catalog, "security", "aaaaaaaa-0000-0000-0000-000000000001").selection;
  const c = toggle(b, catalog, "security", "aaaaaaaa-0000-0000-0000-000000000001").selection;
  const d = toggle(c, catalog, "indicator", "t.cat_beta.2").selection;
  assert.deepEqual(c, a);
  assert.deepEqual(d, []);
});

test(`the server's limit (${LIMIT}) blocks the 41st selection with the message, leaving the selection unchanged`, () => {
  assert.equal(selectionLimit(catalog), LIMIT);
  let sel = [];
  sel = toggleCategory(sel, catalog, alpha).selection; // 15
  sel = toggleCategory(sel, catalog, beta).selection; // 30
  for (let i = 0; i < 10; i += 1) sel = toggle(sel, catalog, "indicator", `t.cat_gamma.${i}`).selection;
  assert.equal(sel.length, LIMIT);
  const r = toggle(sel, catalog, "indicator", "t.cat_gamma.10");
  assert.equal(r.blocked, true);
  assert.equal(r.message, limitMessage(LIMIT));
  assert.equal(r.selection.length, LIMIT);
  // A bulk chip that would cross the limit adds nothing.
  const bulk = toggleCategory(sel, catalog, gamma);
  assert.equal(bulk.blocked, true);
  assert.equal(bulk.selection.length, LIMIT);
});

test("the limit is the server's: a different max_keys changes it, and a missing one allows nothing", () => {
  const body = catalogBody();
  body.vocabularies.limits.max_keys = 2;
  const small = ready(body);
  let s = toggle([], small, "indicator", "t.cat_alpha.0").selection;
  s = toggle(s, small, "indicator", "t.cat_alpha.1").selection;
  assert.equal(toggle(s, small, "indicator", "t.cat_alpha.2").blocked, true);
  delete body.vocabularies.limits;
  const none = ready(body);
  assert.equal(selectionLimit(none), 0);
  assert.equal(toggle([], none, "indicator", "t.cat_alpha.0").blocked, true);
});

test("unselectable securities and notes can never enter the selection", () => {
  for (const id of ["aaaaaaaa-0000-0000-0000-000000000002", "bbbbbbbb-0000-0000-0000-000000000001"]) {
    const r = toggle([], catalog, "security", id);
    assert.equal(r.blocked, true);
    assert.deepEqual(r.selection, []);
  }
  // Not through sanitize either (e.g. a saved list from elsewhere), nor an unknown kind or key.
  const forged = [
    { kind: "security", key: "bbbbbbbb-0000-0000-0000-000000000002" },
    { kind: "note", key: "t.cat_alpha.0" },
    { kind: "indicator", key: "no.such.series" },
  ];
  assert.deepEqual(sanitize(forged, catalog), []);
});

test("the selection list is exactly [{kind, key}] with no extra fields", () => {
  let s = toggle([], catalog, "indicator", "t.cat_alpha.0").selection;
  s = toggle(s, catalog, "security", "aaaaaaaa-0000-0000-0000-000000000001").selection;
  s = toggleCategory(s, catalog, beta).selection;
  assert.ok(onlyKindKey(s));
  assert.deepEqual(s[1], { kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000001" });
  const withExtras = sanitize([{ kind: "indicator", key: "t.cat_alpha.0", org_id: "x", label: "y" }], catalog);
  assert.deepEqual(withExtras, [{ kind: "indicator", key: "t.cat_alpha.0" }]);
  assert.deepEqual(clearAll(), []);
});

test("a security resolves to its linked series and is shown with its own name", () => {
  const r = resolveSelection([{ kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000001" }], catalog);
  assert.equal(r[0].series_key, "t.cat_alpha.0");
  assert.equal(r[0].label, "Linked Index One");
});

test("securities group by type; a type with nothing selectable starts collapsed", () => {
  const g = securityGroups(catalog);
  assert.deepEqual(g.map((x) => [x.type, x.securities.length, x.selectableCount, x.collapsed]), [
    ["index", 2, 1, false],
    ["structured_note", 2, 0, true],
  ]);
});
